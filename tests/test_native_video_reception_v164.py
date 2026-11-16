"""Pure receiver boundaries, not PostgreSQL, model or provenance acceptance.

The fully checked origin context and release verifier are explicit seams. They
do not manufacture real INPUT/RESULT/ISSUED evidence. The actual receipt mapper
and common cold Event/Memory/ACK checks run unchanged over closed read IO.
Root separately runs real new RTSP/CPU/automatic-watch inputs end to end.
"""
from copy import deepcopy
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from generic_agents import models as m
from generic_agents import instance_data_receipts as receiver
from generic_agents import native_video_provenance_v164 as origin
from generic_agents import native_video_reception_v164 as video
from generic_agents.contracts import digest
from generic_agents.security import Principal


RECEIPT_KEYS = {
    'schema', 'workspace_id', 'event_id', 'observation_id', 'entity_id',
    'instance_id', 'release_id', 'release_number', 'package_id', 'configuration_hash',
    'requirements_schema', 'requirements_hash', 'source_id', 'source_hash',
    'origin_source_id', 'origin_source_hash', 'source_record_id', 'processing_version',
    'native_packet_hash', 'source_event_base_hash', 'run_type', 'run_id', 'run_generation',
    'run_request_key', 'run_result_hash', 'run_machine_hash', 'session_id', 'capture_hash',
    'pipeline_hash', 'capture_receipt_hash', 'read_reservation_id', 'supplement_plan_id',
    'automatic_watch_id', 'business_id', 'workflow_task_id', 'invocation_id',
    'capture_job_id', 'capture_actor_id', 'processing_job_id', 'processing_actor_id',
    'receipt_actor_id', 'input_audit_id', 'result_audit_id', 'completion_audit_id',
    'issued_audit_id', 'receipt_audit_id', 'installation_hash', 'frozen_scope_hash',
    'evidence', 'frames', 'saved_frames', 'declaration_status', 'declared_outputs',
    'declaration_reason', 'original_declared_outputs', 'candidate_only', 'not_authoritative',
    'source_time_verified', 'physical_state_verified', 'entity_state_updated',
    'can_execute', 'can_delete', 'receipt_hash',
}


def uid(number):
    return '164bce00-0000-4000-8000-' + f'{number:012x}'


