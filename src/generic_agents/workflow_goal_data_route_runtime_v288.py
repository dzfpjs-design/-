"""Local V288 service over an actual completed V196 graph producer.

The fixed V288 family and the ordinary service catalog must be installed by
the server. Neither hashes nor this result confer dispatch/Goal/ACK authority.
Only the original Invocation receives one local immutable record and Audit;
reconciliation reads that record. There is no model, query execution, sensing
call, acquisition, receiver or replacement Job in this implementation.
"""
import copy
import sys
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select

from . import workflow_goal_data_policy_v288 as policy
from . import workflow_goal_evaluation_contracts_v288 as evaluation
from . import workflow_task_context_v196 as reader
from . import workflow_service_v195 as service
from . import workflow_graph as graph_runtime, workflow_graph_scope as scope
from .business_flow_review_store import _bytes, _same
from .contracts import Manifest, digest
from .models import Audit, Capability, DataGap, DataRun, Invocation, Job, ObservationPlan, Task
from .services import audit, validate

OP = policy.OP
CONTRACT = 'workflow-goal-data-route-contract-v288'
RESULT = 'workflow-goal-data-plan-v288'
PRODUCER = 'workflow-goal-data-reader-producer-v288'
STATE = 'workflow-goal-data-route-state-v288'
KEY = 'workflow_goal_data_route_v288'
CREATED = 'workflow.goal_data_route.created_v288'
BINDING = 'authored-goal-data-workflow-binding-v288'
MAX_BYTES = 4 * 1024 * 1024
ERROR = '原资料读取、正式目标、本地准备策略或独立服务记录无法核对；未发起采集、模型或收件'
FOREIGN = ('workflow_decision_plan_v277', 'workflow_decision_review_plan_v285', 'service_state',
    'business_reasoning_v197', 'business_decision_v276', 'business_decision_v284')


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


def input_schema():
    return service._object({'context_result': reader.output_schema()})


def _producer_schema():
    ident = {'type': 'string', 'pattern': service.UUID_PATTERN}
    sha = {'type': 'string', 'pattern': service.HASH_PATTERN}
    return service._object({'schema': {'const': PRODUCER},
        **{key: copy.deepcopy(ident) for key in ('workspace_id', 'task_id', 'job_id', 'invocation_id', 'actor_id', 'capability_id')},
        'activation_id': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'node_id': {'const': 'context_reader'},
        **{key: copy.deepcopy(sha) for key in ('capability_hash', 'input_hash', 'output_hash', 'request_key_hash',
            'graph_hash', 'source_binding_hash', 'goal_snapshot_hash', 'preparation_policy_hash')}})


def output_schema():
    # Keep an explicit root object for the existing whole-output projection.
    # Cross-field consistency is checked by checked_output at every read.
    return service._object({'schema': {'const': RESULT},
        'route': {'enum': ['data_ready', 'acquire', 'review_required']}, 'decision': policy.output_schema(),
        'resense_context': {**reader.context_schema(), 'type': ['object', 'null']}, 'producer': _producer_schema(),
        **{key: {'const': False} for key in policy.FLAGS}})


@checked
def checked_output(value):
    """Closed output plus route/context coherence; no authority or IO."""
    _bytes(value, MAX_BYTES); validate(output_schema(), value)
    decision = value['decision']; producer = value['producer']
    need(value['route'] == decision['route']
        and producer['output_hash'] == decision['reader_result_hash']
        and producer['preparation_policy_hash'] == decision['preparation_policy_hash'])
    context = value['resense_context']
    if value['route'] == 'acquire':
        need(type(context) is dict)
        reader._check_context(context)
        reference = {'context_hash': context['context_hash'], 'query_id': context['query']['query_id'],
            'query_request_hash': context['query']['request_hash'], 'gap_id': context['gap']['gap_id'],
            'entity_id': context['gap']['entity_id'], 'policy_id': context['policy']['policy_id'],
            'approved_hash': context['policy']['approved_hash']}
        need(_same(decision['context_ref'], reference))
    else:
        need(context is None)
    return copy.deepcopy(value)


def _inputs(value):
    _bytes(value, MAX_BYTES); validate(input_schema(), value)
    reader.checked_result(value['context_result'])
    return copy.deepcopy(value)


