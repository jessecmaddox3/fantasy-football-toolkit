import datetime as dt

import pandas as pd
import pytest

from ff.engine import lineup
from ff.leagues import LeagueRules
from ff.sources.weather import INDOORS, GameWeather

#: One point per "yard" so a projection reads as its own score, and the pass
#: game and the run game are trivially separable.
POINTS = {"pass_yd": 1.0, "rec_yd": 1.0, "rush_yd": 1.0}

STAT_BY_POS = {"QB": "pass_yd", "WR": "rec_yd", "TE": "rec_yd", "RB": "rush_yd"}

SUNDAY = dt.datetime(2026, 9, 13, 13, 0)


def rules(slots, *, teams=12, scoring=None, **kw):
    return LeagueRules(
        key="t", name="t", platform="sleeper", league_id="t",
        format="redraft", num_teams=teams, scoring=scoring or POINTS,
        roster_slots=slots, bench=6, ir=0, taxi=0, waiver_type="faab",
        budget=100, waiver_day="Wednesday", waiver_days=("Wednesday",),
        clear_days=2, trade_deadline_week=11, playoff_start=15, **kw,
    )


def player(pid, pos, points, *, team="KC", stat=None, **extra):
    row = {
        "player_id": pid,
        "name": pid,
        "pos": pos,
        "team": team,
        "opponent": "DEN",
        "injury_status": None,
    }
    row[stat or STAT_BY_POS.get(pos, "rush_yd")] = float(points)
    row.update(extra)
    return row


def projections(*rows):
    return pd.DataFrame(list(rows))


def solve(rows, slots, *, starters=(), week=1, **kw):
    df = projections(*rows)
    return lineup.optimal_lineup(
        [r["player_id"] for r in rows],
        week,
        rules(slots),
        projections=df,
        starters=list(starters),
        **kw,
    )


def by_name(result):
    return {line.name: line for line in result.lines}


def slotted(result):
    return {s.name: (s.player.name if s.player else None) for s in result.optimal}


# ------------------------------------------------------------------- scoring

def test_a_player_is_scored_under_his_own_league_rules():
    got = solve([player("A", "WR", 12.5)], {"WR": 1})
    assert by_name(got)["A"].base_points == pytest.approx(12.5)
    assert by_name(got)["A"].points == pytest.approx(12.5)


def test_a_rostered_player_with_no_projection_row_scores_zero_and_is_flagged():
    df = projections(player("A", "WR", 12.5))
    got = lineup.optimal_lineup(
        ["A", "GHOST"], 1, rules({"WR": 2}), projections=df, starters=[]
    )
    ghost = by_name(got)["GHOST"]
    assert ghost.points == 0.0
    assert "no projection" in ghost.flags


# ------------------------------------------------------------ byes, injuries

def test_a_player_on_bye_is_zeroed_and_flagged():
    got = solve([player("A", "WR", 20, team="DET")], {"WR": 1}, week=6,
                byes={"DET": 6})
    a = by_name(got)["A"]
    assert a.points == 0.0
    assert "bye" in a.flags
    assert a.base_points == pytest.approx(20.0), "the raw projection is still reported"


def test_a_bye_in_another_week_changes_nothing():
    got = solve([player("A", "WR", 20, team="DET")], {"WR": 1}, week=5,
                byes={"DET": 6})
    assert by_name(got)["A"].points == pytest.approx(20.0)


@pytest.mark.parametrize("status", ["Out", "IR", "Doubtful", "Sus"])
def test_an_unavailable_player_is_zeroed_and_flagged(status):
    got = solve([player("A", "WR", 20, injury_status=status)], {"WR": 1})
    a = by_name(got)["A"]
    assert a.points == 0.0
    assert status.lower() in " ".join(a.flags).lower()


def test_questionable_is_flagged_but_never_zeroed():
    """A Q tag clears roughly three times in four, and Rotowire has already
    discounted the projection.  Zeroing would double-count the risk."""
    got = solve([player("A", "WR", 20, injury_status="Questionable")], {"WR": 1})
    a = by_name(got)["A"]
    assert a.points == pytest.approx(20.0)
    assert any("questionable" in f.lower() for f in a.flags)


def test_an_out_player_loses_his_starting_spot_to_a_healthy_bench_player():
    got = solve(
        [player("Star", "WR", 25, injury_status="Out"), player("Scrub", "WR", 6)],
        {"WR": 1},
        starters=["Star"],
    )
    assert slotted(got)["WR"] == "Scrub"
    assert got.delta == pytest.approx(6.0)


# ---------------------------------------------------------------------- wind

def test_wind_under_the_threshold_changes_nothing():
    got = solve([player("A", "WR", 20)], {"WR": 1},
                weather={"KC": GameWeather(wind_speed_10m=14.0)})
    assert by_name(got)["A"].points == pytest.approx(20.0)