def boundary():
    p = Principal(uid(1), uid(2), 'viewer', 'pure-native-video-receiver')
    requirements = {'schema': 'instance-data-requirements-v3',
        'requirements_hash': 'a' * 64, 'businesses': []}
    release = NS(id=uid(3), instance_id=uid(4), release_number=1,
        package_id=uid(5), configuration_hash='b' * 64)
    source = NS(id=uid(6), content_hash='c' * 64)
    native_source = NS(id=uid(7), content_hash='d' * 64)
    run = NS(id=uid(8), evidence_id=uid(9), request_key='e' * 64,
        data={'generation': 1, 'input_sha256': 'f' * 64,
            'result_hash': '1' * 64, 'pipeline_hash': '2' * 64})
    session = NS(id=uid(10), data={'capture_hash': '3' * 64,
        'receipt': {'source_clock_utc': 'unknown', 'audio_captured': False}})
    frames = [NS(id=uid(20 + index), evidence_id=uid(30 + index), sample_index=index,
        data={'frame_sha256': str(index + 4) * 64, 'receipt_hash': str(index + 6) * 64})
        for index in range(2)]
    scope = {'instance_id': release.instance_id, 'release_id': release.id,
        'business_id': 'business_0123456789abcdef', 'business_name': '纯边界视频核查',
        'entity_id': uid(11), 'entity_version': 1, 'source_id': source.id,
        'source_hash': source.content_hash, 'package_id': release.package_id,
        'package_hash': '8' * 64, 'configuration_hash': release.configuration_hash,
        'requirements_schema': requirements['schema'],
        'requirements_hash': requirements['requirements_hash'],
        'channel_id': uid(12), 'gateway_id': uid(13), 'binding_id': uid(14),
        'binding_generation': 1, 'session_id': session.id, 'reservation_id': uid(15),
        'plan_id': uid(16), 'watch_id': uid(17), 'processor_id': uid(18),
        'approved_by': uid(18), 'watch_owner_id': uid(18)}
    packet = {'schema': 'native-observation-v2', 'source_key': 'VideoRun:' + run.id,
        'source_machine_hash': '9' * 64, 'entity_id': scope['entity_id'],
        'task_id': None, 'attributes': {'已保存画面数': 2}, 'evidence_ids': [run.evidence_id]}
    observation = m.Observation(id=uid(40), workspace_id=p.workspace_id,
        source_id=native_source.id, source_record_id=packet['source_key'] + ':' + scope['entity_id'],
        processing_version=digest(packet)[:40], entity_id=scope['entity_id'],
        observed_at=None, business_day='2026-10-21', status='processed', version=1,
        data={'submitted_by': p.user_id, 'candidate_only': True,
            'native_packet': deepcopy(packet), 'native_packet_hash': digest(packet),
            'native_instance_id': release.instance_id, 'evidence_ids': [run.evidence_id],
            'extracted': {'attributes': deepcopy(packet['attributes'])}})
    base = {'observation_id': observation.id, 'entity_id': observation.entity_id,
        'source_id': native_source.id, 'evidence_ids': [run.evidence_id],
        'native_receipt': deepcopy(packet), 'observed_at': None, 'candidate_only': True,
        'extracted': deepcopy(observation.data['extracted']), 'invocation_id': None}
    event = m.Event(id=uid(41), workspace_id=p.workspace_id, kind='observation.data',
        idempotency_key='data:' + observation.id, status='pending_delivery',
        data=deepcopy(base))
    context = {'run': run, 'session': session, 'job': NS(id=uid(42)),
        'capture_job': NS(id=uid(43)), 'input_audit': NS(id=uid(44), actor_id=uid(18)),
        'result_audit': NS(id=uid(45), actor_id=uid(19)),
        'completion_audit': NS(id=uid(46)), 'issued_audit': NS(id=uid(47)),
        'reception_audit': NS(id=uid(52)),
        'entity': NS(id=observation.entity_id), 'instance': NS(id=release.instance_id),
        'release': release, 'plan': requirements, 'requirements': requirements,
        'definition': {}, 'packet': packet, 'frozen_scope': scope,
        'source': source, 'native_source': native_source, 'event': event,
        'base': deepcopy(base), 'frames': frames,
        'installation': {'installation_id': uid(48), 'pack_id': 'rtdetr-v2-r18',
            'generation': 1, 'recipe_hash': 'a' * 64, 'backend': 'cpu',
            'artifact_hashes': {'weights': 'b' * 64},
            'ready_audit_id': uid(49), 'ready_audit_hash': 'c' * 64}}
    return p, observation, event, context


