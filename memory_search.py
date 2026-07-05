import sys
import requests

query = sys.argv[1]
resp = requests.get("http://localhost:8000/search", params={"query": query})
print(resp.json())
