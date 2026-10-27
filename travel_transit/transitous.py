"""The one place that talks to Transitous: `GET /api/v1/plan`, politely.

* every request says who is asking (`User-Agent: travel-transit/<version> (+<contact>)`);
* at most one request a second and `daily_limit` (700) in any 24 hours, booked in the SQLite store;
* answers are kept 6 hours: the same question within that time does not reach Transitous;
* a request waits at most 10 seconds; a timeout, a dropped connection or a 5xx is tried once more, nothing else is;
* a 429 from Transitous is never retried: the caller hears «slow down» at once.

Only the two points (rounded to 4 decimals, about 11 m), the time and the direction leave the computer.
"""

import json
import logging
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from travel_transit.config import PLAN_PATH, Settings
from travel_transit.store import Busy, DailyLimitReached, Store

log = logging.getLogger("travel_transit")

MAX_BODY = 20_000_000
DETAIL_CHARS = 200
ITINERARIES = 5  # asked every time: the cache then serves any `max` up to 5


class UpstreamError(Exception):
    """Transitous gave no usable answer. `status` is what this service answers its own caller."""

    status = 502

    def __init__(self, detail: str, retry_after_s: float | None = None):
        self.detail = detail
        self.retry_after_s = retry_after_s
        super().__init__(detail)


class UpstreamRefused(UpstreamError):
    status = 502


class UpstreamUnavailable(UpstreamError):
    status = 503


class UpstreamTimeout(UpstreamError):
    status = 504


class LimitReached(UpstreamError):
    status = 429


class _Transient(Exception):
    """Worth one more try: a timeout, a dropped connection, the server's own trouble."""

    def __init__(self, final: UpstreamError):
        self.final = final
        super().__init__(final.detail)


@dataclass(frozen=True)
class Question:
    """One question to Transitous. The points are rounded to 4 decimals and the time to the minute, so that the same
    question asked twice is the same key of the cache."""

    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    time: datetime  # aware
    arrive_by: bool = False

    @property
    def params(self) -> dict[str, str]:
        moment = self.time.astimezone(UTC).replace(second=0, microsecond=0)
        return {
            "fromPlace": f"{self.from_lat:.4f},{self.from_lon:.4f}",
            "toPlace": f"{self.to_lat:.4f},{self.to_lon:.4f}",
            "time": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "arriveBy": "true" if self.arrive_by else "false",
            "numItineraries": str(ITINERARIES),
            "detailedTransfers": "false",  # no turn-by-turn walks: smaller answers, less work for the server
        }

    @property
    def key(self) -> str:
        p = self.params
        return f"{PLAN_PATH}|{p['fromPlace']}|{p['toPlace']}|{p['time']}|{p['arriveBy']}"


def _error_text(body: bytes) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    text = data.get("error") if isinstance(data, dict) else None
    return text[:DETAIL_CHARS] if isinstance(text, str) else ""


def _retry_after(exc: HTTPError) -> float | None:
    value = exc.headers.get("Retry-After") if exc.headers else None
    try:
        return max(1.0, float(value)) if value else None
    except ValueError:
        return None


class Transitous:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        opener: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self._settings = settings
        self._store = store
        self._opener = opener if opener is not None else urllib.request.build_opener()
        self._sleep = sleep
        self._clock = clock

    def plan(self, question: Question) -> tuple[dict, bool]:
        """The answer of `/plan` for a question and whether it came from the cache."""
        key = question.key
        kept = self._store.get(key)
        if kept is not None:
            try:
                return json.loads(kept), True
            except ValueError:  # a broken row is asked again
                pass
        body = self._fetch(question)
        try:
            answer = json.loads(body)
        except ValueError:
            raise UpstreamRefused("Transitous sent an answer that is not JSON.") from None
        if not isinstance(answer, dict) or not isinstance(answer.get("itineraries"), list):
            raise UpstreamRefused("Transitous sent an answer without itineraries.")
        self._store.put(key, body, self._settings.cache_ttl_s)
        return answer, False

    def _fetch(self, question: Question) -> bytes:
        url = f"{self._settings.transitous_url}{PLAN_PATH}?{urlencode(question.params)}"
        tries = 2  # the first one and one more for a transient failure
        for attempt in range(1, tries + 1):
            self._wait_turn()
            try:
                return self._once(url)
            except _Transient as exc:
                log.warning("transitous: %s (try %d of %d)", exc.final.detail, attempt, tries)
                if attempt == tries:
                    raise exc.final from None
        raise AssertionError("unreachable")

    def _wait_turn(self) -> None:
        """Book the next moment a request may go out and wait for it."""
        s = self._settings
        try:
            slot = self._store.book(min_interval_s=s.min_interval_s, daily_limit=s.daily_limit, max_wait_s=s.max_wait_s)
        except DailyLimitReached as exc:
            raise LimitReached(
                f"The daily limit of {s.daily_limit} requests to Transitous is reached. Try again later.",
                exc.retry_after_s,
            ) from None
        except Busy as exc:
            raise UpstreamUnavailable(
                "Too many requests are waiting for their turn. Try again soon.", exc.retry_after_s
            ) from None
        wait = slot - self._clock()
        if wait > 0:
            self._sleep(wait)

    def _once(self, url: str) -> bytes:
        request = urllib.request.Request(
            url, headers={"User-Agent": self._settings.user_agent, "Accept": "application/json"}
        )
        try:
            with self._opener.open(request, timeout=self._settings.timeout_s) as response:
                body = response.read(MAX_BODY + 1)
        except HTTPError as exc:  # before URLError, which it extends
            try:
                detail = _error_text(exc.read(MAX_BODY + 1))
            except OSError:
                detail = ""
            if exc.code == 429:
                raise UpstreamUnavailable("Transitous asks to slow down. Try again later.", _retry_after(exc)) from None
            if exc.code >= 500:
                raise _Transient(UpstreamUnavailable(f"Transitous is in trouble (HTTP {exc.code}).")) from None
            said = f": {detail}" if detail else ""
            raise UpstreamRefused(f"Transitous refused the request (HTTP {exc.code}){said}") from None
        except TimeoutError:
            raise _Transient(UpstreamTimeout("Transitous did not answer in time.")) from None
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise _Transient(UpstreamTimeout("Transitous did not answer in time.")) from None
            raise _Transient(UpstreamUnavailable("Transitous cannot be reached.")) from None
        except OSError:
            raise _Transient(UpstreamUnavailable("The connection to Transitous broke.")) from None
        if len(body) > MAX_BODY:
            raise UpstreamRefused("Transitous sent an answer that is too large.")
        return body
