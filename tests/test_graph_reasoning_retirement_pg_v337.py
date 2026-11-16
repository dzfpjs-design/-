"""Real PostgreSQL locking/rollback tests; provenance readers are explicit fixtures."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import graph_reasoning_retirement_v337 as subject
from generic_agents import graph_reasoning_evaluation_status_v335 as projection
from generic_agents import workflow_graph as workflow, workflow_graph_api as api
from generic_agents import goal_evaluation_provider_v288 as provider
from generic_agents import layered_approval_store_v175 as approval
from generic_agents import signal_dual_handoffs
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.worker import claim


@pytest.fixture
def scene(monkeypatch):
    with Session(engine()) as db:
        user=m.User(username='retire-'+uuid4().hex,password_hash='fixture',active=True)
        workspace=m.Workspace(name='independent test workspace');db.add_all([user,workspace]);db.flush()
        db.add(m.Membership(user_id=user.id,workspace_id=workspace.id,role='admin'))
        model=m.Capability(workspace_id=workspace.id,namespace='fixture',name='model',release_version='1',kind='foundation_model',
            status='published',content_hash='a'*64,validated_hash='a'*64,data={})
        db.add(model);db.flush()
        data={'execution':{'adapter':'business_goal_ready_v288','model_profile_id':model.id}}
        cap_hash=digest(data)
        capability=m.Capability(workspace_id=workspace.id,namespace='fixture',name='reasoning',release_version='1',kind='tool',
            status='published',content_hash=cap_hash,validated_hash=cap_hash,data=data)
        db.add(capability);db.flush()
        actor=user.id;ws=workspace.id;activation_id='reasoning-activation';graph_hash='b'*64;head='c'*64
        inputs={'data_packet':{'evidence_id':'local-fixture'},'document_packet':{'evidence_id':'document-fixture'}}
        task=m.Task(workspace_id=ws,owner_id=actor,package_id=capability.id,title='only synthetic',status='workflow_waiting',data={})
        db.add(task);db.flush();key='graph:'+task.id+':'+activation_id+':1'
        activation={'id':activation_id,'node_id':'reasoning','epoch':0,'version':0,'status':'ready',
                    'inputs_snapshot':{'arguments':deepcopy(inputs)}}
        job=m.Job(workspace_id=ws,kind=workflow.KIND,idempotency_key=key,status='queued',attempts=0,
            data={'actor_id':actor,'task_id':task.id,'activation_id':activation_id,'graph_hash':graph_hash,
                  'cancel_epoch':0,'activation_version':0,'invocation_key':key,'inputs':deepcopy(inputs),
                  'input_hash':digest(inputs),'attempt':1})
        db.add(job);db.flush()
        transport={'job_id':job.id,'invocation_key':key,'input_hash':digest(inputs),'phase':'queued','cancel_epoch':0,'attempt':1}
        task.data={'graph_runtime':{'graph_hash':graph_hash,'status':'running','cancel_epoch':0,
                    'activations':{activation_id:activation},'commands':[]},'graph_jobs':{activation_id:transport}}
        db.commit()
        ids=SimpleNamespace(task=task.id,job=job.id,actor=actor,workspace=ws,cap=capability.id,model=model.id,
                            activation=activation_id,head=head,inputs=inputs)
    p=Principal(ids.actor,ids.workspace,'admin','synthetic-test-session')
    node={'id':'reasoning','kind':'ability','actor_id':ids.actor,'execution':{'adapter':'business_goal_ready_v288',
          'capability_id':ids.cap,'capability_hash':cap_hash}}
    def context(db,principal,identity,lock=False):
        q=select(m.Task).where(m.Task.workspace_id==principal.workspace_id,m.Task.id==identity)
        if lock:q=q.with_for_update()
        task=db.scalar(q);assert task is not None
        return task,SimpleNamespace(id='release'),{},{}
    monkeypatch.setattr(api,'context',context)
    monkeypatch.setattr(workflow,'node_for',lambda *_:node)
    monkeypatch.setattr(workflow,'invoke_pure',lambda *_:deepcopy(inputs))
    monkeypatch.setattr(workflow,'public_view',lambda task,*_args,**_kwargs:
        {'id':task.id,'graph_jobs':deepcopy(task.data['graph_jobs']), 'graph_runtime':deepcopy(task.data['graph_runtime'])})
    monkeypatch.setattr(signal_dual_handoffs,'parent_for_task',lambda *_:None)
    monkeypatch.setattr(approval,'guard',lambda *_args,**_kwargs:None)
    monkeypatch.setattr(provider,'_head',lambda *_:{'head_hash':ids.head})
    monkeypatch.setattr(projection,'project',lambda *_:{'status':'superseded','activation_id':ids.activation})
    return SimpleNamespace(ids=ids,p=p)


def request(s,**changes):
    value={'request_id':str(uuid4()),'activation_id':s.ids.activation,'version':0,'cancel_epoch':0,
           'expected_head_hash':s.ids.head,'confirm_retire':True,'reason':'使用新评价前，保留原步骤并停止其未执行请求。'}
    return {**value,**changes}


def call(s,body):
    with Session(engine()) as db:
        result=subject.retire(db,s.p,s.ids.task,body);db.commit();return result


def test_one_original_request_retires_only_the_unclaimed_node_and_survives_new_session(scene):
    s=scene;body=request(s);result=call(s,body)
    assert result['status']=='retired'
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task);job=db.get(m.Job,s.ids.job)
        assert job.status=='cancelled' and job.attempts==0 and job.lease_token is None
        assert task.data['graph_jobs'][s.ids.activation]['phase']=='retired_for_new_evaluation'
        assert task.data['graph_runtime']['activations'][s.ids.activation]['inputs_snapshot']['arguments']==s.ids.inputs
        assert task.data['graph_runtime']['status']=='running'
        assert len(list(db.scalars(select(m.Audit).where(m.Audit.action==subject.ACTION))))==1
    assert call(s,body)['status']=='retired'
    with pytest.raises(HTTPException):call(s,{**body,'reason':'不同的理由'})
    with pytest.raises(HTTPException):call(s,request(s))
    assert claim(s.ids.job,kinds=[workflow.KIND],workspace_id=s.ids.workspace) is None


def test_claim_lock_and_claimed_job_both_block_retirement(scene):
    s=scene
    with Session(engine()) as holder, Session(engine()) as contender:
        holder.scalar(select(m.Job).where(m.Job.id==s.ids.job).with_for_update())
        with pytest.raises(HTTPException) as error:subject.retire(contender,s.p,s.ids.task,request(s))
        assert error.value.status_code==409
        contender.rollback();holder.rollback()
    with Session(engine()) as db:
        job=db.get(m.Job,s.ids.job);job.status='running';job.attempts=1;db.commit()
    with pytest.raises(HTTPException):call(s,request(s))
    with Session(engine()) as db:assert db.get(m.Task,s.ids.task).data['graph_jobs'][s.ids.activation]['phase']=='queued'


def test_active_independent_analysis_prevents_retirement(scene,monkeypatch):
    from generic_agents import same_goal_advice_store_v302 as store
    s=scene
    monkeypatch.setattr(store,'_inventory',lambda *_:[(SimpleNamespace(status='generating'),None,None)])
    with pytest.raises(HTTPException):call(s,request(s))
    with Session(engine()) as db:
        assert db.get(m.Job,s.ids.job).status=='queued'
        assert subject.KEY not in db.get(m.Task,s.ids.task).data


def test_two_connections_same_request_recover_one_durable_retirement(scene):
    s=scene;body=request(s)
    with Session(engine()) as first, Session(engine()) as second:
        assert subject.retire(first,s.p,s.ids.task,body)['status']=='retired'
        with pytest.raises(HTTPException) as error:subject.retire(second,s.p,s.ids.task,body)
        assert error.value.status_code==409
        second.rollback();first.commit()
    assert call(s,body)['status']=='retired'
    with Session(engine()) as db:
        assert len(list(db.scalars(select(m.Audit).where(m.Audit.action==subject.ACTION))))==1


def test_task_lock_is_bounded_without_deadlocking_a_job_then_task_contender(scene):
    s=scene
    with Session(engine()) as holder, Session(engine()) as contender:
        holder.scalar(select(m.Task).where(m.Task.id==s.ids.task).with_for_update())
        with pytest.raises(HTTPException) as error:subject.retire(contender,s.p,s.ids.task,request(s))
        assert error.value.status_code==409
        contender.rollback();holder.rollback()
    with Session(engine()) as db:
        assert db.get(m.Job,s.ids.job).status=='queued'
        assert subject.KEY not in db.get(m.Task,s.ids.task).data


def test_changed_head_and_unconfirmed_request_preserve_the_original_job(scene,monkeypatch):
    s=scene
    with pytest.raises(HTTPException):call(s,request(s,confirm_retire=False))
    monkeypatch.setattr(provider,'_head',lambda *_:{'head_hash':'d'*64})
    with pytest.raises(HTTPException):call(s,request(s))
    with Session(engine()) as db:
        assert db.get(m.Job,s.ids.job).status=='queued'
        assert subject.KEY not in db.get(m.Task,s.ids.task).data


@pytest.mark.parametrize('artifact',['invocation','dispatch_audit','unlinked_cloud','linked_cloud'])
def test_any_actual_or_ambiguous_dispatch_artifact_blocks_retirement(scene,artifact):
    s=scene
    with Session(engine()) as db:
        job=db.get(m.Job,s.ids.job)
        if artifact=='invocation':
            db.add(m.Invocation(workspace_id=s.ids.workspace,capability_id=s.ids.cap,idempotency_key='another-key',
                                status='running',data={'job_id':job.id}))
        elif artifact=='dispatch_audit':
            db.add(m.Audit(workspace_id=s.ids.workspace,actor_id=s.ids.actor,action='workflow.graph.dispatch',
                           object_id=s.ids.task,data={'job_id':job.id}))
        else:
            data={} if artifact=='unlinked_cloud' else {'gateway_dispatch':{'identity':{'job_id':job.id}}}
            db.add(m.CloudCall(workspace_id=s.ids.workspace,capability_id=s.ids.model,status='reserved',
                               reserved_tokens=1,charged_tokens=0,data=data))
        db.commit()
    with pytest.raises(HTTPException):call(s,request(s))
    with Session(engine()) as db:assert db.get(m.Job,s.ids.job).status=='queued'


def test_old_unlinked_same_model_call_still_blocks_retirement(scene):
    from datetime import datetime,timezone
    s=scene
    with Session(engine()) as db:
        db.add(m.CloudCall(workspace_id=s.ids.workspace,capability_id=s.ids.model,status='unknown',
                           reserved_tokens=1,charged_tokens=0,data={},created_at=datetime(2020,1,1,tzinfo=timezone.utc)))
        db.commit()
    with pytest.raises(HTTPException):call(s,request(s))
    with Session(engine()) as db:assert db.get(m.Job,s.ids.job).status=='queued'


def test_call_linked_to_another_job_does_not_block_retirement(scene):
    s=scene
    with Session(engine()) as db:
        db.add(m.CloudCall(workspace_id=s.ids.workspace,capability_id=s.ids.model,status='completed',
                           reserved_tokens=1,charged_tokens=0,
                           data={'gateway_dispatch':{'identity':{'job_id':'another-job'}}}))
        db.commit()
    assert call(s,request(s))['status']=='retired'


@pytest.mark.parametrize('damage',['audit','job','graph','transport'])
def test_recovery_refuses_a_receipt_whose_durable_evidence_was_damaged(scene,damage):
    s=scene;body=request(s);call(s,body)
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task);record=task.data[subject.KEY][body['request_id']]
        if damage=='audit':
            event=db.get(m.Audit,record['audit_id']);event.data={**event.data,'reason':'tampered'}
        elif damage=='job':
            db.get(m.Job,s.ids.job).status='queued'
        else:
            data=deepcopy(task.data)
            if damage=='graph':data['graph_runtime']['graph_hash']='d'*64
            else:data['graph_jobs'][s.ids.activation]['phase']='queued'
            task.data=data
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.historical_record(db,s.p,db.get(m.Task,s.ids.task),body['request_id'])
    with pytest.raises(HTTPException):call(s,body)


def test_failure_after_changes_rolls_back_job_task_and_audit(scene,monkeypatch):
    s=scene
    def failed_view(*_args,**_kwargs):raise RuntimeError('simulated response construction failure')
    monkeypatch.setattr(workflow,'public_view',failed_view)
    with pytest.raises(RuntimeError):call(s,request(s))
    with Session(engine()) as db:
        assert db.get(m.Job,s.ids.job).status=='queued'
        assert subject.KEY not in db.get(m.Task,s.ids.task).data
        assert list(db.scalars(select(m.Audit).where(m.Audit.action==subject.ACTION)))==[]


def test_retired_marker_stops_even_an_incorrectly_requeued_worker(scene,monkeypatch):
    from generic_agents import workspace_api
    s=scene;call(s,request(s))
    def saved(db,_p,identity,_lock=False):return db.get(m.Task,identity),SimpleNamespace(),{}
    monkeypatch.setattr(workspace_api,'saved_execution',saved)
    monkeypatch.setattr(workflow,'graph_for',lambda *_:{})
    with Session(engine()) as db:
        job=db.get(m.Job,s.ids.job)
        # Deliberate test-only corruption represents a future recovery bug.
        job.status='queued';db.flush()
        assert workflow.process(db,s.p,job)=='cancelled'
        assert list(db.scalars(select(m.Invocation)))==[]
        assert list(db.scalars(select(m.CloudCall)))==[]


def test_settle_does_not_recreate_a_retired_job_and_missing_transport_fails_closed(scene,monkeypatch):
    from generic_agents import workflow_graph_waits as waits
    s=scene;call(s,request(s))
    monkeypatch.setattr(workflow,'graph_for',lambda *_:{})
    monkeypatch.setattr(workflow,'business_binding',lambda *_:{})
    monkeypatch.setattr(waits,'enabled',lambda *_:False)
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task);state=deepcopy(task.data['graph_runtime'])
        state['eligible_ability_ids']=[s.ids.activation]
        workflow.settle(db,s.p,task,SimpleNamespace(),{},state);db.flush()
        assert len(list(db.scalars(select(m.Job).where(m.Job.kind==workflow.KIND))))==1
        task.data={**task.data,'graph_jobs':{}}
        with pytest.raises(HTTPException):workflow.settle(db,s.p,task,SimpleNamespace(),{},state)


def test_retry_cannot_revive_a_retired_node_even_if_transport_is_damaged(scene):
    s=scene;call(s,request(s))
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task);jobs=deepcopy(task.data['graph_jobs'])
        jobs[s.ids.activation]['phase']='blocked';task.data={**task.data,'graph_jobs':jobs};db.commit()
    body=api.Retry(request_id=uuid4(),activation_id=s.ids.activation,version=0,cancel_epoch=0,
                   expected_attempt=1,confirm_repeat_read=True)
    with Session(engine()) as db:
        with pytest.raises(HTTPException,match='原步骤已停用'):api.retry(s.ids.task,body,s.p,db)
    with Session(engine()) as db:assert db.get(m.Job,s.ids.job).status=='cancelled'
