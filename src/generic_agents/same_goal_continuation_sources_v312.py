"""Authenticated original advice and current planning metadata, no dispatch.

Historical custody does not renew price, Goal freshness, models or execution.
The mutable permission/catalogue view never participates in the source hash.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import select

from . import same_goal_continuation_contracts_v312 as c
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_execution_v308 as execution
from . import same_goal_advice_runtime_v308 as runtime
from . import same_goal_advice_review_v308 as reviews
from . import same_goal_advice_source_scope_v309 as source_scope
from . import native_goal_tools
from . import native_goals
from .contracts import digest
from .goals import snapshot
from .models import Capability, Membership, Plan, User
from .relations import lock_current_actor
from .security import require
from .services import published

MAX_ASSIGNEES = 1000
MAX_TOOLS = 1000
ANALYSIS_ADAPTERS = frozenset({'same_goal_advice_v302', 'business_goal_ready_v288',
    'business_decision_v284', 'business_decision_v276', 'business_reasoning'})
need = store.need


def _ref(event): return {'id': event.id, 'record_hash': digest(event.data)}


def _original(db, p, task, plan):
    prep, prepare_job = store._saved(db, p, task, plan)
    result = runtime.cold_result(db, p, task, plan)
    reviewed = reviews.checked(db, p, task, plan, prep)
    need(plan.status == 'reviewed' and len(reviewed) == 1)
    review = reviewed[0]
    started = execution.checked(db, p, task, plan, prep, prepare_job)
    need(started is not None and len(started['calls']) == 1)
    sdk = started['calls'][0]
    need(review['result_hash'] == digest(result)
        and result['plan_ref']['id'] == plan.id
        and result['plan_ref']['record_hash'] == started['snapshot']['record_hash'])
    # The Start's protected preview is authoritative, not a fresh/latest choice.
    preview = started['preview']; original = preview.data
    need(started['start'].data['preview_ref'] == {'id': preview.id, 'record_hash': original['record_hash']}
        and digest(started['snapshot']['preview']) == digest(original['preview_record']))
    cloud = started['snapshot']['preview']['cloud_request']
    frame = original['frame_snapshot']; decision = frame['decision_input']
    need(digest(cloud) == original['preview_record']['cloud_request_hash']
        and decision['decision_intent'] == cloud['decision_input']['decision_intent']
        and digest(result['model_value']) == digest(sdk.data['output']['model_value']))
    state = sdk.data[runtime.STATE_KEY]
    need(state['capture'] is not None and state['request']['execution_snapshot'] == started['snapshot'])
    coverage = source_scope.record(cloud, result['model_value'], capture_sha256=state['capture']['raw_sha256'])
    return prep, result, review, started, decision, coverage


def _source(db, p, task, plan):
    prep, result, review, started, decision, coverage = _original(db, p, task, plan)
    selected = store.provider.binding(db, p, task)
    need(selected is not None and selected['release'].id == prep['scope']['release_id'])
    goal = snapshot(task)
    need(goal['hash'] == prep['scope']['goal_hash'] and goal['version'] == prep['scope']['goal_version']
        and digest(goal) == prep['scope']['goal_snapshot_hash'])
    graph = selected['binding']['graph']; graph_hash = digest(graph)
    need(task.data['graph_runtime']['graph_hash'] == graph_hash)
    limitations = ['本阶段仅保存后续办理草案；尚未绑定原业务执行图。',
        '原建议和人工审核不授予执行、派发、正式结论写入或目标验收权限。',
        '来源审阅只针对实际提供的文字，不证明整份原文或现场状态已核验。']
    if any(item['review_scope'] == 'provided_excerpt_only' for item in coverage['sources']):
        limitations.append('部分来源仅提供摘录；拟办范围不能扩大为全文已核验或完整业务事实。')
    public_review = {key: copy.deepcopy(review[key]) for key in
        ('decision', 'reason', 'actor_id', 'reviewed_at', 'review_ref')}
    source = {'schema': c.SOURCE, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'advice_plan_id': plan.id, 'origin_kind': 'independent_advice', 'bridge_state': 'not_bound',
        'goal_hash': goal['hash'], 'goal_version': goal['version'], 'goal_snapshot_hash': digest(goal),
        'release_id': selected['release'].id, 'original_graph_hash': graph_hash,
        'preparation_snapshot_hash': prep['record_hash'],
        'result_ref': {'id': started['calls'][0].id, 'record_hash': digest(result)},
        'review_ref': copy.deepcopy(review['review_ref']), 'review': public_review,
        'start_ref': _ref(started['start']),
        'preview_ref': {'id': started['preview'].id, 'record_hash': started['preview'].data['record_hash']},
        'frame_hash': digest(started['preview'].data['frame_snapshot']),
        'cloud_input_hash': digest(started['snapshot']['preview']['cloud_request']),
        'model_pin': copy.deepcopy(prep['model_pin']), 'decision_intent': decision['decision_intent'],
        'review_decision': review['decision'], 'data_as_of': prep['evaluation_cut'],
        'recommendation': copy.deepcopy(result['model_value']['recommendation']),
        'source_review_scope': coverage, 'limitations': limitations, **c.FLAGS}
    c.advice.base._json(source)
    candidate_reviews = {item['candidate_ref']: item for item in result['model_value']['candidate_reviews']}
    need(len(candidate_reviews) == len(result['model_value']['candidate_reviews'])
        and set(candidate_reviews) == {item['candidate_ref'] for item in decision['candidates']})
    candidates = [{'candidate_ref': item['candidate_ref'], 'proposal': item['proposal'],
        'model_review': copy.deepcopy(candidate_reviews[item['candidate_ref']])} for item in decision['candidates']]
    return prep, result, review, source, selected, candidates


def _tool(cap, pin=None):
    actual = store._pin(cap)
    need(cap.kind in {'tool', 'specialist_model'} and cap.content_hash == cap.validated_hash == digest(cap.data)
        and (pin is None or actual == pin))
    analysis_only = cap.data['execution'].get('adapter') in ANALYSIS_ADAPTERS
    return {**actual, 'name': cap.data['name'], 'kind': cap.kind,
        'planning_available': not analysis_only,
        'blocked_reason': '该能力只产生分析意见，不能作为本阶段的办理动作' if analysis_only else None}


def _tools(db, p, selected):
    release, business, graph = selected['release'], selected['business'], selected['binding']['graph']
    entries = native_goal_tools.catalog(db, p, release, business)
    need(type(entries) is dict and len(entries) <= MAX_TOOLS)
    result = {}
    for identity, entry in entries.items():
        cap = entry['capability']; actual = published(db, p, identity)
        need(actual.id == cap.id and store._pin(actual) == store._pin(cap))
        result[identity] = _tool(actual)
    # Fixed released graph abilities are explicit authority metadata too; a
    # catalogue failure never falls back to name-based or arbitrary tools.
    for node in graph['nodes']:
        if node['kind'] != 'ability': continue
        pin = node['execution']; actual = published(db, p, pin['capability_id'])
        item = _tool(actual, {'id': pin['capability_id'], 'content_hash': pin['capability_hash'],
            'release_version': pin['release_version']})
        if actual.id in result: need(digest(result[actual.id]) == digest(item))
        result[actual.id] = item
    need(len(result) <= MAX_TOOLS)
    return sorted(result.values(), key=lambda item: (item['name'], item['id']))


def _assignees(db, p):
    rows = list(db.execute(select(User, Membership).join(Membership, Membership.user_id == User.id).where(
        User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator'])).order_by(User.username, User.id).limit(MAX_ASSIGNEES + 1)))
    need(len(rows) <= MAX_ASSIGNEES)
    values = [{'id': user.id, 'name': user.username, 'role': member.role} for user, member in rows]
    need(len({item['id'] for item in values}) == len(values))
    return values


def _planning_guard(db, p, task, *, lock_sources):
    require(p, 'admin', 'operator')
    need(task.status not in {'workflow_cancelled', 'cancelled', 'completed', 'accepted', 'closed'}
        and not task.data.get('native_goal_acceptance') and not task.data.get('native_goal_cancelled'))
    native_goals.guard(db, p, task, lock_sources=lock_sources)


def _available(blocks, code, message, read):
    try: return read()
    except HTTPException as error:
        if error.status_code not in {403, 404, 409, 422}: raise
        blocks.append({'code': code, 'message': message})
        return None


@store.checked
def load(db, p, task_id, advice_id, *, write=False, lock=False):
    need(type(write) is bool and type(lock) is bool)
    with db.no_autoflush:
        store._reader(db, p, write=write)
        task = store.existing._task(db, p, task_id, lock=lock or write)
        if write: lock_current_actor(db, p)
        plan = store._fresh(db, p, Plan, store.existing._id(advice_id))
        need(plan.task_id == task.id)
        prep, result, review, source, selected, candidates = _source(db, p, task, plan)
        blocks = []
        if write:
            _planning_guard(db, p, task, lock_sources=True)
            active = True
        else:
            def check_current():
                _planning_guard(db, p, task, lock_sources=False)
                return True
            active = _available(blocks, 'goal_not_current', '当前岗位、目标或发布配置不允许新建办理草案；原来源仍保留', check_current) is True
        rejected = review['decision'] == 'rejected'
        if rejected: blocks.append({'code': 'advice_rejected', 'message': '原建议已被拒绝，不能据此新建办理方案'})
        if review['decision'] == 'needs_revision':
            blocks.append({'code': 'revision_required', 'message': '原建议需要补充或修订；此处仅拟定新稿，不执行原建议'})
        tools = _available(blocks, 'tools_unavailable', '原发布版工具目录暂不能完整核对，未提供替代工具',
            lambda: _tools(db, p, selected))
        assignees = _available(blocks, 'assignees_unavailable', '当前有效岗位清单暂不能核对，未使用虚构负责人',
            lambda: _assignees(db, p))
        writer = p.role in {'admin', 'operator'}
        can_preview = bool(writer and active and not rejected)
        can_create = bool(can_preview and (p.role == 'admin' or p.user_id == prep['business_actor_id']))
        if writer and not can_create and not rejected and active:
            blocks.append({'code': 'origin_business_role_required', 'message': '新建须由原业务岗位或管理员办理；被指派人只能修改自己已有的草案'})
        public = {'schema': c.CONTEXT, 'task_id': task.id, 'advice_plan_id': plan.id,
            'task_version': task.version, 'source_hash': digest(source), 'source': copy.deepcopy(source),
            'candidates': candidates, 'allowed_routes': [] if rejected else [source['decision_intent']],
            'tools': tools or [], 'assignees': assignees or [],
            'permissions': {'can_preview': can_preview, 'can_create': can_create},
            'blocked_reasons': blocks, **c.FLAGS}
        return {'task': task, 'advice_plan': plan, 'prep': prep, 'result': result, 'review': review,
            'source': source, 'source_hash': digest(source), 'business_actor_id': prep['business_actor_id'],
            'context': public}


def context(db, p, task_id, advice_id, *, write=False, lock=False):
    return load(db, p, task_id, advice_id, write=write, lock=lock)['context']
