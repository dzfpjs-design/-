"""Inline compiled-shape examples only; no DB, worker, files or model calls.

Root runs pytest. These tests validate deterministic derivation and integrity,
not publication, permissions, runtime output truth or actual data durability.
"""
import copy
import hashlib
import json

import pytest

from generic_agents.instance_data_requirements import build_requirements, verify_requirements


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def snapshot():
    schema = {'type': 'object', 'additionalProperties': False,
        'properties': {'observations': {'type': 'array', 'items': {'$ref': '#/$defs/Reading'}},
                       'complete': {'type': 'boolean'}}, 'required': ['observations', 'complete'],
        '$defs': {'Reading': {'type': 'object', 'properties': {'value': {'type': 'number'}},
                            'required': ['value'], 'additionalProperties': False}}}
    first = {'id': 'step-first', 'name': '合成处理', 'agent': '多模感知智能体', 'execution': {
        'capability_id': 'synthetic-capability', 'capability_hash': 'a' * 64, 'release_version': '1.2.3',
        'agent_role': 'perception', 'bindings': {'evidence': {'source': 'original_evidence_ids'}},
        'output_schema': schema}}
    second = {'id': 'step-second', 'name': '合成汇集', 'agent': '数据管理智能体', 'execution': {
        'capability_id': 'synthetic-data-capability', 'capability_hash': 'b' * 64, 'release_version': '2.0.0',
        'agent_role': 'data', 'bindings': {'readings': {'source': 'previous_output',
            'step_id': 'step-first', 'field': 'observations'}},
        'output_schema': {'type': 'object', 'properties': {'summary': {'type': 'string'}}}}}
    human = {'id': 'step-human', 'name': '人工核对', 'agent': '人工确认', 'role': '核对者', 'output': '原样说明'}
    return {'businesses': [{'id': 'business-one', 'name': '合成业务', 'available': True, 'issues': [],
        'flow': {'steps': [first, second, human]}}], 'entity_schemas': {'confirmed-record': {'type': 'object'}},
        'object_types': [{'key': 'confirmed-record', 'name': '已供定义', 'fields': [
            {'key': 'field-label', 'label': '名称', 'kind': '文本', 'required': True, 'unit': None}]}],
        'resources': [{'id': 'resource-one', 'category': 'sensor'}],
        'sensor_sources': [{'id': 'source-one', 'version': 1, 'source_hash': 'c' * 64}],
        'materials': [{'id': 'material-one', 'version': 1, 'hash': 'd' * 64}],
        'automation': {'policies': {'合成业务': {'rules': {'state': {'mode': '自动运行'}}}}},
        'equipment': {'selected': ['resource-one']}, 'general_issues': []}


def bound(value):
    result = copy.deepcopy(value)
    result['data_requirements'] = build_requirements(result)
    return result


def test_actual_frozen_outputs_and_original_edges_are_preserved_without_object_inference():
    value = snapshot(); before = copy.deepcopy(value)
    result = build_requirements(value)
    assert set(result) == {'schema', 'origin', 'dependency_hash', 'dependency_hashes',
        'confirmed_object_catalog', 'businesses', 'evidence_policy', 'summary', 'requirements_hash'}
    assert result['schema'] == 'instance-data-requirements-v1' and result['origin'] == 'system_design'
    business = result['businesses'][0]
    assert set(business) == {'id', 'name', 'status', 'issues', 'outputs', 'summary'}
    assert business['status'] == 'derived' and len(business['outputs']) == 2
    for output, step in zip(business['outputs'], value['businesses'][0]['flow']['steps']):
        pin = step['execution']
        assert set(output) == {'step_id', 'step_name', 'agent_role', 'capability_id', 'capability_hash',
            'release_version', 'output_schema', 'output_schema_hash', 'input_bindings', 'fields', 'storage', 'status'}
        assert output['output_schema'] == pin['output_schema']
        assert output['output_schema_hash'] == digest(pin['output_schema'])
        assert output['input_bindings'] == pin['bindings']
        assert output['fields'] == [{'name': name, 'schema': pin['output_schema']['properties'][name]}
            for name in sorted(pin['output_schema']['properties'])]
        assert output['status'] == 'candidate'
    assert business['outputs'][1]['input_bindings']['readings']['step_id'] == 'step-first'
    assert result['confirmed_object_catalog'] == value['object_types']
    assert value == before


def test_no_schema_is_invented_for_manual_results_or_opaque_root_refs():
    value = snapshot()
    original = value['businesses'][0]['flow']['steps'][0]['execution']['output_schema']
    root_ref = {'$ref': '#/$defs/Reading', '$defs': original['$defs']}
    value['businesses'][0]['flow']['steps'][0]['execution']['output_schema'] = root_ref
    result = build_requirements(value)
    outputs = result['businesses'][0]['outputs']
    assert [item['step_id'] for item in outputs] == ['step-first', 'step-second']
    assert outputs[0]['fields'] == [] and outputs[0]['output_schema'] == root_ref
    assert '人工' in result['businesses'][0]['summary']


