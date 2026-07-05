import sys
import json
import os
import requests
from mcp.server.fastmcp import FastMCP

# Initialize MCP Server
mcp = FastMCP("HermesClawZero-Memory")

BASE_URL = "http://localhost:8010"
API_KEY = os.getenv("API_KEY", "change_me_in_env")

@mcp.tool()
def search_memory(query: str, limit: int = 5):
    """Search your long-term memory."""
    resp = requests.get(f"{BASE_URL}/search", params={"query": query, "limit": limit, "key": API_KEY})
    return resp.json()

@mcp.tool()
def capture_memory(text: str):
    """Capture a new piece of information into your long-term memory."""
    resp = requests.post(f"{BASE_URL}/capture", params={"key": API_KEY}, json={"text": text})
    return resp.json()

if __name__ == "__main__":
    mcp.run()
