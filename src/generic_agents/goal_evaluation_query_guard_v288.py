"""Generic-query protection for independently saved V288 evaluation queries.

A marker only restricts an operation. Read access requires the real saved query,
independent source Audit, original Job/Invocation and current Task reader IAM.
This reference never certifies producer completion, ACKs or Goal acceptance.
"""
from functools import wraps
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select

from . import goal_evaluation_query_v288 as queries
from .contracts import digest
from .models import Audit, Capability, DataGap, DataRun, GapCheck, Invocation, Job
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288

KEY = queries.KEY
FAMILY = 'goal_evaluation_v288'
SCHEMA = 'data-query-reference-v144'
WRITE_ERROR = '这是原业务任务的冻结目标核对查询；通用查询不能执行、重试、切换模型、确认方案、反馈或覆盖，请回原任务核对'
READ_ERROR = '原目标核对查询的读取归属或冻结记录无法核实；未返回通用原始查询'
MAX_LINEAGE_ROWS = 1000
MAX_LINEAGE_DEPTH = 32


def need(value):
    if not value:
        raise HTTPException(409, READ_ERROR)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration,
                RecursionError, OverflowError, UnicodeError):
            need(False)
    return wrapped


def _uuid(value):
    need(type(value) is str and str(UUID(value)) == value)
    return value


def managed(row):
    """Presence, including malformed/null content, is a restriction, not a right."""
    return type(getattr(row, 'data', None)) is dict and KEY in row.data


def _scope(p, row):
    if row.workspace_id != p.workspace_id:
        raise HTTPException(404, '查询不存在或不在当前工作区')
    _uuid(row.id)


def _anchors(db, p, row):
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_([queries.SAVED, 'workflow.goal_evaluation.query_saved_v299']),
        Audit.object_id == row.id).limit(2).execution_options(populate_existing=True)))
    evaluations = list(db.scalars(select(GoalEvaluationV288).where(
        GoalEvaluationV288.workspace_id == p.workspace_id,
        GoalEvaluationV288.evaluation_query_id == row.id).limit(2).execution_options(populate_existing=True)))
    return events, evaluations


def _lineage_height(parents, identity, trail=(), heights=None):
    """Return a bounded height, or None for a cycle/depth violation."""
    heights = {} if heights is None else heights
    if identity in trail or len(trail) >= MAX_LINEAGE_DEPTH:
        return None
    if identity in heights:
        return heights[identity]
    height = 1
    for parent in parents.get(identity, ()):
        previous = _lineage_height(parents, parent, (*trail, identity), heights)
        if previous is None:
            return None
        height = max(height, previous + 1)
    if height > MAX_LINEAGE_DEPTH:
        return None
    heights[identity] = height
    return height


def _lineage_trace(db, p, row):
    """Restriction follows the same scoped parent/Gap custody as discovery.

    A damaged, missing, foreign or over-budget ancestor is restricted, never a
    new read grant. A fully resolved ordinary lineage still returns False.
    Keep this independent of QueryVisibility to avoid a classification cycle.
    """
    known = {row.id: row}; pending = {row.id}; parents = {}; rounds = 0
    while pending:
        rounds += 1
        if len(known) > MAX_LINEAGE_ROWS or rounds > MAX_LINEAGE_DEPTH:
            return True
        explicit, wanted = {}, set()
        for identity in sorted(pending):
            current = known[identity]; _scope(p, current)
            if type(current.data) is not dict:
                return True
            if managed(current):
                return True
            events, evaluations = _anchors(db, p, current)
            if events or evaluations:
                return True
            declared = parents.setdefault(identity, set())
            if 'parent_query_id' in current.data:
                parent = _uuid(current.data['parent_query_id'])
                declared.add(parent); wanted.add(parent)
            if 'gap_recheck' in current.data:
                value = current.data['gap_recheck']
                if type(value) is not dict:
                    return True
                gap_id = _uuid(value.get('gap_id'))
                explicit.setdefault(gap_id, set()).add(identity)
        identities = sorted(pending)
        checks = list(db.scalars(select(GapCheck).where(GapCheck.workspace_id == p.workspace_id,
            GapCheck.data['query_id'].as_string().in_(identities)).limit(MAX_LINEAGE_ROWS + 1)))
        if len(checks) > MAX_LINEAGE_ROWS:
            return True
        checked = {}
        for check in checks:
            if (check.workspace_id != p.workspace_id or type(check.data) is not dict
                    or check.data.get('query_id') not in pending):
                return True
            checked.setdefault(_uuid(check.gap_id), set()).add(check.data['query_id'])
        gap_ids = sorted(set(explicit) | set(checked))
        gaps = list(db.scalars(select(DataGap).where(DataGap.workspace_id == p.workspace_id,
            or_(DataGap.query_id.in_(identities),
                DataGap.data['pending_recheck_query_id'].as_string().in_(identities),
                DataGap.data['latest_recheck_query_id'].as_string().in_(identities),
                DataGap.id.in_(gap_ids))).limit(MAX_LINEAGE_ROWS + 1)))
        if len(gaps) > MAX_LINEAGE_ROWS:
            return True
        actual = {gap.id: gap for gap in gaps}
        if set(gap_ids) - set(actual):
            return True
        for gap in gaps:
            if gap.workspace_id != p.workspace_id or type(gap.data) is not dict:
                return True
            parent = _uuid(gap.query_id)
            children = set(checked.get(gap.id, ()))
            children.update(value for value in (
                gap.data.get('pending_recheck_query_id'), gap.data.get('latest_recheck_query_id'))
                if type(value) is str and value in pending)
            if explicit.get(gap.id, set()) - children:
                return True
            for identity in children:
                declared = parents.setdefault(identity, set())
                if declared and declared != {parent}:
                    return True
                declared.add(parent); wanted.add(parent)
        missing = wanted - set(known)
        if len(known) + len(missing) > MAX_LINEAGE_ROWS:
            return True
        loaded = list(db.scalars(select(DataRun).where(DataRun.workspace_id == p.workspace_id,
            DataRun.id.in_(sorted(missing))).limit(MAX_LINEAGE_ROWS + 1))) if missing else []
        new = {value.id: value for value in loaded}
        if len(loaded) > MAX_LINEAGE_ROWS or missing - set(new):
            return True
        known.update(new); pending = set(new)
    return _lineage_height(parents, row.id) is None


