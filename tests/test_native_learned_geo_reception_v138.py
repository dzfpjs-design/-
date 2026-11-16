"""Actual public installation, neural results and independent learned receipts.

Root alone runs this PostgreSQL/local-model suite. The existing business fixture
now uses the public installer and real resource/business inference, never a
seeded ready installation. Corruptions are rollback-only and never re-sign an Audit.
"""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.worker import process_once, deliver_events
from generic_agents import native_learned_geo_pair_provenance_v138 as origin
from generic_agents.instance_data_receipts import observation_receipt
from tests.test_geo_codec import pytestmark
from tests.test_workflow_learned_geo_pairs import business, configured, rasters, start
from tests.test_native_geo_pair_reception_v137 import good, readonly
from tests.test_instance_workspace import complete
from tests.test_workflow_abilities import finish_job, run


@pytest.fixture
def actual_learned(client, business):
    task = finish_job(client, start(client, business))
    assert task['status'] == 'workflow_waiting', task
    with Session(engine()) as db:
        queued = db.scalar(select(m.Job).where(m.Job.kind == 'sync_native_receipts',
            m.Job.data['original_task_id'].as_string() == task['id']))
        assert queued is not None and queued.status == 'queued'
        queue_id = queued.id
    assert process_once(queue_id, deliver=False)
    deliver_events()
    me = good(client.get('/api/me'))
    p = Principal(me['user_id'], me['workspace_id'], me['role'], 'learned-geo-history-test')
    with Session(engine()) as db:
        rows = list(db.scalars(select(m.GeoPair).where(m.GeoPair.workspace_id == p.workspace_id,
            m.GeoPair.data['request_kind'].as_string() == 'learned_binary_change',
            m.GeoPair.data['workflow_task_id'].as_string().is_not(None))))
        assert len(rows) == 1 and rows[0].status == 'succeeded'
        pair = rows[0]
        observations = list(db.scalars(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
            m.Observation.data['native_packet']['source_key'].as_string() == 'GeoPair:' + pair.id)))
        assert len(observations) == 1
        return {'pair_id': pair.id, 'task_id': pair.data['workflow_task_id'],
            'observation_id': observations[0].id, 'principal': p}


def test_actual_learned_history_and_human_review_do_not_use_current_install_state(client, actual_learned):
    value = actual_learned; p = value['principal']
    with Session(engine()) as db:
        observation = db.get(m.Observation, value['observation_id'])
        context = readonly(lambda: origin.checked_observation(db, p, observation))
        received = readonly(lambda: observation_receipt(db, p, observation))['data_reception']
        assert received['schema'] == 'instance-native-learned-geo-pair-data-reception-v1'
        assert received['learned_model'] is True and received['declaration_status'] == 'not_declared'
        assert received['declared_outputs'] == [] and received['original_declared_outputs']
        assert len(received['artifacts']) == 8 and len(received['model_trace']) == 7
        assert context['model_ready_audit'].action == 'changer.install.ready'
        assert context['input_audit'].data['invocation_id'] == context['call'].id
        assert context['input_audit'].data['lease_token'] == context['call'].data['lease_token']
        assert observation.observed_at is None and 'extractor_id' not in observation.data and 'invocation_id' not in observation.data
        assert context['event'].data['invocation_id'] is None
        assert not any(key.startswith('native_learned_') for key in context['pair'].data)
        packet, result_hash = copy.deepcopy(context['packet']), context['pair'].data['result_hash']
        anchors = {context[key].id: copy.deepcopy(context[key].data)
            for key in ('input_audit', 'result_audit', 'completion_audit', 'model_ready_audit', 'issued_audit')}
        transaction = db.begin_nested()
        try:
            model = db.get(m.SpecialistInstallation, received['model_trace']['installation_id'])
            model.status = 'cancelled'; model.data = {**model.data, 'generation': model.data['generation'] + 1}; db.flush()
            restored = readonly(lambda: origin.checked_observation(db, p, observation))
            assert restored['packet'] == packet
            assert readonly(lambda: observation_receipt(db, p, observation))['data_reception'] == received
            from generic_agents.local_changer_install import installed
            with pytest.raises(HTTPException) as refused:
                installed(db, p, model.id)
            assert refused.value.status_code == 409
        finally:
            transaction.rollback()
    task = good(client.get('/api/workspace/tasks/' + value['task_id']))
    good(complete(client, task, note='已核对本地模型及A/B范围，仅保存变化候选，尚未认定林业现场状态。'))
    with Session(engine()) as db:
        observation = db.get(m.Observation, value['observation_id'])
        context = readonly(lambda: origin.checked_observation(db, p, observation))
        assert context['packet'] == packet and context['pair'].data['result_hash'] == result_hash
        assert {identity: db.get(m.Audit, identity).data for identity in anchors} == anchors
        assert readonly(lambda: observation_receipt(db, p, observation))['data_reception'] == received
        assert len(list(db.scalars(select(m.Invocation)))) == 1 and not list(db.scalars(select(m.CloudCall)))
        assert all(db.get(m.Entity, identity).version == 1 for identity in context['task'].data['entity_ids'])


