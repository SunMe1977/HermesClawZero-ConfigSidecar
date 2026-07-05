import sys
import requests
import os

if len(sys.argv) < 2:
    print("Usage: memory-capture \"text\"")
    sys.exit(1)

text = sys.argv[1]
base_url = (os.getenv("MEM_PUBLIC_URL") or os.getenv("OPENCLAW_URL") or "http://localhost:8010").rstrip("/")
api_key = os.getenv("API_KEY") or os.getenv("OPENCLAW_KEY")
scope_id = os.getenv("MEM_SCOPE_ID", "").strip()

params = {"text": text}
if api_key:
    params["key"] = api_key
if scope_id:
    params["scope_id"] = scope_id

resp = requests.post(f"{base_url}/capture", params=params)

print(resp.json())
