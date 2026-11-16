"""Continuous, explicitly armed business rules and proved ordinary Task starts.

Commands and occurrence witnesses are independent of the operational clock and
heartbeat. Reads do not arm, poll, repair sources or execute any work.
"""
import copy
import hashlib
from datetime import datetime, time, timedelta, timezone
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, text
from . import models as m
from .contracts import digest
from .services import audit, scoped, require
from .business_automation_models import BusinessAutomationSubscription as Subscription
from .business_automation_contracts import (
    need, parse, uuid, sha, ERROR, PROVIDER, JOB_KIND, CREATED, COMMAND, HEAD, ORIGIN, STATE, TASK)

CHINA = ZoneInfo('Asia/Shanghai')
TERMINAL = {'task_created', 'linked_existing_task', 'blocked', 'skipped', 'cancelled'}
WAITING = {'waiting_source', 'waiting_limit'}
OCCURRENCE_STATES = TERMINAL | WAITING | {'queued', 'running'}


def iso(value):
    if value is None: return None
    need(isinstance(value, datetime) and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat()


def instant(value):
    # SQLAlchemy returns the real PostgreSQL timestamptz as datetime; saved
    # immutable JSON uses an offset string. This local boundary accepts both
    # without broadening the shared HTTP timestamp parser or guessing a zone.
    if isinstance(value, datetime):
        try:
            if value.tzinfo is None or value.utcoffset() is None: raise ValueError()
            return value.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            raise HTTPException(422, '自动业务时间必须具有明确偏移量，不能猜定本地时区') from None
    from .temporal import instant as checked
    return checked(value)


def clock(db): return db.scalar(select(func.clock_timestamp()))


def lock(db, value):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest(value)[:15], 16)})


def reader(db, p):
    from .media_daily_task_adapter import current_actor
    return current_actor(db, p)


def actor(db, p):
    require(p, 'admin', 'operator')
    from .relations import lock_current_actor
    lock_current_actor(db, p)


def _events(db, p, action, identity, limit=100):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity).order_by(m.Audit.created_at, m.Audit.id).limit(limit+1)))
    need(len(rows) <= limit)
    return rows


def _sealed(db, p, action, identity, value):
    events = _events(db, p, action, identity, 1)
    need(len(events) == 1 and events[0].data == value)
    return events[0]


def _scope(db, p, instance_id, release_id, business_id, *, write=False):
    from .instance_api import active
    instance, release = active(db, p, instance_id, write)
    need(release.id == release_id, '实例已换版，请重新核对持续业务范围')
    businesses = release.data['snapshot']['businesses']
    found = [business for business in businesses if business['id'] == business_id]
    need(len(found) == 1 and business_id in instance.data.get('enabled_business_ids', []), '请选择当前实例已启用的业务')
    return instance, release, found[0]


def first_due(authorized_at, hhmm):
    """A strictly future China wall-clock occurrence, stored as UTC."""
    need(isinstance(authorized_at, datetime) and authorized_at.utcoffset() is not None)
    try:
        need(type(hhmm) is str and len(hhmm) == 5 and hhmm[2] == ':')
        hour, minute = map(int, hhmm.split(':'))
        need(f'{hour:02d}:{minute:02d}' == hhmm)
        local = authorized_at.astimezone(CHINA)
        result = datetime.combine(local.date(), time(hour, minute), CHINA)
    except (ValueError, TypeError): need(False, '发布规则中的中国时间无效')
    if result <= authorized_at: result += timedelta(days=1)
    return result.astimezone(timezone.utc)


def preview_digest(selection, pins):
    return digest({'schema': 'business-automation-preview-proof-v1', 'selection': selection, 'pins': pins})


def _declaration(release, business):
    from .business_automation_admission import policy
    return policy(release.data['snapshot'], business)


def options(db, p, instance_id, release_id, business_id):
    reader(db, p)
    instance, release, business = _scope(db, p, instance_id, release_id, business_id)
    from .business_automation_sources import catalog
    source = catalog(db, p, instance.id, release.id, business['id'])
    declaration = _declaration(release, business)
    blockers = [] if declaration else ['当前发布没有已确认的持续启动许可，请核对自动化范围并生成新发布']
    triggers = [{'id': key, 'label': label,
        'allowed': bool(declaration and key in declaration['allowed_triggers']),
        'time': declaration['time'] if declaration and key == 'daily_time' else None,
        'blockers': blockers if not declaration else [] if key in declaration['allowed_triggers'] else ['此触发未在发布规则中确认']}
        for key, label in [('new_data', '收到新资料时'), ('daily_time', '每天中国时间')]]
    rows = _subscriptions(db, p, instance.id)
    value = {'schema': 'business-automation-options-v1', 'instance_id': instance.id,
        'release_id': release.id, 'configuration_hash': release.configuration_hash,
        'business': {'id': business['id'], 'label': business['name'],
            'policy_status': 'confirmed' if declaration else 'unconfirmed',
            'policy_hash': declaration['policy_hash'] if declaration else None},
        'triggers': triggers, 'providers': [{'id': PROVIDER, 'label': '已独立接收的摄像资料',
            'supported': bool(declaration and PROVIDER in declaration['provider_ids']), 'limitations': source['limitations']}],
        'entities': source['entities'], 'cameras': source['cameras'],
        'subscriptions': [{'id': row.id, 'version': row.version, 'status': row.status,
            'trigger_id': row.data['origin']['selection']['trigger_id']} for row in rows if row.business_id == business_id],
        'permissions': {'can_enable': p.role in {'admin', 'operator'}}, 'limited': source['limited'],
        'limitations': source['limitations']}
    return {**value, 'options_hash': digest(value)}


