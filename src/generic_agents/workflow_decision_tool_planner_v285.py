"""Audited V284 local routing with explicit non-executing review branches.

This module records an original graph Invocation and one local plan. It creates
no model call, observation plan, acquisition, budget, receipt or permission.
Only the acquire branch carries the original V196 context onward; every other
branch retains the answer for ordinary human work without renting authority.
The V277/V278 contracts, records and failed Jobs remain separate and immutable.
"""
import copy
import sys
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select

from . import business_decision_contracts_v284 as decision
from . import business_decision_runtime_v284 as reasoning
from . import business_decision_sources_v276 as sources
from . import workflow_graph as graph_family
from . import workflow_graph_scope as scope
from . import workflow_service_v195 as service
from . import workflow_service_context_v196 as resense
from . import workflow_task_context_v196 as reader
from .business_flow_review_store import _bytes, _same, identity
from .contracts import Manifest, digest
from .models import Audit, Capability, DataGap, Invocation, Job, Task
from .services import audit, validate

OP = 'plan_business_decision_review_v285'
CONTRACT = 'workflow-decision-review-plan-contract-v285'
RESULT = 'workflow-decision-review-plan-v285'
PRODUCER = 'workflow-decision-review-producer-v285'
STATE = 'workflow-decision-review-plan-state-v285'
KEY = 'workflow_decision_review_plan_v285'
CREATED = 'workflow.decision_review_plan.created_v285'
MAX_BYTES = 4 * 1024 * 1024
ERROR = '原决策、事实缺口、补采能力或本地计划审计无法核对；未派发、重跑模型或生成观测'
REASON_CODES = ('acquisition_ready', 'current_facts_ready', 'no_recommendation',
    'selection_needs_review', 'conflicting_evidence', 'facts_missing',
    'source_review_incomplete', 'unsupported_decision_intent', 'unsupported_acquisition_scope')
FOREIGN_STATE_KEYS = ('workflow_decision_plan_v277',)


def need(ok, message=ERROR, status=409):
    if not ok:
        raise HTTPException(status, message)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, IndexError,
                UnicodeError, OverflowError, RecursionError):
            need(False)
    return wrapped


def _pin_schema():
    return service._object({'capability_id': {'type': 'string', 'pattern': service.UUID_PATTERN},
        'capability_hash': {'type': 'string', 'pattern': service.HASH_PATTERN},
        'release_version': {'type': 'string', 'minLength': 1, 'maxLength': 100}})


def input_schema():
    value = decision.output_schema()
    definitions = value.pop('$defs', {})
    return {**service._object({'decision_result': value}), '$defs': definitions}


def _producer_schema():
    ident = {'type': 'string', 'pattern': service.UUID_PATTERN}
    sha = {'type': 'string', 'pattern': service.HASH_PATTERN}
    return service._object({'schema': {'const': PRODUCER},
        **{key: copy.deepcopy(ident) for key in ('workspace_id', 'task_id', 'job_id', 'invocation_id', 'actor_id', 'capability_id')},
        'activation_id': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'node_id': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        **{key: copy.deepcopy(sha) for key in ('capability_hash', 'input_hash', 'output_hash',
            'request_key_hash', 'capture_hash', 'decision_input_hash', 'frame_hash', 'sources_hash')}})


