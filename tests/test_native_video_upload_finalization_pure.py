"""The actual upload producer's optional source context is not a binding."""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import native_receipts as receipts


class DB:
    def __init__(self, prior=None):
        self.prior = prior
        self.reads = 0
        self.added = []

    def scalar(self, query):
        self.reads += 1
        return self.prior

    def add(self, value):
        self.added.append(value)


@pytest.fixture
def scene(monkeypatch):
    p = NS(user_id='actor', workspace_id='workspace')
    run = NS(id='run', status='succeeded', data={'source_context': None})
    job = NS(id='parent', data={'run_id': run.id})
    def scoped(db, cls, identity, actual_p):
        assert cls is receipts.m.VideoRun and identity == run.id and actual_p is p
        return run
    monkeypatch.setattr(receipts, 'scoped', scoped)
    return p, run, job, DB()


@pytest.mark.parametrize('context', [None, {}, {'session_id': 'capture-only'}, {'domain_binding': None}])
def test_plain_upload_or_unbound_source_finishes_without_native_job(scene, context):
    p, run, job, db = scene
    run.data = {'source_context': context}
    assert receipts.enqueue_video(db, p, job) is None
    assert db.reads == 0 and db.added == []


def test_original_dict_business_binding_still_queues_once_with_exact_original_ids(scene):
    p, run, job, db = scene
    run.data = {'source_context': {'domain_binding': {'instance_id': 'original-instance', 'entity_id': 'original-entity'}}}
    receipts.enqueue_video(db, p, job)
    assert db.reads == 1 and len(db.added) == 1
    child = db.added[0]
    assert child.kind == 'sync_native_receipts' and child.idempotency_key == 'native-sync:parent'
    assert child.workspace_id == p.workspace_id
    assert child.data == {'instance_id': 'original-instance', 'entity_ids': ['original-entity'],
        'actor_id': p.user_id, 'original_video_run_id': run.id}
    db.prior = 'original-child'
    receipts.enqueue_video(db, p, job)
    assert len(db.added) == 1


@pytest.mark.parametrize('context', [False, [], 'bad',
    {'domain_binding': False}, {'domain_binding': []}, {'domain_binding': 'bad'},
    {'domain_binding': {}}, {'domain_binding': {'instance_id': 'I'}},
    {'domain_binding': {'instance_id': '', 'entity_id': 'E'}},
    {'domain_binding': {'instance_id': 'I', 'entity_id': 1}}])
def test_malformed_context_never_becomes_an_ordinary_upload_or_business_binding(scene, context):
    p, run, job, db = scene
    run.data = {'source_context': context}
    with pytest.raises(HTTPException) as error:
        receipts.enqueue_video(db, p, job)
    assert error.value.status_code == 409 and db.reads == 0 and db.added == []


def test_non_successful_run_cannot_enqueue_a_native_receipt(scene):
    p, run, job, db = scene
    run.status = 'failed'
    run.data = {'source_context': {'domain_binding': {'instance_id': 'I', 'entity_id': 'E'}}}
    receipts.enqueue_video(db, p, job)
    assert db.added == [] and db.reads == 0
