"""Immutable current-context origin and raw command receipts (M1 only)."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .models import Base,Row,Scoped,Payload


class CatalogueCurrentContextBindingV260(Row,Scoped,Payload,Base):
    __tablename__='catalogue_current_context_bindings_v260'
    __table_args__=(UniqueConstraint('workspace_id','context_key'),UniqueConstraint('workspace_id','working_draft_id'),CheckConstraint('version=1'))
    actor_id: Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    source_draft_id: Mapped[str]=mapped_column(ForeignKey('report_business_catalogue_drafts_v211.id'),index=True)
    working_draft_id: Mapped[str]=mapped_column(ForeignKey('report_business_catalogue_drafts_v211.id'))
    context_key: Mapped[str]=mapped_column(String(64))
    fork_command_id: Mapped[str]=mapped_column(String(36))
    record_hash: Mapped[str]=mapped_column(String(64))


class CatalogueCurrentContextCommandV260(Row,Scoped,Payload,Base):
    __tablename__='catalogue_current_context_commands_v260'
    __table_args__=(UniqueConstraint('workspace_id','request_id'),CheckConstraint('version=1'),
        CheckConstraint("operation IN ('fork','confirm')"),CheckConstraint("outcome IN ('accepted','rejected')"))
    actor_id: Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    request_id: Mapped[str]=mapped_column(String(36))
    source_draft_id: Mapped[str]=mapped_column(ForeignKey('report_business_catalogue_drafts_v211.id'),index=True)
    binding_id: Mapped[str|None]=mapped_column(ForeignKey('catalogue_current_context_bindings_v260.id'),nullable=True)
    operation: Mapped[str]=mapped_column(String(12))
    outcome: Mapped[str]=mapped_column(String(12))
    path: Mapped[str]=mapped_column(Text)
    exact_body: Mapped[str]=mapped_column(Text)
    raw_sha256: Mapped[str]=mapped_column(String(64))
    body_hash: Mapped[str]=mapped_column(String(64))
    record_hash: Mapped[str]=mapped_column(String(64))
