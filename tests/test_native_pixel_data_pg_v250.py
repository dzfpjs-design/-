"""Real registered model/Task/receiver tests. Root alone executes this suite.

Only post-success faults are injected. No seeded Invocation, result, receiver
Memory or successful inference substitute is used.
"""
import copy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.worker import process_once, deliver_events
from generic_agents.instance_data_receipts import observation_receipt
from generic_agents.native_goals import verify_acceptance_inputs
from generic_agents import native_pixel_provenance_v250 as origin
from generic_agents import native_pixel_daily_v250 as daily
from tests.test_workflow_pixel_native_pg_v250 import (flow_scene, native_scene, managed_scene,
    approved_start, pytestmark, ack, good)
from tests.test_instance_workspace import complete
from tests.test_pixel_change_pg_v249 import pause


def test_actual_aggregate_mounts_all_machine_query_and_rebuild_paths_once(client):
    paths = client.app.openapi()['paths']
    base = '/api/workspace/instances/{instance_id}'
    assert 'get' in paths[base + '/pixel-machine-results']
    assert 'get' in paths[base + '/pixel-machine-daily']
    assert 'post' in paths[base + '/pixel-machine-daily/rebuild']
    assert 'get' in paths[base + '/pixel-machine-daily/rebuild-requests']
    assert not any('/api/workspace/api/workspace/' in path for path in paths)


def observation(s):
    with Session(engine()) as db:
        rows = origin.observations(db, s.p, s.run_id)
        assert len(rows) == 1
        return rows[0].id, rows[0].business_day


def entry(s):
    return good(s.client.get('/api/workspace/tasks/' + s.task_id + '/pixel-results'))['records'][0]


def query(s, **params):
    return s.client.get(s.base + '/pixel-machine-results', params=params)


def day(s, registration_day):
    return good(s.client.get(s.base + '/pixel-machine-daily', params={'registration_day': registration_day}))


def inventory(s):
    with Session(engine()) as db:
        return {cls.__name__: sorted((r.id, r.version, digest(r.data)) for r in db.scalars(
            select(cls).where(cls.workspace_id == s.workspace_id)))
            for cls in (m.WorkflowPixelRun, m.Task, m.Invocation, m.Observation, m.Event, m.Memory, m.Audit)}


def test_query_and_day_redact_until_ack_then_keep_current_human_work_separate(flow_scene):
    s = flow_scene; observation_id, registration_day = observation(s)
    before = inventory(s)
    pending = good(query(s, task_id=s.task_id, entity_id=s.entity['id']))['records'][0]
    assert pending['run']['result'] is None and not pending['data_values_available']
    assert pending['reception']['state'] == 'waiting_data'
    first = day(s, registration_day)
    assert first['counts'] == {'registered_results': 1, 'received_results': 0, 'waiting_results': 1}
    assert all(v is None for v in first['metrics'].values()) and first['summary_state'] == 'not_saved'
    assert inventory(s) == before
    assert complete(s.client, good(s.client.get('/api/workspace/tasks/' + s.task_id))).status_code == 409
    ack(s)
    received = day(s, registration_day)
    assert received['summary_state'] == 'saved_current' and received['summary']['version'] == 1
    assert received['counts']['received_results'] == 1 and received['metrics']['candidate_pixels'] >= 0
    with Session(engine()) as db:
        saved_day = copy.deepcopy(db.get(m.Memory, received['summary']['id']).data)
    task = good(s.client.get('/api/workspace/tasks/' + s.task_id))
    draft = good(s.client.put('/api/workspace/tasks/' + s.task_id + '/note', json={
        'version': task['version'], 'step_id': task['workflow_runtime']['current_step_id'],
        'note': '先保存草稿，尚未采用候选。'}))
    assert entry(s)['human_receipts'] == []
    good(complete(s.client, draft, note='已看当次两图与候选，仅采用为技术依据，不认定现场无变化'))
    after = day(s, registration_day)
    assert after['summary'] == received['summary']
    assert after['human_receipts_basis'] == daily.HUMAN_BASIS
    assert after['records'][0]['human_receipts'][0]['note'].startswith('已看当次')
    with Session(engine()) as db:
        assert db.get(m.Memory, received['summary']['id']).data == saved_day
        original = db.get(m.Observation, observation_id)
        assert original.observed_at is None and original.data['extracted'] == {'attributes': {}, 'anomalies': []}
    stable = inventory(s)
    ack(s); day(s, registration_day); good(query(s))
    assert inventory(s) == stable


