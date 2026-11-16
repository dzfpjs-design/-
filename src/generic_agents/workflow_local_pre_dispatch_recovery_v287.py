"""One original V285 local planner Job, proven to precede native dispatch.

This is not a generic ``no Invocation means no effect`` rule. Only the fixed
published local V285 implementation in the adopted V286 graph can qualify.
The normal graph retry endpoint owns Job→Task locks, actor/nonce/version checks,
current live approval and commit. This module never invokes an SDK, creates a
Job, reserves a budget or changes a prior Invocation/answer/receipt.
"""
import copy
from datetime import datetime
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from .contracts import digest
from .models import Audit, Invocation, Job, now
from .services import audit, published

KEY = 'workflow_local_pre_dispatch_recovery_v287'
SCHEMA = 'workflow-local-pre-dispatch-recovery-v287'
ACTION = 'workflow.graph.local_pre_dispatch_recovered_v287'
CONTINUATIONS = KEY + '_continuations'
CONTINUATION = 'workflow-local-pre-dispatch-continuation-v287'
CONTINUED = 'workflow.graph.local_pre_dispatch_continued_v287'
MAX_CONTINUATIONS = 2
ERROR = '本地计划的原失败、未派发证明或当前归属不完整；原请求保留，未新增调用或重新排队'
TRANSPORT_KEYS = {'job_id', 'invocation_key', 'input_hash', 'phase', 'cancel_epoch', 'attempt', 'reason'}
DISPATCH_KEYS = {'dispatch_committed', 'dispatch_ownership', 'dispatch_authorization',
    'dispatch_input_hash', 'invocation_id', 'invocation_status', 'service_state'}


def need(ok):
    if not ok:
        raise HTTPException(409, ERROR)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, IndexError,
                UnicodeError, RecursionError, OverflowError):
            need(False)
    return wrapped


def _empty(db, statement):
    # A single found row is enough to refuse. No bounded inventory is truncated
    # into a successful proof, and no new lock is acquired after the Task lock.
    need(db.scalar(statement.limit(1)) is None)


