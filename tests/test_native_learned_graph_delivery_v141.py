"""Real first-input eligibility, atomic receive/capture and native admission.

The sole injected failure runs after the real intent/queue writer, to verify
savepoint rollback. No actor, source, receipt, model or success gate is patched.
"""
from uuid import uuid4

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventIntent, GraphEventAdmission, GraphEventStream
from generic_agents.worker import deliver_events, process_once, SETUP_KINDS
from tests.test_native_learned_graph_registration_v141 import registered_learned_scene, good, wire, inventory, rasters, pytestmark
from tests.test_instance_workspace import complete
from tests.test_workflow_abilities import finish_job


def new_candidate(client, scene, workspace):
    creation = {**scene['creation'], 'request_id': str(uuid4()), 'title': '登记后真实新输入的本地学习变化候选'}
    task = finish_job(client, good(client.post(scene['base'] + '/tasks', json=creation), 201))
    assert task['status'] == 'workflow_waiting'
    pair_id = task['workflow_runtime']['receipts'][0]['output']['geo_pair_id']
    with Session(engine()) as db:
        queued = db.scalar(select(m.Job).where(m.Job.workspace_id == workspace,
            m.Job.kind == 'sync_native_receipts', m.Job.data['original_task_id'].as_string() == task['id']))
        assert queued is not None and queued.status == 'queued'
        sync_id = queued.id
    assert process_once(sync_id, kinds=SETUP_KINDS, deliver=False, workspace_id=workspace)
    with Session(engine()) as db:
        observation = db.scalar(select(m.Observation).where(m.Observation.workspace_id == workspace,
            m.Observation.data['native_packet']['source_key'].as_string() == 'GeoPair:' + pair_id))
        assert observation is not None and 'data_reception' not in observation.data
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace,
            m.Event.idempotency_key == 'data:' + observation.id))
        assert event is not None and event.status == 'pending_delivery'
        assert not list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.observation_id == observation.id)))
        assert not list(db.scalars(select(m.Memory.id).where(m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence')))
        return {'task': task, 'pair_id': pair_id, 'observation_id': observation.id, 'event_id': event.id}


def test_real_received_learned_result_captures_once_admits_and_preserves_history_after_stop(client, rasters, account):
    scene = registered_learned_scene(client, rasters)
    current = new_candidate(client, scene, account[2])
    deliver_events(workspace_id=account[2], observation_id=current['observation_id'])
    with Session(engine()) as db:
        obs, event = db.get(m.Observation, current['observation_id']), db.get(m.Event, current['event_id'])
        assert event.status == 'received' and obs.data['data_reception']['learned_model']
        memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == account[2],
            m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence')))
        assert len(memories) == 1 and memories[0].status == 'candidate'
        intents = list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.observation_id == obs.id)))
        assert len(intents) == 1 and intents[0].producer_id == scene['producer']['id']
        intent = intents[0]; projection = intent.data['projection']; output = projection['output']
        assert output['data_received'] and output['candidate_only'] and output['observed_at'] is None
        assert output['candidate_pixels'] == obs.data['native_packet']['attributes']['差异候选像素数']
        assert output['unknown_pixels'] == obs.data['native_packet']['attributes']['未知像素数']
        assert projection['proof']['receipt_actor_id'] == intent.actor_id == obs.data['submitted_by']
        job = db.scalar(select(m.Job).where(m.Job.idempotency_key == 'typed-graph-source:' + intent.id))
        assert job is not None and job.kind == 'admit_graph_source_event'
        job_id, intent_id = job.id, intent.id
        original_model_hash = digest(db.get(m.GeoPair, current['pair_id']).data)
    before = inventory()
    assert process_once(job_id, kinds=SETUP_KINDS, deliver=False, workspace_id=account[2])
    with Session(engine()) as db:
        intent = db.get(GraphEventIntent, intent_id)
        assert intent.status == 'admitted' and intent.receipt_id
        admission_id = intent.receipt_id
        assert db.get(m.Job, job_id).status == 'succeeded'
        assert len(list(db.scalars(select(GraphEventAdmission)))) == 1
    path = '/api/setup/graph-event-producers/admissions/' + admission_id
    admission = good(client.get(path))
    assert admission['projection'] == projection
    task = good(client.get('/api/workspace/tasks/' + current['task']['id']))
    good(complete(client, task, note='已核对原模型、A/B范围及保存候选；现场变化尚待核实。'))
    assert good(client.get(path)) == admission
    stages = wire(client.get(scene['producer_base'] + '/observations/' + current['observation_id'] + '/stages'))
    assert stages['data_received'] and stages['deliveries'][0]['admission']['id'] == admission_id
    assert stages['waiting_match'] == 'not_checked'
    stop = {'schema': 'graph-event-native-learned-geo-producer-revocation-v1', 'request_id': str(uuid4()),
        'instance_id': scene['release']['instance_id'], 'producer_id': scene['producer']['id'],
        'producer_version': scene['producer']['version'], 'contract_hash': scene['producer']['contract_hash'],
        'reason': '停止结果来源，保留原接收与已保存历史'}
    stopped = wire(client.post(scene['producer_base'] + '/revocations', json=stop))
    assert stopped['receipt']['result']['status'] == 'revoked'
    assert wire(client.get(scene['producer_base'] + '/revocations/requests/' + stop['request_id']))['receipt'] == stopped['receipt']
    assert good(client.get(path)) == admission
    deliver_events(workspace_id=account[2], observation_id=current['observation_id'])
    after = inventory()
    for key in ('GeoPair', 'Invocation', 'CloudCall', 'GraphEventIntent'):
        assert after[key] == before[key]
    with Session(engine()) as db:
        assert digest(db.get(m.GeoPair, current['pair_id']).data) == original_model_hash
        assert all(db.get(m.Entity, identity).version == 1 for identity in scene['creation']['entity_ids'])
        assert len(list(db.scalars(select(m.Invocation)))) == 2 and not list(db.scalars(select(m.CloudCall)))


