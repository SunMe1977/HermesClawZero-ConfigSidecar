import sys
import os
import requests

if len(sys.argv) < 2:
    print("Usage: memory-capture \"text\"")
    sys.exit(1)

text = sys.argv[1]

resp = requests.post(
    os.getenv("API_URL", "http://localhost:8000") + "/capture",
    headers={"X-API-Key": os.getenv("API_KEY", "")},
    json={"text": text},
)

print(resp.json())
