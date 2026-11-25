"""Actual HTTP and separate worker process with fully synthetic approved Goal.

Uses a sealed dedicated checkpoint and a test-only prepare adapter because the
formal v2 prepare route is not yet wired. No real report, model or device.
"""
import json,os,re,subprocess,sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit,Capability,CloudCall,Job
from generic_agents.setup_api import P,DB
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents import same_goal_advice_document_lease_candidate_v2 as control
from generic_agents import same_goal_advice_offline_grant_candidate_v2 as grants
from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_same_goal_advice_offline_real_checkpoint_pg import preflight

router=APIRouter()
@router.post('/api/_synthetic_only/offline-ui/{task_id}/prepare')
def prepare(task_id:str,body:dict,p:P,db:DB):
    clock=db.scalar(select(func.clock_timestamp()))
    policy_body={'request_id':str(uuid4()),'expected_goal_hash':body['goal_hash'],
        'expected_mature_head_hash':body['head_hash'],'limits':{'max_tokens':512,'max_request_bytes':50000,'max_evidence_items':2},
        'expires_at':(clock+timedelta(minutes=20)).isoformat(),'reason':'合成资料后台测试'}
    issued=policy.authorize(db,p,task_id,policy_body);db.commit()
    request={'request_id':str(uuid4()),'expected_goal_hash':body['goal_hash'],
        'expected_mature_head_hash':body['head_hash'],'document_source':body['selection'],'reason':'合成资料准备'}
    created=writer.prepare(db,p,task_id,request,issued['policy_ref']);db.commit()
    return {'job_id':created['job_id']}
app.include_router(router)


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_FRESH_GOAL_CHECKPOINT'),reason='explicit isolated synthetic checkpoint required')
def test_http_delegation_survives_logout_and_separate_worker_process(monkeypatch):
    root=os.environ['GA_SYNTHETIC_FRESH_GOAL_CHECKPOINT'];task_id=restore_to_disposable_test_db(root)
    login_file=Path(settings().storage_dir)/'__synthetic_checkpoint_login__.json'
    assert login_file.stat().st_mode&0o777==0o600
    identity=json.loads(login_file.read_text())
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    monkeypatch.setattr(settings(),'offline_document_worker_enabled',True)
    with Session(engine()) as db:
        caps=list(db.scalars(select(Capability).where(
            Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
        origins={cap.data['specification']['device_gateway_binding']['origin'] for cap in caps}
        assert len(origins)==1 and all(re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}',o) for o in origins)
        origin=next(iter(origins));monkeypatch.setattr(settings(),'device_gateway_allowed_origins',origin)
    base='/api/workspace/tasks/'+task_id+'/business-advice/offline-delegations'
    with TestClient(app) as client:
        login=client.post('/api/login',json={'username':identity['username'],'password':identity['password']})
        assert login.status_code==200
        client.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':identity['workspace_id']})
        prepared=client.get('/api/_synthetic_only/advice/'+task_id+'/preflight');assert prepared.status_code==200
        body=prepared.json();assert publish(client,body.pop('manifest'))['status']=='published'
        created=client.post('/api/_synthetic_only/offline-ui/'+task_id+'/prepare',json=body)
        assert created.status_code==200,created.text[:200];job_id=created.json()['job_id']
        view=client.get(base);assert view.status_code==200 and view.json()['items'][0]['state']=='not_authorized'
        issue=client.post(base+'/'+job_id+'/grant',json={'request_id':str(uuid4())})
        assert issue.status_code==200,issue.text[:200]
        assert client.post('/api/logout').status_code==200
    print('SYNTHETIC_HTTP_GRANT_COMMITTED_LOGGED_OUT',flush=True)
    env=os.environ.copy();env.update(GA_OFFLINE_DOCUMENT_DELEGATION_ENABLED='true',
        GA_OFFLINE_DOCUMENT_WORKER_ENABLED='true',GA_DEVICE_GATEWAY_ALLOWED_ORIGINS=origin,
        GA_STORAGE_DIR=str(settings().storage_dir),GA_DEVICE_GATEWAY_KEY_FILE=str(settings().device_gateway_key_file))
    child=subprocess.run([sys.executable,'-c',
        'from generic_agents.same_goal_advice_offline_worker_candidate_v2 import tick,stop_heartbeat\ntry:\n assert tick()["completed"] == 1\nfinally:\n stop_heartbeat()'],
        env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=450)
    assert child.returncode==0,'synthetic isolated worker did not complete'
    print('SYNTHETIC_EXTERNAL_WORKER_COMPLETED',flush=True)
    engine().dispose()
    with TestClient(app) as client:
        login=client.post('/api/login',json={'username':identity['username'],'password':identity['password']})
        assert login.status_code==200
        client.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':identity['workspace_id']})
        view=client.get(base);assert view.status_code==200,view.text[:200]
        item=view.json()['items'][0]
        assert item['prepared'] is True and item['work_state']=='complete' and item['background_available'] is False
    with Session(engine()) as db:
        assert not list(db.scalars(select(CloudCall)))
        assert len(list(db.scalars(select(Audit).where(Audit.action==grants.GRANT))))==1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==control.KIND,Job.status=='succeeded'))==1