@checked
def validate_manifest(manifest):
    value = manifest.model_dump(mode='json') if isinstance(manifest, Manifest) else copy.deepcopy(manifest)
    Manifest.model_validate(value)
    need(value['kind'] == 'tool' and value['version'] == '1.0.0' and value['allowed_agent_roles'] == ['business']
        and _same(value['execution'], {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': OP, 'side_effect': True})
        and value['specification'] == {'schema': CONTRACT} and value['dependencies'] == []
        and _same(value['input_schema'], input_schema()) and _same(value['output_schema'], output_schema())
        and value['data_policy'].get('cloud_allowed') is False
        and type(value['budget'].get('max_attempts')) is int and value['budget']['max_attempts'] == 1,
        '本地资料路由须使用固定原读取契约、原服务审批及单次尝试；不接受模型或外部动作协议', 422)
    return value


def _fresh(db, p, cls, identity):
    row = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
        .execution_options(populate_existing=True))
    need(row is not None)
    return row


def _audit(db, p, action, object_id, actor_id, data):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == object_id).limit(2)))
    need(len(rows) == 1 and rows[0].actor_id == actor_id and _same(rows[0].data, data))


def _native_audits(db, p, task, job, activation, call, inputs):
    for action, field, identity, data in (
        ('workflow.graph.dispatch_authorized', 'job_id', job.id, {'activation_id': activation['id'],
            'job_id': job.id, 'epoch': activation['epoch'], 'input_hash': digest(inputs)}),
        ('workflow.graph.node_completed', 'activation_id', activation['id'],
            {'activation_id': activation['id'], 'invocation_id': call.id})):
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == action, Audit.object_id == task.id, Audit.data[field].as_string() == identity).limit(2)))
        need(len(rows) == 1 and rows[0].actor_id == job.data['actor_id'] and _same(rows[0].data, data))


def _binding(task, release, business, graph, node):
    # Fixed server family only. A missing installation cannot be supplied as a
    # callback/module/path by a caller or disguised as an older translation.
    try:
        from .authored_goal_data_workflow_store_v288 import check_binding
    except ImportError:
        need(False, '本地资料准备的固定流程族尚未安装；原输入保留，未获得执行资格')
    value = graph_runtime.business_binding(release, business)
    need(value.get('schema') == BINDING and value.get('workspace_id') == task.workspace_id)
    checked_binding = check_binding(value)
    need(_same(value, checked_binding) and _same(value['graph'], graph)
        and node['id'] == 'local_prepare' and _same(value['capability_pins']['local_prepare'], node['execution'])
        and value['service_delegations']['local_prepare'] == {'service_actor': 'execution_service',
            'operation': OP, 'agent_role': 'business'})
    frozen = evaluation.check_preparation_policy(task.data['goal_spec'], value['preparation_policy'])
    return checked_binding, frozen


def _query(db, p, task, result, goal_snapshot):
    """Read Q0's real immutable rows; never call a query evaluator or refresh it."""
    package = result['evidence_package']
    if package is None:
        return
    query = package['query']; run = _fresh(db, p, DataRun, query['query_id'])
    need(task.data.get('native_goal_query_id') == run.id and run.data.get('native_goal_task_id') == task.id
        and run.status != 'cancelled' and run.version == query['version_at_read']
        and run.data.get('native_goal_request_hash') == digest(run.data['request']) == query['request_hash']
        and run.data.get('native_goal_result_hash') == digest(run.data['result']) == query['result_hash_at_read']
        and run.data.get('goal_delegation') == {'task_id': task.id, 'goal_snapshot': goal_snapshot}
        and digest(run.data['goal_delegation']) == query['goal_delegation_hash']
        and run.data.get('native_goal_context_hash') == digest(task.data.get('native_goal_context'))
        == package['scope']['native_goal_context_hash']
        and run.data['request']['entity_ids'] == package['entity_ids'] == task.data['goal_spec']['entity_ids']
        and run.data['request']['fields'] == package['fields'] == task.data['goal_spec']['required_fields']
        and run.data['request']['evidence_requirement'] == task.data['goal_spec']['evidence']
        and run.data['result']['status'] == package['evidence_status'])
    original_refs = []
    report = run.data['result'].get('result', {})
    for kind, rows in (('archive', report.get('evidence', [])), ('observation', report.get('observation_evidence', []))):
        need(type(rows) is list)
        for row in rows:
            need(type(row) is dict and row.get('entity_id') in task.data['entity_ids'])
            original_refs.append({'kind': kind, 'entity_id': row['entity_id'], 'observation_id': row.get('observation_id'),
                'version': row.get('version'), 'observed_at': row.get('observed_at'), 'evidence_hash': digest(row)})
    need(_same(original_refs, package['evidence_refs']))


