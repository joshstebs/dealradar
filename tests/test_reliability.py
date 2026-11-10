import gzip
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from backend.main import create_app
from backend.config import Settings
from backend.models import Offer
from backend.normalization import flyer_offer
from backend.repository import is_active
from scrapers.flipp_client import FlippClient
from scrapers.http_utils import fetch
from services.product_match import canonical_product, is_same_product


def flyer(title="Kraft Peanut Butter 1 kg", price="8.99", regular="11.99"):
    return {"id": 7, "name": title, "merchant_name": "Test grocer", "store": "Test grocer",
            "current_price": price, "original_price": regular,
            "valid_from": "2020-01-01T00:00:00Z", "valid_to": "2099-01-01T00:00:00Z"}


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_encoded_body_is_decoded_once(self):
        body = gzip.compress(b'{"items": []}')

        def handler(request):
            return httpx.Response(200, content=body, headers={
                "content-encoding": "gzip", "content-type": "application/json",
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            response = await fetch(http, "https://example.test/feed")
        self.assertEqual(response.json(), {"items": []})

    async def test_cross_host_redirect_is_refused(self):
        def handler(request):
            return httpx.Response(301, headers={"location": "https://evil.test/x"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with self.assertRaises(ValueError):
                await fetch(http, "https://example.test/feed")

    async def test_flipp_requires_a_full_postal_code(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(422))) as http:
            with self.assertRaisesRegex(ValueError, "full postal code"):
                await FlippClient(http).search("milk", "N9V")


class PriceIntegrityTests(unittest.TestCase):
    def test_regular_price_below_sale_is_dropped(self):
        offer = flyer_offer(FlippClient.map_item(flyer(regular="7.99")), "N9A6K9", datetime.now(timezone.utc))
        self.assertIsNone(offer.regular_price)
        self.assertIsNone(offer.discount_percent)
        self.assertTrue(any("excluded" in c for c in offer.conditions))

    def test_discount_uses_the_stated_original(self):
        offer = flyer_offer(FlippClient.map_item(flyer(price="8.99", regular="11.99")), "N9A6K9", datetime.now(timezone.utc))
        self.assertEqual(offer.regular_price, "11.99")
        self.assertEqual(offer.discount_percent, "25.0")

    def test_price_is_always_cad(self):
        offer = flyer_offer(FlippClient.map_item(flyer(price="US$8.99")), "N9A6K9", datetime.now(timezone.utc))
        self.assertIsNone(offer, "a non-CAD price must never be published as CAD")

    def test_stale_observation_is_not_active(self):
        offer = Offer(id="00000000-0000-4000-8000-000000000001",
                      item_id="00000000-0000-4000-8000-000000000002",
                      product_name="Milk 2 L", store="Test", fsa="N9A", postal_scope="N9A6K9",
                      source="flipp", sale_price="3.99",
                      comparison_group="milk|2l",
                      observed_at=datetime.now(timezone.utc) - timedelta(hours=7))
        self.assertFalse(is_active(offer, datetime.now(timezone.utc)))
        fresh = offer.model_copy(update={"observed_at": datetime.now(timezone.utc)})
        self.assertTrue(is_active(fresh, datetime.now(timezone.utc)))


class MatchingTests(unittest.TestCase):
    def test_same_brand_name_size_matches(self):
        left = canonical_product("Kraft Smooth Peanut Butter 1 kg")
        right = canonical_product("KRAFT smooth peanut butter, 1 kg")
        self.assertTrue(is_same_product(left, right))

    def test_different_size_does_not_match(self):
        left = canonical_product("Kraft Peanut Butter 1 kg")
        right = canonical_product("Kraft Peanut Butter 750 g")
        self.assertFalse(is_same_product(left, right))

    def test_model_number_wins(self):
        left = canonical_product("Dyson V15 Vacuum", sku="SV47")
        right = canonical_product("Dyson stick vacuum", sku="SV47")
        self.assertTrue(is_same_product(left, right))


class ReliabilityAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.price = "8.99"
        self.directory = tempfile.TemporaryDirectory()

        def handler(request):
            if request.url.host == "backflipp.wishabi.com":
                return httpx.Response(200, json={"items": [flyer(price=self.price)], "ecom_items": []})
            return httpx.Response(202, text="<html>challenge</html>",
                                  headers={"content-type": "text/html"})

        settings = Settings(database_url=f"sqlite:///{self.directory.name}/test.db", rfd_threads=1)
        self.app = create_app(settings, transport=httpx.MockTransport(handler))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.directory.cleanup()

    async def search(self, query="peanut butter", postal="N9A6K9"):
        return await self.client.post("/v1/search", json={"query": query, "postal_code": postal})

    async def test_fsa_search_is_rejected_with_guidance(self):
        response = await self.search(postal="N9V")
        self.assertEqual(response.status_code, 422)
        self.assertIn("full postal code", response.json()["detail"])

    async def test_blocked_source_is_reported_not_fatal(self):
        result = (await self.search()).json()
        rfd = next(s for s in result["sources"] if s["source"] == "rfd")
        self.assertEqual(rfd["status"], "error")
        self.assertEqual(rfd["error_code"], "blocked")

    async def test_watchlist_reports_current_best_offer(self):
        result = (await self.search()).json()
        offer = result["groups"][0]["offers"][0]
        created = await self.client.post("/v1/watchlist", json={
            "device_id": "device-123", "item_id": offer["item_id"], "target_price": "9.99"})
        self.assertEqual(created.status_code, 200)
        watch = (await self.client.get("/v1/watchlist", params={
            "device_id": "device-123", "postal_code": "N9A6K9"})).json()
        self.assertEqual(len(watch["items"]), 1)
        self.assertEqual(watch["items"][0]["best_offer"]["sale_price"], "8.99")
        self.assertTrue(watch["items"][0]["target_hit"])
        removed = await self.client.delete(f"/v1/watchlist/{offer['item_id']}",
                                           params={"device_id": "device-123"})
        self.assertEqual(removed.status_code, 200)
        watch = (await self.client.get("/v1/watchlist", params={
            "device_id": "device-123", "postal_code": "N9A6K9"})).json()
        self.assertEqual(watch["items"], [])

    async def test_history_summary_tracks_observed_changes(self):
        first = (await self.search("peanut butter")).json()
        self.price = "6.49"
        second = (await self.search("peanut")).json()
        item_id = second["groups"][0]["offers"][0]["item_id"]
        history = (await self.client.get(f"/v1/items/{item_id}/history",
                                         params={"postal_code": "N9A6K9"})).json()
        self.assertEqual(history["summary"]["lowest"], "6.49")
        self.assertEqual(history["summary"]["latest"], "6.49")
        self.assertEqual(history["summary"]["observations"], 2)
        self.assertEqual(len(history["observations"]), 2)
        self.assertTrue(first["search_id"] != second["search_id"])


if __name__ == "__main__":
    unittest.main()