@pytest.mark.parametrize('fault', ['missing_memory', 'duplicate_memory', 'memory_receipt',
    'received_clock', 'packet_bool_type', 'remove_marker', 'remove_issued_audit', 'remove_source_audit'])
def test_independent_ack_and_physical_origin_faults_do_not_become_accepted_values(flow_scene, fault):
    s = flow_scene; observation_id, registration_day = observation(s); ack(s)
    with Session(engine()) as db:
        original = db.get(m.Observation, observation_id)
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.idempotency_key == 'data:' + observation_id))
        memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == s.workspace_id,
            m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence'))
        if fault == 'missing_memory': db.delete(memory)
        elif fault == 'duplicate_memory':
            db.add(m.Memory(workspace_id=s.workspace_id, status=memory.status, text=memory.text,
                            data=copy.deepcopy(memory.data)))
        elif fault == 'memory_receipt':
            value = copy.deepcopy(memory.data); value['data_reception']['result_hash'] = '0' * 64; memory.data = value
        elif fault == 'received_clock': event.received_at = None
        elif fault == 'packet_bool_type':
            value = copy.deepcopy(original.data); value[origin.PACKET]['candidate_only'] = 1; original.data = value
        elif fault == 'remove_marker':
            original.data = {k: v for k, v in original.data.items() if k != origin.ISSUED}
        elif fault == 'remove_issued_audit':
            for audit in origin.audits(db, s.p, origin.ISSUED_ACTION, observation_id): db.delete(audit)
        elif fault == 'remove_source_audit':
            for audit in origin.audits(db, s.p, origin.SOURCE_ACTION, original.source_id): db.delete(audit)
        db.commit()
    damaged = entry(s)
    assert damaged['reception']['state'] == 'blocked_integrity'
    assert damaged['reception']['receipt'] is None and not damaged['data_values_available']
    data = good(query(s))['records'][0]
    assert data['run']['result'] is None
    assert complete(s.client, good(s.client.get('/api/workspace/tasks/' + s.task_id))).status_code == 409
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as refused:
            verify_acceptance_inputs(db, s.p, db.get(m.Task, s.task_id), lock_sources=False)
        assert refused.value.status_code == 409
    blocked = day(s, registration_day)
    assert blocked['summary_state'] == 'blocked_integrity'
    assert blocked['metrics'] is None and blocked['counts'] is None


def test_new_pixel_plan_cannot_be_removed_even_by_rehashing_mutable_release_metadata(flow_scene):
    s = flow_scene
    with Session(engine()) as db:
        release = db.get(m.InstanceRelease, s.release['id']); value = copy.deepcopy(release.data)
        del value['snapshot']['native_pixel_results_v250']
        release.data = value; release.configuration_hash = digest(value['snapshot'])
        package = db.get(m.Capability, release.package_id)
        package_value = copy.deepcopy(package.data)
        package_value['specification']['configuration_hash'] = release.configuration_hash
        package.data = package_value; package.content_hash = package.validated_hash = digest(package_value)
        db.commit()
    assert s.client.get('/api/workspace/tasks/' + s.task_id + '/pixel-results').status_code == 409
    with Session(engine()) as db:
        original = origin.observations(db, s.p, s.run_id)[0]
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.idempotency_key == 'data:' + original.id))
        assert event.status != 'received'


