import json

import pandas as pd
import pytest

from ff import cli, leagues
from ff.sources import dynastyprocess as dp
from ff.sources import sleeper
from tests.conftest import DATA, FIXTURES, RecordingRoutes, fixture_json, fixture_text

PROJ = fixture_json("sleeper_season_projections.json")
REDRAFT = json.loads((FIXTURES / "sleeper_league_redraft.json").read_text())
DYNASTY = json.loads((FIXTURES / "sleeper_league_dynasty.json").read_text())
BYES = {"DET": 6, "CIN": 6, "BUF": 7, "LV": 13, "NYG": 8}


def projections():
    r = RecordingRoutes({"/projections/nfl/2026": PROJ})
    return sleeper.season_projections(2026, client=r.client())


def values():
    r = RecordingRoutes({"values.csv": fixture_text("dynastyprocess_values.csv")})
    return dp.values(client=r.client())


def test_board_reports_the_columns_a_draft_room_needs():
    rules = leagues.normalize_league(REDRAFT, key="redraft")
    board = cli.build_board(projections(), rules, byes=BYES, top=10)

    assert list(board.columns) == [
        "rank", "name", "pos", "team", "bye", "adp", "proj", "vor"
    ]
    assert board["rank"].tolist() == list(range(1, len(board) + 1))


def test_board_attaches_bye_weeks_that_sleeper_never_provides():
    rules = leagues.normalize_league(REDRAFT, key="redraft")
    board = cli.build_board(projections(), rules, byes=BYES, top=10).set_index("name")
    assert board.loc["Jahmyr Gibbs", "bye"] == 6
    assert board.loc["Brock Bowers", "bye"] == 13


def test_bye_week_prints_as_a_whole_number():
    rules = leagues.normalize_league(REDRAFT, key="redraft")
    board = cli.build_board(projections(), rules, byes=BYES, top=10)
    assert str(board["bye"].dtype) == "Int64"
    assert "6.0" not in cli.render_table(board[["bye"]])


def test_board_uses_the_format_appropriate_adp_column():
    redraft = leagues.normalize_league(REDRAFT, key="redraft")
    dynasty = leagues.normalize_league(DYNASTY, key="dynasty")
    proj = projections()

    redraft_board = cli.build_board(proj, redraft, byes=BYES, top=10).set_index("name")
    dynasty_board = cli.build_board(proj, dynasty, byes=BYES, top=10).set_index("name")

    assert redraft_board.loc["Josh Allen", "adp"] == proj.set_index("name").loc["Josh Allen", "adp_half_ppr"]
    assert dynasty_board.loc["Josh Allen", "adp"] == proj.set_index("name").loc["Josh Allen", "adp_dynasty_2qb"]


def test_board_drops_positions_the_league_cannot_start():
    """Example Dynasty has no kicker slot, so kickers never reach the board."""
    rules = leagues.normalize_league(DYNASTY, key="dynasty")
    proj = projections()
    proj.loc[len(proj)] = {**{c: None for c in proj.columns},
                           "name": "Some Kicker", "pos": "K", "team": "DET"}
    board = cli.build_board(proj, rules, byes=BYES, top=50)
    assert "Some Kicker" not in board["name"].tolist()


def test_board_is_ordered_by_value_over_replacement():
    rules = leagues.normalize_league(REDRAFT, key="redraft")
    board = cli.build_board(projections(), rules, byes=BYES, top=10)
    assert board["vor"].tolist() == sorted(board["vor"].tolist(), reverse=True)


def test_board_honours_the_top_limit():
    rules = leagues.normalize_league(REDRAFT, key="redraft")
    assert len(cli.build_board(projections(), rules, byes=BYES, top=3)) == 3


# ------------------------------------------------------------ rookie board

def test_rookie_board_keeps_only_first_year_players():
    rules = leagues.normalize_league(DYNASTY, key="dynasty")
    board = cli.build_rookie_board(projections(), rules, values(), byes=BYES, top=50)
    names = board["name"].tolist()
    assert "Jahmyr Gibbs" not in names, "years_exp 3"
    assert "Jaxson Dart" not in names, "years_exp 1: a 2025 rookie is not a 2026 rookie"
    assert "Jeremiyah Love" in names
    assert "Carnell Tate" in names


def test_rookie_board_sorts_by_dynasty_superflex_adp():
    rules = leagues.normalize_league(DYNASTY, key="dynasty")
    board = cli.build_rookie_board(projections(), rules, values(), byes=BYES, top=50)
    adps = [a for a in board["adp"].tolist() if a == a]
    assert adps == sorted(adps)


def test_rookie_board_joins_dynastyprocess_value_2qb():
    rules = leagues.normalize_league(DYNASTY, key="dynasty")
    vals = values()
    expected = vals.set_index("player").loc["Jeremiyah Love"]

    board = cli.build_rookie_board(projections(), rules, vals, byes=BYES, top=50)

    love = board.set_index("name").loc["Jeremiyah Love"]
    assert love["value_2qb"] == expected["value_2qb"] == 7000.0
    assert love["ecr_2qb"] == expected["ecr_2qb"]


