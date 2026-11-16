"""Normal source/assessment persistence and worker routes in disposable PG.

Reuses SourcePG's explicit media/model and qualified-eligibility seams. No
assessment, successful Job, Frame ACK or result Audit is fabricated.
"""
import asyncio
import copy
import hashlib
import json
import threading
from uuid import uuid4
from types import SimpleNamespace as NS
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents import daily_frame_assessment_api_v233 as api
from generic_agents import daily_frame_assessment_source_v233 as source
from generic_agents import evidence_assessment_api as original
from generic_agents.contracts import digest
from generic_agents.worker import run_setup_job
from generic_agents.security import Principal, password_hash
from tests.test_daily_frame_assessment_source_v233 import (
    daily_frame_source, daily_scene, daily_camera_source, media_runtime, mocked_runtime)


def encoded(body): return json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()
def path(case): return '/api/workspace/tasks/'+case.task_id+'/daily-frame'


def body(case, **changes):
    return {'schema': 'daily-frame-assessment-create-v233', 'request_id': str(uuid4()),
        'expected_source_hash': case.source_hash, 'expected_frame_sha256': case.frame_sha256,
        'purpose': '核对这张画面的来源、时间未知与亮度线索', 'parent_assessment_id': None, **changes}


def create(case, **changes):
    value = body(case, **changes); raw = encoded(value)
    response = case.scene.client.post(path(case)+'/assessments', content=raw, headers={'content-type': 'application/json'})
    assert response.status_code == 201, response.text[:300]
    assert response.json()['body_sha256'] == hashlib.sha256(raw).hexdigest()
    return value, raw, response.json()


def finish(case, result):
    summary = result['assessment']
    assert run_setup_job(summary['job_id']) == 0
    response = case.scene.client.get(path(case)+'/assessments/'+summary['id'])
    assert response.status_code == 200, response.text[:300]
    assert response.json()['assessment']['status'] == 'completed', response.text[:500]
    return response.json()['assessment']


def test_real_task_source_content_create_worker_export_freshness_and_original_recovery(daily_frame_source):
    case = daily_frame_source; client = case.scene.client
    preview = client.get(path(case)); assert preview.status_code == 200, preview.text[:300]
    view = preview.json(); assert view['source_hash'] == case.source_hash
    assert view['time']['scene_observed_at'] is None and view['time']['scene_time_trust'] == 'unknown'
    content = client.get(path(case)+'/content', params={'expected_sha256': case.frame_sha256})
    assert content.status_code == 200 and hashlib.sha256(content.content).hexdigest() == case.frame_sha256
    assert content.headers['x-content-sha256'] == case.frame_sha256
    assert int(content.headers['content-length']) == len(content.content)
    assert content.headers['x-content-type-options'] == 'nosniff' and 'no-store' in content.headers['cache-control']
    with Session(case.bound) as db:
        old_task = copy.deepcopy(db.get(m.Task, case.task_id).data)
        old_calls = db.scalar(select(func.count()).select_from(m.CloudCall))
    request, raw, saved = create(case); completed = finish(case, saved)
    assert completed['result']['source_hash'] == case.source_hash
    assert completed['result']['frame_sha256'] == case.frame_sha256
    assert completed['result']['current_state_sufficient'] is False and completed['result']['state_conclusion'] is None
    assert completed['result']['source_time_verified'] is completed['result']['domain_quality_verified'] is False
    assert {'source_time', 'coverage', 'domain_quality'} <= {gap['kind'] for gap in completed['result']['gaps']}
    recovered = client.get(path(case)+'/assessment-requests/'+request['request_id'],
        params={'expected_sha256': hashlib.sha256(raw).hexdigest()})
    assert recovered.status_code == 200 and recovered.json()['found'] is True
    assert recovered.json()['result']['assessment'] == completed  # Current persisted status, not frozen queued.
    replay = client.post(path(case)+'/assessments', content=raw, headers={'content-type': 'application/json'})
    assert replay.status_code == 200 and replay.json()['assessment'] == completed
    fresh = client.get(path(case)+'/assessments/'+completed['id']+'/freshness')
    assert fresh.status_code == 200 and fresh.json()['unchanged'] is True and fresh.json()['source_originals_rechecked'] is True
    export = client.get('/api/evidence-assessments/'+completed['id']+'/export')
    assert export.status_code == 200 and export.json()['assessment']['result_hash'] == completed['result_hash']
    assert len(export.json()['assessment']['result']['records']) == 1
    with Session(case.bound) as db:
        assert db.get(m.Task, case.task_id).data == old_task
        assert db.scalar(select(func.count()).select_from(m.CloudCall)) == old_calls
        assert db.scalar(select(func.count()).select_from(m.EvidenceAssessment)) == 1


