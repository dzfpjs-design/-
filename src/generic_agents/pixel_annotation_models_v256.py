"""Immutable, evidence-bound human annotations; never a model adoption."""
from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Row, Scoped, Payload


class PixelAnnotationV256(Row, Scoped, Payload, Base):
    __tablename__ = 'pixel_annotations_v256'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('pixel_change_runs_v249.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(100))
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey('pixel_annotations_v256.id'), nullable=True)
    raw_sha256: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class PixelAnnotationCommandV256(Row, Scoped, Payload, Base):
    __tablename__ = 'pixel_annotation_commands_v256'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
                      CheckConstraint("outcome IN ('saved','rejected')"))
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('pixel_change_runs_v249.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    raw_sha256: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(12))
    annotation_id: Mapped[str | None] = mapped_column(ForeignKey('pixel_annotations_v256.id'), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64))
