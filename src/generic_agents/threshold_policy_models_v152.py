"""Finite actual numeric-policy authority, separate from shadow and study."""
from sqlalchemy import CheckConstraint,ForeignKey,Index,String,Text,UniqueConstraint,text
from sqlalchemy.orm import Mapped,mapped_column
from .models import Base,Payload,Row,Scoped


class ThresholdPolicyGrant(Row,Scoped,Payload,Base):
    __tablename__='threshold_policy_grants_v152'
    __table_args__=(CheckConstraint("status IN ('active','paused','rolled_back')"),
        Index('uq_threshold_policy_live_scope_v152','workspace_id','scope_key',unique=True,
            postgresql_where=text("status <> 'rolled_back'")),)
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    review_id:Mapped[str]=mapped_column(ForeignKey('quality_reviews_v152.id'),index=True)
    scope_key:Mapped[str]=mapped_column(String(64),index=True)
    status:Mapped[str]=mapped_column(String(30))
    record_hash:Mapped[str]=mapped_column(String(64))


class ThresholdPolicyCommand(Row,Scoped,Payload,Base):
    __tablename__='threshold_policy_commands_v152'
    __table_args__=(UniqueConstraint('workspace_id','actor_id','request_id'),
        CheckConstraint("outcome IN ('saved','rejected')"),
        CheckConstraint("operation IN ('create','pause','resume','rollback','input')"),)
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    request_id:Mapped[str]=mapped_column(String(36))
    method:Mapped[str]=mapped_column(String(8))
    path:Mapped[str]=mapped_column(Text)
    wire_sha256:Mapped[str]=mapped_column(String(64))
    fingerprint:Mapped[str]=mapped_column(String(64))
    operation:Mapped[str]=mapped_column(String(20))
    target_id:Mapped[str|None]=mapped_column(String(36),nullable=True,index=True)
    policy_id:Mapped[str|None]=mapped_column(String(36),nullable=True,index=True)
    outcome:Mapped[str]=mapped_column(String(15))
    command_hash:Mapped[str]=mapped_column(String(64))


class ThresholdPolicySelection(Row,Scoped,Payload,Base):
    __tablename__='threshold_policy_selections_v152'
    __table_args__=(UniqueConstraint('workspace_id','source_id','source_record_id'),
        UniqueConstraint('workspace_id','observation_id'),UniqueConstraint('workspace_id','job_id'),
        CheckConstraint("choice IN ('candidate','baseline')"),
        CheckConstraint("status IN ('queued','reserved','completed','failed','stopped')"),)
    policy_id:Mapped[str]=mapped_column(ForeignKey('threshold_policy_grants_v152.id'),index=True)
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    source_id:Mapped[str]=mapped_column(ForeignKey('capabilities.id'),index=True)
    source_record_id:Mapped[str]=mapped_column(String(200))
    observation_id:Mapped[str]=mapped_column(ForeignKey('observations.id'))
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'))
    choice:Mapped[str]=mapped_column(String(12))
    status:Mapped[str]=mapped_column(String(20))
    record_hash:Mapped[str]=mapped_column(String(64))
