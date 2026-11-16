"""Root-run owned-PG/authenticated HTTP journeys with public synthetic inputs.

The V7 fixture uses fake local document/OCR IO and native provider HTTP only;
its real reservation/worker/capture/journal creates a complete old candidate.
Independent human objects below are explicitly authored, never SQL-created
model output. Normal V166 save/confirm and V169 adoption validators remain real.
Fresh TestClient/Session reads are not physical restart/browser/model quality.
Root alone executes; negative SQL is visible only inside a rolled-back owned
transaction. No production validator, source check or role check is replaced.
"""
import copy
import hashlib
import json
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select, update
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine, session
from generic_agents.models import Audit, SetupDraft, SetupMaterial, SetupRevision
from generic_agents.report_configuration_models import ReportConfigurationRecord
from generic_agents.report_object_revision_models_v166 import ReportObjectRevision, ReportObjectRevisionCommand
from generic_agents import report_object_revision_store_v166 as revisions
from generic_agents import report_object_adoption_v169 as adoption
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v7_postgres import (
    BASE, NativeHTTP, v7_scene, mapped, reduced, storage_files, call_job_rows,
)


PRIVATE_NOTE = 'V169_PRIVATE_HUMAN_NOTE_MUST_NOT_BE_SHARED_末字🙂\n'
_ABSENT = object()


def wire(value, *, pretty=True):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
        indent=2 if pretty else None, separators=None if pretty else (',', ':')).encode('utf-8')


def revision_path(report_id):
    return '/api/workspace/report-evidence-readings/' + report_id + '/object-revisions-v166'


def adoption_path(report_id):
    return '/api/workspace/report-evidence-readings/' + report_id + '/object-adoption-v169'


def post_wire(client, path, body, expected=200):
    raw = wire(body)
    return raw, reply(client.post(path, content=raw, headers={'content-type': 'application/json'}), expected)


def write_revision(client, report_id, operation, value):
    state = reply(client.get(revision_path(report_id)))
    body = {'request_id': 'v169-' + operation + '-' + uuid4().hex,
        'expected_version': state['version'], 'basis_hash': state['basis_hash'], 'value': copy.deepcopy(value)}
    raw, saved = post_wire(client, revision_path(report_id) + '/' + operation, body)
    assert saved['outcome'] == 'saved' and saved['wire_sha256'] == hashlib.sha256(raw).hexdigest()
    assert saved['result']['version'] == state['version'] + 1
    current = reply(client.get(revision_path(report_id)))
    assert current['value'] == value and current['version'] == saved['result']['version']
    return current


def confirm_revision(client, report_id, value):
    saved = write_revision(client, report_id, 'save', value)
    assert saved['status'] == 'draft'
    confirmed = write_revision(client, report_id, 'confirm', value)
    assert confirmed['status'] == 'confirmed'
    return confirmed


def public_only(*values):
    for value in values:
        assert PRIVATE_NOTE not in json.dumps(value, ensure_ascii=False)


def source_command(client, report_id, *, target=None):
    source = reply(client.get(adoption_path(report_id)))
    assert source['available'] is True and source['origin'] is not None
    return source, {'schema': adoption.COMMAND, 'request_id': str(uuid4()),
        'origin': source['origin'], 'configuration_revision': source['configuration_revision'],
        'configuration_hash': source['configuration_hash'], 'share_scope': adoption.SHARE,
        'replace_target': target}


def prepare(client, report_id, *, target=None):
    source, body = source_command(client, report_id, target=target)
    preview = reply(client.post(adoption_path(report_id) + '/preview', json=body))
    assert preview['body'] == body and preview['conflicts'] == []
    assert preview['can_confirm'] is preview['can_publish'] is False
    public_only(source, preview)
    return source, preview, {**body, 'preview_hash': preview['preview_hash'], 'consent': True}


def commit(client, report_id, *, target=None):
    source, preview, body = prepare(client, report_id, target=target)
    raw, result = post_wire(client, adoption_path(report_id) + '/commit', body)
    assert result['record']['design'] == preview['design']
    assert result['record']['wire_sha256'] == result['receipt']['wire_sha256'] == hashlib.sha256(raw).hexdigest()
    public_only(result)
    return source, preview, body, raw, result