def test_source_loss_does_not_destroy_saved_request_recovery_or_prevent_cancel(daily_frame_source):
    case = daily_frame_source; request, raw, saved = create(case); client = case.scene.client
    with Session(case.bound) as db:
        image = db.get(m.Evidence, case.image_id); file = source.daily.originals.settings().storage_dir/image.storage_key
        old = file.read_bytes(); file.unlink()
    try:
        assert client.get(path(case)).status_code == 409
        recovered = client.get(path(case)+'/assessment-requests/'+request['request_id'],
            params={'expected_sha256': hashlib.sha256(raw).hexdigest()})
        assert recovered.status_code == 200 and recovered.json()['found'] is True
        assert client.post(path(case)+'/assessments', content=raw, headers={'content-type': 'application/json'}).status_code == 200
        stopped = client.post('/api/evidence-assessments/'+saved['assessment']['id']+'/cancel',
            json={'version': saved['assessment']['version']})
        assert stopped.status_code == 200
        summary = client.get(path(case)+'/assessments/'+saved['assessment']['id']).json()['assessment']
        assert summary['status'] == 'cancelled' and summary['can_cancel'] is False
    finally: file.write_bytes(old)


@pytest.mark.parametrize('fault', ['result_audit', 'strip_marker'])
def test_dedicated_and_global_reads_cannot_downgrade_a_new_result_to_legacy(daily_frame_source, fault):
    case = daily_frame_source; _, _, saved = create(case); completed = finish(case, saved)
    with Session(case.bound) as db:
        row = db.get(m.EvidenceAssessment, completed['id'])
        if fault == 'result_audit':
            event = db.scalar(select(m.Audit).where(m.Audit.object_id == row.id, m.Audit.action == source.RESULT_AUDIT))
            assert event is not None; db.delete(event)
        else:
            # Only this newly created negative fixture is corrupted; old
            # assessment rows are never rewritten or reclassified.
            request = copy.deepcopy(row.data['request']); request.pop(source.MARKER)
            row.data = {**row.data, 'request': request, 'request_hash': digest(request)}
        db.commit()
    client = case.scene.client
    assert client.get('/api/evidence-assessments/'+completed['id']).status_code == 409
    assert client.get('/api/evidence-assessments/'+completed['id']+'/export').status_code == 409
    rows = client.get('/api/evidence-assessments', params={'instance_id': case.scene.identity})
    assert rows.status_code == 200 and rows.json()[0]['status'] == 'blocked_integrity' and 'result' not in rows.json()[0]
    response = client.get(path(case)+'/assessments/'+completed['id'])
    assert response.status_code == 409 or (response.status_code == 200 and response.json()['assessment']['status'] == 'blocked_integrity')


@pytest.mark.parametrize('remove_witness,rewind_status', [(False, False), (True, False), (True, True)])
def test_removed_completed_result_cannot_be_relabelled_as_a_normal_empty_result(daily_frame_source, remove_witness, rewind_status):
    case = daily_frame_source; _, _, saved = create(case); completed = finish(case, saved)
    baseline = case.scene.client.get('/api/evidence-assessments/'+completed['id'])
    assert baseline.status_code == 200 and baseline.json()['status'] == 'completed' and baseline.json()['result']
    with Session(case.bound) as db:
        row = db.get(m.EvidenceAssessment, completed['id'])
        value = copy.deepcopy(row.data); value.pop('result'); value.pop('result_hash'); row.data = value
        if rewind_status: row.status = 'running'
        if remove_witness:
            event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == case.p.workspace_id,
                m.Audit.object_id == row.id, m.Audit.action == source.RESULT_AUDIT))
            assert event is not None; db.delete(event)
        db.commit()
    view = case.scene.client.get(path(case)+'/assessments/'+completed['id'])
    assert view.status_code == 200
    summary = view.json()['assessment']
    assert summary['status'] == 'blocked_integrity' and summary['error'] and summary['result'] is None
    assert case.scene.client.get('/api/evidence-assessments/'+completed['id']).status_code == 409
    assert case.scene.client.get('/api/evidence-assessments/'+completed['id']+'/export').status_code == 409
    fresh = case.scene.client.get(path(case)+'/assessments/'+completed['id']+'/freshness')
    assert fresh.status_code == 200 and fresh.json()['unchanged'] is False and fresh.json()['source_originals_rechecked'] is False


