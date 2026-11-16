"""Pure typed-family boundaries, not evidence of inference or reception."""
from datetime import UTC, datetime
from types import SimpleNamespace as Row
import pytest
from fastapi import HTTPException
from generic_agents.contracts import digest
from generic_agents import native_sound_provenance as source
from generic_agents.native_sound_receipts import verify_memory


def test_source_is_a_managed_candidate_family_and_never_a_numeric_or_asr_source():
    manifest = source.source_manifest('test-instance').model_dump(mode='json')
    assert manifest['namespace'] == 'platform_native_sound_results'
    spec = manifest['specification']
    assert spec['source_family'] == 'workflow_environment_sound_candidate_v1'
    assert spec['date_basis'] == 'machine_result_registered' and spec['candidate_only'] is True
    assert manifest['data_policy']['cloud_allowed'] is False
    assert 'numeric_rules' not in spec and 'transcription' not in spec


def test_packet_registration_day_is_not_a_fabricated_recording_date(monkeypatch):
    original = {'evidence_id': 'evidence', 'input_sha256': 'a' * 64, 'configuration': {'channel': 'mix', 'threshold': .3}}
    run = Row(id='run', workspace_id='workspace', task_id='task', sound_input_id='input', capability_id='cap',
        job_id='job', invocation_id='call', data={'instance_id': 'instance', 'release_id': 'release',
        'business_id': 'business', 'sound_snapshot': {'snapshot': original}, 'capability_hash': 'b' * 64,
        'request_hash': 'c' * 64, 'result_hash': 'd' * 64, 'output': {}, 'installation_proof': {},
        'result': {'classification': {'windows': [{}, {}], 'candidates': []}}})
    context = {'run': run, 'task': Row(entity_ids=['entity'], data={'entity_ids': ['entity']}),
        'input_audit': Row(id='input-audit'), 'result_audit': Row(id='result-audit'), 'completion_audit': Row(id='completion-audit')}
    monkeypatch.setattr(source, 'declaration', lambda c: ({'plan_hash': 'e' * 64}, []))
    observation = Row(id='observation', source_id='source', entity_id='entity', created_at=datetime(2026, 1, 1, 17, 0, tzinfo=UTC))
    packet = source.packet(context, Row(id='source'), observation)
    assert packet['registration_day'] == '2026-01-02' and packet['recorded_at'] is None
    assert packet['recording_time_verified'] is False and packet['window_count'] == 2
    assert packet['candidate_label_count'] == 0 and packet['physical_state_verified'] is False
    assert packet['association_basis'] == 'task_entity_association_not_verified_sound_attribution'
    base = source.event_base(observation, packet)
    assert base['observed_at'] is None and base['extracted'] == {'attributes': {}, 'anomalies': []}
    assert 'native_receipt' not in base and source.PACKET in base


@pytest.mark.parametrize('damage', ['packet', 'time', 'invocation'])
def test_memory_requires_original_typed_packet_unknown_time_and_real_call(damage):
    observation = Row(data={source.PACKET: {'run_id': 'real-run'}, 'invocation_id': 'real-call'})
    data = {source.PACKET: {'run_id': 'real-run'}, 'invocation_id': 'real-call', 'observed_at': None}
    verify_memory(observation, Row(data=data))
    if damage == 'packet': data[source.PACKET] = {'run_id': 'other-run'}
    elif damage == 'time': data['observed_at'] = '2026-01-01T00:00:00+00:00'
    else: data['invocation_id'] = None
    with pytest.raises(HTTPException) as caught: verify_memory(observation, Row(data=data))
    assert caught.value.status_code == 409


def test_issued_identity_binds_independent_event_and_original_packet():
    observation = Row(id='observation', entity_id='entity', source_id='source')
    packet = {'run_id': 'run', 'task_id': 'task', 'instance_id': 'instance', 'invocation_id': 'call',
        'evidence_ids': ['evidence'], 'registered_at': '2026-01-01T00:00:00+00:00', 'registration_day': '2026-01-01'}
    first = source.issued_anchor(observation, Row(id='event'), packet)
    second = source.issued_anchor(observation, Row(id='other-event'), packet)
    assert first['packet_hash'] == digest(packet) and first['event_base_hash'] == digest(source.event_base(observation, packet))
    assert digest(first) != digest(second)
