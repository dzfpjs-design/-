"""Immutable author self-review records; no configuration or execution grant."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class BusinessFlowQualification(Row, Scoped, Payload, Base):
    __tablename__ = 'business_flow_qualifications'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'request_id'),
        CheckConstraint('version = 1'),
        CheckConstraint("method = 'POST'"),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    attempt_id: Mapped[str] = mapped_column(ForeignKey('report_scoped_design_attempts_v181.id'), index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey('business_flow_review_drafts.id'), index=True)
    save_command_id: Mapped[str] = mapped_column(ForeignKey('business_flow_review_commands.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    method: Mapped[str] = mapped_column(String(4))
    path: Mapped[str] = mapped_column(Text)
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))
