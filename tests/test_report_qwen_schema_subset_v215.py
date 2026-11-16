"""Pure provider dialect contracts; no actual provider/IAM/paid acceptance.

The public V213 evidence is synthetic. Root separately checks old saved request
goldens and a newly authorized actual provider activity. No test repairs an old
HTTP 400, marks a CloudCall successful, or changes a local wire requirement.
"""
import copy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from generic_agents import report_qwen_schema_subset_v215 as subset
from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_business_catalogue_allocation_preparation_v213 as preparation
from generic_agents import report_business_catalogue_allocation_wire_v213 as wire
from generic_agents import report_business_catalogue_allocation_plan_v213 as planning
from generic_agents.contracts import digest
from generic_agents.report_evidence_strict_map import StrictMapError
from tests.test_report_business_catalogue_allocation_native_v213 import scene, candidate, raw


VARIANT = subset.NATIVE_SCHEMA_VARIANT


def compile_schema(value):
    return subset.compile_provider_schema(value, native_schema_variant=VARIANT)


def test_real_local_batch_schema_keeps_unique_and_pending_ownership_while_provider_subset_omits_them():
    schema = wire.schema_for_keys(['p000000'], ['ops', 'other'])
    before = copy.deepcopy(schema)
    compiled = compile_schema(schema)
    options = schema['properties']['decisions']['properties']['p000000']['oneOf']
    provider = compiled['provider_schema']['properties']['decisions']['properties']['p000000']['oneOf']
    for index in range(3):
        assert options[index]['properties']['business_keys']['uniqueItems'] is True
        assert 'uniqueItems' not in provider[index]['properties']['business_keys']
        assert provider[index]['properties']['business_keys']['maxItems'] == (0 if index == 1 else 2)
    assert 'contains' in options[2]['properties']['questions']
    assert 'contains' not in provider[2]['properties']['questions']
    assert provider[2]['properties']['questions']['minItems'] == 1
    expected = {
        '/properties/decisions/properties/p000000/oneOf/' + str(index) + '/properties/business_keys/uniqueItems': True
        for index in range(3)}
    expected['/properties/decisions/properties/p000000/oneOf/2/properties/questions/contains'] = options[2]['properties']['questions']['contains']
    policy = compiled['schema_policy']
    assert {item['path']: item['value'] for item in policy['removed_assertions']} == expected
    assert policy['native_schema_variant'] == VARIANT
    assert policy['local_validation_required'] is True and policy['provider_support_verified'] is False
    assert compiled['local_schema_hash'] == digest(schema)
    assert compiled['provider_schema_hash'] == digest(compiled['provider_schema'])
    assert compiled['schema_policy_hash'] == digest(policy)
    assert schema == before
    assert subset.preflight_provider_schema(compiled['provider_schema']) is compiled['provider_schema']


def test_compiler_distinguishes_schema_keywords_from_property_names_and_literal_objects():
    literal = {'contains': {'uniqueItems': 'ordinary literal data'}, 'multipleOf': 3}
    schema = {'type': 'object', 'properties': {
        'a/b~c': {'type': 'array', 'uniqueItems': True, 'minContains': 1, 'maxContains': 2,
            'contains': {'type': 'number', 'multipleOf': 2}, 'additionalItems': False},
        'contains': {'type': 'object', 'properties': {'uniqueItems': {'type': 'string'}},
            'patternProperties': {'^x/y~': {'type': 'number', 'multipleOf': 3}},
            'propertyNames': {'type': 'string', 'pattern': '^[a-z]+$'}}},
        '$defs': {'uniqueItems': {'type': 'array', 'uniqueItems': True}},
        'dependentSchemas': {'contains': {'type': 'array', 'contains': {'type': 'integer'}}},
        'allOf': [{'not': {'type': 'array', 'contains': {'type': 'boolean'}}}],
        'if': {'properties': {'multipleOf': {'type': 'integer', 'multipleOf': 2}}},
        'then': {'properties': {'contains': {'type': 'string'}}},
        'const': literal, 'enum': [literal], 'default': literal, 'examples': [literal]}
    before = copy.deepcopy(schema)
    result = compile_schema(schema)
    provider, assertions = result['provider_schema'], result['schema_policy']['removed_assertions']
    assert {item['path'].rsplit('/', 1)[-1] for item in assertions} == subset.UNSUPPORTED_KEYWORDS
    by_path = {item['path']: item['value'] for item in assertions}
    assert by_path['/properties/a~1b~0c/uniqueItems'] is True
    assert by_path['/properties/contains/patternProperties'] == schema['properties']['contains']['patternProperties']
    assert by_path['/properties/contains/patternProperties/^x~1y~0/multipleOf'] == 3
    assert provider['properties']['contains']['properties']['uniqueItems'] == {'type': 'string'}
    assert 'uniqueItems' in provider['$defs'] and 'contains' in provider['dependentSchemas']
    assert provider['then']['properties']['contains'] == {'type': 'string'}
    for key in ('const', 'enum', 'default', 'examples'):
        assert provider[key] == schema[key]
    assert schema == before
    subset.preflight_provider_schema(provider)
    # A genuine schema assertion must still fail the preflight.
    with pytest.raises(ValueError):
        subset.preflight_provider_schema({'type': 'array', 'contains': {'type': 'string'}})


@pytest.mark.parametrize('value', [None, True, 1, {}, '', 'qwen-other-subset'])
def test_only_the_explicit_fixed_variant_can_compile(value):
    with pytest.raises(ValueError):
        subset.compile_provider_schema({'type': 'object'}, native_schema_variant=value)


