"""Server-only future workflow barriers; no SDK input or old-state migration.

The barrier delays claiming, not source validation or independent reception.
Only actual kernel trace events propagate graph control frontiers. No source
values enter these records, and no Job/SDK is invented for a human or wait.
"""
import copy
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import aliased

from .contracts import digest
from .models import Audit, Capability, Invocation, Job, Task

KEY = 'workflow_job_dependencies_v264'
REFS = 'workflow_job_dependency_refs_v264'
SCHEMA = 'workflow-job-dependencies-v264'
BOUND = 'workflow.job_dependencies_bound_v264'
CONTROL = 'workflow_job_control_frontier_v264'
CONTROL_SCHEMA = 'workflow-control-frontier-v264'
CONTROL_AUDIT = 'workflow.control_frontier_v264'
ENROLL = 'workflow_compact_enrollment_v264'
ENROLLED = 'workflow.compact_enrolled_v264'
ENROLL_SCHEMA = 'workflow-compact-enrollment-v264'
KINDS = {'execute_workflow_step', 'execute_full_semantic_graph_node'}
MAX_PARENTS = 128
MAX_ACTIVATIONS = 4096
MAX_TRACE = MAX_ACTIVATIONS * 8
MAX_CONTROLS = MAX_TRACE
ERROR = '前序后台任务与真实流程完成依据无法核对；原成果保留，未执行后继能力'
SERVICE_TERMINAL = 'workflow.service.terminal_parent_verified_v293'
SERVICE_TERMINAL_SCHEMA = 'workflow-service-terminal-parent-proof-v293'
PENDING = '上一步仍在保存完成记录，原成果保留，后继任务继续排队'
FAILED = '上一步未成功终结，原成果与失败记录保留；后继能力未执行，请核查原任务'


def need(value, message=ERROR):
    if not value: raise HTTPException(409, message)


def _uuid(value):
    try: return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError): return False


def _seal(value): return {**copy.deepcopy(value), 'record_hash': digest(value)}


def _row(db, p, cls, identity):
    row = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
        .execution_options(populate_existing=True))
    need(row is not None)
    return row


def _proof(db, p, action, object_id, actor_id, expected):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == object_id).limit(2)))
    need(len(rows) == 1 and rows[0].actor_id == actor_id and rows[0].data == expected)
    return rows[0]


def _receipt(db, p, task, job_id):
    rows = [r for r in task.data['workflow_runtime']['receipts'] if r.get('request_id') == job_id
        and r.get('execution_mode') in {'registered_ability', 'registered_graph_ability'}]
    need(len(rows) == 1)
    return rows[0]


def _terminal_service_family(binding, node, cap):
    execution = cap.data.get('execution', {})
    service = (node.get('kind') == 'ability' and execution == {
        'adapter': 'workflow_service', 'service_actor': 'execution_service',
        'operation': 'resense_from_context', 'side_effect': True})
    if not service:
        return False
    if binding.get('schema') == 'business-flow-nested-execution-binding-v195':
        from .business_flow_nested_adoption_v195 import service_delegations
        from .business_flow_nested_execution_v195 import service_delegation
        from .workflow_service_context_v196 import OP
        spec = node.get('execution', {})
        if not (binding.get('simulation_only') is True
                and binding.get('capability_pins', {}).get(node.get('id')) == spec
                and spec.get('capability_id') == cap.id
                and spec.get('capability_hash') == cap.content_hash
                and spec.get('release_version') == cap.release_version
                and spec.get('agent_role') == 'business'
                and spec.get('adapter') == 'workflow_service'):
            return False
        return service_delegations(binding['translation']).get(node['id']) == service_delegation(
            'execution_service', OP)
    return (binding.get('schema') in {'authored-service-workflow-binding-v270',
        'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286',
        'authored-goal-data-workflow-binding-v288'} and node.get('id') == 'resense')


