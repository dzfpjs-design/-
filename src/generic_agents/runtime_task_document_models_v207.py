"""Immutable Task document selections and independently anchored local packets."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class RuntimeTaskDocumentSourceSet(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_task_document_source_sets_v207'
    __table_args__ = (UniqueConstraint('task_id', 'input_key', 'ordinal'), CheckConstraint('version = 1'))
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    input_key: Mapped[str] = mapped_column(String(200))
    ordinal: Mapped[int] = mapped_column(Integer)
    prior_set_id: Mapped[str | None] = mapped_column(ForeignKey('runtime_task_document_source_sets_v207.id'))
    source_set_hash: Mapped[str] = mapped_column(String(64))


class RuntimeTaskDocumentSourceCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_task_document_source_commands_v207'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'), UniqueConstraint('source_set_id'), CheckConstraint('version = 1'))
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    source_set_id: Mapped[str] = mapped_column(ForeignKey('runtime_task_document_source_sets_v207.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    path: Mapped[str] = mapped_column(String(250))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))


class WorkflowDocumentPacketV207(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_document_packets_v207'
    __table_args__ = (UniqueConstraint('workspace_id', 'invocation_id'), UniqueConstraint('workspace_id', 'request_key'),
        UniqueConstraint('workspace_id', 'evidence_id'), CheckConstraint('version = 1'))
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    source_set_id: Mapped[str] = mapped_column(ForeignKey('runtime_task_document_source_sets_v207.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'), index=True)
    request_key: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))


class WorkflowDocumentPacketCommandV207(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_document_packet_commands_v207'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_key'), UniqueConstraint('packet_id'),
        UniqueConstraint('invocation_id'), CheckConstraint('version = 1'))
    packet_id: Mapped[str] = mapped_column(ForeignKey('workflow_document_packets_v207.id'))
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_key: Mapped[str] = mapped_column(String(200))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
