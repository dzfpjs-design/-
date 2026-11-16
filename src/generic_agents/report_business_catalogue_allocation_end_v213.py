"""Explicitly end an interrupted batch without claiming it was never sent.

This closes platform waiting, not the remote provider's request. Original
captures and conservative budget consumption remain. No dispatch or catalogue
save is reachable from this module. Historical proof uses frozen end Audits,
never the current daily-budget head or a newly read response.
"""
import copy
import hashlib
import os
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, StrictStr, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import OperationalError
from starlette.concurrency import run_in_threadpool

from . import models as m
from .cloud_budget import finish_tx
from .config import settings
from .contracts import Contract, digest
from .relations import lock_current_actor
from .security import require
from .services import audit
from . import report_evidence_http_capture_v1 as capture
from . import report_evidence_native_bridge_v1 as bridge

PREFIX = '/api/setup/report-saved-map-designs'
OPERATION = 'end-interrupted'
PROFILE = 'catalogue-allocation-v213'
DATA_KEY = 'interrupted_end'
END_AUDIT = 'report.catalogue_allocation_end_v213'
END_SCHEMA = 'report-business-catalogue-allocation-ended-v213'
CONTEXT = 'report-business-catalogue-allocation-end-context-v213'
MESSAGE = '已明确结束这次中断的归类等待。请求是否送达及供应商实际费用仍未知；原预留和捕获记录保留，没有退款或重新发送。'


def _protocol(value):
    if value is None: return sys.modules[__name__]
    from . import catalogue_current_allocation_end_v260 as current
    need(value is current)
    return value


def marker_pins(row):
    return {}


def _consumer():
    from . import report_saved_map_design_consumer_v176 as value
    return value


def need(ok, message='原归类任务、租约、预算或独立结束证明无法核对', status=409):
    if not ok: raise HTTPException(status, message)


class EndInterruptedBody(Contract):
    request_id: StrictStr
    expected_version: int = Field(strict=True, ge=1)
    confirm: bool = Field(strict=True)
    retain_reserved_budget: bool = Field(strict=True)

    @field_validator('request_id')
    @classmethod
    def identity(cls, value): return _consumer().identity(value)

    @field_validator('confirm', 'retain_reserved_budget')
    @classmethod
    def explicit(cls, value):
        if value is not True: raise ValueError('请明确同意结束等待并保留原保守预算；这不代表未发送或未收费')
        return value


def _family(row, p, *, _adapter=None):
    proto = _protocol(_adapter)
    c = _consumer()
    need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id
        and c._family(row.data['preview']['preparation']).id == proto.PROFILE,
        '这不是当前账号的新族归类任务，不能按本操作结束')


def _job(db, p, row, *, lock=False, _adapter=None):
    proto = _protocol(_adapter)
    query = select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.id == row.job_id)
    if lock: query = query.with_for_update(nowait=True)
    try: job = db.scalar(query.execution_options(populate_existing=True))
    except OperationalError as error:
        if getattr(error.orig, 'sqlstate', getattr(error.orig, 'pgcode', None)) == '55P03':
            raise HTTPException(409, '原任务仍被执行事务占用，请稍后按原号核对；未等待或结束该事务') from None
        raise
    c = _consumer()
    need(job is not None and job.kind == c.JOB and job.data.get('actor_id') == p.user_id
        and job.data.get('consumer_id') == row.id and job.data.get('generation_profile') == proto.PROFILE
        and job.idempotency_key == 'saved-map-design:' + row.request_id)
    return job


def _stopped(job, clock):
    if job.status == 'running':
        need(job.lease_until is not None, '运行任务缺少可核对的租约截止时间，未推定已停止')
        _consumer().identity(job.lease_token)
        return job.lease_until <= clock
    need(job.status in {'failed', 'succeeded'} and job.lease_until is None,
        '原任务仍排队或租约状态不一致，不能按已中断任务结束')
    return True


def _unknown_call(call):
    need(call is not None and type(call.data) is dict and call.status in {'reserved', 'failed'},
        '原调用已有结果或缺少可核对账本，请先普通核对原回答')
    need(call.data.get('receipt') in (None, {}) and call.data.get('usage') in (None, {})
        and call.data.get('confirmed_receipt') in (None, {}) and call.data.get('usage_available') is not True
        and call.data.get('usage_unavailable') is not False,
        '原调用已有回执或用量依据，请先普通核对，不能作为未知用量结束')
    need(type(call.reserved_tokens) is int and call.reserved_tokens >= 0
        and type(call.charged_tokens) is int and call.charged_tokens == call.reserved_tokens,
        '原调用不是完整保留预留量的保守未知分支，未改变财务记录')


