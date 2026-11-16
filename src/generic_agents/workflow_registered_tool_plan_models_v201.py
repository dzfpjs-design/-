"""Immutable approved tool plan and independently audited per-item outbox."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Payload, Row, Scoped


class WorkflowRegisteredToolPlanV201(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_registered_tool_plans_v201'
    __table_args__ = (UniqueConstraint('workspace_id', 'parent_invocation_id'), CheckConstraint('version = 1'))
    parent_invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    request_key: Mapped[str] = mapped_column(String(512))
    input_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class WorkflowRegisteredToolPlanItemV201(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_registered_tool_plan_items_v201'
    __table_args__ = (UniqueConstraint('workspace_id', 'plan_id', 'ordinal'),
        UniqueConstraint('workspace_id', 'request_key'), CheckConstraint('version >= 1'))
    plan_id: Mapped[str] = mapped_column(ForeignKey('workflow_registered_tool_plans_v201.id'), index=True)
    parent_invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    invocation_id: Mapped[str | None] = mapped_column(ForeignKey('invocations.id'))
    request_key: Mapped[str] = mapped_column(String(512))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    record_hash: Mapped[str] = mapped_column(String(64))
