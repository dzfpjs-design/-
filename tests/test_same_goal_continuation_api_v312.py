"""Real ASGI closed-body/routing/commit tests; auth/store are explicit IO spies."""
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from generic_agents import same_goal_continuation_api_v312 as api
from generic_agents.security import authenticated
from generic_agents.db import session


@pytest.fixture
def environment():
    app=FastAPI();app.include_router(api.router,prefix='/api/workspace')
    principal=NS(user_id=str(uuid4()),workspace_id=str(uuid4()),role='admin')
    db=NS(commits=0)
    def commit():db.commits+=1
    db.commit=commit
    app.dependency_overrides[authenticated]=lambda:principal
    app.dependency_overrides[session]=lambda:db
    return NS(client=TestClient(app),db=db,p=principal,
        base='/api/workspace/tasks/task/business-advice/preparations/advice/continuations')


def body(operation):
    command={'request_id':str(uuid4()),'reason':'保存人工办理安排，不派发动作'}
    if operation=='withdraw':return {**command,'expected_continuation_version':2}
    command.update(expected_task_version=3,source_hash='a'*64,
        draft=api.contracts.DraftFields(work_description='先核对现有资料，其他安排稍后补全').model_dump(mode='json'))
    if operation!='preview':command['preview_hash']='b'*64
    if operation=='revise':command['expected_continuation_version']=2
    return command


@pytest.mark.parametrize('path,target,name,args',[
    ('/context','sources','context',('task','advice')),
    ('','store','listing',('task','advice')),
    ('/operations/request','store','get_operation',('task','advice','request')),
    ('/continuation','store','get',('task','advice','continuation')),
])
def test_each_read_uses_its_scoped_reader_and_never_commits(environment,monkeypatch,path,target,name,args):
    e=environment;calls=[]
    def reader(db,p,*received):
        assert db is e.db and p is e.p and received==args
        calls.append(True);return {'read_saved_record':True}
    monkeypatch.setattr(getattr(api,target),name,reader)
    response=e.client.get(e.base+path)
    assert response.status_code==200 and response.json()=={'read_saved_record':True}
    assert calls==[True] and e.db.commits==0


@pytest.mark.parametrize('operation,path',[
    ('preview','/preview'),('create',''),('revise','/continuation/revisions'),('withdraw','/continuation/withdraw')])
def test_closed_commands_preserve_exact_scope_and_success_follows_commit(environment,monkeypatch,operation,path):
    e=environment;request=body(operation);calls=[]
    def writer(db,p,*args):
        expected=('task','advice',request) if operation in {'preview','create'} else ('task','advice','continuation',request)
        assert db is e.db and p is e.p and args==expected and e.db.commits==0
        calls.append(True);return {'can_dispatch':False,'saved':operation!='preview'}
    monkeypatch.setattr(api.store,operation,writer)
    refused=e.client.post(e.base+path,json={**request,'actor_id':str(uuid4()),'execute_now':True})
    assert refused.status_code==422 and calls==[] and e.db.commits==0
    response=e.client.post(e.base+path,json=request)
    assert response.status_code==(201 if operation=='create' else 200)
    assert response.json()['can_dispatch'] is False and calls==[True]
    assert e.db.commits==(0 if operation=='preview' else 1)


@pytest.mark.parametrize('operation,path',[
    ('create',''),('revise','/continuation/revisions'),('withdraw','/continuation/withdraw')])
def test_failed_durable_commit_never_returns_saved_success(environment,monkeypatch,operation,path):
    e=environment;monkeypatch.setattr(api.store,operation,lambda *args:{'saved':True})
    def failed():raise RuntimeError('owned durable commit failed')
    e.db.commit=failed
    with pytest.raises(RuntimeError,match='durable commit'):
        e.client.post(e.base+path,json=body(operation))
