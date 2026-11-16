"""Explicit local retrieval of received learned candidates, never field evidence.

The existing DataRun/worker/registered keyword pipeline owns execution. Independent
intent, source and outcome Audits bind its server-supplied frame; GET only checks
those saved records. No model, registration, reception or object update occurs here.
"""
import copy
import json
from functools import wraps
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictStr, field_validator
from sqlalchemy import select, func, or_

from .contracts import Contract, DataRequest, digest
from .models import Audit, Capability, DataRun, DomainInstance, Entity, InstanceRelease, Invocation, Job, Memory, Observation, Task, uid
from .services import scoped, require

MARKER = 'native_candidate_query_v139'
SCHEMA = 'native-candidate-query-view-v139'
LIST_SCHEMA = 'native-candidate-query-list-v139'
INTENT = 'native_candidate_query.intent_v139'
SOURCES = 'native_candidate_query.sources_v139'
OUTCOME = 'native_candidate_query.outcome_v139'
MAX_SCAN = 200
MESSAGE = '候选查询的原请求、接收依据或调用无法核对；原记录保留，请查看原业务任务。'
CHANGED = '候选范围或原任务核查情况已变化；原查询保留，请明确发起新查询。'


class Submit(Contract):
    request_id: StrictStr
    release_id: StrictStr
    entity_id: StrictStr
    field: Literal['search_text', 'review_status']
    text: StrictStr = Field(min_length=1, max_length=200)

    @field_validator('request_id', 'release_id', 'entity_id')
    @classmethod
    def identifier(cls, value):
        try:
            if str(UUID(value)) != value:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('请保留固定的小写 UUID 编号')
        return value

    @field_validator('text')
    @classmethod
    def meaningful(cls, value):
        try:
            if not value.strip() or '\x00' in value:
                raise ValueError()
            value.encode('utf-8')
        except (ValueError, UnicodeError):
            raise ValueError('请输入有效的查询文字，原输入不会被自动修改')
        return value


def _need(value):
    if not value:
        raise HTTPException(409, MESSAGE)


def _same(left, right):
    return digest(left) == digest(right)


def _safe(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, KeyError, TypeError, OSError, RecursionError):
            raise HTTPException(409, MESSAGE)
    return checked


def _one_audit(db, p, action, identity):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).limit(2)))
    _need(len(rows) == 1)
    return rows[0]


def has_trace(db, p, identity):
    row = db.scalar(select(DataRun).where(DataRun.workspace_id == p.workspace_id, DataRun.id == identity))
    if row is not None and MARKER in row.data:
        return True
    if db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == identity, Audit.action.in_([INTENT, SOURCES, OUTCOME])).limit(1)):
        return True
    return db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == 'data_query',
        Job.data['query_id'].as_string() == identity,
        Job.idempotency_key.startswith('native-candidate-query-v139:')).limit(1)) is not None


def _release(db, p, instance_id, identity):
    from .instance_data_receipts import verify_release
    instance = scoped(db, DomainInstance, instance_id, p)
    release = scoped(db, InstanceRelease, identity, p)
    _need(release.instance_id == instance.id and release.package_id is not None)
    verify_release(db, p, release)
    package = scoped(db, Capability, release.package_id, p)
    _need(package.kind == 'domain_package' and package.namespace == instance.namespace
        and package.content_hash == package.validated_hash == digest(package.data)
        and digest(release.data['snapshot']) == release.configuration_hash
        and package.data['specification']['configuration_hash'] == release.configuration_hash)
    return instance, release, package


