import json

import pandas as pd
import pytest

from ff import leagues, roster
from ff.leagues import LeagueRef
from tests.conftest import DATA, FIXTURES, fixture_json

ROSTERS = fixture_json("sleeper_rosters.json")
ESPN_ROSTER = fixture_json("espn_league_roster.json")
ESPN_LEAGUE = json.loads((FIXTURES / "espn_league.json").read_text())

SLEEPER_REF = LeagueRef(
    key="gp", name="GP", platform="sleeper", league_id="L1",
    user_id="200001",
)
ESPN_REF = LeagueRef(
    key="espn", name="Example ESPN League", platform="espn",
    league_id="100004", team_id=3,
)

#: Enough of a projection frame to exercise the ESPN name join.
PROJECTIONS = pd.DataFrame([
    {"player_id": "4984", "name": "Josh Allen", "pos": "QB", "team": "BUF"},
    {"player_id": "8146", "name": "Amon-Ra St. Brown", "pos": "WR", "team": "DET"},
    {"player_id": "8112", "name": "Drake London", "pos": "WR", "team": "ATL"},
    {"player_id": "9502", "name": "Nobody At All", "pos": "RB", "team": "KC"},
])


# ------------------------------------------------------------------ Sleeper

def test_the_sleeper_roster_is_found_by_owner_id():
    team = roster.sleeper_team(SLEEPER_REF, ROSTERS)
    assert team.team_id == "1"
    assert len(team.player_ids) == 22
    assert team.starters[0] == "6904"


def test_an_owner_with_no_roster_in_the_league_comes_back_empty():
    ref = LeagueRef(key="x", name="x", platform="sleeper", league_id="L1", user_id="999")
    team = roster.sleeper_team(ref, ROSTERS)
    assert team.player_ids == ()
    assert not team.drafted


def test_the_waiver_state_comes_off_roster_settings():
    team = roster.sleeper_team(SLEEPER_REF, ROSTERS)
    assert team.waiver_budget_used == 0
    assert team.waiver_position == 5


def test_a_record_with_no_ties_is_printed_with_two_numbers():
    team = roster.sleeper_team(SLEEPER_REF, ROSTERS)
    assert team.record == "0-0"


# ---------------------------------------------------------------------- ESPN

def espn_rules():
    return leagues.normalize_league(ESPN_LEAGUE, key="espn")


def espn_team():
    return roster.espn_team(ESPN_REF, ESPN_ROSTER, espn_rules(), projections=PROJECTIONS)


def test_the_espn_roster_is_found_by_team_id():
    team = espn_team()
    assert team.name == "Example Team 3"
    assert len(team.player_ids) == 14


def test_espn_players_are_translated_to_sleeper_ids_by_name():
    team = espn_team()
    assert "4984" in team.player_ids, "Josh Allen"
    assert "8112" in team.player_ids, "Drake London"


def test_an_espn_player_with_no_sleeper_match_keeps_an_espn_id_and_its_metadata():
    team = espn_team()
    unmatched = [p for p in team.player_ids if p.startswith("espn:")]
    assert unmatched, "the trimmed projection frame cannot match everyone"
    meta = team.fallbacks[unmatched[0]]
    assert meta["name"] and meta["pos"]


def test_an_espn_defence_maps_off_the_pro_team_id_not_the_name():
    """A Sleeper defence *is* its team code, so 'Titans D/ST' never name-matches."""
    team = espn_team()
    assert "TEN" in team.player_ids


def test_espn_injury_status_is_translated_into_the_sleeper_spelling():
    team = espn_team()
    statuses = {meta["injury_status"] for meta in team.fallbacks.values()}
    assert "Questionable" in statuses
    assert "QUESTIONABLE" not in statuses
    assert None in statuses, "ACTIVE is not an injury"


def test_the_espn_starters_array_lines_up_with_the_leagues_slot_order():
    from ff.engine.lineup import starting_slots

    team = espn_team()
    slots = starting_slots(espn_rules())
    assert len(team.starters) == len(slots)
    assert team.starters[slots.index("QB")] == "4984", "Josh Allen starts at QB"


def test_espn_bench_and_injured_reserve_players_are_not_starters():
    team = espn_team()
    started = {p for p in team.starters if p != roster.EMPTY_SLOT}
    assert len(started) <= len(team.player_ids)
    assert "8112" not in started, "Drake London is on the bench (slot 20)"


def test_a_team_id_that_is_not_in_the_league_comes_back_empty():
    ref = LeagueRef(key="espn", name="x", platform="espn", league_id="100004", team_id=99)
    team = roster.espn_team(ref, ESPN_ROSTER, espn_rules(), projections=PROJECTIONS)
    assert not team.drafted


def test_the_rams_keep_the_sleeper_spelling_on_an_espn_roster():
    """ESPN's fantasy API says LA; a Sleeper roster and projection say LAR."""
    team = espn_team()
    mevis = next(
        m for m in team.fallbacks.values() if m["name"] == "Harrison Mevis"
    )
    assert mevis["team"] == "LAR"
