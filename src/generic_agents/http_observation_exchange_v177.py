"""Audited HTTP exchange storage; transport and live parent guards stay external.

There is no HTTP client, dispatch, Observation creation, Acquisition settlement
or data-gap completion here. A valid receipt projection is not business success.
"""
import copy
from datetime import datetime, timezone
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import func, select, text

from . import http_observation_mapping_v177 as protocol
from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from .models import (Acquisition, Audit, Capability, DataGap, DataRun, DomainInstance,
    Entity, InstanceRelease, Job, MonitorPolicy, Observation, ObservationPlan, SensorAsset, Task, uid)
from .simulation_models_v176 import SimulationEnvironmentV176 as Environment
from .relations import lock_current_actor
from .services import audit, scoped

SCHEMA = 'http-observation-exchange-v177'
ACTION = 'http.observation.exchange.changed_v177'
READ_KIND = 'read_http_observation_receipt_v177'
READ_AUDIT = 'http.observation.receipt_read.enqueued_v177'
RESUME = 'saved_receipt_resume_v177'
RESUME_SCHEMA = 'http-observation-saved-receipt-resume-v177'
MARK = {**protocol.MARK, 'test_only': True, 'production_activation': False}
STATUSES = {'prepared', 'awaiting_receipt', 'received_no_data', 'received'}
_IDENTITIES = ('id', 'workspace_id', 'acquisition_id', 'source_id', 'environment_id',
    'instance_id', 'release_id', 'dispatch_job_id', 'actor_id')


def need(value, message='原HTTP补采交换、独立回执或版本无法核对；保留原号，不重新派发', status=409):
    if not value: raise HTTPException(status, message)


def _safe(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try: return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            raise HTTPException(409, '原HTTP交换字段、来源或完整性无法核对；未重建或派发') from None
    return checked


def digest(value): return protocol.digest(value)
def same(left, right): return protocol.canonical(left) == protocol.canonical(right)


def snapshot(row):
    return {**{key: copy.deepcopy(getattr(row, key)) for key in _IDENTITIES},
        'observation_id': row.observation_id, 'status': row.status, 'version': row.version,
        'data': copy.deepcopy(row.data)}


def verify_authority(state, anchors):
    """Pure strict state chain; no mutable row self-hash can replace an anchor."""
    need(type(state) is dict and type(state.get('version')) is int and state['version'] >= 1
        and len(anchors) == state['version'])
    need(all(type(item) is dict and type(item.get('version')) is int for item in anchors))
    anchors = sorted(anchors, key=lambda item: item['version'])
    previous = None
    for version, item in enumerate(anchors, 1):
        need(set(item) == {'schema', 'exchange_id', 'acquisition_id', 'original_request_key',
            'version', 'operation', 'actor_id', 'previous_state_hash', 'state_hash', 'state'})
        need(item['schema'] == 'http-observation-exchange-audit-v177'
            and item['exchange_id'] == state['id'] and item['acquisition_id'] == state['acquisition_id']
            and item['version'] == version and item['state']['version'] == version
            and item['actor_id'] == state['actor_id']
            and item['previous_state_hash'] == previous
            and item['state_hash'] == digest(item['state'])
            and item['original_request_key'] == item['state']['data']['freeze']['original_request_key'])
        need(all(same(item['state'][key], state[key]) for key in _IDENTITIES))
        if version > 1:
            need(same(item['state']['data']['freeze'], anchors[0]['state']['data']['freeze']))
        previous = item['state_hash']
    need(same(anchors[-1]['state'], state), '当前交换与独立保存状态不同，不能以重封自身摘要恢复权威')
    return previous


def _authority_query(p, identity):
    return select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, Audit.object_id == identity)


def _record(db, p, row, operation, previous):
    state = snapshot(row)
    value = {'schema': 'http-observation-exchange-audit-v177', 'exchange_id': row.id,
        'acquisition_id': row.acquisition_id, 'original_request_key': row.data['freeze']['original_request_key'],
        'version': row.version, 'operation': operation, 'actor_id': p.user_id,
        'previous_state_hash': previous, 'state_hash': digest(state), 'state': state}
    audit(db, p, ACTION, row.id, value)


