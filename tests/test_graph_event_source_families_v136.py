"""Synthetic closed wires, never actual Run/registration/execution authority.

Root executes these pure checks and a separate real-PG/local-ASR pipeline.
No production checker or current authorization is patched into accepting data.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents import graph_event_source_families_v136 as families
from generic_agents import graph_event_native_audio_sources_v136 as native
from generic_agents import graph_event_admission as admission
from generic_agents import graph_event_native_producer_requests_v136 as requests
from tests.test_graph_event_observation_sources import contract_fixture, projection_fixture, identity


def native_contract():
    pin = {'installation_id': identity(601), 'pack_id': 'sensevoice-small-v1', 'generation': 1,
        'recipe_hash': 'a' * 64, 'backend': 'cpu'}
    value = {'schema': native.CONTRACT_SCHEMA, 'adapter_id': native.ADAPTER, 'event_kind': 'observation.data',
        'correlation_kind': 'entity_id', 'instance_id': identity(602), 'source_id': identity(603),
        'source_hash': 'b' * 64, 'source_release_version': '1.0.0', 'run_type': 'AudioRun',
        'installation': pin, 'installation_hash': digest(pin), 'result_schema': copy.deepcopy(native.RESULT_SCHEMA),
        'output_schema': native.output_schema()}
    value['output_schema_hash'] = digest(value['output_schema']); value['contract_hash'] = digest(value)
    return native.check_contract(value)


def native_ledger():
    contract = native_contract()
    p = NS(user_id=identity(604), workspace_id=identity(605), role='admin')
    command = {'schema': families.NATIVE_COMMAND, 'request_id': identity(606), 'label': '合成本地语音候选',
        **native.registration_fields(contract)}
    signature = families.native_command_hash(p, command)
    row = NS(id=identity(607), workspace_id=p.workspace_id, registered_by=p.user_id,
        request_id=command['request_id'], version=1, status='active', command_hash=signature,
        contract_hash=digest(contract), data={'schema': families.NATIVE_RECORD, 'label': command['label'],
            'contract': contract, 'contract_hash': digest(contract), 'registration': {'request_id': command['request_id'],
                'actor_id': p.user_id, 'command_hash': signature, 'command': command}})
    return p, row


def native_projection(contract):
    # This only constructs the pure wire. Actual rows/audits/files are covered
    # by Root's independent pipeline and are not supplied by this fixture.
    proof = {key: 'c' * 64 for key in native._PROOF}
    uuid_keys = ('workspace_id', 'observation_id', 'instance_id', 'source_id', 'entity_id', 'run_id',
        'processing_job_id', 'processing_actor_id', 'receipt_actor_id', 'receipt_audit_id', 'completion_audit_id',
        'input_audit_id', 'result_audit_id', 'workflow_task_id', 'release_id', 'invocation_id')
    for index, key in enumerate(uuid_keys): proof[key] = identity(700 + index)
    proof.update(schema=native.PROOF_SCHEMA, adapter_id=native.ADAPTER, event_kind='observation.data',
        instance_id=contract['instance_id'], source_id=contract['source_id'], source_hash=contract['source_hash'],
        source_release_version=contract['source_release_version'], installation_hash=contract['installation_hash'],
        run_generation=1, run_request_id='synthetic-native-request', processing_version='synthetic-native-v136',
        business_id='business_1234567890abcdef')
    proof['event_key'] = 'data:' + proof['observation_id']
    proof['source_record_id'] = 'AudioRun:' + proof['run_id'] + ':' + proof['entity_id']
    proof['evidence'] = [{'id': identity(750), 'sha256': 'd' * 64}]
    output = {'schema': native.OUTPUT_SCHEMA, 'adapter_id': native.ADAPTER,
        'observation_id': proof['observation_id'], 'entity_id': proof['entity_id'], 'source_id': proof['source_id'],
        'run_id': proof['run_id'], 'task_id': proof['workflow_task_id'], 'business_day': '2026-10-20',
        'date_basis': 'original_audio_upload_request', 'observed_at': None, 'candidate_only': True,
        'source_time_verified': False, 'physical_state_verified': False, 'entity_state_updated': False,
        'result': {'attributes': {'已执行整片转写': True, '机器转写文字': '合成候选，不代表正确或现场事实'}}}
    value = {'schema': native.PROJECTION_SCHEMA, 'source_event_id': identity(751),
        'observation_id': proof['observation_id'], 'source_key': 'native-audio:' + digest({'adapter_id': native.ADAPTER,
            'run_id': proof['run_id'], 'entity_id': proof['entity_id'], 'machine_hash': proof['run_machine_hash']}),
        'correlation': proof['entity_id'], 'output': output, 'proof': proof}
    value['projection_hash'] = digest(value)
    return native.check_projection(value, contract)


def test_processing_wire_and_hash_are_unchanged_and_separate_from_native():
    old = contract_fixture(); projection = projection_fixture(old)
    assert families.check_contract(old) == old and families.check_projection(projection, old) == projection
    p = NS(user_id=identity(100), workspace_id=identity(101))
    command = {'schema': 'graph-event-producer-command-v1', 'request_id': identity(102), 'label': '原处理族',
        **families.registration_fields(old)}
    assert admission.producer_command_hash(p, command) == digest({'schema': 'graph-event-producer-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'command': command})
    with pytest.raises(ValueError): families.check_projection(projection, native_contract())


def test_native_registration_has_real_installation_pin_and_no_extractor():
    p, row = native_ledger(); before = copy.deepcopy(row.data)
    checked = admission.check_producer(row)
    assert families.record_family(row) == families.NATIVE_AUDIO
    assert 'extractor_id' not in checked and 'extractor_id' not in row.data['registration']['command']
    assert row.command_hash == digest({'schema': 'graph-event-native-audio-producer-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'command': row.data['registration']['command']})
    checked['installation']['generation'] += 1
    assert row.data == before
    families.require_instance(row.data['contract'], identity(602))
    with pytest.raises(ValueError): families.require_instance(row.data['contract'], identity(999))


@pytest.mark.parametrize('change', ['record_schema', 'contract_schema', 'extra_extractor', 'installation', 'actor', 'command_hash'])
def test_unknown_mixed_or_resealed_wrong_registration_cannot_select_a_family(change):
    p, row = native_ledger()
    if change == 'record_schema': row.data['schema'] = 'graph-event-producer-record-v1'
    elif change == 'contract_schema': row.data['contract']['schema'] = families.PROCESSING_CONTRACT
    elif change == 'extra_extractor': row.data['registration']['command']['extractor_id'] = identity(880)
    elif change == 'installation': row.data['registration']['command']['installation_hash'] = 'f' * 64
    elif change == 'actor': row.registered_by = identity(881)
    else: row.command_hash = 'e' * 64
    with pytest.raises((HTTPException, ValueError)): admission.check_producer(row)


def test_native_intent_actor_is_actual_receipt_actor_not_original_model_actor():
    _, producer = native_ledger(); projection = native_projection(producer.data['contract'])
    receipt_actor = projection['proof']['receipt_actor_id']; processing_actor = projection['proof']['processing_actor_id']
    assert receipt_actor != processing_actor != producer.registered_by
    frozen = {'schema': 'graph-event-source-intent-v1', 'producer_id': producer.id,
        'contract_hash': producer.contract_hash, 'source_event_id': projection['source_event_id'],
        'observation_id': projection['observation_id'], 'source_key': projection['source_key'],
        'actor_id': receipt_actor, 'projection': projection, 'projection_hash': projection['projection_hash']}
    producer.workspace_id = projection['proof']['workspace_id']
    # Rebind only the synthetic registration workspace and its exact command hash.
    p = NS(user_id=producer.registered_by, workspace_id=producer.workspace_id)
    producer.command_hash = families.native_command_hash(p, producer.data['registration']['command'])
    producer.data['registration']['command_hash'] = producer.command_hash
    frozen['intent_hash'] = digest(frozen)
    row = NS(id=identity(890), workspace_id=producer.workspace_id, producer_id=producer.id,
        source_event_id=projection['source_event_id'], observation_id=projection['observation_id'],
        source_key=projection['source_key'], actor_id=receipt_actor, status='pending', receipt_id=None, version=1, data=frozen)
    assert admission.check_intent(row, producer) == frozen
    row.actor_id = processing_actor; row.data['actor_id'] = processing_actor
    row.data['intent_hash'] = digest({key: value for key, value in row.data.items() if key != 'intent_hash'})
    with pytest.raises(HTTPException): admission.check_intent(row, producer)


@pytest.mark.parametrize('change', ['unknown_schema', 'processing_projection', 'capture_clock', 'physical', 'candidate'])
def test_native_projection_never_falls_back_or_attests_capture_or_physical_state(change):
    contract = native_contract(); value = native_projection(contract)
    if change == 'unknown_schema': value['schema'] = 'graph-event-native-audio-projection-v999'
    elif change == 'processing_projection': value['schema'] = 'graph-event-observation-projection-v1'
    elif change == 'capture_clock': value['output']['observed_at'] = '2026-10-20T00:00:00+00:00'
    elif change == 'physical': value['output']['physical_state_verified'] = True
    else: value['output']['candidate_only'] = False
    value['projection_hash'] = digest({key: child for key, child in value.items() if key != 'projection_hash'})
    with pytest.raises((HTTPException, ValueError)): families.check_projection(value, contract)


@pytest.mark.parametrize('kind', ['register', 'revoke'])
def test_native_retirement_is_independent_exact_original_and_cold_detached(kind):
    p, producer = native_ledger(); instance_id = producer.data['contract']['instance_id']
    original = copy.deepcopy(producer.data['registration']['command'])
    if kind == 'revoke': original = {'schema': 'graph-event-native-audio-producer-revocation-v1',
        'request_id': identity(901), 'instance_id': instance_id, 'producer_id': producer.id,
        'producer_version': 1, 'contract_hash': producer.contract_hash, 'reason': '明确停止供应新结果'}
    command = {'schema': 'graph-event-native-audio-source-retirement-v1', 'request_id': identity(902),
        'instance_id': instance_id, 'kind': kind, 'original': original, 'reason': '原操作未保存，明确结束'}
    assert requests.Retirement.model_validate(command).model_dump(mode='json') == command
    signature = requests.original_hash(p, kind, original)
    row = NS(id=identity(903), workspace_id=p.workspace_id, actor_id=p.user_id, instance_id=instance_id,
        producer_id=None, kind='retire', request_id=command['request_id'], version=1,
        original_kind=kind, original_request_id=original['request_id'], original_hash=signature,
        command_hash=requests.retirement_hash(p, command), data={'command': command, 'result': {
            'outcome': 'retired', 'kind': kind, 'original_request_id': original['request_id'], 'original_command_hash': signature}})
    before = copy.deepcopy(row.data); receipt = requests.checked_receipt(row, p, instance_id, kind='retire')
    receipt['command']['reason'] = '改变回包'
    assert row.data == before
    with pytest.raises(HTTPException): requests.checked_receipt(row, p, identity(999), kind='retire')
    row.data['command']['original']['schema'] = 'graph-event-producer-command-v1'
    with pytest.raises((HTTPException, ValueError)): requests.checked_receipt(row, p, instance_id, kind='retire')
