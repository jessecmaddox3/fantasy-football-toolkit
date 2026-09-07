import math

from ff.sources import nflverse
from tests.conftest import RecordingRoutes, fixture_text

GAMES = fixture_text("nflverse_games.csv")


def routes():
    return RecordingRoutes({"games.csv": GAMES})


def test_games_returns_only_the_requested_season_with_typed_week():
    df = nflverse.games(2026, client=routes().client())
    assert (df["season"] == 2026).all()
    assert df["week"].dtype.kind == "i"
    assert len(df) == 93


def test_byes_maps_each_idle_team_to_its_missing_week():
    """The fixture covers weeks 1-6 only: CAR/KC are off in 5, four teams in 6."""
    byes = nflverse.byes(2026, client=routes().client())
    assert byes == {"CAR": 5, "KC": 5, "CIN": 6, "DET": 6, "MIA": 6, "MIN": 6}


def test_byes_omits_teams_that_play_every_week_in_range():
    byes = nflverse.byes(2026, client=routes().client())
    assert "BUF" not in byes


def test_stadium_info_attaches_home_team_coordinates():
    df = nflverse.stadium_info(2026, client=routes().client()).set_index("game_id")
    row = df.loc["2026_01_NE_SEA"]
    assert row["stadium"] == "Lumen Field"
    assert row["roof"] == "outdoors"
    assert not row["neutral_site"]
    assert row["lat"] == nflverse.STADIUM_COORDS["SEA"][0]
    assert row["lon"] == nflverse.STADIUM_COORDS["SEA"][1]


def test_international_game_is_flagged_neutral_with_no_coordinates():
    """Nothing in games.csv geolocates an overseas venue; use the stadium name."""
    df = nflverse.stadium_info(2026, client=routes().client()).set_index("game_id")
    row = df.loc["2026_01_SF_LA"]
    assert row["neutral_site"]
    assert row["stadium"] == "Melbourne Cricket Ground"
    assert math.isnan(row["lat"]) and math.isnan(row["lon"])


def test_retractable_roof_reads_unknown_not_outdoors():
    """nflverse leaves `roof` blank until a retractable game is played."""
    df = nflverse.stadium_info(2026, client=routes().client())
    ari = df[(df["home_team"] == "ARI") & (~df["neutral_site"])].iloc[0]
    assert ari["roof"] == "unknown"


def test_every_nfl_team_has_coordinates():
    assert len(set(nflverse.STADIUM_COORDS) - set(nflverse.TEAM_ALIASES)) == 32
    for team, (lat, lon) in nflverse.STADIUM_COORDS.items():
        assert 24 < lat < 49, team
        assert -126 < lon < -66, team


MINI_GAMES = (
    "game_id,season,game_type,week,gameday,away_team,home_team,location,roof,"
    "surface,stadium_id,stadium,gametime\n"
    "a,2026,REG,1,2026-09-10,LA,SF,Home,outdoors,grass,SFO01,Levi's,20:20\n"
    "b,2026,REG,2,2026-09-17,SF,SEA,Home,outdoors,turf,SEA00,Lumen,20:20\n"
)


def test_byes_are_keyed_for_sleeper_as_well_as_nflverse_team_codes():
    """nflverse calls the Rams LA; Sleeper calls them LAR. Both must resolve."""
    r = RecordingRoutes({"games.csv": MINI_GAMES})
    byes = nflverse.byes(2026, client=r.client())
    assert byes["LA"] == 2
    assert byes["LAR"] == 2, "Sleeper spells the Rams LAR; the bye must join either way"


def test_stadium_coords_answer_to_both_rams_abbreviations():
    assert nflverse.STADIUM_COORDS["LA"] == nflverse.STADIUM_COORDS["LAR"]


def test_alias_team_maps_sleeper_codes_onto_nflverse_codes():
    assert nflverse.alias_team("LAR") == "LA"
    assert nflverse.alias_team("LA") == "LA"
    assert nflverse.alias_team("KC") == "KC"
