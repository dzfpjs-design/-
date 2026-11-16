"""Goal page projects a held saved Job without confusing it with completion."""
from generic_agents import native_goals,workflow_worker_status
from tests.test_native_goal_approval_completion import scene


def test_accepted_goal_view_reports_historical_queued_check_as_closed(monkeypatch):
    c=scene(monkeypatch,status='accepted')
    saved={'state':'queued','job_status':'queued','historical':False,'attempts':0}
    monkeypatch.setattr(workflow_worker_status,'check',lambda *_:saved)
    result=native_goals.view(c.db,c.p,c.task)
    assert result['approval_completion']['accepted'] is True
    assert result['check_worker']['state']=='approval_closed' and result['check_worker']['job_status']=='queued'
    assert result['check_worker']['historical'] is True and result['check']['evaluation']['completed'] is False
    assert saved=={'state':'queued','job_status':'queued','historical':False,'attempts':0}
