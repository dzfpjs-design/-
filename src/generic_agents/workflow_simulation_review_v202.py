"""Explicit, audited permission for a bounded SIM mechanics test with gaps.

This is not evidence review, a production override or a replacement model
answer. Only an original, positively proven pre-dispatch Job can be queued.
The immutable request remains readable after the Task or authority changes.
"""
import copy
from datetime import timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictBool
from sqlalchemy import or_, select, func

from .contracts import Contract, digest
from .models import Audit, Invocation, Job, now
from .services import audit, published, require, scoped

KEY = 'graph_simulation_reviews_v202'
ACTION = 'workflow.graph.simulation_review_v202'
RECORD = 'workflow-simulation-review-record-v202'
BASIS = 'workflow-simulation-review-basis-v202'
RECEIPT = 'workflow-simulation-review-receipt-v202'
ERROR = '原模拟联动核对、资料缺项、后台工作或独立审计无法核对；未补齐缺项或重新派发'
MAX_RECORDS = 32


class ReviewCommand(Contract):
    request_id: UUID
    activation_id: str = Field(min_length=1, max_length=200)
    expected_task_version: int = Field(strict=True, ge=1)
    expected_job_version: int = Field(strict=True, ge=1)
    basis_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    acknowledged_missing_information: list[str] = Field(min_length=1, max_length=64)
    acknowledge_simulation_only: StrictBool
    reason: str = Field(min_length=1, max_length=1000)


def need(value, message=ERROR, status=409):
    if not value:
        raise HTTPException(status, message)


def _same(a, b):
    from .business_flow_review_store import _same as same
    return same(a, b)


def _time(value):
    if value is None:
        return None
    need(value.tzinfo is not None)
    return value.astimezone(timezone.utc).isoformat()


def selected_with_gaps(result):
    """Keep every original gap; no client flag or replacement recommendation."""
    selected = result['recommended_candidate_ref']
    analysis = result['analysis']
    rows = analysis['candidate_assessments']
    matches = [row for row in rows if row['candidate_ref'] == selected]
    recommendation = analysis['recommendation']
    need(selected is not None and recommendation['candidate_ref'] == selected
        and len(matches) == 1 and matches[0]['assessment'] == 'supported'
        and bool(matches[0]['evidence_refs']) and bool(recommendation['evidence_refs'])
        and not any(row['assessment'] == 'conflicting' for row in rows)
        and result['review_state'] == 'needs_information'
        and type(matches[0]['missing_information']) is list
        and 1 <= len(matches[0]['missing_information']) <= 64
        and all(type(value) is str and bool(value.strip()) for value in matches[0]['missing_information']),
        '只有原研判明确支持、仍列出缺项且没有冲突的方案，才能核对模拟联动条件')
    return selected, copy.deepcopy(matches[0])


def _job_snapshot(job):
    return {'id': job.id, 'kind': job.kind, 'idempotency_key': job.idempotency_key,
        'status': job.status, 'version': job.version, 'attempts': job.attempts,
        'lease_token': job.lease_token, 'lease_until': _time(job.lease_until),
        'available_at': _time(job.available_at), 'data': copy.deepcopy(job.data)}


def _stable_job(job):
    names = ('task_id', 'activation_id', 'actor_id', 'graph_hash', 'cancel_epoch',
        'activation_version', 'input_hash', 'inputs', 'invocation_key', 'attempt')
    return {'id': job.id, 'kind': job.kind, 'idempotency_key': job.idempotency_key,
        'frozen': {name: copy.deepcopy(job.data.get(name)) for name in names}}


def _records(db, task):
    try:
        return _checked_records(db, task)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        need(False)


