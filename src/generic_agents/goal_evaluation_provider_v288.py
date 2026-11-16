"""Cold Goal evidence for the fixed V288 workflow, without refreshing Q0.

An immutable evaluation is not an execution/acceptance grant. Its real packet
producer, independent receipts and current reader scope remain authoritative.
Only an original Goal-check Job can select a head. Reads never make a query,
capture observations, run a model, select a more favourable cut or accept a Goal.
"""
import copy
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from . import workflow_goal_evaluation_contracts_v288 as contracts
from . import goal_evaluation_query_v288 as queries
from .contracts import digest
from .goals import snapshot
from .models import (Acquisition, Audit, Capability, DataRun, Event, Evidence,
    InstanceRelease, Invocation, Job, Memory, Observation, Task)
from .services import audit
from .temporal import instant
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope as binding_structure_scope

BINDING = 'authored-goal-data-workflow-binding-v288'
KEY = 'current_goal_evaluation_v288'
PIN = 'goal_data_preparation_v288'
VIEW = 'goal_evaluation_provider_v288'
HEAD = 'goal-evaluation-head-v288'
SELECTED = 'native_goal.evaluation_selected_v288'
JOB_REF = 'goal_evaluation_ref_v288'
JOB_PRODUCER = 'goal_evaluation_producer_job_v288'
KIND = 'check_native_business_goal'
ERROR = '原目标的独立评价、生产记录或当前读取范围无法核对；未刷新旧查询、补采或验收'
MAX_HEADS = 1024
need, checked = queries.need, queries.checked


def _id(value):
    need(type(value) is str and str(UUID(value)) == value)
    return value


def _fresh(db, p, model, identity, *, lock=False):
    query = select(model).where(model.workspace_id == p.workspace_id, model.id == _id(identity))
    if lock: query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    need(row is not None)
    return row


def _definition_graph(db, p, task, release, business, value, *, definition):
    """Bind an existing graph, or the actual uncommitted approved-start seam.

    Layered start defines the Goal before initializing the graph in that same
    transaction. Only its server-owned initializing context plus the original
    managed approval/command-chain gate may use the frozen release graph here.
    A saved Task marker, definition=True or an arbitrary db.info dict alone is
    not authority. Ordinary runtime reads always require the actual run hash.
    """
    if 'graph_runtime' in task.data:
        runtime = task.data['graph_runtime']
        need(type(runtime) is dict and runtime.get('graph_hash') == digest(value['graph']))
        return
    need(definition is True and not task.data.get('native_goal_context')
        and task.status == 'workflow_waiting' and db.in_transaction())
    from . import layered_approval_store_v175 as approval
    transition = db.info.get('layered_approval_initializing_v175')
    need(type(transition) is dict and set(transition) == {'managed_id', 'state_hash', 'previous_state_hash'})
    row = approval.managed(db, p, task)
    need(row is not None and row.workspace_id == p.workspace_id == task.workspace_id
        and row.task_id == task.id and row.id == transition['managed_id'] and row.status == 'executing')
    # Reuse the real gate: current approved submission, complete independent
    # command chain, original actor, documents and interim authority must pass.
    approval.approval_starter(db, p, task)
    execution = approval.checked(row)['execution']
    need(execution.get('initialized') is False and execution.get('actor_id') == p.user_id
        and transition['state_hash'] == digest(approval.authority(row)))
    from .workflow_graph import graph_for
    need(graph_for(release, business) == value['graph'])


