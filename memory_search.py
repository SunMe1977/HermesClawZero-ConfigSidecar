import sys
import requests
import os

query = sys.argv[1]
base_url = (os.getenv("MEM_PUBLIC_URL") or os.getenv("OPENCLAW_URL") or "http://localhost:8000").rstrip("/")
api_key = os.getenv("API_KEY") or os.getenv("OPENCLAW_KEY")
params = {"query": query}
if api_key:
	params["key"] = api_key

resp = requests.get(f"{base_url}/search", params=params)
print(resp.json())
