"""Explicit local handoff of an already completed sound result.

This bounded Job never invokes a model, device or SDK tool. Original producer
failure, authorization, command, local completion and independent ACK remain
separate immutable facts.
"""
import copy
import hashlib
from datetime import UTC
from typing import Literal
from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import select, text, or_, func
from . import models as m
from .contracts import Contract, digest
from .layered_approval_contract_v175 import original_json
from .relations import lock_current_actor
from .services import scoped, require
from .sound_models import EnvironmentSoundRun

KIND = 'settle_sound_result_v1'
COMMAND = 'sound.result_settlement_requested_v1'
FINISHED = 'sound.result_settlement_finished_v1'
FAILED = 'sound.result_settlement_failed_v1'
PARENT_FAILED = 'sound.parent_handoff_failed_v1'
ERROR = '原声音成果、失败原因、交接申请或独立处理凭据无法核对；未重跑或补造接收'
MAX_BODY = 16384


def need(value, reason=ERROR, code=409):
    if not value:
        raise HTTPException(code, reason)


class Request(Contract):
    schema: Literal['sound-result-settlement-v1']
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    task_version: StrictInt = Field(ge=1)
    run_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    confirm_original_result_handoff: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if type(value) is not dict or value.get('confirm_original_result_handoff') is not True:
            raise ValueError('请明确继续交接已保存成果')
        return value

    @model_validator(mode='after')
    def reason_text(self):
        if not self.reason.strip() or '\x00' in self.reason:
            raise ValueError('请填写有效原因')
        return self


def parse(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BODY, '交接申请为空或超过16KiB', 413)
    try:
        typed, exact, hashed = original_json(raw, Request)
        return typed, exact, hashed
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise HTTPException(422, '交接申请格式不完整，未创建后台任务') from None


def _lock(db, p, key):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest(['sound-settlement-v1', p.workspace_id, key])[:15], 16)})


def freeze_scope(db, p, task):
    """New inference only: freeze the existing independently checked approval."""
    from . import layered_approval_store_v175 as approval
    row = approval.managed(db, p, task)
    if row is None:
        return None
    data = approval.checked(row)
    need(data.get('execution') is not None and data['execution'].get('initialized') is True)
    return {'managed_id': row.id, 'context_hash': data['context_hash'],
        'submission_id': data['current_submission_id'], 'submission_hash': approval.row_hash(db, p, row)}


def verify_saved_scope(db, p, task, value):
    """Historical proof never renews current roles, policy or active state."""
    if value is None:
        return
    from . import layered_approval_store_v175 as approval
    need(type(value) is dict and set(value) == {'managed_id', 'context_hash', 'submission_id', 'submission_hash'})
    row = approval.managed(db, p, task)
    need(row is not None and row.id == value['managed_id'])
    data = approval.checked(row)
    need(data['context_hash'] == value['context_hash'] and value['submission_id'] in data['submission_ids'])
    submission = scoped(db, approval.Submission, value['submission_id'], p)
    frozen = approval.checked(submission)
    need(submission.managed_id == row.id and frozen['task_id'] == task.id
        and frozen['context_hash'] == value['context_hash'] and submission.data['record_hash'] == value['submission_hash'])


def _proof(db, p, action, object_id):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == object_id)))


def _context(db, p, run):
    from .workflow_sound import completed_context
    context = completed_context(db, p, run)
    need('approval_scope' in run.data['request'], '此旧成果没有原审批范围锚，保留原件；不能补签为新的交接授权')
    verify_saved_scope(db, p, context['task'], run.data['request']['approval_scope'])
    context['input_audit'] = scoped(db, m.Audit, run.data['input_audit_id'], p)
    context['result_audit'] = scoped(db, m.Audit, run.data['result_audit_id'], p)
    return context


def _parent_anchor(run, job, completion_audit, error):
    return {'schema': 'sound-parent-handoff-failure-v1', 'run_id': run.id, 'task_id': run.task_id,
        'job_id': job.id, 'job_status': 'failed', 'actor_id': run.owner_id, 'invocation_id': run.invocation_id,
        'request_hash': run.data['request_hash'], 'result_hash': run.data['result_hash'],
        'output_hash': digest(run.data['output']), 'input_audit_id': run.data['input_audit_id'],
        'result_audit_id': run.data['result_audit_id'], 'completion_audit_id': completion_audit.id,
        'approval_scope_hash': digest(run.data['request']['approval_scope']),
        'error': error, 'error_hash': digest(error)}


