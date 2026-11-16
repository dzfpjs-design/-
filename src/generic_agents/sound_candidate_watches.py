"""Confirmed, ACK-only environment-sound watches in the existing candidate queue.

This family only issues Inbox candidates. It never executes a model, creates a
Task, updates object facts, infers recording time, or controls a device.
"""
import copy
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, or_, text, String
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException
from pydantic import ValidationError
from . import models as m
from .contracts import digest
from .security import require
from .services import scoped, published, enqueue_event
from .relations import lock_current_actor
from .sound_models import EnvironmentSoundRun

KEY = 'sound_candidate_watch_v1'
SCHEMA = 'sound-candidate-watch-definition-v1'
COMMAND = 'sound.candidate_watch_command_v1'
ENQUEUED = 'sound.candidate_check_queued_v1'
CHECKED = 'sound.candidate_check_completed_v1'
ISSUED = 'sound.candidate_notification_issued_v1'
REQUEUED = 'sound.candidate_notification_requeued_v1'
JOB_SCHEMA = 'sound-candidate-check-v1'
EVENT_SCHEMA = 'sound-candidate-notification-v1'
MAX_RAW = 32768
LIMITS = {'max_rules': 100, 'max_existing': 100, 'max_windows': 124, 'min_score_default': '0.5'}
ERROR = '声音关注规则、原始成果或数据接收记录无法核对；原申请和原成果保留'
DECIMAL = re.compile(r'[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')


def need(value, message=ERROR, code=409):
    if not value: raise HTTPException(code, message)


def score(value):
    try:
        if type(value) is not str or len(value) > 80 or not DECIMAL.fullmatch(value): raise ValueError()
        result = Decimal(value)
        if not result.is_finite() or not Decimal(0) < result <= Decimal(1): raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise ValueError('最低原分数必须是大于0且不超过1的有限十进制文本') from None


def _no_nul(value):
    if isinstance(value, str): return "\x00" not in value
    if isinstance(value, list): return all(_no_nul(v) for v in value)
    if isinstance(value, dict): return all(_no_nul(k) and _no_nul(v) for k, v in value.items())
    return True


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result: raise ValueError('申请JSON字段重复')
        result[key] = value
    return result


def parse(raw, operation):
    from .sound_candidate_watch_api import Create, Status, Check
    try:
        need(type(raw) is bytes and len(raw) <= MAX_RAW, '申请正文过长', 413)
        original = raw.decode('utf-8')
        data = json.loads(original, object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限JSON')))
        need(_no_nul(data), '申请字段不能包含空字符', 422)
        body = {'create': Create, 'status': Status, 'check_existing': Check}[operation].model_validate(data).model_dump()
        return original, body, hashlib.sha256(raw).hexdigest(), digest(body)
    except (UnicodeError, ValueError, TypeError, ValidationError, KeyError):
        raise HTTPException(422, '请提交完整、无重复字段的声音关注申请') from None


def _audit(db, p, action, identity, data):
    row = m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=action, object_id=identity,
        data=copy.deepcopy(data)); db.add(row); db.flush(); return row


def _audits(db, p, action, identity):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity).limit(301)))


def _one(db, p, cls, identity):
    row = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
        .execution_options(populate_existing=True))
    need(row is not None)
    return row


def handles_watch(db, p, watch):
    return (KEY in watch.data or watch.data.get('definition', {}).get('schema') == SCHEMA
        or db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == COMMAND, m.Audit.object_id == watch.id).limit(1)) is not None
        or db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == ENQUEUED,
            m.Audit.data['watch_id'].as_string() == watch.id).limit(1)) is not None
        or db.scalar(select(m.Job.id).where(m.Job.workspace_id == p.workspace_id,
            m.Job.idempotency_key.like('sound-candidate-check:%'), m.Job.data['watch_id'].as_string() == watch.id).limit(1)) is not None)


def checked(db, p, watch):
    """Cold immutable creation/command ledger; never renew live authorization."""
    try:
        watch = _one(db, p, m.CandidateWatch, watch.id); state = watch.data[KEY]
        definition = state['definition']; commands = state['commands']
        need(watch.data.get('definition') is None and definition['schema'] == SCHEMA
            and definition['workspace_id'] == p.workspace_id and definition['instance_id'] == watch.instance_id
            and digest(definition) == state['definition_hash'] and type(commands) is dict and 1 <= len(commands) <= 300)
        proofs = _audits(db, p, COMMAND, watch.id)
        need(len(proofs) == len(commands) and {v.data.get('request_id') for v in proofs} == set(commands))
        created = []; transitions = []
        for proof in proofs:
            command = commands[proof.data['request_id']]
            original, body, raw_hash, body_hash = parse(command['original_raw'].encode(), command['operation'])
            receipt = command['receipt']; base = {k: v for k, v in receipt.items() if k != 'receipt_hash'}
            need(digest(command) == digest(proof.data) and proof.actor_id == command['actor_id']
                and command['schema'] == 'sound-candidate-watch-command-proof-v1'
                and command['workspace_id'] == p.workspace_id and command['instance_id'] == watch.instance_id
                and command['watch_id'] == watch.id and body['request_id'] == command['request_id']
                and command['raw_sha256'] == raw_hash and command['body_hash'] == body_hash
                and receipt['receipt_hash'] == digest(base) and receipt['actor_id'] == proof.actor_id
                and receipt['request_id'] == body['request_id'] and receipt['watch_id'] == watch.id
                and receipt['workspace_id'] == p.workspace_id and receipt['instance_id'] == watch.instance_id
                and receipt['definition_hash'] == state['definition_hash'] and receipt['operation'] == command['operation']
                and receipt['raw_sha256'] == raw_hash and receipt['body_hash'] == body_hash)
            if command['operation'] == 'create':
                need(digest(command['definition']) == digest(definition) and body['name'] == definition['name']
                    and body['class_index'] == definition['class_index'] and body['min_score_text'] == definition['min_score_text']
                    and body['min_windows'] == definition['min_windows'] and definition['actor_id'] == proof.actor_id
                    and receipt['committed_version'] == 1 and receipt['status_at_commit'] == 'enabled')
                for key in ('release_id', 'configuration_hash', 'business_id', 'entity_id', 'entity_definition_hash',
                            'capability_id', 'capability_hash', 'labels_sha256'):
                    need(body['target_pins'][key] == definition[key])
                created.append(command); transitions.append(receipt)
            else:
                need(command['definition'] is None)
                if command['operation'] == 'status':
                    need(receipt['committed_version'] == body['version'] + 1
                        and receipt['status_at_commit'] == body['status'])
                    transitions.append(receipt)
                else:
                    need(receipt['committed_version'] == body['version'])
        need(len(created) == 1 and created[0]['request_id'] == state['request_id'])
        transitions.sort(key=lambda value: value['committed_version'])
        need([v['committed_version'] for v in transitions] == list(range(1, len(transitions) + 1)))
        latest = transitions[-1]
        need(watch.version == latest['committed_version'] and watch.status == latest['status_at_commit']
            and state['effective_from'] == next(v['created_at'] for v in reversed(transitions) if v['status_at_commit'] == 'enabled'))
        return state
    except HTTPException as error:
        if error.status_code == 403: raise
        need(False)
    except (KeyError, TypeError, ValueError, AttributeError):
        need(False)


def _nonce(db, p, request_id):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int(digest({'sound_watch_nonce': p.workspace_id, 'request_id': request_id})[:15], 16)})


def _original(db, p, identity, request_id):
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND, m.Audit.data['request_id'].as_string() == request_id).limit(2)))
    rows = list(db.scalars(select(m.CandidateWatch).where(m.CandidateWatch.workspace_id == p.workspace_id,
        or_(m.CandidateWatch.data[KEY]['commands'].has_key(request_id),
            m.CandidateWatch.data[KEY]['request_id'].as_string() == request_id)).limit(2)))
    queue_trace = db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == ENQUEUED,
        m.Audit.data['command_request_id'].as_string() == request_id).limit(1))
    job_trace = db.scalar(select(m.Job.id).where(m.Job.workspace_id == p.workspace_id,
        m.Job.idempotency_key.like('sound-candidate-check:%'), m.Job.data['command_request_id'].as_string() == request_id).limit(1))
    if not proofs and not rows:
        need(queue_trace is None and job_trace is None)
        return None
    need(len(proofs) == len(rows) == 1 and proofs[0].object_id == rows[0].id)
    state = checked(db, p, rows[0]); command = state['commands'][request_id]
    if command['actor_id'] != p.user_id: raise HTTPException(404, '当前账号没有该原申请')
    if rows[0].instance_id != identity: raise HTTPException(404, '此实例没有该原申请')
    return rows[0], command


