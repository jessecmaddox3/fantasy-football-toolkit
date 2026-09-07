import math

import pandas as pd
import pytest

from ff.engine import value
from ff.leagues import LeagueRules

HALF_PPR_TD6 = {  # half-PPR with six-point passing TDs
    "rec": 0.5, "rec_yd": 0.1, "rec_td": 6.0,
    "rush_yd": 0.1, "rush_td": 6.0,
    "pass_yd": 0.04, "pass_td": 6.0, "pass_int": -2.0,
    "fum_lost": -1.0,
}
HALF_PPR_TD4 = {**HALF_PPR_TD6, "pass_td": 4.0}


def rules(slots, teams=12, scoring=None, **kw):
    return LeagueRules(
        key="t", name="t", platform="sleeper", league_id="t",
        format=kw.pop("format", "redraft"), num_teams=teams,
        scoring=scoring or HALF_PPR_TD6, roster_slots=slots,
        bench=6, ir=0, taxi=0, waiver_type="faab", budget=100,
        waiver_day="Wednesday", waiver_days=("Wednesday",), clear_days=2,
        trade_deadline_week=11, playoff_start=15, **kw,
    )


# --------------------------------------------------------- score_projection

def test_score_projection_applies_yardage_and_touchdown_rules():
    row = {"rec": 63.0, "rec_yd": 533.0, "rec_td": 3.0, "rush_yd": 1251.0, "rush_td": 12.0}
    # 31.5 + 53.3 + 18 + 125.1 + 72
    assert value.score_projection(row, HALF_PPR_TD6) == pytest.approx(299.9)


def test_pass_td_six_beats_pass_td_four_by_two_points_per_score():
    row = {"pass_yd": 4000.0, "pass_td": 30.0, "pass_int": 10.0}
    six = value.score_projection(row, HALF_PPR_TD6)
    four = value.score_projection(row, HALF_PPR_TD4)
    assert six - four == pytest.approx(60.0)


def test_te_premium_stacks_on_top_of_the_base_reception_rule():
    """GP scores rec 0.5 plus bonus_rec_te 0.5, so a TE catch is worth 1.0."""
    gp = {"rec": 0.5, "bonus_rec_te": 0.5, "rec_yd": 0.1}
    te = {"rec": 80.0, "bonus_rec_te": 80.0, "rec_yd": 900.0}
    wr = {"rec": 80.0, "rec_yd": 900.0}
    assert value.score_projection(te, gp) == pytest.approx(40 + 40 + 90)
    assert value.score_projection(wr, gp) == pytest.approx(40 + 90)


def test_first_down_bonuses_are_scored_for_both_rushing_and_receiving():
    gp = {"rec": 0.5, "rec_fd": 0.5, "rush_fd": 0.5}
    row = {"rec": 60.0, "rec_fd": 50.0, "rush_fd": 120.0}
    assert value.score_projection(row, gp) == pytest.approx(30 + 25 + 60)


def test_turnovers_and_defensive_interceptions_use_distinct_keys():
    """`pass_int` is a QB's giveaway; `int` is a defense's takeaway."""
    scoring = {"pass_int": -2.0, "int": 2.0, "fum_lost": -1.0, "fum": -1.0}
    qb = {"pass_int": 12.0}
    dst = {"int": 14.0}
    assert value.score_projection(qb, scoring) == pytest.approx(-24.0)
    assert value.score_projection(dst, scoring) == pytest.approx(28.0)


def test_missing_and_nan_stats_contribute_nothing_rather_than_nan():
    row = {"rec": 80.0, "pass_td": float("nan")}
    got = value.score_projection(row, HALF_PPR_TD6)
    assert not math.isnan(got)
    assert got == pytest.approx(40.0)


def test_stats_with_no_scoring_rule_are_ignored():
    row = {"rec": 10.0, "adp_ppr": 1.5, "pts_ppr": 331.4, "gp": 17.0}
    assert value.score_projection(row, {"rec": 1.0}) == pytest.approx(10.0)


def test_score_projection_accepts_a_pandas_row():
    row = pd.Series({"rec": 80.0, "rec_yd": 900.0, "pass_td": None})
    assert value.score_projection(row, HALF_PPR_TD6) == pytest.approx(40 + 90)


# -------------------------------------------------------- replacement_level