def receipt(client, report_id, body, raw):
    return reply(client.get(adoption_path(report_id) + '/requests/' + body['request_id'],
        params={'expected_sha256': hashlib.sha256(raw).hexdigest()}))


def assert_paid_unchanged(scene):
    def processing_rows(rows):
        # A normal new login legitimately updates its security rate limiter.
        # Keep every model/cloud budget and processing Job byte-exact.
        return {name: {key: raw for key, raw in values.items()
            if name != 'jobs' or json.loads(raw)['kind'] != 'login_limit'}
            for name, values in rows.items()}
    before_rows, before_files, before_http = scene['paid']
    assert processing_rows(call_job_rows()) == processing_rows(before_rows)
    assert storage_files() == before_files
    assert len(scene['http'].requests) == before_http


@pytest.fixture
def confirmed_scene(v7_scene, monkeypatch):
    fence(); scene, http = v7_scene, NativeHTTP(monkeypatch)
    _, _, _, middle = mapped(scene, http)
    _, _, _, generated = reduced(scene, http, middle)
    assert generated['model_result_hash'] is not None and generated['model_result'] is not None
    client, report_id = scene['client'], generated['id']
    initial = reply(client.get(revision_path(report_id)))
    assert initial['version'] == 0 and initial['source_available'] is True
    value = copy.deepcopy(initial['value'])
    for obj in value['result']['objects']:
        for field in obj['fields']:
            field['required'] = False
    # Explicit human addition, no invented source quote/unit/unique business key.
    why = '人工新增合成待核对象及属性，未认定为原文明示或业务唯一标识。'
    value['result']['objects'].append({'name': '人工待核记录', 'fields': [
        {'label': '备注', 'kind': '文本', 'required': False, 'unit': None,
            'citations': [], 'inference_reason': why},
        {'label': '数量', 'kind': '整数', 'required': False, 'unit': None,
            'citations': [], 'inference_reason': why}],
        'citations': [], 'inference_reason': why,
        'business_identity': {'status': 'not_established', 'field_labels': [],
            'citations': [], 'explanation': '人工稿尚未明确业务标识，不代表原文没有标识。'}})
    value['result']['questions'].append(why)
    value['object_identity_confirmations'] = [{'object_name': obj['name'],
        'acknowledge_platform_identity': True} for obj in value['result']['objects']]
    value['note'] = PRIVATE_NOTE
    confirmed = confirm_revision(client, report_id, value)
    configuration = reply(client.get('/api/setup/draft'))
    proposed = copy.deepcopy(configuration['configuration'])
    proposed.setdefault('instanceProfile', {}).update(name='V169合成人工结构', domain='合成协议核查')
    reply(client.put('/api/setup/draft', json={'revision': configuration['revision'], 'configuration': proposed}))
    return {**scene, 'http': http, 'generated': generated, 'report_id': report_id, 'value': value,
        'confirmed': confirmed, 'paid': (call_job_rows(), storage_files(), len(http.requests))}


def test_normal_append_original_wire_receipt_and_cold_authenticated_configuration_reads(confirmed_scene, account):
    scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    source, preview, body, raw, accepted = commit(client, report_id)
    design = accepted['record']['design']
    assert design['schema'] == 'report-object-semantic-design-v169'
    assert design['result'] == scene['value']['result']
    assert design['object_identity_confirmations'] == scene['value']['object_identity_confirmations']
    assert design['manual_value_hash'] == digest(scene['value']) == source['origin']['confirmed_value_hash']
    assert all(field['required'] is False for obj in design['result']['objects'] for field in obj['fields'])
    assert accepted['record']['parent_id'] is None
    assert all(flow['confirmedAt'] is flow['confirmedSignature'] is None for flow in preview['flows'].values())
    original = receipt(client, report_id, body, raw)
    assert original['state'] == 'saved' and original['reply'] == accepted
    replay = reply(client.post(adoption_path(report_id) + '/commit', content=raw,
        headers={'content-type': 'application/json'}))
    assert replay == accepted
    assert reply(client.get('/api/setup/draft')) == accepted['configuration']
    assert reply(client.get(revision_path(report_id)))['value']['note'] == PRIVATE_NOTE
    with Session(engine()) as db:
        row = db.get(ReportConfigurationRecord, accepted['record']['id'])
        assert row.kind == adoption.KIND and row.data['command_wire'].encode('utf-8') == raw
        assert row.data['command_wire_sha256'] == hashlib.sha256(raw).hexdigest()
        public_only(row.data)
    # A new ordinary login and independent API sessions; not a server restart.
    with TestClient(app) as cold:
        auth = reply(cold.post('/api/login', json={'username': account[0], 'password': account[1]}))
        cold.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        assert receipt(cold, report_id, body, raw)['reply'] == accepted
        assert reply(cold.get('/api/setup/draft')) == accepted['configuration']
        assert reply(cold.get(revision_path(report_id)))['value'] == scene['value']
    assert_paid_unchanged(scene)


