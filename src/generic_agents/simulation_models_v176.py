"""Additive, permanently labelled device-integration rehearsal records."""
from datetime import datetime
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class SimulationEnvironmentV176(Row, Scoped, Payload, Base):
    __tablename__ = 'simulation_environments_v176'
    __table_args__ = (UniqueConstraint('instance_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))


class SimulationDefinitionV176(Row, Scoped, Payload, Base):
    __tablename__ = 'simulation_definitions_v176'
    environment_id: Mapped[str] = mapped_column(ForeignKey('simulation_environments_v176.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='paused')
    next_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimulationSampleV176(Row, Scoped, Payload, Base):
    __tablename__ = 'simulation_samples_v176'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    definition_id: Mapped[str] = mapped_column(ForeignKey('simulation_definitions_v176.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    invocation_id: Mapped[str | None] = mapped_column(ForeignKey('invocations.id'))
    observation_id: Mapped[str | None] = mapped_column(ForeignKey('observations.id'))


class SimulationDailyV176(Row, Scoped, Payload, Base):
    __tablename__ = 'simulation_daily_v176'
    __table_args__ = (UniqueConstraint('environment_id', 'business_day'),)
    environment_id: Mapped[str] = mapped_column(ForeignKey('simulation_environments_v176.id'))
    business_day: Mapped[str] = mapped_column(String(10))


class SimulationCommandV176(Row, Scoped, Payload, Base):
    __tablename__ = 'simulation_commands_v176'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    request_id: Mapped[str] = mapped_column(String(36))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    operation: Mapped[str] = mapped_column(String(60))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))


class SimulationFeedbackV176(Row, Scoped, Payload, Base):
    __tablename__ = 'simulation_feedback_v176'
    environment_id: Mapped[str] = mapped_column(ForeignKey('simulation_environments_v176.id'))
    observation_id: Mapped[str] = mapped_column(ForeignKey('observations.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
