"""Real synthetic observation/Invocation/worker admission on owned PostgreSQL."""
import copy
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta
from threading import Event as ThreadEvent
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Capability, CloudCall, Invocation, Job, Membership, Observation, now
from generic_agents.graph_event_models import GraphEventProducer, GraphEventIntent, GraphEventAdmission, GraphEventStream
from generic_agents.graph_event_delivery_recovery_v124 import GraphEventIntentRetryReceipt
from generic_agents.graph_event_admission import admit, read_intent, read_admission
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_data_gaps import setup

BASE = '/api/setup/graph-event-producers'


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def scene(client, adapters=('processed-observation-v1',)):
    _, obj, source, extractor = setup(client)
    with Session(engine()) as db:
        source_hash = db.get(Capability, source['id']).content_hash
        extractor_hash = db.get(Capability, extractor['id']).content_hash
        member = db.scalar(select(Membership))
        p = Principal(member.user_id, member.workspace_id, member.role, 'synthetic-admission-test')
    commands = [{'schema': 'graph-event-producer-command-v1', 'request_id': str(uuid4()),
        'label': '合成观测消息来源', 'adapter_id': adapter, 'source_id': source['id'],
        'source_hash': source_hash, 'extractor_id': extractor['id'], 'extractor_hash': extractor_hash}
        for adapter in adapters]
    producers = []
    for command in commands:
        preview = ok(client.post(BASE + '/preview', json=command))
        row = ok(client.post(BASE, json=command), 201)
        assert row['created'] and row['producer']['contract_hash'] == preview['contract_hash']
        assert not row['producer']['execution_granted']
        producers.append(row['producer'])
    return {'object': obj, 'source': source, 'extractor': extractor,
        'principal': p, 'commands': commands, 'producers': producers}


def observe(client, value, temperature=28, expected='succeeded'):
    at = now() - timedelta(seconds=1)
    with Session(engine()) as db:
        source_timezone = db.get(Capability, value['source']['id']).data['specification'].get('timezone', 'UTC')
    body = {'source_id': value['source']['id'], 'source_record_id': uuid4().hex,
        'entity_id': value['object']['id'], 'observed_at': at.isoformat(),
        'business_day': str(at.astimezone(ZoneInfo(source_timezone)).date()), 'attributes': {'temperature': temperature},
        'extractor_id': value['extractor']['id'], 'startup_mode': 'manual'}
    row = ok(client.post('/api/observations', json=body), 201)
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.idempotency_key == 'extract:' + row['id']))
        identity = job.id
    assert process_once(identity)
    with Session(engine()) as db:
        assert db.get(Job, identity).status == expected, db.get(Job, identity).data
        assert (db.get(Observation, row['id']).status == 'processed') is (expected == 'succeeded')
    return row, body, identity


def pending_intents(observation_id):
    with Session(engine()) as db:
        return [(row.id, row.producer_id) for row in db.scalars(select(GraphEventIntent)
            .where(GraphEventIntent.observation_id == observation_id).order_by(GraphEventIntent.id))]


def delivery_job(intent_id):
    with Session(engine()) as db:
        row = db.scalar(select(Job).where(Job.idempotency_key == 'typed-graph-source:' + intent_id))
        return row.id, row.version


