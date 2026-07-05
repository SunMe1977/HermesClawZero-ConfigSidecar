from fastapi import Body, FastAPI, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import psycopg
import ollama
import os
import threading
import html
import secrets
import math
from pydantic import BaseModel

app = FastAPI()

# Security
security = HTTPBasic()
API_KEY = os.getenv("API_KEY") or os.getenv("OPENCLAW_KEY")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")

class CaptureRequest(BaseModel):
    text: str

# Auth Helpers
def get_current_username(credentials: HTTPBasicCredentials = Depends(security)):
    correct_password = secrets.compare_digest(credentials.password, DASHBOARD_PASSWORD)
    correct_username = secrets.compare_digest(credentials.username, "admin")
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

@app.middleware("http")
async def url_api_key(request, call_next):
    if request.url.path in ["/openapi.json", "/docs", "/docs/swagger-ui.css", "/docs/swagger-ui-bundle.js"]:
        return await call_next(request)

    key = request.headers.get("x-api-key") or request.query_params.get("key")
    if key != API_KEY:
        return HTMLResponse("Unauthorized", status_code=401)
    return await call_next(request)


# ---------------------------------------------------------
#  DATABASE + OLLAMA CONFIG
# ---------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "gbrain-postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "gbrain")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")


def connect_db():
    conn_kwargs = {"host": DB_HOST, "port": DB_PORT, "dbname": DB_NAME, "user": DB_USER, "password": DB_PASSWORD}
    try: return psycopg.connect(**conn_kwargs)
    except psycopg.OperationalError: raise


def embedding_to_pgvector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11435")
client = ollama.Client(host=OLLAMA_HOST)


# ---------------------------------------------------------
#  CAPTURE & SEARCH
# ---------------------------------------------------------
def find_similar_page(text: str, threshold: float = 0.05):
    resp = client.embeddings(model="nomic-embed-text", prompt=text)
    emb = resp["embedding"]
    emb_str = embedding_to_pgvector_literal(emb)
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT p.id, p.content, e.embedding <-> %s::vector AS dist FROM embeddings e JOIN pages p ON p.id = e.page_id ORDER BY dist ASC LIMIT 1", (emb_str,))
            row = cur.fetchone()
    if row and row[2] <= threshold: return {"id": row[0], "content": row[1], "distance": row[2]}
    return None

@app.post("/capture")
async def capture(text: str | None = None, body: CaptureRequest | None = Body(default=None)):
    capture_text = text if text is not None else (body.text if body else None)
    if not capture_text or not capture_text.strip(): raise HTTPException(status_code=400, detail="text is required")
    capture_text = capture_text.strip()
    similar = find_similar_page(capture_text)
    if similar: return {"status": "duplicate", "page_id": similar["id"], "distance": similar["distance"], "content": similar["content"]}

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pages (content) VALUES (%s) RETURNING id", (capture_text,))
            page_id = cur.fetchone()[0]
            conn.commit()

    resp = client.embeddings(model="nomic-embed-text", prompt=capture_text)
    emb = resp["embedding"]
    emb_str = embedding_to_pgvector_literal(emb)
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)", (page_id, emb_str))
            conn.commit()
    return {"status": "ok", "page_id": page_id}

@app.get("/search")
async def search(query: str = "", limit: int = 5, rerank_results: bool = False):
    if query.strip() == "":
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, content FROM pages ORDER BY id DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
        return [{"id": r[0], "content": r[1]} for r in rows]
    
    resp = client.embeddings(model="nomic-embed-text", prompt=query)
    qemb = resp["embedding"]
    qemb_str = embedding_to_pgvector_literal(qemb)
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT p.id, p.content FROM embeddings e JOIN pages p ON p.id = e.page_id ORDER BY e.embedding <-> %s::vector LIMIT %s", (qemb_str, limit * 2))
            rows = cur.fetchall()
    
    results = [{"id": r[0], "content": r[1]} for r in rows]
    if rerank_results:
        results = rerank(query, results)
    
    return results[:limit]

def rerank(query: str, items: list[dict]) -> list[dict]:
    if not items: return items
    prompt = f"Query: {query}\n\nRe-rank these items by relevance (0-10 score), return JSON: [{{'id': id, 'score': score}}]\n"
    for item in items:
        prompt += f"ID: {item['id']}, Content: {item['content'][:200]}\n"
    
    resp = client.generate(model="llama3.1:8b", prompt=prompt)
    try:
        ranked = json.loads(resp['response'])
        ranked.sort(key=lambda x: x['score'], reverse=True)
        id_map = {r['id']: r for r in items}
        return [id_map[r['id']] for r in ranked if r['id'] in id_map]
    except:
        return items

