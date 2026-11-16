"""Shared producer, real independent actors and original private delivery receipts."""
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventAdmission, GraphEventIntent
from generic_agents.models import CloudCall, Invocation, Job, Membership, User
from generic_agents.security import password_hash
from generic_agents.worker import process_once
from tests.test_graph_event_pipeline import BASE, delivery_job, observe, ok, pending_intents, scene


def test_shared_producer_lists_and_original_receipt_recovery_remain_actor_private(client):
    # Build/register before adding the second membership: scene's original
    # fixture intentionally reads the sole initial administrator membership.
    value = scene(client); owner = value['principal']; producer = value['producers'][0]
    username = 'graph_event_operator_' + uuid4().hex
    password = uuid4().hex
    with Session(engine()) as db:
        operator = User(username=username, password_hash=password_hash(password))
        db.add(operator); db.flush(); operator_id = operator.id
        db.add(Membership(user_id=operator_id, workspace_id=owner.workspace_id, role='operator'))
        db.commit()

    with TestClient(app) as other:
        login = ok(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': owner.workspace_id})
        shared = ok(other.get(BASE))
        assert [item['id'] for item in shared] == [producer['id']]
        assert shared[0]['execution_granted'] is False

        first, _, _ = observe(client, value, 28)
        # Another actor's existing intent must neither leak nor make an empty
        # personal list fail before this operator has processed anything.
        assert ok(other.get(BASE + '/' + producer['id'] + '/intents')) == []
        second, _, _ = observe(other, value, 29)
        expected = {owner.user_id: first['id'], operator_id: second['id']}
        observed = {}
        for actor_id, observation_id in expected.items():
            pairs = pending_intents(observation_id)
            assert len(pairs) == 1 and pairs[0][1] == producer['id']
            identity = pairs[0][0]; job_id, _ = delivery_job(identity)
            assert process_once(job_id)
            with Session(engine()) as db:
                intent = db.get(GraphEventIntent, identity)
                job = db.get(Job, job_id)
                assert intent.actor_id == actor_id and intent.receipt_id is not None
                assert job.status == 'succeeded', job.data
                observed[actor_id] = {'id': identity, 'receipt_id': intent.receipt_id,
                    'job_version': job.version}

        for actor_id, own, foreign in ((owner.user_id, client, other), (operator_id, other, client)):
            record = observed[actor_id]
            rows = ok(own.get(BASE + '/' + producer['id'] + '/intents'))
            assert len(rows) == 1 and rows[0]['id'] == record['id']
            assert rows[0]['intent']['actor_id'] == actor_id
            assert rows[0]['intent']['observation_id'] == expected[actor_id]
            assert rows[0]['receipt_id'] == record['receipt_id']
            assert rows[0]['delivery']['status'] == 'succeeded'
            # Filtering must precede LIMIT, including for the older admin row.
            assert ok(own.get(BASE + '/' + producer['id'] + '/intents', params={'limit': 1})) == rows
            receipt = ok(own.get(BASE + '/admissions/' + record['receipt_id']))
            assert receipt['intent_id'] == record['id']
            assert receipt['projection']['observation_id'] == expected[actor_id]
            assert foreign.get(BASE + '/admissions/' + record['receipt_id']).status_code == 403
            command = {'request_id': str(uuid4()), 'job_version': record['job_version'],
                'reason': '只核对这条原消息已经保存的实际接纳回执'}
            assert foreign.post(BASE + '/intents/' + record['id'] + '/retry', json=command).status_code == 403
            restored = ok(own.post(BASE + '/intents/' + record['id'] + '/retry', json=command))
            assert restored['already_admitted'] and restored['admission'] == receipt

    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(GraphEventIntent)) == 2
        assert db.scalar(select(func.count()).select_from(GraphEventAdmission)) == 2
        assert db.scalar(select(func.count()).select_from(Invocation)) == 2
        assert db.scalar(select(CloudCall.id)) is None
