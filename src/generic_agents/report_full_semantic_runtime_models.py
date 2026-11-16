"""Independent, explicit workspace workflow-design adoptions; no implicit grants."""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Row, Scoped, Payload


class FullSemanticRuntimeAdoption(Row, Scoped, Payload, Base):
    __tablename__ = 'full_semantic_runtime_adoptions'
    __table_args__ = (UniqueConstraint('request_id'),
        UniqueConstraint('workspace_id', 'handoff_id', 'business_name'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    target_actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    handoff_id: Mapped[str] = mapped_column(ForeignKey('report_configuration_records.id'))
    proof_id: Mapped[str] = mapped_column(ForeignKey('full_semantic_region_proofs.id'))
    configuration_id: Mapped[str] = mapped_column(ForeignKey('setup_drafts.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    business_name: Mapped[str] = mapped_column(String(100))