def _keyword(db, p, package, *, live):
    if live:
        from .native_query_catalog import catalog
        values = [cap for cap in catalog(db, p, package) if cap.data['specification']['method'] == 'keyword']
        _need(len(values) == 1)
        cap = values[0]
    else:
        fixed = package.data['specification']['native_query_catalog']
        _need(type(fixed) is dict and fixed.get('schema') == 'native-query-catalog-v1'
            and type(fixed.get('tools')) is list)
        pins = [pin for pin in fixed['tools'] if pin.get('method') == 'keyword']
        _need(len(pins) == 1)
        pin = pins[0]; cap = scoped(db, Capability, pin['id'], p)
        _need(cap.content_hash == pin['hash'] and cap.release_version == pin['version'])
    from .data_tools import validate_manifest
    validate_manifest(cap.data)
    spec, execution = package.data['specification'], cap.data['execution']
    _need(cap.id in package.data['dependencies'] and cap.id in spec['data_tool_ids']
        and cap.kind == 'tool' and cap.content_hash == cap.validated_hash == digest(cap.data)
        and cap.namespace == 'platform_native_queries' and cap.name == 'native_data_keyword'
        and cap.data['specification']['method'] == 'keyword'
        and execution['adapter'] == 'builtin' and execution['handler'] == 'data_keyword_filter'
        and execution['query_tool'] is True and execution['side_effect'] is False
        and execution['input_bindings'] == {'records': 'data_records'}
        and cap.data['allowed_agent_roles'] == ['data']
        and cap.data['data_policy'].get('cloud_allowed', False) is False
        and cap.data['data_policy'].get('external_allowed') is False)
    return cap


def _current(db, p, instance_id, body):
    from .relations import lock_current_actor
    from .services import published
    from .entity_release_compat import require_entity
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    db.expire_all()
    instance, release, package = _release(db, p, instance_id, body['release_id'])
    _need(instance.status == 'partially_active' and instance.active_release_id == release.id)
    published(db, p, package.id, 'domain_package')
    entity = scoped(db, Entity, body['entity_id'], p)
    require_entity(db, p, instance, release, entity, lock_sources=False)
    cap = _keyword(db, p, package, live=True)
    from .data_tools import choices
    _need(cap.id in {value.id for value in choices(db, p, [entity.id])})
    return instance, release, entity, cap


def _review_status(state):
    if state == 'confirmed':
        return '已核查'
    if state == 'draft_saved':
        return '已有草稿'
    return '待核查'


def _source_snapshot(db, p, instance_id, entity_id):
    """Bounded independent inventories: damaged new-family rows cannot disappear.

    Scan native rows plus original V138 issue Audits and received Memory links.
    Pending delivery is not a received candidate; any claimed reception must pass
    its full chain. The limit blocks the whole query, never yields a partial total.
    """
    from . import native_learned_geo_pair_provenance_v138 as origin
    from .instance_data_receipts import observation_receipt
    native = select(Observation.id).where(Observation.workspace_id == p.workspace_id,
        Observation.entity_id == entity_id,
        Observation.data['native_instance_id'].as_string() == instance_id)
    tasks = select(Task.id).where(Task.workspace_id == p.workspace_id,
        Task.data['instance_id'].as_string() == instance_id)
    sources = select(Capability.id).where(Capability.workspace_id == p.workspace_id,
        Capability.data['specification']['managed_instance'].as_string() == instance_id)
    issued = select(Audit.object_id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == origin.ISSUED_ACTION, Audit.data['entity_id'].as_string() == entity_id,
        or_(Audit.data['task_id'].as_string().in_(tasks), Audit.data['source_id'].as_string().in_(sources)))
    memories = select(Memory.data['observation_id'].as_string()).where(Memory.workspace_id == p.workspace_id,
        Memory.data['type'].as_string() == 'observation_evidence',
        Memory.data['data_reception']['schema'].as_string() == origin.RECEPTION_SCHEMA,
        Memory.data['data_reception']['instance_id'].as_string() == instance_id,
        Memory.data['data_reception']['entity_id'].as_string() == entity_id)
    identifiers = list(db.scalars(native.union(issued, memories).limit(MAX_SCAN + 1)))
    if len(identifiers) > MAX_SCAN:
        raise HTTPException(409, '所选对象的原生归集记录超过本次完整扫描上限，未执行部分检索；原记录保留。')
    records, frames, proofs = [], [], []
    for identity in sorted(identifiers):
        observation = scoped(db, Observation, identity, p)
        if not origin.has_issued(db, p, observation):
            continue  # Other native families and genuine old records are unsupported.
        context = origin.checked_observation(db, p, observation)
        _need(observation.entity_id == entity_id and context['release'].instance_id == instance_id)
        event = context['event']
        memory_rows = list(db.scalars(select(Memory).where(Memory.workspace_id == p.workspace_id,
            Memory.data['type'].as_string() == 'observation_evidence',
            Memory.data['event_id'].as_string() == event.id).limit(2)))
        if event.status != 'received':
            _need('data_reception' not in observation.data and 'data_reception' not in event.data and not memory_rows)
            continue
        reception = observation_receipt(db, p, observation)['data_reception']
        _need(reception['schema'] == origin.RECEPTION_SCHEMA and len(memory_rows) == 1)
        record = origin.record_for_pair(context); task = context['task']
        item = {'observation_id': observation.id, 'entity_id': entity_id, 'task_id': task.id,
            'name': record['name'], 'attributes': copy.deepcopy(context['packet']['attributes']),
            'review_state': record['review_state'], 'source_time_verified': False, 'physical_state_verified': False}
        review = _review_status(item['review_state'])
        text = item['name'] + '\n' + '\n'.join(key + '：' + json.dumps(value, ensure_ascii=False,
            sort_keys=True, separators=(',', ':'), allow_nan=False) for key, value in sorted(item['attributes'].items())) + '\n' + review
        records.append(item)
        frames.append({'observation_id': observation.id, 'entity_id': entity_id, 'version': observation.version,
            'attributes': {'search_text': text, 'review_status': review}})
        proofs.append({'observation_id': observation.id, 'version': observation.version,
            'packet_hash': observation.data['native_packet_hash'], 'event_id': event.id, 'event_hash': digest(event.data),
            'memory_id': memory_rows[0].id, 'memory_hash': digest(memory_rows[0].data),
            'issued_audit_id': context['issued_audit'].id, 'issued_hash': digest(context['issued_audit'].data),
            'reception_hash': digest(reception), 'task_id': task.id, 'review_hash': digest({
                'state': record['review_state'], 'status': task.status,
                'receipts': task.data['workflow_runtime'].get('receipts', []),
                'drafts': task.data['workflow_runtime'].get('drafts', {})})})
    return {'records': records, 'frames': frames, 'proofs': proofs}


