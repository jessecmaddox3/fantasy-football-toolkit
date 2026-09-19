"""Invented provider-shaped records. No captured rankings or real league data."""
import copy
import datetime as dt
import json

import httpx
import pytest

from ff.sources import fantasypros as fp

NOW = dt.datetime(2030, 9, 10, 15, tzinfo=dt.timezone.utc)


def payload(scoring="PPR", position="FLX", *, published=NOW):
    pos = "WR" if position == "FLX" else position
    return {"sport": "NFL", "ranking_type_name": "weekly", "year": "2030", "week": "2",
            "scoring": scoring, "position_id": position,
            "last_updated_ts": published.timestamp(), "count": 1, "total_experts": 7,
            "players": [{"player_id": "invented-a", "player_name": "Avery Receiver Jr.",
                         "player_team_id": "LA", "pos_rank": pos + "3", "rank_ecr": 4.5}]}


def transport(data, status=200):
    requests = []
    def serve(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "www.fantasypros.com"
        assert request.url.params["week"] == "2"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(status, text='<script>var ecrData = ' + json.dumps(data) + ';</script>')
    return httpx.Client(transport=httpx.MockTransport(serve)), requests


def test_cache_retains_source_and_retrieval_clocks_then_refreshes():
    with transport(payload())[0] as client:
        first = fp.weekly_rankings(2030, 2, client=client, now=NOW)
    offline, requests = transport({}, 503)
    with offline:
        hit = fp.weekly_rankings(2030, 2, client=offline, now=NOW + dt.timedelta(hours=5))
        assert first["retrieved_at"] == hit["retrieved_at"] == NOW.isoformat()
        assert first["published_at"] == hit["published_at"]
        assert not requests
        with pytest.raises(httpx.HTTPStatusError):
            fp.weekly_rankings(2030, 2, client=offline, now=NOW + dt.timedelta(hours=6))
    assert len(requests) == 1


@pytest.mark.parametrize("rank", ["NaN", "Infinity", "-Infinity", 0, -1, True, None, [], {}])
def test_nonfinite_or_invalid_ranks_are_rejected(rank):
    data = payload(); data["players"][0]["rank_ecr"] = rank
    with transport(data)[0] as client, pytest.raises(fp.RankingsError):
        fp.weekly_rankings(2030, 2, client=client, now=NOW)


@pytest.mark.parametrize("field,value", [
    ("sport", "NBA"), ("year", "2029"), ("week", "1"), ("scoring", "STD"),
    ("position_id", "QB"), ("ranking_type_name", "draft"), ("count", 2),
    ("count", True), ("count", 1.5), ("players", []), ("players", {}),
    ("players", ["not a player"]), ("last_updated_ts", "NaN"),
    ("last_updated_ts", "Infinity"), ("last_updated_ts", 1e100),
])
def test_wrong_or_malformed_metadata_never_becomes_rankings(field, value):
    data = payload(); data[field] = value
    with transport(data)[0] as client, pytest.raises(fp.RankingsError):
        fp.weekly_rankings(2030, 2, client=client, now=NOW)


@pytest.mark.parametrize("field,value", [("player_name", []), ("player_name", ""),
    ("pos_rank", "QB3"), ("pos_rank", "WR0"), ("pos_rank", 3), ("player_team_id", {})])
def test_invalid_player_identity_or_position_is_rejected(field, value):
    data = payload(); data["players"][0][field] = value
    with transport(data)[0] as client, pytest.raises(fp.RankingsError):
        fp.weekly_rankings(2030, 2, client=client, now=NOW)


@pytest.mark.parametrize("age", [dt.timedelta(hours=48,seconds=1), -dt.timedelta(minutes=6)])
def test_stale_or_future_source_is_rejected_even_when_just_retrieved(age):
    with transport(payload(published=NOW-age))[0] as client, pytest.raises(fp.RankingsError):
        fp.weekly_rankings(2030, 2, client=client, now=NOW)


def test_exact_48_hour_source_boundary_is_still_usable():
    with transport(payload(published=NOW-dt.timedelta(hours=48)))[0] as client:
        assert fp.weekly_rankings(2030, 2, client=client, now=NOW)["count"] == 1


@pytest.mark.parametrize("season,week,kwargs", [(1999,2,{}),(2030,19,{}),(True,2,{}),
    (2030,2,{"scoring":"INVALID"}), (2030,2,{"ttl":float("nan")})])
def test_invalid_requests_fail_before_network(season,week,kwargs):
    client, requests = transport(payload())
    with client, pytest.raises(fp.RankingsError):
        fp.weekly_rankings(season,week,client=client,now=NOW,**kwargs)
    assert not requests


def test_forced_refresh_updates_cache_and_write_failure_is_visible(monkeypatch):
    client, requests = transport(payload())
    with client:
        fp.weekly_rankings(2030,2,client=client,now=NOW)
        result = fp.weekly_rankings(2030,2,client=client,now=NOW,ttl=0)
        assert len(requests) == 2 and result["cache_saved"] is True
        def disk_full(*args):
            raise OSError("synthetic failure")
        monkeypatch.setattr(fp,"_write",disk_full)
        result = fp.weekly_rankings(2030,2,client=client,now=NOW,ttl=0)
        assert result["cache_saved"] is False
        assert "cache" in result["cache_note"]


def test_matching_alias_suffix_ambiguity_and_current_status(monkeypatch):
    from ff.engine.lineup import PlayerLine
    data = payload()
    snapshot = fp._snapshot(data,NOW,season=2030,week=2,scoring="PPR",position="FLX",now=NOW,url="https://example.invalid")
    monkeypatch.setattr(fp,"weekly_rankings",lambda *a,**k:snapshot)
    line=PlayerLine("a","Avery Receiver","WR",team="LAR",started=True)
    notes="\n".join(fp.lineup_notes([line],2030,2,reception_points=1))
    assert "current starter Avery Receiver WR3" in notes
    snapshot["players"].append(copy.deepcopy(snapshot["players"][0]))
    notes="\n".join(fp.lineup_notes([line],2030,2,reception_points=1))
    assert "unmatched players: Avery Receiver" in notes
    assert "positional ECR:" not in notes


def test_http_failure_notes_do_not_echo_sensitive_exception_urls(monkeypatch):
    from ff.engine.lineup import PlayerLine
    def unavailable(*a,**k):
        raise httpx.ConnectError("https://example.invalid/?secret=do-not-echo")
    monkeypatch.setattr(fp,"weekly_rankings",unavailable)
    notes="\n".join(fp.lineup_notes([PlayerLine("a","Avery","QB")],2030,2,reception_points=0))
    assert "UNAVAILABLE" in notes and "do-not-echo" not in notes


@pytest.mark.parametrize('field', ['sport', 'rank_ecr'])
def test_provider_error_values_are_never_echoed(field):
    data = payload()
    secret = 'https://example.invalid/?token=synthetic-do-not-echo'
    if field == 'sport':
        data[field] = secret
    else:
        data['players'][0][field] = secret
    with transport(data)[0] as client, pytest.raises(fp.RankingsError) as caught:
        fp.weekly_rankings(2030, 2, client=client, now=NOW)
    assert 'synthetic-do-not-echo' not in fp.failure_reason(caught.value)


def test_defense_matches_unique_canonical_team_without_assuming_display_name(monkeypatch):
    from ff.engine.lineup import PlayerLine
    data = payload('STD', 'DST')
    data['players'][0].update(player_name='Fictional Defense Display Name', player_team_id='LA')
    snapshot = fp._snapshot(data, NOW, season=2030, week=2, scoring='STD', position='DST', now=NOW, url='https://example.invalid')
    monkeypatch.setattr(fp, 'weekly_rankings', lambda *a, **kw: snapshot)
    line = PlayerLine('LAR', 'LAR', 'DEF', team='LAR', started=True)
    notes = '\n'.join(fp.lineup_notes([line], 2030, 2, reception_points=1))
    assert 'current starter LAR DST3' in notes
    snapshot['players'].append(copy.deepcopy(snapshot['players'][0]))
    notes = '\n'.join(fp.lineup_notes([line], 2030, 2, reception_points=1))
    assert 'unmatched players: LAR' in notes
    assert 'positional ECR:' not in notes


@pytest.mark.parametrize('cached', [[], None, {'retrieved_at': 'invalid'},
    {'retrieved_at': '2030-09-10T15:00:00'},
    {'retrieved_at': '2030-09-10T16:00:00+00:00'},
    {'retrieved_at': NOW.isoformat(), 'data': {}}])
def test_corrupt_naive_future_or_wrong_scope_cache_requires_refresh(cached):
    path = fp.cache_path('fantasypros_weekly_2030_2_PPR_FLX')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cached))
    client, requests = transport(payload())
    with client:
        result = fp.weekly_rankings(2030, 2, client=client, now=NOW)
    assert result['retrieved_at'] == NOW.isoformat() and len(requests) == 1


