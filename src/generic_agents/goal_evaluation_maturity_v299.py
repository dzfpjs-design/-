"""A real candidate Goal check precedes an independently fenced publication.

Candidates never replace the mature head. Only this module's transient private
assessment context can read candidate evidence; ordinary readers keep the old
head. A successful original Goal Job queues an outbox without taking Task locks.
The dedicated promoter uses Task -> Goal Job -> promoter Job lock ordering.
No query, SDK, observation, cloud call or Goal acceptance is created here.
"""
import copy
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope as binding_structure_scope
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import goal_evaluation_provider_v288 as provider
from .contracts import digest
from .goals import snapshot
from .models import Audit, Invocation, Job, Memory, Task, uid
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation

KEY = 'goal_evaluation_candidate_v299'
PREPARED_KEY = 'goal_evaluation_prepared_v299'
SCHEMA = 'goal-evaluation-candidate-v299'
PREPARATION = 'goal-evaluation-preparation-v299'
PROMOTION = 'goal-evaluation-promotion-job-v299'
KIND = 'promote_goal_evaluation_v299'
QUEUED = 'goal_evaluation.candidate_queued_v299'
PREPARED = 'goal_evaluation.candidate_prepared_v299'
OUTBOX = 'goal_evaluation.promotion_queued_v299'
MATURED = 'goal_evaluation.matured_v299'
SUPERSEDED = 'goal_evaluation.candidate_superseded_v299'
ERROR = '候选评价、真实目标核对或成熟提升证明无法核对；旧成熟结果保留，未补采、调用模型或验收'
MAX_ROWS = 1024
_WITNESS = object()
_ASSESSMENT = ContextVar('goal_evaluation_candidate_assessment_v299', default=None)
checked = provider.checked


def need(value):
    if not value: raise HTTPException(409, ERROR)


def _seal(value):
    return {**copy.deepcopy(value), 'record_hash': digest(value)}


def _sealed(value, schema):
    need(type(value) is dict and value.get('schema') == schema
        and value.get('record_hash') == digest({key: item for key, item in value.items() if key != 'record_hash'}))
    return value


def _rows(db, p, model, *conditions):
    rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id,
        *conditions).limit(MAX_ROWS + 1).execution_options(populate_existing=True)))
    need(len(rows) <= MAX_ROWS)
    return rows


def _proof(db, p, action, identity, actor, value=None):
    rows = _rows(db, p, Audit, Audit.action == action, Audit.object_id == identity)
    need(len(rows) == 1 and rows[0].actor_id == actor
        and (value is None or digest(rows[0].data) == digest(value)))
    return rows[0]


def _fresh(db, p, model, identity, *, lock=False):
    return provider._fresh(db, p, model, identity, lock=lock)


def _key(task, evaluation_id):
    return 'native-goal:' + task.id + ':' + task.data['native_goal_context_hash'] + ':candidate-v299:' + evaluation_id


def _candidate_key(task, job):
    return type(job.idempotency_key) is str and job.idempotency_key.startswith(
        'native-goal:' + task.id + ':' + task.data['native_goal_context_hash'] + ':candidate-v299:')


def handles(db, p, task, job):
    """Independent queue/key witnesses survive deletion of a JSON marker."""
    return bool(KEY in job.data or _candidate_key(task, job) or _rows(db, p, Audit,
        Audit.action == QUEUED, Audit.object_id == job.id))


def _lease(db, p, job, kind, *, terminal=False):
    from .leases import fenced, ownership
    owner = ownership(db)
    need(fenced(db) and type(owner) is dict and set(owner) == {'job_id', 'lease_token'}
        and owner['job_id'] == job.id and bool(owner['lease_token'])
        and job.workspace_id == p.workspace_id and job.kind == kind and job.data['actor_id'] == p.user_id
        and job.lease_token == owner['lease_token']
        and job.status == ('succeeded' if terminal else 'running'))
    # The final worker hook has staged succeeded/lease_until=None. Never
    # refresh it or flush it before the existing final commit fence runs.
    with db.no_autoflush:
        actual = db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
            Job.kind == kind, Job.status == 'running', Job.lease_token == owner['lease_token'],
            Job.lease_until > func.clock_timestamp(), Job.data['actor_id'].as_string() == p.user_id))
    need(actual == job.id)
    return owner


