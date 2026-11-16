"""Durable human work orders; these are not executable workflow Tasks."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class WorkflowTaskDispatchBatchV198(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_dispatch_batches_v198'
    __table_args__ = (UniqueConstraint('workspace_id', 'invocation_id'), CheckConstraint('version = 1'))
    parent_task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    record_hash: Mapped[str] = mapped_column(String(64))


class WorkflowTaskWorkOrderV198(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_work_orders_v198'
    __table_args__ = (UniqueConstraint('workspace_id', 'batch_id', 'ordinal'),
        CheckConstraint('version >= 1'), CheckConstraint('ordinal >= 0 AND ordinal < 20'))
    batch_id: Mapped[str] = mapped_column(ForeignKey('workflow_task_dispatch_batches_v198.id'), index=True)
    parent_task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    assignee_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    record_hash: Mapped[str] = mapped_column(String(64))


class WorkflowTaskWorkOrderCommandV198(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_work_order_commands_v198'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),
        UniqueConstraint('workspace_id', 'item_id', 'item_version'),
        CheckConstraint('version = 1'), CheckConstraint('item_version >= 2'))
    item_id: Mapped[str] = mapped_column(ForeignKey('workflow_task_work_orders_v198.id'), index=True)
    parent_task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    item_version: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(16))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))