def record_parent_failure(db, p, job, error):
    """Only the original worker's known terminal-failure transaction may call."""
    from .leases import ownership
    from . import workflow_sound as core
    lease = ownership(db)
    need(job.workspace_id == p.workspace_id and job.status in {'running', 'failed'}
        and lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token
        and job.data.get('actor_id') == p.user_id and type(error) is str and 0 < len(error) <= 1000)
    rows = list(db.scalars(select(EnvironmentSoundRun).where(EnvironmentSoundRun.workspace_id == p.workspace_id,
        EnvironmentSoundRun.job_id == job.id, EnvironmentSoundRun.status == 'succeeded')))
    for run in rows:
        if 'approval_scope' not in run.data['request']:
            continue  # No retrofit onto pre-protocol runs.
        task = scoped(db, m.Task, run.task_id, p); call = scoped(db, m.Invocation, run.invocation_id, p)
        need(run.owner_id == p.user_id and call.status == 'succeeded' and call.data.get('job_id') == job.id
            and call.capability_id == run.capability_id and digest(call.data.get('output')) == digest(run.data['output'])
            and digest(run.data['result']) == run.data['result_hash'])
        core._proof(db, p, core.INPUT_ACTION, run, core.input_anchor(run), run.data['input_audit_id'])
        core._proof(db, p, core.RESULT_ACTION, run, core.result_anchor(run), run.data['result_audit_id'])
        if job.kind == 'execute_workflow_step':
            receipts = [r for r in task.data['workflow_runtime']['receipts'] if r.get('invocation_id') == call.id]
            proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action == 'workflow.ability_completed', m.Audit.object_id == task.id,
                m.Audit.data['invocation'].as_string() == call.id)))
            need(len(receipts) == len(proofs) == 1 and digest(receipts[0]['output']) == digest(run.data['output'])
                and proofs[0].data.get('step') == run.data['step_id'])
        else:
            activation_id = run.data['execution_scope']['activation_id']
            activation = task.data['graph_runtime']['activations'][activation_id]
            proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action == 'workflow.graph.node_completed', m.Audit.object_id == task.id,
                m.Audit.data['activation_id'].as_string() == activation_id,
                m.Audit.data['invocation_id'].as_string() == call.id)))
            need(activation['status'] == 'completed' and digest(activation['output']) == digest(run.data['output']) and len(proofs) == 1)
        need(proofs[0].actor_id == run.owner_id)
        value = _parent_anchor(run, job, proofs[0], error); old = _proof(db, p, PARENT_FAILED, job.id)
        need(len(old) <= 1)
        if old:
            need(old[0].actor_id == run.owner_id and digest(old[0].data) == digest(value))
        else:
            db.add(m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=PARENT_FAILED, object_id=job.id, data=value))
    db.flush()


def _failure(db, p, context):
    run, job = context['run'], context['job']; need(job.status == 'failed' and type(job.data.get('error')) is str)
    proofs = _proof(db, p, PARENT_FAILED, job.id)
    need(len(proofs) == 1 and proofs[0].actor_id == run.owner_id
        and digest(proofs[0].data) == digest(_parent_anchor(run, job, context['completion_audit'], job.data['error'])))
    return proofs[0]


def _live(db, p, run, *, write=False):
    from .workspace_api import execution
    from .instance_api import active
    require(p, 'admin', 'operator')
    need(p.user_id == run.owner_id, '请由仍有原任务执行资格的原处理人员继续交接', 403)
    if write:
        lock_current_actor(db, p)
        located = scoped(db, m.Task, run.task_id, p)
        db.scalar(select(m.DomainInstance).where(m.DomainInstance.workspace_id == p.workspace_id,
            m.DomainInstance.id == located.data['instance_id']).with_for_update(read=True).execution_options(populate_existing=True))
    task, release, business = execution(db, p, run.task_id, write, lock_sources=write)
    context = _context(db, p, run); parent_failure = _failure(db, p, context)
    instance, current_release = active(db, p, task.data['instance_id'])
    need(business['id'] in instance.data.get('enabled_business_ids', [])
        and task.status in {'workflow_waiting', 'workflow_failed', 'workflow_blocked', 'workflow_completed'}
        and not task.data.get('native_goal_cancelled') and not task.data.get('native_goal_timed_out'),
        '原业务已暂停、取消、到期或执行范围改变，未开始新的资料交接')
    from .instance_data_receipts import verify_release
    verify_release(db, p, current_release)
    need(digest(current_release.data['snapshot']) == current_release.configuration_hash)
    need(digest(freeze_scope(db, p, task)) == digest(run.data['request']['approval_scope']),
        '原成果的审批范围已改变，不能借新批准恢复旧成果')
    from . import layered_approval_store_v175 as approval
    managed = approval.managed(db, p, task)
    if managed is not None:
        _, _, spec = approval.current_submission(db, p, managed)
        approval.role_allowed(db, p, spec['executor'], lock_sources=write)
    if 'graph_runtime' in task.data:
        need(task.data['graph_runtime']['cancel_epoch'] == run.data['execution_scope']['cancel_epoch'])
    from . import native_sound_provenance as origin
    origin.declaration(context)
    need(len(task.data['entity_ids']) == 1)
    authorization = {'instance_id': instance.id, 'active_release_id': current_release.id,
        'active_configuration_hash': current_release.configuration_hash, 'business_id': business['id'],
        'entity_id': task.data['entity_ids'][0], 'object_snapshot_hash': digest(task.data['object_snapshots'])}
    return {**context, 'parent_failure_audit': parent_failure, 'authorization': authorization}


