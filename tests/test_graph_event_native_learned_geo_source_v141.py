"""Pure closed-format checks; these fixtures are not database authority.

Root separately executes the real installer/CPU/reception/eligibility/admission
journey. No test here treats a self-consistent hash as an actual Run or grant.
"""
import copy

import pytest

from generic_agents.contracts import digest
from generic_agents import graph_event_native_learned_geo_sources_v141 as source
from generic_agents.local_changer_install import PACK, RECIPE_HASH, PIPELINE_HASH, RECIPE


def identity(number):
    # Canonical UUIDs deliberately do not impose a version/variant restriction.
    return f'00000000-0000-7000-8000-{number:012x}'


def seal(value, key):
    value[key] = digest({name: item for name, item in value.items() if name != key})
    return value


def contract_fixture():
    trace = {'installation_id': identity(3), 'pack_id': PACK, 'generation': 1,
        'recipe_hash': RECIPE_HASH, 'pipeline_hash': PIPELINE_HASH,
        'weights_sha256': RECIPE['files'][0]['sha256'], 'backend': 'cpu'}
    output = source.output_schema()
    return seal({'schema': 'graph-event-native-learned-geo-source-contract-v1',
        'adapter_id': 'native-learned-binary-change-v1', 'event_kind': 'observation.data',
        'correlation_kind': 'entity_id', 'instance_id': identity(1), 'source_id': identity(2),
        'source_hash': digest('actual source is checked elsewhere'), 'source_release_version': '1.0.0',
        'run_type': 'GeoPair', 'request_kind': 'learned_binary_change', 'capability_id': identity(4),
        'capability_hash': digest('actual registered capability is checked elsewhere'), 'capability_release_version': '1.0.0',
        'model_trace': trace, 'model_trace_hash': digest(trace), 'probability_threshold': .5,
        'alignment': 'same_grid', 'runtime_hash': PIPELINE_HASH,
        'result_schema': copy.deepcopy(source.RESULT_SCHEMA), 'output_schema': output,
        'output_schema_hash': digest(output)}, 'contract_hash')


def projection_fixture(contract=None):
    contract = contract or contract_fixture()
    proof = {'schema': 'graph-event-native-learned-geo-proof-v1', 'workspace_id': identity(5),
        'adapter_id': 'native-learned-binary-change-v1', 'event_kind': 'observation.data',
        'event_key': 'data:' + identity(6), 'source_event_base_hash': digest('original processing envelope'),
        'observation_id': identity(6), 'source_record_id': 'GeoPair:' + identity(7) + ':' + identity(8),
        'processing_version': 'native-v1', 'instance_id': contract['instance_id'],
        'source_id': contract['source_id'], 'source_hash': contract['source_hash'],
        'source_release_version': contract['source_release_version'], 'entity_id': identity(8),
        'capability_id': contract['capability_id'], 'capability_hash': contract['capability_hash'],
        'capability_release_version': contract['capability_release_version'],
        'run_id': identity(7), 'run_generation': 1, 'run_request_id': digest('first invocation scope'),
        'run_request_hash': digest('original complete learned request'),
        'run_machine_hash': digest('original machine payload excludes later human work'),
        'run_result_hash': digest('actual full eight-artifact result'),
        'model_trace_hash': contract['model_trace_hash'], 'runtime_hash': contract['runtime_hash'],
        'model_ready_audit_id': identity(9), 'model_ready_audit_hash': digest('original ready Audit'),
        'processing_job_id': identity(10), 'processing_actor_id': identity(11),
        'processing_job_hash': digest('original job identity'), 'receipt_actor_id': identity(12),
        'receipt_audit_id': identity(13), 'receipt_audit_hash': digest('original reception Audit'),
        'completion_audit_id': identity(14), 'completion_audit_hash': digest('original completion Audit'),
        'input_audit_id': identity(15), 'input_anchor_hash': digest('original input Audit pins call and original lease'),
        'result_audit_id': identity(16), 'result_anchor_hash': digest('original result Audit'),
        'issued_audit_id': identity(17), 'issued_anchor_hash': digest('original issued Audit'),
        'eligibility_audit_id': identity(18), 'eligibility_hash': digest('actual first-input eligibility Audit'),
        'registration_audit_id': identity(19), 'registration_hash': digest('original registration Audit'),
        'workflow_task_id': identity(20), 'release_id': identity(21), 'configuration_hash': digest('original release'),
        'business_id': 'original-business', 'object_snapshot_hash': digest('original member object'),
        'comparison_snapshot_hash': digest('original ordered A and B snapshots'), 'sources_hash': digest('original sources'),
        'workflow_receipt_hash': digest('original automatic workflow receipt'),
        'invocation_id': identity(22), 'invocation_hash': digest('original successful call and lease'),
        'memory_id': identity(23), 'memory_payload_hash': digest('actual unique received candidate Memory'),
        'data_reception_hash': digest('full original modern reception'),
        'evidence': [{'id': identity(24), 'sha256': digest('A original bytes')},
            {'id': identity(25), 'sha256': digest('B original bytes')}],
        'artifacts': {kind: {'id': identity(index), 'sha256': digest(kind)} for index, kind in enumerate(
            ['before', 'after', 'valid', 'probability', 'candidate', 'probability_data', 'probability_raster', 'change_mask'], 30)},
        'native_packet_hash': digest('unchanged original native packet')}
    output = {'schema': 'graph-event-native-learned-geo-output-v1', 'adapter_id': proof['adapter_id'],
        'observation_id': proof['observation_id'], 'entity_id': proof['entity_id'], 'source_id': proof['source_id'],
        'run_id': proof['run_id'], 'task_id': proof['workflow_task_id'], 'business_day': '2026-10-20',
        'date_basis': 'A_original_source_request', 'observed_at': None, 'candidate_only': True,
        'data_received': True, 'source_time_verified': False, 'physical_state_verified': False,
        'entity_state_updated': False, 'candidate_pixels': 9, 'unknown_pixels': 0,
        'result': {'attributes': {'差异候选像素数': 9, '未知像素数': 0}}}
    return seal({'schema': 'graph-event-native-learned-geo-projection-v1', 'source_event_id': identity(26),
        'observation_id': proof['observation_id'], 'source_key': 'native-learned-geo:' + digest({
            'adapter_id': proof['adapter_id'], 'run_id': proof['run_id'], 'entity_id': proof['entity_id'],
            'machine_hash': proof['run_machine_hash']}), 'correlation': proof['entity_id'],
        'output': output, 'proof': proof}, 'projection_hash')


