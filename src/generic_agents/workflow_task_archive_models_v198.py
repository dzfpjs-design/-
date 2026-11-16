"""Queryable candidate archives; immutable revisions and original commands."""
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class WorkflowTaskArchive(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_archives_v198'
    __table_args__ = (UniqueConstraint('workspace_id', 'task_id', 'source_node_id'),
        CheckConstraint('version >= 1'), CheckConstraint("status = 'candidate'"))
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    source_node_id: Mapped[str] = mapped_column(String(200))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='candidate')
    record_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    simulation_only: Mapped[bool] = mapped_column(Boolean)
    current_version_hash: Mapped[str] = mapped_column(String(64))


class WorkflowTaskArchiveVersion(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_archive_versions_v198'
    __table_args__ = (UniqueConstraint('workspace_id', 'archive_id', 'version'), CheckConstraint('version >= 1'))
    archive_id: Mapped[str] = mapped_column(ForeignKey('workflow_task_archives_v198.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_key: Mapped[str] = mapped_column(String(200))
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    value_hash: Mapped[str] = mapped_column(String(64))
    version_hash: Mapped[str] = mapped_column(String(64))


class WorkflowTaskArchiveReference(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_archive_references_v198'
    __table_args__ = (UniqueConstraint('workspace_id', 'archive_version_id', 'ordinal'),
        CheckConstraint('ordinal >= 0'))
    archive_version_id: Mapped[str] = mapped_column(ForeignKey('workflow_task_archive_versions_v198.id'), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    source_kind: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(200))
    source_hash: Mapped[str] = mapped_column(String(64))


class WorkflowTaskArchiveCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_archive_commands_v198'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_key'),
        UniqueConstraint('workspace_id', 'archive_version_id'), CheckConstraint('version = 1'))
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    archive_id: Mapped[str] = mapped_column(ForeignKey('workflow_task_archives_v198.id'), index=True)
    archive_version_id: Mapped[str] = mapped_column(ForeignKey('workflow_task_archive_versions_v198.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    invocation_id: Mapped[str | None] = mapped_column(ForeignKey('invocations.id'), index=True)
    operation: Mapped[str] = mapped_column(String(20))
    request_key: Mapped[str] = mapped_column(String(200))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