@checked
def binding(db, p, task, *, definition=False):
    """Cheap literal discriminator followed by actual saved release validation.

    A Task marker only restricts. It cannot install this provider for another
    family, nor can stripping the marker bypass the real release binding.
    """
    context = task.data.get('native_goal_context')
    hint = KEY in task.data or PIN in task.data or type(context) is dict and PIN in context
    if not task.data.get('workflow_runtime') or not task.data.get('instance_release_id'):
        need(not hint)
        return None
    release = _fresh(db, p, InstanceRelease, task.data['instance_release_id'])
    need(release.instance_id == task.data['instance_id'] and release.package_id == task.package_id)
    businesses = [item for item in release.data['snapshot']['businesses'] if item['id'] == task.data['business_id']]
    need(len(businesses) == 1)
    business = businesses[0]
    from .workflow_graph import business_binding
    if business.get('execution_mode') != 'confirmed_graph_workflow':
        declarations = (release.data['snapshot'].get('semantic_runtime_plan') or {}).get('graph_adoptions', [])
        need(type(declarations) is list and not hint
            and not any(type(item) is dict and type(item.get('binding')) is dict
                and item['binding'].get('schema') == BINDING
                and item['binding'].get('business_name') == business.get('name') for item in declarations))
        return None
    value = business_binding(release, business)
    if value.get('schema') != BINDING:
        need(not hint)
        return None
    from .workspace_api import saved_execution
    from .authored_goal_data_workflow_store_v288 import check_binding
    queries._reader(db, p)
    actual, saved_release, saved_business = saved_execution(db, p, task.id)
    need(actual.id == task.id and actual.workspace_id == p.workspace_id
        and saved_release.id == release.id and saved_business == business
        and check_binding(value) == value and value['workspace_id'] == p.workspace_id
        and value['business_name'] == business['name'] and value['flow'] == business['flow'])
    _definition_graph(db, p, actual, saved_release, saved_business, value, definition=definition)
    if not definition:
        policy = contracts.check_preparation_policy(actual.data['goal_spec'], value['preparation_policy'])
        expected = {'schema': 'goal-data-preparation-pin-v288', 'source_binding_hash': value['binding_hash'],
            'preparation_policy': policy}
        context = actual.data['native_goal_context']
        need(context.get(PIN) == expected and context['goal_snapshot'] == snapshot(actual)
            and digest(context) == actual.data['native_goal_context_hash'])
    return {'task': actual, 'release': saved_release, 'business': saved_business, 'binding': copy.deepcopy(value)}


@checked
def definition_spec(db, p, task, spec):
    """Default China only before a new V288 Goal is confirmed; old Goals stay."""
    selected = binding(db, p, task, definition=True)
    result = copy.deepcopy(spec)
    if selected is not None:
        unsupported = [name for name in ('relation_query', 'data_query', 'tool_steps') if result.get(name)]
        if unsupported:
            raise HTTPException(422, {'schema': 'goal-data-preparation-unsupported-goal-v288',
                'unsupported_goal_parts': unsupported,
                'reason': '此配方尚未接通目标中的关系、高级查询或外部工具步骤；原完整目标未被缩减或确认'})
        if result['evidence']['time'].get('timezone') is None:
            result['evidence']['time']['timezone'] = 'Asia/Shanghai'
        try: contracts.check_preparation_policy(result, selected['binding']['preparation_policy'])
        except contracts.GoalEvaluationContractError:
            raise HTTPException(422, '本业务冻结的资料准备策略与目标的时间、时效或采集范围不一致；未改写策略或目标') from None
    return result


@checked
def definition_pin(db, p, task):
    selected = binding(db, p, task, definition=True)
    if selected is None: return {}
    return {PIN: {'schema': 'goal-data-preparation-pin-v288',
        'source_binding_hash': selected['binding']['binding_hash'],
        'preparation_policy': contracts.check_preparation_policy(
            task.data['goal_spec'], selected['binding']['preparation_policy'])}}


def _reference(row):
    return {'id': row.id, 'record_hash': row.record_hash}


def _goal_job(task, job):
    """Normal queue/timer identity, not a caller-chosen kind/context marker."""
    need(job.kind == KIND and job.workspace_id == task.workspace_id
        and job.data['task_id'] == task.id and job.data['context_hash'] == task.data['native_goal_context_hash'])
    prefix = 'native-goal:' + task.id + ':' + task.data['native_goal_context_hash'] + ':'
    if type(job.idempotency_key) is str and job.idempotency_key.startswith(prefix) and len(job.idempotency_key) > len(prefix):
        return
    trigger = job.data.get('time_trigger')
    need(type(trigger) is dict and trigger.get('schema') == 'native-goal-time-v1'
        and trigger.get('context_hash') == task.data['native_goal_context_hash']
        and job.data.get('time_trigger_hash') == digest(trigger)
        and job.idempotency_key == 'native-goal-time:' + task.id + ':' + digest(trigger)
        and job.available_at == instant(trigger['due_at']))


def _q0(db, p, task):
    original = _fresh(db, p, DataRun, task.data['native_goal_query_id'])
    need(original.data['native_goal_task_id'] == task.id
        and original.data['goal_delegation'] == {'task_id': task.id, 'goal_snapshot': snapshot(task)}
        and original.data['native_goal_context_hash'] == task.data['native_goal_context_hash']
        and original.data['native_goal_request_hash'] == digest(original.data['request'])
        and original.data['native_goal_result_hash'] == digest(original.data['result']))
    return original