def _terminal_service_value(task, job, call, cap, node, binding, initial):
    from . import workflow_service_v195 as service, workflow_service_context_v196 as variant
    variant.validate_manifest(cap.data)
    state = call.data.get('service_state'); output = call.data.get('output')
    need(type(state) is dict and set(state) == service.STATE_KEYS and state['schema'] == service.STATE
        and state['operation'] == variant.OP and state['state_hash'] == digest({
            key: item for key, item in state.items() if key != 'state_hash'})
        and call.status == 'succeeded' and call.data.get('service_previous_status') == 'waiting_receipt'
        and type(call.data.get('service_receipt_verified_at')) is str
        and bool(call.data['service_receipt_verified_at']) and type(output) is dict)
    # All wire identities are separately checked by service._state at writing
    # and saved_receipt at reading. Hash only these actual original values.
    need(state['task_id'] == task.id and state['job_id'] == job.id
        and state['activation_id'] == job.data['activation_id']
        and state['actor_id'] == node['actor_id'] == job.data['actor_id']
        and state['capability_id'] == cap.id and state['capability_hash'] == cap.content_hash
        and state['input_hash'] == job.data['input_hash'] == call.data['input_hash']
        and state['approval_hash'] == digest(call.data['approval'])
        and state['source_binding_hash'] == binding['binding_hash'] and state['invocation_id'] == call.id
        and state['request_key'] == call.idempotency_key == job.idempotency_key)
    source = call.data.get(variant.PROOF_KEY)
    need(type(source) is dict and source.get('proof_hash') == digest({
        key: item for key, item in source.items() if key != 'proof_hash'}))
    value = {'schema': SERVICE_TERMINAL_SCHEMA, 'workspace_id': task.workspace_id, 'task_id': task.id,
        'job_id': job.id, 'activation_id': job.data['activation_id'], 'invocation_id': call.id,
        'actor_id': node['actor_id'], 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'release_version': cap.release_version, 'input_hash': job.data['input_hash'],
        'approval_hash': digest(call.data['approval']), 'source_binding_hash': binding['binding_hash'],
        'initial_invoke_audit_id': initial.id, 'initial_status': 'waiting_receipt',
        'state_hash': state['state_hash'], 'output_hash': digest(output),
        'source_proof_hash': source['proof_hash'],
        'output_schema_hash': digest(cap.data['output_schema']),
        'verified_at': call.data['service_receipt_verified_at']}
    return {**value, 'record_hash': digest(value)}


def record_terminal_service_parent(db, p, task, job, call, cap, node, binding, inputs, approval):
    """Normal reconcile writer only; never backfill a historical parent proof."""
    if not _terminal_service_family(binding, node, cap): return False
    from .leases import fenced, ownership
    from . import workflow_service_v195 as service
    lease = ownership(db)
    need(fenced(db) and job.kind == 'execute_full_semantic_graph_node' and job.status == 'running'
        and job.workspace_id == task.workspace_id == call.workspace_id == cap.workspace_id == p.workspace_id
        and job.data['actor_id'] == node['actor_id'] == p.user_id and lease.get('job_id') == job.id
        and lease.get('lease_token') == job.lease_token and bool(job.lease_token)
        and job.lease_until > db.scalar(select(func.clock_timestamp()))
        and inputs == job.data['inputs'] == call.data['inputs']
        and approval == call.data['approval'] == service.make_approval(task, job, node, inputs))
    initial = _proof(db, p, 'capability.invoke', call.id, p.user_id, {'status': 'waiting_receipt'})
    context = {'task': task, 'job': job, 'node': node, 'capability': cap, 'binding': binding,
        'delegation': {'service_actor': 'execution_service', 'operation': 'resense_from_context', 'agent_role': 'business'}}
    need(service._state(db, p, context, call, inputs, call.idempotency_key, approval) is not None)
    from .workflow_resense_goal_receipts_v271 import _source_proof
    _source_proof(db, p, context, call, inputs, job.idempotency_key, approval)
    if binding['schema'] in {'authored-service-workflow-binding-v270', 'authored-goal-data-workflow-binding-v288'}:
        _terminal_reader_context_v288(db, p, task, inputs['context'])
    value = _terminal_service_value(task, job, call, cap, node, binding, initial)
    need(not list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == SERVICE_TERMINAL, Audit.object_id == call.id).limit(1))))
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=SERVICE_TERMINAL,
        object_id=call.id, data=value))
    return True


def _terminal_reader_context_v288(db, p, task, value):
    """Reuse the exact builtin producer proof also used by later Eval receipts."""
    from .workflow_resense_goal_receipts_v271 import checked_builtin_producer
    return checked_builtin_producer(db, p, task, value)


