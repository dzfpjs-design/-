"""Continuous grants; occurrences use the existing typed, leased Job ledger."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class BusinessAutomationSubscription(Row, Scoped, Payload, Base):
    __tablename__ = 'business_automation_subscriptions'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'request_id'),
        CheckConstraint("status IN ('active','paused','blocked','superseded')"),
    )
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    business_id: Mapped[str] = mapped_column(String(100))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    body_sha256: Mapped[str] = mapped_column(String(64))
    authorization_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default='active')
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey('business_automation_subscriptions.id'))
    # Operational state, never a dispatch grant or part of command CAS/hash.
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
