"""Real RGB engineering comparisons enter modern data reception, never truth.

Root alone runs these PostgreSQL/local-runtime tests. Successful rows, calls,
artifacts and human work come from the public APIs and actual worker. Faults
exercise independently frozen provenance without re-signing any Audit.
"""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.worker import deliver_events
from tests.test_geo_codec import scenes, pytestmark
from tests.test_geo_pairs import pairs
from tests.test_native_receipts import setup_geo, bridge
from tests.test_workflow_geo_pairs import configured
from tests.test_workflow_abilities import finish_job, run
from tests.test_instance_workspace import complete


def good(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def principal(client):
    me = good(client.get('/api/me'))
    return Principal(me['user_id'], me['workspace_id'], me['role'], 'geo-pair-cold-test')


def readonly(call):
    writes = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        first = statement.lstrip().split(None, 1)[0].upper()
        if first in {'INSERT', 'UPDATE', 'DELETE', 'MERGE'}:
            writes.append(first)

    event.listen(engine(), 'before_cursor_execute', observe)
    try:
        return call()
    finally:
        event.remove(engine(), 'before_cursor_execute', observe)
        assert writes == []


def received_pair(client, pairs):
    a, b, release, body, base, task = setup_geo(client, pairs)
    saved, listing = bridge(client, base)
    assert saved['created'] == 1 and saved['cloud_calls'] == 0
    assert len(listing['records']) == 1
    record = listing['records'][0]
    assert record['data_received'] and record['integrity_error'] is None, record
    receipt = task['workflow_runtime']['receipts'][0]
    return {'a': a, 'b': b, 'release': release, 'body': body, 'base': base,
        'task': task, 'record': record, 'pair_id': receipt['output']['geo_pair_id'],
        'observation_id': record['id'], 'receipt': receipt, 'principal': principal(client)}


def test_actual_rgb_result_has_modern_candidate_receipt_and_readonly_cold_chain(client, pairs):
    from generic_agents import native_geo_pair_provenance_v137 as origin
    from generic_agents.instance_data_receipts import observation_receipt
    value = received_pair(client, pairs); p = value['principal']
    with Session(engine()) as db:
        observation = db.get(m.Observation, value['observation_id'])
        context = readonly(lambda: origin.checked_observation(db, p, observation))
        reception = readonly(lambda: observation_receipt(db, p, observation))['data_reception']
        assert reception == value['record']['data_reception']
        assert reception['schema'] == 'instance-native-geo-pair-data-reception-v1'
        assert reception['declaration_status'] == 'not_declared'
        assert reception['declared_outputs'] == [] and reception['original_declared_outputs']
        assert reception['learned_model'] is False
        for key in ('source_time_verified', 'physical_state_verified', 'entity_state_updated', 'can_execute', 'can_delete'):
            assert reception[key] is False
        assert context['pair'].id == value['pair_id']
        assert context['job'].status == context['call'].status == 'succeeded'
        assert context['call'].id == value['receipt']['invocation_id']
        assert context['pair'].data['result']['candidate_pixels'] == 6
        assert context['pair'].data['result']['unknown_pixels'] == 54
        assert context['pair'].data['after_allowed_window'] == [3, 2, 9, 7]
        assert context['input_audit'].action == origin.INPUT_ACTION
        assert context['input_audit'].data['invocation_id'] == context['call'].id
        assert context['input_audit'].data['lease_token'] == context['call'].data['lease_token']
        assert context['result_audit'].action == origin.RESULT_ACTION
        assert len(origin.issued_audits(db, p, observation.id)) == 1
        assert observation.observed_at is None
        assert 'extractor_id' not in observation.data and 'invocation_id' not in observation.data
        assert context['event'].data['invocation_id'] is None
        assert observation.data['native_packet']['date_basis'] == 'A_original_source_request'
        assert len(context['artifacts']) == 5
        memory = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
            m.Memory.data['event_id'].as_string() == context['event'].id,
            m.Memory.data['type'].as_string() == 'observation_evidence')))
        assert len(memory) == 1 and memory[0].status == 'candidate'
        assert memory[0].data['data_reception'] == reception
        # Historical integrity uses the frozen dispatch lease, not a later
        # mutable Job lease. Only changing the actual Invocation's original
        # lease must invalidate the independent input/result/issued chain.
        original_lease = context['call'].data['lease_token']
        transaction = db.begin_nested()
        try:
            context['job'].lease_token = str(uuid4()); db.flush()
            restored = readonly(lambda: origin.checked_observation(db, p, observation))
            assert restored['input_audit'].data['lease_token'] == original_lease
        finally:
            transaction.rollback()
        before = {cls.__name__: sorted(db.scalars(select(cls.id).where(cls.workspace_id == p.workspace_id)).all())
            for cls in (m.GeoPair, m.Invocation, m.CloudCall, m.Observation)}
    again, listing = bridge(client, value['base'])
    assert again['created'] == 0 and again['already_saved'] == 1
    assert listing['records'][0]['data_reception'] == reception
    with Session(engine()) as db:
        assert before == {cls.__name__: sorted(db.scalars(select(cls.id).where(cls.workspace_id == p.workspace_id)).all())
            for cls in (m.GeoPair, m.Invocation, m.CloudCall, m.Observation)}
        assert len(before['Invocation']) == 1 and before['CloudCall'] == []


