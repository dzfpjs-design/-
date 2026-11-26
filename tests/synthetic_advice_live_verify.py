"""Verify and recover an original synthetic response without sending."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job
from generic_agents import same_goal_advice_spool_recovery_candidate_v2 as recovery
from tests.synthetic_advice_mock_state import read_state

if __name__ == '__main__':
    ref = read_state()
    args = (ref['workspace_id'], ref['call_id'], ref['intent_id'], ref['permit_id'])
    with Session(engine()) as db:
        original = db.get(CloudCall, ref['call_id'])
        assert original is not None and original.status in {'succeeded', 'failed'}
        day, tokens, status = original.data['day'], original.charged_tokens, original.status
    first = recovery.recover(*args)
    second = recovery.recover(*args)
    assert first['state'] == second['state'] == 'settled'
    with Session(engine()) as db:
        current = db.get(CloudCall, ref['call_id'])
        assert (current.status, current.charged_tokens, current.data['day']) == (status, tokens, day)
        daily = db.scalar(select(Job).where(Job.workspace_id == ref['workspace_id'],
            Job.idempotency_key == 'cloud-budget:'+day))
        assert daily is not None and daily.data['tokens'] == tokens and daily.data['calls'] == 1
        usage_unknown = current.data.get('usage_unavailable') is not False
    engine().dispose()
    print('NO_SEND_RESTART_RECOVERY_PASSED status='+status+' charged_tokens='+str(tokens)
          +' usage_unknown='+str(usage_unknown), flush=True)
