import datetime as dt

import pytest

from ff.sources import odds
from tests.conftest import RecordingRoutes, fixture_json, fixture_text

SCOREBOARD = fixture_json("espn_scoreboard_20260913.json")
GAMES = fixture_text("nflverse_games.csv")

SUNDAY = dt.date(2026, 9, 13)


def routes(scoreboard=SCOREBOARD):
    return RecordingRoutes({"/scoreboard": scoreboard, "games.csv": GAMES})


def mini(home, away, spread, over_under=45.0):
    """One scoreboard game, for the cases the synthetic fixture does not cover."""
    return {
        "events": [{
            "id": "1",
            "shortName": f"{away} @ {home}",
            "date": "2026-09-13T17:00Z",
            "competitions": [{
                "id": "1",
                "date": "2026-09-13T17:00Z",
                "neutralSite": False,
                "venue": {"fullName": "Test Field", "indoor": False},
                "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": home}},
                    {"homeAway": "away", "team": {"abbreviation": away}},
                ],
                "odds": [{
                    "provider": {"id": "100", "name": "Example Lines", "priority": 1},
                    "details": f"{home} {spread}",
                    "overUnder": over_under,
                    "spread": spread,
                }],
            }],
        }]
    }


# ------------------------------------------------------------------ game_odds

def test_game_odds_reads_spread_and_total_for_every_game():
    got = {g.matchup: g for g in odds.game_odds(SUNDAY, client=routes().client())}

    assert set(got) == {"TB@CIN", "BAL@IND", "WAS@PHI", "ARI@LAC"}, (
        "matchups are keyed by the Sleeper spelling, so WSH reads WAS"
    )
    assert got["TB@CIN"].spread == pytest.approx(-3.5)
    assert got["TB@CIN"].over_under == pytest.approx(50.5)
    assert got["TB@CIN"].provider == "Example Lines"


def test_game_odds_requests_the_date_in_espn_format():
    r = routes()
    odds.game_odds(SUNDAY, client=r.client())
    assert "dates=20260913" in str(r.requests[0].url)


def test_game_odds_carries_the_kickoff_time():
    got = {g.matchup: g for g in odds.game_odds(SUNDAY, client=routes().client())}
    assert got["TB@CIN"].kickoff == dt.datetime(2026, 9, 13, 17, 0, tzinfo=dt.timezone.utc)


def test_a_game_with_no_posted_line_carries_no_totals():
    got = {g.matchup: g for g in odds.game_odds(SUNDAY, client=routes().client())}
    lac = got["ARI@LAC"]
    assert lac.spread is None and lac.over_under is None
    assert lac.home_total is None and lac.away_total is None


# ------------------------------------------------------------ implied totals

def test_implied_total_splits_the_total_around_the_spread():
    """CIN -3.5 on a 50.5 total: 25.25 + 1.75 = 27.0 home, 23.5 away."""
    got = odds.implied_totals(SUNDAY, client=routes().client())
    assert got["CIN"] == pytest.approx(27.0)
    assert got["TB"] == pytest.approx(23.5)


def test_a_positive_spread_means_the_road_team_is_favoured():
    """ESPN reports `spread` from the home team's side; BAL -3.5 reads +3.5."""
    got = odds.implied_totals(SUNDAY, client=routes().client())
    assert got["BAL"] == pytest.approx(26.0)
    assert got["IND"] == pytest.approx(22.5)


def test_implied_totals_always_sum_to_the_over_under():
    for g in odds.game_odds(SUNDAY, client=routes().client()):
        if g.over_under is None:
            continue
        assert g.home_total + g.away_total == pytest.approx(g.over_under)


def test_a_pick_em_splits_the_total_evenly():
    got = odds.implied_totals(SUNDAY, client=RecordingRoutes(
        {"/scoreboard": mini("KC", "DEN", 0.0, 44.0)}).client())
    assert got["KC"] == pytest.approx(22.0)
    assert got["DEN"] == pytest.approx(22.0)


def test_a_game_with_no_line_is_left_out_of_implied_totals():
    got = odds.implied_totals(SUNDAY, client=routes().client())
    assert "LAC" not in got and "ARI" not in got


# ------------------------------------------------------------- team spellings

def test_espn_spells_washington_wsh_and_sleeper_spells_it_was():
    got = odds.implied_totals(SUNDAY, client=routes().client())
    assert "WAS" in got, "the roster calls them WAS; the scoreboard says WSH"
    assert got["WAS"] == pytest.approx(45.5 / 2 - 4.5 / 2)


def test_the_rams_answer_to_both_abbreviations():
    """nflverse says LA, Sleeper and the scoreboard say LAR. Both must resolve."""
    got = odds.implied_totals(SUNDAY, client=RecordingRoutes(
        {"/scoreboard": mini("LAR", "SF", -6.0, 48.0)}).client())
    assert got["LAR"] == pytest.approx(27.0)
    assert got["LA"] == got["LAR"]


# ------------------------------------------------------------------- by week

def test_week_implied_totals_covers_every_game_day_in_the_week():
    r = routes()
    got = odds.week_implied_totals(2026, 1, client=r.client())

    days = {u for u in (str(x.url) for x in r.requests) if "scoreboard" in u}
    assert len(days) >= 3, "week 1 2026 spans Wednesday, Thursday, Sunday and Monday"
    assert got["CIN"] == pytest.approx(27.0)


def test_league_average_implied_total_is_the_baseline_for_the_multiplier():
    totals = odds.implied_totals(SUNDAY, client=routes().client())
    assert odds.baseline_total(totals) == pytest.approx(
        sum(totals[t] for t in ("CIN", "TB", "BAL", "IND", "PHI", "WAS")) / 6
    )


def test_baseline_falls_back_to_a_league_average_when_no_lines_are_posted():
    assert odds.baseline_total({}) == pytest.approx(odds.DEFAULT_TEAM_TOTAL)
