"""Root-executed PG evidence-reader tests, not a new HTTP/query product.

sound_scene uses normal upload/install/register/Task/worker APIs and the pinned
local CPU YAMNet runtime. Its silence WAV and business configuration are
synthetic engineering fixtures, not environmental-sound quality evidence.
No model/SDK/ACK success is mocked here. Corruption and stale principals below
are explicit disposable-database counterexamples, never fabricated receipts.
"""
import copy
from datetime import timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.sound_models import EnvironmentSoundRun, SoundInput
from generic_agents import task_sound_query_evidence as subject
from generic_agents import workflow_sound
from generic_agents.worker import process_once
from tests.test_workflow_sound_native_pg import sound_scene, original_scene, ack, good
from tests.test_sound_result_settlement_pg import failed_scene


def principal(s):
    # Internal reader entry: actual authenticated HTTP identity and DB
    # membership, not a fabricated access grant or fake HTTP endpoint.
    me = good(s.client.get('/api/me'))
    return Principal(me['user_id'], me['workspace_id'], me['role'], 'task-sound-reader-pg')


def quantities(s):
    workspace = s.attached['workspace_id']
    with Session(engine()) as db:
        result = {}
        for cls in (EnvironmentSoundRun, SoundInput, m.Task, m.Job, m.Invocation,
                    m.Observation, m.Event, m.Memory, m.Audit, m.CloudCall, m.AudioRun):
            result[cls.__name__] = sorted((row.id, row.version,
                getattr(row, 'status', None), digest(getattr(row, 'data', {})),
                getattr(row, 'reserved_tokens', None), getattr(row, 'charged_tokens', None))
                for row in db.scalars(select(cls).where(cls.workspace_id == workspace)))
        return result


def capture(s, *, p=None, instance_id=None, clock=None, **scope):
    who = p or principal(s)
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        stamp = db.scalar(text('SELECT transaction_timestamp()'))
        transaction = db.get_transaction()
        value = subject.capture(db, who, instance_id or s.current_release['instance_id'],
            as_of=clock(stamp) if clock else stamp, **scope)
        assert db.get_transaction() is transaction and db.in_transaction()
        assert not db.new and not db.dirty and not db.deleted
        assert value['as_of'] == stamp.astimezone(timezone.utc).isoformat()
        return value


def readonly_capture(s, **scope):
    before = quantities(s)
    value = capture(s, **scope)
    assert quantities(s) == before
    body = {key: value for key, value in value.items() if key != 'snapshot_hash'}
    assert digest(body) == value['snapshot_hash']
    for record in value['records']:
        assert digest({key: item for key, item in record.items() if key != 'record_hash'}) == record['record_hash']
    return value


def test_real_native_pending_ack_and_later_review_freeze_distinct_sound_evidence_without_notes_or_new_execution(sound_scene):
    s = sound_scene
    pending = readonly_capture(s, task_id=s.task['id'], entity_id=s.entity['id'])
    old_pending = copy.deepcopy(pending); before_hash = digest(pending)
    record = pending['records'][0]
    assert record['source_family'] == 'workflow_environment_sound_candidate_v1'
    assert record['candidate_available'] is True
    assert record['reception']['state'] == 'waiting_data'
    assert record['data_values_available'] is False and record['values'] is None
    assert record['timing']['recorded_at'] is None and record['timing']['received_at'] is None
    assert record['reference']['result_hash'] == s.run['result_hash']
    assert record['reference']['issued_audit'] is not None
    assert pending['inventory']['basis'] == 'registered_run_scan'
    assert pending['inventory']['expected_sources_verified'] is False
    assert 'sound_snapshot' not in record and 'result' not in record and 'content' not in record

    actual = ack(s)  # Existing real consumer and SDK; not a fabricated ACK.
    received = readonly_capture(s, task_id=s.task['id'], entity_id=s.entity['id'])['records'][0]
    classification = actual['run']['result']['classification']
    assert received['reception']['state'] == 'received' and received['data_values_available'] is True
    assert received['values'] == {
        'window_count': len(classification['windows']),
        'candidate_class_count': len(classification['candidates']),
        'candidate_window_count': len({i for item in classification['candidates'] for i in item['window_indices']}),
        'candidates': classification['candidates']}
    assert received['reception']['memory']['id'] == actual['reception']['memory_id']
    assert received['reception']['receipt_hash'] == actual['reception']['receipt']['receipt_hash']
    assert received['reference']['result_hash'] == s.run['result_hash']
    assert received['timing']['received_at'] is not None
    assert pending == old_pending and digest(pending) == before_hash

    long_note = '仅候选人工说明，不外发也不放入查询证据。' * 50
    review = good(s.client.post('/api/sounds/runs/' + s.run['id'] + '/reviews', json={
        'schema': 'sound-review-v1', 'request_id': 'reader-review-' + uuid4().hex,
        'version': actual['run']['version'], 'verdict': 'uncertain', 'note': long_note}), 201)
    frozen_received = copy.deepcopy(received)
    latest = readonly_capture(s, task_id=s.task['id'])['records'][0]
    assert latest['values'] == frozen_received['values']
    assert latest['reference']['result_hash'] == frozen_received['reference']['result_hash']
    assert latest['reference']['review_receipt_hashes'] == [review['receipt_hash']]
    assert long_note not in str(latest) and 'note' not in latest
    assert received == frozen_received and pending == old_pending
    assert latest['quality']['score_calibration_verified'] is False
    assert latest['quality']['physical_state_verified'] is False


