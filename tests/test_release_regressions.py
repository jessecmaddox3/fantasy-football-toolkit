import pandas as pd
import pytest
from ff import cli, roster
from ff.leagues import LeagueRef
from ff.engine import lineup
from tests.test_lineup import rules


@pytest.mark.parametrize('slot,positions', [('RB/WR', {'RB','WR'}),('WR/TE', {'WR','TE'})])
def test_espn_flex_draft_boards_include_eligible_players(slot, positions):
    league = rules({slot:1}, teams=1, scoring={'rec':1.})
    frame = pd.DataFrame([{'player_id':pos,'name':pos,'pos':pos,'team':'KC','rec':10.} for pos in ['QB','RB','WR','TE']])
    board = cli.build_board(frame, league, byes={})
    assert set(board.pos) == positions


def test_reserve_and_taxi_players_are_not_recommended(monkeypatch):
    from ff.sources import nflverse, weather, odds
    league = rules({'WR':1}, scoring={'rec':1.})
    ref = LeagueRef('home','Home','sleeper','100001',user_id='200001')
    team = roster.Team('home','Home','sleeper','1',player_ids=('active','reserve','taxi'), starters=('active',), reserve=('reserve',), taxi=('taxi',))
    monkeypatch.setattr(roster,'load_roster',lambda *a,**kw:team)
    monkeypatch.setattr(nflverse,'byes',lambda *a,**kw:{})
    monkeypatch.setattr(weather,'week_weather',lambda *a,**kw:{})
    monkeypatch.setattr(odds,'week_implied_totals',lambda *a,**kw:{})
    monkeypatch.setattr(lineup,'kickoffs_for_week',lambda *a,**kw:{})
    frame=pd.DataFrame([{'player_id':p,'name':p,'pos':'WR','team':'KC','rec':points} for p,points in [('active',5),('reserve',20),('taxi',30)]])
    result=lineup.league_lineup(ref,1,rules=league,projections=frame)
    assert result.optimal[0].player.player_id == 'active'
    assert result.swaps == ()


def test_espn_injured_reserve_is_preserved():
    ref=LeagueRef('e','ESPN','espn','100004',team_id=1)
    payload={'teams':[{'id':1,'roster':{'entries':[{'lineupSlotId':21,'playerPoolEntry':{'player':{'id':42,'fullName':'Inactive Player','defaultPositionId':3,'proTeamId':12}}}]}}]}
    team=roster.espn_team(ref,payload,rules({'WR':1}))
    assert team.reserve == ('espn:42',)


def test_unavailable_season_projections_return_a_helpful_message(tmp_path,monkeypatch,capsys):
    from tests.test_config import VALID
    from ff.sources import nflverse,sleeper
    path=tmp_path/'leagues.toml';path.write_text(VALID)
    monkeypatch.setattr(nflverse,'games',lambda *a,**kw:pd.DataFrame())
    monkeypatch.setattr(nflverse,'byes',lambda *a,**kw:{})
    monkeypatch.setattr(cli,'_load_rules',lambda *a,**kw:rules({'WR':1}))
    monkeypatch.setattr(sleeper,'season_projections',lambda *a,**kw:sleeper._projection_frame([]))
    assert cli.main(['board','--league','home','--config',str(path)]) == 1
    assert 'no season projections' in capsys.readouterr().out.lower()