def test_actual_rgb_human_review_preserves_packet_audits_and_member_object(client, pairs):
    from generic_agents import native_geo_pair_provenance_v137 as origin
    value = received_pair(client, pairs); p = value['principal']
    with Session(engine()) as db:
        observation = db.get(m.Observation, value['observation_id'])
        context = origin.checked_observation(db, p, observation)
        packet = copy.deepcopy(observation.data['native_packet'])
        result_hash = context['pair'].data['result_hash']
        anchors = {context[key].id: copy.deepcopy(context[key].data)
            for key in ('input_audit', 'result_audit', 'completion_audit')}
        entity = db.get(m.Entity, value['body']['entity_ids'][0])
        original_entity = (entity.version, copy.deepcopy(entity.data))
    good(complete(client, value['task'], note='仅核查A/B工程候选与未知区域，不认定现场变化'))
    _, listing = bridge(client, value['base'])
    assert listing['records'][0]['record']['review_state'] == 'confirmed'
    assert listing['records'][0]['data_received']
    with Session(engine()) as db:
        observation = db.get(m.Observation, value['observation_id'])
        context = readonly(lambda: origin.checked_observation(db, p, observation))
        assert observation.data['native_packet'] == packet
        assert context['pair'].data['result_hash'] == result_hash
        assert {identity: db.get(m.Audit, identity).data for identity in anchors} == anchors
        entity = db.get(m.Entity, value['body']['entity_ids'][0])
        assert (entity.version, entity.data) == original_entity
    good(client.get(value['base'] + '/native-receipts/' + value['observation_id'] + '/export'))


def test_actual_public_retry_keeps_failed_scope_proof_and_uses_its_own_success(client, pairs, monkeypatch):
    from generic_agents import geo_pairs, native_geo_pair_provenance_v137 as origin
    a, b, cap, release, sources, body = configured(client, pairs)
    base = '/api/workspace/instances/' + release['instance_id']
    task = good(client.post(base + '/tasks', json=body), 201)
    actual_call = geo_pairs.call

    def fail_transport(*_args, **_kwargs):
        raise ValueError('本次工程transport明确未完成，须显式按原业务重试')

    with monkeypatch.context() as faults:
        faults.setattr(geo_pairs, 'call', fail_transport)
        failed = finish_job(client, task)
    assert failed['status'] == 'workflow_failed'
    p = principal(client)
    with Session(engine()) as db:
        originals = list(db.scalars(select(m.GeoPair).where(m.GeoPair.workspace_id == p.workspace_id,
            m.GeoPair.data['workflow_task_id'].as_string() == task['id'])))
        assert len(originals) == 1 and originals[0].status == 'failed'
        failed_id = originals[0].id
        failed_anchors = {row.id: copy.deepcopy(row.data) for row in origin.input_audits(db, p, failed_id)}
        assert len(failed_anchors) == 1
        assert not list(db.scalars(select(m.Audit.id).where(m.Audit.object_id == failed_id,
            m.Audit.action == origin.RESULT_ACTION)))
    assert geo_pairs.call is actual_call
    retried = finish_job(client, good(run(client, failed, retry=True)))
    assert retried['status'] == 'workflow_waiting'
    saved, listing = bridge(client, base)
    assert saved['created'] == 1 and listing['records'][0]['data_received']
    success_id = retried['workflow_runtime']['receipts'][0]['output']['geo_pair_id']
    with Session(engine()) as db:
        context = readonly(lambda: origin.checked_pair(db, p, success_id))
        assert context['job'].id == retried['workflow_runtime']['receipts'][0]['request_id']
        assert context['call'].status == 'succeeded'
        assert {identity: db.get(m.Audit, identity).data for identity in failed_anchors} == failed_anchors
        if success_id == failed_id:
            assert context['pair'].data['generation'] == 2
            assert len(origin.input_audits(db, p, success_id)) == 2
        else:
            assert context['pair'].data['generation'] == 1
            assert context['input_audit'].id not in failed_anchors


