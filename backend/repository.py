"""Persistent API read-model: SQLite for local use, psycopg for PostgreSQL.

The catalog schema remains in db/schema.sql. These API tables retain validated
offer snapshots and source runs without requiring the catalog/embedding worker.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
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
                "CREATE TABLE IF NOT EXISTS api_watchlist (device_id TEXT NOT NULL, item_id TEXT NOT NULL, target_price TEXT, created_at TEXT NOT NULL, PRIMARY KEY(device_id, item_id))",
            ):
                self.db.execute(ddl)
            self._ensure_columns()
            for ddl in (
                "CREATE INDEX IF NOT EXISTS idx_api_offers_scope ON api_offers(fsa, scope, item_id)",
                "CREATE INDEX IF NOT EXISTS idx_api_offers_item ON api_offers(item_id)",
                "CREATE INDEX IF NOT EXISTS idx_api_obs_item ON api_price_observations(item_id, observed_at)",
                "CREATE INDEX IF NOT EXISTS idx_api_watch_device ON api_watchlist(device_id)",
            ):
                self.db.execute(ddl)
            self.db.commit()

    def _columns(self) -> set[str]:
        if self.postgres:
            rows = self.db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='api_offers'"
            ).fetchall()
            return {row[0] for row in rows}
        return {row[1] for row in self.db.execute("PRAGMA table_info(api_offers)").fetchall()}

    def _ensure_columns(self):
        """Add the indexed filter columns to databases created before them."""
        existing = self._columns()
        additions = {
            "fsa": "TEXT", "scope": "TEXT", "item_id": "TEXT", "observed_at": "TEXT",
        }
        for name, kind in additions.items():
            if name not in existing:
                self.db.execute(f"ALTER TABLE api_offers ADD COLUMN {name} {kind}")
        obs = (self.db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='api_price_observations'").fetchall()
               if self.postgres else self.db.execute("PRAGMA table_info(api_price_observations)").fetchall())
        obs_columns = {row[0] for row in obs} if self.postgres else {row[1] for row in obs}
        if "item_id" not in obs_columns:
            self.db.execute("ALTER TABLE api_price_observations ADD COLUMN item_id TEXT")
        # Backfill columns for rows written by an older process.
        for row in self.db.execute("SELECT id, payload FROM api_offers WHERE fsa IS NULL").fetchall():
            try:
                offer = Offer.model_validate_json(row[1])
            except Exception:
                continue
            self.execute("UPDATE api_offers SET fsa=?, scope=?, item_id=?, observed_at=? WHERE id=?",
                         (offer.fsa, offer.postal_scope, str(offer.item_id),
                          offer.observed_at.isoformat(), row[0]))

    def execute(self, sql, params=()):
        return self.db.execute(sql.replace("?", "%s") if self.postgres else sql, params)

    def save(self, result: SearchResponse):
        with self.lock:
            try:
                for group in result.groups:
                    for offer in group.offers:
                        payload = offer.model_dump_json()
                        self.execute("INSERT INTO api_offers(id,payload,fsa,scope,item_id,observed_at) VALUES(?,?,?,?,?,?) "
                                     "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, fsa=excluded.fsa, "
                                     "scope=excluded.scope, item_id=excluded.item_id, observed_at=excluded.observed_at",
                                     (str(offer.id), payload, offer.fsa, offer.postal_scope,
                                      str(offer.item_id), offer.observed_at.isoformat()))
                        self.execute("INSERT INTO api_price_observations(offer_id,observed_at,payload,item_id) VALUES(?,?,?,?) "
                                     "ON CONFLICT(offer_id,observed_at) DO NOTHING",
                                     (str(offer.id), offer.observed_at.isoformat(), payload, str(offer.item_id)))
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
        # Deliberately conservative: an FSA browse request excludes full-postal
        # snapshots so prices from different circulars cannot be mixed.
        scope = postal_scope or fsa
        with self.lock:
            rows = self.execute("SELECT payload FROM api_offers WHERE fsa=? AND scope=?",
                                (fsa, scope)).fetchall()
        now = datetime.now(timezone.utc)
        return [offer for row in rows if (offer := Offer.model_validate_json(row[0]))
                and is_active(offer, now)]

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
            rows = self.execute("SELECT payload FROM api_price_observations WHERE item_id=? AND observed_at>=? ORDER BY observed_at",
                                (item_id, since.isoformat())).fetchall()
        return [offer for row in rows if (offer := Offer.model_validate_json(row[0]))
                and offer.fsa == fsa and offer.postal_scope == postal_scope]

    def latest_offers(self, item_id: str, fsa: str, scope: str) -> list[Offer]:
        with self.lock:
            rows = self.execute("SELECT payload FROM api_offers WHERE item_id=? AND fsa=? AND scope=?",
                                (item_id, fsa, scope)).fetchall()
        now = datetime.now(timezone.utc)
        offers = [o for row in rows if (o := Offer.model_validate_json(row[0])) and is_active(o, now)]

        def rank(offer: Offer):
            # Decimal comparison — never lexicographic strings ("10.00" < "9.50").
            # Package-priced offers first, then cheapest reliable unit price,
            # then sale price.
            unit = Decimal(offer.unit_price) if offer.unit_price is not None else None
            return (offer.price_basis != "package", unit is None,
                    unit if unit is not None else Decimal("Infinity"),
                    Decimal(offer.sale_price))

        return sorted(offers, key=rank)

    def watch(self, device_id: str, item_id: str, target_price: str | None = None) -> None:
        with self.lock:
            self.execute("INSERT INTO api_watchlist(device_id,item_id,target_price,created_at) VALUES(?,?,?,?) "
                         "ON CONFLICT(device_id,item_id) DO UPDATE SET "
                         "target_price=COALESCE(excluded.target_price, api_watchlist.target_price)",
                         (device_id, item_id, target_price, datetime.now(timezone.utc).isoformat()))
            self.db.commit()

    def unwatch(self, device_id: str, item_id: str) -> None:
        with self.lock:
            self.execute("DELETE FROM api_watchlist WHERE device_id=? AND item_id=?", (device_id, item_id))
            self.db.commit()

    def watched(self, device_id: str) -> list[dict]:
        with self.lock:
            rows = self.execute("SELECT item_id,target_price,created_at FROM api_watchlist WHERE device_id=? ORDER BY created_at DESC",
                                (device_id,)).fetchall()
        return [{"item_id": row[0], "target_price": row[1], "created_at": row[2]} for row in rows]

    def item_points(self, item_id: str, fsa: str, scope: str, since: datetime) -> list[dict]:
        with self.lock:
            rows = self.execute("SELECT payload FROM api_price_observations WHERE item_id=? AND observed_at>=? ORDER BY observed_at",
                                (item_id, since.isoformat())).fetchall()
        points = []
        for row in rows:
            offer = Offer.model_validate_json(row[0])
            if offer.fsa == fsa and offer.postal_scope == scope:
                points.append({"observed_at": offer.observed_at, "sale_price": offer.sale_price,
                               "price_basis": offer.price_basis,
                               "unit_price": offer.unit_price, "unit_basis": offer.unit_basis,
                               "store": offer.store, "source": offer.source})
        return points

    def close(self):
        self.db.close()


def is_recent(timestamp: datetime | None, now: datetime, max_age: int) -> bool:
    if timestamp is None or timestamp.utcoffset() is None or now.utcoffset() is None:
        return False
    age = (now - timestamp).total_seconds()
    return 0 <= age <= max_age


def is_active(offer: Offer, now: datetime) -> bool:
    # Unknown expiry isn't proof of freshness; online prices age out sooner.
    max_age = 21600 if offer.source == "flipp" else 900
    return ((offer.valid_from is None or offer.valid_from <= now)
            and (offer.valid_to is None or offer.valid_to > now)
            and is_recent(offer.observed_at, now, max_age))
