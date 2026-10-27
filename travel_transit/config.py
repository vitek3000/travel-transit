"""Settings from environment variables. The limits of the Transitous usage policy are built in: the variables may make
the service gentler, never harder on Transitous."""

import os
from dataclasses import dataclass
from pathlib import Path

from travel_transit import __version__

PROJECT_DIR = Path(__file__).resolve().parents[1]

TRANSITOUS_URL = "https://api.transitous.org"
PLAN_PATH = "/api/v1/plan"
CONTACT = "https://github.com/vitek3000/travel-transit"
DAILY_LIMIT = 700  # requests in any 24 hours; Transitous asks to talk to them first above about 720
MIN_INTERVAL_S = 1.0  # at most one request a second
CACHE_TTL_S = 6 * 3600  # the same question within 6 hours is answered from the cache
TIMEOUT_S = 10.0  # one request to Transitous waits at most this long
MAX_WAIT_S = 30.0  # a request that would queue longer than this for its turn is refused (busy) rather than held


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8790
    db_path: Path = PROJECT_DIR / "data" / "travel-transit.sqlite3"
    transitous_url: str = TRANSITOUS_URL
    contact: str = CONTACT
    daily_limit: int = DAILY_LIMIT
    min_interval_s: float = MIN_INTERVAL_S
    cache_ttl_s: float = CACHE_TTL_S
    timeout_s: float = TIMEOUT_S
    max_wait_s: float = MAX_WAIT_S

    def __post_init__(self) -> None:
        # never above the policy, whatever the environment says
        object.__setattr__(self, "daily_limit", max(1, min(int(self.daily_limit), DAILY_LIMIT)))
        object.__setattr__(self, "min_interval_s", max(float(self.min_interval_s), MIN_INTERVAL_S))
        object.__setattr__(self, "transitous_url", self.transitous_url.rstrip("/"))

    @property
    def user_agent(self) -> str:
        """Who is asking: the name, the version and where to reach the people who run it (the policy asks for it)."""
        return f"travel-transit/{__version__} (+{self.contact})"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        values: dict[str, object] = {}
        if env.get("TRANSIT_HOST"):
            values["host"] = env["TRANSIT_HOST"]
        if env.get("TRANSIT_PORT"):
            values["port"] = int(env["TRANSIT_PORT"])
        if env.get("TRANSIT_DB"):
            values["db_path"] = Path(env["TRANSIT_DB"]).expanduser()
        if env.get("TRANSITOUS_URL"):
            values["transitous_url"] = env["TRANSITOUS_URL"]
        if env.get("TRANSIT_CONTACT"):
            values["contact"] = env["TRANSIT_CONTACT"]
        if env.get("TRANSIT_DAILY_LIMIT"):
            values["daily_limit"] = int(env["TRANSIT_DAILY_LIMIT"])
        return cls(**values)  # type: ignore[arg-type]
