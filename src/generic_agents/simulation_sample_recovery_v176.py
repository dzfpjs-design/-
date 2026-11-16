"""Explicit read-only recovery of one original SIM sample; never dispatch again."""
import copy
from functools import wraps
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field, StrictStr, field_validator
from sqlalchemy import select

from .contracts import Contract, digest
from .models import Audit, Invocation, Job, now, uid
from .services import audit, published, scoped, validate
from .simulation_models_v176 import SimulationSampleV176 as Sample
from .simulation_loop_api_v176 import P, DB, command, view
from . import simulation_loop_v176 as store
from .typed_payload_codec_v131 import canonical_json

router = APIRouter(prefix='/api/workspace/simulation-v176')
KIND = 'recover_simulation_sample_v176'
ENQUEUED = 'simulation.sample.recovery.enqueued_v176'
READ = 'simulation.sample.receipt_read_v176'
CHECKED = 'simulation.sample.recovery.checked_v176'
ERROR = '原取样结果仍未知；保留原请求号，不重新发送设备请求'
_INSTRUCTION = {'schema', 'sample_id', 'actor_id', 'original_sampler_job_id',
    'sample_version', 'source_basis_hash', 'request_id'}


def same(left, right):
    return canonical_json(left) == canonical_json(right)


def _closed(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            store.need(False, '原取样、调用或回执结构无法冷核；未重新发送设备请求')
    return checked


class RecoverBody(Contract):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: StrictStr
    expected_version: int = Field(strict=True, ge=1)

    @field_validator('request_id')
    @classmethod
    def identity(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('请保留原小写UUID请求号')
        return value


def _audit_rows(db, p, action, identity):
    return list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).execution_options(populate_existing=True)))


@_closed
def _source(db, p, sample):
    """Bind the original sampler, immutable definition and exact read inputs."""
    store.need(sample.actor_id == p.user_id, '仅原取样账号可以恢复原请求', 403)
    definition, env, instance, release = store.checked_definition(db, p, sample.definition_id)
    store.need(definition.status == 'enabled')
    d, s = definition.data, sample.data
    inputs = {'device_id': d['device_id'], 'action': 'read_telemetry',
        'expected_revision': None, 'arguments': {}}
    store.need(s.get('schema') == 'simulation-sample-v176'
        and s.get('definition_hash') == digest(d) and s.get('environment_hash') == digest(env.data)
        and same(s.get('inputs'), inputs) and s.get('input_hash') == digest(inputs))
    sampler = scoped(db, Job, s['job_id'], p)
    store.need(sampler.kind == store.KIND and sampler.status == 'succeeded'
        and sampler.idempotency_key == 'simulation-sample:' + sample.id
        and sampler.data.get('sample_id') == sample.id and sampler.data.get('actor_id') == sample.actor_id,
        '原取样任务尚未终止或来源不同，不能安排原号恢复')
    anchors = _audit_rows(db, p, 'simulation.sample.enqueued_v176', sample.id)
    expected = {'definition_id': definition.id, 'request_id': sample.request_id}
    store.need(len(anchors) == 1 and anchors[0].actor_id == sample.actor_id
        and same(anchors[0].data, expected))
    cap = published(db, p, d['gateway_id'], 'tool')
    execution, policy = cap.data['execution'], cap.data['data_policy']
    store.need(cap.content_hash == d['pins'][cap.id]
        and cap.namespace == 'equipment_simulation' and cap.name == 'read_equipment'
        and execution.get('adapter') == 'http' and execution.get('side_effect') is False
        and 'perception' in cap.data['allowed_agent_roles']
        and policy.get('simulation') is True and policy.get('test_only') is True
        and policy.get('cloud_allowed') is False
        and type(execution.get('receipt_endpoint')) is str and bool(execution['receipt_endpoint'])
        and type(execution.get('secret_ref')) is str
        and execution['secret_ref'].startswith('GA_CONNECTOR_SECRET_'),
        '原已发布模拟只读网关未配置同号回执查询，不能更换网关或重新发送')
    validate(cap.data['input_schema'], inputs)
    basis = {'sample_id': sample.id, 'actor_id': sample.actor_id,
        'definition_id': definition.id, 'definition_hash': digest(d),
        'environment_id': env.id, 'environment_hash': digest(env.data),
        'instance_id': instance.id, 'release_id': release.id,
        'original_sampler_job_id': sampler.id, 'original_key': sampler.idempotency_key,
        'input_hash': digest(inputs), 'gateway_id': cap.id, 'gateway_hash': cap.content_hash,
        'enqueue_audit_id': anchors[0].id}
    return definition, sampler, cap, basis