def _reply(db, p, watch, command):
    return {'schema': 'sound-candidate-watch-result-v1', 'receipt': copy.deepcopy(command['receipt']),
        'rule': view(db, p, watch)}


def _replay(db, p, identity, raw, operation, watch_id=None):
    original, body, raw_hash, body_hash = parse(raw, operation)
    old = _original(db, p, identity, body['request_id'])
    if old:
        watch, command = old
        need(command['operation'] == operation and command['raw_sha256'] == raw_hash
            and command['body_hash'] == body_hash and command['original_raw'] == original
            and (watch_id is None or watch.id == watch_id), '原号已保存不同申请，请恢复原文')
        return body, _reply(db, p, watch, command)
    return body, None


def _labels(trace):
    from .local_sound import RECIPE
    labels = RECIPE['labels']
    need(len(labels) == len(set(labels)) == 521
        and hashlib.sha256(('\n'.join(labels) + '\n').encode()).hexdigest() == trace['labels_sha256'])
    return labels


def _release(db, p, instance):
    from .instance_api import active
    instance, release = active(db, p, instance.id)
    need(release.instance_id == instance.id and digest(release.data['snapshot']) == release.configuration_hash)
    return release


def _cap(db, p, identity, lock=False):
    if lock:
        db.scalar(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
            m.Capability.id == identity).with_for_update(read=True).execution_options(populate_existing=True))
    cap = published(db, p, identity, 'specialist_model')
    from .workflow_sound import manifest
    from .local_sound import checked_trace
    trace = cap.data['execution']['model_trace']
    need(digest(cap.data) == digest(manifest(trace).model_dump(mode='json')))
    checked_trace(db, p, trace, live=False); _labels(trace)
    return cap, trace


def _target(db, p, instance, business_id, entity_id, cap_id, *, lock=False):
    from .entity_release_compat import require_entity
    from .native_sound_data_plan import verify
    release = _release(db, p, instance); snapshot = release.data['snapshot']
    need(business_id in instance.data.get('enabled_business_ids', []), '此业务尚未启用')
    business = next((b for b in snapshot['businesses'] if b['id'] == business_id), None)
    plan = verify(snapshot)
    need(business is not None and plan is not None and any(b['business_id'] == business_id
        and b['capability_id'] == cap_id for b in plan['bindings']), '此业务没有绑定所选环境声音能力')
    if lock:
        entity = db.scalar(select(m.Entity).where(m.Entity.workspace_id == p.workspace_id, m.Entity.id == entity_id)
            .with_for_update(read=True).execution_options(populate_existing=True))
        if entity is None: raise HTTPException(404, '记录不存在或不在当前工作区')
    else:
        entity = scoped(db, m.Entity, entity_id, p)
    need(entity.namespace == instance.namespace)
    proof = require_entity(db, p, instance, release, entity, lock_sources=lock)
    cap, trace = _cap(db, p, cap_id, lock=lock)
    need(any(b['business_id'] == business_id and b['capability_id'] == cap_id
        and b['capability_hash'] == cap.content_hash for b in plan['bindings']))
    return {'instance_version': instance.version, 'release_id': release.id,
        'configuration_hash': release.configuration_hash, 'business_id': business_id, 'entity_id': entity.id,
        'entity_version': entity.version, 'entity_definition_hash': digest(proof), 'capability_id': cap.id,
        'capability_version': cap.version, 'capability_hash': cap.content_hash, 'labels_sha256': trace['labels_sha256']}, trace


def options(db, p, identity):
    instance = scoped(db, m.DomainInstance, identity, p)
    result = {'schema': 'sound-candidate-watch-options-v1', 'workspace_id': p.workspace_id, 'instance_id': identity,
        'actor_id': p.user_id, 'instance_version': instance.version, 'release_id': None, 'configuration_hash': None,
        'businesses': [], 'entities': [], 'capabilities': [], 'can_create': False, 'reason': None,
        'limits': copy.deepcopy(LIMITS)}
    try:
        from .native_sound_data_plan import verify
        from .entity_release_compat import require_entity
        release = _release(db, p, instance); snapshot = release.data['snapshot']; plan = verify(snapshot)
        result.update(release_id=release.id, configuration_hash=release.configuration_hash)
        if plan is None: raise HTTPException(409, '当前业务版本没有环境声音步骤，请先正常登记并绑定能力')
        enabled = instance.data.get('enabled_business_ids', [])
        rows = [b for b in snapshot['businesses'] if b['id'] in enabled]
        ids = set()
        for business in rows:
            caps = sorted({b['capability_id'] for b in plan['bindings'] if b['business_id'] == business['id']})
            if caps:
                result['businesses'].append({'id': business['id'], 'name': business['name'], 'capability_ids': caps}); ids.update(caps)
        for cap_id in sorted(ids):
            cap, trace = _cap(db, p, cap_id)
            result['capabilities'].append({'id': cap.id, 'name': cap.data.get('description') or cap.name,
                'version': cap.version, 'content_hash': cap.content_hash, 'labels_sha256': trace['labels_sha256'],
                'model_trace': copy.deepcopy(trace), 'classes': [{'index': i, 'label': label} for i, label in enumerate(_labels(trace))]})
        entities = list(db.scalars(select(m.Entity).where(m.Entity.workspace_id == p.workspace_id,
            m.Entity.namespace == instance.namespace).order_by(m.Entity.id).limit(101)))
        need(len(entities) <= 100, '当前对象超过本入口100个限制，请缩小实例对象范围')
        for entity in entities:
            try: proof = require_entity(db, p, instance, release, entity, lock_sources=False)
            except HTTPException as error:
                if error.status_code == 403: raise
                continue
            kind = next((v for v in snapshot['object_types'] if v['key'] == entity.entity_type), {})
            label = next((str(entity.data[f['key']]) for f in kind.get('fields', []) if f.get('required')
                and f.get('kind') == '文本' and entity.data.get(f['key'])), '未命名业务对象')
            result['entities'].append({'id': entity.id, 'label': label, 'version': entity.version, 'definition_hash': digest(proof)})
        result['can_create'] = p.role in {'admin', 'operator'} and bool(result['businesses'] and result['entities'])
        if not result['can_create']: result['reason'] = '请由有权限的人员选择当前业务、对象和已登记的声音能力'
    except HTTPException as error:
        if error.status_code == 403: raise
        result['reason'] = str(error.detail)
    except (KeyError, ValueError, TypeError):
        result['reason'] = ERROR
    return result


def _actor(db, p, author_id=None):
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    if author_id and author_id != p.user_id:
        rows = db.execute(select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id)
            .where(m.User.id == author_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
                m.Membership.role.in_(['admin', 'operator'])).with_for_update(read=True, of=[m.User, m.Membership])).first()
        need(rows is not None, '规则确认人的当前账号或权限已变化')


def _live(db, p, watch, *, allow_paused=False):
    state = checked(db, p, watch); definition = state['definition']
    need(allow_paused or watch.status == 'enabled', '此关注规则已暂停')
    instance = scoped(db, m.DomainInstance, watch.instance_id, p)
    pins, trace = _target(db, p, instance, definition['business_id'], definition['entity_id'], definition['capability_id'], lock=True)
    for key in ('release_id', 'configuration_hash', 'business_id', 'entity_id', 'entity_definition_hash',
                'capability_id', 'capability_hash', 'labels_sha256'):
        need(pins[key] == definition[key], '业务、对象定义或模型版本已变化，请明确创建新规则')
    need(digest(trace) == digest(definition['model_trace']))
    return state, pins


