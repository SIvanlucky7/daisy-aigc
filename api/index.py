"""Vercel serverless entrypoint for the Daisy API.

This keeps the API available when the local computer is offline. Vercel's
serverless filesystem is ephemeral, so the SQLite database here is suitable for
smoke tests and demos until a persistent database is connected.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DAISY_DB_FILE", "/tmp/daisy.db")
os.environ.setdefault("DAISY_ORDERS_FILE", "/tmp/orders.jsonl")
os.environ.setdefault("DAISY_ALLOWED_ORIGINS", "https://daisy-aigc.vercel.app")

from server import DaisyHandler, cleanup_old_order_results, init_db  # noqa: E402


_initialized = False


def _ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    init_db()
    cleanup_old_order_results()
    _initialized = True


class handler(DaisyHandler):
    def __init__(self, *args, **kwargs):
        _ensure_initialized()
        super().__init__(*args, **kwargs)
