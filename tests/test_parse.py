"""The answer of MOTIS (a trimmed Brussels → Maastricht answer of Transitous, 9 November 2026) → journeys."""

import copy

from tests.conftest import fixture
from travel_transit.parse import journeys, local_time
from travel_transit.polyline import encode


def test_a_journey_has_its_times_in_the_local_clock_its_transfers_and_its_legs():
    first = journeys(fixture("plan_bru_mst.json"))[0]
    assert first["departure"] == "2026-11-09T19:59:00+01:00"  # 18:59 UTC in Brussels
    assert first["arrival"] == "2026-11-09T21:59:00+01:00"
    assert first["duration_min"] == 120
    assert first["transfers"] == 2
    assert [leg["mode"] for leg in first["legs"]] == ["walk", "train", "walk", "train", "walk", "bus", "walk"]


def test_a_train_leg_names_the_operator_the_line_the_headsign_and_both_stops():
    train = journeys(fixture("plan_bru_mst.json"))[0]["legs"][1]
    assert train["operator"] == "NMBS/SNCB"
    assert train["operator_url"] == "http://www.belgiantrain.be/"
    assert train["line"] == "IC 518"
    assert train["headsign"] == "Eupen"
    assert train["from"] == {"name": "Bruxelles-Central", "lat": 50.84565, "lon": 4.3568, "track": "3"}
    assert train["to"]["name"] == "Liège-Guillemins"
    assert (train["departure"], train["arrival"], train["duration_min"]) == (
        "2026-11-09T20:01:00+01:00",
        "2026-11-09T20:59:00+01:00",
        58,
    )


def test_a_stop_carries_its_platform_the_current_one_first_then_the_scheduled_one():
    legs = journeys(fixture("plan_bru_mst.json"))[0]["legs"]
    ic, re18, bus = legs[1], legs[3], legs[5]
    assert ic["from"]["track"] == "3"  # the same in both
    assert ic["to"]["track"] == "5"  # only the timetable's
    assert re18["from"]["track"] == "2"  # changed on the day: «2», not the «1» of the timetable
    assert "track" not in re18["to"]  # none known: no key, not a guess
    assert bus["from"]["track"] == "B"  # a stand of a bus station is a track too
    assert "track" not in legs[0]["from"]  # the start of a walk


def test_an_empty_or_odd_platform_is_left_out():
    answer = fixture("plan_bru_mst.json")
    legs = answer["itineraries"][0]["legs"]
    legs[1]["from"]["track"] = "  "  # empty now, the timetable's «3» stays
    legs[1]["to"]["scheduledTrack"] = 5  # not a text
    legs[3]["from"]["track"] = "x" * 40  # not a platform; the scheduled «1» is
    got = journeys(answer)[0]["legs"]
    assert got[1]["from"]["track"] == "3"
    assert "track" not in got[1]["to"]
    assert got[3]["from"]["track"] == "1"


def test_the_second_train_and_the_coach():
    first, second = journeys(fixture("plan_bru_mst.json"))
    arriva = first["legs"][3]
    assert (arriva["operator"], arriva["line"], arriva["headsign"]) == ("Arriva", "Sneltrein RE18", "Maastricht")
    flix = second["legs"][3]
    assert (flix["mode"], flix["operator"], flix["line"]) == ("coach", "FlixBus-eu", "FlixBus 832")
    assert flix["from"]["name"] == "Leuven Station (Perron 15)"


def test_a_transit_leg_carries_its_line_decoded_and_thinned_starting_at_its_stop():
    train = journeys(fixture("plan_bru_mst.json"))[0]["legs"][1]
    line = train["geometry"]
    assert 2 <= len(line) <= 200
    assert line[0] == [50.84565, 4.3568] or abs(line[0][0] - 50.8457) < 0.001
    assert abs(line[-1][0] - train["to"]["lat"]) < 0.002 and abs(line[-1][1] - train["to"]["lon"]) < 0.002
    assert all(round(lat, 5) == lat and round(lon, 5) == lon for lat, lon in line)


