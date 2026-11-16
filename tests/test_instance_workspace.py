"""Actual PG compilation, role gates, immutable releases and workflow results."""
import copy,json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Membership,Memory,Task,Entity,InstanceRelease
from generic_agents.instance_compiler import signature_value
from tests.test_setup import config

OBJECTS=[{'name':'观察场景','fields':[{'label':'名称','kind':'文本','required':True},{'label':'登记日期','kind':'日期','required':False}]},
         {'name':'档案条目','fields':[{'label':'标题','kind':'文本','required':True},{'label':'数量','kind':'整数','required':False}]}]

def configuration(extra=False,branch=False):
    c=config();c['businesses']=['资料核验'];c['instanceProfile']['goal']='核对记录并保留原文依据'
    flow={'trigger':'人工启动','output':'核验结论','steps':[
        {'id':'check','name':'核对资料','purpose':'对照对象记录核对','agent':'人工确认','role':'核验负责人','output':'核对意见','sourceId':'','citation':'人工核对规则','nextId':'auto','condition':'资料是否齐全' if branch else '', 'elseId':'end'},
        {'id':'record','name':'保存结论','purpose':'记录处理结果','agent':'人工确认','role':'核验负责人','output':'核验结论','sourceId':'','citation':'人工核对规则','nextId':'end','condition':'','elseId':'end'}],
        'confirmedAt':'2026-10-08T12:00:00+00:00'}
    flow['confirmedSignature']=json.dumps(signature_value(c,flow),ensure_ascii=False)
    c['flows']={'资料核验':flow};c['automation']={'version':1,'policies':{'资料核验':{'rules':{'write':{'mode':'仅给出建议'}}}}}
    if extra:
        c['businesses'].append('自动分析');c['flows']['自动分析']=copy.deepcopy(flow);c['flows']['自动分析']['steps'][0]['agent']='多模感知智能体'
        f=c['flows']['自动分析'];f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
        c['automation']['policies']['自动分析']={'rules':{'write':{'mode':'仅给出建议'}}}
    return c

def save(client,c,*,review_automation=False):
    revision=client.get('/api/setup/draft').json()['revision']
    r=client.put('/api/setup/draft',json={'revision':revision,'configuration':c});assert r.status_code==200,r.text
    view=r.json()
    if review_automation:
        # Fixture 需要已确认策略时走真实 API；不得伪造服务器确认元数据。
        for row in view.get('automation_review',{}).get('businesses',[]):
            if row['status']!='needs_review':continue
            confirmed=client.post('/api/setup/automation/review',json={'revision':view['revision'],
                'business_id':row['business_id'],'request_id':str(uuid4())})
            assert confirmed.status_code==200,confirmed.text
            view=confirmed.json()
    return view['revision']

def prepared(client,c=None,instance_id=None):
    c=c or configuration();revision=save(client,c,review_automation=True);member=client.get('/api/instances/members').json()[0]
    body={'revision':revision,'objects':OBJECTS,'role_bindings':{'核验负责人':member['id']}}
    check=client.post('/api/instances/check',json=body);assert check.status_code==200,check.text
    r=client.post('/api/instances',json={**body,'confirmed_configuration_hash':check.json()['configuration_hash'],
        'confirm_configuration':True,'instance_id':instance_id});assert r.status_code==201,r.text
    return r.json(),body,check.json()

def enable(client,release):
    instance=next(x for x in client.get('/api/instances').json() if x['id']==release['instance_id'])
    r=client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],'release_id':release['id'],
        'business_ids':release['available_business_ids'],'confirm_human_workflow_scope':True});assert r.status_code==200,r.text;return r.json()

def object_row(client,r,index=0):
    kind=r['snapshot']['object_types'][index];values={kind['fields'][0]['key']:'公开资料场景' if index==0 else '公开测试条目'}
    response=client.post('/api/instances/'+r['instance_id']+'/objects',json={'release_id':r['id'],'object_type':kind['key'],'attributes':values})
    assert response.status_code==201,response.text;return response.json()