def output_schema():
    value = service._object({'schema': {'const': RESULT},
        'route': {'enum': ['acquire', 'continue', 'no_action', 'review_required']},
        'reason_code': {'enum': list(REASON_CODES)},
        'acquisition_required': {'type': 'boolean'}, 'no_gap': {'type': 'boolean'},
        'selected_candidate_ref': {'type': ['string', 'null'], 'pattern': r'^[a-z][a-z0-9_]{0,63}$'},
        'missing_requirement_refs': {'type': 'array', 'maxItems': 30, 'uniqueItems': True,
            'items': {'type': 'string', 'pattern': r'^[a-z][a-z0-9_]{0,63}$'}},
        'conflict_refs': {'type': 'array', 'maxItems': 32, 'uniqueItems': True,
            'items': {'type': 'string', 'pattern': r'^[a-z][a-z0-9_]{0,63}$'}},
        'resense_context': {**reader.context_schema(), 'type': ['object', 'null']},
        'resense_capability': {**_pin_schema(), 'type': ['object', 'null']},
        'producer': _producer_schema(), 'simulation_only': {'type': 'boolean'},
        **{key: {'type': 'boolean', 'const': False} for key in ('can_dispatch', 'permissions_verified',
            'physical_state_verified', 'independent_acceptance')}})
    value['allOf'] = [{'if': {'properties': {'route': {'const': 'acquire'}}},
        'then': {'properties': {'acquisition_required': {'const': True}, 'no_gap': {'const': False},
            'reason_code': {'const': 'acquisition_ready'}, 'selected_candidate_ref': {'type': 'string'},
            'missing_requirement_refs': {'minItems': 1}, 'resense_context': {'type': 'object'},
            'resense_capability': {'type': 'object'}}},
        'else': {'properties': {'acquisition_required': {'const': False},
            'resense_context': {'type': 'null'}, 'resense_capability': {'type': 'null'}}}},
        {'if': {'properties': {'missing_requirement_refs': {'maxItems': 0}}},
            'then': {'properties': {'no_gap': {'const': True}}},
            'else': {'properties': {'no_gap': {'const': False}}}},
        {'if': {'properties': {'route': {'const': 'continue'}}},
            'then': {'properties': {'reason_code': {'const': 'current_facts_ready'},
                'selected_candidate_ref': {'type': 'string'}, 'missing_requirement_refs': {'maxItems': 0}}}},
        {'if': {'properties': {'route': {'const': 'no_action'}}},
            'then': {'properties': {'reason_code': {'const': 'no_recommendation'},
                'selected_candidate_ref': {'type': 'null'}, 'missing_requirement_refs': {'maxItems': 0}}}},
        {'if': {'properties': {'route': {'const': 'review_required'}}},
            'then': {'properties': {'reason_code': {'enum': list(REASON_CODES[3:])}}}}]
    return value


def _inputs(value):
    _bytes(value, MAX_BYTES); validate(input_schema(), value)
    return copy.deepcopy(value)


@checked
def validate_manifest(manifest):
    value = manifest.model_dump(mode='json') if isinstance(manifest, Manifest) else copy.deepcopy(manifest)
    Manifest.model_validate(value)
    spec = value['specification']
    need(type(spec) is dict and set(spec) == {'schema', 'resense_tool_pin'}
        and spec['schema'] == CONTRACT, status=422)
    validate(_pin_schema(), spec['resense_tool_pin']); identity(spec['resense_tool_pin']['capability_id'])
    need(value['kind'] == 'tool' and value['version'] == '1.0.0' and value['allowed_agent_roles'] == ['business']
        and _same(value['execution'], {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': OP, 'side_effect': True}) and _same(value['input_schema'], input_schema())
        and _same(value['output_schema'], output_schema())
        and value['dependencies'] == [spec['resense_tool_pin']['capability_id']]
        and value['data_policy'].get('cloud_allowed') is False
        and type(value['budget'].get('max_attempts')) is int and value['budget']['max_attempts'] == 1,
        '本地决策计划须保留原graph服务审批、固定补采能力和单次尝试；不能执行HTTP动作', 422)
    return value


def _audit(db, p, action, object_id, actor_id, data):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == object_id).limit(2)))
    need(len(rows) == 1 and rows[0].actor_id == actor_id and _same(rows[0].data, data))


