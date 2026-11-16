"""SIM-only studies and immutable command receipts; no production authority."""
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Payload, Row, Scoped


class SimulationStudyV176(Row, Scoped, Payload, Base):
    __tablename__ = 'simulation_studies_v176'
    __table_args__ = (CheckConstraint("status IN ('frozen','completed','blocked')"),)
    environment_id: Mapped[str] = mapped_column(ForeignKey('simulation_environments_v176.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(20))
    frozen_hash: Mapped[str] = mapped_column(String(64))


class SimulationEvolutionCommandV176(Row, Scoped, Payload, Base):
    __tablename__ = 'simulation_evolution_commands_v176'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),
                      UniqueConstraint('target_kind', 'target_id', 'target_version'),
                      CheckConstraint("target_kind IN ('feedback','study')"))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(40))
    path: Mapped[str] = mapped_column(Text)
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    target_kind: Mapped[str] = mapped_column(String(12))
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    target_version: Mapped[int] = mapped_column()
    command_hash: Mapped[str] = mapped_column(String(64))
