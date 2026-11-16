"""Normal-API, real-PG query results and actual read/write SQL boundaries.

Root executes this in its isolated database. No provider/device implementation
is mocked, and no successful Task, query, Invocation or acceptance is inserted
by SQL. The negative case deliberately corrupts a real capability then restores
its original bytes; it never repairs or replays the original query invocation.
"""
import copy
import re
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from generic_agents import models as m, native_goals
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_native_goals import setup, define, drain
from tests.test_instance_workspace import complete


ROW_LOCK = re.compile(r'\bFOR\s+(?:NO\s+KEY\s+UPDATE|KEY\s+SHARE|UPDATE|SHARE)\b', re.I)


def reply(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@contextmanager
def captured_sql():
    target = engine()
    assert target.dialect.name == 'postgresql', 'this proof requires actual PostgreSQL'
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        # SQL shape only. Never retain parameters, cookies or account secrets.
        statements.append(statement)
    event.listen(target, 'before_cursor_execute', record)
    try:
        yield statements
    finally:
        event.remove(target, 'before_cursor_execute', record)


def persisted_state(task_id):
    with Session(engine()) as db:
        task = db.get(m.Task, task_id)
        assert task is not None
        workspace = task.workspace_id
        all_queries = list(db.scalars(select(m.DataRun).where(m.DataRun.workspace_id == workspace,
            m.DataRun.data['native_goal_task_id'].as_string() == task_id)))
        # The primary evidence read is a distinct legitimate DataRun. Only the
        # delegated contract identifies this fixed query; retain both histories.
        queries = [row for row in all_queries
            if row.data.get('native_query_contract', {}).get('task_id') == task_id]
        query_ids = [row.id for row in queries]
        calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == workspace,
            m.Invocation.data['job_id'].as_string().in_(select(m.Job.id).where(
                m.Job.workspace_id == workspace, m.Job.kind == 'execute_native_goal_query',
                m.Job.data['task_id'].as_string() == task_id)))))
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == workspace,
            m.Job.data['task_id'].as_string() == task_id)))
        assert not list(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id == workspace)))
        assert not list(db.scalars(select(m.Acquisition.id).where(m.Acquisition.workspace_id == workspace)))
        assert not list(db.scalars(select(m.Job.id).where(m.Job.workspace_id == workspace,
            m.Job.kind.in_(['dispatch_acquisition', 'read_http_observation_receipt_v177']))))
        return {'task_id': task.id, 'task_version': task.version, 'task_data': copy.deepcopy(task.data),
            'queries': [(row.id, row.version, row.status, copy.deepcopy(row.data))
                for row in sorted(queries, key=lambda row: row.id)],
            'calls': [(row.id, row.status, row.idempotency_key, row.capability_id, copy.deepcopy(row.data))
                for row in sorted(calls, key=lambda row: row.id)],
            'all_task_queries': [(row.id, row.version, row.status, copy.deepcopy(row.data))
                for row in sorted(all_queries, key=lambda row: row.id)],
            'jobs': [(row.id, row.kind, row.status, row.version, copy.deepcopy(row.data))
                for row in sorted(jobs, key=lambda row: row.id)], 'query_ids': sorted(query_ids)}


def finish_only_original_query_jobs(task):
    processed = []
    # The existing helper processes only this Task's due native Goal checks.
    # No generic worker/dispatcher is drained and no failed Job is retried.
    for _ in range(4):
        drain(task)
        with Session(engine()) as db:
            jobs = list(db.scalars(select(m.Job).where(m.Job.kind == 'execute_native_goal_query',
                m.Job.data['task_id'].as_string() == task['id'], m.Job.status == 'queued',
                m.Job.available_at <= m.now()).order_by(m.Job.created_at, m.Job.id)))
            identities = [row.id for row in jobs]
        if not identities:
            break
        for identity in identities:
            assert identity not in processed, 'a completed query Job must never be replayed'
            assert process_once(identity, deliver=False)
            processed.append(identity)
            with Session(engine()) as db:
                assert db.get(m.Job, identity).status == 'succeeded'
    else:
        raise AssertionError('normal query/check sequence did not settle within its bounded rounds')
    drain(task)
    return processed