def _evaluation(db, p, task, row, *, complete):
    from .workflow_goal_evaluation_append_v299 import checked_record
    checked = checked_record(db, p, task, row, producer_complete=complete)
    actual, record = checked['row'], checked['record']
    need(actual.id == row.id and actual.task_id == task.id and actual.workspace_id == p.workspace_id
        and actual.goal_version == task.data['goal_version'] and actual.goal_hash == task.data['goal_hash']
        and record['scope']['native_goal_context_hash'] == task.data['native_goal_context_hash']
        and record['goal_snapshot'] == snapshot(task))
    return actual, record


def _candidate(db, p, task, job):
    provider._goal_job(task, job)
    value = _sealed(job.data[KEY], SCHEMA)
    need(set(value) == {'schema', 'workspace_id', 'task_id', 'actor_id', 'goal_snapshot_hash',
        'context_hash', 'evaluation_ref', 'producer_job_id', 'producer_hash', 'source_binding_hash',
        'goal_check_job_id', 'invocation_key', 'base_head', 'record_hash'}
        and value['workspace_id'] == p.workspace_id == task.workspace_id == job.workspace_id
        and value['task_id'] == task.id and value['actor_id'] == job.data['actor_id']
        and value['goal_snapshot_hash'] == digest(snapshot(task))
        and value['context_hash'] == task.data['native_goal_context_hash'] == job.data['context_hash']
        and value['goal_check_job_id'] == job.id
        and value['invocation_key'] == job.idempotency_key == _key(task, value['evaluation_ref']['id'])
        and job.data[provider.JOB_REF] == value['evaluation_ref']
        and job.data[provider.JOB_PRODUCER] == value['producer_job_id'])
    _proof(db, p, QUEUED, job.id, value['actor_id'], value)
    row = _fresh(db, p, Evaluation, value['evaluation_ref']['id'])
    need(provider._reference(row) == value['evaluation_ref'] and row.job_id == value['producer_job_id']
        and row.workspace_id == p.workspace_id and row.task_id == task.id
        and row.actor_id == value['actor_id'] and row.goal_version == task.data['goal_version']
        and row.goal_hash == task.data['goal_hash'] and row.goal_snapshot_hash == digest(snapshot(task))
        and row.original_query_id == task.data['native_goal_query_id']
        and row.source_binding_hash == value['source_binding_hash']
        and digest(row.data['producer']) == value['producer_hash'])
    return value, row


@checked
def candidate_scope(db, p, task, job):
    """Read-only normal queue scope proof; never producer completion."""
    if not handles(db, p, task, job): return None
    value, _row = _candidate(db, p, task, job)
    return copy.deepcopy(value)


def _proposed(task, job, candidate, row):
    previous = candidate['base_head']
    head = {'schema': provider.HEAD, 'task_id': task.id, 'evaluation_ref': candidate['evaluation_ref'],
        'generation': row.generation, 'query_id': row.evaluation_query_id,
        'goal_snapshot_hash': row.goal_snapshot_hash, 'source_binding_hash': row.source_binding_hash,
        'goal_check_job_id': job.id, 'actor_id': candidate['actor_id'],
        'previous_hash': previous['head_hash'] if previous else None}
    return {**head, 'head_hash': digest(head)}


