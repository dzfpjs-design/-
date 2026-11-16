"""Human continuation drafts over a real reviewed answer; never execution.

Only this family's Plan and protected Audits are written. Original Task, Goal,
Q0, graph, paid answer and their jobs/calls remain owned by their original
services. Preview is read-only. An original nonce is checked before live
source checks, so expiry cannot turn a saved command into a new application.
"""
import copy
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import same_goal_continuation_contracts_v312 as c
from . import same_goal_continuation_sources_v312 as sources
from . import goal_evaluation_append_store_v297 as existing
from .contracts import digest
from .models import Audit, Membership, Plan, User, uid
from .relations import lock_current_actor
from .services import published
from .temporal import instant
from . import workspace_command_nonce_lock_v312 as command_nonce

MAX_ROWS = 1000
MAX_PLANS = 100
MAX_REVISIONS = 100
need, checked = existing.need, existing.checked
_ACTION = {'create': c.ACTION_SAVE, 'revise': c.ACTION_REVISE, 'withdraw': c.ACTION_WITHDRAW}
_MODELS = {'create': c.SaveRequest, 'revise': c.ReviseRequest, 'withdraw': c.WithdrawRequest}
_OLD_ACTIONS = frozenset({'same_goal_advice.preparation_accepted_v302',
    'same_goal_advice.preparation_cancelled_v302', 'same_goal_advice.preview_prepared_v307',
    'same_goal_advice.started_v308', 'same_goal_advice.human_reviewed_v308',
    'same_goal_advice.task_price_registered_v310', 'same_goal_advice.task_quota_configured_v310'})
_COMMAND_FIELDS = {'schema', 'workspace_id', 'task_id', 'advice_plan_id', 'continuation_id',
    'operation', 'actor_id', 'actor_role', 'request_id', 'request', 'body_hash', 'source',
    'source_hash', 'business_actor_id', 'revision', 'preview', 'version_before', 'version_after',
    'phase', 'record_hash'}
_PLAN_FIELDS = {'schema', 'source', 'source_hash', 'business_actor_id', 'origin_actor_id', 'revisions', 'operations'}
_REVISION_FIELDS = {'schema', 'continuation_id', 'version', 'source_hash', 'previous_hash',
    'actor_id', 'request_id', 'draft', 'reason', 'saved_at', 'record_hash'}
_PREVIEW_FIELDS = {'schema', 'task_id', 'advice_plan_id', 'task_version', 'source_hash',
    'preview_hash', 'normalized_draft', 'missing_fields', 'source_refs', *c.FLAGS}


def _rows(db, p, model, *where, lock=False):
    query = select(model).where(model.workspace_id == p.workspace_id, *where).limit(MAX_ROWS + 1)
    if lock: query = query.with_for_update()
    rows = list(db.scalars(query.execution_options(populate_existing=True)))
    need(len(rows) <= MAX_ROWS, '原办理草案库存超过完整核对上限，历史保留，未创建新申请')
    return rows


def _reader(db, p, *, write=False):
    existing._reader(db, p, write=write)


def _time(value):
    return instant(value.isoformat() if isinstance(value, datetime) else value)


def _source(value, p, task_id, advice_id):
    need(type(value) is dict and value.get('schema') == c.SOURCE
        and value.get('workspace_id') == p.workspace_id and value.get('task_id') == task_id
        and value.get('advice_plan_id') == advice_id and value.get('origin_kind') == 'independent_advice'
        and value.get('bridge_state') == 'not_bound'
        and value.get('review_decision') in {'accepted', 'needs_revision'},
        '原建议来源、已保存审核或固定业务范围无法核对；旧草案保留')
    return copy.deepcopy(value)


def _ref(value):
    return {'advice_plan_id': value['advice_plan_id'], 'source_hash': digest(value)}


