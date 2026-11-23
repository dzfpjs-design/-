"""Test-only partial uniqueness experiment; no product migration is installed.

The isolated test creates and drops its own index only in a ga_test database.
An index cannot be enabled on real data until all historical duplicates and
all evidence-upload writers have been audited and a deployment is planned.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import workspace_advice_price_recovery_candidate_v2 as recovery
from generic_agents.db import engine
from generic_agents.models import Audit, Evidence
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_business_reasoning_v197 import manifest
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_advice_nonce_price_candidate_v2_pg import real_price_context

INDEX = 'ga_test_one_upload_per_evidence'
CREATE = (f"CREATE UNIQUE INDEX {INDEX} ON audit_events (workspace_id, object_id) "
          "WHERE action = 'evidence.upload'")


def test_test_only_unique_index_blocks_phantom_and_fails_closed_on_old_duplicates(real_price_context, client):
    s = real_price_context
    assert engine().dialect.name == 'postgresql' and engine().url.database.startswith('ga_test_')
    with engine().begin() as db: db.execute(text(CREATE))
    with Session(engine()) as db:
        result = prices.authorize(db, s['principal'], s['body']); db.commit()
    with Session(engine()) as reader:
        assert recovery.recover_only(reader, s['principal'], s['body'])['basis_ref'] == result['basis_ref']
        original = reader.get(Audit, result['basis_ref']['id'])
        evidence_id = original.data['official_evidence_ref']['evidence_id']
        upload = reader.get(Audit, original.data['upload_audit_ref']['id'])
        fields = {'workspace_id': upload.workspace_id, 'actor_id': upload.actor_id,
            'object_id': upload.object_id, 'action': upload.action, 'data': copy.deepcopy(upload.data)}
        def insert_duplicate():
            with Session(engine()) as db:
                db.add(Audit(id=str(uuid4()), **fields))
                try: db.commit(); return 'unexpectedly-saved'
                except IntegrityError: db.rollback(); return 'unique-rejected'
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(insert_duplicate).result(timeout=5) == 'unique-rejected'
        reader.commit()
    # Identical content in two separate Evidence records remains legitimate.
    second_manifest = copy.deepcopy(manifest()); second_manifest['name'] = 'synthetic_unique_second_model'
    second_manifest['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
    model = publish(client, second_manifest)
    second_body = {**s['body'], 'request_id': str(uuid4()), 'model_pin': {key: model[key]
        for key in ('id', 'content_hash', 'release_version')}}
    with Session(engine()) as db:
        second = prices.authorize(db, s['principal'], second_body); db.commit()
        second_record = db.get(Audit, second['basis_ref']['id'])
        second_id = second_record.data['official_evidence_ref']['evidence_id']
        assert evidence_id != second_id
        assert db.get(Evidence, evidence_id).sha256 == db.get(Evidence, second_id).sha256
    with engine().begin() as db: db.execute(text(f'DROP INDEX {INDEX}'))
    # Characterize a prior duplicate: installation must fail, preserving both.
    duplicate_id = str(uuid4())
    with Session(engine()) as db:
        db.add(Audit(id=duplicate_id, **fields)); db.commit()
    with engine().connect() as db:
        with pytest.raises(IntegrityError): db.execute(text(CREATE))
        db.rollback()
    with Session(engine()) as db:
        count = db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id == fields['workspace_id'],
            Audit.object_id == evidence_id, Audit.action == 'evidence.upload'))
        assert count == 2 and db.get(Audit, duplicate_id) is not None