def test_reader_requires_exact_current_rr_readonly_time_and_strict_limits_without_committing(sound_scene):
    s = sound_scene; p = principal(s); identity = s.current_release['instance_id']
    valid = readonly_capture(s, clock=lambda value: value.astimezone(timezone(timedelta(hours=8))))
    assert valid['as_of'].endswith('+00:00')
    for limit in (True, 1.0, 0, 1001):
        with pytest.raises(HTTPException) as failure:
            capture(s, limit=limit)
        assert failure.value.status_code == 422
    for change in (lambda value: value-timedelta(seconds=1), lambda value: value.replace(tzinfo=None)):
        with pytest.raises(HTTPException) as failure:
            capture(s, clock=change)
        assert failure.value.status_code == 422
    for transaction_mode in ('READ COMMITTED, READ ONLY', 'REPEATABLE READ, READ WRITE'):
        with Session(engine()) as db:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + transaction_mode))
            stamp = db.scalar(text('SELECT transaction_timestamp()'))
            with pytest.raises(HTTPException) as failure:
                subject.capture(db, p, identity, as_of=stamp)
            assert failure.value.status_code == 409
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        stamp = db.scalar(text('SELECT transaction_timestamp()'))
        db.add(m.DataRun(workspace_id=p.workspace_id, owner_id=p.user_id,
            question='unsaved fixture counterexample', status='queued', data={}))
        with pytest.raises(HTTPException) as failure:
            subject.capture(db, p, identity, as_of=stamp)
        assert failure.value.status_code == 409  # No autoflush/INSERT in the readonly session.


def test_reader_keeps_actual_membership_and_instance_task_object_scope_failures_distinct_from_empty(sound_scene):
    s = sound_scene; p = principal(s)
    before = quantities(s)
    for stale in (Principal(str(uuid4()), p.workspace_id, p.role, p.session_id),
                  Principal(p.user_id, str(uuid4()), p.role, p.session_id),
                  Principal(p.user_id, p.workspace_id, 'viewer' if p.role != 'viewer' else 'admin', p.session_id)):
        with pytest.raises(HTTPException) as failure:
            capture(s, p=stale)
        assert failure.value.status_code == 403
    with Session(engine()) as db:
        other = m.DomainInstance(workspace_id=p.workspace_id,
            namespace='other_sound_reader_' + uuid4().hex[:12], status='draft', data={})
        db.add(other); db.flush(); other_id = other.id; db.commit()
    with pytest.raises(HTTPException) as failure:
        capture(s, instance_id=other_id, task_id=s.task['id'])
    assert failure.value.status_code == 404
    assert readonly_capture(s, instance_id=other_id)['records'] == []
    with pytest.raises(HTTPException) as failure:
        capture(s, task_id=s.task['id'], entity_id=str(uuid4()))
    assert failure.value.status_code == 422
    assert quantities(s) == before  # The explicit other-instance fixture is outside these producer inventories.