@_closed
def _call(db, p, sample, sampler, cap, basis, *, lock=False):
    statement = select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == basis['original_key'])
    if lock:
        statement = statement.with_for_update()
    rows = list(db.scalars(statement.execution_options(populate_existing=True)))
    store.need(len(rows) <= 1)
    if not rows:
        # Keep even a historical FK unchanged. Missing storage proves neither
        # dispatch nor non-execution and cannot authorize a replacement call.
        return None
    call = rows[0]
    store.need((sample.invocation_id is None or sample.invocation_id == call.id)
        and call.capability_id == cap.id and call.data.get('capability_hash') == cap.content_hash
        and call.data.get('input_hash') == basis['input_hash']
        and call.data.get('role') == 'perception' and call.data.get('side_effect') is False
        and call.data.get('approval') is None and call.data.get('job_id') == sampler.id
        and type(call.data.get('lease_token')) is str and bool(call.data['lease_token']),
        '原调用与已固定取样身份、输入或权限不同；未查询其他请求')
    return call


def _snapshot(call):
    return {key: copy.deepcopy(getattr(call, key)) for key in (
        'id', 'workspace_id', 'capability_id', 'idempotency_key', 'status', 'version', 'data')}


def _worker_interrupted(db, p, call, sampler):
    if call.status != 'failed':
        return False
    recovery = call.data.get('recovery')
    if not (type(recovery) is dict and recovery.get('reason') == 'expired_worker_lease'
        and recovery.get('recovering_job_id') == sampler.id
        and recovery.get('previous_lease_token') == call.data.get('lease_token')):
        return False
    anchors = _audit_rows(db, p, 'worker.invocation_recovered', call.id)
    return (len(anchors) == 1 and anchors[0].actor_id == p.user_id
        and same(anchors[0].data, {'status': 'failed', 'job_id': sampler.id}))


def validate_receipt(receipt, basis, definition, cap):
    """Only explicit original-key success supplies data; absence proves nothing."""
    from .tool_recovery import RECEIPT_SCHEMA
    from .simulation_origin_v176 import sample_values
    canonical_json(receipt)
    validate(RECEIPT_SCHEMA, receipt)
    store.need(receipt['idempotency_key'] == basis['original_key']
        and receipt['input_hash'] == basis['input_hash'], '服务回执不属于原取样请求')
    if receipt['status'] != 'succeeded':
        return None
    validate(cap.data['output_schema'], receipt['output'])
    sample_values(receipt['output'], definition.data['device_id'], definition.data['fields'])
    return copy.deepcopy(receipt['output'])


@_closed
def _instruction(job):
    instruction = job.data.get('instruction')
    store.need(type(instruction) is dict and set(instruction) == _INSTRUCTION
        and instruction['schema'] == 'simulation-sample-recovery-instruction-v176'
        and instruction['sample_id'] == job.data.get('sample_id')
        and instruction['actor_id'] == job.data.get('actor_id')
        and instruction['original_sampler_job_id'] == job.data.get('original_sampler_job_id'))
    return instruction


def _own(db, p, job):
    from .leases import ownership
    lease = ownership(db)
    store.need(job.workspace_id == p.workspace_id and job.kind == KIND and job.status == 'running'
        and type(lease) is dict and lease.get('job_id') == job.id
        and type(lease.get('lease_token')) is str and bool(lease['lease_token'])
        and job.lease_token == lease['lease_token'])
    instruction = _instruction(job)
    store.need(instruction['actor_id'] == p.user_id)
    anchors = _audit_rows(db, p, ENQUEUED, job.id)
    store.need(len(anchors) == 1 and anchors[0].actor_id == p.user_id
        and same(anchors[0].data, {'sample_id': instruction['sample_id'],
            'instruction_hash': digest(instruction), 'job_key': job.idempotency_key}))
    return instruction