def _access(db, p, watch, state):
    manager = p.role in {'admin', 'operator'} and (p.user_id == state['definition']['actor_id'] or p.role == 'admin')
    result = {'can_pause': manager and watch.status == 'enabled', 'can_enable': False,
        'can_check_existing': False, 'reason': None, 'current_target_pins': None}
    if not manager:
        result['reason'] = '仅规则确认人或管理员可更改或明确检查'; return result
    try:
        pins, trace = _target(db, p, scoped(db, m.DomainInstance, watch.instance_id, p),
            state['definition']['business_id'], state['definition']['entity_id'], state['definition']['capability_id'])
        comparable = {key: pins[key] for key in pins if key not in {'instance_version', 'entity_version', 'capability_version'}}
        need(all(value == state['definition'][key] for key, value in comparable.items())
            and digest(trace) == digest(state['definition']['model_trace']))
        result.update(can_enable=watch.status == 'paused', can_check_existing=watch.status == 'enabled', current_target_pins=pins)
    except HTTPException as error:
        if error.status_code == 403: raise
        result['reason'] = str(error.detail)
    except (KeyError, ValueError, TypeError): result['reason'] = ERROR
    return result


def view(db, p, watch):
    state = checked(db, p, watch)
    return {'schema': 'sound-candidate-watch-view-v1', 'id': watch.id, 'version': watch.version,
        'status': watch.status, 'workspace_id': watch.workspace_id, 'instance_id': watch.instance_id,
        'request_id': state['request_id'], 'definition': copy.deepcopy(state['definition']),
        'definition_hash': state['definition_hash'], 'effective_from': state['effective_from'],
        'created_at': watch.created_at.isoformat(), 'access': _access(db, p, watch, state)}


def watch_row(db, p, identity, watch_id, *, lock=False):
    scoped(db, m.DomainInstance, identity, p)
    watch = scoped(db, m.CandidateWatch, watch_id, p, lock)
    state = checked(db, p, watch)
    if watch.instance_id != identity: raise HTTPException(404, '此实例没有该声音关注规则')
    return watch


def list_rules(db, p, identity, *, limit=20, before_id=None):
    scoped(db, m.DomainInstance, identity, p)
    query = select(m.CandidateWatch).where(m.CandidateWatch.workspace_id == p.workspace_id,
        m.CandidateWatch.instance_id == identity, or_(m.CandidateWatch.data.has_key(KEY),
        m.CandidateWatch.id.in_(select(m.Audit.object_id).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == COMMAND))))
    if before_id: query = query.where(m.CandidateWatch.id < before_id)
    rows = list(db.scalars(query.order_by(m.CandidateWatch.id.desc()).limit(limit + 1)))
    return {'schema': 'sound-candidate-watch-list-v1', 'workspace_id': p.workspace_id, 'instance_id': identity,
        'records': [view(db, p, row) for row in rows[:limit]], 'has_more': len(rows) > limit,
        'next_before_id': rows[limit - 1].id if len(rows) > limit else None}


def _store_command(db, p, watch, operation, original, body, raw_hash, body_hash, **details):
    state = copy.deepcopy(watch.data[KEY]); now = m.now().isoformat()
    receipt = {'schema': 'sound-candidate-watch-receipt-v1', 'operation': operation,
        'workspace_id': p.workspace_id, 'instance_id': watch.instance_id, 'actor_id': p.user_id,
        'request_id': body['request_id'], 'watch_id': watch.id, 'committed_version': watch.version,
        'status_at_commit': watch.status, 'definition_hash': state['definition_hash'],
        'raw_sha256': raw_hash, 'body_hash': body_hash, 'job_ids': details.get('job_ids', []),
        'not_received_run_ids': details.get('not_received_run_ids', []), 'blocked_run_ids': details.get('blocked_run_ids', []),
        'has_more': details.get('has_more', False), 'next_before_id': details.get('next_before_id'), 'created_at': now}
    receipt['receipt_hash'] = digest(receipt)
    command = {'schema': 'sound-candidate-watch-command-proof-v1', 'operation': operation,
        'workspace_id': p.workspace_id, 'instance_id': watch.instance_id, 'actor_id': p.user_id,
        'watch_id': watch.id, 'request_id': body['request_id'], 'original_raw': original, 'raw_sha256': raw_hash,
        'body_hash': body_hash, 'definition': copy.deepcopy(state['definition']) if operation == 'create' else None,
        'receipt': receipt}
    need(body['request_id'] not in state['commands'] and len(state['commands']) < 300, '规则历史已达到300次限制，请保留原记录并另建规则')
    state['commands'][body['request_id']] = command
    if operation == 'create' or operation == 'status' and watch.status == 'enabled': state['effective_from'] = now
    watch.data = {**watch.data, KEY: state}; _audit(db, p, COMMAND, watch.id, command)
    return command


def create(db, p, identity, raw):
    body, reply = _replay(db, p, identity, raw, 'create')
    if reply: return reply
    _actor(db, p); instance = scoped(db, m.DomainInstance, identity, p, True); _nonce(db, p, body['request_id'])
    body, reply = _replay(db, p, identity, raw, 'create')
    if reply: return reply
    supplied = body['target_pins']; pins, trace = _target(db, p, instance, supplied['business_id'], supplied['entity_id'], supplied['capability_id'], lock=True)
    need(digest(pins) == digest(supplied), '业务、对象或模型目录已更新，请保留原申请并核对当前目录')
    count = list(db.scalars(select(m.CandidateWatch.id).where(m.CandidateWatch.workspace_id == p.workspace_id,
        m.CandidateWatch.instance_id == identity).limit(101)))
    need(len(count) < LIMITS['max_rules'], '本实例已达到100条关注规则限制')
    definition = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'instance_id': identity, 'actor_id': p.user_id,
        **{k: pins[k] for k in ('release_id', 'configuration_hash', 'business_id', 'entity_id', 'entity_definition_hash', 'capability_id', 'capability_hash', 'labels_sha256')},
        'model_trace': copy.deepcopy(trace), 'class_index': body['class_index'], 'class_label': _labels(trace)[body['class_index']],
        'min_score_text': body['min_score_text'], 'min_windows': body['min_windows'], 'name': body['name'],
        'candidate_only': True, 'not_authoritative': True, 'entity_state_updated': False, 'cloud_calls': 0, 'device_actions': 0}
    watch = m.CandidateWatch(workspace_id=p.workspace_id, instance_id=identity, status='enabled', version=1,
        data={KEY: {'definition': definition, 'definition_hash': digest(definition), 'request_id': body['request_id'],
                    'effective_from': None, 'commands': {}}})
    db.add(watch); db.flush()
    original, body, rh, bh = parse(raw, 'create'); command = _store_command(db, p, watch, 'create', original, body, rh, bh)
    return _reply(db, p, watch, command)


def _write_watch(db, p, identity, watch_id, body):
    prior = watch_row(db, p, identity, watch_id); state = checked(db, p, prior)
    _actor(db, p, None if body.get('status') == 'paused' else state['definition']['actor_id'])
    need(p.user_id == state['definition']['actor_id'] or p.role == 'admin', '仅规则确认人或管理员可操作', 403)
    scoped(db, m.DomainInstance, identity, p, True); _nonce(db, p, body['request_id'])
    return watch_row(db, p, identity, watch_id, lock=True)


def status_command(db, p, identity, watch_id, raw):
    body, reply = _replay(db, p, identity, raw, 'status', watch_id)
    if reply: return reply
    need((body['status'] == 'paused') == (body['target_pins'] is None), '暂停不需目录，启用须明确核对当前目录', 422)
    watch = _write_watch(db, p, identity, watch_id, body)
    body, reply = _replay(db, p, identity, raw, 'status', watch_id)
    if reply: return reply
    need(watch.version == body['version'], '规则已更新，请保留原申请并恢复当前状态')
    need(watch.status != body['status'], '规则已是所选状态，请核对原记录')
    if body['status'] == 'enabled':
        _, pins = _live(db, p, watch, allow_paused=True)
        need(digest(pins) == digest(body['target_pins']), '当前目录已变化，请明确核对后启用')
    watch.status = body['status']; watch.version += 1
    original, body, rh, bh = parse(raw, 'status'); command = _store_command(db, p, watch, 'status', original, body, rh, bh)
    return _reply(db, p, watch, command)


