"""One first SDK invocation for a fixed V288 context-resense activation.

A graph dispatch intent precedes gateway admission. It is not an SDK receipt.
The fixed gateway persists Invocation before its dispatch authorization and before
workflow_service_context_v196.execute. Recovery therefore requires absence of
*all* original SDK/authorization/effect evidence, actual immutable intent Audits,
current source/reader/dependency/approval authority and the original Job locks.
It never clears an old intent, invents an Invocation, grants an external retry or
uses the absence of a response as permission to replay an unknown call.
"""
import copy
from datetime import datetime
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from .contracts import digest
from .models import Acquisition, Audit, Invocation, Job, Observation, ObservationPlan, Plan, now, uid
from .services import audit, published, require

KEY = 'workflow_resense_pre_dispatch_recovery_v290'
SCHEMA = 'workflow-resense-pre-dispatch-recovery-v290'
ACTION = 'workflow.graph.resense_pre_dispatch_recovered_v290'
CONTINUATIONS = KEY + '_continuations'
CONTINUATION = 'workflow-resense-pre-dispatch-continuation-v290'
CONTINUED = 'workflow.graph.resense_pre_dispatch_continued_v290'
INTENTS = KEY + '_intents'
INTENT = 'workflow-resense-pre-sdk-intent-v290'
INTENT_ACTION = 'workflow.graph.resense_pre_sdk_intent_v290'
MAX_CONTINUATIONS = 2
BINDING = 'authored-goal-data-workflow-binding-v288'
ERROR = '原补采的派发意图、零调用证明或当前授权无法完整核对；原记录保留，未重新调用'
TRANSPORT_KEYS = {'job_id', 'invocation_key', 'input_hash', 'phase', 'cancel_epoch', 'attempt', 'reason',
    'dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash'}
IDENTITY = ('workspace_id', 'task_id', 'activation_id', 'actor_id', 'job_id', 'invocation_key', 'attempt',
    'input_hash', 'graph_hash', 'cancel_epoch', 'activation_version', 'capability_id', 'capability_hash',
    'release_id', 'source_binding_hash', 'context_hash', 'reader_job_id', 'reader_invocation_id', 'policy_id', 'gap_id')


def need(ok):
    if not ok: raise HTTPException(409, ERROR)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try: return function(*args, **kwargs)
        except HTTPException: raise
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration,
                UnicodeError, RecursionError, OverflowError):
            need(False)
    return wrapped


def _seal(value):
    return {**copy.deepcopy(value), 'record_hash': digest(value)}


def _sealed(value):
    need(type(value) is dict and value.get('record_hash') == digest({k: v for k, v in value.items() if k != 'record_hash'}))


def _empty(db, statement):
    need(db.scalar(statement.limit(1)) is None)


def _audits(db, p, action, object_id, limit):
    return list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == object_id).limit(limit)))


def _failure(job, item):
    need(job.status in {'failed', 'blocked', 'cancelled'} and job.lease_until is None
        and type(job.data.get('error')) is str and bool(job.data['error'])
        and type(job.data.get('finished_at')) is str)
    finished = datetime.fromisoformat(job.data['finished_at'].replace('Z', '+00:00'))
    need(finished.tzinfo is not None and finished.utcoffset() is not None)
    return {'status': job.status, 'error': job.data['error'], 'finished_at': job.data['finished_at'],
        'job_version': job.version, 'job_attempts': job.attempts, 'transport': copy.deepcopy(item),
        'diagnostic': copy.deepcopy(job.data.get('failure_diagnostic_v289'))}


def _identity(p, task, release, binding, activation, node, job, context):
    return {'workspace_id': p.workspace_id, 'task_id': task.id, 'activation_id': activation['id'],
        'actor_id': p.user_id, 'job_id': job.id, 'invocation_key': job.idempotency_key, 'attempt': 1,
        'input_hash': job.data['input_hash'], 'graph_hash': task.data['graph_runtime']['graph_hash'],
        'cancel_epoch': activation['epoch'], 'activation_version': activation['version'],
        'capability_id': node['execution']['capability_id'], 'capability_hash': node['execution']['capability_hash'],
        'release_id': release.id, 'source_binding_hash': binding['binding_hash'], 'context_hash': digest(context),
        'reader_job_id': context['producer']['job_id'], 'reader_invocation_id': context['producer']['invocation_id'],
        'policy_id': context['policy']['policy_id'], 'gap_id': context['gap']['gap_id']}


