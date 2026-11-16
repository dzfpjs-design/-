"""Additive runtime records; configuration materials and Tasks are untouched."""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class RuntimeDocument(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_documents'
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    title: Mapped[str] = mapped_column(String(200))
    latest_version_id: Mapped[str | None] = mapped_column(String(36))


class RuntimeDocumentVersion(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_document_versions'
    __table_args__ = (UniqueConstraint('document_id', 'sequence'),)
    document_id: Mapped[str] = mapped_column(ForeignKey('runtime_documents.id'), index=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    sequence: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(250))
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class RuntimeDocumentParseAttempt(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_document_parse_attempts'
    __table_args__ = (UniqueConstraint('document_version_id', 'ordinal'),)
    document_version_id: Mapped[str] = mapped_column(ForeignKey('runtime_document_versions.id'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default='parsing')


class RuntimeDocumentSearch(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_document_searches'
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    keyword: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default='queued')
