"""Real PG worker/lease/cancel boundaries; only cloud HTTP is a test double.

The root conftest creates and destroys its own ga_test_<uuid> database. A
blocked HTTP response must not keep the worker's Job/SetupAnalysis row locks.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.leases import renew
from generic_agents.models import CloudCall, Job, SetupAnalysis
from tests.test_setup_understanding import process, proposal, start
from tests.test_setup_understanding_v4 import long_request


@pytest.mark.parametrize('cancel_during_http', [False, True])
def test_v4_http_wait_releases_real_lease_and_cancel_locks(client, monkeypatch,
                                                        cancel_during_http):
    body, _ = long_request(client)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)
    monkeypatch.setattr(settings(), 'dashscope_api_key', SecretStr('synthetic-no-network'))
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    row, preview = start(client, body)
    entered, release = Event(), Event()
    wire_requests, wire_answers = [], []
    base_client = httpx.Client

    def handler(request):
        actual = json.loads(request.content)
        wire_requests.append(actual)
        value = proposal(json.loads(actual['messages'][1]['content']))
        value['objects'] = []
        content = json.dumps(value, ensure_ascii=False)
        wire_answers.append(content)
        entered.set()
        assert release.wait(10), 'test controller did not release synthetic HTTP'
        return httpx.Response(200, json={
            'choices': [{'message': {'content': content}}],
            'usage': {'total_tokens': 1234},
        }, headers={'x-request-id': 'm50-independent-lease-test'})

    def factory(**kwargs):
        return base_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr('generic_agents.gateway.httpx.Client', factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        worker = executor.submit(process, row)
        cancel = None
        try:
            assert entered.wait(5), 'worker never reached the actual cloud gateway'
            with Session(engine()) as db:
                job = db.scalar(select(Job).where(
                    Job.kind == 'understand_setup',
                    Job.data['analysis_id'].astext == row['id']))
                job_id, token, lease_before = job.id, job.lease_token, job.lease_until
            if cancel_during_http:
                current = client.get('/api/setup/understanding/' + row['id']).json()
                cancel = executor.submit(client.post,
                    '/api/setup/understanding/' + row['id'] + '/cancel',
                    json={'version': current['version']})
                cancelled = cancel.result(timeout=1)
                assert cancelled.status_code == 200, cancelled.text
                assert cancelled.json()['status'] == 'cancelled'
                assert not release.is_set(), 'cancel was verified only after HTTP completed'
            # This is the actual DB-clock heartbeat update on a different
            # connection, including its 250ms lock timeout, not a mock boolean.
            assert renew(engine(), job_id, token), 'HTTP wait blocked actual lease renewal'
            with Session(engine()) as db:
                assert db.get(Job, job_id).lease_until > lease_before
        finally:
            release.set()
            worker.result(timeout=10)
            if cancel is not None:
                cancel.result(timeout=10)

    assert len(wire_requests) == 1
    assert digest(wire_requests[0]['messages']) == preview['first_dispatch_messages_hash']
    with Session(engine()) as db:
        actual = db.get(SetupAnalysis, row['id'])
        saved = copy.deepcopy(actual.data)
        assert actual.status == ('cancelled' if cancel_during_http else 'review')
        assert saved['raw_response']['content'] == wire_answers[0]
        assert saved['raw_response']['receipt']['usage'] == {'total_tokens': 1234}
        assert len(saved['receipts']) == 1
        assert bool(saved.get('result')) is not cancel_during_http
        calls = db.scalars(select(CloudCall)).all()
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        assert calls[0].reserved_tokens == preview['input_summary']['token_bound']
        assert calls[0].charged_tokens == 1234

