"""What the service takes and gives: the request of `POST /journeys` and its answer."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Mode = Literal["walk", "bike", "car", "train", "metro", "tram", "bus", "coach", "ferry", "cable", "flight", "other"]


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    name: str | None = Field(default=None, max_length=120, description="shown instead of MOTIS's START / END")


class JourneysIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: Point = Field(alias="from")
    to: Point
    time: AwareDatetime = Field(description="ISO 8601 with an offset: «2026-11-09T19:00:00+01:00»")
    arrive_by: bool = Field(default=False, description="`time` is the latest arrival, not the earliest departure")
    max: int = Field(default=3, ge=1, le=5, description="at most this many journeys")
    modes: Literal["transit", "rail", "bus"] = Field(
        default="transit",
        description="`transit`: any public transport but planes; `rail`: trains only; `bus`: buses and coaches only",
    )


class Stop(BaseModel):
    name: str
    lat: float
    lon: float
    track: str | None = Field(default=None, description="the platform or stand («3», «B»), when the timetable has it")


class Leg(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True, populate_by_name=True)

    mode: Mode
    operator: str | None = None
    operator_url: str | None = None
    line: str | None = None
    headsign: str | None = None
    from_: Stop = Field(alias="from")
    to: Stop
    departure: str = Field(description="local time of the stop, with its offset")
    arrival: str
    duration_min: int
    geometry: list[list[float]] | None = Field(default=None, description="[[lat, lon], …], thinned")


class Journey(BaseModel):
    duration_min: int
    transfers: int
    departure: str
    arrival: str
    legs: list[Leg]


class Source(BaseModel):
    name: str = "Transitous"
    url: str = "https://transitous.org/sources/"
    osm: str = "© OpenStreetMap contributors"


class JourneysOut(BaseModel):
    journeys: list[Journey]
    source: Source = Field(default_factory=Source)
    cached: bool


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    requests_last_24h: int
    daily_limit: int
