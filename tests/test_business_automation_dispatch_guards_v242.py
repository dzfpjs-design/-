"""Pure adversarial dispatch-window/scope gates, explicitly not PG journeys.

Authority/source/storage/SQL IO are substituted here to isolate each fence.
Actual ordinary Task/ACK/lease/PG tests live in test_business_automation_pg.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from generic_agents import business_automation_service as service, business_automation_consumer as consumer


def test_daily_queued_input_expires_at_dispatch_without_a_new_occurrence_or_task(monkeypatch):
    now=datetime(2026,10,28,1,tzinfo=timezone.utc)
    source={'received_at':(now-timedelta(hours=24,seconds=1)).isoformat()}
    state={'status':'queued','task_id':None,'source_unit':source,'source_label':'原完整资料'}
    origin={'trigger_id':'daily_time','day':'2026-10-28','scheduled_for':'2026-10-28T00:00:00+00:00'}
    row=NS(id='original-subscription');job=NS(id='original-occurrence',data={});written=[]
    monkeypatch.setattr(service,'checked_occurrence',lambda *args:(row,origin,copy.deepcopy(state)))
    monkeypatch.setattr(service,'assert_dispatch_live',lambda *args:(None,None,{}))
    monkeypatch.setattr(service,'clock',lambda _:now)
    monkeypatch.setattr(service,'_state',lambda db,p,actual,value:written.append((actual.id,value)))
    def forbidden(*args,**kwargs):raise AssertionError('stale input reached Task/input creation')
    monkeypatch.setattr(service,'merge_key',forbidden)
    assert service.process(None,None,job)=='waiting_source'
    assert len(written)==1 and written[0][0]=='original-occurrence'
    assert written[0][1]['status']=='waiting_source' and written[0][1]['source_unit'] is None
    assert written[0][1]['source_label'] is None and '24小时' in written[0][1]['reason']
    assert state['source_unit']==source  # Original frozen fixture was not edited.


@pytest.fixture
def scope_case(monkeypatch):
    unit={'run_id':'same-real-run'};body={'request_id':'same-original-request'}
    task=NS(id='actual-task',data={'business_automation_v1':{'merge_key':'merge-key'}})
    witness={'subscription':NS(id='creator-subscription'),'unit':unit,'body':body,'body_hash':'frozen-hash'}
    row=NS(id='second-subscription')
    state={'status':'linked_existing_task','task_id':task.id,'source_unit':copy.deepcopy(unit),
        'task_body':copy.deepcopy(body),'task_body_hash':'frozen-hash'}
    link=NS(id='original-link');db=NS(scalars=lambda statement:[link]);p=NS(workspace_id='workspace')
    checked=[]
    def proof(actual_db,actual_p,actual_link):
        assert actual_db is db and actual_p is p and actual_link is link
        checked.append(link.id);return row,{},state
    monkeypatch.setattr(service,'checked_occurrence',proof)
    monkeypatch.setattr(service,'merge_key',lambda *args:'merge-key')
    return NS(**locals())


def test_second_subscription_can_use_only_its_independently_proved_same_task_link(scope_case):
    s=scope_case
    assert consumer._task_scope(s.db,s.p,s.task,s.witness,'second-subscription') is None
    assert s.checked==['original-link']


@pytest.mark.parametrize('fault',['missing','duplicate','foreign-subscription','foreign-task','foreign-unit','foreign-body','foreign-hash','wrong-state','merge','proof'])
def test_scoped_consumer_rejects_unproved_or_different_associations(scope_case,monkeypatch,fault):
    s=scope_case
    if fault=='missing':s.db.scalars=lambda statement:[]
    elif fault=='duplicate':s.db.scalars=lambda statement:[s.link,s.link]
    elif fault=='foreign-subscription':s.row.id='different-subscription'
    elif fault=='foreign-task':s.state['task_id']='different-task'
    elif fault=='foreign-unit':s.state['source_unit']={'run_id':'another-run'}
    elif fault=='foreign-body':s.state['task_body']={'request_id':'another-request'}
    elif fault=='foreign-hash':s.state['task_body_hash']='another-hash'
    elif fault=='wrong-state':s.state['status']='queued'
    elif fault=='merge':monkeypatch.setattr(service,'merge_key',lambda *args:'another-merge')
    else:
        def missing_proof(*args):raise HTTPException(409,'原发生缺独立证明')
        monkeypatch.setattr(service,'checked_occurrence',missing_proof)
    with pytest.raises(HTTPException):consumer._task_scope(s.db,s.p,s.task,s.witness,'second-subscription')
