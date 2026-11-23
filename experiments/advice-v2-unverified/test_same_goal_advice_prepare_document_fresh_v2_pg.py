"""Fresh real synthetic Goal, original approvals and v2 document workflow.

No paid provider, device, report or real workspace is contacted. The software
observation and approved documents are built through their existing test flow.
"""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Evidence, Job, Plan, Task
from generic_agents.runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet
from generic_agents.security import Principal
from generic_agents.worker import claim
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication
from tests.synthetic_fresh_mature_checkpoint_candidate import save as save_checkpoint
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_same_goal_advice_manual_append_pg import matured_manual_case, managed, scene, reject_non_loopback_network


def test_fresh_goal_real_approval_document_receipt_and_same_number_recovery(matured_manual_case, monkeypatch):
    case = matured_manual_case; source = case.publication.pins['reasoning']
    pin = {key: source[key] for key in ('id', 'content_hash', 'release_version')}
    published = publish(case.client, c.build_manifest(source['data'], pin))
    assert published['status'] == 'published'
    save_checkpoint(case.task_id, case.publication.scene.key)
    with Session(engine()) as db:
        task = db.get(Task, case.task_id)
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == case.principal.user_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert session is not None
        p = Principal(case.principal.user_id, case.workspace_id, 'admin', session.id)
        originals = list(db.scalars(select(RuntimeTaskDocumentSourceSet).where(
            RuntimeTaskDocumentSourceSet.workspace_id == p.workspace_id,
            RuntimeTaskDocumentSourceSet.task_id == task.id)))
        assert len(originals) == 1
        selection = {'kind': 'approved_source_set', 'source_set_ref': {'source_set_id': originals[0].id,
            'source_set_hash': originals[0].source_set_hash}}
        now = db.scalar(select(func.clock_timestamp()))
        policy_body = {'request_id': str(uuid4()), 'expected_goal_hash': task.data['goal_hash'],
            'expected_mature_head_hash': case.head['head_hash'],
            'limits': {'max_tokens': 512, 'max_request_bytes': 50000, 'max_evidence_items': 2},
            'expires_at': (now + timedelta(minutes=20)).isoformat(), 'reason': '隔离合成 v2 政策'}
        issued = policy.authorize(db, p, task.id, policy_body); db.commit()
    print('SYNTHETIC_FRESH_V2_POLICY_COMMITTED', flush=True)
    request = {'request_id': str(uuid4()), 'expected_goal_hash': policy_body['expected_goal_hash'],
        'expected_mature_head_hash': policy_body['expected_mature_head_hash'], 'document_source': selection,
        'reason': '隔离测试准备已批准的合成资料'}
    with Session(engine()) as db:
        created = writer.prepare(db, p, case.task_id, request, issued['policy_ref']); db.commit()
        assert created['replayed'] is False
    assert claim(created['job_id'], kinds=[v2.PREPARE_KIND], workspace_id=p.workspace_id) is None
    print('SYNTHETIC_FRESH_V2_PLAN_AND_JOB_COMMITTED', flush=True)
    with Session(engine()) as db:
        evidence_before = db.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == p.workspace_id))
        assert db.scalar(select(func.count()).select_from(CloudCall).where(CloudCall.workspace_id == p.workspace_id)) == 0
    save = worker.evidence.save_evidence
    def abort_after_save(*args, **kwargs):
        save(*args, **kwargs)
        raise RuntimeError('isolated synthetic failure after file write')
    with monkeypatch.context() as patch:
        patch.setattr(worker.evidence, 'save_evidence', abort_after_save)
        with pytest.raises(RuntimeError), Session(engine()) as db:
            worker.process(db, p, case.task_id, created['job_id'])
    with Session(engine()) as db:
        assert db.get(Job, created['job_id']).status == 'queued'
        assert db.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == p.workspace_id)) == evidence_before
        assert not db.scalar(select(Audit.id).where(Audit.object_id == created['plan_id'],
            Audit.action == v2.DOCUMENT_PREPARED))
    with Session(engine()) as db:
        assert worker.process(db, p, case.task_id, created['job_id'])['replayed'] is False
        db.commit()
    print('SYNTHETIC_FRESH_V2_RECEIPT_COMMITTED', flush=True)
    engine().dispose()
    with Session(engine()) as db:
        task, plan = db.get(Task, case.task_id), db.get(Plan, created['plan_id'])
        prep, job = v2.read_prepared(db, p, task, plan)
        assert prep['request'] == request and job.id == created['job_id'] and job.status == 'succeeded'
        assert common.read(db, p, task)['queued_v2'] == 1
        assert worker.process(db, p, case.task_id, job.id)['replayed'] is True
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == p.workspace_id)))
        db.rollback()
    with Session(engine()) as db:
        assert writer.prepare(db, p, case.task_id, request, issued['policy_ref']) == {**created, 'replayed': True}
        db.rollback()
    print('FRESH_REAL_SYNTHETIC_V2_DOCUMENT_AND_REPLAY_VERIFIED', flush=True)
