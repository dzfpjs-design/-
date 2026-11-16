"""Consumer seams, not paid/source authority or an executed model journey.

Pure cases use the existing explicitly synthetic saved-Map source fixture and
real temporary-FS capture/native decoders. They DO NOT insert a CloudCall or
claim a successful financial record: settlement is observed at its boundary.
The separate PG case uses real login and normal capability create/validate/
publish/list APIs; no cap/current-account permission is mocked as successful.
"""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import cloud_budget
from generic_agents import report_business_catalogue_native_v211 as native
from generic_agents import report_business_catalogue_preparation_v211 as preparation
from generic_agents import report_business_catalogue_wire_v211 as wire
from generic_agents import report_design_profiles_v178 as profiles
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_saved_map_design_runtime_v176 as runtime
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall
from tests.test_report_saved_map_design_consumer_v179 import scene as legacy_scene
from tests.test_report_saved_map_design_preparation_v176 import source_scene, identity
from tests.test_vertical_slice import publish


@pytest.fixture
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Synthetic consumer seams cannot connect or launch processes')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    cap, source = source_scene()
    cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    p = NS(user_id=source['snapshot']['actor_id'], workspace_id=source['snapshot']['workspace_id'], role='operator')
    cap.id = identity(44); cap.workspace_id = p.workspace_id; cap.content_hash = digest(cap.data)
    activity = identity(180)
    body = consumer.PreviewIndexedBody(producer_id=source['snapshot']['producer']['id'], revision=7,
        model_id=cap.id, note='仅合成目录候选，不证明真实来源、费用许可或业务理解质量。',
        generation_profile='catalogue-v211', activity_id=activity)
    prep = preparation.build_preparation(cap, source, note=body.note)
    budget = {'schema': 'report-saved-map-design-budget-v176', 'call_limit': 1,
        'token_bound': prep['input_summary']['token_bound'], 'maximum_cost_usd': 3,
        'pricing_basis_hash': 'b' * 64, 'grant_hash': 'd' * 64}
    preview = {'schema': consumer.PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': body.model_dump(), 'source_context': copy.deepcopy(source),
        'capability': consumer._cap_value(cap), 'preparation': prep, 'budget': budget,
        **consumer.FALSE_FLAGS, 'generation_profile': 'catalogue-v211', 'activity_id': activity}
    preview['preview_hash'] = consumer.h(preview)
    row = NS(id=identity(181), workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=activity, producer_id=body.producer_id, model_id=body.model_id,
        grant_hash=budget['grant_hash'], revision=body.revision, version=1, status='queued',
        job_id=identity(182), reserved_call_id=None,
        data={'schema': consumer.STATE, 'preview': preview, 'dispatch_started': False,
            'dispatch_identity': None, 'call_pin': None, 'response': None, 'projection': None,
            'manual_value': None, 'error': None, 'command_refs': []})
    start = {**body.model_dump(), 'request_id': activity, 'source_snapshot_hash': prep['source_snapshot_hash'],
        'preview_hash': preview['preview_hash'], 'consent': True}
    return p, row, start


def candidate(row, *, question=False):
    count = row.data['preview']['preparation']['indexed_inputs']['input_count']
    return {'schema': wire.WIRE, 'input_count': count,
        'businesses': [{'key': 'b1', 'name': '公开合成目录候选', 'purpose': '结构测试',
            'boundary': '不声称实际业务语义正确', 'seed_positions': [0], 'name_basis': 'inferred',
            'reason': '纯接缝合成建议，不代表原文业务数量或业务划分'}],
        'allocation_groups': [{'kind': 'business', 'business_keys': ['b1'],
            'input_positions': list(range(count)), 'reason': '保留全部原位置，不证明分类合理',
            'question_keys': ['q_info'] if question else []}],
        'questions': [{'key': 'q_info', 'kind': 'missing_info', 'input_positions': [count - 1],
            'candidate_business_keys': ['b1'], 'question': '仍须核对资料中的未明确内容'}] if question else [],
        'merge_suggestions': []}


