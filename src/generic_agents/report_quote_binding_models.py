"""Private human quote-selection drafts; never a source approval or paid record."""
from sqlalchemy import ForeignKey, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Row, Scoped, Payload


class QuoteBindingDraft(Row, Scoped, Payload, Base):
    __tablename__ = 'report_quote_binding_drafts'
    __table_args__ = (Index('ix_quote_binding_owner_handoff', 'workspace_id', 'actor_id', 'handoff_id', 'candidate_index'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    handoff_id: Mapped[str] = mapped_column(ForeignKey('report_configuration_records.id'))
    candidate_index: Mapped[int] = mapped_column(Integer)