def _validate_saved(row):
    data = row.data
    need(type(data) is dict and data.get('schema') == SCHEMA
        and all(type(data.get(k)) is type(v) and data[k] == v for k, v in MARK.items())
        and row.status in STATUSES)
    frozen = data['freeze']; need(data['freeze_hash'] == digest(frozen))
    rebuilt = protocol.to_supplier_request(frozen['domain_frozen']['request'], frozen['mapping'])
    need(same(frozen['domain_frozen'], rebuilt['domain_frozen']) and same(frozen['wire_frozen'], rebuilt['wire_frozen'])
        and row.source_id == frozen['mapping']['source_id']
        and frozen['original_request_key'] == frozen['wire_frozen']['request']['request_key'])
    scope = frozen['mapping']['mapping']['scope']
    need(all(getattr(row, key) == scope[key] for key in ('workspace_id', 'environment_id', 'instance_id', 'release_id')))
    history = data['receipt_history']; need(type(history) is list)
    for item in history:
        need(item['raw_hash'] == digest(item['raw']) and item['projection_hash'] == digest(item['projection']))
        projection = protocol.from_supplier_observation(item['raw'], frozen['mapping'],
            frozen['domain_frozen'], frozen['wire_frozen'])
        need(same(projection, item['projection']))
    if history:
        need(data['latest_receipt_hash'] == history[-1]['raw_hash'] and same(data['projection'], history[-1]['projection']))
    else:
        need(data['latest_receipt_hash'] is data['projection'] is None)
    need((row.observation_id is None) == (data['observation_pin'] is None))
    if row.status == 'prepared': need(not history and row.observation_id is None)
    elif row.status == 'awaiting_receipt':
        need((not history or data['projection']['status'] == 'waiting') and row.observation_id is None)
    elif row.status == 'received_no_data':
        need(bool(history) and data['projection']['status'] == 'no_data' and row.observation_id is None)
    else:
        need(bool(history) and data['projection']['status'] in {'mapped', 'unclocked'})
    if row.observation_id:
        need(data['projection']['temporal_usable'] is True and data['projection']['status'] == 'mapped'
            and data['observation_pin']['observation_id'] == row.observation_id
            and data['observation_pin']['projection_hash'] == digest(data['projection']))


def _saved_receipt_origin(db, p, row, origin):
    need(type(origin) is dict and set(origin) == {'schema', 'operation', 'job_id', 'lease_token',
        'actor_id', 'exchange_id', 'exchange_version', 'original_request_key', 'wire_request_hash', 'read_enqueue_audit_id'}
        and origin['schema'] == 'http-observation-receipt-origin-v177'
        and origin['actor_id'] == row.actor_id and origin['exchange_id'] == row.id
        and type(origin['exchange_version']) is int and 1 <= origin['exchange_version'] < row.version
        and type(origin['lease_token']) is str and bool(origin['lease_token'])
        and origin['original_request_key'] == row.data['freeze']['original_request_key']
        and origin['wire_request_hash'] == row.data['freeze']['wire_frozen']['request_hash'])
    job = scoped(db, Job, origin['job_id'], p)
    need(job.data.get('actor_id') == row.actor_id)
    if origin['operation'] == 'dispatch_post':
        need(job.kind == 'dispatch_acquisition' and job.data.get('acquisition_id') == row.acquisition_id
            and origin['read_enqueue_audit_id'] is None)
        if job.id == row.dispatch_job_id:
            need(origin['lease_token'] == row.data['freeze']['dispatch_identity']['lease_token'])
        else:
            from .managed_source_unsent_v270 import verify_retry_receipt_origin
            need(verify_retry_receipt_origin(db,p,row,origin))
    else:
        need(origin['operation'] == 'original_receipt_get' and job.kind == READ_KIND
            and job.data.get('exchange_id') == row.id
            and job.data.get('exchange_version') == origin['exchange_version']
            and job.data.get('original_request_key') == origin['original_request_key']
            and job.data.get('wire_request_hash') == origin['wire_request_hash'])
        anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == READ_AUDIT, Audit.object_id == job.id)))
        need(len(anchors) == 1 and anchors[0].id == origin['read_enqueue_audit_id']
            and anchors[0].actor_id == row.actor_id and same(anchors[0].data, {
                'exchange_id': row.id, 'request_id': job.data['request_id'],
                'instruction_hash': digest(_read_instruction(job)), 'job_key': job.idempotency_key}))
        if RESUME in job.data:
            pin = job.data[RESUME]; _resume_shape(pin)
            history = row.data['receipt_history']
            positions = [i for i, entry in enumerate(history) if same(entry['receipt_origin'], origin)]
            need(len(positions) == 1)
            current = history[positions[0]]
            need(current['raw_hash'] == pin['receipt_hash'] and current['projection_hash'] == pin['projection_hash']
                and any(entry['raw_hash'] == pin['receipt_hash'] and entry['projection_hash'] == pin['projection_hash']
                    and same(entry['receipt_origin'], pin['receipt_origin']) for entry in history[:positions[0]]),
                '显式恢复须保留原回执及更早独立来源，不能替换原包或伪造新采集')