def _lock_passive_originals(db, p, originals):
    """Task is already locked; don't wait on a receiver's original rows."""
    try:
        with db.begin_nested():
            for model, identity in sorted(set(originals), key=lambda item: (item[0].__tablename__, item[1])):
                actual = db.scalar(select(model).where(model.workspace_id == p.workspace_id,
                    model.id == _id(identity)).with_for_update(nowait=True)
                    .execution_options(populate_existing=True))
                need(actual is not None)
    except DBAPIError as error:
        if getattr(error.orig, 'sqlstate', None) != '55P03': raise
        raise HTTPException(409, '相关传感原件正在处理；本次未采用评估，请稍后重试') from None


def _lock_sources(db, p, row):
    """Acceptance pins actual originals before their cold proof is rebuilt."""
    value = row.data
    entries = [(Evaluation, [row.id]), (DataRun, [row.original_query_id, row.evaluation_query_id]),
        (Job, [row.job_id]), (Invocation, [row.invocation_id]), (Evidence, [row.evidence_id]),
        (Acquisition, [ack['acquisition_id'] for ack in value['ack_refs'] if 'acquisition_id' in ack]),
        (Observation, [ack['observation_id'] for ack in value['ack_refs']]),
        (Event, [ack['event_id'] for ack in value['ack_refs']]),
        (Memory, [ack['memory_id'] for ack in value['ack_refs']]),
        (Capability, [value['producer']['capability_id'], *[identity for ack in value['ack_refs']
            for identity in (ack['source_id'], ack['extractor_id'])]])]
    from . import goal_ack_origin_mqtt_v297 as mqtt_origin
    originals = [(model, identity) for model, identities in entries for identity in identities]
    for ack in value['ack_refs']:
        if ack.get('origin_kind') == mqtt_origin.KIND:
            originals.extend(mqtt_origin.lock_rows(ack))
    if any(ack.get('origin_kind') == mqtt_origin.KIND for ack in value['ack_refs']):
        # Receivers may hold an Event/Gateway before notifying this Task.
        # With the Task already locked, never wait for such an original and
        # create an opposite lock cycle. Roll back the whole source-lock set
        # on contention; verification is never retried without the locks.
        _lock_passive_originals(db, p, originals)
    else:
        for model, identities in entries:
            for identity in sorted(set(identities)):
                _fresh(db, p, model, identity, lock=True)


def _record(db, p, task, selected, reference, *, lock_sources=False):
    need(type(reference) is dict and set(reference) == {'id', 'record_hash'})
    row = _fresh(db, p, Evaluation, reference['id'])
    if lock_sources: _lock_sources(db, p, row)
    need(_reference(row) == reference and row.task_id == task.id and row.goal_version == task.data['goal_version']
        and row.original_query_id == task.data['native_goal_query_id']
        and row.instance_id == selected['release'].instance_id and row.release_id == selected['release'].id
        and row.package_id == task.package_id and row.source_binding_hash == selected['binding']['binding_hash']
        and row.graph_hash == digest(selected['binding']['graph']))
    trusted = db.scalar(select(func.clock_timestamp()))
    expected_scope = {'workspace_id': p.workspace_id, 'task_id': task.id,
        'instance_id': selected['release'].instance_id, 'release_id': selected['release'].id,
        'package_id': task.package_id, 'business_id': task.data['business_id'],
        'graph_hash': digest(selected['binding']['graph']), 'source_binding_hash': selected['binding']['binding_hash'],
        'goal_snapshot_hash': digest(snapshot(task)), 'native_goal_context_hash': task.data['native_goal_context_hash']}
    value = contracts.check_record(row.data, expected_scope=expected_scope, goal_snapshot=snapshot(task),
        preparation_policy=selected['binding']['preparation_policy'], trusted_now=trusted.isoformat())
    need(value['scope']['workspace_id'] == p.workspace_id and value['scope']['task_id'] == task.id
        and value['scope']['native_goal_context_hash'] == task.data['native_goal_context_hash']
        and value['scope']['source_binding_hash'] == selected['binding']['binding_hash']
        and row.record_hash == value['record_hash'] and row.id == value['producer']['invocation_id']
        and row.data['original_query_ref'] == queries.query_ref(_q0(db, p, task)))
    job = _fresh(db, p, Job, row.job_id); call = _fresh(db, p, Invocation, row.invocation_id)
    cap = _fresh(db, p, Capability, value['producer']['capability_id'])
    from .workflow_goal_evaluation_packet_v288 import saved_result
    output = saved_result(db, p, task, job, call, cap, job.data['inputs'])
    need(output['evaluation_ref'] == reference and output['packet']['id'] == row.id
        and output['packet']['evidence_id'] == row.evidence_id)
    return row, value, trusted


