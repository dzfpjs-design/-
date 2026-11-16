"""Real ASGI validation and commit boundary; stores/auth are explicit IO spies."""
from types import SimpleNamespace as NS
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from generic_agents import same_goal_advice_api_v304 as api
from generic_agents.security import authenticated
from generic_agents.db import session
from tests.test_same_goal_advice_contracts_v302 import scene


@pytest.fixture
def environment():
    app=FastAPI();app.include_router(api.router,prefix='/api/workspace')
    db=NS(commits=0)
    def commit():db.commits+=1
    db.commit=commit;p=object()
    app.dependency_overrides[authenticated]=lambda:p
    app.dependency_overrides[session]=lambda:db
    return NS(client=TestClient(app),db=db,p=p,base='/api/workspace/tasks/task/business-advice')


@pytest.mark.parametrize('kind',['prepare','cancel','quota','start','review'])
def test_accepted_write_is_committed_once_and_unrecognized_body_is_rejected_before_store(monkeypatch,environment,kind):
    e=environment;v=scene();calls=[]
    if kind=='prepare':
        path='/preparations';body=v.preparation['request'];module=api.store;name='prepare';args=('task',)
    elif kind=='cancel':
        path='/preparations/plan/cancel';body={'request_id':str(uuid4()),'expected_plan_version':2,'reason':'取消本次准备'}
        module=api.store;name='cancel';args=('task','plan')
    elif kind=='quota':
        path='/quota-policy';module=api.quota;name='authorize';args=('task',)
        body={'request_id':str(uuid4()),'expected_goal_hash':'a'*64,'expected_mature_head_hash':'b'*64,
            'limits':{'max_tokens':128,'max_request_bytes':50000,'max_evidence_items':2,
                'max_cost_minor_units':100,'currency':'CNY'},'expires_at':'2026-11-03T00:00:00+00:00','reason':'配置本地准备上限'}
    elif kind=='start':
        path='/preparations/plan/start';module=api.execution;name='start';args=('task','plan')
        body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':'a'*64,
            'expected_mature_head_hash':'b'*64,'preview_hash':'c'*64,'cloud_authorized':True,'reason':'已核对全文，提交一次分析'}
    else:
        path='/preparations/plan/review';module=api.reviews;name='review';args=('task','plan')
        body={'request_id':str(uuid4()),'expected_plan_version':5,'result_hash':'c'*64,
            'decision':'accepted','reason':'对照证据审核原回答'}
    def store(actual,principal,*received):
        assert actual is e.db and principal is e.p and received==(*args,body)
        calls.append(received);assert e.db.commits==0
        return {'saved':True,'cloud_send_available':False}
    monkeypatch.setattr(module,name,store)
    response=e.client.post(e.base+path,json={**body,'send_now':True})
    assert response.status_code==422 and calls==[] and e.db.commits==0
    response=e.client.post(e.base+path,json=body)
    assert response.status_code==(200 if kind in {'cancel','review'} else 201)
    assert response.json()=={'saved':True,'cloud_send_available':False} and len(calls)==1 and e.db.commits==1


@pytest.mark.parametrize('path,name,args',[
    ('/preparations','listing',('task',)),
    ('/preparations/operations/request','get_operation',('task','request')),
    ('/preparations/plan','get',('task','plan'))])
def test_read_routes_preserve_operation_routing_and_never_commit(monkeypatch,environment,path,name,args):
    e=environment
    def read(actual,principal,*received):
        assert actual is e.db and principal is e.p and received==args
        return {'phase':'queued','cloud_send_available':False}
    monkeypatch.setattr(api.store,name,read)
    result=e.client.get(e.base+path)
    assert result.status_code==200 and result.json()['phase']=='queued' and e.db.commits==0


def test_no_success_response_is_returned_when_durable_commit_fails(monkeypatch,environment):
    e=environment
    monkeypatch.setattr(api.store,'prepare',lambda *args:{'saved':True})
    def failed_commit():raise RuntimeError('durability unavailable')
    e.db.commit=failed_commit
    with pytest.raises(RuntimeError,match='durability unavailable'):
        e.client.post(e.base+'/preparations',json=scene().preparation['request'])