def _prepare(db, p, instance_id, selection, *, write=False):
    instance, release, business = _scope(db, p, instance_id, selection['release_id'], selection['business_id'], write=write)
    from .business_automation_sources import validate_selection, catalog
    declaration = _declaration(release, business)
    need(declaration is not None, '当前发布缺少已确认的持续业务许可，请先核对规则并重新发布')
    need(selection['trigger_id'] in declaration['allowed_triggers'] and selection['provider_id'] in declaration['provider_ids'],
        '当前发布没有批准所选触发或资料来源')
    target = validate_selection(db, p, instance, release, business, selection, write=write)
    pins = {'workspace_id': p.workspace_id, 'instance_id': instance.id, 'release_id': release.id,
        'configuration_hash': release.configuration_hash, 'business_id': business['id'],
        **{key: declaration[key] for key in ('policy_hash', 'admission_hash', 'flow_hash', 'graph_hash', 'capabilities_hash', 'roles_hash')},
        'source_target_hash': digest(target)}
    labels = catalog(db, p, instance.id, release.id, business['id'])
    entity = next((item['label'] for item in labels['entities'] if item['id'] == selection['entity_id']), None)
    camera = next((item['label'] for item in labels['cameras'] if item['id'] == selection['camera_id']), None)
    need(entity is not None and camera is not None)
    lines = [f"业务：{business['name']}；已发布第{release.release_number}版。", f'对象：{entity}；摄像头：{camera}。',
        '收到新的完整资料后启动普通业务任务。' if selection['trigger_id'] == 'new_data'
        else f"每天中国时间{declaration['time']}，采用最近24小时已独立接收的完整资料。",
        '持续授权直到明确停用、撤权或配置变化；原节点审批、设备许可和预算分别保留。',
        '接收时间不能证明拍摄时间，公开或模拟资料不能证明现场业务效果。']
    limits = selection['limits']
    lines.append('新建任务限制：'+('每日不限次数' if limits['max_task_starts_per_day'] is None
        else f"每日最多{limits['max_task_starts_per_day']}次")+f"，最短间隔{limits['min_interval_seconds']}秒；不代替模型费用限制。")
    return instance, release, business, declaration, target, pins, lines


def preview(db, p, instance_id, body):
    available = options(db, p, instance_id, body['selection']['release_id'], body['selection']['business_id'])
    need(body['expected_options_hash'] == available['options_hash'], '业务或来源目录已变化，请重新核对')
    blockers = []
    if p.role not in {'admin', 'operator'}: blockers.append('当前账号只可查看，不能批准持续执行')
    try:
        _, _, _, declaration, target, pins, lines = _prepare(db, p, instance_id, body['selection'])
    except HTTPException as error:
        if error.status_code not in {404, 409, 422}: raise
        # A blocked preview is not an authorization. Its pins are deliberately
        # complete in shape, but cannot pass enable's actual _prepare call.
        blockers.append(str(error.detail))
        empty = digest(None)
        pins = {'workspace_id': p.workspace_id, 'instance_id': instance_id,
            'release_id': body['selection']['release_id'], 'configuration_hash': available['configuration_hash'],
            'business_id': body['selection']['business_id'],
            **{key: empty for key in ('policy_hash', 'admission_hash', 'flow_hash', 'graph_hash', 'capabilities_hash', 'roles_hash', 'source_target_hash')}}
        declaration, target, lines = None, None, ['当前选择受阻，未启用、未创建任务或读取设备。']
    unit = None
    if target is not None:
        from .business_automation_sources import select_unit
        unit = select_unit(db, p, body['selection'], target, at=clock(db))
    due = first_due(clock(db), declaration['time']) if declaration and body['selection']['trigger_id'] == 'daily_time' else None
    return {'schema': 'business-automation-preview-v1', 'selection': copy.deepcopy(body['selection']), 'pins': pins,
        'preview_hash': preview_digest(body['selection'], pins), 'can_enable': not blockers, 'blockers': blockers,
        'scope_lines': lines, 'first_due_at': iso(due), 'source_state': 'ready' if unit else 'waiting',
        'limitations': ['预览只核当前许可和来源元数据，实际派发仍完整核对原件及独立收件。',
            '资料暂缺可启用为等待；接收新近不代表拍摄时间已核实。']}


def _head(db, p, row, status, *, reason=None, superseded_by=None, initial=False):
    previous = row.data.get('head_hash')
    value = {'status': status, 'version': 1 if initial else row.version+1,
        'reason': reason, 'superseded_by': superseded_by}
    row.status, row.version = status, value['version']
    row.data = {**row.data, 'head': value, 'head_hash': digest(value)}
    audit(db, p, HEAD, row.id, {'authorization_hash': row.authorization_hash, 'previous_hash': previous,
        'head': value, 'head_hash': digest(value)})


def checked_subscription(db, p, row):
    """Immutable authorization + command state; never current source admission."""
    try:
        need(row.workspace_id == p.workspace_id)
        origin = row.data['origin']
        need(row.authorization_hash == row.data['origin_hash'] == digest(origin))
        created = _sealed(db, p, CREATED, row.id, {**origin, 'authorization_hash': row.authorization_hash})
        need(created.actor_id == row.actor_id == origin['actor_id'] and origin['workspace_id'] == row.workspace_id
            and origin['id'] == row.id and origin['instance_id'] == row.instance_id and origin['release_id'] == row.release_id
            and origin['business_id'] == row.business_id and origin['request_id'] == row.request_id
            and origin['body_sha256'] == row.body_sha256 == hashlib.sha256(origin['exact_body'].encode('utf8')).hexdigest()
            and iso(row.authorized_at) == origin['authorized_at'] and origin['supersedes_id'] == row.supersedes_id)
        body = parse(origin['exact_body'].encode('utf8'), 'resume' if row.supersedes_id else 'enable')
        need(body['request_id'] == row.request_id and body['selection'] == origin['selection']
            and body['expected_preview_hash'] == preview_digest(origin['selection'], origin['pins']))
        pins = origin['pins']; selection = origin['selection']
        need(pins['workspace_id'] == row.workspace_id and pins['instance_id'] == row.instance_id
            and pins['release_id'] == row.release_id and pins['business_id'] == row.business_id
            and selection['release_id'] == row.release_id and selection['business_id'] == row.business_id
            and pins['source_target_hash'] == digest(origin['source_target'])
            and pins['policy_hash'] == origin['policy']['policy_hash'])
        events = _events(db, p, HEAD, row.id, 10)
        values = sorted((event.data for event in events), key=lambda value: value['head']['version'])
        need(len(values) == row.version and row.version >= 1)
        previous = None
        for version, value in enumerate(values, 1):
            need(value['authorization_hash'] == row.authorization_hash and value['previous_hash'] == previous
                and value['head']['version'] == version and value['head_hash'] == digest(value['head']))
            previous = value['head_hash']
        need(values[-1]['head'] == row.data['head'] and previous == row.data['head_hash']
            and row.data['head']['status'] == row.status and row.data['head']['version'] == row.version)
        return copy.deepcopy(origin)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError): need(False)


