"""Native task analysis with isolated PG and a synthetic HTTP provider only."""
import json,threading
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Job,Capability,CloudCall,Task,Membership,User
from generic_agents.contracts import Manifest,digest
from generic_agents.config import settings
from generic_agents import sensor_native_analysis as service
from generic_agents.sensor_native_analysis_api import router
from tests.test_sensor_native_packet_pg import setup_native,device
from tests.test_same_goal_advice_price_v307 import html

if not any(getattr(r,'path','')=='/api/sensor-native/tasks/{task_id}/analyses/preview' for r in app.routes):app.include_router(router)

def setup_analysis(client,account,device,monkeypatch,*,real=False):
    task,_=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];ctx=client.get(base+'/context').json();row=ctx['records'][0]
    body={'request_id':str(uuid4()),'expected_task_version':ctx['task_version'],'selection':[{'kind':row['kind'],'id':row['id'],'record_hash':row['record_hash']}]}
    packet=client.post(base+'/packets',json=body);assert packet.status_code==200,packet.text
    data=Manifest(kind='foundation_model',namespace='native_analysis_fixture',name='deepseek',version='1.0.0',owner='test',description='合成模型',allowed_agent_roles=['business'],execution={'provider':'deepseek','model':'deepseek-v4-pro','max_tokens':512,'thinking_mode':'disabled','secret_ref':'DEEPSEEK_API_KEY'},data_policy={'cloud_allowed':True}).model_dump(mode='json');h=digest(data)
    with Session(engine(),expire_on_commit=False) as db:
        cap=Capability(workspace_id=account[2],kind='foundation_model',namespace='native_analysis_fixture',name='deepseek',release_version='1.0.0',status='published',content_hash=h,validated_hash=h,data=data);db.add(cap);db.commit();model=cap.id
    if not real:monkeypatch.setattr('generic_agents.same_goal_advice_price_v307._fetch',lambda:(html(),'text/html; charset=utf-8'))
    price=client.post(base+'/analyses/price',json={'request_id':str(uuid4()),'model_id':model,'reason':'核对合成价格测试'});assert price.status_code==200,price.text
    if not real:monkeypatch.setenv('DEEPSEEK_API_KEY','synthetic-key-never-sent')
    monkeypatch.setattr(settings(),'cloud_allowed_providers','deepseek')
    return task,base,packet.json()['packet'],model

def prepare(client,base,packet,model):
    rid=str(uuid4());body={'request_id':rid,'packet_id':packet['id'],'model_id':model,'max_output_tokens':256,'max_cost_fen':5}
    response=client.post(base+'/analyses/preview',json=body);assert response.status_code==200,response.text
    assert client.post(base+'/analyses/preview',json=body).json()==response.json()
    assert client.get(base+'/analyses/previews/requests/'+rid).json()['state']=='saved'
    return response.json()

def fake_provider(monkeypatch,*,entered=None,release=None,invalid=False,crash=False):
    calls=[]
    class Client:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):
            calls.append({'url':url,'json':json});
            if entered:entered.set();assert release.wait(10)
            if crash:raise httpx.ReadTimeout('synthetic network failure')
            value={'summary':'设备线索待核查','observations':[{'evidence_refs':['R1'],'assessment':'需要核实','limitations':'时间未知'}],'suggested_checks':['人工核查']}
            content='not json' if invalid else __import__('json').dumps(value,ensure_ascii=False)
            payload={'choices':[{'finish_reason':'stop','message':{'content':content}}],'usage':{'prompt_tokens':30,'completion_tokens':20,'total_tokens':50}}
            return httpx.Response(200,json=payload,headers={'x-request-id':'synthetic-provider-id'})
    monkeypatch.setattr('generic_agents.cloud_business_reasoning_v197.httpx.Client',Client)
    return calls

