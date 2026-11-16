"""Additive durable approval authority; Task JSON is never the authority."""
from sqlalchemy import ForeignKey, String, Integer, Boolean, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class ApprovalUnitV175(Row, Scoped, Payload, Base):
    __tablename__ = 'approval_units_v175'
    parent_id: Mapped[str | None] = mapped_column(ForeignKey('approval_units_v175.id'))
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ApprovalRoleV175(Row, Scoped, Payload, Base):
    __tablename__ = 'approval_roles_v175'
    __table_args__ = (UniqueConstraint('workspace_id', 'unit_id', 'role_key', 'user_id'),)
    unit_id: Mapped[str] = mapped_column(ForeignKey('approval_units_v175.id'))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    role_key: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ApprovalPolicyV175(Row, Scoped, Payload, Base):
    __tablename__ = 'approval_policies_v175'
    __table_args__ = (UniqueConstraint('workspace_id', 'instance_id', 'business_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    business_id: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ApprovalPolicyVersionV175(Row, Scoped, Payload, Base):
    __tablename__ = 'approval_policy_versions_v175'
    __table_args__ = (UniqueConstraint('policy_id', 'revision'),)
    policy_id: Mapped[str] = mapped_column(ForeignKey('approval_policies_v175.id'))
    revision: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))


class ApprovalManagedTaskV175(Row, Scoped, Payload, Base):
    __tablename__ = 'approval_managed_tasks_v175'
    __table_args__ = (UniqueConstraint('task_id'),)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'))
    policy_id: Mapped[str] = mapped_column(ForeignKey('approval_policies_v175.id'))
    policy_version_id: Mapped[str] = mapped_column(ForeignKey('approval_policy_versions_v175.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(40), default='draft')


class ApprovalSubmissionV175(Row, Scoped, Payload, Base):
    __tablename__ = 'approval_submissions_v175'
    __table_args__ = (UniqueConstraint('managed_id', 'round_number'),)
    managed_id: Mapped[str] = mapped_column(ForeignKey('approval_managed_tasks_v175.id'))
    round_number: Mapped[int] = mapped_column(Integer)


class ApprovalCommandV175(Row, Scoped, Payload, Base):
    __tablename__ = 'approval_commands_v175'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str] = mapped_column(String(100))
    exact_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))

