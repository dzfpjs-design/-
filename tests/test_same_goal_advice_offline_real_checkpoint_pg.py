"""Restore a fresh synthetic approval/Goal into a dedicated disposable PG.

This verifies an authenticated test-only HTTP adapter and the real local
preparation service. The adapter is not installed by the product; no physical
device, real workspace or public cloud is contacted.
"""
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.setup_api import P, DB
from generic_agents.models import Audit, Capability, CloudCall, Job, Plan, Task
from generic_agents import leases, worker as runtime_worker
from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_store_v302 as old
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_offline_grant_candidate_v2 as delegated
from generic_agents import same_goal_advice_document_lease_candidate_v2 as control
from generic_agents.goal_evaluation_provider_v288 import KEY
from generic_agents.runtime_task_document_models_v207 import WorkflowDocumentPacketV207, RuntimeTaskDocumentSourceSet
from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db
from tests.test_authored_service_workflow_pg_v270 import publish

# The isolated test adapter obtains the genuine authenticated Principal from the
# same dependencies as product endpoints. It is never registered in production.
router=APIRouter()
@router.get('/api/_synthetic_only/advice/{task_id}/preflight')
def preflight(task_id:str,p:P,db:DB):
    task=db.get(Task,task_id);head=task.data[KEY]
    binding=old.provider.binding(db,p,task)
    reasoning=next(n for n in binding['binding']['graph']['nodes'] if n['id']=='reasoning')
    cap=db.get(Capability,reasoning['execution']['capability_id'])
    manifest=contracts.build_manifest(cap.data,old._pin(cap))
    packets=list(db.scalars(select(WorkflowDocumentPacketV207).where(
        WorkflowDocumentPacketV207.workspace_id==p.workspace_id,WorkflowDocumentPacketV207.task_id==task.id)))
    assert len(packets)==1,'no original approved V207 packet in synthetic fixture'
    packet=packets[0];source=db.get(RuntimeTaskDocumentSourceSet,packet.source_set_id)
    selection={'kind':'document_packet_v207','packet':{'id':packet.id,'version':packet.version,
        'evidence_id':packet.evidence_id,'sha256':packet.sha256,'source_set_id':source.id,
        'source_set_hash':source.source_set_hash}}
    return {'manifest':manifest,'selection':selection,'goal_hash':task.data['goal_hash'],'head_hash':head['head_hash']}

@router.post('/api/_synthetic_only/advice/{task_id}/prepare')
def prepare(task_id:str,body:dict,p:P,db:DB):
    now=db.scalar(select(func.clock_timestamp()))
    policy_body={'request_id':str(uuid4()),'expected_goal_hash':body['goal_hash'],
        'expected_mature_head_hash':body['head_hash'],'limits':{'max_tokens':512,
            'max_request_bytes':50000,'max_evidence_items':2},
        'expires_at':(now+timedelta(minutes=20)).isoformat(),'reason':'合成资料后台委托'}
    issued=policy.authorize(db,p,task_id,policy_body);db.commit()
    request={'request_id':str(uuid4()),'expected_goal_hash':body['goal_hash'],
        'expected_mature_head_hash':body['head_hash'],
        'document_source':body['selection'],'reason':'准备已批准合成资料包'}
    created=writer.prepare(db,p,task_id,request,issued['policy_ref']);db.commit()
    control_id=control.register(db,p,task_id,created['job_id']);db.commit()
    grant=delegated.issue(db,p,task_id,created['job_id'],str(uuid4()));db.commit()
    return {'control_id':control_id,'job_id':created['job_id'],
        'plan_id':created['plan_id'],'grant_id':grant['grant_id']}
app.include_router(router)


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_FRESH_GOAL_CHECKPOINT'),
    reason='requires an explicitly created private synthetic fresh Goal checkpoint')
def test_real_synthetic_approved_packet_delegated_worker_and_logout(monkeypatch):
    root=os.environ['GA_SYNTHETIC_FRESH_GOAL_CHECKPOINT']
    task_id=restore_to_disposable_test_db(root)
    with Session(engine()) as db:
        caps=list(db.scalars(select(Capability).where(
            Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
        origins={cap.data['specification']['device_gateway_binding']['origin'] for cap in caps}
        assert len(origins)==1 and all(re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}',o) for o in origins)
        monkeypatch.setattr(settings(),'device_gateway_allowed_origins',next(iter(origins)))
    login_path=Path(settings().storage_dir)/'__synthetic_checkpoint_login__.json'
    assert login_path.stat().st_mode & 0o777 == 0o600
    identity=json.loads(login_path.read_text())
    with TestClient(app) as client:
        response=client.post('/api/login',json={'username':identity['username'],'password':identity['password']})
        assert response.status_code==200,'synthetic login failed'
        client.headers.update({'X-CSRF-Token':response.json()['csrf_token'],'X-Workspace-ID':identity['workspace_id']})
        who=client.get('/api/me');assert who.status_code==200
        response=client.get('/api/_synthetic_only/advice/'+task_id+'/preflight')
        assert response.status_code==200,response.text[:250]
        inputs=response.json()
        assert publish(client,inputs.pop('manifest'))['status']=='published'
        response=client.post('/api/_synthetic_only/advice/'+task_id+'/prepare',json=inputs)
        assert response.status_code==200,response.text[:250]
        created=response.json();control_id=created['control_id'];grant_id=created['grant_id']
        assert client.post('/api/logout').status_code==200

    with Session(engine()) as db:
        lease=delegated.claim_worker(db,control_id,grant_id);db.commit()
    with Session(engine()) as db:
        actor=runtime_worker.actor(db,identity['workspace_id'],who.json()['user_id'])
    with Session(engine()) as db:
        db.info['worker_actor']=actor;leases.attach(db,control_id,lease['lease_token'])
        result=delegated.process_packet(db,actor,control_id,lease['lease_token'],grant_id);db.commit()
    assert result['replayed'] is False
    with Session(engine()) as db:
        delegated.settle_worker(db,control_id,lease['lease_token'],grant_id);db.commit()
    engine().dispose()
    with Session(engine()) as db:
        assert db.get(Job,control_id).status=='succeeded'
        assert db.get(Plan,created['plan_id']).status=='awaiting_consent'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.object_id==created['plan_id'],
            Audit.action==control.v2.DOCUMENT_PREPARED))==1
        assert not list(db.scalars(select(CloudCall)))
