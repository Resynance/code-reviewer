"""Vercel Python serverless entrypoint.

Vercel's @vercel/python runtime serves a module-level ASGI `app` directly — no
handler shim needed. We make the sibling packages importable, then re-export the
FastAPI app from backend/main.py.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))           # `import backend.main`
sys.path.insert(0, str(ROOT / "core"))  # bare `import config_store`, etc.

from backend.main import app  # noqa: E402  (re-exported for Vercel)