@checked
def _producer(db, p, task, job, result):
    actual, release, business, graph, state, current, consumer_node = scope._frozen(
        db, p, task.id, job.data['activation_id'])
    scope._job_matches(actual, job, state, current, consumer_node)
    need(actual.id == task.id and consumer_node['actor_id'] == job.data['actor_id'])
    binding, preparation_policy = _binding(actual, release, business, graph, consumer_node)
    edge = current['inputs_snapshot']['bindings'].get('context_result')
    need(type(edge) is dict and edge.get('missing') is False and edge.get('producer_activation_id'))
    activation = state['activations'][edge['producer_activation_id']]
    need(activation['status'] == 'completed' and activation['epoch'] == current['epoch']
        and _same(activation['output'], result) and edge['value_hash'] == digest(result))
    transport = actual.data['graph_jobs'][activation['id']]
    original_job = _fresh(db, p, Job, transport['job_id']); call = _fresh(db, p, Invocation, transport['invocation_id'])
    cap = _fresh(db, p, Capability, call.capability_id)
    original_task, _release, _business, _graph, original_state, original_activation, node = scope._frozen(
        db, p, task.id, activation['id'])
    scope._job_matches(original_task, original_job, original_state, original_activation, node)
    need(node['id'] == 'context_reader' and node['actor_id'] == original_job.data['actor_id']
        and node['execution']['agent_role'] == 'data' and node['execution']['adapter'] == 'builtin'
        and original_job.kind == scope.KIND and original_job.data['task_id'] == task.id
        and call.status == 'succeeded' and call.data.get('role') == 'data' and call.data.get('side_effect') is False
        and call.data.get('approval') is None and _same(call.data.get('output'), result)
        and cap.kind == 'tool' and cap.content_hash == cap.validated_hash == digest(cap.data)
        and cap.id == call.capability_id == node['execution']['capability_id']
        and cap.content_hash == node['execution']['capability_hash'] == call.data.get('capability_hash')
        and cap.release_version == cap.data['version'] == node['execution']['release_version']
        and _same(cap.data['input_schema'], node['execution']['input_schema'])
        and _same(cap.data['output_schema'], node['execution']['output_schema'])
        and ('inputs' not in call.data or _same(call.data['inputs'], original_job.data['inputs'])))
    reader.validate_manifest(cap.data); reader.checked_result(result)
    owner = transport.get('dispatch_ownership'); authorization = transport.get('dispatch_authorization')
    need(type(owner) is dict and owner.get('job_id') == original_job.id and bool(owner.get('lease_token'))
        and type(authorization) is dict and authorization.get('job_id') == original_job.id
        and authorization.get('lease_token') == owner['lease_token']
        and authorization.get('actor_id') == original_job.data['actor_id'] and authorization.get('epoch') == activation['epoch']
        and authorization.get('input_hash') == digest(original_job.data['inputs'])
        and authorization.get('capability_hash') == cap.content_hash
        and transport.get('phase') == 'succeeded' and transport.get('dispatch_committed') is True
        and transport.get('output_hash') == digest(result))
    graph_runtime.check_invocation(call, p, cap, node, original_job.data['inputs'], original_job, owner)
    _native_audits(db, p, actual, original_job, activation, call, original_job.data['inputs'])
    _audit(db, p, 'capability.invoke', call.id, original_job.data['actor_id'], {'status': 'succeeded'})
    _audit(db, p, reader.PRODUCED, call.id, original_job.data['actor_id'], reader.receipt_audit_data(call, original_job, result))
    if original_job.status == 'running':
        raise reader.ProducerPending(original_job.id, call.id)
    need(original_job.status == 'succeeded')
    from .native_goals import snapshot
    goal_snapshot = snapshot(actual)
    package = result['evidence_package']
    if package is not None:
        expected_scope = {'workspace_id': p.workspace_id, 'task_id': actual.id, 'instance_id': release.instance_id,
            'release_id': release.id, 'package_id': release.package_id, 'business_id': actual.data['business_id'],
            'graph_hash': state['graph_hash'], 'source_binding_hash': binding['binding_hash'],
            'goal_snapshot_hash': digest(goal_snapshot), 'native_goal_context_hash': digest(actual.data.get('native_goal_context'))}
        expected_producer = {'capability_id': cap.id, 'invocation_id': call.id, 'job_id': original_job.id,
            'actor_id': original_job.data['actor_id'], 'capability_hash': cap.content_hash,
            'activation_id': activation['id'], 'node_id': node['id'], 'cancel_epoch': activation['epoch'],
            'input_hash': original_job.data['input_hash'], 'request_key_hash': digest(call.idempotency_key)}
        need(_same(package['scope'], expected_scope) and _same(package['producer'], expected_producer))
    _query(db, p, actual, result, goal_snapshot)
    pin = {'schema': PRODUCER, 'workspace_id': p.workspace_id, 'task_id': actual.id, 'job_id': original_job.id,
        'invocation_id': call.id, 'activation_id': activation['id'], 'node_id': node['id'], 'actor_id': original_job.data['actor_id'],
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'input_hash': original_job.data['input_hash'],
        'output_hash': digest(result), 'request_key_hash': digest(call.idempotency_key), 'graph_hash': state['graph_hash'],
        'source_binding_hash': binding['binding_hash'], 'goal_snapshot_hash': digest(goal_snapshot),
        'preparation_policy_hash': preparation_policy['policy_hash']}
    validate(_producer_schema(), pin)
    return copy.deepcopy(pin), copy.deepcopy(goal_snapshot), copy.deepcopy(preparation_policy)


