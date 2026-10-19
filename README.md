# DealRadar CA starter

FastAPI backend, source adapters, persistent offer/history read model, injectable
LLM normalizer, native Android client, and responsive React component. The API
runs locally; external feeds and an Android APK remain unvalidated in this
restricted execution environment. No deployment has been performed.

```sh
cd /workspace/dealradar-ca
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

SQLite in `data/dealradar.db` is the default; no database server is needed locally.
Swagger UI: `http://localhost:8000/docs`. OpenAPI: `/openapi.json`. Health: `/health`.
The API implements localized search, source status, offer details, compatible
comparison groups, history, verified-steal selection, basket quotes, and
completed-search WebSocket event replay. Search uses conservative intent cleanup;
it does not geocode natural-language place names or perform semantic retrieval.

```sh
curl -X POST http://localhost:8000/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"cheap chicken breast near Windsor","postal_code":"N9V"}'
python scripts/validate_live.py --postal-code N9V --query chicken
python scripts/export_openapi.py
```

The live checker writes a report and exits nonzero on failure. It never
substitutes fixtures. See `validation/live-feeds.json` for the actual attempt.
Enable feeds individually with `FLIPP_ENABLED` and `RFD_ENABLED`. All-source
failure returns HTTP 503; partial failures retain useful results and warnings.

Set `DATABASE_URL=postgresql://...` for PostgreSQL and `REDIS_URL=redis://...` for
Redis. Configuration reads process environment; `.env.example` is a template,
not automatically loaded. Drivers are included in `requirements.txt`. The API
initializes its `api_*` read-model tables; `db/schema.sql` is the separate
catalog/vector schema. Move initialization to a privileged migration job before
production. `CORS_ORIGINS` is a comma-separated list of permitted web origins.

Flipp's adapter cache lasts six hours and retains the full supplied postal scope
until equivalent FSA coverage is proven. Search and national RFD snapshots cache
for five minutes. Source-reported prices cannot become verified steals without
independent checkout and local-coverage evidence; the current adapters provide
neither, so verified-steal responses are intentionally empty.

Native Android setup: [`android/README.md`](android/README.md). CI is configured
for Python, real PostgreSQL/Redis connections, and Android JVM tests/debug APK.
The workflow has not been run from this workspace.

Apply `db/schema.sql` to PostgreSQL 15+ with pgvector installed. Use least-privilege
database credentials; migrations run separately from the API identity.

Client usage:

```python
import httpx
from scrapers.flipp_client import FlippClient
from scrapers.rfd_client import RFDClient

async def collect():
    async with httpx.AsyncClient(headers={"User-Agent": "DealRadarCA/0.1"}) as http:
        flyers = await FlippClient(http).search("chicken breast", "N9V")
        threads = await RFDClient(http).trending(min_score=20)
        return flyers, threads
```

For Redis, pass a `redis.asyncio.Redis` instance to `FlippClient(cache=...)`.
Set `region_variant` only after validating identical circular coverage.
Flipp endpoint and RFD selectors need live validation; these examples make no
claim of working partnerships or comprehensive coverage. Reebee and independently
verified PC Express/Walmart integrations still need permitted endpoints or
licensed feeds. Distributed scheduling, account sync, vector retrieval, and
notifications remain roadmap work.

For normalization, supply an async `llm(system, payload, schema)` callback returning
strict JSON using your configured provider's structured-output API. Keep provider
credentials on the server. Call `normalize(title, sale_price, llm)`; set
`source_verified=True` only after independent source verification. Ambiguous
quantities require review before publication. The LLM callback intentionally does
not commit to a provider/model that may have changed availability.

Copy `components/DealRadar.tsx` into a Next.js App Router project with Tailwind.
Supply API data in its documented `Offer` shape. Validate outbound URLs on the
backend; the component renders only HTTPS links. Search currently filters supplied
titles; adapt its camelCase props from the API's snake_case offer fields.
Persistent saved lists are not implemented.