@_safe
def checked_store(db, p, exchange_id, *, lock=False):
    if lock:
        row = scoped(db, Exchange, exchange_id, p, True)
        anchors = list(db.scalars(_authority_query(p, row.id).execution_options(populate_existing=True)))
    else:
        # Both the row and exact immutable Audit inventory use ONE MVCC statement.
        # A later legitimate writer cannot make this captured version look corrupt.
        # No row lock is attempted in read-only/repeatable-read consumers.
        inventory = select(func.array_agg(Audit.id)).where(Audit.workspace_id == p.workspace_id,
            Audit.action == ACTION, Audit.object_id == exchange_id).scalar_subquery()
        result = db.execute(select(Exchange, inventory.label('captured_audit_ids')).where(
            Exchange.workspace_id == p.workspace_id, Exchange.id == exchange_id)
            .execution_options(populate_existing=True)).first()
        need(result is not None, '交换记录不存在或不在当前工作区', 404)
        row, identities = result[0], result[1]
        need(type(identities) is list and len(identities) == row.version)
        anchors = list(db.scalars(_authority_query(p, row.id).where(Audit.id.in_(identities))
            .execution_options(populate_existing=True)))
        need(len(anchors) == len(identities))
    need(all(anchor.actor_id == row.actor_id for anchor in anchors))
    verify_authority(snapshot(row), [copy.deepcopy(anchor.data) for anchor in anchors])
    _validate_saved(row)
    if row.data['receipt_history']:
        from .evidence import evidence_bytes
        for item in row.data['receipt_history']:
            _saved_receipt_origin(db, p, row, item['receipt_origin'])
            ev, raw = evidence_bytes(db, p, item['evidence_id'])
            need(ev.media_type == 'application/json' and ev.sha256 == item['sha256']
                and type(ev.size_bytes) is int and ev.size_bytes == len(raw)
                and raw == protocol.canonical(item['raw']).encode('utf-8')
                and all(type(ev.data.get(key)) is type(value) and ev.data[key] == value for key, value in MARK.items())
                and ev.data.get('purpose') == 'parsed_http_observation_receipt_v177'
                and ev.data.get('native_http_bytes') is False
                and ev.data.get('exchange_id') == row.id
                and same(ev.data.get('receipt_origin'), item['receipt_origin']))
    return row


def _owner(db, p, row):
    need(p.user_id == row.actor_id, '首版仅原派发账号可以继续原HTTP交换', 403)
    lock_current_actor(db, p)


def _leased(db, p):
    from .leases import ownership
    lease = ownership(db)
    need(type(lease) is dict and type(lease.get('job_id')) is str and bool(lease.get('lease_token')))
    job = scoped(db, Job, lease['job_id'], p)
    need(job.status == 'running' and job.lease_token == lease['lease_token']
        and job.data.get('actor_id') == p.user_id)
    return job, lease


