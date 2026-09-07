"""The Tuesday weekly brief: one Markdown page across the configured leagues.

What a manager needs on a Tuesday morning is not a data dump, it is the
short list of things that will cost him points if he does nothing.  So every
league section ends in **Decisions**, and everything above it exists only to
justify those lines.

Per league: standings and record, last week's result with the top and bottom
starter, the points left on the bench (solved with the same exact assignment
the lineup engine uses, against *actual* scores rather than projections),
injuries on the roster, byes over the next three weeks, waiver state, and the
deadlines that are close enough to matter.

Two honest gaps, both surfaced in the output rather than hidden:

* ESPN publishes no per-week box score through the read API views this project
  uses, so an ESPN section carries standings and roster state but no
  "points left on the bench" line.
* ESPN's kicking and D/ST scoring items have no unambiguous Sleeper equivalent
  (``LeagueRules.unmapped_stat_ids``), so those two positions project low there.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import httpx
import pandas as pd

from ff import roster as roster_module
from ff.engine.lineup import (
    LineupResult,
    OUT_STATUSES,
    PlayerLine,
    assign_slots,
    league_lineup,
    now_eastern,
    starting_slots,
)
from ff.leagues import LeagueRef, LeagueRules
from ff.sources import nflverse, sleeper

DEFAULT_SEASON = 2026

#: How far ahead the bye-week warning looks.
BYE_HORIZON = 3

#: Below this many projected points, a lineup change is noise.
SWAP_THRESHOLD = 1.0

#: A deadline this many weeks out is worth a line in the brief.
DEADLINE_HORIZON = 3


@dataclass(frozen=True)
class Standing:
    name: str
    owner: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    mine: bool = False

    @property
    def record(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base


@dataclass(frozen=True)
class WeekResult:
    """One played week, from The configured manager's side."""

    week: int
    points: float
    opponent: str | None = None
    opponent_points: float | None = None
    best_possible: float | None = None
    top: tuple[str, float] | None = None
    bottom: tuple[str, float] | None = None

    @property
    def result(self) -> str:
        if self.opponent_points is None:
            return "no opponent"
        if self.points > self.opponent_points:
            return "W"
        return "L" if self.points < self.opponent_points else "T"

    @property
    def left_on_bench(self) -> float | None:
        if self.best_possible is None:
            return None
        return max(self.best_possible - self.points, 0.0)


@dataclass(frozen=True)
class LeagueBrief:
    """Everything the brief says about one league."""

    ref: LeagueRef
    rules: LeagueRules
    team: roster_module.Team
    lineup: LineupResult | None = None
    standings: tuple[Standing, ...] = ()
    last_week: WeekResult | None = None
    injuries: tuple[PlayerLine, ...] = ()
    byes: dict[int, tuple[str, ...]] = field(default_factory=dict)
    decisions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def my_standing(self) -> Standing | None:
        return next((s for s in self.standings if s.mine), None)


# ------------------------------------------------------------------ pieces

def sleeper_standings(
    rosters: list[dict], users: list[dict], user_id: str
) -> tuple[Standing, ...]:
    """Standings ordered the way the league table is: record, then points for."""
    names = {
        str(u.get("user_id")): (u.get("metadata") or {}).get("team_name")
        or u.get("display_name")
        or "unknown"
        for u in users
    }
    owners = {str(u.get("user_id")): u.get("display_name") or "unknown" for u in users}

    out = []
    for row in rosters:
        settings = row.get("settings") or {}
        owner_id = str(row.get("owner_id"))
        out.append(
            Standing(
                name=names.get(owner_id, f"roster {row.get('roster_id')}"),
                owner=owners.get(owner_id, "unknown"),
                wins=int(settings.get("wins") or 0),
                losses=int(settings.get("losses") or 0),
                ties=int(settings.get("ties") or 0),
                points_for=float(settings.get("fpts") or 0)
                + float(settings.get("fpts_decimal") or 0) / 100,
                mine=owner_id == str(user_id),
            )
        )
    return tuple(sorted(out, key=lambda s: (-s.wins, s.losses, -s.points_for)))


def espn_standings(raw: dict, team_id: int | None) -> tuple[Standing, ...]:
    out = []
    for team in raw.get("teams") or []:
        record = ((team.get("record") or {}).get("overall") or {})
        out.append(
            Standing(
                name=team.get("name")
                or f"{team.get('location', '')} {team.get('nickname', '')}".strip(),
                owner=team.get("abbrev") or "",
                wins=int(record.get("wins") or 0),
                losses=int(record.get("losses") or 0),
                ties=int(record.get("ties") or 0),
                points_for=float(record.get("pointsFor") or 0.0),
                mine=team.get("id") == team_id,
            )
        )
    return tuple(sorted(out, key=lambda s: (-s.wins, s.losses, -s.points_for)))


