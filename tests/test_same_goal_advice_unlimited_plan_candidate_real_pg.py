"""Real synthetic maturity, policy and approved originals; no Plan writer."""
import copy
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as old
from generic_agents import same_goal_advice_unlimited_plan_candidate_v2 as subject
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents.db import engine
from generic_agents.goal_evaluation_provider_v288 import KEY
from generic_agents.models import Audit, AuthSession, CloudCall, Evidence, Invocation, Job, Plan, Task
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network
from tests.test_same_goal_advice_restored_provider_v1_pg import restore_case


def counts(db):
    return {model.__name__:db.scalar(select(func.count()).select_from(model))
        for model in (Audit,CloudCall,Evidence,Invocation,Job,Plan)}


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE'), reason='isolated synthetic checkpoint only')
def test_real_policy_and_approved_sources_derive_non_authorizing_candidate(monkeypatch, reject_non_loopback_network):
    case = restore_case(monkeypatch)
    with Session(engine()) as db:
        task = db.get(Task,case.task_id); now = db.scalar(select(func.clock_timestamp()))
        policy_body = {'request_id':str(uuid4()),'expected_goal_hash':task.data['goal_hash'],
            'expected_mature_head_hash':task.data[KEY]['head_hash'],
            'limits':{'max_tokens':512,'max_request_bytes':50000,'max_evidence_items':2},
            'expires_at':(now+timedelta(minutes=10)).isoformat(),'reason':'仅隔离合成资料的候选验证'}
        issued = policy.authorize(db,case.principal,task.id,policy_body); db.commit()
        assert not issued['cloud_send_authorized']
    with Session(engine()) as db:
        task = db.get(Task,case.task_id)
        original = db.scalar(select(Plan).where(Plan.task_id==task.id, Plan.data['schema'].as_string()==old.PLAN))
        assert original is not None
        body = copy.deepcopy(original.data['preparation_snapshot']['request'])
        body['request_id'] = str(uuid4()); body['reason'] = '保留原批准资料，核对候选准备内容'
        before = counts(db)
        result = subject.derive_candidate(db,case.principal,task.id,body,issued['policy_ref'])
        assert result['preparation_candidate']['request'] == body
        assert result['preparation_candidate']['budget']['policy_ref'] == issued['policy_ref']
        assert result['preparation_candidate']['budget']['cost_policy'] == 'unlimited'
        assert all(result[key] is False for key in ('plan_write_authorized','prepare_job_authorized',
            'preview_authorized','cloud_send_authorized'))
        assert counts(db) == before and not db.new and not db.dirty and not db.deleted
        db.rollback()
    with Session(engine()) as db:
        task = db.get(Task,case.task_id); task.data['unsaved_hint'] = '未跟踪编辑'
        assert task not in db.dirty
        with pytest.raises(HTTPException):
            subject.derive_candidate(db,case.principal,task.id,body,issued['policy_ref'])
        assert task.data['unsaved_hint'] == '未跟踪编辑'
        db.rollback()
    with Session(engine()) as db:
        bad = {**issued['policy_ref'],'record_hash':'0'*64}
        with pytest.raises(HTTPException): subject.derive_candidate(db,case.principal,case.task_id,body,bad)
        db.rollback()
    with Session(engine()) as db:
        db.get(AuthSession,case.principal.session_id).revoked = True;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): subject.derive_candidate(db,case.principal,case.task_id,body,issued['policy_ref'])
    print('REAL_V2_POLICY_AND_APPROVED_DOCUMENT_CANDIDATE_VERIFIED',flush=True)
