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
