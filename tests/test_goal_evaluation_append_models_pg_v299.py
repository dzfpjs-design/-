"""Real disposable PG table/constraint/immutability tests, not append authority.

Only minimal FK parents and fictional storage payloads are seeded. No Goal,
sealed command, ACCEPTED Audit, live capability, producer, SDK, Qe, evaluation,
approval, device, receiver or model is authenticated by these fixtures.
"""
import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
from generic_agents.typed_payload_orm_v131 import FORMAT, CANONICAL


@pytest.fixture(scope='module', autouse=True)
def installed_append_table(disposable_database):
    # Conftest has selected its disposable DB. This late model may have been
    # imported after initial migrate; use its real registered PG DDL events.
    assert engine().dialect.name == 'postgresql'
    Command.__table__.create(engine(), checkfirst=True)


def snapshot(db, identity):
    # Physical JSON text includes typed canonical/format columns and all row
    # fields. It never includes a connection URL or credentials.
    return db.scalar(text('SELECT row_to_json(t)::text FROM goal_evaluation_append_commands_v297 AS t WHERE id=:id'),
        {'id': identity})


@pytest.fixture
def stored(account):
    payload = {'fixture': 'storage-only; not a sealed accepted append command',
        'fraction': 1.0, 'large_integer': 9007199254740993, 'nested': [{'saved': True}]}
    with Session(engine()) as db:
        actor = db.scalar(select(m.User.id).where(m.User.username == account[0]))
        workspace = account[2]
        package = m.Capability(workspace_id=workspace, namespace='append_storage_v299', name='package',
            release_version='1.0.0', kind='domain_package', status='draft',
            content_hash=digest({'fixture': 'package FK only'}), data={'fixture': 'package FK only'})
        cap = m.Capability(workspace_id=workspace, namespace='append_storage_v299', name='evaluation',
            release_version='1.0.0', kind='tool', status='draft',
            content_hash=digest({'fixture': 'tool FK only'}), data={'fixture': 'tool FK only'})
        db.add_all([package, cap]); db.flush()
        task = m.Task(workspace_id=workspace, title='追加表存储约束验证', status='draft',
            owner_id=actor, package_id=package.id, data={'fixture': 'Task FK only'})
        job = m.Job(workspace_id=workspace, kind='storage_fixture_v299', status='queued',
            idempotency_key='storage-fixture:' + str(uuid4()), data={'fixture': 'Job FK only'})
        db.add_all([task, job]); db.flush()
        values = {'id': str(uuid4()), 'workspace_id': workspace, 'task_id': task.id, 'actor_id': actor,
            'version': 1, 'goal_version': 1, 'goal_hash': 'a' * 64, 'request_id': str(uuid4()),
            'body_hash': 'b' * 64, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
            'input_hash': 'c' * 64, 'job_id': job.id, 'invocation_key': 'storage-command:' + str(uuid4()),
            'record_hash': digest(payload), 'data': copy.deepcopy(payload)}
        row = Command(**values); db.add(row); db.commit()
        physical = snapshot(db, row.id)
        assert physical is not None
        return SimpleNamespace(values=values, payload=payload, identity=row.id, physical=physical)


def candidate(db, stored):
    """Fresh FK Job/nonce/key isolate each new-table constraint under test."""
    job = m.Job(workspace_id=stored.values['workspace_id'], kind='storage_fixture_v299', status='queued',
        idempotency_key='independent-storage-fixture:' + str(uuid4()), data={'fixture': 'independent FK only'})
    db.add(job); db.flush()
    values = copy.deepcopy(stored.values)
    values.update(id=str(uuid4()), request_id=str(uuid4()), job_id=job.id,
        invocation_key='independent-storage-command:' + str(uuid4()))
    return values


def assert_database_error(error, state):
    assert getattr(error.orig, 'sqlstate', None) == state
    assert getattr(error.orig.diag, 'table_name', None) == Command.__tablename__ or state == '55000'


def test_actual_schema_has_scoped_unique_keys_fk_parents_and_named_checks():
    metadata = inspect(engine()); name = Command.__tablename__
    columns = {item['name'] for item in metadata.get_columns(name)}
    assert {FORMAT, CANONICAL, 'data', 'task_id', 'job_id', 'request_id', 'invocation_key', 'record_hash'} <= columns
    assert {tuple(item['column_names']) for item in metadata.get_unique_constraints(name)} == {
        ('workspace_id', 'task_id', 'request_id'), ('workspace_id', 'job_id'), ('workspace_id', 'invocation_key')}
    parents = {tuple(item['constrained_columns']): (item['referred_table'], tuple(item['referred_columns']))
        for item in metadata.get_foreign_keys(name)}
    assert parents == {('workspace_id',): ('workspaces', ('id',)), ('task_id',): ('tasks', ('id',)),
        ('actor_id',): ('users', ('id',)), ('capability_id',): ('capabilities', ('id',)),
        ('job_id',): ('jobs', ('id',))}
    checks = {item['name']: item['sqltext'] for item in metadata.get_check_constraints(name)}
    assert 'version = 1' in checks['ck_goal_eval_append_v297_version']
    assert 'goal_version >= 1' in checks['ck_goal_eval_append_v297_goal_version']