def _terminal_service_receipt(db, p, task, job, call, cap, node, binding, release):
    """Immutable reconcile event proof, never current inventory or a grant.

    The fixed real reconcile verified complete Source/producer/ACK inventory
    before its fenced terminal hash Audit was written. Read that original
    result and provenance without re-running resolved-Gap/predispatch guards.
    parent_pin does not declare a handler terminal: require_ready still checks
    the actual parent Job success independently before every child claim.
    """
    from . import workflow_graph as workflow, workflow_service_v195 as service
    from . import workflow_service_context_v196 as variant, workflow_resense_goal_receipts_v271 as receipts
    from .services import validate
    receipts._reader_iam(db, p)
    inputs = job.data['inputs']; approval = service.make_approval(task, job, node, inputs)
    need(call.data.get('approval') == approval and call.data.get('inputs') == inputs)
    variant.validate_manifest(cap.data); variant._inputs(inputs)
    transport = task.data['graph_jobs'][job.data['activation_id']]
    owner, authorized = transport.get('dispatch_ownership'), transport.get('dispatch_authorization')
    need(type(owner) is dict and set(owner) == {'job_id', 'lease_token'}
        and owner['job_id'] == job.id and type(owner['lease_token']) is str and bool(owner['lease_token'])
        and type(authorized) is dict and set(authorized) == {'epoch', 'job_id', 'lease_token',
            'input_hash', 'capability_hash', 'actor_id', 'authorized_at'}
        and authorized['epoch'] == job.data['cancel_epoch'] and authorized['job_id'] == job.id
        and authorized['lease_token'] == owner['lease_token'] and authorized['actor_id'] == node['actor_id']
        and authorized['input_hash'] == digest(inputs) and authorized['capability_hash'] == cap.content_hash
        and transport.get('dispatch_committed') is True and transport.get('phase') == 'succeeded'
        and transport.get('invocation_id') == call.id and transport.get('output_hash') == digest(call.data['output'])
        and transport.get('dispatch_input_hash') == digest(inputs))
    workflow.check_invocation(call, p, cap, node, inputs, job, owner, approval=approval)
    authorized_rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'workflow.graph.dispatch_authorized', Audit.object_id == task.id,
        (Audit.data['job_id'].as_string() == job.id) | (
            Audit.data['activation_id'].as_string() == job.data['activation_id'])).limit(2)))
    need(len(authorized_rows) == 1 and authorized_rows[0].actor_id == node['actor_id']
        and authorized_rows[0].data == {'activation_id': job.data['activation_id'], 'job_id': job.id,
            'epoch': job.data['cancel_epoch'], 'input_hash': digest(inputs)})
    context = {'task': task, 'job': job, 'node': node, 'release': release, 'binding': binding,
        'capability': cap, 'transport': transport, 'delegation': receipts.DELEGATION}
    started = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == service.STARTED, Audit.object_id == call.id).limit(2)))
    state = receipts._state_value(call.data.get('service_state'), context, call, inputs,
        job.idempotency_key, approval, started)
    proof = receipts._source_proof(db, p, context, call, inputs, job.idempotency_key, approval)
    output = copy.deepcopy(call.data['output']); validate(variant.output_schema(), output)
    receipt = output['service_receipt']
    need(receipt['schema'] == variant.RECEIPT and receipt['operation'] == variant.OP
        and receipt['task_id'] == task.id and receipt['invocation_id'] == call.id
        and receipt['state_hash'] == state['state_hash'] and receipt['simulation_only'] is True
        and receipt['context_hash'] == proof['context_hash'] == inputs['context']['context_hash']
        and receipt['producer_invocation_id'] == proof['producer_invocation_id']
        == inputs['context']['producer']['invocation_id']
        and receipt['receipt_hash'] == digest({key: item for key, item in receipt.items() if key != 'receipt_hash'}))
    return output


def _invoke_parent_proof(db, p, task, job, call, cap, receipt, release, business, node=None):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'capability.invoke', Audit.object_id == call.id).limit(2)))
    need(len(rows) == 1 and rows[0].actor_id == receipt['actor'])
    if rows[0].data == {'status': 'succeeded'}: return
    if rows[0].data == {'status': 'unknown'} and node is not None:
        from .workflow_goal_evaluation_recovery_v294 import applies, verify_terminal_parent
        if applies(call):
            verify_terminal_parent(db, p, task, job, call, cap, receipt, release, business, node)
            return
    need(rows[0].data == {'status': 'waiting_receipt'} and job.kind == 'execute_full_semantic_graph_node'
        and node is not None)
    from .workflow_graph import business_binding
    binding = business_binding(release, business)
    need(_terminal_service_family(binding, node, cap))
    expected = _terminal_service_value(task, job, call, cap, node, binding, rows[0])
    _proof(db, p, SERVICE_TERMINAL, call.id, receipt['actor'], expected)
    _proof(db, p, 'workflow.service.receipt_verified', call.id, receipt['actor'],
        {'job_id': job.id, 'activation_id': job.data['activation_id']})
    original = _terminal_service_receipt(db, p, task, job, call, cap, node, binding, release)
    need(digest(original) == expected['output_hash'] == digest(receipt['output']))


