import sys
import os
import requests

query = sys.argv[1]
resp = requests.get(
    os.getenv("OPENCLAW_URL", "http://localhost:8000") + "/search",
    params={"query": query},
    headers={"X-API-Key": os.getenv("OPENCLAW_KEY", "")},
)
print(resp.json())
