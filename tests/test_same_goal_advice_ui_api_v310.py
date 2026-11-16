"""Real ASGI routing, closed bodies and durable commit boundaries; IO spies."""
from uuid import uuid4

import pytest

from generic_agents import same_goal_advice_api_v304 as api
from tests.test_same_goal_advice_api_v304 import environment
from tests.test_same_goal_advice_contracts_v302 import cap


@pytest.mark.parametrize('path,target,name,args,kwargs',[
    ('/context','context','context',('task',),{}),
    ('/operations/request','commands','read',('task','request'),{}),
    ('/preparations/plan/documents','context','documents',('task','plan'),{}),
    ('/preparations/plan/starts/operations/request','commands','read',('task','request'),{'plan_id':'plan','operation':'start'}),
    ('/preparations/plan/reviews/operations/request','commands','read',('task','request'),{'plan_id':'plan','operation':'review'}),
])
def test_ui_reads_reach_actual_named_reader_without_commit(monkeypatch,environment,path,target,name,args,kwargs):
    e=environment;calls=[]
    def read(db,p,*received,**options):
        assert db is e.db and p is e.p and received==args and options==kwargs
        calls.append(True);return {'original_read':True}
    monkeypatch.setattr(getattr(api,target),name,read)
    response=e.client.get(e.base+path)
    assert response.status_code==200 and response.json()=={'original_read':True}
    assert calls==[True] and e.db.commits==0


def body(kind):
    common={'request_id':str(uuid4()),'reason':'管理员明确核对本次费用设置，不授权模型发送'}
    if kind=='price':return {**common,'model_pin':cap('fixed_registered_model'),'validity_seconds':3600}
    return {**common,'expected_goal_hash':'a'*64,'expected_mature_head_hash':'b'*64,
        'max_cost_minor_units':100,'currency':'CNY','validity_seconds':7200}


@pytest.mark.parametrize('kind,name',[('price','price'),('quota','configure_quota')])
def test_ui_admin_commands_reject_caller_authority_and_ack_only_after_commit(monkeypatch,environment,kind,name):
    e=environment;request=body(kind);calls=[]
    def write(db,p,task_id,received):
        assert db is e.db and p is e.p and task_id=='task' and received==request
        assert e.db.commits==0;calls.append(True)
        return {'configuration_only':True,'cloud_send_authorized':False}
    monkeypatch.setattr(api.options,name,write)
    path=e.base+'/'+kind+'-options'
    refused=e.client.post(path,json={**request,'actor_id':str(uuid4()),'send_now':True})
    assert refused.status_code==422 and not calls and e.db.commits==0
    response=e.client.post(path,json=request)
    assert response.status_code==201 and response.json()['cloud_send_authorized'] is False
    assert calls==[True] and e.db.commits==1


@pytest.mark.parametrize('kind,name',[('price','price'),('quota','configure_quota')])
def test_failed_commit_cannot_return_an_admin_configuration_success(monkeypatch,environment,kind,name):
    e=environment
    monkeypatch.setattr(api.options,name,lambda *args:{'configuration_only':True})
    def broken():raise RuntimeError('explicit durable commit IO failed')
    e.db.commit=broken
    with pytest.raises(RuntimeError,match='durable commit'):
        e.client.post(e.base+'/'+kind+'-options',json=body(kind))