def test_actual_processing_creates_two_frozen_intents_and_real_worker_admissions(client):
    value = scene(client, ('processed-observation-v1', 'anomaly-candidate-v1'))
    observation, original, _ = observe(client, value, 105)
    intents = pending_intents(observation['id']); assert len(intents) == 2
    for identity, _ in intents:
        job, _ = delivery_job(identity)
        assert process_once(job)
        with Session(engine()) as db:
            assert db.get(Job, job).status == 'succeeded', db.get(Job, job).data
    with Session(engine()) as db:
        receipts = list(db.scalars(select(GraphEventAdmission).order_by(GraphEventAdmission.sequence)))
        assert [row.sequence for row in receipts] == [1, 2]
        assert db.scalar(select(GraphEventStream.last_sequence)) == 2
        assert len(list(db.scalars(select(Invocation)))) == 1
        assert db.scalar(select(CloudCall.id)) is None
        for row in receipts:
            receipt = read_admission(db, value['principal'], row.id)
            output = receipt['projection']['output']
            assert receipt['projection']['correlation'] == value['object']['id']
            assert output['result']['attributes']['temperature'] == 105
            assert output['source_time_verified'] is False and output['physical_state_verified'] is False
            assert datetime.fromisoformat(output['observed_at']) == datetime.fromisoformat(original['observed_at'])
            assert row.admitted_at > db.get(Observation, observation['id']).observed_at
            assert ok(client.get(BASE + '/admissions/' + row.id)) == receipt
    repeated = ok(client.post('/api/observations', json=original), 201)
    assert repeated['id'] == observation['id']
    assert len(pending_intents(observation['id'])) == 2


def test_registration_nonce_cas_and_revocation_preserve_original_receipts(client):
    value = scene(client); command = value['commands'][0]; producer = value['producers'][0]
    replay = ok(client.post(BASE, json=command), 201); assert not replay['created']
    changed = {**command, 'label': '不能用同号换另一份登记'}
    assert client.post(BASE, json=changed).status_code == 409
    observation, _, _ = observe(client, value)
    identity, _ = pending_intents(observation['id'])[0]
    job, _ = delivery_job(identity); assert process_once(job)
    before = ok(client.get(BASE + '/' + producer['id'] + '/intents'))
    receipt_id = before[0]['receipt_id']; assert receipt_id
    revoked = ok(client.post(BASE + '/' + producer['id'] + '/revoke',
        json={'version': producer['version'], 'reason': '核对后停止新消息接纳'}))
    assert revoked['status'] == 'revoked'
    assert client.post(BASE + '/' + producer['id'] + '/revoke',
        json={'version': producer['version'], 'reason': '旧状态不能重复修改'}).status_code == 409
    assert ok(client.post(BASE, json=command), 201)['producer']['status'] == 'revoked'
    with Session(engine()) as db:
        original, created = admit(db, value['principal'], identity)
        assert not created and original.id == receipt_id
        db.commit()
    assert ok(client.get(BASE + '/admissions/' + receipt_id))['intent_id'] == identity


def test_old_processed_source_cannot_be_backfilled_by_registration_or_job_redelivery(client):
    value = scene(client, ())
    observation, _, job_id = observe(client, value)
    assert not pending_intents(observation['id'])
    with Session(engine()) as db:
        src = db.get(Capability, value['source']['id']); ext = db.get(Capability, value['extractor']['id'])
        command = {'schema': 'graph-event-producer-command-v1', 'request_id': str(uuid4()),
            'label': '登记仅影响未来真实处理', 'adapter_id': 'processed-observation-v1',
            'source_id': src.id, 'source_hash': src.content_hash,
            'extractor_id': ext.id, 'extractor_hash': ext.content_hash}
    ok(client.post(BASE, json=command), 201)
    # Fault injection: redeliver the exact original completed source Job, not a
    # fabricated Event/Invocation or a newly numbered source processing result.
    with Session(engine()) as db:
        job = db.get(Job, job_id); job.status = 'queued'; job.lease_token = None; job.lease_until = None
        db.commit()
    assert process_once(job_id)
    assert not pending_intents(observation['id'])
    with Session(engine()) as db:
        assert db.scalar(select(GraphEventAdmission.id)) is None
        assert len(list(db.scalars(select(Invocation)))) == 1