# ---------------------------------------------------------
#  ADMIN & DASHBOARD (PROTECTED)
# ---------------------------------------------------------
@app.post("/delete", dependencies=[Depends(get_current_username)])
async def delete_page(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pages WHERE id = %s", (page_id,))
            conn.commit()
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/export", dependencies=[Depends(get_current_username)])
async def export_data():
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, content FROM pages")
            rows = cur.fetchall()
    return [{"id": r[0], "content": r[1]} for r in rows]

@app.post("/tag_auto/{page_id}", dependencies=[Depends(get_current_username)])
async def tag_auto(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM pages WHERE id = %s", (page_id,))
            row = cur.fetchone()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    
    prompt = f"Analyze: '{row[0][:500]}'. Provide 3 comma-separated relevant tags. Only output the tags."
    res = client.generate(model="llama3.1:8b", prompt=prompt)
    tags = res["response"].replace(" ", "").split(",")
    
    for tag in tags:
        if tag:
            with connect_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO tags (page_id, tag) VALUES (%s, %s)", (page_id, tag))
                    conn.commit()
    return {"status": "ok", "tags": tags}

@app.get("/page_html", response_class=HTMLResponse, dependencies=[Depends(get_current_username)])
async def view_page_html(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM pages WHERE id = %s", (page_id,))
            row = cur.fetchone()
    if not row: return "<h1>Not found</h1>"
    content = html.escape(row[0])
    return f"<html><head><title>Page {page_id}</title></head><body><h1>Page {page_id}</h1><pre>{content}</pre><br><a href='/dashboard'>Back to Dashboard</a></body></html>"

@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(get_current_username)])
async def dashboard(query: str | None = None, page: int = 1):
    per_page = 20
    offset = (page - 1) * per_page
    
    with connect_db() as conn:
        with conn.cursor() as cur:
            # Get data
            if query:
                cur.execute("SELECT id, content FROM pages WHERE content ILIKE %s ORDER BY id DESC LIMIT %s OFFSET %s", (f"%{query}%", per_page, offset))
                cur.execute("SELECT COUNT(*) FROM pages WHERE content ILIKE %s", (f"%{query}%",))
            else:
                cur.execute("SELECT id, content FROM pages ORDER BY id DESC LIMIT %s OFFSET %s", (per_page, offset))
                cur.execute("SELECT COUNT(*) FROM pages")
            
            rows = cur.fetchall()
            total_items = cur.fetchone()[0]

    total_pages = math.ceil(total_items / per_page)
    
    items = ""
    for r in rows:
        items += f"""
        <li style="margin-bottom: 12px; background: #2a2a2a; padding: 10px; border-radius: 6px; border: 1px solid #444;">
            <a href='/page_html?page_id={r[0]}' style="color: #4da6ff; text-decoration: none;">[{r[0]}]</a> 
            <span style="color: #ddd;">{html.escape(r[1][:80])}</span>
            <form action='/delete' method='post' style='display:inline; float:right;'>
                <input type='hidden' name='page_id' value='{r[0]}'>
                <button type='submit' style="background: #ff4d4d; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer;" onclick='return confirm("Delete?")'>Delete</button>
            </form>
            <form action='/tag_auto/{r[0]}' method='post' style='display:inline; float:right; margin-right: 10px;'>
                <button type='submit' style="background: #28a745; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer;">Auto-Tag</button>
            </form>
        </li>
        """
    
    # Pagination buttons
    nav = ""
    for i in range(1, total_pages + 1):
        active = "background: #4da6ff;" if i == page else "background: #333;"
        nav += f'<a href="/dashboard?page={i}&query={html.escape(query or "")}" style="margin: 0 5px; padding: 5px 10px; color: white; text-decoration: none; border-radius: 4px; {active}">{i}</a>'

    return f"""
    <html>
      <head><title>Memory Dashboard</title>
        <style>body {{ font-family: sans-serif; background-color: #121212; color: #fff; padding: 20px; }} h1 {{ border-bottom: 2px solid #333; padding-bottom: 10px; }} input {{ padding: 8px; width: 300px; border-radius: 4px; border: 1px solid #444; background: #1e1e1e; color: white; }} button {{ padding: 8px 16px; background: #4da6ff; color: white; border: none; border-radius: 4px; cursor: pointer; }} ul {{ list-style-type: none; padding: 0; margin-top: 20px; }}</style>
      </head>
      <body>
        <h1>Memory Dashboard (Page {page})</h1>
        <form method="get" action="/dashboard">
          <input type="text" name="query" placeholder="Suche..." value="{html.escape(query or "")}">
          <button type="submit">Search</button>
        </form>
        <div style="margin: 20px 0;">{nav}</div>
        <ul>{items}</ul>
        <div style="margin: 20px 0;">{nav}</div>
      </body>
    </html>
    """

# ---------------------------------------------------------
#  STARTUP & RUN
# ---------------------------------------------------------
def start_sync():
    import memory_sync
    threading.Thread(target=memory_sync.run_sync, daemon=True).start()

@app.on_event("startup")
def startup_event():
    if not API_KEY: raise RuntimeError("API_KEY is required.")
    start_sync()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
