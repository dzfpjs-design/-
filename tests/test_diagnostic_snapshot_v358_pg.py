"""Disposable PostgreSQL short tests for the staged diagnostic snapshot only.

These tests do not establish the full maturity chain or a real business flow.
The session-wide conftest creates and drops a randomly named ga_test_ database.
"""
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Audit, Job, Observation, Task, Workspace


_STAGE = (Path(__file__).resolve().parents[1] / '.agentic-engineering' /
          'staging-auto-evaluation-diagnostics' / 'bounded_diagnostics.py')
_spec = importlib.util.spec_from_file_location('v358_staged_bounded_diagnostics', _STAGE)
diagnostics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diagnostics)


@pytest.fixture(autouse=True)
def require_disposable_postgresql():
    source = engine()
    assert source.dialect.name == 'postgresql'
    assert (source.url.database or '').startswith('ga_test_')


def _workspace(name):
    with Session(engine()) as db:
        row = Workspace(name=name)
        db.add(row)
        db.commit()
        return row.id


def _workspace_name(identity):
    with Session(engine()) as db:
        return db.scalar(select(Workspace.name).where(Workspace.id == identity))


def test_snapshot_is_read_only_repeatable_read_and_rolls_back():
    with diagnostics._read_snapshot() as db:
        assert db.autoflush is False
        assert db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
        assert db.scalar(text('SHOW transaction_read_only')) == 'on'
        connection = db.connection()
    assert not connection.in_transaction()
    with Session(engine()) as db:
        assert db.scalar(text('SELECT 1')) == 1


def test_write_and_row_lock_are_rejected_without_changing_data():
    identity = _workspace('before')
    for command in (
        text('UPDATE workspaces SET name = :name WHERE id = :identity'),
        text('SELECT id FROM workspaces WHERE id = :identity FOR UPDATE'),
    ):
        with pytest.raises(DBAPIError) as error:
            with diagnostics._read_snapshot() as db:
                db.execute(command, {'identity': identity, 'name': 'changed'})
        assert getattr(error.value.orig, 'sqlstate', None) == '25006'
        assert _workspace_name(identity) == 'before'

    with pytest.raises(DBAPIError) as error:
        with diagnostics._read_snapshot() as db:
            db.add(Workspace(name='must-not-persist'))
            db.flush()  # Explicit flush is also rejected by the database.
    assert getattr(error.value.orig, 'sqlstate', None) == '25006'
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Workspace).where(
            Workspace.name == 'must-not-persist')) == 0


def test_committed_change_is_invisible_until_a_new_snapshot():
    identity = _workspace('before')
    query = text('SELECT name FROM workspaces WHERE id = :identity')
    with diagnostics._read_snapshot() as reader:
        assert reader.scalar(query, {'identity': identity}) == 'before'
        # A distinct session/connection commits after the reader took its snapshot.
        with Session(engine()) as writer:
            row = writer.get(Workspace, identity)
            row.name = 'after'
            writer.commit()
        assert reader.scalar(query, {'identity': identity}) == 'before'
    with diagnostics._read_snapshot() as reader:
        assert reader.scalar(query, {'identity': identity}) == 'after'


def test_missing_scope_returns_without_writes():
    workspace_id, task_id, observation_id, producer_id = (str(uuid4()) for _ in range(4))
    classes = (Workspace, Task, Observation, Job, Audit)
    with Session(engine()) as db:
        before = tuple(db.scalar(select(func.count()).select_from(cls)) for cls in classes)
    statements = []

    def observe_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    source = engine()
    event.listen(source, 'before_cursor_execute', observe_sql)
    try:
        result = diagnostics.capture(workspace_id, task_id, observation_id, producer_id, None)
    finally:
        event.remove(source, 'before_cursor_execute', observe_sql)
    assert result == {'state': 'scope_unavailable'}
    assert not set(statements).intersection({'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'TRUNCATE'})
    with Session(engine()) as db:
        after = tuple(db.scalar(select(func.count()).select_from(cls)) for cls in classes)
    assert after == before
