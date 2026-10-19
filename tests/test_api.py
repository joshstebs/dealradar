from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from uuid import uuid4

import httpx

from backend.config import Settings
from backend.main import create_app
from backend.normalization import cad_price, explicit_quantity
from scrapers.rfd_client import enrich_post, parse_feed


def flyer(title="Chicken breast 1 kg", price="9.00", item_id=1):
    return {"id": item_id, "name": title, "merchant_name": "Test grocer",
            "current_price": price, "original_price": "18.00",
            "url": "https://flipp.com/test", "valid_from": "2020-01-01T00:00:00Z",
            "valid_to": "2099-01-01T00:00:00Z"}


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.items = [flyer(price="12.00", item_id=1), flyer(price="9.00", item_id=2)]
        self.fail_flipp = False
        self.fail_rfd = False
        self.directory = tempfile.TemporaryDirectory()
        def handler(request):
            self.calls.append(str(request.url))
            if request.url.host == "backflipp.wishabi.com":
                return httpx.Response(403) if self.fail_flipp else httpx.Response(200, json={"items": self.items})
            if self.fail_rfd:
                return httpx.Response(403)
            if request.url.path.startswith("/rss/"):
                return httpx.Response(200, text='<rss><channel><item><title>[Store] Chicken breast</title><link>https://forums.redflagdeals.com/deal-1/</link></item></channel></rss>')
            return httpx.Response(200, text='<nav class="breadcrumbs">Hot Deals</nav><div data-vote-score="26"></div><div class="postbody"><a href="https://www.walmart.ca/test">Buy</a></div>')
        settings = Settings(database_url=f"sqlite:///{self.directory.name}/test.db", rfd_threads=1)
        self.app = create_app(settings, transport=httpx.MockTransport(handler))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.directory.cleanup()

    async def search(self, postal="N9V1A1", query="chicken"):
        return await self.client.post("/v1/search", json={"query": query, "postal_code": postal})

    async def test_comparison_persistence_history_and_websocket(self):
        response = await self.search()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        offers = result["groups"][0]["offers"]
        self.assertEqual([o["sale_price"] for o in offers], ["9.00", "12.00"])
        self.assertEqual(offers[0]["unit_price"], "0.90")
        self.assertFalse(offers[0]["price_verified"])
        self.assertEqual(result["community"][0]["score"], 26)
        self.assertEqual((await self.client.get(f'/v1/offers/{offers[0]["id"]}')).status_code, 200)
        history = (await self.client.get(f'/v1/items/{offers[0]["item_id"]}/history', params={"postal_code": "N9V1A1"})).json()
        self.assertEqual(len(history["observations"]), 2)
        # Exercise the actual ASGI WebSocket route without threads or a socket.
        sent = []
        async def receive(): return {"type": "websocket.connect"}
        async def send(message): sent.append(message)
        scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": f'/v1/searches/{result["search_id"]}/events', "raw_path": b"/", "query_string": b"", "headers": [], "server": ("test", 80), "client": ("test", 1), "subprotocols": []}
        await self.app(scope, receive, send)
        events = [json.loads(message["text"]) for message in sent if message["type"] == "websocket.send"]
        self.assertEqual(events[0]["type"], "source_completed")
        self.assertEqual(events[-1]["type"], "search_finished")
        self.assertEqual((await self.client.get(f'/v1/searches/{result["search_id"]}')).status_code, 200)

    async def test_unverified_feed_never_becomes_steal(self):
        await self.search()
        self.assertEqual((await self.client.get("/v1/deals/top", params={"postal_code": "N9V1A1"})).json()["offers"], [])

    async def test_cache_and_postal_scope(self):
        first = (await self.search()).json()
        calls = len(self.calls)
        second = (await self.search()).json()
        self.assertTrue(second["cached"])
        self.assertEqual(len(self.calls), calls)
        await self.search("N9V2A2")
        self.assertGreater(len(self.calls), calls)
        item = first["groups"][0]["offers"][0]
        self.assertEqual((await self.client.get(f'/v1/items/{item["item_id"]}/offers', params={"postal_code": "N9V"})).json()["groups"], [])

    async def test_outage_and_partial_coverage(self):
        self.fail_rfd = True
        partial = await self.search()
        self.assertEqual(partial.status_code, 200)
        self.assertEqual(partial.json()["sources"][1]["status"], "error")
        self.fail_flipp = True
        failed = await self.search(query="unseen query")
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["detail"]["sources"][0]["error_code"], "http_403")
        self.assertEqual(failed.headers["Retry-After"], "15")

    async def test_validation_and_ambiguous_prices(self):
        for query, postal in [(" ", "N9V"), ("chicken", "90210"), ("chicken", "D9V")]:
            self.assertEqual((await self.search(postal, query)).status_code, 422)
        self.items = [flyer(price="2/$5")]
        result = (await self.search()).json()
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["sources"][0]["rejected_count"], 1)

    async def test_expired_and_unknown_offers(self):
        self.items[0]["valid_to"] = "2020-01-02T00:00:00Z"
        result = (await self.search()).json()
        self.assertEqual(len(result["groups"][0]["offers"]), 1)
        offer = result["groups"][0]["offers"][0]
        self.assertEqual((await self.client.get(f"/v1/offers/{uuid4()}")).status_code, 404)
        saved = self.app.state.repository.offer(offer["id"])
        saved.valid_to = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.app.state.repository.execute("UPDATE api_offers SET payload=? WHERE id=?", (saved.model_dump_json(), offer["id"]))
        self.app.state.repository.db.commit()
        self.assertEqual((await self.client.get(f'/v1/offers/{offer["id"]}')).status_code, 410)

    async def test_basket_excludes_wrong_region_and_missing_offers(self):
        offer = (await self.search()).json()["groups"][0]["offers"][0]
        request = {"postal_code": "N9V1A1", "items": [{"offer_id": offer["id"], "quantity": 2}]}
        quote = (await self.client.post("/v1/basket/quote", json=request)).json()
        self.assertEqual(quote["merchandise_total"], "18.00")
        self.assertFalse(quote["checkout_verified"])
        request["postal_code"] = "N9V2A2"
        quote = (await self.client.post("/v1/basket/quote", json=request)).json()
        self.assertFalse(quote["complete"])
        self.assertEqual(quote["merchandise_total"], "0")

    async def test_openapi_and_source_status(self):
        spec = (await self.client.get("/openapi.json")).json()
        self.assertIn("/v1/search", spec["paths"])
        result = (await self.client.get("/v1/sources/status", params={"postal_code": "N9V"})).json()
        self.assertEqual(result["sources"][0]["status"], "not_checked")
        self.assertEqual((await self.client.get("/v1/locations/resolve", params={"postal_code": "n9v 1a1"})).json()["fsa"], "N9V")

    async def test_weight_based_price_is_comparable_but_not_a_package_quote(self):
        self.items = [flyer(title="Chicken breast", price="$4.99/lb")]
        result = (await self.search()).json()
        offer = result["groups"][0]["offers"][0]
        self.assertEqual(offer["price_basis"], "lb")
        self.assertEqual(offer["unit_basis"], "100g")
        self.assertAlmostEqual(float(offer["unit_price"]), 1.1001, places=3)
        quote = (await self.client.post("/v1/basket/quote", json={"postal_code": "N9V1A1", "items": [{"offer_id": offer["id"], "quantity": 1}]})).json()
        self.assertFalse(quote["complete"])
        self.assertEqual(quote["merchandise_total"], "0")


class SafetyTests(unittest.TestCase):
    def test_xml_safety_and_category(self):
        for xml in ('<!DOCTYPE rss [<!ENTITY a "x">]><rss/>', '<html>blocked</html>'):
            with self.assertRaises(ValueError): parse_feed(xml)
        post = {"thread_url": "https://forums.redflagdeals.com/a/"}
        self.assertFalse(enrich_post(post, '<nav>Hot Deals</nav><div data-vote-score="30"></div>')["is_hot_deal"])
        self.assertTrue(enrich_post(post, '<nav class="breadcrumbs">Hot Deals</nav>')["is_hot_deal"])

    def test_currency_and_package(self):
        for price in ("US$4.99", "2/$5", "NaN", "-3", True):
            self.assertIsNone(cad_price(price))
        total, unit, _ = explicit_quantity("Paper 12 rolls")
        self.assertEqual(str(total), "12")
        self.assertEqual(unit, "roll")
        self.assertIsNone(explicit_quantity("Mega value pack")[0])


if __name__ == "__main__":
    unittest.main()