def _subscriptions(db, p, instance_id):
    # Reverse proof union: deleting a grant cannot turn original-nonce recovery
    # into found:false, or silently erase a rule from this instance's history.
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == CREATED, m.Audit.data['instance_id'].as_string() == instance_id)))
    rows = list(db.scalars(select(Subscription).where(Subscription.workspace_id == p.workspace_id,
        Subscription.instance_id == instance_id)))
    need({row.id for row in rows} == {proof.object_id for proof in proofs})
    for row in rows: checked_subscription(db, p, row)
    return rows


def _command(db, p, row, operation, raw, *, target_id=None):
    body = parse(raw, operation)
    value = {'operation': operation, 'request_id': body['request_id'], 'exact_body': raw.decode('utf8'),
        'body_sha256': hashlib.sha256(raw).hexdigest(), 'actor_id': p.user_id, 'subscription_id': row.id,
        'instance_id': row.instance_id, 'target_id': target_id, 'written_version': row.version}
    audit(db, p, COMMAND, row.id, {**value, 'record_hash': digest(value)})


def recover(db, p, instance_id, request_id, expected_sha256):
    reader(db, p); uuid(instance_id); uuid(request_id); sha(expected_sha256)
    records = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND, m.Audit.data['request_id'].as_string() == request_id).limit(2)))
    origins = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == CREATED, m.Audit.data['request_id'].as_string() == request_id).limit(2)))
    rows = list(db.scalars(select(Subscription).where(Subscription.workspace_id == p.workspace_id,
        Subscription.request_id == request_id).limit(2)))
    need(len(records) <= 1 and len(origins) <= 1 and len(rows) <= 1)
    if not records:
        need(not origins and not rows)
        return {'schema': 'business-automation-recovery-v1', 'request_id': request_id,
            'body_sha256': expected_sha256, 'found': False, 'result': None}
    event = records[0]; value = event.data
    need(event.actor_id == value['actor_id'] == p.user_id, '请由原提交账号恢复此原请求', 403)
    need(value['instance_id'] == instance_id and value['body_sha256'] == expected_sha256
        and value['record_hash'] == digest({key: item for key, item in value.items() if key != 'record_hash'}),
        '原请求内容或所在实例不同，原授权保留')
    parsed = parse(value['exact_body'].encode('utf8'), value['operation'])
    need(parsed['request_id'] == request_id and hashlib.sha256(value['exact_body'].encode('utf8')).hexdigest() == expected_sha256)
    row = scoped(db, Subscription, value['subscription_id'], p)
    need(row.id == event.object_id and row.instance_id == instance_id and 1 <= value['written_version'] <= row.version)
    checked_subscription(db, p, row)
    result = {'schema': 'business-automation-command-v1', 'operation': value['operation'],
        'request_id': request_id, 'body_sha256': expected_sha256, 'subscription': view(db, p, row)}
    return {'schema': 'business-automation-recovery-v1', 'request_id': request_id,
        'body_sha256': expected_sha256, 'found': True, 'result': result}