def _request(body, cap):
    return DataRequest(question='检索已接收的本地学习变化候选', entity_ids=[body['entity_id']],
        fields=['search_text', 'review_status'], mode='structured',
        tool_steps=[{'id': 'keyword', 'capability_id': cap.id, 'input_from': 'evidence',
            'parameters': {'field': body['field'], 'text': body['text']}}],
        max_tool_calls=1, max_tool_seconds=60, allow_external_tools=False).model_dump()


def _base(snapshot):
    return {'status': 'sufficient' if snapshot['frames'] else 'insufficient',
        'evidence': [], 'observation_evidence': copy.deepcopy(snapshot['frames']),
        'gaps': [], 'conflicts': [], 'trusted_cases': [], 'candidate_only': True, 'not_authoritative': True}


def _job_key(p, instance_id, request_id):
    return 'native-candidate-query-v139:' + digest({'workspace': p.workspace_id,
        'actor': p.user_id, 'instance': instance_id, 'request': request_id})


def _original(db, p, instance_id, request_id):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == INTENT, Audit.actor_id == p.user_id,
        Audit.data['instance_id'].as_string() == instance_id,
        Audit.data['body']['request_id'].as_string() == request_id).limit(2)))
    _need(len(rows) <= 1)
    job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == _job_key(p, instance_id, request_id)))
    if not rows:
        _need(job is None and db.scalar(select(DataRun.id).where(DataRun.workspace_id == p.workspace_id,
            DataRun.owner_id == p.user_id, DataRun.data[MARKER]['instance_id'].as_string() == instance_id,
            DataRun.data[MARKER]['body']['request_id'].as_string() == request_id).limit(1)) is None)
        return None
    _need(job is not None and job.data.get('query_id') == rows[0].object_id)
    return scoped(db, DataRun, rows[0].object_id, p)