def test_cancelled_before_processing_may_have_successful_job_without_a_result(daily_frame_source):
    case = daily_frame_source; request, raw, saved = create(case); summary = saved['assessment']
    stopped = case.scene.client.post('/api/evidence-assessments/'+summary['id']+'/cancel', json={'version': summary['version']})
    assert stopped.status_code == 200
    assert run_setup_job(summary['job_id']) == 0
    view = case.scene.client.get(path(case)+'/assessments/'+summary['id'])
    assert view.status_code == 200 and view.json()['assessment']['status'] == 'cancelled'
    assert view.json()['assessment']['result'] is None and view.json()['assessment']['error'] is None
    recovered = case.scene.client.get(path(case)+'/assessment-requests/'+request['request_id'],
        params={'expected_sha256': hashlib.sha256(raw).hexdigest()})
    assert recovered.status_code == 200 and recovered.json()['found'] is True


def test_freshness_rechecks_bytes_while_historical_result_remains_readable(daily_frame_source):
    case = daily_frame_source; _, _, saved = create(case); completed = finish(case, saved)
    with Session(case.bound) as db:
        image = db.get(m.Evidence, case.image_id); file = source.daily.originals.settings().storage_dir/image.storage_key
        original_bytes = file.read_bytes(); file.write_bytes(b'bad bytes')
    try:
        view = case.scene.client.get(path(case)+'/assessments/'+completed['id'])
        assert view.status_code == 200 and view.json()['assessment']['result_hash'] == completed['result_hash']
        fresh = case.scene.client.get(path(case)+'/assessments/'+completed['id']+'/freshness')
        assert fresh.status_code == 200 and fresh.json()['unchanged'] is False
    finally: file.write_bytes(original_bytes)


def test_missing_ack_is_not_reported_as_completed_physical_recheck(daily_frame_source):
    case = daily_frame_source; _, _, saved = create(case); completed = finish(case, saved)
    with Session(case.bound) as db:
        memory = db.get(m.Memory, case.origin['ack_memory_id']); assert memory is not None
        db.delete(memory); db.commit()
    response = case.scene.client.get(path(case)+'/assessments/'+completed['id']+'/freshness')
    assert response.status_code == 200
    assert response.json()['unchanged'] is False and response.json()['source_originals_rechecked'] is False


def test_other_operator_can_read_but_is_not_offered_someone_elses_cancel(daily_frame_source):
    case = daily_frame_source; _, _, saved = create(case)
    with Session(case.bound) as db:
        user = m.User(username='operator_'+uuid4().hex, password_hash=password_hash(uuid4().hex))
        db.add(user); db.flush(); user_id = user.id
        db.add(m.Membership(user_id=user.id, workspace_id=case.p.workspace_id, role='operator')); db.commit()
    reader = Principal(user_id, case.p.workspace_id, 'operator', 'metadata-read-test')
    with Session(case.bound) as db:
        result = api.history(db, reader, case.task_id)
        assert result['items'][0]['id'] == saved['assessment']['id'] and result['items'][0]['can_cancel'] is False