def _command(event, p):
    value = event.data
    need(type(value) is dict and set(value) == _COMMAND_FIELDS and value['schema'] == c.COMMAND
        and value['operation'] in _ACTION and event.action == _ACTION[value['operation']]
        and event.workspace_id == value['workspace_id'] == p.workspace_id
        and event.object_id == value['continuation_id'] and event.actor_id == value['actor_id']
        and value['actor_role'] in {'admin', 'operator'} and value['record_hash'] == c.record_hash(value))
    for name in ('task_id', 'advice_plan_id', 'continuation_id', 'actor_id', 'business_actor_id', 'request_id'):
        existing._id(value[name])
    body = c.dump(_MODELS[value['operation']], value['request'])
    need(digest(body) == digest(value['request']) and body['request_id'] == value['request_id']
        and value['body_hash'] == digest(body))
    source = _source(value['source'], p, value['task_id'], value['advice_plan_id'])
    need(value['source_hash'] == digest(source) and type(value['version_before']) is int
        and type(value['version_after']) is int and value['version_before'] >= 0
        and value['version_after'] == value['version_before'] + 1)
    if value['operation'] == 'create':
        need(value['version_before'] == 0 and value['phase'] == 'draft'
            and (value['actor_role'] == 'admin' or value['actor_id'] == value['business_actor_id']))
    else:
        need(body['expected_continuation_version'] == value['version_before']
            and value['phase'] == ('withdrawn' if value['operation'] == 'withdraw' else 'draft'))
    if value['operation'] != 'withdraw':
        need(body['source_hash'] == value['source_hash'])
        preview = value['preview']
        need(type(preview) is dict and set(preview) == _PREVIEW_FIELDS and preview['schema'] == c.PREVIEW
            and preview['task_id'] == value['task_id'] and preview['advice_plan_id'] == value['advice_plan_id']
            and type(preview['task_version']) is int and preview['task_version'] == body['expected_task_version']
            and preview['source_hash'] == value['source_hash'] and digest(preview['source_refs']) == digest(source)
            and digest(preview['normalized_draft']) == digest(c.draft(body['draft']))
            and all(preview[key] is False for key in c.FLAGS)
            and preview['preview_hash'] == body['preview_hash'] == digest({key: item for key, item in preview.items() if key != 'preview_hash'}))
    else: need(value['preview'] is None)
    _time(event.created_at)
    return copy.deepcopy(value)


def _nonce(db, p, request_id):
    """Positive reverse inventory; a missing Audit never means a free nonce."""
    request_id = existing._id(request_id)
    events = _rows(db, p, Audit, Audit.action.in_(c.ACTIONS | _OLD_ACTIONS), or_(
        Audit.data['request_id'].as_string() == request_id,
        Audit.data['request']['request_id'].as_string() == request_id,
        Audit.data['body']['request_id'].as_string() == request_id))
    pointers = _rows(db, p, Plan, or_(
        Plan.data['operations'].contains([{'request_id': request_id}]),
        Plan.data['revisions'].contains([{'request_id': request_id}])))
    need(len(events) <= 1, '原请求编号有重复或跨操作回执，未复用或替换')
    if not events:
        need(not pointers, '原请求仍有草案正文但保护回执缺失，未推断为未保存')
        return None
    event = events[0]
    need(event.action in c.ACTIONS and all(row.data.get('schema') == c.PLAN for row in pointers),
        '此请求编号已用于原业务分析或另一类申请，不能改作办理草案')
    value = _command(event, p)
    need(len(pointers) <= 1 and all(row.id == value['continuation_id'] for row in pointers),
        '原请求编号对应不同草案，未创建替代申请')
    return event, value


def _revision(raw, value, event, previous):
    need(type(raw) is dict and set(raw) == _REVISION_FIELDS and raw['schema'] == c.REVISION
        and raw['continuation_id'] == value['continuation_id'] and raw['source_hash'] == value['source_hash']
        and type(raw['version']) is int and raw['version'] == value['version_after']
        and raw['previous_hash'] == (previous['record_hash'] if previous else None)
        and raw['actor_id'] == value['actor_id'] and raw['request_id'] == value['request_id']
        and raw['reason'] == value['request']['reason'] and raw['record_hash'] == c.record_hash(raw)
        and digest(raw['draft']) == digest(c.draft(value['request']['draft']))
        and c.has_human_content(raw['draft']) and _time(raw['saved_at']) == _time(event.created_at))
    return copy.deepcopy(raw)


