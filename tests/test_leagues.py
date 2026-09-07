import json

import pytest

from ff import leagues
from ff.sources import nflverse
from tests.conftest import DATA, FIXTURES, RecordingRoutes, fixture_text


def sleeper_raw(name):
    return json.loads((FIXTURES / f"sleeper_league_{name}.json").read_text())


GP = json.loads((FIXTURES / "sleeper_league_keeper.json").read_text())
ESPN = json.loads((FIXTURES / "espn_league.json").read_text())


# ---------------------------------------------------------------- registry

# ---------------------------------------------------------------- sleeper

def test_gp_normalizes_to_a_superflex_keeper_league_with_no_te_slot():
    r = leagues.normalize_league(GP, key="gp")
    assert r.platform == "sleeper"
    assert r.format == "keeper"
    assert r.num_teams == 12
    assert r.roster_slots == {
        "QB": 1, "RB": 2, "WR": 2, "FLEX": 2, "REC_FLEX": 1,
        "SUPER_FLEX": 1, "K": 1, "DEF": 1,
    }
    assert "TE" not in r.roster_slots
    assert r.superflex is True
    assert r.bench == 9
    assert r.ir == 3
    assert r.taxi == 0


def test_gp_waiver_mechanics_are_faab_wednesday_two_day_clear():
    r = leagues.normalize_league(GP, key="gp")
    assert r.waiver_type == "faab"
    assert r.budget == 1000
    assert r.waiver_day == "Wednesday"
    assert r.clear_days == 2
    assert r.trade_deadline_week == 11
    assert r.playoff_start == 15


def test_gp_scoring_keeps_the_te_premium_and_first_down_bonuses():
    r = leagues.normalize_league(GP, key="gp")
    assert r.scoring["rec"] == 0.5
    assert r.scoring["bonus_rec_te"] == 0.5
    assert r.scoring["rec_fd"] == 0.5
    assert r.scoring["rush_fd"] == 0.5
    assert r.scoring["pass_td"] == 4.0


def test_redraft_is_a_priority_waiver_redraft_with_six_point_passing_tds():
    r = leagues.normalize_league(sleeper_raw("redraft"), key="redraft")
    assert r.format == "redraft"
    assert r.waiver_type == "priority"
    assert r.budget is None, "waiver_budget is 100 but waiver_type 0 means priority"
    assert r.scoring["pass_td"] == 6.0
    assert r.superflex is False
    assert r.roster_slots["TE"] == 1
    assert r.bench == 5


def test_dynasty_is_a_dynasty_superflex_with_a_taxi_squad_and_no_kicker():
    r = leagues.normalize_league(sleeper_raw("dynasty"), key="dynasty")
    assert r.format == "dynasty"
    assert r.superflex is True
    assert r.taxi == 4
    assert r.ir == 4
    assert "K" not in r.roster_slots
    assert r.waiver_type == "faab"
    assert r.budget == 100
    assert r.clear_days == 3
    assert r.trade_deadline_week == 12


# ------------------------------------------------------------------- espn

def test_espn_normalizes_slot_ids_into_named_starting_slots():
    r = leagues.normalize_league(ESPN, key="espn")
    assert r.platform == "espn"
    assert r.roster_slots == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "DEF": 1, "K": 1, "FLEX": 1}
    assert r.bench == 5
    assert r.ir == 1
    assert r.num_teams == 12
    # 15 matchup periods, 4 playoff teams, 1 week per round -> weeks 14 and 15.
    assert r.playoff_start == 14


def test_espn_scoring_items_map_onto_sleeper_stat_names():
    r = leagues.normalize_league(ESPN, key="espn")
    assert r.scoring["rec"] == 1.0
    assert r.scoring["pass_yd"] == 0.04
    assert r.scoring["pass_td"] == 4.0
    assert r.scoring["pass_int"] == -2.0
    assert r.scoring["rec_yd"] == 0.1
    assert r.scoring["fum_lost"] == -2.0


def test_espn_waivers_are_priority_and_clear_in_a_day():
    r = leagues.normalize_league(ESPN, key="espn")
    assert r.waiver_type == "priority"
    assert r.budget is None
    assert r.clear_days == 1
    assert "Tuesday" not in r.waiver_days
    assert len(r.waiver_days) == 6


def test_espn_trade_deadline_week_needs_the_schedule_to_resolve():
    without = leagues.normalize_league(ESPN, key="espn")
    assert without.trade_deadline_week is None
    assert without.trade_deadline_date.strftime("%Y-%m-%d") == "2026-12-04"


def test_espn_trade_deadline_week_resolves_against_a_schedule():
    r = RecordingRoutes({"games.csv": fixture_text("nflverse_games.csv")})
    schedule = nflverse.games(2026, client=r.client())
    raw = json.loads(json.dumps(ESPN))
    # Move the deadline into the range the fixture covers. Week 3 kicks off
    # Thu 2026-09-24, so a Friday-morning deadline belongs to week 3.
    raw["settings"]["tradeSettings"]["deadlineDate"] = 1790337600000  # 2026-09-25 12:00Z

    rules = leagues.normalize_league(raw, key="espn", schedule=schedule)
    assert rules.trade_deadline_week == 3


# --------------------------------------------------------------- adp field

@pytest.mark.parametrize(
    "key,expected",
    [
        ("redraft", "adp_half_ppr"),
        ("espn", "adp_ppr"),
        ("gp", "adp_2qb"),
        ("dynasty", "adp_dynasty_2qb"),
    ],
)
def test_adp_field_matches_the_league_format(key, expected):
    raw = {"gp": GP, "espn": ESPN, "redraft": sleeper_raw("redraft"), "dynasty": sleeper_raw("dynasty")}[key]
    assert leagues.adp_field(leagues.normalize_league(raw, key=key)) == expected


def test_starting_slot_counts_sum_to_the_lineup_size():
    r = leagues.normalize_league(GP, key="gp")
    assert sum(r.roster_slots.values()) == 11