def test_contract_has_exact_family_pins_and_returns_detached_copy():
    original = contract_fixture(); before = copy.deepcopy(original)
    checked = source.check_contract(original)
    assert checked == original and len(checked) == 22
    assert source.registration_fields(original) == {key: original[key] for key in
        ('adapter_id', 'instance_id', 'source_id', 'source_hash', 'capability_id', 'capability_hash')}
    checked['model_trace']['generation'] = 2
    checked['output_schema']['properties']['observed_at']['type'] = 'string'
    assert original == before


@pytest.mark.parametrize('field,value', [
    ('schema', 'graph-event-native-audio-source-contract-v1'), ('adapter_id', 'native-audio-transcription-v1'),
    ('run_type', 'GeoRun'), ('request_kind', 'rgb_difference'), ('event_kind', 'perception.anomaly'),
    ('correlation_kind', 'source_id'), ('probability_threshold', True), ('probability_threshold', 0),
    ('probability_threshold', 1), ('probability_threshold', float('nan')), ('probability_threshold', float('inf')),
    ('alignment', 'resize_to_fit'), ('runtime_hash', 'a' * 64), ('capability_id', 'NOT-A-UUID')])
def test_contract_rejects_other_families_and_invalid_pins_even_when_resealed(field, value):
    contract = contract_fixture(); contract[field] = value
    if isinstance(value, float) and not (value == value and abs(value) != float('inf')):
        # Non-finite numbers are rejected before they can acquire a valid digest.
        with pytest.raises((ValueError, TypeError)):
            source.check_contract(contract)
        return
    seal(contract, 'contract_hash')
    with pytest.raises((ValueError, TypeError)):
        source.check_contract(contract)


@pytest.mark.parametrize('change', ['missing', 'extra', 'model_generation_bool', 'wrong_weights', 'legacy_installation', 'open_result'])
def test_contract_closed_shapes_and_model_trace_cannot_silently_downgrade(change):
    contract = contract_fixture()
    if change == 'missing': del contract['capability_hash']
    elif change == 'extra': contract['extractor_id'] = identity(40)
    elif change == 'model_generation_bool': contract['model_trace']['generation'] = True
    elif change == 'wrong_weights': contract['model_trace']['weights_sha256'] = 'f' * 64
    elif change == 'legacy_installation': del contract['model_trace']['pipeline_hash']
    else: contract['result_schema']['properties']['attributes']['additionalProperties'] = True
    contract['model_trace_hash'] = digest(contract['model_trace'])
    seal(contract, 'contract_hash')
    with pytest.raises((ValueError, TypeError)):
        source.check_contract(contract)