def _expected_target(value):
    from .sound_candidate_watch_api import TargetPins
    if value is None: return None
    try:
        decoded = json.loads(value, object_pairs_hook=_pairs)
        need(_no_nul(decoded), '原目录条件不能包含空字符', 422)
        return TargetPins.model_validate(decoded).model_dump()
    except (ValueError, ValidationError, TypeError): raise HTTPException(422, '原目录条件不完整') from None


def _superseded(db, p, instance, expected):
    if expected is None: return False
    if instance.version > expected['instance_version']: return True
    # A resource version is monotonic. Never infer a negative execution fact
    # from a temporary permission/model state, a missing row, or found=false.
    for cls, identity, version in ((m.Entity, expected['entity_id'], expected['entity_version']),
                                  (m.Capability, expected['capability_id'], expected['capability_version'])):
        row = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity))
        if row is not None and row.version > version: return True
    return False


def recovery(db, p, identity, request_id, raw_sha256, *, watch_id=None, operation='create', expected_version=None, expected_target=None):
    need(type(request_id) is str and 12 <= len(request_id) <= 100 and '\x00' not in request_id, '原号无效', 422)
    instance = scoped(db, m.DomainInstance, identity, p); expected = _expected_target(expected_target)
    if operation == 'create': need(expected is not None, '请保留原创建目录条件', 422)
    watch = watch_row(db, p, identity, watch_id) if watch_id else None
    ev = expected['instance_version'] if operation == 'create' else expected_version
    need(type(ev) is int and ev >= 1, '原版本条件不完整', 422)
    result = {'schema': 'sound-candidate-watch-recovery-v1', 'operation': operation, 'workspace_id': p.workspace_id,
        'instance_id': identity, 'actor_id': p.user_id, 'request_id': request_id, 'raw_sha256': raw_sha256,
        'found': False, 'receipt': None, 'rule': None, 'original_raw': None, 'body_hash': None,
        'expected_version': ev, 'current_version': watch.version if watch else instance.version,
        'expected_target': expected, 'current_target': None, 'superseded': False, 'reason': None}
    def found(original):
        saved, command = original
        need(command['operation'] == operation and command['raw_sha256'] == raw_sha256
            and (watch_id is None or saved.id == watch_id), '原申请账号、范围或正文摘要不一致')
        _, body, _, _ = parse(command['original_raw'].encode(), operation)
        need((body.get('version', ev) == ev) and digest(body.get('target_pins')) == digest(expected))
        return {**result, 'found': True, 'superseded': False, 'reason': None,
            'receipt': copy.deepcopy(command['receipt']), 'rule': view(db, p, saved),
            'original_raw': command['original_raw'], 'body_hash': command['body_hash']}
    old = _original(db, p, identity, request_id)
    if old: return found(old)
    result['superseded'] = bool(watch and watch.version > ev) or _superseded(db, p, instance, expected)
    if result['superseded']:
        # The writer holds Instance/Entity/Capability locks through commit.
        # A monotonic advance therefore excludes a still-committable old CAS;
        # re-read the nonce afterwards so an already committed original wins.
        committed = _original(db, p, identity, request_id)
        if committed: return found(committed)
    if expected:
        try:
            pins, _ = _target(db, p, instance, expected['business_id'], expected['entity_id'], expected['capability_id'])
            result['current_target'] = pins
        except HTTPException as error:
            if error.status_code == 403: raise
    result['reason'] = ('原固定版本已被后续版本取代；原申请保留，可明确重新拟稿' if result['superseded']
        else '尚未查到原申请，不能据此判断未执行；请保留原文后重新核对')
    return result



def _context(db, p, observation):
    from . import native_sound_provenance as origin
    from .native_sound_receipts import receipt_projection
    context = origin.checked_observation(db, p, observation)
    reception = receipt_projection(db, p, context['run'])
    need(reception['state'] == 'received' and reception['receipt'] is not None and reception['memory_id'],
        '设备原音成果尚未由数据管理接收完成，不能发送关注通知')
    machine = {'run_id': context['run'].id, 'result_hash': context['run'].data['result_hash'],
        'packet_hash': digest(context['packet']), 'receipt_hash': reception['receipt']['receipt_hash'],
        'memory_id': reception['memory_id'], 'labels_sha256': context['run'].data['installation']['labels_sha256']}
    return {**context, 'reception': reception, 'machine_hash': digest(machine)}


def source(db, p, observation, *, event_id=None, job_id=None):
    context = _context(db, p, observation); run = context['run']; packet = context['packet']
    return {'family': 'environment_sound', 'native_kind': 'EnvironmentSoundRun', 'attributes': {},
        'source_name': '环境声音模型候选', 'review': 'candidate_only', 'machine_hash': context['machine_hash'],
        'extracted': {'attributes': {}, 'anomalies': []}, 'time_anchor': None, 'time_protocol': 'recording_time_unknown',
        'context': context, 'original': {'schema': 'sound-candidate-source-reference-v1', 'family': 'environment_sound',
            'task_id': run.task_id, 'run_id': run.id, 'sound_input_id': run.sound_input_id,
            'result_hash': run.data['result_hash'], 'capability_id': run.capability_id,
            'capability_hash': run.data['capability_hash'], 'labels_sha256': packet.get('labels_sha256', run.data['installation']['labels_sha256']),
            'recording_time_verified': False, 'candidate_only': True, 'not_authoritative': True, 'physical_state_verified': False}}


def matched_windows(definition, result):
    """Compare the exact binary float32 value with the user's decimal text."""
    threshold = score(definition['min_score_text']); index = definition['class_index']
    need(type(index) is int and 0 <= index < 521 and type(definition['min_windows']) is int
        and 1 <= definition['min_windows'] <= 124)
    labels = result['model']['labels']; need(len(labels) == 521 and labels[index] == definition['class_label'])
    windows = result['classification']['windows']; need(1 <= len(windows) <= 124)
    selected = []
    for window in windows:
        values = window['scores']; need(len(values) == 521)
        value = values[index]
        need(type(value) in {float, int} and type(value) is not bool)
        exact = Decimal.from_float(value) if type(value) is float else Decimal(value)
        need(exact.is_finite() and 0 <= exact <= 1)
        if exact >= threshold:
            selected.append({**{key: window[key] for key in ('index', 'start_sample', 'end_sample',
                'padded_samples', 'start_seconds', 'end_seconds')}, 'score': value})
    return selected


def selection(definition, observation, report):
    context = report['context']; packet = context['packet']; run = context['run']
    need(observation.entity_id == definition['entity_id'] and packet['instance_id'] == definition['instance_id']
        and packet['release_id'] == definition['release_id'] and packet['business_id'] == definition['business_id']
        and run.capability_id == definition['capability_id'] and run.data['capability_hash'] == definition['capability_hash']
        and digest(run.data['installation']) == digest(definition['model_trace'])
        and run.data['result']['model']['labels_sha256'] == definition['labels_sha256'])
    windows = matched_windows(definition, run.data['result'])
    return {'matched': len(windows) >= definition['min_windows'], 'matched_window_count': len(windows),
        'class_index': definition['class_index'], 'class_label': definition['class_label'],
        'min_score_text': definition['min_score_text'], 'min_windows': definition['min_windows'],
        'run_id': run.id, 'result_hash': run.data['result_hash'], 'machine_hash': report['machine_hash'], 'event_id': None}


def _queue_anchor(job):
    return {'schema': 'sound-candidate-check-queued-proof-v1', 'job_id': job.id, 'job_key': job.idempotency_key, 'workspace_id': job.workspace_id,
        'job_schema': job.data['schema'],
        **{key: copy.deepcopy(job.data[key]) for key in ('actor_id', 'queued_by', 'watch_id', 'watch_version',
            'definition_hash', 'observation_id', 'run_id', 'result_hash', 'mode', 'command_request_id')}}