def synthetic(points_by_pos):
    rows = []
    for pos, points in points_by_pos.items():
        for i, p in enumerate(points, start=1):
            rows.append({"name": f"{pos}{i}", "pos": pos, "proj_points": float(p)})
    return pd.DataFrame(rows)


def test_replacement_rank_is_starting_slots_times_teams():
    df = synthetic({"QB": [30, 28, 26, 24], "RB": [20, 18, 16, 14]})
    got = value.replacement_level(df, rules({"QB": 1, "RB": 1}, teams=2))
    assert got["QB"] == 28  # 1 slot x 2 teams -> QB2
    assert got["RB"] == 18


def test_flex_slots_absorb_whichever_position_is_worth_more():
    df = synthetic({
        "QB": [30, 28, 26, 24],
        "RB": [20, 18, 16, 14],
        "WR": [19, 17, 15, 13],
    })
    got = value.replacement_level(df, rules({"QB": 1, "RB": 1, "WR": 1, "FLEX": 1}, teams=2))
    # Two flex spots take RB3 (16) then WR3 (15); QB is not flex-eligible.
    assert got["QB"] == 28
    assert got["RB"] == 16
    assert got["WR"] == 15


def test_superflex_pushes_the_quarterback_replacement_much_deeper():
    df = synthetic({"QB": [30, 28, 26, 24], "RB": [20, 18, 16, 14]})
    one_qb = value.replacement_level(df, rules({"QB": 1, "RB": 1}, teams=2))
    superflex = value.replacement_level(
        df, rules({"QB": 1, "RB": 1, "SUPER_FLEX": 1}, teams=2)
    )
    assert one_qb["QB"] == 28
    assert superflex["QB"] == 24, "both superflex spots take a QB over RB3"
    assert superflex["RB"] == 18


def test_rec_flex_never_absorbs_a_running_back():
    df = synthetic({"RB": [40, 39, 38, 37], "WR": [20, 18, 16, 14], "TE": [12, 10, 8, 6]})
    got = value.replacement_level(df, rules({"WR": 1, "REC_FLEX": 1}, teams=2))
    # 2 WR slots, then 2 REC_FLEX spots go to WR3 and WR4 because both beat TE1.
    assert got["WR"] == 14
    assert "RB" not in got, "REC_FLEX is WR/TE only; RBs are never startable here"


def test_replacement_falls_back_to_the_worst_player_when_the_pool_is_short():
    df = synthetic({"QB": [30, 28]})
    got = value.replacement_level(df, rules({"QB": 1}, teams=12))
    assert got["QB"] == 28


def test_replacement_is_zero_for_a_position_with_no_players():
    df = synthetic({"QB": [30, 28]})
    got = value.replacement_level(df, rules({"QB": 1, "TE": 1}, teams=2))
    assert got["TE"] == 0.0


# ----------------------------------------------------- value_over_replacement

def test_value_over_replacement_adds_projection_and_vor_columns():
    df = pd.DataFrame([
        {"name": "QB1", "pos": "QB", "pass_yd": 4500.0, "pass_td": 35.0, "pass_int": 10.0},
        {"name": "QB2", "pos": "QB", "pass_yd": 4000.0, "pass_td": 25.0, "pass_int": 12.0},
        {"name": "QB3", "pos": "QB", "pass_yd": 3500.0, "pass_td": 20.0, "pass_int": 14.0},
    ])
    out = value.value_over_replacement(df, rules({"QB": 1}, teams=2))

    assert list(out.columns[-3:]) == ["proj_points", "replacement", "vor"]
    qb1 = out.set_index("name").loc["QB1"]
    qb2 = out.set_index("name").loc["QB2"]
    assert qb1["proj_points"] == pytest.approx(4500 * 0.04 + 35 * 6 - 20)
    assert qb2["vor"] == pytest.approx(0.0), "QB2 is the replacement in a 2-team 1QB league"
    assert qb1["vor"] == pytest.approx(qb1["proj_points"] - qb2["proj_points"])


def test_value_over_replacement_sorts_best_first():
    df = pd.DataFrame([
        {"name": "low", "pos": "RB", "rush_yd": 500.0},
        {"name": "high", "pos": "RB", "rush_yd": 1500.0},
    ])
    out = value.value_over_replacement(df, rules({"RB": 1}, teams=1))
    assert out.iloc[0]["name"] == "high"
