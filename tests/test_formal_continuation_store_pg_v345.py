"""Real PostgreSQL and ASGI workflow; provenance inputs are an explicit test double."""
import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.security import password_hash
from generic_agents.contracts import digest
from generic_agents import formal_continuation_store_v345 as store

def uid():return str(uuid4())

@pytest.fixture
def case(client,account,monkeypatch):
    workspace=account[2];owner=client.get('/api/me').json()['user_id'];secrets=[]
    with Session(engine()) as db:
        cap=m.Capability(workspace_id=workspace,namespace='formal',name='test',release_version='1',kind='domain_package',content_hash='a'*64,data={});db.add(cap);db.flush()
        task=m.Task(workspace_id=workspace,owner_id=owner,package_id=cap.id,title='模拟任务',status='workflow_waiting',data={'graph_runtime':{'unmodified':True}});db.add(task)
        for role in ('reviewer','clerk','chief'):
            name='sim-'+uid();password='pw-'+uid();user=m.User(username=name,password_hash=password_hash(password));db.add(user);db.flush();db.add(m.Membership(workspace_id=workspace,user_id=user.id,role='operator'));secrets.append((role,name,password,user.id))
        db.commit();task_id=task.id
    identities={r:i for r,_,_,i in secrets};advice,execution=uid(),uid()
    context={'available':True,'all_results_reviewed':True,'different_generation':True,'review':{'role':'复核','person':'reviewer','available':True},
      'steps':[{'name':'登记','role':'经办','person':'clerk','output':'结果','available':True},{'name':'审核','role':'主管','person':'chief','output':'意见','available':True}],
      'results':[{'execution_id':execution,'reviewed':True,'current_data':True,'simulation_only':True,'test_only':True}],
      'advice_generation':2,'current_generation':3}
    source={'schema':'test-source','workspace_id':workspace,'task_id':task_id,'advice_id':advice,'execution_id':execution,'context':context,'context_hash':digest(context)}
    definition={'steps':[{'key':'review','name':'复核建议','role':'复核','output':'意见','actor_id':identities['reviewer']},
      {'key':'first','name':'登记','role':'经办','output':'结果','actor_id':identities['clerk']},
      {'key':'second','name':'审核','role':'主管','output':'意见','actor_id':identities['chief']}]}
    live={'source':source,'definition':definition}
    def snapshot(db,p,task,advice_id,execution_id,*,lock_sources=False):
        assert task.id==task_id and advice_id==advice and execution_id==execution
        return copy.deepcopy(live['source']),copy.deepcopy(live['definition'])
    monkeypatch.setattr(store,'_snapshot',snapshot)
    sessions={}
    for role,name,password,_ in secrets:
        session=TestClient(app);session.__enter__();r=session.post('/api/login',json={'username':name,'password':password});assert r.status_code==200
        session.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':workspace});sessions[role]=session
    yield {'client':client,'sessions':sessions,'task':task_id,'advice':advice,'execution':execution,'live':live,'owner':owner,
      'base':f'/api/workspace/tasks/{task_id}/business-advice/preparations/{advice}/formal-continuation'}
    for session in sessions.values():session.__exit__(None,None,None)

def draft(t):
    body={'request_id':uid(),'expected_task_version':1,'execution_id':t['execution'],'context_hash':t['live']['source']['context_hash'],
          'acknowledge_cross_version':True,'reason':'确认旧建议与新资料分别核对'}
    r=t['client'].post(t['base']+'/preview',json=body);assert r.status_code==200,r.text
    return {**body,'preview_hash':r.json()['preview_hash']}

def created(t):
    body=draft(t);r=t['client'].post(t['base'],json=body);assert r.status_code==201,r.text
    return body,r.json()

def command(t,plan,op,actor,version,**extra):
    body={'request_id':uid(),'expected_version':version,'reason':'模拟技术办理',**extra}
    r=actor.post(t['base']+'/'+plan+'/'+op,json=body);return body,r

