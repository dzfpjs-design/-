"""Read-only projections of precisely pinned workflow Jobs, never processors."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import select, func
from .contracts import digest
from .models import Job


def empty(state='unverified', reason='后台任务归属资料无法核对，原记录保留'):
    return {'state': state, 'job_id': None, 'job_status': None, 'database_now': None,
        'lease_until': None, 'attempts': None, 'lease_recoveries': None,
        'recovery_only': None, 'queued_at': None, 'last_activity_at': None,
        'processor': {'state': 'unknown', 'last_reported_at': None, 'reason': '尚未取得处理器心跳'},
        'historical': False, 'reason': reason}


def uuid_value(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _instant(value):
    if isinstance(value, str):
        try: value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: return None
    return value if isinstance(value, datetime) and value.tzinfo is not None else None


def _project(db, task, job, *, historical=False):
    recoveries, recovery_only = job.data.get('lease_recoveries', 0), job.data.get('recovery_only', False)
    created = _instant(getattr(job, 'created_at', None))
    if (job.status not in {'queued', 'running', 'succeeded', 'failed', 'cancelled'}
        or type(job.attempts) is not int or job.attempts < 0 or type(recoveries) is not int
        or recoveries < 0 or type(recovery_only) is not bool or created is None
        or job.lease_until is not None and _instant(job.lease_until) is None):
        return empty()
    clock = db.scalar(select(func.clock_timestamp()))
    if _instant(clock) is None: return empty()
    state, reason = job.status, '仅显示已保存任务与租约；处理器在线状态未知'
    if task.status == 'workflow_cancelled' or task.data.get('native_goal_cancelled'):
        state, historical = 'cancelled', True
        reason = '业务已停止；已开始的旧执行可能仍会留下历史回执，不代表继续采用'
    elif task.data.get('native_goal_timed_out'):
        state, historical = 'stale', True
        reason = '原目标已到截止时间，旧执行记录保留'
    elif job.status == 'running':
        if job.lease_until is None or job.lease_until <= clock:
            state, reason = 'waiting_recovery', '任务租约已失效，等待后台恢复；本次读取不会领取或重发'
        elif not uuid_value(job.lease_token):
            state, reason = 'unverified', '运行租约缺少有效归属，无法核实当前执行'
    elif job.status == 'queued' and not historical:
        from .workflow_task_consumer_v265 import admission_reason
        # Reuse a clean caller RR/RO snapshot; writers keep the generic reason.
        # No nested connection, changed Job state or invented heartbeat.
        from .security import Principal
        from .models import Membership
        member = db.scalar(select(Membership).where(Membership.workspace_id == task.workspace_id,
            Membership.user_id == job.data.get('actor_id')))
        if member is not None:
            projected = admission_reason(db, Principal(member.user_id, member.workspace_id, member.role,
                'workflow-worker-status-read'), task, job)
            if projected is not None: reason = projected
    times = [created]
    for key in ('started_at', 'finished_at', 'updated_at'):
        value = _instant(job.data.get(key))
        if value is not None: times.append(value)
    return {'state': state, 'job_id': job.id, 'job_status': job.status,
        'database_now': clock.isoformat(), 'lease_until': job.lease_until.isoformat() if job.lease_until else None,
        'attempts': job.attempts, 'lease_recoveries': recoveries, 'recovery_only': recovery_only,
        'queued_at': created.isoformat(), 'last_activity_at': max(times).isoformat(),
        'processor': {'state': 'unknown', 'last_reported_at': None, 'reason': '尚未取得处理器心跳'},
        'historical': historical, 'reason': reason}


def automatic(db, p, task):
    runtime = task.data.get('workflow_runtime', {})
    if not isinstance(runtime, dict): return empty()
    auto = runtime.get('automatic')
    if not auto:
        return empty('not_requested', '当前没有已保存的能力执行任务')
    if not isinstance(auto, dict) or task.workspace_id != p.workspace_id:
        return empty()
    if auto.get('job_id') is None and auto.get('phase') == 'blocked':
        return empty('blocked', auto.get('error') or '能力执行尚未获得可用输入或授权，未创建后台任务')
    scope_keys = ('step_id', 'visit', 'attempt', 'inputs', 'input_hash', 'request_id')
    if (not all(key in auto for key in scope_keys) or not uuid_value(auto.get('job_id'))
        or not uuid_value(auto.get('actor_id')) or type(auto['step_id']) is not str
        or type(auto['visit']) is not int or auto['visit'] < 0
        or type(auto['attempt']) is not int or auto['attempt'] < 1
        or type(auto['inputs']) is not dict or digest(auto['inputs']) != auto['input_hash']):
        return empty()
    scope = {key: auto[key] for key in scope_keys}
    key = 'workflow:' + task.id + ':' + str(auto['visit']) + ':' + str(auto['attempt'])
    job = db.scalar(select(Job).where(Job.id == auto['job_id'], Job.workspace_id == p.workspace_id,
        Job.kind == 'execute_workflow_step', Job.idempotency_key == key).execution_options(populate_existing=True))
    if (not job or job.id != auto['job_id'] or job.workspace_id != p.workspace_id
        or job.kind != 'execute_workflow_step' or job.idempotency_key != key or not isinstance(job.data, dict)
        or job.data.get('task_id') != task.id or job.data.get('actor_id') != auto['actor_id']
        or job.data.get('scope') != scope or job.data.get('invocation_key') != key):
        return empty()
    receipts = runtime.get('receipts', [])
    if not isinstance(receipts, list) or auto['visit'] > len(receipts): return empty()
    historical = auto['visit'] < len(receipts)
    if historical:
        receipt = receipts[auto['visit']]
        if (not isinstance(receipt, dict) or receipt.get('request_id') != job.id or receipt.get('step_id') != auto['step_id']
            or receipt.get('actor') != auto['actor_id'] or receipt.get('input_hash') != auto['input_hash']):
            return empty()
    elif runtime.get('current_step_id') != auto['step_id']:
        return empty()
    return _project(db, task, job, historical=historical)


def check(db, p, task):
    context = task.data.get('native_goal_context')
    if context is None and 'native_goal_context' not in task.data:
        return empty('not_requested', '尚未保存原业务目标检查请求')
    pin = task.data.get('native_goal_check_job_pin')
    if (task.workspace_id != p.workspace_id or not isinstance(context, dict) or not isinstance(pin, dict)
        or set(pin) != {'job_id', 'idempotency_key', 'context_hash', 'actor_id'}
        or not uuid_value(pin.get('job_id')) or not uuid_value(pin.get('actor_id'))
        or task.data.get('native_goal_context_hash') != digest(context)
        or pin['context_hash'] != digest(context)
        or task.data.get('native_goal_check_job_pin_hash') != digest(pin)
        or not isinstance(pin['idempotency_key'], str)
        or not pin['idempotency_key'].startswith('native-goal:' + task.id + ':' + digest(context) + ':')):
        return empty()
    job = db.scalar(select(Job).where(Job.id == pin['job_id'], Job.workspace_id == p.workspace_id,
        Job.kind == 'check_native_business_goal', Job.idempotency_key == pin['idempotency_key'])
        .execution_options(populate_existing=True))
    if (not job or job.id != pin['job_id'] or job.workspace_id != p.workspace_id
        or job.kind != 'check_native_business_goal' or job.idempotency_key != pin['idempotency_key']
        or not isinstance(job.data, dict) or job.data.get('task_id') != task.id
        or job.data.get('context_hash') != pin['context_hash'] or job.data.get('actor_id') != pin['actor_id']):
        return empty()
    return _project(db, task, job, historical=bool(task.data.get('native_goal_acceptance')))