def test_capture_failure_rolls_back_actual_memory_ack_intent_and_queue_but_keeps_model_result(client, rasters, account, monkeypatch):
    scene = registered_learned_scene(client, rasters)
    current = new_candidate(client, scene, account[2])
    with Session(engine()) as db:
        original_pair_hash = digest(db.get(m.GeoPair, current['pair_id']).data)
        original_packet_hash = digest(db.get(m.Observation, current['observation_id']).data['native_packet'])
    from generic_agents import graph_event_dispatch
    actual_queue = graph_event_dispatch.queue_intent
    observed = []

    def fail_after_actual_queue(db, p, producer, intent):
        actual_queue(db, p, producer, intent)
        observed.append(intent.id)
        raise ValueError('synthetic transaction failure after real source authorization and queue write')

    with monkeypatch.context() as fault:
        fault.setattr(graph_event_dispatch, 'queue_intent', fail_after_actual_queue)
        deliver_events(workspace_id=account[2], observation_id=current['observation_id'])
    assert len(observed) == 1
    with Session(engine()) as db:
        pair, obs, event = db.get(m.GeoPair, current['pair_id']), db.get(m.Observation, current['observation_id']), db.get(m.Event, current['event_id'])
        assert pair.status == 'succeeded' and pair.data['result']['learned_model'] is True
        assert event.status == 'blocked_delivery' and event.attempts == 1
        assert 'data_reception' not in event.data and 'data_reception' not in obs.data
        assert not list(db.scalars(select(m.Memory).where(m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence')))
        assert not list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.observation_id == obs.id)))
        assert not list(db.scalars(select(GraphEventAdmission)))
        assert not list(db.scalars(select(m.Job).where(m.Job.idempotency_key == 'typed-graph-source:' + observed[0])))
        assert len(list(db.scalars(select(m.Invocation)))) == 2 and not list(db.scalars(select(m.CloudCall)))
        assert all(db.get(m.Entity, identity).version == 1 for identity in scene['creation']['entity_ids'])

    stages_path = scene['producer_base'] + '/observations/' + current['observation_id'] + '/stages'
    retry_path = scene['producer_base'] + '/observations/' + current['observation_id'] + '/retry-data-delivery'
    blocked = wire(client.get(stages_path))
    assert blocked['data_status'] == 'blocked' and not blocked['data_received'] and blocked['deliveries'] == []
    original_version = blocked['observation_version']
    command = {'expected_version': original_version, 'reason': '已恢复原送达故障，仅重试此候选的原数据接收'}
    assert client.post(retry_path, json={**command, 'expected_version': original_version + 1}).status_code == 409
    recovered = wire(client.post(retry_path, json=command))
    assert recovered['data_status'] == 'received' and recovered['data_received']
    assert recovered['observation_version'] == original_version + 1
    assert len(recovered['deliveries']) == 1 and recovered['deliveries'][0]['admission'] is None
    with Session(engine()) as db:
        obs, event = db.get(m.Observation, current['observation_id']), db.get(m.Event, current['event_id'])
        assert event.status == 'received' and event.attempts == 2
        memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == account[2],
            m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence')))
        intents = list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.observation_id == obs.id)))
        assert len(memories) == len(intents) == 1
        assert memories[0].data['data_reception'] == obs.data['data_reception'] == event.data['data_reception']
        intent = intents[0]
        jobs = list(db.scalars(select(m.Job).where(m.Job.idempotency_key == 'typed-graph-source:' + intent.id)))
        assert len(jobs) == 1 and jobs[0].kind == 'admit_graph_source_event' and jobs[0].status == 'queued'
        admit_job_id = jobs[0].id
        recovery_audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == account[2],
            m.Audit.object_id == obs.id, m.Audit.action == 'observation.native_learned_data_delivery_retried_v141')))
        assert len(recovery_audits) == 1 and recovery_audits[0].actor_id == scene['producer']['registration']['actor_id']
        assert recovery_audits[0].data == {'event_id': event.id, 'reason': command['reason'], 'observation_version': obs.version}
        assert digest(db.get(m.GeoPair, current['pair_id']).data) == original_pair_hash
        assert digest(obs.data['native_packet']) == digest(event.data['native_receipt']) == original_packet_hash
        assert len(list(db.scalars(select(m.Invocation)))) == 2 and not list(db.scalars(select(m.CloudCall)))
    assert process_once(admit_job_id, kinds=SETUP_KINDS, deliver=False, workspace_id=account[2])
    saved = wire(client.get(stages_path))
    assert saved['data_received'] and saved['deliveries'][0]['admission'] is not None

    def saved_inventory():
        with Session(engine()) as db:
            row_counts = {table.name: db.scalar(select(func.count()).select_from(table))
                for table in m.Base.metadata.tables.values()}
            obs, event = db.get(m.Observation, current['observation_id']), db.get(m.Event, current['event_id'])
            intent = db.scalar(select(GraphEventIntent).where(GraphEventIntent.observation_id == obs.id))
            stream = db.scalar(select(GraphEventStream).where(GraphEventStream.workspace_id == account[2]))
            return row_counts, (obs.version, digest(obs.data), event.status, event.attempts, digest(event.data),
                intent.id, intent.status, intent.receipt_id, digest(intent.data), stream.last_sequence,
                digest(db.get(m.GeoPair, current['pair_id']).data))

    stable = saved_inventory()
    # Lost-response recovery accepts the original now-old CAS version only after
    # the actual Memory/ACK is cold-verified, without another delivery or model.
    assert wire(client.post(retry_path, json=command)) == saved
    assert wire(client.get(stages_path)) == saved and saved_inventory() == stable


