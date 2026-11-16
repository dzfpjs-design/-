"""Reuse an intact native daily receipt across SQL display time zones only.

Only records[*].created_at is a SQL display field here. Every other value,
including all original source clocks and typed numbers, remains byte-hashed by
the existing digest algorithm. This helper never repairs or re-signs history.
"""
import copy
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from . import models as m
from .contracts import digest


def _need(value):
    if not value:
        raise HTTPException(409, '每日回执与原交付不一致，请重新归集；原记录保留')


def _instant(value):
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def reuse_saved_daily(db, p, memory, candidate):
    """Return the original {value,key}, or None for a real/current difference.

    Bad original Event/Memory integrity raises the same safe 409 that reconcile
    already handles with a repair key. A display comparison cannot bypass that
    original cold check, and it never creates a replacement snapshot.
    """
    from .native_receipts import checked_daily
    actual = db.scalar(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        m.Memory.id == memory.id).execution_options(populate_existing=True))
    _need(actual is not None)
    saved = checked_daily(db, p, actual)
    event = db.scalar(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.id == actual.data['event_id']).execution_options(populate_existing=True))
    # checked_daily preserves the old public protocol. At this reuse boundary
    # also distinguish bool/int, integer/float and the sign of typed zero.
    _need(event is not None and digest(event.data) == digest(saved))
    if (type(candidate) is not dict or type(saved) is not dict
            or candidate.get('schema') != 'native-multimodal-daily-v1'
            or saved.get('schema') != candidate['schema']
            or type(candidate.get('records')) is not list or type(saved.get('records')) is not list
            or len(candidate['records']) != len(saved['records'])):
        return None
    current_records, original_records, ids = [], [], []
    for current, original in zip(candidate['records'], saved['records']):
        if (type(current) is not dict or type(original) is not dict
                or type(current.get('id')) is not str or current['id'] != original.get('id')
                or current['id'] in ids or 'created_at' not in current or 'created_at' not in original):
            return None
        ids.append(current['id'])
        current_time, original_time = _instant(current['created_at']), _instant(original['created_at'])
        if current_time is None or original_time is None:
            return None
        observation = db.scalar(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
            m.Observation.id == current['id']).execution_options(populate_existing=True))
        if (observation is None or observation.created_at is None
                or observation.created_at.tzinfo is None or observation.created_at.utcoffset() is None
                or observation.data.get('native_instance_id') != candidate.get('instance_id')
                or observation.business_day != candidate.get('business_day')
                or current_time != observation.created_at.astimezone(timezone.utc)
                or original_time != observation.created_at.astimezone(timezone.utc)):
            return None
        current_records.append({key: value for key, value in current.items() if key != 'created_at'})
        original_records.append({key: value for key, value in original.items() if key != 'created_at'})
    current_value = {**candidate, 'records': current_records}
    original_value = {**saved, 'records': original_records}
    if digest(current_value) != digest(original_value):
        return None
    # Preserve the exact original event key, including a genuine repair suffix.
    return {'value': copy.deepcopy(saved), 'key': event.idempotency_key}