def _no_effects(db, p, task, job, activation, context):
    """SELECT-only negative proof, independent of Task's current marker flags."""
    key, identity, gap = job.idempotency_key, activation['id'], context['gap']['gap_id']
    _empty(db, select(Invocation.id).where(Invocation.workspace_id == p.workspace_id,
        or_(Invocation.idempotency_key == key, Invocation.data['job_id'].as_string() == job.id,
            and_(Invocation.data['approval']['task_id'].as_string() == task.id,
                Invocation.data['approval']['activation_id'].as_string() == identity))))
    _empty(db, select(Job.id).where(Job.workspace_id == p.workspace_id, Job.id != job.id,
        Job.data['task_id'].as_string() == task.id,
        Job.data['activation_id'].as_string() == identity))
    _empty(db, select(Plan.id).where(Plan.workspace_id == p.workspace_id, Plan.task_id == task.id))
    acquisition = select(Acquisition.id).where(Acquisition.workspace_id == p.workspace_id,
        or_(Acquisition.data['data_gap_id'].as_string() == gap,
            Acquisition.data['observation_request']['gap_id'].as_string() == gap,
            Acquisition.data['workflow_job_id'].as_string() == job.id))
    _empty(db, acquisition)
    _empty(db, select(ObservationPlan.id).where(ObservationPlan.workspace_id == p.workspace_id,
        ObservationPlan.data['observation_request']['gap_id'].as_string() == gap))
    _empty(db, select(Observation.id).where(Observation.workspace_id == p.workspace_id,
        or_(Observation.data['data_gap_id'].as_string() == gap,
            Observation.data['workflow_job_id'].as_string() == job.id,
            Observation.data['acquisition_id'].as_string().in_(acquisition))))
    _empty(db, select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_(['workflow.graph.dispatch_authorized', 'workflow.graph.node_completed']),
        Audit.object_id == task.id, or_(Audit.data['job_id'].as_string() == job.id,
            Audit.data['activation_id'].as_string() == identity)))
    _empty(db, select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_(['workflow_service.started_v195', 'workflow_service.context_source_v196',
            'workflow.service.receipt_verified']),
        or_(Audit.data['job_id'].as_string() == job.id,
            Audit.data['task_id'].as_string() == task.id,
            Audit.data['request_key_hash'].as_string() == digest(key))))
    # Empty forward pointers cannot erase a surviving exchange, receipt or
    # reverse Audit. Reuse the bounded complete footprint inventory without
    # treating its result as IAM, source authority or permission to dispatch.
    from .workflow_local_transaction_capacity_v201 import _resense_footprint_empty
    try:
        need(_resense_footprint_empty(db, p, task, context))
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        need(False)


def _cold_dependencies(db, p, task, job):
    """Original V264 graph proofs and mature parents, without worker authority.

    Called under the failed original Job→Task API locks. No running status,
    ownership token or lease is asserted here; require_ready remains unchanged
    and is repeated by the actual worker with its real new lease.
    """
    from . import workflow_job_dependencies_v264 as dependencies
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == dependencies.BOUND, Audit.object_id == job.id).limit(2)))
    refs = task.data.get(dependencies.REFS, {})
    need(type(refs) is dict and job.kind == 'execute_full_semantic_graph_node'
        and job.workspace_id == task.workspace_id == p.workspace_id and job.data['actor_id'] == p.user_id)
    if dependencies.KEY not in job.data and not proofs and job.id not in refs:
        need(dependencies.checked_control(db, p, task) is None)
        return
    value = job.data.get(dependencies.KEY)
    child = dependencies._child_pin(job)
    need(type(value) is dict and set(value) == {'schema', 'workspace_id', 'task_id', *child,
            'bound_by', 'control_record_hash', 'compact_enrollment_hash', 'parents', 'record_hash'}
        and value['schema'] == dependencies.SCHEMA and value['workspace_id'] == p.workspace_id
        and value['task_id'] == task.id == job.data['task_id']
        and all(value[key] == item for key, item in child.items())
        and value['record_hash'] == digest({key: item for key, item in value.items() if key != 'record_hash'})
        and refs.get(job.id) == value['record_hash'] and len(proofs) == 1
        and proofs[0].actor_id == value['bound_by'] and proofs[0].data == value
        and value['compact_enrollment_hash'] is None and type(value['control_record_hash']) is str)
    control = dependencies.checked_control(db, p, task, record_hash=value['control_record_hash'])
    need(control is not None)
    parents = value['parents']; ids = [pin['job_id'] for pin in parents]
    need(type(parents) is list and len(parents) <= dependencies.MAX_PARENTS
        and ids == sorted(set(ids)) and job.id not in ids
        and ids == sorted(dependencies.graph_parent_ids(task, control, job.data['activation_id'])))
    for pin in parents:
        need(dependencies.parent_pin(db, p, task, pin['job_id']) == pin)
        need(dependencies._row(db, p, Job, pin['job_id']).status == 'succeeded')