def parent_pin(db, p, task, job_id):
    """A completed handler is not declared a completed producer here."""
    try:
        need(task.workspace_id == p.workspace_id and _uuid(job_id))
        receipt = _receipt(db, p, task, job_id)
        job = _row(db, p, Job, job_id); call = _row(db, p, Invocation, receipt['invocation_id'])
        cap = _row(db, p, Capability, receipt['capability_id'])
        need(job.kind in KINDS and job.data.get('task_id') == task.id
            and receipt['actor'] == job.data.get('actor_id')
            and job.idempotency_key == job.data.get('invocation_key') == call.idempotency_key
            and call.status == 'succeeded' and call.capability_id == cap.id
            and call.data.get('job_id') == job.id and call.data.get('capability_hash') == receipt['capability_hash']
            and receipt['capability_hash'] == cap.content_hash == cap.validated_hash == digest(cap.data)
            and receipt['release_version'] == cap.release_version
            and digest(receipt['input_snapshot']) == receipt['input_hash'] == call.data.get('input_hash')
            and digest(call.data.get('output')) == digest(receipt['output']))
        from .workspace_api import saved_execution
        _, release, business = saved_execution(db, p, task.id)
        if job.kind == 'execute_workflow_step':
            need(receipt['execution_mode'] == 'registered_ability' and 'activation_id' not in receipt)
            scope = job.data['scope']; index = task.data['workflow_runtime']['receipts'].index(receipt)
            step = next(s for s in business['flow']['steps'] if s['id'] == receipt['step_id'])
            role = {'业务智能体': 'business', '多模感知智能体': 'perception', '数据管理智能体': 'data'}[step['agent']]
            need(scope['step_id'] == receipt['step_id'] and type(scope['visit']) is int and scope['visit'] == index
                and type(scope['attempt']) is int and scope['attempt'] >= 1
                and job.idempotency_key == 'workflow:' + task.id + ':' + str(index) + ':' + str(scope['attempt'])
                and digest(scope['inputs']) == scope['input_hash'] == receipt['input_hash']
                and step['execution']['capability_id'] == cap.id and step['execution']['capability_hash'] == cap.content_hash
                and step['execution']['release_version'] == cap.release_version
                and receipt['role'] == step['agent'] and call.data.get('role') == role
                and receipt['step_name'] == step['name'] and receipt['output_name'] == step['output'])
            position = business['flow']['steps'].index(step)
            branch = receipt['output'].get(step['execution'].get('branch_field')) if step.get('condition') else None
            need(receipt['branch'] == branch and (not step.get('condition') or type(branch) is bool))
            following = step.get('elseId', 'end') if branch is False else step.get('nextId', 'auto')
            if following == 'auto': following = business['flow']['steps'][position + 1]['id'] if position + 1 < len(business['flow']['steps']) else 'end'
            queued = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
                Audit.action == 'workflow.ability_queued', Audit.object_id == task.id,
                Audit.data['job'].as_string() == job.id).limit(2)))
            completed = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
                Audit.action == 'workflow.ability_completed', Audit.object_id == task.id,
                Audit.data['invocation'].as_string() == call.id).limit(2)))
            need(len(queued) == len(completed) == 1 and queued[0].actor_id == completed[0].actor_id == receipt['actor']
                and queued[0].data == {'step': receipt['step_id'], 'job': job.id, 'attempt': scope['attempt']}
                and completed[0].data == {'step': receipt['step_id'], 'invocation': call.id, 'next': following})
            activation_id = None
        else:
            need(receipt['execution_mode'] == 'registered_graph_ability')
            from .workflow_graph import graph_for, node_for
            state = task.data['graph_runtime']; activation_id = receipt['activation_id']
            activation = state['activations'][activation_id]; node = node_for(graph_for(release, business), activation)
            transport = task.data['graph_jobs'][activation_id]
            need(activation['status'] == 'completed' and node['kind'] == 'ability'
                and job.data['activation_id'] == activation_id and job.data['graph_hash'] == state['graph_hash']
                and job.data['activation_version'] == 0 and type(job.data['attempt']) is int and job.data['attempt'] >= 1
                and job.idempotency_key == 'graph:' + task.id + ':' + activation_id + ':' + str(job.data['attempt'])
                and job.data['cancel_epoch'] == activation['epoch']
                and transport['job_id'] == job.id and transport['invocation_key'] == job.idempotency_key
                and transport['invocation_id'] == call.id and transport['phase'] == 'succeeded'
                and digest(job.data['inputs']) == job.data['input_hash'] == receipt['input_hash']
                and digest(activation['inputs_snapshot']['arguments']) == receipt['input_hash']
                and digest(activation['output']) == digest(receipt['output'])
                and node['actor_id'] == receipt['actor'] and node['execution']['capability_id'] == cap.id
                and receipt['step_id'] == (node['source_step_id'] or node['id'])
                and node['execution']['capability_hash'] == cap.content_hash
                and call.data.get('role') == node['execution']['agent_role'])
            completed = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
                Audit.action == 'workflow.graph.node_completed', Audit.object_id == task.id,
                Audit.data['activation_id'].as_string() == activation_id).limit(2)))
            need(len(completed) == 1 and completed[0].actor_id == receipt['actor']
                and completed[0].data == {'activation_id': activation_id, 'invocation_id': call.id})
        _invoke_parent_proof(db, p, task, job, call, cap, receipt, release, business,
            node if job.kind == 'execute_full_semantic_graph_node' else None)
        return {'job_id': job.id, 'kind': job.kind, 'actor_id': receipt['actor'], 'step_id': receipt['step_id'],
            'activation_id': activation_id, 'invocation_id': call.id, 'capability_id': cap.id,
            'capability_hash': cap.content_hash, 'input_hash': receipt['input_hash'],
            'output_hash': digest(receipt['output']), 'receipt_hash': digest(receipt)}
    except HTTPException: raise
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, IndexError): need(False)


