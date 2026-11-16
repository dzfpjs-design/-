"""Real Task/CPU/SDK/independent ACK, with synthetic engineering audio only.

Root runs these against disposable PG and the fixed local sound runtime. No
model, source capture, SDK, ACK, or permission result is mocked. The adapter
does not create a workflow trace: the real gateway caller must persist its
returned output in the original Invocation, using its own readonly Session.
"""
import copy
from datetime import timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.sound_models import EnvironmentSoundRun, SoundInput
from generic_agents import task_sound_query_evidence as evidence
from generic_agents import workflow_sound_receipt_status_v261 as subject
from tests.test_workflow_sound_native_pg import sound_scene, original_scene, ack, good


def principal(s):
    me = good(s.client.get('/api/me'))
    return Principal(me['user_id'], me['workspace_id'], me['role'], 'sound-receipt-status-pg')


def inventory(p):
    with Session(engine()) as db:
        return {cls.__name__: sorted((row.id, row.version,
            getattr(row, 'status', None), digest(getattr(row, 'data', {})))
            for row in db.scalars(select(cls).where(cls.workspace_id == p.workspace_id)))
            for cls in (EnvironmentSoundRun, SoundInput, m.Task, m.Job, m.Invocation,
                m.Observation, m.Event, m.Memory, m.Audit, m.CloudCall, m.AudioRun)}


def read(s, *, p=None, task_id=None, verify_source=False):
    p = p or principal(s); before = inventory(p)
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        stamp = db.scalar(text('SELECT transaction_timestamp()'))
        transaction = db.get_transaction()
        value = subject.execute(db, p, task_id or s.task['id'])
        assert db.get_transaction() is transaction and db.in_transaction()
        assert not db.new and not db.dirty and not db.deleted
        assert value['as_of'] == stamp.astimezone(timezone.utc).isoformat()
        if verify_source:
            source = evidence.capture(db, p, value['instance_id'],
                task_id=value['task_id'], as_of=stamp)
            assert value['source_snapshot_hash'] == source['snapshot_hash']
            assert [item['source_record_hash'] for item in value['runs']] == [
                item['record_hash'] for item in source['records']]
    assert inventory(p) == before
    Draft202012Validator(subject.output_schema()).validate(value)
    assert value['snapshot_hash'] == digest({key: item for key, item in value.items()
        if key != 'snapshot_hash'})
    assert value['registered_count'] == sum(value[key] for key in
        ('received_count', 'waiting_count', 'blocked_count'))
    assert value['all_registered_received'] is (
        value['registered_count'] > 0 and value['received_count'] == value['registered_count'])
    assert value['expected_sources_verified'] is False
    forbidden = {'scores', 'candidates', 'values', 'sound_classification', 'classification',
        'content', 'raw', 'attributes', 'anomalies', 'note'}
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            assert not set(item) & forbidden
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return value


