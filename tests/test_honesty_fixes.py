"""Regression tests for price honesty, expiry, comparison and filtering.

Live provider payloads are represented by clearly labelled fixtures shaped like
Flipp's ``/flipp/items/search`` response (merchant_name/current_price/original_price).
No fixture price is treated as a live observation.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import tempfile
import unittest
from uuid import uuid4

import httpx

from backend.config import Settings
from backend.main import create_app, summarize_points
from backend.models import ComparisonGroup, Offer, SearchResponse, SourceStatus
from backend.normalization import explicit_quantity, flyer_offer, parse_date
from backend.repository import Repository, is_active


def mapped(title, store="Test grocer", sale="9.00", regular=None, **extra):
    item = {"title": title, "store": store, "sale_price_raw": sale,
            "regular_price_raw": regular, "source_id": f"{store}:{title}", "raw": {}}
    item.update(extra)
    return item


def offer(name, price, unit_price, store="Test grocer", basis="package"):
    return Offer(id=uuid4(), item_id=uuid4(), product_name=name, store=store,
                 fsa="N9V", postal_scope="N9V1A1", source="flipp",
                 sale_price=price, price_basis=basis, unit_price=unit_price,
                 unit_basis="100g", comparison_group=f"{name}|1kg",
                 observed_at=datetime.now(timezone.utc))


class QuantityTests(unittest.TestCase):
    def test_pack_of_multiplier_is_counted(self):
        total, basis, name = explicit_quantity("Peanut Butter 1 kg (pack of 2)")
        self.assertEqual(total, Decimal("2000"))
        self.assertEqual(basis, "100g")
        self.assertIn("peanut butter", name)

    def test_count_after_unit_is_counted(self):
        total, basis, _ = explicit_quantity("Coke 355 mL x 12")
        self.assertEqual(total, Decimal("4260"))
        self.assertEqual(basis, "100ml")

    def test_count_before_unit_is_counted(self):
        total, basis, _ = explicit_quantity("Yogurt 6 x 100 g")
        self.assertEqual(total, Decimal("600"))
        self.assertEqual(basis, "100g")

    def test_rolls_are_counted(self):
        total, basis, _ = explicit_quantity("Paper towels 12 rolls")
        self.assertEqual(total, Decimal("12"))
        self.assertEqual(basis, "roll")


class PriceHonestyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def test_per_measure_price_never_gets_a_package_discount(self):
        item = mapped("Chicken breast", sale="$4.99/lb", regular="9.99")
        result = flyer_offer(item, "N9V1A1", self.now)
        self.assertEqual(result.price_basis, "lb")
        self.assertIsNone(result.regular_price)
        self.assertIsNone(result.discount_percent)
        self.assertTrue(any("no percentage discount" in c for c in result.conditions))

    def test_zero_price_is_not_an_offer(self):
        self.assertIsNone(flyer_offer(mapped("Chicken breast", sale="0.00"), "N9V1A1", self.now))

    def test_implausible_original_price_is_excluded(self):
        item = mapped("Chicken breast", sale="5.00", regular="99.00")
        result = flyer_offer(item, "N9V1A1", self.now)
        self.assertIsNone(result.regular_price)
        self.assertIsNone(result.discount_percent)
        self.assertTrue(any("implausibly high" in c for c in result.conditions))

    def test_normal_original_price_still_produces_a_discount(self):
        item = mapped("Chicken breast", sale="15.00", regular="20.00")
        result = flyer_offer(item, "N9V1A1", self.now)
        self.assertEqual(result.regular_price, "20.00")
        self.assertEqual(result.discount_percent, "25.0")

    def test_date_only_expiry_uses_site_timezone(self):
        # Nov 2 is still EDT (UTC-4); end-of-day means the start of the next day.
        parsed = parse_date("2026-11-01", end=True)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.isoformat(), "2026-11-02T05:00:00+00:00")  # EST after DST ends
        expired = Offer(id=uuid4(), item_id=uuid4(), product_name="Milk", store="S",
                        fsa="N9V", postal_scope="N9V1A1", source="flipp",
                        sale_price="3.99", comparison_group="milk|1l",
                        valid_to=parsed, observed_at=self.now)
        self.assertFalse(is_active(expired, datetime(2026, 11, 3, tzinfo=timezone.utc)))


class RepositoryOrderingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(f"sqlite:///{self.directory.name}/test.db")

    def tearDown(self):
        self.repo.close()
        self.directory.cleanup()

    def _save(self, offers):
        self.repo.save(SearchResponse(
            search_id=uuid4(), query="milk", postal_code="N9V1A1", fsa="N9V",
            approximate_location=False, groups=[ComparisonGroup(key=offers[0].comparison_group,
                                                                 unit_basis="100g", offers=offers)],
            sources=[SourceStatus(source="flipp", status="ok")],
            completed_at=datetime.now(timezone.utc)))

    def test_best_offer_compares_unit_prices_numerically(self):
        expensive = offer("Milk", "10.00", "10.00")
        cheap = offer("Milk", "9.50", "9.50", store="Other grocer")
        cheap.item_id = expensive.item_id
        self._save([expensive, cheap])
        best = self.repo.latest_offers(str(expensive.item_id), "N9V", "N9V1A1")[0]
        self.assertEqual(best.sale_price, "9.50")

    def test_rewatching_without_a_target_keeps_the_existing_target(self):
        item_id = str(uuid4())
        self.repo.watch("device-abc123", item_id, "5.00")
        self.repo.watch("device-abc123", item_id, None)
        self.assertEqual(self.repo.watched("device-abc123")[0]["target_price"], "5.00")


class SummaryTests(unittest.TestCase):
    def test_summary_excludes_per_measure_rows(self):
        t1 = datetime(2026, 11, 1, tzinfo=timezone.utc)
        t2 = datetime(2026, 11, 2, tzinfo=timezone.utc)
        points = [
            {"observed_at": t1, "sale_price": "2.99", "price_basis": "lb", "store": "A",
             "source": "flipp", "unit_price": "0.66", "unit_basis": "100g"},
            {"observed_at": t1, "sale_price": "10.00", "price_basis": "package", "store": "A",
             "source": "flipp", "unit_price": "1.00", "unit_basis": "100g"},
            {"observed_at": t2, "sale_price": "8.00", "price_basis": "package", "store": "A",
             "source": "flipp", "unit_price": "0.80", "unit_basis": "100g"},
        ]
        summary = summarize_points(points)
        self.assertEqual(summary["lowest"], "8.00")
        self.assertEqual(summary["latest"], "8.00")
        self.assertEqual(summary["change_percent"], "-20.0")
        self.assertEqual(summary["checks"], 2)
        self.assertEqual(summary["observations"], 2)

    def test_single_check_does_not_support_a_historical_low_claim(self):
        t1 = datetime(2026, 11, 1, tzinfo=timezone.utc)
        summary = summarize_points([
            {"observed_at": t1, "sale_price": "10.00", "price_basis": "package", "store": "A",
             "source": "flipp", "unit_price": None, "unit_basis": None},
            {"observed_at": t1, "sale_price": "9.00", "price_basis": "package", "store": "B",
             "source": "flipp", "unit_price": None, "unit_basis": None},
        ])
        self.assertEqual(summary["checks"], 1)
        self.assertEqual(summary["latest"], "9.00")


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.items = [
            {"id": 1, "name": "Chicken breast 1 kg", "merchant_name": "Test grocer",
             "current_price": "12.00", "original_price": "18.00",
             "valid_to": "2099-01-01T00:00:00Z"},
            {"id": 2, "name": "Chicken thighs 1 kg", "merchant_name": "Other grocer",
             "current_price": "9.00", "original_price": "9.00",
             "valid_to": "2099-01-01T00:00:00Z"},
        ]
        self.directory = tempfile.TemporaryDirectory()

        def handler(request):
            if request.url.host == "backflipp.wishabi.com":
                return httpx.Response(200, json={"items": self.items})
            if request.url.path.startswith("/rss/"):
                return httpx.Response(200, text="<rss><channel></channel></rss>")
            return httpx.Response(403)

        settings = Settings(database_url=f"sqlite:///{self.directory.name}/test.db", rfd_threads=1)
        self.app = create_app(settings, transport=httpx.MockTransport(handler))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.directory.cleanup()

    async def test_retailer_and_price_filters(self):
        response = await self.client.post("/v1/search", json={
            "query": "chicken", "postal_code": "N9V1A1", "retailer": "Other grocer"})
        self.assertEqual(response.status_code, 200, response.text)
        groups = response.json()["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["offers"][0]["store"], "Other grocer")

        response = await self.client.post("/v1/search", json={
            "query": "chicken", "postal_code": "N9V1A1", "min_price": "10"})
        self.assertEqual([o["sale_price"] for g in response.json()["groups"] for o in g["offers"]], ["12.00"])

    async def test_sort_by_price_orders_groups(self):
        response = await self.client.post("/v1/search", json={
            "query": "chicken", "postal_code": "N9V1A1", "sort": "price"})
        names = [g["offers"][0]["product_name"] for g in response.json()["groups"]]
        self.assertEqual(names, ["Chicken thighs 1 kg", "Chicken breast 1 kg"])

    async def test_inverted_price_range_is_rejected(self):
        response = await self.client.post("/v1/search", json={
            "query": "chicken", "postal_code": "N9V1A1", "min_price": "20", "max_price": "10"})
        self.assertEqual(response.status_code, 422)

    async def test_deals_exclude_per_measure_zero_and_absurd_discounts(self):
        repository = self.app.state.repository
        now = datetime.now(timezone.utc)
        good = offer("Milk", "8.00", None)
        good.regular_price, good.discount_percent, good.reference_price_kind = "10.00", "20.0", "retailer"
        per_lb = offer("Beef", "4.99", None, basis="lb")
        per_lb.regular_price, per_lb.discount_percent, per_lb.reference_price_kind = "9.99", "50.0", "retailer"
        zero = offer("Eggs", "0.00", None)
        zero.regular_price, zero.discount_percent, zero.reference_price_kind = "5.00", "100.0", "retailer"
        repository.save(SearchResponse(
            search_id=uuid4(), query="milk", postal_code="N9V1A1", fsa="N9V",
            approximate_location=False,
            groups=[ComparisonGroup(key=o.comparison_group, unit_basis=None, offers=[o])
                    for o in (good, per_lb, zero)],
            sources=[SourceStatus(source="flipp", status="ok", checked_at=now)],
            completed_at=now))
        response = await self.client.get("/v1/deals/top", params={"postal_code": "N9V1A1"})
        self.assertEqual(response.status_code, 200)
        prices = [o["sale_price"] for o in response.json()["offers"]]
        self.assertEqual(prices, ["8.00"])

    async def test_watchlist_response_is_private_and_does_not_echo_the_device_id(self):
        response = await self.client.get("/v1/watchlist", params={
            "device_id": "device-abc123", "postal_code": "N9V1A1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertNotIn("device_id", response.json())

    async def test_watchlist_rejects_a_malformed_device_id(self):
        response = await self.client.get("/v1/watchlist", params={
            "device_id": "bad id!", "postal_code": "N9V1A1"})
        self.assertEqual(response.status_code, 422)

    async def test_watchlist_target_ignores_per_measure_offers(self):
        device = "device-abc123"
        item_id = str(uuid4())
        await self.client.post("/v1/watchlist", json={
            "device_id": device, "item_id": item_id, "target_price": "9.99"})
        repository = self.app.state.repository
        now = datetime.now(timezone.utc)
        cheap_per_lb = offer("Steak", "4.99", None, basis="lb")
        cheap_per_lb.item_id = item_id
        repository.save(SearchResponse(
            search_id=uuid4(), query="steak", postal_code="N9V1A1", fsa="N9V",
            approximate_location=False,
            groups=[ComparisonGroup(key=cheap_per_lb.comparison_group, unit_basis=None, offers=[cheap_per_lb])],
            sources=[SourceStatus(source="flipp", status="ok", checked_at=now)],
            completed_at=now))
        body = (await self.client.get("/v1/watchlist", params={
            "device_id": device, "postal_code": "N9V1A1"})).json()
        self.assertFalse(body["items"][0]["target_hit"])


if __name__ == "__main__":
    unittest.main()
