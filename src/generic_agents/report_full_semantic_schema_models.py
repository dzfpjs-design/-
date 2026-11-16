"""Independent immutable schema-adoption receipts; never a legacy handoff kind."""
from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Row, Scoped, Payload


class FullSemanticSchemaAdoption(Row, Scoped, Payload, Base):
    __tablename__ = 'full_semantic_schema_adoptions'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'request_id'),
        Index('ix_full_schema_handoff', 'workspace_id', 'handoff_id'),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    handoff_id: Mapped[str] = mapped_column(ForeignKey('report_configuration_records.id'))
    configuration_id: Mapped[str] = mapped_column(ForeignKey('setup_drafts.id'))
    request_id: Mapped[str] = mapped_column(String(36))
