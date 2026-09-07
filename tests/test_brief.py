import json

import pytest

from ff.engine import brief
from ff.engine.lineup import PlayerLine
from ff.leagues import LeagueRules
from tests.conftest import FIXTURES, fixture_json

REDRAFT = json.loads((FIXTURES / "sleeper_league_redraft.json").read_text())

USERS = [
    {"user_id": "1", "display_name": "manager-one", "metadata": {"team_name": "Example Team"}},
    {"user_id": "2", "display_name": "manager-two", "metadata": {}},
    {"user_id": "3", "display_name": "rando", "metadata": None},
]

ROSTERS = [
    {"roster_id": 1, "owner_id": "1", "settings": {"wins": 1, "losses": 1, "fpts": 210, "fpts_decimal": 50}},
    {"roster_id": 2, "owner_id": "2", "settings": {"wins": 2, "losses": 0, "fpts": 240}},
    {"roster_id": 3, "owner_id": "3", "settings": {"wins": 1, "losses": 1, "fpts": 190}},
]


def rules(slots, **kw):
    return LeagueRules(
        key="t", name="t", platform="sleeper", league_id="t", format="redraft",
        num_teams=12, scoring={"rec": 1.0}, roster_slots=slots, bench=6, ir=0,
        taxi=0, waiver_type=kw.pop("waiver_type", "faab"),
        budget=kw.pop("budget", 100), waiver_day="Wednesday",
        waiver_days=("Wednesday",), clear_days=2,
        trade_deadline_week=kw.pop("trade_deadline_week", 11),
        playoff_start=kw.pop("playoff_start", 15), **kw,
    )


def line(pid, pos, points, *, team="KC", name=None, **kw):
    return PlayerLine(player_id=pid, name=name or pid, pos=pos, team=team,
                      points=points, **kw)


# ------------------------------------------------------------------ standings

def test_standings_are_ordered_by_record_then_points_for():
    got = brief.sleeper_standings(ROSTERS, USERS, "1")
    assert [s.owner for s in got] == ["manager-two", "manager-one", "rando"]


def test_a_standing_prefers_the_team_name_over_the_handle():
    got = brief.sleeper_standings(ROSTERS, USERS, "1")
    assert got[1].name == "Example Team"
    assert got[0].name == "manager-two", "no team name set: fall back to the handle"


def test_manager_is_the_only_standing_marked_mine():
    got = brief.sleeper_standings(ROSTERS, USERS, "1")
    assert [s.mine for s in got] == [False, True, False]


def test_sleeper_points_for_reassembles_the_split_decimal():
    """Sleeper stores 210.5 as fpts 210 plus fpts_decimal 50."""
    got = brief.sleeper_standings(ROSTERS, USERS, "1")
    assert got[1].points_for == pytest.approx(210.5)


def test_espn_standings_come_off_the_overall_record():
    raw = fixture_json("espn_league_roster.json")
    got = brief.espn_standings(raw, 3)
    assert len(got) == 12
    assert sum(s.mine for s in got) == 1


# ---------------------------------------------------- points left on the bench

MATCHUP = {
    "roster_id": 1,
    "matchup_id": 4,
    "points": 100.0,
    "starters": ["qb1", "wr1", "wr2"],
    "starters_points": [20.0, 50.0, 30.0],
    "players_points": {"qb1": 20.0, "wr1": 50.0, "wr2": 30.0, "wr3": 44.0, "rb1": 60.0},
}
INDEX = {
    "qb1": ("Dak", "QB"), "wr1": ("Olave", "WR"), "wr2": ("Smith", "WR"),
    "wr3": ("Adams", "WR"), "rb1": ("Jacobs", "RB"),
}


def test_best_possible_respects_slot_eligibility():
    """A 60-point RB cannot fill a WR slot, however much he outscored the WRs."""
    got = brief.best_possible(MATCHUP, rules({"QB": 1, "WR": 2}), INDEX)
    assert got == pytest.approx(20 + 50 + 44)


def test_points_left_on_the_bench_is_best_possible_minus_actual():
    result = brief.sleeper_week_result(
        [MATCHUP], "1", 3, rules({"QB": 1, "WR": 2}), INDEX, {}
    )
    assert result.points == pytest.approx(100.0)
    assert result.left_on_bench == pytest.approx(14.0)


def test_a_flex_lets_the_running_back_back_in():
    got = brief.best_possible(MATCHUP, rules({"QB": 1, "WR": 2, "FLEX": 1}), INDEX)
    assert got == pytest.approx(20 + 50 + 44 + 60)


def test_a_week_with_no_scores_has_no_best_possible():
    assert brief.best_possible({"players_points": {}}, rules({"QB": 1}), INDEX) is None


def test_the_week_result_names_the_best_and_worst_starter():
    result = brief.sleeper_week_result(
        [MATCHUP], "1", 3, rules({"QB": 1, "WR": 2}), INDEX, {}
    )
    assert result.top == ("Olave", 50.0)
    assert result.bottom == ("Dak", 20.0)


def test_the_week_result_finds_the_opponent_through_the_matchup_id():
    opponent = {"roster_id": 2, "matchup_id": 4, "points": 120.0,
                "starters": [], "starters_points": [], "players_points": {}}
    result = brief.sleeper_week_result(
        [MATCHUP, opponent], "1", 3, rules({"QB": 1, "WR": 2}), INDEX, {"2": "Rivals"}
    )
    assert result.opponent == "Rivals"
    assert result.opponent_points == pytest.approx(120.0)
    assert result.result == "L"


