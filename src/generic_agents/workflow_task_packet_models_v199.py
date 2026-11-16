"""Immutable local evidence packets and their exact original tool commands."""
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class WorkflowTaskEvidencePacket(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_evidence_packets_v199'
    __table_args__ = (UniqueConstraint('workspace_id', 'invocation_id'),
        UniqueConstraint('workspace_id', 'request_key'), UniqueConstraint('workspace_id', 'evidence_id'),
        CheckConstraint('version = 1'))
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'), index=True)
    request_key: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    context_result_hash: Mapped[str] = mapped_column(String(64))
    provenance_hash: Mapped[str] = mapped_column(String(64))
    simulation_only: Mapped[bool] = mapped_column(Boolean)


class WorkflowTaskEvidencePacketCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_evidence_packet_commands_v199'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_key'),
        UniqueConstraint('workspace_id', 'packet_id'), UniqueConstraint('workspace_id', 'invocation_id'),
        CheckConstraint('version = 1'))
    packet_id: Mapped[str] = mapped_column(ForeignKey('workflow_task_evidence_packets_v199.id'), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'))
    request_key: Mapped[str] = mapped_column(String(200))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
