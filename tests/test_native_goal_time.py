"""Original-goal time passes in an isolated database; no app clock or data injection."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import copy
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Entity,Job,DataRun,DataGap,Invocation,CloudCall,Acquisition,Membership
from generic_agents.worker import process_once
from tests.test_native_goals import setup,define,drain
from tests.test_instance_workspace import complete

def clock(monkeypatch,value):
    from types import SimpleNamespace
    from sqlalchemy import literal
    from generic_agents import native_goal_time,native_goals,data_requirements,worker
    for module in (native_goal_time,native_goals,data_requirements):monkeypatch.setattr(module,'now',lambda:value)
    monkeypatch.setattr(worker,'func',SimpleNamespace(clock_timestamp=lambda:literal(value)))

def scheduled(task):
    with Session(engine()) as db:return copy.deepcopy(db.get(Task,task['id']).data['native_goal_timer'])

def test_automatic_archive_expiry_reopens_original_need_instead_of_publishing_old_sufficient_result(client,monkeypatch):
    _,obj,task,base,body=setup(client);at=datetime.now(timezone.utc)
    with Session(engine()) as db:db.get(Entity,obj['id']).valid_until=at+timedelta(seconds=10);db.commit()
    define(client,base,body);drain(task);before=client.get(base).json();timer=scheduled(task)
    assert before['check']['evidence']['status']=='sufficient' and before['next_time_check']['status']=='queued'
    clock(monkeypatch,at+timedelta(seconds=11));assert process_once(timer['job_id'],deliver=False)
    after=client.get(base).json();assert after['check']['evidence']['status']=='insufficient'
    assert len(after['check']['gaps'])==1 and after['check']['gaps'][0]['business_task_id']==task['id']
    assert not after['check']['evaluation']['ready_for_acceptance'] and after['next_time_check'] is None
    gap_id=after['check']['gaps'][0]['id']
    with Session(engine()) as db:
        gap=db.get(DataGap,gap_id);gap.data={**gap.data,'resolution_reason':'历史资料曾充分'};db.commit()
    reread=client.get(base).json();assert reread['check']==after['check'],'reading current gap reason must preserve historical receipt'
    assert reread['current_gaps'][0]['reason']!='历史资料曾充分' and reread['current_gaps'][0]['status']=='needs_existing_evidence'
    with Session(engine()) as db:
        query=db.get(DataRun,db.get(Task,task['id']).data['native_goal_query_id']);assert query.data['result']['status']=='insufficient'
        assert len(query.data['native_goal_result_history'])==2 and len(list(db.scalars(select(Task))))==1
        assert not list(db.scalars(select(Invocation))) and not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Acquisition)))

def test_deadline_has_durable_receipt_preserves_human_note_and_can_be_stopped(client,monkeypatch):
    _,_,task,base,body=setup(client);at=datetime.now(timezone.utc);body['goal_spec']['deadline']=(at+timedelta(seconds=20)).isoformat()
    define(client,base,body);drain(task);timer=scheduled(task);before=client.get('/api/workspace/tasks/'+task['id']).json()
    clock(monkeypatch,at+timedelta(seconds=21));assert process_once(timer['job_id'],deliver=False)
    goal=client.get(base).json();assert goal['timed_out'] and goal['next_time_check'] is None and goal['scope_error']
    assert goal['version']==before['version'],'automatic timing must not invalidate an unsaved human note'
    response=client.put('/api/workspace/tasks/'+task['id']+'/note',json={'version':before['version'],'step_id':before['workflow_runtime']['current_step_id'],'note':'保留截止前核查说明，等待另行业务处理'})
    assert response.status_code==200,response.text
    current=client.get('/api/workspace/tasks/'+task['id']).json();assert complete(client,current).status_code==409
    goal=client.get(base).json();response=client.post(base+'/stop',json={'request_id':str(uuid4()),'expected_version':goal['version'],'reason':'原目标截止，明确停止，保留核查说明'})
    assert response.status_code==200,response.text
    with Session(engine()) as db:
        saved=db.get(Task,task['id']);assert saved.data['native_goal_timed_out'] and saved.data['native_goal_cancelled']
        assert saved.data['workflow_runtime']['drafts'][before['workflow_runtime']['current_step_id']]['note'].startswith('保留截止前')
        assert not saved.data.get('native_goal_acceptance') and not list(db.scalars(select(Invocation)))

def test_acceptance_cancels_future_check_and_never_revives_original_goal(client):
    _,obj,task,base,body=setup(client)
    with Session(engine()) as db:db.get(Entity,obj['id']).valid_until=datetime.now(timezone.utc)+timedelta(minutes=1);db.commit()
    define(client,base,body);task=client.get('/api/workspace/tasks/'+task['id']).json();task=complete(client,task).json();task=complete(client,task).json();drain(task)
    timer=scheduled(task);state=client.get(base).json();response=client.post(base+'/accept',json={'request_id':str(uuid4()),'expected_version':state['version'],'basis_hash':state['check']['evaluation']['basis_hash'],'reason':'原目标当前证据与流程已核查'})
    assert response.status_code==200,response.text
    assert not process_once(timer['job_id'],deliver=False)
    assert client.get(base).json()['next_time_check'] is None
    with Session(engine()) as db:assert db.get(Job,timer['job_id']).status=='cancelled'

def test_recheck_supersedes_old_timer_and_bad_trigger_cannot_update_goal(client,monkeypatch):
    _,obj,task,base,body=setup(client);at=datetime.now(timezone.utc)
    with Session(engine()) as db:db.get(Entity,obj['id']).valid_until=at+timedelta(seconds=10);db.commit()
    define(client,base,body);drain(task);old=scheduled(task);goal=client.get(base).json()
    assert client.post(base+'/check',json={'request_id':str(uuid4()),'expected_version':goal['version'],'reason':'按原目标重新核对'}).status_code==200
    drain(task);new=scheduled(task);assert new['job_id']!=old['job_id'] and not process_once(old['job_id'],deliver=False)
    with Session(engine()) as db:
        job=db.get(Job,new['job_id']);job.data={**job.data,'time_trigger_hash':'wrong'};check=db.get(Task,task['id']).data['native_goal_check_id'];db.commit()
    clock(monkeypatch,at+timedelta(seconds=11));assert process_once(new['job_id'],deliver=False)
    with Session(engine()) as db:
        assert db.get(Job,new['job_id']).status=='failed' and db.get(Task,task['id']).data['native_goal_check_id']==check
        assert not list(db.scalars(select(Invocation)))

def test_explicit_history_window_does_not_acquire_rolling_freshness(client,monkeypatch):
    _,obj,task,base,body=setup(client);at=datetime.now(timezone.utc)
    with Session(engine()) as db:db.get(Entity,obj['id']).observed_at=at-timedelta(minutes=10);db.commit()
    body['goal_spec']['evidence']['time']={'mode':'range','timezone':'Asia/Shanghai','start':(at-timedelta(minutes=20)).isoformat(),'end':(at-timedelta(minutes=5)).isoformat()}
    define(client,base,body);drain(task);goal=client.get(base).json();assert goal['check']['evidence']['status']=='sufficient' and goal['next_time_check'] is None
    clock(monkeypatch,at+timedelta(days=1));assert client.post(base+'/check',json={'request_id':str(uuid4()),'expected_version':goal['version'],'reason':'重新核对原历史范围'}).status_code==200
    drain(task);assert client.get(base).json()['check']['evidence']['status']=='sufficient'

def test_past_deadline_is_not_saved_and_revoked_timer_never_reads_or_executes(client,monkeypatch):
    _,_,task,base,body=setup(client);at=datetime.now(timezone.utc)
    invalid=copy.deepcopy(body);invalid['goal_spec']['deadline']=(at-timedelta(seconds=1)).isoformat();assert client.post(base,json=invalid).status_code==422
    body['goal_spec']['deadline']=(at+timedelta(seconds=10)).isoformat();define(client,base,body);drain(task);timer=scheduled(task)
    with Session(engine()) as db:
        saved=db.get(Task,task['id']);check=saved.data['native_goal_check_id'];membership=db.scalar(select(Membership).where(Membership.workspace_id==saved.workspace_id));membership.role='viewer';db.commit()
    clock(monkeypatch,at+timedelta(seconds=11));assert process_once(timer['job_id'],deliver=False)
    with Session(engine()) as db:
        assert db.get(Job,timer['job_id']).status=='failed' and db.get(Task,task['id']).data['native_goal_check_id']==check
        assert not db.get(Task,task['id']).data.get('native_goal_timed_out') and not list(db.scalars(select(Invocation)))
