"""One immutable command receipt; ordinary plans/acquisitions do the work."""
from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload


class GoalConflictObservationCommandV259(Row, Scoped, Payload, Base):
    __tablename__ = 'goal_conflict_observation_commands_v259'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'request_id'),
        UniqueConstraint('workspace_id', 'accepted_key'),
        CheckConstraint("outcome IN ('accepted','rejected')"),
        CheckConstraint("(outcome='accepted' AND accepted_key IS NOT NULL AND branch_query_id IS NOT NULL AND branch_gap_id IS NOT NULL AND observation_plan_id IS NOT NULL) OR (outcome='rejected' AND accepted_key IS NULL AND branch_query_id IS NULL AND branch_gap_id IS NULL AND observation_plan_id IS NULL)"))
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    raw_sha256: Mapped[str] = mapped_column(String(64))
    context_hash: Mapped[str] = mapped_column(String(64))
    conflict_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(10))
    original_query_id: Mapped[str] = mapped_column(ForeignKey('data_query_runs.id'))
    original_gap_id: Mapped[str] = mapped_column(ForeignKey('data_evidence_gaps.id'))
    branch_query_id: Mapped[str | None] = mapped_column(ForeignKey('data_query_runs.id'), nullable=True)
    branch_gap_id: Mapped[str | None] = mapped_column(ForeignKey('data_evidence_gaps.id'), nullable=True)
    observation_plan_id: Mapped[str | None] = mapped_column(ForeignKey('observation_plans.id'), nullable=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey('monitor_policies.id'))
    record_hash: Mapped[str] = mapped_column(String(64))
