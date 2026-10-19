from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from uuid import uuid4

import httpx

from backend.models import ComparisonGroup, SearchRequest, SearchResponse, SourceStatus
from backend.normalization import flyer_offer
from backend.repository import is_active
from scrapers.flipp_client import FlippClient
from scrapers.rfd_client import RFDClient
from services.query_parser import parse_query


def group_offers(offers):
    groups = {}
    for offer in offers:
        groups.setdefault(offer.comparison_group, []).append(offer)
    return [ComparisonGroup(key=key, unit_basis=group[0].unit_basis,
                            offers=sorted(group, key=lambda o: Decimal(o.unit_price) if o.unit_price is not None else Decimal("Infinity")))
            for key, group in sorted(groups.items())]


class Pipeline:
    def __init__(self, settings, http, cache, repository):
        self.settings, self.http, self.cache, self.repository = settings, http, cache, repository
        # A fixed shard count prevents unbounded locks from arbitrary queries.
        self.locks = [asyncio.Lock() for _ in range(32)]
        self.rfd_lock = asyncio.Lock()

    async def rfd(self):
        async with self.rfd_lock:
            key = "rfd:hot:v2"
            hit = await self.cache.get(key)
            if hit:
                import json
                return json.loads(hit)
            result = await RFDClient(self.http, self.settings.rfd_feed).trending(max_threads=self.settings.rfd_threads)
            if not result["errors"]:
                import json
                await self.cache.set(key, json.dumps(result), ex=300)
            return result

    async def search(self, request: SearchRequest):
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        key = f"search:v2:{request.postal_code}:{request.locale}:{digest}"
        lock = self.locks[int(digest[:4], 16) % len(self.locks)]
        async with lock:
            hit = await self.cache.get(key)
            if hit:
                result = SearchResponse.model_validate_json(hit)
                now = datetime.now(timezone.utc)
                offers = [o for g in result.groups for o in g.offers if is_active(o, now)]
                result.groups = group_offers(offers)
                result.cached = True
                return result
            result = await self.collect(request)
            self.repository.save(result)
            # Community votes have a shorter freshness window than circulars.
            # Flyer HTTP responses retain their separate six-hour adapter cache.
            ttl = 300 if any(s.status in ("ok", "partial") for s in result.sources) else 15
            await self.cache.set(key, result.model_dump_json(), ex=ttl)
            return result

    async def collect(self, request):
        observed = datetime.now(timezone.utc)
        parsed_query = parse_query(request.query)
        async def fetch(source):
            enabled = self.settings.flipp_enabled if source == "flipp" else self.settings.rfd_enabled
            if not enabled:
                return SourceStatus(source=source, status="disabled"), None
            try:
                if source == "flipp":
                    task = FlippClient(self.http, self.cache, endpoint=self.settings.flipp_endpoint,
                                       locale=request.locale).search(parsed_query["product_query"], request.postal_code)
                else:
                    task = self.rfd()
                data = await asyncio.wait_for(task, timeout=self.settings.source_timeout)
                partial = source == "rfd" and bool(data["errors"] or data.get("unclassified_count") or data["unscored"])
                return SourceStatus(source=source, status="partial" if partial else "ok",
                                    checked_at=observed, message="Some thread pages, Hot Deals categories, or vote scores could not be confirmed" if partial else None), data
            except (httpx.HTTPError, ValueError, TimeoutError) as exc:
                code = (f"http_{exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
                        else "timeout" if isinstance(exc, TimeoutError)
                        else "schema_changed" if isinstance(exc, ValueError)
                        else "connection_failed")
                return SourceStatus(source=source, status="error", checked_at=observed, error_code=code), None
        results = await asyncio.gather(fetch("flipp"), fetch("rfd"))
        offers, community, sources = [], [], []
        for status, data in results:
            if data and status.source == "flipp":
                for raw in data["items"]:
                    fetched = datetime.fromisoformat(data["fetched_at"])
                    offer = flyer_offer(raw, request.postal_code, fetched)
                    if offer and is_active(offer, observed):
                        offers.append(offer)
                    else:
                        status.rejected_count += 1
                status.item_count = len(offers)
                if status.rejected_count:
                    status.status = "partial"
                    status.message = "Ambiguous prices, missing stores, or inactive offers excluded"
            elif data:
                words = parsed_query["product_query"].casefold().split()
                for post in data["deals"] + data["unscored"]:
                    if all(word in post["title"].casefold() for word in words):
                        community.append(post)
                status.item_count = len(community)
            sources.append(status)
        warnings = ["Flyer prices are source-reported, not independently checkout-verified."] if offers else []
        if len(request.postal_code) == 3:
            warnings.append("FSA coverage is approximate; choose a full postal code and store before purchasing.")
        if any(s.status in ("error", "partial", "disabled") for s in sources):
            warnings.append("Some sources are unavailable or incomplete. Results do not represent all Canadian retailers.")
        if community:
            warnings.append("Community vote counts and links do not verify price, expiry, or local availability.")
        if parsed_query["location_text"]:
            warnings.append(f'Location text "{parsed_query["location_text"]}" was not geocoded; the selected postal code controls results.')
        return SearchResponse(search_id=uuid4(), query=request.query, postal_code=request.postal_code,
                              fsa=request.postal_code[:3], approximate_location=len(request.postal_code) == 3,
                              groups=group_offers(list({str(o.id): o for o in offers}.values())),
                              community=community, sources=sources, warnings=warnings, completed_at=observed)
