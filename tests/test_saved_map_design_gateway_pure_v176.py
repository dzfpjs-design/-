"""V176 central seams with synthetic HTTP/ledger and real tmp-path capture.

No provider, credentials, source authority, price grant or Postgres is exercised.
Actual profile, request, schema proof, capture, envelope and bridge validators
remain intact. Root alone runs this public synthetic test file.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import httpx
import pytest
import sqlalchemy.orm

from generic_agents import gateway, cloud_budget, db as database
from generic_agents import report_design_grouped_native_v176 as native
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.cloud_evidence_profiles import (
    validate_evidence_output, declared_map_transport, evidence_policy, evidence_options,
)
from generic_agents.cloud_input_profiles import input_policy, measure_request_body
from generic_agents.contracts import Manifest, digest
from generic_agents.services import validate
from generic_agents.report_design_grouped_wire_v174 import DesignGroupedWireV174
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution, CALL, USAGE, MESSAGES
from tests.test_report_design_grouped_wire_v174 import scene


ACTOR = '10000000-0000-4000-8000-000000000002'
WORKSPACE = '10000000-0000-4000-8000-000000000003'
DRAFT = '10000000-0000-4000-8000-000000000004'
KEY = 'SYNTHETIC_NOT_A_PROVIDER_CREDENTIAL'


def execution():
    return {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'secret_ref': 'DASHSCOPE_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 32768,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


def capability(e=None):
    return NS(id='10000000-0000-4000-8000-000000000005', workspace_id=WORKSPACE,
        data={'execution': execution() if e is None else e,
            'data_policy': {'cloud_allowed': True, 'redact_fields': []}})


@pytest.fixture
def native_transport(monkeypatch, tmp_path):
    state = NS(events=[], reservations=[], requests=[], callbacks=[], finished=[],
        call=None, response=None, context=None, expected=None, interrupted=False,
        root=tmp_path / 'capture')
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    monkeypatch.setattr(gateway, 'settings', lambda: NS(
        dashscope_api_key=NS(get_secret_value=lambda: KEY), deepseek_api_key=None))

    def reserve(principal, cap, bound, request_hash):
        assert principal.user_id == ACTOR and principal.workspace_id == WORKSPACE
        assert cap.workspace_id == WORKSPACE
        state.events.append('reserve'); state.reservations.append((bound, request_hash))
        state.call = NS(status='reserved', reserved_tokens=bound,
            data={'actor_id': ACTOR, 'request_hash': request_hash})
        return CALL

    def finish(identity, receipt=None, status='failed'):
        assert identity == CALL
        state.events.append('finish'); state.finished.append((copy.deepcopy(receipt), status))
        state.call.status = status

    monkeypatch.setattr(cloud_budget, 'reserve', reserve)
    monkeypatch.setattr(cloud_budget, 'finish', finish)
    marker = object(); monkeypatch.setattr(database, 'engine', lambda: marker)

    class LedgerSession:
        def __init__(self, connection): assert connection is marker
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def scalar(self, query): return state.call
        def commit(self): state.events.append('ledger-commit')

    monkeypatch.setattr(sqlalchemy.orm, 'Session', LedgerSession)

    class Response:
        status_code = 200
        headers = {'x-request-id': 'synthetic-saved-map-design-request'}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def iter_raw(self):
            yield state.response[:17]
            if state.interrupted:
                raise httpx.ReadError('synthetic interrupted EOF')
            yield state.response[17:]

    class Client:
        def __init__(self, **options):
            assert options == {'timeout': 600, 'follow_redirects': False, 'trust_env': False}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def stream(self, method, endpoint, **options):
            assert method == 'POST' and endpoint == native.ENDPOINT
            assert options['headers'] == {'Authorization': 'Bearer ' + KEY,
                'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
            assert type(options['content']) is bytes
            state.events.append('http'); state.requests.append(copy.deepcopy(options))
            return Response()

    monkeypatch.setattr(gateway.httpx, 'Client', Client)
    monkeypatch.setattr(bridge, 'root', lambda: state.root)
    return state


def invoke(state, *, e=None, stage=native.STAGE, schema=None, full_report=True,
           callbacks=True, context_change=None):
    e = execution() if e is None else e
    schema = DesignGroupedWireV174.model_json_schema() if schema is None else schema
    value = scene()[0]
    state.response = json.dumps({'usage': USAGE, 'choices': [{'finish_reason': 'stop',
        'message': {'role': 'assistant', 'content': json.dumps(value, ensure_ascii=False)}}]},
        ensure_ascii=False, allow_nan=False).encode('utf-8')
    cap = capability(e); principal = NS(user_id=ACTOR, workspace_id=WORKSPACE)

    def reserved(identity):
        assert identity == CALL and state.events[-1] == 'ledger-commit'
        messages, _, images = gateway.prepare_cloud_json(cap, MESSAGES, schema)
        assert images == 0
        actual = native.prepare_request(messages, schema,
            evidence_options(e, gateway.cloud_json_options(e), stage), stage)
        policy = evidence_policy(e, stage)
        binding = {'schema': 'report-evidence-journal-binding-v2', 'actor_id': ACTOR,
            'workspace_id': WORKSPACE, 'draft_id': DRAFT, 'stage': 'reduce',
            'reserved_call_id': identity, 'source_hash': 'a' * 64, 'catalog_hash': 'b' * 64,
            'generation_hash': digest({'execution': e, 'schema': schema}),
            'dispatch_hash': digest({'stage': stage, 'request_hash': actual['body_sha256']}),
            'messages_hash': digest(actual['request']['messages'])}
        state.expected = actual
        state.context = {'binding': binding, 'request_hash': actual['body_sha256'],
            'endpoint': actual['endpoint'], 'delivery': native.DELIVERY,
            'schema_proof_hash': digest(actual['schema_proof']), 'profile_hash': digest(policy),
            'token_bound': len(actual['body']) + actual['request']['max_tokens'] + 1024,
            'max_bytes': native.stage_policy(stage)['max_response_bytes']}
        if context_change:
            context_change(state.context)
        state.events.append('on-reserved')
        return capture.prepare(state.root, state.context)

    def received(original, receipt):
        assert original.encode('utf-8') == state.response
        saved = capture.load(state.root, state.context)
        assert saved['ready'] is True and saved['response_complete'] is True
        assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
        state.events.append('callback'); state.callbacks.append((original, copy.deepcopy(receipt)))

    return gateway.cloud_json(cap, MESSAGES, schema, principal, full_report=full_report,
        evidence_stage=stage, on_reserved=reserved if callbacks else None,
        on_response=received if callbacks else None)


def test_registration_and_actual_dispatch_keep_generic_limit_and_freeze_full_native_body(native_transport):
    e = execution(); before = copy.deepcopy(e)
    manifest = Manifest(kind='foundation_model', namespace='public_synthetic',
        name='saved_map_design', version='1.0.0', owner='Synthetic public fixture',
        description='Pure contract fixture, not registered source or execution authority',
        execution=e, allowed_agent_roles=['business'], data_policy={'cloud_allowed': True})
    assert manifest.execution == e
    validate(DesignGroupedWireV174.model_json_schema(), scene()[0])
    assert validate_evidence_output(e) is e
    assert input_policy(e, full_report=True)['max_input_bytes'] == 50000
    assert input_policy(e, full_report=True)['max_output_tokens'] == 8192
    with pytest.raises(ValueError): declared_map_transport(e)
    result, receipt = invoke(native_transport)
    assert result == scene()[0] and receipt['delivery'] == native.DELIVERY
    assert native_transport.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'finish']
    assert len(native_transport.reservations) == len(native_transport.requests) == len(native_transport.callbacks) == 1
    body = native_transport.requests[0]['content']; request = json.loads(body)
    assert body == native_transport.expected['body']
    assert native_transport.reservations[0] == (len(body) + 32768 + 1024, hashlib.sha256(body).hexdigest())
    assert measure_request_body(body, evidence_policy(e, native.STAGE), 32768)['token_bound'] == native_transport.reservations[0][0]
    assert request['model'] == native.MODEL and request['max_tokens'] == 32768
    assert request['response_format']['type'] == 'json_schema'
    assert request['response_format']['json_schema']['schema'] == DesignGroupedWireV174.model_json_schema()
    assert 'reconciliation_groups' in request['response_format']['json_schema']['schema']['properties']
    assert native_transport.call.data['schema_proof_hash'] == digest(native_transport.expected['schema_proof'])
    assert native_transport.call.data['input_policy_hash'] == digest({'single_batch': True, **native.input_policy(e)})
    assert native_transport.call.data['dispatch_options_hash'] == digest({k: v for k, v in request.items() if k != 'messages'})
    assert receipt['response_body_sha256'] == hashlib.sha256(native_transport.response).hexdigest()
    assert receipt['arguments_sha256'] != receipt['response_body_sha256']
    assert native_transport.finished[0][1] == 'succeeded' and e == before


@pytest.mark.parametrize('fault', ['model', 'provider', 'endpoint', 'transport', 'extra_map',
    'generic_overflow', 'evidence_overflow', 'bool_output', 'json_mode', 'secret'])
def test_invalid_registration_or_secret_never_reserves_or_sends(native_transport, fault):
    e = execution(); d = e['report_evidence_output']
    if fault == 'model': e['model'] = 'qwen3.8-flash'
    elif fault == 'provider': e['provider'] = 'deepseek'; e['secret_ref'] = 'DEEPSEEK_API_KEY'
    elif fault == 'endpoint': d['endpoint'] = native.ENDPOINT + '/other'
    elif fault == 'transport': d['reduce_transport'] = 'qwen-public-claim-enum-native-v172'
    elif fault == 'extra_map': d['map_transport'] = native.DELIVERY
    elif fault == 'generic_overflow': e['max_tokens'] = 8193
    elif fault == 'evidence_overflow': d['max_tokens'] = 32769
    elif fault == 'bool_output': d['max_tokens'] = True
    elif fault == 'json_mode': d['response_format'] = {'type': 'json_object'}
    else: e['secret_ref'] = 'QWEN_TOKENPLAN_API_KEY'
    if fault != 'secret':
        with pytest.raises(ValueError): validate_evidence_output(e)
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, e=e)
    assert native_transport.events == native_transport.requests == native_transport.reservations == []


@pytest.mark.parametrize('stage', [None, 'map-v176', 'map-v172', 'reduce-v172', 'map-v168', 'reduce-v168', 'reduce-v2'])
def test_new_profile_has_no_generic_old_family_or_map_fallback(native_transport, stage):
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, stage=stage)
    assert native_transport.events == []


@pytest.mark.parametrize('fault', ['not_full', 'no_callbacks', 'changed_schema'])
def test_actual_request_gates_precede_reservation(native_transport, fault):
    kwargs = {'full_report': False} if fault == 'not_full' else {'callbacks': False}
    if fault == 'changed_schema':
        schema = DesignGroupedWireV174.model_json_schema(); schema['title'] = 'Retagged old schema'
        kwargs = {'schema': schema}
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, **kwargs)
    assert native_transport.events == []


@pytest.mark.parametrize('field', ['request_hash', 'schema_proof_hash', 'profile_hash', 'token_bound'])
def test_sink_must_match_actual_prepared_body_before_http(native_transport, field):
    def mutate(ctx): ctx[field] = ctx[field] + 1 if field == 'token_bound' else 'c' * 64
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, context_change=mutate)
    assert len(native_transport.reservations) == 1 and native_transport.requests == []


def test_complete_same_call_bridge_reads_original_http_without_second_transport(native_transport):
    _, original_receipt = invoke(native_transport)
    wire, receipt, state = bridge.response_context(native_transport.context,
        'qwen', native.MODEL, native.input_policy(execution()), native.STAGE)
    assert wire.encode('utf-8') == native_transport.response and receipt == original_receipt
    assert state['ready'] and len(native_transport.requests) == 1
    for provider, model, stage in [('deepseek', native.MODEL, native.STAGE),
            ('qwen', 'qwen3.8-flash', native.STAGE), ('qwen', native.MODEL, 'reduce-v172')]:
        with pytest.raises(ValueError):
            bridge.response_context(native_transport.context, provider, model,
                native.input_policy(execution()), stage)
    assert len(native_transport.requests) == 1


def test_capture_context_is_only_new_reduce_exact_endpoint_and_limit(native_transport):
    invoke(native_transport); ctx = native_transport.context
    assert capture._context(ctx) == ctx
    wrong_map = copy.deepcopy(ctx)
    wrong_map['binding'].update(schema='report-evidence-journal-binding-v1', stage='map-1')
    wrong_map['max_bytes'] = capture.MAP_LIMIT
    for value in [wrong_map, {**ctx, 'endpoint': native.ENDPOINT + '/other'},
            {**ctx, 'delivery': native.DELIVERY + '-unknown'}, {**ctx, 'max_bytes': capture.MAP_LIMIT}]:
        with pytest.raises(ValueError): capture._context(value)


def test_partial_native_response_preserves_original_call_and_never_reposts(native_transport):
    native_transport.interrupted = True
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport)
    saved = capture.load(native_transport.root, native_transport.context)
    assert saved['ready'] is False and saved['response_complete'] is False
    assert saved['body_bytes'] == 17 and native_transport.callbacks == []
    with pytest.raises(ValueError):
        bridge.response_context(native_transport.context, 'qwen', native.MODEL,
            native.input_policy(execution()), native.STAGE)
    assert len(native_transport.reservations) == len(native_transport.requests) == 1


@pytest.mark.parametrize('version', ['v168', 'v172'])
def test_previous_profiles_remain_previous_and_reject_only_new_reduce(version):
    e = execution(); old = 'qwen-public-claim-native-v168' if version == 'v168' else 'qwen-public-claim-enum-native-v172'
    e['report_evidence_output'] = {'schema': 'report-evidence-output-' + version,
        'max_tokens': 32768, 'map_transport': old, 'reduce_transport': old, 'endpoint': native.ENDPOINT}
    before = copy.deepcopy(e)
    assert validate_evidence_output(e) is e and declared_map_transport(e) == old
    for stage in ('map-' + version, 'reduce-' + version):
        policy = evidence_policy(e, stage)
        assert policy['max_output_tokens'] == 32768
        assert evidence_options(e, gateway.cloud_json_options(e), stage)['max_tokens'] == 32768
    with pytest.raises(ValueError): evidence_policy(e, native.STAGE)
    assert e == before