def _parent_pins(db, p, acq):
    plan = scoped(db, ObservationPlan, acq.data['observation_plan_id'], p)
    origin = plan.data['observation_request']
    gap = scoped(db, DataGap, origin['gap_id'], p); query = scoped(db, DataRun, origin['query_id'], p)
    delegation = origin['goal_delegation']; task = scoped(db, Task, delegation['task_id'], p)
    policy = scoped(db, MonitorPolicy, acq.data['policy_id'], p)
    need(plan.entity_id == gap.entity_id == acq.entity_id and plan.policy_id == policy.id
        and gap.query_id == query.id and same(query.data['goal_delegation'], delegation)
        and origin['query_request_hash'] == digest(query.data['request'])
        and same(origin['request'], gap.data['request'])
        and task.data.get('native_goal_query_id') == query.id
        and query.data.get('native_goal_task_id') == task.id
        and query.data.get('native_goal_request_hash') == digest(query.data['request'])
        and same(delegation['goal_snapshot'], task.data['native_goal_context']['goal_snapshot'])
        and task.data.get('native_goal_context_hash') == digest(task.data['native_goal_context'])
        and query.data.get('native_goal_context_hash') == digest(task.data['native_goal_context'])
        and policy.data.get('approved_hash') == acq.data['policy_hash'])
    need(set(acq.data['selected_fields']) <= set(origin['fields'])
        and set(origin['fields']) <= set(delegation['goal_snapshot']['spec']['required_fields']))
    return {'schema': 'http-observation-parent-pins-v177', 'entity_id': acq.entity_id,
        'plan_id': plan.id, 'plan_origin': copy.deepcopy(origin), 'plan_origin_hash': digest(origin),
        'gap_id': gap.id, 'query_id': query.id, 'query_request_hash': digest(query.data['request']),
        'task_id': task.id, 'goal_snapshot': copy.deepcopy(delegation['goal_snapshot']),
        'goal_snapshot_hash': digest(delegation['goal_snapshot']),
        'goal_context_hash': digest(task.data['native_goal_context']),
        'policy_id': policy.id, 'policy_hash': acq.data['policy_hash'],
        'max_duration_seconds': policy.data['max_duration_seconds'],
        **{key: copy.deepcopy(acq.data.get(key)) for key in ('sensor_asset_id', 'sensor_asset_version',
            'sensor_device_id', 'sensor_device_version', 'device_binding_id', 'device_binding_version',
            'selected_action', 'selected_fields', 'reserved_cost_minor', 'currency')}}


