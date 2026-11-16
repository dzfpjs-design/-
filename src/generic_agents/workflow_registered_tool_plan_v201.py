"""One generic, preapproved registered HTTP tool plan; no scenario interpreter.

The model selects a proposal. Only actual adopted inputs and live V195/V175
authority permit dispatch. A durable intent is never a reason to re-POST an
unknown action. A terminal, same-key receipt is required for completion.
"""
import copy
import base64
import hashlib
import sys

from fastapi import HTTPException
from sqlalchemy import func, select

from . import business_reasoning_v197 as reasoning
from . import business_tools, business_flow_review_store as safe
from . import workflow_service_v195 as service
from .contracts import Manifest, digest
from .leases import checkpoint, ownership, LeaseLost, ExecutionUnauthorized
from .models import Audit, Capability, Invocation, Job, Task, uid
from .services import audit, published, scoped, validate
from .workflow_registered_tool_plan_models_v201 import (
    WorkflowRegisteredToolPlanV201 as Plan, WorkflowRegisteredToolPlanItemV201 as Item)

OP = 'execute_registered_tool_plan'
CONTRACT = 'workflow-registered-tool-plan-contract-v201'
PLAN = 'workflow-registered-tool-plan-v201'
ITEM = 'workflow-registered-tool-plan-item-v201'
KEY = 'workflow_registered_tool_plan_v201'
CHILD_APPROVAL = 'workflow-registered-tool-child-approval-v201'
CREATED = 'workflow.registered_tool_plan.created_v201'
ITEM_STATE = 'workflow.registered_tool_plan.item_v201'
CHILD_RECEIPT = 'workflow.registered_tool_plan.receipt_v201'
RECEIPT = 'registered-tool-receipt-v201'
MAX_BYTES = 2 * 1024 * 1024
MAX_STEPS = 8
MAX_CHECKS = 128
ERROR = '原注册工具计划、真实研判、批准、派发号或独立审计无法核对；未重建或重发动作'
CHILD_KEYS = {'schema', 'parent_invocation_id', 'plan_id', 'item_id', 'task_id', 'job_id',
    'activation_id', 'graph_hash', 'cancel_epoch', 'capability_id', 'capability_hash',
    'input_hash', 'request_key_hash', 'parent_input_hash'}
ITEM_KEYS = {'schema', 'id', 'workspace_id', 'plan_id', 'parent_invocation_id', 'ordinal',
    'blueprint_hash', 'version', 'status', 'dispatch_owner', 'invocation_id', 'dispatch_authorized',
    'receipt_checks', 'receipt', 'record_hash'}
RECEIPT_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['schema', 'idempotency_key', 'input_hash', 'status', 'terminal'],
    'properties': {'schema': {'const': RECEIPT}, 'idempotency_key': {'type': 'string', 'minLength': 1},
        'input_hash': {'type': 'string', 'pattern': service.HASH_PATTERN},
        'status': {'enum': ['succeeded', 'pending', 'failed', 'unknown', 'not_executed']},
        'terminal': {'type': 'boolean'}, 'output': {'type': 'object'}, 'error': {'type': 'string'}},
    'allOf': [
        {'if': {'properties': {'status': {'const': 'succeeded'}}},
         'then': {'required': ['output'], 'properties': {'terminal': {'const': True}}}},
        {'if': {'properties': {'status': {'enum': ['failed', 'not_executed']}}},
         'then': {'properties': {'terminal': {'const': True}}}},
        {'if': {'properties': {'status': {'enum': ['pending', 'unknown']}}},
         'then': {'properties': {'terminal': {'const': False}}}}]}
# not_executed/terminal=true means the registered supplier permanently closes
# this key and rejects late submissions. 404, timeout and pending never mean it.


def need(ok, message=ERROR, status=409):
    if not ok: raise HTTPException(status, message)


def _same(a, b): return safe._same(a, b)
def _seal(value): return {**copy.deepcopy(value), 'record_hash': digest(value)}
def _value(row): return row.data if isinstance(row, (Plan, Item)) else row