@checked
def queue_candidate(db, p, task, row, producer_job):
    """Called after the actual new producer's Eval/journal/CREATED flush."""
    from . import native_goals
    from .goal_evaluation_append_contracts_v297 import KIND as producer_kind
    from .goal_evaluation_append_store_v297 import _mature_head
    _lease(db, p, producer_job, producer_kind)
    actual = _fresh(db, p, Task, task.id, lock=True)
    native_goals.guard(db, p, actual, lock_sources=False)
    row, record = _evaluation(db, p, actual, row, complete=False)
    original_call = _fresh(db, p, Invocation, row.invocation_id)
    need(row.job_id == producer_job.id and row.actor_id == p.user_id
        and original_call.data['job_id'] == producer_job.id
        and record['producer']['lease_token'] == original_call.data['lease_token'])
    key = _key(actual, row.id)
    prior = _rows(db, p, Job, Job.idempotency_key == key)
    if prior:
        need(len(prior) == 1)
        candidate, existing = _candidate(db, p, actual, prior[0])
        need(existing.id == row.id and candidate['producer_hash'] == digest(record['producer']))
        return prior[0]
    need(not _rows(db, p, Audit, Audit.action == QUEUED,
        Audit.data['evaluation_ref']['id'].as_string() == row.id))
    selected = provider.binding(db, p, actual); need(selected is not None)
    base = _mature_head(db, p, actual, selected)['head']
    need(row.generation > base['generation'])
    native_goals.queue(db, p, actual, 'candidate-v299:' + row.id,
        **{provider.JOB_REF: provider._reference(row), provider.JOB_PRODUCER: row.job_id})
    jobs = _rows(db, p, Job, Job.idempotency_key == key)
    need(len(jobs) == 1 and jobs[0].status == 'queued' and jobs[0].attempts == 0
        and KEY not in jobs[0].data)
    job = jobs[0]
    candidate = _seal({'schema': SCHEMA, 'workspace_id': p.workspace_id, 'task_id': actual.id,
        'actor_id': p.user_id, 'goal_snapshot_hash': digest(snapshot(actual)),
        'context_hash': actual.data['native_goal_context_hash'], 'evaluation_ref': provider._reference(row),
        'producer_job_id': row.job_id, 'producer_hash': digest(record['producer']),
        'source_binding_hash': row.source_binding_hash, 'goal_check_job_id': job.id,
        'invocation_key': key, 'base_head': copy.deepcopy(base)})
    job.data = {**job.data, KEY: candidate}
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=QUEUED,
        object_id=job.id, data=copy.deepcopy(candidate)))
    db.flush()
    return job


@dataclass(frozen=True, slots=True)
class _Assessment:
    witness: object
    db: object
    workspace_id: str
    actor_id: str
    task_id: str
    job_id: str
    lease_token: str
    context_hash: str
    evaluation_ref: dict


@checked
def assessment_evidence(db, p, task, *, lock_sources=False):
    """Only a transient server-owned process_candidate context can opt in."""
    value = _ASSESSMENT.get()
    if value is None: return None
    need(lock_sources is False and type(value) is _Assessment and value.witness is _WITNESS and value.db is db
        and value.workspace_id == p.workspace_id == task.workspace_id and value.actor_id == p.user_id
        and value.task_id == task.id and value.context_hash == task.data['native_goal_context_hash'])
    job = _fresh(db, p, Job, value.job_id)
    owner = _lease(db, p, job, provider.KIND)
    need(owner['lease_token'] == value.lease_token)
    candidate, row = _candidate(db, p, task, job)
    need(candidate['evaluation_ref'] == value.evaluation_ref)
    row, record = _evaluation(db, p, task, row, complete=True)
    return provider._evidence(row, record, db.scalar(select(func.clock_timestamp())))


def _prepared(db, p, task, job):
    candidate, row = _candidate(db, p, task, job)
    claim = job.data[PREPARED_KEY]
    event = _proof(db, p, PREPARED, job.id, candidate['actor_id'])
    value = _sealed(event.data, PREPARATION)
    need(digest(claim) == digest({'schema': PREPARATION, 'audit_id': event.id,
        'record_hash': value['record_hash'], 'memory_id': value['memory_id'], 'report_hash': value['report_hash']})
        and value['candidate_hash'] == candidate['record_hash'] and value['goal_check_job_id'] == job.id
        and value['workspace_id'] == p.workspace_id and value['task_id'] == task.id
        and value['evaluation_ref'] == candidate['evaluation_ref']
        and value['producer_job_id'] == row.job_id and value['producer_hash'] == candidate['producer_hash']
        and value['goal_snapshot_hash'] == digest(snapshot(task))
        and value['proposed_head'] == _proposed(task, job, candidate, row)
        and job.data['goal_check_id'] == value['memory_id'])
    memory = _fresh(db, p, Memory, value['memory_id']); report = memory.data['report']
    need(memory.status == 'candidate' and memory.data['type'] == 'native_goal_check'
        and memory.data['task_id'] == task.id and memory.data['report_hash'] == digest(report) == value['report_hash']
        and report['goal_snapshot'] == snapshot(task) and report[provider.JOB_REF] == candidate['evaluation_ref']
        and report['goal_evaluation_head_hash_v288'] == value['proposed_head']['head_hash']
        and report['evidence'][provider.VIEW]['evaluation_ref'] == candidate['evaluation_ref']
        and report['cloud_calls'] == report['new_model_inferences'] == 0
        and report['device_actions_performed'] is False)
    return candidate, row, value, event, memory