def _queued(db, p, job):
    proof = _audits(db, p, ENQUEUED, job.id)
    need(job.kind == 'check_candidate_watch' and job.data['schema'] == JOB_SCHEMA
        and len(proof) == 1 and proof[0].actor_id == job.data['queued_by']
        and digest(proof[0].data) == digest(_queue_anchor(job)))
    watch = _one(db, p, m.CandidateWatch, job.data['watch_id']); state = checked(db, p, watch)
    need(state['definition_hash'] == job.data['definition_hash'])
    if job.data['mode'] == 'manual':
        command = state['commands'][job.data['command_request_id']]
        need(command['operation'] == 'check_existing' and job.id in command['receipt']['job_ids']
            and command['actor_id'] == job.data['actor_id'] and command['actor_id'] == job.data['queued_by'])
    else:
        need(job.data['mode'] == 'automatic' and job.data['command_request_id'] is None
            and job.data['actor_id'] == state['definition']['actor_id'])
    return watch, state


def handles_job(db, p, job):
    return (job.data.get('schema') == JOB_SCHEMA or job.idempotency_key.startswith('sound-candidate-check:')
        or db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == ENQUEUED, m.Audit.object_id == job.id).limit(1)) is not None)


def _enqueue(db, p, watch, state, observation, *, manual=False, command_request_id=None):
    report = source(db, p, observation); selected = selection(state['definition'], observation, report)
    if not manual and datetime.fromisoformat(report['context']['packet']['registered_at']) < datetime.fromisoformat(state['effective_from']):
        return None
    actor = p.user_id if manual else state['definition']['actor_id']
    suffix = digest({'watch_id': watch.id, 'watch_version': watch.version,
        'run_id': selected['run_id'], 'result_hash': selected['result_hash'], 'manual_request_id': command_request_id})
    key = 'sound-candidate-check:' + suffix
    data = {'schema': JOB_SCHEMA, 'actor_id': actor, 'queued_by': p.user_id, 'watch_id': watch.id,
        'watch_version': watch.version, 'definition_hash': state['definition_hash'], 'observation_id': observation.id,
        'run_id': selected['run_id'], 'result_hash': selected['result_hash'],
        'mode': 'manual' if manual else 'automatic', 'command_request_id': command_request_id}
    inserted = db.scalar(insert(m.Job).values(workspace_id=p.workspace_id, kind='check_candidate_watch',
        idempotency_key=key, data=data).on_conflict_do_nothing(index_elements=['workspace_id', 'idempotency_key']).returning(m.Job.id))
    job = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.idempotency_key == key))
    need(job is not None)
    if inserted:
        _audit(db, p, ENQUEUED, job.id, _queue_anchor(job))
    else:
        # Manual command is not persisted until its complete bounded queue is
        # known, so validate its queue proof here without recursive command lookup.
        proof = _audits(db, p, ENQUEUED, job.id)
        need(len(proof) == 1 and digest(proof[0].data) == digest(_queue_anchor(job))
            and digest({k: job.data[k] for k in data}) == digest(data))
    return job


def enqueue_received(db, p, observation):
    """Called after the original data Event+Memory ACK, including candidate holds.

    This is producer-only queuing. It takes no Actor/Instance/Watch locks under
    the receiver's Event lock. The actual check reauthorizes all three later.
    """
    from .native_sound_receipts import handles
    if not handles(db, p, observation): return 0
    report = source(db, p, observation); packet = report['context']['packet']
    rows = list(db.scalars(select(m.CandidateWatch).where(m.CandidateWatch.workspace_id == p.workspace_id,
        m.CandidateWatch.instance_id == packet['instance_id'], m.CandidateWatch.status == 'enabled',
        m.CandidateWatch.data[KEY]['definition']['entity_id'].as_string() == observation.entity_id).limit(101)))
    need(len(rows) <= 100)
    count = 0
    for watch in rows:
        try:
            with db.begin_nested():
                state = checked(db, p, watch)
                if (state['definition']['business_id'] != packet['business_id']
                    or state['definition']['capability_id'] != packet['capability_id']): continue
                if _enqueue(db, p, watch, state, observation): count += 1
        except HTTPException as error:
            if error.status_code not in {404, 409}: raise
            _audit(db, p, 'sound.candidate_enqueue_blocked_v1', observation.id,
                {'watch_id': watch.id, 'reason': ERROR, 'candidate_only': True})
    return count


def check_existing(db, p, identity, watch_id, raw):
    body, reply = _replay(db, p, identity, raw, 'check_existing', watch_id)
    if reply: return reply
    watch = _write_watch(db, p, identity, watch_id, body)
    body, reply = _replay(db, p, identity, raw, 'check_existing', watch_id)
    if reply: return reply
    need(watch.version == body['version'], '规则已更新，请先恢复原申请并核对当前规则')
    state, pins = _live(db, p, watch)
    need(digest(pins) == digest(body['target_pins']), '当前目录已变化，请明确核对当前范围')
    value = state['definition']
    query = select(EnvironmentSoundRun).join(m.Task, m.Task.id == EnvironmentSoundRun.task_id).where(
        EnvironmentSoundRun.workspace_id == p.workspace_id, m.Task.workspace_id == p.workspace_id,
        EnvironmentSoundRun.data['instance_id'].as_string() == identity,
        EnvironmentSoundRun.data['release_id'].as_string() == value['release_id'],
        EnvironmentSoundRun.data['business_id'].as_string() == value['business_id'],
        EnvironmentSoundRun.capability_id == value['capability_id'], m.Task.data['entity_ids'].contains([value['entity_id']]))
    if body['before_run_id']: query = query.where(EnvironmentSoundRun.id < body['before_run_id'])
    runs = list(db.scalars(query.order_by(EnvironmentSoundRun.id.desc()).limit(101)))
    from . import native_sound_provenance as origin
    from .native_sound_receipts import receipt_projection
    from .workflow_sound import checked_saved_run
    jobs = []; pending = []; blocked = []
    for run in runs[:100]:
        try:
            with db.begin_nested():
                checked_saved_run(db, p, run.id)
                reception = receipt_projection(db, p, run)
                if reception['state'] == 'blocked_integrity': blocked.append(run.id); continue
                if reception['state'] != 'received': pending.append(run.id); continue
                observations = origin.observations(db, p, run.id); need(len(observations) == 1)
                job = _enqueue(db, p, watch, state, observations[0], manual=True, command_request_id=body['request_id'])
                need(job is not None); jobs.append(job.id)
        except HTTPException as error:
            if error.status_code not in {404, 409}: raise
            blocked.append(run.id)
    original, body, rh, bh = parse(raw, 'check_existing')
    command = _store_command(db, p, watch, 'check_existing', original, body, rh, bh, job_ids=jobs,
        not_received_run_ids=pending, blocked_run_ids=blocked, has_more=len(runs) > 100,
        next_before_id=runs[99].id if len(runs) > 100 else None)
    return _reply(db, p, watch, command)


def _event_key(watch, selected):
    return 'candidate-sound:' + digest({'watch_id': watch.id, 'definition_hash': watch.data[KEY]['definition_hash'],
        'run_id': selected['run_id'], 'result_hash': selected['result_hash']})


def _event_anchor(event):
    return {'schema': 'sound-candidate-notification-proof-v1', 'event_id': event.id,
        'workspace_id': event.workspace_id, 'event_key': event.idempotency_key,
        'payload_hash': digest({k: v for k, v in event.data.items() if k not in {'delivery_error', 'previous_delivery_error', 'derivation_receipt', 'sound_requeue_audits_v1'}})}


