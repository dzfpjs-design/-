"""Test-only PostgreSQL concurrent-index failure recovery, never a migration."""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents.db import engine
from generic_agents.models import Audit
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_advice_nonce_price_candidate_v2_pg import real_price_context

INDEX = 'ga_test_concurrent_unique_upload'


def test_concurrent_unique_index_failure_retains_originals_and_leaves_invalid_index(real_price_context):
    s = real_price_context
    assert engine().dialect.name == 'postgresql' and engine().url.database.startswith('ga_test_')
    with Session(engine()) as db:
        basis = prices.authorize(db, s['principal'], s['body'])['basis_ref']
        price = db.get(Audit, basis['id']); upload = db.get(Audit, price.data['upload_audit_ref']['id'])
        duplicate = Audit(id=str(uuid4()), workspace_id=upload.workspace_id, actor_id=upload.actor_id,
            object_id=upload.object_id, action=upload.action, data=copy.deepcopy(upload.data))
        duplicate_id, upload_id = duplicate.id, upload.id
        db.add(duplicate); db.commit()
    with engine().connect().execution_options(isolation_level='AUTOCOMMIT') as db:
        with pytest.raises(IntegrityError):
            db.execute(text(f"CREATE UNIQUE INDEX CONCURRENTLY {INDEX} ON audit_events "
                "(workspace_id, object_id) WHERE action = 'evidence.upload'"))
        state = db.execute(text("SELECT i.indisvalid, i.indisready FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname=:name"), {'name': INDEX}).one()
        assert state.indisvalid is False
        with Session(engine()) as check:
            assert check.get(Audit, upload_id) is not None and check.get(Audit, duplicate_id) is not None
        # Test-only cleanup of this isolated failed index, never its originals.
        db.execute(text(f'DROP INDEX CONCURRENTLY {INDEX}'))
        assert db.scalar(text('SELECT to_regclass(:name)'), {'name': INDEX}) is None
