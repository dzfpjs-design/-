"""Additive records for explicit processing of saved imagery, never acquisition."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class SavedImageryReassessment(Row, Scoped, Payload, Base):
    __tablename__ = 'saved_imagery_reassessments_v163'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'assessment_id', 'entity_id'),
        UniqueConstraint('workspace_id', 'task_id'),
        UniqueConstraint('workspace_id', 'reassessment_id'),
        CheckConstraint("status IN ('draft','task_created','reassessment_requested','stopped')"),
    )
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey('evidence_assessments.id'))
    entity_id: Mapped[str] = mapped_column(ForeignKey('resource_entities.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30))
    task_id: Mapped[str | None] = mapped_column(ForeignKey('tasks.id'), nullable=True)
    reassessment_id: Mapped[str | None] = mapped_column(ForeignKey('evidence_assessments.id'), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64))


class SavedImageryCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'saved_imagery_commands_v163'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        CheckConstraint("outcome = 'saved'"),
        CheckConstraint("operation IN ('create','update','create_task','reassess','stop')"),
    )
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(20))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    wire_sha256: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(ForeignKey('saved_imagery_reassessments_v163.id'), index=True)
    outcome: Mapped[str] = mapped_column(String(10))
    command_hash: Mapped[str] = mapped_column(String(64))


class SavedImageryAudit(Row, Scoped, Payload, Base):
    __tablename__ = 'saved_imagery_state_audits_v163'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'plan_id', 'record_version'),
        UniqueConstraint('command_id'),
        CheckConstraint('record_version >= 1'),
    )
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey('saved_imagery_reassessments_v163.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    operation: Mapped[str] = mapped_column(String(20))
    record_version: Mapped[int] = mapped_column(Integer)
    record_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    command_id: Mapped[str] = mapped_column(ForeignKey('saved_imagery_commands_v163.id'))