def _success_audit(db, p, call, job, activation, output):
    """A real original success or the server's audited same-answer recovery."""
    from .business_reasoning_protocols_v276 import recovery_record
    marker, action = recovery_record(reasoning.ADAPTER)
    original = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'capability.invoke', Audit.object_id == call.id).limit(2)))
    recovery = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == call.id).limit(2)))
    saved = call.data.get(marker)
    if saved is None:
        need(not recovery and len(original) == 1 and original[0].actor_id == job.data['actor_id']
            and _same(original[0].data, {'status': 'succeeded'}))
        return
    need(type(saved) is dict and set(saved) == {'previous_status', 'output_hash', 'recovered_at'}
        and type(saved['previous_status']) is str and saved['previous_status']
        and saved['previous_status'] != 'succeeded' and saved['output_hash'] == digest(output)
        and type(saved['recovered_at']) is str and bool(saved['recovered_at'])
        and len(original) <= 1 and len(recovery) == 1
        and all(row.actor_id == job.data['actor_id']
            and _same(row.data, {'status': saved['previous_status']}) for row in original)
        and recovery[0].actor_id == job.data['actor_id']
        and _same(recovery[0].data, {'job_id': job.id, 'activation_id': activation['id'],
            'previous_status': saved['previous_status'], 'output_hash': digest(output)}))


def _native_audits(db, p, task, job, activation, call, inputs):
    records = [('workflow.graph.node_completed', 'activation_id', activation['id'],
        {'activation_id': activation['id'], 'invocation_id': call.id}),
        ('workflow.graph.dispatch_authorized', 'job_id', job.id, {'activation_id': activation['id'],
            'job_id': job.id, 'epoch': activation['epoch'], 'input_hash': digest(inputs)})]
    for action, field, identity_value, expected in records:
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == action, Audit.object_id == task.id,
            Audit.data[field].as_string() == identity_value).limit(2)))
        need(len(rows) == 1 and rows[0].actor_id == job.data['actor_id'] and _same(rows[0].data, expected))


def _producer(db, p, task, job, result):
    """Exact native edge, original paid answer and real completed producer."""
    actual, _release, _business, _graph, state, current, _node = scope._frozen(
        db, p, task.id, job.data['activation_id'])
    need(actual.id == task.id)
    edge = current['inputs_snapshot']['bindings'].get('decision_result')
    need(type(edge) is dict and edge.get('missing') is False and edge.get('producer_activation_id'))
    activation = state['activations'][edge['producer_activation_id']]
    need(activation['status'] == 'completed' and activation['epoch'] == current['epoch']
        and _same(activation['output'], result) and edge['value_hash'] == digest(result))
    transport = actual.data['graph_jobs'][activation['id']]
    original_job = sources.fresh(db, p, Job, transport['job_id'])
    call = sources.fresh(db, p, Invocation, transport['invocation_id'])
    cap = sources.fresh(db, p, Capability, call.capability_id)
    original_task, _r, _b, _g, original_state, original_activation, node = scope._frozen(
        db, p, task.id, activation['id'])
    scope._job_matches(original_task, original_job, original_state, original_activation, node)
    need(call.status == 'succeeded' and original_job.data['task_id'] == task.id
        and original_job.kind == scope.KIND and call.data.get('role') == 'business'
        and call.data.get('side_effect') is False and call.data.get('approval') is None
        and node['actor_id'] == original_job.data['actor_id']
        and cap.data['execution'].get('adapter') == reasoning.ADAPTER
        and node['execution']['adapter'] == reasoning.ADAPTER
        and node['execution']['agent_role'] == 'business'
        and cap.id == node['execution']['capability_id'] == call.capability_id
        and cap.content_hash == node['execution']['capability_hash'] == call.data['capability_hash']
        and cap.kind == 'tool' and cap.content_hash == cap.validated_hash == digest(cap.data)
        and cap.release_version == cap.data['version'] == node['execution']['release_version']
        and _same(cap.data['input_schema'], node['execution']['input_schema'])
        and _same(cap.data['output_schema'], node['execution']['output_schema'])
        and ('inputs' not in call.data or _same(call.data['inputs'], original_job.data['inputs']))
        and _same(call.data.get('output'), result))
    reasoning._manifest_shape(cap.data)
    if original_job.status == 'running':
        raise reader.ProducerPending(original_job.id, call.id)
    need(original_job.status == 'succeeded')
    saved = reasoning.saved_result(db, p, original_task, original_job, call, cap, original_job.data['inputs'])
    need(_same(saved, result))
    owner = transport.get('dispatch_ownership'); authorization = transport.get('dispatch_authorization')
    need(type(owner) is dict and owner.get('job_id') == original_job.id and bool(owner.get('lease_token'))
        and type(authorization) is dict and authorization.get('job_id') == original_job.id
        and authorization.get('lease_token') == owner['lease_token']
        and authorization.get('actor_id') == original_job.data['actor_id']
        and authorization.get('epoch') == activation['epoch']
        and authorization.get('input_hash') == digest(original_job.data['inputs'])
        and authorization.get('capability_hash') == cap.content_hash
        and transport.get('phase') == 'succeeded' and transport.get('dispatch_committed') is True
        and transport.get('output_hash') == digest(result))
    graph_family.check_invocation(call, p, cap, node, original_job.data['inputs'], original_job, owner)
    _native_audits(db, p, original_task, original_job, activation, call, original_job.data['inputs'])
    _success_audit(db, p, call, original_job, activation, result)
    frozen = reasoning._checked_state_for_actor(db, p, cap, original_job.data['inputs'],
        call.idempotency_key, call, actor_id=original_job.data['actor_id'])
    frame = frozen['request']['frame']
    pin = {'schema': PRODUCER, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'job_id': original_job.id, 'invocation_id': call.id, 'activation_id': activation['id'],
        'node_id': node['id'], 'actor_id': original_job.data['actor_id'], 'capability_id': cap.id,
        'capability_hash': cap.content_hash, 'input_hash': call.data['input_hash'], 'output_hash': digest(result),
        'request_key_hash': digest(call.idempotency_key), 'capture_hash': frozen['capture']['raw_sha256'],
        'decision_input_hash': decision.input_hash(frame['decision_input']),
        'frame_hash': frozen['request']['frame_hash'], 'sources_hash': frame['sources']['sources_hash']}
    validate(_producer_schema(), pin)
    return copy.deepcopy(pin), copy.deepcopy(frame)