def _command_rows(db, p, *, run_id=None, request_id=None, finishing_job=None):
    audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == COMMAND,
        (m.Audit.data['run_id'].as_string() == run_id if run_id is not None else m.Audit.data['body']['request_id'].as_string() == request_id)).limit(101)))
    progress = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([FINISHED, FAILED]),
        (m.Audit.data['run_id'].as_string() == run_id if run_id is not None else m.Audit.data['request_id'].as_string() == request_id)).limit(101)))
    condition = (or_(m.Job.data['sound_settlement_command']['run_id'].as_string() == run_id,
        m.Job.idempotency_key.startswith('sound-settlement:' + run_id + ':', autoescape=True)) if run_id is not None else
        or_(m.Job.data['sound_settlement_command']['body']['request_id'].as_string() == request_id,
            m.Job.idempotency_key.endswith(':' + request_id, autoescape=True)))
    jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.kind == KIND,
        or_(condition, m.Job.id.in_([a.object_id for a in [*audits, *progress]]))).order_by(m.Job.created_at, m.Job.id).limit(101)))
    need(len(jobs) <= 100 and len(jobs) == len(audits) and {j.id for j in jobs} == {a.object_id for a in audits}
        and len(progress) <= len(jobs) and {a.object_id for a in progress} <= {j.id for j in jobs})
    if finishing_job is not None:
        _owned(db, p, finishing_job)
    for job in jobs:
        value, _ = _checked_command(db, p, job, audits)
        terminal = [a for a in progress if a.object_id == job.id]
        need(len(terminal) <= 1)
        if terminal:
            proof = terminal[0]
            expected_action = FINISHED if job.status == 'succeeded' or finishing_job is not None and job.id == finishing_job.id else FAILED
            need((job.status in {'succeeded', 'failed', 'cancelled'} or finishing_job is not None and job.id == finishing_job.id)
                and proof.action == expected_action and proof.actor_id == value['actor_id']
                and proof.data.get('run_id') == value['run_id'] and proof.data.get('request_id') == value['body']['request_id']
                and proof.data.get('command_audit_id') == value['command_audit_id'] and proof.data.get('command_hash') == value['command_hash'])
    return jobs


def _checked_command(db, p, job, audits=None):
    try:
        value = job.data['sound_settlement_command']; body, raw, raw_sha = parse(value['original_raw'].encode())
        expected_hash = digest({k: v for k, v in value.items() if k != 'command_hash'})
        own = _proof(db, p, COMMAND, job.id) if audits is None else [a for a in audits if a.object_id == job.id]
        need(job.kind == KIND and job.workspace_id == p.workspace_id and len(own) == 1
            and own[0].actor_id == value['actor_id'] == job.data.get('actor_id')
            and value['schema'] == 'sound-result-settlement-command-record-v1' and value['job_id'] == job.id
            and value['workspace_id'] == p.workspace_id and value['task_id'] == job.data.get('task_id')
            and value['run_id'] == job.data.get('run_id') and value['created_at'] == job.created_at.astimezone(UTC).isoformat()
            and own[0].id == value['command_audit_id']
            and job.idempotency_key == 'sound-settlement:' + value['run_id'] + ':' + body.request_id
            and raw == value['original_raw'] and raw_sha == value['raw_sha256']
            and digest(body.model_dump(mode='json')) == value['body_hash'] == digest(value['body'])
            and expected_hash == value['command_hash'] and digest(own[0].data) == digest(value))
        return value, own[0]
    except HTTPException as error:
        if error.status_code == 403:
            raise
        need(False)
    except (KeyError, ValueError, TypeError, AttributeError):
        need(False)


