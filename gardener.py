import psycopg
import ollama
import os
import json

# DB Config
DB_HOST = os.getenv("DB_HOST", "gbrain-postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "gbrain")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")

client = ollama.Client(host=os.getenv("OLLAMA_HOST", "http://host.docker.internal:11435"))

def gardener():
    conn = psycopg.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cur = conn.cursor()
    
    # 1. Find untagged items
    cur.execute("SELECT p.id, p.content FROM pages p LEFT JOIN tags t ON p.id = t.page_id WHERE t.tag IS NULL LIMIT 10")
    rows = cur.fetchall()
    
    for row in rows:
        page_id, content = row
        print(f"Gardening ID: {page_id}")
        
        # Auto-tag
        prompt = f"Analyze: '{content[:500]}'. Provide 3 comma-separated tags. Only output the tags."
        resp = client.generate(model="llama3.1:8b", prompt=prompt)
        tags = [t.strip() for t in resp['response'].split(',')]
        
        for tag in tags:
            if tag:
                cur.execute("INSERT INTO tags (page_id, tag) VALUES (%s, %s)", (page_id, tag))
        
        conn.commit()
    
    cur.close()
    conn.close()
    print("Gardening complete.")

if __name__ == "__main__":
    gardener()
