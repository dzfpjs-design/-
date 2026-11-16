"""Isolated candidate for a durable one-way stop fence.

This is an internal transaction primitive, NOT an authenticated endpoint.
Callers must first validate the original trigger/ACK and current authority. It
must not be published or enabled until every execution boundary uses the same
producer row lock and real PostgreSQL concurrent/restart tests have passed.
"""
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4, UUID
import json
import re

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column
from generic_agents.models import Base, Job


class Control(Base):
    __tablename__ = 'auto_evaluation_recovery_control_v360'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'request_id', name='uq_recovery_request_v360'),
        UniqueConstraint('workspace_id', 'producer_job_id', name='uq_recovery_producer_v360'),
        CheckConstraint("outcome IN ('fenced_effect_unknown', 'terminal_record_only')", name='ck_recovery_outcome_v360'),
        CheckConstraint('attempt >= 0', name='ck_recovery_attempt_v360'),
        Index('ix_recovery_scope_v360', 'workspace_id', 'task_id', 'producer_job_id'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), nullable=False)
    producer_job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'), nullable=False)
    trigger_id: Mapped[str] = mapped_column(ForeignKey('auto_evaluation_triggers_v356.id'), nullable=False)
    witness_id: Mapped[str] = mapped_column(ForeignKey('auto_evaluation_ack_witnesses_v356.id'), nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_view_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # These values bind the command to what the caller independently checked.
    lease_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Refused(ValueError):
    pass

def rollout_enabled():
    # Never switch on by changing a local environment variable. The staged
    # migration, uniform worker rollout and PG safety evidence are required.
    return False


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value: raise ValueError()
        return value
    except (ValueError, AttributeError, TypeError):
        raise Refused('invalid identifier') from None


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def locked_producer(db, workspace_id, task_id, job_id):
    """Must be held until commit; every participating writer uses this row."""
    workspace_id, task_id, job_id = map(_uuid, (workspace_id, task_id, job_id))
    job = db.scalar(select(Job).where(Job.id == job_id, Job.workspace_id == workspace_id)
                    .with_for_update(nowait=True).execution_options(populate_existing=True))
    if (job is None or job.kind != 'append_goal_evaluation_v297'
            or not isinstance(job.data, dict) or job.data.get('task_id') != task_id):
        raise Refused('producer scope unavailable')
    return job


def create_fence(db, *, workspace_id, task_id, job_id, trigger_id, witness_id,
                 actor_id, request_id, reason, expected_lease_hash, expected_attempt, expected_view_hash,
                 outcome='fenced_effect_unknown'):
    """Write only inside a transaction already checking current auth and origin.

    Serializes decisions against participating claim/commit paths via Job lock.
    It never commits, invokes a device, or guarantees cancellation of network IO.
    """
    if not rollout_enabled(): raise Refused('recovery rollout is not enabled')
    for value in (workspace_id, task_id, job_id, trigger_id, witness_id, actor_id, request_id):
        _uuid(value)
    if type(reason) is not str or not reason.strip() or len(reason) > 500:
        raise Refused('invalid reason')
    if type(expected_attempt) is not int or expected_attempt < 0:
        raise Refused('invalid attempt')
    if outcome not in ('fenced_effect_unknown','terminal_record_only'):
        raise Refused('invalid outcome')
    if type(expected_lease_hash) is not str or re.fullmatch(r'[0-9a-f]{64}', expected_lease_hash) is None:
        raise Refused('invalid expected binding')
    if type(expected_view_hash) is not str or re.fullmatch(r'[0-9a-f]{64}', expected_view_hash) is None:
        raise Refused('invalid preview binding')
    body = {'workspace_id': workspace_id, 'task_id': task_id, 'job_id': job_id,
            'trigger_id': trigger_id, 'witness_id': witness_id, 'actor_id': actor_id,
            'request_id': request_id, 'reason': reason, 'lease_hash': expected_lease_hash,
            'attempt': expected_attempt, 'expected_view_hash': expected_view_hash, 'outcome':outcome}
    request_hash = _hash(body)
    # Lock the producer before checking or writing the control. Concurrent claim
    # and stage-commit integrations MUST take this same lock before effects.
    job = locked_producer(db, workspace_id, task_id, job_id)
    existing = db.scalar(select(Control).where(Control.workspace_id == workspace_id,
                         Control.request_id == request_id))
    if existing:
        if existing.request_hash != request_hash or existing.actor_id != actor_id:
            raise Refused('request conflict')
        return existing
    if db.scalar(select(Control.id).where(Control.workspace_id == workspace_id,
                 Control.producer_job_id == job_id)):
        raise Refused('producer already fenced')
    if job.status not in ('queued', 'running', 'succeeded') or type(job.attempts) is not int:
        raise Refused('producer requires record-only investigation')
    if _hash(job.lease_token) != expected_lease_hash or job.attempts != expected_attempt:
        raise Refused('producer changed')
    row = Control(id=str(uuid4()), workspace_id=workspace_id, task_id=task_id,
                  producer_job_id=job_id, trigger_id=trigger_id, witness_id=witness_id,
                  actor_id=actor_id, request_id=request_id, request_hash=request_hash,
                  reason=reason, lease_hash=expected_lease_hash, attempt=expected_attempt,
                  expected_view_hash=expected_view_hash,
                  outcome=outcome, created_at=datetime.now(timezone.utc))
    db.add(row); db.flush()
    return row


def require_unfenced(db, *, workspace_id, task_id, job_id):
    """Call while holding the producer lock through the caller's commit."""
    if not rollout_enabled(): return None
    job = locked_producer(db, workspace_id, task_id, job_id)
    if db.scalar(select(Control.id).where(Control.workspace_id == workspace_id,
                 Control.producer_job_id == job_id,Control.outcome=='fenced_effect_unknown')) is not None:
        raise Refused('producer fenced; external effect may be unknown')
    return job