def _index(projections: pd.DataFrame) -> dict[str, tuple[str, str]]:
    """``{player_id: (name, position)}`` for naming a box score."""
    if projections is None or projections.empty:
        return {}
    return {
        str(pid): (str(name), str(pos))
        for pid, name, pos in zip(
            projections["player_id"], projections["name"], projections["pos"]
        )
    }


def best_possible(
    matchup: dict, rules: LeagueRules, index: dict[str, tuple[str, str]]
) -> float | None:
    """What the roster would have scored with hindsight.

    Reuses the lineup engine's exact assignment, so "points left on the bench"
    is measured the same way the optimizer measures a lineup: never by summing
    the highest bench scores, which would ignore slot eligibility.
    """
    scored = matchup.get("players_points") or {}
    if not scored:
        return None

    lines = [
        PlayerLine(
            player_id=str(pid),
            name=index.get(str(pid), (str(pid), ""))[0],
            pos=index.get(str(pid), ("", ""))[1] or ("DEF" if str(pid).isalpha() else ""),
            points=float(points or 0.0),
        )
        for pid, points in scored.items()
    ]
    filled = assign_slots(lines, starting_slots(rules))
    return sum(line.points for line in filled if line)


def sleeper_week_result(
    matchups: list[dict],
    roster_id: str,
    week: int,
    rules: LeagueRules,
    index: dict[str, tuple[str, str]],
    names_by_roster: dict[str, str],
) -> WeekResult | None:
    """The configured manager's result for one played week, plus the week's best-possible lineup."""
    mine = next((m for m in matchups if str(m.get("roster_id")) == str(roster_id)), None)
    if mine is None:
        return None

    starters = [str(p) for p in (mine.get("starters") or [])]
    points = [float(p or 0.0) for p in (mine.get("starters_points") or [])]
    scored = [
        (index.get(pid, (pid, ""))[0], pts)
        for pid, pts in zip(starters, points)
        if pid and pid != "0"
    ]

    opponent = next(
        (
            m for m in matchups
            if m.get("matchup_id") is not None
            and m.get("matchup_id") == mine.get("matchup_id")
            and str(m.get("roster_id")) != str(roster_id)
        ),
        None,
    )
    return WeekResult(
        week=week,
        points=float(mine.get("points") or sum(points)),
        opponent=names_by_roster.get(str(opponent.get("roster_id"))) if opponent else None,
        opponent_points=float(opponent.get("points") or 0.0) if opponent else None,
        best_possible=best_possible(mine, rules, index),
        top=max(scored, key=lambda pair: pair[1]) if scored else None,
        bottom=min(scored, key=lambda pair: pair[1]) if scored else None,
    )


def upcoming_byes(
    lines: tuple[PlayerLine, ...],
    byes: dict[str, int],
    week: int,
    horizon: int = BYE_HORIZON,
) -> dict[int, tuple[str, ...]]:
    """``{week: players on bye}`` over the next ``horizon`` weeks, this one included."""
    out: dict[int, list[str]] = {}
    for line in lines:
        if not line.team:
            continue
        bye = byes.get(line.team)
        if bye is not None and week <= bye < week + horizon:
            out.setdefault(bye, []).append(f"{line.name} ({line.pos})")
    return {w: tuple(sorted(names)) for w, names in sorted(out.items())}


def waiver_state(rules: LeagueRules, team: roster_module.Team) -> str:
    if rules.waiver_type == "faab" and rules.budget:
        used = team.waiver_budget_used or 0
        return (
            f"FAAB ${rules.budget - used} of ${rules.budget} left, "
            f"runs {rules.waiver_day or 'unknown day'}, {rules.clear_days or '?'}-day clear"
        )
    position = f"#{team.waiver_position}" if team.waiver_position else "unknown"
    return (
        f"rolling priority {position}, runs {rules.waiver_day or 'unknown day'}, "
        f"{rules.clear_days or '?'}-day clear"
    )


