from datetime import datetime, timezone
from types import SimpleNamespace as N
import copy

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents import native_pixel_provenance_v250 as subject
from generic_agents import native_pixel_data_plan_v250 as design
from tests.test_native_pixel_data_plan_v250 import snapshot


def scene():
    compiled = snapshot(); compiled[design.KEY] = design.build(compiled)
    run = N(id='run', workspace_id='workspace', task_id='task', capability_id='cap-1', job_id='job',
        invocation_id='invocation', owner_id='producer', data={'instance_id': 'instance', 'release_id': 'release',
        'business_id': 'business-opaque-7', 'capability_hash': 'a' * 64,
        'request_hash': 'b' * 64, 'result_hash': 'c' * 64, 'output': {'pixel_run_id': 'run'},
        'sources_hash': 'd' * 64, 'model_trace_hash': 'e' * 64, 'result': {'artifacts': [
            {'kind': kind, 'evidence_id': kind, 'sha256': 'f' * 64, 'size_bytes': 10, 'media_type': 'image/png'}
            for kind in design.ARTIFACTS]}, 'sources': {'before': {'id': 'before'}, 'after': {'id': 'after'}}})
    context = {'run': run, 'task': N(id='task', data={'entity_ids': ['entity']}),
        'release': N(data={'snapshot': compiled}), 'job': N(status='running'),
        'input_audit': N(id='input'), 'result_audit': N(id='result'), 'completion_audit': N(id='complete'),
        'settlement_audit': None}
    observation = N(id='observation', entity_id='entity', source_id='source',
        created_at=datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc))
    return context, N(id='source'), observation


def test_registration_day_is_real_issuance_day_not_upload_capture_or_ack():
    context, source, observation = scene()
    packet = subject.packet(context, source, observation)
    assert packet['registered_at'] == '2026-10-30T18:00:00+00:00'
    assert packet['registration_day'] == '2026-10-31'
    assert packet['coordinate_space'] == 'pixel' and not packet['georeference_available']
    assert packet['candidate_only'] and not packet['field_truth_verified'] and not packet['object_state_updated']
    assert 'observed_at' not in packet
    base = subject.event_base(observation, packet)
    assert base['observed_at'] is None and base['extracted'] == {'attributes': {}, 'anomalies': []}
    assert base['invocation_id'] == 'invocation' and base[subject.PACKET] == packet


def test_normal_terminal_packet_does_not_drift_when_same_transaction_finishes_job():
    context, source, observation = scene()
    before = subject.packet(context, source, observation)
    context['job'].status = 'succeeded'
    context['task'].version = 12
    context['task'].data['human_note'] = '另存人工意见，不改变机器包'
    assert before == subject.packet(context, source, observation)
    assert before['producer_status'] == 'succeeded' and before['settlement'] is None


def test_settled_packet_preserves_failed_producer_and_actual_other_recovery_actor():
    context, source, observation = scene()
    proof = {'schema': 'workflow-pixel-settlement-proof-v250', 'actor_id': 'another-admin',
        'job_status': 'failed', 'result_hash': context['run'].data['result_hash']}
    context['job'].status = 'failed'
    context['settlement_audit'] = N(actor_id='another-admin', data=proof)
    packet = subject.packet(context, source, observation)
    assert packet['producer_status'] == 'failed' and packet['settlement'] == proof
    assert subject.issuer(context) == 'another-admin'
    assert context['run'].owner_id == 'producer'
    assert packet['settlement'] is not proof


@pytest.mark.parametrize('entities,entity', [([], 'entity'), (['entity', 'other'], 'entity'), (['entity'], 'other')])
def test_one_machine_result_cannot_be_counted_for_unselected_or_multiple_objects(entities, entity):
    context, source, observation = scene()
    context['task'].data['entity_ids'] = entities; observation.entity_id = entity
    with pytest.raises(HTTPException) as refused: subject.packet(context, source, observation)
    assert refused.value.status_code == 409


def test_machine_source_is_a_fixed_local_result_identity_without_sensor_or_query_privileges():
    value = subject.source_manifest('00000000-0000-4000-8000-000000000001').model_dump()
    assert value['kind'] == 'source' and value['namespace'] == 'platform_native_pixel_results'
    assert value['execution'] == {'adapter': 'upload'}
    assert value['specification']['source_family'] == design.FAMILY
    assert value['data_policy']['deployment'] == 'local'
    assert value['data_policy']['cloud_allowed'] is value['data_policy']['external_allowed'] is False
    assert not ({'device_id', 'url', 'control', 'query_tool'} & set(value['execution']))
    assert not subject.same({'candidate_only': True}, {'candidate_only': 1})
