"""Sleeper read-only API.

Two hosts, both operated by Sleeper:

* ``api.sleeper.app/v1`` -- the documented read API (players, league, rosters,
  users, transactions, drafts).
* ``api.sleeper.com`` -- undocumented and subject to change, and the only source of the 12
  ADP fields plus Rotowire season/weekly projections.  See ``NOTICE.md``.

Everything here is read-only by design; the project never writes to Sleeper.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pandas as pd

from ff.cache import DAY, HOUR, fetch_json

V1 = "https://api.sleeper.app/v1"
API = "https://api.sleeper.com"

#: Positions we pull projections for.  IDP and P are deliberately excluded.
PROJECTION_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

#: Fields that are ADP, not projections.  Kept separate so callers never mistake
#: one for the other (``search_rank`` is not ADP).
ADP_FIELDS = (
    "adp_std",
    "adp_half_ppr",
    "adp_ppr",
    "adp_2qb",
    "adp_rookie",
    "adp_dynasty",
    "adp_dynasty_std",
    "adp_dynasty_half_ppr",
    "adp_dynasty_ppr",
    "adp_dynasty_2qb",
    "adp_idp",
    "adp_idp_1qb",
)

#: Sleeper reports "no ADP" as 999, which would sort like a real number.
ADP_SENTINEL = 999.0


def _position_params(order_by: str, positions=PROJECTION_POSITIONS) -> list[tuple[str, str]]:
    params = [("season_type", "regular")]
    params += [("position[]", p) for p in positions]
    params.append(("order_by", order_by))
    return params


def _projection_frame(raw: list[dict]) -> pd.DataFrame:
    rows = []
    for rec in raw:
        player = rec.get("player") or {}
        row = {
            "player_id": rec.get("player_id"),
            "name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
            "pos": player.get("position"),
            "team": rec.get("team") or player.get("team"),
            "years_exp": player.get("years_exp"),
            "injury_status": player.get("injury_status"),
            "news_updated": player.get("news_updated"),
            "opponent": rec.get("opponent"),
            "week": rec.get("week"),
            "season": rec.get("season"),
            "company": rec.get("company"),
        }
        # Stats stay un-filled: a WR with no pass_td must read NaN, not 0.
        row.update(rec.get("stats") or {})
        rows.append(row)

    df = pd.DataFrame(rows)
    for col in ADP_FIELDS:
        if col in df.columns:
            df[col] = df[col].replace(ADP_SENTINEL, pd.NA).astype("Float64")
        else:
            df[col] = pd.Series([pd.NA] * len(df), dtype="Float64")
    return df


def season_projections(
    season: int = 2026,
    *,
    client: httpx.Client | None = None,
    ttl: float = DAY,
) -> pd.DataFrame:
    """Full-season Rotowire projections plus all 12 ADP fields."""
    raw = fetch_json(
        f"{API}/projections/nfl/{season}",
        key=f"sleeper_season_projections_{season}",
        ttl=ttl,
        params=_position_params("adp_ppr"),
        client=client,
    )
    return _projection_frame(raw)


def weekly_projections(
    season: int,
    week: int,
    *,
    client: httpx.Client | None = None,
    ttl: float = 6 * HOUR,
) -> pd.DataFrame:
    """Per-week Rotowire projections (``pts_ppr`` / ``pts_half_ppr`` included)."""
    raw = fetch_json(
        f"{API}/projections/nfl/{season}/{week}",
        key=f"sleeper_week_projections_{season}_{week}",
        ttl=ttl,
        params=_position_params("pts_ppr"),
        client=client,
    )
    df = _projection_frame(raw)
    if "week" in df.columns:
        df["week"] = pd.to_numeric(df["week"], errors="coerce").fillna(week).astype(int)
    return df


def players(*, client: httpx.Client | None = None, ttl: float = DAY) -> dict:
    """The ~15 MB player dump.  Sleeper asks for at most one pull per day."""
    return fetch_json(
        f"{V1}/players/nfl", key="sleeper_players_nfl", ttl=ttl, client=client
    )


def league(league_id: str, *, client: httpx.Client | None = None, ttl: float = HOUR) -> dict:
    return fetch_json(
        f"{V1}/league/{league_id}", key=f"sleeper_league_{league_id}", ttl=ttl, client=client
    )


def rosters(league_id: str, *, client: httpx.Client | None = None, ttl: float = 5 * 60) -> list:
    return fetch_json(
        f"{V1}/league/{league_id}/rosters",
        key=f"sleeper_rosters_{league_id}",
        ttl=ttl,
        client=client,
    )


def matchups(
    league_id: str, week: int, *, client: httpx.Client | None = None, ttl: float = 5 * 60
) -> list:
    """One row per roster: ``starters``, ``starters_points`` and
    ``players_points``, which is what "points left on the bench" is computed from.

    Rows exist for a week before it is played, with every point at 0.0.
    """
    return fetch_json(
        f"{V1}/league/{league_id}/matchups/{week}",
        key=f"sleeper_matchups_{league_id}_{week}",
        ttl=ttl,
        client=client,
    )


def state(*, client: httpx.Client | None = None, ttl: float = HOUR) -> dict:
    """Which NFL week the league is on, per Sleeper itself."""
    return fetch_json(f"{V1}/state/nfl", key="sleeper_state_nfl", ttl=ttl, client=client)


def users(league_id: str, *, client: httpx.Client | None = None, ttl: float = DAY) -> list:
    return fetch_json(
        f"{V1}/league/{league_id}/users",
        key=f"sleeper_users_{league_id}",
        ttl=ttl,
        client=client,
    )


def transactions(
    league_id: str, week: int, *, client: httpx.Client | None = None, ttl: float = 5 * 60
) -> list:
    return fetch_json(
        f"{V1}/league/{league_id}/transactions/{week}",
        key=f"sleeper_transactions_{league_id}_{week}",
        ttl=ttl,
        client=client,
    )


def draft(draft_id: str, *, client: httpx.Client | None = None, ttl: float = HOUR) -> dict:
    return fetch_json(
        f"{V1}/draft/{draft_id}", key=f"sleeper_draft_{draft_id}", ttl=ttl, client=client
    )


def draft_picks(draft_id: str, *, client: httpx.Client | None = None) -> list:
    """Live draft picks.

    This endpoint is edge-cached for a few seconds, which during a live draft
    made a landed pick look like it had not landed.  Always uncached on our side
    and always cache-busted upstream.
    """
    return fetch_json(
        f"{V1}/draft/{draft_id}/picks",
        key=f"sleeper_draft_picks_{draft_id}",
        ttl=0,
        params={"t": uuid4().hex},
        client=client,
    )
