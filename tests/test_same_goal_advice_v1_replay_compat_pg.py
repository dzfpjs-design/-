"""Original v1 command replay with a coexisting v2 plan; never a send.

Disposable PG, synthetic custody and a substituted Task lookup; this does not
prove mature goals, approved source selection, v2 execution or production.
"""
import copy
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from fastapi import HTTPException
from generic_agents import same_goal_advice_store_v302 as legacy
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Invocation, Job, Plan, Task, uid
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_same_goal_advice_v1_history_compat_pg import add_cancelled
from tests.test_same_goal_advice_expired_cancel_candidate_v2_pg import expired


def use_synthetic_task(monkeypatch):
    def task_for(db, p, identity, **kwargs):
        task = db.get(Task, identity)
        if task is None or task.workspace_id != p.workspace_id: raise HTTPException(404)
        return task
    monkeypatch.setattr(legacy.existing, '_task', task_for)


def test_http_original_v1_prepare_and_cancel_replay_coexist_with_v2(client, synthetic_custody, monkeypatch):
    s=synthetic_custody; use_synthetic_task(monkeypatch)
    with Session(engine()) as db:
        old_id=add_cancelled(db,s);old=db.get(Plan,old_id)
        prepare=copy.deepcopy(old.data['operations'][0]['body']);cancel=copy.deepcopy(old.data['operations'][1]['body'])
        snapshots={model.__name__:db.scalar(select(func.count()).select_from(model)) for model in (Plan,Job,Audit,CloudCall,Invocation)}
        db.commit()
    base='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
    response=client.post(base,json=prepare)
    assert response.status_code==201,response.text
    assert response.json()['plan_id']==old_id and response.json()['phase']=='cancelled'
    response=client.post(base+'/'+old_id+'/cancel',json=cancel)
    assert response.status_code==200,response.text
    assert response.json()['plan_id']==old_id and response.json()['phase']=='cancelled'
    assert client.post(base,json={**prepare,'reason':'不同正文'}).status_code==409
    assert client.post(base+'/'+old_id+'/cancel',json={**cancel,'reason':'changed'}).status_code==409
    assert client.post(base,json={**prepare,'request_id':uid()}).status_code==409
    assert client.post(base,json={**prepare,'request_id':s['prep']['request']['request_id']}).status_code==409
    assert client.post(base+'/'+old_id+'/cancel',json={**cancel,'request_id':uid()}).status_code==409
    with Session(engine()) as db:
        assert {model.__name__:db.scalar(select(func.count()).select_from(model)) for model in (Plan,Job,Audit,CloudCall,Invocation)}==snapshots
        job=db.get(Job,s['job_id']);job.available_at=db.scalar(select(func.clock_timestamp()));db.commit()
    assert client.post(base,json=prepare).status_code==409
    assert client.post(base+'/'+old_id+'/cancel',json=cancel).status_code==409


def test_another_administrator_cannot_replay_original_actor_commands(client, account, synthetic_custody, monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    from generic_agents.models import User, Membership
    from generic_agents.security import password_hash
    s=synthetic_custody;use_synthetic_task(monkeypatch)
    with Session(engine()) as db:
        old_id=add_cancelled(db,s);old=db.get(Plan,old_id)
        prepare=copy.deepcopy(old.data['operations'][0]['body']);cancel=copy.deepcopy(old.data['operations'][1]['body'])
        other=User(username='other_'+uid(),password_hash=password_hash('only-a-synthetic-test-password'))
        db.add(other);db.flush();db.add(Membership(user_id=other.id,workspace_id=account[2],role='admin'));db.commit()
        name=other.username
    base='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
    with TestClient(app) as other_client:
        login=other_client.post('/api/login',json={'username':name,'password':'only-a-synthetic-test-password'})
        assert login.status_code==200,login.text
        other_client.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert other_client.post(base,json=prepare).status_code==409
        assert other_client.post(base+'/'+old_id+'/cancel',json=cancel).status_code==409
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Plan))==2
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert db.scalar(select(func.count()).select_from(Invocation))==0


def test_http_original_v1_prepare_replay_when_v2_is_expired_cancelled(client, monkeypatch, expired):
    from generic_agents import same_goal_advice_expired_cancel_candidate_v2 as cancellation
    from tests.test_same_goal_advice_common_inventory_v2_pg import add_synthetic_v1
    from tests.test_same_goal_advice_expired_cancel_candidate_v2_pg import body
    s=expired; p=s['principal'];use_synthetic_task(monkeypatch)
    with Session(engine()) as db:
        cancellation.cancel(db,p,s['task_id'],s['plan_id'],body());db.flush()
        old=add_synthetic_v1(db,s);old_id=old.id;original=copy.deepcopy(old.data['operations'][0]['body']);db.commit()
    engine().dispose()
    base='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
    result=client.post(base,json=original)
    assert result.status_code==201,result.text
    assert result.json()['plan_id']==old_id and result.json()['phase']=='queued'
    assert client.post(base,json={**original,'request_id':uid()}).status_code==409
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Plan))==2
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert db.scalar(select(func.count()).select_from(Invocation))==0