def test_design_does_not_upgrade_evidence_timestamps_units_or_retention_authority():
    result = build_requirements(snapshot())
    assert result['evidence_policy'] == {'status': 'candidate',
        'source_timestamp': 'unknown_until_actual_output', 'unit': 'unknown_until_actual_output',
        'retention_authorized': False, 'deletion_authorized': False}
    assert all(output['status'] == 'candidate' for business in result['businesses'] for output in business['outputs'])
    assert 'source_fact' not in result.values()
    assert 'can_apply' not in result and 'can_publish' not in result


@pytest.mark.parametrize('change', ['unavailable', 'numeric_available', 'business_issue', 'general_issue'])
def test_blocked_business_ignores_forged_execution_pins_and_exposes_issues(change):
    value = snapshot(); business = value['businesses'][0]
    if change == 'unavailable': business['available'] = False
    elif change == 'numeric_available': business['available'] = 1
    elif change == 'business_issue': business['issues'] = ['能力尚未接通']
    else: value['general_issues'] = ['生成版本条件未齐']
    business['flow']['steps'][0]['execution'] = {'capability_hash': 'forged', 'output_schema': {'made_up': True}}
    result = build_requirements(value)['businesses'][0]
    assert result['status'] == 'blocked' and result['outputs'] == [] and result['issues']


@pytest.mark.parametrize('key,bad', [('capability_id', ''), ('capability_hash', 'not-a-hash'),
    ('release_version', 'unversioned'), ('agent_role', 'business'), ('output_schema', []), ('bindings', [])])
def test_available_automatic_step_requires_real_frozen_contract_shape(key, bad):
    value = snapshot(); value['businesses'][0]['flow']['steps'][0]['execution'][key] = bad
    with pytest.raises(ValueError): build_requirements(value)


@pytest.mark.parametrize('dependency', ['businesses', 'entity_schemas', 'object_types', 'resources',
    'sensor_sources', 'materials', 'automation', 'equipment', 'general_issues'])
def test_each_declared_dependency_change_invalidates_saved_requirements(dependency):
    value = bound(snapshot()); old_hash = value['data_requirements']['dependency_hash']
    if dependency == 'businesses': value[dependency][0]['flow']['steps'][0]['name'] = '更改处理名'
    elif dependency == 'entity_schemas': value[dependency]['confirmed-record']['description'] = '更改定义'
    elif dependency == 'object_types': value[dependency][0]['fields'][0]['required'] = False
    elif dependency in ('resources', 'sensor_sources', 'materials'): value[dependency].append({'id': 'new-dependency'})
    elif dependency in ('automation', 'equipment'): value[dependency]['changed'] = True
    else: value[dependency].append('新增阻塞')
    with pytest.raises(ValueError): verify_requirements(value)
    assert build_requirements(value)['dependency_hash'] != old_hash


@pytest.mark.parametrize('change', ['hash', 'field', 'schema', 'binding', 'source_fact', 'extra', 'null'])
def test_cold_verifier_rejects_tamper_even_if_final_hash_is_resealed(change):
    value = bound(snapshot()); result = value['data_requirements']
    if change == 'hash': result['dependency_hash'] = 'f' * 64
    elif change == 'field': result['businesses'][0]['outputs'][0]['fields'].append({'name': 'invented', 'schema': {}})
    elif change == 'schema': result['businesses'][0]['outputs'][0]['output_schema']['required'] = []
    elif change == 'binding': result['businesses'][0]['outputs'][1]['input_bindings']['readings']['step_id'] = 'other-step'
    elif change == 'source_fact': result['origin'] = 'source_fact'
    elif change == 'extra': result['can_apply'] = True
    else: value['data_requirements'] = None
    if change != 'null': result['requirements_hash'] = digest({key: item for key, item in result.items() if key != 'requirements_hash'})
    with pytest.raises(ValueError): verify_requirements(value)


def test_legacy_absence_and_current_exact_contract_are_compatible():
    assert verify_requirements({}) is None
    value = bound(snapshot()); before = copy.deepcopy(value)
    assert verify_requirements(value) is None
    assert build_requirements(value) == value['data_requirements']
    assert value == before


def test_deterministic_dictionary_reordering_and_deep_copy_isolation():
    value = snapshot(); first = build_requirements(value)
    def reordered(item):
        if type(item) is dict: return {key: reordered(value) for key, value in reversed(list(item.items()))}
        if type(item) is list: return [reordered(value) for value in item]
        return item
    assert build_requirements(reordered(value)) == first
    original = copy.deepcopy(first)
    first['confirmed_object_catalog'][0]['fields'].clear()
    first['businesses'][0]['outputs'][0]['output_schema']['$defs'].clear()
    assert value['object_types'][0]['fields'] and value['businesses'][0]['flow']['steps'][0]['execution']['output_schema']['$defs']
    assert build_requirements(value) == original
    assert first != original


def test_duplicate_step_identity_and_nonfinite_dependency_fail_closed():
    value = snapshot()
    value['businesses'][0]['flow']['steps'][1]['id'] = 'step-first'
    with pytest.raises(ValueError): build_requirements(value)
    value = snapshot(); value['resources'][0]['nonfinite'] = float('nan')
    with pytest.raises(ValueError): build_requirements(value)
