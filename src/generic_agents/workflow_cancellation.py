"""Durable identity for optional tracked workflow cancellation requests.

Readback never cancels, requeues, infers success from another actor's action, or
manufactures a journal for a legacy cancellation. The Task transaction owns all
changes; no schema migration or separate control engine is needed.
"""
import copy
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, field_validator

from .contracts import Contract, digest
from .models import now
from .services import require


KEY = 'workflow_cancel_commands'
HASH = 'workflow_cancel_commands_hash'
LIMIT = 100
CANCELLABLE = {'workflow_waiting', 'workflow_pending', 'workflow_queued', 'workflow_running',
               'workflow_failed', 'workflow_blocked'}


def uuid_value(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('取消请求编号须为标准小写 UUID') from None
    return value


class Cancel(Contract):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)
    retained_note: str = Field(default='', max_length=5000)
    request_id: str | None = None

    @field_validator('request_id')
    @classmethod
    def valid_request_id(cls, value):
        return None if value is None else uuid_value(value)


def authorize(p, task):
    require(p, 'admin', 'operator')
    if p.role != 'admin' and task.owner_id != p.user_id:
        raise HTTPException(403, '请由任务发起人或管理员取消任务')


def require_not_accepted(db, p, task):
    if 'layered_approval_v175' in task.data:
        from .layered_approval_store_v175 import managed
        row = managed(db, p, task)
        if row is not None and row.status == 'accepted':
            raise HTTPException(409, '独立验收已保存，原流程和补证记录已归档')


def journal(task):
    entries = task.data.get(KEY, [])
    if KEY not in task.data and HASH not in task.data:
        return []
    if not isinstance(entries, list) or len(entries) > LIMIT or digest(entries) != task.data.get(HASH):
        raise HTTPException(409, '任务取消请求历史摘要无法核对，原输入和记录保留')
    seen = set()
    try:
        for entry in entries:
            if set(entry) != {'request_id', 'actor_id', 'body', 'body_hash', 'saved_at', 'result_version'}:
                raise ValueError()
            body = Cancel.model_validate(entry['body'])
            if (not body.request_id or entry['request_id'] != body.request_id
                or entry['request_id'] in seen or uuid_value(entry['actor_id']) != entry['actor_id']
                or body.model_dump(mode='json') != entry['body']
                or digest(entry['body']) != entry['body_hash']
                # Stopping active native executions can itself increment the
                # same Task before cancellation's final version increment.
                or type(entry['result_version']) is not int or entry['result_version'] <= body.version
                or not isinstance(entry['saved_at'], str)):
                raise ValueError()
            seen.add(entry['request_id'])
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(409, '任务取消请求历史无法核对，原输入和记录保留') from None
    return entries


def receipt(task, entry):
    cancellation = task.data.get('cancellation', {})
    body = entry['body']
    if (task.status != 'workflow_cancelled' or task.version < entry['result_version']
        or cancellation.get('request_id') != entry['request_id']
        or cancellation.get('body_hash') != entry['body_hash']
        or cancellation.get('actor') != entry['actor_id']
        or cancellation.get('reason') != body['reason'].strip()
        or cancellation.get('retained_note') != body['retained_note']):
        raise HTTPException(409, '取消请求与持久取消回执无法核对，不能确认本请求已保存')
    return {'request_id': entry['request_id'], 'saved': True, 'status': 'saved',
        'request_hash': entry['body_hash'], 'task_id': task.id, 'task_status': task.status,
        'version': task.version, 'cancellation': copy.deepcopy(cancellation)}


def prior(task, p, body):
    if body.request_id is None:
        return None
    entries = journal(task)
    found = next((entry for entry in entries if entry['request_id'] == body.request_id), None)
    if found:
        if found['actor_id'] != p.user_id:
            raise HTTPException(403, '请由原提交账号核对取消请求')
        if found['body_hash'] != digest(body.model_dump(mode='json')):
            raise HTTPException(409, '同一取消请求不能更换版本、原因或保留输入')
        return receipt(task, found)
    if task.status == 'workflow_cancelled':
        raise HTTPException(409, '任务已取消，但没有本请求的保存回执；请核对原取消记录')
    if len(entries) >= LIMIT:
        raise HTTPException(409, '取消请求保存已达上限，请保留原请求并核对记录')
    return None


