"""Synthetic mature Goal: finish/review v1, then local v2 preparation.

The restored checkpoint, approvals and clocks are never modified. The v1
provider is an explicit loopback test service; v2 makes no cloud request.
"""
import copy
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents import same_goal_advice_execution_v308 as execution
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents import same_goal_advice_review_v308 as review
from generic_agents import same_goal_advice_store_v302 as old
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.goal_evaluation_provider_v288 import KEY
from generic_agents.models import Audit, CloudCall, Evidence, Job, Plan, Task
from tests import test_same_goal_advice_common_provider_v1_pg as provider
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network
from tests.test_same_goal_advice_restored_provider_v1_pg import restore_case


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE'), reason='dedicated synthetic checkpoint only')
def test_real_mature_goal_reviewed_v1_then_v2_receipt_and_replay(monkeypatch, reject_non_loopback_network):
    case = restore_case(monkeypatch); p = case.principal
    # This resumes exactly the original synthetic Start; no policy, deadline,
    # status or approval is forged or altered to force the test forward.
    monkeypatch.setattr(provider, 'prepare_and_start', lambda *_: None)
    provider.test_v1_loopback_response_is_persisted_and_counted_once(case, monkeypatch, reject_non_loopback_network)
    monkeypatch.setenv('DEEPSEEK_API_KEY', '')
    monkeypatch.setattr(settings(), 'cloud_allowed_providers', '')
    with Session(engine()) as db:
        plan = db.scalar(select(Plan).where(Plan.workspace_id == p.workspace_id, Plan.task_id == case.task_id,
            Plan.data['schema'].as_string() == c.PLAN))
        assert plan.status == 'ready_for_review'
        request = copy.deepcopy(plan.data['preparation_snapshot']['request'])
        result_hash = plan.data[old.RESULT_KEY]['result_ref']['record_hash']
        saved = review.review(db, p, case.task_id, plan.id, {'request_id': str(uuid4()),
            'expected_plan_version': plan.version, 'result_hash': result_hash,
            'decision': 'accepted', 'reason': '隔离测试审核原合成建议'})
        db.commit()
        assert saved['decision'] == 'accepted' and saved['can_dispatch'] is False
    print('SYNTHETIC_V1_REAL_REVIEW_COMMITTED', flush=True)
    with Session(engine()) as db:
        task = db.get(Task, case.task_id); now = db.scalar(select(func.clock_timestamp()))
        issued = policy.authorize(db, p, task.id, {'request_id': str(uuid4()),
            'expected_goal_hash': task.data['goal_hash'], 'expected_mature_head_hash': task.data[KEY]['head_hash'],
            'limits': {'max_tokens': 512, 'max_request_bytes': 50000, 'max_evidence_items': 2},
            'expires_at': (now + timedelta(minutes=10)).isoformat(), 'reason': '隔离合成 v2 政策'})
        db.commit()
    request['request_id'] = str(uuid4()); request['reason'] = '隔离测试准备原批准资料'
    with Session(engine()) as db:
        created = writer.prepare(db, p, case.task_id, request, issued['policy_ref']); db.commit()
        assert created['replayed'] is False
    print('SYNTHETIC_V2_PLAN_AND_JOB_COMMITTED', flush=True)
    with Session(engine()) as db:
        before_evidence = db.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == p.workspace_id))
        before_cloud = db.scalar(select(func.count()).select_from(CloudCall).where(CloudCall.workspace_id == p.workspace_id))
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
        assert db.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == p.workspace_id)) == before_evidence
        assert not db.scalar(select(Audit.id).where(Audit.object_id == created['plan_id'],
            Audit.action == v2.DOCUMENT_PREPARED))
    with Session(engine()) as db:
        assert worker.process(db, p, case.task_id, created['job_id'])['replayed'] is False
        db.commit()
    with Session(engine()) as db:
        task, plan = db.get(Task, case.task_id), db.get(Plan, created['plan_id'])
        prep, job = v2.read_prepared(db, p, task, plan)
        assert prep['request'] == request and job.id == created['job_id'] and job.status == 'succeeded'
        assert db.scalar(select(func.count()).select_from(CloudCall).where(CloudCall.workspace_id == p.workspace_id)) == before_cloud
        assert worker.process(db, p, case.task_id, job.id)['replayed'] is True
        db.rollback()
    with Session(engine()) as db:
        replay = writer.prepare(db, p, case.task_id, request, issued['policy_ref'])
        assert replay == {**created, 'replayed': True}
        db.rollback()
    print('SYNTHETIC_REAL_GOAL_V2_DOCUMENT_AND_REPLAY_VERIFIED', flush=True)
