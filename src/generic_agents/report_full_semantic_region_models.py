"""Independent private position work and explicit, note-free public proofs."""
from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class FullSemanticRegionSession(Row, Scoped, Payload, Base):
    __tablename__ = 'full_semantic_region_sessions'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    handoff_id: Mapped[str] = mapped_column(ForeignKey('report_configuration_records.id'))
    request_id: Mapped[str] = mapped_column(String(36))


class FullSemanticRegionEntry(Row, Scoped, Payload, Base):
    __tablename__ = 'full_semantic_region_entries'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        UniqueConstraint('session_id', 'ref_hash', 'version'),
        Index('ix_full_region_ref', 'workspace_id', 'actor_id', 'session_id', 'ref_hash'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    handoff_id: Mapped[str] = mapped_column(ForeignKey('report_configuration_records.id'))
    session_id: Mapped[str] = mapped_column(ForeignKey('full_semantic_region_sessions.id'))
    ref_hash: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(36))


class FullSemanticRegionProof(Row, Scoped, Payload, Base):
    __tablename__ = 'full_semantic_region_proofs'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    handoff_id: Mapped[str] = mapped_column(ForeignKey('report_configuration_records.id'))
    session_id: Mapped[str] = mapped_column(ForeignKey('full_semantic_region_sessions.id'))
    request_id: Mapped[str] = mapped_column(String(36))
