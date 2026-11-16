"""Pure explicit V5 preparation, not API, provider or business-quality proof."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_evidence_preparation as prep
from generic_agents import report_evidence_structured_tool_v2 as tool
from generic_agents.cloud_evidence_profiles import (evidence_policy, evidence_options,
    declared_map_transport, validate_evidence_output)
from generic_agents.contracts import digest
from generic_agents.gateway import cloud_json_options
from generic_agents.report_evidence_plan import build_evidence_source, PLAN_V2
from tests.test_report_evidence_v3_preparation_pure import capability


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*a, **kw): raise AssertionError('Preparation must not connect or execute')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def cap():
    value = capability(maximum=131072)
    value.data['execution']['report_evidence_output'] = {'schema': 'report-evidence-output-v3',
        'max_tokens': 131072, 'map_transport': tool.DELIVERY, 'reduce_transport': tool.DELIVERY}
    return value


def scene():
    model = cap()
    catalog = build_evidence_source('a' * 64, [['w:p1', ' 原字＋+，空白不改。 '],
        ['w:p2', ''], ['o:p1', '合成OCR内容，不是独立证明。']])
    value = prep.build_map_preparation(model, catalog, {'object_names': []},
        generation_version=prep.GENERATION_V5)
    return model, catalog, value


def test_new_declaration_requires_both_exact_transports_and_keeps_separate_stage_limits():
    model = cap(); execution = model.data['execution']; before = copy.deepcopy(execution)
    assert validate_evidence_output(execution) == execution == before
    assert declared_map_transport(execution) == tool.DELIVERY
    m, r = evidence_policy(execution, 'map-v5'), evidence_policy(execution, 'reduce-v3')
    assert m['max_output_tokens'] == 8192 and m['transport_timeout_seconds'] == 180
    assert r['max_output_tokens'] == 131072 and r['transport_timeout_seconds'] == 600
    for stage in ('map-v5', 'reduce-v3'):
        options = evidence_options(execution, cloud_json_options(execution), stage)
        assert options['stream'] is False and 'response_format' not in options
        assert options['thinking'] == {'type': 'disabled'}
    assert execution == before


@pytest.mark.parametrize('change', ['missing_reduce', 'wrong_map', 'wrong_reduce', 'extra',
    'bool_output', 'above_output', 'qwen', 'thinking', 'no_declaration'])
def test_unregistered_ambiguous_or_incompatible_profile_never_falls_back(change):
    value = cap().data['execution']; declaration = value['report_evidence_output']
    if change == 'missing_reduce': declaration.pop('reduce_transport')
    elif change == 'wrong_map': declaration['map_transport'] = 'deepseek-strict-map-tool-v1'
    elif change == 'wrong_reduce': declaration['reduce_transport'] = 'deepseek-strict-map-tool-v1'
    elif change == 'extra': declaration['allow_fallback'] = True
    elif change == 'bool_output': declaration['max_tokens'] = True
    elif change == 'above_output': declaration['max_tokens'] = 131073
    elif change == 'qwen': value.update(provider='qwen', model='qwen-plus'); value.pop('thinking_mode')
    elif change == 'thinking': value['thinking_mode'] = 'enabled'
    else: value.pop('report_evidence_output')
    with pytest.raises(ValueError): evidence_policy(value, 'map-v5')
    with pytest.raises(ValueError): evidence_policy(value, 'reduce-v3')


@pytest.mark.parametrize('stage', ['map-v3', 'map-v4', 'reduce-v2'])
def test_new_manifest_cannot_change_old_generation_meanings(stage):
    with pytest.raises(ValueError): evidence_policy(cap().data['execution'], stage)


@pytest.mark.parametrize('version', ['report-evidence-output-v1', 'report-evidence-output-v2'])
def test_old_manifests_never_opt_into_new_dispatch(version):
    value = capability(maximum=131072).data['execution']
    value['report_evidence_output'] = {'schema': version, 'max_tokens': 131072}
    if version.endswith('v2'): value['report_evidence_output']['map_transport'] = 'deepseek-strict-map-tool-v1'
    for stage in ('map-v5', 'reduce-v3'):
        with pytest.raises(ValueError): evidence_policy(value, stage)


def test_actual_full_post_is_measured_frozen_and_rebuilds_with_all_source_spans_and_empty_sources():
    model, catalog, value = scene(); original = copy.deepcopy((model.data, catalog))
    assert value['plan']['schema'] == PLAN_V2
    expected = list(catalog['index'])
    covered = [ref for chunk in value['plan']['chunks'] for ref in chunk['span_refs']]
    assert covered == expected and len(covered) == len(set(covered))
    assert value['plan']['empty_sources'] == ['w:p2']
    assert value['input_summary']['maximum_total_calls'] == len(value['plan']['chunks']) + 1
    byte_sum = 0
    for chunk in value['plan']['chunks']:
        request = prep.rebuild_map_request(value, catalog, model, chunk['chunk_id'])
        assert request['request_body'] == tool._bytes(request['request'])
        assert request['request_hash'] == hashlib.sha256(request['request_body']).hexdigest() == chunk['request_hash']
        assert request['messages_hash'] == digest(request['request']['messages']) == chunk['messages_hash']
        assert chunk['request_hash'] != chunk['messages_hash']
        assert request['input_summary']['prepared_request_bytes'] == len(request['request_body']) <= 100000
        byte_sum += len(request['request_body'])
    assert byte_sum == value['input_summary']['prepared_request_bytes']
    assert (model.data, catalog) == original and value['can_apply'] is value['can_publish'] is False
    assert prep.check_map_preparation(value, catalog, model) == value


def test_v5_task_has_one_consistent_slot_wire_without_legacy_claims_top_level_instructions():
    _, _, value = scene(); task = value['generation_contract']['task']
    assert 'report-evidence-map-slots-v5' in task and 'slot_080' in task
    assert '顶层只能且必须有schema和claims' not in task
    assert '只返回严格report-evidence-map-v3 JSON' not in task
    assert value['generation_contract']['output_schema']['properties']['slots']['$ref']
    assert '$def' in value['generation_contract']['dispatch_options']['tools'][0]['function']['parameters']


@pytest.mark.parametrize('change', ['source', 'tool', 'schema', 'request_hash', 'missing_chunk', 'context'])
def test_public_rehash_does_not_authorize_tampered_source_plan_or_request(change):
    model, catalog, value = scene(); value = copy.deepcopy(value)
    if change == 'source': value['catalog_descriptor']['source_hash'] = 'b' * 64
    elif change == 'tool': value['generation_contract']['dispatch_options']['tool_choice']['function']['name'] = 'arbitrary'
    elif change == 'schema': value['generation_contract']['output_schema']['properties'].pop('slots')
    elif change == 'request_hash': value['plan']['chunks'][0]['request_hash'] = 'b' * 64
    elif change == 'missing_chunk': value['plan']['chunks'].pop()
    else: value['context']['object_names'] = ['changed']
    value['preparation_hash'] = digest({k: v for k, v in value.items() if k != 'preparation_hash'})
    with pytest.raises(ValueError): prep.check_map_preparation(value, catalog, model)


def test_privacy_projection_cannot_silently_change_frozen_payload(monkeypatch):
    model, catalog, value = scene(); original = prep.prepare_cloud_json
    def changed(*args, **kwargs):
        messages, size, images = original(*args, **kwargs)
        messages = copy.deepcopy(messages); payload = json.loads(messages[-1]['content'])
        payload['spans'][0][1] = 'changed'; messages[-1]['content'] = json.dumps(payload)
        return messages, size, images
    monkeypatch.setattr(prep, 'prepare_cloud_json', changed)
    with pytest.raises(ValueError): prep.rebuild_map_request(value, catalog, model, value['plan']['chunks'][0]['chunk_id'])