def _basis(db, p, task, release, business, graph, activation, node, job, item, *, running):
    from . import workflow_graph as runtime, workflow_graph_scope as scope
    from . import authored_goal_data_workflow_store_v288 as family
    from . import workflow_service_context_v196 as resense, workflow_task_context_v196 as reader
    from .workflow_abilities import eligible_service
    require(p, 'admin', 'operator')
    state, data = task.data['graph_runtime'], job.data
    need(task.workspace_id == job.workspace_id == p.workspace_id and job.kind == scope.KIND
        and type(job.attempts) is int and 1 <= job.attempts <= 3
        and type(data.get('lease_recoveries', 0)) is int and data.get('lease_recoveries', 0) == 0
        and type(item) is dict and set(item) in (TRANSPORT_KEYS, TRANSPORT_KEYS | {'provenance_wait_hash'})
        and item['dispatch_committed'] is True and item['dispatch_input_hash'] == data['input_hash']
        and type(item['dispatch_ownership']) is dict and set(item['dispatch_ownership']) == {'job_id', 'lease_token'}
        and item['dispatch_ownership']['job_id'] == job.id and type(item['dispatch_ownership']['lease_token']) is str
        and bool(item['dispatch_ownership']['lease_token'])
        and item['phase'] in ({'queued', 'running'} if running else {'blocked'})
        and item['attempt'] == data['attempt'] == 1
        and state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids'] and activation['epoch'] == state['cancel_epoch']
        and node['kind'] == 'ability' and node['id'] == activation['node_id'] == 'resense'
        and data['actor_id'] == node['actor_id'] == p.user_id
        and data['inputs'] == activation['inputs_snapshot']['arguments']
        == runtime.invoke_pure(runtime.graph_api.ability_inputs, graph, state, activation['id'])
        and data['input_hash'] == digest(data['inputs'])
        and data['invocation_key'] == job.idempotency_key == item['invocation_key']
        == 'graph:' + task.id + ':' + activation['id'] + ':1'
        and not {'dispatch_authorization', 'invocation_id', 'invocation_status', 'service_state'} & set(data))
    scope._job_matches(task, job, state, activation, node)
    cap = published(db, p, node['execution']['capability_id'])
    resense.validate_manifest(cap.data)
    eligible_service(cap, 'business', service_actor='execution_service', operation=resense.OP)
    need(cap.kind == 'tool' and cap.content_hash == cap.validated_hash == digest(cap.data)
        == node['execution']['capability_hash'] and cap.release_version == node['execution']['release_version']
        and node['execution']['agent_role'] == 'business' and node['execution']['adapter'] == 'workflow_service'
        and node['execution']['input_schema'] == cap.data['input_schema']
        and node['execution']['output_schema'] == cap.data['output_schema']
        and node['execution']['max_attempts'] == cap.data['budget']['max_attempts'] == 1)
    binding = runtime.business_binding(release, business)
    need(binding.get('schema') == BINDING and family.check_binding(binding) == binding
        and binding['graph'] == graph and binding['capability_pins']['resense'] == node['execution']
        and family.service_delegations(binding)['resense'] == {
            'service_actor': 'execution_service', 'operation': resense.OP, 'agent_role': 'business'})
    package = published(db, p, release.package_id, 'domain_package')
    need(cap.id in package.data['dependencies'])
    supplied_context = resense._inputs(data['inputs'])['context']
    if running:
        context = reader.checked_context(db, p, supplied_context, task=task, dispatch=False)
        from .workflow_job_dependencies_v264 import require_ready
        require_ready(db, p, task, job)
    else:
        context = reader.read_original_context_v290(db, p, supplied_context, task=task)
        _cold_dependencies(db, p, task, job)
    from .workflow_provenance_wait_v197 import _inventory
    need(_inventory(db, task, job)['active_hash'] is None)
    from .layered_approval_store_v175 import guard
    guard(db, p, task, record_actor=False)
    runtime.live(db, p, task, release, business)
    from .workflow_graph_goal_guard_v133 import progress
    progress(db, p, task)
    _no_effects(db, p, task, job, activation, context)
    return _identity(p, task, release, binding, activation, node, job, context)