def input_schema():
    ident = {'type': 'string', 'pattern': service.UUID_PATTERN}
    step = service._object({'capability_id': ident,
        'capability_hash': {'type': 'string', 'pattern': service.HASH_PATTERN},
        'agent_role': {'enum': ['business', 'perception', 'data']}, 'arguments': {'type': 'object'},
        'allow_external_inputs': {'const': True, 'type': 'boolean'}})
    candidate = service._object({'candidate_ref': {'type': 'string', 'minLength': 1, 'maxLength': 80},
        'steps': {'type': 'array', 'minItems': 1, 'maxItems': MAX_STEPS, 'items': step}})
    return service._object({'reasoning_result': reasoning.output_schema(),
        'candidate_plans': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': candidate}})


def output_schema():
    sha = {'type': 'string', 'pattern': service.HASH_PATTERN}
    row = service._object({'ordinal': {'type': 'integer', 'minimum': 0, 'maximum': MAX_STEPS - 1},
        'capability_id': {'type': 'string', 'pattern': service.UUID_PATTERN}, 'capability_hash': sha,
        'invocation_id': {'type': 'string', 'pattern': service.UUID_PATTERN}, 'input_hash': sha,
        'receipt_hash': sha, 'output_hash': sha, 'output': {'type': 'object'}})
    receipt = service._object({'schema': {'const': PLAN + '/receipt'},
        'plan_id': {'type': 'string', 'pattern': service.UUID_PATTERN}, 'plan_hash': sha,
        'candidate_ref': {'type': 'string'}, 'status': {'const': 'succeeded'},
        'items': {'type': 'array', 'minItems': 1, 'maxItems': MAX_STEPS, 'items': row},
        'simulation_only': {'type': 'boolean'}, 'physical_state_verified': {'const': False},
        'independent_acceptance': {'const': False}})
    return service._object({'result': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
        'plan_receipt': receipt})


def _inputs(value):
    safe._bytes(value, MAX_BYTES); validate(input_schema(), value)
    refs = [row['candidate_ref'] for row in value['candidate_plans']]
    need(len(refs) == len(set(refs)), '候选编号必须唯一，不自动合并或选择第一项', 422)
    for candidate in value['candidate_plans']:
        for row in candidate['steps']:
            need(row['allow_external_inputs'] is True, status=422)
            safe.identity(row['capability_id'])
    return copy.deepcopy(value)


def validate_manifest(manifest):
    data = manifest.model_dump(mode='json') if isinstance(manifest, Manifest) else copy.deepcopy(manifest)
    Manifest.model_validate(data)
    spec = data.get('specification', {})
    need(type(spec) is dict and set(spec) == {'schema', 'tool_pins'} and spec['schema'] == CONTRACT
        and type(spec['tool_pins']) is list and 1 <= len(spec['tool_pins']) <= 16, status=422)
    identities = []
    for pin in spec['tool_pins']:
        need(type(pin) is dict and set(pin) == {'capability_id', 'capability_hash', 'release_version'}
            and type(pin['capability_hash']) is str and safe.SHA.fullmatch(pin['capability_hash']) is not None
            and type(pin['release_version']) is str and bool(pin['release_version']), status=422)
        safe.identity(pin['capability_id']); identities.append(pin['capability_id'])
    need(len(identities) == len(set(identities)) and data['dependencies'] == sorted(identities)
        and data['kind'] == 'tool' and data['version'] == '1.0.0' and data['allowed_agent_roles'] == ['business']
        and _same(data['execution'], {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': OP, 'side_effect': True}) and data['execution']['side_effect'] is True
        and _same(data['input_schema'], input_schema()) and _same(data['output_schema'], output_schema())
        and data['data_policy'].get('cloud_allowed') is False
        and type(data['budget'].get('max_attempts')) is int and data['budget']['max_attempts'] == 1,
        '工具计划须明确真实执行服务、有副作用、固定目标依赖和单次派发；不支持MCP写动作', 422)
    return data


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    return service.validate_call_approval(db, p, cap, role, inputs, key, approval, _variant=sys.modules[__name__])


def producer_integrity_pin(db, p, task, job, inputs):
    from .workflow_graph_scope import _frozen, _job_matches
    from .full_semantic_graph_v1 import ability_inputs
    from .workflow_monitor_schedule_v200 import _producer
    value = _inputs(inputs)
    actual, _release, _business, graph, state, activation, node = _frozen(db, p, task.id, job.data['activation_id'])
    _job_matches(actual, job, state, activation, node)
    need(actual.id == task.id and job.data['actor_id'] == p.user_id
        and _same(ability_inputs(graph, state, activation['id']), value) and _same(job.data['inputs'], value))
    producer = _producer(db, p, {'task': actual, 'job': job}, value['reasoning_result'])
    return producer


def eligible_candidate(db, p, task, job, inputs, producer):
    # A saved engineering review can authorize only a bounded SIM mechanics
    # test. It never edits the paid answer, resolves evidence gaps or grants
    # production/device authority. All final live gates remain below.
    try:
        return _selected_candidate(inputs['reasoning_result'])
    except HTTPException:
        from .workflow_simulation_review_v202 import checked_selection
        return checked_selection(db, p, task, job, inputs, producer)


def producer_wait_pin(db, p, task, job, inputs):
    producer = producer_integrity_pin(db, p, task, job, inputs)
    eligible_candidate(db, p, task, job, inputs, producer)
    return producer


def _engineering_scope(context):
    # Only the actual approved parent binding returned by the current service
    # authority gate selects this restriction. Client inputs/receipt labels do
    # not grant a physical target permission to an engineering Task.
    value = context['binding']['simulation_only']
    need(type(value) is bool, '原流程的工程模式无法核对，未提交工具动作')
    return value


def _target(db, p, parent_cap, step, *, simulation_only):
    from .security import require
    need(type(simulation_only) is bool)
    cap = published(db, p, step['capability_id'])
    pins = parent_cap.data['specification']['tool_pins']
    matches = [pin for pin in pins if pin['capability_id'] == cap.id]
    need(len(matches) == 1 and cap.id in parent_cap.data['dependencies'] and cap.kind == 'tool'
        and cap.content_hash == step['capability_hash'] == matches[0]['capability_hash']
        and cap.release_version == matches[0]['release_version'] and step['agent_role'] in cap.data['allowed_agent_roles'])
    execution = cap.data['execution']
    need(not simulation_only or cap.data.get('specification', {}).get('simulation_only') is True,
        '工程模拟流程只能调用明确登记为SIM的目标工具；未提交实际设备或外部动作')
    need(execution.get('adapter') == 'http' and execution.get('side_effect') is True
        and execution.get('dispatch_scope') == 'registered_tool_plan_v201'
        and execution.get('approval_policy') == 'admin' and type(execution.get('receipt_endpoint')) is str
        and bool(execution['receipt_endpoint']) and execution.get('receipt_protocol') == RECEIPT
        and cap.data['data_policy'].get('external_allowed') is True and step['allow_external_inputs'] is True
        and type(cap.data['budget'].get('max_attempts')) is int and cap.data['budget']['max_attempts'] == 1,
        '目标必须是已发布的管理员批准HTTP动作、明确外发及原号终态回执；未降级为普通工具')
    require(p, 'admin')
    from .device_gateway_tools_v269 import applies as managed_device, validate_tool
    if managed_device(cap.data):
        validate_tool(db, p, cap, purpose='prepare')
    else:
        from .gateway import validate_endpoint
        validate_endpoint(execution); validate_endpoint({'endpoint': execution['receipt_endpoint']})
    business_tools.validate_bindings(cap.data)
    bindings = execution.get('input_bindings', {})
    need(set(bindings.values()) <= {'entity_ids', 'task_id', 'request_key'},
        '此首版仅支持对象、任务与原请求号服务器绑定；未隐式外发资料或填充未知来源')
    return cap


def _bound(cap, step, task, key):
    evidence = {'evidence': [], 'observation_evidence': [], 'selected_entity_ids': copy.deepcopy(task.data['entity_ids'])}
    return business_tools.bind_inputs(cap, step['arguments'], task, evidence, key)


def _check_candidates(db, p, context, inputs):
    engineering = _engineering_scope(context)
    for candidate in inputs['candidate_plans']:
        for step in candidate['steps']:
            _target(db, p, context['capability'], step, simulation_only=engineering)
    return engineering


def _lock(db, p, parent_id): safe._lock(db, {'registered_tool_plan_v201': p.workspace_id, 'parent_invocation_id': parent_id})


def _event(parent_id, value, previous=None):
    return {'schema': value['schema'], 'parent_invocation_id': parent_id, 'record_hash': value['record_hash'],
        'previous_hash': previous, 'record': copy.deepcopy(value)}


def _inventory(db, p, parent_id):
    """Only historical SQL/hash checks; deliberately no live/Goal/context gate."""
    plans = list(db.scalars(select(Plan).where(Plan.workspace_id == p.workspace_id, Plan.parent_invocation_id == parent_id)))
    rows = list(db.scalars(select(Item).where(Item.workspace_id == p.workspace_id, Item.parent_invocation_id == parent_id)))
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_([CREATED, ITEM_STATE, CHILD_RECEIPT]), Audit.data['parent_invocation_id'].as_string() == parent_id)))
    parent = scoped(db, Invocation, parent_id, p)
    need(len(plans) <= 1 and len(rows) <= MAX_STEPS and len(events) <= MAX_STEPS * (MAX_CHECKS + 5) * 2 + 1)
    if not plans:
        need(not rows and not events and KEY not in parent.data, '原工具计划痕迹仍在，不能重建或降级派发')
        return None, []
    plan = plans[0]; value = plan.data; safe._bytes(value, MAX_BYTES)
    need(type(value) is dict and set(value) == {'schema', 'id', 'workspace_id', 'parent_invocation_id', 'task_id',
        'job_id', 'actor_id', 'capability_id', 'request_key', 'input_hash', 'inputs', 'approval', 'producer',
        'candidate_ref', 'source', 'steps', 'created_at', 'record_hash'} and value['schema'] == PLAN
        and plan.version == 1 and value['record_hash'] == plan.record_hash == digest({k: v for k, v in value.items() if k != 'record_hash'})
        and all(value[k] == getattr(plan, k) for k in ('id', 'workspace_id', 'parent_invocation_id', 'task_id', 'job_id',
            'actor_id', 'capability_id', 'request_key', 'input_hash')) and digest(value['inputs']) == plan.input_hash
        and parent.capability_id == plan.capability_id and parent.idempotency_key == plan.request_key
        and parent.data.get('capability_hash') == value['approval']['capability_hash']
        and digest(parent.data.get('capability_snapshot')) == value['approval']['capability_hash']
        and parent.data.get('role') == 'business' and parent.data.get('side_effect') is True
        and parent.data.get('input_hash') == plan.input_hash and _same(parent.data.get('inputs'), value['inputs'])
        and _same(parent.data.get('approval'), value['approval'])
        and _same(parent.data.get(KEY), {'schema': PLAN, 'plan_id': plan.id, 'plan_hash': plan.record_hash}))
    _inputs(value['inputs'])
    create = [entry for entry in events if entry.action == CREATED and entry.object_id == plan.id]
    need(len(create) == 1 and create[0].actor_id == plan.actor_id and _same(create[0].data, _event(parent_id, value)))
    need(type(value['steps']) is list and 1 <= len(value['steps']) <= MAX_STEPS
        and len(rows) == len(value['steps']) and {row.id for row in rows} == {step['id'] for step in value['steps']})
    used = {create[0].id}; ordered = []
    for ordinal, blueprint in enumerate(value['steps']):
        row = next(item for item in rows if item.id == blueprint['id']); ordered.append(row)
        need(blueprint['ordinal'] == ordinal and row.ordinal == ordinal and row.plan_id == plan.id
            and row.capability_id == blueprint['capability_id'] and row.request_key == blueprint['request_key']
            and row.input_hash == blueprint['input_hash'] == digest(blueprint['inputs'])
            and blueprint['capability_hash'] == digest(blueprint['capability_snapshot']))
        history = sorted([entry for entry in events if entry.action == ITEM_STATE and entry.object_id == row.id],
            key=lambda entry: entry.data.get('record', {}).get('version', 0))
        need(len(history) == row.version and 1 <= row.version <= MAX_CHECKS + 5)
        previous = None; previous_value = None
        for version, entry in enumerate(history, 1):
            state = entry.data.get('record'); safe._bytes(state, MAX_BYTES)
            need(type(state) is dict and set(state) == ITEM_KEYS and state['schema'] == ITEM
                and state['id'] == row.id and state['plan_id'] == plan.id and state['parent_invocation_id'] == parent_id
                and state['workspace_id'] == p.workspace_id and type(state['version']) is int and state['version'] == version
                and state['ordinal'] == ordinal and state['blueprint_hash'] == digest(blueprint)
                and state['record_hash'] == digest({k: v for k, v in state.items() if k != 'record_hash'})
                and _same(entry.data, _event(parent_id, state, previous)) and entry.actor_id == plan.actor_id
                and state['status'] in {'prepared', 'dispatching', 'waiting', 'unknown', 'conflict', 'succeeded', 'failed', 'not_executed'}
                and type(state['dispatch_authorized']) is bool and type(state['receipt_checks']) is list
                and len(state['receipt_checks']) <= MAX_CHECKS)
            if version == 1:
                need(state['status'] == 'prepared' and state['dispatch_owner'] is None and state['invocation_id'] is None
                    and state['dispatch_authorized'] is False and not state['receipt_checks'] and state['receipt'] is None)
            else:
                need(previous_value['status'] not in {'succeeded', 'failed', 'not_executed', 'conflict'}
                    and _same(state['receipt_checks'][:len(previous_value['receipt_checks'])], previous_value['receipt_checks']))
                if previous_value['status'] == 'prepared':
                    need(state['status'] == 'dispatching' and type(state['dispatch_owner']) is dict
                        and set(state['dispatch_owner']) == {'job_id', 'lease_token'} and state['dispatch_owner']['job_id'] == plan.job_id
                        and bool(state['dispatch_owner']['lease_token']) and state['invocation_id'] is None
                        and state['dispatch_authorized'] is False and state['receipt'] is None and not state['receipt_checks'])
                else:
                    need(state['status'] != 'prepared' and _same(state['dispatch_owner'], previous_value['dispatch_owner'])
                        and (previous_value['invocation_id'] is None or state['invocation_id'] == previous_value['invocation_id'])
                        and (previous_value['dispatch_authorized'] is False or state['dispatch_authorized'] is True))
            for check in state['receipt_checks']:
                need(type(check) is dict)
                if check.get('kind') == 'raw_http': _checked_capture(check, plan)
                else:
                    need(set(check) == {'kind', 'raw_sha256', 'receipt', 'receipt_hash', 'error', 'reader', 'checked_at'}
                        and check['kind'] == 'validation' and (check['error'] is None or type(check['error']) is str)
                        and type(check['reader']) is dict and check['reader'].get('job_id') == plan.job_id
                        and bool(check['reader'].get('lease_token')))
                    if check['receipt'] is None: need(check['receipt_hash'] is None)
                    else:
                        _receipt_value(check['receipt'], blueprint)
                        captures = [entry for entry in state['receipt_checks'] if entry.get('kind') == 'raw_http'
                            and entry.get('raw_sha256') == check['raw_sha256']]
                        need(bool(captures) and check['receipt_hash'] == digest(check['receipt'])
                            and _same(reasoning.transport.strict_json(_checked_capture(captures[-1], plan).decode('utf-8')), check['receipt']))
            if state['receipt'] is not None:
                _receipt_value(state['receipt'], blueprint)
                need(state['status'] == state['receipt']['status'] and state['receipt']['terminal'] is True)
            else: need(state['status'] not in {'succeeded', 'failed', 'not_executed'})
            previous = state['record_hash']; previous_value = state; used.add(entry.id)
        need(_same(previous_value, row.data) and row.record_hash == row.data['record_hash']
            and row.status == row.data['status'] and row.invocation_id == row.data['invocation_id'])
        if row.data['receipt'] is not None:
            receipt_events = [entry for entry in events if entry.action == CHILD_RECEIPT and entry.object_id == row.id]
            need(len(receipt_events) == 1 and receipt_events[0].actor_id == plan.actor_id)
            _checked_settlement(db, p, plan, row, blueprint, receipt_events[0]); used.add(receipt_events[0].id)
    need(used == {entry.id for entry in events} and len(used) == len(events))
    return plan, ordered


def _write_item(db, p, plan, item, *, status=None, **changes):
    old = item.data; value = {key: copy.deepcopy(entry) for key, entry in old.items() if key != 'record_hash'}
    value.update(changes); value['version'] += 1
    if status is not None: value['status'] = status
    value = _seal(value); safe._bytes(value, MAX_BYTES)
    item.version = value['version']; item.status = value['status']; item.invocation_id = value['invocation_id']
    item.record_hash = value['record_hash']; item.data = value
    audit(db, p, ITEM_STATE, item.id, _event(plan.parent_invocation_id, value, old['record_hash']))
    db.flush()


def make_child_approval(plan, item):
    value, state = _value(plan), _value(item)
    target = value['steps'][state['ordinal']]
    return {'schema': CHILD_APPROVAL, 'parent_invocation_id': value['parent_invocation_id'],
        'plan_id': value['id'], 'item_id': state['id'], 'task_id': value['task_id'], 'job_id': value['job_id'],
        'activation_id': value['approval']['activation_id'], 'graph_hash': value['approval']['graph_hash'],
        'cancel_epoch': value['approval']['cancel_epoch'], 'capability_id': target['capability_id'],
        'capability_hash': target['capability_hash'], 'input_hash': target['input_hash'],
        'request_key_hash': digest(target['request_key']), 'parent_input_hash': value['input_hash']}


def _child(db, p, plan, item, blueprint, *, required=True):
    rows = list(db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == blueprint['request_key']).execution_options(populate_existing=True)))
    need(len(rows) <= 1)
    if not rows:
        need(not required and item.invocation_id is None)
        return None
    call = rows[0]
    need(item.invocation_id is None or item.invocation_id == call.id)
    need(call.capability_id == blueprint['capability_id'] and call.data.get('capability_hash') == blueprint['capability_hash']
        and call.data.get('role') == blueprint['agent_role'] and call.data.get('input_hash') == blueprint['input_hash']
        and call.data.get('job_id') == plan.job_id and call.data.get('lease_token') == item.data['dispatch_owner']['lease_token']
        and call.data.get('side_effect') is True and _same(call.data.get('inputs'), blueprint['inputs'])
        and _same(call.data.get('approval'), make_child_approval(plan, item))
        and _same(call.data.get('capability_snapshot'), blueprint['capability_snapshot']))
    return call