def test_real_append_and_cross_session_cold_reads_preserve_exact_storage_and_types(stored):
    s = stored
    with Session(engine()) as db:
        first = db.get(Command, s.identity)
        assert first.data == s.payload and type(first.data['fraction']) is float
        assert type(first.data['large_integer']) is int and first.data['large_integer'] == 9007199254740993
        values = candidate(db, s); db.add(Command(**values)); db.commit(); second_id = values['id']
        assert snapshot(db, s.identity) == s.physical
    with Session(engine()) as db:
        physical = snapshot(db, s.identity)
        first = db.scalar(select(Command).where(Command.id == s.identity).execution_options(populate_existing=True))
        assert first.data == s.payload and first.version == 1 and db.get(Command, second_id) is not None
        db.expire_all()
        assert db.get(Command, s.identity).data == s.payload
        assert snapshot(db, s.identity) == physical == s.physical
        assert len(list(db.scalars(select(Command)))) == 2
        assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('operation', ['orm_update', 'orm_delete', 'core_update', 'core_delete',
    'raw_noop_update', 'raw_delete'])
def test_actual_pg_rejects_every_update_delete_path_and_keeps_original_after_savepoint(stored, operation):
    s = stored
    with Session(engine()) as db:
        with pytest.raises(DBAPIError) as rejected:
            with db.begin_nested():
                if operation == 'orm_update':
                    db.get(Command, s.identity).record_hash = 'f' * 64; db.flush()
                elif operation == 'orm_delete':
                    db.delete(db.get(Command, s.identity)); db.flush()
                elif operation == 'core_update':
                    db.execute(update(Command).where(Command.id == s.identity).values(body_hash='f' * 64))
                elif operation == 'core_delete':
                    db.execute(delete(Command).where(Command.id == s.identity))
                elif operation == 'raw_noop_update':
                    db.execute(text('UPDATE goal_evaluation_append_commands_v297 SET record_hash=record_hash WHERE id=:id'),
                        {'id': s.identity})
                else:
                    db.execute(text('DELETE FROM goal_evaluation_append_commands_v297 WHERE id=:id'), {'id': s.identity})
        assert_database_error(rejected.value, '55000')
        assert snapshot(db, s.identity) == s.physical
        assert db.get(Command, s.identity, populate_existing=True).data == s.payload
        assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('duplicate', ['request', 'job', 'invocation_key'])
def test_real_unique_constraints_prevent_reusing_request_job_or_invocation(stored, duplicate):
    s = stored
    with Session(engine()) as db:
        values = candidate(db, s)
        key = {'request': 'request_id', 'job': 'job_id', 'invocation_key': 'invocation_key'}[duplicate]
        values[key] = s.values[key]
        with pytest.raises(IntegrityError) as rejected:
            with db.begin_nested(): db.add(Command(**values)); db.flush()
        assert_database_error(rejected.value, '23505')
        assert snapshot(db, s.identity) == s.physical and len(list(db.scalars(select(Command)))) == 1


@pytest.mark.parametrize('column', ['workspace_id', 'task_id', 'actor_id', 'capability_id', 'job_id'])
def test_real_fk_constraints_refuse_missing_parents_without_overwriting_original(stored, column):
    s = stored
    with Session(engine()) as db:
        values = candidate(db, s); values[column] = str(uuid4())
        with pytest.raises(IntegrityError) as rejected:
            with db.begin_nested(): db.add(Command(**values)); db.flush()
        assert_database_error(rejected.value, '23503')
        assert snapshot(db, s.identity) == s.physical and len(list(db.scalars(select(Command)))) == 1


@pytest.mark.parametrize('column,value', [('version', 2), ('goal_version', 0)])
def test_real_check_constraints_refuse_noninitial_version_or_nonpositive_goal_version(stored, column, value):
    s = stored
    with Session(engine()) as db:
        values = candidate(db, s); values[column] = value
        with pytest.raises(IntegrityError) as rejected:
            with db.begin_nested(): db.add(Command(**values)); db.flush()
        assert_database_error(rejected.value, '23514')
        assert snapshot(db, s.identity) == s.physical and len(list(db.scalars(select(Command)))) == 1
