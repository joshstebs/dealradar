"""Run permitted live feeds, retain schema/outage evidence, return nonzero on failure."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from backend.config import Settings
from backend.main import create_app


async def validate(postal_code, query, output):
    # Fail fast in restricted execution environments before async DNS creates
    # worker threads. This is a real HTTPS request with TLS verification enabled.
    # Successful preflight is followed by the actual API ingestion path below.
    settings = Settings(database_url="sqlite:///:memory:", source_timeout=20, rfd_threads=3)
    failures = []
    with httpx.Client(timeout=15, follow_redirects=False) as preflight:
        for source, url, params in (
            ("flipp", settings.flipp_endpoint, {"q": query, "postal_code": postal_code, "locale": "en-ca"}),
            ("rfd", settings.rfd_feed, None),
        ):
            try:
                response = preflight.get(url, params=params)
                response.raise_for_status()
                if response.is_redirect:
                    raise ValueError("Endpoint redirected; revalidate source URL")
            except (httpx.HTTPError, ValueError) as exc:
                failures.append({"source": source, "status": "error", "error_code": type(exc).__name__,
                                 "message": str(exc)[:200]})
    # A failed preflight for one source must not prevent validating the other.
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://validation") as client:
            response = await client.post("/v1/search", json={"query": query, "postal_code": postal_code})
            body = response.json()
            detail = body.get("detail")
            # 503 uses {"detail": {message, search_id, sources}}; every other
            # error uses a string detail. Handle both without crashing.
            sources = body.get("sources") or (detail.get("sources") if isinstance(detail, dict) else []) or []
            error = detail.get("message") if isinstance(detail, dict) else (detail if isinstance(detail, str) else None)
            report = {"checked_at": datetime.now(timezone.utc).isoformat(), "query": query,
                      "postal_code": postal_code, "api_status": response.status_code,
                      "sources": sources, "error": error,
                      "preflight_failures": failures,
                      "live_validated": any(s.get("status") in ("ok", "partial") for s in sources)
                                         and sum(len(g.get("offers", [])) for g in body.get("groups", [])) > 0,
                      "offer_count": sum(len(g.get("offers", [])) for g in body.get("groups", [])),
                      "community_count": len(body.get("community", [])),
                      "notes": ["Source access does not independently verify retailer checkout prices or local store coverage.",
                                "A blocked source (for example RFD's bot challenge) is reported, not substituted with fixtures."]}
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            return 0 if report["live_validated"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--postal-code", default="N9A 6K9")
    parser.add_argument("--query", default="chicken")
    parser.add_argument("--output", default="validation/live-feeds.json")
    args = parser.parse_args()
    sys.exit(asyncio.run(validate(args.postal_code, args.query, args.output)))
