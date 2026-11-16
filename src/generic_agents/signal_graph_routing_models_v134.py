"""One durable source-family reservation for explicitly authorized graph work."""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class SignalGraphDelivery(Row, Scoped, Payload, Base):
    __tablename__ = 'signal_graph_deliveries_v134'
    __table_args__ = (UniqueConstraint('workspace_id', 'instance_id', 'source_identity_hash'),
        UniqueConstraint('task_id'), UniqueConstraint('workspace_id', 'signal_id'))
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    signal_id: Mapped[str] = mapped_column(ForeignKey('perception_signals.id'), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey('outbox_events.id'))
    observation_id: Mapped[str] = mapped_column(ForeignKey('observations.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    route_id: Mapped[str] = mapped_column(String(36))
    source_identity_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default='reserved')
    task_id: Mapped[str | None] = mapped_column(ForeignKey('tasks.id'))
