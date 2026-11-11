# Verification status — 2026-10-13

## Verified locally

- Python compilation passed for backend, scrapers, services, scripts, and tests.
- Full suite: 25 discovered, 23 passed, 2 skipped. Checks cover actual FastAPI
  ASGI requests and lifespan with fixture-controlled upstream HTTP, SQLite
  persistence, Decimal comparisons, package conversion, postal isolation,
  caching, partial/total outages, ambiguous price exclusion, expiry, unknown
  offers, history, basket totals, XML safety, RFD category evidence, unknown
  votes, and actual WebSocket event dispatch.
- Explicit per-pound flyer prices convert to $/100g, while basket quotes exclude
  variable-weight prices until a requested weight is supplied.
- Android DTO wire fields and money-string types match generated OpenAPI schemas.
- `contracts/openapi.json` was generated from the implemented application.
- Android manifest/resource XML parses locally. This structural check is not an
  Android compilation or device test.

## Live attempt

TLS-verified HTTPS requests to Flipp and RFD both failed with
`ConnectError: [Errno 1] Operation not permitted`. See `validation/live-feeds.json`.
The cloud environment reports unrestricted HTTP egress, but the command sandbox
restricts network execution. Dependency/network permission requests were aborted.
No bypass was attempted.

External endpoint schemas, feed availability, RFD selectors, retailer checkout
prices, and store coverage remain unvalidated. Fixture success does not prove
live compatibility. Source-reported prices cannot earn a verified-steal badge.

## Execution limits

- PostgreSQL/Redis integration tests are skipped locally because test service
  URLs/drivers are unavailable. CI provisions both services; it has not run here.
- Android SDK, Gradle, and Kotlin compiler are unavailable. No APK was built,
  Kotlin/JVM tests were not executed here, and no device/emulator test occurred.
  Build configuration, MockWebServer and ViewModel tests, and CI are included.
- A TCP Uvicorn deployment was not exercised. HTTP checks use the real FastAPI
  app in-process through ASGI. No deployment was performed.
- Catalog pgvector SQL, LLM provider callbacks, native release signing, push,
  accessibility, and account synchronization remain unvalidated.
- The React component has not been typechecked, built, or browser-tested.

## Repeatable commands

```sh
python -m unittest discover -s tests -v
python scripts/export_openapi.py
python scripts/validate_live.py --postal-code N9V --query chicken
cd android
gradle :app:testDebugUnitTest :app:assembleDebug
```

Set `TEST_DATABASE_URL` and `TEST_REDIS_URL` to dedicated test services to run
storage integrations. Do not point these tests at a production database.

## 2026-11-10 — reliability pass (OracleStebs)

Verified locally against Python 3.14 and the live Flipp endpoint:

- Full suite: 40 discovered, 37 passed, 3 skipped (PostgreSQL/Redis services and
  the Android client, which is not in this repository).
- Live Flipp search with a full postal code (N9A 6K9): HTTP 200, 8–43 offers,
  images and sale/regular prices mapped. An FSA-only request returns HTTP 422
  with a clear explanation; the live provider rejects FSAs with code 605.
- RFD is bot-blocked (HTTP 202 JavaScript challenge) and is reported as
  `blocked`, not as a schema change. Search continues with Flipp.
- The gzip double-decode defect was reproduced against the live feed
  (`httpx.DecodingError: incorrect header check`) and is covered by a regression
  test that serves a gzip body with its original headers.
- Watchlist round trip (create, list with best current offer, delete) passes
  against SQLite. History summary (lowest/median/latest/change) passes with two
  observations of the same item.
- `node scripts/ui_smoke.mjs` drives the real `web/app.js` in jsdom against the
  running API: cards render, CAD formatting is present, comparison opens,
  watchlist persists, and an FSA-only search shows the required error.

Not verified here:

- No Chromium/Android runtime is available in this sandbox, so layout and touch
  behaviour were not exercised on a real device or emulator. The jsdom smoke test
  validates behaviour, not visual rendering.
- Online (`ecom_items`) coverage depends on the provider returning them for a
  query; no retailer checkout or stock verification was performed.
