# Reviewer handoff — DealRadar PR #2

For an independent reviewer. State below was last verified 2026-11-11 against
commit `d43ba44`. Do not merge or deploy from this document alone; re-run the
commands and judge the code.

## What this PR is

Reliability/honesty pass over the DealRadar CA FastAPI app + static web client:
correct discount math (no per-measure-vs-package comparisons, zero-price and
>10x-original rejection, `period` applied), date-only flyer expiry resolved in
`America/Toronto`, multipack unit pricing, Decimal watchlist ranking,
package-only history summaries, retailer/price/sort search filters, client
caveat/warning rendering, `[hidden]` CSS fix found in a real browser, legacy
SQLite migration drill, and a hermetic UI smoke harness. Prior work (Flipp
ingestion, gzip single-decode, honest deal labels) is preserved.

Branch `feat/dealradar-reliability-20261110` → `main`, PR #2. Latest commit:
`d43ba44`. Evidence trail: the two review comments on PR #2 (2026-11-11).

## Exact verification commands

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m compileall -q backend scrapers services scripts tests
.venv/bin/python -m unittest discover -s tests -v
# Expected: 61 tests, 58 passed, 3 skipped.

# Hermetic UI smoke — real app, fixture provider transport, zero live calls:
npm install --include=dev          # jsdom 26 dev-only
.venv/bin/python scripts/smoke_server.py 8017 &   # wait for /health
node scripts/ui_smoke.mjs http://127.0.0.1:8017
# Expected: 13/13 assertions, "UI smoke test passed".

# Optional live check (needs unrestricted egress; writes validation/live-feeds.json):
.venv/bin/python scripts/validate_live.py --postal-code "N9A 6K9" --query chicken
# Expected: exit 0, Flipp partial/ok with offers; RFD reported blocked (never substituted).
```

Known skipped tests (intentional, do not "fix" by pointing at real services):

1. `test_contract.AndroidContractTests` — native Android sources are not in
   this repository.
2. `test_external_storage.PostgresTests.test_snapshot_persists_across_connections`
   — needs `TEST_DATABASE_URL`; **PostgreSQL path is unverified** (driver
   installed; dialect shim `?`→`%s` and startup DDL untested on a real server).
3. `test_external_storage.RedisTests.test_redis_round_trip_and_ttl` — needs
   `TEST_REDIS_URL`; Redis resilience is unverified.

## Deployment requirements

- Python 3.12+ (verified on 3.14.4); `pip install -r requirements.txt`.
- `DATABASE_URL` — `sqlite:///path.db` (default `data/dealradar.db`) or
  `postgresql://…`. Optional `REDIS_URL` for a shared cache; without it each
  worker keeps a private in-process cache.
- `CORS_ORIGINS` if the web client is served from a different origin
  (methods GET/POST only — DELETE is not allowed cross-origin).
- Run: `.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000`
  (web UI at `/`, API docs at `/docs`).
- When shipping CSS/JS changes, serve `/static/*` with cache busting or
  `no-cache` — the skeleton-visibility fix is invisible behind a cached
  stylesheet.

## Migration steps

No migration tool. On first startup `Repository` creates the five `api_*`
tables, then `_ensure_columns()` adds `fsa/scope/item_id/observed_at` to
`api_offers` (+`item_id` on observations) and backfills rows from payloads;
corrupt rows are skipped. `MigrationTests` prove this on SQLite including
idempotent reopen. **On PostgreSQL the same code path is untested** — run the
storage tests against a disposable database before first production boot.

## Security limitations (accepted for this release, on record)

- **No account authentication.** Watchlists are scoped by a caller-supplied
  `device_id` (validated 8–64 chars, `no-store` responses, id not echoed).
  Anyone who learns a device id can read/delete/overwrite that device's list.
  `item_id`s are deterministic UUID5s, so lists are enumerable by observation.
- `target_price`/`target_hit` are computed on read; **no notification delivery
  exists** behind them.
- All endpoints are public and unthrottled except the search semaphore
  (8 concurrent, 429 + `Retry-After`).
- Flyer prices are source-reported; no checkout, stock, or store-level
  availability verification. RFD is bot-blocked and reported as such.
- WebSocket `/v1/searches/{id}/events` replays public search snapshots only.

## Known residual defects / review focus (non-blocking unless release-critical)

- Per-measure (`$x/lb`) payloads were never observed live (0/214 probe); that
  path is test-covered only.
- `relevant()` matches query tokens against titles only — near-name products
  (e.g. novelty items containing the query word) can surface.
- Product grouping drops "marketing fluff" words; "sugar free" vs "sugar" can
  over-merge in theory (key requires name+size agreement).
- `sql.replace("?", "%s")` rewrites `?` inside string literals on Postgres.
- `db/schema.sql` (catalog/pgvector) is unused by the API; runtime uses `api_*`
  tables only.
- No CI runs yet: the workflow YAML exists (untracked `.github/workflows/ci.yml`
  and in the PR review comment) and its smoke job was proven locally, but
  pushing it needs a narrowly authorized token with the `workflow` scope.
  Do not request broader PAT permissions.

## Ground rules for the reviewer

Fix release-blocking defects on this branch; document anything larger in the
PR. No new features, no merge, no deploy, no paid services, no fixture
substitution for live providers, no weakening of tests.
