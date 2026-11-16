"""Real saved inference plus an injected final-handoff fault, then local recovery.

Only the final writer is fault-injected. Model, Invocation, original step,
failure audit, command Job, outbox and independent ACK use production code.
"""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.models import Audit, Job, Invocation, Observation, Event, Memory
from generic_agents.sound_models import EnvironmentSoundRun
from generic_agents import native_sound_receipts as receipts
from generic_agents import native_sound_provenance as source
from generic_agents import sound_result_settlement as recovery
from generic_agents.worker import process_once, deliver_events
from tests.test_workflow_sound_native_pg import sound_scene
from tests.test_sound_inputs_pg import original_scene, good, pause
from tests.test_instance_workspace import complete


@pytest.fixture()
def failed_scene(request, monkeypatch, original_scene):
    def fail_final_handoff(db, p, job):
        raise HTTPException(409, '显式注入最终交接故障；模型不被替换')
    with monkeypatch.context() as patch:
        patch.setattr(receipts, 'emit_completed', fail_final_handoff)
        s = request.getfixturevalue('sound_scene')
    with Session(engine()) as db:
        parent = db.get(Job, s.job_id)
        assert parent.status == 'failed' and parent.data.get('error')
        assert db.get(EnvironmentSoundRun, s.run['id']).status == 'succeeded'
        assert len(db.scalars(select(Audit).where(Audit.action == recovery.PARENT_FAILED, Audit.object_id == parent.id)).all()) == 1
        s.original_error = parent.data['error']
    assert s.entry['reception']['state'] == 'producer_failed'
    s.recovery_base = '/api/sounds/runs/' + s.run['id']
    return s


def prepare(s, request_id=None):
    current = good(s.client.get(s.recovery_base + '/settlement'))
    assert current['action']['can_request'], current['action']
    value = {'schema': 'sound-result-settlement-v1', 'request_id': request_id or 'sound-recover-' + uuid4().hex,
        'task_version': current['action']['task_version'], 'run_version': current['action']['run_version'],
        'reason': '原模型及步骤已完成；明确只继续交接原成果。', 'confirm_original_result_handoff': True}
    return value, json.dumps(value, ensure_ascii=False, indent=2).encode()


def post(s, raw):
    return s.client.post(s.recovery_base + '/settlements', content=raw, headers={'Content-Type': 'application/json'})


def lookup(s, body, raw):
    return s.client.get(s.recovery_base + '/settlements/requests', params={'request_id': body['request_id'],
        'raw_sha256': hashlib.sha256(raw).hexdigest(), 'body_hash': digest(body),
        'expected_task_version': body['task_version'], 'expected_run_version': body['run_version']})


def test_true_final_handoff_failure_is_locally_settled_without_reinference_or_forged_ack(failed_scene):
    s = failed_scene; original_result = copy.deepcopy(s.run['result']); body, raw = prepare(s)
    command = good(post(s, raw), 201); assert command['producer_status'] == 'failed' and not command['model_rerun']
    assert good(post(s, raw)) == command
    assert good(lookup(s, body, raw))['result'] == command
    pending = good(s.client.get(s.recovery_base + '/settlement'))
    assert pending['latest']['status'] == 'queued' and pending['reception']['state'] == 'local_handoff_pending'
    assert pending['reception']['receipt'] is None
    assert process_once(job_id=command['job_id'], deliver=False)
    current = good(s.client.get(s.recovery_base + '/settlement'))
    assert current['latest']['status'] == 'succeeded' and current['reception']['state'] == 'waiting_data', current
    assert current['reception']['receipt'] is None
    task = good(s.client.get('/api/workspace/tasks/' + s.task['id']))
    assert complete(s.client, task).status_code == 409
    observation_id = current['reception']['observation_id']
    deliver_events(workspace_id=s.attached['workspace_id'], observation_id=observation_id, event_kind='observation.data')
    received = good(s.client.get(s.recovery_base + '/settlement'))
    assert received['reception']['state'] == 'received'
    proof = received['reception']['receipt']['settlement']
    assert proof['producer_status'] == 'failed' and proof['settlement_job_status'] == 'succeeded'
    assert received['reception']['receipt']['producer_status'] == 'failed'
    assert good(s.client.get(s.recovery_base))['result'] == original_result
    assert good(post(s, raw)) == command and not process_once(job_id=command['job_id'], deliver=False)
    finished = good(complete(s.client, good(s.client.get('/api/workspace/tasks/' + s.task['id']))))
    assert finished['status'] == 'workflow_completed'
    with Session(engine()) as db:
        assert db.get(Job, s.job_id).status == 'failed' and db.get(Job, s.job_id).data.get('error') == s.original_error
        assert len(db.scalars(select(Invocation)).all()) == 1
        assert len(db.scalars(select(EnvironmentSoundRun)).all()) == 1
        assert len(db.scalars(select(Observation).where(Observation.id == observation_id)).all()) == 1
        event = db.get(Event, received['reception']['event_id'])
        assert event.status == 'received'
        assert len(db.scalars(select(Memory).where(Memory.data['event_id'].as_string() == event.id)).all()) == 1


