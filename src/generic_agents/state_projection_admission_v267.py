"""Read saved state/background records without computing or dispatching state.

The caller owns a real clean RR READ ONLY transaction. Current admission is a
display hint, not a lease, heartbeat or write authorization. Historical results
retain their recorded status when their issuer later loses execution authority.
"""
import copy
import json
import math
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, text

from . import models as m
from .contracts import digest
from .media_daily_task_adapter import current_actor
from . import state_projection_consumer_v266 as consumer

SCHEMA = 'state-projection-background-v267'
LOOKUP = 'state-projection-request-v267'
ERROR = '原对象后台记录或独立凭据无法核对；原记录保留，此读口未重算或补写'
CACHE_ERROR = '已存状态缓存的摘要或结构受阻；原缓存保留，此读口未即时重算或修复'
LIMIT = 30
MAX_CACHE_BYTES = 16 * 1024 * 1024
CACHE_KEYS = {'corrected_inputs', 'input_issues', 'inputs_truncated', 'entity_id', 'fields',
    'gaps', 'conflicts', 'status', 'physical_state', 'current_state_verified', 'requirements_basis',
    'required_fields', 'requirements_defined', 'field_freshness_seconds', 'excluded_records',
    'unclocked_candidates', 'candidates_truncated', 'truncated', 'evidence_ids', 'freshness_seconds',
    'authoritative_resource_version', 'rule_version', 'superseded_records'}
RESULT_KEYS = {'schema', 'entity_id', 'instance_id', 'state_hash', 'state_version', 'status',
    'checked_at', 'current_observation_version', 'trigger_reason', 'cloud_calls', 'device_actions',
    'entity_updated', 'physical_state_verified'}


def need(value, message=ERROR):
    if not value: raise HTTPException(409, message)


def _stamp(value):
    if type(value) is str: value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat()


def _finite(value, depth=0):
    need(depth <= 64)
    if type(value) is float: need(math.isfinite(value))
    elif type(value) is dict:
        need(all(type(key) is str for key in value))
        for child in value.values(): _finite(child, depth + 1)
    elif type(value) is list:
        for child in value: _finite(child, depth + 1)
    else: need(value is None or type(value) in {str, bool, int})


def _context(db, p, instance_id, entity_id):
    need(consumer._uuid(instance_id) and consumer._uuid(entity_id))
    need(not db.new and not db.dirty and not db.deleted)
    # dual_read_session already owns the Connection transaction before this
    # freshly constructed Session has issued its first statement.
    bind = db.get_bind()
    need(db.in_transaction() or hasattr(bind, 'in_transaction') and bind.in_transaction())
    need(db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
        and db.scalar(text('SHOW transaction_read_only')) == 'on')
    current_actor(db, p)
    instance = db.scalar(select(m.DomainInstance).where(m.DomainInstance.workspace_id == p.workspace_id,
        m.DomainInstance.id == instance_id))
    entity = db.scalar(select(m.Entity).where(m.Entity.workspace_id == p.workspace_id, m.Entity.id == entity_id))
    if instance is None or entity is None or entity.namespace != instance.namespace:
        raise HTTPException(404, '当前工作区没有此实例对象')
    # Namespace names can be shared by separately generated instances. Match
    # the entity's unique historical source release, without requiring that
    # release/package to be current, active or freshly publishable.
    sources = list(db.scalars(select(m.InstanceRelease).where(
        m.InstanceRelease.workspace_id == p.workspace_id,
        m.InstanceRelease.package_id == entity.schema_capability_id).limit(2)))
    if len(sources) != 1 or sources[0].instance_id != instance.id:
        raise HTTPException(404, '当前工作区没有此实例对象')
    checked_at = _stamp(db.scalar(text('SELECT transaction_timestamp()')))
    return instance, entity, checked_at


def _permissions(db, p, instance, entity):
    if p.role not in {'admin', 'operator'}:
        return {'can_request_new': False, 'reason': '当前账号只读；可查看已保存记录，不能新建后台核对'}
    try:
        from .instance_api import active
        from .entity_release_compat import require_entity
        actual, release = active(db, p, instance.id)
        require_entity(db, p, actual, release, entity, lock_sources=False)
        return {'can_request_new': True, 'reason': None}
    except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError):
        return {'can_request_new': False, 'reason': '当前实例、对象版本或执行范围尚未恢复；原后台记录保留'}


