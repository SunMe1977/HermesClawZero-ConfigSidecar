from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import psycopg
import ollama
import os
import threading
from pydantic import BaseModel

app = FastAPI()

API_KEY = os.getenv("API_KEY", "change_me_in_env")


class CaptureRequest(BaseModel):
    text: str

@app.middleware("http")
async def url_api_key(request, call_next):
    if request.url.path in ["/openapi.json", "/docs", "/docs/swagger-ui.css", "/docs/swagger-ui-bundle.js"]:
        return await call_next(request)

    key = request.query_params.get("key")
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
    conn_kwargs = {
        "host": DB_HOST,
        "port": DB_PORT,
        "dbname": DB_NAME,
        "user": DB_USER,
        "password": DB_PASSWORD,
    }

    try:
        return psycopg.connect(**conn_kwargs)
    except psycopg.OperationalError:
        raise


def embedding_to_pgvector_literal(embedding: list[float]) -> str:
    # pgvector text input must look like: [0.1,0.2,...]
    return "[" + ",".join(str(x) for x in embedding) + "]"

OLLAMA_HOST = os.getenv(
    "OLLAMA_HOST",
    "http://host.docker.internal:11434"
)

client = ollama.Client(host=OLLAMA_HOST)


# ---------------------------------------------------------
#  DUPLICATE DETECTION
# ---------------------------------------------------------
def find_similar_page(text: str, threshold: float = 0.05):
    resp = client.embeddings(model="nomic-embed-text", prompt=text)
    emb = resp["embedding"]
    emb_str = embedding_to_pgvector_literal(emb)

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.content, e.embedding <-> %s::vector AS dist
                FROM embeddings e
                JOIN pages p ON p.id = e.page_id
                ORDER BY dist ASC
                LIMIT 1
                """,
                (emb_str,)
            )
            row = cur.fetchone()

    if row and row[2] <= threshold:
        return {"id": row[0], "content": row[1], "distance": row[2]}

    return None


# ---------------------------------------------------------
#  CAPTURE
# ---------------------------------------------------------
@app.post("/capture")
async def capture(text: str | None = None, body: CaptureRequest | None = Body(default=None)):
    capture_text = text if text is not None else (body.text if body else None)
    if not capture_text or not capture_text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    capture_text = capture_text.strip()
    similar = find_similar_page(capture_text)
    if similar:
        return {
            "status": "duplicate",
            "page_id": similar["id"],
            "distance": similar["distance"],
            "content": similar["content"],
        }

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pages (content) VALUES (%s) RETURNING id",
                (capture_text,)
            )
            page_id = cur.fetchone()[0]
            conn.commit()

    resp = client.embeddings(model="nomic-embed-text", prompt=capture_text)
    emb = resp["embedding"]
    emb_str = embedding_to_pgvector_literal(emb)

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)",
                (page_id, emb_str)
            )
            conn.commit()

    return {"status": "ok", "page_id": page_id}


# ---------------------------------------------------------
#  SEMANTISCHE SUCHE
# ---------------------------------------------------------
@app.get("/search")
async def search(query: str = "", limit: int = 5):
    if query.strip() == "":
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, content FROM pages ORDER BY id DESC LIMIT %s",
                    (limit,)
                )
                rows = cur.fetchall()
        return [{"id": r[0], "content": r[1]} for r in rows]

    resp = client.embeddings(model="nomic-embed-text", prompt=query)
    qemb = resp["embedding"]
    qemb_str = embedding_to_pgvector_literal(qemb)

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.content
                FROM embeddings e
                JOIN pages p ON p.id = e.page_id
                ORDER BY e.embedding <-> %s::vector
                LIMIT %s
                """,
                (qemb_str, limit)
            )
            rows = cur.fetchall()

    return [{"id": r[0], "content": r[1]} for r in rows]


