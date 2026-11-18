"""Test targeted DDL only in an additional disposable, identity-checked database."""
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, event, inspect, text
from generic_agents.db import engine
from generic_agents.models import Workspace, User, SetupReportDraft
from generic_agents.report_full_provisional_migration import install
from generic_agents.typed_payload_migration_v131 import _TRIGGER_FUNCTION


@pytest.fixture
def isolated_database():
    current=engine().url
    assert current.host=='127.0.0.1' and current.database.startswith('ga_test_')
    name='ga_test_install_'+uuid4().hex[:12]
    admin=create_engine(current.set(database='postgres'),isolation_level='AUTOCOMMIT')
    with admin.connect() as c:
        c.execute(text('CREATE DATABASE "'+name+'"'))
    target=create_engine(current.set(database=name))
    try:
        Workspace.__table__.create(target);User.__table__.create(target);SetupReportDraft.__table__.create(target)
        yield target
    finally:
        target.dispose()
        assert name.startswith('ga_test_install_')
        with admin.connect() as c:
            assert c.scalar(text('SELECT count(*) FROM pg_stat_activity WHERE datname=:name'),{'name':name})==0
            c.execute(text('DROP DATABASE "'+name+'"'))
        admin.dispose()


def create_guard(db, valid=True):
    definition=_TRIGGER_FUNCTION if valid else _TRIGGER_FUNCTION.replace("IF TG_OP = 'INSERT'", "IF TG_OP = 'DELETE'")
    with db.begin() as c: c.execute(text(definition))


def exists(db):
    return inspect(db).has_table('report_full_provisional_candidates',schema='public')


def function_text(db):
    with db.connect() as c:
        return c.scalar(text("SELECT prosrc FROM pg_proc WHERE oid='public.ga_typed_payload_no_downgrade_v131()'::regprocedure"))


def test_targeted_install_uses_existing_guard_without_replacing_it(isolated_database):
    db=isolated_database;create_guard(db);before=function_text(db)
    install(db)
    assert exists(db) and function_text(db)==before
    with db.connect() as c:
        names=c.scalars(text("SELECT tgname FROM pg_trigger WHERE tgrelid='public.report_full_provisional_candidates'::regclass AND NOT tgisinternal ORDER BY tgname")).all()
    assert names==['tr_ga_typed_payload_data_v131','tr_ga_typed_payload_v131']
    with pytest.raises(RuntimeError,match='already exists'):install(db)
    assert function_text(db)==before


@pytest.mark.parametrize('invalid', ['missing','different'])
def test_missing_or_changed_guard_does_not_create_table(isolated_database,invalid):
    db=isolated_database
    if invalid=='different':create_guard(db,valid=False)
    with pytest.raises(RuntimeError,match='absent or differs'):install(db)
    assert not exists(db)


def test_guard_install_failure_rolls_back_table(isolated_database):
    db=isolated_database;create_guard(db);before=function_text(db)
    def reject_second(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('CREATE TRIGGER tr_ga_typed_payload_data_v131'):
            raise RuntimeError('synthetic installation failure')
    event.listen(db,'before_cursor_execute',reject_second)
    try:
        with pytest.raises(RuntimeError,match='synthetic installation failure'):install(db)
    finally:event.remove(db,'before_cursor_execute',reject_second)
    assert not exists(db) and function_text(db)==before