@binding_structure_scope()
def _head(db, p, task):
    head = task.data.get(KEY)
    if head is None: return None
    need(type(head) is dict and set(head) == {'schema', 'task_id', 'evaluation_ref', 'generation',
        'query_id', 'goal_snapshot_hash', 'source_binding_hash', 'goal_check_job_id', 'actor_id', 'previous_hash', 'head_hash'}
        and head['schema'] == HEAD and head['task_id'] == task.id
        and head['head_hash'] == digest({key: value for key, value in head.items() if key != 'head_hash'})
        and head['goal_snapshot_hash'] == digest(snapshot(task)))
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == SELECTED, Audit.object_id == task.id,
        Audit.data['goal_snapshot_hash'].as_string() == head['goal_snapshot_hash']).limit(MAX_HEADS + 1)))
    need(0 < len(events) <= MAX_HEADS)
    chain = {}
    for event in events:
        value = event.data
        need(type(value) is dict and set(value) == set(head) and value['schema'] == HEAD
            and value['task_id'] == task.id and value['goal_snapshot_hash'] == head['goal_snapshot_hash']
            and value['source_binding_hash'] == head['source_binding_hash']
            and event.actor_id == value['actor_id'] and type(value['generation']) is int and value['generation'] > 0
            and value['head_hash'] == digest({key: item for key, item in value.items() if key != 'head_hash'})
            and value['head_hash'] not in chain)
        chain[value['head_hash']] = value
    parents = {value['previous_hash'] for value in chain.values() if value['previous_hash'] is not None}
    need(parents <= set(chain) and set(chain) - parents == {head['head_hash']}
        and chain[head['head_hash']] == head)
    visited = set(); cursor = head
    while cursor is not None:
        need(cursor['head_hash'] not in visited); visited.add(cursor['head_hash'])
        previous = chain.get(cursor['previous_hash'])
        if previous is not None:
            need(previous['generation'] <= cursor['generation']
                and (previous['generation'] != cursor['generation']
                    or previous['evaluation_ref'] == cursor['evaluation_ref']))
        cursor = previous
    need(len(visited) == len(chain))
    job = _fresh(db, p, Job, head['goal_check_job_id'])
    _goal_job(task, job)
    need(job.kind == KIND and job.data['task_id'] == task.id and job.data['actor_id'] == head['actor_id']
        and job.data['context_hash'] == task.data['native_goal_context_hash']
        and job.data.get(JOB_REF) == head['evaluation_ref'])
    from .goal_evaluation_maturity_v299 import verify_head
    matured=verify_head(db,p,task,head)
    evaluation=_fresh(db,p,Evaluation,head['evaluation_ref']['id'])
    need(evaluation.task_id==task.id and _reference(evaluation)==head['evaluation_ref'])
    if evaluation.data.get('schema')=='workflow-goal-evaluation-record-v299':
        need(evaluation.data.get('producer_family')=='append_command_v297' and matured is True,
            '追加评价尚未取得正式成熟证明；原结果保留')
    else:
        need(evaluation.data.get('schema')==contracts.RECORD)
    return copy.deepcopy(head)


def _metadata(row, value, state):
    return {'schema': 'goal-evaluation-provider-view-v288', 'state': state,
        'evaluation_ref': _reference(row), 'generation': row.generation,
        'query_id': row.evaluation_query_id, 'cut_at': value['cut_at'],
        'source_binding_hash': row.source_binding_hash, 'goal_snapshot_hash': row.goal_snapshot_hash,
        'physical_state_verified': False, 'goal_completion_verified': False}


def _stale(value, trusted):
    """The same rolling-window rule for projection and commit recheck."""
    if value['preparation_policy']['time_basis'] != 'rolling_latest':return False
    age=timedelta(seconds=value['preparation_policy']['max_age_seconds'])
    report=value['evaluation_result']['result']
    records=[*report['evidence'],*report['observation_evidence']]
    return bool(instant(value['cut_at'])+age<trusted or any(
        not item.get('observed_at') or instant(item['observed_at'])+age<trusted
        or item.get('valid_until') and instant(item['valid_until'])<trusted for item in records))


def _evidence(row, value, trusted):
    report = copy.deepcopy(value['evaluation_result']['result'])
    state = 'current'
    if _stale(value,trusted):
        state = 'stale'
        report['stored_evidence_status'] = report['status']; report['status'] = 'insufficient'
    report[VIEW] = _metadata(row, value, state)
    return report


