"""The pace, the daily limit and the cache, kept in SQLite."""

import pytest

from travel_transit.store import DAY_S, Busy, DailyLimitReached, Store


def book(store: Store, limit: int = 700, wait: float = 30.0) -> float:
    return store.book(min_interval_s=1.0, daily_limit=limit, max_wait_s=wait)


def test_requests_are_booked_a_second_apart(store, clock):
    first = book(store)
    second = book(store)
    third = book(store)
    assert first == clock.now
    assert (second - first, third - second) == (1.0, 1.0)


def test_a_request_after_a_quiet_second_goes_at_once(store, clock):
    book(store)
    clock.now += 5
    assert book(store) == clock.now


def test_the_daily_limit_counts_the_last_24_hours_and_says_when_a_place_frees(store, clock):
    for _ in range(3):
        book(store, limit=3)
        clock.now += 10
    with pytest.raises(DailyLimitReached) as caught:
        book(store, limit=3)
    assert caught.value.retry_after_s == pytest.approx(DAY_S - 30)
    clock.now += DAY_S - 25  # the first request is now more than a day old
    assert book(store, limit=3) == clock.now


def test_a_turn_too_far_away_is_refused_as_busy(store):
    for _ in range(3):  # now, +1 s, +2 s
        book(store, wait=2.5)
    with pytest.raises(Busy) as caught:
        book(store, wait=2.5)  # +3 s is too far
    assert caught.value.retry_after_s == pytest.approx(3.0)
    assert store.calls_last_day() == 3  # a refused turn is not booked


def test_the_count_is_kept_in_the_file_for_another_process(store, settings, clock):
    book(store)
    book(store)
    other = Store(settings.db_path, clock=clock)
    assert other.calls_last_day() == 2
    assert book(other) == clock.now + 2.0  # the pace is shared too


def test_old_rows_are_forgotten(store, clock):
    book(store)
    clock.now += 3 * DAY_S
    book(store)
    assert store.calls_last_day() == 1


def test_an_answer_is_kept_for_its_time_then_forgotten(store, clock):
    store.put("k", b'{"itineraries": []}', ttl_s=6 * 3600)
    clock.now += 6 * 3600 - 1
    assert store.get("k") == b'{"itineraries": []}'
    clock.now += 2
    assert store.get("k") is None


def test_a_new_answer_replaces_the_old_one_and_unknown_keys_are_none(store):
    store.put("k", b"one", ttl_s=60)
    store.put("k", b"two", ttl_s=60)
    assert store.get("k") == b"two"
    assert store.get("other") is None
