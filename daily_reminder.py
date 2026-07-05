import psycopg
import os
import requests
import random

# DB Config
DB_HOST = os.getenv("DB_HOST", "gbrain-postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "gbrain")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# Telegram Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_random_memory():
    conn = psycopg.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cur = conn.cursor()
    cur.execute("SELECT id, content FROM pages ORDER BY RANDOM() LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("[!] Telegram credentials missing. Skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": f"🧠 *Memory Highlight:*\n\n{message}" , "parse_mode": "Markdown"}
    requests.post(url, json=payload)

if __name__ == "__main__":
    memory = get_random_memory()
    if memory:
        msg = f"ID: {memory[0]}\n\n{memory[1]}"
        print(f"Sending: {msg}")
        send_telegram(msg)
