"""Synthetic pure contracts are not database/source authorization fixtures."""
import copy
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from generic_agents.contracts import digest
from generic_agents import graph_event_observation_sources as sources


def identity(number):
    return str(UUID(int=number))


def contract_fixture(adapter_id='processed-observation-v1', result_schema=None):
    """Valid pure wire, never a substitute for build_contract's actual DB checks."""
    result_schema = copy.deepcopy(result_schema if result_schema is not None else {
        'type': 'object', 'additionalProperties': False, 'required': ['attributes', 'anomalies'],
        'properties': {'attributes': {'type': 'object', 'additionalProperties': False,
            'required': ['count'], 'properties': {'count': {'type': 'integer'}}},
            'anomalies': {'type': 'array', 'items': {'type': 'string'}}}})
    value = {'schema': sources.CONTRACT_SCHEMA, 'adapter_id': adapter_id,
        'event_kind': sources.ADAPTERS[adapter_id], 'correlation_kind': 'entity_id',
        'source_id': identity(1), 'source_hash': digest({'source': 'synthetic'}), 'source_release_version': '1.2.0',
        'extractor_id': identity(2), 'extractor_hash': digest({'extractor': 'synthetic'}), 'extractor_release_version': '3.0.1',
        'result_schema': result_schema, 'output_schema': sources._output_schema(result_schema, adapter_id)}
    value['output_schema_hash'] = digest(value['output_schema'])
    value['contract_hash'] = digest(value)
    return sources.check_contract(value)


def projection_fixture(contract=None, result=None):
    """A consistent machine-shaped pure value, without claiming an actual receipt."""
    contract = contract_fixture() if contract is None else copy.deepcopy(contract)
    result = copy.deepcopy(result if result is not None else {'attributes': {'count': 2},
        'anomalies': ['需核查'] if contract['adapter_id'] == 'anomaly-candidate-v1' else []})
    ids = {'workspace_id': identity(3), 'observation_id': identity(4), 'entity_id': identity(5),
           'invocation_id': identity(6), 'processing_job_id': identity(7),
           'processing_actor_id': identity(8), 'invocation_lease_token': identity(9)}
    output = {'schema': sources.OUTPUT_SCHEMA, 'adapter_id': contract['adapter_id'],
        **{key: ids[key] for key in ('observation_id', 'entity_id', 'invocation_id')},
        'source_id': contract['source_id'], 'extractor_id': contract['extractor_id'],
        'observed_at': '2026-10-19T10:00:00+00:00', 'candidate_only': False, 'result': result,
        'source_time_verified': False, 'physical_state_verified': False}
    inputs = {'attributes': {'count': 2}}
    original = {'source_id': contract['source_id'], 'source_record_id': 'reported-001',
        'processing_version': 'v1', 'entity_id': ids['entity_id'], 'business_day': '2026-10-19',
        'observed_at': output['observed_at'], 'extractor_id': contract['extractor_id'], **inputs}
    receipt = {'adapter': 'synthetic-typed-test', 'physical_state_verified': False}
    event_key = ('data:' if contract['event_kind'] == 'observation.data' else 'anomaly:') + ids['observation_id']
    base = {'observation_id': ids['observation_id'], 'entity_id': ids['entity_id'], 'source_id': contract['source_id'],
        'invocation_id': ids['invocation_id'], 'evidence_ids': [], 'model_receipt': receipt,
        'observed_at': output['observed_at'], 'candidate_only': False, 'extracted': result}
    invocation_key = 'observation:' + ids['observation_id'] + ':attempt:1'
    invocation = {'id': ids['invocation_id'], 'capability_id': contract['extractor_id'], 'idempotency_key': invocation_key,
        'input_hash': digest(inputs), 'role': 'perception', 'capability_hash': contract['extractor_hash'],
        'job_id': ids['processing_job_id'], 'lease_token': ids['invocation_lease_token'], 'output': result, 'model_receipt': receipt}
    job = {'id': ids['processing_job_id'], 'kind': 'process_observation', 'idempotency_key': 'extract:' + ids['observation_id'],
        'observation_id': ids['observation_id'], 'extractor_id': contract['extractor_id'], 'actor_id': ids['processing_actor_id']}
    machine = {'source_id': contract['source_id'], 'record': original['source_record_id'], 'processing_version': original['processing_version'],
        'entity_id': ids['entity_id'], 'observed_at': output['observed_at'], 'business_day': original['business_day'],
        'original_request': original, 'input_hash': digest(original), 'attributes': inputs['attributes'], 'extracted': result,
        'evidence_ids': [], 'invocation_id': ids['invocation_id'], 'extractor_id': contract['extractor_id'],
        'source_hash': contract['source_hash'], 'extractor_hash': contract['extractor_hash']}
    proof = {'schema': sources.PROOF_SCHEMA, **ids, 'adapter_id': contract['adapter_id'], 'event_kind': contract['event_kind'],
        'event_key': event_key, 'source_event_base_hash': digest(base), 'source_record_id': original['source_record_id'],
        'processing_version': original['processing_version'], **{key: contract[key] for key in ('source_id', 'source_hash',
            'source_release_version', 'extractor_id', 'extractor_hash', 'extractor_release_version')},
        'invocation_key': invocation_key, 'invocation_hash': digest(invocation), 'invocation_input_hash': digest(inputs),
        'invocation_output_hash': digest(result), 'processing_job_hash': digest(job), 'original_request_hash': digest(original),
        'evidence': [], 'model_receipt_hash': digest(receipt), 'observation_machine_hash': digest(machine)}
    value = {'schema': sources.PROJECTION_SCHEMA, 'source_event_id': identity(10), 'observation_id': ids['observation_id'],
        'source_key': sources._source_key(contract['adapter_id'], ids['observation_id'], original['processing_version']),
        'correlation': ids['entity_id'], 'output': output, 'proof': proof}
    value['projection_hash'] = digest(value)
    return sources.check_projection(value, contract)


