"""Independent future-input shadow authority; no production rule adoption."""
from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class ThresholdShadowGrant(Row, Scoped, Payload, Base):
    __tablename__ = 'threshold_shadow_grants_v151'
    __table_args__ = (
        CheckConstraint("status IN ('active','paused','ended')"),
        Index('uq_threshold_shadow_live_scope_v151', 'workspace_id', 'scope_key',
              unique=True, postgresql_where=text("status <> 'ended'")),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    evaluation_id: Mapped[str] = mapped_column(ForeignKey('threshold_study_evaluations_v149.id'), index=True)
    scope_key: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20))
    record_hash: Mapped[str] = mapped_column(String(64))


class ThresholdShadowCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'threshold_shadow_commands_v151'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        CheckConstraint("outcome IN ('saved','rejected')"),
        CheckConstraint("operation IN ('create','pause','resume','end')"),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    wire_sha256: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(10))
    command_hash: Mapped[str] = mapped_column(String(64))


class ThresholdShadowConsumption(Row, Scoped, Payload, Base):
    __tablename__ = 'threshold_shadow_consumptions_v151'
    __table_args__ = (
        UniqueConstraint('grant_id', 'observation_id'),
        UniqueConstraint('grant_id', 'source_record_id'),
        UniqueConstraint('job_id'),
        CheckConstraint("status IN ('queued','reserved','completed','failed','stopped')"),
    )
    grant_id: Mapped[str] = mapped_column(ForeignKey('threshold_shadow_grants_v151.id'), index=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey('observations.id'), index=True)
    source_record_id: Mapped[str] = mapped_column(String(200))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(20))
    record_hash: Mapped[str] = mapped_column(String(64))