def command(db, p, instance_id, raw, operation, subscription_id=None):
    body = parse(raw, operation); uuid(instance_id)
    lock(db, ['business-automation-command-v1', p.workspace_id, body['request_id']])
    prior = recover(db, p, instance_id, body['request_id'], hashlib.sha256(raw).hexdigest())
    if prior['found']:
        value = prior['result']; need(value['operation'] == operation)
        event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == COMMAND, m.Audit.data['request_id'].as_string() == body['request_id']))
        need(event.data['target_id'] == subscription_id)
        return value, False
    actor(db, p)
    # This instance lock is acquired only after the actor. Existing running
    # occurrence Jobs are never synchronously cancelled/locked by this path.
    scoped(db, m.DomainInstance, instance_id, p, True)
    old = None
    if operation != 'enable':
        need(subscription_id is not None); old = scoped(db, Subscription, subscription_id, p, True)
        checked_subscription(db, p, old)
        need(old.instance_id == instance_id and old.version == body['version'], '授权已变化，请重新核对')
        need(p.role == 'admin' or old.actor_id == p.user_id, '请由原授权者或管理员启停此规则', 403)
    if operation == 'pause':
        need(old.status in {'active', 'blocked'}, '此授权已停用或被新授权替代')
        _head(db, p, old, 'paused', reason=body['reason'])
        _command(db, p, old, operation, raw, target_id=subscription_id); db.flush()
        return {'schema': 'business-automation-command-v1', 'operation': operation, 'request_id': body['request_id'],
            'body_sha256': hashlib.sha256(raw).hexdigest(), 'subscription': view(db, p, old)}, True
    need(operation in {'enable', 'resume'})
    if old:
        need(old.status in {'paused', 'blocked'}, '请先明确停用旧授权，再重新核对范围')
        original = old.data['origin']['selection']
        need(body['selection']['business_id'] == old.business_id and body['selection']['trigger_id'] == original['trigger_id'],
            '重新授权须保持原实例、业务及触发类型')
    instance, release, business, declaration, target, pins, lines = _prepare(db, p, instance_id, body['selection'], write=True)
    need(body['expected_preview_hash'] == preview_digest(body['selection'], pins), '业务、对象或来源配置已变化，请重新预览')
    lock(db, ['business-automation-scope-v1', p.workspace_id, instance.id, body['selection']])
    for existing in _subscriptions(db, p, instance.id):
        old_selection = existing.data['origin']['selection']
        if existing.status == 'active' and all(old_selection[key] == value
                for key,value in body['selection'].items() if key != 'limits'):
            need(False, '此持续范围已启用，请查看原授权，未重复启用')
    at = clock(db); identity = str(uuid4()); user = db.get(m.User, p.user_id)
    origin = {'id': identity, 'workspace_id': p.workspace_id, 'instance_id': instance.id, 'release_id': release.id,
        'business_id': business['id'], 'actor_id': p.user_id, 'request_id': body['request_id'],
        'exact_body': raw.decode('utf8'), 'body_sha256': hashlib.sha256(raw).hexdigest(),
        'selection': copy.deepcopy(body['selection']), 'pins': pins, 'source_target': target, 'policy': declaration,
        'business_label': business['name'], 'authorized_by': user.username, 'authorized_at': iso(at),
        'supersedes_id': old.id if old else None, 'scope_lines': lines,
        'limitations': ['自动启动普通任务，不代表已通过人工审批或已执行外部动作。', '拍摄时间及现场业务状态仍需独立依据。']}
    row = Subscription(id=identity, workspace_id=p.workspace_id, instance_id=instance.id, release_id=release.id,
        business_id=business['id'], actor_id=p.user_id, request_id=body['request_id'], body_sha256=origin['body_sha256'],
        authorization_hash=digest(origin), authorized_at=at, supersedes_id=old.id if old else None,
        next_due_at=first_due(at, declaration['time']) if body['selection']['trigger_id'] == 'daily_time' else None,
        data={'origin': origin, 'origin_hash': digest(origin)})
    db.add(row); db.flush(); audit(db, p, CREATED, row.id, {**origin, 'authorization_hash': row.authorization_hash})
    _head(db, p, row, 'active', initial=True)
    if old: _head(db, p, old, 'superseded', reason='已明确核对新范围并产生新授权，旧任务保留', superseded_by=row.id)
    _command(db, p, row, operation, raw, target_id=subscription_id); db.flush()
    # Recheck under the same held authority/instance locks before the caller's
    # atomic commit. Preview or old confirmed configuration is never a grant.
    _prepare(db, p, instance_id, body['selection'], write=True)
    return {'schema': 'business-automation-command-v1', 'operation': operation, 'request_id': body['request_id'],
        'body_sha256': origin['body_sha256'], 'subscription': view(db, p, row)}, True


def _state(db, p, job, value):
    need(type(value) is dict and value['status'] in OCCURRENCE_STATES)
    previous = job.data.get('state_hash')
    if previous is not None and job.data.get('state') == value: return
    index = job.data.get('state_index', 0)+1
    job.data = {**job.data, 'state': copy.deepcopy(value), 'state_hash': digest(value), 'state_index': index}
    audit(db, p, STATE, job.id, {'job_id': job.id, 'subscription_id': job.data['subscription_id'],
        'authorization_hash': job.data['authorization_hash'], 'index': index,
        'previous_hash': previous, 'state': copy.deepcopy(value), 'state_hash': digest(value)})


def occurrence_key(row, day, unit=None):
    selection = row.data['origin']['selection']
    part = unit['run_id'] if selection['trigger_id'] == 'new_data' else day
    return 'automation:'+digest({'subscription_id': row.id, 'authorization_hash': row.authorization_hash,
        'trigger': selection['trigger_id'], 'part': part})


def checked_occurrence(db, p, job):
    """Read original occurrence and state witnesses, without live permissions."""
    try:
        need(job.workspace_id == p.workspace_id and job.kind == JOB_KIND)
        row = scoped(db, Subscription, job.data['subscription_id'], p)
        authority = checked_subscription(db, p, row)
        origin, state = job.data['origin'], job.data['state']
        need(job.data['actor_id'] == row.actor_id and job.data['authorization_hash'] == row.authorization_hash
            and origin['subscription_id'] == row.id and origin['authorization_hash'] == row.authorization_hash
            and job.data['origin_hash'] == digest(origin) and origin['trigger_id'] == authority['selection']['trigger_id'])
        event = _sealed(db, p, ORIGIN, job.id, {'job_id': job.id, 'workspace_id': p.workspace_id,
            'actor_id': row.actor_id, 'origin': origin, 'origin_hash': digest(origin)})
        need(event.actor_id == row.actor_id and job.idempotency_key == origin['occurrence_key'])
        if origin['trigger_id'] == 'daily_time':
            scheduled = instant(origin['scheduled_for'])
            need(scheduled >= first_due(row.authorized_at, authority['policy']['time'])
                and scheduled.astimezone(CHINA).strftime('%Y-%m-%d') == origin['day']
                and scheduled.astimezone(CHINA).strftime('%H:%M') == authority['policy']['time']
                and job.idempotency_key == occurrence_key(row, origin['day']))
        else:
            need(origin['scheduled_for'] is None and type(origin['unit_id']) is str)
            need(job.idempotency_key == occurrence_key(row, origin['day'], {'run_id': origin['unit_id']}))
        need(state['status'] in OCCURRENCE_STATES and job.data['state_hash'] == digest(state)
            and type(job.data['state_index']) is int and job.data['state_index'] >= 1)
        events = _events(db, p, STATE, job.id, 200)
        values = sorted((event.data for event in events), key=lambda value: value['index'])
        need(len(values) == job.data['state_index']); previous = None
        for index, value in enumerate(values, 1):
            need(value['job_id'] == job.id and value['subscription_id'] == row.id
                and value['authorization_hash'] == row.authorization_hash and value['index'] == index
                and value['previous_hash'] == previous and value['state_hash'] == digest(value['state']))
            previous = value['state_hash']
        need(previous == job.data['state_hash'] and values[-1]['state'] == state)
        unit = state['source_unit']
        if unit is not None:
            need(unit['run_id'] == unit['unit_id'] and unit['source_hash'] == digest(unit['source_pin'])
                and unit['target_hash'] == digest(authority['source_target']))
            if origin['trigger_id'] == 'new_data':
                need(unit['run_id'] == origin['unit_id'] and unit['source_pin'] == origin['source_pin']
                    and instant(unit['received_at']) > row.authorized_at
                    and instant(unit['received_at']).astimezone(CHINA).strftime('%Y-%m-%d') == origin['day'])
        if state['task_id'] is not None:
            from .workspace_api import NewTask, creation_body_hash
            need(state['task_body'] is not None and state['task_body_hash'] == creation_body_hash(NewTask.model_validate(state['task_body'])) and unit is not None)
        return row, copy.deepcopy(origin), copy.deepcopy(state)
    except (KeyError, ValueError, TypeError, AttributeError, IndexError): need(False)


