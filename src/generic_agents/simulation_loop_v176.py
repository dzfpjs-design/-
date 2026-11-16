"""Durable HTTP device rehearsal; never field measurements or implicit approval."""
import copy
from datetime import datetime, timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .contracts import digest, ObservationCreate
from .models import DomainInstance, InstanceRelease, Entity, Capability, Observation, Task, Job, Invocation, Event, Audit, now, uid
from .simulation_models_v176 import (SimulationEnvironmentV176 as Environment,
    SimulationDefinitionV176 as Definition, SimulationSampleV176 as Sample,
    SimulationDailyV176 as Daily)
from .services import scoped, published, require, audit, enqueue_event
from .security import Principal

MARK = {'simulation': True, 'origin_kind': 'simulated_equipment', 'field_verified': False}
KIND = 'sample_simulation_v176'
PROCESS = 'process_simulation_observation_v176'


def need(ok, message='模拟接入依据或当前权限已变化，原记录保留', code=409):
    if not ok: raise HTTPException(code, message)


def json_bytes(value):
    import json
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def environment(db, p, identity, *, live=True):
    row = scoped(db, Environment, identity, p)
    d = row.data
    need(d.get('schema') == 'simulation-environment-v176' and all(type(d.get(k)) is type(v) and d[k] == v for k, v in MARK.items()))
    instance = scoped(db, DomainInstance, row.instance_id, p)
    release = scoped(db, InstanceRelease, row.release_id, p)
    need(d['instance_id'] == instance.id and d['release_id'] == release.id and d['namespace'] == instance.namespace
         and release.instance_id == instance.id and d['configuration_hash'] == release.configuration_hash
         and d['package_id'] == release.package_id and release.data['snapshot']['profile'].get('simulation_v176') is True)
    checks = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == row.id, Audit.action == 'simulation.environment.created_v176')))
    need(len(checks) == 1 and checks[0].actor_id == row.actor_id and checks[0].data.get('record_hash') == digest(d))
    if live:
        from .instance_api import active
        current, selected = active(db, p, instance.id)
        need(selected.id == release.id and current.id == instance.id)
    return row, instance, release


def create_environment(db, p, body):
    require(p, 'admin')
    from .instance_api import active
    instance, release = active(db, p, body['instance_id'])
    need(release.id == body['release_id'] and release.data['snapshot']['profile'].get('simulation_v176') is True,
         '请先创建明确标为模拟联调的独立业务实例')
    need(not db.scalar(select(Environment.id).where(Environment.instance_id == instance.id)), '该实例已有永久模拟来源登记')
    objects = list(db.scalars(select(Entity.id).where(Entity.workspace_id == p.workspace_id, Entity.namespace == instance.namespace)))
    need(not db.scalar(select(Observation.id).where(Observation.workspace_id == p.workspace_id, Observation.entity_id.in_(objects)))
         and not db.scalar(select(Task.id).where(Task.workspace_id == p.workspace_id, Task.data['instance_id'].as_string() == instance.id)),
         '已有业务记录的实例不能改签为模拟环境，请建立独立实例')
    value = {'schema': 'simulation-environment-v176', **MARK, 'instance_id': instance.id,
        'release_id': release.id, 'namespace': instance.namespace, 'package_id': release.package_id,
        'configuration_hash': release.configuration_hash, 'created_by': p.user_id, 'note': body['note']}
    row = Environment(id=uid(), workspace_id=p.workspace_id, instance_id=instance.id,
        release_id=release.id, actor_id=p.user_id, data=value)
    db.add(row); db.flush(); audit(db, p, 'simulation.environment.created_v176', row.id, {'record_hash': digest(value)})
    return row


