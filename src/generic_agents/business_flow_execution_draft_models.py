"""Private unfinished execution configuration, never adoption authority.

Root owns model registration/migrations.  The immutable qualification hash is
a scope pin; the same qualification/flow cannot gain a second mutable slot by
changing that hash.  Commands and their independent Audits remain append-only.
"""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class BusinessFlowExecutionDraft(Row, Scoped, Payload, Base):
    __tablename__ = 'business_flow_execution_drafts'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'qualification_id', 'flow_id'),
        CheckConstraint('version >= 1'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    qualification_id: Mapped[str] = mapped_column(ForeignKey('business_flow_qualifications.id'), index=True)
    qualification_hash: Mapped[str] = mapped_column(String(64))
    flow_id: Mapped[str] = mapped_column(String(48))
    request_id: Mapped[str] = mapped_column(String(36))
    value_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class BusinessFlowExecutionDraftCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'business_flow_execution_draft_commands'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'request_id'),
        UniqueConstraint('workspace_id', 'draft_id', 'draft_version'),
        CheckConstraint('version = 1'),
        CheckConstraint('draft_version >= 1'),
        CheckConstraint("operation = 'save'"),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    draft_id: Mapped[str] = mapped_column(ForeignKey('business_flow_execution_drafts.id'), index=True)
    qualification_id: Mapped[str] = mapped_column(ForeignKey('business_flow_qualifications.id'), index=True)
    qualification_hash: Mapped[str] = mapped_column(String(64))
    flow_id: Mapped[str] = mapped_column(String(48))
    draft_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(12))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
