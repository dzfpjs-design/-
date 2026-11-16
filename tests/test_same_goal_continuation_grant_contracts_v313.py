"""Real pure contracts/manifest/JSON Schema checks; no registry, PG or SDK."""
import copy

import pytest
from jsonschema import Draft202012Validator

from generic_agents import same_goal_continuation_grant_contracts_v313 as c
from generic_agents.contracts import Manifest, digest
from tests.test_same_goal_advice_contracts_v302 import ref


def request():
    return {'request_id': ref('grant-request')['id'], 'expected_task_version': 3,
        'expected_continuation_version': 2, 'revision_hash': 'a' * 64, 'source_hash': 'b' * 64,
        'capability_pin': {'id': ref('grant-tool')['id'], 'content_hash': 'c' * 64, 'release_version': '1.0.0'},
        'field': 'temperature', 'operation': 'mean', 'policy_ref': {'policy_id': ref('grant-policy')['id'],
            'version_id': ref('grant-policy-version')['id'], 'revision': 1, 'record_hash': 'd' * 64},
        'validity_seconds': 7200, 'reason': '明确授权原草案用已核资料计算，不批准执行'}


@pytest.mark.parametrize('binding', c.BINDINGS)
def test_factory_is_a_complete_registrable_exact_readonly_manifest(binding):
    value = c.manifest(binding)
    assert c.validate_manifest(value) == Manifest.model_validate(value).model_dump(mode='json') == value
    assert c.applies(value) and c.applies(Manifest.model_validate(value))
    assert value['allowed_agent_roles'] == ['data']
    assert value['execution'] == {'adapter': 'builtin', 'handler': 'aggregate_values',
        'dispatch_scope': c.SCOPE, 'side_effect': False, 'input_bindings': {'records': binding}}
    assert value['data_policy'] == {'cloud_allowed': False, 'external_allowed': False}
    assert value['dependencies'] == [] and value['budget'] == {'max_attempts': 1, 'max_concurrency': 1}


@pytest.mark.parametrize('mutation', ['handler', 'adapter', 'role', 'side_effect', 'boolean_coercion',
    'cloud', 'external', 'bindings_none', 'bindings_list', 'client_records', 'scope', 'spec', 'schema', 'dependency'])
def test_reserved_facet_rejects_every_nonfixed_provider_or_authority(mutation):
    value = c.manifest()
    if mutation == 'handler': value['execution']['handler'] = 'workflow_task_context_v196'
    elif mutation == 'adapter': value['execution']['adapter'] = 'workflow_service'
    elif mutation == 'role': value['allowed_agent_roles'] = ['business']
    elif mutation == 'side_effect': value['execution']['side_effect'] = True
    elif mutation == 'boolean_coercion': value['execution']['side_effect'] = 0
    elif mutation == 'cloud': value['data_policy']['cloud_allowed'] = True
    elif mutation == 'external': value['data_policy']['external_allowed'] = True
    elif mutation == 'bindings_none': value['execution']['input_bindings'] = None
    elif mutation == 'bindings_list': value['execution']['input_bindings'] = []
    elif mutation == 'client_records': value['execution']['input_bindings'] = {}
    elif mutation == 'scope': value['execution']['dispatch_scope'] = 'registered_tool_plan_v201'
    elif mutation == 'spec': value['specification']['schema'] = 'unrecognized'
    elif mutation == 'schema': value['input_schema']['additionalProperties'] = True
    else: value['dependencies'] = [ref('unrelated-dependency')['id']]
    assert c.applies(value)
    with pytest.raises(ValueError): c.validate_manifest(value)


@pytest.mark.parametrize('witness', ['scope', 'spec', 'namespace', 'name'])
def test_one_surviving_reserved_witness_cannot_downgrade_to_generic(witness):
    fixed = c.manifest(); value = {'execution': {}, 'specification': {}, 'namespace': 'other', 'name': 'other'}
    if witness == 'scope': value['execution']['dispatch_scope'] = c.SCOPE
    elif witness == 'spec': value['specification']['schema'] = c.MANIFEST_SCHEMA
    elif witness == 'namespace': value['namespace'] = c.NAMESPACE
    else: value['name'] = fixed['name']
    assert c.applies(value)
    with pytest.raises(ValueError): c.validate_manifest(value)


@pytest.mark.parametrize('value', [None, [], {'name': []}, {'name': {}}, {'execution': None}])
def test_unrelated_malformed_probe_does_not_crash_reserved_discriminator(value):
    assert c.applies(value) is False


def test_inputs_and_real_aggregate_output_are_closed_and_missing_is_distinct():
    entity = ref('data-entity')['id']
    inputs = {'field': 'temperature', 'operation': 'mean', 'records': [
        {'entity_id': entity, 'version': 2, 'attributes': {'temperature': 24}},
        {'entity_id': entity, 'version': 3, 'attributes': {}}]}
    Draft202012Validator(c.input_schema()).validate(inputs)
    output = {'field': 'temperature', 'operation': 'mean', 'value': 24, 'sample_count': 1,
        'missing_count': 1, 'method': 'registered_archive_aggregate',
        'provenance': [{'entity_id': entity, 'version': row['version']} for row in inputs['records']]}
    Draft202012Validator(c.output_schema()).validate(output)
    for bad in [{**inputs, 'result': 24}, {**inputs, 'records': []}]:
        assert list(Draft202012Validator(c.input_schema()).iter_errors(bad))


def test_closed_issue_revoke_are_distinct_and_preserve_original_human_text():
    value = request(); value['reason'] = '  原文字\n保留，不是执行批准  '
    assert c.dump(c.IssueRequest, value) == value
    revoke = {'request_id': value['request_id'], 'expected_grant_version': 1, 'reason': '撤销未执行授权'}
    assert c.dump(c.RevokeRequest, revoke) == revoke
    with pytest.raises(ValueError): c.dump(c.RevokeRequest, value)
    with pytest.raises(ValueError): c.dump(c.IssueRequest, revoke)
    assert all(flag is False for flag in c.FLAGS.values())


@pytest.mark.parametrize('field,value', [('records', []), ('entity_ids', []), ('result', 0),
    ('operation', 'median'), ('field', 'x' * 101), ('field', ' '), ('reason', '\n'),
    ('reason', 'x' * 2001), ('validity_seconds', True), ('validity_seconds', 7200.0),
    ('validity_seconds', 7201), ('expected_task_version', True), ('capability_pin', {'id': 'bad'})])
def test_issue_cannot_supply_records_results_authority_or_coerced_limits(field, value):
    body = request(); body[field] = value
    with pytest.raises(ValueError): c.dump(c.IssueRequest, body)


def test_hash_includes_all_actual_bytes_except_its_own_slot():
    original = request(); value = {**original, 'record_hash': '0' * 64}
    assert c.record_hash(value) == digest(original)
    changed = copy.deepcopy(value); changed['reason'] += ' '
    assert c.record_hash(changed) != c.record_hash(value)