def create_definition(db, p, body):
    require(p, 'admin')
    env, instance, release = environment(db, p, body['environment_id'])
    entity = scoped(db, Entity, body['entity_id'], p)
    need(entity.namespace == instance.namespace and entity.schema_capability_id == release.package_id)
    caps = {key: published(db, p, body[key]) for key in ('source_id', 'extractor_id', 'gateway_id')}
    source, extractor, gateway = (caps[k] for k in ('source_id', 'extractor_id', 'gateway_id'))
    need(source.kind == 'source' and source.data['specification'].get('simulation_environment_id') == env.id
         and source.data['data_policy'].get('test_only') is True and source.data['data_policy'].get('cloud_allowed') is False
         and source.data['data_policy'].get('deployment') == 'local')
    need(extractor.kind == 'tool' and extractor.data['execution'].get('adapter') == 'builtin' and extractor.data['execution'].get('handler') == 'numeric_rules'
         and not extractor.data['execution'].get('side_effect') and extractor.data['data_policy'].get('cloud_allowed') is False)
    need(gateway.namespace == 'equipment_simulation' and gateway.name == 'read_equipment'
         and gateway.data['execution'].get('adapter') == 'http' and not gateway.data['execution'].get('side_effect')
         and gateway.data['data_policy'].get('simulation') is True and gateway.data['data_policy'].get('test_only') is True)
    need(all('perception' in c.data['allowed_agent_roles'] for c in caps.values()))
    business_id = body['business_id']
    need(business_id in instance.data['enabled_business_ids'])
    from .layered_approval_store_v175 import policy_for, current_policy
    policy = policy_for(db, p, instance.id, business_id)
    need(policy is not None and policy.enabled, '请为异常处置业务启用层级审批，系统不会跳过审批自动执行')
    current_policy(db, p, policy.id)
    fields = body['fields']
    from .simulation_origin_v176 import WEATHER_UNITS
    need(type(fields) is list and fields and len(fields) == len(set(fields)) and set(fields) <= set(WEATHER_UNITS), '请选择已实现的模拟气象字段', 422)
    try: until = datetime.fromisoformat(body['valid_until'])
    except (ValueError, TypeError): raise HTTPException(422, '授权到期时间格式不正确') from None
    need(until.tzinfo is not None and now() < until <= now() + timedelta(days=7), '模拟联调授权应在未来七天内到期', 422)
    value = {'schema': 'simulation-definition-v176', 'environment_hash': digest(env.data),
        'instance_id': instance.id, 'release_id': release.id, 'entity_id': entity.id,
        **{k: caps[k].id for k in caps}, 'business_id': business_id, 'device_id': 'sim-weather-01',
        'fields': fields, 'interval_seconds': body['interval_seconds'], 'valid_until': body['valid_until'],
        'goal_spec': copy.deepcopy(body.get('goal_spec')), 'pins': {c.id: c.content_hash for c in caps.values()}}
    row = Definition(id=uid(), workspace_id=p.workspace_id, environment_id=env.id, actor_id=p.user_id,
        status='paused', data=value)
    db.add(row); db.flush(); audit(db, p, 'simulation.definition.created_v176', row.id, {'record_hash': digest(value)})
    return row


def checked_definition(db, p, identity, *, live=True):
    row = scoped(db, Definition, identity, p)
    env, instance, release = environment(db, p, row.environment_id, live=live)
    d = row.data
    need(d.get('schema') == 'simulation-definition-v176' and d['environment_hash'] == digest(env.data)
         and d['instance_id'] == instance.id and d['release_id'] == release.id)
    entity = scoped(db, Entity, d['entity_id'], p)
    need(entity.namespace == instance.namespace and entity.schema_capability_id == release.package_id)
    checks = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == row.id, Audit.action == 'simulation.definition.created_v176')))
    need(len(checks) == 1 and checks[0].actor_id == row.actor_id and checks[0].data.get('record_hash') == digest(d))
    if live:
        need(now() < datetime.fromisoformat(d['valid_until']), '模拟联调授权已到期，历史记录仍可查阅')
        for identity, pin in d['pins'].items(): need(published(db, p, identity).content_hash == pin)
        from .layered_approval_store_v175 import policy_for, current_policy
        policy = policy_for(db, p, instance.id, d['business_id'])
        need(policy is not None and policy.enabled, '业务审批策略已停止，未继续新接入或补采')
        current_policy(db, p, policy.id)
    return row, env, instance, release