def _auto_producer_authority(db,p,task,row):
    """Keep revoked automatic evaluations from preparing or publishing a head."""
    from .auto_evaluation_trigger_v356 import authority
    producer=_fresh(db,p,Job,row.job_id)
    need(producer.kind=='append_goal_evaluation_v297' and producer.data.get('task_id')==task.id)
    origin=authority(db,p,task,producer)
    if origin is not None:
        from .goal_evaluation_append_query_v299 import checked_frozen
        from .auto_evaluation_evidence_gate_v357 import verify
        frozen=checked_frozen(db,p,task,row.evaluation_query_id,evaluation_id=row.invocation_id)
        verify(db,p,task,producer,origin,row,frozen)
    return origin


@checked
@binding_structure_scope()
def process_candidate(db, p, task, job):
    if not handles(db, p, task, job): return False
    from . import native_goals
    from .relations import lock_current_actor
    task = _fresh(db, p, Task, task.id, lock=True)
    _lease(db, p, job, provider.KIND); lock_current_actor(db, p)
    native_goals.guard(db, p, task, lock_sources=False)
    candidate, row = _candidate(db, p, task, job)
    need(candidate['actor_id'] == p.user_id)
    row, record = _evaluation(db, p, task, row, complete=True)
    auto_origin=_auto_producer_authority(db,p,task,row)
    if auto_origin is not None:
        from .auto_evaluation_recovery_v360 import require_unfenced, rollout_enabled
        if rollout_enabled():
            require_unfenced(db,workspace_id=p.workspace_id,task_id=task.id,job_id=row.job_id)
    prepared = _rows(db, p, Audit, Audit.action == PREPARED, Audit.object_id == job.id)
    if prepared or PREPARED_KEY in job.data or job.data.get('goal_check_id'):
        _prepared(db, p, task, job)
        return True
    proposed = _proposed(task, job, candidate, row)
    context = _Assessment(_WITNESS, db, p.workspace_id, p.user_id, task.id, job.id,
        job.lease_token, task.data['native_goal_context_hash'], copy.deepcopy(candidate['evaluation_ref']))
    token = _ASSESSMENT.set(context)
    try:
        evidence, result = native_goals.assessment(db, p, task, lock_sources=False)
    finally:
        _ASSESSMENT.reset(token)
    need(evidence[provider.VIEW]['evaluation_ref'] == candidate['evaluation_ref'])
    if auto_origin is not None:
        from .auto_evaluation_evidence_gate_v357 import verify_assessment
        verify_assessment(auto_origin,row,evidence)
    report = {'schema': 'native-goal-check-v1',
        'checked_at': db.scalar(select(func.clock_timestamp())).isoformat(), 'goal_snapshot': snapshot(task),
        'evidence': copy.deepcopy(evidence), 'evaluation': copy.deepcopy(result), 'gaps': [],
        provider.JOB_REF: candidate['evaluation_ref'], 'goal_evaluation_head_hash_v288': proposed['head_hash'],
        'cloud_calls': 0, 'new_model_inferences': 0, 'device_actions_performed': False,
        'device_execution_status': 'requires_actual_receipt'}
    memory = Memory(id=uid(), workspace_id=p.workspace_id, status='candidate',
        text='候选资料评价的原Goal独立核对；成熟结果尚未提升',
        data={'type': 'native_goal_check', 'task_id': task.id, 'report': report, 'report_hash': digest(report)})
    db.add(memory); db.flush()
    value = _seal({'schema': PREPARATION, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'candidate_hash': candidate['record_hash'], 'goal_check_job_id': job.id,
        'evaluation_ref': candidate['evaluation_ref'], 'producer_job_id': row.job_id,
        'producer_hash': candidate['producer_hash'], 'goal_snapshot_hash': digest(snapshot(task)),
        'memory_id': memory.id, 'report_hash': digest(report), 'proposed_head': proposed,
        'assessment_lease_hash': digest(job.lease_token), 'assessment_attempt': job.attempts})
    event = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=PREPARED, object_id=job.id, data=value)
    db.add(event)
    job.data = {**job.data, 'goal_check_id': memory.id, PREPARED_KEY: {'schema': PREPARATION,
        'audit_id': event.id, 'record_hash': value['record_hash'], 'memory_id': memory.id,
        'report_hash': value['report_hash']}}
    db.flush()
    return True


