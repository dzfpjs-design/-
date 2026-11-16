"""PG/API vertical slice for grounded object fields; model outputs here are test doubles."""
import copy
import json
from uuid import uuid4
import pytest
import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import SetupAnalysis, SetupMaterial, CloudCall, Job
from generic_agents.models import Membership
from generic_agents.config import settings
from generic_agents.contracts import digest
import generic_agents.setup_understanding as understanding
from generic_agents.instance_compiler import signature_value
from tests.test_setup_understanding import prepared, start, process, proposal
from tests.test_instance_workspace import save, enable

TEXT='设备档案。设备编号：文本，必填。温度：数值，单位℃。振动：数值，未说明单位。收到新数据，由值班人员复核后归档。'

def _synthetic_reservations(monkeypatch):
    """Budget only for installed synthetic transports; never real provider IO."""
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 100)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)

def fake_v2(monkeypatch, mutate=None):
    calls=[]
    def cloud(cap,messages,schema,p,**kwargs):
        payload=json.loads(messages[0]['content']);calls.append(payload)
        value=proposal(payload);block=payload['source_blocks'][0]
        cite={'material_id':block['material_id'],'block_id':block['block_id'],'quote':block['text']}
        value['objects']=[{'name':'设备','citations':[cite],'inference_reason':'','fields':[
            {'label':label,'kind':kind,'required':required,'unit':unit,'citations':[cite],'inference_reason':''}
            for label,kind,required,unit in [('设备编号','文本',True,None),('温度','数值',False,'℃'),('振动','数值',False,None)]]}]
        if mutate:mutate(value,payload)
        from generic_agents.cloud_budget import reserve
        prepared_messages,_,_=understanding.prepare_cloud_json(cap,messages,schema)
        call_id=reserve(p,cap,1000,digest(prepared_messages))
        with Session(engine()) as ledger_db:
            call=ledger_db.get(CloudCall,call_id)
            call.data={**call.data,'dispatch_options_hash':digest(understanding.cloud_json_options(cap.data['execution']))}
            ledger_db.commit()
        if kwargs.get('on_reserved'):kwargs['on_reserved'](call_id)
        receipt={'provider':cap.data['execution']['provider'],'model':cap.data['execution']['model'],
            'usage':{'total_tokens':10},'call_id':call_id}
        if kwargs.get('on_response'):kwargs['on_response'](json.dumps(value,ensure_ascii=False),receipt)
        return value,receipt
    monkeypatch.setattr(understanding,'cloud_json',cloud)
    _synthetic_reservations(monkeypatch)
    return calls

def ready(client,monkeypatch,mutate=None):
    body,m=prepared(client,TEXT);body['contract_version']=2
    calls=fake_v2(monkeypatch,mutate);row,preview=start(client,body);process(row)
    return client.get('/api/setup/understanding/'+row['id']).json(),body,m,calls,preview

def apply(client,row,body,**kwargs):
    return client.post('/api/setup/understanding/'+row['id']+'/apply',json={
        'version':row['version'],'revision':body['revision'],'names':[], 'object_names':['设备'],**kwargs})

def test_v2_preview_consent_is_distinct_and_sends_only_existing_object_names(client,monkeypatch):
    body,m=prepared(client,TEXT)
    draft=client.get('/api/setup/draft').json();c=draft['configuration']
    c['instanceObjects']=[{'name':'人工类型','fields':[{'label':'编号','kind':'文本','required':True}], 'private_note':'OBJECT_PRIVATE_CANARY'}]
    c['instanceObjectOrigins']={'人工类型':{'private':'ORIGIN_PRIVATE_CANARY'}}
    body['revision']=save(client,c)
    legacy=client.post('/api/setup/understanding/preview',json=body).json()
    v2=client.post('/api/setup/understanding/preview',json={**body,'contract_version':2}).json()
    assert legacy['contract_hash']==digest(understanding.Result.model_json_schema())
    assert v2['contract_hash']==digest(understanding.ResultV2.model_json_schema())
    assert v2['context']['object_names']==['人工类型']
    assert not any(x in json.dumps(v2) for x in ['OBJECT_PRIVATE_CANARY','ORIGIN_PRIVATE_CANARY'])
    assert client.post('/api/setup/understanding',json={**body,'contract_version':2,'preview_hash':legacy['preview_hash'],'consent':True}).status_code==409

