"""Dependency-order tests with explicit connection/session doubles; no SQL runs."""
import inspect

import pytest

from generic_agents import material_ocr_read_session as reads
from generic_agents.db import session
from generic_agents.security import authenticated
from generic_agents.snapshot_authentication_v141 import snapshot_authenticated


class Transaction:
    def __init__(self, calls): self.calls = calls; self.is_active = True
    def rollback(self): self.calls.append('rollback'); self.is_active = False


class Connection:
    def __init__(self, calls, failure=None):
        self.calls = calls; self.failure = failure
        self.transaction = Transaction(calls)
    def execution_options(self, **options):
        assert options == {'isolation_level': 'REPEATABLE READ'}
        self.calls.append('isolation_before_begin'); return self
    def __enter__(self): self.calls.append('connection_enter'); return self
    def __exit__(self, *args): self.calls.append('connection_close')
    def begin(self): self.calls.append('begin'); return self.transaction
    def exec_driver_sql(self, statement):
        assert statement == 'SET TRANSACTION READ ONLY'
        assert self.calls[-1] == 'begin'
        self.calls.append('read_only_first_sql')
        if self.failure: raise self.failure


def dependency_fixture(monkeypatch, failure=None):
    calls = []; connection = Connection(calls, failure)
    class Engine:
        def connect(self): calls.append('connect'); return connection
    class Session:
        def __init__(self, **options):
            assert options == {'bind': connection, 'expire_on_commit': False,
                               'autoflush': False, 'join_transaction_mode': 'rollback_only'}
            assert calls[-1] == 'read_only_first_sql'
            calls.append('session_after_read_only')
        def __enter__(self): return self
        def __exit__(self, *args): calls.append('session_close')
    monkeypatch.setattr(reads, 'engine', lambda: Engine())
    monkeypatch.setattr(reads, 'Session', Session)
    return calls, connection


def test_original_authentication_is_required_before_independent_data_dependency():
    parameter = inspect.signature(reads.read_session).parameters['_principal']
    assert parameter.default.dependency is snapshot_authenticated
    dependencies = inspect.signature(snapshot_authenticated).parameters
    assert dependencies['principal'].default.dependency is authenticated
    assert dependencies['auth_db'].default.dependency is session
    assert parameter.default.use_cache and dependencies['principal'].default.use_cache
    assert dependencies['auth_db'].default.use_cache


@pytest.mark.parametrize('failure', [False, True])
def test_snapshot_settings_precede_session_and_success_or_error_only_roll_back(monkeypatch, failure):
    calls, connection = dependency_fixture(monkeypatch)
    reader = reads.read_session(object())
    assert next(reader) is not None
    assert connection.transaction.is_active
    if failure:
        with pytest.raises(ValueError, match='original handler failure'):
            reader.throw(ValueError('original handler failure'))
    else:
        reader.close()
    assert calls == ['connect', 'isolation_before_begin', 'connection_enter', 'begin',
                     'read_only_first_sql', 'session_after_read_only', 'session_close',
                     'rollback', 'connection_close']


def test_read_only_setup_error_propagates_before_any_orm_session(monkeypatch):
    calls, connection = dependency_fixture(monkeypatch, RuntimeError('explicit setup failure'))
    reader = reads.read_session(object())
    with pytest.raises(RuntimeError, match='explicit setup failure'):
        next(reader)
    assert 'session_after_read_only' not in calls
    assert calls[-2:] == ['rollback', 'connection_close']
    assert not connection.transaction.is_active
