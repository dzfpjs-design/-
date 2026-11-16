"""Actual ASGI routing/closed requests/commit boundaries, with explicit IO doubles."""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from generic_agents import same_goal_continuation_execution_api_v314 as api
from generic_agents.db import session
from generic_agents.security import authenticated


def uid():return str(uuid4())


@pytest.fixture
def scene():
    app=FastAPI();app.include_router(api.router,prefix='/api/workspace')
    p=NS(user_id=uid(),workspace_id=uid(),role='operator');db=NS(commits=0)
    def commit():db.commits+=1
    db.commit=commit
    app.dependency_overrides[authenticated]=lambda:p
    app.dependency_overrides[session]=lambda:db
    return NS(client=TestClient(app),db=db,p=p,
        base='/api/workspace/tasks/task/business-advice/preparations/advice/continuations/draft/executions')


def body(operation):
    if operation in {'preview','submit'}:
        value={'request_id':uid(),'expected_task_version':7,'expected_continuation_version':2,
            'revision_hash':'a'*64,'source_hash':'b'*64,'grant_ref':{'id':uid(),'version':1,'record_hash':'c'*64},
            'plan_origin':'human_revision','rationale':'明确核对保存的只读计算方案','supersedes_plan_id':None}
        if operation=='submit':value['preview_hash']='d'*64
        return value
    value={'request_id':uid(),'expected_execution_version':2,'reason':'明确办理原方案，保留原请求与原件'}
    if operation=='decide':value.update(submission_hash='e'*64,decision='approve')
    if operation=='review':value.update(output_hash='f'*64,decision='adopt')
    return value


@pytest.mark.parametrize('operation',['submit','decide','start','cancel','review','recover'])
def test_closed_request_and_success_only_after_commit(scene,monkeypatch,operation):
    t=scene;calls=[];value=body(operation)
    def write(db,p,*args):
        assert db is t.db and p is t.p and db.commits==0 and args[-1]==value
        calls.append(args);return {'saved_original':True}
    monkeypatch.setattr(api.store,operation,write)
    path=t.base+('' if operation=='submit' else '/plan/'+operation)
    assert t.client.post(path,json={**value,'records':[],'execution_approved':True}).status_code==422
    assert not calls and t.db.commits==0
    response=t.client.post(path,json=value)
    assert response.status_code==(201 if operation=='submit' else 200)
    assert response.json()=={'saved_original':True} and len(calls)==1 and t.db.commits==1


@pytest.mark.parametrize('operation',['submit','decide','start','cancel','review','recover'])
def test_commit_failure_never_returns_success(scene,monkeypatch,operation):
    t=scene
    monkeypatch.setattr(api.store,operation,lambda *args:{'saved':True})
    def fail():raise RuntimeError('durable commit failed')
    t.db.commit=fail
    with pytest.raises(RuntimeError,match='durable commit'):
        t.client.post(t.base+('' if operation=='submit' else '/plan/'+operation),json=body(operation))


@pytest.mark.parametrize('path,name,args',[('', 'listing',('task','advice','draft')),
    ('/plan','get',('task','advice','draft','plan')),
    ('/operations/request','get_operation',('task','advice','draft','request'))])
def test_historical_get_retains_scope_and_never_commits(scene,monkeypatch,path,name,args):
    t=scene
    def read(db,p,*actual):
        assert db is t.db and p is t.p and actual==args
        return {'original':True}
    monkeypatch.setattr(api.store,name,read)
    assert t.client.get(t.base+path).json()=={'original':True} and t.db.commits==0


def test_preview_is_flat_and_not_committed(scene,monkeypatch):
    t=scene
    monkeypatch.setattr(api.sources,'build_proposal',lambda *args:{'proposal':{'record_hash':'a'*64}})
    monkeypatch.setattr(api.sources,'proposal_view',lambda proposal:{'schema':api.c.PREVIEW,'preview_hash':proposal['record_hash']})
    response=t.client.post(t.base+'/preview',json=body('preview'))
    assert response.status_code==200 and response.json()=={'schema':api.c.PREVIEW,'preview_hash':'a'*64}
    assert t.db.commits==0