def _parent_context(db, p, plan):
    value = plan.data; cap = published(db, p, plan.capability_id)
    context = validate_call_approval(db, p, cap, 'business', value['inputs'], plan.request_key, value['approval'])
    call = service._call(db, p, context, plan.request_key, value['inputs'], value['approval'], allow_unknown=True)
    need(call.id == plan.parent_invocation_id and context['task'].id == plan.task_id and context['job'].id == plan.job_id
        and context['binding']['binding_hash'] == value['source']['binding_hash'])
    return context


def validate_child_approval(db, p, cap, role, inputs, key, approval):
    need(type(approval) is dict and set(approval) == CHILD_KEYS and approval['schema'] == CHILD_APPROVAL)
    plan, items = _inventory(db, p, approval['parent_invocation_id']); need(plan is not None)
    matches = [item for item in items if item.id == approval['item_id']]; need(len(matches) == 1)
    item = matches[0]; context = _parent_context(db, p, plan); blueprint = plan.data['steps'][item.ordinal]
    _check_candidates(db, p, context, plan.data['inputs'])
    target = _target(db, p, context['capability'], blueprint, simulation_only=_engineering_scope(context))
    previous = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key).execution_options(populate_existing=True))
    # This check is under the ACTUAL parent Task lock, after the earlier intent
    # checkpoint. A concurrent graph action cannot borrow the parent's existing
    # key exemption to exceed the child's real Goal budget.
    _budget(db, p, context, 0 if previous is not None else 1)
    need(_same(approval, make_child_approval(plan, item)) and item.status == 'dispatching'
        and item.data['dispatch_owner'] == ownership(db) and item.data['dispatch_authorized'] is False
        and cap.id == target.id and cap.content_hash == target.content_hash and role == blueprint['agent_role']
        and key == blueprint['request_key'] and _same(inputs, blueprint['inputs']))
    return {**context, 'plan': plan, 'item': item, 'blueprint': blueprint}


