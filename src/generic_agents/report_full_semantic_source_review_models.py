"""Append-only, per-person source checks; never shared source approval."""
from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Row, Scoped, Payload


class FullSemanticSourceReview(Row, Scoped, Payload, Base):
    __tablename__ = 'full_semantic_source_reviews'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        UniqueConstraint('handoff_id', 'actor_id', 'owner_path', 'version'),
        Index('ix_full_source_review_owner', 'workspace_id', 'actor_id', 'handoff_id', 'owner_path'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    handoff_id: Mapped[str] = mapped_column(ForeignKey('report_configuration_records.id'))
    owner_path: Mapped[str] = mapped_column(String(500))
    request_id: Mapped[str] = mapped_column(String(36))

