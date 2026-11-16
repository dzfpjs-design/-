"""Hand V278's real native Gap to its adopted decision graph, never to a device.

The ordinary Goal checker owns its transaction and already-existing DataRun and
Gap. This module records one audited handoff on that Gap. It creates no query,
Task, budget, acquisition or ACK and never treats waiting as device dispatch.
Other workflow families keep their original native routing behavior.
"""
import copy
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select

from . import workflow_graph
from .business_flow_review_store import _bytes, _same
from .contracts import digest
from .models import Acquisition, Audit, DataGap, DataRun, InstanceRelease, ObservationPlan, Task, now
from .services import audit

BINDING = 'authored-decision-workflow-binding-v278'
BINDINGS = frozenset({BINDING, 'authored-decision-workflow-binding-v286', 'authored-goal-data-workflow-binding-v288'})
SCHEMA = 'native-goal-decision-handoff-v279'
KEY = 'native_goal_decision_handoff_v279'
ACTION = 'native_goal.decision_handoff_v279'
MAX_BYTES = 256 * 1024
WAIT_REASON = '原目标资料缺口已交给本流程的业务决策及其后续补采节点处理；本协调不提交采集，实际进度以原图与独立收件为准。'
CONFLICT_REASON = '原目标已有同一事实的冲突证据，字段并未缺失；保留原冲突等待核查，不新增补采字段或采集动作。'
ERROR = '原目标决策交接的已采用流程、查询、缺口或独立审计无法核对；未自动补采'


def need(ok):
    if not ok:
        raise HTTPException(409, ERROR)


def safe(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, IndexError,
                UnicodeError, RecursionError, OverflowError):
            need(False)
    return wrapped


def _binding(db, p, task):
    release = db.scalar(select(InstanceRelease).where(InstanceRelease.workspace_id == p.workspace_id,
        InstanceRelease.id == task.data['instance_release_id']).execution_options(populate_existing=True))
    need(release is not None and release.instance_id == task.data['instance_id']
        and release.package_id == task.package_id)
    businesses = [row for row in release.data['snapshot']['businesses'] if row['id'] == task.data['business_id']]
    need(len(businesses) == 1)
    business = businesses[0]
    if business.get('execution_mode') != 'confirmed_graph_workflow':
        # Compact human workflows have no semantic plan. Do not enter a graph
        # resolver for them; a damaged authored/new-family declaration is not
        # a compact fallback and must never reopen native device routing.
        plan = release.data['snapshot'].get('semantic_runtime_plan') or {}
        declarations = plan.get('graph_adoptions', [])
        need(type(declarations) is list and 'authored_workflow_origin' not in business
            and not any(type(item) is dict and type(item.get('binding')) is dict
                and item['binding'].get('schema') in BINDINGS
                and item['binding'].get('business_name') == business.get('name') for item in declarations))
        return None
    binding = workflow_graph.business_binding(release, business)
    if binding.get('schema') not in BINDINGS:
        return None
    from .authored_workflow_store_v208 import check_binding
    checked = check_binding(binding)
    need(_same(checked, binding) and checked['business_name'] == businesses[0]['name']
        and _same(checked['flow'], businesses[0]['flow']) and checked['simulation_only'] is True
        and task.data['graph_runtime']['graph_hash'] == digest(checked['graph']))
    return release, checked


def _expected(db, p, task, run, gap, current, release, binding):
    from .goals import snapshot
    goal = task.data['native_goal_context']; spec = task.data['goal_spec']; goal_snapshot = snapshot(task)
    need(digest(goal) == task.data['native_goal_context_hash'] and goal['goal_snapshot'] == goal_snapshot
        and goal['instance_id'] == task.data['instance_id'] and goal['release_id'] == release.id
        and goal['business_id'] == task.data['business_id'] and goal['entity_ids'] == task.data['entity_ids']
        and run.id == task.data['native_goal_query_id'] and run.status != 'cancelled'
        and run.data['native_goal_task_id'] == task.id and run.data['native_goal_context_hash'] == digest(goal)
        and run.data['goal_delegation'] == {'task_id': task.id, 'goal_snapshot': goal_snapshot}
        and run.data['native_goal_request_hash'] == digest(run.data['request'])
        and _same(current, run.data['result']) and digest(current) == run.data['native_goal_result_hash']
        and current['status'] in {'sufficient', 'insufficient', 'conflict'}
        and run.data['request']['entity_ids'] == spec['entity_ids'] == task.data['entity_ids']
        and run.data['request']['fields'] == spec['required_fields']
        and gap.query_id == run.id and gap.id in run.data['data_gap_ids']
        and gap.entity_id in task.data['entity_ids'] and gap.data['business_task_id'] == task.id
        and gap.data['request']['entity_ids'] == spec['entity_ids']
        and set(gap.data['request']['fields']) <= set(spec['required_fields']))
    fields = gap.data['missing_fields']
    need(type(fields) is list and len(fields) == len(set(fields)) and set(fields) <= set(spec['required_fields']))
    request = None
    requirement = gap.data['request'].get('evidence_requirement')
    if fields and spec['observation_mode'] == 'allow_new' and requirement is not None \
            and requirement['source'] in {'observations', 'either'} and requirement['time']['mode'] == 'latest':
        # This is the original server ObservationRequest, not a dispatch or a
        # policy selection. A resolved Gap retains its already-saved request.
        if gap.status in {'resolved', 'cancelled'}:
            prior = gap.data.get(KEY)
            request = copy.deepcopy(prior['observation_request']) if type(prior) is dict else None
        else:
            from .observation_requests import pin
            request = pin(db, p, gap)
        if request is not None:
            need(type(request) is dict and request['gap_id'] == gap.id and request['query_id'] == run.id
                and request['entity_id'] == gap.entity_id and request['fields'] == sorted(fields)
                and request['query_request_hash'] == digest(run.data['request'])
                and _same(request['request'], gap.data['request'])
                and _same(request['goal_delegation'], run.data['goal_delegation']))
    return {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'release_id': release.id, 'business_id': task.data['business_id'], 'package_id': task.package_id,
        'binding_hash': binding['binding_hash'], 'goal_context_hash': digest(goal), 'query_id': run.id,
        'gap_id': gap.id, 'entity_id': gap.entity_id, 'fields': copy.deepcopy(fields),
        'query_request_hash': digest(run.data['request']), 'gap_request_hash': digest(gap.data['request']),
        'observation_request': request}


