"""Real scheduler SQL/attempt durability; evaluation business refs are fictional.

This tests restriction and fair queue progress, not a real Goal authority or
producer completion. It does not run the claimed Goal handler or model SDK.
"""
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import worker
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents.workflow_goal_evaluation_barrier_v288 import KIND, REF, PRODUCER
from tests.test_workflow_goal_evaluation_pg_v288 import seed


@pytest.mark.parametrize('terminal', ['succeeded', 'failed', 'unknown'])
def test_real_claim_leaves_pending_goal_untouched_then_allows_actual_handler_to_judge_terminal_producer(terminal):
    record = seed(); w = record['scope']['workspace_id']; task_id = record['scope']['task_id']
    with Session(engine()) as db:
        producer = db.get(Job, record['producer']['job_id'])
        producer.kind = 'execute_full_semantic_graph_node'
        producer.data = {'task_id': task_id}; producer.status = 'running'
        now = db.scalar(select(func.clock_timestamp()))
        target = Job(workspace_id=w, kind=KIND, status='queued', available_at=now-timedelta(minutes=2),
            idempotency_key='v288-barrier:' + str(uuid4()), data={'task_id': task_id,
                'actor_id': record['producer']['actor_id'], REF: {'id': record['evaluation_id'],
                    'record_hash': record['record_hash']}, PRODUCER: producer.id})
        other = Job(workspace_id=w, kind=KIND, status='queued', available_at=now-timedelta(minutes=1),
            idempotency_key='v288-other:' + str(uuid4()), data={'actor_id': record['producer']['actor_id']})
        db.add_all([target, other]); db.commit(); target_id, other_id = target.id, other.id
        before = (target.status, target.attempts, target.lease_token, copy.deepcopy(target.data))
    assert worker.claim(target_id, kinds=[KIND], workspace_id=w) is None
    assert worker.claim(kinds=[KIND], workspace_id=w)[0] == other_id
    with Session(engine()) as db:
        target = db.get(Job, target_id)
        assert (target.status, target.attempts, target.lease_token, target.data) == before
        producer = db.get(Job, record['producer']['job_id']); producer.status = terminal; db.commit()
    assert worker.claim(target_id, kinds=[KIND], workspace_id=w)[0] == target_id
    with Session(engine()) as db:
        target = db.get(Job, target_id)
        assert target.status == 'running' and target.attempts == 1


def test_damaged_reference_is_not_hidden_forever_by_an_unrelated_running_producer():
    record = seed(); w = record['scope']['workspace_id']; task_id = record['scope']['task_id']
    with Session(engine()) as db:
        producer = db.get(Job, record['producer']['job_id'])
        producer.kind = 'execute_full_semantic_graph_node'; producer.data = {'task_id': task_id}
        row = Job(workspace_id=w, kind=KIND, idempotency_key='v288-invalid:' + str(uuid4()),
            data={'task_id': task_id, 'actor_id': record['producer']['actor_id'],
                REF: {'id': record['evaluation_id'], 'record_hash': '0' * 64}, PRODUCER: producer.id})
        db.add(row); db.commit(); identity = row.id
    assert worker.claim(identity, kinds=[KIND], workspace_id=w)[0] == identity