def _checked_records(db, task):
    """A mutable JSON marker alone can never authorize or recover a review."""
    from .business_flow_review_store import _bytes
    saved = task.data.get(KEY, {})
    _bytes(saved, 4 * 1024 * 1024)
    need(type(saved) is dict and len(saved) <= MAX_RECORDS)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == task.workspace_id,
        Audit.object_id == task.id, Audit.action == ACTION)))
    need(len(events) == len(saved) and all(type(row.data) is dict for row in events))
    used = set()
    for request_id, record in saved.items():
        need(type(record) is dict and set(record) == {'schema', 'actor_id', 'body_hash',
            'body', 'basis', 'pin', 'prior_job', 'prior_transport', 'queued_job_version',
            'receipt', 'record_hash'} and record['schema'] == RECORD
            and record['record_hash'] == digest({k: v for k, v in record.items() if k != 'record_hash'})
            and record['body']['request_id'] == request_id
            and record['body_hash'] == digest({'actor_id': record['actor_id'], 'body': record['body']})
            and record['receipt']['request_id'] == request_id
            and record['receipt']['task_id'] == task.id
            and record['receipt']['activation_id'] == record['body']['activation_id']
            and record['receipt']['job_id'] == record['prior_job']['id']
            and record['receipt']['replayed'] is False
            and record['receipt']['engineering_only'] is True
            and record['receipt']['gaps_resolved'] is False
            and record['receipt']['human_review_verified'] is False
            and record['body']['acknowledge_simulation_only'] is True
            and record['body']['acknowledged_missing_information'] == record['basis']['missing_information']
            and record['basis']['basis_hash'] == record['body']['basis_hash']
            and record['basis']['basis_hash'] == digest({'basis': {k: v for k, v in record['basis'].items()
                if k != 'basis_hash'}, 'pin': record['pin']})
            and record['queued_job_version'] == record['prior_job']['version'] + 1)
        matches = [row for row in events if row.data.get('request_id') == request_id]
        need(len(matches) == 1 and matches[0].actor_id == record['actor_id']
            and _same(matches[0].data, {'schema': RECORD, 'request_id': request_id,
                'record_hash': record['record_hash'], 'record': record}))
        used.add(matches[0].id)
    need(len(used) == len(events))
    return copy.deepcopy(saved)


def historical_record(db, task, request_id):
    return _records(db, task).get(str(request_id))


def public_request(record):
    need(record is not None)
    return {key: copy.deepcopy(record[key]) for key in ('actor_id', 'body_hash', 'body', 'receipt')}


def public_receipts(db, task):
    return {row['receipt']['activation_id']: {**copy.deepcopy(row['receipt']),
        'missing_information': copy.deepcopy(row['basis']['missing_information'])}
        for row in _records(db, task).values()}


def _no_dispatch(db, task, job, transport):
    from .workflow_graph_goal_guard_v133 import reservations
    forbidden = {'dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash', 'dispatch_authorization'}
    need(type(transport) is dict and not set(transport) & forbidden
        and transport.get('invocation_id') is None
        and transport.get('job_id') == job.id
        and transport.get('invocation_key') == job.data['invocation_key'])
    call = db.scalar(select(Invocation.id).where(Invocation.workspace_id == task.workspace_id,
        or_(Invocation.idempotency_key == job.data['invocation_key'],
            Invocation.data['job_id'].as_string() == job.id)).limit(1))
    committed = db.scalar(select(Audit.id).where(Audit.workspace_id == task.workspace_id,
        Audit.object_id == task.id, Audit.action.in_(['workflow.graph.dispatch',
            'workflow.graph.dispatch_authorized', 'workflow.graph.goal_reserved']),
        Audit.data['job_id'].as_string() == job.id).limit(1))
    need(call is None and committed is None and not any(row['invocation_key'] == job.data['invocation_key']
        or row['job_id'] == job.id for row in reservations(task)),
        '原节点已有派发、调用或目标预留，不能以模拟核对重新执行；请核对原请求')


def _pre_dispatch(db, task, job, transport):
    _no_dispatch(db, task, job, transport)
    need(job.status in {'failed', 'blocked'} and type(job.attempts) is int and 1 <= job.attempts <= 3
        and job.idempotency_key == job.data['invocation_key']
        and type(transport.get('attempt')) is int and transport['attempt'] == job.data.get('attempt') == 1
        and transport.get('phase') in {'queued', 'blocked'}
        and (job.lease_until is None or job.lease_until <= db.scalar(select(func.clock_timestamp()))),
        '原后台工作尚未停止或无法证明仍在派发前，未重新排队')