def test_real_provider_protocol_once_and_durable_raw_recovery(client,account,device,monkeypatch):
    task,base,packet,model=setup_analysis(client,account,device,monkeypatch);preview=prepare(client,base,packet,model);calls=fake_provider(monkeypatch)
    assert preview['quote']['provider_price_locked'] is False and len(preview['messages']) and all(isinstance(m['content'],str) for m in preview['messages'])
    request={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True};url=base+'/analyses/'+preview['preview_id']+'/send'
    saved=client.post(url,json=request);assert saved.status_code==200,saved.text;assert client.post(url,json=request).json()==saved.json()
    assert service.tick(account[2])==1 and len(calls)==1
    assert calls[0]['json']['messages']==preview['messages']
    assert {k:v for k,v in calls[0]['json'].items() if k!='messages'}==preview['options']
    result=client.get(base+'/analyses/requests/'+request['request_id']);assert result.status_code==200,result.text
    data=result.json();assert data['state']=='completed' and data['analysis']['result']['observations'][0]['evidence_refs']==['R1']
    assert data['analysis']['evidence_refs'][0]['object_name']=='公开测试条目'
    assert client.get(base+'/analyses/'+data['analysis']['id']+'/original').status_code==200
    assert service.tick(account[2])==0 and len(calls)==1
    engine().dispose();assert client.get(base+'/analyses/history').json()['analyses'][0]['state']=='completed'
    with Session(engine()) as db:assert len(list(db.scalars(select(CloudCall))))==1 and db.get(Task,task['id']).status=='workflow_waiting'

def test_unknown_never_auto_retries_and_requires_fresh_risk_ack(client,account,device,monkeypatch):
    task,base,packet,model=setup_analysis(client,account,device,monkeypatch);preview=prepare(client,base,packet,model);calls=fake_provider(monkeypatch,crash=True)
    request={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True};assert client.post(base+'/analyses/'+preview['preview_id']+'/send',json=request).status_code==200
    assert service.tick(account[2])==1 and service.tick(account[2])==0 and len(calls)==1
    assert client.get(base+'/analyses/requests/'+request['request_id']).json()['state']=='response_unknown'
    next_preview=prepare(client,base,packet,model);assert next_preview['requires_uncertainty_ack']
    new={'request_id':str(uuid4()),'preview_hash':next_preview['preview_hash'],'consent':True};url=base+'/analyses/'+next_preview['preview_id']+'/send'
    assert client.post(url,json=new).status_code==409
    assert client.post(url,json={**new,'acknowledge_prior_uncertain':True}).status_code==200

def test_invalid_original_answer_is_preserved_without_retry(client,account,device,monkeypatch):
    import base64
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch);preview=prepare(client,base,packet,model);calls=fake_provider(monkeypatch,invalid=True)
    request={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True};assert client.post(base+'/analyses/'+preview['preview_id']+'/send',json=request).status_code==200
    assert service.tick(account[2])==1 and service.tick(account[2])==0 and len(calls)==1
    result=client.get(base+'/analyses/requests/'+request['request_id']).json();assert result['state']=='response_invalid' and result['analysis']['result'] is None
    original=client.get(base+'/analyses/'+result['analysis']['id']+'/original');assert original.status_code==200 and b'not json' in base64.b64decode(original.json()['raw_base64'])

def test_saved_first_answer_recovered_locally_without_second_send(client,account,device,monkeypatch):
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch);preview=prepare(client,base,packet,model);calls=fake_provider(monkeypatch)
    request={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True};assert client.post(base+'/analyses/'+preview['preview_id']+'/send',json=request).status_code==200
    original=service._recover;monkeypatch.setattr(service,'_recover',lambda *args:False)
    assert service.tick(account[2])==1 and len(calls)==1
    assert client.get(base+'/analyses/requests/'+request['request_id']).json()['state']=='response_saved'
    monkeypatch.setattr(service,'_recover',original);engine().dispose()
    assert service.tick(account[2])==1 and len(calls)==1
    assert client.get(base+'/analyses/requests/'+request['request_id']).json()['state']=='completed'

def test_queued_cancel_and_tombstone_prevent_late_send(client,account,device,monkeypatch):
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch);preview=prepare(client,base,packet,model);calls=fake_provider(monkeypatch)
    rid=str(uuid4());path=base+'/analyses/requests/'+rid
    assert client.post(path+'/cancel',json={}).json()['state']=='cancelled'
    body={'request_id':rid,'preview_hash':preview['preview_hash'],'consent':True}
    assert client.post(base+'/analyses/'+preview['preview_id']+'/send',json=body).status_code==409
    fresh={**body,'request_id':str(uuid4())};assert client.post(base+'/analyses/'+preview['preview_id']+'/send',json=fresh).status_code==200
    assert client.post(base+'/analyses/requests/'+fresh['request_id']+'/cancel',json={}).json()['state']=='cancelled'
    assert service.tick(account[2])==0 and not calls

