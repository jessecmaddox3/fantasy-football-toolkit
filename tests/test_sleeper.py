import pytest

from ff.sources import sleeper
from tests.conftest import DATA, FIXTURES, RecordingRoutes, fixture_json

SEASON_PROJ = fixture_json("sleeper_season_projections.json")
WEEK_PROJ = fixture_json("sleeper_week1_projections.json")


def routes(**extra):
    base = {
        "/projections/nfl/2026/1": WEEK_PROJ,
        "/projections/nfl/2026": SEASON_PROJ,
    }
    base.update(extra)
    return RecordingRoutes(base)


def test_season_projections_flattens_player_and_stats_into_columns():
    r = routes()
    df = sleeper.season_projections(2026, client=r.client())

    assert set(["player_id", "name", "pos", "team", "years_exp"]) <= set(df.columns)
    gibbs = df.set_index("name").loc["Jahmyr Gibbs"]
    assert gibbs["pos"] == "RB"
    assert gibbs["team"] == "DET"
    assert gibbs["years_exp"] == 3
    assert gibbs["rush_yd"] == pytest.approx(1200.0)
    assert gibbs["rec"] == pytest.approx(60.0)


def test_season_projections_carries_every_adp_field():
    df = sleeper.season_projections(2026, client=routes().client())
    for col in ("adp_ppr", "adp_half_ppr", "adp_std", "adp_2qb", "adp_dynasty_2qb"):
        assert col in df.columns
    assert df["adp_ppr"].min() < 5


def test_season_projections_missing_stats_are_nan_not_zero():
    """A WR has no pass_td projection; that must not read as a real zero."""
    df = sleeper.season_projections(2026, client=routes().client()).set_index("name")
    assert "pass_td" in df.columns
    assert df.loc["Ja'Marr Chase", "pass_td"] != df.loc["Ja'Marr Chase", "pass_td"]


def test_weekly_projections_hits_week_url_and_carries_opponent():
    r = routes()
    df = sleeper.weekly_projections(2026, 1, client=r.client())

    assert "/projections/nfl/2026/1" in str(r.requests[0].url)
    assert "week" in df.columns and df["week"].eq(1).all()
    assert "opponent" in df.columns


def test_league_rosters_users_transactions_use_documented_endpoints():
    r = routes(**{
        "/league/L1/rosters": fixture_json("sleeper_rosters.json"),
        "/league/L1/users": fixture_json("sleeper_users.json"),
        "/league/L1/transactions/3": [],
        "/league/L1": {"league_id": "L1"},
    })
    client = r.client()

    assert sleeper.league("L1", client=client)["league_id"] == "L1"
    assert len(sleeper.rosters("L1", client=client)) == 2
    assert len(sleeper.users("L1", client=client)) == 2
    assert sleeper.transactions("L1", 3, client=client) == []
    urls = [str(x.url) for x in r.requests]
    assert urls[0].startswith("https://api.sleeper.app/v1/league/L1")
    assert any(u.endswith("/league/L1/transactions/3") for u in urls)


def test_draft_picks_adds_cache_buster_and_never_caches(tmp_cache_dir):
    r = routes(**{"/draft/D1/picks": fixture_json("sleeper_draft_picks.json")})
    client = r.client()

    sleeper.draft_picks("D1", client=client)
    sleeper.draft_picks("D1", client=client)

    assert len(r.requests) == 2, "draft picks must never be served from cache"
    assert "t=" in str(r.requests[0].url)
    assert str(r.requests[0].url) != str(r.requests[1].url), "cache-buster must change"
    assert not list(tmp_cache_dir.glob("*draft*"))


def test_draft_returns_metadata():
    r = routes(**{"/draft/D1": fixture_json("sleeper_draft.json")})
    d = sleeper.draft("D1", client=r.client())
    assert d["draft_id"] == "300001"
    assert d["settings"]["rounds"] == 20


def test_players_is_cached_for_a_day(tmp_cache_dir):
    r = routes(**{"/players/nfl": {"9221": {"first_name": "Jahmyr"}}})
    client = r.client()

    sleeper.players(client=client)
    sleeper.players(client=client)

    assert len(r.requests) == 1
    assert (tmp_cache_dir / "sleeper_players_nfl.json").exists()


def test_synthetic_keeper_payload_parses():
    """Guards the synthetic fixture the rest of the project reads."""
    import json

    raw = json.loads((FIXTURES / "sleeper_league_keeper.json").read_text())
    assert raw["settings"]["waiver_type"] == 2
    assert "SUPER_FLEX" in raw["roster_positions"]