def test_actual_learned_independent_model_call_and_issued_faults_do_not_fall_back(actual_learned):
    value = actual_learned; p = value['principal']
    faults = ('lease', 'input', 'result', 'ready', 'marker', 'issued', 'both_issued',
        'trace', 'artifact', 'memory', 'memory_packet')
    with Session(engine()) as db:
        for fault in faults:
            observation = db.get(m.Observation, value['observation_id'])
            context = origin.checked_observation(db, p, observation)
            transaction = db.begin_nested()
            try:
                if fault == 'lease':
                    context['call'].data = {**context['call'].data, 'lease_token': str(uuid4())}
                elif fault == 'input':
                    db.delete(context['input_audit'])
                elif fault == 'result':
                    db.delete(context['result_audit'])
                elif fault == 'ready':
                    db.delete(context['model_ready_audit'])
                elif fault in {'marker', 'both_issued'}:
                    data = copy.deepcopy(observation.data); data.pop(origin.ISSUED); observation.data = data
                elif fault == 'trace':
                    data = copy.deepcopy(context['pair'].data)
                    data['model_trace']['generation'] += 1; data['model_trace_hash'] = digest(data['model_trace'])
                    context['pair'].data = data
                elif fault == 'artifact':
                    context['artifacts']['probability_data'].sha256 = '0' * 64
                elif fault in {'memory', 'memory_packet'}:
                    memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
                        m.Memory.data['event_id'].as_string() == context['event'].id,
                        m.Memory.data['type'].as_string() == 'observation_evidence'))
                    if fault == 'memory':
                        db.delete(memory)
                    else:
                        data = copy.deepcopy(memory.data); data['native_receipt']['attributes']['差异候选像素数'] += 1
                        memory.data = data
                if fault in {'issued', 'both_issued'}:
                    db.delete(context['issued_audit'])
                db.flush()
                with pytest.raises(HTTPException) as rejected:
                    readonly(lambda: observation_receipt(db, p, observation))
                assert rejected.value.status_code == 409, fault
            finally:
                transaction.rollback()
            assert readonly(lambda: observation_receipt(db, p, observation))['data_reception']['run_id'] == value['pair_id']


def test_actual_learned_public_retry_reuses_pair_but_never_replaces_first_generation_proof(client, actual_learned, monkeypatch):
    from generic_agents import learned_geo_pairs
    value = actual_learned; p = value['principal']
    with Session(engine()) as db:
        original = db.get(m.Task, value['task_id'])
        base = '/api/workspace/instances/' + original.data['instance_id']
        body = {'release_id': original.data['instance_release_id'], 'business_id': original.data['business_id'],
            'entity_ids': original.data['entity_ids'], 'title': '明确重试同一原模型范围的第二业务任务',
            'comparison_scene_ids': [item['id'] for item in original.data['comparison_scene_snapshots']],
            'comparison_confirmed': True, 'request_id': str(uuid4())}
    task = good(client.post(base + '/tasks', json=body), 201)
    actual_call = learned_geo_pairs.call

    def failed_transport(*_args, **_kwargs):
        raise ValueError('本次本地模型transport明确失败，须原输入显式重试')

    with monkeypatch.context() as fault:
        fault.setattr(learned_geo_pairs, 'call', failed_transport)
        failed = finish_job(client, task)
    assert failed['status'] == 'workflow_failed' and learned_geo_pairs.call is actual_call
    with Session(engine()) as db:
        pair = db.scalar(select(m.GeoPair).where(m.GeoPair.workspace_id == p.workspace_id,
            m.GeoPair.data['workflow_task_id'].as_string() == task['id']))
        assert pair is not None and pair.status == 'failed' and pair.data['generation'] == 1
        pair_id, request_id, request_hash = pair.id, pair.data['request_id'], pair.data['request_hash']
        first = origin.input_audits(db, p, pair_id)
        assert len(first) == 1
        first_id, first_data = first[0].id, copy.deepcopy(first[0].data)
    retried = finish_job(client, good(run(client, failed, retry=True)))
    assert retried['status'] == 'workflow_waiting'
    assert retried['workflow_runtime']['receipts'][0]['output']['geo_pair_id'] == pair_id
    with Session(engine()) as db:
        context = readonly(lambda: origin.checked_pair(db, p, pair_id))
        assert context['pair'].data['generation'] == 2
        assert context['pair'].data['request_id'] == request_id and context['pair'].data['request_hash'] == request_hash
        assert len(origin.input_audits(db, p, pair_id)) == 2
        assert db.get(m.Audit, first_id).data == first_data and context['input_audit'].id != first_id
        queued = db.scalar(select(m.Job).where(m.Job.kind == 'sync_native_receipts',
            m.Job.data['original_task_id'].as_string() == task['id']))
        assert queued is not None and queued.status == 'queued'
        queue_id = queued.id
    assert process_once(queue_id, deliver=False)
    deliver_events()
    with Session(engine()) as db:
        observations = list(db.scalars(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
            m.Observation.data['native_packet']['source_key'].as_string() == 'GeoPair:' + pair_id)))
        assert len(observations) == 1
        assert readonly(lambda: observation_receipt(db, p, observations[0]))['data_reception']['run_generation'] == 2
        assert db.get(m.Audit, first_id).data == first_data and not list(db.scalars(select(m.CloudCall)))
