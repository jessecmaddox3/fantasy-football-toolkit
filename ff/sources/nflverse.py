"""nflverse schedule data: byes, kickoff times, roof and stadium coordinates.

Unknown roofs and neutral venues remain explicitly unknown. See NOTICE.md
for upstream attribution and data terms. Stadium coordinates require periodic
maintenance when teams change venues."""

from __future__ import annotations

import io

import httpx
import pandas as pd

from ff.cache import WEEK, fetch_text

GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"

#: Home-venue coordinates for the 2026 season, used for Open-Meteo lookups.
#: LA and LAC share SoFi; NYG and NYJ share MetLife.  Neutral-site games get no
#: entry -- see :func:`stadium_info`.
STADIUM_COORDS: dict[str, tuple[float, float]] = {
    "ARI": (33.5277, -112.2626),   # State Farm Stadium (retractable)
    "ATL": (33.7554, -84.4008),    # Mercedes-Benz Stadium (retractable)
    "BAL": (39.2780, -76.6227),    # M&T Bank Stadium
    "BUF": (42.7736, -78.7862),    # Highmark Stadium (new build, opens 2026)
    "CAR": (35.2258, -80.8528),    # Bank of America Stadium
    "CHI": (41.8623, -87.6167),    # Soldier Field
    "CIN": (39.0955, -84.5161),    # Paycor Stadium
    "CLE": (41.5061, -81.6995),    # Huntington Bank Field
    "DAL": (32.7473, -97.0945),    # AT&T Stadium (retractable)
    "DEN": (39.7439, -105.0201),   # Empower Field at Mile High
    "DET": (42.3400, -83.0456),    # Ford Field (dome)
    "GB":  (44.5013, -88.0622),    # Lambeau Field
    "HOU": (29.6847, -95.4107),    # NRG Stadium (retractable)
    "IND": (39.7601, -86.1639),    # Lucas Oil Stadium (retractable)
    "JAX": (30.3239, -81.6373),    # EverBank Stadium
    "KC":  (39.0489, -94.4839),    # GEHA Field at Arrowhead
    "LA":  (33.9535, -118.3392),   # SoFi Stadium (dome)
    "LAC": (33.9535, -118.3392),   # SoFi Stadium (dome)
    "LV":  (36.0909, -115.1833),   # Allegiant Stadium (dome)
    "MIA": (25.9580, -80.2389),    # Hard Rock Stadium
    "MIN": (44.9736, -93.2575),    # U.S. Bank Stadium (dome)
    "NE":  (42.0909, -71.2643),    # Gillette Stadium
    "NO":  (29.9511, -90.0812),    # Caesars Superdome (dome)
    "NYG": (40.8135, -74.0745),    # MetLife Stadium
    "NYJ": (40.8135, -74.0745),    # MetLife Stadium
    "PHI": (39.9008, -75.1675),    # Lincoln Financial Field
    "PIT": (40.4468, -80.0158),    # Acrisure Stadium
    "SEA": (47.5952, -122.3316),   # Lumen Field
    "SF":  (37.4030, -121.9700),   # Levi's Stadium
    "TB":  (27.9759, -82.5033),    # Raymond James Stadium
    "TEN": (36.1665, -86.7713),    # Nissan Stadium
    "WAS": (38.9076, -76.8645),    # Northwest Stadium
}

#: nflverse spells the Rams ``LA``; Sleeper and ESPN spell them ``LAR``.  That
#: one mismatch silently dropped every Rams bye week off the draft board, so
#: both spellings are carried everywhere a team code is a key.
TEAM_ALIASES = {"LAR": "LA"}

STADIUM_COORDS["LAR"] = STADIUM_COORDS["LA"]


#: The same mapping read the other way, for joining nflverse or ESPN rows onto
#: a Sleeper roster.
REVERSE_ALIASES = {v: k for k, v in TEAM_ALIASES.items()}


def alias_team(team: str) -> str:
    """Map a Sleeper/ESPN team code onto the nflverse spelling."""
    return TEAM_ALIASES.get(team, team)


def sleeper_team(team: str) -> str:
    """Map an nflverse team code onto the Sleeper spelling."""
    return REVERSE_ALIASES.get(team, team)


#: Roofs where wind and precipitation cannot reach the field.
INDOOR_ROOFS = {"dome", "closed"}


def games(
    season: int, *, client: httpx.Client | None = None, ttl: float = WEEK
) -> pd.DataFrame:
    """Regular- and post-season schedule rows for one season."""
    text = fetch_text(GAMES_URL, key="nflverse_games", ttl=ttl, client=client)
    df = pd.read_csv(io.StringIO(text), low_memory=False)
    df = df[df["season"] == season].copy()
    df["week"] = df["week"].astype(int)
    df["season"] = df["season"].astype(int)
    return df.reset_index(drop=True)


def byes(
    season: int, *, client: httpx.Client | None = None, ttl: float = WEEK
) -> dict[str, int]:
    """``{team: bye week}`` derived by finding the week each team does not appear.

    A team with no idle week in the data (or more than one, which would mean a
    truncated file) is left out rather than guessed at.
    """
    sched = games(season, client=client, ttl=ttl)
    sched = sched[sched["game_type"] == "REG"]
    weeks = set(sched["week"].unique().tolist())

    played: dict[str, set[int]] = {}
    for col in ("home_team", "away_team"):
        for team, week in zip(sched[col], sched["week"]):
            played.setdefault(team, set()).add(int(week))

    out = {}
    for team, team_weeks in played.items():
        idle = sorted(weeks - team_weeks)
        if len(idle) == 1:
            out[team] = idle[0]
    for alias, canonical in TEAM_ALIASES.items():
        if canonical in out:
            out[alias] = out[canonical]
    return out


def stadium_info(
    season: int, *, client: httpx.Client | None = None, ttl: float = WEEK
) -> pd.DataFrame:
    """One row per game with venue, roof, and (where known) coordinates.

    ``neutral_site`` games -- the eight 2026 international dates -- carry NaN
    coordinates on purpose: identify them by the ``stadium`` name.
    """
    sched = games(season, client=client, ttl=ttl)
    out = sched[
        [
            "game_id",
            "week",
            "gameday",
            "gametime",
            "away_team",
            "home_team",
            "location",
            "roof",
            "surface",
            "stadium_id",
            "stadium",
        ]
    ].copy()

    out["neutral_site"] = out["location"].ne("Home").astype(bool)
    out["roof"] = out["roof"].fillna("").replace("", "unknown")
    out["indoor"] = out["roof"].isin(INDOOR_ROOFS)

    coords = out.apply(
        lambda r: (float("nan"), float("nan"))
        if r["neutral_site"]
        else STADIUM_COORDS.get(r["home_team"], (float("nan"), float("nan"))),
        axis=1,
    )
    out["lat"] = [c[0] for c in coords]
    out["lon"] = [c[1] for c in coords]
    return out.reset_index(drop=True)
