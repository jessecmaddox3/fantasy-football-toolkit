"""The configured manager's team in each league, in one shape.

Sleeper hands back a roster as a flat list of player ids plus a positional
``starters`` array.  ESPN hands back roster *entries* carrying a
``lineupSlotId`` and its own player ids.  :class:`Team` is what the lineup
engine and the weekly brief read, and it always looks like the Sleeper one:
Sleeper player ids, and a ``starters`` tuple that lines up index-for-index with
``lineup.starting_slots(rules)``.

ESPN player ids are translated to Sleeper ids by normalized name -- the same
join key the rookie board already uses -- because the projection source for all
configured leagues is Sleeper/Rotowire.  Defences skip the name join entirely and map
straight off ESPN's ``proTeamId``, since a Sleeper defence *is* its team code.
An ESPN player with no Sleeper match keeps an ``espn:<id>`` id and still shows
up on the roster with a "no projection" flag rather than vanishing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pandas as pd

from ff import leagues
from ff.leagues import LeagueRef, LeagueRules
from ff.sources import espn, nflverse, sleeper
from ff.sources.dynastyprocess import normalize_name

#: Sleeper reports an empty starting slot as the string zero.
EMPTY_SLOT = "0"

#: ESPN's roster slots that are not starting slots.
ESPN_BENCH_SLOTS = {20, 21}

#: ESPN writes injury status in caps; Sleeper uses title case, and the lineup
#: engine keys off the Sleeper spelling.
ESPN_INJURY_STATUS = {
    "ACTIVE": None,
    "NORMAL": None,
    "QUESTIONABLE": "Questionable",
    "DOUBTFUL": "Doubtful",
    "OUT": "Out",
    "INJURY_RESERVE": "IR",
    "SUSPENSION": "Sus",
    "PROBABLE": "Probable",
    "DAY_TO_DAY": "Questionable",
}


@dataclass(frozen=True)
class Team:
    """One manager's team in one league."""

    key: str
    name: str
    platform: str
    team_id: str
    player_ids: tuple[str, ...] = ()
    starters: tuple[str, ...] = ()
    reserve: tuple[str, ...] = ()
    taxi: tuple[str, ...] = ()
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    points_against: float = 0.0
    waiver_budget_used: int | None = None
    waiver_position: int | None = None
    fallbacks: dict[str, dict] = field(default_factory=dict)

    @property
    def record(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base

    @property
    def drafted(self) -> bool:
        """False before the league has drafted, when the roster is still empty."""
        return bool(self.player_ids)


def load_rules(
    ref: LeagueRef,
    *,
    season: int = 2026,
    client: httpx.Client | None = None,
    schedule: pd.DataFrame | None = None,
) -> LeagueRules:
    """Normalized rules for one league, whichever platform it lives on."""
    if ref.platform == "sleeper":
        raw = sleeper.league(ref.league_id, client=client)
    else:
        raw = espn.league(int(ref.league_id), season=season, client=client)
    if schedule is None:
        schedule = nflverse.games(season, client=client)
    return leagues.normalize_league(raw, key=ref.key, schedule=schedule)


# ------------------------------------------------------------------- Sleeper

def sleeper_team(ref: LeagueRef, rosters: list[dict]) -> Team:
    """The configured manager's roster out of a Sleeper ``/rosters`` payload."""
    mine = next(
        (r for r in rosters if str(r.get("owner_id")) == str(ref.user_id)), None
    )
    if mine is None:
        return Team(key=ref.key, name=ref.name, platform="sleeper", team_id="")

    settings = mine.get("settings") or {}
    starters = [str(p) for p in (mine.get("starters") or [])]
    return Team(
        key=ref.key,
        name=ref.name,
        platform="sleeper",
        team_id=str(mine.get("roster_id") or ""),
        player_ids=tuple(str(p) for p in (mine.get("players") or [])),
        starters=tuple(starters),
        reserve=tuple(str(p) for p in (mine.get("reserve") or [])),
        taxi=tuple(str(p) for p in (mine.get("taxi") or [])),
        wins=int(settings.get("wins") or 0),
        losses=int(settings.get("losses") or 0),
        ties=int(settings.get("ties") or 0),
        points_for=float(settings.get("fpts") or 0)
        + float(settings.get("fpts_decimal") or 0) / 100,
        points_against=float(settings.get("fpts_against") or 0),
        waiver_budget_used=settings.get("waiver_budget_used"),
        waiver_position=settings.get("waiver_position"),
    )


# ---------------------------------------------------------------------- ESPN

def _sleeper_ids_by_name(projections: pd.DataFrame) -> dict[str, str]:
    if projections is None or projections.empty:
        return {}
    return {
        normalize_name(name): str(pid)
        for name, pid in zip(projections["name"], projections["player_id"])
        if isinstance(name, str)
    }


def espn_team(
    ref: LeagueRef,
    raw: dict,
    rules: LeagueRules,
    *,
    projections: pd.DataFrame | None = None,
) -> Team:
    """The configured manager's ESPN team, translated into the Sleeper-shaped :class:`Team`."""
    from ff.engine.lineup import starting_slots

    team = next((t for t in (raw.get("teams") or []) if t.get("id") == ref.team_id), None)
    if team is None:
        return Team(key=ref.key, name=ref.name, platform="espn", team_id=str(ref.team_id))

    by_name = _sleeper_ids_by_name(projections)
    slots = starting_slots(rules)
    lineup_slots: list[str] = [EMPTY_SLOT] * len(slots)
    open_by_name: dict[str, list[int]] = {}
    for index, slot in enumerate(slots):
        open_by_name.setdefault(slot, []).append(index)

    player_ids: list[str] = []
    reserve_ids: list[str] = []
    fallbacks: dict[str, dict] = {}
    for entry in (team.get("roster") or {}).get("entries") or []:
        player = (entry.get("playerPoolEntry") or {}).get("player") or {}
        pos = espn.POSITION_BY_ID.get(player.get("defaultPositionId"))
        pro_team = nflverse.sleeper_team(
            espn.PRO_TEAM_BY_ID.get(player.get("proTeamId"), "") or ""
        )

        if pos == "DEF":
            player_id = pro_team
        else:
            player_id = by_name.get(normalize_name(player.get("fullName")))
        if not player_id:
            player_id = f"espn:{player.get('id')}"
            fallbacks[player_id] = {
                "name": player.get("fullName"),
                "pos": pos,
                "team": pro_team,
                "injury_status": ESPN_INJURY_STATUS.get(player.get("injuryStatus")),
            }

        player_ids.append(player_id)
        if entry.get("lineupSlotId") == 21:
            reserve_ids.append(player_id)

        slot_name = espn.SLOT_BY_ID.get(entry.get("lineupSlotId"))
        if entry.get("lineupSlotId") in ESPN_BENCH_SLOTS or not slot_name:
            continue
        free = open_by_name.get(slot_name) or []
        if free:
            lineup_slots[free.pop(0)] = player_id

    record = ((team.get("record") or {}).get("overall") or {})
    return Team(
        key=ref.key,
        name=team.get("name") or ref.name,
        platform="espn",
        team_id=str(team.get("id")),
        player_ids=tuple(player_ids),
        reserve=tuple(reserve_ids),
        starters=tuple(lineup_slots),
        wins=int(record.get("wins") or 0),
        losses=int(record.get("losses") or 0),
        ties=int(record.get("ties") or 0),
        points_for=float(record.get("pointsFor") or 0.0),
        points_against=float(record.get("pointsAgainst") or 0.0),
        waiver_position=(team.get("waiverRank")),
        fallbacks=fallbacks,
    )


# ------------------------------------------------------------------ dispatch

def load_roster(
    ref: LeagueRef,
    week: int | None = None,
    *,
    season: int = 2026,
    client: httpx.Client | None = None,
    rules: LeagueRules | None = None,
    projections: pd.DataFrame | None = None,
) -> Team:
    """The configured manager's team in ``ref``, fetched from whichever platform holds it."""
    if ref.platform == "sleeper":
        return sleeper_team(ref, sleeper.rosters(ref.league_id, client=client))

    if rules is None:
        rules = load_rules(ref, season=season, client=client)
    raw = espn.league(int(ref.league_id), season=season, client=client)
    return espn_team(ref, raw, rules, projections=projections)
