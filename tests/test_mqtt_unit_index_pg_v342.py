"""Real PostgreSQL verifies uniqueness, migration, definition and query plan."""
from uuid import uuid4

import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from generic_agents import mqtt_unit_index_v342 as migration
from generic_agents.db import engine
from generic_agents.models import Audit,User


def test_unit_proof_index_is_unique_and_used_for_scoped_lookup(account):
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));identity=str(uuid4())
        db.add(Audit(workspace_id=account[2],actor_id=user.id,object_id=identity,action=migration.ACTION,data={}))
        db.commit()
        db.add(Audit(workspace_id=account[2],actor_id=user.id,object_id=identity,action=migration.ACTION,data={}))
        with pytest.raises(IntegrityError):db.commit()
        db.rollback()
        db.execute(text('SET LOCAL enable_seqscan=off'))
        plan=db.execute(text("EXPLAIN SELECT id FROM audit_events WHERE workspace_id=:w AND object_id=:o "
                             "AND action='mqtt.observation.units_captured_v342'"),{'w':account[2],'o':identity}).all()
        assert migration.NAME in '\n'.join(row[0] for row in plan)


def test_upgrade_of_existing_database_is_idempotent_and_non_destructive(disposable_database):
    assert engine().url.database.startswith('ga_test_')
    with engine().connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        conn.execute(text('DROP INDEX CONCURRENTLY IF EXISTS uq_audit_mqtt_units_v342'))
    migration.ensure(engine());migration.ensure(engine())
    with engine().connect() as conn:migration._checked(conn)


def test_same_named_wrong_index_is_not_silently_accepted(disposable_database):
    assert engine().url.database.startswith('ga_test_')
    try:
        with engine().connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.execute(text('DROP INDEX CONCURRENTLY IF EXISTS uq_audit_mqtt_units_v342'))
            conn.execute(text('CREATE INDEX CONCURRENTLY uq_audit_mqtt_units_v342 ON audit_events (action)'))
        with pytest.raises(RuntimeError):migration.ensure(engine())
    finally:
        with engine().connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.execute(text('DROP INDEX CONCURRENTLY IF EXISTS uq_audit_mqtt_units_v342'))
        migration.ensure(engine())
