"""Private, immutable ciphertext snapshots for original-request receipt reads.

No snapshot is a dispatch grant. The capability's independently issued Audit
pins the snapshot UUID and ciphertext digest; only the server transport opens it.
"""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped


class DeviceGatewayCredentialSnapshotV269(Row, Scoped, Base):
    __tablename__ = 'device_gateway_credential_snapshots_v269'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'connection_id', 'credential_revision'),
        CheckConstraint('credential_revision >= 1'),
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey('device_gateway_connections_v268.id'), index=True)
    credential_revision: Mapped[int] = mapped_column(Integer)
    # Also excluded by services.as_dict if this private ORM ever reaches it.
    token_cipher: Mapped[str] = mapped_column(Text, info={'typed_payload_internal': True})


@event.listens_for(DeviceGatewayCredentialSnapshotV269, 'before_update')
def _immutable_snapshot(mapper, connection, target):
    raise ValueError('Gateway credential snapshots are immutable')
