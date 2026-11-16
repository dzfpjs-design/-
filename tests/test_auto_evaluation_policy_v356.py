from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents import auto_evaluation_policy_v356 as policy
from generic_agents.db import engine
from generic_agents.models import Audit,Task
from tests.test_native_goals import setup


def configured(client,monkeypatch):
    _,_,task,_,_=setup(client)
    from generic_agents import workflow_goal_evaluation_packet_v288 as packet
    from generic_agents import auto_evaluation_policy_api_v356 as api
    monkeypatch.setattr(api,'automatic_runner_available',lambda:True)
    manifest={'kind':'tool','namespace':'local','name':'evaluation','version':'1.0.0','owner':'test','description':'本地评价',
        'input_schema':packet.input_schema(),'output_schema':packet.output_schema(),'allowed_agent_roles':['data'],
        'execution':{'adapter':'workflow_service','service_actor':'reader_service','operation':packet.OP,'side_effect':True},
        'data_policy':{'deployment':'local','cloud_allowed':False,'external_allowed':False},
        'budget':{'max_attempts':1,'max_concurrency':1},'dependencies':[], 'specification':{'schema':packet.CONTRACT}}
    cap=SimpleNamespace(id=str(uuid4()),content_hash='a'*64,data=manifest)
    release=SimpleNamespace(id=str(uuid4()))
    def context(db,p,t):return {'cap':cap,'selected':{'release':release},'goal':{'hash':'b'*64}}
    monkeypatch.setattr(policy,'_context',context)
    base='/api/workspace/tasks/'+task['id']+'/auto-evaluation-policy'
    return task,cap,base


def body(version=0):return {'request_id':str(uuid4()),'expected_version':version,'reason':'允许本任务使用本地评价','min_interval_minutes':30,'max_evaluations_per_day':3}


def test_policy_is_opt_in_durable_and_replay_preserves_original_snapshot(client,monkeypatch):
    task,cap,base=configured(client,monkeypatch)
    assert client.get(base).json()=={'enabled':False,'version':0,'policy':None,'can_enable':True,'unavailable_reason':None}
    command=body();response=client.put(base,json=command);assert response.status_code==200,response.text
    first=response.json();assert first['action']=='enable' and first['policy_snapshot']['version']==1
    assert client.put(base,json=command).json()==first
    bad={**command,'reason':'其他内容'};assert client.put(base,json=bad).status_code==409
    assert client.get(base+'/operations/'+command['request_id']).json()==first
    revoke={'request_id':str(uuid4()),'expected_version':1,'reason':'暂停自动评价'}
    stopped=client.post(base+'/revoke',json=revoke);assert stopped.status_code==200
    assert stopped.json()['policy_snapshot']['version']==2 and not client.get(base).json()['enabled']
    assert client.put(base,json=command).json()==first  # replay is historical, never reenables
    assert not client.get(base).json()['enabled']
    assert client.put(base,json=body(1)).status_code==409
    assert client.get(base+'/operations/'+str(uuid4())).status_code==404
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(policy.AutoEvaluationPolicyCommandV356))==2
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action.in_(['auto_evaluation_policy.enable','auto_evaluation_policy.revoke'])))==2


def test_policy_rejects_nonlocal_external_or_unbounded(client,monkeypatch):
    task,cap,base=configured(client,monkeypatch)
    cap.data['data_policy']['external_allowed']=True
    assert client.put(base,json=body()).status_code==409
    cap.data['data_policy']['external_allowed']=False
    assert client.put(base,json={**body(),'max_evaluations_per_day':101}).status_code==422
    assert client.put(base,json={**body(),'min_interval_minutes':0}).status_code==422
    assert client.put(base,json={**body(),'request_id':'not-a-uuid'}).status_code==422
    assert client.get(base).json()['version']==0


def test_verify_rechecks_scope_policy_integrity_and_revocation(client,monkeypatch):
    from fastapi import HTTPException
    from generic_agents.security import Principal
    import pytest
    task,cap,base=configured(client,monkeypatch);command=body();assert client.put(base,json=command).status_code==200
    with Session(engine()) as db:
        row=db.scalar(select(policy.AutoEvaluationPolicyV356));original=db.get(Task,task['id'])
        p=Principal(row.data['actor_id'],row.workspace_id,'admin','test')
        assert policy.verify(db,p,original,row)['cap'].id==cap.id
        cap.data['data_policy']['cloud_allowed']=True
        with pytest.raises(HTTPException):policy.verify(db,p,original,row)
        cap.data['data_policy']['cloud_allowed']=False
        row.data={**row.data,'max_evaluations_per_day':99}
        with pytest.raises(HTTPException):policy.verify(db,p,original,row)
        db.rollback()
    assert client.post(base+'/revoke',json={'request_id':str(uuid4()),'expected_version':1,'reason':'停用'}).status_code==200
    with Session(engine()) as db:
        row=db.scalar(select(policy.AutoEvaluationPolicyV356));original=db.get(Task,task['id']);p=Principal(row.data['actor_id'],row.workspace_id,'admin','test')
        with pytest.raises(HTTPException):policy.verify(db,p,original,row)


