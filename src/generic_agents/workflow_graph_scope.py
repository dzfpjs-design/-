"""Real graph modality ownership, independent of legacy single-step scheduling.

The scope is an activation identity, not a fabricated ``automatic`` step. Live
checks require the original leased Job and running Invocation. Historical result
checks validate their frozen provenance without granting execution authority.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import select, func

from .contracts import digest
from .models import Job, Task, Invocation, Capability, DomainInstance
from .services import scoped, published, audit
from .leases import ownership
from . import full_semantic_graph_v1 as pure

KIND = 'execute_full_semantic_graph_node'
SCHEMA = 'workflow-graph-activation-scope-v1'


def need(ok):
    if not ok:
        raise HTTPException(409, '图能力的原节点、冻结输入、调用或有效租约不一致；原记录保留')


def owned_graph_job(db, p):
    """Select the explicit graph branch; this alone never grants permission."""
    lease = ownership(db)
    if not lease.get('job_id'):
        return None
    job = scoped(db, Job, lease['job_id'], p)
    return job if job.kind == KIND else None


def _frozen(db, p, task_id, activation_id):
    from .workspace_api import saved_execution
    from .workflow_graph import graph_for
    task = db.scalar(select(Task).where(Task.id == task_id, Task.workspace_id == p.workspace_id)
        .execution_options(populate_existing=True))
    need(task is not None)
    task, release, business = saved_execution(db, p, task.id)
    need(business.get('execution_mode') == 'confirmed_graph_workflow')
    graph = graph_for(release, business)
    try:
        state = pure.check_run(graph, task.data['graph_runtime'])
        activation = state['activations'][activation_id]
        node = next(n for n in graph['nodes'] if n['id'] == activation['node_id'])
    except (ValueError, KeyError, StopIteration, TypeError):
        need(False)
    need(node['kind'] == 'ability' and activation['inputs_snapshot'] is not None)
    return task, release, business, graph, state, activation, node


def _scope(task, job, node, activation):
    return {'schema': SCHEMA, 'task_id': task.id,
        'graph_hash': task.data['graph_runtime']['graph_hash'],
        'activation_id': activation['id'], 'node_id': node['id'],
        'cancel_epoch': activation['epoch'], 'invocation_key': job.data['invocation_key'],
        'input_hash': digest(activation['inputs_snapshot']['arguments']),
        'capability_id': node['execution']['capability_id'],
        'capability_hash': node['execution']['capability_hash']}


def _job_matches(task, job, state, activation, node):
    transport = task.data.get('graph_jobs', {}).get(activation['id'])
    need(transport is not None and job.kind == KIND and job.data.get('task_id') == task.id
        and job.data.get('activation_id') == activation['id']
        and job.data.get('actor_id') == node['actor_id']
        and job.data.get('graph_hash') == state['graph_hash']
        and job.data.get('cancel_epoch') == activation['epoch']
        and job.data.get('activation_version') == 0
        and transport.get('job_id') == job.id
        and transport.get('invocation_key') == job.data.get('invocation_key')
        and transport.get('cancel_epoch') == activation['epoch']
        and job.data.get('input_hash') == digest(job.data.get('inputs'))
        and job.data.get('inputs') == activation['inputs_snapshot']['arguments']
        and transport.get('input_hash') == job.data['input_hash'])
    return transport


def graph_context(db, p, key, cap):
    """Return ``(task, job, node, scope)`` for an actual in-flight graph call."""
    lease = ownership(db)
    need(bool(lease.get('job_id')) and bool(lease.get('lease_token')))
    job = db.scalar(select(Job).where(Job.id == lease['job_id'], Job.workspace_id == p.workspace_id,
        Job.kind == KIND, Job.status == 'running', Job.lease_token == lease['lease_token'],
        Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    need(job is not None and job.data.get('invocation_key') == key)
    task, release, business, graph, state, activation, node = _frozen(
        db, p, job.data.get('task_id'), job.data.get('activation_id'))
    transport = _job_matches(task, job, state, activation, node)
    from .graph_reasoning_retirement_v337 import is_retired
    need(not is_retired(task, activation['id']) and transport.get('phase') != 'retired_for_new_evaluation')
    need(state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids']
        and activation['epoch'] == state['cancel_epoch'] and transport.get('phase') == 'running'
        and node['actor_id'] == p.user_id)
    actual = published(db, p, cap.id)
    db.refresh(actual)
    pin = node['execution']
    need(actual.status == 'published' and actual.validated_hash == actual.content_hash
        and actual.id == pin['capability_id'] and actual.content_hash == pin['capability_hash']
        and digest(actual.data) == actual.content_hash and actual.release_version == pin['release_version']
        and actual.data['execution']['adapter'] == pin['adapter']
        and actual.data['input_schema'] == pin['input_schema']
        and actual.data['output_schema'] == pin['output_schema'])
    try:
        need(pure.ability_inputs(graph, state, activation['id']) == job.data['inputs'])
    except ValueError:
        need(False)
    call = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key).execution_options(populate_existing=True))
    need(call is not None and call.status == 'running' and call.capability_id == actual.id
        and call.data.get('capability_hash') == pin['capability_hash']
        and call.data.get('role') == pin['agent_role']
        and call.data.get('input_hash') == job.data['input_hash']
        and call.data.get('job_id') == job.id and call.data.get('lease_token') == lease['lease_token'])
    instance = scoped(db, DomainInstance, task.data['instance_id'], p)
    # The task keeps its original release when another release becomes active.
    # Its current instance/business/source authority is checked by live below.
    need(instance.id == release.instance_id == task.data['instance_id'])
    from .workflow_graph import live
    live(db, p, task, release, business)
    from .business_reasoning_protocols_v276 import applies, qualify_evidence
    if applies(node['execution']['adapter']):
        # The new protocol qualifies only its original packets here. It does
        # not call _current/graph_context again or construct a live frame.
        qualify_evidence(node['execution']['adapter'], db, p, task, job.data['inputs'])
    return task, job, node, _scope(task, job, node, activation)


def before_dispatch(db, p, key, cap):
    """Commit one dispatch authorization while holding the task's cancel lock.

    Called by the gateway after persisting the running Invocation, immediately
    before the actual adapter call. Cancellation and this authorization serialize
    on the same Task row. Once this transaction commits, the call is in flight;
    its eventual outcome remains history even when a later cancel wins the epoch.
    No pure-state transition or legacy single-step projection is performed.
    """
    lease = ownership(db)
    need(bool(lease.get('job_id')) and bool(lease.get('lease_token')))
    job = db.scalar(select(Job).where(Job.id == lease['job_id'], Job.workspace_id == p.workspace_id,
        Job.kind == KIND, Job.status == 'running', Job.lease_token == lease['lease_token'],
        Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    need(job is not None and job.data.get('invocation_key') == key)
    locked = db.scalar(select(Task).where(Task.id == job.data.get('task_id'),
        Task.workspace_id == p.workspace_id).with_for_update().execution_options(populate_existing=True))
    need(locked is not None)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,locked,record_actor=True)
    # Re-read after acquiring the cancel lock; no pre-lock epoch/source result
    # can authorize the dispatch. The lease hook also fences this final commit.
    task, current_job, node, frozen = graph_context(db, p, key, cap)
    need(task.id == locked.id and current_job.id == job.id)
    from .workflow_graph_goal_guard_v133 import before_dispatch as goal_before_dispatch
    goal_before_dispatch(db, p, task, job, node)
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    transports = copy.deepcopy(task.data['graph_jobs'])
    transport = transports[frozen['activation_id']]
    # A persisted authorization is an in-flight call, never a license to send
    # the same invocation a second time after a lost response or worker restart.
    need('dispatch_authorization' not in transport)
    authorized_at = db.scalar(select(func.clock_timestamp())).isoformat()
    authorization = {'epoch': frozen['cancel_epoch'], 'job_id': job.id,
        'lease_token': lease['lease_token'], 'input_hash': frozen['input_hash'],
        'capability_hash': frozen['capability_hash'], 'actor_id': p.user_id,
        'authorized_at': authorized_at}
    transports[frozen['activation_id']] = {**transport, 'dispatch_authorization': authorization}
    task.data = {**task.data, 'graph_jobs': transports}
    task.version += 1
    audit(db, p, 'workflow.graph.dispatch_authorized', task.id,
        {'activation_id': frozen['activation_id'], 'job_id': job.id,
            'epoch': frozen['cancel_epoch'], 'input_hash': frozen['input_hash']})
    db.commit()
    return copy.deepcopy(authorization)


def metadata(task, job, node, scope, cap):
    """Freeze result provenance on the model's own durable run record."""
    return {'workflow_graph_scope': copy.deepcopy(scope), 'workflow_task_id': task.id,
        'workflow_job_id': job.id, 'job_id': job.id,
        'workflow_step_id': node['source_step_id'] or node['id'],
        'workflow_capability_id': cap.id, 'workflow_capability_hash': cap.content_hash,
        'workflow_capability_snapshot': copy.deepcopy(cap.data)}


