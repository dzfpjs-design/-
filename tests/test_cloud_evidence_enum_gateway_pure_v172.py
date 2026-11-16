"""V172 central profile/gateway seam with explicit synthetic ledger and HTTP.

The actual native capture writes only pytest tmp_path; provider, credentials,
budget rows and ledger Session are fabricated IO, not paid or PG evidence.
Actual registration/profile, request preparation, schema, capture, envelope and
JSON-schema validators are retained. Root alone executes this file.
The real-sink path covers the capture transport allowlist missed by the earlier
static review and subsequently identified by Root's kernel execution.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
import sqlalchemy.orm

from generic_agents import gateway, cloud_budget, db as database
from generic_agents import report_claim_enum_native_v172 as native
from generic_agents import report_claim_native_v168 as old_native
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents.cloud_evidence_profiles import (
    validate_evidence_output, declared_map_transport, evidence_policy, evidence_options,
)
from generic_agents.contracts import digest
from generic_agents.report_evidence_wire_v3 import MapResultWireV3
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution, CALL, USAGE, MESSAGES, VALUE
from tests.test_report_claim_native_v168 import wire as old_wire


ACTOR = '10000000-0000-4000-8000-000000000002'
WORKSPACE = '10000000-0000-4000-8000-000000000003'
DRAFT = '10000000-0000-4000-8000-000000000004'
KEY = 'SYNTHETIC_NOT_A_PROVIDER_CREDENTIAL'
STAGES = ('map-v172', 'reduce-v172')


def execution():
    return {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'secret_ref': 'DASHSCOPE_API_KEY', 'report_evidence_output': {
            'schema': 'report-evidence-output-v172', 'max_tokens': 32768,
            'map_transport': native.DELIVERY, 'reduce_transport': native.DELIVERY,
            'endpoint': native.ENDPOINT}}


def local_schema(stage):
    return native.map_schema(['w:p1/s1']) if stage == 'map-v172' else native._model(stage).model_json_schema()


def answer(stage):
    return copy.deepcopy(VALUE) if stage == 'map-v172' else old_wire('reduce-v168')


@pytest.fixture
def native_transport(monkeypatch, tmp_path):
    """Mirror existing gateway pure IO fixtures, retaining a real native sink."""
    state = NS(events=[], reservations=[], requests=[], callbacks=[], finished=[],
        call=None, response=None, context=None, expected=None, root=tmp_path / 'capture')
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    monkeypatch.setattr(gateway, 'settings', lambda: NS(
        dashscope_api_key=NS(get_secret_value=lambda: KEY), deepseek_api_key=None))

    def reserve(principal, capability, bound, request_hash):
        assert principal.user_id == ACTOR and principal.workspace_id == WORKSPACE
        assert capability.workspace_id == WORKSPACE
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
        headers = {'x-request-id': 'synthetic-enum-request'}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def iter_raw(self):
            # Native gateway must accumulate original bytes and observe EOF;
            # content is never parsed/repaired by this synthetic transport.
            yield state.response[:13]
            yield state.response[13:]

    class Client:
        def __init__(self, **options):
            assert options['follow_redirects'] is False and options['trust_env'] is False
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
    return state


def invoke(state, stage, *, e=None, schema=None, response=None):
    e = execution() if e is None else e
    schema = local_schema(stage) if schema is None else schema
    value = answer(stage) if response is None else response
    state.response = json.dumps({'usage': USAGE, 'choices': [{'finish_reason': 'stop',
        'message': {'role': 'assistant', 'content': json.dumps(value, ensure_ascii=False)}}]},
        ensure_ascii=False, allow_nan=False).encode('utf-8')
    cap = NS(id='10000000-0000-4000-8000-000000000005', workspace_id=WORKSPACE,
        data={'execution': e, 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    principal = NS(user_id=ACTOR, workspace_id=WORKSPACE)

    def reserved(identity):
        assert identity == CALL and state.events[-1] == 'ledger-commit'
        prepared, _, images = gateway.prepare_cloud_json(cap, MESSAGES, schema)
        assert images == 0
        actual = native.prepare_request(prepared, schema,
            evidence_options(e, gateway.cloud_json_options(e), stage), stage)
        policy = evidence_policy(e, stage)
        binding = {'schema': 'report-evidence-journal-binding-v2' if stage == 'reduce-v172'
                else 'report-evidence-journal-binding-v1',
            'actor_id': ACTOR, 'workspace_id': WORKSPACE, 'draft_id': DRAFT,
            'stage': 'reduce' if stage == 'reduce-v172' else 'map-1', 'reserved_call_id': identity,
            'source_hash': 'a' * 64, 'catalog_hash': 'b' * 64,
            'generation_hash': digest({'execution': e, 'schema': schema}),
            'dispatch_hash': digest({'stage': stage, 'request_hash': actual['body_sha256']}),
            'messages_hash': digest(actual['request']['messages'])}
        state.expected = actual
        state.context = {'binding': binding, 'request_hash': actual['body_sha256'],
            'endpoint': actual['endpoint'], 'delivery': native.DELIVERY,
            'schema_proof_hash': digest(actual['schema_proof']), 'profile_hash': digest(policy),
            'token_bound': len(actual['body']) + actual['request']['max_tokens'] + 1024,
            'max_bytes': native.stage_policy(stage)['max_response_bytes']}
        state.events.append('on-reserved')
        return capture.prepare(state.root, state.context)

    def received(original, receipt):
        assert original.encode('utf-8') == state.response
        # A real, complete tmp capture must already exist when the response is
        # handed off, before semantic parsing and final ledger settlement.
        saved = capture.load(state.root, state.context)
        assert saved['ready'] is True and saved['response_complete'] is True
        assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
        state.events.append('callback'); state.callbacks.append((original, copy.deepcopy(receipt)))

    return gateway.cloud_json(cap, MESSAGES, schema, principal, full_report=True,
        evidence_stage=stage, on_reserved=reserved, on_response=received)


@pytest.mark.parametrize('stage', STAGES)
def test_new_registration_prepares_and_dispatches_actual_body_hash_schema_and_full_output(native_transport, stage):
    e = execution(); before = copy.deepcopy(e)
    assert validate_evidence_output(e) is e
    assert declared_map_transport(e) == native.DELIVERY
    base = gateway.cloud_json_options(e)
    assert base['max_tokens'] == 8192 and base['response_format'] == {'type': 'json_object'}
    options = evidence_options(e, base, stage)
    assert options == {'model': native.MODEL, 'max_tokens': 32768,
        'enable_thinking': False, 'stream': False, 'n': 1}
    result, receipt = invoke(native_transport, stage)
    assert result == answer(stage) and receipt['delivery'] == native.DELIVERY
    assert native_transport.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'finish']
    assert len(native_transport.reservations) == len(native_transport.requests) == len(native_transport.callbacks) == 1
    body = native_transport.requests[0]['content']; actual = native_transport.expected
    assert body == actual['body'] and hashlib.sha256(body).hexdigest() == native_transport.reservations[0][1]
    request = json.loads(body)
    assert request['response_format']['type'] == 'json_schema'
    assert request['response_format']['json_schema']['schema'] == local_schema(stage)
    assert request['max_tokens'] == 32768
    assert native_transport.reservations[0][0] == len(body) + 32768 + 1024
    assert native_transport.call.data['schema_proof_hash'] == digest(actual['schema_proof'])
    assert native_transport.call.data['dispatch_options_hash'] == digest({k: v for k, v in request.items() if k != 'messages'})
    assert native_transport.call.data['input_policy_hash'] == digest({'single_batch': True, **evidence_policy(e, stage)})
    assert native_transport.finished[0][1] == 'succeeded' and e == before


@pytest.mark.parametrize('fault', ['model', 'endpoint', 'transport', 'generic_overflow',
    'evidence_overflow', 'json_response_format'])
def test_new_registration_errors_reject_before_reservation_or_transport(native_transport, fault):
    e = execution(); nested = e['report_evidence_output']
    if fault == 'model': e['model'] = 'qwen3.8-flash'
    elif fault == 'endpoint': nested['endpoint'] = 'https://example.invalid/chat/completions'
    elif fault == 'transport': nested['map_transport'] = old_native.DELIVERY
    elif fault == 'generic_overflow': e['max_tokens'] = 8193
    elif fault == 'evidence_overflow': nested['max_tokens'] = 32769
    else: nested['response_format'] = {'type': 'json_object'}
    with pytest.raises(ValueError): validate_evidence_output(e)
    with pytest.raises(ValueError): declared_map_transport(e)
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, 'map-v172', e=e)
    assert native_transport.events == native_transport.reservations == native_transport.requests == []


@pytest.mark.parametrize('stage', ['map-v168', 'reduce-v168'])
def test_new_manifest_cannot_be_dispatched_using_old_stages(native_transport, stage):
    with pytest.raises(ValueError): evidence_policy(execution(), stage)
    # Schema is provided explicitly so the new test's helper does not choose a
    # new-family model on behalf of this intentionally wrong dispatch stage.
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, stage, schema=native.map_schema(['w:p1/s1']))
    assert native_transport.events == []


@pytest.mark.parametrize('fault', ['static_base', 'remove_enum', 'modify_base', 'json_object_dispatch'])
def test_actual_native_preparation_rejects_base_or_json_mode_substitution_before_sending(native_transport, fault):
    schema = native.map_schema(['w:p1/s1'])
    if fault == 'json_object_dispatch':
        # Generic json_object is valid input to evidence_options; it must not
        # survive as an extra/native dispatch option or bypass strict schema.
        options = evidence_options(execution(), gateway.cloud_json_options(execution()), 'map-v172')
        options['response_format'] = {'type': 'json_object'}
        with pytest.raises(ValueError): native.strict_options(schema, options, 'map-v172')
    else:
        if fault == 'static_base': schema = MapResultWireV3.model_json_schema()
        elif fault == 'remove_enum': schema['$defs']['MapClaimWireV3']['properties']['span_refs']['items'].pop('enum')
        else: schema['properties']['claims']['maxItems'] = 81
        with pytest.raises(gateway.ModelBlocked): invoke(native_transport, 'map-v172', schema=schema)
    assert native_transport.events == native_transport.reservations == native_transport.requests == []


def test_capture_accepts_new_exact_context_and_rejects_unknown_delivery_or_endpoint(native_transport):
    invoke(native_transport, 'map-v172')
    ctx = native_transport.context
    assert capture._context(ctx) == ctx
    for field, value in (('delivery', 'qwen-public-claim-enum-native-v999'),
            ('endpoint', 'https://example.invalid/chat/completions')):
        changed = copy.deepcopy(ctx); changed[field] = value
        with pytest.raises(ValueError): capture._context(changed)
    assert len(native_transport.requests) == 1


def test_old_manifest_remains_old_and_never_accepts_a_new_stage():
    old = execution(); old['report_evidence_output'].update(schema='report-evidence-output-v168',
        map_transport=old_native.DELIVERY, reduce_transport=old_native.DELIVERY)
    before = copy.deepcopy(old)
    assert validate_evidence_output(old) is old and declared_map_transport(old) == old_native.DELIVERY
    for stage in ('map-v168', 'reduce-v168'):
        policy = evidence_policy(old, stage)
        assert policy['max_output_tokens'] == 32768
        assert evidence_options(old, gateway.cloud_json_options(old), stage)['max_tokens'] == 32768
    for stage in STAGES:
        with pytest.raises(ValueError): evidence_policy(old, stage)
    assert old == before