def _matching_dispatches(db, p, task, job, activation_id):
    return list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'workflow.graph.dispatch',
        or_(Audit.data['job_id'].as_string() == job.id,
            Audit.data['activation_id'].as_string() == activation_id)).limit(MAX_CONTINUATIONS + 3)))


def _failed_claim(db, p, job, diagnostic, attempt, after):
    """Independent fenced failure proof when this claim never wrote an intent."""
    from .worker_failure_diagnostics_v289 import attempt_record, ACTION as failure_action
    need(type(diagnostic) is dict and 'audit_id' in diagnostic)
    record = attempt_record(job.id, job.workspace_id, attempt, diagnostic['diagnostic'])
    need({key: value for key, value in diagnostic.items() if key != 'audit_id'} == record)
    rows = _audits(db, p, failure_action, job.id, MAX_CONTINUATIONS + 3)
    matches = [row for row in rows if row.id == diagnostic['audit_id']]
    need(len(matches) == 1 and matches[0].actor_id == p.user_id and matches[0].data == record)
    created = matches[0].created_at
    since = datetime.fromisoformat(after.replace('Z', '+00:00'))
    need(created.tzinfo is not None and created.utcoffset() is not None and created > since)
    return {'audit_id': matches[0].id, 'record_hash': record['record_hash'], 'created_at': created.isoformat()}


def checked_intent_chain(certificate, intents):
    """Pure chain shape only; callers must check actual independent Audit rows."""
    _sealed(certificate)
    need(certificate['schema'] == SCHEMA and type(intents) is list and len(intents) <= MAX_CONTINUATIONS + 1)
    previous = certificate['record_hash']; audit_ids = {certificate['initial_intent_audit_id']}
    lease_tokens = {certificate['failure']['transport']['dispatch_ownership']['lease_token']}
    for index, value in enumerate(intents, 1):
        _sealed(value)
        need(set(value) == {'schema', 'version', 'original_record_hash', 'previous_hash', 'job_id', 'task_id',
            'workspace_id', 'actor_id', 'input_hash', 'dispatch_audit_id', 'lease_token', 'job_attempts',
            'transport', 'created_at', 'recovery_attempt', 'record_hash'}
            and value['schema'] == INTENT and value['version'] == index
            and value['original_record_hash'] == certificate['record_hash'] and value['previous_hash'] == previous
            and all(value[key] == certificate[key] for key in ('job_id', 'task_id', 'workspace_id', 'actor_id', 'input_hash'))
            and value['dispatch_audit_id'] not in audit_ids
            and type(value['lease_token']) is str and bool(value['lease_token']) and value['lease_token'] not in lease_tokens
            and type(value['recovery_attempt']) is int and 1 <= value['recovery_attempt'] <= MAX_CONTINUATIONS + 1
            and (index == 1 or value['recovery_attempt'] > intents[index - 2]['recovery_attempt'])
            and value['job_attempts'] == min(value['recovery_attempt'] + 1, 3)
            and type(value['transport']) is dict and set(value['transport']) in (TRANSPORT_KEYS, TRANSPORT_KEYS | {'provenance_wait_hash'})
            and all(value['transport'][key] == certificate[key] for key in ('job_id', 'invocation_key', 'input_hash', 'attempt', 'cancel_epoch'))
            and value['transport']['dispatch_ownership'] == {'job_id': certificate['job_id'], 'lease_token': value['lease_token']}
            and value['transport']['dispatch_committed'] is True
            and value['transport']['dispatch_input_hash'] == certificate['input_hash'])
        audit_ids.add(value['dispatch_audit_id']); lease_tokens.add(value['lease_token']); previous = value['record_hash']
    return previous


