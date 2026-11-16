"""Durable gateway configuration, actor-owned commands and explicit device grants."""
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class DeviceGatewayConnectionV268(Row, Scoped, Payload, Base):
    __tablename__ = 'device_gateway_connections_v268'
    name: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(300))
    protocol: Mapped[str] = mapped_column(String(50), default='procurement-simulator-v1')
    status: Mapped[str] = mapped_column(String(20), default='enabled')
    credential_revision: Mapped[int] = mapped_column(Integer, default=1)
    token_cipher: Mapped[str] = mapped_column(Text)


class DeviceGatewayCommandV268(Row, Scoped, Payload, Base):
    __tablename__ = 'device_gateway_commands_v268'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    connection_id: Mapped[str] = mapped_column(ForeignKey('device_gateway_connections_v268.id'), index=True)
    operation: Mapped[str] = mapped_column(String(20))
    payload_hmac: Mapped[str] = mapped_column(String(64))


class DeviceGatewayDeviceGrantV268(Row, Scoped, Payload, Base):
    __tablename__ = 'device_gateway_device_grants_v268'
    __table_args__ = (UniqueConstraint('workspace_id', 'connection_id', 'device_external_id'),)
    connection_id: Mapped[str] = mapped_column(ForeignKey('device_gateway_connections_v268.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    device_external_id: Mapped[str] = mapped_column(String(128))
    sensor_device_id: Mapped[str] = mapped_column(ForeignKey('sensor_devices.id'))
    discovery_job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    connection_version: Mapped[int] = mapped_column(Integer)
    credential_revision: Mapped[int] = mapped_column(Integer)
    catalogue_digest: Mapped[str] = mapped_column(String(64))