def enqueue_sample(db, p, definition, request_id):
    need(definition.status == 'enabled', '请先启用接入计划')
    prior = db.scalar(select(Sample).where(Sample.workspace_id == p.workspace_id, Sample.request_id == request_id))
    if prior:
        need(prior.definition_id == definition.id and prior.actor_id == p.user_id); return prior
    inputs = {'device_id': definition.data['device_id'], 'action': 'read_telemetry', 'expected_revision': None, 'arguments': {}}
    env = scoped(db, Environment, definition.environment_id, p)
    sample = Sample(id=uid(), workspace_id=p.workspace_id, definition_id=definition.id, actor_id=p.user_id,
        request_id=request_id, data={'schema': 'simulation-sample-v176', 'definition_hash': digest(definition.data),
        'environment_hash': digest(env.data), 'inputs': inputs, 'input_hash': digest(inputs), 'task_id': None})
    db.add(sample); db.flush()
    job = Job(id=uid(), workspace_id=p.workspace_id, kind=KIND, idempotency_key='simulation-sample:' + sample.id,
        data={'sample_id': sample.id, 'actor_id': p.user_id})
    db.add(job); sample.data = {**sample.data, 'job_id': job.id}
    audit(db, p, 'simulation.sample.enqueued_v176', sample.id, {'definition_id': definition.id, 'request_id': request_id})
    return sample


def run_sample(db, p, job, *, cached_only=False):
    from .leases import checkpoint
    sample = scoped(db, Sample, job.data['sample_id'], p, True)
    need(sample.data.get('job_id') == job.id and sample.actor_id == p.user_id)
    if sample.observation_id: return
    definition, env, instance, release = checked_definition(db, p, sample.definition_id)
    need(definition.status == 'enabled' and sample.data['definition_hash'] == digest(definition.data))
    sample.status = 'running'; db.commit(); checkpoint(db)
    if cached_only:
        call = scoped(db, Invocation, sample.invocation_id, p, True)
        need(call.idempotency_key == 'simulation-sample:' + sample.id and call.capability_id == definition.data['gateway_id']
             and call.data.get('input_hash') == sample.data['input_hash'] and call.status == 'succeeded')
    else:
        from .gateway import invoke
        call = invoke(db, p, definition.data['gateway_id'], 'perception', sample.data['inputs'], 'simulation-sample:' + sample.id)
    sample = scoped(db, Sample, sample.id, p, True)
    sample.invocation_id = call.id
    if call.status != 'succeeded':
        sample.status = 'awaiting_receipt'; sample.data = {**sample.data, 'issue': '原取样调用尚无成功回执；保留原号，未重新发送'}; return
    # Recheck after I/O; losing authority preserves the original call but cannot
    # turn its parsed output into an active domain observation.
    definition, env, instance, release = checked_definition(db, p, definition.id)
    need(definition.status == 'enabled')
    from .simulation_origin_v176 import sample_values
    output = call.data['output']; values = sample_values(output, definition.data['device_id'], definition.data['fields'])
    from .evidence import save_immutable
    receipt = save_immutable(db, p, json_bytes(output), 'application/json',
        {**MARK, 'modality': 'json', 'purpose': 'parsed_device_receipt_copy_v176', 'invocation_id': call.id,
         'native_http_bytes': False, 'name': '模拟气象取样接入回执.json'})
    clock = datetime.fromisoformat(values['observed_at'])
    body = ObservationCreate(source_id=definition.data['source_id'], source_record_id='simulation-sample:' + sample.id,
        processing_version='simulation-v176', entity_id=definition.data['entity_id'], observed_at=values['observed_at'],
        business_day=str(clock.astimezone(ZoneInfo('Asia/Shanghai')).date()), attributes=values['attributes'],
        extractor_id=definition.data['extractor_id'], startup_mode='automatic')
    from .api import ingest_observation_record
    created = ingest_observation_record(body, p, db, commit=False)
    observation = scoped(db, Observation, created['id'], p, True)
    process = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == 'extract:' + observation.id).with_for_update())
    need(process is not None and process.status == 'queued' and process.attempts == 0 and process.kind == 'process_observation')
    process.kind = PROCESS
    process.status = 'waiting_parent'
    observation.data = {**observation.data, 'simulation_origin': {'schema': 'simulation-origin-v176',
        'environment_id': env.id, 'definition_id': definition.id, 'sample_id': sample.id,
        'invocation_id': call.id, 'receipt_evidence_id': receipt.id}, 'source_time_verified': False,
        'date_basis': 'simulator_sample_time', 'field_units': values['field_units']}
    sample.observation_id = observation.id; sample.status = 'received'
    sample.data = {**sample.data, 'output_hash': digest(output), 'receipt_evidence_id': receipt.id,
        'receipt_sha256': receipt.sha256, 'observation_request_hash': digest(observation.data['original_request']),
        'processing_job_id': process.id, 'received_at': now().isoformat()}
    from .simulation_origin_v176 import receipt_basis
    audit(db, p, 'simulation.sample.received_v176', sample.id, {'observation_id': observation.id,
        'invocation_id': call.id, 'origin_hash': digest(observation.data['simulation_origin']), 'output_hash': digest(output),
        'record_hash': digest(receipt_basis(sample.data))})


