from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from scrapers.flipp_client import normalize_postal


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=250)
    postal_code: str = Field(min_length=3, max_length=7)
    locale: Literal["en-ca", "fr-ca"] = "en-ca"

    @field_validator("query")
    @classmethod
    def clean_query(cls, value):
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Query is empty")
        return cleaned

    @field_validator("postal_code")
    @classmethod
    def postal(cls, value):
        return normalize_postal(value)[0]


class SourceStatus(BaseModel):
    source: str
    status: Literal["ok", "partial", "error", "disabled", "not_checked"]
    checked_at: datetime | None = None
    error_code: str | None = None
    item_count: int = 0
    rejected_count: int = 0
    message: str | None = None


class Offer(BaseModel):
    id: UUID
    item_id: UUID
    product_name: str
    store: str
    fsa: str
    postal_scope: str
    source: str
    source_url: str | None = None
    currency: Literal["CAD"] = "CAD"
    sale_price: str
    price_basis: Literal["package", "g", "kg", "lb", "ml", "l", "roll", "unit"] = "package"
    regular_price: str | None = None
    unit_price: str | None = None
    unit_basis: str | None = None
    comparison_group: str
    conditions: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    observed_at: datetime
    verified_at: datetime | None = None
    price_verified: bool = False
    coverage_verified: bool = False
    reference_price_kind: str | None = None


class ComparisonGroup(BaseModel):
    key: str
    unit_basis: str | None
    offers: list[Offer]


class SearchResponse(BaseModel):
    search_id: UUID
    query: str
    postal_code: str
    fsa: str
    approximate_location: bool
    groups: list[ComparisonGroup]
    community: list[dict] = Field(default_factory=list)
    sources: list[SourceStatus]
    warnings: list[str] = Field(default_factory=list)
    cached: bool = False
    completed_at: datetime


class BasketLine(BaseModel):
    offer_id: UUID
    quantity: int = Field(ge=1, le=100)


class BasketRequest(BaseModel):
    postal_code: str
    items: list[BasketLine] = Field(min_length=1, max_length=100)

    @field_validator("postal_code")
    @classmethod
    def postal(cls, value):
        return normalize_postal(value)[0]
