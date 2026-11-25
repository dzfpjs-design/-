"""Versioned cold document reads on isolated PG and actual temporary files.

Source/Goal authorization is substituted by the imported ready fixture; this
is not proof of a real consent, preview, Start, cloud call, or mature Goal.
"""
import copy
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as docs
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Evidence, Invocation, Plan, Task
from generic_agents.security import Principal
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def test_cold_document_read_recovers_exact_receipt_and_bytes_after_reconnect(ready):
    s, created, resolved=ready; p=s['principal']
    with Session(engine()) as db:
        docs.process(db,p,s['task_id'],created['job_id']);db.commit()
    engine().dispose()
    with Session(engine()) as db:
        value=docs.cold_read(db,p,db.get(Task,s['task_id']),db.get(Plan,created['plan_id']))
        assert value['content']==resolved['content'] and value['document']['selection']==resolved['selection']
        assert value['document']['approved_selection']==resolved['approved_selection']
        assert value['document']['evidence_ref']['sha256']==resolved['sha256']
        assert value['prepare_job_id']==created['job_id']
        assert value['document']['receipt_ref']==value['receipt_ref']
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))


@pytest.mark.parametrize('fault',['receipt','bytes','session','role','dirty_plan'])
def test_cold_document_read_fails_closed_without_losing_pending_edits(ready,fault):
    s, created, resolved=ready; p=s['principal']
    with Session(engine()) as db:
        docs.process(db,p,s['task_id'],created['job_id']);db.commit()
    if fault in {'receipt','bytes','session'}:
        with Session(engine()) as db:
            receipt=db.scalar(select(Audit).where(Audit.object_id==created['plan_id'],Audit.action==v2.DOCUMENT_PREPARED))
            if fault=='receipt': receipt.data={**receipt.data,'render_sha256':'0'*64}
            if fault=='bytes':
                obj=db.get(Evidence,receipt.data['evidence_ref']['evidence_id']); obj.sha256='0'*64
            if fault=='session': db.get(AuthSession,p.session_id).revoked=True
            db.commit()
    if fault=='role':p=Principal(p.user_id,p.workspace_id,'viewer',p.session_id)
    with Session(engine()) as db:
        task,plan=db.get(Task,s['task_id']),db.get(Plan,created['plan_id'])
        if fault=='dirty_plan':plan.data={**plan.data,'document_preparation':{'untouched':'pending'}}
        with pytest.raises(HTTPException):docs.cold_read(db,p,task,plan)
        if fault=='dirty_plan':assert plan.data['document_preparation']=={'untouched':'pending'}
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))