def test_policy_command_records_are_database_append_only(client,monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    import pytest
    _,_,base=configured(client,monkeypatch);assert client.put(base,json=body()).status_code==200
    with Session(engine()) as db:
        row=db.scalar(select(policy.AutoEvaluationPolicyCommandV356))
        with pytest.raises(DBAPIError):
            db.execute(text('UPDATE auto_evaluation_policy_commands_v356 SET version=version+1 WHERE id=:id'),{'id':row.id})
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text('DELETE FROM auto_evaluation_policy_commands_v356 WHERE id=:id'),{'id':row.id})
        db.rollback()


def test_legacy_fixed_local_packet_and_explicit_external_rejection(client,monkeypatch):
    task,cap,base=configured(client,monkeypatch)
    cap.data['data_policy']={'cloud_allowed':False}
    first=client.put(base,json=body());assert first.status_code==200,first.text
    for changed in ({'cloud_allowed':False,'deployment':'cloud'},
                    {'cloud_allowed':False,'external_allowed':True},
                    {'cloud_allowed':True},{}):
        cap.data['data_policy']=changed
        assert client.put(base,json=body(1)).status_code in (409,422)
    cap.data['data_policy']={'cloud_allowed':False}
    cap.data['execution']['operation']='unregistered_operation'
    assert client.put(base,json=body(1)).status_code in (409,422)
    cap.data['execution']['operation']='create_goal_evaluation_packet_v288'
    cap.data['dependencies']=['network_tool']
    assert client.put(base,json=body(1)).status_code in (409,422)
    assert client.get(base).json()['version']==1


def test_forged_self_consistent_policy_row_cannot_be_read_or_used(client,monkeypatch):
    import copy,pytest
    from fastapi import HTTPException
    from generic_agents.contracts import digest
    from generic_agents.security import Principal
    task,cap,base=configured(client,monkeypatch);assert client.put(base,json=body()).status_code==200
    with Session(engine()) as db:
        row=db.scalar(select(policy.AutoEvaluationPolicyV356));value=copy.deepcopy(row.data)
        value['max_evaluations_per_day']=99
        value['policy_hash']=digest({k:v for k,v in value.items() if k!='policy_hash'})
        row.data=value;db.commit()
    assert client.get(base).status_code==409
    assert client.put(base,json=body(1)).status_code==409
    with Session(engine()) as db:
        row=db.scalar(select(policy.AutoEvaluationPolicyV356));t=db.get(Task,task['id']);p=Principal(row.data['actor_id'],row.workspace_id,'admin','test')
        with pytest.raises(HTTPException):policy.verify(db,p,t,row)


def test_missing_or_cross_actor_audit_cannot_prove_authorization(client,monkeypatch):
    from generic_agents.models import User
    task,cap,base=configured(client,monkeypatch);cmd=body();assert client.put(base,json=cmd).status_code==200
    with Session(engine()) as db:
        event=db.scalar(select(Audit).where(Audit.action=='auto_evaluation_policy.enable'))
        other=User(username='policy_other_'+str(uuid4()),password_hash='unusable');db.add(other);db.flush()
        event.actor_id=other.id;db.commit()
    assert client.get(base).status_code==409
    assert client.get(base+'/operations/'+cmd['request_id']).status_code==409
    assert client.put(base,json=cmd).status_code==409


def test_inserted_policy_without_independent_command_is_not_authorized(client,monkeypatch):
    from generic_agents.security import Principal
    from fastapi import HTTPException
    import pytest
    from generic_agents.contracts import digest
    task,cap,base=configured(client,monkeypatch)
    with Session(engine()) as db:
        t=db.get(Task,task['id']);actor=t.owner_id
        raw={'workspace_id':t.workspace_id,'task_id':t.id,'instance_id':t.data['instance_id'],
             'release_id':str(uuid4()),'actor_id':actor,'goal_hash':'b'*64,'entity_ids':sorted(t.data['entity_ids']),
             'capability_id':cap.id,'capability_hash':cap.content_hash,'min_interval_minutes':5,
             'max_evaluations_per_day':1,'enabled_at':'2026-01-01T00:00:00+00:00'}
        row=policy.AutoEvaluationPolicyV356(workspace_id=t.workspace_id,task_id=t.id,status='enabled',version=1,data={**raw,'policy_hash':digest(raw)})
        db.add(row);db.commit();p=Principal(actor,t.workspace_id,'admin','test')
        with pytest.raises(HTTPException):policy.verify(db,p,t,row)
    assert client.get(base).status_code==409


def test_unavailable_runner_cannot_enable_even_with_valid_request(client,monkeypatch):
    from generic_agents import auto_evaluation_policy_api_v356 as api
    task,cap,base=configured(client,monkeypatch)
    monkeypatch.setattr(api,'automatic_runner_available',lambda:False)
    assert client.get(base).json()['can_enable'] is False
    assert client.put(base,json=body()).status_code==409
    assert client.get(base).json()['version']==0


