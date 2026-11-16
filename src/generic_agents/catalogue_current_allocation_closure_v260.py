"""A negative, immutable nonce fence; no Consumer, model call or refund.

An internal Job occupies the existing Start key. The independent Audit and
nonce-derived UUID prevent a deleted/retagged row from becoming an unknown
request. Public Job serialization exposes only a private-record placeholder.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Literal
from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from sqlalchemy import cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from starlette.concurrency import run_in_threadpool

from . import models as m
from .contracts import digest
from .services import audit

PREFIX = '/api/setup/report-saved-map-designs'
PROFILE = 'catalogue-current-allocation-v260'
TASK_PREFIX = 'report-business-catalogue-current-allocation-v260:'
JOB_KIND = 'catalogue_allocation_fence_v260'
AUDIT_ACTION = 'report.catalogue_current_allocation_closed_v260'
CLOSE_SCHEMA = 'catalogue-current-allocation-close-v260'
RECORD_SCHEMA = 'catalogue-current-allocation-close-record-v260'
RECEIPT_SCHEMA = 'catalogue-current-allocation-closed-v260'
LOOKUP_SCHEMA = 'catalogue-current-allocation-closed-request-v260'
AUDIT_SCHEMA = 'catalogue-current-allocation-close-audit-v260'
PUBLIC_SCHEMA = 'catalogue-current-allocation-private-job-v260'
MAX_CLOSE_RAW = 256 * 1024
ERROR = '原归类申请、关闭记录或独立账本不能完整核对；原记录保留，未推定未执行'
_RECORD_KEYS = set(('schema id version workspace_id actor_id request_id binding_id binding_record_hash '
    'original_raw original_sha256 original_body_hash close_raw close_sha256 close_body_hash created_at reason '
    'job_kind job_key job_status prevents_future_start past_dispatch refunds_authorized record_hash').split())
_PRIVATE_KEYS = {'original_raw', 'close_raw'}
_CUES = {'close_sha256', 'close_body_hash', 'prevents_future_start', 'past_dispatch', 'binding_record_hash'}


def _consumer():
    from . import report_saved_map_design_consumer_v176
    return report_saved_map_design_consumer_v176


def _allocation():
    from . import catalogue_current_allocation_v260
    return catalogue_current_allocation_v260


def _identity(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError('请保留原小写UUID编号')
    return value


def _sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _need(value, message=ERROR, status=409):
    if not value:
        raise HTTPException(status, message)


def fence_identity(workspace_id, nonce):
    return str(uuid5(NAMESPACE_URL, CLOSE_SCHEMA + ':' + _identity(workspace_id) + ':' + _identity(nonce)))


def close_path(nonce):
    return PREFIX + '/current-allocation-requests/' + _identity(nonce) + '/close'


def _key(nonce):
    return 'saved-map-design:' + _identity(nonce)


def public_job_data(row):
    """Pure fail-private classification; never validates or repairs a record."""
    if not isinstance(row, m.Job):
        return None
    data = row.data if isinstance(row.data, dict) else {}
    tagged = row.kind == JOB_KIND or data.get('schema') == RECORD_SCHEMA
    tagged = tagged or _PRIVATE_KEYS <= set(data) or bool(_PRIVATE_KEYS & set(data) and _CUES & set(data))
    key = row.idempotency_key
    if type(key) is str and key.startswith('saved-map-design:'):
        try: tagged = tagged or row.id == fence_identity(row.workspace_id, key[len('saved-map-design:'):])
        except (ValueError, TypeError, AttributeError): pass
    return {'schema': PUBLIC_SCHEMA, 'private_record': True} if tagged else None


class CloseBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema: Literal['catalogue-current-allocation-close-v260']
    original_raw: StrictStr
    original_sha256: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    binding_id: StrictStr
    binding_record_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    confirm_close: Literal[True]
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator('confirm_close', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True: raise ValueError('请明确结束原申请')
        return value


def _safe(value):
    stack, nodes = [(value, 0)], 0
    while stack:
        value, depth = stack.pop(); nodes += 1
        if nodes > 2000 or depth > 24: raise ValueError('原申请结构超过边界')
        if type(value) is str:
            value.encode('utf-8', errors='strict')
            if '\x00' in value: raise ValueError('原申请包含空字符')
        elif type(value) is dict:
            if any(type(k) is not str for k in value): raise ValueError('原申请键不是文本')
            stack.extend((k, depth + 1) for k in value)
            stack.extend((v, depth + 1) for v in value.values())
        elif type(value) is list: stack.extend((v, depth + 1) for v in value)
        elif value is None or type(value) in {bool, int}: pass
        elif type(value) is float and math.isfinite(value): pass
        else: raise ValueError('原申请值不可核对')


def _parse(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_CLOSE_RAW, '关闭申请超过原文容量', 413)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('原申请有重复键')
            result[key] = value
        return result
    def constant(value): raise ValueError('原申请有非有限数值')
    try:
        exact = raw.decode('utf-8', errors='strict')
        value = json.loads(exact, object_pairs_hook=unique, parse_constant=constant)
        _safe(value)
        body = CloseBody.model_validate(value)
        _identity(body.binding_id)
        if body.confirm_close is not True or not body.reason.strip(): raise ValueError('请明确关闭原因')
        original = body.original_raw.encode('utf-8', errors='strict')
        start, original_exact, sha = _consumer().read_wire(original, _allocation().StartBody)
        if (sha != body.original_sha256 or original_exact != body.original_raw
                or start.binding_id != body.binding_id or start.binding_record_hash != body.binding_record_hash):
            raise ValueError('原Start文本与关闭申请不一致')
        return body, start, exact, hashlib.sha256(raw).hexdigest()
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '请保留原Start完整文本、摘要与明确关闭申请') from None


def _reader(db, p, *, lock=False):
    _need(p.role in {'admin', 'operator', 'viewer'}, '当前账号不能读取原归类申请', 403)
    query = select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.id == p.user_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
        m.Membership.role == p.role)
    locked = [m.User, m.Membership]
    try: session_id = _identity(p.session_id)
    except (ValueError, TypeError, AttributeError): session_id = None
    if session_id is not None:
        query = query.add_columns(m.AuthSession).join(m.AuthSession, m.AuthSession.user_id == m.User.id).where(
            m.AuthSession.id == session_id, m.AuthSession.revoked.is_(False), m.AuthSession.expires_at > m.now())
        locked.append(m.AuthSession)
    if lock: query = query.with_for_update(read=True, of=locked)
    _need(db.execute(query.execution_options(populate_existing=True)).first() is not None,
        '当前账号、工作区或登录会话已变化', 403)


def _audit_data(record):
    return {'schema': AUDIT_SCHEMA, 'fence_job_id': record['id'], **{k: record[k] for k in
        ('workspace_id', 'actor_id', 'request_id', 'job_kind', 'job_key', 'original_sha256',
         'original_body_hash', 'close_sha256', 'close_body_hash', 'binding_id', 'binding_record_hash', 'record_hash')}}


def _receipt(record):
    return {'schema': RECEIPT_SCHEMA, **{k: copy.deepcopy(record[k]) for k in
        ('id', 'version', 'workspace_id', 'actor_id', 'request_id', 'binding_id', 'binding_record_hash',
         'original_sha256', 'original_body_hash', 'close_sha256', 'close_body_hash', 'created_at', 'reason',
         'prevents_future_start', 'past_dispatch', 'refunds_authorized', 'record_hash')}}


def _lookup_value(record):
    return {'schema': LOOKUP_SCHEMA, **{k: record[k] for k in
        ('workspace_id', 'actor_id', 'request_id', 'original_sha256')}, 'closed': True,
        'receipt': _receipt(record), 'original_raw': record['original_raw'], 'close_raw': record['close_raw']}


def _related(db, p, nonce):
    c = _consumer()
    ref = {'command_refs': [{'request_id': nonce}]}
    commands = list(db.scalars(select(c.Command).where(c.Command.workspace_id == p.workspace_id,
        c.Command.request_id == nonce).limit(2)))
    consumer_id = db.scalar(select(c.Consumer.id).where(c.Consumer.workspace_id == p.workspace_id,
        or_(c.Consumer.request_id == nonce, cast(c.Consumer.data, JSONB).contains(ref))).limit(1))
    anchor = db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([c.COMMAND_AUDIT, c.STATE_AUDIT]),
        or_(m.Audit.data['request_id'].as_string() == nonce, cast(m.Audit.data, JSONB).contains(ref))).limit(1))
    job = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        m.Job.idempotency_key == _key(nonce)).execution_options(populate_existing=True))
    return commands, consumer_id, anchor, job


def _checked_fence(db, p, nonce):
    fid = fence_identity(p.workspace_id, nonce)
    jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        or_(m.Job.id == fid, m.Job.idempotency_key == _key(nonce))).limit(3)
        .execution_options(populate_existing=True)))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == AUDIT_ACTION, or_(m.Audit.object_id == fid,
            m.Audit.data['request_id'].as_string() == nonce)).limit(3)))
    fences = [row for row in jobs if public_job_data(row) is not None or row.id == fid]
    if not fences and not events: return None
    _need(len(jobs) == len(fences) == len(events) == 1)
    row, event = fences[0], events[0]
    data = row.data
    _need(type(data) is dict)
    if type(data.get('actor_id')) is str and data['actor_id'] != p.user_id:
        raise HTTPException(403, '原编号不能由另一账号读取或借用')
    _need(data.get('actor_id') == p.user_id)
    try:
        _need(set(data) == _RECORD_KEYS and data['schema'] == RECORD_SCHEMA
            and data['id'] == row.id == fid and data['workspace_id'] == row.workspace_id == p.workspace_id
            and data['request_id'] == nonce and type(data['version']) is int and data['version'] == 1
            and row.kind == data['job_kind'] == JOB_KIND and row.status == data['job_status'] == 'internal'
            and row.idempotency_key == data['job_key'] == _key(nonce)
            and type(row.attempts) is int and row.attempts == 0 and row.lease_token is None and row.lease_until is None
            and data['prevents_future_start'] is True and data['past_dispatch'] == 'not_asserted'
            and data['refunds_authorized'] is False and _sha(data['record_hash'])
            and data['record_hash'] == digest({k: v for k, v in data.items() if k != 'record_hash'}))
        stamp = datetime.fromisoformat(data['created_at'])
        _need(stamp.tzinfo is not None and stamp.astimezone(timezone.utc).isoformat() == data['created_at']
            and row.created_at == stamp and row.available_at == stamp)
        body, start, exact, sha = _parse(data['close_raw'].encode('utf-8'))
        _need(start.request_id == nonce and start.activity_id == nonce and exact == data['close_raw']
            and sha == data['close_sha256'] and digest(body.model_dump()) == data['close_body_hash']
            and body.original_raw == data['original_raw'] and body.original_sha256 == data['original_sha256']
            and digest(start.model_dump()) == data['original_body_hash']
            and body.binding_id == data['binding_id'] and body.binding_record_hash == data['binding_record_hash']
            and body.reason == data['reason'] and event.actor_id == p.user_id and event.object_id == fid
            and event.action == AUDIT_ACTION and type(event.data) is dict
            and event.data == _audit_data(data))
        commands, consumer_id, anchor, _ = _related(db, p, nonce)
        _need(not commands and consumer_id is None and anchor is None)
    except HTTPException as error:
        if error.status_code == 403: raise
        raise HTTPException(409, ERROR) from None
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, ERROR) from None
    return data


def _orphan_call(db, p):
    c = _consumer(); calls = m.CloudCall
    identity = calls.data['gateway_dispatch']['identity']
    # Actual identities, not native/scope hashes shared by different activities.
    present = select(c.Consumer.id).join(m.Job, m.Job.id == c.Consumer.job_id).where(
        c.Consumer.workspace_id == calls.workspace_id,
        c.Consumer.id == identity['suggestion_id'].as_string(),
        c.Consumer.job_id == identity['job_id'].as_string(),
        c.Consumer.actor_id == identity['actor_id'].as_string(),
        c.Consumer.model_id == calls.capability_id,
        m.Job.workspace_id == calls.workspace_id, m.Job.kind == c.JOB,
        m.Job.data['consumer_id'].as_string() == c.Consumer.id,
        m.Job.data['actor_id'].as_string() == c.Consumer.actor_id,
        m.Job.data['generation_profile'].as_string() == PROFILE).exists()
    return db.scalar(select(calls.id).where(calls.workspace_id == p.workspace_id,
        identity['task_id'].as_string().startswith(TASK_PREFIX), ~present).limit(1)) is not None


def _actual_result(db, p, nonce, original_sha, *, original_raw=None, exclusive=False, closing=False, inventory=None):
    c = _consumer(); commands, consumer_id, anchor, job = _related(db, p, nonce)
    if commands:
        _need(len(commands) == 1 and commands[0].actor_id == p.user_id,
            '原编号不能由另一账号读取或借用', 403)
        command = commands[0]
        row = db.scalar(select(c.Consumer).where(c.Consumer.workspace_id == p.workspace_id,
            c.Consumer.id == command.consumer_id, c.Consumer.actor_id == p.user_id))
        is_current = row is not None and type(row.data) is dict and type(row.data.get('preview')) is dict \
            and row.data['preview'].get('generation_profile') == PROFILE
        if not is_current and not closing: return None
        if not closing and command.operation != 'start':
            # Ordinary request recovery owns the exact apply/end DTO, immutable
            # result and independent link checks. A Start collision still falls
            # through to its existing global nonce replay-conflict gate.
            return None
        records, checked, _ = inventory if inventory is not None else c._inventory(db, p, exclusive=exclusive)
        command = checked.get(command.id); _need(command is not None)
        row = c._owned(records, p, command.consumer_id)
        _need(c._family(row.data['preview']['preparation']).id == PROFILE and command.operation == 'start'
            and command.path == PREFIX + '/start-allocation' and command.body_sha256 == original_sha
            and (original_raw is None or command.exact_body == original_raw))
        _need(type(command.data['result']) is dict and set(command.data['result'])
            == {'schema', 'id', 'version', 'operation', 'status', 'job_id'})
        return copy.deepcopy(command.data['result'])
    if consumer_id is not None or anchor is not None: _need(False)
    if job is not None:
        if closing or type(job.data) is dict and job.data.get('generation_profile') == PROFILE: _need(False)
        return None
    _need(not _orphan_call(db, p))
    return None


def guard_nonce(db, p, nonce):
    record = _checked_fence(db, p, nonce)
    _need(record is None, '原编号已明确关闭，不能借作另一次操作')


def reject_closed_start(db, p, body, exact, sha):
    record = _checked_fence(db, p, body.request_id)
    if record is not None:
        _need(record['original_sha256'] == sha and record['original_raw'] == exact
            and record['original_body_hash'] == digest(body.model_dump()), '原编号已固定不同原文，未启动')
        raise HTTPException(409, detail=_lookup_value(record))
    _actual_result(db, p, body.request_id, sha, original_raw=exact, exclusive=True)


def lookup(db, p, request_id, original_sha256):
    try: _identity(request_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '原Start编号格式无效') from None
    _need(_sha(original_sha256), '原Start摘要格式无效', 422)
    _reader(db, p); c = _consumer(); c._nonce(db, p, request_id, shared=True)
    record = _checked_fence(db, p, request_id)
    if record is not None:
        _need(record['original_sha256'] == original_sha256, '原编号已固定不同原文')
        return _lookup_value(record)
    _actual_result(db, p, request_id, original_sha256)
    return None


def close(db, p, original_nonce, raw, *, actual_method, actual_path):
    try: _identity(original_nonce)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '原Start编号格式无效') from None
    _need(actual_method == 'POST' and actual_path == close_path(original_nonce), '关闭路径与原编号不一致', 422)
    body, start, exact, sha = _parse(raw)
    _need(start.request_id == start.activity_id == original_nonce, '原Start与路径编号不一致', 422)
    _reader(db, p, lock=True)
    core = _allocation(); c = _consumer()
    core.source.lock_before_consumer(db, p)
    c._nonce(db, p, original_nonce)
    record = _checked_fence(db, p, original_nonce)
    if record is not None:
        _need(record['close_sha256'] == sha and record['close_raw'] == exact
            and record['close_body_hash'] == digest(body.model_dump()), '原关闭申请已保存不同原文')
        return _receipt(record)
    inventory = c._inventory(db, p, exclusive=True)
    result = _actual_result(db, p, original_nonce, body.original_sha256,
        original_raw=body.original_raw, exclusive=True, closing=True, inventory=inventory)
    if result is not None: return result
    # No Consumer/Call is manufactured. Only historical owner metadata is
    # required: old preview/native hashes here remain the client's saved claim.
    current = core.current
    _, _, bindings, _ = current._inventory(db, p)
    binding = bindings.get(body.binding_id)
    _need(binding is not None and binding.actor_id == p.user_id and binding.workspace_id == p.workspace_id,
        '没有当前账号的原工作副本绑定', 403)
    _need(binding.record_hash == body.binding_record_hash and binding.working_draft_id == start.draft_id)
    stamp = m.now(); fid = fence_identity(p.workspace_id, original_nonce)
    value = {'schema': RECORD_SCHEMA, 'id': fid, 'version': 1, 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'request_id': original_nonce, 'binding_id': body.binding_id,
        'binding_record_hash': body.binding_record_hash, 'original_raw': body.original_raw,
        'original_sha256': body.original_sha256, 'original_body_hash': digest(start.model_dump()),
        'close_raw': exact, 'close_sha256': sha, 'close_body_hash': digest(body.model_dump()),
        'created_at': stamp.astimezone(timezone.utc).isoformat(), 'reason': body.reason,
        'job_kind': JOB_KIND, 'job_key': _key(original_nonce), 'job_status': 'internal',
        'prevents_future_start': True, 'past_dispatch': 'not_asserted', 'refunds_authorized': False}
    value['record_hash'] = digest(value)
    db.add(m.Job(id=fid, workspace_id=p.workspace_id, kind=JOB_KIND, idempotency_key=_key(original_nonce),
        status='internal', attempts=0, lease_token=None, lease_until=None, created_at=stamp,
        available_at=stamp, data=value))
    audit(db, p, AUDIT_ACTION, fid, _audit_data(value)); db.flush()
    checked = _checked_fence(db, p, original_nonce); _need(checked is not None)
    return _receipt(checked)


def register_routes(router):
    from .setup_api import P, DB
    @router.post('/current-allocation-requests/{original_nonce}/close')
    async def close_route(original_nonce: str, request: Request, p: P, db: DB):
        raw = bytearray()
        async for block in request.stream():
            _need(len(raw) + len(block) <= MAX_CLOSE_RAW, '关闭申请超过原文容量', 413)
            raw.extend(block)
        def mutation():
            try:
                result = close(db, p, original_nonce, bytes(raw),
                    actual_method=request.method, actual_path=request.url.path)
                db.commit(); return result
            except BaseException:
                db.rollback(); raise
        return await run_in_threadpool(mutation)