def command_result(job):
    value = job.data['sound_settlement_command']
    return {'schema': 'sound-result-settlement-command-v1', 'request_id': value['body']['request_id'],
        'raw_sha256': value['raw_sha256'], 'body_hash': value['body_hash'], 'actor_id': value['actor_id'],
        'task_id': value['task_id'], 'run_id': value['run_id'], 'job_id': job.id,
        'producer_job_id': value['producer_job_id'], 'producer_status': 'failed',
        'command_hash': value['command_hash'], 'created_at': value['created_at'],
        'model_rerun': False, 'device_actions': 0, 'cloud_calls': 0}


def _frozen(context):
    run = context['run']
    return {'task_id': run.task_id, 'run_id': run.id, 'producer_job_id': run.job_id,
        'request_hash': run.data['request_hash'], 'result_hash': run.data['result_hash'],
        'output_hash': digest(run.data['output']), 'sound_snapshot_hash': digest(run.data['sound_snapshot']),
        'installation_proof_hash': digest(run.data['installation_proof']),
        'input_audit_id': context['input_audit'].id, 'result_audit_id': context['result_audit'].id,
        'completion_audit_id': context['completion_audit'].id,
        'parent_failure_audit_id': context['parent_failure_audit'].id,
        'parent_failure_audit_hash': digest(context['parent_failure_audit'].data),
        'approval_scope': copy.deepcopy(run.data['request']['approval_scope'])}


def request(db, p, run_id, raw):
    body, exact, raw_sha = parse(raw)
    # Original receipt recovery precedes current Role/active/CAS checks.
    _lock(db, p, 'nonce:' + body.request_id)
    old = _command_rows(db, p, request_id=body.request_id)
    if old:
        need(len(old) == 1); value, _ = _checked_command(db, p, old[0])
        need(value['actor_id'] == p.user_id and value['run_id'] == run_id and value['original_raw'] == exact,
            '原交接申请不能更换账号、成果或原文')
        return command_result(old[0]), False
    run = scoped(db, EnvironmentSoundRun, run_id, p)
    context = _live(db, p, run, write=True); _lock(db, p, 'run:' + run.id)
    run = scoped(db, EnvironmentSoundRun, run_id, p, True)
    context = _live(db, p, run, write=True)
    need(context['task'].version == body.task_version and run.version == body.run_version,
        '任务或核查版本已变化，原稿保留，请核对当前版本后另拟申请')
    from . import native_sound_provenance as origin
    need(not origin.observations(db, p, run.id) and not list(db.scalars(select(m.Audit.id).where(
        m.Audit.workspace_id == p.workspace_id, m.Audit.action == origin.ISSUED_ACTION,
        m.Audit.data['run_id'].as_string() == run.id))), '原交接已存在，不能再次签发')
    previous = _command_rows(db, p, run_id=run.id)
    need(len(previous) < 100, '原成果的交接记录已达到上限，请核对现有记录，未截断或创建新申请')
    need(all(j.status in {'failed', 'cancelled'} for j in previous), '已有本地交接在处理或已完成，请读取原申请')
    for prior in previous:
        _checked_failure(db, p, prior)
    job = m.Job(workspace_id=p.workspace_id, kind=KIND, idempotency_key='sound-settlement:' + run.id + ':' + body.request_id,
        data={'actor_id': p.user_id, 'task_id': run.task_id, 'run_id': run.id})
    db.add(job); db.flush()
    command_audit_id = m.uid()
    value = {'schema': 'sound-result-settlement-command-record-v1', 'workspace_id': p.workspace_id,
        'job_id': job.id, 'actor_id': p.user_id, 'body': body.model_dump(mode='json'), 'original_raw': exact,
        'raw_sha256': raw_sha, 'body_hash': digest(body.model_dump(mode='json')), 'created_at': job.created_at.astimezone(UTC).isoformat(),
        'command_audit_id': command_audit_id,
        **_frozen(context), 'authorization': context['authorization']}
    value = {**value, 'command_hash': digest(value)}; job.data = {**job.data, 'sound_settlement_command': value}
    db.add(m.Audit(id=command_audit_id, workspace_id=p.workspace_id, actor_id=p.user_id, action=COMMAND, object_id=job.id, data=copy.deepcopy(value)))
    db.flush(); return command_result(job), True


