"""Private incomplete business-flow drafts, separate from original attempts.

These rows do not confirm, adopt, publish, execute or overwrite a model answer.
Independent commands and Audit anchors make original save recovery durable.
"""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class BusinessFlowReviewDraft(Row, Scoped, Payload, Base):
    __tablename__ = 'business_flow_review_drafts'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'actor_id', 'attempt_id'),
        CheckConstraint('version >= 1'),
        CheckConstraint("status = 'incomplete'"),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    attempt_id: Mapped[str] = mapped_column(ForeignKey('report_scoped_design_attempts_v181.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    source_binding_hash: Mapped[str] = mapped_column(String(64))
    baseline_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))


class BusinessFlowReviewCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'business_flow_review_commands'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'request_id'),
        UniqueConstraint('workspace_id', 'draft_id', 'draft_version'),
        CheckConstraint('draft_version >= 1'),
        CheckConstraint("operation = 'save'"),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    draft_id: Mapped[str] = mapped_column(ForeignKey('business_flow_review_drafts.id'), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey('report_scoped_design_attempts_v181.id'), index=True)
    draft_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(12))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
