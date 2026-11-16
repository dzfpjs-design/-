"""Engine-construction spies only; no database, socket or cached engine work."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.pool import NullPool

from generic_agents import db
from generic_agents.config import Settings


@pytest.fixture(autouse=True)
def isolated_mode(monkeypatch):
    monkeypatch.delenv('GA_DATABASE_POOL_MODE', raising=False)


def configured(**values):
    return Settings(_env_file=None,
        database_url='postgresql+psycopg://example.invalid/pool_mode_test',
        redis_url='redis://example.invalid/0', **values)


def test_default_mode_and_explicit_environment_are_exact(monkeypatch):
    assert configured().database_pool_mode == 'pooled'
    assert configured(database_pool_mode='no_idle').database_pool_mode == 'no_idle'
    monkeypatch.setenv('GA_DATABASE_POOL_MODE', 'no_idle')
    assert configured().database_pool_mode == 'no_idle'
    monkeypatch.setenv('GA_DATABASE_POOL_MODE', ' no_idle')
    with pytest.raises(ValidationError):
        configured()


@pytest.mark.parametrize('value', ['', 'Pooled', 'NO_IDLE', ' no_idle', 'no_idle ',
    'no-idle', 'none', True, False, 0, 1, None, [], {}])
def test_invalid_mode_is_not_coerced_or_normalized(value):
    with pytest.raises(ValidationError):
        configured(database_pool_mode=value)


@pytest.mark.parametrize('mode', ['pooled', 'no_idle'])
def test_postgres_mode_selects_only_its_supported_engine_kwargs(monkeypatch, mode):
    url = 'postgresql+psycopg://example.invalid/pool_mode_test'
    settings = SimpleNamespace(database_url=url, database_pool_mode=mode)
    if mode == 'pooled':
        settings.database_pool_size = 2
        settings.database_max_overflow = 0
        settings.database_pool_timeout = 120
    calls = []; marker = object()
    monkeypatch.setattr(db, 'settings', lambda: settings)
    monkeypatch.setattr(db, 'create_engine', lambda actual_url, **kwargs:
        calls.append((actual_url, kwargs)) or marker)
    # Exercise the actual factory without clearing/replacing an unrelated
    # cached test/application engine, disposing pools or opening connections.
    assert db.engine.__wrapped__() is marker
    expected = {'pool_pre_ping': True, 'hide_parameters': True}
    if mode == 'pooled':
        expected.update(pool_size=2, max_overflow=0, pool_timeout=120)
    else:
        expected['poolclass'] = NullPool
    assert calls == [(url, expected)]


def test_default_other_backend_keeps_original_engine_kwargs(monkeypatch):
    calls = []
    monkeypatch.setattr(db, 'settings', lambda: SimpleNamespace(database_url='sqlite://'))
    monkeypatch.setattr(db, 'create_engine', lambda url, **kwargs: calls.append((url, kwargs)))
    db.engine.__wrapped__()
    assert calls == [('sqlite://', {'pool_pre_ping': True, 'hide_parameters': True})]


def test_corrupt_mode_cannot_silently_fall_back_to_a_pool(monkeypatch):
    calls = []
    monkeypatch.setattr(db, 'settings', lambda: SimpleNamespace(
        database_url='postgresql+psycopg://example.invalid/pool_mode_test', database_pool_mode='invalid'))
    monkeypatch.setattr(db, 'create_engine', lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(ValueError):
        db.engine.__wrapped__()
    assert calls == []
