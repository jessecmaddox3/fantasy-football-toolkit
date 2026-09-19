import httpx
import pytest

from ff import cli
from ff.sources import fantasypros as fp


def test_refresh_without_config_covers_all_six_scopes(monkeypatch,capsys):
    calls=[]
    def refresh(season,week,**kwargs):
        calls.append((season,week,kwargs['scoring'],kwargs['position'],kwargs['ttl']))
        return {'count':1,'published_at':'2030-09-10T15:00:00+00:00',
                'retrieved_at':'2030-09-10T15:00:00+00:00','cache_saved':True}
    monkeypatch.setattr(fp,'weekly_rankings',refresh)
    monkeypatch.setattr(cli,'load_leagues',lambda *a:pytest.fail('refresh loaded private config'))
    assert cli.main(['refresh-fantasypros','--season','2030','--week','2']) == 0
    assert {(s,p) for _,_,s,p,_ in calls} == set(fp.PAGES)
    assert all(year==2030 and week==2 and ttl==0 for year,week,_,_,ttl in calls)
    assert 'source 2030' in capsys.readouterr().out


def test_refresh_continues_after_failure_without_echoing_http_details(monkeypatch,capsys):
    calls=[]
    def refresh(*a,**kwargs):
        calls.append(kwargs['scoring'])
        if len(calls)==1:raise httpx.ConnectError('secret=do-not-echo')
        return {'count':1,'published_at':'invented','retrieved_at':'invented','cache_saved':True}
    monkeypatch.setattr(fp,'weekly_rankings',refresh)
    assert cli.main(['refresh-fantasypros','--season','2030','--week','2']) == 1
    assert len(calls)==6
    result=capsys.readouterr()
    assert 'UNAVAILABLE' in result.err and 'do-not-echo' not in result.err


@pytest.mark.parametrize('args',[['--season','1999','--week','2'],['--season','2030','--week','19']])
def test_refresh_invalid_inputs_do_not_fetch(monkeypatch,args):
    monkeypatch.setattr(fp,'weekly_rankings',lambda *a,**k:pytest.fail('invalid live request'))
    with pytest.raises(SystemExit) as error:
        cli.main(['refresh-fantasypros',*args])
    assert error.value.code==2


def test_inferred_week_requires_matching_season(monkeypatch):
    monkeypatch.setattr(cli.sleeper,'state',lambda:{'season':'2029','week':2})
    monkeypatch.setattr(fp,'weekly_rankings',lambda *a,**k:pytest.fail('wrong season fetched'))
    with pytest.raises(SystemExit) as error:
        cli.main(['refresh-fantasypros','--season','2030'])
    assert error.value.code==2


def test_unsaved_refresh_is_reported_as_partial_failure(monkeypatch,capsys):
    monkeypatch.setattr(fp,'weekly_rankings',lambda *a,**k:{'count':1,'published_at':'invented',
        'retrieved_at':'invented','cache_saved':False,'cache_note':'local cache could not be saved'})
    assert cli.main(['refresh-fantasypros','--season','2030','--week','2'])==1
    assert 'cache could not be saved' in capsys.readouterr().err