def _outbox_value(task, job, value, event, promoter_id):
    return _seal({'schema': PROMOTION, 'workspace_id': task.workspace_id, 'task_id': task.id,
        'actor_id': job.data['actor_id'], 'goal_check_job_id': job.id, 'evaluation_ref': value['evaluation_ref'],
        'prepared_audit_id': event.id, 'prepared_hash': value['record_hash'],
        'memory_id': value['memory_id'], 'report_hash': value['report_hash'],
        'promoter_job_id': promoter_id, 'invocation_key': 'goal-evaluation-maturity:' + job.id})


@checked
def enqueue_promotion_after_success(db, p, job):
    """Final-success outbox hook. No Task read lock, refresh or flush here."""
    if job.kind != provider.KIND: return False
    with db.no_autoflush:
        queued = _rows(db, p, Audit, Audit.action == QUEUED, Audit.object_id == job.id)
        if KEY not in job.data and not queued and ':candidate-v299:' not in job.idempotency_key: return False
        _lease(db, p, job, provider.KIND, terminal=True)
        task = _fresh(db, p, Task, job.data['task_id'])
        _candidate(db, p, task, job)
        _candidate_value, _row, value, event, _memory = _prepared(db, p, task, job)
        need(not _rows(db, p, Job, Job.idempotency_key == 'goal-evaluation-maturity:' + job.id)
            and not _rows(db, p, Audit, Audit.action == OUTBOX, Audit.object_id == job.id))
        promoter_id = uid(); payload = _outbox_value(task, job, value, event, promoter_id)
        db.add(Job(id=promoter_id, workspace_id=p.workspace_id, kind=KIND, status='queued',
            idempotency_key=payload['invocation_key'], data=payload))
        db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=OUTBOX,
            object_id=job.id, data=copy.deepcopy(payload)))
    return True


def _outbox(db, p, task, goal_job, promoter):
    event = _proof(db, p, OUTBOX, goal_job.id, goal_job.data['actor_id'])
    value = _sealed(event.data, PROMOTION)
    # Ordinary worker finalization adds finished_at/error/diagnostic fields;
    # the queued authority payload must remain exactly its original value.
    need(all(key in promoter.data for key in value)
        and digest({key: promoter.data[key] for key in value}) == digest(value)
        and promoter.id == value['promoter_job_id']
        and promoter.kind == KIND and promoter.workspace_id == p.workspace_id
        and promoter.idempotency_key == value['invocation_key'] == 'goal-evaluation-maturity:' + goal_job.id
        and value['workspace_id'] == p.workspace_id == task.workspace_id and value['task_id'] == task.id
        and value['goal_check_job_id'] == goal_job.id and value['actor_id'] == goal_job.data['actor_id'])
    return value, event


def _mature_value(task, goal_job, promoter, candidate, prepared, prepared_event, outbox, previous):
    return _seal({'schema': 'goal-evaluation-matured-v299', 'workspace_id': task.workspace_id,
        'task_id': task.id, 'actor_id': goal_job.data['actor_id'],
        'evaluation_ref': candidate['evaluation_ref'], 'producer_job_id': candidate['producer_job_id'],
        'producer_hash': candidate['producer_hash'], 'goal_check_job_id': goal_job.id,
        'promoter_job_id': promoter.id, 'candidate_hash': candidate['record_hash'],
        'prepared_audit_id': prepared_event.id, 'prepared_hash': prepared['record_hash'],
        'memory_id': prepared['memory_id'], 'report_hash': prepared['report_hash'],
        'outbox_hash': outbox['record_hash'], 'before_head_hash': previous,
        'after_head': copy.deepcopy(prepared['proposed_head'])})


