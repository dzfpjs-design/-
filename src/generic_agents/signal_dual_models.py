"""The additive 105th table: two durable human handoffs, never a DAG or Job."""
from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Row, Scoped, Payload


class SignalDualHandoff(Row, Scoped, Payload, Base):
    __tablename__ = 'signal_dual_handoffs'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'instance_id', 'route_id', 'source_identity_hash'),
        UniqueConstraint('response_task_id'), UniqueConstraint('verification_task_id'),
        CheckConstraint('response_task_id IS NULL OR verification_task_id IS NULL OR response_task_id <> verification_task_id'),
    )
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    signal_id: Mapped[str] = mapped_column(ForeignKey('perception_signals.id'), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey('outbox_events.id'))
    observation_id: Mapped[str] = mapped_column(ForeignKey('observations.id'))
    route_id: Mapped[str] = mapped_column(String(36))
    source_identity_hash: Mapped[str] = mapped_column(String(64))
    response_task_id: Mapped[str | None] = mapped_column(ForeignKey('tasks.id'))
    verification_task_id: Mapped[str | None] = mapped_column(ForeignKey('tasks.id'))
