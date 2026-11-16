"""Business-data ownership, immutable previews and mutable import checkpoints."""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class BusinessDataSource(Row, Scoped, Payload, Base):
    __tablename__ = 'business_data_sources'
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default='active')
    capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))


class BusinessDataRead(Row, Scoped, Payload, Base):
    __tablename__ = 'business_data_reads'
    source_id: Mapped[str] = mapped_column(ForeignKey('business_data_sources.id'), index=True)
    capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    evidence_id: Mapped[str | None] = mapped_column(ForeignKey('evidence_objects.id'))


class BusinessDataBinding(Row, Scoped, Payload, Base):
    __tablename__ = 'business_data_bindings'
    source_id: Mapped[str] = mapped_column(ForeignKey('business_data_sources.id'), index=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    object_type: Mapped[str] = mapped_column(String(100))


class BusinessDataImport(Row, Scoped, Payload, Base):
    __tablename__ = 'business_data_imports'
    source_id: Mapped[str] = mapped_column(ForeignKey('business_data_sources.id'), index=True)
    binding_id: Mapped[str] = mapped_column(ForeignKey('business_data_bindings.id'))
    read_id: Mapped[str] = mapped_column(ForeignKey('business_data_reads.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class BusinessDataImportRow(Row, Scoped, Payload, Base):
    __tablename__ = 'business_data_import_rows'
    __table_args__ = (UniqueConstraint('import_id', 'ordinal'),)
    import_id: Mapped[str] = mapped_column(ForeignKey('business_data_imports.id'), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    # Immutable proposal has its own audit; progress never mutates this column.
    preview: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30))


class BusinessDataRecordLink(Row, Scoped, Payload, Base):
    __tablename__ = 'business_data_record_links'
    __table_args__ = (UniqueConstraint('workspace_id', 'source_id', 'instance_id', 'object_type', 'external_key'),
        UniqueConstraint('workspace_id', 'entity_id'))
    source_id: Mapped[str] = mapped_column(ForeignKey('business_data_sources.id'), index=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    object_type: Mapped[str] = mapped_column(String(100))
    external_key: Mapped[str] = mapped_column(String(200))
    entity_id: Mapped[str] = mapped_column(ForeignKey('resource_entities.id'))