def test_explicit_replace_requires_new_normal_save_confirm_and_preserves_old_receipt(confirmed_scene):
    scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    _, _, first_body, first_raw, first = commit(client, report_id)
    edited = copy.deepcopy(scene['value']); edited['note'] += '后续私人版本'
    edited['result']['businesses'][0]['name'] += '（人工修订）'
    revised = confirm_revision(client, report_id, edited)
    assert revised['version'] == scene['confirmed']['version'] + 2
    target = {'handoff_id': first['record']['id'], 'record_hash': first['record']['record_hash'],
        'design_hash': first['record']['design']['design_hash']}
    _, preview, body, raw, second = commit(client, report_id, target=target)
    assert preview['changes']['mode'] == 'replace'
    assert second['record']['parent_id'] == first['record']['id']
    assert second['record']['design']['result'] == edited['result']
    assert second['record']['design']['review_origin']['revision_version'] == revised['version']
    assert second['configuration']['configuration']['fullSemanticDesigns'] == [second['record']['design']]
    historical = receipt(client, report_id, first_body, first_raw)
    assert historical['state'] == 'saved' and historical['reply']['record'] == first['record']
    assert historical['reply']['configuration'] == second['configuration']
    assert receipt(client, report_id, body, raw)['reply'] == second
    public_only(second, historical)
    assert_paid_unchanged(scene)


def test_latest_saved_draft_blocks_fresh_adoption_but_keeps_historical_confirmed_receipt(confirmed_scene):
    scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    _, _, body, raw, accepted = commit(client, report_id)
    latest = copy.deepcopy(scene['value']); latest['note'] += '未重新确认的后续草稿'
    state = write_revision(client, report_id, 'save', latest)
    assert state['status'] == 'draft'
    unavailable = reply(client.get(adoption_path(report_id)))
    assert unavailable['available'] is False and unavailable['origin'] is None
    fresh = {key: copy.deepcopy(value) for key, value in body.items() if key not in {'consent', 'preview_hash'}}
    fresh['request_id'] = str(uuid4())
    fresh.update(configuration_revision=unavailable['configuration_revision'],
        configuration_hash=unavailable['configuration_hash'])
    assert client.post(adoption_path(report_id) + '/preview', json=fresh).status_code == 409
    assert receipt(client, report_id, body, raw)['reply']['record'] == accepted['record']
    assert reply(client.get(revision_path(report_id)))['value'] == latest
    assert_paid_unchanged(scene)


def test_nonce_is_bound_to_exact_raw_bytes_and_request_recovery_checks_sha(confirmed_scene):
    scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    _, _, body, raw, accepted = commit(client, report_id)
    compact = wire(body, pretty=False)
    assert json.loads(compact) == json.loads(raw) and compact != raw
    response = client.post(adoption_path(report_id) + '/commit', content=compact,
        headers={'content-type': 'application/json'})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'object_adoption_nonce_conflict'
    assert client.get(adoption_path(report_id) + '/requests/' + body['request_id'],
        params={'expected_sha256': hashlib.sha256(compact).hexdigest()}).status_code == 409
    assert receipt(client, report_id, body, raw)['reply'] == accepted
    assert reply(client.get('/api/setup/draft')) == accepted['configuration']
    assert_paid_unchanged(scene)