def test_received_candidate_projection_is_detached_and_exposes_only_the_two_machine_metrics():
    contract = contract_fixture(); value = projection_fixture(contract); before = copy.deepcopy(value)
    checked = source.check_projection(value, contract)
    assert checked == value
    assert checked['output']['observed_at'] is None and checked['output']['data_received'] is True
    assert checked['proof']['receipt_actor_id'] != checked['proof']['processing_actor_id']
    assert set(checked['output']['result']['attributes']) == {'差异候选像素数', '未知像素数'}
    assert checked['output']['candidate_pixels'] == checked['output']['result']['attributes']['差异候选像素数']
    assert checked['output']['unknown_pixels'] == checked['output']['result']['attributes']['未知像素数']
    assert not {'review_state', 'task_version', 'task_status', 'created_at', 'received_at'} & set(checked['proof'])
    checked['proof']['evidence'][0]['sha256'] = '0' * 64
    checked['output']['result']['attributes']['差异候选像素数'] = 99
    assert value == before


@pytest.mark.parametrize('part,field,value', [
    ('output', 'data_received', False), ('output', 'data_received', 1), ('output', 'candidate_only', False),
    ('output', 'source_time_verified', True), ('output', 'physical_state_verified', True),
    ('output', 'entity_state_updated', True), ('output', 'observed_at', '2026-10-20T12:00:00Z'),
    ('output', 'date_basis', 'physical_capture_time'), ('output', 'entity_id', identity(99)),
    ('output', 'candidate_pixels', 10), ('output', 'candidate_pixels', True), ('output', 'unknown_pixels', 0.0),
    ('proof', 'schema', 'graph-event-native-audio-proof-v1'), ('proof', 'event_key', 'legacy:record'),
    ('proof', 'source_record_id', 'AudioRun:' + identity(7) + ':' + identity(8)),
    ('proof', 'run_generation', True), ('proof', 'memory_id', None),
    ('proof', 'eligibility_hash', 'bad'), ('proof', 'invocation_hash', 'bad'),
    ('proof', 'capability_hash', 'b' * 64), ('proof', 'model_trace_hash', 'c' * 64)])
def test_projection_resealed_mutations_fail_closed(part, field, value):
    contract = contract_fixture(); projection = projection_fixture(contract)
    projection[part][field] = value; seal(projection, 'projection_hash')
    with pytest.raises((ValueError, TypeError)):
        source.check_projection(projection, contract)


@pytest.mark.parametrize('change', ['missing_memory', 'missing_eligibility', 'mutable_review', 'legacy_rgb_artifacts',
    'extra_artifact', 'bad_evidence', 'source_key', 'negative_metric', 'boolean_metric', 'float_metric', 'extra_metric'])
def test_projection_rejects_incomplete_native_inventory_or_fact_and_type_disguises(change):
    contract = contract_fixture(); value = projection_fixture(contract)
    if change == 'missing_memory': del value['proof']['memory_payload_hash']
    elif change == 'missing_eligibility': del value['proof']['eligibility_audit_id']
    elif change == 'mutable_review': value['proof']['review_state'] = 'confirmed'
    elif change == 'legacy_rgb_artifacts': del value['proof']['artifacts']['probability_data']
    elif change == 'extra_artifact': value['proof']['artifacts']['fake'] = {'id': identity(90), 'sha256': 'a' * 64}
    elif change == 'bad_evidence': value['proof']['evidence'][0]['sha256'] = 'not-the-original'
    elif change == 'source_key': value['source_key'] = 'native-audio:' + digest('other family')
    elif change == 'negative_metric': value['output']['result']['attributes']['差异候选像素数'] = -1
    elif change == 'boolean_metric': value['output']['result']['attributes']['差异候选像素数'] = True
    elif change == 'float_metric': value['output']['result']['attributes']['差异候选像素数'] = 9.0
    else: value['output']['result']['attributes']['现场变化'] = True
    seal(value, 'projection_hash')
    with pytest.raises((ValueError, TypeError)):
        source.check_projection(value, contract)


def test_original_hash_tampering_is_rejected_without_repairing_the_input():
    contract = contract_fixture(); contract['contract_hash'] = '0' * 64
    projection = projection_fixture(); projection['projection_hash'] = '1' * 64
    for function, value in ((source.check_contract, contract), (source.check_projection, projection)):
        before = copy.deepcopy(value)
        with pytest.raises(ValueError):
            function(value)
        assert value == before
