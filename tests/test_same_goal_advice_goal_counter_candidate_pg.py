"""Compare real goal counters under test-only inventory wiring.

The restored original is a separately generated synthetic mature Goal with a
real v1 Start, no cloud call, and a real user/session. No route or production
counter is changed by this test; unknown v2 states remain disabled.
"""
import os
from time import monotonic

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from generic_agents import goal_queries, native_goals
from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_execution_v308 as execution
from generic_agents.db import engine
from generic_agents.models import Task
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network
from tests.test_same_goal_advice_restored_provider_v1_pg import restore_case


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE'),
    reason='requires an explicitly created isolated synthetic Start checkpoint')
def test_actual_goal_counters_match_candidate_for_persisted_v1_start(monkeypatch, reject_non_loopback_network, request):
    case = restore_case(monkeypatch); p = case.principal
    measured = {'queries': 0}
    def count_sql(_conn, _cursor, _statement, _parameters, _context, _executemany):
        measured['queries'] += 1
    bind = engine(); event.listen(bind, 'before_cursor_execute', count_sql)
    request.addfinalizer(lambda: event.remove(bind, 'before_cursor_execute', count_sql))
    start = monotonic()
    with Session(engine()) as db:
        task = db.get(Task, case.task_id)
        original_calls = goal_queries.call_count(db, p, task)
        original_steps = native_goals.total_steps(db, p, task)
    baseline = {'queries': measured['queries'], 'seconds': round(monotonic() - start, 3)}
    print('V1_ORIGINAL_GOAL_COUNTERS_READ', flush=True)

    # Only change the imported wiring in this test; all SQL, authorization,
    # source custody and original domain counters remain genuine.
    def candidate(db, principal, task):
        return common.read(db, principal, task)['v1_calls']
    monkeypatch.setattr(execution, 'budget_calls', candidate)
    measured['queries'] = 0; start = monotonic()
    with Session(engine()) as db:
        task = db.get(Task, case.task_id)
        assert goal_queries.call_count(db, p, task) == original_calls
        assert native_goals.total_steps(db, p, task) == original_steps
        task.title = '未保存的真实目标标题编辑'
        with db.no_autoflush:
            assert goal_queries.call_count(db, p, task) == original_calls
            assert native_goals.total_steps(db, p, task) == original_steps
        assert task in db.dirty and task.title == '未保存的真实目标标题编辑'
        db.commit()
    with Session(engine()) as db:
        assert db.get(Task, case.task_id).title == '未保存的真实目标标题编辑'
    comparison = {'queries': measured['queries'], 'seconds': round(monotonic() - start, 3)}
    print('SYNTHETIC_COUNTER_PERFORMANCE', {'baseline': baseline, 'candidate': comparison}, flush=True)
    print('V1_CANDIDATE_GOAL_COUNTERS_MATCH', flush=True)
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        task = db.get(Task, case.task_id)
        assert common.read_snapshot(db, case.principal, task) == {'v1_calls': 1, 'queued_v2': 0}
    print('V1_START_READ_ONLY_SNAPSHOT_MATCH', flush=True)