def _cold(db, p, row, instance_id):
    _need(row.owner_id == p.user_id and row.workspace_id == p.workspace_id)
    intent, source = _one_audit(db, p, INTENT, row.id), _one_audit(db, p, SOURCES, row.id)
    value = intent.data
    keys = {'schema', 'query_id', 'instance_id', 'actor_id', 'body', 'body_hash', 'request',
        'configuration_hash', 'package_id', 'capability_id', 'capability_hash', 'capability_version',
        'source_audit_id', 'source_hash', 'job_id'}
    _need(type(value) is dict and set(value) == keys and value['schema'] == 'native-candidate-query-intent-v139'
        and value['query_id'] == row.id and value['instance_id'] == instance_id
        and value['actor_id'] == row.owner_id == intent.actor_id == source.actor_id
        and _same(row.data.get(MARKER), value) and row.data.get(MARKER + '_hash') == digest(value)
        and not any(key in row.data for key in ('goal_delegation', 'native_goal_task_id', 'native_task_query_contract', 'native_query_contract')))
    body = Submit.model_validate(value['body']).model_dump()
    _need(value['body_hash'] == digest(body) and value['source_audit_id'] == source.id
        and type(source.data) is dict and set(source.data) == {'schema', 'query_id', 'instance_id', 'body_hash', 'snapshot'}
        and source.data['schema'] == 'native-candidate-query-sources-v139'
        and source.data['query_id'] == row.id and source.data['instance_id'] == instance_id
        and source.data['body_hash'] == value['body_hash'] and value['source_hash'] == digest(source.data))
    _, release, package = _release(db, p, instance_id, body['release_id'])
    cap = _keyword(db, p, package, live=False)
    _need(value['configuration_hash'] == release.configuration_hash and value['package_id'] == package.id
        and value['capability_id'] == cap.id and value['capability_hash'] == cap.content_hash
        and value['capability_version'] == cap.release_version
        and _same(value['request'], _request(body, cap)) and _same(row.data['request'], value['request'])
        and row.question == value['request']['question'] and row.data.get('reference_only') is True)
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.data['query_id'].as_string() == row.id).limit(2)))
    _need(len(jobs) == 1); job = jobs[0]
    _need(job.id == value['job_id'] and job.kind == 'data_query'
        and job.idempotency_key == _job_key(p, instance_id, body['request_id'])
        and job.data.get('query_id') == row.id and job.data.get('actor_id') == row.owner_id
        and job.data.get('native_candidate_intent_hash') == digest(value)
        and row.data.get('native_candidate_job_id') == job.id)
    snapshot = source.data['snapshot']
    _need(type(snapshot) is dict and set(snapshot) == {'records', 'frames', 'proofs'}
        and all(type(snapshot[key]) is list for key in snapshot)
        and len(snapshot['records']) == len(snapshot['frames']) == len(snapshot['proofs']) <= MAX_SCAN)
    return value, snapshot, cap, job


def _call(db, p, row, intent, snapshot, cap, job, result):
    from .data_tools import frame, builtin
    _need(type(result) is dict and result.get('status') in {'sufficient', 'insufficient', 'blocked_evidence', 'failed'}
        and _same(result.get('evidence'), []) and _same(result.get('observation_evidence'), snapshot['frames'])
        and result.get('not_authoritative') is True and result.get('candidate_only') is True)
    traces = result.get('tool_trace', [])
    calls = list(db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == 'data-tool:' + row.id + ':keyword').limit(2)))
    _need(len(calls) <= 1)
    if not snapshot['frames']:
        _need(not traces and not calls and result['status'] == 'insufficient')
        return None
    _need(len(traces) == len(calls) == 1)
    trace, call = traces[0], calls[0]
    expected = {'records': frame(_base(snapshot)), 'field': intent['body']['field'], 'text': intent['body']['text']}
    _need(trace['invocation_id'] == call.id and trace['capability_id'] == call.capability_id == cap.id
        and trace['capability_hash'] == call.data.get('capability_hash') == cap.content_hash
        and trace['release_version'] == cap.release_version and _same(trace['step'], intent['request']['tool_steps'][0])
        and _same(trace['inputs'], expected) and trace['input_hash'] == call.data.get('input_hash') == digest(expected)
        and call.data.get('job_id') == job.id and type(call.data.get('lease_token')) is str and bool(call.data['lease_token'])
        and call.data.get('role') == 'data' and call.data.get('side_effect') is False
        and trace['status'] == call.status and _same(trace.get('output'), call.data.get('output')))
    if call.status == 'succeeded':
        output = builtin('data_keyword_filter', expected)
        _need(_same(output, call.data['output']))
        if result['status'] in {'sufficient', 'insufficient'}:
            _need(_same(result.get('derived_records'), output['records'])
                and result['status'] == ('sufficient' if output['records'] else 'insufficient')
                and result.get('derivation_verification') == 'deterministic_registered_computation')
    else:
        _need(result['status'] == 'failed')
    return call


