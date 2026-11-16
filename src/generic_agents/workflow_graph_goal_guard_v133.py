"""Original-goal limits for graph actions, with Task-serialized reservations.

A running Invocation is history, not dispatch permission.  Reserve before any
modality preparation or gateway write; recheck under the Task cancel lock at
dispatch.  Reservations never refund attempts and never authorize result replay
to advance a stopped/expired goal.  Unconfigured legacy graphs remain unchanged.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import or_, select

from .contracts import digest
from .models import Audit, InstanceRelease, Invocation, Job
from .services import audit

KEY = 'graph_goal_reservations_v133'
SCHEMA = 'workflow-graph-goal-reservations-v133'
RECORD_KEYS = {'invocation_key', 'job_id', 'activation_id', 'actor_id', 'graph_hash',
    'input_hash', 'cancel_epoch', 'attempt', 'goal_context_hash', 'goal_hash', 'reservation_hash'}


def _need(value):
    if not value:
        raise HTTPException(409, '原目标的图执行额度或冻结关联无法核对，原输入与调用历史保留')


def _int(value, minimum=0):
    return type(value) is int and value >= minimum


def _context(task):
    data = task.data
    if 'graph_runtime' not in data:
        _need(KEY not in data)
        return None
    context = data.get('native_goal_context')
    if context is None:
        _need(KEY not in data and data.get('goal_spec') is None
            and not data.get('native_goal_commands') and not data.get('goal_hash'))
        return None
    from .goals import snapshot
    _need(type(context) is dict and digest(context) == data.get('native_goal_context_hash')
        and context.get('goal_snapshot') == snapshot(task)
        and context.get('instance_id') == data.get('instance_id')
        and context.get('release_id') == data.get('instance_release_id')
        and context.get('business_id') == data.get('business_id')
        and context.get('entity_ids') == data.get('entity_ids')
        and type(data.get('goal_spec')) is dict
        and digest(data['goal_spec']) == data.get('goal_hash'))
    return context


def _ledger(task):
    context = _context(task)
    if context is None:
        return None
    value = task.data.get(KEY)
    if value is None:
        return {'schema': SCHEMA, 'task_id': task.id, 'workspace_id': task.workspace_id,
            'goal_context_hash': digest(context), 'items': {}}
    _need(type(value) is dict and set(value) == {'schema', 'task_id', 'workspace_id',
        'goal_context_hash', 'items', 'ledger_hash'}
        and value['schema'] == SCHEMA and value['task_id'] == task.id
        and value['workspace_id'] == task.workspace_id
        and value['goal_context_hash'] == digest(context)
        and value['ledger_hash'] == digest({k: v for k, v in value.items() if k != 'ledger_hash'})
        and type(value['items']) is dict and len(value['items']) <= 1000)
    graph = task.data['graph_runtime']
    for key, item in value['items'].items():
        _need(type(item) is dict and set(item) == RECORD_KEYS
            and all(type(item[k]) is str and item[k] for k in RECORD_KEYS - {'cancel_epoch', 'attempt'})
            and key == item['invocation_key']
            and item['reservation_hash'] == digest({k: v for k, v in item.items() if k != 'reservation_hash'})
            and item['goal_context_hash'] == value['goal_context_hash']
            and item['goal_hash'] == task.data['goal_hash']
            and item['graph_hash'] == graph['graph_hash']
            and _int(item['cancel_epoch']) and item['cancel_epoch'] <= graph['cancel_epoch']
            and _int(item['attempt'], 1))
    return copy.deepcopy(value)


def reservations(task):
    """Cold pure inventory; counting this never grants execution authority."""
    value = _ledger(task)
    return [] if value is None else list(value['items'].values())


def verify_reservations(db, p, task):
    """Check the independent preflight inventory without granting new authority.

    This deliberately does not call native_goals.guard (which calls us). Only
    SELECTs are used after the caller's Task lock; no new Job lock is acquired.
    A pre-V133 failed/unadopted dispatch has no recoverable step reservation:
    new execution is blocked rather than signing a replacement authorization.
    Its original Task/Invocation/receipt remains readable by history paths.
    """
    try:
        return _verify_reservations(db, p, task)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        _need(False)


def _node_call_history(task, job, node, call):
    """Check frozen node approval, never current execution permission.

    Local archive/packet writes and delegated services have explicit installed
    approval contracts. Requiring every graph call to be read-only rejects
    those real nodes after their first durable Invocation. Historical checks
    use the original snapshot and command; live authority stays at dispatch.
    """
    adapter = node['execution']['adapter']
    if adapter == 'workflow_service':
        from .workflow_service_v195 import make_approval
        from .workflow_service_catalog_v197 import validate_manifest
    elif adapter == 'task_archive':
        from .workflow_task_archive_v198 import make_approval, validate_manifest
    elif adapter == 'task_evidence_packet':
        from .workflow_task_packet_v199 import make_approval, validate_manifest
    elif adapter == 'runtime_document_packet':
        from .workflow_document_packet_v207 import make_approval, validate_manifest
    else:
        _need(call.data.get('approval') is None and call.data.get('side_effect') is False)
        return
    snapshot = call.data.get('capability_snapshot')
    _need(type(snapshot) is dict and digest(snapshot) == node['execution']['capability_hash']
        and snapshot['execution']['adapter'] == adapter
        and snapshot['input_schema'] == node['execution']['input_schema']
        and snapshot['output_schema'] == node['execution']['output_schema']
        and node['execution']['agent_role'] in snapshot['allowed_agent_roles']
        and snapshot['execution'].get('side_effect') is True
        and call.data.get('side_effect') is True
        and call.data.get('inputs') == job.data['inputs']
        and call.data.get('approval') == make_approval(task,job,node,job.data['inputs']))
    validate_manifest(snapshot)


def _verify_reservations(db, p, task):
    if _context(task) is None:
        if 'graph_runtime' in task.data:
            _need(task.workspace_id == p.workspace_id)
            # A complete Task-marker deletion cannot turn a once-confirmed
            # zero-call/stopped goal into an unrestricted legacy graph.
            defined = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
                Audit.object_id == task.id, Audit.action.in_(
                    ['native_goal.defined', 'workflow.graph.goal_reserved'])).limit(1))
            original_job = db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id,
                Job.data['task_id'].as_string() == task.id,
                Job.kind == 'check_native_business_goal').limit(1))
            _need(defined is None and original_job is None)
        return
    _need(task.workspace_id == p.workspace_id)
    value = _ledger(task); items = value['items']
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'workflow.graph.goal_reserved')))
    _need(len(rows) == len(items))
    jobs = {row.id: row for row in db.scalars(select(Job).where(
        Job.workspace_id == p.workspace_id, Job.kind == 'execute_full_semantic_graph_node',
        Job.data['task_id'].as_string() == task.id))}
    audits = {}
    for row in rows:
        data = row.data
        _need(type(data) is dict and set(data) == {'job_id', 'activation_id', 'reservation_hash'}
            and type(data['job_id']) is str and data['job_id'] not in audits)
        audits[data['job_id']] = row
    transports = {}
    for activation_id, current in task.data.get('graph_jobs', {}).items():
        # retry() preserves a flat list of every prior attempt. Its entries
        # also carry their old lists, which must not multiply-count attempts.
        _need(type(current) is dict and type(current.get('previous', [])) is list)
        for transport in [*current.get('previous', []), current]:
            _need(type(transport) is dict and type(transport.get('job_id')) is str
                and transport['job_id'] not in transports)
            transports[transport['job_id']] = (activation_id, transport)
    release = db.get(InstanceRelease, task.data['instance_release_id'])
    _need(release is not None and release.workspace_id == p.workspace_id)
    business = next((row for row in release.data['snapshot']['businesses']
        if row['id'] == task.data['business_id']), None)
    _need(business is not None)
    from .workflow_graph import graph_for
    graph = graph_for(release, business)
    nodes = {node['id']: node for node in graph['nodes']}

    def checked_job(job):
        data = job.data; activation = task.data['graph_runtime']['activations'].get(data.get('activation_id'))
        _need(job.id in transports and type(data) is dict and activation is not None)
        node = nodes.get(activation['node_id']); identity, transport = transports[job.id]
        _need(node is not None and node['kind'] == 'ability'
            and job.kind == 'execute_full_semantic_graph_node' and job.workspace_id == p.workspace_id
            and data.get('task_id') == task.id and identity == data['activation_id']
            and data.get('actor_id') == node['actor_id']
            and data.get('graph_hash') == digest(graph) == task.data['graph_runtime']['graph_hash']
            and _int(data.get('attempt'), 1) and _int(data.get('cancel_epoch'))
            and data['cancel_epoch'] == activation['epoch']
            and data.get('input_hash') == digest(data.get('inputs'))
            and data.get('invocation_key') == 'graph:' + task.id + ':' + identity + ':' + str(data['attempt'])
            and job.idempotency_key == data['invocation_key']
            and all(transport.get(key) == data[key]
                for key in ('invocation_key', 'input_hash', 'cancel_epoch', 'attempt')))
        return node, transport

    for key, item in items.items():
        job = jobs.get(item['job_id']); entry = audits.get(item['job_id'])
        _need(job is not None and entry is not None)
        checked_job(job)
        _need(entry.actor_id == item['actor_id'] and entry.data == {
            'job_id': job.id, 'activation_id': item['activation_id'], 'reservation_hash': item['reservation_hash']}
            and all(job.data.get(field) == item[field] for field in (
                'invocation_key', 'activation_id', 'actor_id', 'graph_hash', 'input_hash', 'cancel_epoch', 'attempt')))
    # Reverse lookup catches an erased ledger/audit even when the Task still
    # carries a plausible self-hash. Old completed attempts are not re-signed.
    calls = list(db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        or_(Invocation.idempotency_key.like('graph:' + task.id + ':%'),
            Invocation.data['job_id'].as_string().in_(jobs)))))
    receipts = task.data.get('workflow_runtime', {}).get('receipts', [])
    _need(type(receipts) is list)
    called_jobs = set()
    for call in calls:
        job = jobs.get(call.data.get('job_id')); _need(job is not None)
        node, transport = checked_job(job); called_jobs.add(job.id)
        if call.idempotency_key != job.data['invocation_key']:
            from .workflow_registered_tool_plan_v201 import checked_child_call
            child = checked_child_call(db,p,task,call)
            _need(child['job'].id == job.id
                and child['parent_invocation'].idempotency_key == job.data['invocation_key']
                and child['request_key'] == call.idempotency_key
                and child['input_hash'] == call.data.get('input_hash'))
            continue
        owner = transport.get('dispatch_ownership')
        _need(type(owner) is dict and owner.get('job_id') == job.id and type(owner.get('lease_token')) is str
            and owner['lease_token'] and call.idempotency_key == job.data['invocation_key']
            and call.capability_id == node['execution']['capability_id']
            and call.data.get('capability_hash') == node['execution']['capability_hash']
            and call.data.get('role') == node['execution']['agent_role']
            and call.data.get('input_hash') == job.data['input_hash'] == transport.get('dispatch_input_hash')
            and call.data.get('lease_token') == owner['lease_token'])
        _node_call_history(task,job,node,call)
        if (node['execution']['adapter'] == 'workflow_service'
                and call.data['capability_snapshot']['execution'].get('operation') == 'execute_registered_tool_plan'
                and call.status == 'succeeded'):
            from .workflow_registered_tool_plan_v201 import saved_receipt
            saved_receipt(db,p,call.data['capability_snapshot'],job.data['inputs'],
                call.idempotency_key,call.data['approval'],call)
        if call.idempotency_key not in items:
            original = [row for row in receipts if row.get('request_id') == job.id]
            _need(len(original) == 1 and call.status == 'succeeded')
            receipt = original[0]
            _need(receipt.get('execution_mode') == 'registered_graph_ability'
                and receipt.get('activation_id') == job.data['activation_id']
                and receipt.get('actor') == job.data['actor_id'] and receipt.get('invocation_id') == call.id
                and receipt.get('capability_id') == call.capability_id
                and receipt.get('capability_hash') == call.data['capability_hash']
                and receipt.get('input_hash') == call.data['input_hash'] == digest(receipt.get('input_snapshot'))
                and receipt.get('output') == call.data.get('output'))
    # A committed preflight with no Invocation is still a spent reservation,
    # not a missing-key invitation to start another attempt.
    dispatched = db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'workflow.graph.dispatch'))
    for entry in dispatched:
        job = jobs.get(entry.data.get('job_id')); _need(job is not None)
        checked_job(job)
        _need(entry.actor_id == job.data['actor_id']
            and entry.data.get('activation_id') == job.data['activation_id']
            and (job.data['invocation_key'] in items or job.id in called_jobs))


def uninvoked_calls(db, p, task):
    items = reservations(task)
    if not items:
        return 0
    _need(task.workspace_id == p.workspace_id)
    keys = {item['invocation_key'] for item in items}
    actual = set(db.scalars(select(Invocation.idempotency_key).where(
        Invocation.workspace_id == p.workspace_id, Invocation.idempotency_key.in_(keys))))
    return len(keys - actual)


def pending_steps(task):
    """Reserved graph attempts not already counted by workflow receipts.

    Called by native_step_execution.step_count as well as graph guards.  Do not
    call that function here: native/query/graph users share this same inventory.
    """
    items = reservations(task)
    if not items:
        return 0
    receipts = task.data.get('workflow_runtime', {}).get('receipts', [])
    _need(type(receipts) is list)
    pending = 0
    for item in items:
        matches = [row for row in receipts if row.get('request_id') == item['job_id']]
        _need(len(matches) <= 1)
        if matches:
            row = matches[0]
            _need(row.get('execution_mode') == 'registered_graph_ability'
                and row.get('activation_id') == item['activation_id']
                and row.get('actor') == item['actor_id'] and row.get('input_hash') == item['input_hash'])
        else:
            pending += 1
    return pending


def _usage(db, p, task):
    from .goal_queries import call_count
    from .native_goals import total_steps
    receipts = task.data['workflow_runtime']['receipts']
    _need(type(receipts) is list)
    return call_count(db, p, task), total_steps(db, p, task)


def progress(db, p, task):
    """Time/stop/current authority gate; no reservation, IO, or state change."""
    if _context(task) is None:
        verify_reservations(db, p, task)
        return False
    from .native_goals import guard
    guard(db, p, task)
    _ledger(task)
    return True


def human_step(db, p, task, activation_id):
    if not progress(db, p, task):
        return
    _, steps = _usage(db, p, task)
    from .workflow_graph_human_budget import authorize
    allowance = authorize(db, p, task, activation_id, steps=steps)
    if steps >= task.data['goal_spec']['max_steps'] and allowance is None:
        raise HTTPException(409, '原目标累计步骤预算已用尽；处理草稿与原回执仍可保存和读取')
    return allowance


def new_attempt(db, p, task):
    """A new explicit repeat-read command is not historical nonce recovery."""
    if not progress(db, p, task):
        return
    calls, steps = _usage(db, p, task); spec = task.data['goal_spec']
    if calls >= spec['max_tool_calls'] or steps >= spec['max_steps']:
        raise HTTPException(409, '原目标累计调用或步骤预算已用尽，未安排新的执行尝试')


def _expected(task, job, node):
    data = job.data
    item = {'invocation_key': data['invocation_key'], 'job_id': job.id,
        'activation_id': data['activation_id'], 'actor_id': node['actor_id'],
        'graph_hash': data['graph_hash'], 'input_hash': data['input_hash'],
        'cancel_epoch': data['cancel_epoch'], 'attempt': data['attempt'],
        'goal_context_hash': task.data['native_goal_context_hash'], 'goal_hash': task.data['goal_hash']}
    graph = task.data['graph_runtime']; activation = graph['activations'].get(data['activation_id'])
    transport = task.data.get('graph_jobs', {}).get(data['activation_id'])
    _need(job.workspace_id == task.workspace_id and job.kind == 'execute_full_semantic_graph_node'
        and job.status == 'running' and node['kind'] == 'ability'
        and data['task_id'] == task.id and data['actor_id'] == node['actor_id']
        and data['input_hash'] == digest(data['inputs'])
        and data['graph_hash'] == graph['graph_hash'] and data['cancel_epoch'] == graph['cancel_epoch']
        and activation is not None and activation['node_id'] == node['id']
        and activation['epoch'] == data['cancel_epoch']
        and transport is not None and transport['job_id'] == job.id
        and transport['invocation_key'] == data['invocation_key']
        and transport['input_hash'] == data['input_hash'] and transport['attempt'] == data['attempt']
        and data['invocation_key'] == 'graph:' + task.id + ':' + data['activation_id'] + ':' + str(data['attempt'])
        and job.idempotency_key == data['invocation_key'])
    item['reservation_hash'] = digest(item)
    return item


def reserve(db, p, task, job, node):
    """The caller holds Task FOR UPDATE, before modality preparation/gateway."""
    if not progress(db, p, task):
        return
    value = _ledger(task); expected = _expected(task, job, node)
    _need(node['actor_id'] == p.user_id)
    key = expected['invocation_key']; prior = value['items'].get(key)
    calls, steps = _usage(db, p, task); spec = task.data['goal_spec']
    if prior is not None:
        _need(prior == expected)
        if calls > spec['max_tool_calls'] or steps > spec['max_steps']:
            raise HTTPException(409, '原目标累计调用或步骤预算已用尽，原执行保留')
        return
    if calls >= spec['max_tool_calls'] or steps >= spec['max_steps']:
        raise HTTPException(409, '原目标累计调用或步骤预算已用尽，未分派新调用')
    value['items'][key] = expected
    value.pop('ledger_hash', None); value['ledger_hash'] = digest(value)
    task.data = {**task.data, KEY: value}; task.version += 1
    audit(db, p, 'workflow.graph.goal_reserved', task.id, {'job_id': job.id,
        'activation_id': expected['activation_id'], 'reservation_hash': expected['reservation_hash']})


def before_dispatch(db, p, task, job, node):
    """A running Invocation cannot create or replace a missing reservation."""
    if not progress(db, p, task):
        return
    value = _ledger(task); expected = _expected(task, job, node)
    _need(value['items'].get(expected['invocation_key']) == expected and node['actor_id'] == p.user_id)
    calls, steps = _usage(db, p, task); spec = task.data['goal_spec']
    if calls > spec['max_tool_calls'] or steps > spec['max_steps']:
        raise HTTPException(409, '原目标累计调用或步骤预算已用尽，未执行真实能力')


def result_step(db, p, task, job, node):
    """Saved success may be read; only a still-authorized goal can advance."""
    if not progress(db, p, task):
        return
    value = _ledger(task); expected = _expected(task, job, node)
    prior = value['items'].get(expected['invocation_key'])
    if prior is not None:
        _need(prior == expected)
    calls, steps = _usage(db, p, task); spec = task.data['goal_spec']
    # Pre-V133 successful calls have no reservation. They need one free result
    # step, but never a new call; their original dispatch proof stays untouched.
    if calls > spec['max_tool_calls'] or steps + int(prior is None) > spec['max_steps']:
        raise HTTPException(409, '原目标累计调用或步骤预算已用尽；原成功调用只保留为历史')
