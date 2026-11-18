"""Immutable, private machine candidates; never a report-stage transition."""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class FullProvisionalCandidate(Row, Scoped, Payload, Base):
    __tablename__ = 'report_full_provisional_candidates'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        UniqueConstraint('workspace_id', 'actor_id', 'draft_id', 'stage', 'raw_hash', 'algorithm_version'),
        CheckConstraint('draft_revision > 0', name='ck_full_provisional_revision_positive'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    draft_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'))
    stage: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str] = mapped_column(String(36))
    draft_revision: Mapped[int] = mapped_column(Integer)
    raw_hash: Mapped[str] = mapped_column(String(64))
    algorithm_version: Mapped[str] = mapped_column(String(80))