def _child_pin(job):
    inputs = job.data['scope']['inputs'] if job.kind == 'execute_workflow_step' else job.data['inputs']
    return {'child_job_id': job.id, 'child_kind': job.kind, 'child_actor_id': job.data['actor_id'],
        'child_invocation_key': job.data['invocation_key'], 'child_input_hash': digest(inputs)}


def _compact_scope(job):
    scope = job.data['scope']
    need(job.kind == 'execute_workflow_step' and type(scope) is dict
        and type(scope['visit']) is int and scope['visit'] >= 0
        and type(scope['attempt']) is int and 1 <= scope['attempt'] <= 5
        and type(scope['step_id']) is str and bool(scope['step_id'])
        and digest(scope['inputs']) == scope['input_hash']
        and job.idempotency_key == job.data['invocation_key'] == 'workflow:' + job.data['task_id']
            + ':' + str(scope['visit']) + ':' + str(scope['attempt']))
    return {'step_id': scope['step_id'], 'visit': scope['visit'], 'attempt': scope['attempt'],
        'input_hash': scope['input_hash'], 'invocation_key': job.data['invocation_key']}


def checked_enrollment(db, p, task, *, verify_first=True):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ENROLLED, Audit.object_id == task.id).limit(2)))
    value = task.data.get(ENROLL)
    if value is None and not rows: return None
    need(type(value) is dict and set(value) == {'schema','workspace_id','task_id','actor_id','first_job_id','first_scope','record_hash'}
        and value['schema'] == ENROLL_SCHEMA and value['workspace_id'] == p.workspace_id == task.workspace_id
        and value['task_id'] == task.id and _uuid(value['actor_id']) and _uuid(value['first_job_id'])
        and type(value['first_scope']) is dict
        and set(value['first_scope']) == {'step_id','visit','attempt','input_hash','invocation_key'}
        and type(value['first_scope']['visit']) is int and value['first_scope']['visit'] >= 0
        and type(value['first_scope']['attempt']) is int and 1 <= value['first_scope']['attempt'] <= 5
        and value['record_hash'] == digest({k:v for k,v in value.items() if k != 'record_hash'})
        and len(rows) == 1 and rows[0].actor_id == value['actor_id'] and rows[0].data == value)
    if verify_first:
        first = _row(db, p, Job, value['first_job_id'])
        need(first.data['task_id'] == task.id and first.data['actor_id'] == value['actor_id']
            and _compact_scope(first) == value['first_scope'])
        queued = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == task.id, Audit.action == 'workflow.ability_queued',
            Audit.data['job'].as_string() == first.id).limit(2)))
        need(len(queued) == 1 and queued[0].actor_id == value['actor_id']
            and queued[0].data == {'step': value['first_scope']['step_id'], 'job': first.id,
                'attempt': value['first_scope']['attempt']})
    return copy.deepcopy(value)


def _enroll(db, p, task, job):
    existing = checked_enrollment(db, p, task)
    if existing is not None: return existing
    # Do not recreate a lost enrollment from a later child's marker. An old
    # predecessor's recovery uses the independent header only, below.
    need(not task.data.get(REFS) and db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == BOUND, Audit.data['task_id'].as_string() == task.id).limit(1)) is None
        and db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id,
            Job.data['task_id'].as_string() == task.id, Job.data[KEY]['schema'].as_string() == SCHEMA).limit(1)) is None)
    value = _seal({'schema': ENROLL_SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'actor_id': p.user_id, 'first_job_id': job.id, 'first_scope': _compact_scope(job)})
    task.data = {**task.data, ENROLL: value}
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=ENROLLED,
        object_id=task.id, data=copy.deepcopy(value)))
    db.flush()
    return value