def _waiting(db, p, task):
    # Preserve original facts and limitations as historical display only. This
    # projection cannot satisfy a Goal before a real independent evaluation.
    result = copy.deepcopy(_q0(db, p, task).data['result']['result'])
    result['stored_evidence_status'] = result['status']; result['status'] = 'insufficient'
    result[VIEW] = {'schema': 'goal-evaluation-provider-view-v288', 'state': 'waiting_for_evaluation',
        'evaluation_ref': None, 'physical_state_verified': False, 'goal_completion_verified': False}
    return result


@checked
@binding_structure_scope()
def query_evidence(db, p, task, *, lock_sources=False):
    """None selects an old family; otherwise return only saved evidence."""
    from .goal_evaluation_maturity_v299 import assessment_evidence
    candidate=assessment_evidence(db,p,task,lock_sources=lock_sources)
    if candidate is not None:return candidate
    selected = binding(db, p, task)
    if selected is None: return None
    if not task.data.get('native_goal_query_id'):
        return {'status': 'insufficient', 'evidence': [], 'observation_evidence': [], 'gaps': [],
            'conflicts': [], 'excluded_evidence': [], 'trusted_cases': [], 'learning_decisions': [],
            VIEW: {'schema': 'goal-evaluation-provider-view-v288', 'state': 'waiting_for_initial_query',
                'evaluation_ref': None, 'physical_state_verified': False, 'goal_completion_verified': False}}
    head = _head(db, p, task)
    if head is None: return _waiting(db, p, task)
    row, value, trusted = _record(db, p, task, selected, head['evaluation_ref'], lock_sources=lock_sources)
    need(head['query_id'] == row.evaluation_query_id and head['generation'] == row.generation
        and head['source_binding_hash'] == row.source_binding_hash)
    return _evidence(row, value, trusted)


@checked
@binding_structure_scope()
def require_current(db, p, task, *, expected_ref=None, lock_sources=False):
    """Actual saved Goal-check completion, for analysis and final acceptance."""
    selected = binding(db, p, task)
    if selected is None: return None
    head = _head(db, p, task); need(head is not None, '独立评估尚未由原Goal后台核对完成')
    if expected_ref is not None: need(head['evaluation_ref'] == expected_ref)
    row, value, trusted = _record(db, p, task, selected, head['evaluation_ref'], lock_sources=lock_sources)
    need(head['query_id'] == row.evaluation_query_id and head['generation'] == row.generation
        and head['source_binding_hash'] == row.source_binding_hash)
    job = _fresh(db, p, Job, head['goal_check_job_id'])
    memory = _fresh(db, p, Memory, job.data['goal_check_id'])
    report = memory.data['report']
    need(job.status == 'succeeded' and memory.status == 'candidate' and memory.data['type'] == 'native_goal_check'
        and memory.data['task_id'] == task.id and memory.data['report_hash'] == digest(report)
        and report['goal_snapshot'] == snapshot(task) and report[JOB_REF] == head['evaluation_ref']
        and report['evidence'][VIEW]['evaluation_ref'] == head['evaluation_ref']
        and report['goal_evaluation_head_hash_v288'] == head['head_hash'])
    evidence = _evidence(row, value, trusted)
    need(evidence[VIEW]['state'] == 'current', '原独立评价已超出目标时效；未滑动窗口、重新查询或调用模型')
    return {'evaluation_ref': head['evaluation_ref'], 'goal_check_job_id': job.id,
        'goal_check_id': memory.id, 'report_hash': memory.data['report_hash'], 'evidence': evidence}


@checked
def job_ready(db, p, job):
    """Scheduler check only: a producer still saving consumes no attempts."""
    if job.kind != KIND or JOB_REF not in job.data: return True
    task = _fresh(db, p, Task, job.data['task_id'])
    _goal_job(task, job)
    from .goal_evaluation_maturity_v299 import candidate_scope
    candidate_scope(db,p,task,job)
    selected = binding(db, p, task); need(selected is not None)
    need(job.workspace_id == p.workspace_id and job.data['actor_id'] == p.user_id
        and job.data['context_hash'] == task.data['native_goal_context_hash'])
    row = _fresh(db, p, Evaluation, job.data[JOB_REF]['id'])
    need(_reference(row) == job.data[JOB_REF] and row.task_id == task.id
        and job.data.get(JOB_PRODUCER) == row.job_id)
    producer = _fresh(db, p, Job, row.job_id)
    call = _fresh(db, p, Invocation, row.invocation_id)
    need(producer.data['task_id'] == task.id and call.data['job_id'] == producer.id
        and producer.idempotency_key == call.idempotency_key
        and producer.data['actor_id'] == row.actor_id == row.data['producer']['actor_id']
        and call.capability_id == row.data['producer']['capability_id']
        and call.data['lease_token'] == row.data['producer']['lease_token'])
    if producer.status in {'queued', 'running'}:
        return False
    # A failed/unknown producer is not made successful. The original handler
    # will record its actual cold-proof failure rather than spin forever.
    if producer.status == 'succeeded': _record(db, p, task, selected, job.data[JOB_REF])
    return True


