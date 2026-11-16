"""Owner-private authoring drafts; no shared Memory or business facts."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped, now


class LocalProposalReviewDraftV261(Row, Scoped, Payload, Base):
    __tablename__ = 'local_proposal_review_drafts_v261'
    __table_args__ = (UniqueConstraint('workspace_id', 'owner_id', 'open_request_id'),
        CheckConstraint('source_version >= 1'),
        CheckConstraint("status IN ('editing','submitted')"))

    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('setup_analyses.id'), index=True)
    source_version: Mapped[int] = mapped_column(Integer)
    raw_hash: Mapped[str] = mapped_column(String(64))
    open_request_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default='editing')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
