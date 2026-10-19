import json
import unittest

import httpx
from pydantic import ValidationError

from scrapers.flipp_client import FlippClient, normalize_postal
from services.deal_normalizer import parse_and_price

try:
    from scrapers.rfd_client import enrich_post, parse_feed, safe_rfd_url
    RFD_DEPS_AVAILABLE = True
except ModuleNotFoundError as exc:
    if exc.name not in {"bs4", "defusedxml"}:
        raise
    RFD_DEPS_AVAILABLE = False


def facts(unit="g", quantity="100", packs=6, evidence=None):
    return json.dumps({"brand": None, "model": None, "product_name": "Product",
                       "pack_count": packs, "quantity_per_pack": quantity,
                       "quantity_unit": unit, "evidence": evidence or ["6 x 100g"],
                       "extraction_confidence": 0.95})


class NormalizerTests(unittest.TestCase):
    def test_multipack(self):
        result = parse_and_price(facts(), "Product 6 x 100g", "9.00")
        self.assertEqual(result["unit_price"], "1.5000")
        self.assertEqual(result["total_base_quantity"], "600")
        self.assertFalse(result["source_verified"])

    def test_pounds(self):
        result = parse_and_price(facts("lb", "1", 1, ["1 lb"]), "Chicken 1 lb", "4.5359237")
        self.assertEqual(result["unit_price"], "1.0000")

    def test_rolls(self):
        result = parse_and_price(facts("roll", "12", 1, ["12 rolls"]), "12 rolls mega", "6")
        self.assertEqual(result["unit_price"], "0.5000")

    def test_missing_evidence_blocks_quantity(self):
        result = parse_and_price(facts(), "Unknown pack", "9")
        self.assertIsNone(result["unit_price"])
        self.assertTrue(result["needs_review"])

    def test_partial_quantity_and_invalid_price(self):
        bad = json.loads(facts())
        bad["pack_count"] = None
        with self.assertRaises(ValidationError):
            parse_and_price(json.dumps(bad), "6 x 100g", "9")
        for price in ("NaN", "Infinity", "-1"):
            with self.assertRaises(ValueError):
                parse_and_price(facts(), "6 x 100g", price)

    def test_unknown_quantity(self):
        unknown = json.loads(facts())
        unknown.update(pack_count=None, quantity_per_pack=None, quantity_unit=None, evidence=[])
        result = parse_and_price(json.dumps(unknown), "Unknown package", "9")
        self.assertIsNone(result["unit_price"])


class SourceTests(unittest.TestCase):
    def test_postal(self):
        self.assertEqual(normalize_postal("n9v 1a1"), ("N9V1A1", "N9V"))
        self.assertEqual(normalize_postal("N8X"), ("N8X", "N8X"))
        for postal in ("90210", "D9V", "N9V000", ""):
            with self.assertRaises(ValueError): normalize_postal(postal)

    @unittest.skipUnless(RFD_DEPS_AVAILABLE, "Install beautifulsoup4 and defusedxml for RFD checks")
    def test_rss_missing_votes(self):
        posts = parse_feed('<rss><channel><item><title>[Store] Good deal</title><link>https://forums.redflagdeals.com/example-1/</link></item></channel></rss>')
        self.assertEqual(posts[0]["retailer_tag"], "Store")
        self.assertIsNone(posts[0]["score"])
        self.assertEqual(enrich_post(posts[0], '<div data-vote-score="+26"></div>')["score"], 26)
        self.assertIsNone(enrich_post(posts[0], '<div data-vote-score="votes"></div>')["score"])

    @unittest.skipUnless(RFD_DEPS_AVAILABLE, "Install beautifulsoup4 and defusedxml for RFD checks")
    def test_source_host(self):
        self.assertFalse(safe_rfd_url("https://forums.redflagdeals.com.evil.test/thread"))

    def test_unknown_price(self):
        item = FlippClient.map_item({"name": "Chicken", "current_price": "4.99/lb"})
        self.assertIsNone(item["regular_price_raw"])
        self.assertIsNone(item["source_url"])


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_scopes_and_ttl(self):
        class Cache:
            def __init__(self): self.entries = {}; self.ttls = []
            async def get(self, key): return self.entries.get(key)
            async def set(self, key, value, ex): self.entries[key] = value; self.ttls.append(ex)
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"items": [{"id": 1, "name": "Chicken"}]})
        cache = Cache()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = FlippClient(http, cache)
            await client.search("chicken", "N9V1A1")
            await client.search("chicken", "N9V1A1")
            await client.search("chicken", "N9V2A2")
        self.assertEqual(len(calls), 2)
        self.assertEqual(cache.ttls, [21600, 21600])


if __name__ == "__main__":
    unittest.main()