def before_child_dispatch(db, p, cap, role, inputs, key, approval):
    context = validate_child_approval(db, p, cap, role, inputs, key, approval)
    plan, item, blueprint = context['plan'], context['item'], context['blueprint']
    call = _child(db, p, plan, item, blueprint)
    need(call.status == 'running' and item.invocation_id is None)
    _budget(db, p, context, 0)  # The child already exists: count it exactly once.
    _write_item(db, p, plan, item, invocation_id=call.id, dispatch_authorized=True)
    _inventory(db, p, plan.parent_invocation_id)
    return context


def authorize_device_transport(db, p, cap, call, *, stage):
    """Read-only authority at the managed HTTP boundary, not a new approval.

    Preflight requires the original unissued state. Send requires the approval
    actually saved by before_child_dispatch. Historical GET never reinstates
    a current device grant or authorizes another POST.
    """
    need(stage in {'preflight', 'send', 'receipt'})
    inputs = call.data.get('inputs'); approval = call.data.get('approval')
    need(type(approval) is dict and set(approval) == CHILD_KEYS
        and approval.get('schema') == CHILD_APPROVAL)
    if stage == 'preflight':
        return validate_child_approval(db, p, cap, call.data['role'], inputs,
            call.idempotency_key, approval)
    plan, items = _inventory(db, p, approval['parent_invocation_id'])
    need(plan is not None)
    matches = [row for row in items if row.id == approval['item_id']]
    need(len(matches) == 1)
    item = matches[0]; context = _parent_context(db, p, plan)
    blueprint = plan.data['steps'][item.ordinal]
    actual = _child(db, p, plan, item, blueprint)
    need(actual.id == call.id and _same(approval, make_child_approval(plan, item))
        and item.data['dispatch_authorized'] is True and item.invocation_id == call.id
        and context['job'].id == plan.job_id == ownership(db).get('job_id')
        and cap.id == blueprint['capability_id'] and cap.content_hash == blueprint['capability_hash']
        and _same(cap.data, blueprint['capability_snapshot']))
    if stage == 'send':
        need(item.status == 'dispatching' and actual.status == 'running'
            and item.data['dispatch_owner'] == ownership(db))
        _check_candidates(db, p, context, plan.data['inputs'])
        _budget(db, p, context, 0)
    return {**context, 'plan': plan, 'item': item, 'blueprint': blueprint}


