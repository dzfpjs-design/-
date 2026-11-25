"""Recover a saved fake-provider response after a PostgreSQL process restart."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job
from generic_agents import same_goal_advice_spool_recovery_candidate_v2 as recovery
from tests.synthetic_advice_mock_state import read_state, read_request_marker

if __name__ == '__main__':
    ref = read_state()
    marker = read_request_marker()
    args = (ref['workspace_id'], ref['call_id'], ref['intent_id'], ref['permit_id'])
    with Session(engine()) as db:
        first = db.get(CloudCall, ref['call_id'])
        assert first is not None and first.status in {'reserved','succeeded'}
        day = first.data['day']
    assert recovery.recover(*args)['state'] == 'settled'
    assert recovery.recover(*args)['state'] == 'settled'
    assert read_request_marker() == marker
    with Session(engine()) as db:
        current = db.get(CloudCall, ref['call_id'])
        assert (current.status, current.charged_tokens, current.data['day']) == ('succeeded', 5, day)
        daily=db.scalar(select(Job).where(Job.workspace_id==ref['workspace_id'],
            Job.idempotency_key=='cloud-budget:'+day))
        assert daily is not None and daily.data['tokens']==5 and daily.data['calls']==1
    engine().dispose()
    print('SYNTHETIC_DB_RESTART_AND_NO_SEND_RECOVERY_PASSED', flush=True)