def _new_freeze(db, p, acq, dispatch_job, mapping, domain, wire):
    rebuilt = protocol.to_supplier_request(domain['request'], mapping)
    need(same(domain, rebuilt['domain_frozen']) and same(wire, rebuilt['wire_frozen']))
    scope = mapping['mapping']['scope']
    need(scope['workspace_id'] == p.workspace_id and mapping['source_id'] == acq.capability_id)
    source = scoped(db, Capability, acq.capability_id, p)
    instance = scoped(db, DomainInstance, scope['instance_id'], p)
    release = scoped(db, InstanceRelease, scope['release_id'], p)
    environment = scoped(db, Environment, scope['environment_id'], p)
    entity = scoped(db, Entity, acq.entity_id, p)
    package = scoped(db, Capability, scope['package_id'], p)
    asset = scoped(db, SensorAsset, acq.data['sensor_asset_id'], p)
    need(source.kind == 'source' and source.status == 'published'
        and source.content_hash == source.validated_hash == digest(source.data) == mapping['source_manifest_hash']
        and package.kind == 'domain_package' and package.content_hash == package.validated_hash == digest(package.data) == scope['package_hash']
        and release.instance_id == instance.id and release.package_id == package.id
        and environment.instance_id == instance.id and environment.release_id == release.id
        and entity.namespace == instance.namespace and entity.entity_type == scope['entity_type']
        and entity.schema_capability_id == package.id and asset.source_id == source.id
        and asset.version == acq.data['sensor_asset_version'])
    schemas = package.data['specification']['entity_schemas']
    actual = protocol.freeze_mapping(source.data, {**scope, 'source_id': source.id}, schemas, mapping['mapping']['links'])
    need(same(actual, mapping), '映射须从真实Source和实际release Schema重新核对，不能使用caller自封摘要')
    request = domain['request']
    need(request['request_key'] == acq.idempotency_key and request['target_external_id'] == entity.external_id
        and request['asset_external_id'] == asset.external_id and same(request['fields'], acq.data['selected_fields'])
        and request['action'] == acq.data['selected_action'] and request['max_cost_minor'] == acq.data['reserved_cost_minor']
        and request['currency'] == acq.data['currency'] and acq.data['source_hash'] == source.content_hash)
    pins = _parent_pins(db, p, acq)
    need(type(request['max_duration_seconds']) is int
        and request['max_duration_seconds'] == pins['max_duration_seconds'])
    return {'schema': 'http-observation-exchange-freeze-v177', 'mapping': copy.deepcopy(mapping),
        'domain_frozen': copy.deepcopy(domain), 'wire_frozen': copy.deepcopy(wire), 'parent_pins': pins,
        'original_request_key': acq.idempotency_key,
        'dispatch_identity': {'job_id': dispatch_job.id, 'kind': dispatch_job.kind,
            'idempotency_key': dispatch_job.idempotency_key, 'actor_id': p.user_id,
            'lease_token': dispatch_job.lease_token}}


@_safe
def create(db, p, acquisition_id, dispatch_job_id, mapping, domain_frozen, wire_frozen):
    job, lease = _leased(db, p)
    need(job.id == dispatch_job_id and job.kind == 'dispatch_acquisition'
        and job.data.get('acquisition_id') == acquisition_id)
    lock_current_actor(db, p)
    db.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': int(digest({
        'http_exchange': acquisition_id, 'workspace_id': p.workspace_id})[:15], 16)})
    acq = scoped(db, Acquisition, acquisition_id, p, True)
    need(acq.data.get('actor_id') == p.user_id, '仅原派发账号可以冻结本次交换', 403)
    prior = db.scalar(select(Exchange).where(Exchange.workspace_id == p.workspace_id, Exchange.acquisition_id == acq.id))
    if prior:
        prior = checked_store(db, p, prior.id, lock=True)
        frozen = prior.data['freeze']
        need(prior.dispatch_job_id == dispatch_job_id and same(mapping, frozen['mapping'])
            and same(domain_frozen, frozen['domain_frozen']) and same(wire_frozen, frozen['wire_frozen']))
        return prior, False  # Never a dispatch/replay grant.
    remnants = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, Audit.data['acquisition_id'].as_string() == acq.id)))
    need(not remnants, '同补采仍有独立交换回执，主记录缺失；禁止重建或重新派发')
    frozen = _new_freeze(db, p, acq, job, mapping, domain_frozen, wire_frozen)
    scope = mapping['mapping']['scope']
    row = Exchange(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, acquisition_id=acq.id,
        source_id=acq.capability_id, environment_id=scope['environment_id'], instance_id=scope['instance_id'],
        release_id=scope['release_id'], dispatch_job_id=job.id, observation_id=None, status='prepared', version=1,
        data={'schema': SCHEMA, **MARK, 'freeze': frozen, 'freeze_hash': digest(frozen),
            'receipt_history': [], 'latest_receipt_hash': None, 'projection': None,
            'observation_pin': None, 'read_jobs': [], 'active_read_job_id': None})
    db.add(row); _record(db, p, row, 'create', None)
    return row, True


@_safe
def mark_awaiting_receipt(db, p, exchange_id, expected_version):
    row = checked_store(db, p, exchange_id, lock=True); _owner(db, p, row)
    job, lease = _leased(db, p)
    need(job.id == row.dispatch_job_id and job.kind == 'dispatch_acquisition'
        and job.lease_token == row.data['freeze']['dispatch_identity']['lease_token']
        and row.version == expected_version and row.status == 'prepared')
    previous = digest(snapshot(row)); row.status = 'awaiting_receipt'; row.version += 1
    _record(db, p, row, 'await_original_receipt', previous)
    return row


