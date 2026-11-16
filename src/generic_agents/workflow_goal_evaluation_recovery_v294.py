"""Retained original evaluation query recovery; never an unknown-call replay.

Only the fixed V288 local operation may finish its own independently saved Qe.
The normal graph owns scheduling, its current lease, SDK status and completion.
No SDK, query, cut, old audit, device request or cloud call is replaced here.
"""
import copy
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from .contracts import digest
from .models import Audit, Capability, DataGap, Evidence, Event, Invocation, Job, Memory
from .temporal import instant
from . import goal_evaluation_query_v288 as queries
from . import workflow_goal_evaluation_packet_v288 as ports
from . import workflow_service_v195 as service
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288

TERMINAL = 'workflow.goal_evaluation.retained_terminal_v294'
SCHEMA = 'retained-goal-evaluation-terminal-v294'
MAX_ROWS = 4096
ERROR = '原评价尚无完整可恢复的独立查询证明；不重跑查询、不更换原号或固定时点'


def need(value):
    if not value: raise HTTPException(409, ERROR)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try: return function(*args, **kwargs)
        except HTTPException: raise
        except (KeyError, TypeError, ValueError, AttributeError, IndexError,
                StopIteration, UnicodeError, RecursionError, OverflowError):
            need(False)
    return wrapped


def applies(call):
    try:
        return (call is not None and type(call.data) is dict
            and call.data['capability_snapshot']['execution'] == {
                'adapter': 'workflow_service', 'service_actor': 'reader_service',
                'operation': ports.OP, 'side_effect': True})
    except (KeyError, TypeError, AttributeError): return False


def _rows(db, p, model, *conditions):
    rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id,
        *conditions).limit(MAX_ROWS + 1).execution_options(populate_existing=True)))
    need(len(rows) <= MAX_ROWS)
    return rows


def _audit(db, p, action, object_id, actor_id, expected=None):
    rows = _rows(db, p, Audit, Audit.action == action, Audit.object_id == object_id)
    need(len(rows) == 1 and rows[0].actor_id == actor_id
        and (expected is None or digest(rows[0].data) == digest(expected)))
    return rows[0]


def _base(db, p, task, job, call):
    from . import workflow_graph as runtime, workflow_graph_scope as scope
    from . import workflow_goal_evaluation_store_v288 as store
    queries._reader(db, p)
    actual, release, business, graph, state, activation, node = scope._frozen(
        db, p, task.id, job.data['activation_id'])
    job = queries._fresh(db, p, Job, job.id); call = queries._fresh(db, p, Invocation, call.id)
    cap = queries._fresh(db, p, Capability, call.capability_id)
    transport = scope._job_matches(actual, job, state, activation, node)
    binding = store._binding(actual, release, business, graph, node)
    ports.validate_manifest(cap.data)
    inputs = store._inputs(job.data['inputs']); approval = service.make_approval(actual, job, node, inputs)
    need(job.kind == 'execute_full_semantic_graph_node' and applies(call)
        and actual.id == task.id and job.data['task_id'] == actual.id
        and job.data['actor_id'] == node['actor_id'] and call.workspace_id == p.workspace_id
        and cap.content_hash == cap.validated_hash == digest(cap.data)
        == node['execution']['capability_hash']
        and cap.release_version == node['execution']['release_version']
        and digest(call.data['capability_snapshot']) == cap.content_hash
        and digest(call.data['inputs']) == digest(inputs) == call.data['input_hash'] == job.data['input_hash']
        and digest(activation['inputs_snapshot']['arguments']) == digest(inputs)
        and call.idempotency_key == job.idempotency_key == job.data['invocation_key']
        and digest(call.data['approval']) == digest(approval))
    owner = transport.get('dispatch_ownership'); authorization = transport.get('dispatch_authorization')
    need(type(owner) is dict and set(owner) == {'job_id', 'lease_token'}
        and owner['job_id'] == job.id and bool(owner['lease_token'])
        and type(authorization) is dict and authorization['job_id'] == job.id
        and authorization['lease_token'] == owner['lease_token']
        and authorization['actor_id'] == node['actor_id']
        and authorization['epoch'] == job.data['cancel_epoch']
        and authorization['input_hash'] == digest(inputs)
        and authorization['capability_hash'] == cap.content_hash
        and transport.get('dispatch_committed') is True)
    runtime.check_invocation(call, p, cap, node, inputs, job, owner, approval=approval)
    initial = _audit(db, p, 'capability.invoke', call.id, node['actor_id'], {'status': 'unknown'})
    authorized = _rows(db, p, Audit, Audit.action == 'workflow.graph.dispatch_authorized',
        Audit.object_id == actual.id, or_(Audit.data['job_id'].as_string() == job.id,
            Audit.data['activation_id'].as_string() == activation['id']))
    need(len(authorized) == 1 and authorized[0].actor_id == node['actor_id']
        and authorized[0].data == {'activation_id': activation['id'], 'job_id': job.id,
            'epoch': activation['epoch'], 'input_hash': digest(inputs)})
    return {'task': actual, 'job': job, 'call': call, 'cap': cap, 'node': node,
        'release': release, 'business': business, 'binding': binding,
        'inputs': inputs, 'approval': approval, 'initial': initial}


