"""Real PG and registered builtin calls, never pretend model intelligence."""
import copy,json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job,Task,Invocation,Capability,Memory,Membership
from generic_agents.worker import process_once
from generic_agents.instance_compiler import signature_value
from tests.test_vertical_slice import manifest,publish
from tests.test_instance_workspace import configuration,prepared,OBJECTS,complete


def enable(client,r):
    i=next(x for x in client.get('/api/instances').json() if x['id']==r['instance_id'])
    response=client.post('/api/instances/'+i['id']+'/activate',json={'version':i['version'],'release_id':r['id'],
        'business_ids':r['available_business_ids'],'confirm_workflow_scope':True})
    assert response.status_code==200,response.text;return response.json()


def tool(client,agent='data',operation='aggregate_values',bindings=None,**kwargs):
    return publish(client,manifest('tool',name='workflow_calculation',description='汇总业务数量',allowed_agent_roles=[agent],
        input_schema={'type':'object','required':['records','field','operation'],'additionalProperties':False,
            'properties':{'records':{'type':'array','items':{'type':'object'}},'field':{'type':'string'},'operation':{'enum':['mean','sum','count']}}},
        output_schema={'type':'object','required':['value','sample_count','provenance'],'properties':{'value':{'type':['number','null']},'sample_count':{'type':'integer'},'provenance':{'type':'array'}}},
        execution={'adapter':'builtin','handler':operation,'input_bindings':bindings or {}},specification={},**kwargs))


def conf(cap,agent='数据管理智能体',mode='自动运行',ending=False):
    c=configuration();f=c['flows']['资料核验'];s=f['steps'][0]
    s.update(agent=agent,role='',condition='',name='汇总业务数量',execution={'capability_id':cap['id'],'automation_action':'data',
        'arguments':{'field':'数量','operation':'mean'},'bindings':{'records':{'source':'object_records'}}})
    if ending:f['steps']=[s];s['nextId']='end'
    c['automation']['policies']['资料核验']['rules']['data']={'mode':mode}
    f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False);return c


def start(client,cap,goal_factory=None,**kwargs):
    release,_,check=prepared(client,conf(cap,**kwargs));assert release['available_business_ids'],check
    enable(client,release);kind=release['snapshot']['object_types'][1]
    objects=[]
    for name,value in [('公开数值甲',10),('公开数值乙',30)]:
        attrs={f['key']:name if f['label']=='标题' else value for f in kind['fields']}
        r=client.post('/api/instances/'+release['instance_id']+'/objects',json={'release_id':release['id'],'object_type':kind['key'],'attributes':attrs});assert r.status_code==201,r.text;objects.append(r.json())
    payload={'release_id':release['id'],'business_id':release['available_business_ids'][0],
        'entity_ids':[o['id'] for o in objects],'title':'核对公开登记数量','request_id':str(uuid4())}
    if goal_factory:payload['goal_spec']=goal_factory(release,objects)
    r=client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json=payload);assert r.status_code==201,r.text
    return release,r.json(),objects


def finish_job(client,t):
    job=t['workflow_runtime']['automatic']['job_id'];assert process_once(job,deliver=False)
    return client.get('/api/workspace/tasks/'+t['id']).json()


def run(client,t,retry=False,request_id=None):
    return client.post('/api/workspace/tasks/'+t['id']+'/run-ability',json={'version':t['version'],'step_id':t['workflow_runtime']['current_step_id'],
        'request_id':request_id or str(uuid4()),'retry':retry})