def _saved(plan, events, p, task):
    data = plan.data
    need(plan.workspace_id == p.workspace_id and plan.task_id == task.id and type(data) is dict
        and set(data) == _PLAN_FIELDS and data['schema'] == c.PLAN
        and plan.approved_by is None and plan.approved_hash is None
        and plan.status in {'draft', 'withdrawn'} and type(plan.version) is int
        and type(data['revisions']) is list and 1 <= len(data['revisions']) <= MAX_REVISIONS
        and type(data['operations']) is list and 1 <= len(data['operations']) <= MAX_REVISIONS + 1)
    source = _source(data['source'], p, task.id, data['source']['advice_plan_id'])
    need(data['source_hash'] == digest(source));existing._id(data['origin_actor_id']);existing._id(data['business_actor_id'])
    by_id = {event.id: event for event in events};need(len(by_id) == len(events) == len(data['operations']))
    previous = None;revisions = [];version = 0;phase = 'draft';nonces = set();last_at = None
    for pointer in data['operations']:
        need(type(pointer) is dict and set(pointer) == {'id', 'record_hash', 'request_id'} and pointer['id'] in by_id)
        event = by_id[pointer['id']];value = _command(event, p)
        need(pointer == {'id': event.id, 'record_hash': digest(value), 'request_id': value['request_id']}
            and value['task_id'] == task.id and value['advice_plan_id'] == source['advice_plan_id']
            and value['continuation_id'] == plan.id and value['source_hash'] == data['source_hash']
            and digest(value['source']) == digest(source) and value['business_actor_id'] == data['business_actor_id']
            and value['version_before'] == version and value['request_id'] not in nonces and phase == 'draft'
            and (last_at is None or _time(event.created_at) >= last_at))
        nonces.add(value['request_id']);last_at = _time(event.created_at)
        if version == 0:
            need(value['operation'] == 'create' and data['origin_actor_id'] == value['actor_id']
                and _time(plan.created_at) == last_at)
        elif value['operation'] == 'withdraw':
            need(value['actor_role'] == 'admin' or value['actor_id'] == data['origin_actor_id'])
            need(digest(value['revision']) == digest(previous))
        else:
            need(value['operation'] == 'revise' and (value['actor_role'] == 'admin'
                or value['actor_id'] == data['business_actor_id']
                or previous['draft']['assignee_id'] == value['actor_id'] == value['request']['draft']['assignee_id']))
        if value['operation'] != 'withdraw':
            previous = _revision(value['revision'], value, event, previous);revisions.append(previous)
        version = value['version_after'];phase = value['phase']
    need(version == plan.version and phase == plan.status and digest(revisions) == digest(data['revisions']))
    return {'plan': plan, 'source': source, 'revision': previous, 'events': by_id}


@checked
def _inventory(db, p, task, advice_id):
    """Close both Plan and protected-command inventories, including orphans."""
    scope = or_((Plan.task_id == task.id) & (Plan.data['schema'].as_string() == c.PLAN),
        Plan.data['source']['task_id'].as_string() == task.id,
        Plan.data['source']['advice_plan_id'].as_string() == advice_id)
    plans = _rows(db, p, Plan, scope)
    events = _rows(db, p, Audit, Audit.action.in_(c.ACTIONS), or_(
        Audit.data['task_id'].as_string() == task.id, Audit.data['advice_plan_id'].as_string() == advice_id,
        Audit.data['source']['task_id'].as_string() == task.id, Audit.object_id.in_([row.id for row in plans])))
    commands = [_command(event, p) for event in events]
    need(all(value['task_id'] == task.id for value in commands))
    plans = _rows(db, p, Plan, or_(scope, Plan.id.in_([value['continuation_id'] for value in commands])))
    need(len(plans) <= MAX_PLANS and len({row.id for row in plans}) == len(plans)
        and {value['continuation_id'] for value in commands} == {row.id for row in plans},
        '原草案或保护回执有缺失、孤儿或跨任务记录，未另建申请')
    nonces = [value['request_id'] for value in commands];need(len(nonces) == len(set(nonces)))
    return [_saved(plan, [event for event in events if event.object_id == plan.id], p, task) for plan in plans]


def _assignee(db, p, identity):
    found = db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == identity, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator'])).with_for_update(read=True, of=[User, Membership]))
    need(found == identity, '所选负责人当前未启用或不属于本工作区，请保留草案后重新选择')