def _effects(db, p, context, retained):
    """Positive Qe/SAVED authority must also have a complete empty later phase."""
    from . import workflow_goal_evaluation_store_v288 as store
    task, job, call = context['task'], context['job'], context['call']
    query = retained['row']; marker = retained['marker']
    start = instant(marker['cut_checked_at']); end = context['initial'].created_at
    need(end is not None and end.tzinfo is not None and start <= end)
    need(store.KEY not in call.data and not any(key in call.data for key in store.FOREIGN))
    inventories = [
        _rows(db, p, GoalEvaluationV288, or_(GoalEvaluationV288.id == call.id,
            GoalEvaluationV288.invocation_id == call.id, GoalEvaluationV288.request_id == job.id,
            GoalEvaluationV288.request_key == call.idempotency_key,
            GoalEvaluationV288.evaluation_query_id == query.id)),
        _rows(db, p, Audit, Audit.action == store.CREATED, or_(Audit.object_id == call.id,
            Audit.data['journal']['invocation_id'].as_string() == call.id,
            Audit.data['journal']['job_id'].as_string() == job.id)),
        _rows(db, p, Job, or_(Job.data['goal_evaluation_ref_v288']['id'].as_string() == call.id,
            Job.data['goal_evaluation_producer_job_v288'].as_string() == job.id,
            Job.idempotency_key.endswith(':goal-evaluation-v288:' + call.id, autoescape=True))),
        _rows(db, p, DataGap, DataGap.query_id == query.id),
        _rows(db, p, Event, or_(Event.data['evaluation_id'].as_string() == call.id,
            Event.data['query_id'].as_string() == query.id)),
        _rows(db, p, Memory, or_(Memory.data['evaluation_id'].as_string() == call.id,
            Memory.data['query_id'].as_string() == query.id)),
        # save_evidence flushes its row before returning. The protected saved
        # cut and original unknown Audit bound the original execution window;
        # unrelated later uploads do not permanently disable recovery.
        _rows(db, p, Evidence, Evidence.created_at >= start, Evidence.created_at <= end),
        _rows(db, p, Audit, Audit.action == 'evidence.upload', Audit.created_at >= start,
            Audit.created_at <= end),
        _rows(db, p, Audit, Audit.action == TERMINAL, Audit.object_id == call.id),
    ]
    need(all(not rows for rows in inventories))
    calls = _rows(db, p, Invocation, or_(Invocation.idempotency_key == call.idempotency_key,
        Invocation.data['job_id'].as_string() == job.id))
    need(len(calls) == 1 and calls[0].id == call.id)


def _retained(db, p, context):
    from . import workflow_goal_evaluation_store_v288 as store
    from . import workflow_goal_evaluation_query_repair_v296 as repair
    need(context['call'].status == 'unknown')
    upstream = store._upstream(db, p, context['task'], context['job'], context['inputs'])
    retained = repair.inspect_retained(db, p, context['task'], context['job'], context['call'],
        context['cap'], context['inputs'], context['approval'], upstream)
    _effects(db, p, context, retained)
    return retained, upstream


@checked
def retained_query_authority(db, p, task, job, call):
    if not applies(call): return False
    _retained(db, p, _base(db, p, task, job, call))
    return True


def _fence(db, p, context):
    from .leases import fenced, ownership
    owner = ownership(db); job = context['job']
    need(fenced(db) and job.status == 'running' and context['node']['actor_id'] == p.user_id
        and owner == {'job_id': job.id, 'lease_token': job.lease_token}
        and bool(job.lease_token))
    # This must not flush a pending success before the actual lease is proven.
    with db.no_autoflush:
        actual = db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id,
            Job.id == job.id, Job.kind == 'execute_full_semantic_graph_node',
            Job.status == 'running', Job.lease_token == owner['lease_token'],
            Job.lease_until > func.clock_timestamp(), Job.data['actor_id'].as_string() == p.user_id))
    need(actual == job.id)


@checked
def resume(db, p, current, call, cap, inputs, approval):
    """Finish the original Qe only under the normal current graph worker."""
    from . import workflow_goal_evaluation_store_v288 as store
    from . import workflow_goal_evaluation_query_repair_v296 as repair
    need(call is not None and getattr(call, 'status', None) == 'unknown' and applies(call))
    context = _base(db, p, current['task'], current['job'], call)
    need(cap.id == context['cap'].id and digest(inputs) == digest(context['inputs'])
        and digest(approval) == digest(context['approval']))
    _fence(db, p, context)
    retained, upstream = _retained(db, p, context)
    with db.begin_nested():
        frozen = repair.restore_result(db, p, context['task'], context['job'], context['call'],
            context['cap'], context['inputs'], context['approval'], upstream)
        need(frozen['query_ref'] == retained['audit'].data['query_ref'])
        output = store.materialize_frozen_v294(db, p, current, context['call'],
            context['cap'], context['inputs'], context['approval'], frozen)
        ports.checked_result(output)
        _fence(db, p, context)
    return copy.deepcopy(output)