def deadlines(rules: LeagueRules, week: int) -> tuple[str, ...]:
    """Deadlines close enough to act on."""
    out = []
    if rules.trade_deadline_week and 0 <= rules.trade_deadline_week - week <= DEADLINE_HORIZON:
        out.append(f"trade deadline week {rules.trade_deadline_week}")
    if rules.trade_deadline_date and rules.trade_deadline_week is None:
        out.append(f"trade deadline {rules.trade_deadline_date:%Y-%m-%d}")
    if rules.playoff_start and 0 <= rules.playoff_start - week <= DEADLINE_HORIZON:
        out.append(f"playoffs start week {rules.playoff_start}")
    return tuple(out)


def decisions_for(
    rules: LeagueRules,
    team: roster_module.Team,
    result: LineupResult | None,
    byes: dict[int, tuple[str, ...]],
    week: int,
) -> tuple[str, ...]:
    """The short list: what happens this week if the manager does nothing."""
    out: list[str] = []

    if not team.drafted:
        out.append("roster is empty: this league has not drafted yet")
        return tuple(out)

    if result is not None:
        if result.delta >= SWAP_THRESHOLD:
            out.append(
                f"set the lineup: +{result.delta:.1f} projected points across "
                f"{len(result.swaps)} move(s)"
            )
            out.extend(f"  {swap}" for swap in result.swaps)
        empty = [slot.name for slot in result.current if slot.player is None]
        if empty:
            out.append(f"empty starting slot(s): {', '.join(empty)}")
        unavailable = [
            line for line in result.lines
            if line.injury_status in OUT_STATUSES and line.player_id in
            {slot.player.player_id for slot in result.current if slot.player}
        ]
        for line in unavailable:
            out.append(f"replace {line.label}: {line.injury_status}")

    next_week_byes = byes.get(week + 1)
    if next_week_byes:
        out.append(f"week {week + 1} byes hit {len(next_week_byes)}: {', '.join(next_week_byes)}")

    for deadline in deadlines(rules, week):
        out.append(deadline)
    return tuple(out)


# ------------------------------------------------------------------ loading

def build_league_brief(
    ref: LeagueRef,
    week: int,
    *,
    season: int = DEFAULT_SEASON,
    now: dt.datetime | None = None,
    client: httpx.Client | None = None,
    projections: pd.DataFrame | None = None,
    season_projections: pd.DataFrame | None = None,
) -> LeagueBrief:
    """Fetch and assemble one league's section of the brief."""
    rules = roster_module.load_rules(ref, season=season, client=client)
    if projections is None:
        projections = sleeper.weekly_projections(season, week, client=client)
    if season_projections is None:
        season_projections = sleeper.season_projections(season, client=client)
    index = _index(season_projections)

    team = roster_module.load_roster(
        ref, week, season=season, client=client, rules=rules, projections=projections
    )

    notes: list[str] = []
    result = None
    if team.drafted:
        result = league_lineup(
            ref, week, season=season, now=now, client=client,
            projections=projections, rules=rules,
        )

    standings: tuple[Standing, ...] = ()
    last_week = None
    if ref.platform == "sleeper":
        rosters = sleeper.rosters(ref.league_id, client=client)
        users = sleeper.users(ref.league_id, client=client)
        standings = sleeper_standings(rosters, users, ref.user_id)
        names_by_roster = {
            str(r.get("roster_id")): next(
                (
                    (u.get("metadata") or {}).get("team_name") or u.get("display_name")
                    for u in users
                    if str(u.get("user_id")) == str(r.get("owner_id"))
                ),
                "unknown",
            )
            for r in rosters
        }
        if week > 1 and team.team_id:
            last_week = sleeper_week_result(
                sleeper.matchups(ref.league_id, week - 1, client=client),
                team.team_id, week - 1, rules, index, names_by_roster,
            )
    else:
        raw = espn_league_payload(ref, season=season, client=client)
        standings = espn_standings(raw, ref.team_id)
        notes.append(
            "ESPN: no weekly box score through the read views this project uses, "
            "so there is no points-left-on-the-bench line"
        )
        if rules.unmapped_stat_ids:
            notes.append(
                f"ESPN: {len(rules.unmapped_stat_ids)} scoring items have no Sleeper "
                "equivalent, so K and D/ST project low here"
            )

    byes_by_team = nflverse.byes(season, client=client)
    lines = result.lines if result else ()
    byes = upcoming_byes(lines, byes_by_team, week)
    injuries = tuple(
        line for line in lines
        if line.injury_status and line.injury_status not in (None, "Active")
    )

    return LeagueBrief(
        ref=ref,
        rules=rules,
        team=team,
        lineup=result,
        standings=standings,
        last_week=last_week,
        injuries=injuries,
        byes=byes,
        decisions=decisions_for(rules, team, result, byes, week),
        notes=tuple(notes) + (result.notes if result else ()),
    )