def _ensure_occurrence(db, p, row, day, *, scheduled=None, unit=None, skipped=False):
    key = occurrence_key(row, day, unit)
    existing = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.idempotency_key == key))
    if existing:
        checked_occurrence(db, p, existing); return existing
    # A surviving audit cannot be adopted as a new empty occurrence.
    need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == ORIGIN,
        m.Audit.data['origin']['occurrence_key'].as_string() == key).limit(1)) is None)
    trigger = row.data['origin']['selection']['trigger_id']
    origin = {'subscription_id': row.id, 'authorization_hash': row.authorization_hash,
        'trigger_id': trigger, 'occurrence_key': key, 'day': day, 'scheduled_for': iso(scheduled),
        'unit_id': unit['run_id'] if unit else None, 'source_pin': copy.deepcopy(unit['source_pin']) if unit else None,
        'cause_label': '收到新的完整资料' if trigger == 'new_data' else '每天中国时间'+row.data['origin']['policy']['time']}
    state = {'status': 'skipped' if skipped else 'queued' if unit else 'waiting_source',
        'reason': '已错过原中国日期，保留记录，不自动追跑旧日任务' if skipped else None if unit else '尚无合格完整资料，等待至本中国日期结束',
        'source_unit': copy.deepcopy(unit), 'source_label': unit['source_label'] if unit else None,
        'task_id': None, 'task_body': None, 'task_body_hash': None, 'image_id': None}
    job = m.Job(workspace_id=p.workspace_id, kind=JOB_KIND, idempotency_key=key,
        status='cancelled' if skipped else 'queued' if unit else 'waiting_source',
        data={'actor_id': row.actor_id, 'subscription_id': row.id, 'authorization_hash': row.authorization_hash,
            'origin': origin, 'origin_hash': digest(origin)})
    db.add(job); db.flush()
    audit(db, p, ORIGIN, job.id, {'job_id': job.id, 'workspace_id': p.workspace_id, 'actor_id': row.actor_id,
        'origin': origin, 'origin_hash': digest(origin)})
    _state(db, p, job, state); db.flush()
    return job


def assert_dispatch_live(db, p, subscription, job):
    """All fresh gates remain actual DB/source reads, including before commit."""
    from .leases import ownership
    actor(db, p)
    instance, release, _ = _scope(db, p, subscription.instance_id, subscription.release_id, subscription.business_id, write=True)
    row = scoped(db, Subscription, subscription.id, p, True)
    origin = checked_subscription(db, p, row)
    need(row.status == 'active' and row.actor_id == p.user_id, '此持续授权已停用、撤销或由新授权替代')
    owned = ownership(db)
    need(owned.get('job_id') == job.id and owned.get('lease_token') == job.lease_token
        and job.status == 'running' and job.lease_until is not None and job.lease_until > clock(db),
        '本次启动没有当前有效任务租约')
    actual, _, _ = checked_occurrence(db, p, job); need(actual.id == row.id)
    _, _, _, _, _, pins, _ = _prepare(db, p, row.instance_id, origin['selection'], write=True)
    need(pins == origin['pins'], '持续业务、对象、角色、能力或来源配置已变化，需明确重新授权')
    return instance, release, origin


def merge_key(row, origin, unit):
    value = row.data['origin']; selection = value['selection']
    return digest({'schema': 'business-automation-task-merge-v1', 'workspace_id': row.workspace_id,
        'instance_id': row.instance_id, 'release_id': row.release_id, 'business_id': row.business_id,
        'entity_id': selection['entity_id'], 'camera_id': selection['camera_id'], 'provider_id': selection['provider_id'],
        'run_id': unit['run_id'], 'day': origin['day'], 'graph_hash': value['pins']['graph_hash']})


def _task_event(db, p, key):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == TASK, m.Audit.data['merge_key'].as_string() == key).limit(2)))
    need(len(rows) <= 1)
    return rows[0] if rows else None