def _owned(db, p, job):
    from .leases import ownership, LeaseLost
    lease = ownership(db)
    actual = db.scalar(select(m.Job).where(m.Job.id == job.id, m.Job.workspace_id == p.workspace_id)
        .execution_options(populate_existing=True))
    if (actual is None or actual.status != 'running' or lease.get('job_id') != job.id
            or not actual.lease_token or lease.get('lease_token') != actual.lease_token
            or db.scalar(select(m.Job.id).where(m.Job.id == job.id, m.Job.workspace_id == p.workspace_id,
                m.Job.lease_token == actual.lease_token, m.Job.lease_until > func.clock_timestamp())) is None):
        raise LeaseLost('原本地交接租约已变化，旧处理未写入')
    need(actual.kind == KIND and actual.data.get('actor_id') == p.user_id)


def process(db, p, job):
    _owned(db, p, job); value, _ = _checked_command(db, p, job)
    run = scoped(db, EnvironmentSoundRun, value['run_id'], p); context = _live(db, p, run, write=True)
    need(digest(_frozen(context)) == digest({k: value[k] for k in _frozen(context)})
        and digest(context['authorization']) == digest(value['authorization']))
    return 'succeeded'  # Final terminal transaction alone may issue the handoff.


def _finished_body(job, value, context, proof_id):
    return {'schema': 'sound-result-settlement-proof-v1', 'audit_id': proof_id, 'workspace_id': job.workspace_id,
        'actor_id': value['actor_id'], 'settlement_job_id': job.id, 'settlement_job_status': 'succeeded',
        'command_hash': value['command_hash'], 'command_audit_id': value['command_audit_id'],
        'request_id': value['body']['request_id'], 'raw_sha256': value['raw_sha256'], 'body_hash': value['body_hash'],
        'producer_status': 'failed', **_frozen(context),
        'authorization': copy.deepcopy(value['authorization'])}


def finalize(db, p, job):
    _owned(db, p, job); process(db, p, job)
    value, _ = _checked_command(db, p, job); run = scoped(db, EnvironmentSoundRun, value['run_id'], p, True)
    context = _live(db, p, run, write=True)
    _lock(db, p, 'run:' + run.id)
    need(not _proof(db, p, FINISHED, job.id) and not _proof(db, p, FAILED, job.id))
    identity = m.uid(); data = _finished_body(job, value, context, identity)
    proof = m.Audit(id=identity, workspace_id=p.workspace_id, actor_id=p.user_id, action=FINISHED,
        object_id=job.id, data={**data, 'settlement_hash': digest(data)})
    db.add(proof); db.flush()
    from .native_sound_receipts import emit_settled
    need(emit_settled(db, p, run, job, proof) == 1)


def record_failure(db, p, job, error):
    _owned(db, p, job); value, _ = _checked_command(db, p, job)
    need(not _proof(db, p, FINISHED, job.id))
    data = {'schema': 'sound-result-settlement-failed-v1', 'job_id': job.id, 'run_id': value['run_id'],
        'request_id': value['body']['request_id'], 'command_audit_id': value['command_audit_id'],
        'command_hash': value['command_hash'], 'actor_id': value['actor_id'], 'error': str(error)[:1000]}
    old = _proof(db, p, FAILED, job.id); need(len(old) <= 1)
    if old:
        need(old[0].actor_id == p.user_id and digest(old[0].data) == digest(data))
    else:
        db.add(m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=FAILED, object_id=job.id, data=data))


def _checked_failure(db, p, job):
    value, _ = _checked_command(db, p, job); proofs = _proof(db, p, FAILED, job.id)
    expected = {'schema': 'sound-result-settlement-failed-v1', 'job_id': job.id, 'run_id': value['run_id'],
        'request_id': value['body']['request_id'], 'command_audit_id': value['command_audit_id'],
        'command_hash': value['command_hash'], 'actor_id': value['actor_id'], 'error': job.data.get('error')}
    need(job.status in {'failed', 'cancelled'} and len(proofs) == 1 and not _proof(db, p, FINISHED, job.id)
        and proofs[0].actor_id == value['actor_id'] and digest(proofs[0].data) == digest(expected))
    return proofs[0]