@checked
def prepare(db, p, task, release, business, graph, activation, node, job, item, body, signature):
    """SELECT-only proof under the original retry's already held Job/Task locks."""
    from . import workflow_graph as runtime, workflow_graph_scope as scope
    from . import workflow_decision_tool_planner_v285 as planner
    from . import authored_decision_workflow_store_v286 as family
    from .leases import ownership
    from .workflow_abilities import eligible_service

    state = task.data['graph_runtime']; data = job.data
    need(task.workspace_id == job.workspace_id == p.workspace_id and not ownership(db)
        and job.kind == scope.KIND and job.status in {'failed', 'blocked', 'cancelled'}
        and job.lease_until is None and type(job.attempts) is int and job.attempts == 1
        and type(job.version) is int and job.version >= 1
        and type(data) is dict and KEY not in data and not (DISPATCH_KEYS & set(data))
        and type(item) is dict and set(item) == TRANSPORT_KEYS and item['phase'] == 'blocked'
        and type(item['reason']) is str and bool(item['reason'])
        and type(item['attempt']) is int and item['attempt'] == 1
        and type(data.get('attempt')) is int and data['attempt'] == 1
        and type(data.get('lease_recoveries', 0)) is int and data.get('lease_recoveries', 0) == 0
        and data.get('recovery_only', False) is False and 'retry_request_id' not in data
        and data['actor_id'] == node['actor_id'] == p.user_id
        and state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
        and activation['id'] == body.activation_id == data['activation_id']
        and activation['id'] in state['eligible_ability_ids'] and activation['version'] == body.version
        and activation['epoch'] == state['cancel_epoch'] == body.cancel_epoch == data['cancel_epoch']
        and type(body.expected_attempt) is int and body.expected_attempt == 1
        and data['task_id'] == task.id and data['graph_hash'] == state['graph_hash'] == digest(graph)
        and data['input_hash'] == digest(data['inputs']) == item['input_hash']
        and data['invocation_key'] == job.idempotency_key == item['invocation_key']
        == 'graph:' + task.id + ':' + activation['id'] + ':1'
        and item['job_id'] == job.id and item['cancel_epoch'] == activation['epoch']
        and node['kind'] == 'ability' and node['id'] == activation['node_id'] == 'planner'
        and node['execution']['adapter'] == 'workflow_service'
        and data['inputs'] == activation['inputs_snapshot']['arguments']
        and data['inputs'] == runtime.invoke_pure(runtime.graph_api.ability_inputs, graph, state, activation['id'])
        and signature == digest({'actor_id': p.user_id, 'body': body.model_dump(mode='json')}))
    need(type(data.get('error')) is str and bool(data['error'])
        and type(data.get('finished_at')) is str)
    finished = datetime.fromisoformat(data['finished_at'].replace('Z', '+00:00'))
    need(finished.tzinfo is not None and finished.utcoffset() is not None)
    scope._job_matches(task, job, state, activation, node)
    need(not any(value.get('activation_id') == activation['id']
        for value in task.data.get('graph_retry_requests', {}).values()))

    cap = published(db, p, node['execution']['capability_id'])
    planner.validate_manifest(cap.data)
    eligible_service(cap, 'business', service_actor='execution_service', operation=planner.OP)
    need(cap.kind == 'tool' and cap.content_hash == cap.validated_hash == digest(cap.data)
        and cap.content_hash == node['execution']['capability_hash']
        and cap.release_version == node['execution']['release_version']
        and node['execution']['agent_role'] == 'business'
        and node['execution']['input_schema'] == cap.data['input_schema']
        and node['execution']['output_schema'] == cap.data['output_schema']
        and node['execution']['max_attempts'] == cap.data['budget']['max_attempts'] == 1)
    binding = runtime.business_binding(release, business)
    need(binding.get('schema') == 'authored-decision-workflow-binding-v286'
        and family.check_binding(binding) == binding and binding['graph'] == graph
        and binding['capability_pins']['planner'] == node['execution']
        and family.service_delegations(binding)['planner'] == {
            'service_actor': 'execution_service', 'operation': planner.OP, 'agent_role': 'business'})
    package = published(db, p, release.package_id, 'domain_package')
    need(cap.id in package.data['dependencies'])
    _empty(db, select(Invocation.id).where(Invocation.workspace_id == p.workspace_id,
        or_(Invocation.idempotency_key == job.idempotency_key, Invocation.data['job_id'].as_string() == job.id)))
    _empty(db, select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == scope.KIND,
        Job.id != job.id, Job.data['task_id'].as_string() == task.id,
        Job.data['activation_id'].as_string() == activation['id']))
    _empty(db, select(Audit.id).where(Audit.workspace_id == p.workspace_id, Audit.object_id == task.id,
        Audit.action.in_(['workflow.graph.dispatch', 'workflow.graph.dispatch_authorized',
            'workflow.graph.node_completed', 'workflow.graph.recovery_requested']),
        or_(Audit.data['job_id'].as_string() == job.id,
            Audit.data['activation_id'].as_string() == activation['id'])))
    _empty(db, select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == job.id, Audit.action == ACTION))

    record = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'activation_id': activation['id'], 'actor_id': p.user_id, 'job_id': job.id,
        'invocation_key': job.idempotency_key, 'attempt': 1, 'input_hash': data['input_hash'],
        'graph_hash': state['graph_hash'], 'cancel_epoch': activation['epoch'],
        'activation_version': activation['version'], 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'request_id': str(body.request_id), 'body_hash': signature, 'job_data_hash': digest(data),
        'failure': {'status': job.status, 'error': data['error'], 'finished_at': data['finished_at'],
            'job_version': job.version, 'attempts': job.attempts, 'transport': copy.deepcopy(item)},
        'created_at': now().isoformat()}
    record['record_hash'] = digest(record)
    return record