def test_wind_downgrades_the_pass_game():
    got = solve([player("A", "WR", 20)], {"WR": 1},
                weather={"KC": GameWeather(wind_speed_10m=22.0)})
    a = by_name(got)["A"]
    assert a.points < 20.0
    assert a.wind_penalty > 0
    assert any("wind" in f for f in a.flags)


def test_the_wind_penalty_is_convex():
    steps = [lineup.wind_penalty(w) for w in (15, 20, 25, 30)]
    marginal = [b - a for a, b in zip(steps, steps[1:])]

    assert steps[0] == 0.0
    assert all(m > 0 for m in marginal)
    assert all(b > a for a, b in zip(marginal, marginal[1:])), marginal


def test_the_second_five_mph_step_costs_about_twice_the_first():
    """Research: the marginal effect from 15-20 to 20+ mph is 1.5-2x."""
    first = lineup.wind_penalty(20) - lineup.wind_penalty(15)
    second = lineup.wind_penalty(25) - lineup.wind_penalty(20)
    assert 1.5 <= second / first <= 2.0


def test_the_wind_penalty_is_capped():
    assert lineup.wind_penalty(60) == pytest.approx(lineup.WIND_CAP)


def test_a_running_back_gains_a_little_in_the_wind_as_the_pass_game_loses():
    windy = {"KC": GameWeather(wind_speed_10m=25.0)}
    got = solve([player("RB", "RB", 20), player("WR", "WR", 20)],
                {"RB": 1, "WR": 1}, weather=windy)

    assert by_name(got)["RB"].points > 20.0
    assert by_name(got)["WR"].points < 20.0
    assert by_name(got)["RB"].points - 20.0 < 20.0 - by_name(got)["WR"].points


def test_a_dome_takes_no_wind_penalty():
    got = solve([player("A", "WR", 20)], {"WR": 1}, weather={"KC": INDOORS})
    assert by_name(got)["A"].points == pytest.approx(20.0)


def test_an_unknown_retractable_roof_takes_no_wind_penalty_but_is_flagged():
    got = solve([player("A", "WR", 20)], {"WR": 1}, weather={
        "KC": GameWeather(wind_speed_10m=28.0, unknown=True, note="retractable roof")
    })
    a = by_name(got)["A"]
    assert a.points == pytest.approx(20.0)
    assert any("roof" in f or "unknown" in f for f in a.flags)


def test_a_team_with_no_weather_entry_is_left_alone():
    got = solve([player("A", "WR", 20)], {"WR": 1}, weather={})
    assert by_name(got)["A"].points == pytest.approx(20.0)


# ------------------------------------------------------------ implied totals

def test_a_high_implied_total_lifts_a_player_by_at_most_the_bound():
    got = solve([player("A", "WR", 20)], {"WR": 1},
                implied_totals={"KC": 40.0}, baseline=22.0)
    assert by_name(got)["A"].points == pytest.approx(20.0 * (1 + lineup.TOTAL_BOUND))


def test_a_low_implied_total_cuts_a_player_by_at_most_the_bound():
    got = solve([player("A", "WR", 20)], {"WR": 1},
                implied_totals={"KC": 3.0}, baseline=22.0)
    assert by_name(got)["A"].points == pytest.approx(20.0 * (1 - lineup.TOTAL_BOUND))


def test_a_team_on_the_slate_average_is_not_moved_at_all():
    got = solve([player("A", "WR", 20)], {"WR": 1},
                implied_totals={"KC": 22.0}, baseline=22.0)
    assert by_name(got)["A"].points == pytest.approx(20.0)
    assert by_name(got)["A"].total_multiplier == pytest.approx(1.0)


def test_the_multiplier_moves_smoothly_between_the_bounds():
    got = solve([player("A", "WR", 20)], {"WR": 1},
                implied_totals={"KC": 25.0}, baseline=22.0)
    mult = by_name(got)["A"].total_multiplier
    assert 1.0 < mult < 1 + lineup.TOTAL_BOUND


def test_a_defence_reads_its_opponents_implied_total_not_its_own():
    """A DST is good when the other side is expected to score nothing."""
    got = solve([player("D", "DEF", 10, team="KC", opponent="DEN")], {"DEF": 1},
                implied_totals={"KC": 30.0, "DEN": 13.0}, baseline=22.0)
    assert by_name(got)["D"].points > 10.0


def test_a_team_with_no_posted_line_is_not_moved():
    got = solve([player("A", "WR", 20)], {"WR": 1},
                implied_totals={"BUF": 30.0}, baseline=22.0)
    assert by_name(got)["A"].points == pytest.approx(20.0)