@checked
def process_check(db, p, task, job):
    """True handles V288 post-bootstrap checks; no Q0/query/Gap mutation."""
    selected = binding(db, p, task)
    if selected is None or not task.data.get('native_goal_query_id'): return False
    from .leases import fenced, ownership
    lease = ownership(db)
    need(fenced(db) and lease.get('job_id') == job.id and bool(lease.get('lease_token')))
    actual_job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
        Job.kind == KIND, Job.status == 'running', Job.lease_token == lease['lease_token'],
        Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    need(actual_job is not None and actual_job.data == job.data and actual_job.idempotency_key == job.idempotency_key)
    _goal_job(task, actual_job)
    need(job.kind == KIND and job.workspace_id == p.workspace_id and job.data['task_id'] == task.id
        and job.data['actor_id'] == p.user_id and job.data['context_hash'] == task.data['native_goal_context_hash'])
    previous = _head(db, p, task)
    reference = job.data.get(JOB_REF) or (previous['evaluation_ref'] if previous else None)
    if reference is not None:
        if JOB_REF in job.data:
            need(job_ready(db, p, job), '原评估生产者尚未成功收尾，未提前核对目标')
        row, _value, _trusted = _record(db, p, task, selected, reference)
        if previous and row.generation < previous['generation']:
            job.data = {**job.data, 'goal_evaluation_superseded_v288': True}
            return True
        # Rechecking an unchanged evaluation does not create another query or
        # renew its cut. A new original Goal-check Job may independently prove
        # that same evidence after a failed/interrupted older check.
        prior_check = _fresh(db, p, Job, previous['goal_check_job_id']) if previous else None
        recovery = prior_check is not None and prior_check.id != job.id and prior_check.status in {'failed', 'cancelled'}
        if previous is None or row.generation > previous['generation'] or recovery:
            head = {'schema': HEAD, 'task_id': task.id, 'evaluation_ref': _reference(row),
                'generation': row.generation, 'query_id': row.evaluation_query_id,
                'goal_snapshot_hash': row.goal_snapshot_hash, 'source_binding_hash': row.source_binding_hash,
                'goal_check_job_id': job.id, 'actor_id': p.user_id,
                'previous_hash': previous['head_hash'] if previous else None}
            head['head_hash'] = digest(head)
            task.data = {**task.data, KEY: head}; task.version += 1
            job.data = {**job.data, JOB_REF: _reference(row), JOB_PRODUCER: row.job_id}
            audit(db, p, SELECTED, task.id, copy.deepcopy(head)); db.flush()
        else:
            need(previous['evaluation_ref'] == _reference(row))
    from .native_goals import assessment
    evidence, result = assessment(db, p, task)
    head = _head(db, p, task)
    report = {'schema': 'native-goal-check-v1', 'checked_at': db.scalar(select(func.clock_timestamp())).isoformat(),
        'goal_snapshot': snapshot(task), 'evidence': evidence, 'evaluation': result, 'gaps': [],
        JOB_REF: head['evaluation_ref'] if head else None,
        'goal_evaluation_head_hash_v288': head['head_hash'] if head else None,
        'cloud_calls': 0, 'new_model_inferences': 0, 'device_actions_performed': False,
        'device_execution_status': 'requires_actual_receipt'}
    memory = Memory(workspace_id=p.workspace_id, status='candidate', text='原Goal按独立资料评价核对',
        data={'type': 'native_goal_check', 'task_id': task.id, 'report': report, 'report_hash': digest(report)})
    db.add(memory); db.flush()
    task.data = {**task.data, 'native_goal_check_id': memory.id, 'native_goal_error': None}
    job.data = {**job.data, 'goal_check_id': memory.id}
    from .native_goal_time import arm
    arm(db, p, task, evidence, generation=memory.id)
    return True