def enqueue(db, p, identity, body):
    sample = scoped(db, Sample, identity, p, True)
    store.need(sample.actor_id == p.user_id, '仅原取样账号可以恢复原请求', 403)
    store.need(sample.version == body.expected_version, '取样记录已变化，请核对原版本')
    if sample.observation_id:
        return {**view(sample), 'recovery_status': 'already_received', 'recovery_job_id': None}
    store.need(sample.status == 'awaiting_receipt', '原取样尚未进入待核对状态，不新增恢复任务')
    _, sampler, _, basis = _source(db, p, sample)
    previous = sample.data.get('recovery')
    if previous:
        old = scoped(db, Job, previous['job_id'], p)
        store.need(old.kind == KIND and old.data.get('sample_id') == sample.id
            and old.data.get('actor_id') == sample.actor_id)
        store.need(old.status not in {'queued', 'running'}, '原号恢复仍在进行，请查询原任务，不新增查询')
    identity = uid()
    instruction = {'schema': 'simulation-sample-recovery-instruction-v176',
        'sample_id': sample.id, 'actor_id': p.user_id, 'original_sampler_job_id': sampler.id,
        'sample_version': sample.version + 1, 'source_basis_hash': digest(basis), 'request_id': body.request_id}
    job = Job(id=identity, workspace_id=p.workspace_id, kind=KIND,
        idempotency_key='simulation-sample-recovery:' + sample.id + ':' + body.request_id,
        data={'sample_id': sample.id, 'actor_id': p.user_id,
            'original_sampler_job_id': sampler.id, 'instruction': instruction})
    db.add(job)
    sample.version += 1
    sample.data = {**sample.data, 'recovery': {'schema': 'simulation-sample-recovery-v176',
        'job_id': job.id, 'request_id': body.request_id, 'original_sampler_job_id': sampler.id,
        'source_basis_hash': digest(basis), 'status': 'queued',
        'previous_recovery_job_id': previous['job_id'] if previous else None}}
    audit(db, p, ENQUEUED, job.id, {'sample_id': sample.id,
        'instruction_hash': digest(instruction), 'job_key': job.idempotency_key})
    return {**view(sample), 'recovery_status': 'queued', 'recovery_job_id': job.id,
        'original_sampler_job_id': sampler.id}


@router.post('/samples/{identity}/recover')
async def recover(identity: str, body: RecoverBody, request: Request, p: P, db: DB):
    return await command(request, body, 'sample.' + identity + '.recover', p, db,
        lambda: enqueue(db, p, identity, body))


def _unknown(db, p, sample, job, reason, *, receipt=None, queried=False):
    recovery = {**sample.data['recovery'], 'status': 'unknown', 'reason': reason,
        'checked_at': now().isoformat(), 'query_only': True,
        'receipt_query_performed': queried, 'post_replayed': False}
    if receipt is not None:
        # This is the bounded connector's parsed JSON, not captured native bytes.
        from .evidence import save_immutable
        evidence = save_immutable(db, p, store.json_bytes(receipt), 'application/json',
            {**store.MARK, 'purpose': 'parsed_simulation_pending_get_receipt_v176',
                'native_http_bytes': False, 'sample_id': sample.id,
                'recovery_job_id': job.id, 'name': '原模拟取样待确认回执.json'})
        recovery['parsed_receipt'] = copy.deepcopy(receipt)
        recovery['receipt_hash'] = digest(receipt)
        recovery['receipt_evidence_id'] = evidence.id
        recovery['receipt_sha256'] = evidence.sha256
    sample.data = {**sample.data, 'recovery': recovery, 'issue': ERROR}
    sample.status = 'awaiting_receipt'; sample.version += 1
    audit(db, p, CHECKED, job.id, {'sample_id': sample.id, 'status': 'unknown',
        'recovery_hash': digest(recovery), 'recovery_snapshot': copy.deepcopy(recovery), 'post_replayed': False})
    return {'sample_id': sample.id, 'status': 'unknown', 'reason': reason, 'post_replayed': False}