def test_missing_real_parent_failure_audit_cannot_be_reconstructed_from_failed_status(failed_scene):
    s = failed_scene; body, raw = prepare(s)
    with Session(engine()) as db:
        db.delete(db.scalar(select(Audit).where(Audit.action == recovery.PARENT_FAILED, Audit.object_id == s.job_id))); db.commit()
    assert not good(s.client.get(s.recovery_base + '/settlement'))['action']['can_request']
    assert post(s, raw).status_code == 409
    with Session(engine()) as db: assert not db.scalars(select(Job).where(Job.kind == recovery.KIND)).all()


def test_uncommitted_old_body_is_only_superseded_after_real_monotonic_review_cas(failed_scene):
    s = failed_scene; body, raw = prepare(s)
    unknown = good(lookup(s, body, raw))
    assert not unknown['found'] and not unknown['superseded'] and unknown['supersession'] is None
    review = good(s.client.post(s.recovery_base + '/reviews', json={
        'schema': 'sound-review-v1', 'request_id': 'advance-review-' + uuid4().hex, 'version': s.run['version'],
        'verdict': 'uncertain', 'note': '另一页真实保存核查，旧交接申请尚未提交。'}), 201)
    obsolete = good(lookup(s, body, raw))
    assert not obsolete['found'] and obsolete['superseded'] and obsolete['result'] is None
    assert obsolete['supersession']['run_version'] == review['version'] > body['run_version']
    assert post(s, raw).status_code == 409
    current_body, current_raw = prepare(s)
    assert current_body['request_id'] != body['request_id'] and current_body['run_version'] == review['version']
    saved = good(post(s, current_raw), 201)
    assert good(lookup(s, current_body, current_raw))['result'] == saved
    with Session(engine()) as db:
        assert len(db.scalars(select(Job).where(Job.kind == recovery.KIND)).all()) == 1
        assert len(db.scalars(select(Invocation)).all()) == 1


def test_pause_after_queue_blocks_local_handoff_but_keeps_original_command_recoverable(failed_scene):
    s = failed_scene; body, raw = prepare(s); command = good(post(s, raw), 201); pause(s)
    assert process_once(job_id=command['job_id'], deliver=False)
    result = good(lookup(s, body, raw))
    assert result['found'] and result['result'] == command
    assert result['current']['latest']['status'] == 'failed' and not result['current']['action']['can_request']
    with Session(engine()) as db:
        assert len(db.scalars(select(Invocation)).all()) == 1
        assert not db.scalars(select(Observation).where(Observation.source_record_id.startswith('EnvironmentSoundRun:' + s.run['id']))).all()


def test_two_requests_serialize_one_handoff_job_and_preserve_both_original_nonces(failed_scene):
    s = failed_scene; one, raw_one = prepare(s); two = {**one, 'request_id': 'second-recover-' + uuid4().hex}
    raw_two = json.dumps(two, ensure_ascii=False).encode()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda raw: post(s, raw), [raw_one, raw_two]))
    assert sorted(r.status_code for r in replies) == [201, 409], [r.text[:500] for r in replies]
    with Session(engine()) as db:
        assert len(db.scalars(select(Job).where(Job.kind == recovery.KIND)).all()) == 1
        assert len(db.scalars(select(Audit).where(Audit.action == recovery.COMMAND)).all()) == 1
        assert len(db.scalars(select(Invocation)).all()) == 1


def test_removed_command_marker_is_integrity_failure_not_an_unknown_or_new_request(failed_scene):
    s = failed_scene; body, raw = prepare(s); command = good(post(s, raw), 201)
    with Session(engine()) as db:
        job = db.get(Job, command['job_id']); job.data = {k: v for k, v in job.data.items() if k != 'sound_settlement_command'}; db.commit()
    assert lookup(s, body, raw).status_code == 409 and post(s, raw).status_code == 409
    with Session(engine()) as db:
        assert len(db.scalars(select(Job).where(Job.kind == recovery.KIND)).all()) == 1


def test_missing_finished_audit_hides_received_trust_without_resigning_or_rerunning(failed_scene):
    s = failed_scene; body, raw = prepare(s); command = good(post(s, raw), 201)
    assert process_once(job_id=command['job_id'], deliver=False)
    current = good(s.client.get(s.recovery_base + '/settlement'))
    observation_id = current['reception']['observation_id']
    deliver_events(workspace_id=s.attached['workspace_id'], observation_id=observation_id, event_kind='observation.data')
    with Session(engine()) as db:
        db.delete(db.scalar(select(Audit).where(Audit.action == recovery.FINISHED, Audit.object_id == command['job_id']))); db.commit()
    current = good(s.client.get(s.recovery_base + '/settlement'))
    assert current['reception']['state'] == 'blocked_integrity' and current['reception']['receipt'] is None
    assert good(lookup(s, body, raw))['result'] == command
    with Session(engine()) as db: assert len(db.scalars(select(Invocation)).all()) == 1