def _outcome_value(row, intent, snapshot, cap, job, result, call):
    return {'schema': 'native-candidate-query-outcome-v139', 'query_id': row.id,
        'intent_hash': digest(intent), 'source_hash': intent['source_hash'], 'job_id': job.id,
        'capability_hash': cap.content_hash, 'result_hash': digest(result),
        'call': None if call is None else {'id': call.id, 'capability_id': call.capability_id,
            'idempotency_key': call.idempotency_key, 'status': call.status, 'data': copy.deepcopy(call.data)}}


def _view(db, p, instance_id, row):
    intent, snapshot, cap, job = _cold(db, p, row, instance_id)
    records, error, changed = [], None, False
    status = row.status
    if job.status == 'failed' and status in {'queued', 'running'}:
        status = 'failed'
    try:
        db.expire_all()
        current = _source_snapshot(db, p, instance_id, intent['body']['entity_id'])
        changed = not _same(current, snapshot)
        result = row.data.get('result')
        outcomes = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == OUTCOME, Audit.object_id == row.id).limit(2)))
        if result is not None:
            _need(len(outcomes) == 1 and outcomes[0].actor_id == row.owner_id)
            call = _call(db, p, row, intent, snapshot, cap, job, result)
            _need(_same(outcomes[0].data, _outcome_value(row, intent, snapshot, cap, job, result, call))
                and _same(result.get('tool_trace', []), row.data.get('tool_trace', [])))
            if not changed and job.status == 'succeeded' and status == 'sufficient':
                references = {ref for item in result['derived_records'] for ref in item['evidence_refs']}
                records = [copy.deepcopy(record) for record, original in zip(snapshot['records'], snapshot['frames'])
                    if 'observation:' + record['observation_id'] + ':' + str(original['version']) in references]
        else:
            _need(not outcomes and status not in {'sufficient', 'insufficient'})
    except (HTTPException, ValueError, KeyError, TypeError, OSError, RecursionError):
        error = MESSAGE; records = []
    return {'schema': SCHEMA, 'id': row.id, 'version': row.version, 'status': status,
        'request_id': intent['body']['request_id'], 'request': copy.deepcopy(intent['body']), 'job_id': job.id,
        'integrity_error': error, 'source_changed': changed, 'not_authoritative': True,
        'candidate_only': True, 'records': records, 'truncated': False}


def detail(db, p, instance_id, identity):
    scoped(db, DomainInstance, instance_id, p)
    row = scoped(db, DataRun, identity, p)
    if row.owner_id != p.user_id:
        raise HTTPException(404, '当前账号没有这份原候选查询')
    try:
        return _view(db, p, instance_id, row)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        raise HTTPException(409, MESSAGE)


@_safe
def lookup(db, p, instance_id, request_id, expected_hash):
    scoped(db, DomainInstance, instance_id, p)
    row = _original(db, p, instance_id, request_id)
    if row is None:
        return {'found': False, 'query': None}
    intent, _, _, _ = _cold(db, p, row, instance_id)
    if intent['body_hash'] != expected_hash:
        raise HTTPException(409, '原请求编号不能更换完整查询条件')
    return {'found': True, 'query': detail(db, p, instance_id, row.id)}


@_safe
def listing(db, p, instance_id):
    scoped(db, DomainInstance, instance_id, p)
    # Each row is independently cold-checked; history is bounded and not a total.
    audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == INTENT, Audit.actor_id == p.user_id,
        Audit.data['instance_id'].as_string() == instance_id).order_by(Audit.created_at.desc(), Audit.id).limit(50)))
    return {'schema': LIST_SCHEMA, 'queries': [detail(db, p, instance_id, value.object_id) for value in audits]}