- RFD remains unavailable until a permitted feed or browser-based collector is
  approved.

## 2026-11-11 — honesty, expiry, watchlist and filtering pass (Bosun)

Verified locally on Python 3.14.4 / Node 26.12.0 against the live Flipp endpoint:

- Full suite: 60 discovered, 57 passed, 3 skipped (PostgreSQL/Redis integrations,
  Android client absent). New `tests/test_honesty_fixes.py` covers 20 cases:
  multipack quantities ("6 x 100 g", "355 mL x 12", "1 kg (pack of 2)"),
  per-measure discount suppression, zero-price rejection, implausible-original
  rejection, date-only expiry in the Canadian site timezone, numeric (not
  lexicographic) best-offer ordering, target preservation on re-watch, history
  summaries excluding per-measure rows, deals filtering, retailer/price filters,
  price sorting, inverted range rejection, and watchlist cache/privacy headers.
- `scripts/validate_live.py --postal-code "N9A 6K9" --query chicken`:
  HTTP 200, Flipp `partial`, **74 items parsed, 70 offers returned**, 14 real
  retailer discounts, 0 zero/absurd prices, valid windows
  `2026-11-03T05:00Z → 2026-11-16T04:59Z`. Report retained at
  `validation/live-feeds.json`. RFD remains blocked: HTTPS preflight 406 and a
  202 JavaScript challenge, reported as `blocked`, never substituted.
- UI smoke (`node scripts/ui_smoke.mjs`, jsdom 26): **13/13 assertions** against
  a local uvicorn instance and live data — cards, CAD formatting, visible
  source-reported caveat, price notes, comparison, server-persisted watchlist,
  FSA rejection, failed-fetch error, and stale-comparison removal.

Fixes in this pass:

- No percentage discount is derived from a per-measure price against a package
  original; a "$0.00" sale price is rejected; a stated original more than 10x
  the sale price is excluded as a data error; `/v1/deals/top` additionally
  excludes per-measure rows, zero prices, >95% claims, and now applies its
  `period` window instead of echoing it.
- Date-only `valid_to`/`valid_from` resolve in `America/Toronto` (end of day)
  instead of becoming `None`, so an expired circular can no longer remain
  "active" for the whole cache window.
- Unit pricing understands trailing counts and "pack of N", so multipacks are
  not compared with single units at the wrong unit price.
- Watchlist `best_offer`/`target_hit` compare Decimals (the previous string sort
  ranked "$10.00" below "$9.50"); targets only match package-priced offers;
  re-watching without a target keeps the existing target; watchlist and history
  responses are `private, no-store`, the device id is no longer echoed, and
  GET/DELETE validate the id format.
- History summaries use one representative package price per check, exclude
  per-measure rows, report `checks`, and only support a historical-low claim
  with at least two checks.
- Search accepts `retailer`, `min_price`, `max_price` and
  `sort=relevance|price|unit_price|discount` (validated; inverted ranges are
  422). Category filtering is deliberately absent because Flipp's live payload
  contains no category field.
- Client: all price caveats are available per card, source/outage warnings are
  rendered, filtered counts reflect visible cards, failed searches clear the
  stale comparison and reset the result heading, watchlist writes are verified
  before the star/localStorage update (and reverted on failure), empty states
  distinguish "no results" from "start here", the comparison table is a
  keyboard-scrollable region with focus management, and the postal dialog's
  Cancel no longer risks discarding typed input.

Still not verified / not implemented (unchanged or newly documented):

- No account authentication. Watchlists remain device-id scoped (now with
  format validation and no-store caching); a leaked device id can read/delete
  that device's list. Account tokens are a prerequisite for real alerts.
- No notification delivery exists behind `target_price`/`target_hit`; state is
  recomputed on GET. README already lists notifications as roadmap.
- RFD is bot-blocked; no browser-based collector is used or approved.
- No retailer checkout, stock, or store-level availability verification.
  Flyer prices remain source-reported.
- Category data is unavailable from the supported feed, so no category filter.
- jsdom exercises behaviour, not layout or touch; no Chromium/device run.
- PostgreSQL/Redis paths remain skipped without test services; no CI workflow
  exists in this repository.