def finalize_sample(db, job):
    """Activate child only in the same final transaction as parent success."""
    sample = db.get(Sample, job.data['sample_id'])
    need(sample is not None and sample.workspace_id == job.workspace_id)
    if job.kind == 'recover_simulation_sample_v176':
        original = db.get(Job, sample.data['job_id'])
        need(original is not None and original.kind == KIND and original.status == 'succeeded'
             and original.workspace_id == job.workspace_id and original.data['sample_id'] == sample.id)
    else: need(job.kind == KIND and sample.data['job_id'] == job.id)
    if sample.observation_id is None: return
    process = db.get(Job, sample.data['processing_job_id'])
    need(sample.status == 'received' and process is not None and process.workspace_id == job.workspace_id
         and process.kind == PROCESS and process.status == 'waiting_parent'
         and process.data['observation_id'] == sample.observation_id)
    process.status = 'queued'


def simulated_source(db, identity):
    return bool(db.scalar(select(Definition.id).where(Definition.data['source_id'].as_string() == identity).limit(1)))


def handoff(db, p, sample, signal):
    from .simulation_origin_v176 import guard
    observation = scoped(db, Observation, sample.observation_id, p)
    guard(db, p, observation)
    definition, env, instance, release = checked_definition(db, p, sample.definition_id)
    need(signal.instance_id == instance.id and signal.entity_id == observation.entity_id
         and signal.data['basis']['observation_id'] == observation.id)
    from .workspace_api import NewTask, create_task
    body = NewTask(release_id=release.id, business_id=definition.data['business_id'], entity_ids=[observation.entity_id],
        title='模拟设备异常核查：' + ', '.join(definition.data['fields']),
        request_id=str(uuid5(UUID(sample.id), 'approved-simulation-handoff-v176')), goal_spec=definition.data.get('goal_spec'),
        signal_id=signal.id, signal_version=signal.version, signal_basis_hash=signal.data['basis_hash'])
    task = create_task(instance.id, body, p, db, simulation_handoff=sample)
    from .layered_approval_models_v175 import ApprovalManagedTaskV175
    managed = db.scalar(select(ApprovalManagedTaskV175).where(ApprovalManagedTaskV175.task_id == task.id))
    need(managed is not None and managed.status == 'draft', '异常处置必须进入真实审批草稿')
    task.data = {**task.data, 'simulation_origin': {**MARK, 'environment_id': env.id, 'sample_id': sample.id}}
    sample.data = {**sample.data, 'task_id': task.id, 'signal_id': signal.id}
    audit(db, p, 'simulation.anomaly.handed_off_v176', sample.id, {'task_id': task.id, 'signal_id': signal.id})
    return task


def validate_handoff(db, p, sample, body, instance, release):
    observation = scoped(db, Observation, sample.observation_id, p)
    from .simulation_origin_v176 import guard
    guard(db, p, observation)
    definition, env, bound, selected = checked_definition(db, p, sample.definition_id)
    need(bound.id == instance.id and selected.id == release.id and body.entity_ids == [observation.entity_id]
         and body.business_id == definition.data['business_id'] and body.signal_id
         and body.request_id == str(uuid5(UUID(sample.id), 'approved-simulation-handoff-v176')))
    from .perception_signals import snapshot
    return snapshot(db, p, body.signal_id, instance, release, body.entity_ids, body.signal_version, body.signal_basis_hash)


