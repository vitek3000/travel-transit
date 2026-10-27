"""Google encoded polylines (the `legGeometry` of MOTIS) and a thinner that keeps the shape with few points.

MOTIS answers `/api/v1/plan` with precision 7 (seven decimals). Precision 7 cannot hold a longitude beyond ±107.37°:
the 32-bit value overflows there, so the parser drops such a geometry rather than draw it in the wrong place.
"""

import math

Point = tuple[float, float]  # (lat, lon)

PRECISION_7_MAX_LON = 107.37


def decode(points: str, precision: int = 7) -> list[Point]:
    """Decode an encoded polyline into [(lat, lon), …]. Raises ValueError on a string that is cut short or garbled."""
    factor = 10.0**precision
    out: list[Point] = []
    index = lat = lon = 0
    length = len(points)
    while index < length:
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                if index >= length:
                    raise ValueError("polyline ends in the middle of a value")
                byte = ord(points[index]) - 63
                index += 1
                if not 0 <= byte <= 0x3F:
                    raise ValueError("not a polyline character")
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        out.append((lat / factor, lon / factor))
    return out


def encode(coords: list[Point], precision: int = 7) -> str:
    """The reverse of `decode` (the tests and the fixtures use it)."""
    factor = 10**precision
    out: list[str] = []
    prev_lat = prev_lon = 0
    for lat, lon in coords:
        ilat, ilon = round(lat * factor), round(lon * factor)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def _metres(points: list[Point]) -> list[tuple[float, float]]:
    """The points on a flat plane, in metres (good enough for the few hundred kilometres of one leg)."""
    lat0 = points[0][0]
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    return [(lon * kx, lat * ky) for lat, lon in points]


def _distance_to_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def thin(points: list[Point], tolerance_m: float = 25.0, max_points: int = 200, decimals: int = 5) -> list[Point]:
    """Fewer points, the same line: Ramer–Douglas–Peucker with `tolerance_m`, then at most `max_points` (picked
    evenly, both ends kept), rounded to `decimals` (5 decimals is about a metre)."""
    if len(points) <= 2:
        return [(round(lat, decimals), round(lon, decimals)) for lat, lon in points]
    flat = _metres(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        worst, worst_at = 0.0, -1
        for i in range(first + 1, last):
            d = _distance_to_segment(flat[i], flat[first], flat[last])
            if d > worst:
                worst, worst_at = d, i
        if worst_at >= 0 and worst > tolerance_m:
            keep[worst_at] = True
            stack.append((first, worst_at))
            stack.append((worst_at, last))
    kept = [p for p, k in zip(points, keep, strict=True) if k]
    if len(kept) > max_points:
        step = (len(kept) - 1) / (max_points - 1)
        kept = [kept[round(i * step)] for i in range(max_points)]
    rounded: list[Point] = []
    for lat, lon in kept:
        point = (round(lat, decimals), round(lon, decimals))
        if not rounded or rounded[-1] != point:
            rounded.append(point)
    if len(rounded) == 1:  # a line whose ends round to one point is still a line of two
        rounded.append(rounded[0])
    return rounded
