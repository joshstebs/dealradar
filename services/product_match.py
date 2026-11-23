"""Deterministic product identity for cross-retailer comparison.

The goal is conservative matching: two listings only join the same comparison
group when their brand, product name and declared size agree. Model numbers and
SKUs win over fuzzy names when present. Nothing here invents a brand or size.
"""
from __future__ import annotations

import re
import unicodedata

from services.deal_normalizer import CONVERSIONS

SIZE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(kg|g|mg|lb|lbs|ml|l|oz|fl\s*oz|rolls?|units?|ct|count|sheets?|pods?|capsules?)\b",
    re.I,
)
MULTI = re.compile(
    r"\b(\d+)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)\s*(kg|g|mg|lb|ml|l|oz|fl\s*oz|rolls?|units?|ct|count)\b",
    re.I,
)
MODEL = re.compile(r"\b(?:model|mod\.?|sku|upc|gtin|item)\s*#?\s*:?\s*([A-Z0-9][A-Z0-9._/-]{2,24})\b", re.I)
CODE = re.compile(r"\b([A-Z]{1,4}[-_]?\d{3,}[A-Z0-9-]*)\b")

FLUFF = {
    "new", "fresh", "best", "value", "family", "pack", "size", "assorted",
    "premium", "classic", "original", "great", "deal", "save", "savings",
    "limited", "edition", "bonus", "free", "with", "and", "the", "of", "for",
    "your", "our", "ea", "each", "pkg", "package", "bag", "box", "bottle",
}

UNIT_ALIASES = {
    "lbs": "lb", "lb": "lb", "oz": "oz", "fl oz": "floz", "floz": "floz",
    "roll": "roll", "rolls": "roll", "unit": "unit", "units": "unit",
    "ct": "unit", "count": "unit", "sheet": "sheet", "sheets": "sheet",
    "pod": "pod", "pods": "pod", "capsule": "capsule", "capsules": "capsule",
    "mg": "mg", "g": "g", "kg": "kg", "ml": "ml", "l": "l",
}


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def extract_size(title: str) -> tuple[str | None, str | None]:
    """Return (declared size, base unit) without converting package prices."""
    text = str(title or "")
    multi = MULTI.search(text)
    if multi:
        count, qty, unit = int(multi.group(1)), multi.group(2), multi.group(3).lower()
        if 0 < count <= 1000:
            return f"{count}x{qty}{unit}", UNIT_ALIASES.get(unit, unit)
    match = SIZE.search(text)
    if not match:
        return None, None
    qty, unit = match.group(1), match.group(2).lower().replace(" ", "")
    unit = UNIT_ALIASES.get(unit, unit)
    if unit not in CONVERSIONS:
        return f"{qty}{unit}", unit
    return f"{qty}{unit}", unit


def extract_model(title: str, sku: str | None = None) -> str | None:
    if sku:
        cleaned = re.sub(r"[^A-Za-z0-9._/-]", "", str(sku))
        if len(cleaned) >= 4:
            return cleaned.upper()
    text = str(title or "")
    tagged = MODEL.search(text)
    if tagged:
        return tagged.group(1).upper()
    code = CODE.search(text)
    return code.group(1).upper() if code else None


def product_name(title: str) -> str:
    """Strip size, model tags and marketing fluff, keeping meaningful words."""
    text = str(title or "")
    text = MULTI.sub(" ", text)
    text = SIZE.sub(" ", text)
    text = MODEL.sub(" ", text)
    words = [w for w in normalize_text(text).split() if w not in FLUFF and len(w) > 1]
    return " ".join(words)


def canonical_product(title: str, brand: str | None = None,
                      sku: str | None = None) -> dict:
    size, unit = extract_size(title)
    model = extract_model(title, sku)
    name = product_name(title)
    clean_brand = normalize_text(brand).replace(" ", "") if brand else ""
    if model:
        # A real model/SKU identifies the item more tightly than prose.
        key = f"model:{model.lower()}"
    elif clean_brand and name and size:
        key = f"{clean_brand}|{name}|{size}"
    elif name and size:
        key = f"{name}|{size}"
    elif name:
        key = f"{name}|unknown"
    else:
        key = "unknown"
    return {"key": key, "name": name, "brand": clean_brand or None,
            "size": size, "unit": unit, "model": model}


def is_same_product(left: dict, right: dict) -> bool:
    """True only when two parsed products are safe to compare."""
    if left.get("model") and right.get("model"):
        return left["model"] == right["model"]
    if left.get("size") != right.get("size"):
        return False
    if left.get("brand") and right.get("brand") and left["brand"] != right["brand"]:
        return False
    if not left.get("name") or not right.get("name"):
        return False
    return left["name"] == right["name"]