def test_automatic_object_binding_and_manual_result_are_durable(client):
    cap=tool(client);release,t,objects=start(client,cap)
    assert t['status']=='workflow_queued' and complete(client,t).status_code==409
    t=finish_job(client,t);assert t['status']=='workflow_waiting',t
    receipt=t['workflow_runtime']['receipts'][0];assert receipt['output']['value']==20 and receipt['output']['sample_count']==2
    assert receipt['capability_hash']==cap['content_hash'] and receipt['input_snapshot']['records'][0]['attributes']['数量']==10
    assert receipt['invocation_id'] and t['workflow_runtime']['current_step_id']=='record'
    final=complete(client,t);assert final.status_code==200,final.text;t=final.json()
    with Session(engine()) as db:
        assert db.get(Memory,t['workflow_runtime']['data_receipt_id']).status=='candidate'
        assert len(db.scalars(select(Invocation)).all())==1
    assert {x['id']:(x['version'],x['attributes']) for x in client.get('/api/instances/'+release['instance_id']+'/objects').json()}=={x['id']:(x['version'],x['attributes']) for x in objects}


import pytest
@pytest.mark.parametrize('agent,role',[('业务智能体','business'),('多模感知智能体','perception'),('数据管理智能体','data')])
def test_all_roles_use_registered_abilities_and_automatic_end(client,agent,role):
    cap=tool(client,role);r,t,_=start(client,cap,agent=agent,ending=True);t=finish_job(client,t)
    assert t['status']=='workflow_completed' and t['workflow_runtime']['receipts'][0]['role']==agent
    assert not t['workflow_runtime']['receipts'][0]['entity_state_updated']


def test_confirm_before_run_and_duplicate_request_do_not_repeat_calls(client):
    cap=tool(client);r,t,_=start(client,cap,mode='执行前确认')
    assert t['status']=='workflow_waiting' and not t['workflow_runtime'].get('automatic')
    key=str(uuid4());body={'version':t['version'],'step_id':'check','request_id':key,'retry':False}
    response=client.post('/api/workspace/tasks/'+t['id']+'/run-ability',json=body);assert response.status_code==200,response.text;t=response.json()
    assert client.post('/api/workspace/tasks/'+t['id']+'/run-ability',json=body).json()['id']==t['id']
    t=finish_job(client,t)
    assert client.post('/api/workspace/tasks/'+t['id']+'/run-ability',json=body).status_code==200
    with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==1


def test_pause_and_revoked_actor_stop_real_queued_execution(client,account):
    cap=tool(client);r,t,_=start(client,cap)
    i=client.get('/api/instances').json()[0]
    assert client.post('/api/instances/'+i['id']+'/pause',json={'version':i['version']}).status_code==200
    t=finish_job(client,t);assert t['status']=='workflow_blocked'
    assert '暂停' in t['workflow_runtime']['automatic']['error']
    with Session(engine()) as db:assert not db.scalars(select(Invocation)).all()
    assert enable(client,r)['status']=='partially_active'
    response=run(client,t,True);assert response.status_code==200,response.text;t=response.json()
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='viewer';db.commit()
    assert process_once(t['workflow_runtime']['automatic']['job_id'],deliver=False)
    with Session(engine()) as db:
        assert db.get(Task,t['id']).status=='workflow_blocked';assert not db.scalars(select(Invocation)).all()


def test_cancelled_task_does_not_accept_late_call_or_resume(client,monkeypatch):
    cap=tool(client);r,t,_=start(client,cap)
    import generic_agents.gateway as gateway
    original=gateway.invoke
    def cancel_after_call(db,p,*args,**kwargs):
        call=original(db,p,*args,**kwargs)
        row=db.get(Task,t['id']);row.status='workflow_cancelled';row.data={**row.data,'cancellation':{'reason':'核对中取消'}};row.version+=1;db.commit();return call
    monkeypatch.setattr(gateway,'invoke',cancel_after_call)
    t=finish_job(client,t);assert t['status']=='workflow_cancelled' and t['workflow_runtime']['receipts']==[]
    assert run(client,t,True).status_code==409
    with Session(engine()) as db:assert db.scalar(select(Invocation)).status=='succeeded'


