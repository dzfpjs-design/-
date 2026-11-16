"""Independent grants, original commands and one immutable source occurrence."""
from sqlalchemy import ForeignKey,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .models import Base,Row,Scoped,Payload


class SignalApprovalRouteV247(Row,Scoped,Payload,Base):
    __tablename__='signal_approval_routes_v247'
    instance_id:Mapped[str]=mapped_column(ForeignKey('domain_instances.id'),index=True)
    release_id:Mapped[str]=mapped_column(ForeignKey('domain_instance_releases.id'))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    status:Mapped[str]=mapped_column(String(32),default='active')
    payload_hash:Mapped[str]=mapped_column(String(64))


class SignalApprovalOccurrenceV247(Row,Scoped,Payload,Base):
    __tablename__='signal_approval_occurrences_v247'
    __table_args__=(UniqueConstraint('workspace_id','instance_id','source_identity_hash'),
        UniqueConstraint('workspace_id','signal_id'),UniqueConstraint('task_id'))
    instance_id:Mapped[str]=mapped_column(ForeignKey('domain_instances.id'),index=True)
    route_id:Mapped[str]=mapped_column(ForeignKey('signal_approval_routes_v247.id'),index=True)
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    signal_id:Mapped[str]=mapped_column(ForeignKey('perception_signals.id'))
    event_id:Mapped[str]=mapped_column(ForeignKey('outbox_events.id'))
    observation_id:Mapped[str]=mapped_column(ForeignKey('observations.id'),index=True)
    source_identity_hash:Mapped[str]=mapped_column(String(64))
    task_id:Mapped[str|None]=mapped_column(ForeignKey('tasks.id'))
    status:Mapped[str]=mapped_column(String(32),default='frozen',index=True)
    payload_hash:Mapped[str]=mapped_column(String(64))


class SignalApprovalCommandV247(Row,Scoped,Payload,Base):
    __tablename__='signal_approval_commands_v247'
    __table_args__=(UniqueConstraint('workspace_id','request_id'),)
    instance_id:Mapped[str]=mapped_column(ForeignKey('domain_instances.id'))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    route_id:Mapped[str]=mapped_column(ForeignKey('signal_approval_routes_v247.id'))
    request_id:Mapped[str]=mapped_column(String(36))
    operation:Mapped[str]=mapped_column(String(16))
    payload_hash:Mapped[str]=mapped_column(String(64))