def _saved_origin(db, job, request, seen=None):
    """Saved linkage only; never require an old issuer to remain executable."""
    seen = set() if seen is None else seen
    need(job.id not in seen and len(seen) < consumer.MAX_CHAIN)
    seen.add(job.id)
    if request['trigger_observation_id'] is not None:
        need(consumer._uuid(request['trigger_observation_id'])
            and type(request['trigger_observation_version']) is int and request['trigger_observation_version'] >= 1)
        observation = db.scalar(select(m.Observation).where(m.Observation.workspace_id == job.workspace_id,
            m.Observation.id == request['trigger_observation_id']))
        need(observation is not None and observation.entity_id == request['entity_id']
            and observation.version >= request['trigger_observation_version'])
        need(job.idempotency_key == 'state-refresh:' + observation.id + ':'
            + str(request['trigger_observation_version']) + ':' + digest(request['reason'])[:12])
        return
    if job.idempotency_key.startswith('state-refresh:manual:'):
        consumer._manual_proof(db, job, request); return
    need(request['reason'] == '登记依据时效到期' and consumer._sha(request['due_state_hash']))
    prefix = 'state-refresh:expiry:' + request['entity_id'] + ':' + request['due_state_hash'] + ':'
    need(job.idempotency_key.startswith(prefix))
    parent_id = job.idempotency_key[len(prefix):]
    need(consumer._uuid(parent_id))
    parent = db.scalar(select(m.Job).where(m.Job.workspace_id == job.workspace_id, m.Job.id == parent_id))
    parent_request = consumer._request(parent)
    result = consumer._completed_proof(db, parent)
    need(parent_request['instance_id'] == request['instance_id'] and parent_request['entity_id'] == request['entity_id']
        and parent_request['authorized_by'] == request['authorized_by']
        and parent.data.get('next_expiry_job_id') == job.id and result['state_hash'] == request['due_state_hash'])
    _saved_origin(db, parent, parent_request, seen)


def _result(db, job):
    value = consumer._completed_proof(db, job)
    need(set(value) == RESULT_KEYS and value['status'] in {'observed', 'conflict', 'insufficient'}
        and type(value['cloud_calls']) is int and value['cloud_calls'] == 0
        and type(value['device_actions']) is int and value['device_actions'] == 0
        and (value['current_observation_version'] is None or type(value['current_observation_version']) is int
            and value['current_observation_version'] >= 1))
    _stamp(value['checked_at']); _finite(value)
    return copy.deepcopy(value)


def _job(db, p, instance, entity, row, permissions, clock):
    # Preserve the real persisted status even if a separate display proof is
    # blocked. Never rewrite a succeeded Job into failure because of live IAM.
    saved = row.data if type(row.data) is dict else {}
    value = {'id': row.id, 'status': row.status, 'available_at': _stamp(row.available_at),
        'created_at': _stamp(row.created_at), 'reason': None, 'result': None,
        'error': saved.get('error') if type(saved.get('error')) is str else None,
        'cancel_reason': saved.get('cancel_reason') if type(saved.get('cancel_reason')) is str else None,
        'admission': {'state': 'blocked_integrity', 'reason': ERROR, 'can_request_new': False}}
    try:
        request = consumer._request(row)
        need(row.workspace_id == p.workspace_id and request['instance_id'] == instance.id
            and request['entity_id'] == entity.id)
        _saved_origin(db, row, request)
        value['reason'] = request['reason']
        if row.data.get('state_result') is not None: value['result'] = _result(db, row)
        if row.status in {'succeeded', 'failed', 'cancelled'}:
            need(row.status != 'succeeded' or value['result'] is not None)
            value['admission'] = {'state': 'historical', 'reason': '这是已保存的历史后台记录；不代表当前执行授权或处理器在线',
                'can_request_new': permissions['can_request_new']}
        elif row.status == 'running':
            value['admission'] = {'state': 'not_checked', 'reason': '原任务记录为运行中；本次读取不续租、不证明处理器在线',
                'can_request_new': False}
        elif row.status == 'queued' and row.available_at > clock:
            value['admission'] = {'state': 'not_checked', 'reason': '尚未到原定核对时间；未提前执行',
                'can_request_new': permissions['can_request_new']}
        elif row.status == 'queued':
            try:
                consumer._prove(db, p.workspace_id, instance.id, row.id, entity_id=entity.id)
                value['admission'] = {'state': 'ready', 'reason': '本次读取中的执行条件已核对；尚不代表已领取或处理器在线',
                    'can_request_new': permissions['can_request_new']}
            except HTTPException as error:
                state = ('waiting_data' if error.detail == consumer.WAIT_ACK else
                    'waiting_parent' if error.detail == consumer.WAIT_PARENT else
                    'blocked_authorization' if error.detail == consumer.AUTH_ERROR else
                    'stale' if error.detail == consumer.STALE else 'blocked_integrity')
                reason = error.detail if error.detail in {consumer.WAIT_ACK, consumer.WAIT_PARENT,
                    consumer.AUTH_ERROR, consumer.STALE, consumer.ERROR, consumer.SOURCE_ERROR} else ERROR
                value['admission'] = {'state': state, 'reason': reason, 'can_request_new': permissions['can_request_new']}
        else: need(False)
    except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError, RecursionError):
        value.update(result=None, error=ERROR)
        value['admission'] = {'state': 'blocked_integrity', 'reason': ERROR, 'can_request_new': False}
    return value


