"""Private unfinished recursive configuration; no review/adoption authority.

One slot belongs to one workspace/actor/immutable qualification. Commands retain
each exact UTF-8 request and original response. Root owns additive migration.
"""
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class BusinessFlowNestedConfigurationDraft(Row, Scoped, Payload, Base):
    __tablename__ = 'business_flow_nested_configuration_drafts'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'qualification_id'),
        CheckConstraint('version >= 1'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    qualification_id: Mapped[str] = mapped_column(ForeignKey('business_flow_qualifications.id'), index=True)
    qualification_hash: Mapped[str] = mapped_column(String(64))
    source_plan_hash: Mapped[str] = mapped_column(String(64))
    scope_binding_hash: Mapped[str] = mapped_column(String(64))
    review_mode: Mapped[str] = mapped_column(String(32))
    simulation_only: Mapped[bool] = mapped_column(Boolean)
    value_schema: Mapped[str] = mapped_column(String(100))
    flow_count: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    value_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class BusinessFlowNestedConfigurationCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'business_flow_nested_configuration_commands'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),
        UniqueConstraint('workspace_id', 'draft_id', 'draft_version'), CheckConstraint('version = 1'),
        CheckConstraint('draft_version >= 1'), CheckConstraint("operation = 'save'"))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    draft_id: Mapped[str] = mapped_column(ForeignKey('business_flow_nested_configuration_drafts.id'), index=True)
    qualification_id: Mapped[str] = mapped_column(ForeignKey('business_flow_qualifications.id'), index=True)
    qualification_hash: Mapped[str] = mapped_column(String(64))
    source_plan_hash: Mapped[str] = mapped_column(String(64))
    scope_binding_hash: Mapped[str] = mapped_column(String(64))
    draft_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(12))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
