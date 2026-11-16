"""Independent full human drafts and exact original command histories only."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Payload, Row, Scoped


class ReportBusinessCatalogueDraftV211(Row, Scoped, Payload, Base):
    __tablename__ = 'report_business_catalogue_drafts_v211'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'), CheckConstraint('version >= 1'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    consumer_id: Mapped[str] = mapped_column(ForeignKey('report_saved_map_designs_v176.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    origin_hash: Mapped[str] = mapped_column(String(64))
    document_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ReportBusinessCatalogueCommandV211(Row, Scoped, Payload, Base):
    __tablename__ = 'report_business_catalogue_commands_v211'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),
        UniqueConstraint('workspace_id', 'draft_id', 'draft_version'),
        CheckConstraint("operation IN ('create','save','confirm')"), CheckConstraint('draft_version >= 1'),
        CheckConstraint('version = 1'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    draft_id: Mapped[str] = mapped_column(ForeignKey('report_business_catalogue_drafts_v211.id'), index=True)
    draft_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(12))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    exact_body: Mapped[str] = mapped_column(Text)
    wire_sha256: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
