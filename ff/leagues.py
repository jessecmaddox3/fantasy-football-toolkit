"""League references and normalized Sleeper/ESPN rules.

Read waiver_type before waiver_budget. settings.draft_rounds represents
future tradable draft rounds, not the length of the current draft."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from ff.sources.espn import SLOT_BY_ID as ESPN_SLOT_BY_ID


#: Sleeper's ``waiver_day_of_week`` is 0-indexed from Monday.  The example reports 2,
#: which is Wednesday -- verified against 2025 transaction timestamps.
SLEEPER_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

SLEEPER_FORMATS = {0: "redraft", 1: "keeper", 2: "dynasty"}

#: roster_positions entries that are not starting lineup slots.
SLEEPER_NON_STARTING = {"BN", "IR", "TAXI"}

SUPERFLEX_SLOTS = {"SUPER_FLEX", "OP", "QB/RB/WR/TE"}

#: ESPN ``statId`` -> the Sleeper stat name the projections use.  Only IDs whose
#: meaning is unambiguous are mapped; anything else is reported in
#: ``unmapped_stat_ids`` rather than guessed at.  ESPN's kicking and D/ST items
#: are among the unmapped ones -- see LeagueRules.unmapped_stat_ids.
ESPN_STAT_TO_SLEEPER = {
    0: "pass_att",
    1: "pass_cmp",
    3: "pass_yd",
    4: "pass_td",
    19: "pass_2pt",
    20: "pass_int",
    23: "rush_att",
    24: "rush_yd",
    25: "rush_td",
    26: "rush_2pt",
    42: "rec_yd",
    43: "rec_td",
    44: "rec_2pt",
    53: "rec",
    58: "rec_tgt",
    72: "fum_lost",
    86: "xpm",
}


@dataclass(frozen=True)
class LeagueRef:
    """Registry entry: how to reach a league, before any rules are loaded."""

    key: str
    name: str
    platform: str
    league_id: str
    season: int = 2026
    team_id: int | None = None          # ESPN team id
    user_id: str | None = None          # Sleeper user id
    draft_id: str | None = None
    note: str = ""




@dataclass(frozen=True)
class LeagueRules:
    """Platform-independent league rules."""

    key: str
    name: str
    platform: str
    league_id: str
    format: str                          # redraft | keeper | dynasty
    num_teams: int
    scoring: dict[str, float]
    roster_slots: dict[str, int]         # starting slots only
    bench: int
    ir: int
    taxi: int
    waiver_type: str                     # faab | priority
    budget: int | None
    waiver_day: str | None
    waiver_days: tuple[str, ...]
    clear_days: int | None
    trade_deadline_week: int | None
    playoff_start: int | None
    playoff_teams: int | None = None
    trade_deadline_date: datetime | None = None
    superflex: bool = False
    #: Sleeper's ``settings.bench_lock``: the whole bench locks at the first
    #: kickoff of the week.  ESPN locks each player at his own kickoff instead,
    #: so it reads False there.
    bench_lock: bool = False
    unmapped_stat_ids: tuple[int, ...] = field(default_factory=tuple)

    @property
    def starters(self) -> int:
        return sum(self.roster_slots.values())


def _week_for_date(when: datetime, schedule: pd.DataFrame | None) -> int | None:
    """The NFL week whose games straddle ``when``."""
    if schedule is None or when is None or schedule.empty:
        return None
    days = pd.to_datetime(schedule["gameday"])
    target = pd.Timestamp(when.date())
    on_or_before = schedule[days <= target]
    if on_or_before.empty:
        return None
    return int(on_or_before.loc[days[days <= target].idxmax(), "week"])


def _normalize_sleeper(raw: dict, key: str | None, ref: LeagueRef | None) -> LeagueRules:
    settings = raw.get("settings") or {}
    positions = raw.get("roster_positions") or []

    slots: dict[str, int] = {}
    bench = 0
    for slot in positions:
        if slot == "BN":
            bench += 1
        elif slot in SLEEPER_NON_STARTING:
            continue
        else:
            slots[slot] = slots.get(slot, 0) + 1

    waiver_type = "faab" if settings.get("waiver_type") == 2 else "priority"
    day_index = settings.get("waiver_day_of_week")
    waiver_day = (
        SLEEPER_WEEKDAYS[day_index]
        if isinstance(day_index, int) and 0 <= day_index < 7
        else None
    )

    return LeagueRules(
        key=key or raw.get("league_id", ""),
        name=raw.get("name", ""),
        platform="sleeper",
        league_id=str(raw.get("league_id") or (ref.league_id if ref else "")),
        format=SLEEPER_FORMATS.get(settings.get("type"), "redraft"),
        num_teams=int(raw.get("total_rosters") or settings.get("num_teams") or 0),
        scoring={k: float(v) for k, v in (raw.get("scoring_settings") or {}).items()},
        roster_slots=slots,
        bench=bench,
        ir=int(settings.get("reserve_slots") or 0),
        taxi=int(settings.get("taxi_slots") or 0),
        waiver_type=waiver_type,
        # waiver_budget is populated even when waivers run on priority.
        budget=int(settings["waiver_budget"]) if waiver_type == "faab" and settings.get("waiver_budget") is not None else None,
        waiver_day=waiver_day,
        waiver_days=(waiver_day,) if waiver_day else (),
        clear_days=settings.get("waiver_clear_days"),
        trade_deadline_week=settings.get("trade_deadline"),
        playoff_start=settings.get("playoff_week_start"),
        playoff_teams=settings.get("playoff_teams"),
        superflex=any(s in SUPERFLEX_SLOTS for s in slots),
        bench_lock=bool(settings.get("bench_lock")),
    )


def _normalize_espn(
    raw: dict, key: str | None, ref: LeagueRef | None, schedule: pd.DataFrame | None
) -> LeagueRules:
    settings = raw.get("settings") or {}
    roster = settings.get("rosterSettings") or {}
    acq = settings.get("acquisitionSettings") or {}
    trade = settings.get("tradeSettings") or {}
    sched = settings.get("scheduleSettings") or {}

    counts = {int(k): v for k, v in (roster.get("lineupSlotCounts") or {}).items()}
    slots = {
        ESPN_SLOT_BY_ID.get(sid, str(sid)): n
        for sid, n in counts.items()
        if n and sid not in (20, 21)
    }

    scoring: dict[str, float] = {}
    unmapped: list[int] = []
    for item in (settings.get("scoringSettings") or {}).get("scoringItems", []):
        stat_id = item.get("statId")
        name = ESPN_STAT_TO_SLEEPER.get(stat_id)
        if name is None:
            unmapped.append(stat_id)
            continue
        scoring[name] = float(item.get("points") or 0.0)

    uses_faab = bool(acq.get("isUsingAcquisitionBudget"))
    waiver_days = tuple(
        d.capitalize()
        for d in ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
        if d in set(acq.get("waiverProcessDays") or [])
    )

    deadline_ms = trade.get("deadlineDate")
    deadline = (
        datetime.fromtimestamp(deadline_ms / 1000, tz=timezone.utc) if deadline_ms else None
    )

    matchups = sched.get("matchupPeriodCount")
    playoff_teams = sched.get("playoffTeamCount")
    playoff_len = sched.get("playoffMatchupPeriodLength") or 1
    playoff_rounds = 0
    while playoff_teams and 2 ** playoff_rounds < playoff_teams:
        playoff_rounds += 1
    playoff_start = (
        matchups - playoff_rounds * playoff_len + 1 if matchups and playoff_rounds else None
    )

    waiver_hours = acq.get("waiverHours")
    return LeagueRules(
        key=key or str(raw.get("id", "")),
        name=settings.get("name", ""),
        platform="espn",
        league_id=str(raw.get("id") or (ref.league_id if ref else "")),
        format="keeper" if (settings.get("draftSettings") or {}).get("keeperCount") else "redraft",
        num_teams=int(settings.get("size") or 0),
        scoring=scoring,
        roster_slots=slots,
        bench=int(counts.get(20, 0)),
        ir=int(counts.get(21, 0)),
        taxi=0,
        waiver_type="faab" if uses_faab else "priority",
        budget=int(acq.get("acquisitionBudget")) if uses_faab else None,
        waiver_day=", ".join(waiver_days) if waiver_days else None,
        waiver_days=waiver_days,
        clear_days=round(waiver_hours / 24) if waiver_hours else None,
        trade_deadline_week=_week_for_date(deadline, schedule),
        playoff_start=playoff_start,
        playoff_teams=playoff_teams,
        trade_deadline_date=deadline,
        superflex=any(s in SUPERFLEX_SLOTS for s in slots),
        bench_lock=False,  # ESPN locks each player at his own kickoff
        unmapped_stat_ids=tuple(sorted(set(unmapped))),
    )


def normalize_league(
    raw: dict,
    *,
    key: str | None = None,
    schedule: pd.DataFrame | None = None,
) -> LeagueRules:
    """Turn a raw Sleeper or ESPN league payload into :class:`LeagueRules`.

    Pass ``schedule`` (an ``nflverse.games`` frame) to resolve ESPN's absolute
    trade-deadline date into an NFL week.
    """
    ref = None
    if "roster_positions" in raw or "scoring_settings" in raw:
        return _normalize_sleeper(raw, key, ref)
    if "settings" in raw and "rosterSettings" in (raw.get("settings") or {}):
        return _normalize_espn(raw, key, ref, schedule)
    raise ValueError("unrecognized league payload: neither Sleeper nor ESPN shaped")


def adp_field(rules: LeagueRules) -> str:
    """The Sleeper ADP column that matches this league's format.

    Sleeper only publishes superflex ADP in two flavours (``adp_2qb`` and
    ``adp_dynasty_2qb``), so a superflex league gets those regardless of PPR.
    """
    dynasty = rules.format == "dynasty"
    if rules.superflex:
        return "adp_dynasty_2qb" if dynasty else "adp_2qb"

    rec = rules.scoring.get("rec", 0.0)
    base = "ppr" if rec >= 1.0 else "half_ppr" if rec >= 0.5 else "std"
    return f"adp_dynasty_{base}" if dynasty else f"adp_{base}"
