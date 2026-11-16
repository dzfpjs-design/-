import pytest
from generic_agents.runtime_readiness import wait_for_api,api_is_ready


def timing():
 value=[0.0]
 def pause(seconds):value[0]+=seconds
 return lambda:value[0],pause


def test_transient_connect_or_database_failure_waits_for_actual_ready_without_restart():
 clock,pause=timing();responses=iter([False,False,True]);calls=[]
 def probe():calls.append(1);return next(responses)
 assert wait_for_api(lambda:True,timeout=2,probe=probe,clock=clock,pause=pause)
 assert len(calls)==3 and clock()==pytest.approx(.4)


def test_dead_or_never_ready_process_is_not_reported_as_started():
 clock,pause=timing()
 with pytest.raises(RuntimeError,match='进程已退出'):wait_for_api(lambda:False,probe=lambda:True,clock=clock,pause=pause)
 with pytest.raises(RuntimeError,match='尚未就绪'):wait_for_api(lambda:True,timeout=.5,probe=lambda:False,clock=clock,pause=pause)
 assert clock()==pytest.approx(.5)


def test_health_requires_actual_api_and_database_ready(monkeypatch):
 class R:
  status_code=200
  def json(self):return {'status':'ok','database':'not-ready'}
 monkeypatch.setattr('generic_agents.runtime_readiness.httpx.get',lambda *args,**kw:R())
 assert not api_is_ready()
 monkeypatch.setattr(R,'json',lambda self:{'status':'ok','database':'postgresql'});assert api_is_ready()
 R.status_code=503;assert not api_is_ready()