def checked_child_call(db, p, task, call):
    """Read-only history seam for Goal accounting. Never calls live/guard."""
    approval = call.data.get('approval')
    need(type(approval) is dict and set(approval) == CHILD_KEYS and approval['schema'] == CHILD_APPROVAL)
    plan, rows = _inventory(db, p, approval['parent_invocation_id']); need(plan is not None and plan.task_id == task.id)
    item = next((row for row in rows if row.id == approval['item_id']), None); need(item is not None)
    parent = scoped(db, Invocation, plan.parent_invocation_id, p); job = scoped(db, Job, plan.job_id, p)
    node = plan.data['approval']['activation_id']; transport = task.data['graph_jobs'].get(node)
    need(job.data.get('task_id') == task.id and job.data.get('activation_id') == node
        and job.data.get('actor_id') == plan.actor_id and job.data.get('invocation_key') == plan.request_key
        and job.data.get('input_hash') == plan.input_hash and _same(job.data.get('inputs'), plan.data['inputs'])
        and job.data.get('graph_hash') == plan.data['approval']['graph_hash']
        and job.data.get('cancel_epoch') == plan.data['approval']['cancel_epoch']
        and type(transport) is dict and transport.get('job_id') == job.id
        and transport.get('invocation_key') == plan.request_key
        and ('invocation_id' not in transport or transport['invocation_id'] == parent.id)
        and parent.data.get('job_id') == job.id and parent.data.get('side_effect') is True
        and parent.data.get('role') == 'business' and parent.data.get('capability_hash') == plan.data['approval']['capability_hash'])
    owner = transport.get('dispatch_ownership'); authorized = transport.get('dispatch_authorization')
    need(type(owner) is dict and owner.get('job_id') == job.id and bool(owner.get('lease_token'))
        and owner['lease_token'] == parent.data.get('lease_token') and type(authorized) is dict
        and authorized.get('job_id') == job.id and authorized.get('lease_token') == owner['lease_token']
        and authorized.get('input_hash') == plan.input_hash and authorized.get('capability_hash') == parent.data['capability_hash']
        and authorized.get('actor_id') == plan.actor_id and authorized.get('epoch') == plan.data['approval']['cancel_epoch'])
    blueprint = plan.data['steps'][item.ordinal]
    actual = _child(db, p, plan, item, blueprint); need(actual.id == call.id)
    return {'plan': plan, 'item': item, 'parent_invocation': parent, 'job': job,
        'blueprint': copy.deepcopy(blueprint), 'request_key': blueprint['request_key'],
        'input_hash': blueprint['input_hash']}


def _budget(db, p, context, prepared_count):
    from .goal_queries import call_count
    spec = context['task'].data.get('goal_spec')
    need(type(spec) is dict and type(spec.get('max_tool_calls')) is int
        and call_count(db, p, context['task']) + prepared_count <= spec['max_tool_calls'],
        '原目标剩余调用预算不足，未提交后续工具动作')


def _selected_candidate(result):
    """Check the recommended option after its original producer is proven.

    The historical overall review state describes the complete comparison.
    An unselected alternative's missing information does not make a supported,
    complete recommendation incomplete. Conflicting evidence anywhere still
    blocks this conservative executor. This never changes the original model
    result or supplies any execution authority.
    """
    selected = result['recommended_candidate_ref']
    analysis = result['analysis']; assessments = analysis['candidate_assessments']
    recommendation = analysis['recommendation']
    matches = [row for row in assessments if row['candidate_ref'] == selected]
    need(selected is not None and recommendation['candidate_ref'] == selected and len(matches) == 1
        and matches[0]['assessment'] == 'supported' and not matches[0]['missing_information']
        and bool(matches[0]['evidence_refs']) and bool(recommendation['evidence_refs'])
        and not any(row['assessment'] == 'conflicting' for row in assessments),
        '所推荐方案仍有缺项、依据不足或候选比较存在冲突，未执行工具方案')
    unselected_gaps = any(row['candidate_ref'] != selected
        and (row['missing_information'] or row['assessment'] == 'insufficient') for row in assessments)
    expected_state = 'needs_information' if unselected_gaps else 'ready_for_human_review'
    need(result['review_state'] == expected_state,
        '原研判的候选评估与整体核对状态不一致，未执行工具方案')
    return selected


