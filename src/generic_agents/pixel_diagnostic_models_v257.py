"""Immutable freeze, independent result and exact request receipts."""
from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class PixelDiagnosticStudyV257(Row, Scoped, Payload, Base):
    __tablename__ = 'pixel_diagnostic_studies_v257'
    __table_args__ = (UniqueConstraint('workspace_id','actor_id','request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('pixel_change_runs_v249.id'), index=True)
    annotation_id: Mapped[str] = mapped_column(ForeignKey('pixel_annotations_v256.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    record_hash: Mapped[str] = mapped_column(String(64))


class PixelDiagnosticEvaluationV257(Row, Scoped, Payload, Base):
    __tablename__ = 'pixel_diagnostic_evaluations_v257'
    __table_args__ = (UniqueConstraint('study_id'), UniqueConstraint('workspace_id','actor_id','request_id'))
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    run_id: Mapped[str] = mapped_column(ForeignKey('pixel_change_runs_v249.id'))
    annotation_id: Mapped[str] = mapped_column(ForeignKey('pixel_annotations_v256.id'))
    study_id: Mapped[str] = mapped_column(ForeignKey('pixel_diagnostic_studies_v257.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    record_hash: Mapped[str] = mapped_column(String(64))


class PixelDiagnosticCommandV257(Row, Scoped, Payload, Base):
    __tablename__ = 'pixel_diagnostic_commands_v257'
    __table_args__ = (UniqueConstraint('workspace_id','actor_id','request_id'),
        CheckConstraint("operation IN ('freeze','evaluate')"), CheckConstraint("outcome IN ('saved','rejected')"))
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    run_id: Mapped[str] = mapped_column(ForeignKey('pixel_change_runs_v249.id'))
    annotation_id: Mapped[str] = mapped_column(ForeignKey('pixel_annotations_v256.id'), index=True)
    study_id: Mapped[str | None] = mapped_column(ForeignKey('pixel_diagnostic_studies_v257.id'), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    raw_sha256: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(10))
    outcome: Mapped[str] = mapped_column(String(10))
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64))