def _ledger(db, p, task, job, item):
    certificate = job.data[KEY]; _sealed(certificate)
    need(certificate['schema'] == SCHEMA and all(certificate[key] == value for key, value in {
        'workspace_id': p.workspace_id, 'task_id': task.id, 'job_id': job.id,
        'actor_id': p.user_id, 'invocation_key': job.idempotency_key,
        'input_hash': job.data['input_hash']}.items()) and certificate['failure']['job_attempts'] == 1)
    originals = _audits(db, p, ACTION, job.id, 2)
    need(len(originals) == 1 and originals[0].actor_id == p.user_id and originals[0].data == certificate)
    history = job.data.get(CONTINUATIONS, []); intents = job.data.get(INTENTS, [])
    head = checked_intent_chain(certificate, intents)
    need(type(history) is list and len(history) <= MAX_CONTINUATIONS
        and (CONTINUATIONS not in job.data or bool(history)) and (INTENTS not in job.data or bool(intents)))
    intent_audits = _audits(db, p, INTENT_ACTION, job.id, MAX_CONTINUATIONS + 2)
    need(len(intent_audits) == len(intents) and all(sum(row.actor_id == p.user_id and row.data == value
        for row in intent_audits) == 1 for value in intents))
    dispatches = _matching_dispatches(db, p, task, job, certificate['activation_id'])
    dispatch_ids = [certificate['initial_intent_audit_id'], *[value['dispatch_audit_id'] for value in intents]]
    need(len(dispatches) == len(dispatch_ids) and {row.id for row in dispatches} == set(dispatch_ids)
        and all(row.actor_id == p.user_id and row.data == {'activation_id': certificate['activation_id'], 'job_id': job.id}
            for row in dispatches))
    latest = (intents[-1]['transport'] if intents else certificate['failure']['transport'])
    need({k: v for k, v in item.items() if k not in {'phase', 'reason'}}
        == {k: v for k, v in latest.items() if k not in {'phase', 'reason'}})
    rows = _audits(db, p, CONTINUED, job.id, MAX_CONTINUATIONS + 1)
    need(len(rows) == len(history))
    prior, nonces, previous_intent_count = certificate['record_hash'], [certificate], 0
    for index, value in enumerate(history, 1):
        _sealed(value)
        need(value['schema'] == CONTINUATION and value['version'] == index
            and value['original_record_hash'] == certificate['record_hash'] and value['previous_hash'] == prior
            and all(value[key] == certificate[key] for key in IDENTITY)
            and value['request_id'] not in {entry['request_id'] for entry in nonces}
            and value['failure']['job_attempts'] == min(index + 1, 3)
            and type(value['intent_count']) is int and previous_intent_count <= value['intent_count'] <= previous_intent_count + 1
            and value['intent_count'] == len([intent for intent in intents if intent['recovery_attempt'] <= index])
            and value['intent_head_hash'] == (intents[value['intent_count'] - 1] if value['intent_count'] else certificate)['record_hash']
            and sum(row.actor_id == p.user_id and row.data == value for row in rows) == 1)
        if value['intent_count'] == previous_intent_count:
            need(value['failure_proof'] == _failed_claim(db, p, job, value['failure']['diagnostic'],
                value['failure']['job_attempts'], nonces[-1]['created_at']))
        else:
            need(value['failure_proof'] is None)
        previous_intent_count = value['intent_count']
        nonces.append(value); prior = value['record_hash']
    retained = task.data.get('graph_retry_requests', {})
    need({key for key, value in retained.items() if value.get('activation_id') == certificate['activation_id']}
        == {value['request_id'] for value in nonces})
    for value in nonces:
        need(retained.get(value['request_id']) == {'actor_id': p.user_id, 'body_hash': value['body_hash'],
            'activation_id': value['activation_id'], 'job_id': job.id})
    recovery = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'workflow.graph.recovery_requested',
        Audit.data['activation_id'].as_string() == certificate['activation_id']).limit(MAX_CONTINUATIONS + 2)))
    need(len(recovery) == len(nonces) and all(sum(row.actor_id == p.user_id and row.data == {
        'activation_id': value['activation_id'], 'request_id': value['request_id']} for row in recovery) == 1 for value in nonces))
    return certificate, history, intents, head