def test_unknown_request_retirement_is_durable_and_blocks_late_original(client,monkeypatch):
    _,_,base=configured(client,monkeypatch);command=body();url=base+'/operations/'+command['request_id']+'/retire'
    retired=client.post(url,json={'reason':'原响应未知，封存原请求'});assert retired.status_code==200,retired.text
    assert retired.json()=={'request_id':command['request_id'],'phase':'retired','operation':None}
    assert client.post(url,json={'reason':'原响应未知，封存原请求'}).json()==retired.json()
    assert client.post(url,json={'reason':'改变退役说明'}).status_code==409
    assert client.put(base,json=command).status_code==409
    assert client.get(base).json()['version']==0


def test_retire_returns_saved_original_instead_of_retiring_it(client,monkeypatch):
    _,_,base=configured(client,monkeypatch);command=body();saved=client.put(base,json=command);assert saved.status_code==200
    result=client.post(base+'/operations/'+command['request_id']+'/retire',json={'reason':'恢复原请求'})
    assert result.status_code==200 and result.json()=={'request_id':command['request_id'],'phase':'recorded','operation':saved.json()}
    assert client.put(base,json=command).json()==saved.json()


def test_task_management_requires_owner_or_administrator(client,monkeypatch):
    import pytest
    from fastapi import HTTPException
    from generic_agents.models import Membership,User
    from generic_agents.security import Principal
    task,cap,base=configured(client,monkeypatch);cmd=body();assert client.put(base,json=cmd).status_code==200
    with Session(engine()) as db:
        t=db.get(Task,task['id']);other=User(username='policy_operator_'+str(uuid4()),password_hash='unusable');db.add(other);db.flush()
        db.add(Membership(user_id=other.id,workspace_id=t.workspace_id,role='operator'));db.commit()
        p=Principal(other.id,t.workspace_id,'operator','test')
        with pytest.raises(HTTPException) as denied:
            policy.change(db,p,t.id,'revoke',{'request_id':str(uuid4()),'expected_version':1,'reason':'非所有人'})
        assert denied.value.status_code==403;db.rollback()
        with pytest.raises(HTTPException) as denied:
            policy.retire(db,p,t.id,str(uuid4()),'非所有人')
        assert denied.value.status_code==403;db.rollback()
        with pytest.raises(HTTPException) as denied:
            policy.change(db,p,t.id,'enable',body(1))
        assert denied.value.status_code==403;db.rollback()
    assert client.post(base+'/revoke',json={'request_id':str(uuid4()),'expected_version':1,'reason':'管理员停用'}).status_code==200


def test_retire_and_original_are_serialized_by_real_task_lock(client,monkeypatch):
    import threading
    from fastapi import HTTPException
    from generic_agents.security import Principal
    for first in ('retire','command'):
        task,cap,base=configured(client,monkeypatch);cmd=body();locked=threading.Event();done=threading.Event();out={}
        with Session(engine()) as main:
            t=main.get(Task,task['id']);row_actor=t.owner_id;p=Principal(row_actor,t.workspace_id,'admin','test')
            policy._task(main,p,t.id,lock=True)
            def other():
                try:
                    with Session(engine()) as db:
                        locked.set()
                        if first=='retire':out['result']=policy.change(db,p,t.id,'enable',cmd)
                        else:out['result']=policy.retire(db,p,t.id,cmd['request_id'],'并发退役')
                        db.commit()
                except HTTPException as exc:out['status']=exc.status_code
                finally:done.set()
            thread=threading.Thread(target=other);thread.start();assert locked.wait(3)
            assert not done.wait(.3)
            if first=='retire':result=policy.retire(main,p,t.id,cmd['request_id'],'并发退役')
            else:result=policy.change(main,p,t.id,'enable',cmd)
            main.commit();assert done.wait(15);thread.join()
            if first=='retire':assert result['phase']=='retired' and out.get('status')==409
            else:assert out['result']['phase']=='recorded' and out['result']['operation']==result


def test_execution_rechecks_owner_after_task_transfer(client,monkeypatch):
    import pytest
    from fastapi import HTTPException
    from generic_agents.models import User,Membership
    from generic_agents.security import Principal
    task,cap,base=configured(client,monkeypatch);assert client.put(base,json=body()).status_code==200
    with Session(engine()) as db:
        row=db.scalar(select(policy.AutoEvaluationPolicyV356));t=db.get(Task,task['id'])
        member=db.scalar(select(Membership).where(Membership.workspace_id==row.workspace_id,Membership.user_id==row.data['actor_id']))
        assert t.owner_id==member.user_id
        member.role='operator';db.commit();p=Principal(member.user_id,row.workspace_id,'operator','test')
        assert policy.verify(db,p,t,row)['cap'].id==cap.id
        other=User(username='new_owner_'+str(uuid4()),password_hash='unusable');db.add(other);db.flush()
        t.owner_id=other.id;db.commit()
        with pytest.raises(HTTPException) as denied:policy.verify(db,p,t,row)
        assert denied.value.status_code==403