def _budget(db, call, *, lock=True):
    day = call.data.get('day')
    try: need(type(day) is str and date.fromisoformat(day).isoformat() == day)
    except ValueError: need(False)
    query = select(m.Job).where(m.Job.workspace_id == call.workspace_id, m.Job.idempotency_key == 'cloud-budget:' + day)
    if lock: query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    need(row is not None and row.kind == 'cloud_budget' and row.status == 'internal' and type(row.data) is dict
        and row.data.get('day') == day and type(row.data.get('tokens')) is int
        and row.data['tokens'] >= call.reserved_tokens and type(row.data.get('calls')) is int
        and row.data['calls'] >= 1, '原预留的日预算无法核对，未释放或重建额度')
    return row


def _call_finance(call):
    return {'id': call.id, 'status': call.status, 'reserved_tokens': call.reserved_tokens,
        'charged_tokens': call.charged_tokens, 'data_hash': digest(call.data)}


def _finance(call, budget):
    return {'call': _call_finance(call),
        'budget': {'id': budget.id, 'day': budget.data['day'], 'tokens': budget.data['tokens'],
            'calls': budget.data['calls']}}


def _job_pin(job):
    return {'id': job.id, 'status': job.status, 'attempts': job.attempts,
        'lease_token': job.lease_token, 'lease_until': job.lease_until.isoformat() if job.lease_until else None,
        'data_hash': digest(job.data)}


def _capture(row, call):
    c = _consumer()
    detached = SimpleNamespace(**c._row_pin(row)); detached.reserved_call_id = call.id
    # A missing capture slot is a fact only inside a present, safely readable
    # trusted storage base. Losing the whole disk must never look like absence.
    try:
        fd = capture._root(Path(settings().storage_dir).absolute(), create=False)
        os.close(fd)
        ctx = c.runtime()._context(detached)
        state = capture.load(bridge.root(), ctx)
        if state['ready'] is True or state['response_complete'] is True:
            need(False, '原完整回答已经保存，请先使用“核对已保存的原回答”，不能把它当未知结果结束')
        need(state['state'] in {'missing', 'partial'} and state['response_complete'] is False,
            '原捕获已隔离或状态异常，请先核对存储，不能推定没有回答')
        if state['state'] == 'partial':
            body = capture.read_body(bridge.root(), ctx, state['capture_hash'])
            need(type(body) is bytes and len(body) == state['body_bytes']
                and hashlib.sha256(body).hexdigest() == state['body_sha256']
                and capture.load(bridge.root(), ctx)['capture_hash'] == state['capture_hash'])
        return copy.deepcopy(state)
    except (OSError, ValueError, KeyError, TypeError):
        need(False, '原捕获或存储无法可靠读取；原记录保留，未把读取失败当作没有回答')


def _audit_data(p, row, command, marker, *, _adapter=None):
    proto = _protocol(_adapter)
    return {'schema': proto.END_SCHEMA, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'consumer_id': row.id, 'command_id': command.id, 'request_id': command.request_id,
        'body_sha256': command.body_sha256, 'marker': copy.deepcopy(marker)}


