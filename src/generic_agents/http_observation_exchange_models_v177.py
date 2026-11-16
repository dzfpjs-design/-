"""One durable original HTTP exchange per Acquisition; no execution authority."""
from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class HTTPObservationExchangeV177(Row, Scoped, Payload, Base):
    __tablename__ = 'http_observation_exchanges_v177'
    __table_args__ = (UniqueConstraint('workspace_id', 'acquisition_id'),
        CheckConstraint("status IN ('prepared','awaiting_receipt','received_no_data','received')"))
    acquisition_id: Mapped[str] = mapped_column(ForeignKey('acquisition_tasks.id'), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    environment_id: Mapped[str] = mapped_column(ForeignKey('simulation_environments_v176.id'))
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    dispatch_job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    observation_id: Mapped[str | None] = mapped_column(ForeignKey('observations.id'), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30))
