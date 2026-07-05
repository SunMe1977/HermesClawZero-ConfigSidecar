import requests
import os

url = "http://localhost:8000/capture"
key = os.getenv("API_KEY", "")
text = "debug test"
resp = requests.post(url, headers={"X-API-Key": key}, json={"text": text})
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text}")
