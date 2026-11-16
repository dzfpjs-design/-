"""Additive typed study records; existing learning and feedback tables stay closed."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class ThresholdStudyCandidate(Row, Scoped, Payload, Base):
    __tablename__ = 'threshold_study_candidates_v149'
    __table_args__ = (CheckConstraint("status IN ('draft','frozen')"),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    baseline_capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'), index=True)
    candidate_capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    status: Mapped[str] = mapped_column(String(20))
    record_hash: Mapped[str] = mapped_column(String(64))


class ThresholdStudyEvaluation(Row, Scoped, Payload, Base):
    __tablename__ = 'threshold_study_evaluations_v149'
    __table_args__ = (CheckConstraint("status IN ('draft','frozen','queued','running','completed','failed','cancelled')"),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    candidate_id: Mapped[str] = mapped_column(ForeignKey('threshold_study_candidates_v149.id'), index=True)
    status: Mapped[str] = mapped_column(String(20))
    record_hash: Mapped[str] = mapped_column(String(64))


class ThresholdStudyCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'threshold_study_commands_v149'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
                      CheckConstraint("outcome IN ('saved','rejected')"),
                      CheckConstraint("target_kind IN ('candidate','evaluation')"))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    wire_sha256: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(50))
    target_kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(10))
    command_hash: Mapped[str] = mapped_column(String(64))
