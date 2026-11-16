"""One explicit audio-track controller; originals remain Evidence/Observation."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class AudioStream(Row, Scoped, Payload, Base):
    __tablename__ = 'audio_streams'
    channel_id: Mapped[str] = mapped_column(ForeignKey('media_channels.id'), index=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey('resource_entities.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')