@checked
@binding_structure_scope()
def process_promotion(db, p, job):
    """Normal leased promoter; no SDK and no fresh Goal assessment."""
    from . import native_goals
    from .relations import lock_current_actor
    need(job.kind == KIND and job.data.get('schema') == PROMOTION)
    task = _fresh(db, p, Task, job.data['task_id'], lock=True)
    goal_job = _fresh(db, p, Job, job.data['goal_check_job_id'], lock=True)
    job = _fresh(db, p, Job, job.id, lock=True)
    _lease(db, p, job, KIND); lock_current_actor(db, p)
    native_goals.guard(db, p, task, allow_expired=True, lock_sources=False)
    need(goal_job.status == 'succeeded' and goal_job.lease_until is None
        and goal_job.data['actor_id'] == p.user_id)
    candidate, row, prepared, event, memory = _prepared(db, p, task, goal_job)
    auto_origin=_auto_producer_authority(db,p,task,row)
    if auto_origin is not None:
        from .auto_evaluation_recovery_v360 import require_unfenced, rollout_enabled
        if rollout_enabled():
            require_unfenced(db,workspace_id=p.workspace_id,task_id=task.id,job_id=row.job_id)
    row,record=_evaluation(db, p, task, row, complete=True)
    if auto_origin is not None:
        from .auto_evaluation_evidence_gate_v357 import verify_assessment
        verify_assessment(auto_origin,row,memory.data['report']['evidence'])
        verify_assessment(auto_origin,row,provider._evidence(row,record,db.scalar(select(func.clock_timestamp()))))
    outbox, _event = _outbox(db, p, task, goal_job, job)
    need(outbox['prepared_audit_id'] == event.id and outbox['prepared_hash'] == prepared['record_hash']
        and outbox['evaluation_ref'] == candidate['evaluation_ref']
        and outbox['memory_id'] == memory.id and outbox['report_hash'] == prepared['report_hash'])
    current = provider._head(db, p, task)
    before = current['head_hash'] if current else None
    expected = candidate['base_head']['head_hash'] if candidate['base_head'] else None
    need(not _rows(db, p, Audit, Audit.action.in_([MATURED, SUPERSEDED]),
        Audit.data['goal_check_job_id'].as_string() == goal_job.id))
    value = _mature_value(task, goal_job, job, candidate, prepared, event, outbox, before)
    if before != expected:
        db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=SUPERSEDED,
            object_id=task.id, data=value))
        return 'succeeded'
    need(current is None or current['generation'] < row.generation)
    if auto_origin is not None:
        from .auto_evaluation_evidence_gate_v357 import install_commit_freshness, verify_assessment
        verify_assessment(auto_origin,row,provider._evidence(row,record,db.scalar(select(func.clock_timestamp()))))
        install_commit_freshness(db,auto_origin,row,record)
    task.data = {**task.data, provider.KEY: copy.deepcopy(prepared['proposed_head']),
        'native_goal_check_id': memory.id, 'native_goal_error': None}
    task.version += 1
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=provider.SELECTED,
        object_id=task.id, data=copy.deepcopy(prepared['proposed_head'])))
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=MATURED,
        object_id=task.id, data=value))
    return 'succeeded'


