"""The HTTP service: `GET /health` and `POST /journeys`.

Errors are JSON with a `detail` (FastAPI's own shape): 422 for a request that does not fit, 429 when the daily limit
is reached, 502 when Transitous refused or sent nonsense, 503 when it cannot be reached or asks to slow down, 504
when it did not answer in time. A `Retry-After` header says when to come back, when that is known.

The log holds the two points rounded to 3 decimals (about 100 m), how many journeys were found and whether they came
from the cache: no names, no times, nothing else of the request.
"""

import logging
import math

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from travel_transit import __version__
from travel_transit.config import Settings
from travel_transit.models import Health, JourneysIn, JourneysOut
from travel_transit.parse import journeys as read_journeys
from travel_transit.store import Store
from travel_transit.transitous import Question, Transitous, UpstreamError

log = logging.getLogger("travel_transit")


def _point(lat: float, lon: float) -> str:
    return f"{lat:.3f},{lon:.3f}"


def create_app(
    settings: Settings | None = None, *, store: Store | None = None, transitous: Transitous | None = None
) -> FastAPI:
    """The service. Tests hand in their own store and client (a fake clock, a fake network)."""
    settings = settings or Settings.from_env()
    store = store or Store(settings.db_path)
    client = transitous or Transitous(settings, store)
    app = FastAPI(
        title="travel-transit",
        version=__version__,
        description="Public-transport journeys from Transitous (https://transitous.org), asked politely.",
    )
    app.state.settings = settings
    app.state.store = store
    app.state.transitous = client

    @app.exception_handler(UpstreamError)
    def upstream_error(request: Request, exc: UpstreamError) -> JSONResponse:
        headers = {"Retry-After": str(math.ceil(exc.retry_after_s))} if exc.retry_after_s else None
        return JSONResponse({"detail": exc.detail}, status_code=exc.status, headers=headers)

    @app.get("/health")
    def health() -> Health:
        return Health(version=__version__, requests_last_24h=store.calls_last_day(), daily_limit=settings.daily_limit)

    @app.post("/journeys")
    def journeys(body: JourneysIn) -> JourneysOut:
        """Journeys by public transport between two points, leaving after (or arriving before) a moment."""
        question = Question(
            body.from_.lat, body.from_.lon, body.to.lat, body.to.lon, body.time, body.arrive_by, body.modes
        )
        where = f"{_point(body.from_.lat, body.from_.lon)} -> {_point(body.to.lat, body.to.lon)}"
        try:
            answer, cached = client.plan(question)
        except UpstreamError as exc:
            log.warning("journeys %s: %s", where, exc.detail)
            raise
        found = read_journeys(answer, from_name=body.from_.name, to_name=body.to.name, limit=body.max)
        log.info("journeys %s: %d found%s", where, len(found), " (cache)" if cached else "")
        return JourneysOut.model_validate({"journeys": found, "cached": cached})

    return app