def test_stripped_forward_completed_receipt_cannot_hide_an_independently_issued_source(flow_scene):
    s = flow_scene
    with Session(engine()) as db:
        task = db.get(m.Task, s.task_id); value = copy.deepcopy(task.data)
        value['workflow_runtime']['receipts'] = [r for r in value['workflow_runtime']['receipts']
                                               if r.get('output', {}).get('pixel_run_id') != s.run_id]
        task.data = value; db.commit()
    response = s.client.get('/api/workspace/tasks/' + s.task_id)
    if response.status_code == 200:
        assert response.json()['permissions']['can_complete_step'] is False
    else: assert response.status_code == 409
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as refused:
            verify_acceptance_inputs(db, s.p, db.get(m.Task, s.task_id), lock_sources=False)
        assert refused.value.status_code == 409


def rebuild_body(s, value):
    return {'schema': 'native-pixel-daily-rebuild-v250', 'request_id': 'daily-rebuild-' + uuid4().hex,
        'registration_day': value['registration_day'], 'version': value['rebuild']['version'],
        'expected_summary_hash': value['rebuild']['expected_summary_hash'],
        'expected_blocked_fingerprint': value['rebuild']['expected_blocked_fingerprint'],
        'reason': '原始成果恢复后，明确仅重建登记日机器汇总', 'confirm_rebuild': True}


def test_explicit_daily_rebuild_original_request_recovers_readonly_and_lost_command_cannot_reissue(flow_scene):
    s = flow_scene; _, registration_day = observation(s); ack(s)
    original = day(s, registration_day); body = rebuild_body(s, original)
    saved = good(s.client.post(s.base + '/pixel-machine-daily/rebuild', json=body))
    assert saved['summary']['version'] == original['summary']['version'] + 1
    assert saved['summary']['id'] != original['summary']['id']
    before = inventory(s)
    assert good(s.client.post(s.base + '/pixel-machine-daily/rebuild', json=body)) == saved
    recovered = good(s.client.get(s.base + '/pixel-machine-daily/rebuild-requests', params={
        'request_id': body['request_id'], 'expected_body_hash': digest(body)}))
    assert recovered['found'] and recovered['result'] == saved and inventory(s) == before
    assert s.client.post(s.base + '/pixel-machine-daily/rebuild', json={**body, 'reason': '同号换正文'}).status_code == 409
    with Session(engine()) as db:
        command = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == 'native_pixel.daily_rebuild_saved_v250',
            m.Audit.data['body']['request_id'].as_string() == body['request_id']))
        db.delete(command); db.commit()
    broken = inventory(s)
    assert s.client.post(s.base + '/pixel-machine-daily/rebuild', json=body).status_code == 409
    assert s.client.get(s.base + '/pixel-machine-daily/rebuild-requests', params={
        'request_id': body['request_id'], 'expected_body_hash': digest(body)}).status_code == 409
    assert inventory(s) == broken