@checked
def prepare(db, p, task, release, business, graph, activation, node, job, item, body, signature):
    from .leases import ownership
    need(not ownership(db) and KEY not in job.data and CONTINUATIONS not in job.data and INTENTS not in job.data
        and 'workflow_local_pre_dispatch_recovery_v287' not in job.data and job.attempts == 1
        and job.data.get('recovery_only', False) is False and 'retry_request_id' not in job.data
        and job.lease_token == item['dispatch_ownership']['lease_token']
        and body.expected_attempt == 1 and body.activation_id == activation['id']
        and body.version == activation['version'] and body.cancel_epoch == activation['epoch']
        and signature == digest({'actor_id': p.user_id, 'body': body.model_dump(mode='json')}))
    identity = _basis(db, p, task, release, business, graph, activation, node, job, item, running=False)
    failure = _failure(job, item)
    need(not any(value.get('activation_id') == activation['id'] for value in task.data.get('graph_retry_requests', {}).values()))
    intents = _matching_dispatches(db, p, task, job, activation['id'])
    need(len(intents) == 1 and intents[0].actor_id == p.user_id
        and intents[0].data == {'activation_id': activation['id'], 'job_id': job.id})
    for action in (ACTION, CONTINUED, INTENT_ACTION):
        need(not _audits(db, p, action, job.id, 1))
    _empty(db, select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'workflow.graph.recovery_requested',
        Audit.data['activation_id'].as_string() == activation['id']))
    return _seal({'schema': SCHEMA, **identity, 'initial_intent_audit_id': intents[0].id,
        'request_id': str(body.request_id), 'body_hash': signature, 'job_data_hash': digest(job.data),
        'failure': failure, 'created_at': now().isoformat()})


def _existing(db, p, task, job, *, running):
    from . import workflow_graph_scope as scope
    actual, release, business, graph, state, activation, node = scope._frozen(db, p, task.id, job.data['activation_id'])
    item = scope._job_matches(actual, job, state, activation, node)
    certificate, history, intents, head = _ledger(db, p, actual, job, item)
    identity = _basis(db, p, actual, release, business, graph, activation, node, job, item, running=running)
    need(all(identity[key] == certificate[key] for key in IDENTITY)
        and job.attempts == min(len(history) + 2, 3))
    return certificate, history, intents, head, actual, release, business, activation, node, item


@checked
def prepare_continuation(db, p, task, release, business, graph, activation, node, job, item, body, signature):
    from .leases import ownership
    need(not ownership(db) and body.expected_attempt == 1 and body.activation_id == activation['id']
        and body.version == activation['version'] and body.cancel_epoch == activation['epoch']
        and str(body.request_id) not in task.data.get('graph_retry_requests', {})
        and signature == digest({'actor_id': p.user_id, 'body': body.model_dump(mode='json')}))
    certificate, history, intents, head, *_ = _existing(db, p, task, job, running=False)
    previous_count = history[-1]['intent_count'] if history else 0
    need(len(history) < MAX_CONTINUATIONS and previous_count <= len(intents) <= previous_count + 1
        and all(value['recovery_attempt'] <= len(history) + 1 for value in intents))
    failure = _failure(job, item)
    proof = (_failed_claim(db, p, job, failure['diagnostic'], job.attempts,
        (history[-1] if history else certificate)['created_at']) if len(intents) == previous_count else None)
    return _seal({'schema': CONTINUATION, **{key: certificate[key] for key in IDENTITY},
        'version': len(history) + 1, 'original_record_hash': certificate['record_hash'],
        'previous_hash': (history[-1] if history else certificate)['record_hash'], 'intent_head_hash': head,
        'intent_count': len(intents), 'failure_proof': proof,
        'request_id': str(body.request_id), 'body_hash': signature, 'job_data_hash': digest(job.data),
        'failure': failure, 'created_at': now().isoformat()})


