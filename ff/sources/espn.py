"""Unofficial, read-only ESPN fantasy integration.

All requests are GETs. Optional browser cookies come from environment variables
or a local .env file; they are never bundled. Endpoints may change. Missing
weekly projections remain NaN, distinct from a real zero."""

from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path

import httpx
import pandas as pd

from ff.cache import HOUR, fetch_json

HOST = "https://lm-api-reads.fantasy.espn.com"
DEFAULT_SEASON = 2026

DEFAULT_VIEWS = (
    "mSettings",
    "mTeam",
    "mRoster",
    "mDraftDetail",
    "mMatchup",
    "mPendingTransactions",
)

#: ESPN ``defaultPositionId`` -> position.
POSITION_BY_ID = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}

#: ESPN ``lineupSlotId`` -> slot name.
SLOT_BY_ID = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 16: "DEF", 17: "K", 18: "P", 19: "HC", 20: "BN",
    21: "IR", 22: "UNKNOWN", 23: "FLEX", 24: "EDR",
}

#: ESPN ``proTeamId`` -> nflverse-style abbreviation.
PRO_TEAM_BY_ID = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LA", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB",
    28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

#: Offensive + K + DST slots, the pool a redraft board cares about.
OFFENSIVE_SLOT_IDS = [0, 2, 4, 6, 16, 17, 23]


def load_cookies(env_path: Path | str | None = None) -> dict[str, str]:
    """Read ``ESPN_S2`` and ``SWID`` out of ``.env`` (never committed)."""
    path = Path(env_path) if env_path else Path(os.environ.get("FF_ESPN_ENV", Path.cwd() / ".env"))
    if env_path is not None and not path.exists():
        raise FileNotFoundError(f"ESPN cookies: no env file at {path}")

    values: dict[str, str] = {}
    for line in (path.read_text().splitlines() if path.exists() else []):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")

    if env_path is None:
        values.update({k: os.environ[k] for k in ("ESPN_S2", "SWID") if k in os.environ})
    missing = [k for k in ("ESPN_S2", "SWID") if not values.get(k)]
    if missing:
        raise FileNotFoundError(f"ESPN cookies: {', '.join(missing)} missing from {path}")
    return {"espn_s2": values["ESPN_S2"], "SWID": values["SWID"]}


def _cache_scope(cookies: dict, selection: list) -> str:
    """Partition cached private responses by account and requested view/slots."""
    material = json.dumps([cookies, selection], sort_keys=True).encode()
    return hashlib.sha256(material).hexdigest()[:24]


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def league(
    league_id: int,
    *,
    season: int = DEFAULT_SEASON,
    views: tuple[str, ...] | list[str] = DEFAULT_VIEWS,
    cookies: dict[str, str] | None = None,
    client: httpx.Client | None = None,
    ttl: float = HOUR,
) -> dict:
    """Full league payload for the requested ``views``."""
    cookies = cookies or load_cookies()
    return fetch_json(
        f"{HOST}/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}",
        key=f"espn_league_{league_id}_{season}_{_cache_scope(cookies, list(views))}",
        ttl=ttl,
        params=[("view", v) for v in views],
        headers={"Cookie": _cookie_header(cookies)},
        client=client,
    )


def _fantasy_filter(week: int, season: int, limit: int, slot_ids: list[int]) -> str:
    return json.dumps(
        {
            "players": {
                "filterSlotIds": {"value": slot_ids},
                "filterStatsForTopScoringPeriodIds": {
                    "value": week,
                    "additionalValue": [
                        f"00{season}",
                        f"10{season}",
                        f"00{season - 1}",
                        f"02{season}",
                        f"11{season}{week:02d}",
                    ],
                },
                "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
                "limit": limit,
                "offset": 0,
            }
        }
    )


def players_projections(
    week: int,
    *,
    season: int = DEFAULT_SEASON,
    limit: int = 500,
    slot_ids: list[int] | None = None,
    cookies: dict[str, str] | None = None,
    client: httpx.Client | None = None,
    ttl: float = HOUR,
) -> pd.DataFrame:
    """Player pool with ESPN's own projections and percent-owned.

    ``proj_week`` is ESPN's projection for ``week`` (``statSourceId == 1``,
    ``scoringPeriodId == week``) and ``proj_season`` is its full-season
    projection.  See the module docstring: before week 1, only the season figure
    exists.
    """
    cookies = cookies or load_cookies()
    raw = fetch_json(
        f"{HOST}/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3",
        key=f"espn_kona_{season}_{week}_{limit}_{_cache_scope(cookies, slot_ids or OFFENSIVE_SLOT_IDS)}",
        ttl=ttl,
        params={"view": "kona_player_info", "scoringPeriodId": week},
        headers={
            "Cookie": _cookie_header(cookies),
            "x-fantasy-filter": _fantasy_filter(
                week, season, limit, slot_ids or OFFENSIVE_SLOT_IDS
            ),
        },
        client=client,
    )

    rows = []
    for entry in raw.get("players", []):
        p = entry.get("player") or {}
        proj_week = proj_season = float("nan")
        for stat in p.get("stats") or []:
            if stat.get("statSourceId") != 1 or stat.get("seasonId") != season:
                continue
            if stat.get("statSplitTypeId") == 0:
                proj_season = stat.get("appliedTotal", float("nan"))
            elif stat.get("scoringPeriodId") == week:
                proj_week = stat.get("appliedTotal", float("nan"))

        ownership = p.get("ownership") or {}
        rows.append(
            {
                "espn_id": p.get("id"),
                "name": p.get("fullName"),
                "pos": POSITION_BY_ID.get(p.get("defaultPositionId")),
                "team": PRO_TEAM_BY_ID.get(p.get("proTeamId")),
                "injury_status": p.get("injuryStatus"),
                "injured": p.get("injured"),
                "eligible_slots": [SLOT_BY_ID.get(s) for s in p.get("eligibleSlots") or []],
                "pct_owned": ownership.get("percentOwned"),
                "pct_started": ownership.get("percentStarted"),
                "on_team_id": entry.get("onTeamId"),
                "week": week,
                "proj_week": proj_week,
                "proj_season": proj_season,
            }
        )
    return pd.DataFrame(rows)
