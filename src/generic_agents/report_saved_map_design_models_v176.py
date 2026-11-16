"""Independent single-call consumer; original reports and paid Maps stay intact."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class ReportSavedMapDesignV176(Row, Scoped, Payload, Base):
    __tablename__ = 'report_saved_map_designs_v176'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'request_id'),
        UniqueConstraint('workspace_id', 'grant_hash', name='uq_report_saved_map_design_grant_v176'),
        UniqueConstraint('reserved_call_id'),
        CheckConstraint("status IN ('queued','running','response_unknown','review','needs_clarification','failed')"),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    producer_id: Mapped[str] = mapped_column(ForeignKey('setup_report_drafts.id'))
    model_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    grant_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(30))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'), unique=True)
    reserved_call_id: Mapped[str | None] = mapped_column(ForeignKey('cloud_model_calls.id'))


class ReportSavedMapDesignCommandV176(Row, Scoped, Payload, Base):
    __tablename__ = 'report_saved_map_design_commands_v176'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    consumer_id: Mapped[str] = mapped_column(ForeignKey('report_saved_map_designs_v176.id'), index=True)
    operation: Mapped[str] = mapped_column(String(30))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(Text)
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