@pytest.mark.parametrize('damage', ['original_blob', 'independent_memory', 'input_audit'])
def test_current_bad_original_or_anchor_retains_an_owned_blocked_reference_and_no_values(sound_scene, damage):
    s = sound_scene; actual = ack(s)
    frozen = readonly_capture(s); old = copy.deepcopy(frozen)
    restore = None
    with Session(engine()) as db:
        if damage == 'original_blob':
            source = db.get(SoundInput, s.attached['id']); evidence = db.get(m.Evidence, source.evidence_id)
            path = settings().storage_dir / evidence.storage_key
            original = path.read_bytes(); path.write_bytes(b'explicit synthetic test corruption')
            restore = lambda: path.write_bytes(original)
        elif damage == 'independent_memory':
            db.delete(db.get(m.Memory, actual['reception']['memory_id']))
        else:
            run = db.get(EnvironmentSoundRun, s.run['id'])
            db.delete(db.get(m.Audit, run.data['input_audit_id']))
        db.commit()
    try:
        blocked = readonly_capture(s, task_id=s.task['id'])
        assert blocked['inventory']['complete'] is True
        assert blocked['source_integrity_complete'] is False
        assert len(blocked['records']) == 1
        record = blocked['records'][0]
        assert record['run_id'] == s.run['id'] and record['task_id'] == s.task['id']
        assert record['reception']['state'] == 'blocked_integrity'
        assert record['values'] is None and record['data_values_available'] is False
        assert record['reception']['memory'] is None and record['reception']['receipt_hash'] is None
        assert record['integrity_issue']
        assert frozen == old
    finally:
        if restore:
            restore()


def test_registered_run_limit_is_hard_and_uses_two_real_task_invocations_without_truncated_totals(sound_scene):
    s = sound_scene
    second = good(s.client.post('/api/workspace/instances/' + s.current_release['instance_id'] + '/tasks', json={
        'release_id': s.current_release['id'], 'business_id': s.current_release['available_business_ids'][0],
        'entity_ids': [s.entity['id']], 'sound_ids': [s.attached['id']],
        'title': '第二份合成原音任务用于真实扫描上限反例', 'request_id': str(uuid4())}), 201)
    assert process_once(job_id=second['workflow_runtime']['automatic']['job_id'], deliver=False)
    before = quantities(s)
    with pytest.raises(HTTPException) as failure:
        capture(s, limit=1)
    assert failure.value.status_code == 422
    assert '截断' in str(failure.value.detail)
    full = readonly_capture(s, limit=2)
    assert full['inventory']['complete'] is True and len(full['inventory']['scanned_run_ids']) == 2
    assert len(full['records']) == 2 and all(record['values'] is None for record in full['records'])
    narrow = readonly_capture(s, task_id=s.task['id'], limit=1)
    assert [record['run_id'] for record in narrow['records']] == [s.run['id']]
    assert quantities(s) == before


def test_original_sound_input_instance_keeps_a_corrupt_mutable_task_run_scope_from_disappearing(sound_scene):
    s = sound_scene
    with Session(engine()) as db:
        task = db.get(m.Task, s.task['id']); run = db.get(EnvironmentSoundRun, s.run['id'])
        wrong = str(uuid4())
        task.data = {**task.data, 'instance_id': wrong}
        run.data = {**run.data, 'instance_id': wrong}
        db.commit()
    value = readonly_capture(s)
    assert value['inventory']['scanned_run_ids'] == [s.run['id']]
    assert len(value['records']) == 1 and value['source_integrity_complete'] is False
    assert value['records'][0]['reception']['state'] == 'blocked_integrity'
    assert value['records'][0]['values'] is None


def test_real_completed_model_with_failed_parent_handoff_is_visible_as_candidate_without_received_values(failed_scene):
    s = failed_scene
    value = readonly_capture(s, task_id=s.task['id'])['records'][0]
    assert value['producer_status'] == 'failed'
    assert value['candidate_available'] is True
    assert value['reception']['state'] == 'producer_failed'
    assert value['values'] is None and value['data_values_available'] is False
    assert value['reference']['result_hash'] == s.run['result_hash']
    assert value['reference']['result_audit'] is not None
