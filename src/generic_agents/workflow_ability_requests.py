"""Actor-bound ability request receipts; reads never schedule or infer delivery."""
import copy
from datetime import datetime
import re
from fastapi import HTTPException
from .contracts import digest
from .models import now
from .services import require


def authorize(p, task):
    require(p, 'admin', 'operator')
    if task.owner_id != p.user_id and p.role != 'admin':
        raise HTTPException(403, '请由任务发起人或管理员核对自动执行')


def request_hash(body):
    return digest({key: value for key, value in body.items() if key != 'version'})


def journal(task):
    runtime = task.data['workflow_runtime']
    entries = runtime.get('automatic_requests', [])
    try:
        if not isinstance(entries, list) or len(entries) > 500: raise ValueError()
        if 'automatic_requests_hash' in runtime and digest(entries) != runtime['automatic_requests_hash']: raise ValueError()
        seen = set()
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) not in (
                {'request_id', 'actor', 'hash'}, {'request_id', 'actor', 'hash', 'body', 'visit', 'result_version', 'saved_at'})
                or not isinstance(entry['request_id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}', entry['request_id'])
                or entry['request_id'] in seen or not isinstance(entry['actor'], str) or not entry['actor']
                or not isinstance(entry['hash'], str) or not re.fullmatch(r'[0-9a-f]{64}', entry['hash'])):
                raise ValueError()
            seen.add(entry['request_id'])
            if 'body' not in entry: continue
            body = entry['body']
            if ('automatic_requests_hash' not in runtime or not isinstance(body, dict)
                or set(body) != {'version', 'step_id', 'request_id', 'retry'}
                or type(body['version']) is not int or body['version'] < 1
                or type(body['step_id']) is not str or not body['step_id']
                or body['request_id'] != entry['request_id'] or type(body['retry']) is not bool
                or request_hash(body) != entry['hash'] or type(entry['visit']) is not int or entry['visit'] < 0
                or type(entry['result_version']) is not int or entry['result_version'] <= body['version']
                or task.version < entry['result_version'] or not isinstance(entry['saved_at'], str)
                or datetime.fromisoformat(entry['saved_at'].replace('Z', '+00:00')).tzinfo is None):
                raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(409, '原能力请求历史无法核对，请保留原记录') from None
    return entries


def receipt(task, p, request_id, entry=None):
    runtime = task.data['workflow_runtime']
    if entry is not None and entry['actor'] != p.user_id:
        raise HTTPException(403, '请由原提交账号核对能力请求')
    return {'request_id': request_id, 'task_id': task.id, 'actor_id': p.user_id,
        'saved': entry is not None, 'status': 'saved' if entry is not None else 'unknown',
        'request_hash': entry['hash'] if entry is not None else None,
        'step_id': entry['body']['step_id'] if entry is not None and 'body' in entry else None,
        'visit': entry.get('visit') if entry is not None else None,
        'result_version': entry.get('result_version') if entry is not None else None,
        'task_version': task.version, 'task_status': task.status,
        'current_step_id': runtime.get('current_step_id'), 'current_visit': len(runtime.get('receipts', []))}


def prior(task, p, body):
    entries = journal(task)
    found = next((value for value in entries if value['request_id'] == body.request_id), None)
    if found is None: return None
    if found['actor'] != p.user_id or found['hash'] != request_hash(body.model_dump(mode='json')):
        raise HTTPException(409, '同一执行请求不能更换内容或提交账号')
    return receipt(task, p, body.request_id, found)


def record(task, p, body, visit):
    runtime = copy.deepcopy(task.data['workflow_runtime'])
    entries = journal(task)
    entry = {'request_id': body.request_id, 'actor': p.user_id,
        'hash': request_hash(body.model_dump(mode='json')), 'body': body.model_dump(mode='json'),
        'visit': visit, 'result_version': task.version, 'saved_at': now().isoformat()}
    entries = [*entries, entry]
    runtime.update(automatic_requests=entries, automatic_requests_hash=digest(entries))
    task.data = {**task.data, 'workflow_runtime': runtime}
    return receipt(task, p, body.request_id, entry)


def probe(task, p, request_id):
    authorize(p, task)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}', request_id):
        raise HTTPException(422, '能力请求编号格式无效')
    found = next((entry for entry in journal(task) if entry['request_id'] == request_id), None)
    return receipt(task, p, request_id, found)


def permissions(p, task, business, *, blocked=False):
    manager = p.role in {'admin', 'operator'} and (p.role == 'admin' or p.user_id == task.owner_id)
    runtime = task.data['workflow_runtime']
    visit = len(runtime.get('receipts', []))
    step = next((value for value in business['flow']['steps'] if value['id'] == runtime.get('current_step_id')), None)
    automatic = bool(step and step.get('agent') != '人工确认' and isinstance(step.get('execution'), dict))
    available = manager and not blocked and automatic and task.status in {'workflow_waiting', 'workflow_failed', 'workflow_blocked'}
    previous = runtime.get('automatic', {})
    repeat = bool(previous and previous.get('step_id') == runtime.get('current_step_id') and previous.get('visit') == visit)
    reason = ('请由任务发起人或管理员核对自动执行' if not manager else
        '原目标或执行依据当前不可用，请保留原请求核对' if blocked else
        '当前没有可申请的能力步骤' if not automatic else
        '能力任务已排队、执行中或当前流程已结束' if not available else None)
    return {'can_run_ability': bool(available and not repeat), 'can_retry_ability': bool(available and repeat),
        'can_probe_ability': bool(manager), 'reasons': {'run_ability': reason or ('原执行已保存，请明确核对后重试' if repeat else None),
            'retry_ability': reason or (None if repeat else '当前没有原能力执行可重试'),
            'probe_ability': None if manager else '请由任务发起人或管理员核对原能力请求'}}