def test_bad_old_day_does_not_erase_new_ack_and_operator_can_rebuild_after_actual_original_restore(flow_scene):
    s = flow_scene; first_observation, registration_day = observation(s); ack(s)
    first = day(s, registration_day)
    with Session(engine()) as db:
        run = db.get(m.WorkflowPixelRun, s.run_id)
        artifact = next(a for a in run.data['result']['artifacts'] if a['kind'] == 'manifest')
        evidence = db.get(m.Evidence, artifact['evidence_id'])
        path = settings().storage_dir / evidence.storage_key
        snapshot = copy.deepcopy(db.get(m.Memory, first['summary']['id']).data)
    raw = path.read_bytes()
    try:
        path.write_bytes(b'fault after actual success; not an inference substitute')
        body = {**s.body, 'request_id': str(uuid4()), 'title': '同日另一份真实任务成果'}
        task = good(s.client.post(s.base + '/tasks', json=body), 201)
        job_id = task['workflow_runtime']['automatic']['job_id']
        assert process_once(job_id=job_id, deliver=False, workspace_id=s.workspace_id)
        produced = good(s.client.get('/api/workspace/tasks/' + task['id'] + '/pixel-results'))['records'][0]['run']
        another = SimpleNamespace(**{**vars(s), 'task_id': task['id'], 'run_id': produced['id'], 'job_id': job_id})
        second_observation = ack(another)
        assert entry(another)['reception']['state'] == 'received'
        blocked = day(s, registration_day)
        assert blocked['summary_state'] == 'blocked_integrity' and blocked['metrics'] is None
        assert blocked['blocked_record'] and blocked['rebuild']['can_rebuild']
        with Session(engine()) as db:
            original = db.get(m.Observation, second_observation)
            before_errors = len(daily.blocked(db, s.p, s.instance_id, registration_day))
            daily.refresh_received(db, s.p, original); daily.refresh_received(db, s.p, original)
            db.commit()
        with Session(engine()) as db:
            assert len(daily.blocked(db, s.p, s.instance_id, registration_day)) == before_errors
        path.write_bytes(raw)
        saved = good(s.client.post(s.base + '/pixel-machine-daily/rebuild', json=rebuild_body(s, blocked)))
        assert saved['summary']['version'] == first['summary']['version'] + 1
        final = day(s, registration_day)
        assert final['summary_state'] == 'saved_current' and final['counts']['received_results'] == 2
        with Session(engine()) as db:
            assert db.get(m.Memory, first['summary']['id']).data == snapshot
            assert observation_receipt(db, s.p, db.get(m.Observation, first_observation))
            assert observation_receipt(db, s.p, db.get(m.Observation, second_observation))
    finally:
        path.write_bytes(raw)


def test_received_queries_day_and_acceptance_source_guard_are_true_readonly(flow_scene):
    s = flow_scene; observation_id, registration_day = observation(s); ack(s)
    before = inventory(s)
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert observation_receipt(db, s.p, db.get(m.Observation, observation_id))['data_reception']['run_id'] == s.run_id
        verify_acceptance_inputs(db, s.p, db.get(m.Task, s.task_id), lock_sources=False)
        value = daily.projection(db, s.p, s.instance_id, registration_day)
        assert value['summary_state'] == 'saved_current' and not db.new and not db.dirty
    assert inventory(s) == before


def test_unknown_rebuild_lookup_and_paused_history_never_create_new_work(flow_scene):
    s = flow_scene; _, registration_day = observation(s); ack(s)
    before = inventory(s)
    value = good(s.client.get(s.base + '/pixel-machine-daily/rebuild-requests', params={
        'request_id': 'daily-unknown-' + uuid4().hex, 'expected_body_hash': 'a' * 64}))
    assert value['found'] is False and value['state'] == 'unknown' and value['result'] is None
    assert inventory(s) == before
    pause(s)
    received = good(query(s, task_id=s.task_id))['records'][0]
    assert received['data_values_available'] and received['reception']['state'] == 'received'
    old = day(s, registration_day)
    body = rebuild_body(s, old)
    rebuilt = good(s.client.post(s.base + '/pixel-machine-daily/rebuild', json=body))
    assert rebuilt['summary']['version'] == old['summary']['version'] + 1
    with Session(engine()) as db:
        assert len(list(db.scalars(select(m.WorkflowPixelRun).where(m.WorkflowPixelRun.workspace_id == s.workspace_id)))) == 1