def test_identical_request_uuid_is_independent_in_two_real_workspaces(daily_frame_source, monkeypatch):
    """Both tenants create genuine sources via the normal mocked IO pipeline."""
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    from generic_agents import video_daily, media_daily_delivery
    from generic_agents import media_daily_task_adapter as daily
    from tests.test_local_specialists import ready
    from tests.test_media import channel, capture, finish as finish_media
    from tests import test_workflow_images as images
    from tests.test_instance_workspace import object_row
    from tests.test_workflow_abilities import enable
    case = daily_frame_source; nonce = str(uuid4()); create(case, request_id=nonce)
    password = uuid4().hex; username = 'tenant_'+uuid4().hex
    with Session(case.bound) as db:
        user = m.User(username=username, password_hash=password_hash(password)); workspace = m.Workspace(name='第二独立验证工作区')
        db.add_all([user, workspace]); db.flush(); workspace_id = workspace.id
        db.add(m.Membership(user_id=user.id, workspace_id=workspace.id, role='admin')); db.commit()
    with TestClient(app) as other:
        login = other.post('/api/login', json={'username': username, 'password': password})
        assert login.status_code == 200
        other.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': workspace_id})
        installation = ready(other); camera = channel(other)
        captured = capture(other, camera, installation); assert captured.status_code == 201, captured.text[:300]
        _, processed = finish_media(other, captured.json()); assert processed['status'] == 'succeeded'
        with Session(case.bound) as db:
            video_daily.reconcile(db, workspace_id); db.commit()
        assert media_daily_delivery.consume_once(workspace_id)['received'] == 1
        monkeypatch.setattr(images, 'ready', lambda _: installation)
        release, _, _ = images.setup(other); enable(other, release); entity = object_row(other, release)
        with Session(case.bound) as db:
            instance = db.get(m.DomainInstance, release['instance_id']); cam = db.get(m.MediaChannel, camera['id'])
            released = db.get(m.InstanceRelease, release['id'])
            preview_body = {**case.scene.body, 'instance_version': instance.version, 'release_id': released.id,
                'configuration_hash': released.configuration_hash, 'business_id': released.data['snapshot']['businesses'][0]['id'],
                'entity_id': entity['id'], 'camera_id': cam.id, 'camera_version': cam.version, 'camera_hash': daily.camera_hash(cam)}
        base = '/api/workspace/instances/'+release['instance_id']+'/daily-task-intents'
        preview = other.post(base+'/preview', json=preview_body); assert preview.status_code == 200, preview.text[:300]
        enabled = other.post(base+'/enable', json={**preview_body, 'schema': 'media-daily-task-enable-v226',
            'request_id': str(uuid4()), 'preview_hash': preview.json()['preview_hash'], 'confirm_scope': True})
        assert enabled.status_code == 201, enabled.text[:300]
        with Session(case.bound) as db:
            slot_id = enabled.json()['intent']['slots'][0]['id']; assert daily.process_slot(db, workspace_id, slot_id)
            task_id = db.get(daily.Slot, slot_id).task_id
        base = '/api/workspace/tasks/'+task_id+'/daily-frame'
        source_view = other.get(base); assert source_view.status_code == 200, source_view.text[:300]
        request = {'schema': 'daily-frame-assessment-create-v233', 'request_id': nonce,
            'expected_source_hash': source_view.json()['source_hash'], 'expected_frame_sha256': source_view.json()['image']['sha256'],
            'purpose': '第二工作区独立请求', 'parent_assessment_id': None}
        created = other.post(base+'/assessments', json=request)
        assert created.status_code == 201, created.text[:300]
    with Session(case.bound) as db:
        rows = list(db.scalars(select(m.EvidenceAssessment).where(m.EvidenceAssessment.request_id == nonce)))
        assert len(rows) == 2 and {row.workspace_id for row in rows} == {case.p.workspace_id, workspace_id}


def test_pagination_parent_and_failed_job_are_real_persisted_states(daily_frame_source, monkeypatch):
    case = daily_frame_source; monkeypatch.setattr(api, 'PAGE_SIZE', 1)
    _, _, first = create(case); _, _, second = create(case, parent_assessment_id=first['assessment']['id'])
    with Session(case.bound) as db:
        # A deliberate failure state, never a synthetic successful Job/result.
        job = db.get(m.Job, second['assessment']['job_id']); job.status = 'failed'; db.commit()
    page = case.scene.client.get(path(case)+'/assessments')
    assert page.status_code == 200 and len(page.json()['items']) == 1 and page.json()['next_cursor']
    assert page.json()['items'][0]['status'] == 'failed' and page.json()['items'][0]['can_cancel'] is False
    next_page = case.scene.client.get(path(case)+'/assessments', params={'cursor': page.json()['next_cursor']})
    assert next_page.status_code == 200 and [item['id'] for item in next_page.json()['items']] == [first['assessment']['id']]
    assert case.scene.client.get(path(case)+'/assessments', params={'cursor': str(uuid4())}).status_code == 422


def test_same_raw_concurrent_creation_and_lost_response_keep_one_assessment_and_job(daily_frame_source):
    case = daily_frame_source; request = body(case); raw = encoded(request); barrier = Barrier(2)
    def save():
        with Session(case.bound) as db:
            barrier.wait(timeout=5)
            return api.transaction(db, api.create_assessment, case.p, case.task_id, raw)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save), pool.submit(save)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(created for _, created in results) == [False, True]
    assert len({result['assessment']['id'] for result, _ in results}) == 1
    with Session(case.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.EvidenceAssessment)) == 1
        assert db.scalar(select(func.count()).select_from(m.Job).where(m.Job.kind == 'assess_evidence')) == 1
    changed = {**request, 'purpose': '同原号换用途不允许'}
    assert case.scene.client.post(path(case)+'/assessments', json=changed).status_code == 409