def _checked_marker(marker, row, body, *, _adapter=None):
    proto = _protocol(_adapter)
    c = _consumer()
    fields = set(('schema consumer_id command_id job_id call_id expected_version before_version association_was_missing ended_version ended_at '
        'preview_hash usage_unknown retain_reserved_budget capture financial_before financial_after '
        'job_before job_after marker_hash').split())
    need(type(marker) is dict and set(marker) == fields | set(proto.marker_pins(row)) and marker['schema'] == proto.END_SCHEMA
        and marker['marker_hash'] == digest({key: value for key, value in marker.items() if key != 'marker_hash'}))
    need(all(marker[k] == v for k, v in proto.marker_pins(row).items()))
    for key in ('consumer_id', 'command_id', 'job_id', 'call_id'): c.identity(marker[key])
    need(type(marker['expected_version']) is int and type(marker['ended_version']) is int
        and marker['expected_version'] == body.expected_version
        and type(marker['before_version']) is int and marker['before_version'] == marker['expected_version']
        and type(marker['association_was_missing']) is bool
        and marker['ended_version'] == marker['before_version'] + 1 + int(marker['association_was_missing'])
        and marker['ended_version'] <= row.version and marker['usage_unknown'] is True
        and marker['retain_reserved_budget'] is True and row.data.get('projection') is None
        and row.data.get('manual_value') is None and row.data.get('allocation_raw_arguments_b64') is None
        and row.data.get('error') == MESSAGE)
    ended = datetime.fromisoformat(marker['ended_at']); need(ended.tzinfo is not None)
    state = marker['capture']
    need(type(state) is dict and state.get('schema') == 'report-evidence-http-capture-state-v1'
        and state.get('state') in {'missing', 'partial'} and state.get('response_complete') is False
        and state.get('ready') is False and state.get('can_adopt') is False
        and state.get('capture_hash') == digest({key: value for key, value in state.items() if key != 'capture_hash'})
        and state.get('context_hash') == digest(c.runtime()._context(row)))
    before, after = marker['job_before'], marker['job_after']
    job_fields = {'id', 'status', 'attempts', 'lease_token', 'lease_until', 'data_hash'}
    need(type(before) is dict and type(after) is dict and set(before) == set(after) == job_fields
        and before['id'] == after['id'] == row.job_id and after['status'] == 'failed'
        and after['lease_token'] is None and after['lease_until'] is None
        and type(before['attempts']) is int and before['attempts'] >= 0
        and after['attempts'] == before['attempts'] and before['data_hash'] == after['data_hash'])
    lease = datetime.fromisoformat(before['lease_until']) if before['lease_until'] is not None else None
    need(_stopped(SimpleNamespace(**{**before, 'lease_until': lease}), ended))
    return marker


def check_parent_command(db, p, row, command, *, ref, _adapter=None):
    proto = _protocol(_adapter)
    c = _consumer(); proto._family(row, p)
    body, _, sha = c.read_wire(command.exact_body.encode('utf-8'), EndInterruptedBody)
    marker = proto._checked_marker(row.data.get(proto.DATA_KEY), row, body)
    need(marker['consumer_id'] == row.id and marker['command_id'] == command.id
        and marker['job_id'] == row.job_id and marker['call_id'] == row.reserved_call_id
        and marker['expected_version'] == body.expected_version and marker['ended_version'] <= row.version
        and marker['preview_hash'] == row.data['preview']['preview_hash'] and row.status == 'failed'
        and marker['usage_unknown'] is True and marker['retain_reserved_budget'] is True)
    need(command.operation == proto.OPERATION and command.path == PREFIX + '/' + row.id + '/' + proto.OPERATION
        and command.method == 'POST' and command.consumer_id == row.id and command.actor_id == p.user_id
        and command.body_sha256 == sha and body.request_id == command.request_id
        and command.command_hash == c.h(c._command_pin(command))
        and c.same(ref, {'id': command.id, 'request_id': command.request_id, 'operation': proto.OPERATION, 'wire_sha256': sha}))
    before, after = marker['financial_before'], marker['financial_after']
    for state in (before, after):
        need(type(state) is dict and set(state) == {'call', 'budget'}
            and set(state['call']) == {'id', 'status', 'reserved_tokens', 'charged_tokens', 'data_hash'}
            and set(state['budget']) == {'id', 'day', 'tokens', 'calls'}
            and all(type(state['call'][key]) is int for key in ('reserved_tokens', 'charged_tokens'))
            and state['call']['reserved_tokens'] == state['call']['charged_tokens']
                == c._native_preparation(row.data['preview']['preparation'])['input_summary']['token_bound']
            and type(state['budget']['tokens']) is int and state['budget']['tokens'] >= state['call']['reserved_tokens']
            and type(state['budget']['calls']) is int and state['budget']['calls'] >= 1)
        c.identity(state['budget']['id']); need(c.SHA.fullmatch(state['call']['data_hash']) is not None)
        need(date.fromisoformat(state['budget']['day']).isoformat() == state['budget']['day'])
    need(before['budget'] == after['budget'] and before['call']['id'] == after['call']['id'] == marker['call_id']
        and before['call']['reserved_tokens'] == after['call']['reserved_tokens'] == after['call']['charged_tokens']
        and after['call']['status'] == 'failed'
        and (before['call']['status'] == 'reserved' or before['call'] == after['call']))
    result = command.data.get('result')
    need(set(command.data) == {'schema', 'result'} and command.data['schema'] == c.COMMAND
        and type(result) is dict and set(result) == {'schema','id','version','operation','status','job_id','interrupted_end'}
        and result['schema'] == c.RESULT and result['id'] == row.id and result['job_id'] == row.job_id
        and result['operation'] == proto.OPERATION and result['status'] == 'failed'
        and result['version'] == marker['ended_version'] and c.same(result['interrupted_end'], marker))
    # Only this original Call and Job are fixed by the end witness. Other
    # legitimate calls may change the same day's budget after this commit.
    actual_call = db.scalar(select(m.CloudCall).where(m.CloudCall.workspace_id == p.workspace_id,
        m.CloudCall.id == marker['call_id']).execution_options(populate_existing=True))
    need(actual_call is not None); c._check_call(row, actual_call)
    need(c.same(_call_finance(actual_call), after['call'])
        and c.same(_job_pin(proto._job(db, p, row)), marker['job_after']))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == proto.END_AUDIT, m.Audit.object_id == command.id,
        or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
    need(len(events) == 1 and events[0].actor_id == p.user_id
        and c.same(events[0].data, proto._audit_data(p, row, command, marker)))
    return marker


