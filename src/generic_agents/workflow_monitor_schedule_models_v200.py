"""Policy-check cadence; never a hardware sampling-rate configuration."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Payload, Row, Scoped


class WorkflowMonitorScheduleHeadV200(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_monitor_schedule_heads_v200'
    __table_args__ = (UniqueConstraint('workspace_id', 'policy_id'), CheckConstraint('version >= 1'))
    policy_id: Mapped[str] = mapped_column(ForeignKey('monitor_policies.id'), index=True)
    status: Mapped[str] = mapped_column(String(24))
    next_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_hash: Mapped[str] = mapped_column(String(64))


class WorkflowMonitorScheduleInstallationV200(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_monitor_schedule_installations_v200'
    __table_args__ = (UniqueConstraint('workspace_id', 'invocation_id'),
        UniqueConstraint('workspace_id', 'request_key'), CheckConstraint('version = 1'))
    head_id: Mapped[str] = mapped_column(ForeignKey('workflow_monitor_schedule_heads_v200.id'), index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey('monitor_policies.id'), index=True)
    parent_task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'))
    invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    request_key: Mapped[str] = mapped_column(String(512))
    input_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class WorkflowMonitorScheduleCycleV200(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_monitor_schedule_cycles_v200'
    __table_args__ = (UniqueConstraint('workspace_id', 'head_id', 'head_version'),
        CheckConstraint('version = 1'), CheckConstraint('head_version >= 2'))
    head_id: Mapped[str] = mapped_column(ForeignKey('workflow_monitor_schedule_heads_v200.id'), index=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey('workflow_monitor_schedule_installations_v200.id'))
    policy_id: Mapped[str] = mapped_column(ForeignKey('monitor_policies.id'), index=True)
    head_version: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(24))
    record_hash: Mapped[str] = mapped_column(String(64))


class WorkflowMonitorScheduleStopCommandV200(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_monitor_schedule_stop_commands_v200'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'), CheckConstraint('version = 1'))
    policy_id: Mapped[str] = mapped_column(ForeignKey('monitor_policies.id'), index=True)
    head_id: Mapped[str] = mapped_column(ForeignKey('workflow_monitor_schedule_heads_v200.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    body_sha256: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))