def test_configuration_cas_change_leaves_no_adoption_record_and_keeps_human_work(confirmed_scene):
    scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    _, _, body = prepare(client, report_id)
    before = reply(client.get('/api/setup/draft')); changed = copy.deepcopy(before['configuration'])
    changed['instanceProfile']['name'] += '配置并发修改'
    current = reply(client.put('/api/setup/draft', json={'revision': before['revision'], 'configuration': changed}))
    raw = wire(body)
    assert client.post(adoption_path(report_id) + '/commit', content=raw,
        headers={'content-type': 'application/json'}).status_code == 409
    recovered = receipt(client, report_id, body, raw)
    assert recovered['state'] == 'missing' and recovered['reply'] is None
    assert reply(client.get('/api/setup/draft')) == current
    assert reply(client.get(revision_path(report_id)))['value'] == scene['value']
    assert_paid_unchanged(scene)


@pytest.mark.parametrize('fault', ['private_top_note', 'private_origin_note', 'different_share_scope'])
def test_private_note_or_unapproved_share_scope_cannot_enter_shared_command(confirmed_scene, fault):
    scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    _, body = source_command(client, report_id)
    before = reply(client.get('/api/setup/draft'))
    if fault == 'private_top_note': body['note'] = PRIVATE_NOTE
    elif fault == 'private_origin_note': body['origin']['note'] = PRIVATE_NOTE
    else: body['share_scope'] = 'whole_result_including_private_note'
    response = client.post(adoption_path(report_id) + '/preview', json=body)
    assert response.status_code == (409 if fault == 'private_origin_note' else 422)
    assert reply(client.get('/api/setup/draft')) == before
    assert reply(client.get(revision_path(report_id)))['value']['note'] == PRIVATE_NOTE
    assert_paid_unchanged(scene)


@contextmanager
def rolled_back_source_fault(scene, fault):
    """Real negative SQL + normal dependency sessions, no validator success stub."""
    fence()
    old = app.dependency_overrides.get(session, _ABSENT)
    with engine().connect() as connection:
        transaction = connection.begin()
        try:
            report_id = scene['report_id']
            if fault == 'confirm_audit_missing':
                origin = scene['adopted_source']['origin']
                result = connection.execute(delete(Audit).where(Audit.id == origin['confirmed_audit_id']))
                assert result.rowcount == 1
            elif fault == 'revision_inventory_missing':
                command_ids = list(connection.scalars(select(ReportObjectRevisionCommand.id).where(
                    ReportObjectRevisionCommand.report_id == report_id)))
                assert len(command_ids) == 2
                connection.execute(delete(Audit).where(Audit.action == revisions.ACTION,
                    Audit.object_id.in_(command_ids)))
                connection.execute(delete(ReportObjectRevisionCommand).where(
                    ReportObjectRevisionCommand.report_id == report_id))
                result = connection.execute(delete(ReportObjectRevision).where(ReportObjectRevision.report_id == report_id))
                assert result.rowcount == 1
            else:
                assert fault == 'material_version_changed'
                result = connection.execute(update(SetupMaterial).where(SetupMaterial.id == scene['original']['id'])
                    .values(version=SetupMaterial.version + 1))
                assert result.rowcount == 1
            def bound_session():
                with Session(bind=connection, expire_on_commit=False,
                        join_transaction_mode='create_savepoint') as db:
                    yield db
            app.dependency_overrides[session] = bound_session
            yield
        finally:
            if old is _ABSENT:
                app.dependency_overrides.pop(session, None)
            else:
                app.dependency_overrides[session] = old
            transaction.rollback()