def espn_league_payload(
    ref: LeagueRef, *, season: int = DEFAULT_SEASON, client: httpx.Client | None = None
) -> dict:
    from ff.sources import espn

    return espn.league(int(ref.league_id), season=season, client=client)


# ---------------------------------------------------------------- rendering

def render_league(brief: LeagueBrief) -> str:
    rules, team = brief.rules, brief.team
    out: list[str] = [f"## {brief.ref.name}"]

    slots = " ".join(f"{k}x{v}" for k, v in rules.roster_slots.items())
    standing = brief.my_standing
    place = (
        f"{[s.mine for s in brief.standings].index(True) + 1} of {len(brief.standings)}"
        if standing else "-"
    )
    out.append(
        f"`{rules.format}` · {rules.num_teams} teams · {slots}  \n"
        f"Record **{team.record}** ({place}), {team.points_for:.1f} PF · "
        f"waivers: {waiver_state(rules, team)}"
    )

    if brief.last_week:
        lw = brief.last_week
        line = f"**Week {lw.week}:** {lw.result} {lw.points:.1f}"
        if lw.opponent:
            line += f" - {lw.opponent_points:.1f} vs {lw.opponent}"
        if lw.top:
            line += f" · best {lw.top[0]} {lw.top[1]:.1f} · worst {lw.bottom[0]} {lw.bottom[1]:.1f}"
        if lw.left_on_bench is not None:
            line += f" · **{lw.left_on_bench:.1f} left on the bench**"
        out.append(line)

    result = brief.lineup
    if result is not None:
        out.append(
            f"**Week {result.week} lineup:** {result.current_points:.1f} projected now, "
            f"{result.optimal_points:.1f} optimal (**{result.delta:+.1f}**)"
        )
        rows = [
            f"| {slot.name} | {slot.player.name if slot.player else '-'} | "
            f"{slot.player.pos + '-' + (slot.player.team or '') if slot.player else '-'} | "
            f"{slot.points:.1f} | {', '.join(slot.player.flags) if slot.player else ''} |"
            for slot in result.optimal
        ]
        out.append(
            "| Slot | Player | Pos | Proj | Flags |\n|---|---|---|---|---|\n"
            + "\n".join(rows)
        )

    if brief.injuries:
        out.append(
            "**Injuries:** "
            + ", ".join(f"{p.name} ({p.injury_status})" for p in brief.injuries)
        )

    if brief.byes:
        out.append(
            "**Byes:** "
            + " · ".join(f"wk {w}: {', '.join(names)}" for w, names in brief.byes.items())
        )

    if brief.decisions:
        bullets = [
            f"  - {d.strip()}" if d.startswith("  ") else f"- {d}"
            for d in brief.decisions
        ]
        out.append("**Decisions**\n" + "\n".join(bullets))
    else:
        out.append("**Decisions**\n- nothing to do")

    for note in brief.notes:
        out.append(f"> {note}")
    return "\n\n".join(out)


def render_brief(briefs: list[LeagueBrief], week: int, now: dt.datetime | None = None) -> str:
    now = now or now_eastern()
    total = sum(b.lineup.delta for b in briefs if b.lineup)
    # Indented lines are the detail under a decision, not decisions themselves.
    todo = sum(
        len([d for d in b.decisions if not d.startswith("  ")]) for b in briefs
    )

    header = [
        f"Week {week} · generated {now:%Y-%m-%d %H:%M} ET",
        f"**{todo} decision(s) across {len(briefs)} leagues · "
        f"{total:+.1f} projected points available from lineup changes.**",
    ]
    return "\n\n".join(header + [render_league(b) for b in briefs])


def weekly_brief(
    week: int,
    *,
    season: int = DEFAULT_SEASON,
    keys: list[str] | None = None,
    refs: dict[str, LeagueRef] | None = None,
    now: dt.datetime | None = None,
    client: httpx.Client | None = None,
) -> tuple[str, list[LeagueBrief]]:
    """Build the whole brief.  Returns the Markdown and the structured briefs."""
    if refs is None:
        from ff.config import load_leagues
        refs = load_leagues()
    projections = sleeper.weekly_projections(season, week, client=client)
    season_proj = sleeper.season_projections(season, client=client)

    briefs = [
        build_league_brief(
            refs[key], week, season=season, now=now, client=client,
            projections=projections, season_projections=season_proj,
        )
        for key in (keys if keys is not None else list(refs))
    ]
    return render_brief(briefs, week, now), briefs
