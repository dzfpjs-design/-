"""Typed source registration, frozen intents and serialized admission receipts.

These records grant no graph subscription or wait execution authority. The
workspace stream serializes admission decisions, never PostgreSQL commit time.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Row, Scoped, Payload


class GraphEventProducer(Row, Scoped, Payload, Base):
    __tablename__ = 'graph_event_producers'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    registered_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    command_hash: Mapped[str] = mapped_column(String(64))
    contract_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default='active')


class GraphEventStream(Row, Scoped, Base):
    __tablename__ = 'graph_event_streams'
    __table_args__ = (UniqueConstraint('workspace_id'),)
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0)


class GraphEventIntent(Row, Scoped, Payload, Base):
    __tablename__ = 'graph_event_intents'
    __table_args__ = (UniqueConstraint('workspace_id', 'producer_id', 'source_key'),)
    producer_id: Mapped[str] = mapped_column(ForeignKey('graph_event_producers.id'))
    source_event_id: Mapped[str] = mapped_column(ForeignKey('outbox_events.id'))
    observation_id: Mapped[str] = mapped_column(ForeignKey('observations.id'))
    source_key: Mapped[str] = mapped_column(String(200))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='pending')
    # Name the cycle so metadata can create/drop both actual FK constraints.
    receipt_id: Mapped[str | None] = mapped_column(ForeignKey(
        'graph_event_admissions.id', use_alter=True,
        name='fk_graph_event_intent_admission'), nullable=True)


class GraphEventAdmission(Row, Scoped, Payload, Base):
    __tablename__ = 'graph_event_admissions'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'sequence'),
        UniqueConstraint('workspace_id', 'producer_id', 'source_key'),
        UniqueConstraint('intent_id'),
    )
    producer_id: Mapped[str] = mapped_column(ForeignKey('graph_event_producers.id'))
    intent_id: Mapped[str] = mapped_column(ForeignKey('graph_event_intents.id'))
    source_key: Mapped[str] = mapped_column(String(200))
    sequence: Mapped[int] = mapped_column(BigInteger)
    admitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