def test_lost_worker_completion_is_reconciled_from_actual_admission_without_reprocessing(client, monkeypatch):
    value = scene(client); observation, _, _ = observe(client, value)
    identity, producer_id = pending_intents(observation['id'])[0]; job_id, _ = delivery_job(identity)
    from generic_agents import graph_event_dispatch
    process = graph_event_dispatch.process
    def lose_completion(db, p, job):
        result = process(db, p, job)
        raise RuntimeError('synthetic crash after actual admission commit, before Job completion')
    monkeypatch.setattr(graph_event_dispatch, 'process', lose_completion)
    assert process_once(job_id)
    monkeypatch.setattr(graph_event_dispatch, 'process', process)
    with Session(engine()) as db:
        job = db.get(Job, job_id); assert job.status == 'failed'
        intent = db.get(GraphEventIntent, identity); assert intent.receipt_id
        original_receipt = read_admission(db, value['principal'], intent.receipt_id)
        version = job.version
    response = ok(client.post(BASE + '/intents/' + identity + '/retry', json={
        'request_id': str(uuid4()), 'job_version': version, 'reason': '查询原消息是否已经持久接纳'}))
    assert response['already_admitted'] and response['admission'] == original_receipt
    with Session(engine()) as db:
        assert db.get(Job, job_id).status == 'succeeded'
        assert db.scalar(select(GraphEventStream.last_sequence)) == 1
        assert len(list(db.scalars(select(Invocation)))) == 1
    assert ok(client.get(BASE + '/' + producer_id + '/intents'))[0]['receipt_id'] == original_receipt['id']


def test_pending_admission_revocation_is_visible_and_never_allocates_a_sequence(client):
    value = scene(client); observation, _, _ = observe(client, value)
    identity, producer_id = pending_intents(observation['id'])[0]; job_id, _ = delivery_job(identity)
    producer = value['producers'][0]
    ok(client.post(BASE + '/' + producer_id + '/revoke',
        json={'version': producer['version'], 'reason': '接纳前撤销来源'}))
    assert process_once(job_id)
    rows = ok(client.get(BASE + '/' + producer_id + '/intents'))
    assert rows[0]['id'] == identity and rows[0]['status'] == 'pending' and rows[0]['receipt_id'] is None
    assert rows[0]['delivery']['status'] == 'failed' and rows[0]['delivery']['error']
    with Session(engine()) as db:
        assert db.scalar(select(GraphEventAdmission.id)) is None
        assert db.scalar(select(GraphEventStream.id)) is None


def test_original_admission_job_can_explicitly_recover_after_three_failures(client, monkeypatch):
    value = scene(client); observation, _, _ = observe(client, value)
    identity, producer_id = pending_intents(observation['id'])[0]; job_id, _ = delivery_job(identity)
    from generic_agents import graph_event_dispatch
    real_admit = graph_event_dispatch.admit
    def unavailable(*args, **kwargs):
        raise RuntimeError('synthetic admission service failure, no source/model reprocessing')
    monkeypatch.setattr(graph_event_dispatch, 'admit', unavailable)
    for _ in range(3):
        assert process_once(job_id)
        status = ok(client.get(BASE + '/' + producer_id + '/intents'))[0]['delivery']
        assert status['status'] == 'failed' and status['can_retry']
        body = {'request_id': str(uuid4()), 'job_version': status['version'], 'reason': '恢复原消息接纳'}
        result = ok(client.post(BASE + '/intents/' + identity + '/retry', json=body))
        assert not result['replayed'] and result['delivery']['status'] == 'queued'
        replay = ok(client.post(BASE + '/intents/' + identity + '/retry', json=body))
        assert replay['replayed'] and replay['retry'] == result['retry']
        assert client.post(BASE + '/intents/' + identity + '/retry',
            json={**body, 'reason': '同编号不能换原恢复条件'}).status_code == 409
    monkeypatch.setattr(graph_event_dispatch, 'admit', real_admit)
    assert process_once(job_id)
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        assert job.status == 'succeeded' and job.data['recovery_only'] is True
        assert job.attempts == 3 and job.data['lease_recoveries'] == 0
        retries = list(db.scalars(select(GraphEventIntentRetryReceipt).where(GraphEventIntentRetryReceipt.job_id == job_id)))
        assert len(retries) == 3 and all(row.outcome == 'queued' for row in retries)
        assert job.data.get('graph_event_retries', []) == []
        assert db.get(GraphEventIntent, identity).receipt_id
        assert db.scalar(select(GraphEventStream.last_sequence)) == 1
        assert len(list(db.scalars(select(Invocation)))) == 1


