"""Independent recovery attacks through the normal GET on real saved needs.

Only isolated persisted rows are damaged after normal creation. No successful
query, original request, receipt or independent audit is constructed by hand.
Root executes on disposable PostgreSQL; this file never repairs the records.
"""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import workspace_need_request_recovery_v248 as recovery
from generic_agents.contracts import digest
from generic_agents.models import (Acquisition, Audit, CloudCall, DataRun,
    DomainInstance, InstanceRelease, Job, Task)
from tests.test_workspace_need_request_recovery_pg_v248 import scene, published_scene


def endpoint(s):
    return '/api/workspace/instances/' + s.release['instance_id'] + '/data-need-requests'


def lookup(s, **params):
    return s.client.get(endpoint(s), params={
        'request_id': s.body['request_id'], 'expected_body_hash': s.body_hash, **params})


def inventory(db, s):
    return {model.__name__: db.scalar(select(func.count()).select_from(model).where(
        model.workspace_id == s.p.workspace_id)) for model in
        (DataRun, Job, Audit, Task, Acquisition, CloudCall)}


def saved_rows(db, s):
    result = {}
    for model in (DataRun, Job, Audit):
        result[model.__name__] = {row.id: digest({
            'data': row.data, 'version': row.version,
            **{key: getattr(row, key) for key in ('owner_id', 'question', 'status',
                'kind', 'idempotency_key', 'actor_id', 'action', 'object_id')
                if hasattr(row, key)}}) for row in db.scalars(select(model).where(
                    model.workspace_id == s.p.workspace_id))}
    return result


def assert_blocked_without_repair(s, expected=409):
    with Session(s.bound) as db:
        before = inventory(db, s)
        original_rows = saved_rows(db, s)
        original = copy.deepcopy(db.get(DataRun, s.need['id']).data)
    response = lookup(s)
    assert response.status_code == expected, response.text[:500]
    with Session(s.bound) as db:
        assert inventory(db, s) == before
        assert saved_rows(db, s) == original_rows
        assert db.get(DataRun, s.need['id']).data == original


@pytest.mark.parametrize('damage', ['marker', 'receipt_audit', 'both'])
def test_modern_request_never_downgrades_when_marker_or_audit_is_removed(scene, damage):
    s = scene
    with Session(s.bound) as db:
        row = db.get(DataRun, s.need['id']); data = copy.deepcopy(row.data)
        assert data['workspace_need_context']['creation_protocol'] == recovery.PROTOCOL
        if damage in {'marker', 'both'}:
            data.pop(recovery.MARKER)
        if damage in {'receipt_audit', 'both'}:
            audit = db.scalar(select(Audit).where(Audit.workspace_id == s.p.workspace_id,
                Audit.action == recovery.ACTION, Audit.object_id == row.id))
            assert audit is not None
            db.delete(audit)
        row.data = data; db.commit()
    assert_blocked_without_repair(s)


@pytest.mark.parametrize('damage', ['scope_only', 'scope_rehashed', 'body_hash_rehashed', 'same_label_other_field'])
def test_self_consistent_json_cannot_replace_independent_initial_context(scene, damage):
    s = scene
    with Session(s.bound) as db:
        row = db.get(DataRun, s.need['id']); data = copy.deepcopy(row.data)
        context = data['workspace_need_context']
        if damage in {'scope_only', 'scope_rehashed'}:
            context['instance_id'] = str(uuid4())
        elif damage == 'body_hash_rehashed':
            context['body_hash'] = digest({'different': 'original body'})
        else:
            fields = data['request']['fields']
            old = fields[0]; other = old + '_different_identity'
            data['request']['fields'] = [other, *fields[1:]]
            labels = context['field_labels']; title = labels.pop(old)
            labels[other] = title
            context['query_request_hash'] = digest(data['request'])
        if damage != 'scope_only':
            data['workspace_need_context_hash'] = digest(context)
        row.data = data; db.commit()
    # Neither a readable identical title nor a fresh self hash renews the
    # original Job's independent context/body/request pin. Bad scope is 409.
    assert_blocked_without_repair(s)


@pytest.mark.parametrize('damage', ['kind', 'query_id', 'actor_id', 'context_hash', 'flag', 'missing'])
def test_original_initial_job_is_required_even_when_typed_receipt_remains(scene, damage):
    s = scene
    with Session(s.bound) as db:
        job = db.scalar(select(Job).where(Job.workspace_id == s.p.workspace_id,
            Job.idempotency_key == 'workspace-need:' + s.need['id'] + ':initial'))
        assert job is not None
        if damage == 'missing': db.delete(job)
        elif damage == 'kind': job.kind = 'unrelated_query'
        else:
            data = copy.deepcopy(job.data)
            key, value = {'query_id': ('query_id', str(uuid4())),
                'actor_id': ('actor_id', str(uuid4())),
                'context_hash': ('context_hash', digest('different context')),
                'flag': ('existing_records_only', False)}[damage]
            data[key] = value; job.data = data
        db.commit()
    assert_blocked_without_repair(s)


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'wrong_actor', 'different_json_type'])
def test_original_creation_audit_cannot_be_replaced_by_an_unrelated_or_resealed_claim(scene, damage):
    s = scene
    other = None
    if damage == 'wrong_actor':
        response = s.client.post('/api/users', json={'username': 'need_integrity_' + uuid4().hex[:12],
            'password': uuid4().hex, 'role': 'operator'})
        assert response.status_code == 201, response.text[:500]
        other = response.json()['id']
    with Session(s.bound) as db:
        audit = db.scalar(select(Audit).where(Audit.workspace_id == s.p.workspace_id,
            Audit.action == 'workspace.need_created', Audit.object_id == s.need['id']))
        assert audit is not None
        if damage == 'missing': db.delete(audit)
        elif damage == 'duplicate':
            db.add(Audit(workspace_id=audit.workspace_id, actor_id=audit.actor_id,
                action=audit.action, object_id=audit.object_id, data=copy.deepcopy(audit.data)))
        elif damage == 'wrong_actor':
            # A different real existing actor keeps the FK and supplies no
            # authority. The fixture's account is never impersonated.
            audit.actor_id = other
        else:
            audit.data = {**audit.data, 'existing_records_only': 1}
        db.commit()
    assert_blocked_without_repair(s)


def test_duplicate_nonce_rows_are_never_arbitrarily_selected_even_with_same_body(scene):
    s = scene
    with Session(s.bound) as db:
        original = db.get(DataRun, s.need['id'])
        # An explicit duplicate-row corruption sentinel. It has no invented
        # successful Audit or Job and is never a valid second saved need.
        db.add(DataRun(workspace_id=original.workspace_id, owner_id=original.owner_id,
            question=original.question, status=original.status, data=copy.deepcopy(original.data)))
        db.commit()
    assert_blocked_without_repair(s)


def test_missing_original_release_is_integrity_failure_not_a_new_lookup_miss(scene):
    s = scene
    with Session(s.bound) as db:
        instance = db.get(DomainInstance, s.release['instance_id'])
        instance.active_release_id = None
        db.flush()
        db.delete(db.get(InstanceRelease, s.release['id']))
        db.commit()
    assert_blocked_without_repair(s)