def _output(result, goal_snapshot, preparation_policy, producer):
    decision = policy.route(result, goal_snapshot['spec'], preparation_policy)
    context = None
    if decision['route'] == 'acquire':
        reference = decision['context_ref']
        matches = [row for row in result['candidates'] if row['context_hash'] == reference['context_hash']]
        need(len(matches) == 1 and _same(matches[0], result['resense_context']))
        context = copy.deepcopy(matches[0])
    output = {'schema': RESULT, 'route': decision['route'], 'decision': decision,
        'resense_context': context, 'producer': copy.deepcopy(producer), **policy.FLAGS}
    return checked_output(output)


@checked
def producer_integrity_pin(db, p, task, job, inputs):
    value = _inputs(inputs)
    actual, _r, _b, graph, state, activation, node = scope._frozen(db, p, task.id, job.data['activation_id'])
    scope._job_matches(actual, job, state, activation, node)
    need(actual.id == task.id and job.data['actor_id'] == node['actor_id'] == p.user_id
        and _same(job.data['inputs'], value)
        and _same(graph_runtime.graph_api.ability_inputs(graph, state, activation['id']), value))
    producer, goal_snapshot, preparation_policy = _producer(db, p, actual, job, value['context_result'])
    _output(value['context_result'], goal_snapshot, preparation_policy, producer)
    return producer


def producer_wait_pin(db, p, task, job, inputs):
    return producer_integrity_pin(db, p, task, job, inputs)


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    from .leases import fenced
    need(fenced(db))
    value = _inputs(inputs)
    current = service.validate_call_approval(db, p, cap, role, value, key, approval, _variant=sys.modules[__name__])
    from .native_goals import guard
    guard(db, p, current['task'], key)
    producer, goal_snapshot, preparation_policy = _producer(db, p, current['task'], current['job'], value['context_result'])
    _output(value['context_result'], goal_snapshot, preparation_policy, producer)
    return current


def _anchor(record):
    return {'schema': STATE, 'record_hash': record['record_hash'], 'record': copy.deepcopy(record)}