def test_full_ordered_manual_flow_replay_and_recovery(case):
    t=case;body,first=created(t);plan=first['plan_id']
    r=t['client'].post(t['base'],json=body);assert r.status_code==201 and r.json()==first
    r=t['client'].get(t['base']+'/operations/'+body['request_id']);assert r.status_code==200 and r.json()['receipt']==first
    changed={**body,'reason':'另一个正文'};assert t['client'].post(t['base'],json=changed).status_code==409
    view=t['client'].get(t['base']+'/'+plan);assert view.status_code==200 and view.json()['permissions']['can_activate']
    activation,r=command(t,plan,'activate',t['client'],1);assert r.status_code==200,r.text
    assert t['client'].post(t['base']+'/'+plan+'/activate',json=activation).json()==r.json()
    _,bad=command(t,plan,'complete-step',t['client'],2,step_key='review',result='不能代签');assert bad.status_code==409
    version=2
    for key,actor in [('review','reviewer'),('first','clerk'),('second','chief')]:
        _,r=command(t,plan,'complete-step',t['sessions'][actor],version,step_key=key,result='已完成 '+key)
        assert r.status_code==200,r.text;version+=1
    assert r.json()['state']['phase']=='awaiting_acceptance'
    _,r=command(t,plan,'return-step',t['sessions']['chief'],version,step_key='second');assert r.status_code==200 and r.json()['state']['phase']=='in_progress';version+=1
    _,r=command(t,plan,'complete-step',t['sessions']['chief'],version,step_key='second',result='已完成 second');assert r.status_code==200
    view=t['client'].get(t['base']+'/'+plan).json();assert not view['goal_completed'] and not view['acceptance_available']
    assert [s['result'] for s in view['steps']]==['已完成 review','已完成 first','已完成 second']
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);assert task.status=='workflow_waiting' and task.data['graph_runtime']=={'unmodified':True}

def test_version_drift_blocks_action_but_not_withdrawal(case):
    t=case;_,r=created(t);plan=r['plan_id'];t['live']['source']['context_hash']='b'*64
    view=t['client'].get(t['base']+'/'+plan).json();assert not view['current_sources_verified'] and not view['permissions']['can_activate']
    _,blocked=command(t,plan,'activate',t['client'],1);assert blocked.status_code==409
    _,cancel=command(t,plan,'withdraw',t['client'],1);assert cancel.status_code==200 and cancel.json()['state']['phase']=='withdrawn'

def test_return_keeps_original_history_and_respects_assignee(case):
    t=case;_,r=created(t);plan=r['plan_id'];_,r=command(t,plan,'activate',t['client'],1);assert r.status_code==200
    _,r=command(t,plan,'complete-step',t['sessions']['reviewer'],2,step_key='review',result='原意见');assert r.status_code==200
    _,r=command(t,plan,'return-step',t['sessions']['clerk'],3,step_key='first');assert r.status_code==200 and r.json()['state']['next_step']==0
    _,r=command(t,plan,'complete-step',t['sessions']['reviewer'],4,step_key='review',result='更正意见');assert r.status_code==200
    with Session(engine()) as db:
        row=db.get(m.Plan,plan);events=list(db.scalars(select(m.Audit).where(m.Audit.object_id==plan).order_by(m.Audit.created_at)))
        assert len(row.data['operations'])==5 and any(x.data['request'].get('result')=='原意见' for x in events)

def test_audit_or_definition_tampering_blocks_read(case):
    t=case;_,r=created(t);plan=r['plan_id']
    with Session(engine()) as db:
        row=db.get(m.Plan,plan);data=copy.deepcopy(row.data);data['definition']['steps'][0]['actor_id']=t['owner'];data['definition_hash']=digest(data['definition']);row.data=data;db.commit()
    assert t['client'].get(t['base']+'/'+plan).status_code==409

def test_cross_version_requires_explicit_confirmation(case):
    t=case;body={'request_id':uid(),'expected_task_version':1,'execution_id':t['execution'],'context_hash':t['live']['source']['context_hash'],
      'acknowledge_cross_version':False,'reason':'测试未确认'}
    assert t['client'].post(t['base']+'/preview',json=body).status_code==409

def test_concurrent_same_request_creates_exactly_one_plan(case):
    t=case;body=draft(t)
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:t['client'].post(t['base'],json=body),range(2)))
    assert all(r.status_code==201 for r in responses),[r.text for r in responses]
    assert responses[0].json()==responses[1].json()
    with Session(engine()) as db:
        plans=list(db.scalars(select(m.Plan).where(m.Plan.task_id==t['task'],m.Plan.data['schema'].as_string()==store.c.SCHEMA)))
        assert len(plans)==1 and len(plans[0].data['operations'])==1