def _create(db, p, context, call, inputs, key, approval):
    producer = producer_wait_pin(db, p, context['task'], context['job'], inputs)
    result = inputs['reasoning_result']
    selected_ref = eligible_candidate(db, p, context['task'], context['job'], inputs, producer)
    need([row['candidate_ref'] for row in inputs['candidate_plans']] == [row['candidate_ref'] for row in producer['candidates']])
    selected = next(row for row in inputs['candidate_plans'] if row['candidate_ref'] == selected_ref)
    all_ids = {step['capability_id'] for row in inputs['candidate_plans'] for step in row['steps']}
    need(all_ids == set(context['capability'].data['dependencies']))
    engineering = _check_candidates(db, p, context, inputs)
    _budget(db, p, context, len(selected['steps']))
    plan_id = uid(); blueprints = []
    for ordinal, step in enumerate(selected['steps']):
        target = _target(db, p, context['capability'], step, simulation_only=engineering)
        item_key = 'registered-tool-plan-v201:' + call.id + ':' + str(ordinal)
        bound = _bound(target, step, context['task'], item_key)
        blueprints.append({**copy.deepcopy(step), 'id': uid(), 'ordinal': ordinal, 'request_key': item_key,
            'inputs': bound, 'input_hash': digest(bound), 'release_version': target.release_version,
            'capability_snapshot': copy.deepcopy(target.data)})
    value = _seal({'schema': PLAN, 'id': plan_id, 'workspace_id': p.workspace_id, 'parent_invocation_id': call.id,
        'task_id': context['task'].id, 'job_id': context['job'].id, 'actor_id': p.user_id,
        'capability_id': context['capability'].id, 'request_key': key, 'input_hash': digest(inputs),
        'inputs': inputs, 'approval': copy.deepcopy(approval), 'producer': producer,
        'candidate_ref': selected['candidate_ref'], 'source': {'binding_hash': context['binding']['binding_hash'],
            'simulation_only': engineering or any(
                row['capability_snapshot'].get('specification', {}).get('simulation_only') is True for row in blueprints)},
        'steps': blueprints,
        'created_at': db.scalar(select(func.clock_timestamp())).isoformat()})
    safe._bytes(value, MAX_BYTES)
    plan = Plan(**{name: value[name] for name in ('id', 'workspace_id', 'parent_invocation_id', 'task_id',
        'job_id', 'actor_id', 'capability_id', 'request_key', 'input_hash', 'record_hash')}, version=1, data=value)
    db.add(plan); db.flush(); audit(db, p, CREATED, plan.id, _event(call.id, value))
    for blueprint in blueprints:
        state = _seal({'schema': ITEM, 'id': blueprint['id'], 'workspace_id': p.workspace_id,
            'plan_id': plan.id, 'parent_invocation_id': call.id, 'ordinal': blueprint['ordinal'],
            'blueprint_hash': digest(blueprint), 'version': 1, 'status': 'prepared', 'dispatch_owner': None,
            'invocation_id': None, 'dispatch_authorized': False, 'receipt_checks': [], 'receipt': None})
        row = Item(id=state['id'], workspace_id=p.workspace_id, plan_id=plan.id, parent_invocation_id=call.id,
            ordinal=blueprint['ordinal'], capability_id=blueprint['capability_id'], request_key=blueprint['request_key'],
            input_hash=blueprint['input_hash'], invocation_id=None, status='prepared', version=1,
            record_hash=state['record_hash'], data=state)
        db.add(row); audit(db, p, ITEM_STATE, row.id, _event(call.id, state))
    call.data = {**call.data, KEY: {'schema': PLAN, 'plan_id': plan.id, 'plan_hash': plan.record_hash}}
    db.flush(); _inventory(db, p, call.id)
    return plan


def _receipt_value(receipt, blueprint):
    safe._bytes(receipt, MAX_BYTES); validate(RECEIPT_SCHEMA, receipt)
    need(receipt['idempotency_key'] == blueprint['request_key'] and receipt['input_hash'] == blueprint['input_hash']
        and type(receipt['terminal']) is bool)
    if receipt['status'] == 'succeeded': validate(blueprint['capability_snapshot']['output_schema'], receipt['output'])
    return copy.deepcopy(receipt)


def _checked_settlement(db, p, plan, item, blueprint, event):
    call = _child(db, p, plan, item, blueprint)
    evidence = event.data
    need(type(evidence) is dict and set(evidence) == {'schema', 'parent_invocation_id', 'plan_id', 'item_id',
        'invocation_id', 'original_gateway_outcome', 'receipt', 'receipt_hash', 'raw_receipt', 'settled_hash'}
        and evidence['schema'] == CHILD_RECEIPT and evidence['parent_invocation_id'] == plan.parent_invocation_id
        and evidence['plan_id'] == plan.id and evidence['item_id'] == item.id and evidence['invocation_id'] == call.id
        and _same(evidence['receipt'], item.data['receipt']) and evidence['receipt_hash'] == digest(evidence['receipt'])
        and _same(call.data.get(KEY + '/settled'), evidence)
        and call.status == evidence['receipt']['status']
        and evidence['settled_hash'] == digest({'status': call.status, 'output': call.data.get('output'),
            'error': call.data.get('error')})
        and (call.status != 'succeeded' or _same(call.data.get('output'), evidence['receipt']['output'])))
    raw = _checked_capture(evidence['raw_receipt'], plan)
    need(_same(reasoning.transport.strict_json(raw.decode('utf-8')), evidence['receipt'])
        and any(_same(entry, evidence['raw_receipt']) for entry in item.data['receipt_checks']))


def _checked_capture(entry, plan):
    need(type(entry) is dict and set(entry) == {'kind', 'raw_base64', 'raw_sha256', 'size_bytes', 'metadata', 'reader', 'checked_at'}
        and entry['kind'] == 'raw_http' and type(entry['size_bytes']) is int and 0 < entry['size_bytes'] <= 1024 * 1024
        and type(entry['metadata']) is dict and set(entry['metadata']) == {'status_code', 'content_type'}
        and type(entry['metadata']['status_code']) is int and entry['metadata']['status_code'] == 200
        and type(entry['metadata']['content_type']) is str and entry['metadata']['content_type'].split(';')[0] == 'application/json'
        and type(entry['reader']) is dict and set(entry['reader']) == {'job_id', 'lease_token'}
        and entry['reader']['job_id'] == plan.job_id and bool(entry['reader']['lease_token']))
    try: raw = base64.b64decode(entry['raw_base64'], validate=True)
    except (TypeError, ValueError): raise HTTPException(409, ERROR) from None
    need(len(raw) == entry['size_bytes'] and hashlib.sha256(raw).hexdigest() == entry['raw_sha256'])
    return raw


