"""Real new native/gateway/capture validators, synthetic HTTP and ledger IO.

No live provider, credentials, IAM authority or successful paid row is authored.
The synthetic source fixture remains explicitly untrusted server input; Root
owns normal source/IAM/budget integration and target-runtime verification.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import gateway
from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_business_catalogue_allocation_wire_v213 as wire
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import setup_understanding as understanding
from generic_agents.cloud_evidence_profiles import (
    validate_evidence_output, declared_map_transport, evidence_policy, evidence_options)
from generic_agents.contracts import digest
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution, CALL
from tests import test_saved_map_design_gateway_pure_v176 as transport_fixture
from tests.test_saved_map_design_gateway_pure_v176 import native_transport as base_native_transport, capability, ACTOR, WORKSPACE, DRAFT
from tests.test_report_business_catalogue_allocation_native_v213 import scene, candidate, response
from tests.test_report_business_catalogue_gateway_v211 import invoke as old_invoke


def execution():
    return {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'secret_ref': 'DASHSCOPE_API_KEY', 'report_evidence_output': {'schema': native.OUTPUT_DECLARATION,
            'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


@pytest.fixture
def native_transport(base_native_transport, monkeypatch):
    # Keep the transport's explicit synthetic principal checks, using this new
    # protocol fixture's ACTUAL source actor/workspace rather than another
    # fixture's identities. No gate is bypassed and no evidence is rewritten.
    _cap, _source, _kwargs, evidence = scene()
    reference = evidence['plan']['base_draft_ref']
    monkeypatch.setattr(transport_fixture, 'ACTOR', reference['actor_id'])
    monkeypatch.setattr(transport_fixture, 'WORKSPACE', reference['workspace_id'])
    base_native_transport.actor = reference['actor_id']
    base_native_transport.workspace = reference['workspace_id']
    return base_native_transport


def invoke(state, *, evidence_mode='valid', stage=native.STAGE, full_report=False,
        alter=None, mutate_context=None, alter_response=None, principal=None, e=None,
        dispatch_mode='valid', callbacks=True, reject_current_source=False):
    _cap, _source, _kwargs, evidence = scene()
    schema = wire.output_schema(evidence['plan'], evidence['batch_ordinal'])
    messages = [{'role': 'system', 'content': json.dumps(native.task_envelope(), ensure_ascii=False)},
        {'role': 'user', 'content': json.dumps(native.payload_for(evidence), ensure_ascii=False)}]
    value = candidate(evidence)
    if alter_response is not None:
        alter_response(value)
    state.response = response(value)
    configured = execution() if e is None else e
    cap = capability(configured)
    principal = NS(user_id=state.actor, workspace_id=state.workspace) if principal is None else principal
    if alter is not None:
        alter(evidence, messages, schema)

    def reserved(identity):
        assert identity == CALL and state.events[-1] == 'ledger-commit'
        assert not state.requests, 'the current-source callback must precede any provider HTTP'
        if reject_current_source:
            raise gateway.ModelBlocked('Public synthetic current-source callback refused dispatch')
        prepared, _, images = gateway.prepare_cloud_json(cap, messages, schema)
        assert images == 0
        bound = native.request_inputs(prepared, allocation_evidence=evidence)
        actual = native.prepare_request(prepared, schema,
            evidence_options(configured, gateway.cloud_json_options(configured), native.STAGE),
            native.STAGE, **bound, allocation_evidence=evidence)
        policy = evidence_policy(configured, native.STAGE)
        source = evidence['source_context']
        binding = {'schema': 'report-evidence-journal-binding-v2', 'actor_id': state.actor,
            'workspace_id': state.workspace, 'draft_id': DRAFT, 'stage': 'reduce', 'reserved_call_id': identity,
            'source_hash': source['snapshot']['source_hash'], 'catalog_hash': source['snapshot']['catalog_hash'],
            'generation_hash': digest({'execution': configured, 'schema': schema}),
            'dispatch_hash': digest({'stage': native.STAGE, 'request_hash': actual['body_sha256']}),
            'messages_hash': digest(actual['request']['messages'])}
        state.expected = actual
        state.context = {'binding': binding, 'request_hash': actual['body_sha256'],
            'endpoint': actual['endpoint'], 'delivery': native.DELIVERY,
            'schema_proof_hash': digest(actual['schema_proof']), 'profile_hash': digest(policy),
            'token_bound': len(actual['body']) + actual['request']['max_tokens'] + 1024,
            'max_bytes': native.stage_policy(native.STAGE)['max_response_bytes']}
        if mutate_context is not None:
            mutate_context(state.context)
        state.events.append('on-reserved')
        return capture.prepare(state.root, state.context)

    def received(original, receipt):
        assert original.encode('utf-8') == state.response
        saved = capture.load(state.root, state.context)
        assert saved['ready'] and saved['response_complete']
        assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
        state.callbacks.append((original, copy.deepcopy(receipt)))

    extra = {} if evidence_mode == 'omitted' else {'allocation_evidence':
        None if evidence_mode == 'none' else {} if evidence_mode == 'empty' else evidence}
    if dispatch_mode != 'omitted':
        extra['dispatch_identity'] = None if dispatch_mode == 'none' else {
            'suggestion_id': evidence['draft_ref']['id'], 'job_id': 'public-synthetic-allocation-job',
            'task_id': native.TASK_ID if dispatch_mode == 'envelope-id' else 'report-business-catalogue-allocation-v213',
            'request_hash': digest({'plan_hash': evidence['plan']['plan_hash'], 'note': evidence['note']})}
    result = gateway.cloud_json(cap, messages, schema, principal, full_report=full_report,
        evidence_stage=stage, on_reserved=reserved if callbacks else None,
        on_response=received if callbacks else None, **extra)
    return result, evidence


def test_new_allocation_has_one_real_synthetic_http_and_distinct_projection_without_server_inventory(native_transport):
    (projection, receipt), evidence = invoke(native_transport)
    assert len(native_transport.requests) == len(native_transport.reservations) == len(native_transport.callbacks) == 1
    request = native_transport.expected['request']
    encoded = json.dumps(request, ensure_ascii=False)
    assert 'allocation_evidence' not in request and 'evidence_catalogue' not in request
    sent = json.loads(request['messages'][-1]['content'])
    assert sent == native.payload_for(evidence)
    assert 'source_context' not in sent and 'draft_origin' not in sent
    assert not any(identifier in encoded for identifier in evidence['plan']['source']['input_order'])
    assert projection['schema'] == wire.PROJECTION and projection['raw_wire']['schema'] == wire.WIRE
    assert projection['flags']['can_apply'] is projection['flags']['human_confirmed'] is False
    assert receipt['delivery'] == native.DELIVERY
    assert receipt['response_body_sha256'] == hashlib.sha256(native_transport.response).hexdigest()
    assert native_transport.finished[-1][1] == 'succeeded'


@pytest.mark.parametrize('mode', ['omitted', 'none', 'empty'])
def test_missing_none_or_wrong_source_is_blocked_before_budget_reservation(native_transport, mode):
    with pytest.raises((gateway.ModelBlocked, ValueError)):
        invoke(native_transport, evidence_mode=mode)
    assert not native_transport.reservations and not native_transport.requests


@pytest.mark.parametrize('mode', ['omitted', 'none', 'envelope-id'])
def test_new_stage_requires_independent_tracked_consumer_identity_not_the_task_envelope(native_transport, mode):
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, dispatch_mode=mode)
    assert not native_transport.reservations and not native_transport.requests


def test_source_callback_is_required_before_reservation_and_http(native_transport):
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, callbacks=False)
    assert not native_transport.reservations and not native_transport.requests


def test_current_source_callback_rejection_preserves_real_reservation_without_any_http(native_transport):
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, reject_current_source=True)
    assert len(native_transport.reservations) == 1 and not native_transport.requests
    assert native_transport.finished[-1][1] == 'failed' and not native_transport.callbacks


@pytest.mark.parametrize('scope', [True, 1, None])
def test_new_stage_never_claims_full_report_even_for_truthy_one(native_transport, scope):
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, full_report=scope)
    assert not native_transport.reservations and not native_transport.requests


@pytest.mark.parametrize('stage', [None, 'catalogue-v211', 'reduce-v179', 'map-v172'])
def test_old_stage_rejects_even_explicit_none_allocation_evidence_before_any_work(native_transport, stage):
    with pytest.raises(gateway.ModelBlocked):
        gateway.cloud_json(capability(execution()), [], {}, NS(user_id=ACTOR, workspace_id=WORKSPACE),
            evidence_stage=stage, full_report=True, allocation_evidence=None)
    assert not native_transport.reservations and not native_transport.requests


@pytest.mark.parametrize('fault', ['original_id', 'payload', 'schema', 'namespace'])
def test_source_payload_schema_or_original_id_mismatch_never_reserves(native_transport, fault):
    def altered(evidence, messages, schema):
        if fault == 'original_id':
            evidence['plan']['source']['input_order'][0] = 'borrowed-map-id'
        elif fault == 'payload':
            user = json.loads(messages[-1]['content'])
            user['disclosure']['claims'].pop()
            messages[-1]['content'] = json.dumps(user, ensure_ascii=False)
        elif fault == 'schema':
            schema['additionalProperties'] = True
        else:
            evidence['source_context']['snapshot']['actor_id'] = '00000000-0000-4000-8000-000000000099'
    with pytest.raises((gateway.ModelBlocked, ValueError)):
        invoke(native_transport, alter=altered)
    assert not native_transport.reservations and not native_transport.requests


def test_valid_evidence_from_another_principal_is_not_a_new_call_grant(native_transport):
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, principal=NS(user_id='another-account', workspace_id=native_transport.workspace))
    assert not native_transport.reservations and not native_transport.requests


def test_new_stage_cannot_borrow_old_evidence_catalogue_kw(native_transport):
    with pytest.raises(gateway.ModelBlocked):
        gateway.cloud_json(capability(execution()), [], {}, NS(user_id=ACTOR, workspace_id=WORKSPACE),
            evidence_stage=native.STAGE, allocation_evidence={}, evidence_catalogue=None)
    assert not native_transport.reservations and not native_transport.requests


def test_invalid_completed_same_call_is_durable_failed_without_partial_or_old_decoder(native_transport):
    with pytest.raises((gateway.ModelBlocked, ValueError)):
        invoke(native_transport, alter_response=lambda value: value['decisions'].clear())
    assert len(native_transport.requests) == len(native_transport.reservations) == len(native_transport.callbacks) == 1
    assert native_transport.finished[-1][1] == 'failed'
    assert capture.load(native_transport.root, native_transport.context)['ready'] is True


@pytest.mark.parametrize('fault', ['endpoint', 'delivery', 'stage', 'profile'])
def test_new_capture_identity_must_match_before_the_only_provider_post(native_transport, fault):
    def altered(context):
        if fault == 'endpoint':
            context['endpoint'] = 'https://wrong.invalid/chat/completions'
        elif fault == 'delivery':
            context['delivery'] = 'qwen-public-business-catalogue-native-v211'
        elif fault == 'stage':
            context['binding']['stage'] = 'map-1'
        else:
            context['profile_hash'] = 'f' * 64
    with pytest.raises((gateway.ModelBlocked, ValueError)):
        invoke(native_transport, mutate_context=altered)
    assert len(native_transport.reservations) == 1 and not native_transport.requests
    assert native_transport.finished[-1][1] == 'failed'


@pytest.mark.parametrize('field,value', [('provider', 'deepseek'), ('model', 'qwen-plus'),
    ('secret_ref', 'QWEN_TOKENPLAN_API_KEY')])
def test_new_profile_cannot_borrow_another_provider_model_or_secret_reference(native_transport, field, value):
    changed = execution(); changed[field] = value
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, e=changed)
    assert not native_transport.reservations and not native_transport.requests


def test_original_capture_read_has_no_http_and_cannot_relabel_stage_or_provider(native_transport):
    (_projection, expected), _evidence = invoke(native_transport)
    policy = evidence_policy(execution(), native.STAGE)
    count = len(native_transport.requests)
    body, receipt, _ = bridge.response_context(native_transport.context, 'qwen', native.MODEL, policy, native.STAGE)
    assert body.encode('utf-8') == native_transport.response and receipt == expected
    for stage in ('catalogue-v211', 'reduce-v179', None):
        with pytest.raises(ValueError):
            bridge.response_context(native_transport.context, 'qwen', native.MODEL, policy, stage)
    with pytest.raises(ValueError):
        bridge.response_context(native_transport.context, 'deepseek', native.MODEL, policy, native.STAGE)
    changed = copy.deepcopy(policy); changed['full_report'] = True
    with pytest.raises(ValueError):
        bridge.response_context(native_transport.context, 'qwen', native.MODEL, changed, native.STAGE)
    assert len(native_transport.requests) == count


def test_old_catalogue_capture_remains_readable_and_cannot_be_promoted_to_new_allocation(base_native_transport):
    old_invoke(base_native_transport)
    with pytest.raises(ValueError):
        bridge.response_context(base_native_transport.context, 'qwen', native.MODEL,
            evidence_policy(execution(), native.STAGE), native.STAGE)
    assert len(base_native_transport.requests) == 1


def test_registration_is_exact_new_profile_and_not_an_old_map_or_full_report_manifest():
    value = execution()
    validate_evidence_output(value)
    assert evidence_policy(value, native.STAGE) == native.input_policy(value)
    assert evidence_policy(value, native.STAGE)['full_report'] is False
    with pytest.raises(ValueError):
        declared_map_transport(value)
    for bad in ('catalogue-v211', 'map-v172', 'reduce-v179'):
        with pytest.raises(ValueError):
            evidence_policy(value, bad)


def test_model_discovery_only_exposes_the_exact_current_valid_new_declaration(monkeypatch):
    good = capability(execution()); good.name = '独立小批分类'; good.release_version = 1; good.status = 'published'
    wrong = copy.deepcopy(good); wrong.id = 'bad-cap'; wrong.data['execution']['report_evidence_output']['endpoint'] = 'https://wrong.invalid/'
    denied = copy.deepcopy(good); denied.id = 'denied-cap'
    def current_model(db, p, identity):
        if identity == denied.id:
            raise HTTPException(403, 'Denied synthetic cap')
        return good
    monkeypatch.setattr(understanding, 'model', current_model)
    class ReadOnlyCapabilities:
        def scalars(self, statement):
            return NS(all=lambda: [good, wrong, denied])
    rows = understanding.available_models(NS(workspace_id=WORKSPACE), ReadOnlyCapabilities())
    assert len(rows) == 1 and rows[0]['generation_profile'] == native.STAGE
    assert rows[0]['id'] == good.id and 'secret_ref' not in json.dumps(rows)
