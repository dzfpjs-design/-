import json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import SecretStr
import httpx
from generic_agents.cloud_budget import reserve, finish
from generic_agents.models import Capability, Job, CloudCall
from generic_agents.security import Principal
from generic_agents.db import engine
from generic_agents.gateway import cloud_json, ModelBlocked
from generic_agents.config import settings
from .test_vertical_slice import publish, manifest, entity


def profile(client):
    return publish(client, manifest("foundation_model", name="qwen", execution={
        "provider":"qwen","model":"qwen-plus","secret_ref":"DASHSCOPE_API_KEY","max_tokens":128},
        data_policy={"cloud_allowed":True,"redact_fields":["contact"]}))


def principal(client):
    me=client.get('/api/me').json()
    with Session(engine()) as db:
        from generic_agents.models import User
        user=db.scalar(select(User).where(User.username==me['username']))
        return Principal(user.id,me['workspace_id'],'admin','test')


def test_budget_reservation_conservative_failure_and_reconciliation(client,monkeypatch):
    # Finite owned ledger capacity; the runner keeps real provider budgets zero.
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',2)
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',2000)
    caprow=profile(client);p=principal(client)
    with Session(engine()) as db:
        cap=db.get(Capability,caprow['id'])
        call_id=reserve(p,cap,1000,'input_hash')
    finish(call_id,{'usage':{'total_tokens':20}},'succeeded')
    finish(call_id,{'usage':{'total_tokens':20}},'succeeded') # idempotent acknowledgement
    with Session(engine()) as db:
        call=db.get(CloudCall,call_id)
        assert call.charged_tokens==20 and call.status=='succeeded'
        budget=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
        assert budget.data['tokens']==20 and budget.data['calls']==1
        cap=db.get(Capability,caprow['id']);failed=reserve(p,cap,400,'uncertain_request')
    finish(failed)
    with Session(engine()) as db:
        assert db.get(CloudCall,failed).charged_tokens==400 # timeout can still be billed


def test_cloud_redacts_fields_and_validates_output(client,monkeypatch):
    # Exact synthetic HTTP only; reserve/settle remain real.
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',1)
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',10000)
    caprow=profile(client);p=principal(client);captured=[]
    base=httpx.Client
    def handler(request):
        assert str(request.url)=='https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}],
                                       'usage':{'total_tokens':20}})
    monkeypatch.setattr('generic_agents.gateway.httpx.Client',lambda **kw:base(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(settings(),'dashscope_api_key',SecretStr('test-only-nonlive-key'))
    monkeypatch.delenv('DASHSCOPE_API_KEY',raising=False)
    with Session(engine()) as db:
        value,receipt=cloud_json(db.get(Capability,caprow['id']),[{'role':'user','content':json.dumps({
            'evidence':{'phone':'test-sensitive-phone','contact':'test-sensitive-contact','temperature':25}})}],
            {'type':'object','required':['ok'],'additionalProperties':False,'properties':{'ok':{'type':'boolean'}}},p)
    assert value['ok'] and receipt['usage']['total_tokens']==20
    sent=json.dumps(captured)
    assert 'test-sensitive-phone' not in sent and 'test-sensitive-contact' not in sent
    assert '[已脱敏]' in captured[0]['messages'][1]['content']


def test_cloud_query_input_is_saved_before_worker_runs(client,monkeypatch):
    from generic_agents.worker import process_once
    package=publish(client,manifest());obj=entity(client,package);model=profile(client)
    run=client.post('/api/data/queries',json={'question':'云端解析需求','entity_ids':[obj['id']],
           'model_profile_id':model['id'],'mode':'cloud'}).json()
    assert run['status']=='queued' and run['data']['request']['question']=='云端解析需求'
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:({'entity_ids':[obj['id']],
         'fields':['temperature'],'purpose':'核实温度'},{'provider':'test_fixture'}))
    assert process_once()
    saved=client.get('/api/data/queries').json()[0]
    assert saved['id']==run['id'] and saved['status']=='sufficient'


def test_cloud_cannot_widen_object_scope(client,monkeypatch):
    from generic_agents.worker import process_once
    package=publish(client,manifest());obj=entity(client,package);model=profile(client)
    run=client.post('/api/data/queries',json={'question':'范围约束','entity_ids':[obj['id']],
           'model_profile_id':model['id'],'mode':'cloud'}).json()
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:({'entity_ids':['outside_scope'],
         'fields':[],'purpose':'恶意扩张'},{'provider':'test_fixture'}))
    process_once()
    assert client.get('/api/data/queries').json()[0]['status']=='blocked_model'
