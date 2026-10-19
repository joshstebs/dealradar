from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import httpx

from backend.cache import ResilientCache
from backend.config import Settings
from backend.models import BasketRequest, Offer, SearchRequest, SearchResponse, SourceStatus
from backend.pipeline import Pipeline, group_offers
from backend.repository import Repository, is_active
from scrapers.flipp_client import normalize_postal


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

    @app.get("/v1/locations/resolve")
    async def resolve(postal_code: str):
        postal, fsa = location(postal_code)
        return {"postal_code": postal, "fsa": fsa, "approximate": len(postal) == 3,
                "coverage_verified": False, "store_selection_required": True}

    @app.post("/v1/search", response_model=SearchResponse, responses={503: {"description": "All enabled feeds failed"}})
    async def search(request: SearchRequest):
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
    async def history(item_id: UUID, postal_code: str, days: int = Query(90, ge=1, le=365)):
        postal, fsa = location(postal_code)
        offers = app.state.repository.history(str(item_id), fsa, postal,
                                              datetime.now(timezone.utc) - timedelta(days=days))
        return {"item_id": str(item_id), "postal_code": postal, "days": days,
                "observations": [{"observed_at": o.observed_at, "sale_price": o.sale_price,
                                  "unit_price": o.unit_price, "store": o.store,
                                  "source_url": o.source_url, "verified": o.price_verified} for o in offers],
                "historical_low_claim_supported": False}

    @app.get("/v1/sources/status")
    async def statuses(postal_code: str):
        postal, fsa = location(postal_code)
        sources = app.state.repository.statuses(postal)
        if sources is None:
            sources = [SourceStatus(source=name, status="not_checked").model_dump(mode="json") for name in ("flipp", "rfd")]
        return {"postal_code": postal, "fsa": fsa, "sources": sources}

    @app.get("/v1/deals/top")
    async def top(postal_code: str, period: str = Query("day", pattern="^(day|week)$")):
        postal, fsa = location(postal_code)
        now = datetime.now(timezone.utc)
        candidates = []
        for offer in app.state.repository.offers(fsa, postal):
            if not (offer.price_verified and offer.coverage_verified and offer.verified_at and offer.valid_to
                    and (now - offer.verified_at).total_seconds() <= settings.verification_max_age_seconds):
                continue
            if offer.regular_price is None or offer.reference_price_kind is None:
                continue
            reference, price = Decimal(offer.regular_price), Decimal(offer.sale_price)
            if reference > 0 and (reference - price) / reference >= Decimal("0.35"):
                candidates.append(offer)
        candidates.sort(key=lambda o: (Decimal(o.regular_price) - Decimal(o.sale_price)) / Decimal(o.regular_price), reverse=True)
        return {"offers": candidates[:10], "period": period, "postal_code": postal,
                "message": "No independently verified local steals yet" if not candidates else None}

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
