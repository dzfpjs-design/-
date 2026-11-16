"""Root-run owned PG + authenticated HTTP, with only public synthetic IO.

Document/OCR IO comes from reading_scene. Only the provider HTTP transport is
invented below. Model registration, reservations, worker, raw capture, journal,
projections and independent human saves remain actual product paths. These
tests prove neither supplier enum compliance nor original-report quality.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import gateway
from generic_agents import report_claim_enum_native_v172 as native
from generic_agents import report_business_catalogue_native_v211 as catalogue_native
from generic_agents import report_business_catalogue_wire_v211 as catalogue_wire
from generic_agents import report_design_healthy_source as healthy_source
from generic_agents import report_evidence_enum_map_v172 as maps
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Invocation, Job, Memory, SetupReportDraft
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v5 import call_ids, original
from tests.test_report_evidence_pipeline_v7_postgres import (
    NativeStream, NativeHTTP, mapped, reduced, storage_files, v7_scene)
from tests.test_report_evidence_enum_pipeline_v172 import candidate
from tests.test_report_object_adoption_postgres_v169 import revision_path, confirm_revision
from tests.test_vertical_slice import publish

BASE = '/api/setup/report-evidence-readings'
FAKE_KEY = 'PUBLIC_SYNTHETIC_ENUM_TRANSPORT_NOT_A_CREDENTIAL'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def paid_snapshot():
    """Observe existing settlements; never seed or repair a successful row."""
    with Session(engine()) as db:
        return {row.id: (row.workspace_id, row.capability_id, row.created_at,
            row.version, row.status, row.reserved_tokens, row.charged_tokens,
            copy.deepcopy(row.data)) for row in db.scalars(select(CloudCall))}


def assert_older_preserved(calls, files):
    current_calls, current_files = paid_snapshot(), storage_files()
    assert {identity: current_calls[identity] for identity in calls} == calls
    assert {path: current_files[path] for path in files} == files


@pytest.fixture
def enum_scene(reading_scene, monkeypatch):
    fence(); marker = os.environ.get('GA_F11_DUAL_TESTS_OWNED', '')
    assert marker.isdigit() and engine().url.port == int(marker)
    with Session(engine()) as db:
        assert tuple(db.execute(text("SELECT current_database(),shobj_description(oid,'pg_database') "
            'FROM pg_database WHERE datname=current_database()')).one()) == (
                'ga_f11_dual_tests', 'owned-disposable-f11-dual')
    scene = reading_scene
    manifest = json.loads((Path(__file__).parents[1] / 'examples/qwen.json').read_text())
    manifest.update(name='public_enum_v172_' + uuid4().hex[:10],
        description='PUBLIC synthetic transport only; not model or report quality')
    manifest['execution'].update(provider='qwen', model=native.MODEL, secret_ref='DASHSCOPE_API_KEY',
        max_tokens=8192, report_evidence_output={'schema': 'report-evidence-output-v172',
            'max_tokens': 32768, 'map_transport': native.DELIVERY, 'reduce_transport': native.DELIVERY,
            'endpoint': native.ENDPOINT})
    assert 'thinking_mode' not in manifest['execution']
    cap = publish(scene['client'], manifest)
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 25)
    return {**scene, 'cap': cap, 'body': {**copy.deepcopy(scene['body']),
        'request_id': 'fe_' + str(uuid4()), 'model_id': cap['id']}}


class EnumHTTP:
    """The only provider seam: original streaming HTTPX MockTransport bytes."""
    def __init__(self, monkeypatch, bad_ref=None, *, nested_flow=False, sensing=False):
        from generic_agents import report_business_flow_native_v193 as flow_native
        self.bad_ref, self.requests, self.responses, self.values, self.all_refs = bad_ref, [], [], [], []
        for name in ('DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_TOKENPLAN_API_KEY'):
            monkeypatch.setenv(name, '')
        monkeypatch.setattr(gateway, 'settings', lambda: NS(
            deepseek_api_key=NS(get_secret_value=lambda: FAKE_KEY) if nested_flow else None,
            dashscope_api_key=NS(get_secret_value=lambda: FAKE_KEY), qwen_tokenplan_api_key=None))
        original_client, owner = httpx.Client, self
        class Client:
            def __init__(self, **options):
                assert options.get('timeout') in (300, 600)
                assert options.get('follow_redirects') is False and options.get('trust_env') is False
                self.timeout = options['timeout']; options.pop('transport', None)
                self.inner = original_client(transport=httpx.MockTransport(self.handle), **options)
            def __enter__(self): self.inner.__enter__(); return self
            def __exit__(self, *args): return self.inner.__exit__(*args)
            def stream(self, method, endpoint, **options):
                assert method == 'POST' and (endpoint == native.ENDPOINT or nested_flow and endpoint == flow_native.ENDPOINT)
                assert set(options) == {'headers', 'content'} and type(options['content']) is bytes
                return self.inner.stream(method, endpoint, **options)
            def handle(self, request):
                assert request.headers['authorization'] == 'Bearer ' + FAKE_KEY
                assert request.headers['accept-encoding'] == 'identity'
                posted = json.loads(request.content); payload = json.loads(posted['messages'][-1]['content'])
                if nested_flow and str(request.url) == flow_native.ENDPOINT:
                    from generic_agents import business_flow_nested_plan_v192 as plans
                    from tests.test_report_business_flow_protocol_v184 import answer as simple_plan
                    from tests.test_report_business_flow_native_v193 import answer as nested_answer
                    assert payload['schema'] == flow_native.REQUEST_PAYLOAD_SCHEMA
                    assert posted['model'] == flow_native.MODEL and posted['stream'] is False
                    assert posted['max_tokens'] == 65536 and self.timeout == 600
                    plan = simple_plan({'scope_binding': payload['scope_binding'], 'payload': payload})
                    plan['schema'] = plans.PLAN
                    if sensing:
                        original_flow = plan['business']['flows'][0]
                        phase = original_flow['phases'][0]
                        action = copy.deepcopy(phase['steps'][0])
                        action.update(name='分析已登记温度', actor='perception_agent', role='温度监测岗位',
                            inputs=['本次温度记录'], outputs=['阈值判读方法'])
                        phase['steps'].insert(0, action)
                    return self.response(request, 'flow-v193', nested_answer(plan, payload['scope_binding']))
                assert str(request.url) == native.ENDPOINT
                if payload['schema'] == catalogue_native.REQUEST_PAYLOAD_SCHEMA:
                    assert self.timeout == 600 and posted['model'] == catalogue_native.MODEL
                    indexed = payload['indexed_inputs']
                    assert indexed['input_count'] > 0
                    assert [entry['input_position'] for entry in indexed['input_claims']] == list(range(indexed['input_count']))
                    wire = {'schema': catalogue_wire.WIRE, 'input_count': indexed['input_count'],
                        'businesses': [{'key': 'b1', 'name': '合成条件核对', 'purpose': '核对条件',
                            'boundary': '仅限合成资料', 'seed_positions': [0], 'name_basis': 'inferred',
                            'reason': '合成测试建议名称，最终名称由人工保存'}],
                        'allocation_groups': [{'kind': 'business', 'business_keys': ['b1'],
                            'input_positions': list(range(indexed['input_count'])),
                            'reason': '按原位置保留每条合成声明', 'question_keys': []}],
                        'questions': [], 'merge_suggestions': []}
                    return self.response(request, 'catalogue-v211', wire)
                reducing = payload['schema'] == 'report-claim-enum-reduction-payload-v172'
                assert posted['model'] == native.MODEL and posted['max_tokens'] == 32768
                assert posted['enable_thinking'] is False and posted['stream'] is False and posted['n'] == 1
                assert not ({'tools', 'tool_choice', 'thinking'} & set(posted))
                actual_schema = posted['response_format']['json_schema']['schema']
                stage = 'reduce-v172' if reducing else 'map-v172'
                native.identity_schema_proof(actual_schema, stage)
                if reducing:
                    wire = candidate({**payload, 'input_order': list(range(len(payload['input_claims'])))})
                else:
                    assert payload['schema'] == 'report-claim-array-input-v167'
                    assert payload['generation'] == maps.GENERATION and payload['task_id'] == maps.TASK_ID
                    assert type(payload['spans']) is list and payload['spans']
                    assert all(type(item) is dict and set(item) == {'span_ref', 'text'}
                        and type(item['span_ref']) is str and type(item['text']) is str
                        for item in payload['spans'])
                    refs = [item['span_ref'] for item in payload['spans']]
                    assert actual_schema == native.map_schema(refs)
                    reference = refs[0]
                    if owner.bad_ref == 'phantom':
                        reference = reference.split('/s')[0] + '/s999999'
                        assert reference not in owner.all_refs
                    elif owner.bad_ref == 'neighbor':
                        reference = next(ref for ref in owner.all_refs if ref not in refs)
                    wire = {'schema': 'report-evidence-map-v3', 'claims': [{'kind': '条件',
                        'statement': '仅当合成条件满足才办理；没有认定原文或行业事实。',
                        'span_refs': [reference], 'status': 'explicit', 'explanation': ''}]}
                return self.response(request, stage, wire)
            def response(self, request, stage, wire):
                owner.requests.append({'stage': stage, 'body': request.content})
                owner.values.append(copy.deepcopy(wire))
                raw = encoded({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
                    'content': encoded(wire).decode('utf-8')}}],
                    'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}})
                owner.responses.append(raw)
                return httpx.Response(200, headers={'x-request-id': 'public-enum-' + str(len(owner.requests))},
                    request=request, stream=NativeStream(raw, request))
        monkeypatch.setattr(gateway.httpx, 'Client', Client)


def started(scene, provider):
    client = scene['client']; preview = reply(client.post(BASE + '/preview', json=scene['body']))
    prep = preview['map_preparation']; assert prep['generation_contract']['schema'] == maps.GENERATION
    assert not {'output_schema', 'schema_proof', 'dispatch_options'} & set(prep['generation_contract'])
    assert 2 <= len(prep['plan']['chunks']) <= 24
    provider.all_refs = [ref for chunk in prep['plan']['chunks'] for ref in chunk['span_refs']]
    body = {**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}
    accepted = reply(client.post(BASE, json=body), 202)
    assert accepted['draft']['schema'] == 'report-claim-enum-reading-draft-v172'
    assert process_once(accepted['draft']['job_id'])
    current = reply(client.get(BASE + '/' + accepted['draft']['id']))
    return preview, body, accepted, current


def test_normal_enum_full_pipeline_dynamic_schema_raw_receipts_and_independent_human_cold_read(enum_scene, v7_scene, monkeypatch, account):
    # Create genuine older-family paid stages through its unchanged API/worker;
    # the new-family run must not alter those calls or immutable source bytes.
    with monkeypatch.context() as legacy_patch:
        legacy_provider = NativeHTTP(legacy_patch)
        _, _, _, legacy_middle = mapped(v7_scene, legacy_provider)
        _, _, _, legacy_draft = reduced(v7_scene, legacy_provider, legacy_middle)
        assert legacy_draft['status'] == 'review'
    legacy_count = len(legacy_provider.requests)
    assert legacy_count > 0
    scene = enum_scene; provider = EnumHTTP(monkeypatch)
    older_calls, older_files = paid_snapshot(), storage_files()
    preview, start_body, started_reply, middle = started(scene, provider)
    assert middle['status'] == 'awaiting_merge' and middle['model_result'] is None
    prep = preview['map_preparation']; assert len(provider.requests) == len(prep['plan']['chunks'])
    generation_hashes = []
    for index, (chunk, stage) in enumerate(zip(prep['plan']['chunks'], middle['maps'])):
        spec = maps.stage_spec(prep, chunk['chunk_id']); posted = json.loads(provider.requests[index]['body'])
        assert stage['status'] == 'succeeded' and posted['response_format']['json_schema']['schema'] == spec['output_schema']
        assert original(scene, middle, chunk['chunk_id'])['content'].encode('utf-8') == provider.responses[index]
        assert stage['result']['stage_spec_hash'] == digest(spec)
        assert stage['result']['schema_proof_hash'] == digest(spec['schema_proof'])
        generation_hashes.append(stage['result']['binding']['generation_hash'])
        with Session(engine()) as db:
            call = db.get(CloudCall, stage['reserved_call_id'])
            assert call.data['delivery'] == native.DELIVERY
            assert call.data['schema_proof_hash'] == digest(spec['schema_proof'])
            assert call.data['request_hash'] == hashlib.sha256(provider.requests[index]['body']).hexdigest() == chunk['request_hash']
    assert len(set(generation_hashes)) == len(generation_hashes)
    command = {'revision': middle['revision'], 'request_id': 'fer_' + str(uuid4())}
    reduce_preview = reply(scene['client'].post(BASE + '/' + middle['id'] + '/reduce-preview', json=command))
    assert reduce_preview['reduction_preparation']['generation_contract']['schema'] == 'report-claim-enum-reduction-generation-v172'
    command.update(preview_hash=reduce_preview['preview_hash'], consent=True)
    accepted = reply(scene['client'].post(BASE + '/' + middle['id'] + '/reduce', json=command), 202)
    assert process_once(accepted['draft']['job_id'])
    final = reply(scene['client'].get(BASE + '/' + middle['id']))
    assert final['status'] == 'review' and final['model_result'] == provider.values[-1]['result']
    assert final['model_result_hash'] == digest(final['model_result'])
    report_id = final['id']; independent = reply(scene['client'].get(revision_path(report_id)))
    assert independent['value']['result'] == final['model_result']
    assert all(ack['acknowledge_platform_identity'] is False for ack in independent['value']['object_identity_confirmations'])
    human = copy.deepcopy(independent['value']); human['note'] = '本人的独立私稿，不能变成机器原答。'
    for ack in human['object_identity_confirmations']: ack['acknowledge_platform_identity'] = True
    saved = confirm_revision(scene['client'], report_id, human)
    identities = call_ids(final)
    with Session(engine()) as db:
        calls = {identity: copy.deepcopy(db.get(CloudCall, identity).data) for identity in identities}
    files = storage_files(); count = len(provider.requests)
    with TestClient(app) as cold:
        auth = reply(cold.post('/api/login', json={'username': account[0], 'password': account[1]}))
        cold.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        assert reply(cold.get(BASE + '/' + report_id))['model_result'] == final['model_result']
        assert reply(cold.get(revision_path(report_id)))['value'] == saved['value'] == human
        assert reply(cold.get(BASE + '/by-request/' + start_body['request_id']))['draft']['id'] == report_id
        me = reply(cold.get('/api/me'))
    principal = Principal(me['user_id'], me['workspace_id'], me['role'], '')
    reduce_prep = reduce_preview['reduction_preparation']
    expected_claims = [claim for value in provider.values[:-1] for claim in value['claims']]
    paid_before = paid_snapshot()
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, report_id)
        original_state = (row.version, row.status, copy.deepcopy(row.data))
        job_ids = set(db.scalars(select(Job.id).where(Job.workspace_id == principal.workspace_id)))
        context = healthy_source.cold_source_context(db, principal, report_id)
        snapshot, claims, spans = healthy_source.check_context(context)
        from generic_agents import report_design_saved_source_v174, report_saved_map_design_preparation_v176
        assert report_design_saved_source_v174.load_source_context(db, principal, report_id) == context
        assert report_saved_map_design_preparation_v176._source(context) == (snapshot, claims, spans)
        assert snapshot['schema'] == healthy_source.SCHEMA
        assert snapshot['producer']['id'] == report_id
        assert snapshot['input_order'] == reduce_prep['input_order']
        assert snapshot['input_claims_hash'] == reduce_prep['input_claims_hash'] == digest(expected_claims)
        assert claims == expected_claims == reduce_prep['payload']['input_claims']
        assert spans == reduce_prep['payload']['spans']
        assert [item['cloud_call']['id'] for item in snapshot['map_calls']] == [
            stage['reserved_call_id'] for stage in middle['maps']]
        reduction = snapshot['healthy_reduction']
        assert reduction['job']['id'] == accepted['draft']['job_id']
        assert reduction['job']['status'] == reduction['cloud_call']['status'] == 'succeeded'
        assert reduction['model_result_hash'] == final['model_result_hash']
        assert reduction['http_body_sha256'] == hashlib.sha256(provider.responses[-1]).hexdigest()
        assert snapshot['original_reduction_adopted'] is False
        db.commit()
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, report_id)
        assert (row.version, row.status, row.data) == original_state
        assert set(db.scalars(select(Job.id).where(Job.workspace_id == principal.workspace_id))) == job_ids
    assert paid_snapshot() == paid_before
    assert len(provider.requests) == count and storage_files() == files
    with Session(engine()) as db:
        assert {identity: db.get(CloudCall, identity).data for identity in identities} == calls
    assert reply(scene['client'].get(BASE + '/' + legacy_draft['id'])) == legacy_draft
    assert len(legacy_provider.requests) == legacy_count
    assert_older_preserved(older_calls, older_files)


def confirmed_catalogue(enum_scene, monkeypatch, account, *, nested_flow=False, sensing=False):
    client = enum_scene['client']
    provider = EnumHTTP(monkeypatch, nested_flow=nested_flow, sensing=sensing)
    _, _, _, middle = started(enum_scene, provider)
    command = {'revision': middle['revision'], 'request_id': 'fer_' + str(uuid4())}
    quote = reply(client.post(f"{BASE}/{middle['id']}/reduce-preview", json=command))
    command.update(preview_hash=quote['preview_hash'], consent=True)
    accepted = reply(client.post(f"{BASE}/{middle['id']}/reduce", json=command), 202)
    assert process_once(accepted['draft']['job_id'])
    report = reply(client.get(f"{BASE}/{middle['id']}"))
    assert report['status'] == 'review' and report['model_result'] == provider.values[-1]['result']
    me = reply(client.get('/api/me'))
    p = Principal(me['user_id'], me['workspace_id'], me['role'], '')
    with Session(engine()) as db:
        context = healthy_source.cold_source_context(db, p, report['id'])
        snapshot, claims, spans = healthy_source.check_context(context)
    assert snapshot['producer']['id'] == report['id'] and claims and spans
    older_calls, older_files = paid_snapshot(), storage_files()

    manifest = json.loads((Path(__file__).parents[1] / 'examples/qwen.json').read_text())
    manifest.update(name='public_catalogue_v211_' + uuid4().hex[:10])
    manifest['execution'].update(provider='qwen', model=catalogue_native.MODEL,
        secret_ref='DASHSCOPE_API_KEY', max_tokens=8192, report_evidence_output={
            'schema': catalogue_native.OUTPUT_DECLARATION, 'max_tokens': 32768,
            'reduce_transport': catalogue_native.DELIVERY, 'endpoint': catalogue_native.ENDPOINT})
    cap = publish(client, manifest)
    activity = str(uuid4())
    base = '/api/setup/report-saved-map-designs'
    configuration_revision = reply(client.get('/api/setup/draft'))['revision']
    body = {'producer_id': report['id'], 'revision': configuration_revision, 'model_id': cap['id'],
        'note': '仅从已保存合成声明整理业务', 'activity_id': activity, 'generation_profile': 'catalogue-v211'}
    quote = reply(client.post(base + '/preview-indexed', json=body))
    assert quote['source_snapshot_hash'] == snapshot['snapshot_hash']
    assert quote['claim_count'] == len(claims)
    count = len(provider.requests)
    start = reply(client.post(base + '/start-indexed', json={**body, 'request_id': activity,
        'source_snapshot_hash': quote['source_snapshot_hash'], 'preview_hash': quote['preview_hash'],
        'consent': True}), 202)
    assert process_once(start['job_id'])
    machine = reply(client.get(base + '/' + start['id']))
    capture = reply(client.get(base + '/' + start['id'] + '/capture-summary'))
    assert len(provider.requests) == count + 1 and provider.requests[-1]['stage'] == 'catalogue-v211'
    assert capture['available'] and capture['response_complete'] and capture['http_status'] == 200
    assert machine['catalogue']['wire'] == provider.values[-1]
    assert_older_preserved(older_calls, older_files)
    calls, files = paid_snapshot(), storage_files()

    drafts = '/api/setup/report-business-catalogues/v211/drafts'
    made = reply(client.post(drafts, json={'request_id': str(uuid4()), 'consumer_id': machine['id'],
        'expected_projection_hash': machine['catalogue']['projection_hash']}), 201)
    path = drafts + '/' + made['id']
    dto = reply(client.get(path))
    handoff = path + '/business-flow-source'
    assert client.get(handoff, params={'scope_id': 'b1'}).status_code == 409
    human = copy.deepcopy(dto['document'])
    human['businesses'][0]['name'] = '人工确认的合成条件核对'
    saved = reply(client.post(path + '/save', json={'request_id': str(uuid4()),
        'expected_version': dto['version'], 'expected_record_hash': dto['record_hash'],
        'document': human, 'actions': []}))
    confirm_body = {'request_id': str(uuid4()), 'expected_version': saved['version'],
        'expected_record_hash': saved['record_hash'], 'document_hash': saved['document_hash'], 'confirm': True}
    confirmed = reply(client.post(path + '/confirm', json=confirm_body))
    with TestClient(app) as cold:
        auth = reply(cold.post('/api/login', json={'username': account[0], 'password': account[1]}))
        cold.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        assert reply(cold.post(path + '/confirm', json=confirm_body)) == confirmed
        result = reply(cold.get(handoff, params={'scope_id': 'b1'}))
        assert result['schema'] == 'report-catalogue-flow-source-v316'
        assert result['producer_id'] == report['id'] and result['revision'] == configuration_revision
        assert result['scope_id'] == 'b1' and result['business_name'] == human['businesses'][0]['name']
        assert result['draft_version'] == confirmed['version']
        assert result['document_hash'] == confirmed['document_hash']
        for key in ('plan_id', 'plan_version', 'plan_hash'):
            assert result[key] == confirmed['plan_ref'][key]
        assert result['source_snapshot_hash'] == snapshot['snapshot_hash']
        assert cold.get(handoff, params={'scope_id': 'other'}).status_code == 404
    assert reply(client.get(f"{BASE}/{report['id']}")) == report
    assert len(provider.requests) == count + 1 and paid_snapshot() == calls and storage_files() == files
    return provider, result


def test_healthy_report_native_catalogue_human_confirmation_and_flow_source(enum_scene, monkeypatch, account):
    confirmed_catalogue(enum_scene, monkeypatch, account)


def nested_human_adoption_body(context, actor_id, business_name, *, sensing=None):
    """Explicit synthetic operator choices using the API's actual action ports."""
    def schema(fields):
        return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}

    def guards():
        return dict.fromkeys(('start', 'precondition', 'stop', 'exception'))

    assert any(member['user_id'] == actor_id for member in context['members'])
    assert 'declared_manual' in context['allowed_entry_modes'] and not context['controls']
    actions = context['action_ports']
    assert actions and all(item['actor'] in {'human', 'perception_agent'} for item in actions)
    assert [item['path'] for item in actions] == [item['path'] for item in context['all_action_ports']]
    contract = {'data_type': '人工核对资料', 'unit': None}
    field = {'type': 'string', 'minLength': 1}
    entry = '/entry_ports/0'
    execution = {'schema': 'business-flow-nested-execution-bindings-v195',
        'input_schema': schema({'original_material': field}),
        'references': {'original_material': {'kind': 'string', 'nullable': False,
            'source': 'input', 'field': 'original_material', 'node_id': None}},
        'predicates': {}, 'guards': guards(), 'nodes': {}, 'entries': {entry: 'original_material'},
        'conditions': [], 'loops': [], 'contract_schemas': [{'contract': contract, 'kind': 'string', 'nullable': False}]}
    causal = {'schema': 'business-flow-nested-causal-bindings-v192',
        'source_plan_hash': context['source_plan_hash'], 'scope_binding_hash': context['scope_binding_hash'],
        'entry_ports': [{'path': entry, 'flow_path': '/business/flows/0', 'contract': contract}],
        'output_contracts': [], 'input_bindings': []}
    node_actions = {}
    if sensing is not None:
        sensor_contract = {'data_type': '温度结构化记录', 'unit': None}
        execution['input_schema'] = schema({'original_material': field,
            'temperature_reading': sensing['data']['input_schema']['properties']['attributes']})
        execution['references']['temperature_reading'] = {'kind': 'object', 'nullable': False,
            'source': 'input', 'field': 'temperature_reading', 'node_id': None}
        execution['entries']['/entry_ports/1'] = 'temperature_reading'
        execution['contract_schemas'].append({'contract': sensor_contract, 'kind': 'object', 'nullable': False})
        causal['entry_ports'].append({'path': '/entry_ports/1', 'flow_path': '/business/flows/0',
            'contract': sensor_contract})
    for action in actions:
        if action['actor'] == 'perception_agent':
            assert sensing is not None and len(action['input_ports']) == len(action['output_ports']) == 1
            cap = sensing['data']
            execution['nodes'][action['path']] = {'kind': 'ability', 'actor_id': actor_id,
                'guards': guards(), 'input_ports': [{'ordinal': 0, 'reference': 'temperature_reading'}],
                'output_ports': [{'ordinal': 0, 'field': 'method'}],
                'execution': {'capability_id': sensing['id'], 'capability_hash': sensing['content_hash'],
                    'agent_role': 'perception', 'input_bindings': {'attributes': 'temperature_reading'},
                    'arguments': {}, 'input_schema': cap['input_schema'], 'output_schema': cap['output_schema'],
                    'adapter': 'builtin', 'release_version': sensing['release_version'], 'max_attempts': 1}}
            node_actions[action['graph_node_id']] = 'observe'
            causal['output_contracts'].append({'path': action['output_ports'][0]['path'], 'contract': contract})
            causal['input_bindings'].append({'input_path': action['input_ports'][0]['path'],
                'contract': sensor_contract, 'sources': ['/entry_ports/1']})
            continue
        outputs = [{'ordinal': port['ordinal'], 'field': 'output_' + str(port['ordinal'] + 1)}
            for port in action['output_ports']]
        execution['nodes'][action['path']] = {'kind': 'human', 'actor_id': actor_id,
            'role': action['role'], 'guards': guards(), 'output_schema': schema({item['field']: field for item in outputs}),
            'input_ports': [{'ordinal': port['ordinal'], 'reference': 'original_material'}
                for port in action['input_ports']], 'output_ports': outputs}
        causal['output_contracts'].extend({'path': port['path'], 'contract': contract} for port in action['output_ports'])
        causal['input_bindings'].extend({'input_path': port['path'], 'contract': contract, 'sources': [entry]}
            for port in action['input_ports'])
    return {'schema': 'business-flow-nested-execution-preview-command-v195',
        'qualification_id': context['qualification_id'], 'qualification_hash': context['qualification_hash'],
        'flow_index': 0, 'business_name': business_name, 'configuration_revision': context['configuration_revision'],
        'configuration_hash': context['configuration_hash'], 'execution_bindings': execution,
        'causal_bindings': causal, 'role_bindings': dict.fromkeys(context['roles'], actor_id),
        'node_actions': node_actions, 'external_node_ids': [], 'entry_mode': 'declared_manual'}