# ---------------------------------------------------------- slot assignment

def test_a_flex_slot_does_not_get_to_eat_the_only_receiver():
    """The greedy trap: filling FLEX first leaves the WR slot empty."""
    got = solve([player("WR1", "WR", 20), player("RB1", "RB", 19)],
                {"FLEX": 1, "WR": 1})
    assert slotted(got) == {"FLEX": "RB1", "WR": "WR1"}
    assert got.optimal_points == pytest.approx(39.0)


def test_a_superflex_slot_does_not_get_to_eat_the_only_quarterback():
    got = solve([player("QB1", "QB", 25), player("RB1", "RB", 20)],
                {"SUPER_FLEX": 1, "QB": 1})
    assert slotted(got) == {"SUPER_FLEX": "RB1", "QB": "QB1"}


def test_a_superflex_takes_a_second_quarterback_when_one_is_worth_it():
    got = solve([player("QB1", "QB", 25), player("QB2", "QB", 22),
                 player("RB1", "RB", 18)],
                {"QB": 1, "SUPER_FLEX": 1})
    assert slotted(got)["SUPER_FLEX"] == "QB2"


def test_a_rec_flex_never_starts_a_running_back():
    got = solve([player("RB1", "RB", 40), player("WR1", "WR", 10)],
                {"REC_FLEX": 1})
    assert slotted(got) == {"REC_FLEX": "WR1"}


def test_a_league_with_no_tight_end_slot_can_still_flex_one():
    """GP has no TE slot; a tight end reaches the lineup through REC_FLEX."""
    got = solve([player("TE1", "TE", 18), player("WR1", "WR", 9)],
                {"WR": 1, "REC_FLEX": 1})
    assert slotted(got) == {"WR": "WR1", "REC_FLEX": "TE1"}


def test_a_league_with_no_kicker_slot_never_starts_a_kicker():
    got = solve([player("K1", "K", 30, stat="rush_yd"), player("WR1", "WR", 9)],
                {"WR": 1})
    assert slotted(got) == {"WR": "WR1"}
    assert "K1" in [line.name for line in got.bench]


def test_a_defence_fills_the_def_slot_however_the_league_spells_it():
    got = solve([player("D", "DEF", 8)], {"DST": 1})
    assert slotted(got) == {"DST": "D"}


def test_duplicate_slots_are_filled_with_the_two_best_eligible_players():
    got = solve([player("WR1", "WR", 20), player("WR2", "WR", 18),
                 player("WR3", "WR", 16)],
                {"WR": 2})
    assert sorted(s.player.name for s in got.optimal) == ["WR1", "WR2"]


def test_a_slot_with_nobody_eligible_is_reported_empty_rather_than_filled():
    got = solve([player("WR1", "WR", 20)], {"WR": 1, "QB": 1})
    assert slotted(got)["QB"] is None


def test_the_exact_solve_beats_filling_slots_one_at_a_time():
    """A four-slot case where any single pass over the slots loses points."""
    got = solve(
        [player("WR1", "WR", 21), player("WR2", "WR", 15), player("TE1", "TE", 14),
         player("RB1", "RB", 20), player("RB2", "RB", 13)],
        {"RB": 1, "WR": 1, "FLEX": 1, "REC_FLEX": 1},
    )
    assert got.optimal_points == pytest.approx(21 + 20 + 15 + 14)
    assert slotted(got)["REC_FLEX"] in ("WR2", "TE1")


# ------------------------------------------------------- the current lineup

def test_the_current_lineup_is_read_positionally_off_the_starters_array():
    """Sleeper's `starters` is ordered to match `roster_positions`."""
    got = solve([player("QB1", "QB", 25), player("RB1", "RB", 20),
                 player("WR1", "WR", 18)],
                {"QB": 1, "RB": 1, "WR": 1},
                starters=["QB1", "RB1", "WR1"])
    assert {s.name: s.player.name for s in got.current} == {
        "QB": "QB1", "RB": "RB1", "WR": "WR1"
    }
    assert got.current_points == pytest.approx(63.0)
    assert got.delta == pytest.approx(0.0)


def test_an_empty_starter_slot_reads_as_empty_not_as_a_player():
    got = solve([player("QB1", "QB", 25)], {"QB": 1, "RB": 1},
                starters=["QB1", "0"])
    assert {s.name: (s.player.name if s.player else None) for s in got.current} == {
        "QB": "QB1", "RB": None
    }


def test_the_delta_is_what_the_better_lineup_is_worth():
    got = solve([player("WR1", "WR", 20), player("WR2", "WR", 8)],
                {"WR": 1}, starters=["WR2"])
    assert got.current_points == pytest.approx(8.0)
    assert got.optimal_points == pytest.approx(20.0)
    assert got.delta == pytest.approx(12.0)