def task_row(client,r,obj):
    body={'release_id':r['id'],'business_id':r['available_business_ids'][0],'entity_ids':[obj['id']],
        'title':'核对公开资料场景','request_id':str(uuid4())}
    response=client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=body);assert response.status_code==201,response.text
    return response.json(),body

def complete(client,t,note='已对照对象档案核对并保存依据',**kwargs):
    return client.post('/api/workspace/tasks/'+t['id']+'/complete-step',json={'version':t['version'],
        'step_id':t['workflow_runtime']['current_step_id'],'note':note,'request_id':str(uuid4()),**kwargs})

def test_generated_package_partial_activation_objects_and_complete_workflow(client):
    r,body,check=prepared(client,configuration(extra=True));assert len(r['available_business_ids'])==1
    assert '自动节点' in ''.join(check['businesses'][1]['issues']);i=enable(client,r)
    assert i['status']=='partially_active';obj=object_row(client,r);other=object_row(client,r,1)
    task,creation=task_row(client,r,obj)
    assert task['title']==creation['title']
    duplicate=client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=creation);assert duplicate.json()['id']==task['id']
    response=client.put('/api/workspace/tasks/'+task['id']+'/note',json={'version':task['version'],'step_id':'check','note':'已核对名称，结论尚在整理'})
    assert response.status_code==200,response.text;task=response.json()
    fetched=client.get('/api/workspace/tasks/'+task['id']).json();assert fetched['workflow_runtime']['drafts']['check']['note']=='已核对名称，结论尚在整理'
    response=complete(client,task);assert response.status_code==200,response.text;task=response.json()
    assert task['workflow_runtime']['current_step_id']=='record'
    response=complete(client,task);assert response.status_code==200,response.text;task=response.json();assert task['status']=='workflow_completed'
    with Session(engine()) as db:
        m=db.get(Memory,task['workflow_runtime']['data_receipt_id']);assert m.status=='candidate' and not m.data['entity_state_updated']
        assert db.get(Entity,obj['id']).version==1 and db.get(Entity,other['id']).version==1

def test_branch_needs_explicit_decision_and_retries_are_idempotent(client):
    r,_,_=prepared(client,configuration(branch=True));enable(client,r);task,_=task_row(client,r,object_row(client,r))
    assert complete(client,task).status_code==422
    key=str(uuid4());response=complete(client,task,branch=False,request_id=key);assert response.status_code==200,response.text
    assert response.json()['status']=='workflow_completed'
    again=complete(client,task,branch=False,request_id=key);assert again.status_code==200 and len(again.json()['workflow_runtime']['receipts'])==1
    assert complete(client,task,note='更换原结果',branch=False,request_id=key).status_code==409

def test_stale_confirmation_and_forged_resources_cannot_enable_automatic_flow(client):
    c=configuration(extra=True);c['flows']['资料核验']['steps'][0]['output']='未经重新确认的输出'
    r,_,check=prepared(client,c);assert r['available_business_ids']==[]
    assert any('重新确认' in x for x in check['businesses'][0]['issues'])
    instance=client.get('/api/instances').json()[0]
    response=client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],'release_id':r['id'],
        'business_ids':[check['businesses'][0]['id']],'confirm_human_workflow_scope':True});assert response.status_code==409

def test_role_revocation_and_changed_snapshot_block_activation(client):
    r,body,check=prepared(client)
    with Session(engine()) as db:member=db.scalar(select(Membership));member.role='viewer';db.commit()
    assert client.post('/api/instances',json={**body,'confirmed_configuration_hash':check['configuration_hash'],'confirm_configuration':True}).status_code==403

def test_new_release_does_not_overwrite_old_task_and_pause_allows_finish(client):
    r,_,_=prepared(client);enable(client,r);task,_=task_row(client,r,object_row(client,r))
    c=configuration();c['flows']['资料核验']['steps'][0]['name']='新版本的核对步骤'
    f=c['flows']['资料核验'];f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    new,_,_=prepared(client,c,r['instance_id']);assert new['release_number']==2;instance=enable(client,new)
    detail=client.get('/api/workspace/tasks/'+task['id']).json();assert detail['business']['flow']['steps'][0]['name']=='核对资料'
    response=client.post('/api/instances/'+instance['id']+'/pause',json={'version':instance['version']});assert response.status_code==200
    assert complete(client,task).status_code==200

