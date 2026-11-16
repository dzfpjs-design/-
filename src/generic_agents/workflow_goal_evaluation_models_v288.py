"""Independent append-only Goal evaluations; original DataRuns stay untouched.

Importing this late model registers metadata/listeners only. It does not create
tables, migrate, start a transaction or change a Task's original query anchor.
Root's controlled installation must create this table and verify its PostgreSQL
guard before enabling the V288 writers. Hashes and foreign keys grant nothing.
"""
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DDL, DateTime, ForeignKey, Integer, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped
from . import workflow_goal_evaluation_contracts_v288 as contracts

IMMUTABLE_ERROR = 'GoalEvaluationV288 records are append-only'
PG_GUARD_FUNCTION = '''
CREATE OR REPLACE FUNCTION ga_goal_evaluation_v288_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $v288$
BEGIN
  RAISE EXCEPTION 'GoalEvaluationV288 records are append-only' USING ERRCODE = '55000';
END;
$v288$
'''
PG_GUARD_TRIGGER = '''
CREATE TRIGGER tr_goal_evaluation_v288_append_only
BEFORE UPDATE OR DELETE ON %(fullname)s
FOR EACH ROW EXECUTE FUNCTION ga_goal_evaluation_v288_reject_mutation()
'''


class GoalEvaluationV288(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_goal_evaluations_v288'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'task_id', 'goal_version', 'generation'),
        UniqueConstraint('workspace_id', 'request_id'),
        UniqueConstraint('workspace_id', 'request_key'),
        UniqueConstraint('evaluation_query_id'),
        UniqueConstraint('evidence_id'),
        UniqueConstraint('invocation_id'),
        CheckConstraint('version = 1', name='ck_goal_evaluation_v288_version'),
        CheckConstraint('goal_version >= 1 AND generation >= 1', name='ck_goal_evaluation_v288_generation'),
        CheckConstraint('original_query_id <> evaluation_query_id', name='ck_goal_evaluation_v288_separate_query'),
        CheckConstraint('id = invocation_id AND request_id = job_id', name='ck_goal_evaluation_v288_original_request'),
    )
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    package_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    goal_version: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    original_query_id: Mapped[str] = mapped_column(ForeignKey('data_query_runs.id'), index=True)
    evaluation_query_id: Mapped[str] = mapped_column(ForeignKey('data_query_runs.id'), index=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))
    invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    request_key: Mapped[str] = mapped_column(String(200))
    goal_hash: Mapped[str] = mapped_column(String(64))
    goal_snapshot_hash: Mapped[str] = mapped_column(String(64))
    source_binding_hash: Mapped[str] = mapped_column(String(64))
    graph_hash: Mapped[str] = mapped_column(String(64))
    preparation_policy_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))
    cut_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    simulation_only: Mapped[bool] = mapped_column(Boolean)
    test_only: Mapped[bool] = mapped_column(Boolean)


def row_values(record):
    """Detached ORM constructor values after the caller's independent checks.

    This repeats stored structural checks only. Its self-supplied scope/time
    must never replace the builder's actual DB/source/ownership verification.
    The existing creator Invocation id also identifies this evaluation; the
    existing creator Job id is its original request id. Neither is regenerated.
    """
    value = contracts.check_record(record, expected_scope=record['scope'],
        goal_snapshot=record['goal_snapshot'], preparation_policy=record['preparation_policy'],
        trusted_now=record['cut_checked_at'])
    scope = value['scope']
    return {
        **{key: scope[key] for key in ('workspace_id', 'task_id', 'instance_id', 'release_id',
            'package_id', 'goal_snapshot_hash', 'source_binding_hash', 'graph_hash')},
        'id': value['evaluation_id'], 'version': 1, 'actor_id': value['producer']['actor_id'],
        'goal_version': value['goal_snapshot']['version'], 'generation': value['generation'],
        'original_query_id': value['original_query_ref']['query_id'],
        'evaluation_query_id': value['evaluation_query_ref']['query_id'],
        'evidence_id': value['evidence_ref']['evidence_id'],
        'invocation_id': value['producer']['invocation_id'], 'job_id': value['producer']['job_id'],
        'request_id': value['request_id'], 'request_key': value['request_key'],
        'goal_hash': value['goal_snapshot']['hash'],
        'preparation_policy_hash': value['preparation_policy']['policy_hash'],
        'record_hash': value['record_hash'],
        'cut_at': contracts._instant(value['cut_at']),
        'simulation_only': value['simulation_only'], 'test_only': value['test_only'], 'data': value,
    }


@event.listens_for(GoalEvaluationV288, 'before_insert')
def _check_columns(mapper, connection, target):
    # Independent FK columns cannot disagree with the immutable stored pins.
    # Payload's existing Base hook captures original typed canonical JSON.
    expected = row_values(target.data)
    for key, value in expected.items():
        actual = getattr(target, key)
        if key == 'data':
            matches = contracts._json(actual) == contracts._json(value)
        elif key == 'cut_at':
            matches = isinstance(actual, datetime) and actual.tzinfo is not None and actual == value
        else:
            matches = type(actual) is type(value) and actual == value
        if not matches:
            raise contracts.GoalEvaluationContractError(contracts.ERROR)


@event.listens_for(GoalEvaluationV288, 'before_update')
@event.listens_for(GoalEvaluationV288, 'before_delete')
def _immutable_record(mapper, connection, target):
    raise ValueError(IMMUTABLE_ERROR)


# No DDL executes on import. Core/bulk/direct-SQL updates and deletes are
# rejected by PostgreSQL after the table is created through this metadata.
event.listen(GoalEvaluationV288.__table__, 'after_create',
    DDL(PG_GUARD_FUNCTION).execute_if(dialect='postgresql'))
event.listen(GoalEvaluationV288.__table__, 'after_create',
    DDL(PG_GUARD_TRIGGER).execute_if(dialect='postgresql'))