@checked
def requeue(db, p, job, record):
    """Called only after the original retry's full runtime.live authority gate."""
    if record.get('schema') == CONTINUATION:
        return _requeue_continuation(db, p, job, record)
    need(record['schema'] == SCHEMA and record['workspace_id'] == job.workspace_id == p.workspace_id
        and record['actor_id'] == p.user_id and record['job_id'] == job.id
        and record['invocation_key'] == job.idempotency_key and record['job_data_hash'] == digest(job.data)
        and record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'})
        and job.status == record['failure']['status'] and job.version == record['failure']['job_version']
        and job.attempts == record['failure']['attempts'] == 1 and job.lease_until is None and KEY not in job.data)
    # Historical lease_token may remain on a terminal Job. It is not a live
    # owner: worker termination clears lease_until. The next claim gets its own
    # token; no original dispatch token or authorization is reconstructed.
    job.status = 'queued'; job.available_at = now(); job.lease_token = None; job.lease_until = None
    job.version += 1
    job.data = {**job.data, KEY: copy.deepcopy(record), 'recovery_only': False}
    audit(db, p, ACTION, job.id, copy.deepcopy(record))


def _certificate(db, p, task, job):
    """Original immutable certificate, bounded continuations and actual nonces."""
    certificate = job.data[KEY]
    need(type(certificate) is dict and certificate['schema'] == SCHEMA
        and certificate['record_hash'] == digest({key: value for key, value in certificate.items() if key != 'record_hash'})
        and certificate['workspace_id'] == p.workspace_id == task.workspace_id == job.workspace_id
        and certificate['task_id'] == task.id and certificate['job_id'] == job.id
        and certificate['actor_id'] == p.user_id == job.data['actor_id']
        and certificate['attempt'] == certificate['failure']['attempts'] == 1
        and certificate['failure']['status'] in {'failed', 'blocked', 'cancelled'}
        and certificate['invocation_key'] == job.idempotency_key == job.data['invocation_key']
        and certificate['input_hash'] == job.data['input_hash'] == digest(job.data['inputs']))
    original = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == job.id, Audit.action == ACTION).limit(2)))
    need(len(original) == 1 and original[0].actor_id == certificate['actor_id']
        and original[0].data == certificate)
    history = job.data.get(CONTINUATIONS, [])
    need(type(history) is list and len(history) <= MAX_CONTINUATIONS
        and (CONTINUATIONS not in job.data or bool(history)))
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == job.id, Audit.action == CONTINUED).limit(MAX_CONTINUATIONS + 1)))
    need(len(rows) == len(history))
    prior = certificate['record_hash']; nonces = [certificate]; used = set()
    for index, record in enumerate(history, 1):
        need(type(record) is dict and record['schema'] == CONTINUATION and record['version'] == index
            and record['original_record_hash'] == certificate['record_hash'] and record['previous_hash'] == prior
            and record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'})
            and all(record[key] == certificate[key] for key in ('workspace_id', 'task_id', 'activation_id',
                'actor_id', 'job_id', 'invocation_key', 'attempt', 'input_hash', 'graph_hash', 'cancel_epoch',
                'activation_version', 'capability_id', 'capability_hash'))
            and record['request_id'] not in {item['request_id'] for item in nonces})
        matches = [row for row in rows if row.data == record and row.actor_id == record['actor_id']]
        need(len(matches) == 1 and id(matches[0]) not in used)
        used.add(id(matches[0])); nonces.append(record); prior = record['record_hash']
    retained = task.data.get('graph_retry_requests', {})
    for record in nonces:
        need(retained.get(record['request_id']) == {'actor_id': record['actor_id'], 'body_hash': record['body_hash'],
            'activation_id': record['activation_id'], 'job_id': job.id})
    # A removed/truncated continuation or retained nonce cannot discard its
    # independent normal graph recovery Audit and become a fresh permission.
    recovery = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'workflow.graph.recovery_requested',
        Audit.data['activation_id'].as_string() == certificate['activation_id']).limit(MAX_CONTINUATIONS + 2)))
    expected = [{'activation_id': record['activation_id'], 'request_id': record['request_id']} for record in nonces]
    need(len(recovery) == len(expected) and all(sum(row.actor_id == p.user_id and row.data == value
        for row in recovery) == 1 for value in expected))
    need({key for key, value in retained.items() if value.get('activation_id') == certificate['activation_id']}
        == {record['request_id'] for record in nonces})
    return certificate, history


