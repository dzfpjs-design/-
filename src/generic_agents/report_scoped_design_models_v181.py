"""Independent scoped human plans and reserved single-call attempt tables.

No original report, Map, failed answer or V166 human revision is overwritten.
Attempt execution and its independent command/audit checks are central seams;
declaring these tables confers no call, budget, source or adoption permission.
"""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class ReportScopedDesignPlanV181(Row, Scoped, Payload, Base):
    __tablename__ = 'report_scoped_design_plans_v181'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        CheckConstraint("status IN ('draft','confirmed')"),
        CheckConstraint('version >= 1'),
        CheckConstraint('revision >= 1'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    producer_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    record_hash: Mapped[str] = mapped_column(String(64))


class ReportScopedDesignPlanCommandV181(Row, Scoped, Payload, Base):
    __tablename__ = 'report_scoped_design_plan_commands_v181'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        UniqueConstraint('workspace_id', 'plan_id', 'plan_version'),
        CheckConstraint("operation IN ('create','save','confirm')"),
        CheckConstraint('plan_version >= 1'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    producer_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey('report_scoped_design_plans_v181.id'), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(12))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    exact_body: Mapped[str] = mapped_column(Text)
    wire_sha256: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))


class ReportScopedDesignAttemptV181(Row, Scoped, Payload, Base):
    __tablename__ = 'report_scoped_design_attempts_v181'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        UniqueConstraint('workspace_id', 'grant_hash', name='uq_report_scoped_design_grant_v181'),
        UniqueConstraint('job_id'),
        UniqueConstraint('reserved_call_id'),
        CheckConstraint("status IN ('queued','running','response_unknown','review','needs_clarification','reviewed','failed')"),
        CheckConstraint('version >= 1'),
        CheckConstraint('plan_version >= 1'),
        CheckConstraint('revision >= 1'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    plan_id: Mapped[str] = mapped_column(ForeignKey('report_scoped_design_plans_v181.id'), index=True)
    producer_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'), index=True)
    scope_id: Mapped[str] = mapped_column(String(128))
    plan_version: Mapped[int] = mapped_column(Integer)
    plan_hash: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    grant_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    reserved_call_id: Mapped[str | None] = mapped_column(ForeignKey('cloud_model_calls.id'))
    status: Mapped[str] = mapped_column(String(30))


class ReportScopedDesignAttemptCommandV181(Row, Scoped, Payload, Base):
    __tablename__ = 'report_scoped_design_attempt_commands_v181'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        CheckConstraint('attempt_version >= 1'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    plan_id: Mapped[str] = mapped_column(ForeignKey('report_scoped_design_plans_v181.id'), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey('report_scoped_design_attempts_v181.id'), index=True)
    attempt_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(30))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    exact_body: Mapped[str] = mapped_column(Text)
    wire_sha256: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