def test_object_field_types_dates_and_repeat_reference_are_validated(client):
    r,_,_=prepared(client);enable(client,r);kind=r['snapshot']['object_types'][0]
    payload={'release_id':r['id'],'object_type':kind['key'],'reference':'public-001',
        'attributes':{kind['fields'][0]['key']:'公开日期记录',kind['fields'][1]['key']:'2026-02-31'}}
    path='/api/instances/'+r['instance_id']+'/objects'
    assert client.post(path,json=payload).status_code==422
    payload['attributes'][kind['fields'][1]['key']]='2026-02-28';assert client.post(path,json=payload).status_code==201
    assert client.post(path,json=payload).status_code==409


def cited_configuration(client):
    from tests.test_setup import upload
    from generic_agents.worker import process_once
    row=upload(client,'公开核验规则\n核对名称与数量，记录依据和结论。'.encode(),'公开核验规则.txt')
    assert process_once(row['job_id'])
    row=client.get('/api/setup/materials/'+row['id']).json()
    c=configuration();c['materials']=[row];f=c['flows']['资料核验'];s=f['steps'][0]
    s.update(sourceId=row['id'],citation='核对名称与数量，记录依据和结论。',citations=[{'material_id':row['id'],'block_id':row['blocks'][1]['id'],'quote':row['blocks'][1]['text']}])
    f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    return c,row


def test_original_integrity_fences_completion_preserves_notes_and_recovers(client):
    from generic_agents.models import SetupMaterial,Evidence
    from generic_agents.config import settings
    c,source=cited_configuration(client);r,_,_=prepared(client,c);enable(client,r)
    task,_=task_row(client,r,object_row(client,r))
    saved=client.put('/api/workspace/tasks/'+task['id']+'/note',json={'version':task['version'],'step_id':'check','note':'已填写但还未提交'})
    assert saved.status_code==200;task=saved.json()
    with Session(engine()) as db:
        mat=db.get(SetupMaterial,source['id']);raw=db.get(Evidence,mat.evidence_id);path=settings().storage_dir/raw.storage_key
        original=path.read_bytes();path.write_bytes(original+b'tampered')
    try:
        assert complete(client,task).status_code==409
        current=client.get('/api/workspace/tasks/'+task['id']).json()
        assert current['version']==task['version'] and not current['workflow_runtime']['receipts']
        assert current['workflow_runtime']['drafts']['check']['note']=='已填写但还未提交'
    finally:path.write_bytes(original)
    done=complete(client,task);assert done.status_code==200,done.text
    assert done.json()['workflow_runtime']['receipts'][0]['material_snapshots'][0]['hash']==source['hash']


def test_invalid_source_quote_and_malformed_config_are_rejected(client):
    c,row=cited_configuration(client);f=c['flows']['资料核验'];f['steps'][0]['citations'][0]['quote']='原文不存在的句子'
    f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    r,_,check=prepared(client,c);assert not r['available_business_ids'] and any('引用已失效' in x for x in check['businesses'][0]['issues'])
    c['flows']['资料核验']['steps'][0]['condition']={}
    rev=client.get('/api/setup/draft').json()['revision']
    assert client.put('/api/setup/draft',json={'revision':rev,'configuration':c}).status_code==422


def test_blank_required_object_text_is_rejected_for_valid_configuration(client):
    r,_,check=prepared(client)
    assert r['available_business_ids'] and not check['businesses'][0]['issues']
    enable(client,r);kind=r['snapshot']['object_types'][0]
    for blank in ['', '  ']:
        assert client.post('/api/instances/'+r['instance_id']+'/objects',json={'release_id':r['id'],'object_type':kind['key'],
            'attributes':{kind['fields'][0]['key']:blank}}).status_code==422