@checked
def _state(db, p, call, task, job, cap, inputs, approval):
    need(not any(key in call.data for key in FOREIGN))
    record = call.data.get(KEY)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == CREATED, Audit.object_id == call.id).limit(2)))
    if record is None:
        need(not events, '原本地路由审计已存在但原记录缺失；未重建或推进')
        return None
    _bytes(record, MAX_BYTES)
    need(type(record) is dict and set(record) == {'schema', 'workspace_id', 'task_id', 'job_id', 'invocation_id',
        'actor_id', 'capability_id', 'capability_hash', 'input_hash', 'approval_hash', 'goal_snapshot',
        'preparation_policy', 'producer', 'output', 'record_hash'}
        and record['schema'] == STATE and record['workspace_id'] == p.workspace_id == call.workspace_id
        and record['task_id'] == task.id and record['job_id'] == job.id and record['invocation_id'] == call.id
        and record['actor_id'] == job.data['actor_id'] and record['capability_id'] == call.capability_id == cap.id
        and record['capability_hash'] == cap.content_hash == call.data['capability_hash']
        and record['input_hash'] == digest(inputs) == call.data['input_hash']
        and record['approval_hash'] == digest(approval) and _same(call.data.get('approval'), approval)
        and _same(call.data.get('inputs'), inputs)
        and record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'})
        and len(events) == 1 and events[0].actor_id == job.data['actor_id'] and _same(events[0].data, _anchor(record)))
    checked_output(record['output'])
    return copy.deepcopy(record)


def _first_output(db, p, current, inputs):
    producer, goal_snapshot, preparation_policy = _producer(db, p, current['task'], current['job'], inputs['context_result'])
    output = _output(inputs['context_result'], goal_snapshot, preparation_policy, producer)
    if output['route'] == 'acquire':
        context = output['resense_context']
        need(_same(reader.checked_context(db, p, context, task=current['task'], dispatch=False), context))
        gap = _fresh(db, p, DataGap, context['gap']['gap_id'])
        need(gap.status in {'received', 'open'})
        existing = db.scalar(select(ObservationPlan.id).where(ObservationPlan.workspace_id == p.workspace_id,
            ObservationPlan.data['observation_request']['gap_id'].as_string() == gap.id).limit(1))
        need(existing is None, '原缺口已有观测计划或历史自动周期；请核对原号，未新建并行补采')
    return output, goal_snapshot, preparation_policy, producer


def execute(db, p, cap, inputs, key, approval):
    value = _inputs(inputs); current = validate_call_approval(db, p, cap, 'business', value, key, approval)
    call = service._call(db, p, current, key, value, approval)
    prior = _state(db, p, call, current['task'], current['job'], cap, value, approval)
    if prior is not None:
        return checked_output(prior['output'])
    output, goal_snapshot, preparation_policy, producer = _first_output(db, p, current, value)
    record = {'schema': STATE, 'workspace_id': p.workspace_id, 'task_id': current['task'].id,
        'job_id': current['job'].id, 'invocation_id': call.id, 'actor_id': p.user_id, 'capability_id': cap.id,
        'capability_hash': cap.content_hash, 'input_hash': digest(value), 'approval_hash': digest(approval),
        'goal_snapshot': goal_snapshot, 'preparation_policy': preparation_policy, 'producer': producer, 'output': output}
    record['record_hash'] = digest(record); _bytes(record, MAX_BYTES)
    call.data = {**call.data, KEY: record}
    audit(db, p, CREATED, call.id, _anchor(record)); db.flush()
    return checked_output(output)


def reconcile(db, p, cap, inputs, key, approval, call):
    value = _inputs(inputs); current = validate_call_approval(db, p, cap, 'business', value, key, approval)
    actual = service._call(db, p, current, key, value, approval, supplied=call, allow_unknown=True)
    record = _state(db, p, actual, current['task'], current['job'], cap, value, approval)
    need(record is not None, '原本地路由尚未形成；不借回执入口重新计算或推进')
    return checked_output(record['output'])


def retained_marker_shape(call):
    """Routing only; full audited reconciliation remains mandatory."""
    try:
        need(call is not None and call.status == 'unknown' and type(call.data) is dict
            and not any(key in call.data for key in FOREIGN))
        snapshot = call.data['capability_snapshot']; validate_manifest(snapshot)
        record = call.data[KEY]; _bytes(record, MAX_BYTES)
        checked_output(record['output'])
        return (record['schema'] == STATE and record['workspace_id'] == call.workspace_id
            and record['job_id'] == call.data['job_id'] and record['invocation_id'] == call.id
            and record['capability_id'] == call.capability_id
            and record['capability_hash'] == digest(snapshot) == call.data['capability_hash']
            and record['input_hash'] == digest(call.data['inputs']) == call.data['input_hash']
            and record['approval_hash'] == digest(call.data['approval'])
            and record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'}))
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError, OverflowError):
        return False