def checked_task_witness(db, p, task):
    """Fixed Task proof only. The source adapter separately cold-reads bytes."""
    from .workspace_api import NewTask, creation_body_hash
    try:
        value = task.data.get('business_automation_v1')
        events = _events(db, p, TASK, task.id, 1)
        if value is None and not events: return None
        need(type(value) is dict and len(events) == 1 and task.workspace_id == p.workspace_id)
        record = events[0].data
        need(record['record_hash'] == digest({key: item for key, item in record.items() if key != 'record_hash'})
            and record['task_id'] == task.id and record['workspace_id'] == p.workspace_id)
        job = scoped(db, m.Job, record['occurrence_id'], p)
        row, origin, state = checked_occurrence(db, p, job)
        need(events[0].actor_id == task.owner_id == row.actor_id and record['authorization_hash'] == row.authorization_hash
            and record['subscription_id'] == row.id and record['unit'] == state['source_unit']
            and state['task_id'] == task.id and state['task_body'] == record['body']
            and state['task_body_hash'] == record['body_hash'] and state['image_id'] == record['image_id']
            and record['body_hash'] == creation_body_hash(NewTask.model_validate(record['body']))
            and record['merge_key'] == merge_key(row, origin, record['unit'])
            and value == {'subscription_id': row.id, 'occurrence_id': job.id,
                'authorization_hash': row.authorization_hash, 'merge_key': record['merge_key'], 'body_hash': record['body_hash']})
        body = NewTask.model_validate(record['body'])
        need(task.data['creation_request_id'] == body.request_id == 'auto_'+record['merge_key']
            and task.data['creation_hash'] == creation_body_hash(body) == record['creation_hash']
            and task.data['instance_id'] == row.instance_id and task.data['instance_release_id'] == row.release_id
            and task.data['business_id'] == row.business_id and task.data['entity_ids'] == [row.data['origin']['selection']['entity_id']]
            and task.data['image_snapshots'] == [record['image_snapshot']]
            and task.data['object_snapshots'] == [record['entity_snapshot']]
            and task.data['object_definition_bindings'] == record['definition_bindings']
            and task.data['object_definition_bindings_hash'] == digest(record['definition_bindings']))
        need(body.image_ids == [record['image_id']] and record['image_snapshot']['id'] == record['image_id']
            and record['source_target'] == row.data['origin']['source_target'])
        return {'subscription': row, 'job': job, 'unit': copy.deepcopy(record['unit']), 'body': copy.deepcopy(record['body']),
            'body_hash': record['body_hash'], 'source_target': copy.deepcopy(record['source_target']),
            'image_id': record['image_id'], 'image_snapshot': copy.deepcopy(record['image_snapshot']),
            'entity_snapshot': copy.deepcopy(record['entity_snapshot']), 'definition_bindings': copy.deepcopy(record['definition_bindings'])}
    except (KeyError, TypeError, ValueError, AttributeError, IndexError): need(False)


def attach_task_witness(db, p, task, token, body):
    from .workspace_api import creation_body_hash
    job = scoped(db, m.Job, token.job_id, p)
    row, origin, state = checked_occurrence(db, p, job)
    need(token.subscription_id == row.id and state['source_unit'] == token.unit
        and token.source_target == row.data['origin']['source_target']
        and token.body_hash == creation_body_hash(body) and task.owner_id == row.actor_id)
    need(not _events(db, p, TASK, task.id, 1) and 'business_automation_v1' not in task.data)
    key = merge_key(row, origin, token.unit)
    need(_task_event(db, p, key) is None and body.request_id == 'auto_'+key)
    need(len(task.data['image_snapshots']) == len(task.data['object_snapshots']) == 1)
    from .workspace_api import creation_body_hash
    record = {'workspace_id': p.workspace_id, 'task_id': task.id, 'subscription_id': row.id,
        'occurrence_id': job.id, 'authorization_hash': row.authorization_hash, 'merge_key': key,
        'unit': copy.deepcopy(token.unit), 'body': body.model_dump(), 'body_hash': token.body_hash,
        'creation_hash': creation_body_hash(body), 'source_target': copy.deepcopy(token.source_target),
        'image_id': body.image_ids[0], 'image_snapshot': copy.deepcopy(task.data['image_snapshots'][0]),
        'entity_snapshot': copy.deepcopy(task.data['object_snapshots'][0]),
        'definition_bindings': copy.deepcopy(task.data['object_definition_bindings'])}
    task.data = {**task.data, 'business_automation_v1': {'subscription_id': row.id, 'occurrence_id': job.id,
        'authorization_hash': row.authorization_hash, 'merge_key': key, 'body_hash': token.body_hash}}
    audit(db, p, TASK, task.id, {**record, 'record_hash': digest(record)})
    # This state is written before initialize() can queue the first real node.
    _state(db, p, job, {**state, 'task_id': task.id, 'task_body': body.model_dump(),
        'task_body_hash': token.body_hash, 'image_id': body.image_ids[0], 'status': 'task_created', 'reason': None})
    db.flush()


def _limits(db, p, row, at):
    limits = row.data['origin']['selection']['limits']
    day = at.astimezone(CHINA).date()
    start = datetime.combine(day, time.min, CHINA).astimezone(timezone.utc)
    records = list(db.execute(select(m.Task.id, m.Task.created_at).join(m.Audit, m.Audit.object_id == m.Task.id).where(
        m.Task.workspace_id == p.workspace_id, m.Audit.workspace_id == p.workspace_id, m.Audit.action == TASK,
        m.Audit.data['subscription_id'].as_string() == row.id).order_by(m.Task.created_at.desc())))
    if limits['max_task_starts_per_day'] is not None and sum(created >= start for _, created in records) >= limits['max_task_starts_per_day']:
        return '已达到本授权当日新建任务上限，等待至原中国日期结束'
    if records and at < records[0][1]+timedelta(seconds=limits['min_interval_seconds']):
        return '尚未达到本授权的新任务最短间隔，原发生记录保留'
    return None


