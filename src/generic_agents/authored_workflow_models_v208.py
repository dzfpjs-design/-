"""Human-authored graph authority; independent of paid report qualifications."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Payload, Row, Scoped


class AuthoredWorkflowAdoption(Row, Scoped, Payload, Base):
    __tablename__ = 'authored_workflow_adoptions_v208'
    __table_args__ = (UniqueConstraint('configuration_id', 'business_name', 'ordinal'),
        UniqueConstraint('configuration_id', 'accepted_revision'), CheckConstraint('version = 1'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    configuration_id: Mapped[str] = mapped_column(ForeignKey('setup_drafts.id'))
    business_name: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int]
    prior_adoption_id: Mapped[str | None] = mapped_column(ForeignKey('authored_workflow_adoptions_v208.id'), nullable=True)
    accepted_revision: Mapped[int]
    binding_hash: Mapped[str] = mapped_column(String(64))


class AuthoredWorkflowCommand(Row, Scoped, Payload, Base):
    __tablename__ = 'authored_workflow_commands_v208'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),
        UniqueConstraint('adoption_id'), CheckConstraint('version = 1'))
    adoption_id: Mapped[str] = mapped_column(ForeignKey('authored_workflow_adoptions_v208.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    path: Mapped[str] = mapped_column(String(200))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