def checked_settled(db, p, run, *, finishing_job=None):
    context = _context(db, p, run); parent_failure = _failure(db, p, context)
    context = {**context, 'parent_failure_audit': parent_failure}
    jobs = _command_rows(db, p, run_id=run.id, finishing_job=finishing_job)
    good = []
    for job in jobs:
        proofs = _proof(db, p, FINISHED, job.id); need(len(proofs) <= 1)
        if proofs:
            proof = proofs[0]; value, _ = _checked_command(db, p, job)
            if finishing_job is not None and job.id == finishing_job.id:
                _owned(db, p, job)
            else:
                need(job.status == 'succeeded')
            expected = _finished_body(job, value, context, proof.id)
            need(proof.actor_id == value['actor_id'] and not _proof(db, p, FAILED, job.id)
                and digest(proof.data) == digest({**expected, 'settlement_hash': digest(expected)}))
            good.append((job, proof))
        elif job.status in {'failed', 'cancelled'}:
            _checked_failure(db, p, job)
        else:
            need(job.status in {'queued', 'running'})
    need(len(good) == 1)
    return {**context, 'settlement_job': good[0][0], 'settlement_audit': good[0][1]}


def status(db, p, run_id):
    run = scoped(db, EnvironmentSoundRun, run_id, p)
    action = {'can_request': False, 'reason': '当前没有可恢复的原成果交接', 'task_version': None, 'run_version': run.version}
    jobs = _command_rows(db, p, run_id=run.id)
    latest = None if not jobs else {'job_id': jobs[-1].id, 'status': jobs[-1].status,
        'command': command_result(jobs[-1]), 'error': jobs[-1].data.get('error')}
    try:
        context = _live(db, p, run); action['task_version'] = context['task'].version
        from . import native_sound_provenance as origin
        need(len(jobs) < 100 and not origin.observations(db, p, run.id) and all(j.status in {'failed', 'cancelled'} for j in jobs),
            '原交接正在处理或已存在，请读取原申请与独立收件状态')
        for prior in jobs:
            _checked_failure(db, p, prior)
        action.update(can_request=True, reason='可继续交接已保存成果；不会重跑模型、修改旧失败记录或代替独立收件')
    except HTTPException as error:
        action['reason'] = str(error.detail)
    from .native_sound_receipts import receipt_projection
    return {'schema': 'sound-result-settlement-status-v1', 'run_id': run.id, 'task_id': run.task_id,
        'action': action, 'latest': latest, 'reception': receipt_projection(db, p, run)}


def recover(db, p, run_id, request_id, raw_sha, body_hash, expected_task_version, expected_run_version):
    run = scoped(db, EnvironmentSoundRun, run_id, p)
    rows = _command_rows(db, p, request_id=request_id); need(len(rows) <= 1)
    result = None; supersession = None
    if not rows:
        need(run.owner_id == p.user_id, '原交接申请不属于当前账号或成果', 404)
        # No command and no later version is always unknown. Only complete
        # original custody plus a monotonic CAS advance proves an old body can
        # no longer create work. This is read-only, never a cancellation grant.
        context = _context(db, p, run); _failure(db, p, context)
        task_version, run_version = context['task'].version, context['run'].version
        if task_version > expected_task_version or run_version > expected_run_version:
            # Re-read nonce inventory AFTER observing versions. A request that
            # committed before a version-advancing writer must now be found;
            # one still uncommitted cannot pass the held Task/Run CAS later.
            rows = _command_rows(db, p, request_id=request_id); need(len(rows) <= 1)
            if not rows:
                supersession = {'task_version': task_version, 'run_version': run_version}
    if rows:
        value, _ = _checked_command(db, p, rows[0])
        need(value['actor_id'] == p.user_id and value['run_id'] == run_id, '原交接申请不属于当前账号或成果', 404)
        need(value['raw_sha256'] == raw_sha and value['body_hash'] == body_hash
            and value['body']['task_version'] == expected_task_version and value['body']['run_version'] == expected_run_version,
            '原交接申请摘要或原版本不一致')
        result = command_result(rows[0])
    return {'schema': 'sound-result-settlement-recovery-v1', 'run_id': run_id, 'request_id': request_id,
        'raw_sha256': raw_sha, 'body_hash': body_hash, 'found': result is not None, 'result': result,
        'expected_task_version': expected_task_version, 'expected_run_version': expected_run_version,
        'superseded': supersession is not None, 'supersession': supersession,
        'reason': '原任务或核查版本已前进，原申请未保存且旧版本不能再创建交接；可明确保留旧稿后另拟当前版本。' if supersession is not None else None,
        'current': status(db, p, run_id)}