@_safe
def submit(db, p, instance_id, body):
    require(p, 'admin', 'operator')
    value = body.model_dump()
    key = int(digest({'scope': 'native-candidate-query-v139', 'workspace': p.workspace_id,
        'actor': p.user_id, 'instance': instance_id, 'request': body.request_id})[:15], 16)
    db.execute(select(func.pg_advisory_xact_lock(key)))
    prior = _original(db, p, instance_id, body.request_id)
    if prior is not None:
        intent, _, _, _ = _cold(db, p, prior, instance_id)
        if not _same(intent['body'], value):
            raise HTTPException(409, '原请求编号不能更换完整查询条件')
        return detail(db, p, instance_id, prior.id)
    _, release, _, cap = _current(db, p, instance_id, value)
    snapshot = _source_snapshot(db, p, instance_id, body.entity_id)
    identity, job_id, source_id = uid(), uid(), uid()
    request = _request(value, cap)
    source = {'schema': 'native-candidate-query-sources-v139', 'query_id': identity,
        'instance_id': instance_id, 'body_hash': digest(value), 'snapshot': snapshot}
    intent = {'schema': 'native-candidate-query-intent-v139', 'query_id': identity,
        'instance_id': instance_id, 'actor_id': p.user_id, 'body': value, 'body_hash': digest(value),
        'request': request, 'configuration_hash': release.configuration_hash, 'package_id': release.package_id,
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'capability_version': cap.release_version,
        'source_audit_id': source_id, 'source_hash': digest(source), 'job_id': job_id}
    row = DataRun(id=identity, workspace_id=p.workspace_id, owner_id=p.user_id,
        question=request['question'], status='queued', data={'request': request, MARKER: intent,
            MARKER + '_hash': digest(intent), 'reference_only': True, 'native_candidate_job_id': job_id})
    db.add(row)
    db.add(Job(id=job_id, workspace_id=p.workspace_id, kind='data_query',
        idempotency_key=_job_key(p, instance_id, body.request_id), data={'query_id': identity,
            'actor_id': p.user_id, 'native_candidate_intent_hash': digest(intent)}))
    db.add(Audit(id=source_id, workspace_id=p.workspace_id, actor_id=p.user_id, action=SOURCES, object_id=identity, data=source))
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=INTENT, object_id=identity, data=intent))
    db.flush()
    return detail(db, p, instance_id, identity)


def execute_agent(db, p, request, identity, *, cached_only=False):
    """Called only for an actual server-held DataRun with this family's traces."""
    row = scoped(db, DataRun, identity, p, True)
    intent_audit = _one_audit(db, p, INTENT, row.id)
    instance_id = intent_audit.data['instance_id']
    intent, snapshot, cap, job = _cold(db, p, row, instance_id)
    from .leases import ownership
    lease = ownership(db)
    _need(p.user_id == row.owner_id and job.status == 'running' and row.status in {'queued', 'running'}
        and lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token
        and _same(request, intent['request']))
    try:
        def fresh():
            _current(db, p, instance_id, intent['body'])
            current = _source_snapshot(db, p, instance_id, intent['body']['entity_id'])
            if not _same(current, snapshot):
                raise HTTPException(409, CHANGED)
            return _base(current)
        base = fresh()
        from .data_tools import execute
        result = execute(db, p, intent['request'], base, intent['request']['tool_steps'], row.id,
            fresh, cached_only=cached_only)
        # A changed/revoked scope after calculation preserves the call but never
        # publishes its values. The data-tools checkpoints retain worker fencing.
        fresh()
        call = _call(db, p, row, intent, snapshot, cap, job, result)
        _need(not list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == OUTCOME, Audit.object_id == row.id).limit(1))))
        db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=OUTCOME, object_id=row.id,
            data=_outcome_value(row, intent, snapshot, cap, job, result, call)))
        return {'status': result['status'], 'result': result}
    except (HTTPException, ValueError, KeyError, TypeError, OSError, RecursionError):
        return {'status': 'blocked_evidence', 'error': MESSAGE}
