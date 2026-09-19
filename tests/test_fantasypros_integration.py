"""Invented league flow through the real consensus parser and both renderers."""
import datetime as dt
import json

import httpx
import pandas as pd
import pytest

from ff import cli, roster
from ff.engine import brief, lineup
from ff.leagues import LeagueRef
from ff.sources import fantasypros as fp, nflverse, odds, sleeper, weather
from tests.test_fantasypros import NOW, payload
from tests.test_lineup import rules


@pytest.mark.parametrize('status', [200,503])
@pytest.mark.parametrize('locked', [False,True])
def test_consensus_never_changes_scoring_locks_or_reserve_exclusion(monkeypatch,status,locked):
    league=rules({'WR':1},scoring={'rec':1.5})
    ref=LeagueRef('demo','Invented League','sleeper','100001',user_id='200001')
    team=roster.Team('demo','Invented League','sleeper','1',player_ids=('a','b','ir'),starters=('a',),reserve=('ir',))
    frame=pd.DataFrame([
        {'player_id':'a','name':'Avery Receiver','pos':'WR','team':'LAR','rec':2},
        {'player_id':'b','name':'Morgan Receiver','pos':'WR','team':'SEA','rec':4},
        {'player_id':'ir','name':'Reserved Receiver','pos':'WR','team':'ATL','rec':100}])
    monkeypatch.setattr(roster,'load_rules',lambda *a,**kw:league)
    monkeypatch.setattr(roster,'load_roster',lambda *a,**kw:team)
    monkeypatch.setattr(nflverse,'byes',lambda *a,**kw:{})
    monkeypatch.setattr(weather,'week_weather',lambda *a,**kw:{})
    monkeypatch.setattr(odds,'week_implied_totals',lambda *a,**kw:{})
    monkeypatch.setattr(lineup,'kickoffs_for_week',lambda *a,**kw:{'LAR':NOW-dt.timedelta(hours=1)} if locked else {})
    monkeypatch.setattr(sleeper,'rosters',lambda *a,**kw:[])
    monkeypatch.setattr(sleeper,'users',lambda *a,**kw:[])
    monkeypatch.setattr(sleeper,'matchups',lambda *a,**kw:[])
    actual_rankings=fp.weekly_rankings
    monkeypatch.setattr(fp,'weekly_rankings',lambda *a,**kw:actual_rankings(*a,now=NOW,**kw))
    data=payload()
    # Consensus prefers the lower projected starter; it must not drive the solver.
    data['players'][0]['pos_rank']='WR1'
    data['players'].append(dict(player_id='invented-b',player_name='Morgan Receiver',
        player_team_id='SEA',pos_rank='WR10',rank_ecr=15))
    data['count']=2
    def serve(request):
        assert request.method=='GET' and request.url.host=='www.fantasypros.com'
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        return httpx.Response(status,text='<script>var ecrData = '+json.dumps(data)+';</script>')
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        result=lineup.league_lineup(ref,2,season=2030,rules=league,projections=frame,now=NOW,client=client)
        section=brief.build_league_brief(ref,2,season=2030,now=NOW,client=client,
            projections=frame,season_projections=frame)
    assert [p.player_id for p in result.lines]==['a','b']
    assert result.current_points==3
    assert result.optimal[0].player.player_id==('a' if locked else 'b')
    assert result.optimal_points==(3 if locked else 6)
    assert section.lineup.optimal==result.optimal
    for text in (cli.render_lineup(result,league),brief.render_league(section)):
        assert ('FantasyPros UNAVAILABLE' if status==503 else 'current starter Avery Receiver WR1') in text
        assert 'not an equivalent scoring format' in text
