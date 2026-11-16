"""Simulated ORM flush/reload boundary around the actual frozen query code.

The DB, receipt authentication and leased creator are explicit fixture spies.
The test models populate_existing discarding dirty data under no_autoflush;
it is not actual PG or a creator/permission proof. Root runs PG separately.
"""
import copy

from generic_agents import agents, goal_evaluation_query_v288 as subject
from generic_agents import models as m
from tests.test_goal_evaluation_query_v288 import scene as query_scene


def test_result_and_independent_saved_audit_are_flushed_before_immediate_cold_reload(query_scene, monkeypatch):
    s = query_scene; durable = {}; snapshots = []; agent_calls = []
    original_scalar = s.db.scalar; original_agent = agents.data_agent

    def flush():
        durable.clear()
        for key, row in s.db.rows.items():
            if key[0] is m.DataRun:
                durable[key] = {'status': row.status, 'data': copy.deepcopy(row.data)}
        snapshots.append(copy.deepcopy((durable, [event.data for event in s.db.events])))

    def scalar(statement):
        row = original_scalar(statement)
        if isinstance(row, m.DataRun) and statement.get_execution_options().get('populate_existing') is True:
            saved = durable[m.DataRun, row.id]
            row.status = saved['status']; row.data = copy.deepcopy(saved['data'])
        return row

    def agent(*args, **kwargs):
        agent_calls.append(kwargs.get('_goal_evaluation_cut'))
        return original_agent(*args, **kwargs)

    flush(); snapshots.clear()  # Only Q0 existed durably before this creator.
    monkeypatch.setattr(s.db, 'flush', flush)
    monkeypatch.setattr(s.db, 'scalar', scalar)
    monkeypatch.setattr(agents, 'data_agent', agent)
    before = copy.deepcopy((s.parent.data, s.task.data))
    row = s.create()
    assert len(snapshots) == 2  # Initial request flush, then complete result + SAVED.
    first, final = snapshots
    assert 'result' not in first[0][m.DataRun, row.id]['data'] and first[1] == []
    assert final[0][m.DataRun, row.id]['data']['result'] == row.data['result'] and len(final[1]) == 1
    frozen = subject.checked_frozen(s.db, s.reader, s.task, row.id, evaluation_id=s.evaluation_id)
    assert frozen['result'] == row.data['result']
    assert final[1][0]['query_ref'] == frozen['query_ref']
    assert s.create().id == row.id and len(agent_calls) == 1  # Same original cut reuses exact Qe.
    assert len(snapshots) == 2 and (s.parent.data, s.task.data) == before