def process(db, p, job):
    from .workflow_abilities import WorkflowBlocked
    watch, state = _queued(db, p, job)
    _actor(db, p, state['definition']['actor_id'])
    scoped(db, m.DomainInstance, watch.instance_id, p, True)
    watch = _one_locked_watch(db, p, watch.id)
    try:
        state, _ = _live(db, p, watch)
        need(job.data['actor_id'] == p.user_id and watch.version == job.data['watch_version']
            and state['definition_hash'] == job.data['definition_hash'], '规则启停或当前权限已变化，原检查不自动重放')
        observation = _one(db, p, m.Observation, job.data['observation_id'])
        report = source(db, p, observation); selected = selection(state['definition'], observation, report)
        need(selected['run_id'] == job.data['run_id'] and selected['result_hash'] == job.data['result_hash'])
        if job.data['mode'] == 'automatic':
            need(datetime.fromisoformat(report['context']['packet']['registered_at']) >= datetime.fromisoformat(state['effective_from']))
    except HTTPException as error:
        if error.status_code == 403: raise
        raise WorkflowBlocked(str(error.detail)) from None
    proofs = _audits(db, p, CHECKED, job.id)
    if proofs:
        need(len(proofs) == 1 and proofs[0].actor_id == p.user_id
            and digest(proofs[0].data) == digest({'schema': 'sound-candidate-check-completed-proof-v1',
                'job_id': job.id, 'queue_hash': digest(_queue_anchor(job)), 'result': job.data.get('result')}))
        result = job.data['result']; comparison = {**result, 'event_id': None}
        need(digest(comparison) == digest(selected))
        if result['event_id']: event_report(db, p, _one(db, p, m.Event, result['event_id']), finishing_job=job)
        return copy.deepcopy(result)
    need(job.data.get('result') is None)
    if selected['matched']:
        key = _event_key(watch, selected)
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == p.workspace_id, m.Event.idempotency_key == key))
        if event:
            event = db.scalar(select(m.Event).where(m.Event.workspace_id == p.workspace_id, m.Event.id == event.id)
                .with_for_update().execution_options(populate_existing=True))
            _, prior = event_report(db, p, event)
            need(prior['machine_hash'] == selected['machine_hash'])
            if job.data['mode'] == 'manual' and event.status == 'blocked_delivery' and watch.version == event.data['watch_version']:
                previous = event.data.get('delivery_error') or ERROR
                proof = _audit(db, p, REQUEUED, event.id, {
                    'schema': 'sound-candidate-notification-requeued-proof-v1', 'event_id': event.id,
                    'watch_id': watch.id, 'watch_version': watch.version, 'definition_hash': state['definition_hash'],
                    'machine_hash': selected['machine_hash'], 'check_job_id': job.id,
                    'request_id': job.data['command_request_id'], 'previous_error': previous,
                    'previous_attempts': event.attempts, 'previous_version': event.version})
                data = copy.deepcopy(event.data); data['previous_delivery_error'] = previous; data.pop('delivery_error', None)
                data['sound_requeue_audits_v1'] = data.get('sound_requeue_audits_v1', []) + [{'id': proof.id, 'hash': digest(proof.data)}]
                event.data = data; event.status = 'pending_delivery'; event.version += 1
        else:
            payload = {'schema': EVENT_SCHEMA, 'observation_id': observation.id, 'entity_id': observation.entity_id,
                'source_id': observation.source_id, 'watch_id': watch.id, 'watch_version': watch.version,
                'definition_hash': state['definition_hash'], 'watch_definition': copy.deepcopy(state['definition']),
                'selection': copy.deepcopy(selected), 'machine_hash': report['machine_hash'], 'candidate_only': True,
                'evidence_ids': copy.deepcopy(observation.data['evidence_ids']), 'check_job_id': job.id,
                'notification_only': True, 'run_id': selected['run_id'], 'result_hash': selected['result_hash']}
            event = enqueue_event(db, p, 'perception.candidate', key, payload)
            _audit(db, p, ISSUED, event.id, _event_anchor(event))
        selected['event_id'] = event.id
    job.data = {**job.data, 'result': copy.deepcopy(selected)}
    _audit(db, p, CHECKED, job.id, {'schema': 'sound-candidate-check-completed-proof-v1',
        'job_id': job.id, 'queue_hash': digest(_queue_anchor(job)), 'result': selected})
    return selected


def _one_locked_watch(db, p, identity):
    row = db.scalar(select(m.CandidateWatch).where(m.CandidateWatch.workspace_id == p.workspace_id,
        m.CandidateWatch.id == identity).with_for_update().execution_options(populate_existing=True))
    need(row is not None); checked(db, p, row); return row


def handles_event(db, p, event):
    return (event.data.get('schema') == EVENT_SCHEMA or event.idempotency_key.startswith('candidate-sound:')
        or db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == ISSUED, m.Audit.object_id == event.id).limit(1)) is not None)


def _compact(event, report):
    selection = event.data['selection']; run = report['context']['run']; value = event.data['watch_definition']
    return {'schema': 'sound-candidate-signal-reference-v1', 'family': 'environment_sound',
        'task_id': run.task_id, 'run_id': run.id, 'sound_input_id': run.sound_input_id, 'result_hash': run.data['result_hash'],
        'capability_id': run.capability_id, 'capability_hash': run.data['capability_hash'],
        'labels_sha256': run.data['installation']['labels_sha256'], 'class_index': selection['class_index'],
        'class_label': selection['class_label'], 'rule_min_score_text': selection['min_score_text'],
        'rule_min_windows': selection['min_windows'], 'matched_window_count': selection['matched_window_count'],
        'notification_evidence_path': '/api/workspace/instances/' + value['instance_id'] + '/sound-candidate-notifications/' + event.id,
        'recording_time_verified': False, 'candidate_only': True, 'not_authoritative': True, 'physical_state_verified': False}


def event_report(db, p, event, *, finishing_job=None, _pending_checks=None):
    """Historical signal source. Pause/revoke never rewrites an issued Signal."""
    try:
        need(event.kind == 'perception.candidate' and event.data['schema'] == EVENT_SCHEMA
            and event.data['notification_only'] is True)
        watch = _one(db, p, m.CandidateWatch, event.data['watch_id']); state = checked(db, p, watch)
        need(state['definition_hash'] == event.data['definition_hash']
            and digest(state['definition']) == digest(event.data['watch_definition']))
        observation = _one(db, p, m.Observation, event.data['observation_id'])
        report = source(db, p, observation); selected = selection(state['definition'], observation, report)
        need(selected['matched'] is True and digest(selected) == digest(event.data['selection'])
            and event.data['machine_hash'] == report['machine_hash'] and event.data['entity_id'] == observation.entity_id
            and event.data['source_id'] == observation.source_id
            and digest(event.data['evidence_ids']) == digest(observation.data['evidence_ids'])
            and event.data['run_id'] == selected['run_id'] and event.data['result_hash'] == selected['result_hash']
            and event.idempotency_key == _event_key(watch, selected))
        proofs = _audits(db, p, ISSUED, event.id); job = _one(db, p, m.Job, event.data['check_job_id'])
        _queued(db, p, job)
        if _pending_checks is not None and job.status in {'queued', 'running'}:
            _pending_checks.append(job.id)
        else:
            need(job.status == 'succeeded' or finishing_job is not None and finishing_job.id == job.id and job.status == 'running')
        completed = _audits(db, p, CHECKED, job.id)
        expected = {**selected, 'event_id': event.id}
        need(len(proofs) == len(completed) == 1 and proofs[0].actor_id == job.data['actor_id']
            and completed[0].actor_id == job.data['actor_id'] and digest(proofs[0].data) == digest(_event_anchor(event))
            and digest(job.data['result']) == digest(expected)
            and digest(completed[0].data) == digest({'schema': 'sound-candidate-check-completed-proof-v1',
                'job_id': job.id, 'queue_hash': digest(_queue_anchor(job)), 'result': expected}))
        _verify_requeues(db, p, event, selected, finishing_job=finishing_job, pending_checks=_pending_checks)
        return observation, {**report, 'original': _compact(event, report), 'attributes': {},
            'extracted': {'attributes': {}, 'anomalies': [selected]}, 'basis': 'explicit_sound_class_candidate_rule'}
    except HTTPException as error:
        if error.status_code == 403: raise
        need(False)
    except (KeyError, TypeError, ValueError, AttributeError): need(False)


def delivery_state(db, p, event):
    """pending_check keeps the outbox pending; never fall back to anomaly Task."""
    need(handles_event(db, p, event))
    job = _one(db, p, m.Job, event.data['check_job_id']); _queued(db, p, job)
    watch = _one(db, p, m.CandidateWatch, event.data['watch_id']); state = checked(db, p, watch)
    _actor(db, p, state['definition']['actor_id'])
    scoped(db, m.DomainInstance, watch.instance_id, p, True)
    watch = _one_locked_watch(db, p, watch.id); _live(db, p, watch)
    need(watch.version == event.data['watch_version'], '规则启停已变化，旧通知不自动发送')
    # Only a fully anchored handler result may wait for its real Job terminal
    # commit. Current cancellation/revocation gates above are never hidden by it.
    pending = []
    event_report(db, p, event, _pending_checks=pending)
    return 'pending_check' if pending else 'ready'


