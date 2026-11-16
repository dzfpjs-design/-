"""Independent scheduling preference; never a MonitorPolicy approval."""
from sqlalchemy import CheckConstraint,ForeignKey,Integer,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .models import Base,Payload,Row,Scoped

class MonitorSchedulingHeadV253(Row,Scoped,Payload,Base):
    __tablename__='monitor_scheduling_heads_v253'
    __table_args__=(UniqueConstraint('workspace_id','policy_id'),CheckConstraint('version >= 1'),CheckConstraint('epoch >= 1'))
    policy_id:Mapped[str]=mapped_column(ForeignKey('monitor_policies.id'),index=True)
    mode:Mapped[str]=mapped_column(String(16))
    epoch:Mapped[int]=mapped_column(Integer)
    record_hash:Mapped[str]=mapped_column(String(64))

class MonitorSchedulingCommandV253(Row,Scoped,Payload,Base):
    __tablename__='monitor_scheduling_commands_v253'
    __table_args__=(UniqueConstraint('workspace_id','request_id'),CheckConstraint('version = 1'))
    policy_id:Mapped[str]=mapped_column(ForeignKey('monitor_policies.id'),index=True)
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    request_id:Mapped[str]=mapped_column(String(100))
    command_hash:Mapped[str]=mapped_column(String(64))


class MonitorSchedulingRetiredRequestV253(Row,Scoped,Payload,Base):
    __tablename__='monitor_scheduling_retired_requests_v253'
    __table_args__=(UniqueConstraint('workspace_id','request_id'),CheckConstraint('version = 1'))
    policy_id:Mapped[str]=mapped_column(ForeignKey('monitor_policies.id'),index=True)
    instance_id:Mapped[str]=mapped_column(ForeignKey('domain_instances.id'))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    request_id:Mapped[str]=mapped_column(String(100))
    record_hash:Mapped[str]=mapped_column(String(64))