@pytest.mark.parametrize('fault', ['confirm_audit_missing', 'revision_inventory_missing', 'material_version_changed'])
def test_missing_independent_confirmation_or_changed_source_fails_closed_and_rolls_back(confirmed_scene, fault):
    scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    source, _, body, raw, accepted = commit(client, report_id)
    scene['adopted_source'] = source
    fresh_source, fresh = source_command(client, report_id, target={'handoff_id': accepted['record']['id'],
        'record_hash': accepted['record']['record_hash'], 'design_hash': accepted['record']['design']['design_hash']})
    with rolled_back_source_fault(scene, fault):
        assert client.post(adoption_path(report_id) + '/preview', json=fresh).status_code == 409
        response = client.get(adoption_path(report_id) + '/requests/' + body['request_id'],
            params={'expected_sha256': hashlib.sha256(raw).hexdigest()})
        if fault == 'material_version_changed':
            historical = reply(response)
            assert historical['state'] == 'saved' and historical['reply']['record'] == accepted['record']
            unavailable = reply(client.get(adoption_path(report_id)))
            assert unavailable['available'] is False
        else:
            assert response.status_code == 409
    assert receipt(client, report_id, body, raw)['reply'] == accepted
    assert reply(client.get(adoption_path(report_id)))['available'] is True
    assert reply(client.get(revision_path(report_id)))['value'] == scene['value']
    assert_paid_unchanged(scene)


def test_confirmed_new_objects_cannot_enter_old_handoff_or_gain_source_execution_approval(confirmed_scene):
    scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    source, body = source_command(client, report_id)
    basis = scene['confirmed']['basis']
    legacy = {'schema': 'report-full-semantic-handoff-command-v1', 'request_id': str(uuid4()),
        'report_revision': basis['report_revision'], 'review_receipt_hash': '0' * 64,
        'manual_value_hash': digest(scene['value']), 'model_result_hash': basis['model_result_hash'],
        'source_hash': basis['source_hash'], 'catalog_hash': basis['catalog_hash'],
        'context_hash': basis['context_hash'], 'configuration_revision': source['configuration_revision'],
        'configuration_hash': source['configuration_hash'], 'share_scope': 'whole_result_excluding_private_note_v1'}
    denied = client.post(BASE + '/' + report_id + '/full-configuration-preview', json=legacy)
    assert denied.status_code == 409 and '独立新结构稿' in str(denied.json()['detail'])
    preview = reply(client.post(adoption_path(report_id) + '/preview', json=body))
    assert preview['source_status'] == 'unconfirmed' and preview['can_publish'] is preview['can_confirm'] is False
    assert preview['design']['execution_policy'] == 'requires_full_semantic_runtime_v1'
    assert all(flow['confirmedAt'] is flow['confirmedSignature'] is None for flow in preview['flows'].values())
    public_only(preview)
    assert_paid_unchanged(scene)


def test_missing_adoption_record_with_surviving_audit_is_integrity_failure_not_unknown_request(confirmed_scene):
    """Remove only the new record inside an externally rolled-back transaction."""
    fence(); scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    _, _, body, raw, accepted = commit(client, report_id)
    record = accepted['record']; previous_override = app.dependency_overrides.get(session, _ABSENT)
    with engine().connect() as connection:
        transaction = connection.begin()
        try:
            audit_ids = list(connection.scalars(select(Audit.id).where(
                Audit.workspace_id == record['workspace_id'], Audit.action == adoption.ACTION,
                Audit.object_id == record['id'])))
            assert len(audit_ids) == 1
            revision_ids = list(connection.scalars(select(SetupRevision.id).where(
                SetupRevision.draft_id == record['configuration_id'])))
            assert revision_ids
            config_before = connection.execute(select(SetupDraft.version, SetupDraft.data).where(
                SetupDraft.id == record['configuration_id'])).one()
            deleted = connection.execute(delete(ReportConfigurationRecord).where(
                ReportConfigurationRecord.id == record['id']))
            assert deleted.rowcount == 1
            # The independent command audit and durable configuration history
            # remain present; a lost record must not become a never-sent nonce.
            assert list(connection.scalars(select(Audit.id).where(Audit.id.in_(audit_ids)))) == audit_ids
            assert list(connection.scalars(select(SetupRevision.id).where(
                SetupRevision.draft_id == record['configuration_id']))) == revision_ids
            assert connection.execute(select(SetupDraft.version, SetupDraft.data).where(
                SetupDraft.id == record['configuration_id'])).one() == config_before
            def bound_session():
                with Session(bind=connection, expire_on_commit=False,
                        join_transaction_mode='create_savepoint') as db:
                    yield db
            app.dependency_overrides[session] = bound_session
            rejected = client.get(adoption_path(report_id) + '/requests/' + body['request_id'],
                params={'expected_sha256': hashlib.sha256(raw).hexdigest()})
            assert rejected.status_code == 409
            assert rejected.json().get('state') != 'missing'
        finally:
            if previous_override is _ABSENT:
                app.dependency_overrides.pop(session, None)
            else:
                app.dependency_overrides[session] = previous_override
            transaction.rollback()
    restored = receipt(client, report_id, body, raw)
    assert restored['state'] == 'saved' and restored['reply'] == accepted
    assert reply(client.get('/api/setup/draft')) == accepted['configuration']
    assert_paid_unchanged(scene)


