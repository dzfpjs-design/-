"""Delegated origins in an explicitly fenced, synthetic PostgreSQL database."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Job, Membership, SetupDraft, SetupReportDraft, User
from generic_agents.security import Principal, password_hash
from generic_agents import report_evidence_pipeline as evidence
from generic_agents.report_evidence_delegated_origin import ACTION, verify_link
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_second_batch_preview import scene_with_candidate


@pytest.fixture(autouse=True)
def owned_disposable_database(monkeypatch):
    # This fixture exercises isolated protocol behavior with synthetic
    # providers; the production dispatch gate is closed by default.
    from generic_agents import report_evidence_delegated_dispatch_gate as gate
    monkeypatch.setattr(gate, 'ready', lambda: True)
    from generic_agents import report_evidence_delegated_budget_start as budget_start
    monkeypatch.setattr(budget_start, '_enabled', lambda: True)
    import tests.test_report_full_reading_source as source_tests
    import tests.test_report_full_reading_pipeline as pipeline_tests
    def fenced():
        url = engine().url
        assert url.host == '127.0.0.1' and url.database.startswith('ga_test_')
    fenced()
    monkeypatch.setattr(source_tests, 'fence', fenced)
    monkeypatch.setattr(pipeline_tests, 'fence', fenced)
    if not inspect(engine()).has_table('report_full_provisional_candidates', schema='public'):
        from generic_agents.report_full_provisional_migration import install
        install(engine())


BASE = '/api/setup/report-evidence-delegated'


def proposed(scene, monkeypatch):
    provider, old, saved = scene_with_candidate(scene, monkeypatch)
    reading = copy.deepcopy(scene['body'])
    reading['request_id'] = 'fl_' + str(uuid4())
    body = {'original_draft_id': old['id'], 'original_revision': old['revision'],
            'operator_asserted_history_hash': 'a' * 64, 'reading': reading}
    response = scene['client'].post(BASE + '/preview', json=body)
    assert response.status_code == 200, response.text
    return provider, old, saved, body, response.json()


def launch(scene, body, preview):
    return scene['client'].post(BASE, json={**body,
        'linked_preview_hash': preview['linked_preview_hash'], 'consent': True})


def authorized_proposed(scene, monkeypatch):
    """The new fl_ task uses a real DeepSeek scoped-plan authorization."""
    from tests.test_vertical_slice import publish
    provider, old, saved = scene_with_candidate(scene, monkeypatch)
    model=json.loads((Path(__file__).parents[1]/'examples/deepseek.json').read_text())
    model.update(name='synthetic_origin_'+uuid4().hex[:10],description='Synthetic no-network scope')
    model['execution'].update(model='deepseek-v4-pro',thinking_mode='disabled',max_tokens=8192)
    cap=publish(scene['client'],model)
    reading={**copy.deepcopy(scene['body']),'request_id':'fl_'+str(uuid4()),'model_id':cap['id']}
    body={'original_draft_id':old['id'],'original_revision':old['revision'],
        'operator_asserted_history_hash':'a'*64,'reading':reading}
    reply=scene['client'].post(BASE+'/preview',json=body)
    assert reply.status_code==200,reply.text
    return provider,old,saved,body,reply.json()


def authorized_launch(scene, body, preview):
    """Commit the actual server-owned preview and single-plan start contracts."""
    import httpx
    from fastapi import HTTPException
    from generic_agents import report_evidence_delegated_origin as origin
    from generic_agents import report_evidence_delegated_budget_start as budget_start
    from generic_agents.security import authenticated
    from tests.test_cloud_report_plan_allowance_postgres import prepared_request
    try:
        command=origin.LinkedPreviewCommand(**body)
        request=prepared_request(scene)
        with Session(engine(),expire_on_commit=False) as db:
            p=authenticated(request,db)
            budget_start._authority(db,p,request,preview_only=True)
            # Compare the exact server preview exposed by the old read-only
            # endpoint; its start is closed, its source scope still matters.
            existing=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
                Job.idempotency_key==budget_start._preview_key(p,command.reading.request_id)))
            if existing is None:
                current=origin._proposal(db,p,command)
                current={**current,'can_start':False,
                    'start_blocker':'分段读取的定向额度及安全派发尚未接通；旧入口不能签发额度，本次只预览。'}
                current['linked_preview_hash']=digest({k:v for k,v in current.items() if k!='linked_preview_hash'})
                if current['linked_preview_hash'] != preview['linked_preview_hash']:
                    raise HTTPException(409,detail={'code':'delegated_preview_changed'})
            saved=budget_start.preview_tx(db,p,request,command);db.commit()
            start=origin.LinkedStartCommand(**command.model_dump(mode='json'),
                linked_preview_hash=saved['linked_preview_hash'],consent=True)
            result=budget_start.start_tx(db,p,request,start,preview_id=saved['preview_id'],
                allowance_preview=saved['allowance'])
            db.commit()
            return httpx.Response(202,json=result['result'])
    except HTTPException as error:
        return httpx.Response(error.status_code,json={'detail':error.detail})


def test_closed_dispatch_gate_never_creates_new_task_or_cloud_call(reading_scene, monkeypatch):
    from generic_agents import report_evidence_delegated_dispatch_gate as gate
    scene = reading_scene
    provider, _, _, body, _ = proposed(scene, monkeypatch)
    monkeypatch.setattr(gate, 'ready', lambda: False)
    # Large generic quota cannot open the scoped report dispatch gate.
    from generic_agents.config import settings
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 999999999)
    with Session(engine()) as db:
        before_rows = tuple(db.scalar(select(func.count()).select_from(model))
                            for model in (SetupReportDraft, Job, CloudCall, Audit))
    before = len(provider.calls)
    reply = scene['client'].post(BASE + '/preview', json=body)
    assert reply.status_code == 200
    preview = reply.json()
    assert preview['can_start'] is False
    assert '定向额度及安全派发尚未接通' in preview['start_blocker']
    assert preview['basis']['historical_second_send'] == 'not_independently_proven'
    assert preview['basis']['duplicate_cost_risk'] is True
    assert len(provider.calls) == before
    with Session(engine()) as db:
        assert tuple(db.scalar(select(func.count()).select_from(model))
                     for model in (SetupReportDraft, Job, CloudCall, Audit)) == before_rows
    reply = launch(scene, body, preview)
    assert reply.status_code == 409
    assert reply.json()['detail']['code'] == 'delegated_dispatch_not_ready'
    assert len(provider.calls) == before
    with Session(engine()) as db:
        assert db.scalar(select(SetupReportDraft.id).where(
            SetupReportDraft.workspace_id == scene['p'].workspace_id,
            SetupReportDraft.request_id == body['reading']['request_id'])) is None


def test_new_analysis_origin_atomic_and_replay_preserve_paid_history(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, saved, body, preview = authorized_proposed(scene, monkeypatch)
    assert preview['basis']['historical_second_send'] == 'not_independently_proven'
    assert preview['basis']['source_relationship'] == 'same_report_word_and_ocr_views_overlap'
    assert preview['basis']['user_reviewed_history'] is preview['basis']['user_confirmed_business'] is False
    assert preview['basis']['authorization_basis'] == 'authenticated_actor_submitted_request'
    assert preview['basis']['history_digest_verification'] == 'operator_asserted_not_independently_verified'
    assert 'delegation' not in preview['basis']
    with Session(engine()) as db:
        original = db.get(SetupReportDraft, old['id'])
        before = (original.status, original.version, digest(original.data))
        call = copy.deepcopy(db.get(CloudCall, provider.calls[0]).data)
    response = authorized_launch(scene, body, preview)
    assert response.status_code == 202, response.text
    created = response.json(); row = created['draft']
    assert row['request_id'] == body['reading']['request_id'] and row['status'] == 'queued'
    assert authorized_launch(scene, body, preview).json() == created
    linked = scene['client'].post(BASE + '/' + row['id'] + '/origin')
    assert linked.status_code == 200, linked.text
    assert linked.json()['origin']['payload']['basis']['original_draft_id'] == old['id']
    with Session(engine()) as db:
        original = db.get(SetupReportDraft, old['id'])
        assert (original.status, original.version, digest(original.data)) == before
        assert db.get(CloudCall, provider.calls[0]).data == call
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == ACTION)) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'report_evidence_origin_v1')) == 1
    assert len(provider.calls) == 1


def test_history_digest_is_optional_and_server_observations_are_scope_bound(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _, body, _ = authorized_proposed(scene, monkeypatch)
    del body['operator_asserted_history_hash']
    preview_reply = scene['client'].post(BASE + '/preview', json=body)
    assert preview_reply.status_code == 200, preview_reply.text
    preview = preview_reply.json(); basis = preview['basis']; assessment = basis['history_assessment']
    assert basis['operator_asserted_history_hash'] is None
    assert basis['history_digest_verification'] == 'not_provided'
    assert assessment['assessment_hash'] == digest({k: v for k, v in assessment.items() if k != 'assessment_hash'})
    assert assessment['original_draft_id'] == old['id']
    assert assessment['second_messages_hash'] == old['preview']['batches'][1]['messages_hash']
    assert assessment['independent_transport_or_provider_proof'] is False
    assert assessment['conclusion'] == 'conditional_engineering_inference_not_provider_proof'
    assert len(assessment['assumptions']) == 3
    created = authorized_launch(scene, body, preview)
    assert created.status_code == 202, created.text
    assert authorized_launch(scene, body, preview).json() == created.json()
    # An omitted optional assertion and explicit null are the same intent.
    assert authorized_launch(scene, {**body, 'operator_asserted_history_hash': None}, preview).json() == created.json()
    assert len(provider.calls) == 1


@pytest.mark.parametrize(('field', 'forged'), [
    ('independent_transport_or_provider_proof', True),
    ('conclusion', 'provider_confirmed_not_sent'),
    ('second_stage_pending_and_workspace_ledger_clear', False),
    ('assumptions', []),
    ('second_messages_hash', 'f' * 64),
])
def test_rehashed_server_history_cannot_forge_proof_or_scope(reading_scene, monkeypatch, field, forged):
    scene = reading_scene
    provider, _, _, body, preview = authorized_proposed(scene, monkeypatch)
    created = authorized_launch(scene, body, preview).json()['draft']
    with Session(engine()) as db:
        marker = db.scalar(select(Job).where(Job.kind == 'report_evidence_origin_v1'))
        value = copy.deepcopy(marker.data)
        assessment = value['payload']['basis']['history_assessment']
        assessment[field] = forged
        assessment['assessment_hash'] = digest({k: v for k, v in assessment.items() if k != 'assessment_hash'})
        value['origin_hash'] = digest(value['payload']); marker.data = value
        event = db.scalar(select(Audit).where(Audit.action == ACTION))
        event.data = {'origin_hash': value['origin_hash'], 'request_id': created['request_id']}
        db.commit()
    assert scene['client'].post(BASE + '/' + created['id'] + '/origin').status_code == 409
    assert len(provider.calls) == 1


def test_normal_start_refuses_linked_nonce_and_scope_drift_fails(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    ordinary = scene['client'].post('/api/setup/report-evidence-readings/preview', json=body['reading'])
    assert ordinary.status_code == 409
    changed = {**body, 'operator_asserted_history_hash': 'b' * 64}
    assert authorized_launch(scene, changed, preview).status_code == 409
    assert len(provider.calls) == 1


def test_existing_active_unknown_gate_is_not_bypassed(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, old['id']); row.status = 'response_unknown'; db.commit()
    assert authorized_launch(scene, body, preview).status_code == 409
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'report_evidence_origin_v1')) == 0
    assert len(provider.calls) == 1


def test_scoped_plan_does_not_use_or_change_ordinary_daily_limit(reading_scene, monkeypatch):
    from generic_agents.config import settings
    scene = reading_scene
    provider, old, _, body, _ = authorized_proposed(scene, monkeypatch)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1)
    preview = scene['client'].post(BASE + '/preview', json=body)
    assert preview.status_code == 200 and preview.json()['can_start'] is False
    response = authorized_launch(scene, body, preview.json())
    assert response.status_code == 202
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'report_evidence_origin_v1')) == 1
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 1
    assert settings().cloud_daily_token_limit == 1
    assert len(provider.calls) == 1


@pytest.mark.parametrize(('field', 'forged'), [
    ('user_confirmed_business', True),
    ('user_reviewed_history', True),
    ('authorization_basis', 'operator_on_user_instruction'),
    ('history_digest_verification', 'independently_verified'),
])
def test_rehashed_user_confirmation_claim_still_cannot_be_forged(reading_scene, monkeypatch, field, forged):
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    response = authorized_launch(scene, body, preview)
    assert response.status_code == 202, response.text
    created = response.json()['draft']
    with Session(engine()) as db:
        marker = db.scalar(select(Job).where(Job.kind == 'report_evidence_origin_v1'))
        value = copy.deepcopy(marker.data); value['payload']['basis'][field] = forged
        value['origin_hash'] = digest(value['payload']); marker.data = value
        event = db.scalar(select(Audit).where(Audit.action == ACTION))
        event.data = {'origin_hash': value['origin_hash'], 'request_id': created['request_id']}
        db.commit()
    assert scene['client'].post(BASE + '/' + created['id'] + '/origin').status_code == 409
    assert len(provider.calls) == 1


def test_original_fe_contract_and_real_http_read_still_work(reading_scene):
    scene = reading_scene
    body = copy.deepcopy(scene['body']); body['request_id'] = 'fe_' + str(uuid4())
    preview = scene['client'].post('/api/setup/report-evidence-readings/preview', json=body)
    assert preview.status_code == 200, preview.text
    response = scene['client'].post('/api/setup/report-evidence-readings',
        json={**body, 'preview_hash': preview.json()['preview_hash'], 'consent': True})
    assert response.status_code == 202, response.text
    row = response.json()['draft']
    fetched = scene['client'].get('/api/setup/report-evidence-readings/' + row['id'])
    assert fetched.status_code == 200 and fetched.json()['request_id'] == body['request_id']
    assert fetched.headers['cache-control'] == 'private, no-store'


def test_same_snapshot_remains_read_only_in_real_http_request(reading_scene):
    from generic_agents.api import app
    from generic_agents.report_full_reading_pipeline import ReadP
    from generic_agents.report_reading_drafts import DB
    path = '/api/setup/report-evidence-readings/{identity}/isolated-test-readonly'
    @app.get(path)
    def probe(identity: str, p: ReadP, db: DB):
        assert db.scalar(text("SHOW transaction_read_only")) == 'on'
        assert db.scalar(text("SHOW transaction_isolation")) == 'repeatable read'
        with pytest.raises(DBAPIError) as denied:
            db.execute(text('UPDATE jobs SET status=status WHERE false'))
        assert getattr(denied.value.orig, 'sqlstate', None) == '25006'
        db.rollback()
        return {'readonly': True}
    try:
        response = reading_scene['client'].get(path.replace('{identity}', str(uuid4())))
        assert response.status_code == 200 and response.json() == {'readonly': True}
    finally:
        app.router.routes[:] = [route for route in app.router.routes if getattr(route, 'endpoint', None) is not probe]


def test_map_policy_keeps_admin_gate_for_evidence_and_origin(reading_scene, monkeypatch):
    from generic_agents import map_access
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    response = authorized_launch(scene, body, preview)
    assert response.status_code == 202, response.text
    row = response.json()['draft']
    with Session(engine()) as db:
        db.add(Job(workspace_id=scene['p'].workspace_id, kind=map_access.KIND, status='internal',
            idempotency_key=map_access.KEY, data={'businesses': {}})); db.commit()
    assert scene['client'].get('/api/setup/report-evidence-readings/' + row['id']).status_code == 200
    assert scene['client'].post(BASE + '/' + row['id'] + '/origin').status_code == 200
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == scene['p'].workspace_id,
            Membership.user_id == scene['p'].user_id)); member.role = 'operator'; db.commit()
    assert scene['client'].get('/api/setup/report-evidence-readings/' + row['id']).status_code == 403
    assert scene['client'].post(BASE + '/' + row['id'] + '/origin').status_code == 403
    assert len(provider.calls) == 1


def test_linked_origin_and_draft_remain_private_to_actor(reading_scene, monkeypatch):
    from generic_agents.api import app
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    response = authorized_launch(scene, body, preview)
    assert response.status_code == 202, response.text
    row = response.json()['draft']; username = 'other_' + uuid4().hex; password = uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password)); db.add(user); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=scene['p'].workspace_id, role='admin')); db.commit()
    with TestClient(app) as other:
        login = other.post('/api/login', json={'username': username, 'password': password})
        assert login.status_code == 200
        other.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        assert other.get('/api/setup/report-evidence-readings/' + row['id']).status_code == 404
        assert other.post(BASE + '/' + row['id'] + '/origin').status_code == 404
    assert len(provider.calls) == 1


def test_link_missing_or_tampered_blocks_dispatch(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    response = authorized_launch(scene, body, preview)
    assert response.status_code == 202, response.text
    created = response.json()['draft']
    with Session(engine()) as db:
        marker = db.scalar(select(Job).where(Job.kind == 'report_evidence_origin_v1'))
        data = copy.deepcopy(marker.data); data['payload']['basis']['user_confirmed_business'] = True
        marker.data = data; db.commit()
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        with pytest.raises(Exception):
            evidence._fresh_cap(db, scene['p'], row, map_source=True)
    assert scene['client'].post(BASE + '/' + created['id'] + '/origin').status_code == 409
    assert len(provider.calls) == 1


@pytest.mark.parametrize('advance', ['revision', 'second_call', 'old_job', 'old_job_metadata'])
def test_original_task_advances_but_saved_link_remains_readable_and_no_new_send(reading_scene, monkeypatch, advance):
    from fastapi import HTTPException
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    response = authorized_launch(scene, body, preview)
    assert response.status_code == 202, response.text
    created = response.json()['draft']
    with Session(engine()) as db:
        original = db.get(SetupReportDraft, old['id'])
        if advance == 'revision':
            original.version += 1
        elif advance == 'second_call':
            db.add(CloudCall(workspace_id=scene['p'].workspace_id, capability_id=scene['cap']['id'],
                status='reserved', reserved_tokens=1, charged_tokens=1,
                data={'request_hash': old['preview']['batches'][1]['messages_hash']}))
        elif advance == 'old_job':
            old_job = db.get(Job, original.data['job_id'])
            old_job.status = 'running'
        else:
            old_job = db.get(Job, original.data['job_id'])
            old_job.data = {**old_job.data, 'later_update': True}
        db.commit()
    fetched = scene['client'].get('/api/setup/report-evidence-readings/' + created['id'])
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()['id'] == created['id']
    assert scene['client'].get('/api/setup/report-evidence-readings/by-request/' + created['request_id']).status_code == 200
    assert scene['client'].post(BASE + '/' + created['id'] + '/origin').status_code == 200
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        with pytest.raises(HTTPException) as error:
            evidence._fresh_cap(db, scene['p'], row, map_source=True)
        assert error.value.detail['code'] in {'delegated_original_changed',
            'delegated_original_job_changed', 'delegated_original_ledger_changed'}
    assert len(provider.calls) == 1


@pytest.mark.parametrize('tamper', ['missing_marker', 'original_revision', 'original_ledger', 'second_call'])
def test_each_outbound_rechecks_original_and_missing_marker(reading_scene, monkeypatch, tamper):
    from fastapi import HTTPException
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    response = authorized_launch(scene, body, preview)
    assert response.status_code == 202, response.text
    created = response.json()['draft']
    with Session(engine()) as db:
        if tamper == 'missing_marker':
            db.delete(db.scalar(select(Job).where(Job.kind == 'report_evidence_origin_v1')))
        elif tamper == 'original_revision':
            db.get(SetupReportDraft, old['id']).version += 1
        elif tamper == 'original_ledger':
            call = db.get(CloudCall, provider.calls[0]); value = copy.deepcopy(call.data)
            value['actor_id'] = str(uuid4()); call.data = value
        else:
            db.add(CloudCall(workspace_id=scene['p'].workspace_id, capability_id=scene['cap']['id'],
                status='reserved', reserved_tokens=1, charged_tokens=1,
                data={'request_hash': old['preview']['batches'][1]['messages_hash']}))
        db.commit()
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        with pytest.raises(HTTPException):
            evidence._fresh_cap(db, scene['p'], row, map_source=True)
    if tamper == 'missing_marker':
        assert scene['client'].get('/api/setup/report-evidence-readings/' + created['id']).status_code == 409
        assert scene['client'].get('/api/setup/report-evidence-readings/by-request/' + created['request_id']).status_code == 409
    assert len(provider.calls) == 1


def test_concurrent_same_nonce_replays_one_task(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    barrier = threading.Barrier(2)
    def run():
        barrier.wait(timeout=10)
        return authorized_launch(scene, body, preview)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: run(), range(2)))
    assert [r.status_code for r in responses] == [202, 202]
    assert responses[0].json()['draft']['id'] == responses[1].json()['draft']['id']
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'report_evidence_origin_v1')) == 1


def test_worker_holds_original_job_while_linked_start_holds_workspace_fails_without_deadlock(reading_scene, monkeypatch):
    from generic_agents import report_evidence_delegated_budget_start as budget_start
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    start_has_workspace = threading.Event()
    worker_has_job = threading.Event()
    original_lock = budget_start._lock_authority

    def synchronized_lock(db, p, request):
        # start_route already holds the workspace SetupDraft row here.
        start_has_workspace.set()
        assert worker_has_job.wait(5)
        return original_lock(db, p, request)

    monkeypatch.setattr(budget_start, '_lock_authority', synchronized_lock)

    def holding_worker_job():
        assert start_has_workspace.wait(5)
        with Session(engine()) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            job = db.scalar(select(Job).where(Job.id == old['job_id']).with_for_update())
            assert job is not None
            worker_has_job.set()
            # This models the existing lease-before-workspace order.
            assert db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == scene['p'].workspace_id)
                             .with_for_update()) is not None
            db.rollback()

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(holding_worker_job)
        start = pool.submit(authorized_launch, scene, body, preview)
        result = start.result(timeout=10)
        worker.result(timeout=10)
    assert result.status_code == 409, result.text
    assert result.json()['detail']['code'] == 'delegated_original_busy'
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'report_evidence_origin_v1')) == 0
    assert len(provider.calls) == 1
    assert len(provider.calls) == 1


def test_different_nonces_for_same_original_cannot_both_start(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _, body, first = authorized_proposed(scene, monkeypatch)
    other = copy.deepcopy(body); other['reading']['request_id'] = 'fl_' + str(uuid4())
    r = scene['client'].post(BASE + '/preview', json=other)
    assert r.status_code == 200, r.text
    second = r.json(); barrier = threading.Barrier(2)
    def run(pair):
        barrier.wait(timeout=10)
        return authorized_launch(scene, *pair)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(run, [(body, first), (other, second)]))
    assert sorted(r.status_code for r in responses) == [202, 409]
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'report_evidence_origin_v1')) == 1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == ACTION)) == 1
    assert len(provider.calls) == 1


def test_origin_creation_failure_rolls_back_all_new_rows(reading_scene, monkeypatch):
    from generic_agents import report_evidence_delegated_budget_start as delegated
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    with Session(engine()) as db:
        before = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                  for model in (SetupReportDraft, CloudCall)}
    actual = delegated.audit
    def fail_origin(db, principal, action, object_id, details=None):
        if action == ACTION:
            raise RuntimeError('synthetic interruption before origin audit')
        return actual(db, principal, action, object_id, details)
    monkeypatch.setattr(delegated, 'audit', fail_origin)
    with pytest.raises(RuntimeError):
        authorized_launch(scene, body, preview)
    with Session(engine()) as db:
        after = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                 for model in (SetupReportDraft, CloudCall)}
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'report_evidence_origin_v1')) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'cloud_report_plan_allowance_v1')) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'cloud_report_plan_preview_v1')) == 1
    assert before == after and len(provider.calls) == 1


def test_commit_response_loss_replays_original_nonce(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    original = evidence.replay_start
    def interrupted(*args, **kwargs):
        raise RuntimeError('synthetic lost response after committed start')
    monkeypatch.setattr(evidence, 'replay_start', interrupted)
    with pytest.raises(RuntimeError):
        authorized_launch(scene, body, preview)
    monkeypatch.setattr(evidence, 'replay_start', original)
    replay = authorized_launch(scene, body, preview)
    assert replay.status_code == 202, replay.text
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'report_evidence_origin_v1')) == 1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == ACTION)) == 1
    assert len(provider.calls) == 1


@pytest.mark.parametrize('revocation', ['session', 'user', 'membership'])
def test_revocation_while_waiting_for_workspace_refuses_new_task(reading_scene, monkeypatch, revocation):
    from generic_agents import report_reading_drafts as legacy
    scene = reading_scene
    provider, old, _, body, preview = authorized_proposed(scene, monkeypatch)
    entered = threading.Event(); original = legacy.workspace_draft
    def observed(db, p, lock=True):
        entered.set(); return original(db, p, lock=lock)
    monkeypatch.setattr(legacy, 'workspace_draft', observed)
    with Session(engine()) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.scalar(select(SetupDraft).where(SetupDraft.workspace_id == scene['p'].workspace_id).with_for_update())
        pending = pool.submit(launch, scene, body, preview)
        assert entered.wait(10)
        with Session(engine()) as db:
            if revocation == 'session':
                session = db.scalar(select(AuthSession).where(AuthSession.user_id == scene['p'].user_id,
                    AuthSession.revoked.is_(False)).order_by(AuthSession.created_at.desc()))
                session.revoked = True
            elif revocation == 'user':
                db.get(User, scene['p'].user_id).active = False
            else:
                member = db.scalar(select(Membership).where(Membership.workspace_id == scene['p'].workspace_id,
                    Membership.user_id == scene['p'].user_id)); member.role = 'viewer'
            db.commit()
        blocker.commit(); response = pending.result(timeout=20)
    assert response.status_code in (401, 403)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'report_evidence_origin_v1')) == 0
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == ACTION)) == 0
    assert len(provider.calls) == 1