def _normalize(db, p, loaded, raw):
    value = c.draft(raw);context = loaded['context']
    need(context['task_id'] == loaded['task'].id and context['advice_plan_id'] == loaded['advice_plan'].id
        and context['source_hash'] == loaded['source_hash'] == digest(loaded['source'])
        and digest(context['source']) == digest(loaded['source']))
    if value['route'] is not None:
        need(value['route'] in context['allowed_routes'], '办理方式不属于原建议允许的范围，未升级为其他业务动作')
    if value['candidate_ref'] is not None:
        need(sum(item['candidate_ref'] == value['candidate_ref'] for item in context['candidates']) == 1,
            '所选建议不属于原回答，请保留正文并重新核对')
    for pin in value['selected_tools']:
        matches = [item for item in context['tools'] if item['id'] == pin['id']]
        need(len(matches) == 1 and matches[0]['planning_available'] is True
            and {key: matches[0][key] for key in ('id', 'content_hash', 'release_version')} == pin,
            '所选工具不属于原发布版本可用于计划的能力；未添加替代工具')
        cap = published(db, p, pin['id'])
        need(cap.kind in {'tool', 'specialist_model'} and cap.content_hash == cap.validated_hash == digest(cap.data) == pin['content_hash']
            and cap.release_version == pin['release_version'])
    if value['assignee_id'] is not None:
        need(sum(item['id'] == value['assignee_id'] for item in context['assignees']) == 1,
            '所选负责人不在当前可用名单，请保留草案后重新选择')
        _assignee(db, p, value['assignee_id'])
    need(c.has_human_content(value), '请至少填写一项实际办理内容，未创建空白草案')
    return value


def _missing(value, context):
    labels = {'route': '办理方式', 'purpose': '办理目的', 'work_description': '具体工作',
        'expected_result': '期望结果', 'assignee_id': '负责人'}
    result = [{'field': field, 'message': '尚未填写' + label} for field, label in labels.items()
        if value[field] is None or isinstance(value[field], str) and not value[field].strip()]
    if context['candidates'] and value['candidate_ref'] is None:
        result.append({'field': 'candidate_ref', 'message': '尚未选择对应的原建议'})
    return result


def _preview(db, p, loaded, body):
    task, advice = loaded['task'], loaded['advice_plan']
    source = _source(loaded['source'], p, task.id, advice.id)
    need(task.version == body['expected_task_version'] and body['source_hash'] == loaded['source_hash'] == digest(source),
        '原任务版本或建议来源已变化；草案正文保留，请重新核对预览')
    value = _normalize(db, p, loaded, body['draft'])
    result = {'schema': c.PREVIEW, 'task_id': task.id, 'advice_plan_id': advice.id,
        'task_version': task.version, 'source_hash': loaded['source_hash'], 'normalized_draft': value,
        'missing_fields': _missing(value, loaded['context']), 'source_refs': source, **c.FLAGS}
    result['preview_hash'] = digest(result)
    return result


def _view(saved, p):
    plan, revision = saved['plan'], saved['revision'];data = plan.data
    can_revise = p.role == 'admin' or p.role == 'operator' and p.user_id in {data['business_actor_id'], revision['draft']['assignee_id']}
    can_withdraw = p.role == 'admin' or p.role == 'operator' and p.user_id == data['origin_actor_id']
    def item(value, *, current=False):
        result = {key: copy.deepcopy(value[key]) for key in ('version', 'record_hash', 'actor_id', 'saved_at', 'reason')}
        result['draft'] = copy.deepcopy(value['draft'])
        if not current: result['previous_hash'] = value['previous_hash']
        return result
    return {'schema': c.VIEW, 'task_id': plan.task_id, 'advice_plan_id': saved['source']['advice_plan_id'],
        'continuation_id': plan.id, 'version': plan.version, 'phase': plan.status,
        'source_hash': data['source_hash'], 'source_ref': _ref(saved['source']),
        'revision': item(revision, current=True), 'history': [item(value) for value in data['revisions']],
        'permissions': {'can_revise': plan.status == 'draft' and can_revise,
            'can_withdraw': plan.status == 'draft' and can_withdraw}, **c.FLAGS}


def _find(inventory, continuation_id, advice_id):
    identity = existing._id(continuation_id)
    found = [saved for saved in inventory if saved['plan'].id == identity and saved['source']['advice_plan_id'] == advice_id]
    if not found: raise HTTPException(404, '此办理草案不存在或不属于当前任务和建议')
    need(len(found) == 1);return found[0]


