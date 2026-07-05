import sys
import requests
import os

query = sys.argv[1]
base_url = (os.getenv("MEM_PUBLIC_URL") or os.getenv("OPENCLAW_URL") or "http://localhost:8010").rstrip("/")
api_key = os.getenv("API_KEY") or os.getenv("OPENCLAW_KEY")
scope_id = os.getenv("MEM_SCOPE_ID", "").strip()
params = {"query": query}
if api_key:
	params["key"] = api_key
if scope_id:
	params["scope_id"] = scope_id

resp = requests.get(f"{base_url}/search", params=params)
print(resp.json())