def test_saved_original_can_be_recovered_after_live_source_changes(case):
    t=case;body,first=created(t);t['live']['source']['context_hash']='c'*64
    r=t['client'].post(t['base'],json=body);assert r.status_code==201 and r.json()==first
    assert t['sessions']['reviewer'].get(t['base']+'/operations/'+body['request_id']).status_code==409
    assert t['client'].get(t['base']+'/operations/'+body['request_id']).json()['receipt']==first

def test_request_id_cannot_be_reused_for_different_operation(case):
    t=case;body,first=created(t);plan=first['plan_id']
    reused={'request_id':body['request_id'],'expected_version':1,'reason':'不能复用原编号'}
    assert t['client'].post(t['base']+'/'+plan+'/activate',json=reused).status_code==409
    assert t['client'].get(t['base']+'/'+plan).json()['phase']=='draft'

def test_concurrent_distinct_creations_cannot_create_two_active_plans(case):
    t=case;first=draft(t);second=draft(t)
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda b:t['client'].post(t['base'],json=b),[first,second]))
    assert sorted(r.status_code for r in responses)==[201,409]
    with Session(engine()) as db:
        plans=list(db.scalars(select(m.Plan).where(m.Plan.task_id==t['task'],m.Plan.data['schema'].as_string()==store.c.SCHEMA)))
        assert len(plans)==1

def test_independent_audit_tampering_is_detected(case):
    t=case;_,first=created(t);plan=first['plan_id']
    with Session(engine()) as db:
        row=db.get(m.Plan,plan);audit=db.get(m.Audit,row.data['operations'][0]['id']);data=copy.deepcopy(audit.data);data['request']['reason']='被篡改';audit.data=data;db.commit()
    assert t['client'].get(t['base']+'/'+plan).status_code==409

def test_open_formal_continuation_blocks_goal_and_withdrawal_does_not_erase_history(case):
    from generic_agents.security import Principal
    t=case;_,first=created(t);plan=first['plan_id']
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);p=Principal(t['owner'],task.workspace_id,'admin','test')
        guard=store.goal_guard(db,p,task);assert guard['open_count']==1 and not guard['all_settled'] and not guard['formal_completion_verified'];open_hash=guard['basis_hash']
    _,r=command(t,plan,'withdraw',t['client'],1);assert r.status_code==200
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);p=Principal(t['owner'],task.workspace_id,'admin','test')
        guard=store.goal_guard(db,p,task);assert guard['open_count']==0 and guard['all_settled'] and guard['continuation_count']==1 and guard['basis_hash']!=open_hash

def test_removing_task_index_cannot_hide_existing_continuation(case):
    from generic_agents.security import Principal
    t=case;_,first=created(t)
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);data=copy.deepcopy(task.data);data.pop(store.c.INDEX);data['graph_reasoning_retirements_v337']={'marker':'exists'};task.data=data;db.commit()
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);p=Principal(t['owner'],task.workspace_id,'admin','test')
        with pytest.raises(Exception) as error:store.goal_guard(db,p,task)
        assert getattr(error.value,'status_code',None)==409

def test_missing_live_source_keeps_saved_history_readable(case,monkeypatch):
    t=case;_,first=created(t);plan=first['plan_id']
    def missing(*args,**kwargs):raise KeyError('missing original source')
    monkeypatch.setattr(store,'_snapshot',missing)
    r=t['client'].get(t['base']+'/'+plan);assert r.status_code==200 and not r.json()['current_sources_verified']
    assert r.json()['phase']=='draft' and not r.json()['permissions']['can_activate']
    _,r=command(t,plan,'withdraw',t['client'],1);assert r.status_code==200

def test_other_advice_family_request_id_cannot_be_reused(case):
    t=case;body=draft(t)
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);event=m.Audit(workspace_id=task.workspace_id,actor_id=t['owner'],
            action='same_goal_advice.started_v308',object_id=uid(),data={'task_id':task.id,'request_id':body['request_id'],'body':{'request_id':body['request_id']}})
        db.add(event);db.commit()
    assert t['client'].post(t['base'],json=body).status_code==409
    with Session(engine()) as db:
        assert not list(db.scalars(select(m.Plan).where(m.Plan.task_id==t['task'],m.Plan.data['schema'].as_string()==store.c.SCHEMA)))

