"""Persistent API read-model: SQLite for local use, psycopg for PostgreSQL.

The catalog schema remains in db/schema.sql. These API tables retain validated
offer snapshots and source runs without requiring the catalog/embedding worker.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock

from backend.models import Offer, SearchResponse


class Repository:
    def __init__(self, url: str):
        self.lock = RLock()
        self.postgres = url.startswith(("postgresql://", "postgres://"))
        if self.postgres:
            import psycopg
            self.db = psycopg.connect(url)
        elif url.startswith("sqlite:///"):
            path = url.removeprefix("sqlite:///")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path, check_same_thread=False)
        else:
            raise ValueError("DATABASE_URL must use sqlite:/// or postgresql://")
        with self.lock:
            for ddl in (
                "CREATE TABLE IF NOT EXISTS api_offers (id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS api_searches (id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS api_price_observations (offer_id TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(offer_id, observed_at))",
                "CREATE TABLE IF NOT EXISTS api_source_runs (scope TEXT PRIMARY KEY, payload TEXT NOT NULL)",
            ):
                self.db.execute(ddl)
            self.db.commit()

    def execute(self, sql, params=()):
        return self.db.execute(sql.replace("?", "%s") if self.postgres else sql, params)

    def save(self, result: SearchResponse):
        with self.lock:
            try:
                for group in result.groups:
                    for offer in group.offers:
                        self.execute("INSERT INTO api_offers(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                                     (str(offer.id), offer.model_dump_json()))
                        self.execute("INSERT INTO api_price_observations(offer_id,observed_at,payload) VALUES(?,?,?) ON CONFLICT(offer_id,observed_at) DO NOTHING",
                                     (str(offer.id), offer.observed_at.isoformat(), offer.model_dump_json()))
                self.execute("INSERT INTO api_searches(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                             (str(result.search_id), result.model_dump_json()))
                scope = result.postal_code
                payload = json.dumps([s.model_dump(mode="json") for s in result.sources])
                self.execute("INSERT INTO api_source_runs(scope,payload) VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload", (scope, payload))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def offer(self, offer_id: str) -> Offer | None:
        with self.lock:
            row = self.execute("SELECT payload FROM api_offers WHERE id=?", (offer_id,)).fetchone()
        return Offer.model_validate_json(row[0]) if row else None

    def offers(self, fsa: str, postal_scope: str | None = None) -> list[Offer]:
        with self.lock:
            rows = self.execute("SELECT payload FROM api_offers").fetchall()
        # Deliberately conservative: an FSA browse request excludes full-postal
        # snapshots so prices from different circulars cannot be mixed.
        scope = postal_scope or fsa
        now = datetime.now(timezone.utc)
        return [offer for row in rows if (offer := Offer.model_validate_json(row[0])).fsa == fsa
                and offer.postal_scope == scope and is_active(offer, now)]

    def search(self, search_id: str):
        with self.lock:
            row = self.execute("SELECT payload FROM api_searches WHERE id=?", (search_id,)).fetchone()
        return SearchResponse.model_validate_json(row[0]) if row else None

    def statuses(self, scope: str):
        with self.lock:
            row = self.execute("SELECT payload FROM api_source_runs WHERE scope=?", (scope,)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, item_id: str, fsa: str, postal_scope: str, since: datetime):
        with self.lock:
            rows = self.execute("SELECT payload FROM api_price_observations WHERE observed_at >= ? ORDER BY observed_at", (since.isoformat(),)).fetchall()
        return [offer for row in rows if str((offer := Offer.model_validate_json(row[0])).item_id) == item_id
                and offer.fsa == fsa and offer.postal_scope == postal_scope]

    def close(self):
        self.db.close()


def is_active(offer: Offer, now: datetime) -> bool:
    # Unknown expiry isn't proof of freshness; online prices age out sooner.
    max_age = 21600 if offer.source == "flipp" else 900
    return ((offer.valid_from is None or offer.valid_from <= now)
            and (offer.valid_to is None or offer.valid_to > now)
            and (now - offer.observed_at).total_seconds() <= max_age)