@pytest.mark.parametrize('body', ['<html>No rankings</html>', 'var ecrData = {broken',
    'var ecrData = [];', 'var ecrData = null;'])
def test_missing_or_malformed_script_does_not_save_cache(body):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,text=body)))
    with client, pytest.raises(fp.RankingsError):
        fp.weekly_rankings(2030, 2, client=client, now=NOW)
    assert not fp.cache_path('fantasypros_weekly_2030_2_PPR_FLX').exists()


@pytest.mark.parametrize('reception,expected', [(0,'STD'),(.5,'HALF'),(1,'PPR'),(.75,'HALF'),(1.5,'PPR')])
def test_all_position_scopes_and_custom_scoring_are_explained(monkeypatch, reception, expected):
    from ff.engine.lineup import PlayerLine
    calls=[]
    def rankings(season, week, **kwargs):
        scoring, position = kwargs['scoring'], kwargs['position']
        calls.append((scoring, position))
        return fp._snapshot(payload(scoring,position), NOW, season=2030, week=2,
            scoring=scoring, position=position, now=NOW, url='https://example.invalid')
    monkeypatch.setattr(fp,'weekly_rankings',rankings)
    lines = [PlayerLine(p,p,p) for p in ('RB','WR','TE','QB','DEF','K')]
    notes='\n'.join(fp.lineup_notes(lines,2030,2,reception_points=reception))
    assert calls == [(expected,'FLX'),('STD','QB'),('STD','DST'),('STD','K')]
    assert ('not an equivalent scoring format' in notes) == (reception not in (0,.5,1))
