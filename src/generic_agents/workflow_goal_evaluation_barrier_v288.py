"""Scheduler restriction only; complete authority stays in the Goal handler.

A real pending producer cannot consume its dependent Goal Job's attempts. Bad
or missing references are not silently skipped forever: the ordinary handler
will record their actual failure. This predicate grants no execution rights.
"""
from sqlalchemy import and_, select
from sqlalchemy.orm import aliased

from .models import Job
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288

KIND = 'check_native_business_goal'
REF = 'goal_evaluation_ref_v288'
PRODUCER = 'goal_evaluation_producer_job_v288'


def pending_predicate():
    evaluation = aliased(GoalEvaluationV288)
    producer = aliased(Job)
    pending = (select(evaluation.id).join(producer,
        and_(producer.id == evaluation.job_id, producer.workspace_id == evaluation.workspace_id))
        .where(evaluation.workspace_id == Job.workspace_id,
            evaluation.task_id == Job.data['task_id'].as_string(),
            evaluation.id == Job.data[REF]['id'].as_string(),
            evaluation.record_hash == Job.data[REF]['record_hash'].as_string(),
            producer.id == Job.data[PRODUCER].as_string(),
            producer.kind == 'execute_full_semantic_graph_node',
            producer.data['task_id'].as_string() == evaluation.task_id,
            producer.status.in_(['queued', 'running']))
        .correlate(Job).exists())
    return and_(Job.kind == KIND, pending)