def bind(db, p, task, job, parent_ids, *, control_record_hash=None, compact_enrollment_hash=None):
    need(job.kind in KINDS and job.workspace_id == task.workspace_id == p.workspace_id
        and job.data['task_id'] == task.id and job.status == 'queued' and job.attempts == 0
        and KEY not in job.data)
    need(type(parent_ids) is list and len(parent_ids) <= MAX_PARENTS and len(set(parent_ids)) == len(parent_ids)
        and job.id not in parent_ids)
    value = _seal({'schema': SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        **_child_pin(job), 'bound_by': p.user_id, 'control_record_hash': control_record_hash,
        'compact_enrollment_hash': compact_enrollment_hash,
        'parents': [parent_pin(db, p, task, identity) for identity in sorted(parent_ids)]})
    need(db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == BOUND, Audit.object_id == job.id)) is None)
    refs = copy.deepcopy(task.data.get(REFS, {})); need(type(refs) is dict and job.id not in refs)
    refs[job.id] = value['record_hash']; task.data = {**task.data, REFS: refs}
    job.data = {**job.data, KEY: value}
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=BOUND, object_id=job.id, data=copy.deepcopy(value)))
    db.flush()
    return value


def bind_compact(db, p, task, job):
    enrollment = _enroll(db, p, task, job)
    receipts = task.data['workflow_runtime']['receipts']
    parent = receipts[-1] if receipts else None
    parents = [parent['request_id']] if parent and parent.get('execution_mode') == 'registered_ability' else []
    return bind(db, p, task, job, parents, compact_enrollment_hash=enrollment['record_hash'])


def pending_predicate():
    """Correlated SQL filter runs before LIMIT; no wait/attempt or auth cache."""
    parent = aliased(Job); proof = aliased(Audit); task = aliased(Task)
    hint = func.jsonb_build_array(func.jsonb_build_object('job_id', parent.id))
    # A damaged/missing half is not a perpetual pending wait: let the ordinary
    # owned handler reject it under Task->Job locking before any SDK call.
    bound = select(proof.id).join(task, task.id == Job.data['task_id'].as_string()).where(
        task.workspace_id == Job.workspace_id, proof.workspace_id == Job.workspace_id,
        proof.action == BOUND, proof.object_id == Job.id, proof.data == Job.data[KEY],
        proof.actor_id == Job.data[KEY]['bound_by'].as_string(),
        Job.data[KEY]['schema'].as_string() == SCHEMA,
        Job.data[KEY]['workspace_id'].as_string() == Job.workspace_id,
        Job.data[KEY]['task_id'].as_string() == task.id,
        task.data[REFS][Job.id].as_string() == Job.data[KEY]['record_hash'].as_string(),
        proof.data['parents'].contains(hint)).correlate(Job, parent).exists()
    return select(parent.id).where(Job.kind.in_(KINDS), Job.status == 'queued',
        parent.workspace_id == Job.workspace_id, parent.kind.in_(KINDS),
        parent.data['task_id'].as_string() == Job.data['task_id'].as_string(), parent.status.in_(['queued', 'running']),
        bound).correlate(Job).exists()


