"""The HTTP service: /health and /journeys, the shape of the answer, and every error as JSON with a `detail`."""

import logging
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeOpener, fixture_bytes
from travel_transit import __version__
from travel_transit.app import create_app
from travel_transit.config import Settings
from travel_transit.store import Store
from travel_transit.transitous import Transitous

BODY = {
    "from": {"lat": 50.9010, "lon": 4.4844, "name": "Brussels Airport"},
    "to": {"lat": 50.8497, "lon": 5.6896, "name": "Maastricht"},
    "time": "2026-11-09T19:45:00+01:00",
}


@pytest.fixture
def make(settings, clock):
    def make(*replies, settings_=None, **kw):
        s = settings_ or settings
        store = Store(s.db_path, clock=clock)
        opener = FakeOpener(*replies, **kw)
        transitous = Transitous(s, store, opener=opener, sleep=clock.sleep, clock=clock)
        return TestClient(create_app(s, store=store, transitous=transitous)), opener

    return make


def test_health_says_the_version_and_the_requests_of_the_day(make):
    client, _ = make()
    assert client.get("/health").json() == {
        "status": "ok",
        "version": __version__,
        "requests_last_24h": 0,
        "daily_limit": 700,
    }


def test_journeys_with_legs_stops_times_geometry_and_the_attribution(make):
    client, _ = make(fixture_bytes("plan_bru_mst.json"))
    answer = client.post("/journeys", json=BODY).json()
    assert answer["cached"] is False
    assert answer["source"] == {
        "name": "Transitous",
        "url": "https://transitous.org/sources/",
        "osm": "© OpenStreetMap contributors",
    }
    first = answer["journeys"][0]
    assert (first["duration_min"], first["transfers"], first["departure"], first["arrival"]) == (
        120,
        2,
        "2026-11-09T19:59:00+01:00",
        "2026-11-09T21:59:00+01:00",
    )
    train = first["legs"][1]
    assert train["mode"] == "train" and train["operator"] == "NMBS/SNCB" and train["line"] == "IC 518"
    assert train["from"]["name"] == "Bruxelles-Central" and train["to"]["name"] == "Liège-Guillemins"
    assert (train["from"]["track"], train["to"]["track"]) == ("3", "5")  # the platforms
    assert first["legs"][3]["to"]["track"] is None  # none known: null
    assert len(train["geometry"]) >= 2
    assert first["legs"][0]["from"]["name"] == "Brussels Airport"
    assert first["legs"][0]["geometry"] is None  # a walk without details from MOTIS


def test_max_cuts_the_list_and_the_second_ask_comes_from_the_cache(make):
    client, opener = make(fixture_bytes("plan_bru_mst.json"))
    assert len(client.post("/journeys", json=BODY).json()["journeys"]) == 2
    again = client.post("/journeys", json={**BODY, "max": 1}).json()
    assert again["cached"] is True and len(again["journeys"]) == 1 and len(opener.requests) == 1


def test_trains_only_are_asked_as_such_and_kept_apart_in_the_cache(make):
    client, opener = make(fixture_bytes("plan_bru_mst.json"), fixture_bytes("plan_bru_mst.json"))
    client.post("/journeys", json=BODY)
    answer = client.post("/journeys", json={**BODY, "modes": "rail"}).json()
    assert answer["cached"] is False and len(opener.requests) == 2
    assert "transitModes=RAIL&" in opener.requests[1].full_url
    assert "AIRPLANE" not in opener.requests[0].full_url


def test_an_arrival_by_a_time_is_asked_as_such(make):
    client, opener = make(fixture_bytes("plan_empty.json"))
    answer = client.post("/journeys", json={**BODY, "arrive_by": True}).json()
    assert answer["journeys"] == []
    assert "arriveBy=true" in opener.requests[0].full_url


@pytest.mark.parametrize(
    "change",
    [
        {"time": "2026-11-09T19:45:00"},  # no offset: whose clock?
        {"max": 6},
        {"max": 0},
        {"modes": "plane"},
        {"from": {"lat": 91, "lon": 4}},
        {"to": {"lat": 50, "lon": 181}},
        {"extra": True},
    ],
)
def test_a_request_that_does_not_fit_is_a_422_with_a_detail(make, change):
    client, opener = make()
    answer = client.post("/journeys", json={**BODY, **change})
    assert answer.status_code == 422 and "detail" in answer.json()
    assert opener.requests == []


def test_a_refusal_of_transitous_is_a_502(make):
    client, _ = make((400, b'{"error": "unknown place"}'))
    answer = client.post("/journeys", json=BODY)
    assert answer.status_code == 502
    assert answer.json() == {"detail": "Transitous refused the request (HTTP 400): unknown place"}


def test_a_transitous_out_of_reach_is_a_503_and_one_that_is_slow_a_504(make):
    client, _ = make(URLError("refused"), URLError("refused"), TimeoutError(), TimeoutError())
    down = client.post("/journeys", json=BODY)
    assert down.status_code == 503 and down.json()["detail"] == "Transitous cannot be reached."
    slow = client.post("/journeys", json=BODY)
    assert slow.status_code == 504 and slow.json()["detail"] == "Transitous did not answer in time."


def test_the_daily_limit_is_a_429_with_retry_after(make, settings):
    gentle = Settings(db_path=settings.db_path, transitous_url=settings.transitous_url, daily_limit=1)
    client, opener = make(fixture_bytes("plan_bru_mst.json"), settings_=gentle)
    client.post("/journeys", json=BODY)
    answer = client.post("/journeys", json={**BODY, "time": "2026-11-10T19:45:00+01:00"})
    assert answer.status_code == 429
    assert "daily limit" in answer.json()["detail"]
    assert int(answer.headers["Retry-After"]) > 3600
    assert len(opener.requests) == 1


def test_the_log_has_the_points_rounded_to_three_decimals_and_nothing_else_of_the_request(make, caplog):
    client, _ = make(fixture_bytes("plan_bru_mst.json"))
    with caplog.at_level(logging.INFO, logger="travel_transit"):
        client.post("/journeys", json={**BODY, "from": {"lat": 50.901234, "lon": 4.484456, "name": "Secret Hotel"}})
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "50.901,4.484 -> 50.850,5.690: 2 found" in text
    assert "Secret" not in text and "50.9012" not in text and "2026" not in text
