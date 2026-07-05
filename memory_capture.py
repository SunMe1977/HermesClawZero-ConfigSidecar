import sys
import requests

if len(sys.argv) < 2:
    print("Usage: memory-capture \"text\"")
    sys.exit(1)

text = sys.argv[1]

resp = requests.post(
    "http://localhost:8000/capture",
    params={"text": text}
)

print(resp.json())