def test_inventory_and_configuration_use_one_snapshot_during_normal_http_replacement(confirmed_scene, account):
    """Real W commit after R's inventory SELECT, without artificial success IO.

    The hook belongs only to R's connection. It observes completion of the one
    inventory SQL statement; W uses a different connection and ordinary login,
    preview/commit protocol. No lock, clock or isolation change makes W succeed.
    """
    from generic_agents import report_full_semantic_replacement as versions
    fence(); scene = confirmed_scene; client, report_id = scene['client'], scene['report_id']
    _, _, first_body, first_raw, first = commit(client, report_id)
    target = {'handoff_id': first['record']['id'], 'record_hash': first['record']['record_hash'],
        'design_hash': first['record']['design']['design_hash']}
    _, replacement_preview, replacement_body = prepare(client, report_id, target=target)
    replacement_raw = wire(replacement_body)
    state = {'fired': False, 'writer': None}

    def ordinary_writer():
        with TestClient(app) as writer:
            auth = reply(writer.post('/api/login', json={'username': account[0], 'password': account[1]}))
            writer.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
            assert reply(writer.get('/api/me'))['role'] == 'admin'
            return reply(writer.post(adoption_path(report_id) + '/commit', content=replacement_raw,
                headers={'content-type': 'application/json'}))

    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine().connect() as reader_connection:
            def after_inventory(conn, cursor, statement, parameters, context, executemany):
                normalized = ' '.join(statement.lower().split())
                if (state['fired'] or 'report_configuration_records' not in normalized
                        or 'full outer join' not in normalized or 'setup_drafts' not in normalized):
                    return
                assert conn is reader_connection
                state['fired'] = True
                state['writer'] = pool.submit(ordinary_writer).result(timeout=45)
            event.listen(reader_connection, 'after_cursor_execute', after_inventory)
            try:
                with Session(bind=reader_connection, expire_on_commit=False) as db:
                    records, heads, children, configuration = versions.record_set(
                        db, scene['p'], include_configuration=True)
                    assert state['fired'] is True and state['writer'] is not None
                    assert set(records) == set(heads) == {first['record']['id']}
                    assert children == {}
                    assert heads[first['record']['id']] == first['record']
                    assert configuration.id == first['record']['configuration_id']
                    assert configuration.version == first['configuration']['revision']
                    assert configuration.data == first['configuration']['configuration']
                    versions.assert_active_configuration(configuration.data, heads)
            finally:
                event.remove(reader_connection, 'after_cursor_execute', after_inventory)
    second = state['writer']
    assert second['record']['design'] == replacement_preview['design']
    assert second['record']['parent_id'] == first['record']['id']
    assert second['configuration']['revision'] == first['configuration']['revision'] + 1
    with Session(engine()) as fresh:
        records, heads, children, configuration = versions.record_set(
            fresh, scene['p'], include_configuration=True)
        assert set(records) == {first['record']['id'], second['record']['id']}
        assert set(heads) == {second['record']['id']}
        assert children == {first['record']['id']: second['record']['id']}
        assert configuration.version == second['configuration']['revision']
        assert configuration.data == second['configuration']['configuration']
        versions.assert_active_configuration(configuration.data, heads)
    assert receipt(client, report_id, first_body, first_raw)['reply']['record'] == first['record']
    assert receipt(client, report_id, replacement_body, replacement_raw)['reply'] == second
    assert_paid_unchanged(scene)