def machine_basis(db, p, observation, *, event_id=None, job_id=None):
    report = source(db, p, observation, event_id=event_id, job_id=job_id)
    return {'machine_hash': report['machine_hash'], 'observed_at': None, 'time_anchor': None,
        'protocol': 'native_sound_registered_candidate_v1'}


def is_sound_signal(db, p, signal):
    basis = signal.data.get('basis')
    original = basis.get('original_model_result') if type(basis) is dict else None
    if type(original) is dict and original.get('family') == 'environment_sound': return True
    event = db.scalar(select(m.Event).where(m.Event.workspace_id == p.workspace_id, m.Event.id == signal.event_id))
    return event is not None and handles_event(db, p, event)


def require_notification_only(db, p, signal):
    from .audio_stream_delivery import is_signal as is_audio_signal
    if is_audio_signal(db,p,signal):
        raise HTTPException(409,'设备音轨候选只通知核查；请打开原音轨记录，不从此通知重复建立分析任务')
    if is_sound_signal(db, p, signal):
        raise HTTPException(409, '此声音关注只通知核查；请打开原任务播放原音，不从候选通知重复新建分析任务')


def checks(db, p, watch):
    checked(db, p, watch)
    rows = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        m.Job.kind == 'check_candidate_watch', m.Job.data['watch_id'].as_string() == watch.id,
        or_(m.Job.data['schema'].as_string() == JOB_SCHEMA,
            m.Job.idempotency_key.like('sound-candidate-check:%'))).order_by(m.Job.created_at.desc(), m.Job.id).limit(51)))
    values = []
    for job in rows[:50]:
        item = {'id': job.id, 'status': job.status, 'attempts': job.attempts, 'error': job.data.get('error'),
            'created_at': job.created_at.isoformat(), 'result': None, 'integrity_issue': None,
            'delivery_state': None, 'delivery_error': None}
        try:
            _queued(db, p, job)
            if job.data.get('result') is not None:
                proofs = _audits(db, p, CHECKED, job.id)
                observation = _one(db, p, m.Observation, job.data['observation_id'])
                report = source(db, p, observation); selected = selection(checked(db, p, watch)['definition'], observation, report)
                result = job.data['result']
                need(len(proofs) == 1 and proofs[0].actor_id == job.data['actor_id']
                    and digest(proofs[0].data) == digest({'schema': 'sound-candidate-check-completed-proof-v1',
                        'job_id': job.id, 'queue_hash': digest(_queue_anchor(job)), 'result': result})
                    and digest({**result, 'event_id': None}) == digest(selected))
                if result['event_id']:
                    event = _one(db, p, m.Event, result['event_id']); event_report(db, p, event, _pending_checks=[])
                    delivery = notification_delivery(db, p, event)
                    item.update(delivery_state=delivery['state'], delivery_error=delivery['error'])
                item['result'] = copy.deepcopy(result)
        except HTTPException as error:
            if error.status_code == 403: raise
            item['integrity_issue'] = ERROR
        except (KeyError, TypeError, ValueError): item['integrity_issue'] = ERROR
        values.append(item)
    return {'schema': 'sound-candidate-watch-checks-v1', 'watch_id': watch.id, 'records': values, 'has_more': len(rows) > 50}


def notification_evidence(db, p, identity, event_id):
    scoped(db, m.DomainInstance, identity, p)
    event = scoped(db, m.Event, event_id, p)
    observation, report = event_report(db, p, event); context = report['context']
    if context['packet']['instance_id'] != identity: raise HTTPException(404, '此实例没有该声音关注通知')
    from .sound_inputs import view as input_view
    from .workflow_sound import run_view
    definition = event.data['watch_definition']; run = context['run']
    return {'schema': 'sound-candidate-notification-evidence-v1', 'workspace_id': p.workspace_id,
        'instance_id': identity, 'event_id': event.id, 'watch_id': event.data['watch_id'],
        'definition_hash': event.data['definition_hash'], 'definition': copy.deepcopy(definition),
        'task_id': run.task_id, 'run_id': run.id, 'result_hash': run.data['result_hash'], 'run': run_view(run),
        'sound_input': input_view(context['input']), 'installation': copy.deepcopy(run.data['installation']),
        'reception': context['reception'], 'model_threshold': run.data['result']['classification']['threshold'],
        'matched_windows': matched_windows(definition, run.data['result']), 'content_path': '/api/sounds/runs/' + run.id + '/content',
        'candidate_only': True, 'not_authoritative': True, 'physical_state_verified': False,
        'recording_time_verified': False, 'window_semantics': 'overlapping_analysis_windows_not_duration_or_sources'}



def prepare_delivery(db, event_id, workspace_id):
    """Call before taking an Event lock, in a fresh per-event transaction.

    The ordinary receiver must exclude this family from its Event-first batch.
    Return None while the producer check is still running. A failed/currently
    revoked rule raises a bounded error; caller may mark only this Event blocked
    in a separate Event-only transaction, never fabricate a legacy Task.
    """
    from .worker import actor
    event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id, m.Event.id == event_id))
    need(event is not None)
    job = db.scalar(select(m.Job).where(m.Job.workspace_id == workspace_id, m.Job.id == event.data.get('check_job_id')))
    need(job is not None)
    p = actor(db, workspace_id, job.data['actor_id'])
    state = delivery_state(db, p, event)
    if state == 'pending_check': return None
    event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id, m.Event.id == event_id)
        .with_for_update(skip_locked=True).execution_options(populate_existing=True))
    if event is None: return None
    event_report(db, p, event)
    return p, event