def test_failure_explicit_retry_preserves_first_call_and_refuses_human_completion(client,monkeypatch):
    cap=tool(client);r,t,_=start(client,cap)
    import generic_agents.gateway as gateway
    original=gateway.invoke
    def fail_once(db,p,*args,**kwargs):
        call=original(db,p,*args,**kwargs);call.status='failed';call.data={**call.data,'error':'明确失败用于恢复核查'};db.commit();return call
    monkeypatch.setattr(gateway,'invoke',fail_once)
    t=finish_job(client,t);assert t['status']=='workflow_failed'
    assert complete(client,t).status_code==409 and run(client,t).status_code==409
    monkeypatch.setattr(gateway,'invoke',original)
    response=run(client,t,True);assert response.status_code==200,response.text;t=finish_job(client,response.json())
    assert t['status']=='workflow_waiting'
    with Session(engine()) as db:
        calls=db.scalars(select(Invocation).order_by(Invocation.created_at)).all();assert [x.status for x in calls]==['failed','succeeded']


def test_suggestion_permission_and_changed_capability_do_not_run(client):
    cap=tool(client);release,_,check=prepared(client,conf(cap,mode='仅给出建议'))
    assert not release['available_business_ids'] and any('仅给出建议' in x for b in check['businesses'] for x in b['issues'])
    r,t,_=start(client,cap)
    with Session(engine()) as db:
        c=db.get(Capability,cap['id']);c.data={**c.data,'description':'已被篡改'};db.commit()
    t=finish_job(client,t);assert t['status']=='workflow_blocked'
    with Session(engine()) as db:assert not db.scalars(select(Invocation)).all()


def test_worker_receipt_recovery_reuses_completed_invocation(client,monkeypatch):
    cap=tool(client);r,t,_=start(client,cap)
    import generic_agents.gateway as gateway
    from generic_agents.leases import LeaseLost
    from generic_agents.models import now
    from datetime import timedelta
    original=gateway.invoke
    def lost_after_saved_call(db,p,*args,**kwargs):
        original(db,p,*args,**kwargs);raise LeaseLost()
    monkeypatch.setattr(gateway,'invoke',lost_after_saved_call)
    job=t['workflow_runtime']['automatic']['job_id'];assert process_once(job,deliver=False)
    with Session(engine()) as db:
        assert db.get(Task,t['id']).status=='workflow_running' and db.scalar(select(Invocation)).status=='succeeded'
        row=db.get(Job,job);row.lease_until=now()-timedelta(seconds=1);db.commit()
    monkeypatch.setattr(gateway,'invoke',original);assert process_once(job,deliver=False)
    t=client.get('/api/workspace/tasks/'+t['id']).json();assert t['status']=='workflow_waiting'
    with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==1


def test_cancel_api_keeps_note_and_stops_queued_job(client):
    cap=tool(client);r,t,_=start(client,cap)
    response=client.post('/api/workspace/tasks/'+t['id']+'/cancel',json={'version':t['version'],'reason':'操作人员取消公开验证','retained_note':'保留未提交的核对记录'})
    assert response.status_code==200,response.text
    final=finish_job(client,t);assert final['status']=='workflow_cancelled' and final['cancellation']['retained_note']=='保留未提交的核对记录'
    with Session(engine()) as db:assert not db.scalars(select(Invocation)).all()


def test_missing_previous_result_is_visible_and_never_guessed(client):
    cap=tool(client);c=conf(cap);f=c['flows']['资料核验'];s=copy.deepcopy(f['steps'][0]);s.update(id='next',name='再次汇总')
    f['steps'].insert(1,s);s['execution']['bindings']['records']={'source':'previous_output','step_id':'check','field':'does_not_exist'}
    f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    r,_,check=prepared(client,c)
    # The referenced step exists, but runtime must still reject a missing actual field.
    enable(client,r);kind=r['snapshot']['object_types'][1]
    o=client.post('/api/instances/'+r['instance_id']+'/objects',json={'release_id':r['id'],'object_type':kind['key'],
        'attributes':{f['key']:'公开项' if f['label']=='标题' else 10 for f in kind['fields']}}).json()
    t=client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json={'release_id':r['id'],'business_id':r['available_business_ids'][0],
        'entity_ids':[o['id']],'title':'检查前序结果缺失','request_id':str(uuid4())}).json();t=finish_job(client,t)
    assert t['status']=='workflow_blocked' and t['workflow_runtime']['receipts'][0]['output']['value']==10
    assert len(t['workflow_runtime']['receipts'])==1 and '前序能力' in t['workflow_runtime']['automatic']['error']
    with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==1