def captured(tmp_path, monkeypatch, row, value):
    """Actual FS/decoder output over synthetic bytes, without a DB reservation."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    row.reserved_call_id = identity(188); row.status = 'running'
    row.data.update(dispatch_started=True, dispatch_identity=consumer.dispatch_identity(row))
    ctx = runtime._context(row)
    selected = consumer._family(row.data['preview']['preparation']).native
    original = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
        'content': json.dumps(value, ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}}, ensure_ascii=False).encode('utf-8')
    root = tmp_path / 'synthetic-native-capture'
    with capture.prepare(root, ctx) as sink:
        sink.mark_request_started(); sink.begin_response(200, None, 'synthetic-public-test')
        sink.append(original); sink.finish(eof=True)
    monkeypatch.setattr(bridge, 'root', lambda: root)
    policy = copy.deepcopy(row.data['preview']['preparation']['generation_contract']['input_policy'])
    assert policy.pop('single_batch') is True
    text, receipt, descriptor = bridge.response_context(ctx, 'qwen', selected.MODEL, policy, selected.STAGE)
    assert text.encode('utf-8') == original
    state = capture.load(root, ctx)
    assert capture.read_body(root, ctx, state['capture_hash']) == original
    return {'body': original, 'receipt': receipt, 'capture': state, 'descriptor': descriptor,
        'binding': consumer.journal_binding(row)}, root, ctx


def read_boundary(monkeypatch, payload):
    reads, settlements = [], []
    db = object()
    def read(actual_db, p, row, *, recover_saved):
        assert actual_db is db
        reads.append(recover_saved)
        return copy.deepcopy(payload)
    def settle(actual_db, call_id, receipt, expected, status):
        assert actual_db is db
        settlements.append((call_id, copy.deepcopy(receipt), copy.deepcopy(expected), status))
    monkeypatch.setattr(consumer, 'runtime', lambda: NS(read_response=read))
    monkeypatch.setattr(cloud_budget, 'settle_paid_response', settle)
    return db, reads, settlements


@pytest.mark.usefixtures('no_external_io')
def test_catalogue_start_indexed_identity_and_original_replay_do_not_change_to_an_old_family():
    p, row, start = scene(); prep = row.data['preview']['preparation']
    consumer.check_preview(row.data['preview'], p)
    quote = consumer.preview_view(row.data['preview'])
    assert 'source_catalog' not in quote and 'catalog' not in quote['sent_payload']
    assert quote['sent_payload']['spans'] == prep['payload']['spans']
    value, exact, sha = consumer.read_wire(json.dumps(start, ensure_ascii=False).encode('utf-8'), consumer.StartIndexedBody)
    family = profiles.modules(value.generation_profile)
    assert family.id == 'catalogue-v211' and family.wire is wire
    assert consumer._start_path(family) == consumer.PREFIX + '/start-indexed'
    assert consumer.dispatch_identity(row)['task_id'] == 'report-business-catalogue-v211'
    assert consumer.dispatch_identity(row)['request_hash'] == prep['request_hash']
    frozen = consumer.result(row, 'start')
    command = NS(request_id=start['request_id'], actor_id=p.user_id, operation='start',
        path=consumer.PREFIX + '/start-indexed', exact_body=exact, body_sha256=sha, data={'result': frozen})
    row.version = 9; row.status = 'review'
    replay = consumer._replay({'original': command}, p, start['request_id'], 'start', command.path, exact, sha)
    assert replay == frozen and replay['version'] == 1
    replay['version'] = 999
    assert command.data['result'] == frozen
    changed = {**start, 'generation_profile': 'indexed-v179'}
    _, changed_exact, changed_sha = consumer.read_wire(json.dumps(changed).encode(), consumer.StartIndexedBody)
    with pytest.raises(HTTPException):
        consumer._replay({'original': command}, p, start['request_id'], 'start', command.path, changed_exact, changed_sha)
    wrong = copy.deepcopy(row.data['preview']); wrong['generation_profile'] = 'indexed-v179'
    wrong['body']['generation_profile'] = 'indexed-v179'
    wrong['preview_hash'] = consumer.h({k: v for k, v in wrong.items() if k != 'preview_hash'})
    with pytest.raises(HTTPException): consumer.check_preview(wrong, p)


@pytest.mark.usefixtures('no_external_io')
def test_real_capture_decoder_reaches_catalogue_projection_and_only_original_settlement_pins(tmp_path, monkeypatch):
    p, row, _ = scene(); payload, root, ctx = captured(tmp_path, monkeypatch, row, candidate(row, question=True))
    original_source = copy.deepcopy(row.data['preview']['source_context'])
    assert row.data['preview']['preparation']['source_catalog'] == original_source['catalog']
    assert 'catalog' not in row.data['preview']['preparation']['payload']
    db, reads, settlements = read_boundary(monkeypatch, payload)
    data, status = consumer._read_paid(db, p, row, recover_saved=True)
    assert status == 'needs_clarification' and reads == [True]
    assert data['manual_value'] is None and data['projection']['schema'] == wire.PROJECTION
    row.data = data; row.status = status
    view = consumer.view(row)
    assert view['result'] is None and view['manual_value'] is None
    assert view['catalogue'] == data['projection'] and view['generation_profile'] == 'catalogue-v211'
    assert view['catalogue']['input_coverage_complete'] is True
    assert row.data['preview']['source_context'] == original_source
    call_id, receipt, expected, settlement = settlements[0]
    prep = row.data['preview']['preparation']
    assert len(settlements) == 1 and call_id == row.reserved_call_id == receipt['call_id']
    assert settlement == 'succeeded'  # observed intent, NOT a DB CloudCall
    assert expected == {'workspace_id': p.workspace_id, 'capability_id': row.model_id, 'actor_id': p.user_id,
        'request_hash': prep['request_hash'], 'dispatch_options_hash': digest(prep['generation_contract']['dispatch_options']),
        'provider': 'qwen', 'model': native.MODEL}
    assert data['response']['body_sha256'] == hashlib.sha256(payload['body']).hexdigest()
    assert capture.read_body(root, ctx, payload['capture']['capture_hash']) == payload['body']
    again, again_status = consumer._read_paid(db, p, row, recover_saved=False)
    assert again == data and again_status == status and reads == [True] and len(settlements) == 1


@pytest.mark.usefixtures('no_external_io')
@pytest.mark.parametrize('fault', ['missing_position', 'old_design'])
def test_original_capture_is_retained_when_directory_is_incomplete_or_an_old_design(tmp_path, monkeypatch, fault):
    p, row, _ = scene(); value = candidate(row)
    if fault == 'missing_position':
        value['allocation_groups'][0]['input_positions'].pop()
    else:
        value = {'result': {'schema': 'report-object-semantic-candidate-wire-v166'}, 'reconciliation_groups': []}
    payload, root, ctx = captured(tmp_path, monkeypatch, row, value)
    db, reads, settlements = read_boundary(monkeypatch, payload)
    data, status = consumer._read_paid(db, p, row, recover_saved=True)
    assert status == 'failed' and data['projection'] is data['manual_value'] is None
    assert reads == [True] and len(settlements) == 1 and settlements[0][-1] == 'failed'
    assert data['response']['body_sha256'] == hashlib.sha256(payload['body']).hexdigest()
    assert capture.read_body(root, ctx, payload['capture']['capture_hash']) == payload['body']


@pytest.mark.usefixtures('no_external_io')
def test_capture_call_mismatch_cannot_settle_and_catalogue_does_not_decode_as_v179(tmp_path, monkeypatch):
    p, row, _ = scene(); value = candidate(row)
    payload, root, ctx = captured(tmp_path, monkeypatch, row, value)
    payload['receipt']['call_id'] = identity(999)
    db, _, settlements = read_boundary(monkeypatch, payload)
    with pytest.raises(HTTPException): consumer._read_paid(db, p, row, recover_saved=True)
    assert settlements == []
    old = legacy_scene(); old_p, old_row = old[0], old[4]
    # A separate actual old-family FS slot avoids manufacturing an old binding
    # for the directory capture. Its real old decoder must reject this JSON.
    old_payload, _, _ = captured(tmp_path / 'separate-legacy-fixture', monkeypatch, old_row, value)
    db, _, settlements = read_boundary(monkeypatch, old_payload)
    data, status = consumer._read_paid(db, old_p, old_row, recover_saved=True)
    assert status == 'failed' and data['projection'] is data['manual_value'] is None
    assert len(settlements) == 1 and settlements[0][-1] == 'failed'
    assert capture.read_body(root, ctx, payload['capture']['capture_hash']) == payload['body']


def registered_manifest(name='directory'):
    return {'contract_version': '1.0', 'kind': 'foundation_model', 'namespace': 'catalogue_tests',
        'name': name, 'version': '1.0.0', 'owner': '公开测试操作者', 'description': '正常模型登记与发现接口验证',
        'allowed_agent_roles': ['business'], 'execution': {'provider': 'qwen', 'model': native.MODEL,
            'secret_ref': 'DASHSCOPE_API_KEY', 'max_tokens': 8192, 'report_evidence_output': {
                'schema': native.OUTPUT_DECLARATION, 'max_tokens': 32768,
                'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}},
        'data_policy': {'cloud_allowed': True, 'redact_fields': []}}


def test_pg_models_discovery_uses_real_published_cap_and_current_native_policy_without_model_call(client, monkeypatch):
    with Session(engine()) as db:
        before = db.scalar(select(func.count()).select_from(CloudCall))
    cap = publish(client, registered_manifest())
    policy = native.input_policy; checked = []
    def observe(execution):
        result = policy(execution)
        checked.append(copy.deepcopy(execution))
        return result
    monkeypatch.setattr(native, 'input_policy', observe)
    response = client.get('/api/setup/understanding/models')
    assert response.status_code == 200
    selected = next(row for row in response.json() if row['id'] == cap['id'])
    assert selected['generation_profile'] == 'catalogue-v211' and selected['model'] == native.MODEL
    assert len(checked) == 1 and 'secret_ref' not in response.text
    # Denial only: model()/published()/IAM remain real and unchanged. A current
    # native-policy refusal must hide the option rather than label it eligible.
    def deny_after_real_validation(execution):
        policy(execution)
        raise ValueError('synthetic current-contract denial, never a success bypass')
    monkeypatch.setattr(native, 'input_policy', deny_after_real_validation)
    denied = client.get('/api/setup/understanding/models')
    assert denied.status_code == 200 and all(row['id'] != cap['id'] for row in denied.json())
    monkeypatch.setattr(native, 'input_policy', policy)
    invalid = registered_manifest('invalid_directory')
    invalid['execution']['report_evidence_output']['endpoint'] = 'https://invalid.example/never-contacted'
    created = client.post('/api/capabilities', json=invalid)
    assert created.status_code == 201, created.text
    refused = client.post('/api/capabilities/' + created.json()['id'] + '/validate',
        json={'expected_version': created.json()['version'], 'reason': '非法目录合同必须拒绝'})
    assert refused.status_code == 422
    visible = client.get('/api/setup/understanding/models')
    assert visible.status_code == 200 and all(row['id'] != created.json()['id'] for row in visible.json())
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before