def _existing_basis(db, p, task, job, *, running):
    from . import workflow_graph as runtime, workflow_graph_scope as scope
    from . import workflow_decision_tool_planner_v285 as planner
    from . import authored_decision_workflow_store_v286 as family
    from .workflow_abilities import eligible_service
    certificate, history = _certificate(db, p, task, job)
    actual, release, business, graph, state, activation, node = scope._frozen(
        db, p, task.id, certificate['activation_id'])
    item = scope._job_matches(actual, job, state, activation, node)
    need(actual.id == task.id and node['kind'] == 'ability' and node['id'] == activation['node_id'] == 'planner'
        and node['actor_id'] == p.user_id == certificate['actor_id']
        and node['execution']['adapter'] == 'workflow_service' and state['status'] in {'running', 'waiting'}
        and activation['status'] == 'ready' and activation['id'] in state['eligible_ability_ids']
        and activation['version'] == certificate['activation_version']
        and activation['epoch'] == state['cancel_epoch'] == certificate['cancel_epoch']
        and state['graph_hash'] == digest(graph) == certificate['graph_hash']
        and job.kind == scope.KIND and type(job.attempts) is int and 1 <= job.attempts <= 3
        and type(job.data.get('lease_recoveries', 0)) is int and job.data.get('lease_recoveries', 0) == 0
        and job.data['attempt'] == certificate['attempt'] == item['attempt'] == 1
        and type(item) is dict and set(item) == TRANSPORT_KEYS
        and item['phase'] in ({'queued', 'running'} if running else {'blocked'})
        and not (DISPATCH_KEYS & set(job.data))
        and job.data['inputs'] == activation['inputs_snapshot']['arguments']
        == runtime.invoke_pure(runtime.graph_api.ability_inputs, graph, state, activation['id'])
        and job.idempotency_key == 'graph:' + task.id + ':' + activation['id'] + ':1')
    cap = published(db, p, node['execution']['capability_id'])
    planner.validate_manifest(cap.data)
    eligible_service(cap, 'business', service_actor='execution_service', operation=planner.OP)
    need(cap.id == certificate['capability_id'] and cap.content_hash == cap.validated_hash == digest(cap.data)
        == certificate['capability_hash'] == node['execution']['capability_hash']
        and cap.release_version == node['execution']['release_version']
        and node['execution']['agent_role'] == 'business'
        and node['execution']['input_schema'] == cap.data['input_schema']
        and node['execution']['output_schema'] == cap.data['output_schema']
        and node['execution']['max_attempts'] == cap.data['budget']['max_attempts'] == 1)
    binding = runtime.business_binding(release, business)
    need(binding.get('schema') == 'authored-decision-workflow-binding-v286'
        and family.check_binding(binding) == binding and binding['graph'] == graph
        and binding['capability_pins']['planner'] == node['execution']
        and family.service_delegations(binding)['planner'] == {
            'service_actor': 'execution_service', 'operation': planner.OP, 'agent_role': 'business'})
    package = published(db, p, release.package_id, 'domain_package')
    need(cap.id in package.data['dependencies'])
    _empty(db, select(Invocation.id).where(Invocation.workspace_id == p.workspace_id,
        or_(Invocation.idempotency_key == job.idempotency_key, Invocation.data['job_id'].as_string() == job.id)))
    _empty(db, select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == scope.KIND,
        Job.id != job.id, Job.data['task_id'].as_string() == task.id,
        Job.data['activation_id'].as_string() == activation['id']))
    _empty(db, select(Audit.id).where(Audit.workspace_id == p.workspace_id, Audit.object_id == task.id,
        Audit.action.in_(['workflow.graph.dispatch', 'workflow.graph.dispatch_authorized', 'workflow.graph.node_completed']),
        or_(Audit.data['job_id'].as_string() == job.id,
            Audit.data['activation_id'].as_string() == activation['id'])))
    return certificate, history, release, business


