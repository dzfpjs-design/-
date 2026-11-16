"""Append-only executor revisions; legacy adoption rows remain immutable roots."""
from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Row, Scoped, Payload


class FullSemanticRuntimeRevision(Row, Scoped, Payload, Base):
    __tablename__ = 'full_semantic_runtime_revisions'
    __table_args__ = (
        UniqueConstraint('request_id'),
        UniqueConstraint('parent_id'),
        Index('ix_full_runtime_revision_root', 'workspace_id', 'root_id'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    target_actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    handoff_id: Mapped[str] = mapped_column(ForeignKey('report_configuration_records.id'))
    proof_id: Mapped[str] = mapped_column(ForeignKey('full_semantic_region_proofs.id'))
    configuration_id: Mapped[str] = mapped_column(ForeignKey('setup_drafts.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    business_name: Mapped[str] = mapped_column(String(100))
    # A parent may be the legacy root or another row in this table. Its actual
    # type, scope and uninterrupted chain are checked against both ledgers.
    parent_id: Mapped[str] = mapped_column(String(36))
    root_id: Mapped[str] = mapped_column(ForeignKey('full_semantic_runtime_adoptions.id'))