def check_inventory(db, p, by_consumer, by_command, events, *, _adapter=None):
    proto = _protocol(_adapter)
    expected = set()
    for command in by_command.values():
        if command.operation != proto.OPERATION: continue
        row = by_consumer.get(command.consumer_id); need(row is not None)
        proto.check_parent_command(db, p, row, command, ref={'id': command.id, 'request_id': command.request_id,
            'operation': proto.OPERATION, 'wire_sha256': command.body_sha256}); expected.add(command.id)
    need(len(events) == len(expected) and {event.object_id for event in events} == expected
        and all(event.actor_id == p.user_id and event.action == proto.END_AUDIT for event in events))
    for row in by_consumer.values():
        if row.data.get(proto.DATA_KEY) is not None:
            need(row.data[proto.DATA_KEY].get('command_id') in expected)


def end(db, p, consumer_id, body, exact, sha, *, _adapter=None):
    proto = _protocol(_adapter)
    c = _consumer(); c._nonce(db, p, body.request_id); c._any_nonce(db, p, body.request_id)
    records, commands, calls = c._inventory(db, p, exclusive=True)
    row = c._owned(records, p, consumer_id); path = PREFIX + '/' + row.id + '/' + proto.OPERATION
    prior = c._replay(commands, p, body.request_id, proto.OPERATION, path, exact, sha)
    if prior is not None: return prior
    require(p, 'admin', 'operator'); lock_current_actor(db, p); proto._family(row, p)
    need(row.version == body.expected_version, '原归类版本已变化，请保留原号并读取最新状态')
    need(row.data.get(proto.DATA_KEY) is None and row.status in {'running', 'response_unknown'}
        and row.data['dispatch_started'] is True, '当前没有可按本操作结束的已中断归类任务')
    job = proto._job(db, p, row, lock=True)
    call = calls.get(row.id); need(call is not None, '独立原调用库存不完整，未凭空创建调用或结算')
    call = db.scalar(select(m.CloudCall).where(m.CloudCall.id == call.id).with_for_update()
        .execution_options(populate_existing=True)); c._check_call(row, call); _unknown_call(call)
    budget = _budget(db, call); clock = db.scalar(select(func.clock_timestamp()))
    need(_stopped(job, clock), '原任务还有活租约，请先等待当前执行结束；未取消运行中的供应商请求')
    state = _capture(row, call); clock = db.scalar(select(func.clock_timestamp()))
    need(_stopped(job, clock), '原租约最终状态已变化，未结束任务')
    before = _finance(call, budget); job_before = _job_pin(job)
    before_version = row.version; association_was_missing = row.reserved_call_id is None
    if row.reserved_call_id is None: c._associate(db, p, row, call)
    if call.status == 'reserved': need(finish_tx(db, call.id, None, 'failed') is True)
    after = _finance(call, budget); need(before['budget'] == after['budget'])
    job.status = 'failed'; job.lease_token = None; job.lease_until = None
    command_id = m.uid()
    marker = {'schema': proto.END_SCHEMA, 'consumer_id': row.id, 'command_id': command_id, 'job_id': job.id,
        'call_id': call.id, 'expected_version': body.expected_version, 'before_version': before_version,
        'association_was_missing': association_was_missing, 'ended_version': row.version + 1,
        'ended_at': clock.isoformat(), 'preview_hash': row.data['preview']['preview_hash'],
        'usage_unknown': True, 'retain_reserved_budget': True, 'capture': state,
        'financial_before': before, 'financial_after': after, 'job_before': job_before, 'job_after': _job_pin(job), **proto.marker_pins(row)}
    marker['marker_hash'] = digest(marker)
    data = {**row.data, 'error': MESSAGE, proto.DATA_KEY: marker,
        'command_refs': row.data['command_refs'] + [{'id': command_id, 'request_id': body.request_id,
            'operation': proto.OPERATION, 'wire_sha256': sha}]}
    c._transition(db, p, row, data, 'failed')
    result = {**c.result(row, proto.OPERATION), 'interrupted_end': copy.deepcopy(marker)}
    command = c.Command(id=command_id, workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=body.request_id, consumer_id=row.id, operation=proto.OPERATION, method='POST', path=path,
        exact_body=exact, body_sha256=sha, data={'schema': c.COMMAND, 'result': result})
    command.command_hash = c.h(c._command_pin(command)); db.add(command); db.flush()
    audit(db, p, c.COMMAND_AUDIT, command.id, c._command_anchor(command))
    audit(db, p, proto.END_AUDIT, command.id, proto._audit_data(p, row, command, marker)); return result