def record_node(db, p, task, row, step=None):
    """Cold-check one graph result; completed/cancelled history remains readable."""
    saved = row.data.get('workflow_graph_scope')
    need(type(saved) is dict and saved.get('schema') == SCHEMA
        and row.data.get('workflow_task_id') == task.id)
    task, release, business, graph, state, activation, node = _frozen(
        db, p, task.id, saved.get('activation_id'))
    job = scoped(db, Job, row.data.get('workflow_job_id'), p)
    _job_matches(task, job, state, activation, node)
    need(saved == _scope(task, job, node, activation)
        and row.data.get('workflow_capability_id') == node['execution']['capability_id']
        and row.data.get('workflow_capability_hash') == node['execution']['capability_hash']
        and row.data.get('workflow_step_id') == (node['source_step_id'] or node['id']))
    snapshot = row.data.get('workflow_capability_snapshot')
    need(type(snapshot) is dict and digest(snapshot) == node['execution']['capability_hash']
        and snapshot.get('input_schema') == node['execution']['input_schema']
        and snapshot.get('output_schema') == node['execution']['output_schema']
        and snapshot.get('execution', {}).get('adapter') == node['execution']['adapter'])
    if step is not None:
        need(step['id'] == (node['source_step_id'] or node['id'])
            and step['execution']['capability_id'] == node['execution']['capability_id']
            and step['execution']['capability_hash'] == node['execution']['capability_hash'])
    return node


def guard_record(db, p, row):
    """Live model checkpoint: retain old results, forbid any stale activation IO."""
    saved = row.data.get('workflow_graph_scope')
    need(type(saved) is dict)
    cap = scoped(db, Capability, row.data.get('workflow_capability_id'), p)
    task, job, node, scope = graph_context(db, p, saved.get('invocation_key'), cap)
    need(row.data.get('workflow_job_id') == job.id and saved == scope)
    record_node(db, p, task, row)
    return row