def _review_delegation(binding, business, graph, node, cap):
    """Verify each explicit SIM family's own frozen service provenance."""
    from .workflow_registered_tool_plan_v201 import OP
    need(type(binding) is dict and binding.get('simulation_only') is True,
        '此核对只适用于明确批准的工程模拟流程，不能授权实际设备动作')
    if binding.get('schema') == 'authored-service-workflow-binding-v270':
        from .authored_service_workflow_store_v270 import check_binding, service_delegations
        checked = check_binding(binding)
        need(_same(checked, binding) and _same(checked['graph'], graph)
            and checked['business_name'] == business['name'] and _same(checked['flow'], business['flow']))
        selected = checked['tool_bindings']['planner']
        nodes = {value['id']: value for value in graph['nodes']}
        need(node['id'] == 'planner' and _same(nodes.get(node['id']), node)
            and node['kind'] == 'ability' and node['actor_id'] == selected['actor_id']
            and node['execution']['agent_role'] == 'business'
            and _same(checked['capability_pins']['planner'], node['execution'])
            and cap.id == selected['capability_id'] == node['execution']['capability_id']
            and cap.content_hash == cap.validated_hash == digest(cap.data)
                == selected['capability_hash'] == node['execution']['capability_hash']
            and node['execution']['release_version'] == cap.release_version
            and _same(node['execution']['input_schema'], cap.data['input_schema'])
            and _same(node['execution']['output_schema'], cap.data['output_schema'])
            and cap.data['execution'].get('adapter') == node['execution']['adapter'] == 'workflow_service'
            and cap.data['execution'].get('service_actor') == 'execution_service'
            and cap.data['execution'].get('operation') == OP
            and cap.data['execution'].get('side_effect') is True)
        delegation = service_delegations(checked).get(node['id'])
        need(_same(delegation, {'service_actor': 'execution_service', 'operation': OP, 'agent_role': 'business'}))
    else:
        # Preserve the original nested-family verifier. Its binding comes only
        # from the already frozen/live-checked business above, never a client.
        from .business_flow_nested_adoption_v195 import service_delegations
        delegation = service_delegations(binding['translation']).get(node['id'])
        need(delegation is not None and delegation.get('side_effect') is True)
    need(delegation is not None and delegation.get('operation') == OP
        and delegation.get('service_actor') == 'execution_service' and delegation.get('agent_role') == 'business')
    return delegation


def _context(db, p, task, activation_id, job=None, *, read_mode=False):
    from . import workflow_graph as runtime
    from .workflow_graph_scope import _frozen, _job_matches
    from .workflow_registered_tool_plan_v201 import (OP, validate_manifest,
        producer_integrity_pin, _check_candidates)
    from .worker import actor
    actual, release, business, graph, state, activation, node = _frozen(db, p, task.id, activation_id)
    transport = actual.data.get('graph_jobs', {}).get(activation_id)
    need(type(transport) is dict and transport.get('job_id'))
    if job is None:
        job = scoped(db, Job, transport['job_id'], p)
    _job_matches(actual, job, state, activation, node)
    need(state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids']
        and activation['epoch'] == state['cancel_epoch'] == job.data['cancel_epoch']
        and node['execution']['adapter'] == 'workflow_service')
    executor = actor(db, p.workspace_id, node['actor_id'])
    if read_mode:
        runtime.live(db, executor, actual, release, business, lock_sources=False)
    else:
        runtime.live(db, executor, actual, release, business)
    if actual.data.get('native_goal_context') is not None:
        from .native_goals import guard
        if read_mode:
            guard(db, executor, actual, job.data['invocation_key'],lock_sources=False)
        else:
            guard(db, executor, actual, job.data['invocation_key'])
    cap = published(db, executor, node['execution']['capability_id'])
    need(cap.content_hash == node['execution']['capability_hash']
        and cap.data['execution'].get('operation') == OP)
    validate_manifest(cap.data)
    binding = runtime.business_binding(release, business)
    _review_delegation(binding, business, graph, node, cap)
    inputs = job.data['inputs']
    producer = producer_integrity_pin(db, executor, actual, job, inputs)
    selected, assessment = selected_with_gaps(inputs['reasoning_result'])
    need([row['candidate_ref'] for row in inputs['candidate_plans']]
        == [row['candidate_ref'] for row in producer['candidates']])
    context = {'task': actual, 'job': job, 'capability': cap, 'binding': binding}
    need(_check_candidates(db, executor, context, inputs) is True)
    all_ids = {step['capability_id'] for row in inputs['candidate_plans'] for step in row['steps']}
    need(all_ids == set(cap.data['dependencies']))
    targets = []
    for identity in sorted(all_ids):
        target = published(db, executor, identity)
        need(target.data.get('specification', {}).get('simulation_only') is True)
        label = target.data.get('specification', {}).get('display_name')
        description = target.data.get('description')
        label = (label if type(label) is str and label.strip()
            else description if type(description) is str and description.strip() else target.name)
        targets.append({'display_name': label,
            'simulation_only': True})
    pin = {'job': _stable_job(job), 'graph_hash': state['graph_hash'],
        'cancel_epoch': state['cancel_epoch'], 'binding_hash': binding['binding_hash'],
        'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'producer': producer, 'result_hash': digest(inputs['reasoning_result']),
        'targets': [{'capability_id': identity,
            'capability_hash': published(db, executor, identity).content_hash} for identity in sorted(all_ids)]}
    return actual, job, state, activation, transport, selected, assessment, targets, pin