def publish_numeric_sensing(client):
    from generic_agents.contracts import Manifest

    def schema(**fields):
        return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}

    attributes = schema(temperature={'type': 'number'})
    anomaly = schema(field={'type': 'string'}, value={'type': 'number'},
        rule_id={'type': 'string'}, severity={'type': 'string'})
    return publish(client, Manifest(kind='specialist_model', namespace='public_report_flow',
        name='local_temperature_' + uuid4().hex[:10], version='1.0.0', owner='合成测试',
        description='对明确登记的合成温度做本地规则识别', allowed_agent_roles=['perception'],
        input_schema=schema(attributes=attributes), output_schema=schema(attributes=attributes,
            anomalies={'type': 'array', 'items': anomaly}, method={'type': 'string'}),
        execution={'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
        data_policy={'cloud_allowed': False}, budget={'max_attempts': 1, 'max_concurrency': 1},
        specification={'rules': [{'id': 'high_temp', 'field': 'temperature',
            'operator': 'gt', 'threshold': 50}]}).model_dump(mode='json'))


@pytest.mark.parametrize('sensing', [False, True], ids=['human-only', 'local-sensing'])
def test_confirmed_catalogue_scope_drives_registered_nested_flow_and_saved_human_review(enum_scene, monkeypatch, account, sensing):
    from generic_agents import report_business_flow_native_v193 as flow
    from generic_agents import report_scoped_design_consumer_v181 as attempts

    client = enum_scene['client']
    provider, source = confirmed_catalogue(enum_scene, monkeypatch, account, nested_flow=True, sensing=sensing)
    previous_calls, previous_files = paid_snapshot(), storage_files()
    manifest = json.loads((Path(__file__).parents[1] / 'examples/qwen.json').read_text())
    manifest.update(name='public_nested_flow_v193_' + uuid4().hex[:10])
    manifest['execution'] = {'provider': flow.PROVIDER, 'model': flow.MODEL, 'max_tokens': 2048,
        'secret_ref': 'DEEPSEEK_API_KEY', 'report_evidence_output': {
            'schema': flow.OUTPUT_DECLARATION, 'max_tokens': 65536,
            'reduce_transport': flow.DELIVERY, 'endpoint': flow.ENDPOINT}}
    cap = publish(client, manifest)
    nonce = str(uuid4())
    body = {'plan_id': source['plan_id'], 'expected_plan_version': source['plan_version'],
        'plan_hash': source['plan_hash'], 'scope_id': source['scope_id'], 'revision': source['revision'],
        'model_id': cap['id'], 'note': '先核对资料，再由人工保存结论。',
        'activity_id': nonce, 'generation_profile': 'flow-v193'}
    quote = reply(client.post(attempts.PREFIX + '/preview-flow-nested', json=body))
    assert quote['source_snapshot_hash'] == source['source_snapshot_hash']
    count = len(provider.requests)
    started = reply(client.post(attempts.PREFIX + '/start-flow-nested', json={
        **body, 'request_id': nonce, 'consent': True, 'source_snapshot_hash': quote['source_snapshot_hash'],
        'preview_hash': quote['preview_hash']}), 202)
    assert process_once(started['job_id'])
    path = attempts.PREFIX + '/' + started['id']
    generated = reply(client.get(path))
    assert generated['status'] in {'review', 'needs_clarification'}
    assert generated['generation_profile'] == 'flow-v193'
    assert generated['result'] == provider.values[-1]['plan']
    assert generated['projection']['raw_causal_plan'] == provider.values[-1]
    assert generated['result']['business']['name'] == source['business_name']
    assert len(provider.requests) == count + 1 and provider.requests[-1]['stage'] == 'flow-v193'
    sent = json.loads(json.loads(provider.requests[-1]['body'])['messages'][-1]['content'])
    assert sent['design_note'] == body['note'] and sent['scope_binding']['name'] == source['business_name']
    assert_older_preserved(previous_calls, previous_files)
    calls, files = paid_snapshot(), storage_files()

    review = '/api/setup/business-flow-reviews/attempts/' + started['id']
    original = reply(client.get(review))
    assert original['source_binding']['generation_profile'] == 'flow-v193'
    human = copy.deepcopy(original['value'])
    human['business']['flows'][0]['name'] = '已人工核对的合成流程'
    saved = reply(client.post(review + '/save', json={'request_id': str(uuid4()),
        'expected_version': original['version'], 'value': human, 'note': '保留人工核对依据'}))
    mode = 'author_self_review'
    review_quote = reply(client.get(review + '/nested-review-preview', params={'review_mode': mode}))
    assert review_quote['expected_version'] == saved['version'] and review_quote['value_hash'] == digest(human)
    review_nonce = str(uuid4())
    review_body = encoded({'schema': 'business-flow-nested-review-command-v195',
        'request_id': review_nonce, 'expected_version': review_quote['expected_version'],
        'save_request_id': review_quote['save_request_id'], 'value_hash': review_quote['value_hash'],
        'ack_scope': True, 'ack_inferences': True, 'reason': '已逐项核对合成来源、条件和办理边界',
        'review_mode': mode})
    qualified = reply(client.post(review + '/qualify-nested', content=review_body))
    assert qualified['status'] == 'qualified' and qualified['human_review'] == 'author_self_reviewed'
    assert qualified['value'] == human and qualified['source_binding'] == original['source_binding']
    assert qualified['source_snapshot_hash'] == review_quote['source_snapshot_hash']
    execution = '/api/setup/business-flow-executions'
    context_query = {'qualification_id': qualified['id'], 'flow_index': 0}
    extractor = publish_numeric_sensing(client) if sensing else None
    actual = reply(client.get(execution + '/nested-context', params=context_query))
    if extractor is not None:
        assert any(row['id'] == extractor['id'] and 'perception' in row['eligible_agent_roles']
            for row in actual['capabilities'])
    before = reply(client.get('/api/setup/draft'))
    assert before['revision'] == actual['configuration_revision']
    actor = reply(client.get('/api/me'))['user_id']
    adoption = nested_human_adoption_body(actual, actor, source['business_name'], sensing=extractor)
    stale = {**adoption, 'configuration_revision': adoption['configuration_revision'] + 1}
    assert client.post(execution + '/nested-preview', json=stale).status_code == 409
    assert reply(client.get('/api/setup/draft')) == before
    adoption_preview = reply(client.post(execution + '/nested-preview', json=adoption))
    assert adoption_preview['can_execute'] is False
    adoption_nonce = str(uuid4())
    adoption_raw = encoded({**adoption, 'schema': 'business-flow-nested-execution-adopt-command-v195',
        'request_id': adoption_nonce, 'preview_hash': adoption_preview['preview_hash'],
        'consent': True, 'share_source': True})
    adopted = reply(client.post(execution + '/nested-adopt', content=adoption_raw))
    assert adopted['can_execute'] is False and adopted['qualification_id'] == qualified['id']
    assert adopted['accepted_revision'] == before['revision'] + 1
    with TestClient(app) as cold:
        auth = reply(cold.post('/api/login', json={'username': account[0], 'password': account[1]}))
        cold.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        assert reply(cold.get(path)) == generated
        reviewed = reply(cold.get(review))
        assert reviewed['version'] == saved['version'] and reviewed['value'] == human
        original_request = '/api/setup/business-flow-reviews/nested-review-requests/' + review_nonce
        receipt = reply(cold.get(original_request, params={'expected_sha256': hashlib.sha256(review_body).hexdigest()}))
        assert receipt['found'] is True and receipt['result'] == qualified
        assert reply(cold.get(review + '/nested-reviews')) == [qualified]
        original_adoption = reply(cold.get(execution + '/nested-requests/' + adoption_nonce,
            params={'expected_sha256': hashlib.sha256(adoption_raw).hexdigest()}))
        assert original_adoption['found'] is True and original_adoption['result'] == adopted
        assert reply(cold.post(execution + '/nested-adopt', content=adoption_raw)) == adopted
        configuration = reply(cold.get('/api/setup/draft'))
        assert configuration['revision'] == adopted['accepted_revision']
        assert digest(configuration['configuration']) == adopted['configuration_hash']
        name = source['business_name']
        assert configuration['configuration']['businessFlowExecutions'][name] == adopted['origin']
        assert configuration['configuration']['flows'][name] == adoption_preview['binding']['flow']

    from generic_agents.setup_automation import safe_policy
    from tests.test_instance_workspace import OBJECTS, object_row, save

    configured = copy.deepcopy(configuration['configuration'])
    configured.setdefault('automation', {'version': 1, 'policies': {}})['policies'][name] = safe_policy()
    if sensing:
        configured['automation']['policies'][name]['rules']['observe'] = {'mode': '自动运行'}
    revision = save(client, configured, review_automation=True)
    publication = {'revision': revision, 'objects': copy.deepcopy(OBJECTS),
        'role_bindings': adoption['role_bindings']}
    checked = reply(client.post('/api/instances/check', json=publication))
    assert not checked['general_issues']
    business = next(row for row in checked['businesses'] if row['name'] == name)
    assert business['available'] and business['execution_mode'] == 'confirmed_graph_workflow', business['issues']
    release = reply(client.post('/api/instances', json={**publication,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    business_id = business['id']
    assert business_id in release['available_business_ids']
    bindings = release['snapshot']['semantic_runtime_plan']['graph_adoptions']
    selected = [row['binding'] for row in bindings if row['binding']['binding_hash'] == adopted['binding_hash']]
    assert selected == [adoption_preview['binding']]
    instance = next(item for item in reply(client.get('/api/instances')) if item['id'] == release['instance_id'])
    reply(client.post('/api/instances/' + instance['id'] + '/activate', json={'version': instance['version'],
        'release_id': release['id'], 'business_ids': [business_id], 'confirm_workflow_scope': True}))
    entity = object_row(client, release)
    creation = {'request_id': str(uuid4()), 'release_id': release['id'], 'business_id': business_id,
        'entity_ids': [entity['id']], 'title': '从已采用报告流程创建的合成任务',
        'graph_inputs': {'original_material': '人工登记的本次核对材料'}}
    if sensing:
        creation['graph_inputs']['temperature_reading'] = {'temperature': 70}
    tasks = '/api/workspace/instances/' + instance['id'] + '/tasks'
    created = reply(client.post(tasks, json=creation), 201)
    task_path = '/api/workspace/tasks/' + created['id']
    if sensing:
        initial = reply(client.get(task_path))
        (activation_id, transport), = initial['graph_jobs'].items()
        assert transport['phase'] == 'queued' and process_once(transport['job_id'], deliver=False)
        settled = reply(client.get(task_path))
        assert settled['graph_jobs'][activation_id]['phase'] == 'succeeded'
        sensor_receipt, = settled['workflow_runtime']['receipts']
        assert sensor_receipt['execution_mode'] == 'registered_graph_ability'
        assert sensor_receipt['activation_id'] == activation_id
        assert sensor_receipt['input_snapshot'] == {'attributes': {'temperature': 70}}
        assert sensor_receipt['output']['anomalies'] == [
            {'field': 'temperature', 'value': 70, 'rule_id': 'high_temp', 'severity': 'warning'}]
        with Session(engine()) as db:
            job = db.get(Job, transport['job_id'])
            call = db.get(Invocation, sensor_receipt['invocation_id'])
            assert job.status == call.status == 'succeeded'
            assert call.capability_id == extractor['id'] and call.idempotency_key == transport['invocation_key']
            assert call.data['output'] == sensor_receipt['output']
            audit, = list(db.scalars(select(Audit).where(Audit.workspace_id == call.workspace_id,
                Audit.action == 'capability.invoke', Audit.object_id == call.id)))
            assert audit.actor_id == actor and audit.data == {'status': 'succeeded'}
    with TestClient(app) as cold:
        auth = reply(cold.post('/api/login', json={'username': account[0], 'password': account[1]}))
        cold.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        task = reply(cold.get(task_path))
        assert task['title'] == creation['title'] and task['business']['id'] == business_id
        assert task['workflow_review_mode'] == 'author_self_review' and not task['completed']
        assert task['graph_runtime']['graph_hash'] == digest(adoption_preview['binding']['graph'])
        assert any(row['actor_id'] == actor and row['can_complete'] for row in task['graph_pending_humans'])
    assert task['layered_approval_v175'] is None
    human_nodes = [node for node in adoption_preview['binding']['graph']['nodes'] if node['kind'] == 'human']
    assert len(human_nodes) == 2
    commands = []
    for index in range(len(human_nodes)):
        pending = [row for row in task['graph_pending_humans'] if row['can_complete']]
        assert len(pending) == 1 and pending[0]['actor_id'] == actor
        ready = pending[0]
        command = {'request_id': str(uuid4()), 'activation_id': ready['activation_id'],
            'version': ready['version'], 'cancel_epoch': task['graph_runtime']['cancel_epoch'],
            'output': {key: f'人工核对第 {index + 1} 步并保存原说明'
                for key in ready['output_schema']['required']}}
        commands.append(command)
        task = reply(client.post(task_path + '/graph/complete', json=command))
        assert task['completed'] is (index == len(human_nodes) - 1)
        assert len(task['workflow_runtime']['receipts']) == index + 1 + int(sensing)
    assert task['status'] == 'workflow_completed' and task['graph_runtime']['status'] == 'succeeded'
    assert task['graph_runtime']['goal_receipts'] and task['graph_pending_humans'] == []
    receipts = [row for row in task['workflow_runtime']['receipts'] if row['execution_mode'] == 'human_graph_result']
    assert [(row['request_id'], row['activation_id'], row['actor'], row['output']) for row in receipts] == [
        (command['request_id'], command['activation_id'], actor, command['output']) for command in commands]
    assert all(row['execution_mode'] == 'human_graph_result' and row['entity_state_updated'] is False for row in receipts)
    with TestClient(app) as cold:
        auth = reply(cold.post('/api/login', json={'username': account[0], 'password': account[1]}))
        cold.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        reopened = reply(cold.get(task_path))
        assert reopened['workflow_runtime'] == task['workflow_runtime'] and reopened['graph_runtime'] == task['graph_runtime']
        for command in commands:
            replay = reply(cold.post(task_path + '/graph/complete', json=command))
            assert replay['version'] == reopened['version'] and replay['workflow_runtime'] == reopened['workflow_runtime']
    with Session(engine()) as db:
        evidence = db.get(Memory, task['workflow_runtime']['data_receipt_id'])
        assert evidence.status == 'candidate' and evidence.data['type'] == 'workflow_graph_result'
        assert evidence.data['task_id'] == task['id'] and evidence.data['graph_hash'] == task['graph_runtime']['graph_hash']
        assert evidence.data['state_hash'] == task['graph_runtime']['state_hash']
        assert evidence.data['goal_receipts'] == task['graph_runtime']['goal_receipts']
        assert evidence.data['auto_publish'] is False and evidence.data['entity_state_updated'] is False
    assert len(provider.requests) == count + 1 and paid_snapshot() == calls and storage_files() == files


@pytest.mark.parametrize('bad_ref', ['phantom', 'neighbor'])
def test_invalid_original_reference_stays_failed_with_raw_capture_no_later_maps_or_reduce(enum_scene, monkeypatch, bad_ref):
    scene = enum_scene; provider = EnumHTTP(monkeypatch, bad_ref)
    older_calls, older_files = paid_snapshot(), storage_files()
    preview, _, accepted, final = started(scene, provider)
    assert final['status'] == 'failed' and final['model_result'] is final['model_result_hash'] is None
    assert len(provider.requests) == 1 and provider.requests[0]['stage'] == 'map-v172'
    assert final['maps'][0]['status'] == 'failed' and all(stage['reserved_call_id'] is None for stage in final['maps'][1:])
    raw = original(scene, final, 'map-1')['content'].encode('utf-8')
    assert raw == provider.responses[0]
    original_ref = provider.values[0]['claims'][0]['span_refs'][0]
    assert original_ref not in preview['map_preparation']['plan']['chunks'][0]['span_refs']
    assert scene['client'].get(revision_path(final['id'])).status_code == 409
    before = storage_files(); assert not process_once(accepted['draft']['job_id'])
    assert reply(scene['client'].get(BASE + '/' + final['id']))['status'] == 'failed'
    assert len(provider.requests) == 1 and storage_files() == before
    assert_older_preserved(older_calls, older_files)
