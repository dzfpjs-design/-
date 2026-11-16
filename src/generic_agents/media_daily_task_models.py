"""Finite user intents and globally unique business-day Task slots."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class DailyTaskIntent(Row, Scoped, Payload, Base):
    __tablename__ = 'media_daily_task_intents_v226'
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(20), default='enabled')
    record_hash: Mapped[str] = mapped_column(String(64), default='')


class DailyTaskSlot(Row, Scoped, Payload, Base):
    __tablename__ = 'media_daily_task_slots_v226'
    __table_args__ = (UniqueConstraint('workspace_id', 'instance_id', 'business_id', 'entity_id', 'camera_id', 'day'),
        UniqueConstraint('task_id'),)
    intent_id: Mapped[str] = mapped_column(ForeignKey('media_daily_task_intents_v226.id'), index=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    business_id: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(ForeignKey('resource_entities.id'))
    camera_id: Mapped[str] = mapped_column(ForeignKey('media_channels.id'))
    day: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default='waiting_data')
    task_id: Mapped[str | None] = mapped_column(ForeignKey('tasks.id'))
    # Scheduling progress only: never an authorization, source or success pin.
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_hash: Mapped[str] = mapped_column(String(64), default='')
