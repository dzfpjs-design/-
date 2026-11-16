"""Actual saved ASR provenance, independent Audits and self-reseal attacks.

All successful state comes from public API + the real local model/worker. The
faults are contained in rollback-only savepoints; no original Audit is re-signed
to make a corrupt source pass. Root alone executes these tests.
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
from generic_agents.worker import process_once, deliver_events
from generic_agents import native_audio_provenance_v136 as origin
from tests.test_native_audio_worker_v136 import prepare_audio_task, exact_child, good
from tests.test_workflow_abilities import finish_job


def actual_native(client):
    value = prepare_audio_task(client)
    task = finish_job(client, value['task'])
    receipt = task['workflow_runtime']['receipts'][0]
    with Session(engine()) as db:
        identity = exact_child(db, value['job_id']).id
    assert process_once(identity, deliver=False)
    with Session(engine()) as db:
        child = db.get(m.Job, identity)
        assert child.status == 'succeeded'
        assert child.data['result']['created'] == 1
        observations = child.data['result']['observation_ids']
        assert len(observations) == 1
    deliver_events(observation_id=observations[0])
    me = good(client.get('/api/me'))
    p = Principal(me['user_id'], me['workspace_id'], me['role'], 'native-audio-readonly-test')
    return {**value, 'task': task, 'receipt': receipt, 'run_id': receipt['output']['audio_run_id'],
        'observation_id': observations[0], 'principal': p}


def readonly(call):
    writes = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'MERGE'}:
            writes.append(statement.split(None, 1)[0].upper())

    event.listen(engine(), 'before_cursor_execute', observe)
    try:
        return call()
    finally:
        event.remove(engine(), 'before_cursor_execute', observe)
        assert writes == []


def test_actual_native_history_and_independent_audit_inventory_are_readonly(client):
    value = actual_native(client); p = value['principal']
    with Session(engine()) as db:
        obs = db.get(m.Observation, value['observation_id'])
        context = readonly(lambda: origin.checked_observation(db, p, obs))
        assert context['run'].id == value['run_id']
        assert context['task'].id == value['task']['id']
        assert context['job'].id == value['job_id']
        assert context['input_audit'].action == origin.INPUT_ACTION
        assert context['result_audit'].action == origin.RESULT_ACTION
        issued = origin.issued_audits(db, p, obs.id)
        assert len(issued) == 1 and issued[0].data == obs.data[origin.ISSUED]
        assert context['input_audit'].actor_id == context['result_audit'].actor_id == context['job'].data['actor_id']
        assert context['reception_audit'].actor_id == obs.data['submitted_by']
        assert obs.observed_at is None and 'extractor_id' not in obs.data and not obs.data.get('invocation_id')
        assert context['packet']['attributes']['已执行整片转写'] is True
        assert context['event'].status == 'received'
        assert obs.data['data_reception']['schema'] == 'instance-native-audio-data-reception-v1'
        assert obs.data['data_reception']['declaration_status'] == 'not_declared'
        assert obs.data['data_reception']['original_declared_outputs']
        assert not list(db.scalars(select(m.CloudCall.id)))


def test_actual_scope_and_result_audits_reject_jointly_resealed_machine_json(client):
    value = actual_native(client); p = value['principal']
    # A second object is created through the actual instance schema, never by
    # inventing a UUID and claiming it was part of the original processing.
    release = value['release']; kind = release['snapshot']['object_types'][0]
    other = good(client.post('/api/instances/' + release['instance_id'] + '/objects', json={
        'release_id': release['id'], 'object_type': kind['key'],
        'attributes': {kind['fields'][0]['key']: '另一真实登记对象，非原处理成员'}}), 201)
    for fault in ('joint_result', 'joint_member_scope'):
        with Session(engine()) as db:
            context = origin.checked_run(db, p, value['run_id'])
            run, task = context['run'], context['task']
            frozen_audits = {context[key].id: copy.deepcopy(context[key].data)
                for key in ('input_audit', 'result_audit', 'completion_audit')}
            transaction = db.begin_nested()
            try:
                data = copy.deepcopy(task.data)
                if fault == 'joint_result':
                    result = copy.deepcopy(run.data['result'])
                    result['transcription']['text'] += ' 被联合改写'
                    result['transcription']['segments'][0]['text'] = result['transcription']['text']
                    run.data = {**run.data, 'result': result, 'result_hash': digest(result)}
                    output = copy.deepcopy(context['receipt']['output'])
                    output['audio_transcription'] = copy.deepcopy(result)
                    output['attributes']['transcription_text_candidate'] = result['transcription']['text']
                    call = context['call']; call.data = {**call.data, 'output': copy.deepcopy(output)}
                    target = next(item for item in data['workflow_runtime']['receipts']
                        if item['request_id'] == value['job_id'])
                    target['output'] = copy.deepcopy(output)
                else:
                    data['entity_ids'] = [other['id']]
                    data['object_snapshots'][0].update(id=other['id'], attributes=copy.deepcopy(other['attributes']))
                    binding = data['object_definition_bindings']['objects'][0]
                    binding['entity_id'] = other['id']
                    binding['attributes_hash'] = digest(other['attributes'])
                    binding['definition']['entity_id'] = other['id']
                    data['object_definition_bindings_hash'] = digest(data['object_definition_bindings'])
                task.data = data; db.flush()
                # The original Job/Invocation and self-consistent workflow JSON
                # still agree. The independent input/result Audit must decide.
                origin._call(db, p, task, context['business'], run)
                with pytest.raises(HTTPException) as denied:
                    readonly(lambda: origin.checked_run(db, p, run.id))
                assert denied.value.status_code == 409
                assert {identity: db.get(m.Audit, identity).data for identity in frozen_audits} == frozen_audits
            finally:
                transaction.rollback()
            assert origin.checked_run(db, p, run.id)['run'].data['result_hash'] == context['result_audit'].data['result_hash']


def test_actual_native_marker_and_audit_faults_never_select_legacy(client):
    value = actual_native(client); p = value['principal']
    faults = ('observation_marker', 'issued_audit', 'marker_and_native_packet',
        'input_audit', 'result_audit', 'issued_wrong_actor', 'original_event')
    with Session(engine()) as db:
        for fault in faults:
            obs = db.get(m.Observation, value['observation_id'])
            context = origin.checked_observation(db, p, obs)
            transaction = db.begin_nested()
            try:
                if fault == 'observation_marker':
                    data = copy.deepcopy(obs.data); data.pop(origin.ISSUED); obs.data = data
                elif fault == 'issued_audit':
                    db.delete(origin.issued_audits(db, p, obs.id)[0])
                elif fault == 'marker_and_native_packet':
                    data = copy.deepcopy(obs.data)
                    data.pop(origin.ISSUED); data.pop('native_packet'); data.pop('native_packet_hash')
                    obs.data = data
                    assert origin.issued_audits(db, p, obs.id)
                elif fault == 'input_audit':
                    db.delete(context['input_audit'])
                elif fault == 'result_audit':
                    row = context['result_audit']; row.data = {**row.data, 'result_hash': '0' * 64}
                elif fault == 'issued_wrong_actor':
                    row = origin.issued_audits(db, p, obs.id)[0]
                    row.data = {**row.data, 'receipt_actor_id': str(uuid4())}
                else:
                    db.delete(context['event'])
                db.flush()
                with pytest.raises(HTTPException) as denied:
                    readonly(lambda: origin.checked_observation(db, p, obs))
                assert denied.value.status_code == 409
            finally:
                transaction.rollback()
            assert origin.checked_observation(db, p, obs)['run'].id == value['run_id']
