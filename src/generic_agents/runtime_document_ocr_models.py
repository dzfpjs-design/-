"""Runtime OCR owns its records; native/configuration document rows are sources."""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class RuntimeDocumentOcrRun(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_document_ocr_runs'
    document_version_id: Mapped[str] = mapped_column(ForeignKey('runtime_document_versions.id'), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('runtime_documents.id'))
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    upload_release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    execution_release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class RuntimeDocumentOcrPage(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_document_ocr_pages'
    __table_args__ = (UniqueConstraint('run_id', 'page_number'),)
    run_id: Mapped[str] = mapped_column(ForeignKey('runtime_document_ocr_runs.id'), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default='queued')


class RuntimeDocumentOcrResult(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_document_ocr_results'
    __table_args__ = (UniqueConstraint('page_id', 'ordinal'),)
    page_id: Mapped[str] = mapped_column(ForeignKey('runtime_document_ocr_pages.id'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default='processing')


class RuntimeDocumentOcrSearch(Row, Scoped, Payload, Base):
    __tablename__ = 'runtime_document_ocr_searches'
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    keyword: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default='queued')
