"""Weekly lineup optimizer: projections, adjustments, and an exact slot solve.

Three things happen here, in this order.

**Score.**  Sleeper's weekly Rotowire projection for each rostered player is run
through the league's own scoring by :func:`ff.engine.value.score_projection`, so
A TE premium and 6-point passing touchdown are already in the number
before anything else touches it.

**Adjust.**  Four adjustments, deliberately few:

* *Bye* and *unavailable injury status* zero the projection outright.
  ``Questionable`` is flagged but **not** zeroed: the model retains the supplied
  projection and flags the status for the manager to check.
* *Wind* above :data:`WIND_THRESHOLD` mph downgrades pass-game points on a
  convex curve -- each further 5 mph costs more than the step before it -- with
  a small offsetting bump to rushing.  Only wind is modelled; temperature and
  precipitation move fantasy scoring too little to be worth the false precision.
  A dome, a neutral site, or a retractable roof of unknown state never takes the
  penalty.
* *Implied team total* becomes a bounded multiplier, at most
  :data:`TOTAL_BOUND` either way, measured against the average implied total of
  that week's slate.  **This is a prior, not a projection.**  The market knows
  things the projection does not, but it prices teams, not players, so it is
  allowed to nudge a ranking and never to invent one.

**Assign.**  Slots are solved exactly, not greedily.  Filling slots one at a
time is wrong whenever a flex is involved: a FLEX filled first will happily eat
the only receiver on the roster and leave the WR slot empty.  The roster is
small, so an exact dynamic program over slot capacities is both cheap and
correct.

Lock rules are honoured.  A player whose game has kicked off cannot be moved in
either direction, and in a ``bench_lock`` league (when ``bench_lock: 1``) the entire bench freezes at the first kickoff of the
week.  ``now`` is a parameter so this is testable; pass it as naive US/Eastern,
the same clock ``games.csv`` uses.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from ff.engine.value import FLEX_ELIGIBILITY
from ff.leagues import LeagueRules
from ff.sources import fantasypros, nflverse, odds as odds_source, sleeper, weather as weather_source
from ff.sources.weather import GameWeather

EASTERN = ZoneInfo("America/New_York")

# ----------------------------------------------------------------- constants

#: Below this, wind does not measurably move passing production.
WIND_THRESHOLD = 15.0
WIND_STEP = 5.0

#: ``penalty = COEFFICIENT * ((mph - THRESHOLD) / STEP) ** EXPONENT``.  The
#: exponent is what makes the curve convex: the 20-25 mph step costs a little
#: under twice the 15-20 step, which is the shape the play-by-play research
#: reports.  The cap keeps a 45 mph freak reading from zeroing a quarterback.
WIND_COEFFICIENT = 0.06
WIND_EXPONENT = 1.55
WIND_CAP = 0.35

#: Positions whose pass-game points take the wind penalty.
WIND_PASS_POSITIONS = frozenset({"QB", "WR", "TE"})

#: A running back gains some of what the pass game loses, but far less than it
#: loses: game script shifts carries, it does not create yards.
RB_WIND_SHARE = 0.4
RB_WIND_CAP = 0.06

#: Implied team total: the most it may move a projection, either way.
TOTAL_BOUND = 0.08

#: Points of implied team total that buy the full bound.  Week-1 2026 implied
#: totals span roughly 17 to 28, so six points is about two standard deviations.
TOTAL_SCALE = 6.0

#: Statuses that mean the player will not score.  ``Doubtful`` is here on
#: purpose: doubtful players play under 10% of the time.  The tail of the list
#: is roster designations rather than game-day tags, and they mean the same
#: thing for a lineup.
OUT_STATUSES = frozenset({"Out", "IR", "Doubtful", "Sus", "PUP", "NA", "DNR", "COV"})

#: Flagged, never zeroed.
RISK_STATUSES = frozenset({"Questionable", "Probable"})

#: Which positions each starting slot accepts.  The flex-style slots are shared
#: with the value engine so a board and a lineup can never disagree.
SLOT_ELIGIBILITY: dict[str, tuple[str, ...]] = {
    **{pos: (pos,) for pos in ("QB", "RB", "WR", "TE", "K")},
    "DEF": ("DEF", "DST"),
    "DST": ("DEF", "DST"),
    "D/ST": ("DEF", "DST"),
    "RB/WR": ("RB", "WR"),
    "WR/TE": ("WR", "TE"),
    **FLEX_ELIGIBILITY,
}

#: Scoring keys that belong to the passing game, and to the running game.
PASS_PREFIXES = ("pass_", "rec", "bonus_rec", "bonus_pass", "cmp")
RUSH_PREFIXES = ("rush_", "bonus_rush")


# ------------------------------------------------------------------- results

@dataclass(frozen=True)
class PlayerLine:
    """One rostered player, scored and adjusted for this week."""

    player_id: str
    name: str
    pos: str
    team: str | None = None
    opponent: str | None = None
    base_points: float = 0.0
    points: float = 0.0
    wind_penalty: float = 0.0
    total_multiplier: float = 1.0
    injury_status: str | None = None
    locked: bool = False
    started: bool = False
    flags: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.name} ({self.pos}{'-' + self.team if self.team else ''})"


@dataclass(frozen=True)
class Slot:
    """One starting spot and whoever is in it."""

    name: str
    player: PlayerLine | None = None

    @property
    def points(self) -> float:
        return self.player.points if self.player else 0.0


@dataclass(frozen=True)
class LineupMove:
    """A before/after assignment, not a claim about platform click order."""

    action: str
    player: PlayerLine
    slot_index: int
    slot: str
    from_slot: str | None = None

    def __str__(self) -> str:
        if self.action == "move":
            return f"move {self.player.label} from {self.from_slot} to {self.slot}"
        if self.action == "bench":
            return f"bench {self.player.label} from {self.slot}"
        return f"start {self.player.label} in {self.slot}"


@dataclass(frozen=True)
class Swap:
    """One replacement chain, with a direct swap as its simplest case."""

    slot: str
    start: PlayerLine
    sit: PlayerLine | None
    gain: float
    repositions: tuple[LineupMove, ...] = ()
    terminal_slot: str | None = None

    def __str__(self) -> str:
        if self.repositions:
            steps = ([f"bench {self.sit.label} from {self.terminal_slot}"] if self.sit else [])
            steps.extend(str(move) for move in reversed(self.repositions))
            steps.append(f"start {self.start.label} in {self.slot}")
            return "; ".join(steps) + f" (chain {self.gain:+.1f})"
        sit = self.sit.label if self.sit else "an empty slot"
        return f"{self.slot}: start {self.start.label} over {sit} (+{self.gain:.1f})"


@dataclass(frozen=True)
class LineupResult:
    """Everything a lineup job or a brief needs for one team, one week."""

    league: str
    week: int
    optimal: tuple[Slot, ...]
    current: tuple[Slot, ...]
    bench: tuple[PlayerLine, ...]
    swaps: tuple[Swap, ...]
    lines: tuple[PlayerLine, ...]
    notes: tuple[str, ...] = ()

    @property
    def optimal_points(self) -> float:
        return sum(s.points for s in self.optimal)

    @property
    def current_points(self) -> float:
        return sum(s.points for s in self.current)

    @property
    def delta(self) -> float:
        return self.optimal_points - self.current_points

    @property
    def flagged(self) -> tuple[PlayerLine, ...]:
        return tuple(line for line in self.lines if line.flags)

    @property
    def moves(self) -> tuple[LineupMove, ...]:
        return lineup_changes(self.current, self.optimal)


# ----------------------------------------------------------------- adjusters

def wind_penalty(mph: float | None) -> float:
    """Fraction of pass-game production wind takes away.

    Zero below :data:`WIND_THRESHOLD`, convex above it, capped at
    :data:`WIND_CAP`.
    """
    if not mph or mph <= WIND_THRESHOLD:
        return 0.0
    steps = (float(mph) - WIND_THRESHOLD) / WIND_STEP
    return min(WIND_COEFFICIENT * steps ** WIND_EXPONENT, WIND_CAP)


def total_multiplier(team_total: float | None, baseline: float) -> float:
    """Bounded directional multiplier from an implied team total.

    A prior, not a projection: at most :data:`TOTAL_BOUND` in either direction,
    and exactly 1.0 for a team sitting on the slate average.
    """
    if team_total is None:
        return 1.0
    shift = (float(team_total) - baseline) / TOTAL_SCALE * TOTAL_BOUND
    return 1.0 + max(-TOTAL_BOUND, min(TOTAL_BOUND, shift))


def _split_scoring(scoring: Mapping[str, float]) -> tuple[dict, dict]:
    passing = {k: v for k, v in scoring.items() if k.startswith(PASS_PREFIXES)}
    rushing = {k: v for k, v in scoring.items() if k.startswith(RUSH_PREFIXES)}
    return passing, rushing


# ---------------------------------------------------------------- slot logic

def starting_slots(rules: LeagueRules) -> tuple[str, ...]:
    """The starting lineup, expanded and in platform order.

    Sleeper's ``starters`` array lines up index-for-index with the starting
    entries of ``roster_positions``, and ``roster_positions`` groups duplicates
    together, so re-expanding the collapsed counts reproduces that order.
    """
    out: list[str] = []
    for slot, count in rules.roster_slots.items():
        out.extend([slot] * int(count))
    return tuple(out)


def slot_accepts(slot: str, pos: str | None) -> bool:
    return bool(pos) and pos in SLOT_ELIGIBILITY.get(slot, ())


def assign_slots(lines: Sequence[PlayerLine], slots: Sequence[str]) -> list[PlayerLine | None]:
    """Exact maximum-points assignment of ``lines`` onto ``slots``.

    A dynamic program over remaining capacity per distinct slot type.  With at
    most a dozen slots of half a dozen types and a roster in the twenties the
    state space is a few hundred, so exactness is free -- and greedy is wrong,
    which is the whole point.
    """
    filled: list[PlayerLine | None] = [None] * len(slots)
    if not slots or not lines:
        return filled

    counts: dict[str, int] = {}
    for slot in slots:
        counts[slot] = counts.get(slot, 0) + 1
    names = tuple(counts)
    caps = tuple(counts[n] for n in names)

    pool = [ln for ln in lines if any(slot_accepts(n, ln.pos) for n in names)]
    pool.sort(key=lambda ln: (-ln.points, ln.name))
    eligible = [
        tuple(j for j, n in enumerate(names) if slot_accepts(n, ln.pos)) for ln in pool
    ]

    @lru_cache(maxsize=None)
    def best(i: int, remaining: tuple[int, ...]):
        """(total points, slots filled, ((player index, slot type index), ...))"""
        if i == len(pool) or not any(remaining):
            return 0.0, 0, ()
        top = best(i + 1, remaining)  # bench this player
        for j in eligible[i]:
            if not remaining[j]:
                continue
            nxt = remaining[:j] + (remaining[j] - 1,) + remaining[j + 1:]
            points, count, picks = best(i + 1, nxt)
            candidate = (points + pool[i].points, count + 1, ((i, j),) + picks)
            # Ties go to the fuller lineup: an empty slot is never an upgrade.
            if candidate[:2] > top[:2]:
                top = candidate
        return top

    _, _, picks = best(0, caps)
    best.cache_clear()

    open_positions: dict[str, list[int]] = {n: [] for n in names}
    for index, slot in enumerate(slots):
        open_positions[slot].append(index)
    for player_index, slot_index in picks:
        filled[open_positions[names[slot_index]].pop(0)] = pool[player_index]
    return filled


# ------------------------------------------------------------------- locking

def to_eastern(when: dt.datetime | None) -> dt.datetime | None:
    """Naive US/Eastern, the clock ``games.csv`` kickoff times are written in."""
    if when is None:
        return None
    if when.tzinfo is not None:
        return when.astimezone(EASTERN).replace(tzinfo=None)
    return when


def now_eastern() -> dt.datetime:
    return dt.datetime.now(EASTERN).replace(tzinfo=None)


def kickoffs_for_week(
    season: int, week: int, *, client: httpx.Client | None = None
) -> dict[str, dt.datetime]:
    """``{team: kickoff}`` in naive US/Eastern for one NFL week."""
    sched = nflverse.games(season, client=client)
    sched = sched[sched["week"] == week]

    out: dict[str, dt.datetime] = {}
    for row in sched.itertuples(index=False):
        kickoff = weather_source.kickoff_time(row.gameday, row.gametime)
        if kickoff is None:
            continue
        for team in (row.home_team, row.away_team):
            out[team] = kickoff
            out[nflverse.sleeper_team(team)] = kickoff
    return out


# ------------------------------------------------------------------ the core

def _row_for(projections: pd.DataFrame, player_id: str) -> Mapping | None:
    if projections.empty or "player_id" not in projections:
        return None
    hits = projections.loc[projections["player_id"] == player_id]
    return None if hits.empty else hits.iloc[0]


def _value(row, key, default=None):
    try:
        got = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    if got is None or (isinstance(got, float) and got != got):
        return default
    return got


def build_line(
    player_id: str,
    row: Mapping | None,
    rules: LeagueRules,
    *,
    week: int,
    byes: Mapping[str, int],
    weather: Mapping[str, GameWeather],
    implied: Mapping[str, float],
    baseline: float,
    fallback: Mapping | None = None,
) -> PlayerLine:
    """Score and adjust one rostered player."""
    from ff.engine.value import score_projection

    meta = row if row is not None else (fallback or {})
    name = _value(meta, "name", player_id) or player_id
    pos = _value(meta, "pos", "") or ""
    team = _value(meta, "team")
    opponent = _value(meta, "opponent")
    status = _value(meta, "injury_status")

    flags: list[str] = []
    if row is None:
        flags.append("no projection")

    base = score_projection(row, rules.scoring) if row is not None else 0.0
    passing, rushing = _split_scoring(rules.scoring)
    pass_points = score_projection(row, passing) if row is not None else 0.0
    rush_points = score_projection(row, rushing) if row is not None else 0.0

    on_bye = team is not None and byes.get(team) == week
    if on_bye:
        flags.append("bye")
    if status in OUT_STATUSES:
        flags.append(f"injury: {status}")
    elif status in RISK_STATUSES:
        flags.append(f"injury: {status} (flagged, not discounted)")
    elif status:
        flags.append(f"injury: {status}")

    if on_bye or status in OUT_STATUSES:
        return PlayerLine(
            player_id=player_id, name=name, pos=pos, team=team, opponent=opponent,
            base_points=base, points=0.0, injury_status=status, flags=tuple(flags),
        )

    conditions = weather.get(team) if team else None
    penalty = wind_penalty(conditions.wind) if conditions else 0.0
    points = base
    if penalty:
        flags.append(f"wind {conditions.wind_speed_10m:.0f} mph")
        if pos in WIND_PASS_POSITIONS:
            points -= penalty * pass_points
        elif pos == "RB":
            points += min(RB_WIND_SHARE * penalty, RB_WIND_CAP) * rush_points
        elif pos == "K":
            points -= penalty * base
    elif conditions is not None and conditions.unknown and (conditions.wind_speed_10m or 0) >= WIND_THRESHOLD:
        flags.append(f"weather unknown: {conditions.note}")

    # A defence wants the other team held down, so it reads the opponent's line.
    if pos in ("DEF", "DST"):
        opp_total = implied.get(opponent) if opponent else None
        mirrored = None if opp_total is None else 2 * baseline - opp_total
        multiplier = total_multiplier(mirrored, baseline)
    else:
        multiplier = total_multiplier(implied.get(team) if team else None, baseline)
    points *= multiplier

    return PlayerLine(
        player_id=player_id, name=name, pos=pos, team=team, opponent=opponent,
        base_points=base, points=max(points, 0.0), wind_penalty=penalty,
        total_multiplier=multiplier, injury_status=status, flags=tuple(flags),
    )


def optimal_lineup(
    roster_player_ids: Iterable[str],
    week: int,
    rules: LeagueRules,
    *,
    projections: pd.DataFrame,
    starters: Sequence[str] = (),
    byes: Mapping[str, int] | None = None,
    weather: Mapping[str, GameWeather] | None = None,
    implied_totals: Mapping[str, float] | None = None,
    baseline: float | None = None,
    kickoffs: Mapping[str, dt.datetime] | None = None,
    now: dt.datetime | None = None,
    fallbacks: Mapping[str, Mapping] | None = None,
) -> LineupResult:
    """The best legal lineup, what is set now, and how to get from one to the other.

    ``starters`` is Sleeper's positional array: index *i* is the player in
    ``starting_slots(rules)[i]``, and ``"0"`` means the slot is empty.  ``now``
    (naive US/Eastern) drives the lock rules; leave it ``None`` to reason about
    a lineup with nothing locked.
    """
    byes = byes or {}
    weather = weather or {}
    implied = implied_totals or {}
    kickoffs = {k: to_eastern(v) for k, v in (kickoffs or {}).items()}
    fallbacks = fallbacks or {}
    now = to_eastern(now)
    if baseline is None:
        baseline = odds_source.baseline_total(dict(implied))

    slots = starting_slots(rules)
    current_ids = [str(p) for p in starters][: len(slots)]
    started_ids = {p for p in current_ids if p and p != "0"}

    lines: dict[str, PlayerLine] = {}
    for player_id in roster_player_ids:
        player_id = str(player_id)
        line = build_line(
            player_id,
            _row_for(projections, player_id),
            rules,
            week=week,
            byes=byes,
            weather=weather,
            implied=implied,
            baseline=baseline,
            fallback=fallbacks.get(player_id),
        )
        lines[player_id] = replace(line, started=player_id in started_ids)

    notes: list[str] = []
    first_kickoff = min(kickoffs.values()) if kickoffs else None
    bench_frozen = bool(
        rules.bench_lock and now and first_kickoff and now >= first_kickoff
    )
    if bench_frozen:
        notes.append(
            f"bench is locked: this league locks the bench at the first kickoff "
            f"({first_kickoff:%a %H:%M} ET) and it has passed"
        )

    for player_id, line in list(lines.items()):
        kickoff = kickoffs.get(line.team) if line.team else None
        locked = bool(now and kickoff and now >= kickoff)
        if bench_frozen and not line.started:
            locked = True
        if locked:
            lines[player_id] = replace(
                line, locked=True, flags=line.flags + ("locked",)
            )

    current = tuple(
        Slot(name, lines.get(pid) if pid and pid != "0" else None)
        for name, pid in zip(slots, current_ids + ["0"] * (len(slots) - len(current_ids)))
    )

    pinned = {
        i: slot.player
        for i, slot in enumerate(current)
        if slot.player is not None and slot.player.locked
    }
    open_slots = [(i, name) for i, name in enumerate(slots) if i not in pinned]
    pinned_ids = {line.player_id for line in pinned.values()}
    movable = [
        line for line in lines.values()
        if line.player_id not in pinned_ids and not line.locked
    ]

    assigned = assign_slots(movable, [name for _, name in open_slots])
    optimal_players: list[PlayerLine | None] = [None] * len(slots)
    for i, line in pinned.items():
        optimal_players[i] = line
    for (i, _), line in zip(open_slots, assigned):
        optimal_players[i] = line

    optimal = tuple(Slot(name, p) for name, p in zip(slots, optimal_players))
    optimal_ids = {s.player.player_id for s in optimal if s.player}
    bench = tuple(
        sorted(
            (ln for ln in lines.values() if ln.player_id not in optimal_ids),
            key=lambda ln: -ln.points,
        )
    )

    swaps = _swaps(current, optimal)
    if any(ln.locked for ln in lines.values()) and not bench_frozen:
        notes.append("players whose games have kicked off are held in place")
    return LineupResult(
        league=rules.key,
        week=week,
        optimal=optimal,
        current=current,
        bench=bench,
        swaps=swaps,
        lines=tuple(lines.values()),
        notes=tuple(notes),
    )


def slot_labels(slots: Sequence[Slot]) -> tuple[str, ...]:
    counts = Counter(slot.name for slot in slots)
    seen: Counter = Counter()
    labels = []
    for slot in slots:
        seen[slot.name] += 1
        labels.append(f"{slot.name} #{seen[slot.name]}" if counts[slot.name] > 1 else slot.name)
    return tuple(labels)


def lineup_changes(current: Sequence[Slot], optimal: Sequence[Slot]) -> tuple[LineupMove, ...]:
    """Describe all benches, retained-player repositions and new starts by exact slot."""
    before = {s.player.player_id: i for i, s in enumerate(current) if s.player}
    after = {s.player.player_id: i for i, s in enumerate(optimal) if s.player}
    old_labels, new_labels = slot_labels(current), slot_labels(optimal)
    benches, repositions, starts = [], [], []
    for i, slot in enumerate(current):
        if slot.player and slot.player.player_id not in after:
            benches.append(LineupMove("bench", slot.player, i, old_labels[i]))
    for i, slot in enumerate(optimal):
        if slot.player is None:
            continue
        old = before.get(slot.player.player_id)
        if old is None:
            starts.append(LineupMove("start", slot.player, i, new_labels[i]))
        elif old != i:
            repositions.append(LineupMove("move", slot.player, i, new_labels[i], old_labels[old]))
    return tuple(benches + repositions + starts)


def _swaps(current: Sequence[Slot], optimal: Sequence[Slot]) -> tuple[Swap, ...]:
    """Follow each incoming player's actual vacancy chain, never sort unrelated pairs."""
    current_ids = {s.player.player_id for s in current if s.player}
    destinations = {s.player.player_id: i for i, s in enumerate(optimal) if s.player}
    old_labels, new_labels = slot_labels(current), slot_labels(optimal)
    swaps = []
    for i, slot in enumerate(optimal):
        if slot.player is None or slot.player.player_id in current_ids:
            continue
        cursor, repositions, visited = i, [], set()
        occupant = current[cursor].player
        while occupant and occupant.player_id in destinations:
            if cursor in visited:
                raise ValueError("invalid duplicate-player replacement chain")
            visited.add(cursor)
            target = destinations[occupant.player_id]
            repositions.append(LineupMove("move", occupant, target,
                                         new_labels[target], old_labels[cursor]))
            cursor = target
            occupant = current[cursor].player
        swaps.append(Swap(new_labels[i], slot.player, occupant,
                          slot.player.points - (occupant.points if occupant else 0.0),
                          tuple(repositions), old_labels[cursor]))
    return tuple(swaps)


