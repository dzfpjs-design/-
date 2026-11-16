"""Pool settings/engine construction only; never connect to any database."""
import socket
import os
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Engine, make_url

from generic_agents import db
from generic_agents.config import Settings


def _dispose_idle_owned_test_engine(candidate, expected_database):
    if not isinstance(candidate, Engine):
        return False
    # Never dispose another environment/another test run, and never interrupt
    # a still-running fixture transaction. No SQL or session termination here.
    assert type(expected_database) is str and expected_database.startswith('ga_test_')
    assert candidate.url.get_backend_name() == 'postgresql'
    assert candidate.url.database == expected_database
    assert candidate.pool.checkedout() == 0
    candidate.dispose()
    return True


@pytest.fixture(autouse=True)
def isolated_pool_configuration(monkeypatch, request):
    expected_database = None
    if not request.config.getoption('noconftest'):
        # The global fixture's with engine().begin() has FINISHED when it
        # returns; making this dependency explicit prevents premature disposal.
        request.getfixturevalue('isolate_worker_queue')
        expected_database = make_url(os.environ['GA_DATABASE_URL']).database
        captured = db.engine()
        _dispose_idle_owned_test_engine(captured, expected_database)
    else:
        # An optional fresh-process pure run must have no real cached engine.
        assert db.engine.cache_info().currsize == 0
    db.engine.cache_clear()
    def forbidden(*args, **kwargs):
        raise AssertionError('Pool configuration tests must not connect')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    for name in ('GA_DATABASE_POOL_SIZE', 'GA_DATABASE_MAX_OVERFLOW', 'GA_DATABASE_POOL_TIMEOUT'):
        monkeypatch.delenv(name, raising=False)
    yield
    if db.engine.cache_info().currsize:
        # Test fake engines have no connections. Any genuine engine must be
        # exactly the disposable engine owned by this run before being cleared.
        _dispose_idle_owned_test_engine(db.engine(), expected_database)
    db.engine.cache_clear()
    # The dependent monkeypatch fixture then restores settings/create_engine/
    # environment. The next global fixture cannot inherit a fake cached engine.


def configured(**values):
    # Explicitly bypass local .env; synthetic URLs carry no real credentials.
    return Settings(_env_file=None, database_url='postgresql+psycopg://example.invalid/test',
        redis_url='redis://example.invalid/0', **values)


def test_defaults_preserve_existing_sqlalchemy_postgres_pool_limits():
    value = configured()
    assert (value.database_pool_size, value.database_max_overflow, value.database_pool_timeout) == (5, 10, 30)


def test_ga_environment_accepts_h_isolated_bounded_pool_and_timeout(monkeypatch):
    monkeypatch.setenv('GA_DATABASE_POOL_SIZE', '2')
    monkeypatch.setenv('GA_DATABASE_MAX_OVERFLOW', '0')
    monkeypatch.setenv('GA_DATABASE_POOL_TIMEOUT', '120')
    value = configured()
    assert (value.database_pool_size, value.database_max_overflow, value.database_pool_timeout) == (2, 0, 120)


@pytest.mark.parametrize('field,low,high', [
    ('database_pool_size', 1, 64), ('database_max_overflow', 0, 64),
    ('database_pool_timeout', 1, 600),
])
def test_exact_integer_and_decimal_environment_boundaries(field, low, high, monkeypatch):
    for valid in (low, high, str(low), str(high)):
        assert getattr(configured(**{field: valid}), field) == int(valid)
    for invalid in (low - 1, high + 1, str(high + 1), True, False, 1.0, 1.5,
            '1.0', '1e1', '+1', '-1', ' 1', '1 ', '', None, [], {}, '１', '999999999999999'):
        with pytest.raises(ValidationError):
            configured(**{field: invalid})
    for invalid in ('true', 'false', '-1', '1.0', '1e1', ' 2', str(high + 1)):
        monkeypatch.setenv('GA_' + field.upper(), invalid)
        with pytest.raises(ValidationError):
            configured()


@pytest.mark.parametrize('url', [
    'postgresql://example.invalid/test', 'postgresql+psycopg://example.invalid/test',
    'postgresql+psycopg2://example.invalid/test',
])
def test_only_postgresql_driver_variants_receive_actual_pool_controls(monkeypatch, url):
    calls, created = [], object()
    monkeypatch.setattr(db, 'settings', lambda: SimpleNamespace(database_url=url,
        database_pool_size=2, database_max_overflow=0, database_pool_timeout=120))
    def fake_engine(actual_url, **options):
        calls.append((actual_url, options))
        return created
    monkeypatch.setattr(db, 'create_engine', fake_engine)
    assert db.engine() is created
    assert calls == [(url, {'pool_pre_ping': True, 'hide_parameters': True,
        'pool_size': 2, 'max_overflow': 0, 'pool_timeout': 120})]
    assert db.engine() is created and len(calls) == 1


@pytest.mark.parametrize('url', ['sqlite://', 'sqlite+pysqlite:///:memory:', 'mysql+pymysql://example.invalid/test'])
def test_other_backends_keep_original_kwargs_without_requiring_pg_settings(monkeypatch, url):
    calls = []
    monkeypatch.setattr(db, 'settings', lambda: SimpleNamespace(database_url=url))
    monkeypatch.setattr(db, 'create_engine', lambda actual_url, **options: calls.append((actual_url, options)))
    db.engine()
    assert calls == [(url, {'pool_pre_ping': True, 'hide_parameters': True})]


def test_default_pg_engine_uses_same_defaults_and_config_cache_is_not_bypassed(monkeypatch):
    value, calls = configured(), []
    monkeypatch.setattr(db, 'settings', lambda: value)
    monkeypatch.setattr(db, 'create_engine', lambda url, **options: calls.append(options) or object())
    first = db.engine()
    assert calls == [{'pool_pre_ping': True, 'hide_parameters': True,
        'pool_size': 5, 'max_overflow': 10, 'pool_timeout': 30}]
    value.database_pool_size = 2
    assert db.engine() is first and len(calls) == 1
    db.engine.cache_clear()
    assert db.engine() is not first and calls[1]['pool_size'] == 2


def test_cleanup_never_disposes_foreign_or_checked_out_engines(monkeypatch):
    class Candidate:
        def __init__(self, database, checked_out=0, backend='postgresql'):
            self.url = SimpleNamespace(database=database, get_backend_name=lambda: backend)
            self.pool = SimpleNamespace(checkedout=lambda: checked_out)
            self.disposed = 0
        def dispose(self):
            self.disposed += 1
    monkeypatch.setattr(sys.modules[__name__], 'Engine', Candidate)
    owned = Candidate('ga_test_this_run')
    assert _dispose_idle_owned_test_engine(owned, 'ga_test_this_run')
    assert owned.disposed == 1
    for foreign in (Candidate('production'), Candidate('ga_test_another_run'),
            Candidate('ga_test_this_run', checked_out=1), Candidate('ga_test_this_run', backend='sqlite')):
        with pytest.raises(AssertionError):
            _dispose_idle_owned_test_engine(foreign, 'ga_test_this_run')
        assert foreign.disposed == 0
    assert _dispose_idle_owned_test_engine(object(), 'ga_test_this_run') is False
