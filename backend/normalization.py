from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.models import Offer
from services.deal_normalizer import CONVERSIONS
from services.product_match import canonical_product


def cad_price(raw):
    """Only an explicit simple CAD merchandise price; bundles need review."""
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip()
    match = re.fullmatch(r"(?:CAD\s*|CA\$\s*|\$\s*)?(\d+(?:\.\d{1,4})?)", text)
    if not match:
        return None
    try:
        value = Decimal(match[1])
        return value if value.is_finite() and 0 <= value <= 1000000 else None
    except InvalidOperation:
        return None


def safe_https(raw):
    try:
        parts = urlsplit(str(raw or ""))
        return str(raw) if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password and parts.port in (None, 443) else None
    except ValueError:
        return None


def parse_date(raw, end=False, timezone_name=None):
    if not raw:
        return None
    try:
        text = str(raw)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Date-only provider data requires an explicit circular timezone.
            if not timezone_name:
                return None
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
            if len(text) == 10 and end:
                parsed += timedelta(days=1)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return None


def explicit_quantity(title):
    multi = re.search(r"\b(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(kg|g|lb|ml|l)\b", title, re.I)
    single = re.search(r"\b(\d+(?:\.\d+)?)\s*(kg|g|lb|ml|l|rolls?|units?)\b", title, re.I)
    match = multi or single
    if not match:
        return None, None, title.casefold().strip()
    if multi:
        count, quantity, unit = int(multi[1]), Decimal(multi[2]), multi[3].lower()
    else:
        count, quantity, unit = 1, Decimal(single[1]), single[2].lower().rstrip("s")
    if count <= 0 or count > 10000 or quantity <= 0 or quantity > 1000000:
        return None, None, title.casefold().strip()
    factor, basis, _ = CONVERSIONS[unit]
    # Only package amount is removed. Brand/model/other variant words remain.
    name = " ".join((title[:match.start()] + title[match.end():]).casefold().split())
    return Decimal(count) * quantity * factor, basis, name or title.casefold()


def _discount(price: Decimal, regular: Decimal | None) -> tuple[Decimal | None, str | None]:
    """A discount exists only against a genuine higher retailer reference."""
    if regular is None or regular <= price or regular <= 0:
        return None, None
    percent = ((regular - price) / regular * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return regular, str(percent)


def flyer_offer(item: dict, postal: str, observed: datetime):
    price_text = str(item.get("sale_price_raw") or "").strip()
    per_measure = re.fullmatch(r"(.+?)\s*/\s*(kg|g|lb|ml|l|roll|unit)", price_text, re.I)
    price = cad_price(per_measure[1] if per_measure else item.get("sale_price_raw"))
    title = str(item.get("title") or "").strip()
    store = str(item.get("store") or "").strip()
    if price is None or not title or not store:
        return None
    kind = str(item.get("kind") or "flyer")
    source = "flipp" if kind == "flyer" else "flipp-online"
    total, basis, legacy_name = explicit_quantity(title)
    price_basis = per_measure[2].lower() if per_measure else "package"
    if per_measure and price_basis in CONVERSIONS:
        factor, basis, _ = CONVERSIONS[price_basis]
        total = factor  # The source price explicitly buys one declared measure.
    elif per_measure:
        price_basis = "package"
    multiplier = Decimal(100) if basis in ("100g", "100ml") else Decimal(1)
    unit_price = str((price * multiplier / total).quantize(Decimal("0.01"))) if total else None
    identity = canonical_product(title, item.get("brand"), item.get("sku"))
    group_key = identity["key"]
    if group_key.endswith("|unknown"):
        # Without a declared size or model there is no defensible claim that
        # two stores sell the identical item; keep the listing store-scoped.
        group_key = f"{group_key}|{store.casefold()}"
    regular, discount = _discount(price, cad_price(item.get("regular_price_raw")))
    name = identity["name"] or legacy_name
    item_id = uuid5(NAMESPACE_URL, f"dealradar:item:{identity['key']}")
    identity_key = item.get("source_id") or f"{store}|{title}|{item.get('flyer_id') or ''}"
    offer_id = uuid5(NAMESPACE_URL, f"dealradar:flipp:{identity_key}:{store}:{postal}")
    raw = item.get("raw") or {}
    # No inferred province-wide timezone or assumed branch applicability.
    tz = raw.get("timezone")
    valid_from = parse_date(item.get("valid_from"), timezone_name=tz)
    valid_to = parse_date(item.get("valid_to"), end=True, timezone_name=tz)
    if valid_from and valid_to and valid_to <= valid_from:
        return None
    conditions = ["Retailer price and local store applicability not independently verified"]
    if per_measure:
        conditions.append(f"Source price is per {price_basis}; final package cost depends on actual weight or quantity")
    if item.get("valid_to") and valid_to is None:
        conditions.append("Source expiry could not be resolved without circular timezone")
    if item.get("regular_price_raw") and regular is None:
        conditions.append("Stated original price was not higher than the sale price and was excluded")
    if regular is not None:
        conditions.append("Original price is retailer-reported; discount is not independently verified")
    if kind != "flyer":
        conditions.append("Online listing: availability and shipping can change after the observation time")
    return Offer(id=offer_id, item_id=item_id, product_name=title, store=store,
                 fsa=postal[:3], postal_scope=postal, source=source,
                 source_url=safe_https(item.get("source_url")),
                 image_url=safe_https(item.get("image_url")),
                 brand=str(item.get("brand")) if item.get("brand") else None,
                 sku=str(item.get("sku")) if item.get("sku") else None,
                 discount_percent=discount,
                 sale_price=str(price),
                 price_basis=price_basis,
                 regular_price=str(regular) if regular is not None else None,
                 reference_price_kind="retailer" if regular is not None else None,
                 unit_price=unit_price, unit_basis=basis,
                 comparison_group=group_key, observed_at=observed,
                 valid_from=valid_from, valid_to=valid_to, conditions=conditions)
