"""Expose, rather than hide, the candidate's duplicate-upload phantom gap.

All rows are synthetic and disposable. This is a characterization test,
not a statement that the current helper is production-ready.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import workspace_advice_price_recovery_candidate_v2 as recovery
from generic_agents.db import engine
from generic_agents.models import Audit
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_advice_nonce_price_candidate_v2_pg import real_price_context


def test_duplicate_upload_insert_can_arrive_after_receipt_read(real_price_context):
    s = real_price_context
    with Session(engine()) as db:
        basis = prices.authorize(db, s['principal'], s['body'])['basis_ref']; db.commit()
    with Session(engine()) as original_reader:
        recovered = recovery.recover_only(original_reader, s['principal'], s['body'])
        assert recovered['basis_ref'] == basis
        upload_id = original_reader.get(Audit, basis['id']).data['upload_audit_ref']['id']
        original = original_reader.get(Audit, upload_id)
        fields = {'workspace_id': original.workspace_id, 'actor_id': original.actor_id,
            'object_id': original.object_id, 'action': original.action, 'data': copy.deepcopy(original.data)}
        def insert_duplicate():
            with Session(engine()) as db:
                row = Audit(id=str(uuid4()), **fields); db.add(row); db.commit(); return row.id
        with ThreadPoolExecutor(max_workers=1) as pool:
            # This commit succeeds while the first transaction holds SHARE
            # locks on the original rows: those locks are not predicate locks.
            duplicate_id = pool.submit(insert_duplicate).result(timeout=5)
        original_reader.commit()
    with Session(engine()) as db:
        assert db.get(Audit, duplicate_id) is not None
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['body'])
        db.rollback()