def _replay(original, p, task, advice_id, operation, body, continuation_id=None):
    event, value = original
    need(value['task_id'] == task.id and value['advice_plan_id'] == advice_id
        and value['operation'] == operation and value['actor_id'] == p.user_id
        and value['body_hash'] == digest(body)
        and (continuation_id is None or value['continuation_id'] == continuation_id),
        '原编号不能更换任务、建议、草案、操作、提交者或完整正文')
    return value['continuation_id']


@checked
def preview(db, p, task_id, advice_id, body):
    body = c.dump(c.PreviewRequest, body)
    with db.no_autoflush:
        _reader(db, p, write=True)
        loaded = sources.load(db, p, existing._id(task_id), existing._id(advice_id), write=True, lock=False)
        return _preview(db, p, loaded, body)


def _write(db, p, task_id, advice_id, body, operation, continuation_id=None):
    body = c.dump(_MODELS[operation], body);advice_id = existing._id(advice_id)
    with db.no_autoflush:
        _reader(db, p, write=True)
        command_nonce.acquire(db,p,action=_ACTION[operation],task_id=task_id,body=body,
            subject_id=continuation_id,advice_id=advice_id)
        task = existing._task(db, p, task_id, lock=True);lock_current_actor(db, p)
        original = _nonce(db, p, body['request_id'])
        if original:
            identity = _replay(original, p, task, advice_id, operation, body, continuation_id)
            return _view(_find(_inventory(db, p, task, advice_id), identity, advice_id), p)
        inventory = _inventory(db, p, task, advice_id)
        saved = None if operation == 'create' else _find(inventory, continuation_id, advice_id)
        if saved:
            plan = saved['plan']
            # The Task serializes this family's writers before its Plan lock.
            actual = _rows(db, p, Plan, Plan.id == plan.id, lock=True);need(len(actual) == 1)
            need(digest(actual[0].data) == digest(plan.data) and actual[0].version == plan.version)
            need(plan.status == 'draft' and plan.version == body['expected_continuation_version'],
                '草案已由其他人修改或撤回；旧版本与当前正文均保留')
            permissions = _view(saved, p)['permissions']
            need(permissions['can_withdraw' if operation == 'withdraw' else 'can_revise'],
                '当前账号不能修改或撤回这份草案；未覆盖他人的工作')
        else: need(len(inventory) < MAX_PLANS, '本任务办理草案达到保存上限，历史保留')
        loaded = None;prepared = None
        if operation != 'withdraw':
            loaded = sources.load(db, p, task.id, advice_id, write=True, lock=True)
            need(loaded['task'].id == task.id and loaded['task'].workspace_id == p.workspace_id)
            need(p.role == 'admin' or operation == 'revise' or p.user_id == loaded['business_actor_id'],
                '只有原业务负责人或管理员可创建办理草案')
            prepared = _preview(db, p, loaded, body)
            need(body['preview_hash'] == prepared['preview_hash'], '预览与本次完整草案不一致；正文保留，未保存替代方案')
            if saved:
                need(loaded['source_hash'] == saved['plan'].data['source_hash']
                    and digest(loaded['source']) == digest(saved['source'])
                    and loaded['business_actor_id'] == saved['plan'].data['business_actor_id'],
                    '原建议、审核或岗位已变化；旧草案来源不改写，请核查后另建对应方案')
                if p.role != 'admin' and p.user_id != loaded['business_actor_id']:
                    need(saved['revision']['draft']['assignee_id'] == body['draft']['assignee_id'] == p.user_id,
                        '负责人只能修改自己负责的草案，不能转派给其他人')
        clock = db.scalar(select(func.clock_timestamp()));need(type(clock) is datetime and clock.tzinfo is not None)
        event_id = uid();plan = saved['plan'] if saved else Plan(id=uid(), workspace_id=p.workspace_id,
            task_id=task.id, status='draft', version=1, created_at=clock, approved_by=None, approved_hash=None)
        source = saved['source'] if saved else loaded['source'];source_hash = digest(source)
        before = plan.version if saved else 0;after = before + 1
        previous = saved['revision'] if saved else None
        revision = previous
        if operation != 'withdraw':
            need(not saved or len(plan.data['revisions']) < MAX_REVISIONS, '本草案修订版本达到保存上限，历史保留')
            revision = {'schema': c.REVISION, 'continuation_id': plan.id, 'version': after, 'source_hash': source_hash,
                'previous_hash': previous['record_hash'] if previous else None, 'actor_id': p.user_id,
                'request_id': body['request_id'], 'draft': copy.deepcopy(prepared['normalized_draft']),
                'reason': body['reason'], 'saved_at': clock.isoformat()}
            revision['record_hash'] = c.record_hash(revision)
        value = {'schema': c.COMMAND, 'workspace_id': p.workspace_id, 'task_id': task.id,
            'advice_plan_id': advice_id, 'continuation_id': plan.id, 'operation': operation,
            'actor_id': p.user_id, 'actor_role': p.role, 'request_id': body['request_id'], 'request': body,
            'body_hash': digest(body), 'source': copy.deepcopy(source), 'source_hash': source_hash,
            'business_actor_id': plan.data['business_actor_id'] if saved else loaded['business_actor_id'],
            'revision': copy.deepcopy(revision), 'preview': prepared, 'version_before': before,
            'version_after': after, 'phase': 'withdrawn' if operation == 'withdraw' else 'draft'}
        value['record_hash'] = c.record_hash(value)
        pointer = {'id': event_id, 'record_hash': digest(value), 'request_id': body['request_id']}
        event = Audit(id=event_id, workspace_id=p.workspace_id, actor_id=p.user_id,
            action=_ACTION[operation], object_id=plan.id, created_at=clock, data=value)
        with db.begin_nested():
            plan.data = {'schema': c.PLAN, 'source': copy.deepcopy(source), 'source_hash': source_hash,
                'business_actor_id': value['business_actor_id'], 'origin_actor_id': plan.data['origin_actor_id'] if saved else p.user_id,
                'revisions': [*copy.deepcopy(plan.data['revisions']), revision] if saved and operation != 'withdraw'
                    else copy.deepcopy(plan.data['revisions']) if saved else [revision],
                'operations': [*copy.deepcopy(plan.data['operations']), pointer] if saved else [pointer]}
            plan.version = after;plan.status = value['phase']
            if not saved: db.add(plan)
            db.add(event);db.flush()
            return _view(_find(_inventory(db, p, task, advice_id), plan.id, advice_id), p)