def _read_instruction(job):
    data = job.data
    value = {key: copy.deepcopy(data[key]) for key in ('exchange_id', 'actor_id', 'request_id',
        'original_request_key', 'wire_request_hash', 'exchange_version')}
    if RESUME in data: value[RESUME] = copy.deepcopy(data[RESUME])
    return value


def can_resume_observation(row):
    projection = row.data.get('projection')
    return (row.status == 'received' and row.observation_id is None and type(projection) is dict
        and projection.get('status') == 'mapped' and projection.get('temporal_usable') is True)


def _resume_shape(pin):
    need(type(pin) is dict and set(pin) == {'schema', 'receipt_hash', 'projection_hash', 'receipt_origin'}
        and pin['schema'] == RESUME_SCHEMA and type(pin['receipt_origin']) is dict)
    protocol._hash(pin['receipt_hash']); protocol._hash(pin['projection_hash'])


def _resume_pin(row):
    need(can_resume_observation(row) and bool(row.data['receipt_history']))
    latest = row.data['receipt_history'][-1]
    return {'schema': RESUME_SCHEMA, 'receipt_hash': latest['raw_hash'],
        'projection_hash': latest['projection_hash'], 'receipt_origin': copy.deepcopy(latest['receipt_origin'])}


def _resume_allowed(row, job):
    if RESUME not in job.data: return False
    _resume_shape(job.data[RESUME])
    need(job.kind == READ_KIND and row.data['active_read_job_id'] == job.id
        and can_resume_observation(row) and same(job.data[RESUME], _resume_pin(row)),
        '本次显式原号恢复与已保存原包不同，不能重采或覆盖')
    return True


@_safe
def enqueue_receipt_read(db, p, exchange_id, expected_version, request_id):
    protocol._uuid(request_id)
    row = checked_store(db, p, exchange_id, lock=True); _owner(db, p, row)
    need(type(expected_version) is int and row.version == expected_version)
    resume = can_resume_observation(row)
    if row.status in {'received', 'received_no_data'} and not resume: return row, None
    need(row.status == 'awaiting_receipt' or resume, '交换尚未进入原号回执核对阶段')
    for saved in row.data['read_jobs']:
        if saved['request_id'] == request_id:
            job = scoped(db, Job, saved['job_id'], p)
            need(job.kind == READ_KIND and digest(_read_instruction(job)) == saved['instruction_hash'])
            return row, job
    if row.data['active_read_job_id']:
        active = scoped(db, Job, row.data['active_read_job_id'], p)
        need(active.status not in {'queued', 'running'}, '原号回执查询仍在进行，不增加第二个查询任务')
    resume_pin = _resume_pin(row) if resume else None
    previous = digest(snapshot(row)); row.version += 1
    job = Job(id=uid(), workspace_id=p.workspace_id, kind=READ_KIND, status='queued',
        idempotency_key='http-observation-receipt:' + row.id + ':' + request_id,
        data={'exchange_id': row.id, 'actor_id': p.user_id, 'request_id': request_id,
            'original_request_key': row.data['freeze']['original_request_key'],
            'wire_request_hash': row.data['freeze']['wire_frozen']['request_hash'], 'exchange_version': row.version})
    if resume_pin is not None: job.data = {**job.data, RESUME: resume_pin}
    db.add(job); instruction_hash = digest(_read_instruction(job))
    row.data = {**row.data, 'read_jobs': [*row.data['read_jobs'], {'job_id': job.id,
        'request_id': request_id, 'instruction_hash': instruction_hash}], 'active_read_job_id': job.id}
    audit(db, p, READ_AUDIT, job.id, {'exchange_id': row.id, 'request_id': request_id,
        'instruction_hash': instruction_hash, 'job_key': job.idempotency_key})
    _record(db, p, row, 'receipt_read.enqueued', previous)
    return row, job


