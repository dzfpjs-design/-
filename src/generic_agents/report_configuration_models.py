"""Explicit configuration handoff ledger; no tasks, paid state or private-row edits."""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped


class ReportConfigurationRecord(Row, Scoped, Base):
    __tablename__ = 'report_configuration_records'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    report_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'))
    configuration_id: Mapped[str] = mapped_column(ForeignKey('setup_drafts.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey('report_configuration_records.id'))
    data: Mapped[dict] = mapped_column(JSONB)