def test_object_only_review_apply_undo_reuse_are_durable_and_no_extra_model_calls(client,monkeypatch):
    row,body,m,calls,preview=ready(client,monkeypatch);assert row['status']=='review',row.get('error')
    before=client.get('/api/setup/draft').json()
    review=client.post('/api/setup/understanding/'+row['id']+'/review',json={
        'version':row['version'],'names':[],'object_names':['设备'],'accept_domain':False})
    assert review.status_code==200,review.text;row=review.json()
    assert client.get('/api/setup/understanding/'+row['id']).json()['review_selection']['object_names']==['设备']
    result=apply(client,row,body);assert result.status_code==200,result.text;applied=result.json()
    assert applied['configuration']['businesses']==before['configuration'].get('businesses',[])
    assert applied['configuration']['instanceObjects'][0]['fields'][1]['unit']=='℃'
    assert applied['configuration']['instanceObjects'][0]['fields'][2]['unit'] is None
    assert applied['configuration']['instanceObjectOrigins']['设备']['result_hash']==row['result_hash']
    undo=client.post('/api/setup/understanding/'+row['id']+'/undo',json={
        'version':applied['analysis']['version'],'revision':applied['revision'],'object_names':['设备']})
    assert undo.status_code==200 and undo.json()['configuration']==before['configuration']
    undone=undo.json()
    reused=client.post('/api/setup/understanding/'+row['id']+'/reuse',json={
        'version':undone['analysis']['version'],'revision':undone['revision']})
    assert reused.status_code==201,reused.text
    assert reused.json()['review_selection']['object_names']==['设备']
    assert reused.json()['receipts']==[] and len(calls)==1

def test_unknown_unit_cannot_be_promoted_from_other_field_quote_and_paid_raw_is_kept(client,monkeypatch):
    row,body,m,calls,preview=ready(client,monkeypatch,lambda v,p:v['objects'][0]['fields'][2].update(
        unit='℃',citations=[{**v['objects'][0]['fields'][2]['citations'][0],'quote':'温度：数值，单位℃。'}]))
    assert row['status']=='failed' and row['receipts'] and not row['result']
    assert '单位' in row['error'] and len(calls)==1
    with Session(engine()) as db:
        raw=db.get(SetupAnalysis,row['id']).data['raw_response']
        assert raw['value']['objects'][0]['fields'][2]['unit']=='℃'
    assert 'instanceObjects' not in client.get('/api/setup/draft').json()['configuration']

def test_human_schema_changes_block_late_apply_and_v2_reuse(client,monkeypatch):
    row,body,m,calls,preview=ready(client,monkeypatch)
    applied=apply(client,row,body).json()
    undone=client.post('/api/setup/understanding/'+row['id']+'/undo',json={
        'version':applied['analysis']['version'],'revision':applied['revision'],'object_names':['设备']}).json()
    changed=copy.deepcopy(undone['configuration']);changed['instanceObjects']=[{
        'name':'人工类型','fields':[{'label':'手工编号','kind':'文本','required':True}]}]
    rev=save(client,changed)
    assert client.post('/api/setup/understanding/'+row['id']+'/reuse',json={
        'version':undone['analysis']['version'],'revision':rev}).status_code==409
    assert client.get('/api/setup/draft').json()['configuration']==changed