@_safe
def receipt_origin(db, p, exchange_id):
    row = checked_store(db, p, exchange_id); need(p.user_id == row.actor_id, status=403)
    job, lease = _leased(db, p)
    operation = 'dispatch_post'
    read_audit_id = None
    if job.kind == 'dispatch_acquisition':
        need(job.data.get('acquisition_id') == row.acquisition_id)
        if job.id == row.dispatch_job_id:
            need(lease['lease_token'] == row.data['freeze']['dispatch_identity']['lease_token'])
        else:
            from .managed_source_unsent_v270 import require_unsent_retry
            acq=scoped(db,Acquisition,row.acquisition_id,p)
            need(require_unsent_retry(db,p,acq,row,stage='receive'))
    else:
        need(job.kind == READ_KIND and job.data.get('exchange_id') == row.id
            and job.data.get('original_request_key') == row.data['freeze']['original_request_key']
            and job.data.get('wire_request_hash') == row.data['freeze']['wire_frozen']['request_hash']
            and type(job.data.get('exchange_version')) is int
            and row.data['active_read_job_id'] == job.id)
        anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == READ_AUDIT, Audit.object_id == job.id)))
        need(len(anchors) == 1 and anchors[0].actor_id == p.user_id and same(anchors[0].data, {
            'exchange_id': row.id, 'request_id': job.data['request_id'],
            'instruction_hash': digest(_read_instruction(job)), 'job_key': job.idempotency_key}))
        operation = 'original_receipt_get'; read_audit_id = anchors[0].id
    result = {'schema': 'http-observation-receipt-origin-v177', 'operation': operation,
        'job_id': job.id, 'lease_token': lease['lease_token'], 'actor_id': p.user_id,
        'exchange_id': row.id, 'exchange_version': job.data['exchange_version'] if operation == 'original_receipt_get' else row.version,
        'original_request_key': row.data['freeze']['original_request_key'],
        'wire_request_hash': row.data['freeze']['wire_frozen']['request_hash'],
        'read_enqueue_audit_id': read_audit_id}
    # Saving a response advances the exchange, not the immutable GET instruction.
    # Only the same actually owned parent/token can continue its exact saved
    # receipt. The independently anchored historical origin is never rewritten.
    history = row.data['receipt_history']
    prior = history[-1]['receipt_origin'] if history else None
    if prior and prior['job_id'] == job.id:
        result['exchange_version'] = prior['exchange_version']
        need(same(result, prior), '原回执已保存，但当前租约或原GET指令不同，不能借历史继续派生')
    else:
        need((row.status not in {'received', 'received_no_data'} or _resume_allowed(row, job))
            and result['exchange_version'] == row.version)
    return result