def ready_query(client):
    release, obj, task, base, body = setup(client)
    with Session(engine()) as db:
        package = db.get(m.Capability, release['package_id'])
        fixed = package.data['specification']['native_query_catalog']['tools']
        pin = next(value for value in fixed if value['method'] == 'aggregate')
        cap = db.get(m.Capability, pin['id'])
        assert cap.status == 'published' and cap.content_hash == pin['hash'] == digest(cap.data)
        assert cap.data['execution']['adapter'] == 'builtin'
        assert cap.data['execution']['handler'] == 'data_record_aggregate'
        assert cap.data['execution']['side_effect'] is False
        actual = db.get(m.Task, task['id'])
        # Mandatory proof, from the normal create_task API. Never skip or
        # manufacture bindings to make this branch appear covered.
        assert actual.data.get('object_definition_bindings')
        assert actual.data['object_definition_bindings_hash'] == digest(actual.data['object_definition_bindings'])
    field = body['goal_spec']['required_fields'][0]
    body['goal_spec']['data_query'] = {'tool_steps': [{'id': 'count_original',
        'capability_id': pin['id'], 'input_from': 'evidence',
        'parameters': {'field': field, 'operation': 'count'}}],
        'max_tool_seconds': 60, 'allow_external_tools': False}
    body['goal_spec']['criteria'].append({'id': 'count', 'kind': 'query_value', 'field': 'value',
        'operator': 'eq', 'expected': 1, 'description': '原范围恰有一条可追溯登记'})
    body['goal_spec']['subgoals'][0]['criterion_ids'].append('count')
    declared = define(client, base, body)
    assert define(client, base, body)['version'] == declared['version']
    processed = finish_only_original_query_jobs(task)
    assert len(processed) == 1, 'this one-step query must execute exactly one original query Job'
    for index in range(2):
        task = reply(client.get('/api/workspace/tasks/' + task['id']))
        assert task['workflow_runtime']['current_step_id'] in {'check', 'record'}
        task = reply(complete(client, task, note='已核对原记录及保存的计数依据，第' + str(index + 1) + '步'))
    assert task['status'] == 'workflow_completed'
    assert finish_only_original_query_jobs(task) == []
    goal = reply(client.get(base))
    assert goal['data_query']['current_valid'] is True
    assert goal['check']['evaluation']['ready_for_acceptance'] is True
    state = persisted_state(task['id'])
    assert len(state['queries']) == len(state['calls']) == 1
    query = state['queries'][0]
    call = state['calls'][0]
    assert query[2] == 'sufficient' and call[1] == 'succeeded'
    assert call[2] == 'data-tool:' + query[0] + ':count_original'
    assert query[3]['native_query_job_id'] == processed[0]
    assert query[3]['result']['derived_records'][0]['values']['value'] == 1
    return {'release': release, 'object': obj, 'task': task, 'base': base,
        'definition': body, 'goal': goal, 'state': state, 'capability_id': pin['id']}


def test_normal_bound_query_survives_actual_readonly_task_and_assessment_then_locked_acceptance(client):
    value = ready_query(client)
    task_id, base = value['task']['id'], value['base']
    before = persisted_state(task_id)
    with captured_sql() as get_sql:
        task = reply(client.get('/api/workspace/tasks/' + task_id))
    assert task['status'] == 'workflow_completed'
    assert any('SET TRANSACTION READ ONLY' in statement.upper() for statement in get_sql)
    assert get_sql and not any(ROW_LOCK.search(statement) for statement in get_sql)
    assert persisted_state(task_id) == before

    me = reply(client.get('/api/me'))
    p = Principal(me['user_id'], me['workspace_id'], me['role'], 'native-query-readonly-pg-proof')
    # A non-managed linear Task GET does not itself build an approval outcome.
    # Exercise the complete optional acceptance assessment against the same
    # real API-generated rows in a genuine PostgreSQL read-only snapshot too.
    with captured_sql() as assessment_sql:
        with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                with Session(bind=connection, autoflush=False, expire_on_commit=False,
                        join_transaction_mode='rollback_only') as db:
                    actual = db.get(m.Task, task_id)
                    evidence, evaluation = native_goals.assessment(db, p, actual,
                        accepted=True, lock_sources=False)
                    assert evidence['data_query']['status'] == 'sufficient'
                    assert evaluation['completed'] is True
            finally:
                if transaction.is_active: transaction.rollback()
    assert assessment_sql and not any(ROW_LOCK.search(statement) for statement in assessment_sql)
    assert persisted_state(task_id) == before

    command = {'request_id': str(uuid4()), 'expected_version': task['version'],
        'reason': '逐项核对原查询、两步人工结果并正式验收；不认定现场真值',
        'basis_hash': evaluation['basis_hash']}
    with captured_sql() as write_sql:
        accepted = reply(client.post(base + '/accept', json=command))
    assert accepted['acceptance']['evaluation']['completed'] is True
    assert any(ROW_LOCK.search(statement) for statement in write_sql)
    assert any(m.Capability.__tablename__.upper() in statement.upper()
        and ROW_LOCK.search(statement) for statement in write_sql)
    again = reply(client.post(base + '/accept', json=command))
    assert again['version'] == accepted['version']
    after = persisted_state(task_id)
    assert after['queries'] == before['queries'] and after['calls'] == before['calls']
    assert after['all_task_queries'] == before['all_task_queries']
    assert after['jobs'] == before['jobs']
    with Session(engine()) as db:
        saved = db.get(m.Task, task_id)
        assert saved.data['native_goal_acceptance']['request_id'] == command['request_id']
        assert saved.data['workflow_runtime']['receipts'] == before['task_data']['workflow_runtime']['receipts']
        outcomes = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
            m.Memory.data['type'].as_string() == 'native_goal_outcome',
            m.Memory.data['task_id'].as_string() == task_id)))
        assert len(outcomes) == 1


def test_changed_registered_query_source_rejects_acceptance_without_reexecution(client):
    value = ready_query(client)
    before = persisted_state(value['task']['id'])
    with Session(engine()) as db:
        cap = db.get(m.Capability, value['capability_id'])
        original = copy.deepcopy(cap.data)
        cap.data = {**cap.data, 'description': '故意破坏原查询源摘要的负控'}
        db.commit()
    try:
        command = {'request_id': str(uuid4()), 'expected_version': value['goal']['version'],
            'reason': '来源损坏必须拒绝，不能复用旧预览授权',
            'basis_hash': value['goal']['check']['evaluation']['basis_hash']}
        response = client.post(value['base'] + '/accept', json=command)
        assert response.status_code == 409, response.text
        after = persisted_state(value['task']['id'])
        assert after == before
        assert not after['task_data'].get('native_goal_acceptance')
    finally:
        with Session(engine()) as db:
            cap = db.get(m.Capability, value['capability_id'])
            cap.data = original
            db.commit()
