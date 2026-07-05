import requests
import sys
import os

# Update these to match your HermesClawZero setup
BASE_URL = os.getenv("OPENCLAW_URL", "http://localhost:8000")
API_KEY = os.getenv("OPENCLAW_KEY", "")
SYNC_DIR = os.getenv("OPENCLAW_SYNC_DIR", os.path.join(os.getcwd(), "sync"))

def capture(text):
    url = f"{BASE_URL}/capture"
    data = {"text": text}
    try:
        response = requests.post(url, headers={"X-API-Key": API_KEY}, json=data)
        print(response.json())
    except Exception as e:
        print(f"Error: {e}")

def search(query, limit=5):
    url = f"{BASE_URL}/search"
    params = {"query": query, "limit": limit}
    try:
        response = requests.get(url, params=params, headers={"X-API-Key": API_KEY})
        print(response.json())
    except Exception as e:
        print(f"Error: {e}")

def autosave(content, filename):
    if not filename.endswith(('.txt', '.md', '.json')):
        filename += ".txt"
    filepath = os.path.join(SYNC_DIR, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Autosaved to {filepath}")
    except Exception as e:
        print(f"Error writing file: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: memory.py [capture|search|autosave] [args...]")
        sys.exit(1)
        
    cmd = sys.argv[1]
    if cmd == "capture":
        capture(sys.argv[2])
    elif cmd == "search":
        search(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 5)
    elif cmd == "autosave":
        autosave(sys.argv[2], sys.argv[3])