def test_non_owner_operator_cannot_create_for_owner(case):
    t=case;body=draft(t);r=t['sessions']['reviewer'].post(t['base'],json=body)
    assert r.status_code==403
    assert t['client'].get(t['base']).json()['plans']==[]

def test_other_workspace_cannot_preview_or_create(case):
    t=case;body=draft(t)
    with Session(engine()) as db:
        other=m.Workspace(name='不相关工作区');db.add(other);db.flush()
        cap=m.Capability(workspace_id=other.id,namespace='other',name='pkg',release_version='1',kind='domain_package',content_hash='b'*64,data={});db.add(cap);db.flush()
        task=m.Task(workspace_id=other.id,owner_id=t['owner'],package_id=cap.id,title='别的任务',status='workflow_waiting',data={});db.add(task);db.commit();foreign=task.id
    foreign_base=t['base'].replace(t['task'],foreign)
    assert t['client'].post(foreign_base,json=body).status_code==404

def test_full_history_never_creates_an_unreadable_extra_record(case,monkeypatch):
    t=case;body=draft(t)
    monkeypatch.setattr(store,'_inventory',lambda *args:{str(i):{'state':{'phase':'withdrawn'}} for i in range(store.MAX_PLANS)})
    r=t['client'].post(t['base'],json=body);assert r.status_code==409
    with Session(engine()) as db:
        assert not list(db.scalars(select(m.Plan).where(m.Plan.task_id==t['task'],m.Plan.data['schema'].as_string()==store.c.SCHEMA)))

def abort_body(body,operation='create',plan_id=None):
    return {'request_id':body['request_id'],'operation':operation,'plan_id':plan_id,'original_body':body,'reason':'确认终止未完成的原申请'}

def test_unexecuted_request_can_be_atomically_abandoned_and_never_later_execute(case):
    t=case;body=draft(t);abandon=abort_body(body);path=t['base']+'/operations/'+body['request_id']+'/abandon'
    r=t['client'].post(path,json=abandon);assert r.status_code==200,r.text
    assert r.json()['abandoned'] and r.json()['body_hash']==digest(body)
    assert t['client'].post(path,json=abandon).json()==r.json()
    changed={**abandon,'reason':'不同原因'};assert t['client'].post(path,json=changed).status_code==409
    assert t['client'].post(t['base'],json=body).status_code==409
    receipt=t['client'].get(t['base']+'/operations/'+body['request_id']);assert receipt.json()==r.json()
    assert t['sessions']['reviewer'].get(t['base']+'/operations/'+body['request_id']).status_code==409
    assert t['client'].get(t['base']).json()['plans']==[]

def test_saved_original_cannot_be_abandoned_after_success(case):
    t=case;body,first=created(t);path=t['base']+'/operations/'+body['request_id']+'/abandon'
    assert t['client'].post(path,json=abort_body(body)).status_code==409
    assert t['client'].get(t['base']+'/operations/'+body['request_id']).json()['receipt']==first

def test_abandon_and_original_race_has_only_one_durable_outcome(case):
    t=case;body=draft(t);negative=abort_body(body);path=t['base']+'/operations/'+body['request_id']+'/abandon'
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(t['client'].post,t['base'],json=body);b=pool.submit(t['client'].post,path,json=negative);ra,rb=a.result(),b.result()
    assert (ra.status_code,rb.status_code) in {(201,409),(409,200)},(ra.text,rb.text)
    view=t['client'].get(t['base']+'/operations/'+body['request_id']).json();assert view['found']
    assert bool(view.get('abandoned'))==(rb.status_code==200)

def test_context_displays_saved_state_without_changing_frozen_context_hash(case,monkeypatch):
    from generic_agents import formal_continuation_api_v344 as api
    t=case;value={**copy.deepcopy(t['live']['source']['context']),'reason':'正式续段尚未启动'}
    monkeypatch.setattr(api,'read_context',lambda *args:copy.deepcopy(value))
    before=t['client'].get(t['base']+'/context');assert before.status_code==200
    _,created_receipt=created(t)
    after=t['client'].get(t['base']+'/context');assert after.status_code==200
    assert after.json()['context_hash']==before.json()['context_hash']==digest(value)
    assert '已保存' in after.json()['reason'] and after.json()['task_version']==2