@_safe
def record_receipt(db, p, exchange_id, expected_version, raw_receipt, *, receipt_origin):
    from .leases import checkpoint
    _leased(db, p); checkpoint(db)  # Actual current lease/IAM, before freezing a returned result.
    actual = globals()['receipt_origin'](db, p, exchange_id)
    need(same(actual, receipt_origin), '回执必须来自原派发或已独立登记的原号GET实际租约')
    row = checked_store(db, p, exchange_id, lock=True); _owner(db, p, row)
    need(type(expected_version) is int and row.version == expected_version)
    raw_hash = digest(raw_receipt)
    same_parent = bool(row.data['receipt_history']) and same(row.data['receipt_history'][-1]['receipt_origin'], actual)
    resume = False
    if row.data['latest_receipt_hash'] == raw_hash:
        if same_parent or row.status == 'awaiting_receipt' and actual['exchange_version'] == expected_version:
            return row
        job = scoped(db, Job, actual['job_id'], p)
        resume = _resume_allowed(row, job)
        need(resume, '已保存终态回执只允许原租约或独立登记的原包恢复，不能替换其来源')
    need(row.status == 'awaiting_receipt' or resume, '已完成原回执不能覆盖、降级或换一个新结果')
    need(actual['exchange_version'] == expected_version, '新回执须匹配原GET登记版本，不能改写指令绕过CAS')
    frozen = row.data['freeze']
    projection = protocol.from_supplier_observation(raw_receipt, frozen['mapping'],
        frozen['domain_frozen'], frozen['wire_frozen'])
    if resume:
        need(digest(projection) == job.data[RESUME]['projection_hash']
            and raw_hash == job.data[RESUME]['receipt_hash'], '恢复只重用原包，不接受更新数值或新时间')
    from .evidence import save_immutable
    evidence = save_immutable(db, p, protocol.canonical(raw_receipt).encode('utf-8'), 'application/json',
        {**MARK, 'purpose': 'parsed_http_observation_receipt_v177', 'native_http_bytes': False,
            'exchange_id': row.id, 'acquisition_id': row.acquisition_id,
            'receipt_origin': copy.deepcopy(actual), 'name': 'HTTP补采原号解析回执.json'})
    previous = digest(snapshot(row))
    entry = {'raw': copy.deepcopy(raw_receipt), 'raw_hash': raw_hash,
        'evidence_id': evidence.id, 'sha256': evidence.sha256,
        'projection': copy.deepcopy(projection), 'projection_hash': digest(projection),
        'receipt_origin': copy.deepcopy(actual)}
    row.status = ('awaiting_receipt' if projection['status'] == 'waiting' else
        'received_no_data' if projection['status'] == 'no_data' else 'received')
    row.data = {**row.data, 'receipt_history': [*row.data['receipt_history'], entry],
        'latest_receipt_hash': raw_hash, 'projection': copy.deepcopy(projection)}
    row.version += 1; _record(db, p, row, 'receipt.recorded', previous)
    return row


@_safe
def attach_observation(db, p, exchange_id, expected_version, observation_id):
    row = checked_store(db, p, exchange_id, lock=True); _owner(db, p, row)
    need(type(expected_version) is int and row.version == expected_version)
    if row.observation_id:
        need(row.observation_id == observation_id); return row
    projection = row.data['projection']
    need(row.status == 'received' and projection and projection['temporal_usable'] is True
        and projection['status'] == 'mapped', '原供应商资料缺少可用原采样时间或观测，不绑定正式观测')
    observation = scoped(db, Observation, observation_id, p, True)
    body = observation.data['original_request']; frozen = row.data['freeze']; pins = frozen['parent_pins']
    request_basis = copy.deepcopy(body)
    if request_basis.get('evidence_ids') == []: request_basis.pop('evidence_ids')
    need(observation.source_id == row.source_id and observation.entity_id == pins['entity_id']
        and observation.data.get('submitted_by') == p.user_id
        and observation.data.get('input_hash') == digest(request_basis)
        and body.get('evidence_ids', []) == []
        and body['source_id'] == row.source_id and body['entity_id'] == pins['entity_id']
        and observation.source_record_id == 'http-acquisition:' + row.id
        and body['source_record_id'] == observation.source_record_id
        and body['acquisition_id'] == row.acquisition_id and body['sensor_asset_id'] == pins['sensor_asset_id']
        and body['sensor_modality'] == 'numeric' and body['startup_mode'] == 'automatic'
        and observation.data.get('acquisition_id') == row.acquisition_id
        and observation.data.get('sensor_asset_id') == pins['sensor_asset_id']
        and observation.data.get('sensor_modality') == 'numeric'
        and same(body['attributes'], projection['mapped_attributes'])
        and same(observation.data['attributes'], projection['mapped_attributes'])
        and body['observed_at'] == projection['observed_at']
        and observation.observed_at is not None
        and observation.observed_at.tzinfo is not None
        and observation.observed_at.astimezone(timezone.utc)
            == datetime.fromisoformat(projection['observed_at']).astimezone(timezone.utc))
    previous = digest(snapshot(row)); row.observation_id = observation.id
    row.data = {**row.data, 'observation_pin': {'observation_id': observation.id,
        'input_hash': digest(request_basis), 'original_request': copy.deepcopy(body),
        'projection_hash': digest(projection)}}
    row.version += 1; _record(db, p, row, 'observation.attached', previous)
    return row
