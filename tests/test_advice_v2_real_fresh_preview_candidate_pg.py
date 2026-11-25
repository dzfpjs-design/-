"""Offline replay of an isolated synthetic approved mature Goal checkpoint.

The source pipeline and human approvals are real application records generated
in a disposable synthetic workspace. No actual report, user or cloud is used.
"""
import json, os, re
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import AuthSession, User, Task, Plan, Job, Evidence, Audit, CloudCall, Invocation, Capability
from generic_agents.runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet
from generic_agents.security import Principal
from generic_agents.goal_evaluation_provider_v288 import KEY, binding
from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_store_v302 as old_store
from tests.test_authored_service_workflow_pg_v270 import publish
from generic_agents import evidence
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_unlimited_store_v2 as store
from generic_agents import same_goal_advice_unlimited_analysis_candidate_v2 as frame
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
from generic_agents import same_goal_advice_start_safety_candidate_v2 as safety
from generic_agents.layered_approval_models_v175 import ApprovalManagedTaskV175
from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_FRESH_CHECKPOINT'), reason='requires isolated synthetic fresh checkpoint')
def test_real_mature_approved_source_to_durable_v2_preview(monkeypatch):
    monkeypatch.setattr(settings(),'cloud_allowed_providers','')
    task_id=restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_CHECKPOINT'])
    print('RESTORED_PRIVATE_CHECKPOINT',flush=True)
    login=json.loads((settings().storage_dir/'__synthetic_checkpoint_login__.json').read_text())
    with TestClient(app) as client:
        auth=client.post('/api/login',json={'username':login['username'],'password':login['password']})
        assert auth.status_code==200,auth.status_code
        client.headers.update({'X-CSRF-Token':auth.json()['csrf_token'],'X-Workspace-ID':login['workspace_id']})
        with Session(engine()) as db:
            user=db.scalar(select(User).where(User.username==login['username']))
            session=db.scalar(select(AuthSession).where(AuthSession.user_id==user.id,AuthSession.revoked.is_(False),
                AuthSession.expires_at>func.clock_timestamp()).order_by(AuthSession.created_at.desc()))
            p=Principal(user.id,login['workspace_id'],'admin',session.id)
            task=db.get(Task,task_id);head=task.data[KEY]
            caps=list(db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,
                Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
            origins={cap.data['specification']['device_gateway_binding']['origin'] for cap in caps}
            assert len(origins)==1 and all(re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}',o) for o in origins)
            monkeypatch.setattr(settings(),'device_gateway_allowed_origins',next(iter(origins)))
            assert head['generation']==2
            source=db.scalar(select(RuntimeTaskDocumentSourceSet).where(RuntimeTaskDocumentSourceSet.task_id==task_id,
                RuntimeTaskDocumentSourceSet.workspace_id==p.workspace_id))
            assert source is not None
            selected=binding(db,p,task)
            node=next(n for n in selected['binding']['graph']['nodes'] if n['id']=='reasoning')
            source_cap=db.get(Capability,node['execution']['capability_id'])
            advice_manifest=contracts.build_manifest(source_cap.data,old_store._pin(source_cap))
        published=publish(client,advice_manifest)
        print('PUBLISHED_SYNTHETIC_ADVICE',flush=True)
        assert published['status']=='published'
        with Session(engine()) as db:
            now=db.scalar(select(func.clock_timestamp()))
            body={'request_id':str(uuid4()),'expected_goal_hash':task.data['goal_hash'],
                'expected_mature_head_hash':head['head_hash'],
                'limits':{'max_tokens':512,'max_request_bytes':50000,'max_evidence_items':2},
                'expires_at':(now+timedelta(minutes=20)).isoformat(),'reason':'合成测试政策'}
            issued=policy.authorize(db,p,task_id,body);db.commit()
            print('AUTHORIZED_SYNTHETIC_POLICY',flush=True)
            source_ref={'kind':'approved_source_set','source_set_ref':{'source_set_id':source.id,'source_set_hash':source.source_set_hash}}
        request={'request_id':str(uuid4()),'expected_goal_hash':body['expected_goal_hash'],
            'expected_mature_head_hash':body['expected_mature_head_hash'],'document_source':source_ref,'reason':'合成测试准备'}
        with Session(engine()) as db:created=writer.prepare(db,p,task_id,request,issued['policy_ref']);db.commit()
        print('PREPARATION_CREATED',flush=True)
        with Session(engine()) as db:intent.register(db,p,task_id,created['job_id']);db.commit()
        print('DOCUMENT_INTENT_REGISTERED',flush=True)
        with Session(engine()) as db:document.process(db,p,task_id,created['job_id']);db.commit()
        print('DOCUMENT_PREPARED',flush=True)
        with Session(engine()) as db:
            task,plan=db.get(Task,task_id),db.get(Plan,created['plan_id'])
            prep,_=store.read_prepared(db,p,task,plan)
            current=frame.build_current(db,p,task,plan)
            for identity in current['evidence_ids']:
                item=db.get(Evidence,identity)
                evidence.authorize_cloud(db,p,identity,prep['model_pin']['id'],item.version,'合成测试授权')
            db.commit()
        print('FRAME_BUILT_AND_CONSENTS_SAVED',flush=True)
        req={'request_id':str(uuid4()),'expected_plan_version':2,'expected_goal_hash':body['expected_goal_hash'],
            'expected_mature_head_hash':body['expected_mature_head_hash'],'reason':'合成测试预览'}
        with Session(engine()) as db:first=preview.prepare(db,p,task_id,created['plan_id'],req);db.commit()
        print('PREVIEW_SAVED',flush=True)
        engine().dispose()
        with Session(engine()) as db:
            again=preview.prepare(db,p,task_id,created['plan_id'],req)
            assert first==again and first['can_start'] is False and first['cloud_send_authorized'] is False
            assert db.get(Plan,created['plan_id']).version==3
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==preview.ACTION))==1
            assert db.scalar(select(func.count()).select_from(CloudCall))==0

        # Real synthetic source/approval rows must not race a Start. The
        # competing writes are rolled back; the original approvals stay intact.
        with Session(engine()) as db:
            task,plan=db.get(Task,task_id),db.get(Plan,created['plan_id'])
            prep,_=store.read_prepared(db,p,task,plan)
            event=db.scalar(select(Audit).where(Audit.action==preview.ACTION,Audit.object_id==plan.id))
            saved_preview=event.data['preview_record']
            managed_id=db.scalar(select(ApprovalManagedTaskV175.id).where(ApprovalManagedTaskV175.task_id==task_id))
            assert managed_id is not None
        for model,identity,field,changed in [
            (RuntimeTaskDocumentSourceSet,source_ref['source_set_ref']['source_set_id'],'source_set_hash','0'*64),
            (ApprovalManagedTaskV175,managed_id,'status','withdrawn')]:
            with Session(engine()) as editor:
                row=editor.get(model,identity);setattr(row,field,changed);editor.flush()
                with Session(engine()) as first_db:
                    with pytest.raises(HTTPException):safety.lock_current(first_db,p,first_db.get(Task,task_id),prep,saved_preview)
                    first_db.rollback()
                editor.rollback()
        print('SOURCE_AND_APPROVAL_CONFLICTS_REFUSED',flush=True)
        start_body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':body['expected_goal_hash'],
            'expected_mature_head_hash':body['expected_mature_head_hash'],'preview_hash':first['preview_hash'],
            'cloud_authorized':True,'reason':'合成测试开始'}
        with Session(engine()) as db:started=start.begin(db,p,task_id,created['plan_id'],start_body);db.commit()
        print('START_AND_PARKED_JOB_SAVED',flush=True)
        engine().dispose()
        with Session(engine()) as db:
            assert start.begin(db,p,task_id,created['plan_id'],start_body)==started
            assert db.get(Job,started['job_id']).available_at==store.UNAVAILABLE_TO_GENERAL_WORKERS
            assert db.scalar(select(func.count()).select_from(CloudCall))==0
        print('START_ORIGINAL_REPLAYED',flush=True)