def producer_integrity_pin(db, p, task, job, inputs):
    value = _inputs(inputs)
    actual, _r, _b, graph, state, activation, node = scope._frozen(db, p, task.id, job.data['activation_id'])
    scope._job_matches(actual, job, state, activation, node)
    from .full_semantic_graph_v1 import ability_inputs
    need(actual.id == task.id and job.data['actor_id'] == p.user_id
        and _same(ability_inputs(graph, state, activation['id']), value) and _same(job.data['inputs'], value))
    pin, frame = _producer(db, p, actual, job, value['decision_result'])
    _selection(value['decision_result'], frame)
    return pin


def _selection(result, frame):
    value = decision.check_result(frame['decision_input'], result)
    selected = value['model_value']['recommendation']['candidate_ref']
    candidate = None if selected is None else next(row for row in frame['decision_input']['candidates']
        if row['candidate_ref'] == selected)
    return value, candidate


def _acquisition_scope(cap, candidate, missing, frame):
    """None is an unsupported legitimate plan, never a replacement context.

    The authenticated producer has already cold-checked the complete Frame.
    Malformed schemas/refs remain errors; scope coverage alone cannot authorize
    a device. Actual current policy/Gap checks occur only in _plan's acquire.
    """
    requirements = [row for row in frame['decision_input']['requirements'] if row['requirement_ref'] in missing]
    if not requirements or not all(row['required_kind'] in {'observation', 'observed_or_recorded'} for row in requirements):
        return None
    wanted = cap.data['specification']['resense_tool_pin']
    mappings = [row for row in candidate['requirement_mappings'] if row['requirement_ref'] in missing]
    if ({row['requirement_ref'] for row in mappings} != set(missing)
            or not all(row['delivery'] == 'requested_observation' for row in mappings)):
        return None
    refs = {row['capability_ref'] for row in mappings}
    if set(candidate['capability_refs']) != refs:
        return None  # Do not silently omit additional selected capabilities.
    capabilities = [row for row in frame['capability_pins']['capabilities'] if row['capability_ref'] in refs]
    if len(capabilities) != 1 or len(refs) != 1:
        return None
    selected = capabilities[0]
    target = {key: selected[key] for key in ('capability_id', 'capability_hash', 'release_version')}
    execution = selected['data']['execution']
    if (not _same(target, wanted) or selected['delivery'] != 'requested_observation'
            or selected['action_kind'] != 'information_acquisition'
            or execution.get('adapter') != 'workflow_service' or execution.get('operation') != resense.OP):
        return None
    resense.validate_manifest(selected['data'])
    contexts = frame['sources']['data']['context_result']['candidates']
    matches = [row for row in contexts if all(row['gap']['entity_id'] == req['entity_id']
        and req['field'] in row['gap']['fields'] for req in requirements)]
    if len(matches) != 1:
        return None
    context = matches[0]
    if not any(row['context_hash'] == context['context_hash'] and _same(row['context'], context)
            for row in frame['capability_pins']['policies']):
        return None
    return copy.deepcopy(context), target