@checked
def create(db, p, task_id, advice_id, body):
    return _write(db, p, task_id, advice_id, body, 'create')


@checked
def revise(db, p, task_id, advice_id, continuation_id, body):
    return _write(db, p, task_id, advice_id, body, 'revise', existing._id(continuation_id))


@checked
def withdraw(db, p, task_id, advice_id, continuation_id, body):
    return _write(db, p, task_id, advice_id, body, 'withdraw', existing._id(continuation_id))


@checked
def get(db, p, task_id, advice_id, continuation_id):
    with db.no_autoflush:
        _reader(db, p);task = existing._task(db, p, task_id);advice_id = existing._id(advice_id)
        return _view(_find(_inventory(db, p, task, advice_id), continuation_id, advice_id), p)


@checked
def listing(db, p, task_id, advice_id):
    with db.no_autoflush:
        _reader(db, p);task = existing._task(db, p, task_id);advice_id = existing._id(advice_id)
        return {'schema': c.VIEW + '/list', 'task_id': task.id, 'advice_plan_id': advice_id,
            'records': [_view(saved, p) for saved in _inventory(db, p, task, advice_id)
                if saved['source']['advice_plan_id'] == advice_id]}


@checked
def get_operation(db, p, task_id, advice_id, request_id):
    with db.no_autoflush:
        _reader(db, p);task = existing._task(db, p, task_id);advice_id = existing._id(advice_id)
        original = _nonce(db, p, request_id)
        if original is None: raise HTTPException(404, '尚未查到原申请，请保留原编号和完整正文核对')
        event, value = original
        need(value['task_id'] == task.id and value['advice_plan_id'] == advice_id,
            '原请求不属于当前任务和业务建议，未作为本申请恢复')
        status = 'available'
        try: result = _view(_find(_inventory(db, p, task, advice_id), value['continuation_id'], advice_id), p)
        except HTTPException as error:
            if error.status_code not in {404, 409, 410}: raise
            result = None;status = 'needs_check'
        return {'schema': c.RECEIPT, 'found': True, 'operation': value['operation'], 'task_id': task.id,
            'advice_plan_id': advice_id, 'continuation_id': value['continuation_id'],
            'request_id': value['request_id'], 'actor_id': value['actor_id'], 'body_hash': value['body_hash'],
            'result': result, 'result_status': status}
