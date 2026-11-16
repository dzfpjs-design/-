"""Lease races use actual PostgreSQL transactions and independent HTTP service waits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import timedelta
import threading
import time
import os
import json
import subprocess
import sys
from pathlib import Path
import pytest
from sqlalchemy import select, update, text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job, DataRun, User, Invocation, Membership, Acquisition, SourceReservation, now
from generic_agents import leases
from generic_agents.worker import claim, process_once
from .test_business_tools import service, setup, saved_task
from .test_vertical_slice import manifest, publish


def query_job(account, **kwargs):
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        row = DataRun(workspace_id=account[2], owner_id=user.id, question='合成租约验收', status='queued',
            data={'request': {'mode': 'structured', 'entity_ids': [], 'fields': [], 'question': '合成租约验收'}})
        db.add(row); db.flush()
        job = Job(workspace_id=account[2], kind='data_query', idempotency_key='query:' + row.id,
            data={'query_id': row.id, 'actor_id': user.id}, **kwargs)
        db.add(job); db.commit(); return row.id, job.id


def expire(job_id):
    with engine().begin() as connection:
        connection.execute(update(Job).where(Job.id == job_id).values(lease_until=now() - timedelta(seconds=1)))


def approved(client, monkeypatch, service, side_effect=False):
    _, _, _, task, plan = setup(client, monkeypatch, service, side_effect=side_effect)
    assert client.post('/api/plans/' + plan['id'] + '/approve', json={
        'expected_version': plan['version'], 'reason': '合成后台恢复验收'}).status_code == 200
    with Session(engine()) as db:
        job_id = db.scalar(select(Job.id).where(Job.kind=='execute_plan',Job.data['plan_id'].as_string() == plan['id']))
    return task, plan, job_id


def test_expired_transaction_rolls_back_intermediate_state(account, monkeypatch):
    monkeypatch.setattr(leases, 'LEASE_SECONDS', 0.2)
    row_id, job_id = query_job(account)
    _, token = claim(job_id)
    with Session(engine()) as db:
        leases.attach(db, job_id, token)
        row = db.get(DataRun, row_id); row.status = 'sufficient'
        db.execute(text('SELECT pg_sleep(0.25)'))
        with pytest.raises(leases.LeaseLost):
            db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(DataRun, row_id).status == 'queued'


def test_stale_cached_object_cannot_commit_after_takeover(account):
    row_id, job_id = query_job(account)
    _, token = claim(job_id)
    with Session(engine(), expire_on_commit=False) as old:
        leases.attach(old, job_id, token)
        row = old.get(DataRun, row_id); old.commit()
        expire(job_id)
        _, new_token = claim(job_id)
        assert new_token != token
        row.status = 'sufficient'
        with pytest.raises(leases.LeaseLost):
            old.commit()
        old.rollback()
    with Session(engine()) as db:
        assert db.get(DataRun, row_id).status == 'queued'


def test_concurrent_claim_has_only_one_owner(account):
    _, job_id = query_job(account)
    barrier = threading.Barrier(3)
    def compete():
        barrier.wait(); return claim(job_id)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: compete(), range(3)))
    assert len([r for r in results if r]) == 1


def test_heartbeat_keeps_slow_network_call_owned(client, monkeypatch, service):
    task, _, job_id = approved(client, monkeypatch, service)
    # Keep the HTTP wait longer than the initial lease, while avoiding a subsecond
    # deadline that conflates host/DB scheduling stalls with missing renewal.
    monkeypatch.setattr(leases, 'LEASE_SECONDS', 2.0)
    monkeypatch.setattr(leases, 'HEARTBEAT_SECONDS', 0.1)
    renewal_events = []
    original_renew = leases.renew
    def tracked_renew(*args):
        started = time.monotonic()
        try:
            renewed = original_renew(*args)
            renewal_events.append((round(time.monotonic()-started,3), renewed))
            return renewed
        except Exception as error:
            renewal_events.append((round(time.monotonic()-started,3), type(error).__name__))
            raise
    monkeypatch.setattr(leases, 'renew', tracked_renew)
    service['entered'] = threading.Event(); service['release'] = threading.Event()
    worker = threading.Thread(target=lambda: process_once(job_id)); worker.start()
    try:
        assert service['entered'].wait(3)
        with Session(engine()) as db:
            original_deadline = db.get(Job, job_id).lease_until
        threading.Event().wait(2.5)
        assert now() > original_deadline, 'HTTP call must remain blocked beyond the original lease'
        assert claim(job_id) is None, f'Heartbeat renewal duration/result samples: {renewal_events[-8:]}'
        with Session(engine()) as db:
            assert db.get(Job, job_id).lease_until > original_deadline
    finally:
        service['release'].set(); worker.join(timeout=5)
    assert not worker.is_alive()
    assert saved_task(client, task['id'])['status'] == 'awaiting_acceptance'
    assert service['calls'] == 1


@pytest.mark.parametrize('side_effect', [False, True])
def test_takeover_fences_late_response_and_preserves_recovery(client, monkeypatch, service, side_effect):
    task, plan, job_id = approved(client, monkeypatch, service, side_effect)
    monkeypatch.setattr(leases, 'heartbeat', lambda *args: nullcontext())
    service['entered'] = threading.Event(); service['release'] = threading.Event()
    worker = threading.Thread(target=lambda: process_once(job_id)); worker.start()
    try:
        assert service['entered'].wait(3)
        if side_effect:
            with Session(engine()) as db:
                live = db.scalar(select(Invocation).where(Invocation.data['job_id'].as_string() == job_id))
                live_id, live_version = live.id, live.version
            assert client.post('/api/invocations/' + live_id + '/reconcile', json={
                'expected_version': live_version, 'reason': '运行中不能先确认未执行再重发'}).status_code == 409
        expire(job_id)
        assert process_once(job_id)
        interrupted = saved_task(client, task['id'])
        result = interrupted['data']['step_receipts'][plan['id'] + ':0']['result']
        assert interrupted['status'] == 'waiting_tool'
        assert result['status'] == ('unknown' if side_effect else 'failed')
        with Session(engine()) as db:
            call = db.get(Invocation, result['invocation_id'])
            assert call.data['job_id'] == job_id and call.data['recovery']
    finally:
        service['release'].set(); worker.join(timeout=5)
    assert not worker.is_alive()
    current = saved_task(client, task['id'])
    assert current == interrupted  # Old worker response cannot overwrite any checkpoint or version.
    assert service['calls'] == 1
    if side_effect:
        assert client.post('/api/tasks/' + task['id'] + '/retry_execution', json={
            'expected_version': current['version'], 'reason': '禁止重复动作'}).status_code == 409
        call = client.get('/api/invocations/' + result['invocation_id']).json()
        assert client.post('/api/invocations/' + call['id'] + '/reconcile', json={
            'expected_version': call['version'], 'reason': '只读核对合成服务记录'}).status_code == 200
    else:
        assert client.post('/api/tasks/' + task['id'] + '/retry_execution', json={
            'expected_version': current['version'], 'reason': '明确重试只读计算'}).status_code == 200
    assert process_once()
    assert saved_task(client, task['id'])['status'] == 'awaiting_acceptance'
    assert service['calls'] == (1 if side_effect else 2)


def test_permission_revocation_during_call_blocks_old_result(client, monkeypatch, service):
    task, plan, job_id = approved(client, monkeypatch, service)
    service['entered'] = threading.Event(); service['release'] = threading.Event()
    worker = threading.Thread(target=lambda: process_once(job_id)); worker.start()
    try:
        assert service['entered'].wait(3)
        with Session(engine()) as db:
            membership = db.scalar(select(Membership).where(Membership.workspace_id == task['workspace_id']))
            membership.role = 'viewer'; db.commit()
    finally:
        service['release'].set(); worker.join(timeout=5)
    assert not worker.is_alive()
    with Session(engine()) as db:
        from generic_agents.models import Task
        row = db.get(Task, task['id'])
        assert row.status == 'waiting_tool' and not row.data.get('final_evidence')
        result = row.data['step_receipts'][plan['id'] + ':0']['result']
        assert result['status'] == 'failed'
        assert db.get(Job, job_id).status == 'failed'


def test_exhaustion_marks_query_failed_not_orphaned_running(account):
    row_id, job_id = query_job(account, status='running', attempts=3,
        lease_token='expired-token', lease_until=now() - timedelta(seconds=1))
    assert process_once(job_id)
    with Session(engine()) as db:
        assert db.get(DataRun, row_id).status == 'failed'
        assert db.get(Job, job_id).status == 'failed'
        assert db.get(Job, job_id).attempts == 3


def test_unexpected_query_failure_has_durable_error(account, monkeypatch):
    row_id, job_id = query_job(account)
    def broken(*args, **kwargs):
        raise RuntimeError('explicit_failure_fixture')
    monkeypatch.setattr('generic_agents.agents.data_agent', broken)
    assert process_once(job_id)
    with Session(engine()) as db:
        row = db.get(DataRun, row_id)
        assert row.status == 'failed' and row.data.get('error')
        assert db.get(Job, job_id).status == 'failed'


def test_connection_loss_rolls_back_and_records_recoverable_failure(account, monkeypatch):
    from sqlalchemy.exc import DBAPIError
    row_id, job_id = query_job(account)
    def disconnect(db, *args, **kwargs):
        try:
            db.execute(text('SELECT pg_terminate_backend(pg_backend_pid())'))
        except DBAPIError:
            raise
        db.execute(text('SELECT 1'))
    monkeypatch.setattr('generic_agents.agents.data_agent', disconnect)
    assert process_once(job_id)
    with Session(engine()) as db:
        assert db.get(DataRun, row_id).status == 'failed'
        assert db.get(Job, job_id).status == 'failed'


def test_missing_legacy_lease_can_be_recovered(account):
    row_id, job_id = query_job(account, status='running', attempts=1, lease_token='legacy', lease_until=None)
    assert process_once(job_id)
    with Session(engine()) as db:
        assert db.get(DataRun, row_id).status == 'insufficient'  # Empty scope stays an honest no-answer result.
        assert db.get(Job, job_id).status == 'succeeded'


def test_database_retry_loop_survives_transient_connection_error(monkeypatch):
    from generic_agents import worker
    from sqlalchemy.exc import OperationalError
    monkeypatch.setattr(worker, 'daily_reconcile', lambda: None)
    monkeypatch.setattr(worker, 'monitor_once', lambda: None)
    waits = []; calls = []
    monkeypatch.setattr(worker.time, 'sleep', lambda seconds: waits.append(seconds))
    class StopFixture(Exception): pass
    def process():
        calls.append(True)
        if len(calls) == 1:
            raise OperationalError('explicit fixture', {}, RuntimeError('fixture'))
        raise StopFixture()
    monkeypatch.setattr(worker, 'process_once', process)
    with pytest.raises(StopFixture): worker.run()
    assert len(calls) == 2 and waits == [2]


def test_business_commit_before_job_finish_is_idempotent(client, monkeypatch, service):
    task, _, job_id = approved(client, monkeypatch, service)
    assert process_once(job_id)
    before = saved_task(client, task['id'])
    assert before['status'] == 'awaiting_acceptance'
    with Session(engine()) as db:
        job = db.get(Job, job_id); job.status = 'running'; job.lease_until = now() - timedelta(seconds=1)
        db.commit()
    assert process_once(job_id)
    assert saved_task(client, task['id']) == before and service['calls'] == 1
    with Session(engine()) as db:
        assert db.get(Job, job_id).status == 'succeeded'


def test_temporary_not_executed_cannot_authorize_replay(client, monkeypatch, service):
    task, plan, job_id = approved(client, monkeypatch, service, True)
    service['unknown'] = True; assert process_once(job_id)
    current = saved_task(client, task['id'])
    identifier = current['data']['step_receipts'][plan['id'] + ':0']['result']['invocation_id']
    call = client.get('/api/invocations/' + identifier).json()
    service['receipt_status'] = 'not_executed'; service['terminal_receipt'] = False
    assert client.post('/api/invocations/' + identifier + '/reconcile', json={
        'expected_version': call['version'], 'reason': '临时未查询到不是最终未执行'}).status_code == 200
    current = saved_task(client, task['id'])
    assert current['status'] == 'waiting_tool'
    assert current['data']['step_receipts'][plan['id'] + ':0']['result']['status'] == 'unknown'
    assert client.post('/api/tasks/' + task['id'] + '/retry_execution', json={
        'expected_version': current['version'], 'reason': '不得重发'}).status_code == 409
    assert service['calls'] == 1


def test_interrupted_device_intent_stays_unknown_and_reserved(client, monkeypatch, service):
    _, _, obj, task, _ = setup(client, monkeypatch, service)
    source = publish(client, manifest('source', name='device_crash_fixture', execution={'adapter': 'upload'}))
    with Session(engine(), expire_on_commit=False) as db:
        from generic_agents.models import Task
        owner = db.get(Task, task['id']).owner_id
        acq = Acquisition(workspace_id=obj['workspace_id'], entity_id=obj['id'], capability_id=source['id'],
            idempotency_key='synthetic-device-intent', status='dispatching', data={'request_hash': 'synthetic-intent'})
        db.add(acq); db.flush()
        reservation = SourceReservation(workspace_id=obj['workspace_id'], source_id=source['id'], acquisition_id=acq.id)
        job = Job(workspace_id=obj['workspace_id'], kind='dispatch_acquisition', idempotency_key='dispatch:' + acq.id,
            status='running', attempts=1, lease_token='interrupted-worker', lease_until=now() - timedelta(seconds=1),
            data={'acquisition_id': acq.id, 'actor_id': owner})
        db.add_all([reservation, job]); db.commit(); acq_id, job_id = acq.id, job.id
    assert process_once(job_id)
    with Session(engine()) as db:
        assert db.get(Acquisition, acq_id).status == 'unknown'
        assert db.scalar(select(SourceReservation).where(SourceReservation.source_id == source['id'])).acquisition_id == acq_id
        assert db.get(Job, job_id).status == 'failed'
    assert service['calls'] == 0  # Persisted intent fixture, no physical device dispatch claim.


def test_cancel_during_takeover_remains_cancelled(client, monkeypatch, service):
    task, plan, job_id = approved(client, monkeypatch, service, True)
    monkeypatch.setattr(leases, 'heartbeat', lambda *args: nullcontext())
    service['entered'] = threading.Event(); service['release'] = threading.Event()
    worker = threading.Thread(target=lambda: process_once(job_id)); worker.start()
    try:
        assert service['entered'].wait(3)
        current = saved_task(client, task['id'])
        assert client.post('/api/tasks/' + task['id'] + '/cancel', json={
            'expected_version': current['version'], 'reason': '取消合成恢复任务'}).status_code == 200
        expire(job_id); assert process_once(job_id)
    finally:
        service['release'].set(); worker.join(timeout=5)
    current = saved_task(client, task['id'])
    assert current['status'] == 'cancelled' and not current['data'].get('final_evidence')
    result = current['data']['step_receipts'][plan['id'] + ':0']['result']
    assert result['status'] == 'unknown' and service['calls'] == 1
    call = client.get('/api/invocations/' + result['invocation_id']).json()
    assert client.post('/api/invocations/' + call['id'] + '/reconcile', json={
        'expected_version': call['version'], 'reason': '取消后只读保留服务结果'}).status_code == 200
    assert saved_task(client, task['id'])['status'] == 'cancelled'
    assert not process_once(job_id)


@pytest.mark.parametrize('side_effect', [False, True])
@pytest.mark.parametrize('crash_window', ['inflight', 'after_result_saved'])
def test_actual_worker_sigkill_recovers_committed_intent(client, monkeypatch, service, side_effect, crash_window):
    task, plan, job_id = approved(client, monkeypatch, service, side_effect)
    service['entered'] = threading.Event(); service['release'] = threading.Event()
    if crash_window == 'after_result_saved': service['release'].set()
    env = {**os.environ, 'GA_DEVELOPMENT_CONNECTOR_URLS': ','.join(service[k] for k in ('http', 'mcp', 'receipt'))}
    program = ('from generic_agents import leases; from generic_agents.worker import process_once; '
        'leases.LEASE_SECONDS=0.6; leases.HEARTBEAT_SECONDS=0.1; process_once(' + repr(job_id) + ')')
    if crash_window == 'after_result_saved':
        program = '''from generic_agents import leases, gateway
from generic_agents.worker import process_once
import os, signal
leases.LEASE_SECONDS=0.6
leases.HEARTBEAT_SECONDS=0.1
original=gateway.invoke
def kill_after_commit(*args, **kwargs):
    result=original(*args, **kwargs)
    assert result.status == 'succeeded'
    os.kill(os.getpid(), signal.SIGKILL)
gateway.invoke=kill_after_commit
from generic_agents import agents
agents.invoke=kill_after_commit
process_once(''' + repr(job_id) + ')\n'
    process = subprocess.Popen([sys.executable, '-c', program], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert service['entered'].wait(4), '真实子进程没有到达独立服务'
        if crash_window == 'inflight': process.kill()
        process.wait(timeout=3)
        with Session(engine()) as db:
            before = db.scalar(select(Invocation).where(Invocation.data['job_id'].as_string() == job_id))
            assert before.status == ('running' if crash_window == 'inflight' else 'succeeded')
            assert before.data['lease_token']
        assert process.returncode < 0
        # Lease expires naturally after SIGKILL; no fabricated expiry or fixture worker execution.
        for _ in range(30):
            with Session(engine()) as db:
                expired = db.get(Job, job_id).lease_until < now()
            if expired: break
            threading.Event().wait(0.1)
        assert expired
        assert process_once(job_id)
        current = saved_task(client, task['id'])
        result = current['data']['step_receipts'][plan['id'] + ':0']['result']
        assert current['status'] == ('waiting_tool' if crash_window == 'inflight' else 'awaiting_acceptance')
        assert result['status'] == ('succeeded' if crash_window == 'after_result_saved' else 'unknown' if side_effect else 'failed')
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=3)
        service['release'].set()
    assert service['calls'] == 1
    if crash_window == 'inflight':
        if side_effect:
            call = client.get('/api/invocations/' + result['invocation_id']).json()
            assert client.post('/api/invocations/' + call['id'] + '/reconcile', json={
                'expected_version': call['version'], 'reason': '强制退出后只读核对'}).status_code == 200
        else:
            assert client.post('/api/tasks/' + task['id'] + '/retry_execution', json={
                'expected_version': current['version'], 'reason': '强制退出后明确重试只读工具'}).status_code == 200
        assert process_once()
    saved = saved_task(client, task['id'])
    assert saved['status'] == 'awaiting_acceptance'
    assert client.post('/api/tasks/' + task['id'] + '/accept', json={
        'expected_version': saved['version'], 'reason': '合成后台故障恢复协议核对通过'}).status_code == 200
    assert service['calls'] == (2 if crash_window == 'inflight' and not side_effect else 1)
    evidence_path = os.environ.get('GA_WORKER_EVIDENCE_PATH')
    if evidence_path:
        path = Path(evidence_path); cases = json.loads(path.read_text()) if path.exists() else []
        cases = [c for c in cases if (c['side_effect'],c.get('crash_window','inflight')) != (side_effect,crash_window)]
        cases.append({'side_effect': side_effect, 'crash_window': crash_window, 'actual_subprocess_sigkill': True,
            'natural_lease_expiry': True, 'committed_intent_recovered': True,
            'external_action_calls': service['calls'], 'restored_task_accepted': True,
            'input': 'synthetic single archive temperature 24', 'cloud_planning': 'explicit fixture, no cloud call'})
        path.write_text(json.dumps(cases, ensure_ascii=False, indent=2)); path.chmod(0o600)


def test_committed_success_with_changed_evidence_preserves_action_and_stops_old_plan(client, monkeypatch, service):
    task, plan, job_id = approved(client, monkeypatch, service, True)
    with Session(engine()) as db:
        from generic_agents.models import Task, Entity
        step = plan['data']['result']['plan']['steps'][0]
        job = db.get(Job, job_id); job.status = 'running'; job.attempts = 1
        job.lease_token = 'crashed-after-commit'; job.lease_until = now() - timedelta(seconds=1)
        call = Invocation(workspace_id=task['workspace_id'], capability_id=step['capability_id'],
            idempotency_key=step['request_key'], status='succeeded', data={'job_id': job_id, 'side_effect': True,
                'approval': {'plan_id': plan['id'], 'step_index': 0}, 'input_hash': step['input_hash'],
                'output': {'value': 24, 'samples': 1, 'method': 'explicit_committed_result_fixture'}})
        entity_id = db.get(Task, task['id']).data['entity_ids'][0]
        obj = db.get(Entity, entity_id); obj.data = {**obj.data, 'temperature': 35}; obj.version += 1
        db.add(call); db.commit()
    assert process_once(job_id)
    saved = saved_task(client, task['id'])
    assert saved['status'] == 'waiting_evidence' and saved['data']['execution_error']
    assert saved['data']['step_receipts'][plan['id'] + ':0']['result']['output']['value'] == 24
    assert not saved['data'].get('final_evidence') and service['calls'] == 0
    with Session(engine()) as db:
        assert db.get(Job, job_id).status == 'failed'


def test_perception_result_committed_before_sigkill_routes_once_without_reextracting(client):
    from .test_vertical_slice import entity
    from datetime import datetime, timezone
    package = publish(client, manifest())
    obj = entity(client, package)
    source = publish(client, manifest('source', name='crash_camera', execution={'adapter': 'upload'}))
    extractor = publish(client, manifest('specialist_model', name='crash_extractor', allowed_agent_roles=['perception'],
        execution={'adapter': 'builtin', 'handler': 'numeric_rules'}, specification={'rules': [
            {'id': 'synthetic_high', 'field': 'temperature', 'threshold': 50, 'operator': 'gt'}]},
        input_schema={'type': 'object', 'required': ['attributes']},
        output_schema={'type': 'object', 'required': ['attributes', 'anomalies', 'method']}))
    stamp = datetime.now(timezone.utc)
    response = client.post('/api/observations', json={'source_id': source['id'], 'extractor_id': extractor['id'],
        'entity_id': obj['id'], 'source_record_id': 'synthetic_crash_observation', 'processing_version': '1',
        'observed_at': stamp.isoformat(), 'business_day': str(stamp.date()), 'startup_mode': 'automatic',
        'attributes': {'temperature': 70}})
    assert response.status_code == 201, response.text
    observation_id = response.json()['id']
    with Session(engine()) as db:
        job_id = db.scalar(select(Job.id).where(Job.data['observation_id'].as_string() == observation_id))
    program = '''from generic_agents import leases, gateway
from generic_agents.worker import process_once
import os, signal
leases.LEASE_SECONDS=0.6
leases.HEARTBEAT_SECONDS=0.1
original=gateway.invoke
def kill_after_commit(*args, **kwargs):
    result=original(*args, **kwargs)
    assert result.status == 'succeeded'
    os.kill(os.getpid(), signal.SIGKILL)
gateway.invoke=kill_after_commit
from generic_agents import agents
agents.invoke=kill_after_commit
process_once(''' + repr(job_id) + ')\n'
    process = subprocess.Popen([sys.executable, '-c', program], env=os.environ.copy(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        process.wait(timeout=5); assert process.returncode < 0
        for _ in range(30):
            with Session(engine()) as db:
                expired = db.get(Job, job_id).lease_until < now()
            if expired: break
            threading.Event().wait(0.1)
        assert expired and process_once(job_id)
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=3)
    with Session(engine()) as db:
        from generic_agents.models import Observation, Event, Task
        from sqlalchemy import func
        assert db.get(Observation, observation_id).status == 'processed'
        assert db.scalar(select(func.count()).select_from(Invocation)) == 1
        assert db.scalar(select(func.count()).select_from(Event).where(Event.status == 'received')) == 2
        assert db.scalar(select(func.count()).select_from(Task)) == 1
    assert not process_once(job_id)
