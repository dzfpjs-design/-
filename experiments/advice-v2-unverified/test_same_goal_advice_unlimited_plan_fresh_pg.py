"""Long fresh synthetic goal, actual policy and approved document custody."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents import same_goal_advice_unlimited_plan_candidate_v2 as candidate
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents.db import engine
from generic_agents.models import AuthSession, CloudCall, Job, Plan, Task
from generic_agents.runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet
from generic_agents.security import Principal
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_same_goal_advice_manual_append_pg import matured_manual_case, managed, scene, reject_non_loopback_network


def test_fresh_real_goal_policy_and_approved_documents_remain_non_authorizing(matured_manual_case):
    case=matured_manual_case; source=case.publication.pins['reasoning']
    pin={'id':source['id'],'content_hash':source['content_hash'],'release_version':source['release_version']}
    registered=publish(case.client,c.build_manifest(source['data'],pin));assert registered['status']=='published'
    with Session(engine()) as db:
        task=db.get(Task,case.task_id)
        session=db.scalar(select(AuthSession).where(AuthSession.user_id==case.principal.user_id,
            AuthSession.revoked.is_(False),AuthSession.expires_at>func.clock_timestamp()).order_by(AuthSession.created_at.desc()))
        assert session is not None
        principal=Principal(case.principal.user_id,case.workspace_id,'admin',session.id)
        sets=list(db.scalars(select(RuntimeTaskDocumentSourceSet).where(
            RuntimeTaskDocumentSourceSet.workspace_id==case.workspace_id,RuntimeTaskDocumentSourceSet.task_id==task.id)))
        assert len(sets)==1
        source_ref={'kind':'approved_source_set','source_set_ref':{'source_set_id':sets[0].id,'source_set_hash':sets[0].source_set_hash}}
        now=db.scalar(select(func.clock_timestamp()))
        body={'request_id':str(uuid4()),'expected_goal_hash':task.data['goal_hash'],
            'expected_mature_head_hash':case.head['head_hash'],
            'limits':{'max_tokens':512,'max_request_bytes':50000,'max_evidence_items':2},
            'expires_at':(now+timedelta(minutes=20)).isoformat(),'reason':'真实合成目标的受控政策验证'}
        issued=policy.authorize(db,principal,task.id,body);db.commit()
    request={'request_id':str(uuid4()),'expected_goal_hash':body['expected_goal_hash'],
        'expected_mature_head_hash':body['expected_mature_head_hash'],
        'document_source':source_ref,'reason':'核对原批准资料，尚不创建准备任务'}
    with Session(engine()) as db:
        prior_plans=db.scalar(select(func.count()).select_from(Plan).where(Plan.workspace_id==case.workspace_id))
        prior_jobs=db.scalar(select(func.count()).select_from(Job).where(Job.workspace_id==case.workspace_id))
        result=candidate.derive_candidate(db,principal,case.task_id,request,issued['policy_ref'])
        assert result['preparation_candidate']['request']==request
        assert result['preparation_candidate']['budget']['policy_ref']==issued['policy_ref']
        assert not any(result[k] for k in ('plan_write_authorized','prepare_job_authorized','preview_authorized','cloud_send_authorized'))
        assert db.scalar(select(func.count()).select_from(Plan).where(Plan.workspace_id==case.workspace_id))==prior_plans
        assert db.scalar(select(func.count()).select_from(Job).where(Job.workspace_id==case.workspace_id))==prior_jobs
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id==case.workspace_id)))
    print('FRESH_REAL_V2_PLAN_CANDIDATE_AND_SOURCE_VERIFIED',flush=True)