def test_source_capture_failure_rolls_back_intents_and_recovers_the_original_successful_invocation(client, monkeypatch):
    value = scene(client)
    from generic_agents import graph_event_dispatch
    capture = graph_event_dispatch.capture_source
    def lose_source_transaction(*args, **kwargs):
        capture(*args, **kwargs)
        raise RuntimeError('synthetic failure after real source intent insert before source commit')
    monkeypatch.setattr(graph_event_dispatch, 'capture_source', lose_source_transaction)
    observation, _, source_job = observe(client, value, expected='failed')
    assert not pending_intents(observation['id'])
    with Session(engine()) as db:
        row = db.get(Observation, observation['id']); version = row.version
        calls = list(db.scalars(select(Invocation)))
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        original_call = calls[0].id
        assert db.scalar(select(GraphEventAdmission.id)) is None
    monkeypatch.setattr(graph_event_dispatch, 'capture_source', capture)
    ok(client.post('/api/observations/' + observation['id'] + '/retry', json={
        'expected_version': version, 'reason': '恢复原成功处理回执并保存消息意图'}))
    assert process_once(source_job)
    identities = pending_intents(observation['id']); assert len(identities) == 1
    with Session(engine()) as db:
        row = db.get(Observation, observation['id'])
        assert row.status == 'processed' and row.data['invocation_id'] == original_call
        assert len(list(db.scalars(select(Invocation)))) == 1
        assert db.get(Job, source_job).data['resume_invocation_key']
    delivery_id, _ = delivery_job(identities[0][0]); assert process_once(delivery_id)
    with Session(engine()) as db:
        assert db.get(GraphEventIntent, identities[0][0]).receipt_id
        assert db.scalar(select(GraphEventStream.last_sequence)) == 1
        assert db.scalar(select(CloudCall.id)) is None


def test_actual_rollback_and_two_connection_gate_order_use_admission_not_legacy_time(client):
    value = scene(client); first, _, _ = observe(client, value); second, _, _ = observe(client, value)
    one = pending_intents(first['id'])[0][0]; two = pending_intents(second['id'])[0][0]
    with Session(engine()) as db:
        receipt, created = admit(db, value['principal'], one); assert created
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(GraphEventAdmission.id)) is None
        assert db.get(GraphEventIntent, one).receipt_id is None
    admitted = ThreadEvent(); commit = ThreadEvent(); started = ThreadEvent(); stamps = {}
    def receive_first():
        with Session(engine(), expire_on_commit=False) as db:
            row, created = admit(db, value['principal'], one); assert created
            stamps['admission'] = row.admitted_at; admitted.set()
            assert commit.wait(15)
            db.commit(); return row.id, row.sequence
    def receive_second():
        started.set()
        with Session(engine(), expire_on_commit=False) as db:
            row, created = admit(db, value['principal'], two); assert created
            db.commit(); return row.id, row.sequence
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(receive_first); assert admitted.wait(15)
        b = pool.submit(receive_second); assert started.wait(15)
        try:
            b.result(timeout=.2)
            raise AssertionError('a second uncommitted admission crossed the stream gate')
        except TimeoutError:
            pass
        finally:
            with Session(engine()) as db:
                assert db.scalar(select(GraphEventAdmission.id)) is None
                stamps['before_commit'] = db.scalar(select(func.clock_timestamp()))
            commit.set()
        assert a.result(timeout=15)[1] == 1
        assert b.result(timeout=15)[1] == 2
    assert stamps['admission'] < stamps['before_commit']
    with Session(engine()) as db:
        assert db.scalar(select(GraphEventStream.last_sequence)) == 2
        assert len(list(db.scalars(select(GraphEventAdmission)))) == 2
        assert read_intent(db, value['principal'], one)['source_event_id']