@checked
def prepare_continuation(db, p, task, release, business, graph, activation, node, job, item, body, signature):
    """New ordinary retry nonce; retain the original certificate verbatim."""
    from .leases import ownership
    need(not ownership(db) and job.status in {'failed', 'blocked', 'cancelled'} and job.lease_until is None
        and signature == digest({'actor_id': p.user_id, 'body': body.model_dump(mode='json')}))
    certificate, history, _release, _business = _existing_basis(db, p, task, job, running=False)
    need(len(history) < MAX_CONTINUATIONS and body.activation_id == certificate['activation_id']
        and body.version == certificate['activation_version'] and body.cancel_epoch == certificate['cancel_epoch']
        and body.expected_attempt == 1 and str(body.request_id) not in task.data.get('graph_retry_requests', {})
        and type(job.data.get('error')) is str and bool(job.data['error'])
        and type(job.data.get('finished_at')) is str)
    finished = datetime.fromisoformat(job.data['finished_at'].replace('Z', '+00:00'))
    need(finished.tzinfo is not None and finished.utcoffset() is not None)
    record = {key: certificate[key] for key in ('workspace_id', 'task_id', 'activation_id', 'actor_id', 'job_id',
        'invocation_key', 'attempt', 'input_hash', 'graph_hash', 'cancel_epoch', 'activation_version',
        'capability_id', 'capability_hash')}
    record.update(schema=CONTINUATION, version=len(history) + 1,
        original_record_hash=certificate['record_hash'], previous_hash=(history[-1] if history else certificate)['record_hash'],
        request_id=str(body.request_id), body_hash=signature, job_data_hash=digest(job.data),
        failure={'status': job.status, 'error': job.data['error'], 'finished_at': job.data['finished_at'],
            'job_version': job.version, 'attempts': job.attempts, 'transport': copy.deepcopy(item)}, created_at=now().isoformat())
    record['record_hash'] = digest(record)
    return record


def _requeue_continuation(db, p, job, record):
    certificate = job.data[KEY]; history = job.data.get(CONTINUATIONS, [])
    need(record['workspace_id'] == job.workspace_id == p.workspace_id and record['actor_id'] == p.user_id
        and record['job_id'] == job.id and record['invocation_key'] == job.idempotency_key
        and record['job_data_hash'] == digest(job.data) and record['original_record_hash'] == certificate['record_hash']
        and record['version'] == len(history) + 1 <= MAX_CONTINUATIONS
        and record['previous_hash'] == (history[-1] if history else certificate)['record_hash']
        and record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'})
        and job.status == record['failure']['status'] and job.version == record['failure']['job_version']
        and job.attempts == record['failure']['attempts'] and job.lease_until is None)
    job.status = 'queued'; job.available_at = now(); job.lease_token = None; job.lease_until = None; job.version += 1
    job.data = {**job.data, CONTINUATIONS: [*copy.deepcopy(history), copy.deepcopy(record)], 'recovery_only': False}
    audit(db, p, CONTINUED, job.id, copy.deepcopy(record))


def can_start_after_pre_dispatch_recovery(db, p, task, job):
    """Read-only worker exception for the certified first local SDK invocation.

    All ordinary reserve/before_dispatch/gateway fences still run afterwards.
    The certificate does not authorize a second SDK call or an external action.
    """
    if KEY not in job.data:
        return False
    try:
        from .leases import fenced, ownership
        from .relations import lock_current_actor
        from . import workflow_graph as runtime, workflow_graph_scope as scope
        from .workflow_graph_goal_guard_v133 import progress
        lease = ownership(db)
        need(fenced(db) and lease.get('job_id') == job.id
            and type(lease.get('lease_token')) is str and bool(lease['lease_token']))
        actual = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
            Job.kind == scope.KIND, Job.status == 'running', Job.lease_token == lease['lease_token'],
            Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
        need(actual is not None and actual.lease_token == lease['lease_token'])
        lock_current_actor(db, p)
        _certificate_value, _history, release, business = _existing_basis(db, p, task, actual, running=True)
        runtime.live(db, p, task, release, business)
        progress(db, p, task)
        return True
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError,
            UnicodeError, RecursionError, OverflowError):
        return False