@_closed
def _restored_proof(db, p, call):
    proof = call.data.get('simulation_receipt_recovery_v176')
    if proof is None:
        return
    store.need(type(proof) is dict and proof.get('schema') == 'simulation-read-receipt-recovery-v176')
    original = proof['original_state']
    store.need(proof['original_state_hash'] == digest(original)
        and proof['receipt_hash'] == digest(proof['receipt'])
        and proof['receipt']['status'] == 'succeeded'
        and proof['receipt']['idempotency_key'] == call.idempotency_key
        and proof['receipt']['input_hash'] == call.data['input_hash']
        and same(proof['receipt']['output'], call.data['output'])
        and original['id'] == call.id and original['workspace_id'] == p.workspace_id
        and original['idempotency_key'] == call.idempotency_key
        and original['capability_id'] == call.capability_id
        and call.version == original['version'] + 1)
    from .evidence import evidence_bytes
    evidence, raw = evidence_bytes(db, p, proof['receipt_evidence_id'])
    store.need(evidence.sha256 == proof['receipt_sha256']
        and raw == store.json_bytes(proof['receipt'])
        and evidence.data.get('purpose') == 'parsed_simulation_get_receipt_v176'
        and evidence.data.get('native_http_bytes') is False)
    anchors = _audit_rows(db, p, READ, call.id)
    expected = {'sample_id': proof['sample_id'], 'recovery_job_id': proof['recovery_job_id'],
        'receipt_evidence_id': evidence.id, 'receipt_sha256': evidence.sha256,
        'receipt_hash': proof['receipt_hash'], 'original_state_hash': proof['original_state_hash'],
        'resulting_call_hash': digest(_snapshot(call)), 'query_only': True, 'post_replayed': False}
    store.need(len(anchors) == 1 and anchors[0].actor_id == p.user_id and same(anchors[0].data, expected))


def _accept(db, p, sample, job, call, receipt, output):
    from .evidence import save_immutable
    original = _snapshot(call)
    evidence = save_immutable(db, p, store.json_bytes(receipt), 'application/json',
        {**store.MARK, 'purpose': 'parsed_simulation_get_receipt_v176', 'native_http_bytes': False,
            'sample_id': sample.id, 'invocation_id': call.id, 'recovery_job_id': job.id,
            'name': '原模拟取样只读回执.json'})
    proof = {'schema': 'simulation-read-receipt-recovery-v176', 'sample_id': sample.id,
        'recovery_job_id': job.id, 'original_state': original, 'original_state_hash': digest(original),
        'receipt': copy.deepcopy(receipt), 'receipt_hash': digest(receipt),
        'receipt_evidence_id': evidence.id, 'receipt_sha256': evidence.sha256,
        'checked_at': now().isoformat(), 'query_only': True, 'post_replayed': False}
    # Every old financial, lease and supplier field remains verbatim. A real
    # supplier failure never reaches here; only explicit original-key success.
    call.data = {**call.data, 'output': copy.deepcopy(output), 'simulation_receipt_recovery_v176': proof}
    call.status = 'succeeded'; call.version += 1
    audit(db, p, READ, call.id, {'sample_id': sample.id, 'recovery_job_id': job.id,
        'receipt_evidence_id': evidence.id, 'receipt_sha256': evidence.sha256,
        'receipt_hash': proof['receipt_hash'], 'original_state_hash': proof['original_state_hash'],
        'resulting_call_hash': digest(_snapshot(call)), 'query_only': True, 'post_replayed': False})