def record(task, p, body):
    if body.request_id is None:
        return None
    entries = journal(task)
    frozen = body.model_dump(mode='json')
    entry = {'request_id': body.request_id, 'actor_id': p.user_id, 'body': frozen,
        'body_hash': digest(frozen), 'saved_at': now().isoformat(), 'result_version': task.version}
    entries = [*entries, entry]
    task.data = {**task.data, KEY: entries, HASH: digest(entries),
        'cancellation': {**task.data['cancellation'], 'request_id': body.request_id,
                         'body_hash': entry['body_hash']}}
    return receipt(task, entry)


def probe(task, p, request_id):
    authorize(p, task)
    try:
        uuid_value(request_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    entries = journal(task)
    entry = next((value for value in entries if value['request_id'] == request_id), None)
    if entry:
        if entry['actor_id'] != p.user_id:
            raise HTTPException(403, '请由原提交账号核对取消请求')
        return receipt(task, entry)
    return {'request_id': request_id, 'saved': False, 'status': 'unknown', 'request_hash': None,
        'task_id': task.id, 'task_status': task.status, 'version': task.version, 'cancellation': None}


def permissions(db, p, task, release, business, authorize_step, *, execution_waiting=False, lock_sources=True):
    """Read-model authority for the current actor; request inputs still validate.

    Saving a draft uses the same step authority as the actual note endpoint.
    Completion additionally needs the live native goal/budget and settled step.
    An admin override remains an explicit required input, never an implied grant.
    """
    reasons = {}
    result = {'current_actor_role': p.role, 'can_save_note': False, 'can_complete_step': False,
              'can_cancel': False, 'can_stop_goal': False, 'requires_override': False}
    allowed = p.role in {'admin', 'operator'}
    manager = allowed and (p.role == 'admin' or task.owner_id == p.user_id)
    runtime = task.data.get('workflow_runtime', {})
    step = next((value for value in business['flow']['steps'] if value['id'] == runtime.get('current_step_id')), None)
    if allowed and task.status == 'workflow_waiting' and step:
        assigned = release.data['snapshot']['roles'].get(step.get('role'), {}).get('user_id')
        override = p.role == 'admin' and p.user_id != assigned
        try:
            authorize_step(p, release, step, '权限预览；实际提交须填写代办原因' if override else '')
            result.update(can_save_note=True, can_complete_step=True, requires_override=override)
        except HTTPException as error:
            reasons['save_note'] = reasons['complete_step'] = str(error.detail)
    else:
        reasons['save_note'] = reasons['complete_step'] = ('当前账号仅可查看任务' if not allowed else '当前没有可填写的人工步骤')
    context = task.data.get('native_goal_context')
    if result['can_complete_step']:
        try:
            if context:
                from .native_goals import guard
                if lock_sources:guard(db, p, task, step=True)
                else:guard(db, p, task, step=True, lock_sources=False)
            if execution_waiting:
                raise HTTPException(409, '当前步骤的已批准执行尚未核查，原人工草稿保留')
            if len(runtime.get('receipts', [])) >= runtime.get('max_steps', 100):
                raise HTTPException(409, '已达到流程次数上限，原记录保留')
        except HTTPException as error:
            result['can_complete_step'] = False
            reasons['complete_step'] = str(error.detail)
    if manager:
        result['can_cancel'] = task.status in CANCELLABLE
        if context:
            try:
                from .native_goals import saved_context
                saved_context(db, p, task)
                result['can_stop_goal'] = not (task.status == 'workflow_cancelled'
                    or task.data.get('native_goal_cancelled') or task.data.get('native_goal_acceptance'))
            except HTTPException as error:
                result['can_cancel'] = False
                reasons['cancel'] = reasons['stop_goal'] = str(error.detail)
    else:
        reasons['cancel'] = reasons['stop_goal'] = '请由任务发起人或管理员管理目标或取消任务'
    if not result['can_cancel'] and 'cancel' not in reasons:
        reasons['cancel'] = '当前任务状态不可取消'
    if not result['can_stop_goal'] and 'stop_goal' not in reasons:
        reasons['stop_goal'] = '当前没有可停止的原目标'
    result['reasons'] = reasons
    result['reason'] = ('当前账号仅可查看任务' if not allowed else
        reasons.get('save_note') or reasons.get('complete_step') or None)
    return result