@checked
def _route(cap, result, frame, producer):
    value, candidate = _selection(result, frame)
    review = value['mechanical_review']; missing = review['missing_requirement_refs']
    conflict_refs = [row['conflict_ref'] for row in review['conflicts']]
    complete = all(row['assessment'] == 'reviewed' for row in review['sources'])
    sufficient = review['current_facts_sufficient'] is True and review['fact_consistency'] == 'consistent' and not missing
    route, reason, context, target = 'review_required', 'selection_needs_review', None, None
    if conflict_refs:
        reason = 'conflicting_evidence'
    elif value['decision_intent'] != 'information_acquisition':
        reason = 'unsupported_decision_intent'
    elif candidate is None:
        if sufficient and complete:
            route, reason = 'no_action', 'no_recommendation'
        else:
            reason = 'source_review_incomplete' if not complete else 'facts_missing'
    else:
        selected = next(row for row in review['candidates'] if row['candidate_ref'] == candidate['candidate_ref'])
        if (selected['applicability'] != 'applicable' or selected['support'] != 'supported'
                or selected['blocking_conditions'] or value['review_state'] == 'review_required'):
            reason = 'selection_needs_review'
        elif not complete:
            reason = 'source_review_incomplete'
        elif value['review_state'] != 'ready_for_review':
            reason = 'facts_missing'
        elif not missing:
            if sufficient:
                route, reason = 'continue', 'current_facts_ready'
            else:
                reason = 'facts_missing'
        else:
            scope_value = _acquisition_scope(cap, candidate, missing, frame)
            if scope_value is None:
                reason = 'unsupported_acquisition_scope'
            else:
                route, reason = 'acquire', 'acquisition_ready'
                context, target = scope_value
    output = {'schema': RESULT, 'route': route, 'reason_code': reason,
        'acquisition_required': route == 'acquire', 'no_gap': not bool(missing),
        'selected_candidate_ref': value['model_value']['recommendation']['candidate_ref'],
        'missing_requirement_refs': copy.deepcopy(missing), 'conflict_refs': conflict_refs,
        'resense_context': context, 'resense_capability': target, 'producer': copy.deepcopy(producer),
        'simulation_only': frame['sources']['data']['packet']['simulation_only'],
        'can_dispatch': False, 'permissions_verified': False, 'physical_state_verified': False,
        'independent_acceptance': False}
    validate(output_schema(), output)
    return output


def producer_wait_pin(db, p, task, job, inputs):
    pin = producer_integrity_pin(db, p, task, job, inputs)
    # Eligibility is checked again with the real service capability at approval.
    return pin


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    current = service.validate_call_approval(db, p, cap, role, inputs, key, approval, _variant=sys.modules[__name__])
    producer, frame = _producer(db, p, current['task'], current['job'], inputs['decision_result'])
    _route(current['capability'], inputs['decision_result'], frame, producer)
    return current