def rehash(value, key):
    value[key] = digest({name: child for name, child in value.items() if name != key})
    return value


def test_contract_exact_schema_registration_projection_and_detached_copies():
    original = contract_fixture()
    checked = sources.check_contract(original)
    assert checked == original and checked is not original
    checked['result_schema']['properties']['attributes']['properties']['count']['type'] = 'string'
    assert original['result_schema']['properties']['attributes']['properties']['count']['type'] == 'integer'
    assert sources.registration_fields(original) == {key: original[key] for key in
        ('adapter_id', 'source_id', 'source_hash', 'extractor_id', 'extractor_hash')}
    projection = projection_fixture(original)
    copied = sources.check_projection(projection, original)
    copied['output']['result']['attributes']['count'] = 4
    assert projection['output']['result']['attributes']['count'] == 2
    assert projection['correlation'] == projection['proof']['entity_id']
    assert len(projection['source_key']) < 200


@pytest.mark.parametrize('key,value,code', [
    ('extra', True, 'contract_shape'), ('adapter_id', 'native-receipt-v1', 'contract_adapter'),
    ('adapter_id', [], 'contract_adapter'), ('event_kind', 'perception.reviewed', 'contract_event'),
    ('correlation_kind', 'task_id', 'contract_event'), ('source_id', 'foreign', 'uuid'),
    ('source_hash', 'F' * 64, 'hash'), ('extractor_release_version', 3, 'release_version')])
def test_contract_closed_and_controlled_adapters_even_after_rehash(key, value, code):
    contract = contract_fixture(); contract[key] = value; rehash(contract, 'contract_hash')
    with pytest.raises(ValueError, match=code): sources.check_contract(contract)


def test_contract_cannot_redefine_envelope_or_impersonate_source_as_extractor():
    contract = contract_fixture()
    contract['output_schema']['additionalProperties'] = True
    contract['output_schema_hash'] = digest(contract['output_schema']); rehash(contract, 'contract_hash')
    with pytest.raises(ValueError, match='output_schema'): sources.check_contract(contract)
    contract = contract_fixture(); contract['extractor_id'] = contract['source_id']; rehash(contract, 'contract_hash')
    with pytest.raises(ValueError, match='contract_identity'): sources.check_contract(contract)


@pytest.mark.parametrize('schema', [
    {'$ref': 'https://invalid.example/model.json'}, {'$ref': '#named'}, {'$ref': '#/missing'},
    {'$id': 'https://invalid.example/model.json'}, {'$dynamicRef': '#node'},
    {'$defs': {'x': {'type': 'integer'}}, '$ref': '#/$defs/x~2bad'}])
def test_schema_never_resolves_external_or_ambiguous_resources(schema):
    with pytest.raises(ValueError, match='schema_'): contract_fixture(result_schema=schema)


