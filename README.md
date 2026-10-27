# travel-transit

A small HTTP service that asks [Transitous](https://transitous.org) for public-transport journeys between two
points and answers with plain JSON: trains, buses and coaches with their operators, lines, stops, local departure and
arrival times, the number of transfers and the line of each leg for a map.

Transitous is a free, community-run public-transport router for the whole world, built on
[MOTIS](https://github.com/motis-project/motis) and on open timetable data. Its usage policy asks that the code that
uses it be open source and that the use stay light. This service is that code: one place that talks to Transitous,
with the limits of the policy built in, that other programs on the same computer can ask.

It was written to give a trip planner real departure times for the way from an airport to a town (and back), and it
works for any two points that Transitous covers.

## Run it

You need [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer.

```bash
uv sync
uv run python -m travel_transit        # http://127.0.0.1:8790
```

Settings, all optional, from environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `TRANSIT_HOST` | `127.0.0.1` | where to listen; keep it local unless you know why not |
| `TRANSIT_PORT` | `8790` | the port |
| `TRANSIT_DB` | `data/travel-transit.sqlite3` | the small SQLite file with the request counter and the cache |
| `TRANSIT_CONTACT` | `https://github.com/vitek3000/travel-transit` | the contact sent in the `User-Agent`; **set your own** if you run a fork |
| `TRANSIT_DAILY_LIMIT` | `700` | requests to Transitous in any 24 hours; it can be lowered, never raised above 700 |
| `TRANSITOUS_URL` | `https://api.transitous.org` | for tests against a local fake only; the policy forbids the staging host in applications |

## API

### `GET /health`

```json
{"status": "ok", "version": "0.2.0", "requests_last_24h": 12, "daily_limit": 700}
```

### `POST /journeys`

```json
{
  "from": {"lat": 50.8454, "lon": 4.3570, "name": "Brussels Central"},
  "to": {"lat": 50.8497, "lon": 5.6896, "name": "Maastricht"},
  "time": "2026-11-09T19:45:00+01:00",
  "arrive_by": false,
  "max": 3
}
```

* `time` is ISO 8601 **with an offset**: the earliest departure, or with `"arrive_by": true` the latest arrival.
* `max` is 1 to 5 (3 by default). `name` is optional: it replaces MOTIS's `START` and `END` in the answer.

Answer (shortened):

```json
{
  "journeys": [
    {
      "duration_min": 120,
      "transfers": 2,
      "departure": "2026-11-09T19:59:00+01:00",
      "arrival": "2026-11-09T21:59:00+01:00",
      "legs": [
        {"mode": "walk", "operator": null, "line": null, "headsign": null,
         "from": {"name": "Brussels Central", "lat": 50.8454, "lon": 4.357, "track": null},
         "to": {"name": "Bruxelles-Central", "lat": 50.84565, "lon": 4.3568, "track": null},
         "departure": "2026-11-09T19:59:00+01:00", "arrival": "2026-11-09T20:01:00+01:00",
         "duration_min": 2, "geometry": null},
        {"mode": "train", "operator": "NMBS/SNCB", "operator_url": "http://www.belgiantrain.be/",
         "line": "IC 518", "headsign": "Eupen",
         "from": {"name": "Bruxelles-Central", "lat": 50.84565, "lon": 4.3568, "track": "3"},
         "to": {"name": "Liège-Guillemins", "lat": 50.62455, "lon": 5.5667, "track": "5"},
         "departure": "2026-11-09T20:01:00+01:00", "arrival": "2026-11-09T20:59:00+01:00",
         "duration_min": 58, "geometry": [[50.84565, 4.3568], [50.8597, 4.36085], "…"]}
      ]
    }
  ],
  "source": {"name": "Transitous", "url": "https://transitous.org/sources/", "osm": "© OpenStreetMap contributors"},
  "cached": false
}
```

* Times are local to the stop where they happen, with the offset of that place.
* `track` is the platform or stand of a stop («3», «12b», «B»), when the timetable gives one: the current track when
  real-time data changed it, else the scheduled one (MOTIS's `track`, else `scheduledTrack`). `null` when it is not
  known, which is often the case for buses and for the ends of a walk. Added in 0.2.0; a caller written for 0.1.0
  simply does not read it.
* `mode` is one of `walk`, `bike`, `car`, `train`, `metro`, `tram`, `bus`, `coach`, `ferry`, `cable`, `flight`,
  `other`.
* `geometry` is the leg's line as `[[lat, lon], …]`, thinned to at most 200 points (about 25 m of tolerance). Walks
  have none: the service asks Transitous for the transit legs in detail and the walks in brief.
* A journey with a cancelled leg is left out, and so is a journey that is only a walk.
* An empty `journeys` list is a real answer: Transitous has nothing for that time. Timetables that are not published
  yet (a winter timetable in October, say) come back empty too.

Errors are JSON with a `detail`:

| Status | When |
|---|---|
| 422 | the request does not fit (a time without an offset, `max` above 5, a point off the globe) |
| 429 | the daily limit is reached; `Retry-After` says when a place frees |
| 502 | Transitous refused the request or sent something that is not a plan |
| 503 | Transitous cannot be reached, asks to slow down, or too many requests wait for their turn |
| 504 | Transitous did not answer within 10 seconds (after one more try) |

## How it follows the Transitous usage policy

* **Open source.** This repository, under the MIT licence.
* **Light use.**
  * At most one request a second and 700 in any 24 hours (the policy asks to talk to the Transitous team above about
    720). Both are counted in the SQLite file before a request leaves, so two copies of the service sharing the file
    keep them together.
  * Every answer is kept for 6 hours: the same question in that time never reaches Transitous. Points are rounded to
    4 decimals (about 11 m) and times to the minute, so near-identical questions are one question.
  * A request waits at most 10 seconds. A timeout, a dropped connection or a server error is tried once more, a
    refusal never, and a `429` from Transitous is passed on at once, not retried.
  * The service only answers the questions it is asked: no prefetching, no background jobs, no scraping loops.
    (Whoever needs bulk data should download the source datasets, as the policy says.)
* **Contact.** Every request carries `User-Agent: travel-transit/<version> (+<contact>)`.
* **The production API only** (`https://api.transitous.org/api/v1/plan`); never the staging host.
* **Attribution.** Every answer carries `source`: the link to [the Transitous sources](https://transitous.org/sources/)
  and the OpenStreetMap credit. A program that shows these journeys must show both where it shows them, for example
  «Timetables: Transitous · © OpenStreetMap contributors», each linked.

## Data and licences

The journeys come from Transitous, which combines the timetables (GTFS, GTFS-RT, NeTEx and others) published by
transport operators and public bodies, each under its own licence, with OpenStreetMap data
(© OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright)) for the walks and the stops. The
list of sources and their licences is at <https://transitous.org/sources/>. Some of those licences require attribution
and some exclude commercial use, which is why Transitous itself does not allow commercial use. The MIT licence of
this repository covers its code only, not the data that passes through it.

The test fixtures are trimmed, hand-edited samples in the shape of a Transitous answer.

## Privacy

* No keys and no accounts: Transitous needs neither.
* What leaves the computer: the two points (rounded to 4 decimals), the time and whether it is an arrival time.
  Names given in the request never leave the service.
* The log holds the two points rounded to 3 decimals (about 100 m), the number of journeys found and whether they came
  from the cache. No names, no times, no client addresses (`python -m travel_transit` turns the web server's access
  log off).
* The SQLite file holds the moments of the requests of the last two days and the answers of the last 6 hours.

## Development

```bash
uv sync
uv run pytest -q            # no test reaches the network
uv run ruff check . && uv run ruff format --check .
```

The service calls `/api/v1/plan`, whose lines are Google polylines with precision 7. Precision 7 cannot hold a
longitude beyond ±107.37°, so a leg there comes without `geometry` rather than with a wrong one.

## Licence

MIT, © 2026 Viktar Viorstav. See [LICENSE](LICENSE).
