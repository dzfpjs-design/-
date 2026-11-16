"""Immutable append commands. Progress comes from real Jobs and receipts.

Import registers metadata only; Root must explicitly install the new table and
its PostgreSQL mutation guard before exposing a runner or API.
"""
from sqlalchemy import CheckConstraint, DDL, ForeignKey, Integer, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class GoalEvaluationAppendCommandV297(Row, Scoped, Payload, Base):
    __tablename__ = 'goal_evaluation_append_commands_v297'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'task_id', 'request_id'),
        UniqueConstraint('workspace_id', 'job_id'),
        UniqueConstraint('workspace_id', 'invocation_key'),
        CheckConstraint('version = 1', name='ck_goal_eval_append_v297_version'),
        CheckConstraint('goal_version >= 1', name='ck_goal_eval_append_v297_goal_version'),
    )
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    goal_version: Mapped[int] = mapped_column(Integer)
    goal_hash: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(36))
    body_hash: Mapped[str] = mapped_column(String(64))
    capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    capability_hash: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'), index=True)
    invocation_key: Mapped[str] = mapped_column(String(512))
    record_hash: Mapped[str] = mapped_column(String(64))


PG_GUARD_FUNCTION = '''
CREATE OR REPLACE FUNCTION ga_goal_evaluation_append_v297_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $v297$
BEGIN
  RAISE EXCEPTION 'GoalEvaluationAppendCommandV297 records are append-only' USING ERRCODE = '55000';
END;
$v297$;
'''
PG_GUARD_TRIGGER = '''
CREATE TRIGGER tr_goal_evaluation_append_v297_immutable
BEFORE UPDATE OR DELETE ON %(fullname)s
FOR EACH ROW EXECUTE FUNCTION ga_goal_evaluation_append_v297_reject_mutation()
'''
event.listen(GoalEvaluationAppendCommandV297.__table__, 'after_create',
    DDL(PG_GUARD_FUNCTION).execute_if(dialect='postgresql'))
event.listen(GoalEvaluationAppendCommandV297.__table__, 'after_create',
    DDL(PG_GUARD_TRIGGER).execute_if(dialect='postgresql'))