def _terminal_value(db, p, context):
    from . import workflow_goal_evaluation_store_v288 as store
    call, job, cap, task = context['call'], context['job'], context['cap'], context['task']
    need(call.status == 'succeeded' and call.data.get('service_previous_status') == 'unknown')
    journal = call.data[store.KEY]
    need(journal['schema'] == store.STATE and journal['journal_hash'] == store.hashed({
        key: value for key, value in journal.items() if key != 'journal_hash'})
        and journal['task_id'] == task.id and journal['job_id'] == job.id and journal['invocation_id'] == call.id
        and journal['capability_id'] == cap.id and journal['capability_hash'] == cap.content_hash
        and journal['input_hash'] == digest(context['inputs'])
        and journal['approval_hash'] == digest(context['approval']))
    _audit(db, p, store.CREATED, call.id, context['node']['actor_id'], store._anchor(journal))
    row = queries._fresh(db, p, GoalEvaluationV288, call.id)
    frozen = queries.checked_frozen(db, p, task, row.evaluation_query_id, evaluation_id=call.id)
    from . import workflow_goal_evaluation_contracts_v288 as contract
    from .workflow_goal_evaluation_models_v288 import row_values
    record = contract.check_record(row.data, expected_scope=frozen['provenance']['scope'],
        goal_snapshot=frozen['provenance']['goal_snapshot'],
        preparation_policy=frozen['provenance']['preparation_policy'], trusted_now=row.data['cut_checked_at'])
    need(all(key == 'data' or getattr(row, key) == value for key, value in row_values(record).items())
        and row.record_hash == journal['evaluation_record_hash']
        and record['evaluation_query_ref'] == frozen['query_ref']
        and record['request_id'] == job.id and record['request_key'] == call.idempotency_key
        and record['producer']['invocation_id'] == call.id
        and record['producer']['lease_token'] == call.data['lease_token'])
    output = store._output(row)
    need(digest(output) == digest(call.data['output']) == digest(journal['output']))
    from .evidence import evidence_bytes
    evidence, content = evidence_bytes(db, p, row.evidence_id)
    need(content == store._document(task, record) and evidence.sha256 == record['evidence_ref']['sha256']
        and evidence.size_bytes == record['evidence_ref']['size_bytes'] and evidence.media_type == 'text/plain')
    # Consent can legitimately change later. The immutable upload Audit proves
    # generation had no cloud grant; current consent is not a result hash.
    _audit(db, p, 'evidence.upload', evidence.id, context['node']['actor_id'],
        {'media_type': 'text/plain', 'sha256': record['evidence_ref']['sha256'],
            'cloud_model_id': None, 'consent': False})
    value = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'job_id': job.id, 'invocation_id': call.id, 'actor_id': context['node']['actor_id'],
        'activation_id': job.data['activation_id'], 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'input_hash': digest(context['inputs']), 'approval_hash': digest(context['approval']),
        'source_binding_hash': context['binding']['binding_hash'],
        'initial_unknown_audit_id': context['initial'].id, 'query_ref': frozen['query_ref'],
        'evaluation_record_hash': row.record_hash, 'journal_hash': journal['journal_hash'], 'output_hash': digest(output)}
    return {**value, 'record_hash': digest(value)}


@checked
def record_terminal_parent(db, p, task, job, call, cap, node, binding, inputs, approval):
    if not applies(call): return False
    need(job.workspace_id == task.workspace_id == call.workspace_id == cap.workspace_id == p.workspace_id
        and call.capability_id == cap.id and job.data['actor_id'] == node['actor_id'] == p.user_id)
    _fence(db, p, {'job': job, 'node': node})
    # Normal reconcile has just assigned succeeded/output and the independent
    # receipt Audit. Flush inside the existing transaction before any refresh
    # can discard those dirty values; never commit or manufacture a status.
    db.flush()
    context = _base(db, p, task, job, call); _fence(db, p, context)
    need(context['node'] == node and digest(context['binding']) == digest(binding)
        and context['cap'].id == cap.id and digest(inputs) == digest(context['inputs'])
        and digest(approval) == digest(context['approval']))
    value = _terminal_value(db, p, context)
    need(not _rows(db, p, Audit, Audit.action == TERMINAL, Audit.object_id == call.id))
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=TERMINAL,
        object_id=call.id, data=value))
    return True


@checked
def verify_terminal_parent(db, p, task, job, call, cap, receipt, release, business, node):
    need(applies(call))
    context = _base(db, p, task, job, call)
    need(context['cap'].id == cap.id and context['node'] == node
        and digest(receipt['output']) == digest(call.data['output']))
    value = _terminal_value(db, p, context)
    _audit(db, p, TERMINAL, call.id, context['node']['actor_id'], value)
    _audit(db, p, 'workflow.service.receipt_verified', call.id, context['node']['actor_id'],
        {'job_id': job.id, 'activation_id': job.data['activation_id']})