def context(db, p, consumer_id, *, _adapter=None):
    proto = _protocol(_adapter)
    c = _consumer(); c._current_reader(db, p); records, _, calls = c._inventory(db, p)
    row = c._owned(records, p, consumer_id); proto._family(row, p)
    marker = row.data.get(proto.DATA_KEY)
    value = {'schema': proto.CONTEXT, 'id': row.id, 'version': row.version, 'status': row.status,
        'job_id': row.job_id, 'call_id': row.reserved_call_id, 'can_end': False,
        'job_status': None, 'lease_until': None, 'lease_active': None,
        'reason': '当前没有可结束的已中断等待。', 'retain_reserved_budget': True,
        'usage_unknown': None, 'interrupted_end': copy.deepcopy(marker)}
    if marker is not None:
        return {**value, 'job_status': marker['job_after']['status'], 'lease_active': False,
            'usage_unknown': True, 'reason': MESSAGE}
    if row.status not in {'running', 'response_unknown'}: return value
    job = proto._job(db, p, row); clock = db.scalar(select(func.clock_timestamp()))
    value = {**value, 'job_status': job.status, 'lease_until': job.lease_until.isoformat() if job.lease_until else None}
    if not _stopped(job, clock): return {**value, 'lease_active': True, 'reason': '原任务还有活租约，不能结束正在执行的请求。'}
    call = calls.get(row.id); _unknown_call(call); _budget(db, call, lock=False); state = _capture(row, call)
    return {**value, 'call_id': call.id, 'call_status': call.status, 'reserved_tokens': call.reserved_tokens,
        'lease_active': False, 'can_end': p.role in {'admin', 'operator'}, 'usage_unknown': True,
        'reason': '此归类等待已经中断。有操作权限时可明确结束等待；这不代表请求未送达或供应商未收费，原保守预留继续保留。',
        'capture_state': state['state']}


c = _consumer()
router = APIRouter(prefix=PREFIX)


@router.get('/{consumer_id}/end-interrupted-context')
def context_route(consumer_id: str, p: c.P, db: c.DB):
    c.route_identity(consumer_id); return context(db, p, consumer_id)


@router.post('/{consumer_id}/end-interrupted')
async def end_route(consumer_id: str, request: Request, p: c.P, db: c.DB):
    c.route_identity(consumer_id)
    need(request.method == 'POST' and request.url.path == PREFIX + '/' + consumer_id + '/' + OPERATION,
        '结束路径或方法不符合原请求合同', 422)
    raw = bytearray()
    async for chunk in request.stream():
        need(len(raw) + len(chunk) <= c.MAX_WIRE_BYTES, '结束请求超过完整正文容量，原请求未保存', 413)
        raw.extend(chunk)
    body, exact, sha = c.read_wire(bytes(raw), EndInterruptedBody)
    def mutation():
        try:
            result = end(db, p, consumer_id, body, exact, sha); db.commit(); return result
        except BaseException:
            db.rollback(); raise
    return await run_in_threadpool(mutation)
