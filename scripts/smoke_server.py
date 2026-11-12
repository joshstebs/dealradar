"""Serve the real app with a fixture provider transport for hermetic UI tests.

No live provider calls are made: Flipp responses are fixtures shaped like the
real payload and clearly labelled as test data. Run:

    python scripts/smoke_server.py [port]
    node scripts/ui_smoke.mjs http://127.0.0.1:<port>
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import uvicorn

from backend.config import Settings
from backend.main import create_app

# Test fixtures — not real market data.
ITEMS = [
    {"id": 1, "name": "Chicken breast 1 kg", "merchant_name": "Test grocer",
     "current_price": "12.00", "original_price": "18.00",
     "url": "https://flipp.com/test", "valid_from": "2026-11-01T05:00:00Z",
     "valid_to": "2099-01-01T05:00:00Z"},
    {"id": 2, "name": "Chicken breast 1 kg", "merchant_name": "Other grocer",
     "current_price": "9.00", "original_price": "18.00",
     "url": "https://flipp.com/test2", "valid_from": "2026-11-01T05:00:00Z",
     "valid_to": "2099-01-01T05:00:00Z"},
    {"id": 3, "name": "Chicken thighs 750 g", "merchant_name": "Test grocer",
     "current_price": "7.50", "original_price": None,
     "url": "https://flipp.com/test3", "valid_from": "2026-11-01T05:00:00Z",
     "valid_to": "2099-01-01T05:00:00Z"},
]


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "backflipp.wishabi.com":
        return httpx.Response(200, json={"items": ITEMS})
    if request.url.path.startswith("/rss/"):
        return httpx.Response(403)
    return httpx.Response(403)


def build_app():
    settings = Settings(database_url=f"sqlite:////tmp/dealradar-smoke-{__import__('os').getpid()}.db",
                        rfd_threads=1)
    return create_app(settings, transport=httpx.MockTransport(handler))


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8017)
