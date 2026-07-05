import psycopg
import ollama
import os

from dotenv import load_dotenv

load_dotenv()

# Lokaler Zugriff auf Postgres im Docker-Container
DB_DSN = (
    f"postgresql://{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', '')}"
    f"@{os.getenv('DB_HOST', 'host.docker.internal')}:{os.getenv('DB_PORT', '5666')}"
    f"/{os.getenv('DB_NAME', 'gbrain')}"
)

# Ollama läuft lokal
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

client = ollama.Client(host=OLLAMA_HOST)

with psycopg.connect(DB_DSN) as conn:
    with conn.cursor() as cur:
        # Alle Pages ohne Embeddings finden
        cur.execute("""
            SELECT p.id, p.content
            FROM pages p
            LEFT JOIN embeddings e ON p.id = e.page_id
            WHERE e.page_id IS NULL
        """)
        rows = cur.fetchall()

        print(f"Rebuilding embeddings for {len(rows)} pages...")

        for page_id, content in rows:
            # Embedding erzeugen
            resp = client.embeddings(model="nomic-embed-text", prompt=content)
            emb = resp["embedding"]
            emb_str = ",".join(str(x) for x in emb)

            # Embedding speichern
            cur.execute(
                "INSERT INTO embeddings (page_id, embedding) VALUES (%s, %s::vector)",
                (page_id, emb_str)
            )

        conn.commit()

print("Done.")
