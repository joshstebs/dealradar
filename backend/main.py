from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import statistics
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx

from backend.cache import ResilientCache
from backend.config import Settings
from backend.models import BasketRequest, Offer, SearchRequest, SearchResponse, SourceStatus, WatchRequest
from backend.pipeline import Pipeline, group_offers
from backend.repository import Repository, is_active, is_recent
from scrapers.flipp_client import normalize_postal


def summarize_points(points):
    """Price summary over comparable package observations.

    Per-measure ($/lb, $/kg) rows are excluded from the numeric summary: mixing
    them with package prices produces a "lowest" that no shopper can pay. Each
    observation time contributes its cheapest store as one check, so "latest"
    and "change" describe a check, not an arbitrary store row.
    """
    comparable = [p for p in points if p.get("price_basis", "package") == "package"
                  and Decimal(p["sale_price"]) > 0]
    empty = {"lowest": None, "highest": None, "median": None, "latest": None,
             "previous": None, "change_percent": None, "observations": 0, "checks": 0}
    if not comparable:
        return empty
    by_time = {}
    for point in comparable:
        by_time.setdefault(point["observed_at"], []).append(Decimal(point["sale_price"]))
    series = [min(by_time[stamp]) for stamp in sorted(by_time)]
    latest = series[-1]
    previous = series[-2] if len(series) > 1 else None
    change = None
    if previous is not None and previous > 0:
        change = str(((latest - previous) / previous * 100).quantize(Decimal("0.1")))
    return {"lowest": str(min(series)), "highest": str(max(series)),
            "median": str(statistics.median(series)), "latest": str(latest),
            "previous": str(previous) if previous is not None else None,
            "change_percent": change, "observations": len(comparable),
            "checks": len(series)}


