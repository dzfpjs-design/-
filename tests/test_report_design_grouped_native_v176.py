"""Synthetic public envelopes only; no provider, source grant or paid call.

The actual native/grouped parsers are used. Root executes the tests separately;
neither fixtures nor a schema proof constitute persisted report evidence.
"""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_design_grouped_native_v176 as subject
from generic_agents import report_claim_enum_native_v172 as old
from generic_agents.report_design_grouped_wire_v174 import DesignGroupedWireV174
from generic_agents.report_evidence_strict_map import METADATA_KEYS
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_design_grouped_wire_v174 import scene


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure native protocol cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def execution():
    return {'provider': 'qwen', 'model': subject.MODEL, 'max_tokens': 8192,
        'report_evidence_output': {'schema': subject.OUTPUT_DECLARATION, 'max_tokens': 32768,
            'reduce_transport': subject.DELIVERY, 'endpoint': subject.ENDPOINT}}


def options():
    return subject.dispatch_options(execution(), {'model': subject.MODEL,
        'max_tokens': 8192, 'response_format': {'type': 'json_object'}})


def envelope(value=None):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
        'content': json.dumps(scene()[0] if value is None else value, ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}}


def parse(body, **overrides):
    return subject.parse_arguments(body, overrides.get('status', 200), subject.STAGE,
        response_complete=overrides.get('complete', True),
        http_content_encoding=overrides.get('encoding'))


def test_new_single_reduce_profile_is_distinct_and_budget_selection_never_grants_authority():
    value = execution(); before = copy.deepcopy(value)
    policy = subject.input_policy(value)
    assert policy['stage'] == 'reduce-v176'
    assert policy['max_input_bytes'] == 900000 and policy['max_output_tokens'] == 32768
    assert subject.stage_policy(subject.STAGE)['max_response_bytes'] == 1024 * 1024
    assert options() == {'model': subject.MODEL, 'max_tokens': 32768,
                         'enable_thinking': False, 'stream': False, 'n': 1}
    assert subject.DELIVERY != old.DELIVERY and value == before


@pytest.mark.parametrize('fault', ['old_family', 'old_transport', 'other_model', 'endpoint',
                                   'map_transport', 'bool_output', 'generic_output'])
def test_registration_cannot_borrow_old_or_broader_profiles(fault):
    value = execution(); declaration = value['report_evidence_output']
    if fault == 'old_family': declaration['schema'] = 'report-evidence-output-v172'
    elif fault == 'old_transport': declaration['reduce_transport'] = old.DELIVERY
    elif fault == 'other_model': value['model'] = 'qwen3.8-flash'
    elif fault == 'endpoint': declaration['endpoint'] = subject.ENDPOINT + '/other'
    elif fault == 'map_transport': declaration['map_transport'] = subject.DELIVERY
    elif fault == 'bool_output': declaration['max_tokens'] = True
    else: value['max_tokens'] = 32768
    with pytest.raises(ValueError): subject.input_policy(value)