@checked
def requeue(db, p, job, record):
    _sealed(record)
    need(record['workspace_id'] == job.workspace_id == p.workspace_id and record['actor_id'] == p.user_id
        and record['job_id'] == job.id and record['invocation_key'] == job.idempotency_key
        and record['job_data_hash'] == digest(job.data) and job.lease_until is None
        and record['failure']['status'] == job.status and record['failure']['job_version'] == job.version
        and record['failure']['job_attempts'] == job.attempts)
    if record['schema'] == SCHEMA:
        need(KEY not in job.data and job.attempts == 1)
        additions = {KEY: copy.deepcopy(record)}; action = ACTION
    else:
        history = job.data.get(CONTINUATIONS, [])
        need(record['schema'] == CONTINUATION and record['version'] == len(history) + 1 <= MAX_CONTINUATIONS
            and record['original_record_hash'] == job.data[KEY]['record_hash']
            and record['previous_hash'] == (history[-1] if history else job.data[KEY])['record_hash'])
        additions = {CONTINUATIONS: [*copy.deepcopy(history), copy.deepcopy(record)]}; action = CONTINUED
    job.status = 'queued'; job.available_at = now(); job.lease_token = None; job.lease_until = None; job.version += 1
    job.data = {**job.data, **additions, 'recovery_only': False}
    audit(db, p, action, job.id, copy.deepcopy(record))


@checked
def preflight(db, p, task, job, node, cap, inputs):
    """A cold original-producer check, not a new wait, SDK, query or grant."""
    from .leases import ownership, fenced
    from .relations import lock_current_actor
    lease = ownership(db)
    need(KEY in job.data and fenced(db) and lease.get('job_id') == job.id and lease.get('lease_token'))
    current = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
        Job.kind == 'execute_full_semantic_graph_node', Job.status == 'running',
        Job.lease_token == lease['lease_token'], Job.lease_until > func.clock_timestamp())
        .execution_options(populate_existing=True))
    need(current is not None)
    lock_current_actor(db, p)
    certificate, history, intents, head, actual, release, business, activation, actual_node, item = _existing(
        db, p, task, current, running=True)
    need(actual_node == node and cap.id == certificate['capability_id']
        and cap.content_hash == certificate['capability_hash'] and inputs == current.data['inputs']
        and len(intents) == (history[-1]['intent_count'] if history else 0))
    return False


def can_start_after_pre_dispatch_recovery(db, p, task, job):
    if KEY not in job.data: return False
    try:
        from . import workflow_graph_scope as scope
        actual, release, business, graph, state, activation, node = scope._frozen(db, p, task.id, job.data['activation_id'])
        cap = published(db, p, node['execution']['capability_id'])
        preflight(db, p, actual, job, node, cap, job.data['inputs'])
        return True
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration,
            UnicodeError, RecursionError, OverflowError):
        return False


@checked
def record_dispatch_intent(db, p, task, job, next_transport):
    """Anchor this new lease's ordinary graph intent; retain every earlier one."""
    from .leases import ownership
    old = task.data['graph_jobs'][job.data['activation_id']]
    certificate, history, intents, head = _ledger(db, p, task, job, old)
    lease = ownership(db)
    need(len(intents) == (history[-1]['intent_count'] if history else 0) and len(intents) < MAX_CONTINUATIONS + 1
        and lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token
        and next_transport['dispatch_ownership'] == lease and next_transport['phase'] == 'running'
        and next_transport['dispatch_input_hash'] == certificate['input_hash'])
    dispatch_id = uid()
    value = _seal({'schema': INTENT, 'version': len(intents) + 1,
        'original_record_hash': certificate['record_hash'], 'previous_hash': head,
        **{key: certificate[key] for key in ('job_id', 'task_id', 'workspace_id', 'actor_id', 'input_hash')},
        'dispatch_audit_id': dispatch_id, 'lease_token': lease['lease_token'], 'job_attempts': job.attempts,
        'recovery_attempt': len(history) + 1,
        'transport': copy.deepcopy(next_transport), 'created_at': now().isoformat()})
    db.add(Audit(id=dispatch_id, workspace_id=p.workspace_id, actor_id=p.user_id,
        action='workflow.graph.dispatch', object_id=task.id,
        data={'activation_id': certificate['activation_id'], 'job_id': job.id}))
    audit(db, p, INTENT_ACTION, job.id, copy.deepcopy(value))
    job.data = {**job.data, INTENTS: [*copy.deepcopy(intents), value]}