def test_existing_original_event_delivery_recovery_accepts_real_pixel_family_without_new_inference(flow_scene):
    s = flow_scene; observation_id, _ = observation(s)
    base = s.base + '/observations/' + observation_id + '/data-delivery-v245'
    context = good(s.client.get(base))
    assert context['state'] == 'pending' and context['receipt'] is None
    with Session(engine()) as db:
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.idempotency_key == 'data:' + observation_id))
        original_event_id = event.id
        event.status = 'blocked_delivery'; event.data = {**event.data, 'delivery_error': '明确的投递故障测试'}
        db.commit()
    context = good(s.client.get(base))
    assert context['state'] == 'blocked' and context['can_retry']
    request = {'schema': 'observation-data-delivery-retry-command-v245', 'request_id': str(uuid4()),
        'expected_context_hash': context['context_hash'], 'reason': '仅重试原机器成果投递，不重新分析', 'confirm_retry': True}
    raw = json.dumps(request, ensure_ascii=False, indent=1).encode('utf8')
    saved = good(s.client.post(base + '/retry', content=raw))
    assert saved['decision'] == 'released_for_delivery'
    assert saved['body_sha256'] == hashlib.sha256(raw).hexdigest()
    assert entry(s)['reception']['state'] == 'waiting_data'
    ack(s)
    recovered = good(s.client.get(base + '/requests/' + request['request_id'], params={
        'expected_sha256': hashlib.sha256(raw).hexdigest()}))
    assert recovered['found'] and recovered['result'] == saved and recovered['current']['state'] == 'received'
    with Session(engine()) as db:
        assert db.get(m.Event, original_event_id).status == 'received'
        assert len(list(db.scalars(select(m.WorkflowPixelRun).where(m.WorkflowPixelRun.workspace_id == s.workspace_id)))) == 1
        assert len(list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace_id)))) == 1


@pytest.fixture
def completed_managed_pixel(managed_scene):
    s = managed_scene; approval_path = approved_start(s)
    current = good(s.executor_client.get('/api/workspace/tasks/' + s.task_id))
    job_id = current['workflow_runtime']['automatic']['job_id']
    assert process_once(job_id=job_id, deliver=False, workspace_id=s.workspace_id)
    produced = good(s.reviewer_client.get('/api/workspace/tasks/' + s.task_id + '/pixel-results'))['records'][0]['run']
    scene = SimpleNamespace(**{**vars(s), 'run_id': produced['id'], 'job_id': job_id, 'approval_path': approval_path})
    ack(scene)
    current = good(s.reviewer_client.get('/api/workspace/tasks/' + s.task_id))
    good(complete(s.reviewer_client, current, note='独立人工核对了模型技术成果，现场结论仍未证实'))
    original = good(s.acceptor_client.get(approval_path))
    assert original['acceptance_basis'] is not None and original['permissions']['accept']
    return scene, original


@pytest.mark.parametrize('fault', ['missing_memory', 'missing_human_audit', 'bad_probability_bytes'])
def test_real_formal_acceptance_basis_and_post_cannot_launder_broken_pixel_or_human_proofs(completed_managed_pixel, fault):
    s, before = completed_managed_pixel; path = None; raw = None
    observation_id, _ = observation(s)
    try:
        with Session(engine()) as db:
            if fault == 'missing_memory':
                event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
                    m.Event.idempotency_key == 'data:' + observation_id))
                memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == s.workspace_id,
                    m.Memory.data['event_id'].as_string() == event.id,
                    m.Memory.data['type'].as_string() == 'observation_evidence'))
                db.delete(memory)
            elif fault == 'missing_human_audit':
                for record in origin.audits(db, s.p, 'native_pixel.human_receipt_saved_v250', s.task_id):
                    db.delete(record)
            else:
                run = db.get(m.WorkflowPixelRun, s.run_id)
                ref = next(a for a in run.data['result']['artifacts'] if a['kind'] == 'probability_data')
                evidence = db.get(m.Evidence, ref['evidence_id']); path = settings().storage_dir / evidence.storage_key
                raw = path.read_bytes(); path.write_bytes(b'bad actual probability artifact')
            db.commit()
        blocked = good(s.acceptor_client.get(s.approval_path))
        assert blocked['acceptance_basis'] is None and blocked['acceptance_issue']
        assert blocked['permissions']['accept'] is False
        response = s.acceptor_client.post(s.approval_path + '/accept', json={
            'request_id': str(uuid4()), 'expected_version': blocked['version'],
            'reason': '仍须核对真实收件与原件，不能用旧预览绕过', 'decision': 'accept',
            'basis_hash': before['acceptance_basis']['basis_hash']})
        assert response.status_code == 409, response.text[:500]
        assert good(s.acceptor_client.get(s.approval_path))['status'] != 'accepted'
    finally:
        if path is not None and raw is not None: path.write_bytes(raw)