def test_provider_relaxation_cannot_relax_the_original_local_validation():
    schema = wire.schema_for_keys(['p000000'], ['ops', 'other'])
    provider = compile_schema(schema)['provider_schema']
    duplicated = {'schema': wire.WIRE, 'decisions': {'p000000': {'disposition': 'assign',
        'business_keys': ['ops', 'ops'], 'reason': 'public duplicate-recipient test', 'questions': []}}}
    Draft202012Validator(provider).validate(duplicated)
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(duplicated)
    _, _, _, evidence = scene()
    pending = candidate(evidence)
    first = next(iter(pending['decisions']))
    pending['decisions'][first].update(disposition='pending',
        questions=[{'kind': 'missing_info', 'question': 'public missing information only'}])
    actual_schema = wire.output_schema(evidence['plan'], evidence['batch_ordinal'])
    Draft202012Validator(compile_schema(actual_schema)['provider_schema']).validate(pending)
    with pytest.raises(planning.AllocationPlanError) as error:
        wire.parse_wire(raw(pending), plan=evidence['plan'], batch_ordinal=evidence['batch_ordinal'])
    assert 'pending_ownership_question' in str(error.value)


@pytest.mark.parametrize('value', [None, False, 0, {}, 'unknown'])
def test_explicit_invalid_capability_variant_is_not_treated_as_legacy(value):
    cap, _, _, _ = scene()
    cap.data['execution']['native_schema_variant'] = value
    with pytest.raises(StrictMapError):
        native.input_policy(cap.data['execution'])


def test_registration_selector_is_outside_the_unchanged_four_key_declaration():
    cap, _, _, _ = scene()
    execution = cap.data['execution']
    original = copy.deepcopy(execution['report_evidence_output'])
    assert native.schema_variant(execution) is None
    assert 'native_schema_variant' not in native.input_policy(execution)
    execution['native_schema_variant'] = VARIANT
    assert native.schema_variant(execution) == VARIANT
    assert native.input_policy(execution)['native_schema_variant'] == VARIANT
    assert execution['report_evidence_output'] == original and len(original) == 4
    execution['provider'] = 'deepseek'
    with pytest.raises(StrictMapError):
        native.schema_variant(execution)
    execution['provider'] = 'qwen'
    execution['report_evidence_output']['schema'] = 'report-business-catalogue-native-output-v211'
    with pytest.raises(StrictMapError):
        native.schema_variant(execution)


def test_legacy_projection_contract_stays_explicitly_legacy_without_selector():
    schema = wire.schema_for_keys(['p000000'], ['ops'])
    proof = native.schema_shape_proof(schema, native.STAGE)
    assert proof == native.schema_shape_proof(schema, native.STAGE, native_schema_variant=None)
    policy = proof['schema_policy']
    assert policy['schema'] == native.SCHEMA_POLICY
    assert policy['transformation'] == 'remove_array_uniqueItems_only'
    assert 'native_schema_variant' not in policy
    assert 'native_schema_variant' not in native.native_policy(native.STAGE)
    branches = proof['provider_schema']['properties']['decisions']['properties']['p000000']['oneOf']
    assert all('uniqueItems' not in branch['properties']['business_keys'] for branch in branches)
    assert 'contains' in branches[2]['properties']['questions'], 'old captures must reconstruct their old request'
    subset_proof = native.schema_shape_proof(schema, native.STAGE, native_schema_variant=VARIANT)
    assert subset_proof['local_schema_hash'] == proof['local_schema_hash']
    assert subset_proof['provider_schema_hash'] != proof['provider_schema_hash']
    assert subset_proof['native_policy_hash'] != proof['native_policy_hash']
    assert subset_proof['schema_policy']['native_schema_variant'] == VARIANT


def test_preparation_freezes_the_new_cap_and_rebuilds_each_dialect_without_selector_in_http():
    cap, source, kwargs, _ = scene()
    old = preparation.build_preparation(cap, source, **kwargs)
    newer = copy.deepcopy(cap)
    newer.data['execution']['native_schema_variant'] = VARIANT
    current = preparation.build_preparation(newer, source, **kwargs)
    assert current['generation_contract']['output_schema'] == old['generation_contract']['output_schema']
    assert current['generation_contract']['model_hash'] == digest(newer.data)
    assert current['generation_contract']['input_policy']['native_schema_variant'] == VARIANT
    assert 'native_schema_variant' not in old['generation_contract']['input_policy']
    assert current['generation_contract']['schema_proof']['schema_policy']['native_schema_variant'] == VARIANT
    assert current['request_hash'] != old['request_hash']
    assert preparation.stage_spec(current) == current['generation_contract']
    assert preparation.stage_spec(old) == old['generation_contract']
    for value, frozen_cap in ((old, cap), (current, newer)):
        rebuilt = preparation.rebuild_request(value, frozen_cap, source, **kwargs)
        assert rebuilt['request_hash'] == value['request_hash']
        assert 'native_schema_variant' not in rebuilt['request']
        assert b'native_schema_variant' not in rebuilt['request_body']
    # A current model edit cannot silently requalify a frozen new preparation.
    newer.data['execution'].pop('native_schema_variant')
    assert preparation.stage_spec(current) == current['generation_contract']
    with pytest.raises(ValueError):
        preparation.check_preparation(current, newer, source, **kwargs)


def test_a_resealed_mixed_frozen_policy_cannot_select_a_different_provider_request():
    cap, source, kwargs, _ = scene()
    cap.data['execution']['native_schema_variant'] = VARIANT
    value = preparation.build_preparation(cap, source, **kwargs)
    broken = copy.deepcopy(value)
    broken['generation_contract']['input_policy'].pop('native_schema_variant')
    broken['generation_contract_hash'] = digest(broken['generation_contract'])
    broken['preparation_hash'] = digest({key: item for key, item in broken.items() if key != 'preparation_hash'})
    with pytest.raises(ValueError):
        preparation.stage_spec(broken)
