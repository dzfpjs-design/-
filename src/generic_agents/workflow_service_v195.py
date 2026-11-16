"""Two explicit adopted workflow services, never an implicit device grant.

Only a real leased graph Job, frozen activation, registered service capability,
V195 delegation and current Task/goal approval can enter these handlers. A
pending acquisition preserves its original Invocation and request IDs. Reading a
receipt never proposes another acquisition or marks an observation received.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import func, select

from .contracts import digest
from .models import (Acquisition, Audit, DataGap, Invocation, Job, Membership,
    MonitorPolicy, Observation, ObservationPlan, Task, User, DomainInstance)
from .services import audit, published, require, scoped, validate
from . import business_flow_nested_adoption_v195 as adoption
from . import business_flow_review_store as safe


APPROVAL = 'workflow-service-approval-v195'
STATE = 'workflow-service-state-v195'
NOTICE = 'workflow-service-in-app-notice-v195'
RECEIPT = 'workflow-service-receipt-v195'
STARTED = 'workflow_service.started_v195'
NOTICES = 'workflow_service_notices_v195'
APPROVAL_KEYS = {'schema', 'task_id', 'job_id', 'activation_id', 'graph_hash',
    'input_hash', 'capability_id', 'capability_hash', 'cancel_epoch'}
STATE_KEYS = {'schema', 'operation', 'task_id', 'job_id', 'activation_id', 'capability_id',
    'capability_hash', 'input_hash', 'approval_hash', 'source_binding_hash', 'invocation_id',
    'request_key', 'actor_id', 'created_at', 'simulation_only', 'operation_data', 'state_hash'}
MAX_ACQUISITIONS = 128
MAX_NOTICES = 1000
UUID_PATTERN = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
HASH_PATTERN = r'^[a-f0-9]{64}$'


class PendingService(Exception):
    """Original requests exist, but their actual successful receipts do not yet."""


def need(ok, message='原服务委托、实际执行或回执无法核对；未重新通知、补采或推进流程', status=409):
    if not ok:
        raise HTTPException(status, message)


def _same(left, right):
    return safe._same(left, right)


def _id(value):
    safe.identity(value)
    return value


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def input_schema(operation):
    identity = {'type': 'string', 'pattern': UUID_PATTERN}
    if operation == 'in_app_notice':
        return _object({'recipient_ids': {'type': 'array', 'items': identity, 'minItems': 1,
            'maxItems': 100, 'uniqueItems': True},
            'message': {'type': 'string', 'minLength': 1, 'maxLength': 8000, 'pattern': r'\S'}})
    need(operation == 'resense_with_receipt', status=422)
    return _object({'policy_id': identity, 'gap_id': copy.deepcopy(identity)})


def output_schema(operation):
    identity = {'type': 'string', 'pattern': UUID_PATTERN}
    hashed = {'type': 'string', 'pattern': HASH_PATTERN}
    properties = {'schema': {'const': RECEIPT}, 'operation': {'const': operation},
        'task_id': identity, 'invocation_id': copy.deepcopy(identity)}
    if operation == 'in_app_notice':
        properties.update(delivery_status={'const': 'delivered_to_in_app'}, read_status={'const': 'not_tracked'},
            recipient_ids={'type': 'array', 'items': copy.deepcopy(identity), 'minItems': 1, 'maxItems': 100, 'uniqueItems': True},
            message_hash=hashed, delivered_at={'type': 'string', 'format': 'date-time'})
    else:
        need(operation == 'resense_with_receipt', status=422)
        observation = _object({'acquisition_id': copy.deepcopy(identity), 'observation_id': copy.deepcopy(identity),
            'data_receipt_hash': copy.deepcopy(hashed)})
        supporting = _object({'observation_id': copy.deepcopy(identity), 'data_receipt_hash': copy.deepcopy(hashed)})
        properties.update(policy_id=copy.deepcopy(identity), gap_id=copy.deepcopy(identity),
            observations={'type': 'array', 'items': observation, 'minItems': 1, 'maxItems': MAX_ACQUISITIONS},
            followup_observations={'type': 'array', 'items': copy.deepcopy(observation), 'maxItems': MAX_ACQUISITIONS},
            plan_receipts={'type': 'array', 'minItems': 1, 'maxItems': MAX_ACQUISITIONS,
                'items': _object({'plan_id': copy.deepcopy(identity), 'evidence_hash': copy.deepcopy(hashed),
                    'supporting_observations': {'type': 'array', 'items': supporting, 'minItems': 1, 'maxItems': MAX_ACQUISITIONS}})})
    properties.update(state_hash=copy.deepcopy(hashed), simulation_only={'type': 'boolean'}, receipt_hash=copy.deepcopy(hashed))
    return _object({'result': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
        'service_receipt': _object(properties)})


def validate_manifest(manifest):
    """Exact callable service schemas; this pure check is not publication/IAM."""
    need(type(manifest) is dict and manifest.get('kind') == 'tool'
        and manifest.get('allowed_agent_roles') == ['business'], status=422)
    safe._bytes(manifest, 1024 * 1024)
    execution = manifest.get('execution')
    need(type(execution) is dict and set(execution) == {'adapter', 'service_actor', 'operation', 'side_effect'}
        and execution['adapter'] == 'workflow_service' and execution['side_effect'] is True, status=422)
    try:
        delegated = adoption.compiler.service_delegation(execution['service_actor'], execution['operation'])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, '服务职责与操作不匹配；未完成能力验证或授予执行资格') from None
    need(_same(manifest.get('input_schema'), input_schema(delegated['operation']))
        and _same(manifest.get('output_schema'), output_schema(delegated['operation'])),
        '服务登记的输入输出必须与实际处理器合同一致；未接受不可调用的宽泛或缺失字段', 422)
    return copy.deepcopy(manifest)


def _inputs(operation, inputs):
    need(type(inputs) is dict, status=422)
    safe._bytes(inputs, 64 * 1024)
    if operation == 'in_app_notice':
        need(set(inputs) == {'recipient_ids', 'message'} and type(inputs['recipient_ids']) is list
            and 1 <= len(inputs['recipient_ids']) <= 100
            and all(type(value) is str for value in inputs['recipient_ids'])
            and len(set(inputs['recipient_ids'])) == len(inputs['recipient_ids'])
            and type(inputs['message']) is str and 1 <= len(inputs['message']) <= 8000
            and bool(inputs['message'].strip()), status=422)
        for value in inputs['recipient_ids']:
            _id(value)
    else:
        need(operation == 'resense_with_receipt' and set(inputs) == {'policy_id', 'gap_id'}, status=422)
        _id(inputs['policy_id']); _id(inputs['gap_id'])
    return copy.deepcopy(inputs)


def make_approval(task, job, node, inputs):
    """A frozen command description; alone it authorizes nothing."""
    return {'schema': APPROVAL, 'task_id': task.id, 'job_id': job.id,
        'activation_id': job.data['activation_id'], 'graph_hash': job.data['graph_hash'],
        'input_hash': digest(inputs), 'capability_id': node['execution']['capability_id'],
        'capability_hash': node['execution']['capability_hash'], 'cancel_epoch': job.data['cancel_epoch']}


def validate_call_approval(db, p, cap, role, inputs, key, approval, *, _variant=None):
    """Actual current authority before invocation creation and every receipt poll.

    The original call's lease token is historical dispatch provenance. Polling
    checks current ownership of the same Job independently; it does not replace
    that original token or create another invocation.
    """
    from . import workflow_graph_scope as scope
    from . import workflow_graph as workflow
    from . import full_semantic_graph_v1 as kernel
    from .leases import ownership
    from .relations import lock_current_actor
    from .workflow_abilities import eligible_service
    require(p, 'admin', 'operator')
    need(type(approval) is dict and set(approval) == APPROVAL_KEYS and approval['schema'] == APPROVAL)
    need(type(approval['cancel_epoch']) is int and approval['cancel_epoch'] >= 0)
    for field in ('task_id', 'job_id', 'capability_id'):
        _id(approval[field])
    for field in ('graph_hash', 'input_hash', 'capability_hash'):
        need(type(approval[field]) is str and safe.SHA.fullmatch(approval[field]) is not None)
    need(type(approval['activation_id']) is str and bool(approval['activation_id']))
    current = published(db, p, cap.id)
    execution = current.data['execution']
    if _variant is None:
        validate_manifest(current.data)
        _inputs(execution['operation'], inputs)
    else:
        # This is an installed server implementation, never a client-supplied
        # verified flag or a second permission system. All common live gates
        # below still inspect the real capability, Task, Job and wire inputs.
        from .workflow_service_catalog_v197 import family
        installed = family(current)
        need(_variant is installed and execution.get('operation') == installed.OP)
        installed.validate_manifest(current.data)
        installed._inputs(inputs)
    eligible_service(current, role, service_actor=execution.get('service_actor'), operation=execution.get('operation'))
    need(current.id == cap.id and current.content_hash == cap.content_hash
        and current.validated_hash == current.content_hash and current.content_hash == digest(current.data))
    lease = ownership(db)
    need(type(lease) is dict and lease.get('job_id') == approval['job_id'] and bool(lease.get('lease_token')))
    job = db.scalar(select(Job).where(Job.id == approval['job_id'], Job.workspace_id == p.workspace_id,
        Job.kind == scope.KIND, Job.status == 'running', Job.lease_token == lease['lease_token'],
        Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    need(job is not None and job.data.get('actor_id') == p.user_id and job.data.get('invocation_key') == key
        and job.data.get('task_id') == approval['task_id'])
    # Acquisition dispatch locks Gap before its parent Task. Preserve that order
    # here; no caller-supplied gap is accepted as the Task's goal delegation.
    if execution['operation'] == 'resense_with_receipt':
        from .data_gaps import locked_gap
        locked_gap(db, p, inputs['gap_id'])
    elif execution['operation'] == 'resense_from_context':
        from .data_gaps import locked_gap
        locked_gap(db, p, _variant.prelock_gap_id(inputs))
    locked = db.scalar(select(Task).where(Task.workspace_id == p.workspace_id,
        Task.id == approval['task_id']).with_for_update().execution_options(populate_existing=True))
    need(locked is not None)
    task, release, business, graph, state, activation, node = scope._frozen(
        db, p, approval['task_id'], approval['activation_id'])
    transport = scope._job_matches(task, job, state, activation, node)
    need(task.id == locked.id and task.workspace_id == p.workspace_id
        and state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids']
        and activation['epoch'] == state['cancel_epoch'] and node['actor_id'] == p.user_id
        and transport.get('phase') in {'running', 'waiting_service'}
        and _same(approval, make_approval(task, job, node, inputs))
        and _same(inputs, job.data['inputs']) and _same(inputs, kernel.ability_inputs(graph, state, activation['id'])))
    binding = workflow.business_binding(release, business)
    if binding.get('schema') == 'authored-goal-data-workflow-binding-v288':
        from .authored_goal_data_workflow_store_v288 import service_delegations
        delegations = service_delegations(binding)
    elif binding.get('schema') == 'authored-decision-workflow-binding-v286':
        from .authored_decision_workflow_store_v286 import service_delegations
        delegations = service_delegations(binding)
    elif binding.get('schema') == 'authored-decision-workflow-binding-v278':
        from .authored_decision_workflow_store_v278 import service_delegations
        delegations = service_delegations(binding)
    elif binding.get('schema') == 'authored-service-workflow-binding-v270':
        from .authored_service_workflow_store_v270 import service_delegations
        delegations = service_delegations(binding)
    else:
        need(binding.get('schema') == adoption.BINDING)
        delegations = adoption.service_delegations(binding['translation'])
    delegation = delegations.get(node['id'])
    need(delegation is not None and delegation['service_actor'] == execution['service_actor']
        and delegation['operation'] == execution['operation'] and role == delegation['agent_role'])
    spec = node['execution']
    need(spec['adapter'] == 'workflow_service' and spec['capability_id'] == current.id
        and spec['capability_hash'] == current.content_hash and spec['agent_role'] == role
        and spec['release_version'] == current.release_version
        and _same(spec['input_schema'], current.data['input_schema'])
        and _same(spec['output_schema'], current.data['output_schema'])
        and spec['max_attempts'] == current.data['budget'].get('max_attempts', 2))
    validate(current.data['input_schema'], inputs)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db, p, task, record_actor=True)
    workflow.live(db, p, task, release, business)
    from .workflow_graph_goal_guard_v133 import before_dispatch
    before_dispatch(db, p, task, job, node)
    lock_current_actor(db, p)
    return {'task': task, 'job': job, 'node': node, 'release': release, 'binding': binding,
        'transport': transport, 'delegation': delegation, 'capability': current}


def _call(db, p, context, key, inputs, approval, supplied=None, *, allow_unknown=False):
    row = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key).with_for_update().execution_options(populate_existing=True))
    allowed = {'running', 'waiting_receipt', 'unknown'} if allow_unknown else {'running', 'waiting_receipt'}
    need(row is not None and (supplied is None or supplied.id == row.id)
        and row.status in allowed and row.capability_id == context['capability'].id
        and row.data.get('role') == context['delegation']['agent_role']
        and row.data.get('capability_hash') == context['capability'].content_hash
        and row.data.get('input_hash') == digest(inputs) and _same(row.data.get('inputs'), inputs)
        and _same(row.data.get('approval'), approval) and row.data.get('side_effect') is True
        and row.data.get('job_id') == context['job'].id)
    transport = context['transport']; authorization = transport.get('dispatch_authorization')
    owner = transport.get('dispatch_ownership')
    need(type(owner) is dict and owner.get('job_id') == context['job'].id
        and owner.get('lease_token') == row.data.get('lease_token') and bool(row.data.get('lease_token'))
        and type(authorization) is dict and authorization.get('job_id') == context['job'].id
        and authorization.get('lease_token') == row.data['lease_token']
        and authorization.get('input_hash') == digest(inputs)
        and authorization.get('capability_hash') == context['capability'].content_hash
        and authorization.get('actor_id') == p.user_id and authorization.get('epoch') == approval['cancel_epoch'])
    return row


def _audit_data(state):
    return {'schema': STATE, 'state_hash': state['state_hash'], 'task_id': state['task_id'],
        'request_key_hash': digest(state['request_key']), 'operation': state['operation']}


def _state(db, p, context, call, inputs, key, approval):
    value = call.data.get('service_state')
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == STARTED, Audit.object_id == call.id)))
    if value is None:
        need(not events)
        return None
    need(type(value) is dict and set(value) == STATE_KEYS and value['schema'] == STATE)
    safe._bytes(value, 2 * 1024 * 1024)
    expected = {'operation': context['delegation']['operation'], 'task_id': context['task'].id,
        'job_id': context['job'].id, 'activation_id': approval['activation_id'],
        'capability_id': context['capability'].id, 'capability_hash': context['capability'].content_hash,
        'input_hash': digest(inputs), 'approval_hash': digest(approval),
        'source_binding_hash': context['binding']['binding_hash'], 'invocation_id': call.id,
        'request_key': key, 'actor_id': p.user_id, 'simulation_only': context['binding']['simulation_only']}
    need(all(_same(value.get(field), item) for field, item in expected.items())
        and type(value['created_at']) is str and bool(value['created_at'])
        and type(value['operation_data']) is dict
        and value['state_hash'] == digest({field: item for field, item in value.items() if field != 'state_hash'})
        and len(events) == 1 and events[0].actor_id == value['actor_id']
        and _same(events[0].data, _audit_data(value)))
    return copy.deepcopy(value)


def _record(db, p, context, call, inputs, key, approval, operation_data, created_at):
    state = {'schema': STATE, 'operation': context['delegation']['operation'], 'task_id': context['task'].id,
        'job_id': context['job'].id, 'activation_id': approval['activation_id'],
        'capability_id': context['capability'].id, 'capability_hash': context['capability'].content_hash,
        'input_hash': digest(inputs), 'approval_hash': digest(approval),
        'source_binding_hash': context['binding']['binding_hash'], 'invocation_id': call.id,
        'request_key': key, 'actor_id': p.user_id, 'created_at': created_at,
        'simulation_only': context['binding']['simulation_only'], 'operation_data': copy.deepcopy(operation_data)}
    state['state_hash'] = digest(state)
    safe._bytes(state, 2 * 1024 * 1024)
    call.data = {**call.data, 'service_state': state}
    audit(db, p, STARTED, call.id, _audit_data(state))
    db.flush()
    return state


def _acquisition_pin(db, p, acq, policy, request):
    plan = scoped(db, ObservationPlan, acq.data.get('observation_plan_id'), p)
    need(acq.entity_id == request['entity_id'] and acq.data.get('policy_id') == policy.id
        and acq.data.get('policy_hash') == policy.data['approved_hash']
        and plan.policy_id == policy.id and plan.entity_id == request['entity_id']
        and _same(plan.data.get('observation_request'), request)
        and type(acq.data.get('source_hash')) is str and safe.SHA.fullmatch(acq.data['source_hash']) is not None)
    return {'acquisition_id': acq.id, 'request_key': acq.idempotency_key, 'entity_id': acq.entity_id,
        'source_id': acq.capability_id, 'source_hash': acq.data['source_hash'], 'policy_id': policy.id,
        'policy_hash': acq.data['policy_hash'], 'observation_plan_id': plan.id,
        'observation_request_hash': digest(request)}


def _parent_request(db, p, context, inputs, *, saved=None):
    from . import observation_requests as requests
    from .perception import authorized_monitor
    gap = scoped(db, DataGap, inputs['gap_id'], p)
    request = requests.pin(db, p, gap) if saved is None else copy.deepcopy(saved)
    checked_gap, run = requests.guard(db, p, request, dispatch=saved is None)
    delegation = request.get('goal_delegation')
    task = context['task']
    need(checked_gap.id == gap.id and type(delegation) is dict and delegation.get('task_id') == task.id
        and request['entity_id'] in task.data['entity_ids']
        and request['entity_id'] in task.data['goal_spec']['entity_ids']
        and set(request['fields']).issubset(task.data['goal_spec']['required_fields']))
    policy = scoped(db, MonitorPolicy, inputs['policy_id'], p, True)
    authorized_monitor(db, p, policy)
    need(bool(policy.data.get('selection_requirement')), '补采服务需要真实设备选择及原观测计划合同；未走旧连接器')
    requests.effective(db, p, policy, request, dispatch=saved is None)
    source_contracts = _policy_sources(db, p, context, policy, request)
    return request, policy, source_contracts


def _policy_sources(db, p, context, policy, request):
    """Fence dynamic device targets before queuing, not after receiving data.

    Engineering can reach only an independently registered SIM environment and
    a server-approved local development endpoint. Source flags alone cannot
    redirect the service to a production connector. Polling repeats these live
    checks and compares the original pins; no authority result is cached.
    """
    from urllib.parse import urlparse
    from .config import settings
    from .signal_graph_http_source_v177 import configured, prepare
    identifiers = policy.data.get('source_ids')
    need(type(identifiers) is list and 0 < len(identifiers) <= MAX_ACQUISITIONS
        and len(set(identifiers)) == len(identifiers))
    result = []
    for identifier in sorted(identifiers):
        source = published(db, p, identifier, 'source')
        specification = source.data['specification']; data_policy = source.data['data_policy']
        if context['binding']['simulation_only']:
            need(data_policy.get('deployment') == 'local' and data_policy.get('test_only') is True
                and data_policy.get('simulation') is True and data_policy.get('cloud_allowed') is False
                and configured(source), '工程补采仅允许真实登记的本地模拟来源；未安排生产设备请求')
            endpoint = source.data['execution'].get('endpoint')
            allowed = {value.strip() for value in settings().development_connector_urls.split(',') if value.strip()}
            try:
                parsed = urlparse(endpoint); port = parsed.port
            except (ValueError, TypeError, AttributeError):
                raise HTTPException(409, '模拟连接器地址无法核对；未安排设备请求') from None
            need(type(endpoint) is str and endpoint in allowed and parsed.scheme in {'http', 'https'}
                and parsed.hostname == '127.0.0.1' and port is not None
                and not (parsed.username or parsed.password or parsed.query or parsed.fragment),
                '模拟设备接入未列入服务器明确的本地开发连接器名单；未安排生产动作')
            extractor = published(db, p, specification.get('http_observation_extractor_id_v177'), 'tool')
            from .business_flow_nested_adoption_v195 import BINDING as NESTED_BINDING
            from .device_gateway_sources_v270 import BINDING_KEY as MANAGED_SOURCE
            binding = context['binding']
            if binding.get('schema') == NESTED_BINDING and MANAGED_SOURCE in specification:
                pin = binding.get('observation_extractor_pins', {}).get(extractor.id)
                need(pin is not None and pin['capability_hash'] == extractor.content_hash
                    and pin['release_version'] == extractor.release_version
                    and pin['output_schema'] == extractor.data['output_schema'],
                    '此业务发布版本未授权该托管观测提取工具；未安排设备请求')
            instance = scoped(db, DomainInstance, context['task'].data['instance_id'], p)
            contract = prepare(db, p, instance, context['release'], source, extractor, [request['entity_id']])
            need(contract['scope']['instance_id'] == context['task'].data['instance_id']
                and contract['scope']['release_id'] == context['task'].data['instance_release_id'])
            result.append({'source_id': source.id, 'source_hash': source.content_hash,
                'extractor_id': extractor.id, 'extractor_hash': extractor.content_hash,
                'contract_hash': digest(contract), 'environment_id': contract['scope']['environment_id'],
                'environment_hash': contract['environment_hash'], 'configuration_hash': contract['configuration_hash'],
                'development_endpoint_hash': digest(endpoint)})
        else:
            need(not specification.get('simulation_environment_id')
                and data_policy.get('simulation') is not True and data_policy.get('test_only') is not True
                and not configured(source), '作者核对流程不能调度模拟来源；没有借模式移除来源标签')
            result.append({'source_id': source.id, 'source_hash': source.content_hash})
    return result


def _existing_acquisitions(db, p, policy, request):
    rows = list(db.execute(select(Acquisition, ObservationPlan).join(ObservationPlan,
        ObservationPlan.id == Acquisition.data['observation_plan_id'].as_string()).where(
        Acquisition.workspace_id == p.workspace_id, ObservationPlan.workspace_id == p.workspace_id,
        ObservationPlan.policy_id == policy.id, ObservationPlan.entity_id == request['entity_id'],
        Acquisition.entity_id == request['entity_id'], Acquisition.data['policy_id'].as_string() == policy.id,
        Acquisition.status.in_(['authorized', 'dispatching', 'unknown', 'awaiting_observation', 'succeeded']))).all())
    need(len(rows) <= MAX_ACQUISITIONS, '同一原缺口的补采数量超过服务容量；未截断')
    return sorted({acq.id for acq, plan in rows if _same(plan.data.get('observation_request'), request)})


def _notice_output(task, call, state):
    row = task.data.get(NOTICES, {}).get(call.id)
    need(type(row) is dict and set(state['operation_data']) == {'notice_id', 'notice_hash'}
        and state['operation_data']['notice_id'] == call.id
        and digest(row) == state['operation_data']['notice_hash']
        and row.get('schema') == NOTICE and row.get('task_id') == task.id
        and row.get('invocation_id') == call.id and row.get('message_hash') == digest(row.get('message'))
        and row.get('delivery_status') == 'delivered_to_in_app'
        and row.get('read_status') == 'not_tracked')
    receipt = {'schema': RECEIPT, 'operation': 'in_app_notice', 'task_id': task.id,
        'invocation_id': call.id, 'delivery_status': 'delivered_to_in_app', 'read_status': 'not_tracked',
        'recipient_ids': list(row['recipient_ids']), 'message_hash': row['message_hash'],
        'delivered_at': row['delivered_at'], 'state_hash': state['state_hash'],
        'simulation_only': state['simulation_only']}
    receipt['receipt_hash'] = digest(receipt)
    return {'result': '通知已保存到所选成员的站内收件记录；尚无已读确认。', 'service_receipt': receipt}


def notices(db, p, task):
    """Recipient-filtered storage projection, not a claim the person read it."""
    need(task.workspace_id == p.workspace_id)
    rows = task.data.get(NOTICES, {})
    need(type(rows) is dict and len(rows) <= MAX_NOTICES)
    result = []
    for identity, row in rows.items():
        need(type(row) is dict and row.get('schema') == NOTICE and row.get('invocation_id') == identity
            and row.get('task_id') == task.id and type(row.get('recipient_ids')) is list)
        if p.user_id in row['recipient_ids']:
            call = scoped(db, Invocation, identity, p)
            state = call.data.get('service_state')
            events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
                Audit.action == STARTED, Audit.object_id == call.id)))
            need(call.status == 'succeeded' and type(state) is dict and set(state) == STATE_KEYS
                and state['schema'] == STATE and state['operation'] == 'in_app_notice'
                and state['task_id'] == task.id and state['invocation_id'] == call.id
                and state['request_key'] == call.idempotency_key
                and state['input_hash'] == call.data.get('input_hash') == digest(call.data.get('inputs'))
                and state['approval_hash'] == digest(call.data.get('approval'))
                and state['state_hash'] == digest({key: value for key, value in state.items() if key != 'state_hash'})
                and len(events) == 1 and events[0].actor_id == state['actor_id']
                and _same(events[0].data, _audit_data(state))
                and _same(call.data.get('output'), _notice_output(task, call, state)))
            sender = db.scalar(select(User).where(User.id == state['actor_id'], User.active.is_(True)))
            result.append({'id': identity, 'message': row['message'], 'delivered_at': row['delivered_at'],
                'delivery_status': row['delivery_status'], 'read_status': row['read_status'],
                'simulation_only': row['simulation_only'], 'sender_name': sender.username if sender is not None else None})
    return copy.deepcopy(result)


def execute(db, p, cap, inputs, key, approval):
    context = validate_call_approval(db, p, cap, 'business', inputs, key, approval)
    call = _call(db, p, context, key, inputs, approval)
    previous = _state(db, p, context, call, inputs, key, approval)
    if previous is not None:
        return _reconcile(db, p, context, inputs, call, previous)
    task = context['task']; operation = context['delegation']['operation']
    at = db.scalar(select(func.clock_timestamp())).isoformat()
    if operation == 'in_app_notice':
        rows = list(db.execute(select(User, Membership).join(Membership, Membership.user_id == User.id).where(
            User.id.in_(inputs['recipient_ids']), User.active.is_(True), Membership.workspace_id == p.workspace_id)
            .with_for_update(read=True, of=[User, Membership]).execution_options(populate_existing=True)).all())
        need(len(rows) == len(inputs['recipient_ids']) and {user.id for user, membership in rows} == set(inputs['recipient_ids']),
            '所选收件人已停用或不属于当前工作区；未保存替代收件人', 403)
        notices_value = copy.deepcopy(task.data.get(NOTICES, {}))
        need(type(notices_value) is dict and call.id not in notices_value and len(notices_value) < MAX_NOTICES)
        notice = {'schema': NOTICE, 'task_id': task.id, 'invocation_id': call.id, 'actor_id': p.user_id,
            'recipient_ids': list(inputs['recipient_ids']), 'message': inputs['message'], 'message_hash': digest(inputs['message']),
            'delivery_status': 'delivered_to_in_app', 'read_status': 'not_tracked', 'delivered_at': at,
            'simulation_only': context['binding']['simulation_only']}
        notices_value[call.id] = notice
        task.data = {**task.data, NOTICES: notices_value}; task.version += 1
        state = _record(db, p, context, call, inputs, key, approval,
            {'notice_id': call.id, 'notice_hash': digest(notice)}, at)
        return _notice_output(task, call, state)
    return start_resense(db, p, context, call, inputs, key, approval,
        request_inputs=inputs, created_at=at)


def start_resense(db, p, context, call, wire_inputs, key, approval, *, request_inputs, created_at):
    """Share actual acquisition creation without changing frozen wire inputs.

    Callers must pass the live context from validate_call_approval. The common
    parent-request/source guards below still establish the actual Task/gap and
    approved monitor scope. The input/approval/state hashes use wire_inputs.
    """
    request, policy, source_contracts = _parent_request(db, p, context, request_inputs)
    from .perception import propose_acquisitions
    identifiers = propose_acquisitions(db, p, policy.id, entity_ids=[request['entity_id']], observation_request=request)
    need(type(identifiers) is list and len(set(identifiers)) == len(identifiers))
    if not identifiers:
        identifiers = _existing_acquisitions(db, p, policy, request)
    need(0 < len(identifiers) <= MAX_ACQUISITIONS,
        '没有同一批准策略、原任务及原缺口的可核对补采请求；缺口保留，未将空结果当完成')
    pins = [_acquisition_pin(db, p, scoped(db, Acquisition, identity, p), policy, request)
        for identity in sorted(identifiers)]
    _record(db, p, context, call, wire_inputs, key, approval,
        {'observation_request': request, 'acquisition_pins': pins, 'source_contracts': source_contracts}, created_at)
    # The child dispatch Jobs and original IDs become durable together with this
    # Invocation's immutable state before a device worker can observe them.
    db.commit()
    raise PendingService('原补采请求已保存，等待原观测处理和数据接收回执')


def _reconcile(db, p, context, inputs, call, state):
    if state['operation'] == 'in_app_notice':
        return _notice_output(context['task'], call, state)
    return resense_receipt(db, p, context, inputs, call, state)


def start_existing_resense(db, p, context, call, wire_inputs, key, approval, *,
        request_inputs, observation_request, created_at):
    """Freeze only the exact already-existing request; never propose a device call.

    A goal coordinator may finish its authorized observation after the reader
    snapshot. The caller must still verify the original producer and current
    authority. Completion remains resense_receipt's full-plan/ACK decision.
    """
    request, policy, source_contracts = _parent_request(db, p, context,
        request_inputs, saved=observation_request)
    identifiers = _existing_acquisitions(db, p, policy, request)
    need(0 < len(identifiers) <= MAX_ACQUISITIONS,
        '原缺口没有同一授权策略和请求的完整补采库存；未创建替代设备请求')
    pins = [_acquisition_pin(db, p, scoped(db, Acquisition, identity, p), policy, request)
        for identity in sorted(identifiers)]
    _record(db, p, context, call, wire_inputs, key, approval,
        {'observation_request': request, 'acquisition_pins': pins,
            'source_contracts': source_contracts}, created_at)
    return copy.deepcopy(call.data['service_state'])


def resense_receipt(db, p, context, request_inputs, call, state, *, receipt_operation='resense_with_receipt'):
    """Read the complete original plan; a new operation keeps its own receipt."""
    need(receipt_operation in {'resense_with_receipt', 'resense_from_context'})
    data = state['operation_data']
    need(set(data) == {'observation_request', 'acquisition_pins', 'source_contracts'}
        and type(data['acquisition_pins']) is list and 0 < len(data['acquisition_pins']) <= MAX_ACQUISITIONS)
    request, policy, source_contracts = _parent_request(db, p, context, request_inputs, saved=data['observation_request'])
    need(_same(source_contracts, data['source_contracts']), '原设备来源、模拟环境或接入合同已变化；仅保留历史请求')
    need(len({row['acquisition_id'] for row in data['acquisition_pins']}) == len(data['acquisition_pins']))
    observations = []
    for pin in data['acquisition_pins']:
        acq = scoped(db, Acquisition, pin['acquisition_id'], p)
        need(_same(_acquisition_pin(db, p, acq, policy, request), pin))
        if acq.status != 'succeeded':
            need(acq.status in {'authorized', 'dispatching', 'unknown', 'awaiting_observation'},
                '原补采已停止或受阻且没有成功观测；原请求保留，未重新创建或派发')
            raise PendingService('原补采尚无可采用的成功观测；保留原请求，未重新派发')
        observation_id = acq.data.get('closing_observation_id')
        need(type(observation_id) is str and bool(observation_id))
        observation = scoped(db, Observation, observation_id, p)
        need(observation.status == 'processed' and observation.entity_id == acq.entity_id
            and observation.source_id == acq.capability_id and observation.data.get('acquisition_id') == acq.id)
        observations.append({'acquisition_id': acq.id, 'observation_id': observation.id,
            'data_receipt_hash': _observation_receipt_hash(db, p, context, observation, state)})
    plan_receipts, followups = _complete_plans(db, p, context, policy, request, state)
    receipt = {'schema': RECEIPT, 'operation': receipt_operation, 'task_id': context['task'].id,
        'invocation_id': call.id, 'policy_id': policy.id, 'gap_id': request['gap_id'],
        'observations': observations, 'followup_observations': followups, 'plan_receipts': plan_receipts,
        'state_hash': state['state_hash'], 'simulation_only': state['simulation_only']}
    receipt['receipt_hash'] = digest(receipt)
    return {'result': '原补采请求已取得匹配的处理观测和数据接收回执。', 'service_receipt': receipt}


def _observation_receipt_hash(db, p, context, observation, state):
    from .observation_integrity import checked_report
    from .instance_data_receipts import observation_receipt
    need(observation.status == 'processed')
    checked_report(db, p, observation, allow_candidates=True)
    received = observation_receipt(db, p, observation)
    receipt = received.get('data_reception') if type(received) is dict else None
    need(type(receipt) is dict and receipt.get('observation_id') == observation.id
        and receipt.get('instance_id') == context['task'].data['instance_id']
        and receipt.get('release_id') == context['task'].data['instance_release_id']
        and receipt.get('package_id') == context['task'].package_id,
        '原观测尚无同一任务版本的完整数据接收回执；未推进流程')
    simulated = receipt.get('simulation_origin')
    if state['simulation_only']:
        need(type(simulated) is dict and simulated.get('simulation') is True
            and simulated.get('test_only') is True and simulated.get('field_verified') is False,
            '工程模拟来源标识缺失；未把真实或未核来源自行贴为模拟')
    else:
        need(simulated is None, '生产核对流程不能采用模拟数据回执；原观测和标签保留，未推进流程')
    return digest(received)


def _complete_plans(db, p, context, policy, request, state):
    """A first acquisition may cover only one part of the original gap.

    Follow-up acquisition Jobs are created solely by the original observation
    plan. Polling never calls refresh/propose and never changes its frozen first
    acquisition set; it records the actual later chain in the final receipt.
    """
    from . import observation_requests as requests
    from .sensing_selection import evidence
    from .data_gaps import receipts_ready
    pins = state['operation_data']['acquisition_pins']
    original_ids = {row['acquisition_id'] for row in pins}
    plans = sorted({row['observation_plan_id'] for row in pins})
    plan_receipts = []; followups = []
    effective = requests.effective(db, p, policy, request, dispatch=False)
    for identifier in plans:
        plan = scoped(db, ObservationPlan, identifier, p)
        need(plan.policy_id == policy.id and plan.entity_id == request['entity_id']
            and plan.data.get('policy_hash') == policy.data['approved_hash']
            and _same(plan.data.get('observation_request'), request))
        need(plan.status != 'cancelled', '原完整观测计划已取消；保留已收到的数据，未宣称完整补采成功')
        if plan.status != 'completed':
            raise PendingService('原观测计划尚未完整满足所有字段；等待其原后继请求，未再次安排补采')
        historical = None
        if context['binding'].get('schema') == 'authored-goal-data-workflow-binding-v288':
            from .workflow_completed_plan_receipts_v292 import completed_plan
            historical = completed_plan(db, p, context, effective, request, state, plan,
                max_acquisitions=MAX_ACQUISITIONS)
        current = (historical['evidence'] if historical is not None else
            evidence(db, p, effective, request['entity_id'], fields=request['fields']))
        if current.get('status') != 'sufficient' or not receipts_ready(db, p, {'result': current}):
            raise PendingService('原计划全部字段的当前合格证据或数据回执尚不充分')
        used = current.get('observation_evidence')
        need(type(used) is list and 0 < len(used) <= MAX_ACQUISITIONS
            and len({item['observation_id'] for item in used}) == len(used))
        supporting = []
        for item in used:
            observation = scoped(db, Observation, item['observation_id'], p)
            need(observation.entity_id == request['entity_id'])
            supporting.append({'observation_id': observation.id,
                'data_receipt_hash': _observation_receipt_hash(db, p, context, observation, state)})
        children = list(db.scalars(select(Acquisition).where(Acquisition.workspace_id == p.workspace_id,
            Acquisition.data['observation_plan_id'].as_string() == plan.id)))
        need(len(children) <= MAX_ACQUISITIONS, '原计划后继请求超过服务回执容量；未截断或宣称完整')
        for child in sorted(children, key=lambda row: row.id):
            _acquisition_pin(db, p, child, policy, request)
            if child.id in original_ids or child.status != 'succeeded':
                continue
            observation = scoped(db, Observation, child.data.get('closing_observation_id'), p)
            need(observation.data.get('acquisition_id') == child.id and observation.entity_id == child.entity_id
                and observation.source_id == child.capability_id)
            followups.append({'acquisition_id': child.id, 'observation_id': observation.id,
                'data_receipt_hash': _observation_receipt_hash(db, p, context, observation, state)})
        plan_receipts.append({'plan_id': plan.id,
            'evidence_hash': historical['evidence_hash'] if historical is not None else digest(current),
            'supporting_observations': supporting})
    need(len(followups) <= MAX_ACQUISITIONS, '原后继观测超过服务回执容量；未截断')
    return plan_receipts, followups


def reconcile(db, p, cap, inputs, key, approval, call):
    context = validate_call_approval(db, p, cap, 'business', inputs, key, approval)
    actual = _call(db, p, context, key, inputs, approval, supplied=call, allow_unknown=True)
    state = _state(db, p, context, actual, inputs, key, approval)
    need(state is not None, '原服务调用缺少已保存的请求库存；未创建替代请求')
    return _reconcile(db, p, context, inputs, actual, state)