def _publication_receipt(db, p, task, head):
    """Independent publication only; never authenticate the underlying facts."""
    goal_job = _fresh(db, p, Job, head['goal_check_job_id'])
    matured = _rows(db, p, Audit, Audit.action == MATURED, Audit.object_id == task.id,
        Audit.data['goal_check_job_id'].as_string() == goal_job.id)
    if not handles(db, p, task, goal_job) and not matured: return None
    need(goal_job.status == 'succeeded' and goal_job.lease_until is None and len(matured) == 1)
    candidate, row, prepared, event, memory = _prepared(db, p, task, goal_job)
    from .workflow_goal_evaluation_contracts_v299 import RECORD, PRODUCER_FAMILY, record_hash
    record = row.data
    # The independent queued evaluation reference anchors these stored bytes.
    # This is not the producer's source/command/ACK/current-permission proof.
    need(type(record) is dict and record.get('schema') == RECORD
        and record.get('producer_family') == PRODUCER_FAMILY
        and record.get('record_hash') == row.record_hash == record_hash(record))
    promoter = _fresh(db, p, Job, matured[0].data['promoter_job_id'])
    outbox, outbox_event = _outbox(db, p, task, goal_job, promoter)
    need(promoter.status == 'succeeded' and promoter.lease_until is None
        and prepared['proposed_head'] == head and head['evaluation_ref'] == candidate['evaluation_ref']
        and matured[0].actor_id == candidate['actor_id'])
    before = candidate['base_head']['head_hash'] if candidate['base_head'] else None
    expected = _mature_value(task, goal_job, promoter, candidate, prepared, event, outbox, before)
    need(digest(matured[0].data) == digest(expected))
    selected = _rows(db, p, Audit, Audit.action == provider.SELECTED, Audit.object_id == task.id,
        Audit.data['head_hash'].as_string() == head['head_hash'])
    need(len(selected) == 1 and selected[0].actor_id == candidate['actor_id']
        and digest(selected[0].data) == digest(head))
    return {'row': row, 'record': copy.deepcopy(record), 'head': copy.deepcopy(head),
        'candidate': copy.deepcopy(candidate), 'goal_check_job': goal_job, 'memory': memory,
        'report_hash': prepared['report_hash'], 'promoter_job': promoter,
        'prepared': copy.deepcopy(prepared), 'outbox': copy.deepcopy(outbox),
        'prepared_audit': event, 'outbox_audit': outbox_event,
        'matured_audit': matured[0], 'selected_audit': selected[0]}


@checked
def publication_receipt(db, p, task, head):
    """Display a completed V299 publication, without a source or live-evidence grant.

    Callers retain their normal reader/scope authorization. A real historical
    publication can be displayed even when its original facts are now stale;
    acceptance, budgets and reasoning must continue to use the strong readers.
    Missing or inconsistent publication evidence is never an awaiting result.
    """
    result = _publication_receipt(db, p, task, head)
    need(result is not None)
    return result


@checked
@binding_structure_scope()
def verify_head(db, p, task, head):
    """Full cold proof: publication plus the original complete source proof."""
    result = _publication_receipt(db, p, task, head)
    if result is None: return False
    _evaluation(db, p, task, result['row'], complete=True)
    return True


@checked
@binding_structure_scope()
def read_mature_history(db, p, task, *, head_hash, evaluation_ref):
    """Authenticate a completed historical base without selecting current.

    A later head and naturally expired evidence do not erase a real completed
    assessment. This does not return a live-evidence grant or perform _head(),
    and immutable append producers must follow strictly earlier generations.
    """
    from .goal_evaluation_append_store_v297 import _mature_value as checked_maturity
    provider.queries._reader(db, p)
    selected = provider.binding(db, p, task); need(selected is not None)
    events = _rows(db, p, Audit, Audit.action == provider.SELECTED, Audit.object_id == task.id,
        Audit.data['head_hash'].as_string() == head_hash,
        Audit.data['evaluation_ref']['id'].as_string() == evaluation_ref['id'])
    need(len(events) == 1)
    head = copy.deepcopy(events[0].data)
    need(set(head) == {'schema', 'task_id', 'evaluation_ref', 'generation', 'query_id',
        'goal_snapshot_hash', 'source_binding_hash', 'goal_check_job_id', 'actor_id', 'previous_hash', 'head_hash'}
        and head['schema'] == provider.HEAD and head['task_id'] == task.id
        and head['evaluation_ref'] == evaluation_ref and head['head_hash'] == head_hash
        and head_hash == digest({key: value for key, value in head.items() if key != 'head_hash'})
        and events[0].actor_id == head['actor_id'] and head['goal_snapshot_hash'] == digest(snapshot(task))
        and head['source_binding_hash'] == selected['binding']['binding_hash'])
    result = checked_maturity(db, p, task, selected, head)
    verify_head(db, p, task, head)
    return result
