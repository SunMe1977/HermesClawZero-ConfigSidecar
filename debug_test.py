import requests
import os
url = "http://localhost:8010/capture"
key = "MYSECRET!!1344"
text = "debug test"
resp = requests.post(url, params={"key": key}, json={"text": text})
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text}")
