"""Stand-ins for the network and the clock: no test ever reaches Transitous."""

import io
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from travel_transit.config import Settings
from travel_transit.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeClock:
    """Seconds since the epoch that move only when the code sleeps (or a test moves them)."""

    def __init__(self, now: float = 1_797_000_000.0):
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = body

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeOpener:
    """Answers in order: bytes (a 200), a (status, bytes) pair (400 and above raised as an HTTPError), or an exception.
    An extra request fails the test."""

    def __init__(self, *replies: Any, headers: dict[str, str] | None = None):
        self.replies = list(replies)
        self.requests: list[Any] = []
        self.timeouts: list[float | None] = []
        self.headers = headers or {}

    def open(self, request: Any, timeout: float | None = None) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self.replies:
            raise AssertionError(f"unexpected request to {request.full_url}")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        status, body = reply if isinstance(reply, tuple) else (200, reply)
        if status >= 400:
            raise HTTPError(request.full_url, status, "error", self.headers, io.BytesIO(body))  # type: ignore[arg-type]
        return FakeResponse(body, status)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(db_path=tmp_path / "transit.sqlite3", transitous_url="https://transitous.test")


@pytest.fixture
def store(settings, clock) -> Store:
    return Store(settings.db_path, clock=clock)