def process(db, p, job):
    row, origin, state = checked_occurrence(db, p, job)
    if state['task_id']:
        from .business_automation_admission import verify_task
        verify_task(db, p, scoped(db, m.Task, state['task_id'], p))
        return state['status']
    if state['status'] in TERMINAL: return state['status']
    if job.data.get('recovery_only') is True:
        _state(db, p, job, {**state, 'status': 'blocked', 'reason': '后台恢复次数已用完，未创建新任务；请重新核对授权'})
        return 'blocked'
    instance, release, authority = assert_dispatch_live(db, p, row, job)
    at = clock(db)
    if origin['scheduled_for']:
        need(instant(origin['scheduled_for']) <= at, '尚未到原计划中国时间，未提前启动')
    if origin['day'] < at.astimezone(CHINA).strftime('%Y-%m-%d') and origin['trigger_id'] == 'daily_time':
        _state(db, p, job, {**state, 'status': 'skipped', 'reason': '原计划中国日期已结束，未追跑旧日任务'})
        return 'skipped'
    unit = state['source_unit']
    if unit is not None and origin['trigger_id'] == 'daily_time' and not (
            at-timedelta(hours=24) <= instant(unit['received_at']) <= at):
        _state(db, p, job, {**state, 'status': 'waiting_source', 'source_unit': None, 'source_label': None,
            'reason': '原计划资料在派发时已不在最近24小时接收范围，保留本次发生并等待合格资料；未创建任务'})
        return 'waiting_source'
    if unit is None:
        _state(db, p, job, {**state, 'status': 'waiting_source', 'reason': '等待本中国日期内合格资料；未创建空任务'})
        return 'waiting_source'
    key = merge_key(row, origin, unit)
    lock(db, ['business-automation-merge-v1', p.workspace_id, key])
    existing = _task_event(db, p, key)
    if existing:
        task = scoped(db, m.Task, existing.object_id, p)
        witness = checked_task_witness(db, p, task)
        need(witness is not None and witness['unit'] == unit
            and witness['subscription'].instance_id == row.instance_id
            and witness['subscription'].release_id == row.release_id)
        from .business_automation_admission import verify_task
        verify_task(db, p, task)
        _state(db, p, job, {**state, 'status': 'linked_existing_task', 'reason': '同日同资料已启动，两个触发关联原任务',
            'task_id': task.id, 'task_body': witness['body'], 'task_body_hash': witness['body_hash'], 'image_id': witness['image_id']})
        assert_dispatch_live(db, p, row, job)
        return 'linked_existing_task'
    reason = _limits(db, p, row, at)
    if reason:
        _state(db, p, job, {**state, 'status': 'waiting_limit', 'reason': reason})
        return 'waiting_limit'
    from .business_automation_sources import materialize_input
    from .business_automation_admission import prepare_handoff
    from .workspace_api import NewTask, create_task
    # Missing/foreign ordinary request proof is never adopted, even if its
    # apparent body or image happens to match this derived merge request.
    need(db.scalar(select(m.Task.id).where(m.Task.workspace_id == p.workspace_id,
        m.Task.data['instance_id'].as_string() == row.instance_id,
        m.Task.data['creation_request_id'].as_string() == 'auto_'+key).limit(1)) is None)
    image = materialize_input(db, p, instance, unit)
    body = NewTask(release_id=release.id, business_id=row.business_id,
        entity_ids=[authority['selection']['entity_id']], title=authority['business_label']+' · '+origin['day']+'资料',
        image_ids=[image.id], request_id='auto_'+key)
    token = prepare_handoff(db, p, row, job, body, instance, release)
    outcome = {}; task = create_task(instance.id, body, p, db, automation_handoff=token, outcome=outcome)
    need(outcome.get('created') is True and checked_task_witness(db, p, task) is not None)
    assert_dispatch_live(db, p, row, job)
    from .business_automation_sources import checked_unit
    checked_unit(db, p, unit, authority['source_target'])
    return 'task_created'


def occurrence_view(db, p, job):
    row, origin, state = checked_occurrence(db, p, job)
    task = None
    if state['task_id']:
        task = scoped(db, m.Task, state['task_id'], p)
        witness = checked_task_witness(db, p, task)
        need(witness is not None and witness['unit'] == state['source_unit'])
    return {'id': job.id, 'trigger_id': origin['trigger_id'], 'cause_label': origin['cause_label'],
        'scheduled_for': origin['scheduled_for'], 'source_label': state['source_label'],
        'status': 'running' if job.status == 'running' and state['status'] == 'queued' else state['status'],
        'reason': state['reason'], 'task_id': task.id if task else None, 'task_status': task.status if task else None,
        'task_path': f'/api/workspace/tasks/{task.id}' if task else None, 'created_at': iso(job.created_at)}


def _occurrences(db, p, row):
    rows = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.kind == JOB_KIND,
        m.Job.data['subscription_id'].as_string() == row.id)))
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ORIGIN, m.Audit.data['origin']['subscription_id'].as_string() == row.id)))
    need({job.id for job in rows} == {proof.object_id for proof in proofs})
    for job in rows: checked_occurrence(db, p, job)
    return sorted(rows, key=lambda job: (job.created_at, job.id), reverse=True)


def _page(rows, cursor):
    if cursor is not None:
        try: uuid(cursor)
        except ValueError: need(False, '分页位置无效', 422)
        positions = [index for index, row in enumerate(rows) if row.id == cursor]
        need(len(positions) == 1, '分页位置不属于当前范围', 422)
        rows = rows[positions[0]+1:]
    selected = rows[:20]
    return selected, selected[-1].id if len(rows) > 20 else None


def occurrences(db, p, row, cursor=None):
    reader(db, p); checked_subscription(db, p, row)
    selected, next_cursor = _page(_occurrences(db, p, row), cursor)
    return {'schema': 'business-automation-occurrences-v1', 'subscription_id': row.id,
        'occurrences': [occurrence_view(db, p, job) for job in selected], 'next_cursor': next_cursor}


