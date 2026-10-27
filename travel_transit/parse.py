"""A MOTIS `/plan` answer → the journeys this service returns.

MOTIS gives every time in UTC and the time zone of every stop; a journey here gives each time in the local time of
the place it happens, with its offset («2026-11-09T20:01:00+01:00»), which is what a timetable on the platform says.
The two ends of the trip are called START and END by MOTIS: they get the names the caller gave, when it gave them.

An itinerary that cannot be read (a field missing or of the wrong type) is skipped, not guessed.
"""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from travel_transit.polyline import PRECISION_7_MAX_LON, decode, thin

# MOTIS modes → the few words a caller needs (v1 calls a suburban train METRO; later versions call it SUBURBAN)
MODES = {
    "WALK": "walk",
    "BIKE": "bike",
    "CAR": "car",
    "RENTAL": "other",
    "CAR_PARKING": "car",
    "CAR_DROPOFF": "car",
    "ODM": "bus",
    "FLEX": "bus",
    "RIDE_SHARING": "car",
    "TRAM": "tram",
    "SUBWAY": "metro",
    "METRO": "train",
    "SUBURBAN": "train",
    "FERRY": "ferry",
    "AIRPLANE": "flight",
    "BUS": "bus",
    "COACH": "coach",
    "RAIL": "train",
    "HIGHSPEED_RAIL": "train",
    "LONG_DISTANCE": "train",
    "NIGHT_RAIL": "train",
    "REGIONAL_FAST_RAIL": "train",
    "REGIONAL_RAIL": "train",
    "FUNICULAR": "cable",
    "AERIAL_LIFT": "cable",
    "AREAL_LIFT": "cable",
    "CABLE_CAR": "cable",
}
NOT_TRANSIT = frozenset({"walk", "bike", "car", "other"})
ENDS = {"START": "from", "END": "to"}


def _zone(name: Any) -> ZoneInfo | None:
    if not isinstance(name, str) or not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def local_time(value: str, zone_name: Any) -> datetime:
    """«2026-11-09T19:01:00Z» and «Europe/Brussels» → 2026-11-09 20:01+01:00. Without a known zone it stays UTC."""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    zone = _zone(zone_name)
    return moment.astimezone(zone) if zone is not None else moment.astimezone(UTC)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("not a number")
    return float(value)


def _place(raw: dict, names: dict[str, str | None]) -> dict:
    name = _text(raw.get("name")) or "?"
    end = ENDS.get(name) if raw.get("vertexType") != "TRANSIT" and not raw.get("stopId") else None
    if end is not None:
        name = names.get(end) or ("Start" if end == "from" else "Destination")
    return {"name": name, "lat": round(_number(raw["lat"]), 5), "lon": round(_number(raw["lon"]), 5)}


def _geometry(raw: Any, ends: list[dict]) -> list[list[float]] | None:
    """The decoded and thinned line of a leg, or None when MOTIS sent none (walks without details) or it is unusable."""
    if not isinstance(raw, dict):
        return None
    points, precision = raw.get("points"), raw.get("precision", 7)
    if not isinstance(points, str) or not points or not isinstance(precision, int) or not 1 <= precision <= 9:
        return None
    if precision >= 7 and any(abs(end["lon"]) > PRECISION_7_MAX_LON for end in ends):
        return None  # precision 7 overflows there: the line would be drawn in the wrong place
    try:
        line = decode(points, precision)
    except ValueError:
        return None
    if len(line) < 2:
        return None
    return [[lat, lon] for lat, lon in thin(line)]


def _line(raw: dict) -> str | None:
    return _text(raw.get("displayName")) or _text(raw.get("routeShortName")) or _text(raw.get("tripShortName"))


def _leg(raw: dict, names: dict[str, str | None]) -> dict:
    origin, destination = _place(raw["from"], names), _place(raw["to"], names)
    departure = local_time(raw["startTime"], raw["from"].get("tz"))
    arrival = local_time(raw["endTime"], raw["to"].get("tz"))
    mode = MODES.get(str(raw.get("mode")), "other")
    transit = mode not in NOT_TRANSIT
    duration = raw.get("duration")
    seconds = _number(duration) if duration is not None else (arrival - departure).total_seconds()
    leg: dict[str, Any] = {
        "mode": mode,
        "operator": _text(raw.get("agencyName")) if transit else None,
        "line": _line(raw) if transit else None,
        "headsign": _text(raw.get("headsign")) if transit else None,
        "from": origin,
        "to": destination,
        "departure": departure.isoformat(),
        "arrival": arrival.isoformat(),
        "duration_min": max(0, round(seconds / 60)),
    }
    url = _text(raw.get("agencyUrl"))
    if transit and url and url.startswith(("https://", "http://")):
        leg["operator_url"] = url
    geometry = _geometry(raw.get("legGeometry"), [origin, destination])
    if geometry is not None:
        leg["geometry"] = geometry
    return leg


def _journey(raw: dict, names: dict[str, str | None]) -> dict | None:
    if raw.get("cancelled") is True:
        return None
    legs_raw = raw.get("legs")
    if not isinstance(legs_raw, list) or not legs_raw:
        return None
    if any(isinstance(leg, dict) and leg.get("cancelled") is True for leg in legs_raw):
        return None  # a journey with a cancelled train is not a way to go
    legs = [_leg(leg, names) for leg in legs_raw]
    if not any(leg["mode"] not in NOT_TRANSIT for leg in legs):
        return None  # a walk is not a public-transport journey
    departure = local_time(raw["startTime"], legs_raw[0]["from"].get("tz"))
    arrival = local_time(raw["endTime"], legs_raw[-1]["to"].get("tz"))
    duration = raw.get("duration")
    seconds = _number(duration) if duration is not None else (arrival - departure).total_seconds()
    transfers = raw.get("transfers")
    if not isinstance(transfers, int) or isinstance(transfers, bool) or transfers < 0:
        transfers = max(0, sum(1 for leg in legs if leg["mode"] not in NOT_TRANSIT) - 1)
    return {
        "duration_min": max(0, round(seconds / 60)),
        "transfers": transfers,
        "departure": departure.isoformat(),
        "arrival": arrival.isoformat(),
        "legs": legs,
    }


def journeys(answer: dict, *, from_name: str | None = None, to_name: str | None = None, limit: int = 5) -> list[dict]:
    """The journeys of an answer, in its order, at most `limit`."""
    names = {"from": _text(from_name), "to": _text(to_name)}
    out: list[dict] = []
    for raw in answer.get("itineraries") or []:
        if len(out) >= limit:
            break
        if not isinstance(raw, dict):
            continue
        try:
            journey = _journey(raw, names)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if journey is not None:
            out.append(journey)
    return out
