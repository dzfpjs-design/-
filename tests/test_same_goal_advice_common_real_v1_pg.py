"""Long isolated synthetic v1 custody journey; never use user or cloud data.

All authority, Task, worker, Evidence, consent and accounting use real local
application code and disposable PG. Official price bytes and the provider are
explicit synthetic inputs; all network except loopback is rejected.
"""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_execution_v308 as execution
from generic_agents import same_goal_advice_frame_v305 as frames
from generic_agents import same_goal_advice_preview_v307 as preview
from generic_agents import same_goal_advice_price_v307 as price
from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents import runtime_task_documents as documents
from generic_agents.db import engine
from generic_agents.evidence import authorize_cloud
from generic_agents.models import AuthSession, CloudCall, Evidence, Job, Plan, Task
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_same_goal_advice_manual_append_pg import (
    matured_manual_case, managed, scene, reject_non_loopback_network,
)
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication
from tests.test_same_goal_advice_price_v307 import html
from tests.synthetic_advice_checkpoint import save_if_requested


def test_real_v1_prepare_and_start_count_in_isolated_pg(matured_manual_case, monkeypatch):
    case = matured_manual_case
    source = case.publication.pins['reasoning']
    pin = {key: source[key] for key in ('id', 'content_hash', 'release_version')}
    publish(case.client, contracts.build_manifest(source['data'], pin))
    with Session(engine()) as db:
        task = db.get(Task, case.task_id)
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == case.principal.user_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert session is not None
        actor = Principal(case.principal.user_id, case.workspace_id, 'admin', session.id)
        source_sets = list(db.scalars(select(documents.SourceSet).where(documents.SourceSet.task_id == task.id,
            documents.SourceSet.workspace_id == case.workspace_id)))
        assert len(source_sets) == 1
        selected = {'kind': 'approved_source_set', 'source_set_ref': {'source_set_id': source_sets[0].id,
            'source_set_hash': source_sets[0].source_set_hash}}
        goal_hash = task.data['goal_hash']; now = db.scalar(select(func.clock_timestamp()))
        granted = quota.authorize(db, actor, task.id, {'request_id': str(uuid4()),
            'expected_goal_hash': goal_hash, 'expected_mature_head_hash': case.head['head_hash'],
            'limits': {'max_tokens': 512, 'max_request_bytes': 50000, 'max_evidence_items': 2,
                'max_cost_minor_units': 100, 'currency': 'CNY'},
            'expires_at': (now + timedelta(minutes=45)).isoformat(), 'reason': '隔离合成目标的政策测试'})
        db.commit(); assert granted['cloud_send_authorized'] is False
    print('V1_POLICY_COMMITTED', flush=True)
    with Session(engine()) as db:
        created = store.prepare(db, actor, case.task_id, {'request_id': str(uuid4()),
            'expected_goal_hash': goal_hash, 'expected_mature_head_hash': case.head['head_hash'],
            'document_source': selected, 'reason': '隔离合成目标资料准备测试'})
        db.commit()
    plan_id, job_id = created['plan_id'], created['prepare_job_id']
    print('V1_PREPARATION_QUEUED', flush=True)
    assert process_once(job_id, kinds=[contracts.PREPARE_KIND], deliver=False, workspace_id=case.workspace_id)
    with Session(engine()) as db:
        task, plan, job = db.get(Task, case.task_id), db.get(Plan, plan_id), db.get(Job, job_id)
        assert job.status == 'succeeded' and plan.status == 'awaiting_consent'
        frame = frames.build_current(db, actor, task, plan)
        model_id = frame['model_pin']['id']
        for evidence_id in frame['evidence_ids']:
            item = db.get(Evidence, evidence_id)
            authorize_cloud(db, actor, evidence_id, model_id, item.version, '仅隔离测试模拟模型调用')
        db.commit()
    print('V1_DOCUMENTS_AND_CONSENT_COMMITTED', flush=True)
    monkeypatch.setattr(price, '_fetch', lambda: (html(), 'text/html; charset=utf-8'))
    response = case.client.post('/api/workspace/business-advice/price-bases', json={
        'request_id': str(uuid4()), 'model_pin': frame['model_pin'], 'validity_seconds': 3600,
        'reason': '隔离测试模拟价格材料'})
    assert response.status_code == 201, response.text
    with Session(engine()) as db:
        plan = db.get(Plan, plan_id)
        view = preview.prepare(db, actor, case.task_id, plan_id, {'request_id': str(uuid4()),
            'expected_plan_version': plan.version, 'expected_goal_hash': goal_hash,
            'expected_mature_head_hash': case.head['head_hash'], 'reason': '核对合成内容的匿名发送预览'})
        db.commit()
    print('V1_PREVIEW_COMMITTED', flush=True)
    with Session(engine()) as db:
        result = execution.start(db, actor, case.task_id, plan_id, {'request_id': str(uuid4()),
            'expected_plan_version': view['plan_version'], 'expected_goal_hash': goal_hash,
            'expected_mature_head_hash': case.head['head_hash'], 'preview_hash': view['preview_hash'],
            'cloud_authorized': True, 'reason': '隔离测试确认合成预览后开始'})
        db.commit()
    print('V1_START_COMMITTED', flush=True)
    with Session(engine()) as db:
        task = db.get(Task, case.task_id)
        assert execution.budget_calls(db, actor, task) == 1
        assert common.read(db, actor, task) == {'v1_calls': 1, 'queued_v2': 0}
        assert result['invocation_created'] is False
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))
    print('V1_NONZERO_COUNTERS_MATCH', flush=True)
    save_if_requested(case.task_id, plan_id, case.publication.scene.key)