def deliver_pending(workspace_id):
    """Thin scoped use of the existing Event/Signal receiver, no new consumer.

    Returns received/blocked/pending/skipped counts. Pending producer checks do
    not consume an attempt. Only received/blocked Events increment attempts.
    Caller must exclude EVENT_SCHEMA and candidate-sound: from Event-first
    delivery. Unknown/global workspace is never interpreted as all workspaces.
    """
    from uuid import UUID
    from sqlalchemy.orm import Session
    from .db import engine
    from .security import Principal
    from .perception_signals import receive
    try:
        need(isinstance(workspace_id, str) and str(UUID(workspace_id)) == workspace_id,
            '声音关注通知必须明确限定工作区', 422)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '声音关注通知必须明确限定工作区') from None
    result = {'received': 0, 'blocked': 0, 'pending': 0, 'skipped': 0}
    from sqlalchemy.orm import aliased
    requeue_job = aliased(m.Job)
    waiting_requeue = select(m.Audit.id).join(requeue_job,
        (requeue_job.workspace_id == m.Audit.workspace_id) & (requeue_job.id == m.Audit.data['check_job_id'].as_string())).where(
        m.Audit.workspace_id == workspace_id, m.Audit.action == REQUEUED, m.Audit.object_id == m.Event.id,
        requeue_job.status.in_(['queued', 'running'])).correlate(m.Event).exists()
    def member(identity):
        return select(m.User.id).join(m.Membership, m.Membership.user_id == m.User.id).where(
            m.User.id == identity, m.User.active.is_(True), m.Membership.workspace_id == workspace_id,
            m.Membership.role.in_(['admin', 'operator'])).correlate(m.Job, m.CandidateWatch).exists()
    current_cap = select(m.Capability.id).where(m.Capability.workspace_id == workspace_id,
        m.Capability.id == m.CandidateWatch.data[KEY]['definition']['capability_id'].as_string(),
        m.Capability.status == 'published',
        m.Capability.content_hash == m.CandidateWatch.data[KEY]['definition']['capability_hash'].as_string()).correlate(m.CandidateWatch).exists()
    coarse_live = (m.CandidateWatch.id.is_not(None) & m.DomainInstance.id.is_not(None)
        & (m.CandidateWatch.status == 'enabled')
        & (m.CandidateWatch.version.cast(String) == m.Event.data['watch_version'].as_string())
        & (m.DomainInstance.status == 'partially_active')
        & (m.DomainInstance.active_release_id == m.CandidateWatch.data[KEY]['definition']['release_id'].as_string())
        & m.DomainInstance.data['enabled_business_ids'].has_key(m.CandidateWatch.data[KEY]['definition']['business_id'].as_string())
        & member(m.Job.data['actor_id'].as_string())
        & member(m.CandidateWatch.data[KEY]['definition']['actor_id'].as_string()) & current_cap)
    # This is only a bounded discovery filter, never authority. Valid waiting
    # Jobs cannot occupy the head window; visibly paused/revoked records still
    # reach the full locked guard instead of being hidden as pending.
    with Session(engine()) as db:
        identities = list(db.scalars(select(m.Event.id).outerjoin(m.Job,
            (m.Job.workspace_id == m.Event.workspace_id) & (m.Job.id == m.Event.data['check_job_id'].as_string()))
            .outerjoin(m.CandidateWatch, (m.CandidateWatch.workspace_id == m.Event.workspace_id)
                & (m.CandidateWatch.id == m.Event.data['watch_id'].as_string()))
            .outerjoin(m.DomainInstance, (m.DomainInstance.workspace_id == m.Event.workspace_id)
                & (m.DomainInstance.id == m.CandidateWatch.instance_id)).where(
            m.Event.workspace_id == workspace_id,
            or_(~(m.Job.status.in_(['queued', 'running']) | waiting_requeue), ~coarse_live),
            m.Event.kind == 'perception.candidate', m.Event.status == 'pending_delivery',
            or_(m.Event.data['schema'].as_string() == EVENT_SCHEMA,
                m.Event.idempotency_key.like('candidate-sound:%'),
                m.Event.id.in_(select(m.Audit.object_id).where(m.Audit.workspace_id == workspace_id,
                    m.Audit.action == ISSUED)))).order_by(m.Event.created_at, m.Event.id).limit(100)))
    for identity in identities:
        attempt_version = None
        try:
            with Session(engine()) as db:
                initial = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id, m.Event.id == identity))
                if initial is not None: attempt_version = initial.version
                prepared = prepare_delivery(db, identity, workspace_id)
                if prepared is None:
                    result['pending'] += 1; continue
                p, event = prepared
                if event.status != 'pending_delivery':
                    result['skipped'] += 1; continue
                need(receive(db, event) is True)
                signal = db.scalar(select(m.PerceptionSignal).where(m.PerceptionSignal.workspace_id == workspace_id,
                    m.PerceptionSignal.event_id == event.id))
                need(signal is not None and signal.status == 'unassigned' and signal.data.get('basis') is not None
                    and signal.data['basis'].get('original_model_result', {}).get('schema') == 'sound-candidate-signal-reference-v1'
                    and signal.data.get('task_ids') == [])
                need(not db.scalar(select(m.Task.id).where(m.Task.workspace_id == workspace_id,
                    m.Task.data['signal_id'].as_string() == signal.id).limit(1)))
                event.status = 'received'; event.received_at = m.now(); event.attempts += 1; event.version += 1
                _audit(db, p, 'sound.candidate_notification_received_v1', event.id,
                    {'signal_id': signal.id, 'watch_id': event.data['watch_id'], 'watch_version': event.data['watch_version'],
                     'definition_hash': event.data['definition_hash'], 'machine_hash': event.data['machine_hash'],
                     'check_job_id': event.data['check_job_id'], 'candidate_only': True, 'task_created': False})
                db.commit(); result['received'] += 1
        except HTTPException as error:
            if error.status_code not in {403, 404, 409, 422}: raise
            with Session(engine()) as db:
                event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id,
                    m.Event.id == identity).with_for_update().execution_options(populate_existing=True))
                if event is not None and event.status == 'pending_delivery' and event.version == attempt_version:
                    event.status = 'blocked_delivery'; event.attempts += 1; event.version += 1
                    event.data = {**event.data, 'delivery_error': '声音关注规则或原始数据与接收记录暂不能核对，未发送业务通知；请保留原成果并检查权限与规则'}
                    db.commit(); result['blocked'] += 1
        except (KeyError, ValueError, TypeError, AttributeError, OSError):
            with Session(engine()) as db:
                event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id,
                    m.Event.id == identity).with_for_update().execution_options(populate_existing=True))
                if event is not None and event.status == 'pending_delivery' and event.version == attempt_version:
                    event.status = 'blocked_delivery'; event.attempts += 1; event.version += 1
                    event.data = {**event.data, 'delivery_error': ERROR}
                    db.commit(); result['blocked'] += 1
    return result



def notification_delivery(db, p, event):
    """Delivery is independent from a successful score-filter Job."""
    signals = list(db.scalars(select(m.PerceptionSignal).where(m.PerceptionSignal.workspace_id == p.workspace_id,
        m.PerceptionSignal.event_id == event.id).limit(2)))
    proofs = _audits(db, p, 'sound.candidate_notification_received_v1', event.id)
    if event.status == 'received':
        need(event.received_at is not None and event.attempts >= 1 and len(signals) == len(proofs) == 1)
        from .perception_signals import checked as checked_signal
        checked_signal(db, p, signals[0], lock_sources=False)
        expected = {'signal_id': signals[0].id, 'watch_id': event.data['watch_id'], 'watch_version': event.data['watch_version'],
            'definition_hash': event.data['definition_hash'], 'machine_hash': event.data['machine_hash'],
            'check_job_id': event.data['check_job_id'], 'candidate_only': True, 'task_created': False}
        job = _one(db, p, m.Job, event.data['check_job_id'])
        need(proofs[0].actor_id == job.data['actor_id'] and digest(proofs[0].data) == digest(expected))
        return {'state': 'received', 'error': None}
    need(event.status in {'pending_delivery', 'blocked_delivery'} and event.received_at is None and not signals and not proofs)
    return {'state': event.status, 'error': event.data.get('delivery_error') if event.status == 'blocked_delivery' else None}



def _verify_requeues(db, p, event, selected, *, finishing_job=None, pending_checks=None):
    proofs = _audits(db, p, REQUEUED, event.id)
    markers = event.data.get('sound_requeue_audits_v1', [])
    need(type(markers) is list and len(markers) == len(proofs)
        and {v['id'] for v in markers} == {v.id for v in proofs})
    by_id = {proof.id: proof for proof in proofs}; previous = 0; last = None
    for marker in markers:
        proof = by_id[marker['id']]; data = proof.data
        need(marker['hash'] == digest(data) and data['schema'] == 'sound-candidate-notification-requeued-proof-v1'
            and data['event_id'] == event.id and data['watch_id'] == event.data['watch_id']
            and data['watch_version'] == event.data['watch_version']
            and data['definition_hash'] == event.data['definition_hash'] and data['machine_hash'] == event.data['machine_hash']
            and type(data['previous_attempts']) is int and data['previous_attempts'] > previous
            and type(data['previous_version']) is int and data['previous_version'] >= 1)
        job = _one(db, p, m.Job, data['check_job_id']); _queued(db, p, job)
        need(job.data['mode'] == 'manual' and job.data['command_request_id'] == data['request_id']
            and job.data['watch_id'] == data['watch_id'] and job.data['watch_version'] == data['watch_version']
            and job.data['actor_id'] == proof.actor_id)
        if pending_checks is not None and job.status in {'queued', 'running'}:
            pending_checks.append(job.id)
        else:
            need(job.status == 'succeeded' or finishing_job is not None and finishing_job.id == job.id and job.status == 'running')
        completion = _audits(db, p, CHECKED, job.id); expected = {**selected, 'event_id': event.id}
        need(len(completion) == 1 and completion[0].actor_id == proof.actor_id
            and digest(job.data.get('result')) == digest(expected)
            and digest(completion[0].data) == digest({'schema': 'sound-candidate-check-completed-proof-v1',
                'job_id': job.id, 'queue_hash': digest(_queue_anchor(job)), 'result': expected}))
        previous = data['previous_attempts']; last = data
    if last is not None:
        need(event.data.get('previous_delivery_error') == last['previous_error']
            and event.attempts == last['previous_attempts'] + (0 if event.status == 'pending_delivery' else 1)
            and event.version == last['previous_version'] + (1 if event.status == 'pending_delivery' else 2))
    else:
        need('previous_delivery_error' not in event.data and event.attempts == (0 if event.status == 'pending_delivery' else 1))
