"""Deterministic business-date interpretation; cloud models cannot choose a reference clock."""
from datetime import datetime, date, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import HTTPException
from .contracts import TimeRequirement
from .models import now


def instant(value, tz=None, *, end_date=False):
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value or ''):
            if not tz: raise ValueError('date needs business timezone')
            day = date.fromisoformat(value) + timedelta(days=int(end_date))
            return datetime.combine(day, time.min, tz).astimezone(timezone.utc)
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None: raise ValueError('offset required')
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '时间必须是带偏移量的时间戳；业务日期需要明确时区')


def resolve(value, reference=None):
    spec = TimeRequirement.model_validate(value or {}).model_dump()
    reference = reference or now()
    try:
        tz = ZoneInfo(spec['timezone']) if spec['timezone'] else None
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, '业务时区不是有效IANA时区')
    mode = spec['mode']; start = end = at = None
    permitted = {'archive':set(),'latest':{'max_age_seconds'},'range':{'start','end'},
        'as_of':{'at','max_age_seconds'},'relative':{'relative','days'}}[mode]
    if any(spec[k] is not None for k in {'start','end','at','relative','days','max_age_seconds'} - permitted):
        raise HTTPException(422,'时间模式与附加参数不一致，请核对日期要求')
    if mode == 'archive':
        if any(spec[k] is not None for k in ('start','end','at','relative','days','max_age_seconds')):
            raise HTTPException(422, '档案模式不能混入观测时间窗')
    elif mode == 'latest':
        if not spec['max_age_seconds']: raise HTTPException(422, '实时证据需要明确可接受时效')
        start = reference - timedelta(seconds=spec['max_age_seconds']); end = reference
    elif mode == 'range':
        if not spec['start'] or not spec['end']: raise HTTPException(422, '时间范围需要开始和结束日期')
        start = instant(spec['start'], tz); end = instant(spec['end'], tz, end_date=True)
        if start >= end: raise HTTPException(422, '结束时间必须晚于开始时间')
    elif mode == 'as_of':
        if not spec['at']: raise HTTPException(422, '历史时点需要明确带偏移量时间')
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',spec['at']):raise HTTPException(422,'历史时点需要明确时刻；按日查询请使用日期范围')
        at = instant(spec['at'], tz)
        if at > reference: raise HTTPException(422, '不能把未来时点当作已存在的历史证据')
    elif mode == 'relative':
        if not tz: raise HTTPException(422, '相对日期需要明确业务时区，不能猜定')
        today = reference.astimezone(tz).date(); relative = spec['relative']
        if relative == 'today': first, last = today, today + timedelta(days=1)
        elif relative == 'yesterday': first, last = today - timedelta(days=1), today
        elif relative in {'this_week', 'last_week'}:
            first = today - timedelta(days=today.weekday() + (7 if relative == 'last_week' else 0))
            last = first + timedelta(days=7)
        elif relative == 'last_days' and spec['days']:
            first, last = today - timedelta(days=spec['days'] - 1), today + timedelta(days=1)
        else: raise HTTPException(422, '相对日期缺少可解析类型或天数')
        # Convert local midnight independently: a DST business day need not have 24 hours.
        start = datetime.combine(first, time.min, tz).astimezone(timezone.utc)
        end = datetime.combine(last, time.min, tz).astimezone(timezone.utc)
        mode = 'range'
    return {'mode': mode, 'timezone': spec['timezone'], 'start': start.isoformat() if start else None,
        'end': end.isoformat() if end else None, 'at': at.isoformat() if at else None,
        'reference_at': reference.isoformat(), 'max_age_seconds': spec['max_age_seconds'],
        'method': 'deterministic_business_time', 'original': spec}


def matches(observed_at, window, evaluated_at=None):
    evaluated_at = evaluated_at or now()
    if observed_at and observed_at > evaluated_at:return False
    if window['mode'] == 'archive': return True
    if not observed_at or observed_at > evaluated_at: return False
    if window['mode'] == 'as_of':
        at=instant(window['at'])
        return observed_at<=at and (not window['max_age_seconds'] or observed_at >= at-timedelta(seconds=window['max_age_seconds']))
    start, end = instant(window['start']), instant(window['end'])
    return start <= observed_at <= end if window['mode'] == 'latest' else start <= observed_at < end


def question_time(question):
    """Only unambiguous supported hints; caller preserves missing timezone as clarification."""
    if '昨天' in question or '昨日' in question: return {'mode':'relative','relative':'yesterday'}
    if '今天' in question or '今日' in question: return {'mode':'relative','relative':'today'}
    if '上周' in question: return {'mode':'relative','relative':'last_week'}
    if '本周' in question or '这周' in question: return {'mode':'relative','relative':'this_week'}
    match = re.search(r'(?:最近|近|过去)\s*(\d+)\s*天', question)
    if match: return {'mode':'relative','relative':'last_days','days':int(match.group(1))}
    return None
