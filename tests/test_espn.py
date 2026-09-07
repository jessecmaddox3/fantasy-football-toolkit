import json

import pytest

from ff.sources import espn
from tests.conftest import DATA, FIXTURES, RecordingRoutes, fixture_json

KONA = fixture_json("espn_kona_week1.json")


def test_load_cookies_reads_espn_s2_and_swid_from_an_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nexport ESPN_S2=abc%3Ddef\nSWID={AAAA-BBBB}\nOTHER=x\n")

    assert espn.load_cookies(env) == {"espn_s2": "abc%3Ddef", "SWID": "{AAAA-BBBB}"}


def test_load_cookies_raises_a_useful_error_when_the_file_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="ESPN cookies"):
        espn.load_cookies(tmp_path / "nope.env")


def test_league_requests_the_read_host_with_repeated_view_params(monkeypatch):
    monkeypatch.setattr(espn, "load_cookies", lambda *a, **k: {"espn_s2": "s", "SWID": "w"})
    r = RecordingRoutes({"/leagues/100004": {"id": 100004}})

    got = espn.league(100004, client=r.client())

    url = str(r.requests[0].url)
    assert url.startswith("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/100004")
    assert url.count("view=") == len(espn.DEFAULT_VIEWS)
    assert got["id"] == 100004


def test_league_sends_the_cookies_and_only_ever_issues_gets(monkeypatch):
    monkeypatch.setattr(espn, "load_cookies", lambda *a, **k: {"espn_s2": "s2v", "SWID": "{sw}"})
    r = RecordingRoutes({"/leagues/100004": {"id": 100004}})

    espn.league(100004, client=r.client())

    req = r.requests[0]
    assert req.method == "GET"
    assert "espn_s2=s2v" in req.headers["cookie"]
    assert "SWID={sw}" in req.headers["cookie"]


def test_players_projections_sends_the_x_fantasy_filter_for_the_week(monkeypatch):
    monkeypatch.setattr(espn, "load_cookies", lambda *a, **k: {"espn_s2": "s", "SWID": "w"})
    r = RecordingRoutes({"kona_player_info": KONA})

    espn.players_projections(1, client=r.client(), limit=4)

    req = r.requests[0]
    assert "scoringPeriodId=1" in str(req.url)
    flt = json.loads(req.headers["x-fantasy-filter"])["players"]
    assert flt["filterStatsForTopScoringPeriodIds"]["value"] == 1
    assert flt["limit"] == 4


def test_players_projections_decodes_positions_and_pro_teams():
    r = RecordingRoutes({"kona_player_info": KONA})
    df = espn.players_projections(1, client=r.client(), cookies={"espn_s2": "s", "SWID": "w"})

    gibbs = df.set_index("name").loc["Jahmyr Gibbs"]
    assert gibbs["pos"] == "RB"
    assert gibbs["team"] == "DET"
    assert gibbs["injury_status"] == "ACTIVE"


def test_players_projections_separates_season_projection_from_week_projection():
    """ESPN publishes 2026 season projections but no weekly ones before week 1."""
    r = RecordingRoutes({"kona_player_info": KONA})
    df = espn.players_projections(1, client=r.client(), cookies={"espn_s2": "s", "SWID": "w"})

    gibbs = df.set_index("name").loc["Jahmyr Gibbs"]
    assert gibbs["proj_season"] == pytest.approx(300.0, abs=0.01)
    assert gibbs["proj_week"] != gibbs["proj_week"]  # NaN: not published yet


def test_synthetic_espn_payload_has_the_settings_leagues_py_depends_on():
    raw = json.loads((FIXTURES / "espn_league.json").read_text())
    s = raw["settings"]
    assert s["size"] == 12
    assert s["rosterSettings"]["lineupSlotCounts"]["0"] == 1
    assert s["acquisitionSettings"]["acquisitionType"] == "WAIVERS_TRADITIONAL"