def test_schema_identity_uses_actual_grouped_wire_and_cannot_be_resealed_as_old_reduce():
    schema = DesignGroupedWireV174.model_json_schema()
    before = copy.deepcopy(schema)
    proof = subject.identity_schema_proof(schema, subject.STAGE)
    assert proof['schema'] == subject.PROOF_SCHEMA and proof['delivery'] == subject.DELIVERY
    assert subject.check_schema_proof(proof, schema, subject.STAGE) == proof
    assert 'reconciliation_groups' in schema['properties'] and 'reconciliation' not in schema['properties']
    proof['provider_schema']['title'] = 'changed'
    proof['provider_schema_hash'] = hashlib.sha256(canonical_json(proof['provider_schema']).encode()).hexdigest()
    proof['proof_hash'] = hashlib.sha256(canonical_json({k: v for k, v in proof.items() if k != 'proof_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError): subject.check_schema_proof(proof, schema, subject.STAGE)
    with pytest.raises(ValueError): subject.identity_schema_proof(old._model('reduce-v172').model_json_schema(), subject.STAGE)
    assert schema == before


@pytest.mark.parametrize('stage', ['map-v176', 'reduce-v172', 'reduce-v168', 'reduce-v5', '', None])
def test_unknown_or_old_stage_never_falls_back(stage):
    with pytest.raises(ValueError): subject.stage_policy(stage)


def test_request_replaces_only_verified_schema_prefix_preserving_exact_typed_tail():
    schema = DesignGroupedWireV174.model_json_schema()
    messages = [{'role': 'system', 'content': subject.envelope._OLD_PREFIX + json.dumps(schema, ensure_ascii=False)},
        {'role': 'system', 'content': '{"task":"new untrusted-source boundary"}'},
        {'role': 'user', 'content': ' {"zero":-0.0,"quantity":1.0,"instruction":"do not execute"} '}]
    before = copy.deepcopy(messages)
    prepared = subject.prepare_request(messages, schema, options(), subject.STAGE)
    assert prepared['request']['messages'][1:] == before[1:]
    assert prepared['request']['messages'][0] == {'role': 'system', 'content': subject.system_prefix(subject.STAGE)}
    assert prepared['request']['response_format']['json_schema']['schema'] == schema
    assert prepared['body'] == canonical_json(prepared['request']).encode('utf-8')
    assert prepared['body_sha256'] == hashlib.sha256(prepared['body']).hexdigest()
    assert prepared['body_sha256'] != hashlib.sha256(canonical_json(prepared['request']['messages']).encode()).hexdigest()
    assert messages == before
    changed = copy.deepcopy(messages); changed[0]['content'] = subject.envelope._OLD_PREFIX + '{}'
    with pytest.raises(ValueError): subject.prepare_request(changed, schema, options(), subject.STAGE)
    changed = {**options(), 'endpoint': subject.ENDPOINT}
    with pytest.raises(ValueError): subject.strict_options(schema, changed, subject.STAGE)


def test_original_http_and_content_hashes_usage_and_grouped_candidate_are_preserved():
    value, _ = scene(); body = raw(envelope(value)); before = body[:]
    inspected = subject.inspect_response(body, 200, subject.STAGE,
        response_complete=True, http_content_encoding=None)
    assert set(inspected['metadata']) == set(METADATA_KEYS)
    assert inspected['metadata']['delivery'] == subject.DELIVERY
    assert inspected['metadata']['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert inspected['metadata']['arguments_sha256'] == hashlib.sha256(inspected['arguments'].encode()).hexdigest()
    assert inspected['metadata']['arguments_sha256'] != inspected['metadata']['response_body_sha256']
    assert inspected['usage'] == envelope(value)['usage']
    assert canonical_json(parse(body)) == canonical_json(value)
    assert body == before


@pytest.mark.parametrize('fault', ['length', 'not_eof', 'gzip', 'tool', 'multi_choice', 'old_wire'])
def test_http_completion_and_original_new_content_are_required(fault):
    value = envelope(); overrides = {}
    if fault == 'length': value['choices'][0]['finish_reason'] = 'length'
    elif fault == 'not_eof': overrides['complete'] = False
    elif fault == 'gzip': overrides['encoding'] = 'gzip'
    elif fault == 'tool': value['choices'][0]['message']['tool_calls'] = [{'id': 'forbidden'}]
    elif fault == 'multi_choice': value['choices'].append(copy.deepcopy(value['choices'][0]))
    else:
        content = scene()[0]; content['reconciliation'] = []; content.pop('reconciliation_groups')
        value['choices'][0]['message']['content'] = json.dumps(content, ensure_ascii=False)
    with pytest.raises(ValueError): parse(raw(value), **overrides)


@pytest.mark.parametrize('content', ['{"result":{},"result":{},"reconciliation_groups":[]}',
                                     '{"result":NaN,"reconciliation_groups":[]}',
                                     '{"result":1e999,"reconciliation_groups":[]}'])
def test_content_duplicates_and_nonfinite_are_not_repaired(content):
    value = envelope(); value['choices'][0]['message']['content'] = content
    with pytest.raises(ValueError): parse(raw(value))