def _cache(db, p, entity, jobs):
    row = db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id == p.workspace_id,
        m.ObjectState.entity_id == entity.id))
    if row is None:
        return None, {'status': 'missing', 'reason': '尚无已保存状态缓存；此读口未发起重算', 'background_result_matched': False}
    try:
        need(type(row.data) is dict and type(row.version) is int and row.version >= 1)
        payload = {key: copy.deepcopy(value) for key, value in row.data.items()
            if key not in {'state_hash', 'evaluated_at', 'history', 'history_truncated'}}
        need(set(payload) <= CACHE_KEYS and payload['entity_id'] == entity.id and type(payload['fields']) is dict
            and type(payload['gaps']) is list and type(payload['conflicts']) is list
            and type(payload['unclocked_candidates']) is list
            and payload['status'] in {'observed', 'conflict', 'insufficient'}
            and payload['physical_state'] == 'unknown' and payload['current_state_verified'] is False
            and consumer._sha(row.data.get('state_hash')) and digest(payload) == row.data['state_hash'])
        _finite(payload)
        need(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')) <= MAX_CACHE_BYTES)
        stamp = _stamp(row.data['evaluated_at'])
        matched = any(item['status'] == 'succeeded' and item['result'] is not None and item['admission']['state'] != 'blocked_integrity'
            and item['result']['state_hash'] == row.data['state_hash'] and item['result']['state_version'] == row.version for item in jobs)
        return {'version': row.version, 'state_hash': row.data['state_hash'], 'evaluated_at': stamp, 'value': payload}, {
            'status': 'saved', 'reason': '仅显示保存缓存；本次未重算状态或重新验证全部来源', 'background_result_matched': matched}
    except (HTTPException, KeyError, ValueError, TypeError, AttributeError, RecursionError):
        return None, {'status': 'blocked_integrity', 'reason': CACHE_ERROR, 'background_result_matched': False}


def background(db, p, instance_id, entity_id):
    instance, entity, checked_at = _context(db, p, instance_id, entity_id)
    permissions = _permissions(db, p, instance, entity)
    clock = datetime.fromisoformat(checked_at)
    # Include independent manual proofs as reverse hints, even if mutable Job
    # request markers were removed. No hint grants execution or repairs data.
    requested = select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == consumer.REQUESTED, m.Audit.object_id == m.Job.id,
        m.Audit.data['instance_id'].as_string() == instance_id,
        m.Audit.data['entity_id'].as_string() == entity_id).exists()
    rows = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        (m.Job.kind == consumer.KIND) | requested,
        (m.Job.data['state_request']['entity_id'].as_string() == entity_id) | requested)
        .order_by(m.Job.created_at.desc(), m.Job.id.desc()).limit(LIMIT)))
    history = [_job(db, p, instance, entity, row, permissions, clock) for row in rows]
    latest = next((item for item in history if item['status'] != 'cancelled'
        and not (item['status'] == 'queued' and item['reason'] == '登记依据时效到期')), None)
    future = next((item for item in history if item['status'] in {'queued', 'running'}
        and item['reason'] == '登记依据时效到期'), None)
    stored, cache = _cache(db, p, entity, history)
    need(not db.new and not db.dirty and not db.deleted)
    return {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'instance_id': instance_id, 'entity_id': entity_id, 'stored_state': stored, 'cache': cache,
        'background': {'latest': latest, 'history': history, 'next_expiry': future, 'history_limit': LIMIT},
        'permissions': permissions, 'checked_at': checked_at, 'recomputed': False, 'cloud_calls': 0}


def lookup(db, p, instance_id, entity_id, request_id):
    instance, entity, checked_at = _context(db, p, instance_id, entity_id)
    key = consumer._manual_key(p.user_id, request_id)
    row = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.idempotency_key == key))
    proofs = consumer._manual_rows(db, p.workspace_id, key, row.id if row is not None else None)
    receipt = request = None
    if row is None: need(not proofs)
    else:
        original = consumer._request(row)
        need(original['instance_id'] == instance_id and original['entity_id'] == entity_id
            and row.data['actor_id'] == p.user_id)
        consumer._manual_proof(db, row, original)
        # Receipt integrity is independent of current admission. A revoked
        # actor/stale target may still recover the saved request; broken saved
        # result/Audit links cannot masquerade as a trustworthy receipt.
        _saved_origin(db, row, original)
        if row.data.get('state_result') is not None: _result(db, row)
        else: need(row.status != 'succeeded')
        proof = proofs[0]
        need(len(proofs) == 1 and proof.actor_id == p.user_id and proof.object_id == row.id)
        permissions = _permissions(db, p, instance, entity)
        receipt = _job(db, p, instance, entity, row, permissions, datetime.fromisoformat(checked_at))
        request = {'request_id': request_id, 'actor_id': p.user_id, 'instance_id': instance_id,
            'entity_id': entity_id, 'job_id': row.id, 'idempotency_key': key,
            'state_request_hash': row.data['state_request_hash'], 'canonical_body_hash': digest({'request_id': request_id}),
            'audit': {'id': proof.id, 'action': proof.action, 'object_id': proof.object_id,
                'actor_id': proof.actor_id, 'data_hash': digest(proof.data)}}
    need(not db.new and not db.dirty and not db.deleted)
    return {'schema': LOOKUP, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'instance_id': instance_id, 'entity_id': entity_id, 'request_id': request_id,
        'found': row is not None, 'job': receipt, 'request': request, 'checked_at': checked_at,
        'recomputed': False, 'cloud_calls': 0, 'not_found_proves_unexecuted': False}
