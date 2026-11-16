"""Explicit dialect seams with public synthetic source/transport only.

These tests cannot establish real IAM, accepted source, provider quality or
paid-call success. The original native/strict-wire/capture validators remain
real; only the established public HTTP/ledger fixture supplies transport IO.
Root runs this file and separately verifies immutable real request goldens.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import gateway, services
from generic_agents import cloud_evidence_profiles as profiles
from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_business_catalogue_allocation_wire_v213 as wire
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import setup_understanding as understanding
from generic_agents.contracts import Manifest, digest
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution, CALL
from tests.test_saved_map_design_gateway_pure_v176 import capability, DRAFT
from tests.test_saved_map_design_gateway_pure_v176 import native_transport as base_native_transport
from tests.test_report_business_catalogue_allocation_gateway_v213 import (
    native_transport, execution, invoke)
from tests.test_report_business_catalogue_allocation_native_v213 import scene, candidate, response


VARIANT = 'qwen-json-schema-subset-20261027'


def configured():
    return {**execution(), 'native_schema_variant': VARIANT}


def _invoke_registered(state, *, opt_in=True):
    """Expected request is rebuilt independently of the actual gateway path."""
    _cap, _source, _kwargs, evidence = scene()
    selected = configured() if opt_in else execution()
    cap = capability(selected)
    schema = wire.output_schema(evidence['plan'], evidence['batch_ordinal'])
    messages = [{'role': 'system', 'content': json.dumps(native.task_envelope(), ensure_ascii=False)},
        {'role': 'user', 'content': json.dumps(native.payload_for(evidence), ensure_ascii=False)}]
    prepared, _, images = gateway.prepare_cloud_json(cap, messages, schema)
    assert images == 0
    inputs = native.request_inputs(prepared, allocation_evidence=evidence)
    extra = {'native_schema_variant': VARIANT} if opt_in else {}
    expected = native.prepare_request(prepared, schema,
        profiles.evidence_options(selected, gateway.cloud_json_options(selected), native.STAGE),
        native.STAGE, **inputs, allocation_evidence=evidence, **extra)
    policy = profiles.evidence_policy(selected, native.STAGE)
    state.response = response(candidate(evidence))

    def reserved(identity):
        assert identity == CALL and not state.requests
        source = evidence['source_context']['snapshot']
        binding = {'schema': 'report-evidence-journal-binding-v2', 'actor_id': state.actor,
            'workspace_id': state.workspace, 'draft_id': DRAFT,
            'stage': 'reduce', 'reserved_call_id': identity, 'source_hash': source['source_hash'],
            'catalog_hash': source['catalog_hash'], 'generation_hash': digest({'execution': selected}),
            'dispatch_hash': digest({'request_hash': expected['body_sha256']}),
            'messages_hash': digest(expected['request']['messages'])}
        state.context = {'binding': binding, 'request_hash': expected['body_sha256'],
            'endpoint': expected['endpoint'], 'delivery': native.DELIVERY,
            'schema_proof_hash': digest(expected['schema_proof']), 'profile_hash': digest(policy),
            'token_bound': len(expected['body']) + expected['request']['max_tokens'] + 1024,
            'max_bytes': native.stage_policy(native.STAGE)['max_response_bytes']}
        state.events.append('on-reserved')
        return capture.prepare(state.root, state.context)

    def received(original, receipt):
        assert original.encode('utf-8') == state.response
        saved = capture.load(state.root, state.context)
        assert saved['ready'] and saved['response_complete'] is True
        assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
        state.callbacks.append((original, copy.deepcopy(receipt)))

    result = gateway.cloud_json(cap, messages, schema,
        NS(user_id=state.actor, workspace_id=state.workspace), full_report=False,
        evidence_stage=native.STAGE, on_reserved=reserved, on_response=received,
        allocation_evidence=evidence, dispatch_identity={
            'suggestion_id': evidence['draft_ref']['id'], 'job_id': 'public-synthetic-variant-job',
            'task_id': 'report-business-catalogue-allocation-v213',
            'request_hash': digest({'plan_hash': evidence['plan']['plan_hash']})})
    return result, expected, policy, selected


def test_explicit_registered_variant_reaches_real_native_http_and_cold_capture(native_transport):
    (projection, receipt), expected, policy, selected = _invoke_registered(native_transport)
    assert len(native_transport.requests) == len(native_transport.reservations) == 1
    assert len(native_transport.callbacks) == 1
    assert native_transport.requests[0]['content'] == expected['body']
    assert expected['body_sha256'] == hashlib.sha256(expected['body']).hexdigest()
    assert policy['native_schema_variant'] == VARIANT
    assert expected['schema_proof']['schema_policy']['native_schema_variant'] == VARIANT
    assert 'native_schema_variant' not in expected['request']
    assert 'native_schema_variant' not in profiles.evidence_options(selected,
        gateway.cloud_json_options(selected), native.STAGE)
    assert projection['schema'] == wire.PROJECTION and projection['flags']['can_apply'] is False
    assert native_transport.finished[-1][1] == 'succeeded'
    original, cold, saved = bridge.response_context(native_transport.context, 'qwen', native.MODEL,
        policy, native.STAGE)
    assert original.encode('utf-8') == native_transport.response
    assert cold == receipt and saved['response_complete'] is True
    assert set(native_transport.context) == {
        'binding', 'request_hash', 'endpoint', 'delivery', 'schema_proof_hash',
        'profile_hash', 'token_bound', 'max_bytes'}


def test_missing_selector_keeps_legacy_policy_proof_and_gateway_kwargs(native_transport, monkeypatch):
    original = native.prepare_request
    calls = []
    def observed(*args, **kwargs):
        calls.append(copy.deepcopy(kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(native, 'prepare_request', observed)
    (_projection, receipt), expected, policy, selected = _invoke_registered(native_transport, opt_in=False)
    assert calls and all('native_schema_variant' not in value for value in calls)
    assert 'native_schema_variant' not in selected
    assert 'native_schema_variant' not in policy
    assert expected['schema_proof']['schema_policy']['transformation'] == 'remove_array_uniqueItems_only'
    assert 'native_schema_variant' not in expected['schema_proof']['schema_policy']
    assert native_transport.requests[0]['content'] == expected['body']
    assert receipt['evidence_profile'] == policy


@pytest.mark.parametrize('value', [None, '', False, True, 1, {}, 'unknown', VARIANT + '-other'])
def test_invalid_selector_is_rejected_by_registration_and_actual_gateway_before_reserve(native_transport, value):
    e = execution(); e['native_schema_variant'] = value
    with pytest.raises(ValueError):
        profiles.validate_evidence_output(e)
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, e=e)
    assert native_transport.reservations == native_transport.requests == []


@pytest.mark.parametrize('fault', ['missing-declaration', 'other-declaration', 'other-provider', 'other-model', 'nested-selector'])
def test_selector_cannot_borrow_another_declaration_or_provider(native_transport, fault):
    e = configured()
    if fault == 'missing-declaration': del e['report_evidence_output']
    elif fault == 'other-declaration': e['report_evidence_output']['schema'] = 'report-business-catalogue-native-output-v211'
    elif fault == 'other-provider': e['provider'] = 'deepseek'
    elif fault == 'other-model': e['model'] = 'qwen-plus'
    else: e['report_evidence_output']['native_schema_variant'] = VARIANT
    with pytest.raises(ValueError): profiles.validate_evidence_output(e)
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, e=e)
    assert native_transport.reservations == native_transport.requests == []


@pytest.mark.parametrize('stage', [None, 'catalogue-v211', 'map-v172', 'business-flow-v193'])
def test_valid_selector_is_not_an_implicit_upgrade_of_any_other_stage(stage):
    with pytest.raises(ValueError): profiles.evidence_policy(configured(), stage)
    with pytest.raises(ValueError): profiles.declared_map_transport(configured())


def test_manual_provider_option_injection_is_not_a_registered_variant(native_transport, monkeypatch):
    original = profiles.evidence_options
    def inject(*args, **kwargs):
        return {**original(*args, **kwargs), 'native_schema_variant': VARIANT}
    monkeypatch.setattr(profiles, 'evidence_options', inject)
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport)
    assert native_transport.reservations == native_transport.requests == []


def test_existing_capture_cannot_be_requalified_by_reading_it_with_the_new_variant(native_transport):
    (_projection, receipt), _ = invoke(native_transport)
    context_before = copy.deepcopy(native_transport.context)
    state_before = capture.load(native_transport.root, context_before)
    body_before = capture.read_body(native_transport.root, context_before, state_before['capture_hash'])
    events_before = copy.deepcopy(native_transport.finished)
    upgraded = profiles.evidence_policy(configured(), native.STAGE)
    with pytest.raises(ValueError):
        bridge.response_context(context_before, 'qwen', native.MODEL, upgraded, native.STAGE)
    assert capture.load(native_transport.root, context_before) == state_before
    assert capture.read_body(native_transport.root, context_before, state_before['capture_hash']) == body_before
    assert native_transport.context == context_before and native_transport.finished == events_before
    assert len(native_transport.requests) == len(native_transport.reservations) == 1
    assert 'native_schema_variant' not in receipt['evidence_profile']


@pytest.mark.parametrize('stage,provider,value', [
    (native.STAGE, 'qwen', None), (native.STAGE, 'qwen', 'unknown'),
    ('catalogue-v211', 'qwen', VARIANT), (native.STAGE, 'deepseek', VARIANT)])
def test_cold_reader_rejects_foreign_or_unknown_variant_before_any_capture_read(monkeypatch, stage, provider, value):
    def forbidden(*args, **kwargs):
        raise AssertionError('An invalid frozen variant cannot reach storage')
    monkeypatch.setattr(capture, 'load', forbidden)
    with pytest.raises(ValueError):
        bridge.response_context({}, provider, native.MODEL, {'native_schema_variant': value}, stage)


def test_manifest_and_policy_validation_never_insert_a_default_or_rewrite_old_configuration():
    e = execution()
    manifest = Manifest(kind='foundation_model', namespace='public_synthetic', name='variant_fixture',
        version='1.0.0', owner='Public synthetic test', description='No source or paid authority',
        execution=e, data_policy={'cloud_allowed': True})
    before = manifest.model_dump(mode='json'); before_hash = digest(before)
    assert profiles.validate_evidence_output(manifest.execution) is manifest.execution
    assert native.schema_variant(manifest.execution) is None
    assert manifest.model_dump(mode='json') == before and digest(manifest.model_dump(mode='json')) == before_hash
    assert 'native_schema_variant' not in manifest.execution


@pytest.mark.parametrize('fault', ['non-model', 'missing-declaration', 'none', 'unknown'])
def test_capability_validation_cannot_publish_an_ignored_selector(monkeypatch, fault):
    e = configured(); kind = 'tool' if fault == 'non-model' else 'foundation_model'
    if fault == 'missing-declaration': del e['report_evidence_output']
    elif fault == 'none': e['native_schema_variant'] = None
    elif fault == 'unknown': e['native_schema_variant'] = 'unknown'
    manifest = Manifest(kind=kind, namespace='public_synthetic', name='invalid_variant_fixture',
        version='1.0.0', owner='Public synthetic test', description='Negative format gate only',
        execution=e, data_policy={'cloud_allowed': True})
    cap = NS(id='10000000-0000-4000-8000-000000000005', namespace=manifest.namespace,
        version=1, status='draft', data=manifest.model_dump(mode='json'))
    before = copy.deepcopy(cap.data)
    monkeypatch.setattr(services, 'scoped', lambda *_args, **_kwargs: cap)
    with pytest.raises(HTTPException) as error:
        services.validate_capability(None, NS(role='operator'), cap.id, 1)
    assert error.value.status_code == 422 and cap.status == 'draft' and cap.data == before
    if fault == 'non-model': assert '基础模型' in error.value.detail


def test_discovery_skips_ignored_or_foreign_selectors_without_exposing_private_execution(monkeypatch):
    good = capability(configured()); good.name = 'Explicit Qwen schema subset'; good.release_version = '1.0.1'; good.status = 'published'
    absent = copy.deepcopy(good); absent.id = 'absent'; del absent.data['execution']['report_evidence_output']
    unknown = copy.deepcopy(good); unknown.id = 'unknown'; unknown.data['execution']['native_schema_variant'] = 'unknown'
    foreign = copy.deepcopy(good); foreign.id = 'foreign'; foreign.data['execution']['report_evidence_output']['schema'] = 'report-business-catalogue-native-output-v211'
    monkeypatch.setattr(understanding, 'model', lambda *_args: good)
    class Rows:
        def scalars(self, _statement): return NS(all=lambda: [good, absent, unknown, foreign])
    rows = understanding.available_models(NS(workspace_id=good.workspace_id), Rows())
    assert len(rows) == 1 and rows[0]['id'] == good.id
    assert rows[0]['generation_profile'] == native.STAGE
    assert 'secret_ref' not in json.dumps(rows)