# ------------------------------------------------------------------- loading

def league_lineup(
    ref,
    week: int,
    *,
    season: int = 2026,
    now: dt.datetime | None = None,
    client: httpx.Client | None = None,
    projections: pd.DataFrame | None = None,
    rules: LeagueRules | None = None,
) -> LineupResult:
    """Fetch everything one league needs and run :func:`optimal_lineup`.

    Sleeper weekly projections are used across providers. Reserve and taxi
    players are excluded until the manager activates them in the platform.
    """
    from ff import roster as roster_module

    if rules is None:
        rules = roster_module.load_rules(ref, season=season, client=client)
    if projections is None:
        projections = sleeper.weekly_projections(season, week, client=client)

    team = roster_module.load_roster(ref, week, season=season, client=client,
                                     projections=projections, rules=rules)
    inactive = set(team.reserve) | set(team.taxi)
    result = optimal_lineup(
        [pid for pid in team.player_ids if pid not in inactive],
        week,
        rules,
        projections=projections,
        starters=[pid if pid not in inactive else "0" for pid in team.starters],
        byes=nflverse.byes(season, client=client),
        weather=weather_source.week_weather(season, week, client=client),
        implied_totals=odds_source.week_implied_totals(season, week, client=client),
        kickoffs=kickoffs_for_week(season, week, client=client),
        now=now,
        fallbacks=team.fallbacks,
    )
    consensus = fantasypros.lineup_notes(
        result.lines, season, week,
        reception_points=rules.scoring.get("rec", 0), client=client,
    )
    return replace(result, notes=result.notes + consensus)