def _basis_value(task, job, state, activation, selected, assessment, targets, pin):
    value = {'schema': BASIS, 'state': 'reviewable', 'task_id': task.id,
        'activation_id': activation['id'], 'task_version': task.version,
        'job_id': job.id, 'job_version': job.version, 'activation_version': activation['version'],
        'cancel_epoch': state['cancel_epoch'], 'recommended_candidate_ref': selected,
        'missing_information': copy.deepcopy(assessment['missing_information']),
        'analysis_summary': job.data['inputs']['reasoning_result']['analysis']['summary'],
        'candidate_reason': assessment['reason'], 'targets': targets,
        'purpose': 'bounded_simulated_tool_test', 'gaps_resolved': False,
        'human_review_verified': False, 'can_confirm': True,
        'reason': '资料缺项仍未解决；仅核对已批准模拟工具的联动、回执和归档，不验证实际设备或业务结论'}
    return {**value, 'basis_hash': digest({'basis': value, 'pin': pin})}


def describe(db, p, task, activation_id):
    require(p, 'admin')
    saved = _records(db, task)
    try:
        need(not any(row['body']['activation_id'] == activation_id for row in saved.values()),
            '此节点已保存模拟联动核对，请查看原请求；不能重复提交')
        actual, job, state, activation, transport, selected, assessment, targets, pin = _context(
            db, p, task, activation_id, read_mode=True)
        _pre_dispatch(db, actual, job, transport)
        return _basis_value(actual, job, state, activation, selected, assessment, targets, pin)
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration) as exc:
        if isinstance(exc, HTTPException) and exc.status_code not in {409, 422}:
            raise
        state = task.data['graph_runtime']
        return {'schema': BASIS, 'state': 'not_applicable', 'task_id': task.id,
            'activation_id': activation_id, 'task_version': task.version,
            'job_id': None, 'job_version': None, 'activation_version': None,
            'cancel_epoch': state['cancel_epoch'], 'basis_hash': None,
            'recommended_candidate_ref': None, 'missing_information': [],
            'analysis_summary': '', 'candidate_reason': '', 'targets': [],
            'purpose': 'bounded_simulated_tool_test', 'gaps_resolved': False,
            'human_review_verified': False, 'can_confirm': False,
            'reason': str(exc.detail) if isinstance(exc, HTTPException) else ERROR}