def test_a_swap_names_the_player_to_start_the_player_to_sit_and_the_gain():
    got = solve([player("WR1", "WR", 20), player("WR2", "WR", 8)],
                {"WR": 1}, starters=["WR2"])
    (swap,) = got.swaps
    assert swap.start.name == "WR1"
    assert swap.sit.name == "WR2"
    assert swap.slot == "WR"
    assert swap.gain == pytest.approx(12.0)


def test_an_already_optimal_lineup_recommends_nothing():
    got = solve([player("WR1", "WR", 20), player("WR2", "WR", 8)],
                {"WR": 1}, starters=["WR1"])
    assert got.swaps == ()
    assert got.delta == pytest.approx(0.0)


def test_filling_an_empty_slot_is_a_swap_with_nobody_to_sit():
    got = solve([player("WR1", "WR", 20)], {"WR": 1}, starters=["0"])
    (swap,) = got.swaps
    assert swap.start.name == "WR1"
    assert swap.sit is None
    assert swap.gain == pytest.approx(20.0)


# ---------------------------------------------------------------- lock rules

KICKOFFS = {"KC": dt.datetime(2026, 9, 13, 13, 0), "BUF": dt.datetime(2026, 9, 13, 16, 25)}


def test_nothing_is_locked_when_no_clock_is_supplied():
    got = solve([player("A", "WR", 20)], {"WR": 1}, kickoffs=KICKOFFS)
    assert not by_name(got)["A"].locked


def test_a_player_whose_game_has_kicked_off_cannot_be_moved():
    got = solve(
        [player("Started", "WR", 4, team="KC"), player("Better", "WR", 20, team="BUF")],
        {"WR": 1},
        starters=["Started"],
        kickoffs=KICKOFFS,
        now=dt.datetime(2026, 9, 13, 13, 30),
    )
    assert by_name(got)["Started"].locked
    assert slotted(got)["WR"] == "Started", "his game is already under way"
    assert got.swaps == ()


def test_a_bench_player_whose_game_has_kicked_off_cannot_be_moved_in():
    got = solve(
        [player("Bench", "WR", 25, team="KC"), player("Starter", "WR", 8, team="BUF")],
        {"WR": 1},
        starters=["Starter"],
        kickoffs=KICKOFFS,
        now=dt.datetime(2026, 9, 13, 14, 0),
    )
    assert by_name(got)["Bench"].locked
    assert slotted(got)["WR"] == "Starter"


def test_before_kickoff_everything_is_still_movable():
    got = solve(
        [player("Bench", "WR", 25, team="KC"), player("Starter", "WR", 8, team="BUF")],
        {"WR": 1},
        starters=["Starter"],
        kickoffs=KICKOFFS,
        now=dt.datetime(2026, 9, 13, 11, 0),
    )
    assert slotted(got)["WR"] == "Bench"
    assert got.delta == pytest.approx(17.0)


def test_bench_lock_freezes_the_bench_at_the_first_kickoff_of_the_week():
    """A league may report `bench_lock: 1`."""
    df = projections(player("Bench", "WR", 25, team="BUF"),
                     player("Starter", "WR", 8, team="BUF"))
    got = lineup.optimal_lineup(
        ["Bench", "Starter"], 1, rules({"WR": 1}, bench_lock=True),
        projections=df, starters=["Starter"], kickoffs=KICKOFFS,
        now=dt.datetime(2026, 9, 13, 13, 30),
    )
    assert by_name(got)["Bench"].locked
    assert slotted(got)["WR"] == "Starter"
    assert any("bench" in n for n in got.notes)


def test_without_bench_lock_a_later_game_can_still_be_swapped_in():
    df = projections(player("Bench", "WR", 25, team="BUF"),
                     player("Starter", "WR", 8, team="BUF"))
    got = lineup.optimal_lineup(
        ["Bench", "Starter"], 1, rules({"WR": 1}, bench_lock=False),
        projections=df, starters=["Starter"], kickoffs=KICKOFFS,
        now=dt.datetime(2026, 9, 13, 13, 30),
    )
    assert slotted(got)["WR"] == "Bench"


def test_a_locked_starter_keeps_the_slot_he_is_already_in():
    got = solve(
        [player("QB1", "QB", 22, team="KC"), player("RB1", "RB", 19, team="BUF"),
         player("RB2", "RB", 25, team="BUF")],
        {"QB": 1, "SUPER_FLEX": 1},
        starters=["QB1", "RB1"],
        kickoffs=KICKOFFS,
        now=dt.datetime(2026, 9, 13, 13, 30),
    )
    assert slotted(got)["QB"] == "QB1"
    assert slotted(got)["SUPER_FLEX"] == "RB2", "the unlocked slot still improves"