@checked
def has_trace(db, p, row):
    """Call even without a marker; retained independent records cannot downgrade."""
    with db.no_autoflush:
        _scope(p, row)
        return _lineage_trace(db, p, row)


def reject_generic_write(db, p, row):
    """Deny even damaged snapshots; failed visibility is never permission to write."""
    if has_trace(db, p, row):
        raise HTTPException(409, WRITE_ERROR)


def _fresh(db, p, model, identity):
    value = db.scalar(select(model).where(model.workspace_id == p.workspace_id, model.id == _uuid(identity))
        .execution_options(populate_existing=True))
    need(value is not None)
    return value


@checked
def visible(db, p, row):
    """Cold read under the actual reader; never construct the original actor."""
    from .workspace_api import saved_execution
    from .workflow_graph_scope import KIND
    with db.no_autoflush:
        _scope(p, row); queries._reader(db, p)
        actual = _fresh(db, p, DataRun, row.id)
        events, evaluations = _anchors(db, p, actual)
        need(managed(actual) and len(events) == 1 and len(evaluations) <= 1)
        event = events[0]; anchor = event.data
        if (type(anchor) is dict and anchor.get('schema') == 'goal-evaluation-query-provenance-v299'
                and event.action == 'workflow.goal_evaluation.query_saved_v299'):
            from .goal_evaluation_append_query_v299 import visible_reference
            return visible_reference(db, p, actual, events, evaluations)
        need(type(anchor) is dict and anchor.get('schema') == queries.SCHEMA
            and event.action == queries.SAVED
            and anchor['query_ref']['query_id'] == actual.id)
        # The actual creator Job, not a user-controlled marker, supplies the Task.
        job = _fresh(db, p, Job, anchor['job_id'])
        call = _fresh(db, p, Invocation, anchor['evaluation_id'])
        task, release, business = saved_execution(db, p, _uuid(job.data['task_id']))
        frozen = queries.checked_frozen(db, p, task, actual.id, evaluation_id=call.id)
        marker = frozen['provenance']; creator = marker['creator']; scope = marker['scope']
        cap = _fresh(db, p, Capability, call.capability_id)
        need(job.kind == KIND and job.workspace_id == call.workspace_id == task.workspace_id == p.workspace_id
            and call.id == marker['evaluation_id'] == creator['invocation_id']
            and job.id == anchor['job_id'] == creator['job_id']
            and event.actor_id == actual.owner_id == job.data['actor_id'] == creator['actor_id']
            and job.data['activation_id'] == creator['activation_id'] and creator['node_id'] == 'evaluation_packet'
            and call.data.get('job_id') == job.id and call.data.get('role') == 'data'
            and call.data.get('side_effect') is True
            and call.data.get('input_hash') == job.data['input_hash'] == creator['input_hash']
            and call.idempotency_key == job.idempotency_key == job.data['invocation_key'] == creator['request_key']
            and job.data['graph_hash'] == scope['graph_hash']
            and cap.id == creator['capability_id'] and cap.kind == 'tool'
            and cap.content_hash == cap.validated_hash == digest(cap.data) == creator['capability_hash']
            and cap.data['allowed_agent_roles'] == ['data']
            and cap.data['execution'] == {'adapter': 'workflow_service', 'service_actor': 'reader_service',
                'operation': queries.OP, 'side_effect': True}
            and scope['task_id'] == task.id and scope['release_id'] == release.id
            and scope['instance_id'] == release.instance_id == task.data['instance_id']
            and scope['package_id'] == release.package_id == task.package_id
            and scope['business_id'] == business['id'] == task.data['business_id'])
        if evaluations:
            evaluation = evaluations[0]
            need(evaluation.id == evaluation.invocation_id == call.id
                and evaluation.evaluation_query_id == actual.id and evaluation.task_id == task.id
                and evaluation.job_id == job.id and evaluation.actor_id == creator['actor_id']
                and evaluation.workspace_id == p.workspace_id and evaluation.release_id == release.id
                and evaluation.instance_id == release.instance_id and evaluation.package_id == release.package_id
                and evaluation.data['evaluation_id'] == call.id
                and evaluation.data['evaluation_query_ref'] == frozen['query_ref']
                and evaluation.data['scope'] == scope)
        return {'schema': SCHEMA, 'id': actual.id, 'version': actual.version, 'status': actual.status,
            'question': actual.question, 'created_at': actual.created_at.isoformat(),
            'reference_only': True, 'family': FAMILY,
            'context': {'instance_id': release.instance_id, 'task_id': task.id,
                'detail_path': '/api/workspace/tasks/' + task.id},
            'notice': '这是原任务的独立资料核对快照。此入口仅核对读取归属，不表示生产任务已完成、独立收件完整或目标已达成；请回原任务查看。'}
