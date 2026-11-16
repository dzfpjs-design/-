"""Private object-semantic revisions, independent of the original report."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class ReportObjectRevision(Row, Scoped, Payload, Base):
    __tablename__ = 'report_object_revisions_v166'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'report_id', 'actor_id'),
        CheckConstraint("status IN ('draft','confirmed')"),
        CheckConstraint('version >= 1'),
    )
    report_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(12))
    basis_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ReportObjectRevisionCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'report_object_revision_commands_v166'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        UniqueConstraint('workspace_id', 'revision_id', 'record_version'),
        CheckConstraint("operation IN ('save','confirm')"),
        CheckConstraint('record_version >= 1'),
    )
    report_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    revision_id: Mapped[str] = mapped_column(ForeignKey('report_object_revisions_v166.id'), index=True)
    record_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(8))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    wire_sha256: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