def _has_plan_or_acquisition(db, p, gap):
    direct = db.scalar(select(Acquisition.id).where(Acquisition.workspace_id == p.workspace_id,
        Acquisition.data['data_gap_id'].as_string() == gap.id).limit(1))
    if direct is not None:
        return True
    return db.scalar(select(ObservationPlan.id).where(ObservationPlan.workspace_id == p.workspace_id,
        ObservationPlan.data['observation_request']['gap_id'].as_string() == gap.id).limit(1)) is not None


@safe
def defer_route(db, p, task, run, gap, current):
    """True suppresses BOTH native perception routing and parallel coordination.

    Call only in the original Goal check transaction. Native process already
    holds the Task/Gap and authenticates current IAM/175/Goal authority. Existing
    ORM objects are not refreshed: their just-built real query must not be
    overwritten by a stale database payload before the caller commits it.
    """
    need(isinstance(task, Task) and isinstance(run, DataRun) and isinstance(gap, DataGap)
        and task.workspace_id == run.workspace_id == gap.workspace_id == p.workspace_id)
    adopted = _binding(db, p, task)
    if adopted is None:
        need(KEY not in gap.data)
        return False
    release, binding = adopted
    expected = _expected(db, p, task, run, gap, current, release, binding)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, Audit.object_id == gap.id).limit(2)))
    record = gap.data.get(KEY)
    if record is None and gap.status in {'resolved', 'cancelled'}:
        need(not events)
        return True  # No open need to hand off; preserve the real terminal Gap.
    if record is None:
        need(not events)
        record = {**expected, 'actor_id': p.user_id}
        record['record_hash'] = digest(record); _bytes(record, MAX_BYTES)
        retained_acquisition = bool(gap.data.get('acquisition_ids')) \
            or gap.status in {'awaiting_observation', 'unknown_action'} or _has_plan_or_acquisition(db, p, gap)
        patch = {} if retained_acquisition else {'decision_reason': WAIT_REASON}
        gap.data = {**gap.data, KEY: copy.deepcopy(record), **patch}
        gap.version += 1
        audit(db, p, ACTION, gap.id, copy.deepcopy(record))
    else:
        _bytes(record, MAX_BYTES)
        need(type(record) is dict and set(record) == {*expected, 'actor_id', 'record_hash'}
            and all(_same(record[key], value) for key, value in expected.items())
            and record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'})
            and len(events) == 1 and events[0].actor_id == record['actor_id'] and _same(events[0].data, record))
    if current['status'] == 'conflict' and gap.data.get('kind') == 'conflict' \
            and not gap.data['missing_fields'] and not gap.data.get('acquisition_ids') \
            and gap.status not in {'resolved', 'cancelled', 'awaiting_observation', 'unknown_action'} \
            and not _has_plan_or_acquisition(db, p, gap):
        if gap.status != 'needs_review' or gap.data.get('decision_reason') != CONFLICT_REASON:
            gap.status = 'needs_review'; gap.version += 1
            gap.data = {**gap.data, 'decision_reason': CONFLICT_REASON}
            from .data_gaps import record as gap_record
            gap_record(db, p, gap, 'decision_review_required', query_id=run.id,
                result_status='conflict', query_result_hash=digest(current), reason=CONFLICT_REASON)
    # Use the actual query already evaluated by this Goal check and its real
    # receiver Events; never create a fresh query merely to resolve the Gap.
    if current['status'] == 'sufficient' and gap.status not in {'resolved', 'cancelled'}:
        from .data_gaps import receipts_ready, record as gap_record
        if receipts_ready(db, p, current):
            gap.status = 'resolved'; gap.version += 1
            gap.data = {**gap.data, 'resolved_at': now().isoformat(),
                'resolution_reason': '原目标当前资料充分，所用观测已由数据管理独立接收；未由本协调新增采集。'}
            gap_record(db, p, gap, 'verified', query_id=run.id, result_status='sufficient',
                reason=gap.data['resolution_reason'])
    return True