def _plan(db, p, current, inputs):
    producer, frame = _producer(db, p, current['task'], current['job'], inputs['decision_result'])
    output = _route(current['capability'], inputs['decision_result'], frame, producer)
    if output['route'] == 'acquire':
        context = output['resense_context']
        need(_same(reader.checked_context(db, p, context, task=current['task'], dispatch=False), context))
        gap = sources.fresh(db, p, DataGap, context['gap']['gap_id'])
        need(gap.status not in {'resolved', 'cancelled', 'needs_review', 'pending_delivery'},
            '原缺口已完成、停止或待接收；需核对资料，未再安排采集')
        target = sources.fresh(db, p, Capability, output['resense_capability']['capability_id'])
        need(target.status == 'published' and target.content_hash == target.validated_hash == digest(target.data)
            and target.content_hash == output['resense_capability']['capability_hash']
            and target.release_version == output['resense_capability']['release_version'])
        resense.validate_manifest(target.data)
    return output


def _record_anchor(record):
    return {'schema': STATE, 'record_hash': record['record_hash'], 'record': copy.deepcopy(record)}


def retained_marker_shape(call):
    """Route only an original saved local plan to full audited reconciliation."""
    try:
        if call is None or call.status != 'unknown' or type(call.data) is not dict:
            return False
        if any(key in call.data for key in FOREIGN_STATE_KEYS):
            return False
        snapshot = call.data['capability_snapshot']; validate_manifest(snapshot)
        record = call.data[KEY]; _bytes(record, MAX_BYTES)
        return (type(record) is dict and record['schema'] == STATE
            and record['workspace_id'] == call.workspace_id and record['invocation_id'] == call.id
            and record['capability_id'] == call.capability_id and record['capability_hash'] == digest(snapshot)
            == call.data['capability_hash'] and record['job_id'] == call.data['job_id']
            and record['input_hash'] == digest(call.data['inputs']) == call.data['input_hash']
            and record['approval_hash'] == digest(call.data['approval'])
            and record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'}))
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError):
        return False


@checked
def _state(db, p, call, task, job, cap, inputs, approval):
    need(not any(key in call.data for key in FOREIGN_STATE_KEYS), '原调用保留另一版本的本地计划；未迁移或重建')
    record = call.data.get(KEY)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == CREATED, Audit.object_id == call.id).limit(2)))
    if record is None:
        need(not events, '原计划审计存在但记录缺失；未重建计划')
        return None
    _bytes(record, MAX_BYTES)
    need(type(record) is dict and set(record) == {'schema', 'workspace_id', 'task_id', 'job_id', 'invocation_id',
        'actor_id', 'capability_id', 'capability_hash', 'input_hash', 'approval_hash', 'output', 'record_hash'}
        and record['schema'] == STATE and record['workspace_id'] == p.workspace_id == call.workspace_id
        and record['task_id'] == task.id and record['job_id'] == job.id and record['invocation_id'] == call.id
        and record['actor_id'] == job.data['actor_id']
        and record['capability_id'] == call.capability_id == cap.id
        and record['capability_hash'] == cap.content_hash == call.data['capability_hash']
        and record['input_hash'] == digest(inputs) == call.data['input_hash']
        and record['approval_hash'] == digest(approval) and _same(call.data['approval'], approval)
        and _same(call.data['inputs'], inputs) and record['record_hash'] == digest({
            key: value for key, value in record.items() if key != 'record_hash'})
        and len(events) == 1 and events[0].actor_id == job.data['actor_id']
        and _same(events[0].data, _record_anchor(record)))
    validate(output_schema(), record['output'])
    return copy.deepcopy(record)