def test_a_walk_has_no_operator_no_line_and_no_line_on_the_map_when_motis_sent_none():
    walk = journeys(fixture("plan_bru_mst.json"))[0]["legs"][0]
    assert walk["mode"] == "walk"
    assert walk["operator"] is None and walk["line"] is None and walk["headsign"] is None
    assert "geometry" not in walk and "operator_url" not in walk


def test_the_ends_get_the_names_the_caller_gave_and_neutral_names_otherwise():
    named = journeys(fixture("plan_bru_mst.json"), from_name="Brussels Airport", to_name="Hotel Derlon")[0]
    assert named["legs"][0]["from"]["name"] == "Brussels Airport"
    assert named["legs"][-1]["to"]["name"] == "Hotel Derlon"
    plain = journeys(fixture("plan_bru_mst.json"))[0]
    assert plain["legs"][0]["from"]["name"] == "Start"
    assert plain["legs"][-1]["to"]["name"] == "Destination"


def test_the_limit_and_an_empty_answer():
    assert len(journeys(fixture("plan_bru_mst.json"), limit=1)) == 1
    assert journeys(fixture("plan_empty.json")) == []


def test_an_itinerary_that_cannot_be_read_is_skipped_not_guessed():
    answer = fixture("plan_bru_mst.json")
    del answer["itineraries"][0]["legs"][1]["startTime"]
    found = journeys(answer)
    assert len(found) == 1 and found[0]["legs"][3]["line"] == "FlixBus 832"


def test_a_cancelled_journey_or_one_without_any_vehicle_is_left_out():
    answer = fixture("plan_bru_mst.json")
    answer["itineraries"][0]["legs"][1]["cancelled"] = True
    walk_only = copy.deepcopy(answer["itineraries"][1])
    walk_only["legs"] = [walk_only["legs"][0]]
    answer["itineraries"].append(walk_only)
    found = journeys(answer)
    assert [j["legs"][3]["line"] for j in found] == ["FlixBus 832"]


def test_a_journey_with_a_plane_is_left_out():
    # Düsseldorf airport → Maastricht, 7 November 2026: Transitous answered Eurowings to Cagliari, a night there, KLM to
    # Amsterdam and the trains — 30 hours. A way over the ground never takes a plane.
    answer = fixture("plan_bru_mst.json")
    answer["itineraries"][0]["legs"][1]["mode"] = "AIRPLANE"
    found = journeys(answer)
    assert [j["legs"][3]["line"] for j in found] == ["FlixBus 832"]


def test_the_suburban_train_of_v1_and_of_later_versions_is_a_train_and_an_unknown_mode_is_other():
    answer = fixture("plan_bru_mst.json")
    legs = answer["itineraries"][0]["legs"]
    legs[1]["mode"], legs[3]["mode"], legs[5]["mode"] = "METRO", "SUBURBAN", "HOVERCRAFT"
    modes = [leg["mode"] for leg in journeys(answer)[0]["legs"]]
    assert modes[1] == modes[3] == "train" and modes[5] == "other"


def test_a_line_that_precision_7_cannot_hold_is_dropped():
    answer = fixture("plan_bru_mst.json")
    leg = answer["itineraries"][0]["legs"][1]
    leg["to"]["lon"] = 139.7
    leg["legGeometry"] = {"points": encode([(35.6, 139.6), (35.7, 139.7)], 7), "precision": 7, "length": 2}
    assert "geometry" not in journeys(answer)[0]["legs"][1]


def test_precision_6_of_later_versions_is_read_too():
    answer = fixture("plan_bru_mst.json")
    leg = answer["itineraries"][0]["legs"][1]
    leg["legGeometry"] = {"points": encode([(50.8457, 4.3568), (50.6246, 5.5667)], 6), "precision": 6, "length": 2}
    assert journeys(answer)[0]["legs"][1]["geometry"] == [[50.8457, 4.3568], [50.6246, 5.5667]]


def test_a_time_without_a_known_zone_stays_utc():
    assert local_time("2026-11-09T18:59:00Z", None).isoformat() == "2026-11-09T18:59:00+00:00"
    assert local_time("2026-11-09T18:59:00Z", "Mars/Olympus").isoformat() == "2026-11-09T18:59:00+00:00"
    assert local_time("2026-07-09T18:59:00Z", "Europe/Brussels").isoformat() == "2026-07-09T20:59:00+02:00"