def test_failure_after_real_create_saved_rolls_back_the_whole_request(daily_frame_source, monkeypatch):
    case = daily_frame_source; original_audit = api.audit; reached = []
    def fail(db, p, action, identity, value):
        if action == api.CREATE:
            assert db.get(m.EvidenceAssessment, identity) is not None
            reached.append(identity); raise RuntimeError('injected before independent creation seal')
        return original_audit(db, p, action, identity, value)
    monkeypatch.setattr(api, 'audit', fail)
    with Session(case.bound) as db:
        with pytest.raises(RuntimeError): api.transaction(db, api.create_assessment, case.p, case.task_id, encoded(body(case)))
        assert db.scalar(select(func.count()).select_from(m.EvidenceAssessment)) == 0
        assert db.scalar(select(func.count()).select_from(m.Job).where(m.Job.kind == 'assess_evidence')) == 0
    assert len(reached) == 1


def test_viewer_cannot_create_a_new_assessment_even_with_valid_pins(daily_frame_source):
    case = daily_frame_source
    with Session(case.bound) as db:
        member = db.scalar(select(m.Membership).where(m.Membership.user_id == case.p.user_id,
            m.Membership.workspace_id == case.p.workspace_id)); member.role = 'viewer'; db.commit()
    reader = Principal(case.p.user_id, case.p.workspace_id, 'viewer', 'read-only-test')
    with Session(case.bound) as db:
        with pytest.raises(HTTPException) as error: api.transaction(db, api.create_assessment, reader, case.task_id, encoded(body(case)))
        assert error.value.status_code == 403
        assert db.scalar(select(func.count()).select_from(m.EvidenceAssessment)) == 0


def test_duplicate_keys_and_over_capacity_never_reach_creation(monkeypatch):
    with pytest.raises(HTTPException): api.parse(b'{"request_id":"a","request_id":"b"}')
    called = []; monkeypatch.setattr(api, 'create_assessment', lambda *args: called.append(True))
    async def receive(): return {'type': 'http.request', 'body': b'x'*(api.MAX_BODY+1), 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}, receive)
    async def run():
        with pytest.raises(HTTPException) as error: await api.create(uuid4(), request, Response(), NS(), NS())
        assert error.value.status_code == 413
    asyncio.run(run()); assert not called


@pytest.mark.parametrize('change', [{'purpose': ' '}, {'purpose': True}, {'expected_source_hash': 'bad'},
    {'daily_task_frame_v233': {'task_id': 'untrusted'}}, {'parent_assessment_id': False}])
def test_strict_write_contract_rejects_untrusted_scope_and_coercion(daily_frame_source, change):
    case = daily_frame_source; value = body(case); value.update(change)
    response = case.scene.client.post(path(case)+'/assessments', content=encoded(value), headers={'content-type': 'application/json'})
    assert response.status_code == 422
    with Session(case.bound) as db: assert db.scalar(select(func.count()).select_from(m.EvidenceAssessment)) == 0


def test_public_request_stays_closed_and_server_subtype_binds_the_marker():
    base = {'instance_id': 'i', 'entity_ids': ['e'], 'purpose': '核查', 'required_modalities': ['video'],
        'lookback_hours': 24, 'request_id': str(uuid4())}
    with pytest.raises(ValueError): original.Request.model_validate({**base, source.MARKER: {'task_id': 't'}})
    bound = api.bound_request(**base, daily_task_frame_v233={'task_id': 't'})
    assert bound.model_dump()[source.MARKER]['task_id'] == 't'
    assert source.MARKER not in original.Request.model_fields


def test_async_create_yields_while_complete_sync_transaction_is_running(monkeypatch):
    entered, release = threading.Event(), threading.Event(); payload = b'{}'; sent = False
    async def receive():
        nonlocal sent
        if sent: return {'type': 'http.disconnect'}
        sent = True; return {'type': 'http.request', 'body': payload, 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}, receive)
    def call(*args): entered.set(); assert release.wait(3); return {'saved': True}, True
    monkeypatch.setattr(api, 'create_assessment', call)
    async def run():
        response = Response(); task = asyncio.create_task(api.create(uuid4(), request, response, NS(), NS(rollback=lambda: None)))
        try:
            for _ in range(200):
                if entered.is_set(): break
                await asyncio.sleep(.005)
            assert entered.is_set() and not task.done()
            release.set(); assert await task == {'saved': True} and response.status_code == 201
        finally: release.set(); await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