def test_local_schema_refs_rebased_while_original_schema_is_exact():
    result_schema = {'type': 'object', 'additionalProperties': False, 'required': ['attributes'],
        '$defs': {'measure': {'type': 'integer'}},
        'properties': {'attributes': {'type': 'object', 'properties': {'count': {'$ref': '#/$defs/measure'}},
            'required': ['count'], 'additionalProperties': False}}}
    contract = contract_fixture(result_schema=result_schema)
    assert contract['result_schema'] == result_schema
    assert contract['output_schema']['$defs']['extractor_result']['properties']['attributes']['properties']['count']['$ref'] == '#/$defs/extractor_result/$defs/measure'
    projection_fixture(contract, {'attributes': {'count': 2}})
    with pytest.raises(ValueError, match='output_validation'):
        projection_fixture(contract, {'attributes': {'count': True}})


@pytest.mark.parametrize('mutate,code', [
    (lambda p: p.update(extra='grant'), 'projection_shape'),
    (lambda p: p.update(correlation=identity(99)), 'projection_identity'),
    (lambda p: p.update(source_key='observation:anything'), 'source_key'),
    (lambda p: p['proof'].update(event_key='data:wrong'), 'event_key'),
    (lambda p: p['proof'].update(processing_actor_id='system'), 'uuid'),
    (lambda p: p['output'].update(source_id=identity(99)), 'output_identity'),
    (lambda p: p['output'].update(physical_state_verified=True), 'fact_boundary'),
    (lambda p: p['output'].update(source_time_verified=0), 'fact_boundary'),
    (lambda p: p['output'].update(candidate_only=1), 'fact_boundary'),
    (lambda p: p['output'].update(observed_at='2026-10-19T10:00:00'), 'observed_at'),
    (lambda p: p['output'].update(observed_at=None), 'observed_at'),
    (lambda p: p['proof'].update(evidence=[{'id': identity(1), 'sha256': 'a' * 64}] * 2), 'duplicate_evidence')])
def test_projection_rehashed_tampering_is_closed_and_correlated(mutate, code):
    contract = contract_fixture(); value = projection_fixture(contract)
    mutate(value); rehash(value, 'projection_hash')
    with pytest.raises(ValueError, match=code): sources.check_projection(value, contract)


def test_projection_hash_and_typed_result_required():
    contract = contract_fixture(); value = projection_fixture(contract)
    value['output']['result']['attributes']['count'] = 3
    with pytest.raises(ValueError, match='projection_hash'): sources.check_projection(value, contract)
    rehash(value, 'projection_hash')
    with pytest.raises(ValueError, match='result_hash'): sources.check_projection(value, contract)
    value['proof']['invocation_output_hash'] = digest(value['output']['result']); rehash(value, 'projection_hash')
    assert sources.check_projection(value, contract)['output']['result']['attributes']['count'] == 3
    value['output']['result']['attributes']['count'] = '3'
    value['proof']['invocation_output_hash'] = digest(value['output']['result']); rehash(value, 'projection_hash')
    with pytest.raises(ValueError, match='output_validation'): sources.check_projection(value, contract)


def test_candidate_time_is_explicitly_unverified_not_inferred():
    contract = contract_fixture(); value = projection_fixture(contract)
    value['output'].update(candidate_only=True, observed_at=None); rehash(value, 'projection_hash')
    checked = sources.check_projection(value, contract)
    assert checked['output']['source_time_verified'] is False and checked['output']['physical_state_verified'] is False


def test_anomaly_requires_nonempty_actual_candidates_and_keeps_fact_boundary():
    contract = contract_fixture('anomaly-candidate-v1')
    value = projection_fixture(contract)
    assert value['output']['result']['anomalies'] == ['需核查']
    with pytest.raises(ValueError, match='no_anomaly_candidate'):
        projection_fixture(contract, {'attributes': {'count': 2}, 'anomalies': []})
    assert value['output']['physical_state_verified'] is False
    assert value['source_key'] != projection_fixture()['source_key']


def test_historical_projection_contract_is_still_pinned_and_never_calls_database():
    contract = contract_fixture(); value = projection_fixture(contract)
    changed = copy.deepcopy(contract); changed['source_hash'] = digest({'other': 'source'}); rehash(changed, 'contract_hash')
    with pytest.raises(ValueError, match='projection_contract'): sources.check_projection(value, changed)
    assert sources.check_projection(value, contract) == value
    assert sources.check_projection(value) == value


@pytest.mark.parametrize('unsafe', [float('nan'), float('inf'), '\x00', '\ud800', {'not', 'json'}])
def test_non_json_context_cannot_enter_hashes(unsafe):
    contract = contract_fixture(); contract['result_schema']['description'] = unsafe
    with pytest.raises(ValueError, match='graph_event_observation:'): sources.check_contract(contract)