def test_missing_official_price_and_model_without_business_role_block_preview(client,account,device,monkeypatch):
    from sqlalchemy import delete
    from generic_agents.models import Audit
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    with Session(engine()) as db:db.execute(delete(Audit).where(Audit.workspace_id==account[2],Audit.action=='same_goal_advice.official_price_registered_v307'));db.commit()
    assert not next(x for x in client.get(base+'/analyses/context').json()['models'] if x['id']==model)['price_available']
    body={'request_id':str(uuid4()),'packet_id':packet['id'],'model_id':model,'max_output_tokens':256,'max_cost_fen':5}
    assert client.post(base+'/analyses/preview',json=body).status_code==409

def test_unlimited_preview_without_price_is_durable_and_sent_once(client,account,device,monkeypatch):
    from sqlalchemy import delete
    from generic_agents.models import Audit
    task,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    with Session(engine()) as db:
        db.execute(delete(Audit).where(Audit.workspace_id==account[2],Audit.action=='same_goal_advice.official_price_registered_v307'));db.commit()
    body={'request_id':str(uuid4()),'packet_id':packet['id'],'model_id':model,'max_output_tokens':256}
    first=client.post(base+'/analyses/preview',json=body);assert first.status_code==200,first.text
    preview=first.json();assert client.post(base+'/analyses/preview',json=body).json()==preview
    assert preview['quote']['schema']=='sensor-native-unlimited-quote-v2'
    assert preview['quote']['user_limit_fen'] is None and preview['quote']['maximum_cost_fen'] is None
    calls=fake_provider(monkeypatch)
    req={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    url=base+'/analyses/'+preview['preview_id']+'/send';saved=client.post(url,json=req)
    assert saved.status_code==200,saved.text
    assert client.post(url,json=req).json()==saved.json()
    with Session(engine()) as db:
        assert db.get(Job,saved.json()['analysis']['id']).kind==service.ANALYSIS_UNLIMITED
    assert service.tick(account[2])==1 and len(calls)==1
    result=client.get(base+'/analyses/requests/'+req['request_id']).json()
    assert result['state']=='completed' and result['analysis']['cost_status']['maximum_cost_fen'] is None
    assert result['analysis']['cost_status']['actual_cost_fen'] is None
    assert service.tick(account[2])==0 and len(calls)==1
    engine().dispose()
    assert client.get(base+'/analyses/history').json()['analyses'][0]['request_id']==req['request_id']
    with Session(engine()) as db:
        assert len(list(db.scalars(select(CloudCall))))==1 and db.get(Task,task['id']).status=='workflow_waiting'

def test_unlimited_queue_keeps_original_identity_without_price(client,account,device,monkeypatch):
    from sqlalchemy import delete
    from generic_agents.models import Audit
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    with Session(engine()) as db:
        db.execute(delete(Audit).where(Audit.workspace_id==account[2],Audit.action=='same_goal_advice.official_price_registered_v307'));db.commit()
    body={'request_id':str(uuid4()),'packet_id':packet['id'],'model_id':model,'max_output_tokens':256}
    response=client.post(base+'/analyses/preview',json=body);assert response.status_code==200,response.text
    preview=response.json();assert client.post(base+'/analyses/preview',json=body).json()==preview
    assert preview['quote']['schema']=='sensor-native-unlimited-quote-v2'
    assert preview['quote']['maximum_cost_fen'] is None and preview['quote']['user_limit_fen'] is None
    req={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    url=base+'/analyses/'+preview['preview_id']+'/send';saved=client.post(url,json=req)
    assert saved.status_code==200,saved.text
    assert client.post(url,json=req).json()==saved.json()
    engine().dispose()
    assert client.get(base+'/analyses/requests/'+req['request_id']).json()==saved.json()
    with Session(engine()) as db:
        row=db.get(Job,saved.json()['analysis']['id']);assert row.kind==service.ANALYSIS_UNLIMITED
        assert not list(db.scalars(select(CloudCall)))

def test_unlimited_quote_does_not_require_price_after_preview(client,account,device,monkeypatch):
    from sqlalchemy import delete
    from generic_agents.models import Audit
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    body={'request_id':str(uuid4()),'packet_id':packet['id'],'model_id':model,'max_output_tokens':256}
    response=client.post(base+'/analyses/preview',json=body);assert response.status_code==200,response.text
    preview=response.json();assert isinstance(preview['quote']['maximum_cost_fen'],int)
    with Session(engine()) as db:
        db.execute(delete(Audit).where(Audit.workspace_id==account[2],Audit.action=='same_goal_advice.official_price_registered_v307'));db.commit()
    req={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    saved=client.post(base+'/analyses/'+preview['preview_id']+'/send',json=req)
    assert saved.status_code==200,saved.text
    assert saved.json()['state']=='queued'
    assert saved.json()['analysis']['cost_status']['maximum_cost_fen']==preview['quote']['maximum_cost_fen']
    with Session(engine()) as db:assert not list(db.scalars(select(CloudCall)))

def test_unlimited_unknown_never_retries_and_is_visible_to_legacy_flow(client,account,device,monkeypatch):
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    body={'request_id':str(uuid4()),'packet_id':packet['id'],'model_id':model,'max_output_tokens':256}
    preview=client.post(base+'/analyses/preview',json=body).json();calls=fake_provider(monkeypatch,crash=True)
    request={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    assert client.post(base+'/analyses/'+preview['preview_id']+'/send',json=request).status_code==200
    assert service.tick(account[2])==1 and len(calls)==1
    assert client.get(base+'/analyses/requests/'+request['request_id']).json()['state']=='response_unknown'
    assert service.tick(account[2])==0 and len(calls)==1
    old_preview=prepare(client,base,packet,model);assert old_preview['requires_uncertainty_ack']
    fresh={'request_id':str(uuid4()),'preview_hash':old_preview['preview_hash'],'consent':True}
    assert client.post(base+'/analyses/'+old_preview['preview_id']+'/send',json=fresh).status_code==409
    assert len(calls)==1

def test_unlimited_saved_response_recovery_never_resends(client,account,device,monkeypatch):
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    body={'request_id':str(uuid4()),'packet_id':packet['id'],'model_id':model,'max_output_tokens':256}
    preview=client.post(base+'/analyses/preview',json=body).json();calls=fake_provider(monkeypatch)
    request={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    assert client.post(base+'/analyses/'+preview['preview_id']+'/send',json=request).status_code==200
    original=service._recover;monkeypatch.setattr(service,'_recover',lambda *args:False)
    assert service.tick(account[2])==1 and len(calls)==1
    assert client.get(base+'/analyses/requests/'+request['request_id']).json()['state']=='response_saved'
    monkeypatch.setattr(service,'_recover',original);engine().dispose()
    assert service.tick(account[2])==1 and len(calls)==1
    result=client.get(base+'/analyses/requests/'+request['request_id']).json()
    assert result['state']=='completed' and result['analysis']['receipt']['usage']['total_tokens']==50
    with Session(engine()) as db:assert len(list(db.scalars(select(CloudCall))))==1

def test_one_real_deepseek_synthetic_only(client,account,device,monkeypatch):
    import os,pytest
    if os.getenv('GA_RUN_SENSOR_REAL_DEEPSEEK')!='1':pytest.skip('explicit synthetic-only paid run required')
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch,real=True);preview=prepare(client,base,packet,model)
    assert preview['model']['model']=='deepseek-v4-pro' and preview['quote']['user_limit_fen']==5
    print('SYNTHETIC_REAL_CALL_AUTHORIZED quote_fen=',preview['quote']['maximum_cost_fen'],'output_limit=256')
    request={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    response=client.post(base+'/analyses/'+preview['preview_id']+'/send',json=request);assert response.status_code==200,response.text
    assert service.tick(account[2])==1
    result=client.get(base+'/analyses/requests/'+request['request_id']).json()
    print('SYNTHETIC_REAL_CALL_STATE',result['state'])
    assert result['state']=='completed',result['state']
    assert result['analysis']['result'] and result['analysis']['receipt']['provider']=='deepseek'
    assert service.tick(account[2])==0
