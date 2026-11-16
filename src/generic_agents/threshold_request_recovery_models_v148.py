"""Additive, immutable decisions for an exact original feedback request."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class ThresholdRequestRetirement(Row, Scoped, Payload, Base):
    __tablename__ = 'threshold_request_retirements_v148'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        UniqueConstraint('workspace_id', 'actor_id', 'original_request_id'),
        CheckConstraint("outcome IN ('saved','retired')"),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    original_request_id: Mapped[str] = mapped_column(String(36))
    original_method: Mapped[str] = mapped_column(String(4))
    original_path: Mapped[str] = mapped_column(Text)
    original_sha256: Mapped[str] = mapped_column(String(64))
    wire_sha256: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(8))
    command_hash: Mapped[str] = mapped_column(String(64))
