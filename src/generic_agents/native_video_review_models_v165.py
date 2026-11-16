"""Independent human video opinions and immutable exact-wire commands."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class NativeVideoReview(Row, Scoped, Payload, Base):
    __tablename__ = 'native_video_reviews_v165'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'instance_id', 'observation_id', 'actor_id'),
        CheckConstraint("status IN ('draft','reviewed')"),
        CheckConstraint('version >= 1'),
    )
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey('observations.id'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('video_runs.id'))
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    receipt_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12))
    record_hash: Mapped[str] = mapped_column(String(64))


class NativeVideoReviewCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'native_video_review_commands_v165'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        UniqueConstraint('workspace_id', 'review_id', 'record_version'),
        CheckConstraint("operation IN ('draft','submit')"),
        CheckConstraint('record_version >= 1'),
    )
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey('observations.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    review_id: Mapped[str] = mapped_column(ForeignKey('native_video_reviews_v165.id'), index=True)
    record_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(10))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    wire_sha256: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