def location(value):
    try:
        return normalize_postal(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def create_app(settings: Settings | None = None, transport=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        repository = Repository(settings.database_url)
        cache = ResilientCache(settings.redis_url)
        async with httpx.AsyncClient(transport=transport, timeout=15,
                                    headers={"User-Agent": "DealRadarCA/0.2 (+source attribution in app)"},
                                    follow_redirects=False,
                                    limits=httpx.Limits(max_connections=10)) as http:
            app.state.repository = repository
            app.state.pipeline = Pipeline(settings, http, cache, repository)
            app.state.search_gate = asyncio.Semaphore(8)
            try:
                yield
            finally:
                await cache.close()
                repository.close()

    app = FastAPI(title="DealRadar CA", version="0.2.0", lifespan=lifespan,
                  description="Canadian localized source-reported offers. Verification and coverage are explicit.")
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "dealradar-ca", "version": "0.2.0"}

    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/v1/locations/resolve")
    async def resolve(postal_code: str):
        postal, fsa = location(postal_code)
        return {"postal_code": postal, "fsa": fsa, "approximate": len(postal) == 3,
                "coverage_verified": False, "store_selection_required": True}

    @app.post("/v1/search", response_model=SearchResponse, responses={503: {"description": "All enabled feeds failed"}})
    async def search(request: SearchRequest):
        if len(request.postal_code) == 3:
            raise HTTPException(422, "A full postal code is required: flyer coverage and prices differ within one FSA.")
        try:
            await asyncio.wait_for(app.state.search_gate.acquire(), timeout=0.5)
        except TimeoutError:
            raise HTTPException(429, "Search capacity reached; retry shortly", headers={"Retry-After": "2"})
        try:
            result = await app.state.pipeline.search(request)
        finally:
            app.state.search_gate.release()
        if not any(source.status in ("ok", "partial") for source in result.sources):
            raise HTTPException(503, {"message": "No sources available", "search_id": str(result.search_id),
                                      "sources": [s.model_dump(mode="json") for s in result.sources]}, headers={"Retry-After": "15"})
        return result

    @app.get("/v1/searches/{search_id}", response_model=SearchResponse)
    async def search_result(search_id: UUID):
        result = app.state.repository.search(str(search_id))
        if result is None:
            raise HTTPException(404, "Search not found")
        now = datetime.now(timezone.utc)
        result.groups = group_offers([o for group in result.groups for o in group.offers if is_active(o, now)])
        return result

    @app.get("/v1/offers/{offer_id}", response_model=Offer)
    async def offer(offer_id: UUID):
        result = app.state.repository.offer(str(offer_id))
        if result is None:
            raise HTTPException(404, "Offer not found")
        if not is_active(result, datetime.now(timezone.utc)):
            raise HTTPException(410, "Offer expired or stale; search again")
        return result

    @app.get("/v1/items/{item_id}/offers")
    async def item_offers(item_id: UUID, postal_code: str):
        postal, fsa = location(postal_code)
        offers = [o for o in app.state.repository.offers(fsa, postal) if o.item_id == item_id]
        return {"groups": group_offers(offers), "postal_code": postal}

    @app.get("/v1/items/{item_id}/history")
    async def history(item_id: UUID, response: Response, postal_code: str, days: int = Query(90, ge=1, le=365)):
        response.headers["Cache-Control"] = "private, no-store"
        postal, fsa = location(postal_code)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        points = app.state.repository.item_points(str(item_id), fsa, postal, since)
        observations = [{"observed_at": p["observed_at"], "sale_price": p["sale_price"],
                         "unit_price": p["unit_price"], "unit_basis": p["unit_basis"],
                         "store": p["store"], "source": p["source"]} for p in points]
        summary = summarize_points(points)
        return {"item_id": str(item_id), "postal_code": postal, "days": days,
                "observations": observations, "summary": summary,
                "historical_low_claim_supported": summary["checks"] >= 2,
                "note": ("History is built from observed package prices, not manufacturer MSRP. "
                         "Per-measure prices are listed but excluded from the summary because their "
                         "final cost depends on weight or quantity.")}

    @app.get("/v1/sources/status")
    async def statuses(postal_code: str):
        postal, fsa = location(postal_code)
        sources = app.state.repository.statuses(postal)
        if sources is None:
            sources = [SourceStatus(source=name, status="not_checked").model_dump(mode="json") for name in ("flipp", "rfd")]
        return {"postal_code": postal, "fsa": fsa, "sources": sources}

    @app.get("/v1/deals/top")
    async def top(postal_code: str, period: str = Query("day", pattern="^(day|week)$"),
                  min_discount: int = Query(20, ge=1, le=99)):
        """Source-reported deals, explicitly not checkout-verified.

        The old endpoint required verification flags no adapter can set, so it
        always returned an empty list. This returns real observed discounts with
        their provenance instead of pretending they are verified steals.
        """
        postal, fsa = location(postal_code)
        now = datetime.now(timezone.utc)
        # "day" and "week" are observation windows, not flyer validity claims.
        window = timedelta(hours=24) if period == "day" else timedelta(days=7)
        candidates = []
        for offer in app.state.repository.offers(fsa, postal):
            if offer.regular_price is None or offer.reference_price_kind != "retailer":
                continue
            # A percentage only makes sense for a package price against a
            # package original. Per-unit prices have a different basis.
            if offer.price_basis != "package" or not is_recent(offer.observed_at, now, int(window.total_seconds())):
                continue
            reference, price = Decimal(offer.regular_price), Decimal(offer.sale_price)
            if reference <= 0 or price <= 0 or reference <= price:
                continue
            percent = (reference - price) / reference * 100
            # Exclude implausible data errors rather than advertising them.
            if percent > 95 or percent < Decimal(min_discount):
                continue
            candidates.append((percent, offer))
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        return {"offers": [offer for _, offer in candidates[:20]], "period": period,
                "postal_code": postal, "verification": "source_reported",
                "message": None if candidates else "No source-reported deals match this postal code yet",
                "note": "Discounts compare the retailer's own stated original price; they are not checkout-verified."}

    @app.post("/v1/basket/quote")
    async def basket(request: BasketRequest):
        total = Decimal(0)
        lines, missing = [], []
        now = datetime.now(timezone.utc)
        for line in request.items:
            offer = app.state.repository.offer(str(line.offer_id))
            if not offer or offer.postal_scope != request.postal_code or offer.price_basis != "package" or not is_active(offer, now):
                missing.append(str(line.offer_id))
                continue
            subtotal = Decimal(offer.sale_price) * line.quantity
            total += subtotal
            lines.append({"offer_id": str(offer.id), "store": offer.store,
                          "quantity": line.quantity, "subtotal": str(subtotal),
                          "conditions": offer.conditions})
        return {"currency": "CAD", "merchandise_total": str(total), "lines": lines,
                "missing_offer_ids": missing, "complete": not missing,
                "checkout_verified": False, "excludes": ["tax", "shipping", "deposits", "membership fees"]}

    @app.post("/v1/watchlist")
    async def watch(payload: WatchRequest):
        app.state.repository.watch(payload.device_id, str(payload.item_id), payload.target_price)
        return {"watching": True, "item_id": str(payload.item_id)}

    @app.delete("/v1/watchlist/{item_id}")
    async def unwatch(item_id: UUID, device_id: str = Query(min_length=8, max_length=64,
                                                            pattern=r"^[A-Za-z0-9._-]+$")):
        app.state.repository.unwatch(device_id, str(item_id))
        return {"watching": False, "item_id": str(item_id)}

    @app.get("/v1/watchlist")
    async def watchlist(response: Response, device_id: str = Query(min_length=8, max_length=64,
                                                                   pattern=r"^[A-Za-z0-9._-]+$"),
                        postal_code: str = Query()):
        # Device-scoped data must not be cached by a browser or intermediary,
        # and the device identifier is not repeated back in the body.
        response.headers["Cache-Control"] = "private, no-store"
        postal, fsa = location(postal_code)
        items = []
        now = datetime.now(timezone.utc)
        for entry in app.state.repository.watched(device_id):
            latest = app.state.repository.latest_offers(entry["item_id"], fsa, postal)
            points = app.state.repository.item_points(entry["item_id"], fsa, postal,
                                                      now - timedelta(days=90))
            summary = summarize_points(points)
            target = Decimal(entry["target_price"]) if entry["target_price"] else None
            best = latest[0] if latest else None
            # A target is a package price the user chose to pay. Comparing it to
            # a $/lb offer would report a hit the shopper cannot actually claim.
            hit_target = bool(best and target is not None and best.price_basis == "package"
                              and Decimal(best.sale_price) <= target)
            items.append({**entry, "offers": latest, "summary": summary,
                          "best_offer": best, "target_hit": hit_target})
        return {"postal_code": postal, "items": items}

    @app.get("/")
    async def index():
        page = Path(__file__).resolve().parent.parent / "web" / "index.html"
        if not page.exists():
            raise HTTPException(404, "Web client not built")
        return FileResponse(page)

    @app.websocket("/v1/searches/{search_id}/events")
    async def events(websocket: WebSocket, search_id: UUID):
        # Public search data only. Do not reuse this channel for private lists.
        result = app.state.repository.search(str(search_id))
        if result is None:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            # Completed snapshot replay. Search POST currently performs bounded
            # orchestration, not an unimplemented asynchronous job queue.
            for source in result.sources:
                await websocket.send_json({"type": "source_completed", "source": source.model_dump(mode="json")})
            await websocket.send_json({"type": "search_finished", "search_id": str(search_id)})
            await websocket.close()
        except WebSocketDisconnect:
            pass

    return app


app = create_app()