class ColdRead:
    """Only the common receiver's exact Event read and bounded Memory read."""
    def __init__(self, p, observation, event, memories):
        self.p = p; self.observation = observation; self.event = event
        self.memories = memories; self.calls = []

    def scalar(self, statement):
        assert self.calls == []
        assert statement.column_descriptions[0]['entity'] is m.Event
        assert statement._for_update_arg is None
        assert len(statement._where_criteria) == 3
        expected = select(m.Event).where(m.Event.workspace_id == self.p.workspace_id,
            m.Event.kind == 'observation.data',
            m.Event.idempotency_key == 'data:' + self.observation.id)
        # Full mapped payload projection legitimately binds its typed marker,
        # canonical and owner envelope labels. Compare both the actual SQL
        # structure and all parameters against this one closed model query.
        assert statement.compare(expected)
        assert statement.compile().params == expected.compile().params
        self.calls.append('event')
        return self.event

    def scalars(self, statement):
        assert self.calls == ['event']
        assert statement.column_descriptions[0]['entity'] is m.Memory
        assert statement._for_update_arg is None
        assert len(statement._where_criteria) == 3 and statement._limit_clause.value == 2
        expected = select(m.Memory).where(m.Memory.workspace_id == self.p.workspace_id,
            m.Memory.data['event_id'].as_string() == self.event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence').limit(2)
        assert statement.compare(expected)
        assert statement.compile().params == expected.compile().params
        self.calls.append('memory')
        return self.memories


class OriginalRunRead:
    """Closed original Run/release reads for the legacy decision only."""
    def __init__(self, p, reads):
        self.p = p; self.reads = list(reads); self.calls = []

    def scalar(self, statement):
        assert self.reads, 'unexpected original-scope query'
        model, identity, value = self.reads.pop(0)
        expected = select(model).where(model.id == identity, model.workspace_id == self.p.workspace_id)
        assert statement.column_descriptions[0]['entity'] is model
        assert statement._for_update_arg is None
        assert statement.compare(expected)
        assert statement.compile().params == expected.compile().params
        self.calls.append(model.__name__)
        return value


def checked_context(monkeypatch, db, p, observation, event, context):
    def issued(actual_db, actual_p, actual_observation):
        assert actual_db is db and actual_p is p and actual_observation is observation
        return True

    def checked(actual_db, actual_p, actual_observation, actual_event=None, *, live=False):
        assert actual_db is db and actual_p is p and actual_observation is observation
        assert actual_event is event and live is False
        return context

    def plan(actual_db, actual_p, release):
        assert actual_db is db and actual_p is p and release is context['release']
        return deepcopy(context['requirements'])

    monkeypatch.setattr(origin, 'has_issued', issued)
    monkeypatch.setattr(origin, 'checked_observation', checked)
    monkeypatch.setattr(receiver, 'verify_release', plan)

    def unexpected_declaration(*_args, **_kwargs):
        pytest.fail('saved-frame summary must not be matched to a single-image workflow output')

    monkeypatch.setattr(receiver, '_declarations', unexpected_declaration)


@pytest.mark.parametrize('packet,expected', [
    ({'source_key': 'VideoRun:' + uid(8)}, True),
    ({'source_key': 'AudioRun:' + uid(8)}, False),
    ({'source_key': 'GeoPair:' + uid(8)}, False),
    ({'source_key': []}, False), ({}, False), (None, False),
])
def test_known_video_family_selection_is_closed(monkeypatch, packet, expected):
    monkeypatch.setattr(origin, 'has_issued', lambda *_args: False)
    observation = NS(data={'native_packet': packet})
    assert video.handles(object(), object(), observation) is expected


def test_independent_issued_inventory_selects_video_when_json_tag_is_missing(monkeypatch):
    monkeypatch.setattr(origin, 'has_issued', lambda *_args: True)
    assert video.handles(object(), object(), NS(data={})) is True


def test_receipt_is_native_candidate_data_without_task_invocation_or_step_declaration(monkeypatch):
    p, observation, event, context = boundary(); db = object()
    checked_context(monkeypatch, db, p, observation, event, context)
    before = deepcopy((observation.data, event.data, context['frozen_scope']))
    value = video.receipt(db, p, event, observation)
    assert set(value) == RECEIPT_KEYS
    assert value['schema'] == 'instance-native-video-data-reception-v1'
    assert value['source_id'] == observation.source_id == context['native_source'].id
    assert value['origin_source_id'] == context['source'].id != value['source_id']
    assert value['source_hash'] == context['native_source'].content_hash
    assert value['origin_source_hash'] == context['source'].content_hash
    assert value['workflow_task_id'] is None and value['invocation_id'] is None
    assert value['declaration_status'] == 'not_declared'
    assert value['declared_outputs'] == value['original_declared_outputs'] == []
    assert value['declaration_reason'] == 'native_saved_video_frames_not_single_image_step_output'
    assert value['capture_actor_id'] == context['input_audit'].actor_id
    assert value['processing_actor_id'] == context['result_audit'].actor_id
    assert value['capture_actor_id'] != value['processing_actor_id']
    assert value['receipt_actor_id'] == p.user_id
    assert value['input_audit_id'] == context['input_audit'].id
    assert value['result_audit_id'] == context['result_audit'].id
    assert value['completion_audit_id'] == context['completion_audit'].id
    assert value['issued_audit_id'] == context['issued_audit'].id
    assert value['receipt_audit_id'] == context['reception_audit'].id
    assert value['requirements_hash'] == context['requirements']['requirements_hash']
    assert value['native_packet_hash'] == digest(context['packet'])
    assert value['source_event_base_hash'] == digest(context['base'])
    assert value['frozen_scope_hash'] == digest(context['frozen_scope'])
    assert value['installation_hash'] == digest(context['installation'])
    assert value['saved_frames'] == 2 and [item['sample_index'] for item in value['frames']] == [0, 1]
    assert value['evidence'] == [{'id': context['run'].evidence_id,
        'sha256': context['run'].data['input_sha256']}]
    for key in ('source_time_verified', 'physical_state_verified', 'entity_state_updated', 'can_execute', 'can_delete'):
        assert value[key] is False
    assert value['candidate_only'] is value['not_authoritative'] is True
    assert value['receipt_hash'] == digest({key: item for key, item in value.items() if key != 'receipt_hash'})
    assert digest((observation.data, event.data, context['frozen_scope'])) == digest(before)


@pytest.mark.parametrize('field', [
    'instance_id', 'release_id', 'entity_id', 'source_id', 'source_hash',
    'requirements_schema', 'requirements_hash',
])
def test_receipt_does_not_cross_original_scope_or_data_plan(monkeypatch, field):
    p, observation, event, context = boundary(); db = object()
    context['frozen_scope'][field] = 'changed-scope'
    checked_context(monkeypatch, db, p, observation, event, context)
    with pytest.raises(HTTPException) as error:
        video.receipt(db, p, event, observation)
    assert error.value.status_code == 409


@pytest.mark.parametrize('fault', [
    None, 'no_event', 'pending_ack', 'no_memory', 'duplicate_memory',
    'memory_status', 'memory_receipt', 'memory_packet_number_kind',
    'missing_memory_clock', 'missing_memory_invocation',
    'missing_observation_receipt', 'missing_event_receipt',
])
def test_actual_common_cold_receiver_requires_unique_memory_ack_and_exact_packet(monkeypatch, fault):
    p, observation, event, context = boundary()
    db = ColdRead(p, observation, event, [])
    checked_context(monkeypatch, db, p, observation, event, context)
    # Keep unrelated families out of this isolated receiver-dispatch test. No
    # actual provenance or original-source verification is asserted here.
    from generic_agents import native_audio_reception_v136, native_geo_pair_reception_v137
    from generic_agents import native_learned_geo_pair_reception_v138
    for family in (native_audio_reception_v136, native_geo_pair_reception_v137,
            native_learned_geo_pair_reception_v138):
        monkeypatch.setattr(family, 'handles', lambda *_args: False)
    reception = video.receipt(db, p, event, observation)
    observation.data = {**observation.data, 'data_reception': deepcopy(reception)}
    event.data = {**event.data, 'data_reception': deepcopy(reception)}
    event.status = 'received'
    memory = m.Memory(id=uid(50), workspace_id=p.workspace_id, status='candidate',
        text='纯接收边界', data={**deepcopy(event.data), 'event_id': event.id,
            'type': 'observation_evidence', 'not_authoritative': True})
    db.memories = [memory]
    if fault == 'no_event': db.event = None
    elif fault == 'pending_ack': event.status = 'pending_delivery'
    elif fault == 'no_memory': db.memories = []
    elif fault == 'duplicate_memory':
        db.memories.append(m.Memory(id=uid(51), workspace_id=p.workspace_id,
            status='candidate', text='重复接收边界', data=deepcopy(memory.data)))
    elif fault == 'memory_status': memory.status = 'confirmed'
    elif fault == 'memory_receipt':
        memory.data = {**memory.data, 'data_reception': {**reception, 'saved_frames': 3}}
    elif fault == 'memory_packet_number_kind':
        packet = deepcopy(memory.data['native_receipt']); packet['attributes']['已保存画面数'] = 2.0
        memory.data = {**memory.data, 'native_receipt': packet}
    elif fault == 'missing_memory_clock':
        memory.data = {key: value for key, value in memory.data.items() if key != 'observed_at'}
    elif fault == 'missing_memory_invocation':
        memory.data = {key: value for key, value in memory.data.items() if key != 'invocation_id'}
    elif fault == 'missing_observation_receipt':
        observation.data = {key: value for key, value in observation.data.items() if key != 'data_reception'}
    elif fault == 'missing_event_receipt':
        event.data = {key: value for key, value in event.data.items() if key != 'data_reception'}
    before = deepcopy((observation.data, event.data, memory.data))
    if fault is None:
        assert receiver.observation_receipt(db, p, observation) == {'data_reception': reception}
        assert db.calls == ['event', 'memory']
    else:
        with pytest.raises(HTTPException) as error:
            receiver.observation_receipt(db, p, observation)
        assert error.value.status_code == 409
    assert digest((observation.data, event.data, memory.data)) == digest(before)


@pytest.mark.parametrize('modern_plan', [False, True])
def test_old_unissued_video_cannot_acquire_a_v164_receipt(monkeypatch, modern_plan):
    from generic_agents import native_receipts
    p, observation, event, context = boundary()
    db = OriginalRunRead(p, [(m.VideoRun, context['run'].id, context['run'])])
    monkeypatch.setattr(origin, 'has_issued', lambda *_args: False)
    calls = []

    def original(actual_db, actual_p, actual_observation):
        assert actual_db is db and actual_p is p and actual_observation is observation
        calls.append('legacy-cold-check')
        return {'legacy_only': True}

    def destination(actual_db, actual_p, actual_observation):
        assert actual_db is db and actual_p is p and actual_observation is observation
        calls.append('destination')
        return context['release'], context['entity'], []

    def release(actual_db, actual_p, actual_release):
        assert actual_db is db and actual_p is p and actual_release is context['release']
        calls.append('release')
        return context['requirements'] if modern_plan else None

    def no_new_signature(*_args, **_kwargs):
        pytest.fail('old no-input result must not enter V164 checked/issued context')

    monkeypatch.setattr(native_receipts, 'checked', original)
    monkeypatch.setattr(receiver, '_destination', destination)
    monkeypatch.setattr(receiver, 'verify_release', release)
    monkeypatch.setattr(origin, 'checked_observation', no_new_signature)
    if modern_plan:
        with pytest.raises(HTTPException) as error:
            video.receipt(db, p, event, observation)
        assert error.value.status_code == 409
    else:
        assert video.receipt(db, p, event, observation) is None
    assert calls == ['legacy-cold-check', 'destination', 'release']
    assert db.calls == ['VideoRun'] and db.reads == []
    assert 'data_reception' not in observation.data and 'data_reception' not in event.data


def test_old_run_bound_to_modern_release_cannot_use_entity_legacy_destination(monkeypatch):
    from generic_agents import native_receipts
    p, observation, event, context = boundary()
    context['run'].data['source_context'] = {'domain_binding': {
        'instance_id': context['release'].instance_id,
        'instance_release_id': context['release'].id}}
    db = OriginalRunRead(p, [(m.VideoRun, context['run'].id, context['run']),
        (m.InstanceRelease, context['release'].id, context['release'])])
    monkeypatch.setattr(origin, 'has_issued', lambda *_args: False)
    monkeypatch.setattr(native_receipts, 'checked', lambda *_args: {'legacy_cold_seam': True})
    monkeypatch.setattr(receiver, 'verify_release', lambda *_args: context['requirements'])

    def wrong_destination(*_args, **_kwargs):
        pytest.fail('Entity legacy origin must not override the Run fixed modern release')

    monkeypatch.setattr(receiver, '_destination', wrong_destination)
    with pytest.raises(HTTPException) as error:
        video.receipt(db, p, event, observation)
    assert error.value.status_code == 409
    assert db.calls == ['VideoRun', 'InstanceRelease'] and db.reads == []
    assert 'data_reception' not in observation.data and 'data_reception' not in event.data


@pytest.mark.parametrize('fault', [
    'empty_binding', 'non_object_binding', 'missing_release', 'foreign_binding', 'foreign_release',
])
def test_present_broken_or_foreign_run_binding_is_not_an_unbound_legacy_video(monkeypatch, fault):
    from generic_agents import native_receipts
    p, observation, event, context = boundary()
    association = {'instance_id': context['release'].instance_id,
        'instance_release_id': context['release'].id}
    if fault == 'empty_binding': association = {}
    elif fault == 'non_object_binding': association = []
    elif fault == 'missing_release': association.pop('instance_release_id')
    elif fault == 'foreign_binding': association['instance_id'] = uid(99)
    elif fault == 'foreign_release': context['release'].instance_id = uid(99)
    context['run'].data['source_context'] = {'domain_binding': association}
    reads = [(m.VideoRun, context['run'].id, context['run'])]
    if fault == 'foreign_release':
        reads.append((m.InstanceRelease, context['release'].id, context['release']))
    db = OriginalRunRead(p, reads)
    monkeypatch.setattr(origin, 'has_issued', lambda *_args: False)
    monkeypatch.setattr(native_receipts, 'checked', lambda *_args: {'legacy_cold_seam': True})

    def forbidden(*_args, **_kwargs):
        pytest.fail('malformed/foreign binding must reject before legacy fallback or plan verification')

    monkeypatch.setattr(receiver, '_destination', forbidden)
    monkeypatch.setattr(receiver, 'verify_release', forbidden)
    with pytest.raises(HTTPException) as error:
        video.receipt(db, p, event, observation)
    assert error.value.status_code == 409 and db.reads == []
    assert 'data_reception' not in observation.data and 'data_reception' not in event.data


def test_provenance_failure_never_falls_back_or_rebuilds_old_authority(monkeypatch):
    p, observation, event, _context = boundary(); db = object()
    failure = HTTPException(409, '独立原始依据缺失')
    monkeypatch.setattr(origin, 'has_issued', lambda *_args: True)

    def reject(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(origin, 'checked_observation', reject)
    with pytest.raises(HTTPException) as error:
        video.receipt(db, p, event, observation)
    assert error.value is failure