def test_manifest_is_exact_readonly_data_tool_with_server_task_binding():
    value = subject.manifest().model_dump(mode='json')
    assert subject.validate_manifest(value) == value
    assert value['kind'] == 'tool' and value['allowed_agent_roles'] == ['data']
    assert value['execution'] == {'adapter': 'builtin', 'handler': subject.HANDLER,
        'side_effect': False, 'input_bindings': {'task_id': 'task_identifier'}}
    assert value['input_schema']['required'] == ['task_id']
    assert value['input_schema']['additionalProperties'] is False
    assert value['data_policy'] == {
        'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False}
    mutations = [lambda item: item['allowed_agent_roles'].append('perception'),
        lambda item: item['execution']['input_bindings'].update(task_id='object_ids'),
        lambda item: item['execution'].update(side_effect=True),
        lambda item: item['data_policy'].update(cloud_allowed=True)]
    for mutate in mutations:
        bad = copy.deepcopy(value); mutate(bad)
        with pytest.raises(HTTPException) as failure:
            subject.validate_manifest(bad)
        assert failure.value.status_code == 422
    assert subject.manifest().model_dump(mode='json') == value


def test_real_registered_run_waits_then_independent_ack_changes_only_a_new_snapshot(sound_scene):
    s = sound_scene
    pending = read(s, verify_source=True); frozen = copy.deepcopy(pending)
    assert (pending['registered_count'], pending['received_count'],
        pending['waiting_count'], pending['blocked_count']) == (1, 0, 1, 0)
    assert pending['all_registered_received'] is False
    original = pending['runs'][0]
    assert original['run_id'] == s.run['id'] and original['reception_state'] == 'waiting_data'
    assert original['memory'] is None and original['receipt_hash'] is None
    actual = ack(s)  # Existing workspace-scoped real Event consumer, no fabricated receipt.
    received = read(s, verify_source=True)
    assert (received['registered_count'], received['received_count'],
        received['waiting_count'], received['blocked_count']) == (1, 1, 0, 0)
    assert received['all_registered_received'] is True
    record = received['runs'][0]
    assert record['run_id'] == original['run_id']
    assert record['reception_state'] == 'received'
    assert record['memory']['id'] == actual['reception']['memory_id']
    assert record['receipt_hash'] == actual['reception']['receipt']['receipt_hash']
    assert record['result_hash'] == s.run['result_hash']
    assert pending == frozen and pending['snapshot_hash'] != received['snapshot_hash']
    assert pending['source_snapshot_hash'] != received['source_snapshot_hash']


def test_real_queued_task_with_selected_original_but_no_registered_run_is_not_complete(sound_scene):
    s = sound_scene
    task = good(s.client.post('/api/workspace/instances/' + s.current_release['instance_id']
        + '/tasks', json={'release_id': s.current_release['id'],
            'business_id': s.current_release['available_business_ids'][0],
            'entity_ids': [s.entity['id']], 'sound_ids': [s.attached['id']],
            'title': '登记范围为空不冒充原音齐全', 'request_id': str(uuid4())}), 201)
    # Normal Task and selected original exist; its queued model Job is not run.
    value = read(s, task_id=task['id'])
    assert value['runs'] == [] and value['registered_count'] == 0
    assert value['all_registered_received'] is False
    assert value['expected_sources_verified'] is False


def test_entry_requires_caller_actual_rr_readonly_and_refuses_dirty_session(sound_scene):
    s = sound_scene; p = principal(s)
    for mode in ('READ COMMITTED, READ ONLY', 'REPEATABLE READ, READ WRITE'):
        with Session(engine()) as db:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + mode))
            with pytest.raises(HTTPException) as failure:
                subject.execute(db, p, s.task['id'])
            assert failure.value.status_code == 409
            assert db.in_transaction()  # Entry neither commits nor rolls back caller's transaction.
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        db.add(m.DataRun(workspace_id=p.workspace_id, owner_id=p.user_id,
            question='仅未保存反例', status='queued', data={}))
        with pytest.raises(HTTPException) as failure:
            subject.execute(db, p, s.task['id'])
        assert failure.value.status_code == 409 and db.new
    for invalid in (True, 1, 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA', 'not-a-task'):
        with Session(engine()) as db:
            with pytest.raises(HTTPException) as failure:
                subject.execute(db, p, invalid)
            assert failure.value.status_code == 422
    read(s)


def test_actual_membership_and_task_scope_errors_do_not_become_empty_success(sound_scene):
    s = sound_scene; p = principal(s)
    for stale in (Principal(str(uuid4()), p.workspace_id, p.role, p.session_id),
            Principal(p.user_id, str(uuid4()), p.role, p.session_id),
            Principal(p.user_id, p.workspace_id, 'viewer' if p.role != 'viewer' else 'admin', p.session_id)):
        with pytest.raises(HTTPException) as failure:
            read(s, p=stale)
        assert failure.value.status_code == 403
    with pytest.raises(HTTPException) as failure:
        read(s, task_id=str(uuid4()))
    assert failure.value.status_code == 404


@pytest.mark.parametrize('damage', ['independent_memory', 'input_audit'])
def test_broken_owned_ack_or_independent_source_anchor_is_blocked_without_values(sound_scene, damage):
    s = sound_scene; actual = ack(s)
    old = read(s); frozen = copy.deepcopy(old)
    with Session(engine()) as db:
        if damage == 'independent_memory':
            db.delete(db.get(m.Memory, actual['reception']['memory_id']))
        else:
            run = db.get(EnvironmentSoundRun, s.run['id'])
            db.delete(db.get(m.Audit, run.data['input_audit_id']))
        db.commit()  # Explicit disposable-DB corruption, not a fabricated accepted source.
    value = read(s)
    assert (value['registered_count'], value['received_count'],
        value['waiting_count'], value['blocked_count']) == (1, 0, 0, 1)
    assert value['all_registered_received'] is False
    record = value['runs'][0]
    assert record['run_id'] == s.run['id'] and record['reception_state'] == 'blocked_integrity'
    assert record['memory'] is None and record['receipt_hash'] is None
    assert record['integrity_issue']
    assert old == frozen and old['received_count'] == 1