def test_schema_caps_are_rejected_not_truncated():
    schema = {'type': 'object'}
    for _ in range(70): schema = {'allOf': [schema]}
    with pytest.raises(ValueError, match='json_limit'): contract_fixture(result_schema=schema)


def event_rows():
    value = projection_fixture(); output = value['output']
    call = SimpleNamespace(id=output['invocation_id'], data={'model_receipt': {'model': 'receipt'}})
    row = SimpleNamespace(id=output['observation_id'], entity_id=output['entity_id'], source_id=output['source_id'],
        observed_at=datetime.fromisoformat(output['observed_at']), data={'extracted': output['result'],
            'evidence_ids': [], 'model_receipt': call.data['model_receipt'], 'candidate_only': False})
    base = {'observation_id': row.id, 'entity_id': row.entity_id, 'source_id': row.source_id,
        'invocation_id': call.id, 'evidence_ids': [], 'model_receipt': call.data['model_receipt'],
        'observed_at': output['observed_at'], 'candidate_only': False, 'extracted': copy.deepcopy(output['result'])}
    return SimpleNamespace(data=base), row, call


def test_legacy_metadata_extension_is_never_authority_or_part_of_frozen_base():
    event, row, call = event_rows()
    before = sources._base_event(event, row, call)
    event.data.update(correlation=identity(99), output={'physical_state_verified': True}, business_review={'status': 'reviewed'})
    assert sources._base_event(event, row, call) == before
    assert set(before) == sources._BASE_KEYS
    del event.data['invocation_id']
    with pytest.raises(ValueError, match='event_base_missing'): sources._base_event(event, row, call)


def test_event_equality_is_json_exact_including_bool_versus_integer():
    event, row, call = event_rows()
    row.data['extracted']['attributes']['count'] = True
    event.data['extracted']['attributes']['count'] = 1
    assert row.data['extracted'] == event.data['extracted']  # Python equality is insufficient.
    with pytest.raises(ValueError, match='event_base_changed'): sources._base_event(event, row, call)


def test_live_projection_cannot_disable_locks_before_reading_any_database():
    with pytest.raises(ValueError, match='live_lock_required'):
        sources.project_source(None, None, None, None, None, live=True, lock=False)
    with pytest.raises(ValueError, match='live_lock_required'):
        sources.project_source(None, None, None, None, None, live=1)


@pytest.mark.parametrize('change,code', [
    (lambda row: setattr(row, 'status', 'failed'), 'no_processed_invocation'),
    (lambda row: row.data.pop('invocation_id'), 'no_processed_invocation'),
    (lambda row: row.data.update(native_packet={}), 'no_processed_invocation'),
    (lambda row: row.data.update(native_instance_id=identity(30)), 'no_processed_invocation'),
    (lambda row: row.data.pop('original_request'), 'original_request_required')])
def test_same_named_native_or_legacy_event_has_no_processing_receipt(change, code):
    row = SimpleNamespace(status='processed', data={'original_request': {'attributes': {'count': 2}},
        'extracted': {'attributes': {'count': 2}}, 'invocation_id': identity(6)})
    sources._processed_observation(row)
    change(row)
    with pytest.raises(ValueError, match=code): sources._processed_observation(row)


def test_processing_identity_uses_actual_job_actor_and_ignores_later_job_lifecycle():
    p = SimpleNamespace(user_id=identity(8))
    row = SimpleNamespace(id=identity(4), data={'submitted_by': identity(77)})
    extractor = SimpleNamespace(id=identity(2))
    call = SimpleNamespace(data={'lease_token': identity(9)})
    job = SimpleNamespace(id=identity(7), kind='process_observation', idempotency_key='extract:' + row.id,
        status='running', version=1, lease_token=identity(9), data={'observation_id': row.id,
            'extractor_id': extractor.id, 'actor_id': p.user_id})
    before = sources._processing_identity(job, row, extractor, call, p, True)
    job.status = 'succeeded'; job.version = 3; job.lease_token = None
    job.data.update(lease_recoveries=1, legacy_reception={'done': True})
    assert sources._processing_identity(job, row, extractor, call, p, True) == before
    job.data['actor_id'] = identity(99)
    with pytest.raises(ValueError, match='processing_actor_mismatch'):
        sources._processing_identity(job, row, extractor, call, p, True)
    job.data['actor_id'] = p.user_id; job.data['observation_id'] = identity(99)
    with pytest.raises(ValueError, match='processing_job_mismatch'):
        sources._processing_identity(job, row, extractor, call, p, True)