def reconcile_daily(db, p, environment_id):
    env, _, _ = environment(db, p, environment_id, live=False)
    definitions = list(db.scalars(select(Definition).where(Definition.environment_id == env.id)))
    samples = list(db.scalars(select(Sample).where(Sample.workspace_id == p.workspace_id,
        Sample.definition_id.in_([x.id for x in definitions])).order_by(Sample.created_at, Sample.id)))
    by_day = {}
    for sample in samples:
        day = str(sample.created_at.astimezone(ZoneInfo('Asia/Shanghai')).date())
        obs = db.get(Observation, sample.observation_id) if sample.observation_id else None
        receipt = db.scalar(select(Event).where(Event.workspace_id == p.workspace_id,
            Event.idempotency_key == 'data:' + obs.id)) if obs else None
        by_day.setdefault(day, []).append({'sample_id': sample.id, 'invocation_id': sample.invocation_id,
            'observation_id': sample.observation_id, 'sample_status': sample.status,
            'processing_status': obs.status if obs else None, 'data_received': bool(receipt and receipt.status == 'received'),
            'receipt_evidence_id': sample.data.get('receipt_evidence_id'), 'receipt_sha256': sample.data.get('receipt_sha256'),
            'task_id': sample.data.get('task_id'), 'observed_at': obs.observed_at.isoformat() if obs and obs.observed_at else None})
    for day, records in by_day.items():
        db.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': int(digest({'sim_daily': env.id, 'day': day})[:15], 16)})
        row = db.scalar(select(Daily).where(Daily.environment_id == env.id, Daily.business_day == day).with_for_update())
        if row and row.data.get('records_hash') == digest(records): continue
        if row is None:
            row = Daily(id=uid(), workspace_id=p.workspace_id, environment_id=env.id, business_day=day, data={}); db.add(row); db.flush()
        else: row.version += 1
        data = {'schema': 'http-sim-daily-v176', **MARK, 'environment_id': env.id, 'business_day': day,
            'day_basis': 'platform_reception_day_Asia_Shanghai', 'records': records, 'records_hash': digest(records),
            'cloud_calls': 0, 'physical_state_verified': False, 'entity_state_updated': False}
        event = enqueue_event(db, p, 'perception.daily', 'http-sim-daily:' + row.id + ':' + str(row.version), data)
        row.data = {**data, 'event_id': event.id}


def schedule_once(workspace_id=None):
    """Called by the normal worker; no HTTP or inference in the scheduler."""
    from .db import engine
    from .worker import actor
    with Session(engine()) as db:
        query = select(Definition).where(Definition.status == 'enabled', Definition.next_due <= now())
        if workspace_id: query = query.where(Definition.workspace_id == workspace_id)
        for definition in db.scalars(query.order_by(Definition.next_due).limit(20).with_for_update(skip_locked=True)):
            try:
                with db.begin_nested():
                    p = actor(db, definition.workspace_id, definition.actor_id)
                    checked_definition(db, p, definition.id)
                    request = str(uuid5(UUID(definition.id), definition.next_due.isoformat()))
                    enqueue_sample(db, p, definition, request)
                    definition.next_due = now() + timedelta(seconds=definition.data['interval_seconds']); definition.version += 1
            except HTTPException:
                definition.status = 'blocked'; definition.version += 1
        db.commit()


def deliver_pending(workspace_id=None):
    """Receive original anomaly after its normal outbox ACK; never execute Task."""
    from .db import engine
    from .worker import actor
    from .models import PerceptionSignal
    with Session(engine()) as db:
        query = select(Sample).where(Sample.status == 'received', Sample.data['task_id'].as_string().is_(None))
        if workspace_id: query = query.where(Sample.workspace_id == workspace_id)
        for sample in db.scalars(query.order_by(Sample.created_at).limit(40).with_for_update(skip_locked=True)):
            signal = db.scalar(select(PerceptionSignal).where(PerceptionSignal.workspace_id == sample.workspace_id,
                PerceptionSignal.data['basis']['observation_id'].as_string() == sample.observation_id))
            if signal is None: continue
            try:
                with db.begin_nested():
                    p = actor(db, sample.workspace_id, sample.actor_id)
                    handoff(db, p, sample, signal)
            except HTTPException as exc:
                sample.data = {**sample.data, 'handoff_issue': str(exc.detail)[:400]}
        environments = list(db.scalars(select(Environment).where(Environment.workspace_id == workspace_id))) if workspace_id else list(db.scalars(select(Environment).limit(100)))
        for env in environments:
            try:
                with db.begin_nested(): reconcile_daily(db, Principal(env.actor_id, env.workspace_id, 'viewer', 'simulation-daily'), env.id)
            except HTTPException: pass
        db.commit()