def _query(db, p, plan, item):
    from .connectors import query_receipt
    blueprint = plan.data['steps'][item.ordinal]
    need(len(item.data['receipt_checks']) <= MAX_CHECKS - 2,
        '原回执核对次数达到完整记录容量，待人工核查；未重发')
    # Reserve room for the largest bounded full response before issuing GET.
    # Lack of durable capacity blocks the read, never truncates its raw body.
    need(len(safe._bytes(item.data)) + 1500000 <= MAX_BYTES,
        '完整原回执记录空间不足，需保留原号人工核查；未截断或重新派发')
    # No locks are carried across the read. No exception can trigger a POST.
    checkpoint(db)
    receipt = None; error = None; captures = []
    def captured(raw, metadata):
        need(not captures and type(raw) is bytes and 0 < len(raw) <= 1024 * 1024)
        _parent_context(db, p, plan)
        actual_plan, actual_items = _inventory(db, p, plan.parent_invocation_id)
        actual_item = next(row for row in actual_items if row.id == item.id)
        entry = {'kind': 'raw_http', 'raw_base64': base64.b64encode(raw).decode('ascii'),
            'raw_sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw),
            'metadata': copy.deepcopy(metadata), 'reader': ownership(db),
            'checked_at': db.scalar(select(func.clock_timestamp())).isoformat()}
        _checked_capture(entry, actual_plan)
        _write_item(db, p, actual_plan, actual_item,
            receipt_checks=[*actual_item.data['receipt_checks'], entry])
        _inventory(db, p, actual_plan.parent_invocation_id)
        checkpoint(db)  # Exact raw HTTP is durable BEFORE connector JSON parse.
        captures.append(entry)
    try:
        from .device_gateway_tools_v269 import applies as managed_device
        if managed_device(blueprint['capability_snapshot']):
            from .models import Capability
            from .device_gateway_http_v269 import query_device_receipt
            actual_cap = scoped(db, Capability, blueprint['capability_id'], p)
            need(actual_cap.content_hash == blueprint['capability_hash']
                and _same(actual_cap.data, blueprint['capability_snapshot']))
            actual_child = _child(db, p, plan, item, blueprint, required=False)
            need(actual_child is not None, '缺少原设备调用，不能使用连接凭据核对回执')
            parsed = query_device_receipt(db, p, actual_cap, blueprint['request_key'],
                invocation=actual_child, on_response=captured)
        else:
            parsed = query_receipt(blueprint['capability_snapshot']['execution'], blueprint['request_key'], on_response=captured)
        need(len(captures) == 1, '缺少完整原HTTP回执，未把202或空响应当作完成')
        strict = reasoning.transport.strict_json(_checked_capture(captures[0], plan).decode('utf-8'))
        need(_same(parsed, strict))
        receipt = _receipt_value(strict, blueprint)
    except (LeaseLost, ExecutionUnauthorized): raise
    except Exception:
        receipt = None; error = '原号回执未能核对，执行结果仍未知，未重发'
    context = _parent_context(db, p, plan)
    plan, rows = _inventory(db, p, plan.parent_invocation_id)
    item = next(row for row in rows if row.id == item.id)
    if item.status in {'succeeded', 'failed', 'not_executed'}: return
    actual = _child(db, p, plan, item, blueprint, required=False)
    entry = {'kind': 'validation', 'raw_sha256': captures[0]['raw_sha256'] if captures else None,
        'receipt': copy.deepcopy(receipt), 'receipt_hash': digest(receipt) if receipt is not None else None,
        'error': error, 'reader': ownership(db), 'checked_at': db.scalar(select(func.clock_timestamp())).isoformat()}
    checks = [*item.data['receipt_checks'], entry]
    if receipt is None or not receipt['terminal'] or actual is None or item.data['dispatch_authorized'] is not True:
        if actual is None: entry['error'] = '原派发调用缺失；不将缺失当作未执行或新建调用'
        elif item.data['dispatch_authorized'] is not True: entry['error'] = '原调用缺少已保存的实际派发授权，未采用回执完成'
        _write_item(db, p, plan, item, status='unknown' if receipt is None or actual is None else 'waiting', receipt_checks=checks)
        return
    original = {'status': actual.status, 'output': copy.deepcopy(actual.data.get('output')), 'error': actual.data.get('error')}
    need(actual.data.get(KEY + '/settled') is None, '原子调用已有结算痕迹但库存不一致，未选择另一个答案')
    if actual.status == 'succeeded':
        if receipt['status'] != 'succeeded' or not _same(actual.data.get('output'), receipt.get('output')):
            entry['error'] = '同步成功原答与终态回执冲突，需人工核查；未覆盖原结果'
            _write_item(db, p, plan, item, status='conflict', receipt_checks=checks)
            raise HTTPException(409, entry['error'])
    actual.status = receipt['status']
    patch = {'output': receipt['output']} if receipt['status'] == 'succeeded' else {'error': receipt.get('error', '原号已终结，未自动重试')}
    actual.data = {**actual.data, **patch}
    evidence = {'schema': CHILD_RECEIPT, 'parent_invocation_id': plan.parent_invocation_id,
        'plan_id': plan.id, 'item_id': item.id, 'invocation_id': actual.id,
        'original_gateway_outcome': original, 'receipt': copy.deepcopy(receipt), 'receipt_hash': digest(receipt),
        'raw_receipt': copy.deepcopy(captures[0]),
        'settled_hash': digest({'status': actual.status, 'output': actual.data.get('output'), 'error': actual.data.get('error')})}
    actual.data = {**actual.data, KEY + '/settled': evidence}; actual.version += 1
    audit(db, p, CHILD_RECEIPT, item.id, evidence)
    _write_item(db, p, plan, item, status=receipt['status'], invocation_id=actual.id,
        receipt_checks=checks, receipt=receipt)
    _inventory(db, p, plan.parent_invocation_id)