# ---------------------------------------------------------
#  RAG ANSWER
# ---------------------------------------------------------
@app.post("/answer")
async def answer(query: str, limit: int = 5):
    resp = client.embeddings(model="nomic-embed-text", prompt=query)
    qemb = resp["embedding"]
    qemb_str = embedding_to_pgvector_literal(qemb)

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.content
                FROM embeddings e
                JOIN pages p ON p.id = e.page_id
                ORDER BY e.embedding <-> %s::vector
                LIMIT %s
                """,
                (qemb_str, limit)
            )
            rows = cur.fetchall()

    context = "\n\n".join([r[0] for r in rows])

    prompt = f"""
Nutze den folgenden Kontext, um die Frage zu beantworten:

Kontext:
{context}

Frage:
{query}

Antwort:
"""

    result = client.generate(model="llama3.1:8b", prompt=prompt)

    return {
        "query": query,
        "context_used": rows,
        "answer": result["response"]
    }


# ---------------------------------------------------------
#  TAG SYSTEM
# ---------------------------------------------------------
@app.post("/tag")
async def add_tag(page_id: int, tag: str):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tags (page_id, tag) VALUES (%s, %s)",
                (page_id, tag)
            )
            conn.commit()
    return {"status": "ok", "page_id": page_id, "tag": tag}


@app.get("/tags")
async def get_tags(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tag FROM tags WHERE page_id = %s",
                (page_id,)
            )
            rows = cur.fetchall()
    return [r[0] for r in rows]


@app.get("/search_by_tag")
async def search_by_tag(tag: str):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.content
                FROM tags t
                JOIN pages p ON p.id = t.page_id
                WHERE t.tag = %s
                ORDER BY p.id DESC
                """,
                (tag,)
            )
            rows = cur.fetchall()
    return [{"id": r[0], "content": r[1]} for r in rows]


@app.delete("/tag")
async def delete_tag(page_id: int, tag: str):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tags WHERE page_id = %s AND tag = %s",
                (page_id, tag)
            )
            conn.commit()
    return {"status": "ok", "deleted_tag": tag, "page_id": page_id}


# ---------------------------------------------------------
#  DELETE PAGE
# ---------------------------------------------------------
@app.delete("/delete")
async def delete_page(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pages WHERE id = %s", (page_id,))
            conn.commit()
    return {"status": "ok", "deleted_page_id": page_id}


# ---------------------------------------------------------
#  PAGE VIEWER
# ---------------------------------------------------------
@app.get("/page")
async def get_page(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM pages WHERE id = %s", (page_id,))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    return {"id": page_id, "content": row[0]}


@app.get("/page_html", response_class=HTMLResponse)
async def view_page_html(page_id: int):
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM pages WHERE id = %s", (page_id,))
            row = cur.fetchone()

    if not row:
        return "<h1>Not found</h1>"

    content = row[0]
    return f"""
    <html>
      <head><title>Page {page_id}</title></head>
      <body>
        <h1>Page {page_id}</h1>
        <pre>{content}</pre>
      </body>
    </html>
    """


# ---------------------------------------------------------
#  SYNC THREAD
# ---------------------------------------------------------
def start_sync():
    import memory_sync
    threading.Thread(target=memory_sync.run_sync, daemon=True).start()


@app.on_event("startup")
def startup_event():
    start_sync()


# ---------------------------------------------------------
#  DASHBOARD
# ---------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(query: str | None = None):
    with connect_db() as conn:
        with conn.cursor() as cur:
            if query:
                cur.execute(
                    "SELECT id, content FROM pages WHERE content ILIKE %s ORDER BY id DESC LIMIT 50",
                    (f"%{query}%",)
                )
            else:
                cur.execute(
                    "SELECT id, content FROM pages ORDER BY id DESC LIMIT 50"
                )
            rows = cur.fetchall()

    items = ""
    for r in rows:
        items += f"<li><a href='/page_html?page_id={r[0]}'>[{r[0]}]</a> {r[1][:80]}</li>"

    return f"""
    <html>
      <head><title>Memory Dashboard</title></head>
      <body>
        <h1>Memory Dashboard</h1>
        <form method="get" action="/dashboard">
          <input type="text" name="query" placeholder="Suche..." value="{query or ''}">
          <button type="submit">Search</button>
        </form>

        <ul>
          {items}
        </ul>
      </body>
    </html>
    """


# ---------------------------------------------------------
#  UVICORN SERVER START
# ---------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
