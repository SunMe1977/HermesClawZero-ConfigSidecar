#!/usr/bin/env python3
"""HermesClawZero Memory CLI — capture, search & autosave via Sidecar API.

Usage:
    python memory.py capture "<text>" [scope_id]
    python memory.py search "<query>" [limit=5]
    python memory.py autosave "<text>" [filename]
"""
import json, os, sys
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_dotenv(path: str) -> dict:
    env = {}
    if not os.path.isfile(path):
        return env
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip("\"'")
    return env

_env_vars = {**os.environ}
_env_file = _load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
_env_cwd = _load_dotenv(os.path.join(os.getcwd(), ".env"))

API_BASE = (
    _env_vars.get("MEM_PUBLIC_URL")
    or _env_file.get("MEM_PUBLIC_URL", "http://localhost:8010")
)
API_KEY = (
    _env_vars.get("API_KEY")
    or _env_file.get("API_KEY", "")
    or _env_cwd.get("API_KEY", "")
)


def _api_call(method: str, path: str, **kwargs):
    url = f"{API_BASE}{path}"
    params = kwargs.pop("params", {})
    params.setdefault("key", API_KEY)
    kwargs["params"] = params
    r = requests.request(method, url, **kwargs)
    r.raise_for_status()
    return r.json()


def capture(text: str, scope_id: str | None = None):
    """Capture to global chat (always findable via default search)."""
    body = {"text": text}
    if scope_id:
        body["scope_id"] = scope_id
    return _api_call("POST", "/capture", json=body)


def search(query: str, limit: int = 5, rerank: bool = False):
    return _api_call(
        "GET", "/search",
        params={"query": query, "limit": limit, "rerank_results": str(rerank).lower()},
    )


def autosave(text: str, filename: str | None = None):
    body = {"text": text}
    if filename:
        body["scope_id"] = f"autosave:{filename}"
    return _api_call("POST", "/capture", json=body)


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "capture":
        if len(sys.argv) < 3:
            print('Usage: memory.py capture "<text>" [scope_id]', file=sys.stderr)
            sys.exit(1)
        r = capture(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        print(json.dumps(r, ensure_ascii=False))

    elif cmd == "search":
        query = sys.argv[2] if len(sys.argv) > 2 else ""
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        r = search(query, limit)
        if isinstance(r, list):
            for item in r:
                content = item.get("content") or item.get("page_content") or json.dumps(item, ensure_ascii=False)
                print(content)
        else:
            print(json.dumps(r, ensure_ascii=False))

    elif cmd == "autosave":
        if len(sys.argv) < 3:
            print('Usage: memory.py autosave "<text>" [filename]', file=sys.stderr)
            sys.exit(1)
        r = autosave(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        print(json.dumps(r, ensure_ascii=False))

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
