"""Open-Meteo game-time weather (CC-BY-4.0, no key).

The current heuristic adjusts for **wind** only. Temperature and precipitation
are available as metadata but do not change scores. This is a modeling choice,
not a claim of validated predictive performance.  See :mod:`ff.engine.lineup` for the curve.

Two traps, both of which would push a bogus penalty into a lineup:

* ``roof`` is blank in nflverse for retractable venues until the game is played.
  Blank is **unknown**, not "outdoors".  ARI/ATL/DAL/HOU/IND games are reported
  with ``unknown=True`` and a flag, and never take a wind penalty.
* Neutral-site games have no coordinates in the current venue lookup,
  so they also read unknown rather than silently defaulting to the home team's
  usual stadium.

Kickoff times come out of ``games.csv`` as naive US/Eastern clock times, so the
hourly series is requested in ``America/New_York`` and indexed by that same
naive timestamp.  No timezone arithmetic happens anywhere in this module.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import httpx

from ff.cache import HOUR, fetch_json
from ff.sources.nflverse import INDOOR_ROOFS, TEAM_ALIASES, stadium_info

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: The four hourly variables the brief and the lineup engine read.
HOURLY_VARIABLES = (
    "wind_speed_10m",
    "wind_gusts_10m",
    "precipitation_probability",
    "temperature_2m",
)

#: nflverse kickoff clock times are US/Eastern.
KICKOFF_TZ = "America/New_York"

#: Forecasts move; three hours is short enough for a Sunday-morning re-run and
#: long enough that a job that retries does not re-pull 16 stadiums.
FORECAST_TTL = 3 * HOUR

#: Roof values that mean the field is exposed.  Anything else that is not in
#: ``INDOOR_ROOFS`` -- notably the blank retractable -- is unknown.
OUTDOOR_ROOFS = {"outdoors", "open"}


@dataclass(frozen=True)
class GameWeather:
    """Conditions at one kickoff.

    ``indoor`` and ``unknown`` are the two states in which no wind penalty may
    be applied.  The readings are still reported when they exist, so a brief can
    say "28 mph outside, but the roof state is unknown" instead of going silent.
    """

    wind_speed_10m: float | None = None
    wind_gusts_10m: float | None = None
    precipitation_probability: float | None = None
    temperature_2m: float | None = None
    indoor: bool = False
    unknown: bool = False
    note: str = ""

    @property
    def wind(self) -> float:
        """Wind speed to adjust on: 0.0 whenever an adjustment is not allowed."""
        if self.indoor or self.unknown or self.wind_speed_10m is None:
            return 0.0
        return float(self.wind_speed_10m)

    @property
    def known(self) -> bool:
        return not self.unknown and not self.indoor


INDOORS = GameWeather(indoor=True, note="dome or closed roof; no weather adjustment")


def _missing(value) -> bool:
    """None or NaN.  A non-numeric string (``"20:20"``) is a real value."""
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _hour_key(kickoff: dt.datetime) -> str:
    return f"{kickoff:%Y-%m-%dT%H}:00"


def forecast(
    lat: float,
    lon: float,
    kickoff: dt.datetime,
    *,
    client: httpx.Client | None = None,
    ttl: float = FORECAST_TTL,
) -> GameWeather:
    """Hourly conditions at ``lat``/``lon`` for the hour ``kickoff`` falls in.

    One day of hourly data is fetched and cached per stadium per date, so the
    four Sunday windows for the same venue cost one request.
    """
    day = kickoff.date()
    raw = None
    try:
        raw = fetch_json(
            FORECAST_URL,
            key=f"openmeteo_{lat:.3f}_{lon:.3f}_{day:%Y%m%d}",
            ttl=ttl,
            params={
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "hourly": ",".join(HOURLY_VARIABLES),
                "wind_speed_unit": "mph",
                "temperature_unit": "fahrenheit",
                "timezone": KICKOFF_TZ,
                "start_date": f"{day:%Y-%m-%d}",
                "end_date": f"{day:%Y-%m-%d}",
            },
            client=client,
        )
    except httpx.HTTPError as exc:
        return GameWeather(unknown=True, note=f"open-meteo error: {type(exc).__name__}")

    hourly = (raw or {}).get("hourly") or {}
    times = hourly.get("time") or []
    want = _hour_key(kickoff)
    if want not in times:
        return GameWeather(
            unknown=True,
            note=f"{want} is outside the returned forecast window",
        )

    i = times.index(want)

    def at(name):
        series = hourly.get(name) or []
        return series[i] if i < len(series) else None

    return GameWeather(
        wind_speed_10m=at("wind_speed_10m"),
        wind_gusts_10m=at("wind_gusts_10m"),
        precipitation_probability=at("precipitation_probability"),
        temperature_2m=at("temperature_2m"),
    )


def game_weather(
    *,
    roof: str | None,
    lat: float | None,
    lon: float | None,
    kickoff: dt.datetime | None,
    neutral_site: bool = False,
    client: httpx.Client | None = None,
    ttl: float = FORECAST_TTL,
) -> GameWeather:
    """Conditions for one game, with the roof and neutral-site rules applied."""
    roof = (roof or "unknown").strip().lower()
    # Neutral site first: nflverse's roof label for an overseas venue is not
    # something to trust, and there are no coordinates to check it against.
    if neutral_site:
        return GameWeather(unknown=True, note="neutral-site game: no stadium coordinates")
    if roof in INDOOR_ROOFS:
        return INDOORS
    if _missing(lat) or _missing(lon):
        return GameWeather(unknown=True, note="no stadium coordinates for this venue")
    if kickoff is None:
        return GameWeather(unknown=True, note="no kickoff time for this game")

    reading = forecast(float(lat), float(lon), kickoff, client=client, ttl=ttl)
    if roof in OUTDOOR_ROOFS:
        return reading

    # Retractable, state not yet published.  Report the reading, refuse the
    # penalty: the roof may well be closed.
    return GameWeather(
        wind_speed_10m=reading.wind_speed_10m,
        wind_gusts_10m=reading.wind_gusts_10m,
        precipitation_probability=reading.precipitation_probability,
        temperature_2m=reading.temperature_2m,
        unknown=True,
        note=f"retractable roof, state unknown (roof={roof!r}); no wind adjustment",
    )


def kickoff_time(gameday, gametime) -> dt.datetime | None:
    """A ``games.csv`` date and clock time as one naive US/Eastern datetime."""
    if _missing(gameday) or _missing(gametime):
        return None
    try:
        return dt.datetime.fromisoformat(f"{gameday}T{str(gametime)[:5]}")
    except ValueError:
        return None


def week_weather(
    season: int,
    week: int,
    *,
    client: httpx.Client | None = None,
    ttl: float = FORECAST_TTL,
) -> dict[str, GameWeather]:
    """``{team: GameWeather}`` for one week, both sides of every game.

    Teams on bye are simply absent.  The Rams answer to both spellings, as
    everywhere else a team code is a dict key.
    """
    sched = stadium_info(season, client=client)
    sched = sched[sched["week"] == week]

    out: dict[str, GameWeather] = {}
    for row in sched.itertuples(index=False):
        got = game_weather(
            roof=row.roof,
            lat=row.lat,
            lon=row.lon,
            kickoff=kickoff_time(row.gameday, row.gametime),
            neutral_site=bool(row.neutral_site),
            client=client,
            ttl=ttl,
        )
        out[row.home_team] = got
        out[row.away_team] = got

    for sleeper_code, nflverse_code in TEAM_ALIASES.items():
        if nflverse_code in out:
            out[sleeper_code] = out[nflverse_code]
        elif sleeper_code in out:
            out[nflverse_code] = out[sleeper_code]
    return out