def _result(plan, items):
    rows = []
    for item in items:
        blueprint = plan.data['steps'][item.ordinal]; receipt = item.data['receipt']
        need(item.status == 'succeeded' and receipt is not None and item.invocation_id is not None)
        rows.append({'ordinal': item.ordinal, 'capability_id': item.capability_id,
            'capability_hash': blueprint['capability_hash'], 'invocation_id': item.invocation_id,
            'input_hash': item.input_hash, 'receipt_hash': digest(receipt),
            'output_hash': digest(receipt['output']), 'output': copy.deepcopy(receipt['output'])})
    value = {'result': '所选预批准工具方案已取得全部原号终态回执；不代表物理事实或独立验收通过',
        'plan_receipt': {'schema': PLAN + '/receipt', 'plan_id': plan.id, 'plan_hash': plan.record_hash,
            'candidate_ref': plan.data['candidate_ref'], 'status': 'succeeded', 'items': rows,
            'simulation_only': plan.data['source']['simulation_only'], 'physical_state_verified': False,
            'independent_acceptance': False}}
    validate(output_schema(), value); return value


def _continue(db, p, plan):
    while True:
        context = _parent_context(db, p, plan)
        plan, items = _inventory(db, p, plan.parent_invocation_id)
        current = next((row for row in items if row.status != 'succeeded'), None)
        if current is None: return _result(plan, items)
        need(current.status not in {'failed', 'not_executed', 'conflict'},
            '原工具方案有终态失败、永久未执行或原答冲突；不自动重试、换号或选择另一个结果')
        if current.status != 'prepared':
            _query(db, p, plan, current)
            _plan, after = _inventory(db, p, plan.parent_invocation_id)
            after_status = next(row for row in after if row.id == current.id).status
            if after_status == 'succeeded': continue
            need(after_status not in {'failed', 'not_executed', 'conflict'}, '原工具已明确失败、永久未执行或回执冲突；未重试')
            raise service.PendingService('原工具动作仍待原号终态回执核对；未重派')
        need(all(row.status == 'succeeded' for row in items[:current.ordinal]))
        # A cold prepared plan may predate this gate. Validate the entire
        # closed candidate inventory before its first/next never-sent item,
        # rather than dispatching a SIM prefix before discovering a real target.
        _check_candidates(db, p, context, plan.data['inputs'])
        _budget(db, p, context, sum(row.status == 'prepared' for row in items))
        blueprint = plan.data['steps'][current.ordinal]
        target = _target(db, p, context['capability'], blueprint, simulation_only=_engineering_scope(context))
        need(_same(_bound(target, blueprint, context['task'], blueprint['request_key']), blueprint['inputs']))
        lease = ownership(db); need(lease.get('job_id') == plan.job_id and bool(lease.get('lease_token')))
        _write_item(db, p, plan, current, status='dispatching', dispatch_owner=lease)
        _inventory(db, p, plan.parent_invocation_id)
        checkpoint(db)  # Original intent/audit is durable BEFORE gateway invocation.
        from .gateway import invoke
        invoke(db, p, target.id, blueprint['agent_role'], copy.deepcopy(blueprint['inputs']),
            blueprint['request_key'], approval=make_child_approval(plan, current))
        # Even a synchronous HTTP answer is not a terminal supplier receipt.
        _query(db, p, plan, current)
        _plan, after = _inventory(db, p, plan.parent_invocation_id)
        after_status = next(row for row in after if row.id == current.id).status
        need(after_status not in {'failed', 'not_executed', 'conflict'}, '原工具已明确失败、永久未执行或回执冲突；未重试')
        if after_status != 'succeeded':
            raise service.PendingService('原工具动作已提交，等待核对同一原号回执；未宣布完成或重发')


def execute(db, p, cap, inputs, key, approval):
    value = _inputs(inputs); context = validate_call_approval(db, p, cap, 'business', value, key, approval)
    parent = service._call(db, p, context, key, value, approval)
    _lock(db, p, parent.id); plan, _items = _inventory(db, p, parent.id)
    if plan is None: plan = _create(db, p, context, parent, value, key, approval)
    return _continue(db, p, plan)


def reconcile(db, p, cap, inputs, key, approval, call):
    value = _inputs(inputs); context = validate_call_approval(db, p, cap, 'business', value, key, approval)
    parent = service._call(db, p, context, key, value, approval, supplied=call, allow_unknown=True)
    _lock(db, p, parent.id); plan, _items = _inventory(db, p, parent.id)
    need(plan is not None, '原工具计划不存在；不借核对入口新建或重发')
    return _continue(db, p, plan)


def saved_receipt(db, p, cap, inputs, key, approval, call):
    """Cold complete-parent proof. No live/Goal/lease, HTTP or continuation.

    A succeeded parent cannot hide deleted child rows, settlements, raw replies
    or independent audits. Frozen capability snapshots remain history even if
    a current published version or executor is no longer available.
    """
    manifest = cap if type(cap) is dict else cap.data
    validate_manifest(manifest)
    actual = scoped(db, Invocation, call.id, p)
    plan, rows = _inventory(db, p, actual.id)
    need(plan is not None and actual.status == 'succeeded' and key == actual.idempotency_key == plan.request_key
        and actual.capability_id == plan.capability_id and digest(manifest) == actual.data['capability_hash']
        and _same(inputs, plan.data['inputs']) and _same(approval, plan.data['approval'])
        and _same(actual.data.get('approval'), approval) and digest(inputs) == plan.input_hash
        and all(row.status == 'succeeded' for row in rows))
    result = _result(plan, rows)
    need(_same(result, actual.data.get('output')), '原成功父结果与完整子调用、原回执或独立审计不一致；未继续流程')
    return copy.deepcopy(result)


def retained_marker_shape(call):
    """Routing hint only. Reconciliation still cold-checks every original row."""
    if call is None or type(call.data) is not dict: return False
    try:
        marker = call.data[KEY]; snapshot = call.data['capability_snapshot']
        validate_manifest(snapshot)
        return (type(marker) is dict and set(marker) == {'schema', 'plan_id', 'plan_hash'}
            and marker['schema'] == PLAN and safe.identity(marker['plan_id']) == marker['plan_id']
            and type(marker['plan_hash']) is str and safe.SHA.fullmatch(marker['plan_hash']) is not None
            and digest(snapshot) == call.data['capability_hash'] and call.data['side_effect'] is True
            and call.data['role'] == 'business' and call.data['approval']['schema'] == service.APPROVAL)
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError): return False