def run(db, p, job):
    """One bounded GET, or consume an already saved success; never a device POST."""
    from .leases import checkpoint
    from .relations import lock_current_actor
    from .simulation_origin_v176 import sample_values
    instruction = _own(db, p, job)
    sample = scoped(db, Sample, instruction['sample_id'], p, True)
    if sample.observation_id:
        return {'sample_id': sample.id, 'status': 'already_received', 'observation_id': sample.observation_id}
    lock_current_actor(db, p)
    store.need(sample.status == 'awaiting_receipt' and sample.version == instruction['sample_version']
        and sample.data.get('recovery', {}).get('job_id') == job.id)
    definition, sampler, cap, basis = _source(db, p, sample)
    store.need(instruction['original_sampler_job_id'] == sampler.id
        and instruction['source_basis_hash'] == digest(basis)
        and sample.data['recovery']['source_basis_hash'] == digest(basis))
    call = _call(db, p, sample, sampler, cap, basis, lock=True)
    if call is None:
        return _unknown(db, p, sample, job, '没有原调用回执，不能确认请求是否已发送；未新建调用')
    queried = False
    if call.status != 'succeeded' or call.data.get('simulation_receipt_recovery_v176') is None:
        if call.status not in {'running', 'unknown', 'succeeded'} and not _worker_interrupted(db, p, call, sampler):
            return _unknown(db, p, sample, job, '原调用不是可核对的未知状态；保留原失败与回执，不覆盖供应商失败')
        original = _snapshot(call); call_id = call.id
        # Release row locks during bounded network read; worker lease remains the
        # fencing authority at every subsequent DB transaction and final commit.
        checkpoint(db)
        try:
            from .connectors import query_receipt
            queried = True
            receipt = query_receipt(copy.deepcopy(cap.data['execution']), basis['original_key'])
            output = validate_receipt(receipt, basis, definition, cap)
            issue = None if output is not None else ERROR
        except Exception:
            receipt = None; output = None; issue = '原号只读回执查询失败、尚不存在或结构不符；未重新发送'
        checkpoint(db)
        _own(db, p, job)
        sample = scoped(db, Sample, sample.id, p, True)
        if sample.observation_id:
            return {'sample_id': sample.id, 'status': 'already_received', 'observation_id': sample.observation_id}
        lock_current_actor(db, p)
        definition, sampler, cap, fresh = _source(db, p, sample)
        store.need(same(fresh, basis) and sample.version == instruction['sample_version']
            and sample.data.get('recovery', {}).get('job_id') == job.id)
        call = _call(db, p, sample, sampler, cap, basis, lock=True)
        store.need(call is not None and call.id == call_id and same(_snapshot(call), original),
            '原调用在查询期间已变化，未覆盖其他恢复结果，请查询原记录')
        if issue:
            return _unknown(db, p, sample, job, issue, receipt=receipt, queried=queried)
        # Revalidate semantic output against current fixed bindings after I/O.
        output = validate_receipt(receipt, fresh, definition, cap)
        store.need(output is not None)
        if original['status'] == 'succeeded':
            store.need(same(original['data'].get('output'), output),
                '原成功输出与服务原号回执不同，保留原结果，不能覆盖或新建观测')
        _accept(db, p, sample, job, call, receipt, output)
    else:
        _restored_proof(db, p, call)
        validate(cap.data['output_schema'], call.data['output'])
        sample_values(call.data['output'], definition.data['device_id'], definition.data['fields'])
    original_sample = {'status': sample.status, 'version': sample.version,
        'invocation_id': sample.invocation_id, 'issue': sample.data.get('issue')}
    sample.invocation_id = call.id
    recovery = {**sample.data['recovery'], 'status': 'verified_succeeded',
        'checked_at': now().isoformat(), 'invocation_id': call.id,
        'output_hash': digest(call.data['output']), 'original_sample_state': original_sample,
        'query_only': True, 'receipt_query_performed': queried, 'post_replayed': False}
    # Finalize all recovery metadata BEFORE run_sample freezes received provenance.
    sample.data = {**{key: value for key, value in sample.data.items() if key != 'issue'}, 'recovery': recovery}
    sample.version += 1
    audit(db, p, CHECKED, job.id, {'sample_id': sample.id, 'status': 'verified_succeeded',
        'recovery_hash': digest(recovery), 'recovery_snapshot': copy.deepcopy(recovery), 'post_replayed': False})
    store.run_sample(db, p, sampler, cached_only=True)
    # No Sample/Invocation source mutation is permitted after the received audit.
    return {'sample_id': sample.id, 'status': 'received', 'observation_id': sample.observation_id,
        'invocation_id': call.id, 'post_replayed': False}