def test_side_effect_and_wrong_agent_cannot_be_enabled(client):
    cap=tool(client)
    c=conf(cap,agent='业务智能体');r,_,check=prepared(client,c)
    assert not r['available_business_ids'] and any('角色' in e for b in check['businesses'] for e in b['issues'])
    with Session(engine()) as db:
        from generic_agents.contracts import digest
        row=db.get(Capability,cap['id']);row.data={**row.data,'execution':{**row.data['execution'],'side_effect':True}}
        row.content_hash=digest(row.data);row.validated_hash=row.content_hash;db.commit()
    r,_,check=prepared(client,conf(cap));assert not r['available_business_ids']
    assert any('设备动作或外部写入' in e for b in check['businesses'] for e in b['issues'])


def test_registered_server_binding_cannot_be_replaced_by_constant_records(client):
    cap=tool(client,bindings={'records':'archive_records'});c=conf(cap)
    spec=c['flows']['资料核验']['steps'][0]['execution'];spec['bindings'].pop('records');spec['arguments']['records']=[{'attributes':{'数量':999}}]
    f=c['flows']['资料核验'];f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    r,_,check=prepared(client,c);assert not r['available_business_ids']
    assert any('不能被参数' in e for b in check['businesses'] for e in b['issues'])
    options=client.get('/api/workspace/ability-options').json();assert options[0]['fixed_bindings']['records']=='object_records'
    r,t,_=start(client,cap);t=finish_job(client,t);assert t['workflow_runtime']['receipts'][0]['output']['value']==20


def test_queued_task_uses_pinned_objects_not_current_unconfirmed_values(client):
    cap=tool(client);r,t,objects=start(client,cap)
    from generic_agents.models import Entity
    with Session(engine()) as db:
        obj=db.get(Entity,objects[0]['id']);field=next(f['key'] for f in r['snapshot']['object_types'][1]['fields'] if f['label']=='数量')
        obj.data={**obj.data,field:999};obj.version+=1;db.commit()
    t=finish_job(client,t);receipt=t['workflow_runtime']['receipts'][0]
    assert receipt['output']['value']==20 and receipt['object_snapshots'][0]['version']==1


def test_auto_branch_without_typed_boolean_result_is_blocked(client):
    cap=tool(client);c=conf(cap);f=c['flows']['资料核验'];f['steps'][0]['condition']='库存是否异常';f['steps'][0]['elseId']='end'
    f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    r,_,check=prepared(client,c);assert not r['available_business_ids']
    assert any('布尔字段' in e for b in check['businesses'] for e in b['issues'])


def test_old_human_scope_confirmation_cannot_enable_automatic_business(client):
    cap=tool(client);r,_,_=prepared(client,conf(cap));i=client.get('/api/instances').json()[0]
    response=client.post('/api/instances/'+i['id']+'/activate',json={'version':i['version'],'release_id':r['id'],
        'business_ids':r['available_business_ids'],'confirm_human_workflow_scope':True})
    assert response.status_code==422


def test_readonly_tool_cannot_masquerade_as_business_write(client):
    cap=tool(client);c=conf(cap);f=c['flows']['资料核验'];f['steps'][0]['execution']['automation_action']='write'
    c['automation']['policies']['资料核验']['rules']['write']={'mode':'自动运行'};f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    r,_,check=prepared(client,c);assert not r['available_business_ids']
    assert any('不能冒充该动作' in e for b in check['businesses'] for e in b['issues'])
