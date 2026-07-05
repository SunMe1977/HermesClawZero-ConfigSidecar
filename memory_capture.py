import sys
import requests
import os

if len(sys.argv) < 2:
    print("Usage: memory-capture \"text\"")
    sys.exit(1)

text = sys.argv[1]
base_url = (os.getenv("MEM_PUBLIC_URL") or os.getenv("OPENCLAW_URL") or "http://localhost:8000").rstrip("/")
api_key = os.getenv("API_KEY") or os.getenv("OPENCLAW_KEY")

resp = requests.post(
    f"{base_url}/capture",
    params={"text": text, "key": api_key} if api_key else {"text": text}
)

print(resp.json())
