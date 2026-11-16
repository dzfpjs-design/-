"""Independent human quality review; approval is not a policy deployment."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class ThresholdQualityReview(Row, Scoped, Payload, Base):
    __tablename__ = 'quality_reviews_v152'
    __table_args__ = (CheckConstraint("status IN ('draft','approved','rejected','revoked')"),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    evaluation_id: Mapped[str] = mapped_column(ForeignKey('threshold_study_evaluations_v149.id'), index=True)
    shadow_id: Mapped[str] = mapped_column(ForeignKey('threshold_shadow_grants_v151.id'))
    status: Mapped[str] = mapped_column(String(20))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    approval_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64))


class ThresholdQualityCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'quality_review_commands_v152'
    __table_args__ = (UniqueConstraint('workspace_id','actor_id','request_id'),
                      CheckConstraint("outcome IN ('saved','rejected')"))
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
