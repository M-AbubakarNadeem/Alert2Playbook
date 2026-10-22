"""Local server: the API plus the static page, as Vercel serves them.

    python scripts/dev.py          ->  http://127.0.0.1:8000
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from server import app  # noqa: E402

app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
