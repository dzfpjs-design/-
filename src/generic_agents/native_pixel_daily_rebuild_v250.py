"""Explicit, recoverable local daily rebuild; never another inference or ACK."""
import copy
from datetime import date
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, StrictInt, StrictStr, StrictBool, field_validator
from sqlalchemy import select, text

from . import models as m
from .contracts import Contract, digest
from .services import scoped, require
from .relations import lock_current_actor
from .media_daily_task_adapter import current_actor
from . import native_pixel_daily_v250 as daily
from . import native_pixel_provenance_v250 as origin

ACTION = 'native_pixel.daily_rebuild_saved_v250'


class Rebuild(Contract):
    schema: Literal['native-pixel-daily-rebuild-v250']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    registration_day: StrictStr
    version: StrictInt = Field(ge=0)
    expected_summary_hash: StrictStr | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    expected_blocked_fingerprint: StrictStr | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    reason: StrictStr = Field(min_length=1, max_length=2000)
    confirm_rebuild: StrictBool

    @field_validator('request_id', 'reason')
    @classmethod
    def text(cls, value):
        if not value.strip() or '\x00' in value:
            raise ValueError('请保留原操作编号并填写有效重建原因')
        value.encode('utf-8', errors='strict')
        return value

    @field_validator('registration_day')
    @classmethod
    def canonical_day(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('登记日须为YYYY-MM-DD')
        return value

    @field_validator('confirm_rebuild')
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError('请明确确认只重建原登记日汇总')
        return value


def _commands(db, p, nonce):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION, m.Audit.data['body']['request_id'].as_string() == nonce).limit(2)))


def _markers(db, p, nonce):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == daily.ACTION, m.Audit.data['rebuild_request_id'].as_string() == nonce).limit(2)))


def checked_command(db, p, command, instance_id, expected_hash):
    value = command.data
    try:
        origin.need(command.actor_id == p.user_id and command.object_id == instance_id)
        body = Rebuild.model_validate(value['body'])
        origin.need(set(value) == {'schema', 'workspace_id', 'actor_id', 'instance_id', 'body', 'body_hash', 'result', 'command_hash'}
            and value['schema'] == 'native-pixel-daily-rebuild-command-v250'
            and value['workspace_id'] == p.workspace_id and value['actor_id'] == p.user_id
            and value['instance_id'] == instance_id and command.action == ACTION
            and value['body_hash'] == expected_hash == digest(body.model_dump())
            and value['command_hash'] == digest({k: v for k, v in value.items() if k != 'command_hash'}))
        result = value['result']; summary = result['summary']
        memory = origin.row(db, p, m.Memory, summary['id']); daily.checked_summary(db, p, memory)
        origin.need(memory.data['instance_id'] == instance_id and memory.data['registration_day'] == body.registration_day
            and origin.same(summary, {'id': memory.id, 'version': memory.version,
                'summary_hash': memory.data['summary_hash'], 'created_at': origin.iso(memory.created_at)})
            and origin.same(result, _result(body, value['body_hash'], instance_id, memory)))
        markers = _markers(db, p, body.request_id)
        origin.need(len(markers) == 1 and markers[0].object_id == memory.id
            and markers[0].actor_id == p.user_id and markers[0].data['rebuild_command_id'] == command.id)
        return copy.deepcopy(result)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        origin.need(False)


def _result(body, body_hash, instance_id, memory):
    return {'schema': 'native-pixel-daily-rebuild-result-v250', 'request_id': body.request_id,
        'body_hash': body_hash, 'instance_id': instance_id, 'registration_day': body.registration_day,
        'summary': {'id': memory.id, 'version': memory.version, 'summary_hash': memory.data['summary_hash'],
                    'created_at': origin.iso(memory.created_at)},
        'counts': copy.deepcopy(memory.data['counts']), 'metrics': copy.deepcopy(memory.data['metrics'])}


def lookup(db, p, instance_id, request_id, expected_body_hash):
    scoped(db, m.DomainInstance, instance_id, p)
    rows = _commands(db, p, request_id)
    origin.need(len(rows) <= 1)
    if rows and (rows[0].actor_id != p.user_id or rows[0].object_id != instance_id):
        raise HTTPException(404, '原请求不存在或不属于当前范围')
    if not rows:
        origin.need(not _markers(db, p, request_id))
    result = checked_command(db, p, rows[0], instance_id, expected_body_hash) if rows else None
    return {'schema': 'native-pixel-daily-rebuild-recovery-v250', 'found': bool(rows),
        'state': 'found' if rows else 'unknown', 'request_id': request_id,
        'expected_body_hash': expected_body_hash, 'result': result}


def rebuild(db, p, instance_id, body):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int(digest(['native-pixel-daily-rebuild-v250', p.workspace_id, body.request_id])[:15], 16)})
    body_hash = digest(body.model_dump())
    rows = _commands(db, p, body.request_id)
    origin.need(len(rows) <= 1)
    if rows:
        return checked_command(db, p, rows[0], instance_id, body_hash)
    origin.need(not _markers(db, p, body.request_id))
    require(p, 'admin', 'operator'); lock_current_actor(db, p); current_actor(db, p, write=True)
    scoped(db, m.DomainInstance, instance_id, p)
    daily.lock_day(db, p, instance_id, body.registration_day)
    previous = daily.summaries(db, p, instance_id, body.registration_day)
    errors = daily.blocked(db, p, instance_id, body.registration_day)
    head = previous[0] if previous else None
    origin.need(body.version == (head.version if head else 0)
        and body.expected_summary_hash == (head.data['summary_hash'] if head else None)
        and body.expected_blocked_fingerprint == (errors[0].data['fingerprint'] if errors else None),
        '原登记日版本已变化；原输入保留，请重新核对当前日版本')
    identity = m.uid()
    memory = daily._refresh_day(db, p, instance_id, body.registration_day,
        rebuild={'command_id': identity, 'request_id': body.request_id})
    result = _result(body, body_hash, instance_id, memory)
    value = {'schema': 'native-pixel-daily-rebuild-command-v250', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'instance_id': instance_id, 'body': body.model_dump(),
        'body_hash': body_hash, 'result': result}
    command = m.Audit(id=identity, workspace_id=p.workspace_id, actor_id=p.user_id,
        action=ACTION, object_id=instance_id, data={**value, 'command_hash': digest(value)})
    db.add(command); db.flush()
    return checked_command(db, p, command, instance_id, body_hash)