def test_rookie_board_columns():
    rules = leagues.normalize_league(DYNASTY, key="dynasty")
    board = cli.build_rookie_board(projections(), rules, values(), byes=BYES, top=50)
    assert list(board.columns) == [
        "rank", "name", "pos", "team", "bye", "adp", "value_2qb", "ecr_2qb", "proj"
    ]


# ---------------------------------------------------------------- rendering

def test_render_table_prints_a_header_and_one_line_per_row():
    df = pd.DataFrame([{"rank": 1, "name": "A", "vor": 12.345}])
    text = cli.render_table(df)
    lines = text.splitlines()
    assert lines[0].split() == ["rank", "name", "vor"]
    assert "12.3" in lines[-1]
    assert "A" in lines[-1]


def test_unknown_league_key_fails_loudly():
    with pytest.raises(SystemExit):
        cli.main(["board", "--league", "nope"])


def test_scoring_caveats_flag_espn_kicker_and_dst_gaps():
    espn_rules = leagues.normalize_league(
        json.loads((FIXTURES / "espn_league.json").read_text()), key="espn"
    )
    notes = cli.scoring_caveats(espn_rules)
    assert any("K/DST" in n for n in notes)
    assert any(str(len(espn_rules.unmapped_stat_ids)) in n for n in notes)


def test_scoring_caveats_flag_the_missing_short_field_goal_projection():
    rules = leagues.normalize_league(REDRAFT, key="redraft")
    notes = cli.scoring_caveats(rules)
    assert any("field goal" in n for n in notes)


def test_a_league_with_no_kicker_slot_gets_no_kicking_caveat():
    rules = leagues.normalize_league(DYNASTY, key="dynasty")
    assert cli.scoring_caveats(rules) == []


# ------------------------------------------------------------------- lineup

def lineup_result(starters=("WR2",)):
    from tests.test_lineup import player, solve

    return solve([player("WR1", "WR", 20.0), player("WR2", "WR", 8.0)],
                 {"WR": 1}, starters=list(starters))


def lineup_rules(**kw):
    from tests.test_lineup import rules as make_rules

    return make_rules({"WR": 1}, **kw)


def test_the_lineup_render_has_one_row_per_starting_slot():
    text = cli.render_lineup(lineup_result(), lineup_rules())
    header, _rule, *rows = text.split("\n\n")[0].splitlines()
    assert header.split() == ["slot", "player", "pos", "team", "proj", "base", "flags"]
    assert len(rows) == 1


def test_the_lineup_render_states_current_optimal_and_delta():
    text = cli.render_lineup(lineup_result(), lineup_rules())
    assert "current 8.0" in text
    assert "optimal 20.0" in text
    assert "delta +12.0" in text


def test_the_lineup_render_lists_the_recommended_moves():
    text = cli.render_lineup(lineup_result(), lineup_rules())
    assert "recommended moves:" in text
    assert "start WR1" in text


def test_an_optimal_lineup_says_there_is_nothing_to_do():
    text = cli.render_lineup(lineup_result(starters=("WR1",)), lineup_rules())
    assert "already is the optimal one" in text
    assert "recommended moves:" not in text


def test_the_lineup_render_shows_the_best_bench_players():
    text = cli.render_lineup(lineup_result(), lineup_rules())
    assert "best bench: WR2 8.0" in text


def test_the_week_defaults_to_whatever_sleeper_says_so_a_cron_job_needs_no_edit():
    args = cli.build_parser().parse_args(["lineup", "--league", "gp"])
    assert args.week is None


def test_the_current_week_is_read_off_sleepers_own_state(monkeypatch):
    monkeypatch.setattr(
        sleeper, "state", lambda **kw: {"season": "2026", "week": 4, "display_week": 4}
    )
    assert cli.current_week(2026) == 4


def test_an_out_of_season_state_falls_back_to_week_one(monkeypatch):
    """Sleeper still reports last season's week 18 during the off-season."""
    monkeypatch.setattr(sleeper, "state", lambda **kw: {"season": "2025", "week": 18})
    assert cli.current_week(2026) == 1


def test_the_lineup_command_takes_all_leagues_at_once():
    args = cli.build_parser().parse_args(["lineup", "--all", "--week", "1"])
    assert args.all and args.week == 1 and args.league is None


def test_the_brief_command_defaults_to_every_league():
    args = cli.build_parser().parse_args(["brief", "--week", "2"])
    assert args.week == 2 and args.leagues is None


def test_the_brief_command_can_be_narrowed_to_named_leagues():
    args = cli.build_parser().parse_args(["brief", "--week", "2", "--leagues", "gp", "dynasty"])
    assert args.leagues == ["gp", "dynasty"]
