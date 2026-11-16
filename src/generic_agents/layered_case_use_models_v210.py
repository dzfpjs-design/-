"""Explicit historical-case references and independent, append-only feedback."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class LayeredCaseUseV210(Row, Scoped, Payload, Base):
    __tablename__ = 'layered_case_uses_v210'
    __table_args__ = (UniqueConstraint('workspace_id', 'creation_request_id'),
        UniqueConstraint('document_version_id'), CheckConstraint('version >= 1'))
    target_task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    source_case_id: Mapped[str] = mapped_column(ForeignKey('layered_cases_v209.id'), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    document_version_id: Mapped[str] = mapped_column(ForeignKey('runtime_document_versions.id'))
    binding_hash: Mapped[str] = mapped_column(String(64))
    creation_request_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20))


class LayeredCaseUseCommandV210(Row, Scoped, Payload, Base):
    __tablename__ = 'layered_case_use_commands_v210'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'), CheckConstraint('version = 1'))
    use_id: Mapped[str] = mapped_column(ForeignKey('layered_case_uses_v210.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(250))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))


class LayeredCaseUseFeedbackV210(Row, Scoped, Payload, Base):
    __tablename__ = 'layered_case_use_feedback_v210'
    __table_args__ = (UniqueConstraint('use_id', 'acceptance_request_id'), CheckConstraint('version = 1'))
    use_id: Mapped[str] = mapped_column(ForeignKey('layered_case_uses_v210.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    acceptance_request_id: Mapped[str] = mapped_column(String(36))
    consumption_fingerprint: Mapped[str] = mapped_column(String(64))
    feedback_hash: Mapped[str] = mapped_column(String(64))