def _success(db, p, task, job, activation, call):
    marker = call.data.get('service_previous_status')
    if marker is None:
        _audit(db, p, 'capability.invoke', call.id, job.data['actor_id'], {'status': 'succeeded'})
        return
    need(marker in {'unknown', 'waiting_receipt'} and type(call.data.get('service_receipt_verified_at')) is str)
    _audit(db, p, 'capability.invoke', call.id, job.data['actor_id'], {'status': marker})
    _audit(db, p, 'workflow.service.receipt_verified', call.id, job.data['actor_id'],
        {'job_id': job.id, 'activation_id': activation['id']})


@checked
def saved_result(db, p, task, job, call, cap, inputs):
    """Read-only actual success under the reader; no live policy re-dispatch."""
    value = _inputs(inputs); validate_manifest(cap.data)
    actual = _fresh(db, p, Invocation, call.id); actual_job = _fresh(db, p, Job, job.id)
    actual_task = _fresh(db, p, Task, task.id)
    actual_cap = _fresh(db, p, Capability, cap.id)
    need(actual_cap.content_hash == cap.content_hash and _same(actual_cap.data, cap.data))
    cap = actual_cap
    need(actual.status == actual_job.status == 'succeeded' and actual.capability_id == cap.id
        and actual.idempotency_key == actual_job.idempotency_key == actual_job.data['invocation_key']
        and actual_job.data['task_id'] == actual_task.id and actual.data.get('job_id') == actual_job.id
        and actual.data.get('role') == 'business' and actual.data.get('side_effect') is True
        and _same(actual.data, call.data) and _same(actual_job.data, job.data)
        and _same(actual_job.data['inputs'], value))
    original, _release, _business, _graph, state, activation, node = scope._frozen(
        db, p, task.id, actual_job.data['activation_id'])
    transport = scope._job_matches(original, actual_job, state, activation, node)
    need(activation['status'] == 'completed' and transport.get('phase') == 'succeeded'
        and transport.get('invocation_id') == actual.id and node['execution']['capability_id'] == cap.id
        and cap.kind == 'tool' and node['execution']['adapter'] == 'workflow_service'
        and node['execution']['agent_role'] == 'business'
        and cap.release_version == cap.data['version'] == node['execution']['release_version']
        and node['execution']['capability_hash'] == cap.content_hash == cap.validated_hash == digest(cap.data)
        and _same(actual.data.get('capability_snapshot'), cap.data))
    approval = service.make_approval(original, actual_job, node, value)
    owner = transport.get('dispatch_ownership'); authorization = transport.get('dispatch_authorization')
    need(type(owner) is dict and type(authorization) is dict and owner.get('job_id') == actual_job.id
        and authorization.get('job_id') == actual_job.id and authorization.get('lease_token') == owner.get('lease_token')
        and authorization.get('actor_id') == actual_job.data['actor_id'] and authorization.get('epoch') == activation['epoch']
        and authorization.get('input_hash') == digest(value) and authorization.get('capability_hash') == cap.content_hash)
    graph_runtime.check_invocation(actual, p, cap, node, value, actual_job, owner, approval=approval)
    _native_audits(db, p, original, actual_job, activation, actual, value)
    record = _state(db, p, actual, original, actual_job, cap, value, approval); need(record is not None)
    producer, goal_snapshot, preparation_policy = _producer(db, p, original, actual_job, value['context_result'])
    need(_same(record['producer'], producer) and _same(record['goal_snapshot'], goal_snapshot)
        and _same(record['preparation_policy'], preparation_policy))
    expected = _output(value['context_result'], goal_snapshot, preparation_policy, producer)
    need(_same(checked_output(record['output']), expected) and _same(checked_output(actual.data.get('output')), expected)
        and _same(checked_output(activation['output']), expected) and transport.get('output_hash') == digest(expected))
    _success(db, p, original, actual_job, activation, actual)
    return checked_output(expected)


def saved_receipt(db, p, cap, inputs, key, approval, call):
    job = _fresh(db, p, Job, call.data['job_id']); task = _fresh(db, p, Task, job.data['task_id'])
    need(key == call.idempotency_key and _same(approval, call.data['approval']))
    return saved_result(db, p, task, job, call, cap, inputs)