def submit(db, p, task, job, command):
    require(p, 'admin')
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    saved = _records(db, task)
    body = command.model_dump(mode='json')
    signature = digest({'actor_id': p.user_id, 'body': body})
    prior = saved.get(str(command.request_id))
    if prior is not None:
        need(prior['actor_id'] == p.user_id and prior['body_hash'] == signature,
            '原模拟核对请求不能更换账号或内容')
        return {**copy.deepcopy(prior['receipt']), 'replayed': True}
    need(command.acknowledge_simulation_only is True and len(saved) < MAX_RECORDS
        and not any(row['body']['activation_id'] == command.activation_id for row in saved.values()))
    actual, current, state, activation, transport, selected, assessment, targets, pin = _context(
        db, p, task, command.activation_id, job)
    need(current is job and task.id == actual.id
        and task.version == command.expected_task_version and job.version == command.expected_job_version,
        '任务或原后台工作已变化，请保留输入后重新核对')
    _pre_dispatch(db, task, job, transport)
    basis = _basis_value(actual, job, state, activation, selected, assessment, targets, pin)
    need(command.basis_hash == basis['basis_hash']
        and _same(command.acknowledged_missing_information, basis['missing_information']),
        '原研判、缺项或模拟工具已变化，请保留输入后重新核对')
    receipt = {'schema': RECEIPT, 'request_id': str(command.request_id), 'task_id': task.id,
        'activation_id': command.activation_id, 'job_id': job.id, 'state': 'saved',
        'queued': True, 'engineering_only': True, 'gaps_resolved': False,
        'human_review_verified': False, 'replayed': False, 'original_request_preserved': True}
    record = {'schema': RECORD, 'actor_id': p.user_id, 'body_hash': signature, 'body': body,
        'basis': basis, 'pin': pin, 'prior_job': _job_snapshot(job),
        'prior_transport': copy.deepcopy(transport), 'queued_job_version': job.version + 1,
        'receipt': receipt}
    record['record_hash'] = digest(record)
    saved[str(command.request_id)] = record
    transports = copy.deepcopy(task.data['graph_jobs'])
    queued_transport = {**transport, 'phase': 'queued', 'reason': None}
    # _pre_dispatch has proved no original call/intent/reservation. A null
    # failure placeholder is not an Invocation reference: preserve its exact
    # original form in the audited record above, then restore the canonical
    # never-dispatched projection before the first authorized invocation.
    for field in ('invocation_id', 'invocation_status'):
        if queued_transport.get(field) is None:
            queued_transport.pop(field, None)
    transports[command.activation_id] = queued_transport
    job.status = 'queued'; job.available_at = now(); job.lease_token = None; job.lease_until = None
    job.version += 1
    # Do not clear attempts or change the original effect attempt/key/input.
    job.data = {**job.data, 'simulation_review_v202': record['record_hash'],
        'recovery_only': job.attempts >= 3}
    task.data = {**task.data, KEY: saved, 'graph_jobs': transports}; task.version += 1
    audit(db, p, ACTION, task.id, {'schema': RECORD, 'request_id': str(command.request_id),
        'record_hash': record['record_hash'], 'record': copy.deepcopy(record)})
    db.flush()
    _records(db, task)
    return copy.deepcopy(receipt)


def checked_selection(db, p, task, job, inputs, producer):
    """No queue/replay/fee changes; original proof plus current strict SIM pins."""
    from .workflow_registered_tool_plan_v201 import _check_candidates
    from .workflow_graph import business_binding
    from .workflow_graph_scope import _frozen, _job_matches
    records = _records(db, task)
    matches = [row for row in records.values() if row['receipt']['job_id'] == job.id]
    need(len(matches) == 1)
    record = matches[0]
    actual, release, business, graph, state, activation, node = _frozen(
        db, p, task.id, job.data['activation_id'])
    _job_matches(actual, job, state, activation, node)
    selected, assessment = selected_with_gaps(inputs['reasoning_result'])
    binding = business_binding(release, business)
    cap = published(db, p, node['execution']['capability_id'])
    if binding.get('schema') == 'authored-service-workflow-binding-v270':
        _review_delegation(binding, business, graph, node, cap)
    need(binding.get('simulation_only') is True
        and record['pin']['binding_hash'] == binding['binding_hash']
        and record['pin']['capability_id'] == cap.id
        and record['pin']['capability_hash'] == cap.content_hash == node['execution']['capability_hash']
        and _same(record['pin']['job'], _stable_job(job))
        and _same(record['pin']['producer'], producer)
        and record['pin']['result_hash'] == digest(inputs['reasoning_result'])
        and record['pin']['cancel_epoch'] == state['cancel_epoch'] == activation['epoch']
        and record['pin']['graph_hash'] == state['graph_hash']
        and record['body']['acknowledged_missing_information'] == assessment['missing_information']
        and record['basis']['recommended_candidate_ref'] == selected
        and job.data.get('simulation_review_v202') == record['record_hash'])
    need(_check_candidates(db, p, {'task': actual, 'job': job, 'capability': cap,
        'binding': binding}, inputs) is True)
    for pin in record['pin']['targets']:
        target = published(db, p, pin['capability_id'])
        need(target.content_hash == pin['capability_hash']
            and target.data.get('specification', {}).get('simulation_only') is True)
    return selected


def can_start_after_review(db, p, task, job):
    from .workflow_registered_tool_plan_v201 import producer_integrity_pin
    transport = task.data['graph_jobs'][job.data['activation_id']]
    _no_dispatch(db, task, job, transport)
    producer = producer_integrity_pin(db, p, task, job, job.data['inputs'])
    checked_selection(db, p, task, job, job.data['inputs'], producer)
    return True
