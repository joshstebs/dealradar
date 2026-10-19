"""Conservative intent cleanup; keep brand, model, size, and package constraints."""
import re


def parse_query(query: str):
    location = re.search(r"\s+near\s+(.+)$", query, re.I)
    product = query[:location.start()] if location else query
    product = re.sub(r"^(?:find\s+)?(?:cheap(?:est)?|best price (?:on|for)|deals? (?:on|for))\s+", "", product, flags=re.I)
    product = " ".join(product.split())
    return {"product_query": product or query,
            "location_text": location[1] if location else None,
            "location_policy": "explicit_postal_code_controls_coverage"}
