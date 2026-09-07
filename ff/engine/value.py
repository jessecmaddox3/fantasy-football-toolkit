"""League-specific scoring, replacement level, and value over replacement.

Sleeper's projection rows and Sleeper's ``scoring_settings`` use the same stat
names, so scoring a projection is a dot product over the keys the league
actually scores.  ESPN scoring is translated into those same names in
``ff.leagues``, so one code path serves the configured leagues.

Replacement level is what makes cross-position comparison honest: a QB1 in a
1QB league is nearly worthless above the QB12 anyone can stream, while in
superflex the same QB is scarce.  The flex allocation below is what produces
that difference.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping

import pandas as pd

from ff.leagues import LeagueRules

#: Which positions each flex-style slot can start.  Ordered most restrictive
#: first: restrictive slots are filled before permissive ones so a REC_FLEX is
#: not left empty after SUPER_FLEX has eaten the best receivers.
FLEX_ELIGIBILITY: dict[str, tuple[str, ...]] = {
    "REC_FLEX": ("WR", "TE"),
    "WR/TE": ("WR", "TE"),
    "RB/WR": ("RB", "WR"),
    "WRRB_FLEX": ("RB", "WR"),
    "FLEX": ("RB", "WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "OP": ("QB", "RB", "WR", "TE"),
}

#: Slots that name a single position outright.
BASE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF", "DST")


def _num(x) -> float | None:
    """Coerce a projection cell to a float, treating NaN/None/'' as absent."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def score_projection(row: Mapping | pd.Series, scoring: Mapping[str, float]) -> float:
    """Fantasy points for one projection row under one league's scoring.

    Driven by the scoring dict, so stat columns the league does not score
    (``adp_*``, ``pts_ppr``, ``gp``, ...) can never leak in.  Missing or NaN
    stats contribute zero rather than poisoning the total.
    """
    total = 0.0
    for stat, points in scoring.items():
        if not points:
            continue
        try:
            raw = row[stat]
        except (KeyError, IndexError):
            continue
        v = _num(raw)
        if v:
            total += v * float(points)
    return total


def _slot_counts(rules: LeagueRules) -> tuple[dict[str, int], list[tuple[str, int]]]:
    """Split starting slots into fixed-position counts and flex spots."""
    base: dict[str, int] = {}
    flex: list[tuple[str, int]] = []
    for slot, n in rules.roster_slots.items():
        if slot in FLEX_ELIGIBILITY:
            flex.append((slot, n * rules.num_teams))
        elif slot in BASE_POSITIONS:
            pos = "DEF" if slot == "DST" else slot
            base[pos] = base.get(pos, 0) + n * rules.num_teams
    flex.sort(key=lambda sn: len(FLEX_ELIGIBILITY[sn[0]]))
    return base, flex


def replacement_rank(df: pd.DataFrame, rules: LeagueRules, points_col: str = "proj_points") -> dict[str, int]:
    """How many players at each position are startable league-wide.

    Fixed slots are counted directly; flex spots are then handed out greedily,
    most-restrictive slot type first, always to the best player still available
    at any eligible position.
    """
    base, flex = _slot_counts(rules)

    pools: dict[str, list[float]] = {
        pos: sorted(
            (p for p in (_num(v) for v in grp[points_col]) if p is not None),
            reverse=True,
        )
        for pos, grp in df.groupby("pos")
    }
    taken = {pos: base.get(pos, 0) for pos in set(base) | set(pools)}

    for slot, spots in flex:
        eligible = FLEX_ELIGIBILITY[slot]
        for _ in range(spots):
            best_pos, best_pts = None, None
            for pos in eligible:
                pool = pools.get(pos) or []
                idx = taken.get(pos, 0)
                if idx >= len(pool):
                    continue
                if best_pts is None or pool[idx] > best_pts:
                    best_pos, best_pts = pos, pool[idx]
            if best_pos is None:
                break
            taken[best_pos] = taken.get(best_pos, 0) + 1

    return {pos: n for pos, n in taken.items() if n > 0}


def replacement_level(
    df: pd.DataFrame, rules: LeagueRules, points_col: str = "proj_points"
) -> dict[str, float]:
    """Projected points of the last startable player at each position.

    A position whose pool is shorter than its replacement rank falls back to the
    worst player available; a position with no players at all scores 0.0.
    """
    ranks = replacement_rank(df, rules, points_col)
    out: dict[str, float] = {}
    for pos, rank in ranks.items():
        pool = sorted(
            (p for p in (_num(v) for v in df.loc[df["pos"] == pos, points_col]) if p is not None),
            reverse=True,
        )
        if not pool:
            out[pos] = 0.0
        else:
            out[pos] = pool[min(rank, len(pool)) - 1]
    return out


def value_over_replacement(
    df: pd.DataFrame,
    rules: LeagueRules,
    *,
    scoring: Mapping[str, float] | None = None,
    positions: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Add ``proj_points``, ``replacement`` and ``vor``, best VOR first."""
    scoring = scoring if scoring is not None else rules.scoring
    out = df.copy()
    if positions is not None:
        out = out[out["pos"].isin(list(positions))].copy()

    out["proj_points"] = [
        score_projection(row, scoring) for _, row in out.iterrows()
    ]
    levels = replacement_level(out, rules)
    out["replacement"] = out["pos"].map(levels).fillna(0.0)
    out["vor"] = out["proj_points"] - out["replacement"]
    return out.sort_values("vor", ascending=False).reset_index(drop=True)
