"""Append-only private source requirement checks; independent of draft JSON."""
from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class SetupRequirementCheck(Row, Scoped, Payload, Base):
    __tablename__ = 'setup_requirement_checks'
    __table_args__ = (
        UniqueConstraint('draft_id', 'actor_id', 'check_id', 'item_version'),
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        Index('ix_requirement_check_latest', 'workspace_id', 'actor_id', 'draft_id', 'check_id', 'item_version'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    draft_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'))
    check_id: Mapped[str] = mapped_column(String(36))
    item_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    command_hash: Mapped[str] = mapped_column(String(64))
