"""Exercise the typed recovery branch with explicit Session and fence I/O doubles."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_continuation_execution_recovery_v314 as r


def uid(number):
    return str(UUID(int=number))


@pytest.fixture
def scene(monkeypatch):
    p = SimpleNamespace(workspace_id=uid(1), user_id=uid(2))
    task, plan = uid(3), uid(4)
    key = f'continuation-read-v314:{plan}:1'
    inputs = {'field': 'humidity', 'operation': 'mean',
        'records': [{'entity_id': uid(5), 'version': 1, 'attributes': {'humidity': 10}}]}
    job = r.Job(id=uid(6), workspace_id=p.workspace_id, kind=r.c.KIND,
        idempotency_key=key, status='running', lease_token=uid(7),
        lease_until=datetime(2026, 11, 2, tzinfo=timezone.utc), data={
            'schema': r.c.JOB, 'actor_id': p.user_id, 'task_id': task, 'plan_id': plan,
            'submission_hash': 'a' * 64, 'input_hash': r.digest(inputs), 'invocation_key': key})
    approval = {'schema': r.c.APPROVAL, 'task_id': task, 'plan_id': plan,
        'job_id': job.id, 'grant_ref': {'id': uid(8), 'record_hash': 'b' * 64, 'version': 1},
        'submission_hash': job.data['submission_hash'], 'input_hash': job.data['input_hash']}
    snapshot = r.tools.manifest()
    call = r.Invocation(id=uid(9), workspace_id=p.workspace_id, version=3,
        capability_id=uid(10), idempotency_key=key, status='running', data={
            'job_id': job.id, 'lease_token': uid(11), 'approval': approval, 'role': 'data',
            'side_effect': False, 'input_hash': r.digest(inputs), 'inputs': inputs,
            'capability_hash': r.digest(snapshot), 'capability_snapshot': snapshot})
    v = SimpleNamespace(p=p, job=job, call=call, calls=[call], added=[], queries=[])

    class DB:
        no_autoflush = nullcontext()

        def scalars(self, statement):
            assert statement.column_descriptions[0]['entity'] is r.Invocation
            assert statement._for_update_arg is not None
            assert p.workspace_id in statement.compile().params.values()
            v.queries.append(statement)
            return iter(v.calls)

        def add(self, row):
            v.added.append(row)

    v.db = DB()
    monkeypatch.setattr(r, 'inspect', lambda row: SimpleNamespace(persistent=row is job, session=v.db))
    monkeypatch.setattr(r.leases, 'fenced', lambda db: db is v.db)
    monkeypatch.setattr(r.leases, 'ownership', lambda db: {'job_id': job.id, 'lease_token': job.lease_token})
    return v


def test_original_success_is_preserved_and_recovery_is_only_mode(scene):
    v = scene
    v.call.status = 'succeeded'
    v.call.data['output'] = {'value': 10}
    original = deepcopy(v.call.data)
    assert r.recover_calls(v.db, v.p, v.job) == 1
    assert (v.call.status, v.call.version, v.call.data) == ('succeeded', 3, original)
    assert v.job.data['recovery_only'] is True and not v.added


def test_running_previous_token_becomes_unknown_with_worker_audit(scene):
    v = scene
    original = deepcopy(v.call.data)
    assert r.recover_calls(v.db, v.p, v.job) == 1
    assert v.call.status == 'unknown' and v.call.version == 4
    assert all(v.call.data[key] == value for key, value in original.items())
    assert v.call.data['recovery']['previous_lease_token'] == original['lease_token']
    assert v.job.data['recovery_only'] is True
    event, = v.added
    assert (event.action, event.object_id, event.actor_id) == (
        'worker.invocation_recovered', v.call.id, v.p.user_id)


@pytest.mark.parametrize('damage', ['duplicate', 'approval', 'key', 'input', 'same_token', 'actor'])
def test_conflicting_custody_is_rejected_before_mutation(scene, damage):
    v = scene
    if damage == 'duplicate':
        v.calls.append(v.call)
    elif damage == 'approval':
        v.call.data['approval']['job_id'] = uid(99)
    elif damage == 'key':
        v.call.idempotency_key += ':other'
    elif damage == 'input':
        v.call.data['inputs']['operation'] = 'sum'
    elif damage == 'same_token':
        v.call.data['lease_token'] = v.job.lease_token
    else:
        v.job.data['actor_id'] = uid(99)
    with pytest.raises(HTTPException) as exc:
        r.recover_calls(v.db, v.p, v.job)
    assert exc.value.status_code == 409
    assert v.call.status == 'running' and v.call.version == 3
    assert 'recovery_only' not in v.job.data and not v.added


@pytest.mark.parametrize('recovery_only', [False, True])
def test_absent_sdk_never_changes_existing_recovery_mode(scene, recovery_only):
    v = scene
    v.calls.clear()
    v.job.data['recovery_only'] = recovery_only
    assert r.recover_calls(v.db, v.p, v.job) == 0
    assert v.job.data['recovery_only'] is recovery_only and not v.added
