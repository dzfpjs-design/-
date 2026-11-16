"""Independent original sounds and Task-owned environmental classifications."""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class SoundInput(Row, Scoped, Payload, Base):
    __tablename__ = 'sound_inputs'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))


class EnvironmentSoundRun(Row, Scoped, Payload, Base):
    __tablename__ = 'environment_sound_runs'
    __table_args__ = (UniqueConstraint('workspace_id', 'invocation_id'),)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'))
    capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    sound_input_id: Mapped[str] = mapped_column(ForeignKey('sound_inputs.id'))
    status: Mapped[str] = mapped_column(String(30), default='running')