def view(db, p, row):
    origin = checked_subscription(db, p, row)
    recent = row.last_seen_at is not None and timedelta(0) <= clock(db)-row.last_seen_at <= timedelta(seconds=120)
    page = occurrences(db, p, row)
    return {'schema': 'business-automation-view-v1', 'id': row.id, 'version': row.version, 'status': row.status,
        'instance_id': row.instance_id, 'release_id': row.release_id, 'business_id': row.business_id,
        'business_label': origin['business_label'], 'selection': origin['selection'],
        'authorized_at': origin['authorized_at'], 'authorized_by': origin['authorized_by'],
        'policy_hash': origin['pins']['policy_hash'], 'configuration_hash': origin['pins']['configuration_hash'],
        'supersedes_id': row.supersedes_id, 'next_due_at': iso(row.next_due_at),
        'service': {'state': 'confirmed_recently' if recent else 'unconfirmed', 'last_seen_at': iso(row.last_seen_at)},
        'blockers': [row.data['head']['reason']] if row.status == 'blocked' else [],
        'scope_lines': origin['scope_lines'], 'occurrences': page['occurrences'],
        'occurrences_next_cursor': page['next_cursor'],
        'permissions': {'can_pause': p.role in {'admin','operator'} and (p.role == 'admin' or p.user_id == row.actor_id)
            and row.status in {'active','blocked'}, 'can_resume': p.role in {'admin','operator'}
            and (p.role == 'admin' or p.user_id == row.actor_id) and row.status in {'paused','blocked'}},
        'limitations': origin['limitations']}


def history(db, p, instance_id, cursor=None):
    reader(db, p); scoped(db, m.DomainInstance, instance_id, p)
    rows = sorted(_subscriptions(db, p, instance_id), key=lambda row: (row.created_at,row.id), reverse=True)
    selected, next_cursor = _page(rows, cursor)
    return {'schema': 'business-automation-list-v1', 'instance_id': instance_id,
        'subscriptions': [view(db, p, row) for row in selected], 'next_cursor': next_cursor}


def get(db, p, instance_id, identity):
    reader(db, p); row = scoped(db, Subscription, identity, p)
    need(row.instance_id == instance_id, '此规则不属于当前实例', 404)
    checked_subscription(db, p, row)
    return row


def fail(db, p, job, reason):
    """Independent failure receipt after the dispatch transaction rolled back."""
    _, _, state = checked_occurrence(db, p, job)
    if state['task_id'] or state['status'] in TERMINAL: return
    _state(db, p, job, {**state, 'status': 'blocked', 'reason': reason})


def poll_subscription(db, p, identity, *, limit=20):
    """One fair bounded subscription tick, clock from PostgreSQL only."""
    need(type(limit) is int and 1 <= limit <= 100)
    actor(db, p)
    original = scoped(db, Subscription, identity, p)
    scoped(db, m.DomainInstance, original.instance_id, p, True)
    row = scoped(db, Subscription, identity, p, True)
    authority = checked_subscription(db, p, row)
    at = clock(db)
    if row.last_seen_at is None or at-row.last_seen_at >= timedelta(seconds=30): row.last_seen_at = at
    row.last_polled_at = at
    if row.status != 'active': return 0
    try:
        _, _, _, _, _, pins, _ = _prepare(db, p, row.instance_id, authority['selection'], write=True)
        need(pins == authority['pins'], '持续业务、对象、角色、能力或来源已变化，请重新核对授权')
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}: raise
        _head(db, p, row, 'blocked', reason=str(error.detail)); return 0
    selection = authority['selection']; day = at.astimezone(CHINA).strftime('%Y-%m-%d'); count = 0
    from .business_automation_sources import new_units, select_unit
    # Progress columns are operational hints, never a second scheduling grant.
    # Authenticate the reverse ledger before anti-join discovery; a fabricated
    # or missing original occurrence cannot silently hide input or skip days.
    previous = _occurrences(db,p,row)
    if selection['trigger_id'] == 'daily_time':
        expected = first_due(row.authorized_at,authority['policy']['time'])
        scheduled = sorted(instant(job.data['origin']['scheduled_for']) for job in previous)
        for value in scheduled:
            need(value == expected, '原每日计划链存在缺口，未补猜或跳过原记录')
            expected += timedelta(days=1)
        need(row.next_due_at == expected, '下次计划与原发生链不符，未按可变进度列派发')
    if selection['trigger_id'] == 'new_data':
        units = new_units(db, p, selection, authority['source_target'], authorized_at=row.authorized_at, at=at,
            limit=limit, exclude_occurrence_for={'subscription_id': row.id, 'authorization_hash': row.authorization_hash})
        for unit in units:
            unit_day = instant(unit['received_at']).astimezone(CHINA).strftime('%Y-%m-%d')
            _ensure_occurrence(db, p, row, unit_day, unit=unit); count += 1
    else:
        need(row.next_due_at is not None)
        while row.next_due_at <= at and count < limit:
            due = row.next_due_at; due_day = due.astimezone(CHINA).strftime('%Y-%m-%d')
            skipped = due_day < day
            unit = None if skipped else select_unit(db, p, selection, authority['source_target'], at=at)
            _ensure_occurrence(db, p, row, due_day, scheduled=due, unit=unit, skipped=skipped)
            row.next_due_at = due+timedelta(days=1); count += 1
    # Waiting Jobs are not repeatedly claimed while there is no input/budget.
    # Tick requeues only the original occurrence, never a clone or new nonce.
    waiting = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.kind == JOB_KIND,
        m.Job.data['subscription_id'].as_string() == row.id, m.Job.status.in_(WAITING))
        .order_by(m.Job.created_at,m.Job.id).limit(limit)))
    for job in waiting:
        _, origin, state = checked_occurrence(db, p, job)
        if origin['day'] < day:
            _state(db, p, job, {**state, 'status': 'skipped', 'reason': '原发生中国日期已结束，未创建空任务或追跑旧日'})
            job.status = 'cancelled'; continue
        unit = state['source_unit']
        if unit is None: unit = select_unit(db, p, selection, authority['source_target'], at=at)
        if unit is None: continue
        if state['status'] == 'waiting_limit' and _limits(db,p,row,at): continue
        _state(db, p, job, {**state, 'status': 'queued', 'reason': None,
            'source_unit': unit, 'source_label': unit['source_label']})
        job.status = 'queued'; job.available_at = at
    db.flush()
    return count
