"""Local server: the API plus the static page, as Vercel serves them.

    python scripts/dev.py          ->  http://127.0.0.1:8000

It applies the same security headers as vercel.json and serves /admin as admin.html,
like Vercel's cleanUrls, so the local site behaves the way the live one does.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from server import app  # noqa: E402

VERCEL = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
RULES = [(re.compile("^" + rule["source"].replace("(.*)", ".*") + "$"), rule["headers"]) for rule in VERCEL.get("headers", [])]


@app.middleware("http")
async def vercel_headers(request: Request, call_next):
    response = await call_next(request)
    for pattern, headers in RULES:
        if pattern.match(request.url.path):
            for h in headers:
                if h["key"] != "Strict-Transport-Security":  # HTTPS only; this server is plain HTTP
                    response.headers[h["key"]] = h["value"]
    return response


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(ROOT / "public" / "admin.html")


app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