def test_source_schema_compiles_actual_unit_create_checks_and_derived_plan(client,monkeypatch):
    row,body,m,calls,preview=ready(client,monkeypatch)
    response=apply(client,row,body,names=['异常复核']);assert response.status_code==200,response.text
    c=response.json()['configuration'];flow=c['flows']['异常复核']
    flow['confirmedAt']='2026-10-11T12:00:00+08:00';flow['confirmedSignature']=json.dumps(signature_value(c,flow),ensure_ascii=False)
    c['automation']={'version':1,'policies':{'异常复核':{'rules':{'write':{'mode':'仅给出建议'}}}}}
    rev=save(client,c,review_automation=True);member=client.get('/api/instances/members').json()[0]
    compilation={'revision':rev,'objects':c['instanceObjects'],'role_bindings':{'值班人员':member['id']}}
    checked=client.post('/api/instances/check',json=compilation);assert checked.status_code==200,checked.text
    check=checked.json();assert not check['general_issues'] and check['businesses'][0]['available']
    catalog=check['object_types'][0];fields={f['label']:f['key'] for f in catalog['fields']}
    props=check['entity_schemas'][catalog['key']]['properties']
    assert props[fields['温度']]['unit']=='℃' and 'unit' not in props[fields['振动']]
    assert m['id'] in check['object_material_ids']
    assert '振动：单位未明确' in check['data_plan'][0]['details']
    assert check['data_plan'][-1]['status']=='not_enabled'
    r=client.post('/api/instances',json={**compilation,'confirmed_configuration_hash':check['configuration_hash'],'confirm_configuration':True})
    assert r.status_code==201,r.text;release=r.json();enable(client,release)
    base={'release_id':release['id'],'object_type':catalog['key']}
    endpoint='/api/instances/'+release['instance_id']+'/objects'
    bad=client.post(endpoint,json={**base,'attributes':{fields['温度']:'wrong'}})
    assert bad.status_code==422
    good=client.post(endpoint,json={**base,'attributes':{fields['设备编号']:'公开测试对象',fields['温度']:23.5}})
    assert good.status_code==201,good.text
    with Session(engine()) as db:
        source=db.get(SetupMaterial,m['id']);source.archived=True;source.version+=1;db.commit()
    assert client.post('/api/instances/check',json=compilation).status_code==409

def test_origin_cannot_be_transplanted_or_fake_declared_from_manual_draft(client,monkeypatch):
    row,body,m,calls,preview=ready(client,monkeypatch)
    c=apply(client,row,body).json()['configuration']
    c['instanceObjectOrigins']['设备']['object']['fields'][1]['unit']='mm'
    rev=save(client,c)
    assert client.post('/api/instances/check',json={'revision':rev,'objects':c['instanceObjects']}).status_code==409
    # Explicitly keeping a human definition with no suggestion association is allowed.
    c['instanceObjectOrigins']={};rev=save(client,c)
    assert client.post('/api/instances/check',json={'revision':rev,'objects':c['instanceObjects']}).status_code==200

