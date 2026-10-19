from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/dealradar.db"
    redis_url: str | None = None
    flipp_enabled: bool = True
    rfd_enabled: bool = True
    source_timeout: float = 20
    verification_max_age_seconds: int = 900
    # Fixed provider hosts; callers cannot supply fetch URLs through the API.
    flipp_endpoint: str = "https://backflipp.wishabi.com/flipp/items/search"
    rfd_feed: str = "https://forums.redflagdeals.com/rss/feed/active/"
    rfd_threads: int = 5
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            redis_url=os.getenv("REDIS_URL") or None,
            flipp_enabled=os.getenv("FLIPP_ENABLED", "true").lower() == "true",
            rfd_enabled=os.getenv("RFD_ENABLED", "true").lower() == "true",
            source_timeout=float(os.getenv("SOURCE_TIMEOUT_SECONDS", "20")),
            rfd_threads=int(os.getenv("RFD_MAX_THREADS", "5")),
            cors_origins=tuple(filter(None, os.getenv("CORS_ORIGINS", "").split(","))),
        )
