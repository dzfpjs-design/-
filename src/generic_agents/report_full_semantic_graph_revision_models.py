"""Append-only graph revisions; the original graph adoption remains the root."""
from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Row, Scoped, Payload


class FullSemanticGraphRevision(Row, Scoped, Payload, Base):
    __tablename__ = 'full_semantic_graph_revisions'
    __table_args__ = (
        UniqueConstraint('request_id'),
        UniqueConstraint('parent_id'),
        Index('ix_full_graph_revision_root', 'workspace_id', 'root_id'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    handoff_id: Mapped[str] = mapped_column(ForeignKey('report_configuration_records.id'))
    proof_id: Mapped[str] = mapped_column(ForeignKey('full_semantic_region_proofs.id'))
    configuration_id: Mapped[str] = mapped_column(ForeignKey('setup_drafts.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    business_name: Mapped[str] = mapped_column(String(100))
    # The parent belongs to either graph table. Both its actual row and its
    # uninterrupted chain are checked before a current binding is replaced.
    parent_id: Mapped[str] = mapped_column(String(36))
    root_id: Mapped[str] = mapped_column(ForeignKey('full_semantic_graph_adoptions.id'))