@pytest.mark.parametrize('response_content',['{"domain":"invalid-only"}','not-json',None])
def test_actual_gateway_preserves_paid_content_before_strict_schema_validation(client,monkeypatch,response_content):
    body,m=prepared(client,TEXT);body['contract_version']=2;row,preview=start(client,body)
    sent=[];base=httpx.Client
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':response_content}}],
            'usage':{'total_tokens':23}})
    monkeypatch.setattr('generic_agents.gateway.httpx.Client',lambda **kw:base(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(settings(),'dashscope_api_key',SecretStr('test-only-key'))
    monkeypatch.delenv('DASHSCOPE_API_KEY',raising=False)
    _synthetic_reservations(monkeypatch)
    process(row);record=client.get('/api/setup/understanding/'+row['id']).json()
    assert record['status']=='failed' and record['receipts'][0]['usage']['total_tokens']==23 and len(sent)==1
    with Session(engine()) as db:
        raw=db.get(SetupAnalysis,row['id']).data['raw_response']
        assert raw['content']==response_content and raw['hash']==digest({k:v for k,v in raw.items() if k!='hash'})
        call=db.scalar(select(CloudCall));assert call.charged_tokens==23
        assert call.data['request_hash']==preview['first_dispatch_messages_hash']

def test_new_generation_rules_change_blocks_dispatch_without_erasing_original_request(client,monkeypatch):
    body,m=prepared(client,TEXT);body['contract_version']=2;row,preview=start(client,body)
    calls=fake_v2(monkeypatch)
    monkeypatch.setattr(understanding,'OBJECT_TASK',understanding.OBJECT_TASK+'changed')
    process(row);record=client.get('/api/setup/understanding/'+row['id']).json()
    assert record['status']=='failed' and not calls and '发送规则' in record['error']
    with Session(engine()) as db:
        assert db.get(SetupAnalysis,row['id']).data['request']['generation_contract']==preview['generation_contract']

@pytest.mark.parametrize('change',['version','blocks'])
def test_generated_object_definition_cannot_activate_after_same_file_reparse(client,monkeypatch,change):
    row,body,m,calls,preview=ready(client,monkeypatch)
    c=apply(client,row,body,names=['异常复核']).json()['configuration'];flow=c['flows']['异常复核']
    flow['confirmedAt']='2026-10-11T12:00:00+08:00';flow['confirmedSignature']=json.dumps(signature_value(c,flow),ensure_ascii=False)
    c['automation']={'version':1,'policies':{'异常复核':{'rules':{'write':{'mode':'仅给出建议'}}}}}
    rev=save(client,c,review_automation=True);member=client.get('/api/instances/members').json()[0]
    compilation={'revision':rev,'objects':c['instanceObjects'],'role_bindings':{'值班人员':member['id']}}
    checked=client.post('/api/instances/check',json=compilation).json()
    release=client.post('/api/instances',json={**compilation,'confirmed_configuration_hash':checked['configuration_hash'],'confirm_configuration':True}).json()
    with Session(engine()) as db:
        material=db.get(SetupMaterial,m['id']);sha=material.sha256
        if change=='version':material.version+=1
        else:material.data={**material.data,'blocks':[{**b,'text':b['text']+'新解析版本'} for b in material.data['blocks']]}
        db.commit();assert material.sha256==sha
    instance=client.get('/api/instances').json()[0]
    result=client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
        'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True})
    assert result.status_code==409,result.text

def test_v2_start_retry_across_completed_status_recovers_same_request_no_second_job(client,monkeypatch):
    body,m=prepared(client,TEXT);body['contract_version']=2;fake_v2(monkeypatch)
    row,preview=start(client,body);process(row)
    repeat=client.post('/api/setup/understanding',json={**body,'preview_hash':preview['preview_hash'],'consent':True})
    assert repeat.status_code==202 and repeat.json()['id']==row['id'] and repeat.json()['status']=='review'
    recovered=client.get('/api/setup/understanding/by-preview/'+preview['preview_hash'])
    assert recovered.status_code==200 and recovered.json()['id']==row['id']
    assert len(client.get('/api/setup/understanding').json())==1
    assert client.get('/api/setup/understanding/by-preview/'+'0'*64).status_code==404

def test_paid_content_is_saved_after_mid_http_actor_revocation_without_applying(client,monkeypatch):
    body,m=prepared(client,TEXT);body['contract_version']=2;row,preview=start(client,body)
    sent=[];base=httpx.Client
    def handler(request):
        sent.append(True)
        with Session(engine()) as db:
            membership=db.scalar(select(Membership));membership.role='viewer';db.commit()
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"domain":"retained-paid"}'}}],
            'usage':{'total_tokens':19}})
    monkeypatch.setattr('generic_agents.gateway.httpx.Client',lambda **kw:base(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(settings(),'dashscope_api_key',SecretStr('test-only-key'))
    monkeypatch.delenv('DASHSCOPE_API_KEY',raising=False)
    _synthetic_reservations(monkeypatch)
    process(row)
    with Session(engine()) as db:
        stored=db.get(SetupAnalysis,row['id'])
        assert stored.data['raw_response']['content']=='{"domain":"retained-paid"}'
        assert stored.data['receipts'][0]['usage']['total_tokens']==19
        assert not stored.data.get('result') and len(sent)==1
    assert client.post('/api/setup/understanding/'+row['id']+'/retry',json={'version':1}).status_code==403


@pytest.mark.parametrize('invalid_type',[False,True])
def test_cached_paid_answer_reconciles_reserved_cost_locally_and_keeps_strict_schema(client,monkeypatch,invalid_type):
    row,body,m,calls,preview=ready(client,monkeypatch)
    with Session(engine()) as db:
        analysis=db.get(SetupAnalysis,row['id']);raw=copy.deepcopy(analysis.data['raw_response'])
        if invalid_type:
            raw['value']['objects'][0]['fields'][0]['required']='false'
            raw['content']=json.dumps(raw['value'],ensure_ascii=False)
            raw['hash']=digest({k:v for k,v in raw.items() if k!='hash'})
        call=db.get(CloudCall,raw['receipt']['call_id'])
        budget=db.scalar(select(Job).where(Job.workspace_id==call.workspace_id,
            Job.idempotency_key=='cloud-budget:'+call.data['day']))
        budget.data={**budget.data,'tokens':budget.data['tokens']-call.charged_tokens+call.reserved_tokens}
        call.status='reserved';call.charged_tokens=call.reserved_tokens
        call.data={k:v for k,v in call.data.items() if k not in {'receipt','finished_at','usage_unavailable'}}
        analysis.status='queued';analysis.data={**analysis.data,'raw_response':raw,
            'completed':[],'result':None,'result_hash':None,'inflight':0,'error':None}
        job=db.scalar(select(Job).where(Job.kind=='understand_setup',Job.data['analysis_id'].astext==row['id']))
        job.status='queued';job.lease_token=None;job.lease_until=None;db.commit()
    monkeypatch.setattr(understanding,'cloud_json',lambda *a,**kw:pytest.fail('cached recovery must not send HTTP'))
    process(row)
    result=client.get('/api/setup/understanding/'+row['id']).json()
    assert result['status']==('failed' if invalid_type else 'review')
    with Session(engine()) as db:
        call=db.get(CloudCall,raw['receipt']['call_id'])
        assert call.charged_tokens==10 and call.status==('failed' if invalid_type else 'succeeded')
        budget=db.scalar(select(Job).where(Job.kind=='cloud_budget'));assert budget.data['tokens']==10
        assert db.get(SetupAnalysis,row['id']).data['raw_response']==raw
        if invalid_type:assert db.get(SetupAnalysis,row['id']).data.get('result') is None


def test_v2_nonce_cannot_be_reused_for_different_authorized_content(client,monkeypatch):
    body,m=prepared(client,TEXT);body.update(contract_version=2,request_id=str(uuid4()))
    fake_v2(monkeypatch);row,preview=start(client,body);process(row)
    changed={**body,'instruction':'新增人工核对说明'}
    changed_preview=client.post('/api/setup/understanding/preview',json=changed).json()
    response=client.post('/api/setup/understanding',json={**changed,
        'preview_hash':changed_preview['preview_hash'],'consent':True})
    assert response.status_code==409 and '启动编号' in response.json()['detail']
    assert len(client.get('/api/setup/understanding').json())==1


def test_paid_transaction_failure_releases_call_lock_before_gateway_recovery(client,monkeypatch):
    from generic_agents.leases import LeaseLost
    body,m=prepared(client,TEXT);body['contract_version']=2;row,preview=start(client,body)
    base=httpx.Client
    monkeypatch.setattr('generic_agents.gateway.httpx.Client',lambda **kw:base(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
            'choices':[{'message':{'content':'not-json'}}],'usage':{'total_tokens':17}})),**kw))
    monkeypatch.setattr(settings(),'dashscope_api_key',SecretStr('test-only-key'))
    monkeypatch.delenv('DASHSCOPE_API_KEY',raising=False)
    _synthetic_reservations(monkeypatch)
    original=understanding.settle_paid_raw
    def lost_after_lock(*args):
        original(*args);raise LeaseLost('test lease failure before commit')
    monkeypatch.setattr(understanding,'settle_paid_raw',lost_after_lock)
    process(row)
    with Session(engine()) as db:
        analysis=db.get(SetupAnalysis,row['id']);call=db.scalar(select(CloudCall))
        assert analysis.data.get('raw_response') is None
        assert call.status=='failed' and call.charged_tokens==17
