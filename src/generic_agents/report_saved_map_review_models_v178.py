"""One private source-selection head for the ORIGINAL report's V166 draft."""
from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class ReportSavedMapReviewSourceV178(Row, Scoped, Payload, Base):
    __tablename__ = 'report_saved_map_review_sources_v178'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'report_id', 'actor_id'),
        CheckConstraint('version >= 1'),
    )
    report_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'), index=True)
    consumer_id: Mapped[str] = mapped_column(ForeignKey('report_saved_map_designs_v176.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    record_hash: Mapped[str] = mapped_column(String(64))