def test_corrected_source_quote_requires_explicit_confirmation_of_saved_server_flow(client):
    from datetime import datetime,timezone
    c,material=cited_configuration(client)
    flow=c['flows']['资料核验'];flow['steps'][0]['citations'][0]['quote']='原文不存在的句子'
    flow['confirmedSignature']=json.dumps(signature_value(c,flow),ensure_ascii=False)
    blocked,_,check=prepared(client,c)
    assert not blocked['available_business_ids'] and any('引用已失效' in issue for issue in check['businesses'][0]['issues'])
    prior=client.get('/api/setup/draft').json()
    corrected=copy.deepcopy(prior['configuration'])
    corrected['flows']['资料核验']['steps'][0]['citations'][0]['quote']=material['blocks'][1]['text']
    # Keep the old approval deliberately: editing the quote cannot inherit it.
    response=client.put('/api/setup/draft',json={'revision':prior['revision'],'configuration':corrected})
    assert response.status_code==200,response.text
    saved=response.json();saved_flow=saved['configuration']['flows']['资料核验']
    assert saved_flow['steps'][0]['citations'][0]['quote']==material['blocks'][1]['text']
    assert saved_flow['confirmedSignature'] is None and saved_flow['confirmedAt'] is None
    member=client.get('/api/instances/members').json()[0]
    check=client.post('/api/instances/check',json={'revision':saved['revision'],'objects':OBJECTS,
        'role_bindings':{'核验负责人':member['id']}})
    assert check.status_code==200,check.text
    assert not check.json()['businesses'][0]['available']
    assert any('重新确认' in issue for issue in check.json()['businesses'][0]['issues'])
    # Explicit human confirmation uses the complete normalized server value,
    # including retained provenance; automation review remains a separate API.
    latest=client.get('/api/setup/draft').json()['configuration']
    confirmed=copy.deepcopy(latest);flow=confirmed['flows']['资料核验']
    flow['confirmedAt']=datetime.now(timezone.utc).isoformat()
    flow['confirmedSignature']=json.dumps(signature_value(confirmed,flow),ensure_ascii=False)
    recovered,_,check=prepared(client,confirmed)
    assert recovered['available_business_ids'] and not check['businesses'][0]['issues']
    enable(client,recovered)
    assert object_row(client,recovered)['id']


def test_branch_draft_and_cas_do_not_lose_human_input(client):
    r,_,_=prepared(client,configuration(branch=True));enable(client,r);task,_=task_row(client,r,object_row(client,r))
    path='/api/workspace/tasks/'+task['id']+'/note'
    payload={'version':task['version'],'step_id':'check','note':'依据不齐全，已核对','branch':False}
    saved=client.put(path,json=payload);assert saved.status_code==200
    assert client.put(path,json={**payload,'note':'过期页面输入'}).status_code==409
    detail=client.get('/api/workspace/tasks/'+task['id']).json()
    assert detail['workflow_runtime']['drafts']['check']['branch'] is False
    assert detail['workflow_runtime']['drafts']['check']['note']==payload['note']


def test_interrupted_object_request_is_idempotent_and_may_not_change_fields(client):
    r,_,_=prepared(client);enable(client,r);kind=r['snapshot']['object_types'][0]
    payload={'release_id':r['id'],'object_type':kind['key'],'attributes':{kind['fields'][0]['key']:'公开幂等对象'},'request_id':str(uuid4())}
    path='/api/instances/'+r['instance_id']+'/objects'
    first=client.post(path,json=payload);again=client.post(path,json=payload)
    assert first.status_code==again.status_code==201 and first.json()['id']==again.json()['id']
    payload['attributes'][kind['fields'][0]['key']]='更换原输入'
    assert client.post(path,json=payload).status_code==409
    assert len(client.get(path).json())==1


def test_cancel_preserves_input_and_cannot_complete(client):
    r,_,_=prepared(client);enable(client,r);task,_=task_row(client,r,object_row(client,r))
    response=client.post('/api/workspace/tasks/'+task['id']+'/cancel',json={'version':task['version'],'reason':'重复安排，保留档案','retained_note':'取消前已核对的说明'})
    assert response.status_code==200 and response.json()['status']=='workflow_cancelled'
    assert response.json()['cancellation']['retained_note']=='取消前已核对的说明'
    assert complete(client,response.json()).status_code==409
    assert client.get('/api/workspace/tasks/'+task['id']).json()['cancellation']['reason']=='重复安排，保留档案'