def test_a_roster_that_is_not_in_the_week_has_no_result():
    assert brief.sleeper_week_result([MATCHUP], "9", 3, rules({"QB": 1}), INDEX, {}) is None


# ----------------------------------------------------------------------- byes

BYES = {"DET": 6, "KC": 5, "BUF": 7, "SF": 11}


def test_upcoming_byes_look_three_weeks_ahead_including_this_one():
    lines = (line("a", "WR", 10, team="KC"), line("b", "RB", 10, team="DET"),
             line("c", "TE", 10, team="BUF"), line("d", "QB", 10, team="SF"))
    got = brief.upcoming_byes(lines, BYES, 5)
    assert set(got) == {5, 6, 7}
    assert got[5] == ("a (WR)",)
    assert "d (QB)" not in str(got), "week 11 is outside the horizon"


def test_a_player_with_no_team_is_skipped_rather_than_crashing():
    assert brief.upcoming_byes((line("x", "WR", 0, team=None),), BYES, 5) == {}


# --------------------------------------------------------------- waiver state

def test_faab_state_reports_what_is_left_not_what_was_spent():
    from ff.roster import Team

    team = Team(key="gp", name="gp", platform="sleeper", team_id="7",
                waiver_budget_used=150)
    got = brief.waiver_state(rules({"QB": 1}, budget=1000), team)
    assert "$850 of $1000" in got
    assert "Wednesday" in got


def test_priority_state_reports_the_waiver_position():
    from ff.roster import Team

    team = Team(key="redraft", name="redraft", platform="sleeper", team_id="12",
                waiver_position=10)
    got = brief.waiver_state(
        rules({"QB": 1}, waiver_type="priority", budget=None), team
    )
    assert "#10" in got and "priority" in got


# ------------------------------------------------------------------ deadlines

def test_a_deadline_inside_the_horizon_is_reported():
    assert "trade deadline week 11" in brief.deadlines(rules({"QB": 1}), 9)


def test_a_deadline_far_away_is_not_noise_in_the_brief():
    assert brief.deadlines(rules({"QB": 1}), 2) == ()


def test_the_playoff_start_shows_up_once_it_is_close():
    got = brief.deadlines(rules({"QB": 1}, trade_deadline_week=None), 13)
    assert any("playoffs start week 15" in d for d in got)


# ------------------------------------------------------------------ decisions

def test_an_undrafted_league_says_so_and_says_nothing_else():
    from ff.roster import Team

    team = Team(key="redraft", name="redraft", platform="sleeper", team_id="12")
    got = brief.decisions_for(rules({"QB": 1}), team, None, {}, 1)
    assert got == ("roster is empty: this league has not drafted yet",)


def test_a_worthwhile_lineup_change_becomes_a_decision_with_its_swaps():
    from tests.test_lineup import player, projections, solve

    result = solve([player("WR1", "WR", 20), player("WR2", "WR", 8)],
                   {"WR": 1}, starters=["WR2"])
    team = _drafted_team()
    got = brief.decisions_for(rules({"WR": 1}), team, result, {}, 1)

    assert got[0].startswith("set the lineup: +12.0")
    assert got[1].startswith("  ")
    assert "start WR1" in got[1]


def test_a_lineup_already_optimal_produces_no_lineup_decision():
    from tests.test_lineup import player, solve

    result = solve([player("WR1", "WR", 20)], {"WR": 1}, starters=["WR1"])
    got = brief.decisions_for(rules({"WR": 1}), _drafted_team(), result, {}, 1)
    assert not any("set the lineup" in d for d in got)


def test_an_out_player_still_in_the_lineup_is_called_out():
    from tests.test_lineup import player, solve

    result = solve([player("Star", "WR", 25, injury_status="Out")],
                   {"WR": 1}, starters=["Star"])
    got = brief.decisions_for(rules({"WR": 1}), _drafted_team(), result, {}, 1)
    assert any("replace Star" in d and "Out" in d for d in got)


def test_next_weeks_byes_are_a_decision_this_week():
    got = brief.decisions_for(
        rules({"QB": 1}), _drafted_team(), None, {6: ("Gibbs (RB)", "Chase (WR)")}, 5
    )
    assert any("week 6 byes hit 2" in d for d in got)


def _drafted_team():
    from ff.roster import Team

    return Team(key="t", name="t", platform="sleeper", team_id="1",
                player_ids=("a",))


# ------------------------------------------------------------------ rendering

def test_a_rendered_league_section_leads_with_the_name_and_ends_with_decisions():
    section = brief.render_league(
        brief.LeagueBrief(
            ref=type("R", (), {"name": "Example Redraft League", "platform": "sleeper"})(),
            rules=rules({"QB": 1, "RB": 2}),
            team=_drafted_team(),
            decisions=("do the thing", "  detail"),
        )
    )
    assert section.startswith("## Example Redraft League")
    assert "**Decisions**" in section
    assert "- do the thing" in section
    assert "  - detail" in section, "swap detail nests under its decision"


def test_a_league_with_nothing_to_do_says_so():
    section = brief.render_league(
        brief.LeagueBrief(
            ref=type("R", (), {"name": "GP", "platform": "sleeper"})(),
            rules=rules({"QB": 1}),
            team=_drafted_team(),
        )
    )
    assert "- nothing to do" in section


def test_the_brief_header_counts_decisions_but_not_their_detail_lines():
    one = brief.LeagueBrief(
        ref=type("R", (), {"name": "A", "platform": "sleeper"})(),
        rules=rules({"QB": 1}), team=_drafted_team(),
        decisions=("real", "  detail", "  detail"),
    )
    text = brief.render_brief([one], 3)
    assert "1 decision(s) across 1 leagues" in text