def execute(db, p, cap, inputs, key, approval):
    value = _inputs(inputs); current = validate_call_approval(db, p, cap, 'business', value, key, approval)
    call = service._call(db, p, current, key, value, approval)
    existing = _state(db, p, call, current['task'], current['job'], cap, value, approval)
    if existing is not None:
        return existing['output']
    output = _plan(db, p, current, value)
    record = {'schema': STATE, 'workspace_id': p.workspace_id, 'task_id': current['task'].id,
        'job_id': current['job'].id, 'invocation_id': call.id, 'actor_id': p.user_id,
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'input_hash': digest(value),
        'approval_hash': digest(approval), 'output': output}
    record['record_hash'] = digest(record); _bytes(record, MAX_BYTES)
    call.data = {**call.data, KEY: record}
    audit(db, p, CREATED, call.id, _record_anchor(record)); db.flush()
    return copy.deepcopy(output)


def reconcile(db, p, cap, inputs, key, approval, call):
    value = _inputs(inputs); current = validate_call_approval(db, p, cap, 'business', value, key, approval)
    actual = service._call(db, p, current, key, value, approval, supplied=call, allow_unknown=True)
    record = _state(db, p, actual, current['task'], current['job'], cap, value, approval)
    need(record is not None, '原本地计划不存在；不借核对入口重建或推进')
    return record['output']


@checked
def saved_result(db, p, task, job, call, cap, inputs):
    """Read-only successful local plan proof; never require a current Gap."""
    actual = sources.fresh(db, p, Invocation, call.id); actual_job = sources.fresh(db, p, Job, job.id)
    actual_task = sources.fresh(db, p, Task, task.id); value = _inputs(inputs)
    validate_manifest(cap.data)
    need(actual.status == actual_job.status == 'succeeded' and actual.capability_id == cap.id
        and actual.idempotency_key == actual_job.data['invocation_key'] == actual_job.idempotency_key
        and actual_job.data['task_id'] == actual_task.id
        and actual.data.get('job_id') == actual_job.id and actual.data.get('role') == 'business'
        and actual.data.get('side_effect') is True and _same(actual_job.data['inputs'], value)
        and _same(actual.data, call.data) and _same(actual_job.data, job.data))
    original_task, _r, _b, _g, state, activation, node = scope._frozen(db, p, task.id, actual_job.data['activation_id'])
    transport = scope._job_matches(original_task, actual_job, state, activation, node)
    need(activation['status'] == 'completed' and transport.get('phase') == 'succeeded'
        and transport.get('invocation_id') == actual.id and node['execution']['capability_id'] == cap.id
        and node['execution']['capability_hash'] == cap.content_hash
        and cap.content_hash == digest(cap.data) == actual.data['capability_hash'])
    approval = service.make_approval(original_task, actual_job, node, value)
    owner = transport.get('dispatch_ownership'); authorization = transport.get('dispatch_authorization')
    need(type(owner) is dict and type(authorization) is dict and owner.get('job_id') == actual_job.id
        and authorization.get('job_id') == actual_job.id and authorization.get('lease_token') == owner.get('lease_token')
        and authorization.get('epoch') == activation['epoch'] and authorization.get('actor_id') == actual_job.data['actor_id']
        and authorization.get('input_hash') == digest(value) and authorization.get('capability_hash') == cap.content_hash)
    graph_family.check_invocation(actual, p, cap, node, value, actual_job, owner, approval=approval)
    _native_audits(db, p, original_task, actual_job, activation, actual, value)
    record = _state(db, p, actual, original_task, actual_job, cap, value, approval)
    need(record is not None)
    producer, frame = _producer(db, p, original_task, actual_job, value['decision_result'])
    expected = _route(cap, value['decision_result'], frame, producer)
    need(_same(record['output'], expected) and _same(actual.data.get('output'), expected)
        and _same(activation['output'], expected) and transport.get('output_hash') == digest(expected))
    _audit(db, p, 'capability.invoke', actual.id, actual_job.data['actor_id'], {'status': 'succeeded'})
    return copy.deepcopy(expected)


def saved_receipt(db, p, cap, inputs, key, approval, call):
    job = sources.fresh(db, p, Job, call.data['job_id'])
    task = sources.fresh(db, p, Task, job.data['task_id'])
    need(key == call.idempotency_key and _same(approval, call.data['approval']))
    return saved_result(db, p, task, job, call, cap, inputs)


