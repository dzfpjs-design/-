"""Human reviewed cases, separate from source Task/Memory and learning release."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class LayeredCaseV209(Row, Scoped, Payload, Base):
    __tablename__ = 'layered_cases_v209'
    __table_args__ = (CheckConstraint('version >= 1'),)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey('memory_items.id'))
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    head_revision_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30))
    creation_request_id: Mapped[str] = mapped_column(String(36))


class LayeredCaseRevisionV209(Row, Scoped, Payload, Base):
    __tablename__ = 'layered_case_revisions_v209'
    __table_args__ = (UniqueConstraint('case_id', 'ordinal'), CheckConstraint('version = 1'))
    case_id: Mapped[str] = mapped_column(ForeignKey('layered_cases_v209.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    ordinal: Mapped[int]
    parent_revision_id: Mapped[str | None] = mapped_column(ForeignKey('layered_case_revisions_v209.id'), nullable=True)
    parent_revision_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision_hash: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(36))


class LayeredCaseCommandV209(Row, Scoped, Payload, Base):
    __tablename__ = 'layered_case_commands_v209'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'), CheckConstraint('version = 1'))
    case_id: Mapped[str] = mapped_column(ForeignKey('layered_cases_v209.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    path: Mapped[str] = mapped_column(String(250))
    operation: Mapped[str] = mapped_column(String(20))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