def test_actual_native_reception_faults_cannot_select_legacy_or_trust_ack_alone(client, pairs):
    from generic_agents import native_geo_pair_provenance_v137 as origin
    from generic_agents.instance_data_receipts import observation_receipt
    value = received_pair(client, pairs); p = value['principal']
    faults = ('marker', 'issued_audit', 'both_markers', 'input_audit', 'result_audit',
        'event', 'memory', 'memory_packet', 'memory_receipt', 'memory_duplicate')
    with Session(engine()) as db:
        for fault in faults:
            observation = db.get(m.Observation, value['observation_id'])
            context = origin.checked_observation(db, p, observation)
            memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
                m.Memory.data['event_id'].as_string() == context['event'].id,
                m.Memory.data['type'].as_string() == 'observation_evidence'))
            transaction = db.begin_nested()
            try:
                if fault in {'marker', 'both_markers'}:
                    data = copy.deepcopy(observation.data); data.pop(origin.ISSUED); observation.data = data
                if fault in {'issued_audit', 'both_markers'}:
                    db.delete(origin.issued_audits(db, p, observation.id)[0])
                elif fault == 'input_audit':
                    db.delete(context['input_audit'])
                elif fault == 'result_audit':
                    db.delete(context['result_audit'])
                elif fault == 'event':
                    db.delete(context['event'])
                elif fault == 'memory':
                    db.delete(memory)
                elif fault == 'memory_packet':
                    data = copy.deepcopy(memory.data); data['native_receipt']['attributes']['差异候选像素数'] += 1
                    memory.data = data
                elif fault == 'memory_receipt':
                    data = copy.deepcopy(memory.data); data['data_reception']['run_result_hash'] = '0' * 64
                    memory.data = data
                elif fault == 'memory_duplicate':
                    db.add(m.Memory(workspace_id=p.workspace_id, status=memory.status,
                        text=memory.text, data=copy.deepcopy(memory.data)))
                db.flush()
                with pytest.raises(HTTPException) as rejected:
                    readonly(lambda: observation_receipt(db, p, observation))
                assert rejected.value.status_code == 409
            finally:
                transaction.rollback()
            assert readonly(lambda: observation_receipt(db, p, observation))['data_reception']['run_id'] == value['pair_id']


def test_actual_resource_sample_never_gains_business_scope_proof(client, pairs):
    from generic_agents import native_geo_pair_provenance_v137 as origin
    a, b, cap, release, sources, body = configured(client, pairs)
    p = principal(client)
    with Session(engine()) as db:
        samples = list(db.scalars(select(m.GeoPair).where(m.GeoPair.workspace_id == p.workspace_id)))
        assert len(samples) == 1 and samples[0].status == 'succeeded'
        sample = samples[0]; original = copy.deepcopy(sample.data)
        assert 'workflow_task_id' not in sample.data
        assert origin.input_audits(db, p, sample.id) == []
        with pytest.raises(HTTPException) as rejected:
            readonly(lambda: origin.checked_pair(db, p, sample.id))
        assert rejected.value.status_code == 409
        assert sample.data == original
        assert not list(db.scalars(select(m.Audit.id).where(m.Audit.object_id == sample.id,
            m.Audit.action.in_([origin.INPUT_ACTION, origin.RESULT_ACTION]))))