def require_ready(db, p, task, job):
    """Called only under the ordinary Task lock and owned child lease."""
    try:
        proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == BOUND, Audit.object_id == job.id).limit(2)))
        refs = task.data.get(REFS, {}); need(type(refs) is dict)
        if KEY not in job.data and not proofs and job.id not in refs:
            if job.kind == 'execute_full_semantic_graph_node':
                # The independently captured future graph frontier is a family
                # witness even when all three per-child links were removed.
                need(checked_control(db, p, task) is None)
            else:
                enrollment = checked_enrollment(db, p, task, verify_first=False)
                if enrollment is not None:
                    scope = _compact_scope(job); start = enrollment['first_scope']
                    need((scope['visit'],scope['attempt']) < (start['visit'],start['attempt']))
                    # Only a genuinely completed old predecessor can cross the
                    # migration boundary without a new bound record.
                    parent_pin(db, p, task, job.id)
            return
        value = job.data.get(KEY)
        fields = {'schema', 'workspace_id', 'task_id', *list(_child_pin(job)), 'bound_by', 'control_record_hash',
            'compact_enrollment_hash', 'parents', 'record_hash'}
        need(type(value) is dict and set(value) == fields and value['schema'] == SCHEMA
            and value['workspace_id'] == p.workspace_id == task.workspace_id == job.workspace_id
            and value['task_id'] == task.id == job.data['task_id']
            and all(value[k] == v for k, v in _child_pin(job).items())
            and value['record_hash'] == digest({k: v for k, v in value.items() if k != 'record_hash'})
            and refs.get(job.id) == value['record_hash'] and len(proofs) == 1
            and proofs[0].actor_id == value['bound_by'] and proofs[0].data == value)
        from .leases import ownership
        lease = ownership(db)
        need(job.status == 'running' and job.data['actor_id'] == p.user_id and lease.get('job_id') == job.id
            and lease.get('lease_token') == job.lease_token and job.lease_token is not None
            and job.lease_until > db.scalar(select(func.clock_timestamp())))
        parents = value['parents']; ids = [pin['job_id'] for pin in parents]
        need(type(parents) is list and len(parents) <= MAX_PARENTS and ids == sorted(set(ids)) and job.id not in ids)
        if job.kind == 'execute_full_semantic_graph_node':
            need(value['compact_enrollment_hash'] is None and type(value['control_record_hash']) is str)
            control = checked_control(db, p, task, record_hash=value['control_record_hash'])
            need(control is not None)
            expected = graph_parent_ids(task, control, job.data['activation_id'])
        else:
            enrollment = checked_enrollment(db, p, task)
            scope = _compact_scope(job); start = enrollment['first_scope'] if enrollment else None
            need(value['control_record_hash'] is None and enrollment is not None
                and value['compact_enrollment_hash'] == enrollment['record_hash']
                and (scope['visit'],scope['attempt']) >= (start['visit'],start['attempt']))
            prior = task.data['workflow_runtime']['receipts'][:job.data['scope']['visit']]
            expected = [prior[-1]['request_id']] if prior and prior[-1].get('execution_mode') == 'registered_ability' else []
        need(ids == sorted(expected))
        for pin in parents:
            need(parent_pin(db, p, task, pin['job_id']) == pin)
            parent = _row(db, p, Job, pin['job_id'])
            need(parent.status not in {'queued', 'running'}, PENDING)
            need(parent.status == 'succeeded', FAILED)
    except HTTPException: raise
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, IndexError): need(False)


class ControlObserver:
    """Per-call collector explicitly passed to the pure kernel, never global."""
    def __init__(self, previous=None, *, producer_job_id=None, activation_id=None):
        self.previous = copy.deepcopy(previous)
        self.frontiers = copy.deepcopy(previous['frontiers']) if previous else {}
        self.arrivals = copy.deepcopy(previous['arrivals']) if previous else {}
        self.producer_job_id = producer_job_id; self.activation_id = activation_id; self.events = []

    def __call__(self, event):
        need(type(event) is dict and len(self.events) < MAX_TRACE)
        kind = event.get('kind'); target = event.get('activation_id')
        if kind == 'spawn':
            need(set(event) == {'kind', 'activation_id', 'node_id', 'scope_id', 'branch_id'}
                and target not in self.frontiers and len(self.frontiers) < MAX_ACTIVATIONS)
            self.frontiers[target] = []
        elif kind == 'link':
            need(set(event) == {'kind', 'source_activation_id', 'activation_id', 'edge_id'}
                and target in self.frontiers and not self.frontiers[target])
            self.frontiers[target] = list(self.frontiers[event['source_activation_id']])
        elif kind == 'complete':
            need(set(event) == {'kind', 'activation_id'} and target in self.frontiers)
            if self.producer_job_id is not None:
                need(target == self.activation_id and _uuid(self.producer_job_id))
                self.frontiers[target] = [self.producer_job_id]
        elif kind == 'arrive':
            need(set(event) == {'kind', 'source_activation_id', 'scope_id', 'branch_id', 'node_id'}
                and event['source_activation_id'] in self.frontiers)
            values = self.arrivals.setdefault(event['scope_id'], {})
            need(event['branch_id'] not in values or values[event['branch_id']] == event['source_activation_id'])
            values[event['branch_id']] = event['source_activation_id']
        elif kind == 'join':
            need(set(event) == {'kind', 'activation_id', 'scope_id', 'parents'} and target in self.frontiers
                and event['parents'] == sorted(self.arrivals[event['scope_id']].values()))
            self.frontiers[target] = sorted({identity for source in event['parents'] for identity in self.frontiers[source]})
        else: need(False)
        need(all(len(values) <= MAX_PARENTS and values == sorted(set(values)) for values in self.frontiers.values()))
        self.events.append(copy.deepcopy(event))


