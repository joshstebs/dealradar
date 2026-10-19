"""Real service checks enabled explicitly in CI; no external DB assumed locally."""
import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from backend.cache import ResilientCache
from backend.models import SearchResponse, SourceStatus
from backend.repository import Repository


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL integration URL not configured")
class PostgresTests(unittest.TestCase):
    def test_snapshot_persists_across_connections(self):
        result = SearchResponse(search_id=uuid4(), query="storage smoke test", postal_code="N9V",
                                fsa="N9V", approximate_location=True, groups=[],
                                sources=[SourceStatus(source="test", status="ok")],
                                completed_at=datetime.now(timezone.utc))
        repository = Repository(os.environ["TEST_DATABASE_URL"])
        repository.save(result)
        repository.close()
        repository = Repository(os.environ["TEST_DATABASE_URL"])
        try:
            self.assertEqual(repository.search(str(result.search_id)).query, result.query)
        finally:
            # Remove only this test's own generated snapshot; no table truncation.
            repository.execute("DELETE FROM api_searches WHERE id=?", (str(result.search_id),))
            repository.db.commit()
            repository.close()


@unittest.skipUnless(os.getenv("TEST_REDIS_URL"), "Redis integration URL not configured")
class RedisTests(unittest.IsolatedAsyncioTestCase):
    async def test_redis_round_trip_and_ttl(self):
        cache = ResilientCache(os.environ["TEST_REDIS_URL"])
        key = f"test:dealradar:{uuid4()}"
        try:
            await cache.set(key, "payload", ex=21600)
            self.assertEqual(await cache.redis.get(key), b"payload")
            self.assertGreater(await cache.redis.ttl(key), 21590)
            self.assertEqual(await cache.get(key), b"payload")
        finally:
            await cache.redis.delete(key)
            await cache.close()
