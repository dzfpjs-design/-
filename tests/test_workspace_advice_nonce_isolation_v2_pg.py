"""Isolation rejection tests for the unwired candidate nonce protocol.

These tests intentionally exercise real PostgreSQL isolation settings, not
application authorization, document custody or the full v310 chain.
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import workspace_advice_nonce_candidate_v2 as candidate
from generic_agents.db import engine
from generic_agents.models import Audit
from tests.test_workspace_advice_nonce_candidate_v2_pg import disposable_pg_only, scene, receipt


@pytest.mark.parametrize('level', ['REPEATABLE READ', 'SERIALIZABLE'])
def test_unsupported_snapshot_isolation_fails_closed_before_command(scene, level):
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + level))
        assert db.scalar(text('SHOW transaction_isolation')).upper() == level
        with pytest.raises(HTTPException):
            candidate.acquire(db, scene['principal'], action=candidate.V1_POLICY_REQUEST,
                task_id=scene['first'], body=scene['body'])
        db.rollback()


def test_repeatable_read_old_snapshot_cannot_claim_a_committed_nonce(scene):
    with Session(engine()) as stale, Session(engine()) as writer:
        stale.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        where = [Audit.workspace_id == scene['principal'].workspace_id,
            Audit.data['request_id'].as_string() == scene['body']['request_id']]
        assert not list(stale.scalars(select(Audit).where(*where)))
        candidate.acquire(writer, scene['principal'], action=candidate.V1_POLICY_REQUEST,
            task_id=scene['first'], body=scene['body'])
        original = receipt(scene, candidate.V1_POLICY_REQUEST, scene['first']); original_id = original.id
        writer.add(original); writer.commit()
        # The old snapshot really cannot see the committed original. The
        # protocol must reject the isolation level rather than infer absence.
        assert not list(stale.scalars(select(Audit).where(*where)))
        with pytest.raises(HTTPException):
            candidate.acquire(stale, scene['principal'], action=candidate.V2_POLICY_REQUEST,
                task_id=scene['second'], body=scene['body'])
        stale.rollback()
    with Session(engine()) as check:
        rows = list(check.scalars(select(Audit).where(*where)))
        assert len(rows) == 1 and rows[0].id == original_id
