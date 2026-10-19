"""Experimental public-endpoint adapter; endpoint/schema require live validation.

No authentication bypass. Configure a licensed endpoint for production.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from scrapers.http_utils import fetch

POSTAL = re.compile(r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z](?:\d[ABCEGHJ-NPRSTV-Z]\d)?$")


def normalize_postal(value: str) -> tuple[str, str]:
    compact = value.upper().replace(" ", "").strip()
    if not POSTAL.fullmatch(compact):
        raise ValueError("Enter a Canadian FSA or full postal code")
    return compact, compact[:3]


class FlippClient:
    def __init__(self, http: httpx.AsyncClient, cache: Any = None,
                 endpoint: str = "https://backflipp.wishabi.com/flipp/items/search",
                 locale: str = "en-ca", region_variant: str | None = None):
        self.http, self.cache = http, cache
        self.endpoint, self.locale = endpoint, locale
        # Only use FSA-wide caching after validating identical circular coverage.
        self.region_variant = region_variant

    async def search(self, query: str, postal_code: str) -> dict:
        postal, fsa = normalize_postal(postal_code)
        query = " ".join(query.split())
        if not query or len(query) > 250:
            raise ValueError("Query must contain 1–250 characters")
        scope = self.region_variant or postal
        digest = hashlib.sha256(query.casefold().encode()).hexdigest()[:24]
        key = f"flyer:v1:flipp:{fsa}:{scope}:{self.locale}:{digest}"
        if self.cache:
            hit = await self.cache.get(key)
            if hit:
                return json.loads(hit)
        # FSA is accepted as input, but provider support must be checked. Never
        # invent a full postal code by appending an arbitrary suffix.
        response = await fetch(self.http, self.endpoint, params={
            "q": query, "postal_code": postal, "locale": self.locale,
        })
        response.raise_for_status()  # Caller applies capped retry/backoff on 429/5xx.
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("Flipp schema changed: expected an items array")
        items = [self.map_item(item) for item in payload["items"]]
        result = {"source": "flipp", "fsa": fsa, "coverage_verified": False,
                  "items": items, "fetched_at": datetime.now(timezone.utc).isoformat()}
        if self.cache:
            await self.cache.set(key, json.dumps(result), ex=21600)
        return result

    @staticmethod
    def map_item(raw: dict) -> dict:
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ValueError("Flipp item lacks a product name")
        # Preserve price strings for currency-aware parsing downstream. Do not
        # infer regular prices, discounts, dates, or clipping URLs from IDs.
        return {"source_id": str(raw.get("id", "")), "title": raw["name"],
                "store": raw.get("merchant_name"), "sale_price_raw": raw.get("current_price"),
                "regular_price_raw": raw.get("original_price"),
                "valid_from": raw.get("valid_from"), "valid_to": raw.get("valid_to"),
                "image_url": raw.get("image_url"), "flyer_id": raw.get("flyer_id"),
                "polygon": raw.get("polygon"), "source_url": raw.get("url"),
                "raw": raw}
