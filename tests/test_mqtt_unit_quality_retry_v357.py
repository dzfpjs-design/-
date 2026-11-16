"""Restricted recovery of the same local diagnostic, never MQTT redelivery."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Audit, Event, Issue, Job, Membership, Observation
from generic_agents.mqtt_unit_quality_dispatch_v357 import KIND
from generic_agents.mqtt_unit_quality_retry_v357 import (MqttUnitDiagnosticRetryV357,
    MqttUnitDiagnosticRetryRetirementV357, retry)
from generic_agents.worker import deliver_events
from tests.test_mqtt_inbox import gateway, packet, save


@pytest.fixture(scope='module', autouse=True)
def isolated_retry_table():
    # The candidate is intentionally not in the application's migration yet.
    # conftest migrates before test collection, so create it in this test DB only.
    MqttUnitDiagnosticRetryV357.__table__.create(engine(), checkfirst=True)
    MqttUnitDiagnosticRetryRetirementV357.__table__.create(engine(), checkfirst=True)


def failed_job(gateway, record_id='r1'):
    p = gateway[0]
    raw = packet(record_id=record_id, units={'t': 'F'})
    receipt = save(gateway, raw)
    deliver_events(workspace_id=p.workspace_id, event_kind='perception.sensor_packet',
                   packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.kind == KIND,
            Job.data['unit_request']['receipt_id'].as_string()==receipt.id))
        event = db.scalar(select(Event).where(Event.kind == 'perception.sensor_packet',
            Event.data['receipt_id'].as_string()==receipt.id))
        assert event.status == 'received' and job.status == 'queued'
        # Isolate recovery from error-injection specifics in the generic worker.
        job.status = 'failed'
        job.attempts = 1
        job.data = {**job.data, 'error': 'local test failure', 'finished_at': 'test'}
        db.commit()
        return receipt.id, job.id, job.version


def test_retry_is_durable_idempotent_and_same_job_runs(gateway):
    from generic_agents.evidence import evidence_bytes
    from generic_agents.mqtt_unit_quality_worker_v357 import tick
    from generic_agents.mqtt_unit_quality_v357 import KIND as ISSUE_KIND
    p = gateway[0]
    receipt_id, job_id, version = failed_job(gateway)
    request_id = str(uuid4())
    with Session(engine()) as db:
        result = retry(db, p, job_id, request_id, version, '恢复本地诊断')
        command_id = result.id
        db.commit()
    with Session(engine()) as db:
        assert retry(db, p, job_id, request_id, version, '恢复本地诊断').id == command_id
        job = db.get(Job, job_id)
        assert job.status == 'queued' and job.data['unit_quality_retry_history'][0]['previous_failure']['error'] == 'local test failure'
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == KIND)) == 1
        db.commit()
    assert tick(p.workspace_id, gateway[1])['attempted'] == 1
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        assert job.status == 'succeeded'
        assert db.scalar(select(Issue).where(Issue.kind == ISSUE_KIND)).status == 'open'
        assert db.scalar(select(func.count()).select_from(Observation)) == 0
        from generic_agents.models import MqttReceipt
        receipt = db.get(MqttReceipt, receipt_id)
        assert evidence_bytes(db, p, receipt.evidence_id)[1] == packet(units={'t': 'F'})
        # An idempotent replay acknowledges the historical request, not a new retry.
        assert retry(db, p, job_id, request_id, version, '恢复本地诊断').id == command_id
        assert db.get(Job, job_id).status == 'succeeded'


@pytest.mark.parametrize('case', ['revoked', 'stale', 'partial', 'attempts', 'proof'])
def test_retry_refuses_unsafe_state_without_creating_command(gateway, case):
    p = gateway[0]
    receipt_id, job_id, version = failed_job(gateway)
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        if case == 'revoked':
            db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
                                               Membership.user_id == p.user_id)).role = 'viewer'
        elif case == 'partial':
            job.data = {**job.data, 'unit_quality_result': {}}
        elif case == 'attempts':
            job.attempts = 3
        elif case == 'proof':
            event = db.scalar(select(Event).where(Event.kind == 'perception.sensor_packet'))
            event.data = {**event.data, 'report_hash': 'damaged'}
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            retry(db, p, job_id, str(uuid4()), version + (1 if case == 'stale' else 0), '恢复本地诊断')
        db.rollback()
        assert db.scalar(select(func.count()).select_from(MqttUnitDiagnosticRetryV357)) == 0
        assert db.get(Job, job_id).status == 'failed'


def test_parallel_duplicate_request_returns_single_command(gateway):
    p = gateway[0]
    _, job_id, version = failed_job(gateway)
    request_id = str(uuid4())
    def call():
        with Session(engine()) as db:
            try:
                command = retry(db, p, job_id, request_id, version, '恢复本地诊断')
                identity = command.id
                db.commit()
                return identity
            except HTTPException as exc:
                assert exc.status_code == 409
                db.rollback()
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: call(), range(2)))
    accepted = [identity for identity in ids if identity]
    assert accepted and len(set(accepted)) == 1
    with Session(engine()) as db:
        assert retry(db, p, job_id, request_id, version, '恢复本地诊断').id == accepted[0]
        assert db.scalar(select(func.count()).select_from(MqttUnitDiagnosticRetryV357)) == 1
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.action == 'quality.mqtt_unit_diagnostic_retry_v357')) == 1
        assert db.get(Job, job_id).status == 'queued'


def test_retry_api_records_one_historical_request_and_exposes_real_state(client, gateway):
    _, job_id, version = failed_job(gateway)
    url = '/api/mqtt/gateways/' + gateway[1] + '/unit-quality'
    before = client.get(url)
    assert before.status_code == 200
    record = before.json()['records'][0]
    assert record['state'] == 'needs_review' and record['can_retry'] and record['version'] == version
    body = {'request_id': str(uuid4()), 'expected_version': version, 'reason': '恢复本地诊断'}
    response = client.post(url + '/jobs/' + job_id + '/retry', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['accepted'] and response.json()['same_job']
    assert client.post(url + '/jobs/' + job_id + '/retry', json=body).json()['receipt'] == response.json()['receipt']
    after = client.get(url).json()['records'][0]
    assert after['state'] == 'waiting_worker' and not after['can_retry']
    assert not client.get(url).json()['worker_online_verified']


def test_expired_running_job_is_not_reported_as_processing(client, gateway):
    from datetime import timedelta
    _, job_id, _ = failed_job(gateway)
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        job.status = 'running'
        job.lease_until = db.scalar(select(func.clock_timestamp())) - timedelta(seconds=10)
        db.commit()
    url = '/api/mqtt/gateways/' + gateway[1] + '/unit-quality'
    record = client.get(url).json()['records'][0]
    assert record['state'] == 'lease_expired_waiting_recovery' and not record['can_retry']
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        job.lease_until = db.scalar(select(func.clock_timestamp())) + timedelta(minutes=1)
        db.commit()
    assert client.get(url).json()['records'][0]['state'] == 'processing'
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        job.lease_until = None
        db.commit()
    assert client.get(url).json()['records'][0]['state'] == 'lease_expired_waiting_recovery'


def test_unknown_request_requires_retirement_and_rejects_late_arrival(client, gateway):
    _, job_id, version = failed_job(gateway)
    url = '/api/mqtt/gateways/' + gateway[1] + '/unit-quality/jobs/' + job_id
    request_id = str(uuid4())
    path = url + '/requests/' + request_id
    assert client.get(path).json() == {'state': 'unknown', 'retire_required': True}
    retired = client.post(path + '/retire')
    assert retired.status_code == 200 and retired.json()['state'] == 'retired'
    assert retired.json()['request_id']==request_id and retired.json()['job_id']==job_id
    assert retired.json()['subject_id']==gateway[0].user_id
    assert client.post(path + '/retire').json() == retired.json()
    assert client.get(path).json() == retired.json()
    late = client.post(url + '/retry', json={'request_id': request_id,
        'expected_version': version, 'reason': '恢复本地诊断'})
    assert late.status_code == 409
    with Session(engine()) as db:
        assert db.get(Job, job_id).status == 'failed'
        assert db.scalar(select(func.count()).select_from(MqttUnitDiagnosticRetryV357)) == 0
        assert db.scalar(select(func.count()).select_from(MqttUnitDiagnosticRetryRetirementV357)) == 1


def test_success_before_retire_returns_original_receipt(client, gateway):
    from generic_agents.contracts import digest
    _, job_id, version = failed_job(gateway)
    url = '/api/mqtt/gateways/' + gateway[1] + '/unit-quality/jobs/' + job_id
    request_id = str(uuid4())
    response = client.post(url + '/retry', json={'request_id': request_id,
        'expected_version': version, 'reason': '恢复本地诊断'})
    assert response.status_code == 200
    status = client.get(url + '/requests/' + request_id).json()
    assert status == {'state': 'accepted', 'receipt': response.json()['receipt'],
        'request_id': request_id, 'job_id': job_id, 'subject_id': gateway[0].user_id,
        'body_hash': digest({'job_id':job_id,'expected_version':version,'reason':'恢复本地诊断'})}
    assert client.post(url + '/requests/' + request_id + '/retire').json() == status
    different = client.post(url + '/retry', json={'request_id': request_id,
        'expected_version':version, 'reason':'不同的请求内容'})
    assert different.status_code == 409
    assert client.get(url + '/requests/' + request_id).json() == status


def test_corrupted_receipt_audit_cannot_clear_pending(client, gateway):
    _, job_id, version = failed_job(gateway)
    url = '/api/mqtt/gateways/' + gateway[1] + '/unit-quality/jobs/' + job_id
    request_id = str(uuid4())
    response = client.post(url + '/retry', json={'request_id':request_id,
        'expected_version':version, 'reason':'恢复本地诊断'})
    assert response.status_code == 200
    with Session(engine()) as db:
        audit = db.scalar(select(Audit).where(Audit.action=='quality.mqtt_unit_diagnostic_retry_v357'))
        audit.data={**audit.data,'body_hash':'not-the-original'}
        db.commit()
    path=url + '/requests/' + request_id
    assert client.get(path).status_code == 409
    assert client.post(path + '/retire').status_code == 409


def test_same_request_for_different_jobs_is_conflict(client, gateway):
    _, first, first_version = failed_job(gateway, 'one')
    _, second, second_version = failed_job(gateway, 'two')
    base = '/api/mqtt/gateways/' + gateway[1] + '/unit-quality/jobs/'
    request_id = str(uuid4())
    body = {'request_id': request_id, 'expected_version': first_version, 'reason': '恢复本地诊断'}
    assert client.post(base + first + '/retry', json=body).status_code == 200
    body['expected_version'] = second_version
    assert client.post(base + second + '/retry', json=body).status_code == 409
    assert client.post(base + second + '/requests/' + request_id + '/retire').status_code == 409
    with Session(engine()) as db:
        assert db.get(Job, second).status == 'failed'


def test_locked_request_is_neither_absent_nor_retired(client, gateway):
    _, job_id, version = failed_job(gateway)
    request_id = str(uuid4())
    key = 'mqtt-unit-retry-v357:' + gateway[0].workspace_id + ':' + request_id
    path = ('/api/mqtt/gateways/' + gateway[1] + '/unit-quality/jobs/' + job_id
            + '/requests/' + request_id)
    with Session(engine()) as blocker:
        assert blocker.scalar(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'), {'key': key})
        assert client.get(path).status_code == 409
        assert client.post(path + '/retire').status_code == 409
        blocker.rollback()
    assert client.post(path + '/retire').json()['state'] == 'retired'