def test_matching_registration_with_only_record_tag_corrupted_blocks_first_input_instead_of_losing_eligibility(client, rasters, account):
    import copy
    scene = registered_learned_scene(client, rasters)
    creation = {**scene['creation'], 'request_id': str(uuid4()), 'title': '核对原登记族标签损坏时的首次输入边界'}
    task = good(client.post(scene['base'] + '/tasks', json=creation), 201)
    job_id = task['workflow_runtime']['automatic']['job_id']
    with Session(engine()) as db:
        from generic_agents.graph_event_models import GraphEventProducer
        producer = db.get(GraphEventProducer, scene['producer']['id'])
        original = {column.name: copy.deepcopy(getattr(producer, column.key))
            for column in GraphEventProducer.__table__.columns
            if column.name == 'data' or column.info.get('typed_payload_internal')}
        registration = copy.deepcopy(producer.data['registration'])
        contract = copy.deepcopy(producer.data['contract'])
        producer.data = {**producer.data, 'schema': 'graph-event-native-audio-producer-record-v1'}
        db.commit()
    try:
        assert process_once(job_id, kinds=SETUP_KINDS, deliver=False, workspace_id=account[2])
        refused = good(client.get('/api/workspace/tasks/' + task['id']))
        assert refused['status'] in {'workflow_failed', 'workflow_blocked'}, refused['status']
        with Session(engine()) as db:
            producer = db.get(GraphEventProducer, scene['producer']['id'])
            assert producer.data['registration'] == registration and producer.data['contract'] == contract
            pairs = list(db.scalars(select(m.GeoPair).where(m.GeoPair.workspace_id == account[2],
                m.GeoPair.data['workflow_task_id'].as_string() == task['id'])))
            assert len(pairs) == 1 and pairs[0].status != 'succeeded'
            assert pairs[0].data['result'] is None  # No actual neural result was silently accepted.
            calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == account[2],
                m.Invocation.data['job_id'].as_string() == job_id)))
            assert len(calls) == 1 and calls[0].status != 'succeeded'
            assert not calls[0].data.get('output')
            assert not list(db.scalars(select(GraphEventIntent)))
            assert not list(db.scalars(select(m.CloudCall)))
    finally:
        with Session(engine()) as db:
            db.execute(GraphEventProducer.__table__.update().where(GraphEventProducer.id == scene['producer']['id'])
                .values(**copy.deepcopy(original)))
            db.commit()
    assert wire(client.get(scene['producer_base'] + '/requests/' + scene['registration']['request_id'])) == {
        'found': True, 'producer': scene['producer']}
    exported = good(client.get(scene['base'] + '/native-receipts/' + scene['observation']['id'] + '/export'))
    assert exported['observation']['data_received'] and exported['observation']['integrity_error'] is None
