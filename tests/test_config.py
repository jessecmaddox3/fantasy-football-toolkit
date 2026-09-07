from pathlib import Path
import httpx
import pytest
from ff import cli

VALID = '''[leagues.home]
name = "Home league"
platform = "sleeper"
league_id = "100001"
user_id = "200001"
season = 2026
'''

def test_config_loads_an_arbitrary_league(tmp_path):
    from ff.config import load_leagues
    path = tmp_path / "leagues.toml"
    path.write_text(VALID)
    refs = load_leagues(path)
    assert list(refs) == ["home"]
    assert refs["home"].user_id == "200001"
    assert refs["home"].season == 2026

@pytest.mark.parametrize("change", [
    lambda s: s.replace('platform = "sleeper"', 'platform = "unknown"'),
    lambda s: s.replace('user_id = "200001"', ''),
    lambda s: s.replace('league_id = "100001"', 'league_id = "../../bad"'),
    lambda s: s.replace('season = 2026', 'season = "later"'),
    lambda s: s + 'surprise = true\n',
])
def test_invalid_config_is_rejected(tmp_path, change):
    from ff.config import load_leagues
    path = tmp_path / "leagues.toml"
    path.write_text(change(VALID))
    with pytest.raises(ValueError):
        load_leagues(path)

def test_missing_config_fails_before_any_http(tmp_path, monkeypatch, capsys):
    def no_http(*a, **k):
        pytest.fail("missing config must not make HTTP requests")
    monkeypatch.setattr(httpx.Client, "send", no_http)
    with pytest.raises(SystemExit) as error:
        cli.main(["lineup", "--all", "--config", str(tmp_path / "missing.toml")])
    assert error.value.code == 2
    assert "config" in capsys.readouterr().err.lower()

def test_offline_demo_does_not_read_config_or_use_network(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FF_CONFIG", str(tmp_path / "absent.toml"))
    def no_http(*a, **k):
        pytest.fail("offline demo attempted HTTP")
    monkeypatch.setattr(httpx.Client, "send", no_http)
    assert cli.main(["demo"]) == 0
    text = capsys.readouterr().out
    assert "synthetic" in text.lower()
    assert "vor" in text and "recommended moves" in text
    assert not list(tmp_path.iterdir())

def test_cache_and_output_are_not_written_to_installation(tmp_path, monkeypatch):
    from ff import cache, notify
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FF_CACHE_DIR", raising=False)
    monkeypatch.delenv("FF_OUTPUT_DIR", raising=False)
    assert cache.cache_dir() == tmp_path / "data/cache"
    result = Path(notify.FileNotifier().send(notify.Message("Example", "body")))
    assert result == tmp_path / "data/out/example.md"

def test_cookie_environment_supported_without_a_file(tmp_path, monkeypatch):
    from ff.sources import espn
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ESPN_S2", "synthetic-cookie")
    monkeypatch.setenv("SWID", "synthetic-swid")
    assert espn.load_cookies() == {"espn_s2": "synthetic-cookie", "SWID": "synthetic-swid"}


def test_custom_league_board_uses_configured_id_and_season(tmp_path, monkeypatch, capsys):
    from tests.conftest import fixture_json, fixture_text, RecordingRoutes
    routes = RecordingRoutes({
        "/league/100001": fixture_json("sleeper_league_redraft.json"),
        "/projections/nfl/2027": fixture_json("sleeper_season_projections.json"),
        "games.csv": fixture_text("nflverse_games.csv").replace("2026", "2027"),
    })
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, req: routes.handler(req))
    path = tmp_path / "leagues.toml"
    path.write_text(VALID.replace("2026", "2027"))
    assert cli.main(["board", "--league", "home", "--config", str(path), "--top", "3"]) == 0
    assert "draft board" in capsys.readouterr().out
    assert any("/2027" in str(req.url) for req in routes.requests)
    assert all(req.method == "GET" for req in routes.requests)


def test_custom_league_weekly_brief_writes_locally(tmp_path, monkeypatch, capsys):
    from tests.conftest import fixture_json, fixture_text, RecordingRoutes
    routes = RecordingRoutes({
        "/league/100001/rosters": fixture_json("sleeper_rosters.json"),
        "/league/100001/users": fixture_json("sleeper_users.json"),
        "/league/100001": fixture_json("sleeper_league_redraft.json"),
        "/projections/nfl/2026/1": fixture_json("sleeper_week1_projections.json"),
        "/projections/nfl/2026": fixture_json("sleeper_season_projections.json"),
        "games.csv": fixture_text("nflverse_games.csv"),
        "/scoreboard": fixture_json("espn_scoreboard_20260913.json"),
        "api.open-meteo.com": fixture_json("openmeteo_forecast.json"),
    })
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, req: routes.handler(req))
    path = tmp_path / "leagues.toml"
    path.write_text(VALID)
    assert cli.main(["brief", "--config", str(path), "--week", "1", "--ignore-locks"]) == 0
    assert (tmp_path / "data/out/brief_week1.md").is_file()
    assert "Home league" in capsys.readouterr().out
    assert all(req.method == "GET" for req in routes.requests)
