"""Additive durable feedback and exact-command records for the V147 slice."""
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class FeedbackRevision(Row, Scoped, Payload, Base):
    __tablename__ = 'threshold_feedback_revisions_v147'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'root_id', 'revision'),
        UniqueConstraint('previous_id'),
        CheckConstraint('revision >= 1'),
        CheckConstraint("status IN ('draft','submitted','cancelled')"),
        Index('uq_threshold_feedback_business_root_v147', 'workspace_id', 'observation_id',
              'baseline_capability_id', 'rule_id', unique=True, postgresql_where=text('revision = 1')),
    )
    root_id: Mapped[str] = mapped_column(ForeignKey('threshold_feedback_revisions_v147.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey('threshold_feedback_revisions_v147.id'), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    observation_id: Mapped[str] = mapped_column(ForeignKey('observations.id'), index=True)
    baseline_capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'), index=True)
    rule_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default='draft')


class ThresholdCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'threshold_commands_v147'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    wire_sha256: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
    feedback_id: Mapped[str | None] = mapped_column(ForeignKey('threshold_feedback_revisions_v147.id'), nullable=True, index=True)
