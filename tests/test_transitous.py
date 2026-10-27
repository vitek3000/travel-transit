"""The client of Transitous: what it asks, how it waits, what it retries and what it refuses."""

import json
from datetime import UTC, datetime, timedelta, timezone
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.conftest import FakeOpener, fixture_bytes
from travel_transit import __version__
from travel_transit.config import Settings
from travel_transit.transitous import (
    LimitReached,
    Question,
    Transitous,
    UpstreamRefused,
    UpstreamTimeout,
    UpstreamUnavailable,
)

CET = timezone(timedelta(hours=1))
QUESTION = Question(50.84541, 4.35698, 50.84968, 5.68963, datetime(2026, 11, 9, 19, 30, 25, tzinfo=CET))
ANSWER = fixture_bytes("plan_bru_mst.json")


def client(settings, store, clock, *replies, **kw):
    opener = FakeOpener(*replies, **kw)
    return Transitous(settings, store, opener=opener, sleep=clock.sleep, clock=clock), opener


def params(opener, index=-1) -> dict[str, str]:
    query = parse_qs(urlsplit(opener.requests[index].full_url).query)
    return {name: values[0] for name, values in query.items()}


def test_the_question_goes_to_v1_plan_with_rounded_points_the_time_in_utc_and_light_details(settings, store, clock):
    transitous, opener = client(settings, store, clock, ANSWER)
    answer, cached = transitous.plan(QUESTION)
    assert not cached and len(answer["itineraries"]) == 2
    url = urlsplit(opener.requests[0].full_url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://transitous.test/api/v1/plan"
    assert params(opener) == {
        "fromPlace": "50.8454,4.3570",
        "toPlace": "50.8497,5.6896",
        "time": "2026-11-09T18:30:00Z",
        "arriveBy": "false",
        "numItineraries": "5",
        "transitModes": "RAIL,BUS,COACH,TRAM,SUBWAY,FERRY,FUNICULAR,AERIAL_LIFT",
        "maxPreTransitTime": "1800",
        "maxPostTransitTime": "1800",
        "detailedTransfers": "false",
    }


def test_trains_only_or_buses_only_and_never_a_plane():
    rail = Question(1, 2, 3, 4, datetime(2026, 11, 13, 8, 0, tzinfo=UTC), modes="rail")
    bus = Question(1, 2, 3, 4, datetime(2026, 11, 13, 8, 0, tzinfo=UTC), modes="bus")
    assert rail.params["transitModes"] == "RAIL" and bus.params["transitModes"] == "BUS,COACH"
    assert all("AIRPLANE" not in q.params["transitModes"] for q in (rail, bus, QUESTION))
    assert len({rail.key, bus.key, Question(1, 2, 3, 4, rail.time).key}) == 3  # each kind is its own answer


def test_every_request_says_who_asks_and_waits_ten_seconds_at_most(settings, store, clock):
    transitous, opener = client(settings, store, clock, ANSWER)
    transitous.plan(QUESTION)
    agent = opener.requests[0].get_header("User-agent")
    assert agent == f"travel-transit/{__version__} (+https://github.com/vitek3000/travel-transit)"
    assert opener.timeouts == [10.0]


def test_an_arrival_question_says_so():
    q = Question(1, 2, 3, 4, datetime(2026, 11, 13, 8, 0, tzinfo=UTC), arrive_by=True)
    assert q.params["arriveBy"] == "true" and q.params["time"] == "2026-11-13T08:00:00Z"


def test_the_same_question_within_six_hours_is_answered_from_the_cache(settings, store, clock):
    transitous, opener = client(settings, store, clock, ANSWER, ANSWER)
    transitous.plan(QUESTION)
    clock.now += 6 * 3600 - 10
    answer, cached = transitous.plan(QUESTION)
    assert cached and len(answer["itineraries"]) == 2 and len(opener.requests) == 1
    clock.now += 20
    _, cached = transitous.plan(QUESTION)
    assert not cached and len(opener.requests) == 2


def test_a_question_a_few_metres_and_seconds_away_is_the_same_question(settings, store, clock):
    transitous, opener = client(settings, store, clock, ANSWER)
    transitous.plan(QUESTION)
    near = Question(50.84539, 4.35702, 50.84971, 5.68958, QUESTION.time + timedelta(seconds=20))
    assert transitous.plan(near)[1] is True and len(opener.requests) == 1


def test_two_questions_in_a_row_are_a_second_apart(settings, store, clock):
    other = Question(50.0, 4.0, 50.5, 5.0, QUESTION.time)
    transitous, opener = client(settings, store, clock, ANSWER, ANSWER)
    transitous.plan(QUESTION)
    transitous.plan(other)
    assert clock.slept == [1.0] and len(opener.requests) == 2


def test_a_timeout_is_tried_once_more_and_then_said(settings, store, clock):
    transitous, opener = client(settings, store, clock, TimeoutError(), URLError(TimeoutError()))
    with pytest.raises(UpstreamTimeout) as caught:
        transitous.plan(QUESTION)
    assert caught.value.status == 504 and len(opener.requests) == 2
    assert clock.slept == [1.0]  # the second try kept the pace too


def test_a_server_error_is_tried_once_more_and_a_success_then_counts(settings, store, clock):
    transitous, opener = client(settings, store, clock, (503, b"{}"), ANSWER)
    answer, cached = transitous.plan(QUESTION)
    assert len(answer["itineraries"]) == 2 and not cached and len(opener.requests) == 2
    assert store.calls_last_day() == 2


def test_an_unreachable_host_is_tried_once_more_then_unavailable(settings, store, clock):
    transitous, opener = client(settings, store, clock, URLError("refused"), OSError("reset"))
    with pytest.raises(UpstreamUnavailable) as caught:
        transitous.plan(QUESTION)
    assert caught.value.status == 503 and len(opener.requests) == 2


def test_a_429_of_transitous_is_never_retried_and_passes_its_wait_on(settings, store, clock):
    transitous, opener = client(settings, store, clock, (429, b"{}"), headers={"Retry-After": "120"})
    with pytest.raises(UpstreamUnavailable) as caught:
        transitous.plan(QUESTION)
    assert len(opener.requests) == 1 and caught.value.retry_after_s == 120.0


def test_a_refusal_is_not_retried_and_says_what_transitous_said(settings, store, clock):
    transitous, opener = client(settings, store, clock, (400, json.dumps({"error": "bad fromPlace"}).encode()))
    with pytest.raises(UpstreamRefused) as caught:
        transitous.plan(QUESTION)
    assert len(opener.requests) == 1
    assert (
        caught.value.status == 502 and caught.value.detail == "Transitous refused the request (HTTP 400): bad fromPlace"
    )


@pytest.mark.parametrize("body", [b"<html>", b"[]", b'{"error": "x"}'])
def test_an_answer_that_is_not_a_plan_is_refused_and_not_kept(settings, store, clock, body):
    transitous, opener = client(settings, store, clock, body, ANSWER)
    with pytest.raises(UpstreamRefused):
        transitous.plan(QUESTION)
    assert transitous.plan(QUESTION)[1] is False  # asked again, not served from the cache


def test_the_daily_limit_stops_the_requests_before_they_leave(settings, store, clock):
    gentle = Settings(db_path=settings.db_path, transitous_url=settings.transitous_url, daily_limit=1)
    transitous, opener = client(gentle, store, clock, ANSWER)
    transitous.plan(QUESTION)
    with pytest.raises(LimitReached) as caught:
        transitous.plan(Question(50.0, 4.0, 50.5, 5.0, QUESTION.time))
    assert caught.value.status == 429 and caught.value.retry_after_s > 3600
    assert len(opener.requests) == 1


def test_the_settings_never_go_above_the_policy():
    loud = Settings(daily_limit=5000, min_interval_s=0.1)
    assert (loud.daily_limit, loud.min_interval_s) == (700, 1.0)
    env = Settings.from_env({"TRANSIT_DAILY_LIMIT": "100", "TRANSIT_PORT": "8800", "TRANSITOUS_URL": "https://x.test/"})
    assert (env.daily_limit, env.port, env.transitous_url) == (100, 8800, "https://x.test")
