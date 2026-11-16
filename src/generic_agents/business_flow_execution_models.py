"""Immutable explicit flow adoption, separate from failed model answers."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Payload, Row, Scoped


class BusinessFlowExecutionAdoption(Row, Scoped, Payload, Base):
    __tablename__ = 'business_flow_execution_adoptions'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),
        UniqueConstraint('configuration_id', 'accepted_revision'), CheckConstraint('version = 1'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    qualification_id: Mapped[str] = mapped_column(ForeignKey('business_flow_qualifications.id'), index=True)
    configuration_id: Mapped[str] = mapped_column(ForeignKey('setup_drafts.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    business_name: Mapped[str] = mapped_column(String(200))
    accepted_revision: Mapped[int]
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))