def test_final_goal_acceptance_freezes_continuation_but_original_replay_remains(case):
    t=case;body,first=created(t);plan=first['plan_id']
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);data=copy.deepcopy(task.data);acceptance={'decision':'accepted','technical_test_only':True};data['native_goal_acceptance']=acceptance;data['native_goal_acceptance_hash']=digest(acceptance);task.data=data;db.commit()
    _,r=command(t,plan,'withdraw',t['client'],1);assert r.status_code==409
    view=t['client'].get(t['base']+'/'+plan);assert view.status_code==200 and view.json()['goal_completed'] and not view.json()['permissions']['can_withdraw']
    assert t['client'].post(t['base'],json=body).json()==first

def test_only_independently_verified_finished_steps_can_settle_goal_guard(case,monkeypatch):
    from generic_agents.security import Principal
    from generic_agents import formal_continuation_proof_v346 as proof
    t=case;_,first=created(t);plan=first['plan_id'];_,r=command(t,plan,'activate',t['client'],1);assert r.status_code==200
    version=2
    for key,actor in [('review','reviewer'),('first','clerk'),('second','chief')]:
        _,r=command(t,plan,'complete-step',t['sessions'][actor],version,step_key=key,result='技术测试记录')
        assert r.status_code==200;version+=1
    verified=[]
    def check(db,p,task,*,lock_sources=False):
        verified.append(lock_sources);return {'basis_hash':'f'*64,'advice_generation':2,'data_generation':4,'simulation_only':True,'test_only':True}
    monkeypatch.setattr(proof,'verify',check)
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);p=Principal(t['owner'],task.workspace_id,'admin','test')
        gate=store.goal_guard(db,p,task,lock_sources=True)
        assert gate['all_settled'] and gate['formal_completion_verified'] and gate['simulation_only'] and gate['completion_proof_hash']=='f'*64 and verified==[True]
    def reject(*args,**kwargs):raise HTTPException(409,'原凭证无效')
    monkeypatch.setattr(proof,'verify',reject)
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);p=Principal(t['owner'],task.workspace_id,'admin','test')
        with pytest.raises(HTTPException):store.goal_guard(db,p,task,lock_sources=True)

def test_acceptance_and_withdrawal_are_serialized_and_never_both_succeed(case,monkeypatch):
    import threading
    from generic_agents.security import Principal
    from generic_agents import formal_continuation_proof_v346 as proof
    t=case;_,first=created(t);plan=first['plan_id'];_,r=command(t,plan,'activate',t['client'],1);assert r.status_code==200
    version=2
    for key,actor in [('review','reviewer'),('first','clerk'),('second','chief')]:
        _,r=command(t,plan,'complete-step',t['sessions'][actor],version,step_key=key,result='模拟记录')
        assert r.status_code==200;version+=1
    monkeypatch.setattr(proof,'verify',lambda *a,**k:{'basis_hash':'f'*64,'advice_generation':2,'data_generation':4,'simulation_only':True,'test_only':True})
    with Session(engine()) as db:
        task=db.get(m.Task,t['task']);p=Principal(t['owner'],task.workspace_id,'admin','test');expected=store.goal_guard(db,p,task)['basis_hash']
    barrier=threading.Barrier(2)
    def accept():
        barrier.wait()
        with Session(engine()) as db:
            task=db.scalar(select(m.Task).where(m.Task.id==t['task']).with_for_update());p=Principal(t['owner'],task.workspace_id,'admin','test')
            gate=store.goal_guard(db,p,task,lock_sources=True)
            if not gate['formal_completion_verified'] or gate['basis_hash']!=expected:db.rollback();return False
            value={'decision':'accept','technical_test_only':True};task.data={**task.data,'native_goal_acceptance':value,'native_goal_acceptance_hash':digest(value)};db.commit();return True
    def withdraw():
        barrier.wait();_,r=command(t,plan,'withdraw',t['client'],version);return r.status_code==200
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(accept);b=pool.submit(withdraw);accepted,withdrawn=a.result(),b.result()
    assert accepted!=withdrawn
