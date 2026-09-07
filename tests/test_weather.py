import datetime as dt

import httpx
import pytest

from ff.sources import weather
from tests.conftest import RecordingRoutes, fixture_json, fixture_text

FORECAST = fixture_json("openmeteo_forecast.json")
GAMES = fixture_text("nflverse_games.csv")

SUNDAY_1PM = dt.datetime(2026, 9, 13, 13, 0)


def windy(mph, gusts=None):
    """A forecast whose every hour blows at ``mph``."""
    hours = [f"2026-09-13T{h:02d}:00" for h in range(24)]
    return {
        "hourly_units": {"wind_speed_10m": "mp/h"},
        "hourly": {
            "time": hours,
            "wind_speed_10m": [mph] * 24,
            "wind_gusts_10m": [gusts if gusts is not None else mph * 1.4] * 24,
            "precipitation_probability": [10] * 24,
            "temperature_2m": [55.0] * 24,
        },
    }


def routes(forecast=FORECAST, **extra):
    base = {"api.open-meteo.com": forecast, "games.csv": GAMES}
    base.update(extra)
    return RecordingRoutes(base)


# ------------------------------------------------------------------ forecast

def test_forecast_returns_the_four_variables_at_the_kickoff_hour():
    got = weather.forecast(47.5952, -122.3316, SUNDAY_1PM, client=routes().client())

    assert got.wind_speed_10m == pytest.approx(3.0)
    assert got.wind_gusts_10m == pytest.approx(6.0)
    assert got.precipitation_probability == pytest.approx(10)
    assert got.temperature_2m == pytest.approx(63.0)
    assert not got.indoor and not got.unknown


def test_forecast_asks_open_meteo_for_mph_and_eastern_kickoff_times():
    """Kickoff times come out of nflverse in ET, so the series must be in ET."""
    r = routes()
    weather.forecast(47.5952, -122.3316, SUNDAY_1PM, client=r.client())

    url = str(r.requests[0].url)
    assert "wind_speed_unit=mph" in url
    assert "timezone=America%2FNew_York" in url or "timezone=America/New_York" in url
    assert "wind_gusts_10m" in url


def test_forecast_picks_the_hour_the_game_starts_not_the_first_hour():
    got = weather.forecast(47.5952, -122.3316, dt.datetime(2026, 9, 13, 20, 0),
                           client=routes().client())
    assert got.temperature_2m == pytest.approx(70.0)


def test_a_kickoff_outside_the_forecast_window_reads_unknown():
    got = weather.forecast(47.5952, -122.3316, dt.datetime(2026, 11, 30, 13, 0),
                           client=routes().client())
    assert got.unknown
    assert got.wind_speed_10m is None
    assert "forecast" in got.note


def test_an_open_meteo_error_reads_unknown_rather_than_raising():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400)))
    got = weather.forecast(47.5, -122.3, SUNDAY_1PM, client=client)
    assert got.unknown and got.wind == 0.0


def test_forecast_is_cached_per_stadium_and_day(tmp_cache_dir):
    r = routes()
    client = r.client()
    weather.forecast(47.5952, -122.3316, SUNDAY_1PM, client=client)
    weather.forecast(47.5952, -122.3316, dt.datetime(2026, 9, 13, 16, 0), client=client)

    assert len(r.requests) == 1, "both kickoffs read the same day's hourly series"


# ------------------------------------------------------------- game_weather

def test_a_dome_never_calls_open_meteo_and_carries_no_wind():
    r = routes()
    got = weather.game_weather(
        roof="dome", lat=42.34, lon=-83.05, kickoff=SUNDAY_1PM, client=r.client()
    )
    assert got.indoor and got.wind == 0.0
    assert r.requests == []


def test_a_closed_retractable_roof_is_treated_as_indoors():
    got = weather.game_weather(
        roof="closed", lat=32.7473, lon=-97.0945, kickoff=SUNDAY_1PM,
        client=routes().client(),
    )
    assert got.indoor


def test_an_unknown_retractable_roof_is_flagged_and_never_penalised():
    """nflverse leaves `roof` blank for retractables until the game is played.

    Blank is 'unknown', not 'outdoors': ARI/ATL/DAL/HOU/IND would otherwise
    collect a wind penalty for games played under a closed roof.
    """
    got = weather.game_weather(
        roof="unknown", lat=29.6847, lon=-95.4107, kickoff=SUNDAY_1PM,
        client=routes(windy(28)).client(),
    )
    assert got.unknown
    assert got.wind == 0.0, "a retractable roof must never take a wind penalty"
    assert "retractable" in got.note
    assert got.wind_speed_10m == pytest.approx(28), "the reading is still reported"


def test_an_open_retractable_roof_is_scored_as_outdoors():
    got = weather.game_weather(
        roof="open", lat=29.6847, lon=-95.4107, kickoff=SUNDAY_1PM,
        client=routes(windy(22)).client(),
    )
    assert not got.unknown and not got.indoor
    assert got.wind == pytest.approx(22)


def test_a_neutral_site_game_has_no_coordinates_and_reads_unknown():
    r = routes()
    got = weather.game_weather(
        roof="outdoors", lat=float("nan"), lon=float("nan"), kickoff=SUNDAY_1PM,
        neutral_site=True, client=r.client(),
    )
    assert got.unknown and got.wind == 0.0
    assert "neutral" in got.note
    assert r.requests == []


def test_a_missing_coordinate_reads_unknown_even_at_a_home_game():
    got = weather.game_weather(
        roof="outdoors", lat=None, lon=None, kickoff=SUNDAY_1PM, client=routes().client()
    )
    assert got.unknown


def test_a_missing_kickoff_time_reads_unknown():
    got = weather.game_weather(
        roof="outdoors", lat=47.6, lon=-122.3, kickoff=None, client=routes().client()
    )
    assert got.unknown


# -------------------------------------------------------------- week_weather

def test_week_weather_keys_both_teams_in_every_game():
    got = weather.week_weather(2026, 1, client=routes().client())
    assert got["CIN"].wind_speed_10m is not None
    assert got["TB"] == got["CIN"], "both sides of a game share the venue"


def test_week_weather_answers_to_the_sleeper_spelling_of_the_rams():
    got = weather.week_weather(2026, 1, client=routes().client())
    assert got["LAR"] == got["LA"]


def test_week_weather_marks_the_international_game_unknown():
    got = weather.week_weather(2026, 1, client=routes().client())
    assert got["SF"].unknown
    assert got["LA"].unknown