def _control_anchor(value):
    return {**{k: copy.deepcopy(v) for k, v in value.items() if k not in {'frontiers', 'arrivals'}},
        'frontiers_hash': digest(value['frontiers']), 'arrivals_hash': digest(value['arrivals'])}


def _checked_control(db, p, task, *, record_hash=None):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == CONTROL_AUDIT).limit(MAX_CONTROLS + 1)))
    value = task.data.get(CONTROL)
    if value is None and not rows: return None  # Explicit old-Task boundary.
    need(type(value) is dict and len(rows) <= MAX_CONTROLS and len(rows) == value['version'])
    need(all(type(row.data.get('version')) is int for row in rows))
    rows.sort(key=lambda row: row.data['version']); previous = None; previous_value = None; selected = None
    for version, row in enumerate(rows, 1):
        data = row.data
        need(set(data) == {'schema','workspace_id','task_id','graph_hash','version','previous_hash','bound_by',
            'state_hash_at_capture','producer','frontiers_hash','arrivals_hash','trace','record_hash'}
            and data['schema'] == CONTROL_SCHEMA and data['workspace_id'] == p.workspace_id
            and data['task_id'] == task.id and data['version'] == version and data['previous_hash'] == previous
            and row.actor_id == data['bound_by'] and data['graph_hash'] == task.data['graph_runtime']['graph_hash'])
        producer = data['producer']
        if producer is not None: need(parent_pin(db, p, task, producer['job_id']) == producer)
        replay = ControlObserver(previous_value, producer_job_id=producer['job_id'] if producer else None,
            activation_id=producer['activation_id'] if producer else None)
        need(type(data['trace']) is list and 0 < len(data['trace']) <= MAX_TRACE)
        for event in data['trace']: replay(event)
        need(digest(replay.frontiers) == data['frontiers_hash'] and digest(replay.arrivals) == data['arrivals_hash'])
        full = {k: copy.deepcopy(v) for k, v in data.items() if k not in {'frontiers_hash', 'arrivals_hash'}}
        full.update(frontiers=replay.frontiers, arrivals=replay.arrivals)
        need(full['record_hash'] == digest({k: v for k, v in full.items() if k != 'record_hash'}))
        previous = data['record_hash']
        previous_value = full
        if record_hash == full['record_hash']: selected = copy.deepcopy(full)
    need(previous_value == value and set(value['frontiers']) == set(task.data['graph_runtime']['activations']))
    if record_hash is not None:
        need(selected is not None)
        return selected
    return copy.deepcopy(value)


def checked_control(db, p, task, *, record_hash=None):
    try: return _checked_control(db, p, task, record_hash=record_hash)
    except HTTPException: raise
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, IndexError): need(False)


def trace_for(db, p, task, *, initialize=False, producer_job=None, activation_id=None):
    previous = None if initialize else checked_control(db, p, task)
    if previous is None and not initialize: return None
    return ControlObserver(previous, producer_job_id=producer_job.id if producer_job is not None else None,
        activation_id=activation_id)


def observer_options(observer): return {'_observer': observer} if observer is not None else {}


def save_control(db, p, task, state, observer):
    if observer is None or not observer.events: return
    need(type(observer) is ControlObserver and set(observer.frontiers) == set(state['activations']))
    producer = parent_pin(db, p, task, observer.producer_job_id) if observer.producer_job_id else None
    previous = observer.previous
    value = _seal({'schema': CONTROL_SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'graph_hash': state['graph_hash'], 'version': previous['version'] + 1 if previous else 1,
        'previous_hash': previous['record_hash'] if previous else None, 'bound_by': p.user_id,
        'state_hash_at_capture': state['state_hash'], 'producer': producer,
        'frontiers': copy.deepcopy(observer.frontiers), 'arrivals': copy.deepcopy(observer.arrivals),
        'trace': copy.deepcopy(observer.events)})
    task.data = {**task.data, CONTROL: value}
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=CONTROL_AUDIT,
        object_id=task.id, data=_control_anchor(value)))
    db.flush()


def graph_parent_ids(task, control, activation_id):
    activation = task.data['graph_runtime']['activations'][activation_id]
    ids = set(control['frontiers'][activation_id])
    for binding in activation['inputs_snapshot']['bindings'].values():
        producer = binding['producer_activation_id']
        if producer is not None: ids.update(control['frontiers'][producer])
    need(len(ids) <= MAX_PARENTS)
    return sorted(ids)


def bind_graph(db, p, task, job):
    control = checked_control(db, p, task)
    if control is None: return  # Never infer a historical frontier.
    return bind(db, p, task, job, graph_parent_ids(task, control, job.data['activation_id']),
        control_record_hash=control['record_hash'])
