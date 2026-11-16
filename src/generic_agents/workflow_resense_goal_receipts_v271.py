"""SELECT-only acceptance of an original V270 context-resense result.

This never reconciles a service, queries fresh evidence, completes a plan,
renews a lease, dispatches, receives an event or impersonates its producer.
The current reader and the historical execution actors are distinct identities.
"""
import copy
from datetime import datetime
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .models import (Acquisition, Audit, Capability, DataGap, DataRun, Event,
    Invocation, Job, Membership, MonitorPolicy, Observation, ObservationPlan, Task, User)
from .services import validate
from . import workflow_graph as workflow
from . import workflow_graph_scope as scope
from . import workflow_service_v195 as service
from . import workflow_service_context_v196 as variant
from . import workflow_task_context_v196 as reader
from . import authored_service_workflow_store_v270 as adopted
from . import http_observation_mapping_v177 as mapping
from . import mapped_acquisition_origin_v177 as mapped
from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from .simulation_models_v176 import SimulationEnvironmentV176 as Environment

ERROR = '原上下文补采的实际调用、原供应方回执或独立数据接收无法完整核对，不能用于目标验收'
DELEGATION = {'service_actor': 'execution_service', 'operation': variant.OP, 'agent_role': 'business'}
CONTRACT_KEYS = {'source_id', 'source_hash', 'extractor_id', 'extractor_hash', 'contract_hash',
    'environment_id', 'environment_hash', 'configuration_hash', 'development_endpoint_hash'}


def _adopted(binding):
    if type(binding) is dict and binding.get('schema') == 'authored-goal-data-workflow-binding-v288':
        from . import authored_goal_data_workflow_store_v288
        return authored_goal_data_workflow_store_v288
    if type(binding) is dict and binding.get('schema') == 'authored-decision-workflow-binding-v286':
        from . import authored_decision_workflow_store_v286
        return authored_decision_workflow_store_v286
    if type(binding) is dict and binding.get('schema') == 'authored-decision-workflow-binding-v278':
        from . import authored_decision_workflow_store_v278
        return authored_decision_workflow_store_v278
    need(type(binding) is dict and binding.get('schema') == 'authored-service-workflow-binding-v270')
    return adopted


def need(value):
    if not value:
        raise HTTPException(409, ERROR)


def same(left, right):
    return digest(left) == digest(right)


def safe(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (HTTPException, ValueError, KeyError, TypeError, AttributeError,
                StopIteration, UnicodeError, OverflowError, RecursionError, OSError):
            raise HTTPException(409, ERROR) from None
    return checked


def fresh(db, p, model, identity):
    rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id,
        model.id == identity).execution_options(populate_existing=True)))
    need(len(rows) == 1)
    return rows[0]


def audits(db, p, action, identity):
    return list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).execution_options(populate_existing=True)))


def _reader_iam(db, p):
    need(db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id)) == p.user_id)


def _node_call(db, p, task, job, call, cap, inputs, approval, binding):
    actual, release, business, graph, state, activation, node = scope._frozen(
        db, p, task.id, job.data['activation_id'])
    actual_binding = workflow.business_binding(release, business)
    need(same(_adopted(actual_binding).check_binding(actual_binding), binding)
        and same(binding['graph'], graph) and actual.id == task.id
        and activation['status'] == 'completed' and job.status == 'succeeded'
        and call.status == 'succeeded' and node['execution']['capability_id'] == cap.id
        and node['execution']['capability_hash'] == cap.content_hash
        and cap.content_hash == cap.validated_hash == digest(cap.data)
        and node['execution']['release_version'] == cap.release_version
        and same(node['execution']['input_schema'], cap.data['input_schema'])
        and same(node['execution']['output_schema'], cap.data['output_schema'])
        and same(inputs, activation['inputs_snapshot']['arguments'])
        and same(inputs, call.data['inputs']) and same(call.data['output'], activation['output']))
    transport = scope._job_matches(actual, job, state, activation, node)
    attempt = transport.get('attempt')
    need(type(attempt) is int and 1 <= attempt <= node['execution']['max_attempts']
        and job.data.get('attempt') == attempt
        and job.idempotency_key == job.data['invocation_key']
            == 'graph:' + task.id + ':' + activation['id'] + ':' + str(attempt))
    owner = transport.get('dispatch_ownership'); authorization = transport.get('dispatch_authorization')
    need(type(owner) is dict and set(owner) == {'job_id', 'lease_token'}
        and owner['job_id'] == job.id and type(owner['lease_token']) is str and bool(owner['lease_token'])
        and type(authorization) is dict and set(authorization) == {'epoch', 'job_id', 'lease_token',
            'input_hash', 'capability_hash', 'actor_id', 'authorized_at'}
        and authorization['epoch'] == activation['epoch'] and authorization['job_id'] == job.id
        and authorization['lease_token'] == owner['lease_token']
        and authorization['input_hash'] == digest(inputs) and authorization['capability_hash'] == cap.content_hash
        and authorization['actor_id'] == node['actor_id'] == job.data['actor_id']
        and transport.get('phase') == 'succeeded' and transport.get('dispatch_committed') is True
        and transport.get('invocation_id') == call.id and transport.get('output_hash') == digest(call.data['output'])
        and transport.get('dispatch_input_hash') == digest(inputs))
    workflow.check_invocation(call, p, cap, node, inputs, job, owner, approval=approval)
    validate(cap.data['input_schema'], inputs); validate(cap.data['output_schema'], call.data['output'])
    completed = [item for item in audits(db, p, 'workflow.graph.node_completed', task.id)
        if item.data.get('activation_id') == activation['id']]
    need(len(completed) == 1 and completed[0].actor_id == node['actor_id']
        and completed[0].data == {'activation_id': activation['id'], 'invocation_id': call.id})
    authorized = [item for item in audits(db, p, 'workflow.graph.dispatch_authorized', task.id)
        if item.data.get('job_id') == job.id]
    need(len(authorized) == 1 and authorized[0].actor_id == node['actor_id']
        and authorized[0].data == {'activation_id': activation['id'], 'job_id': job.id,
            'epoch': activation['epoch'], 'input_hash': digest(inputs)})
    return {'task': actual, 'job': job, 'node': node, 'release': release, 'binding': binding,
        'capability': cap, 'transport': transport, 'delegation': DELEGATION}, activation


def _state_value(state, context, call, inputs, key, approval, started):
    """Pure original-actor comparison against an independent STARTED record."""
    need(type(state) is dict and set(state) == service.STATE_KEYS and state['schema'] == service.STATE)
    expected = {'operation': variant.OP, 'task_id': context['task'].id, 'job_id': context['job'].id,
        'activation_id': approval['activation_id'], 'capability_id': context['capability'].id,
        'capability_hash': context['capability'].content_hash, 'input_hash': digest(inputs),
        'approval_hash': digest(approval), 'source_binding_hash': context['binding']['binding_hash'],
        'invocation_id': call.id, 'request_key': key, 'actor_id': context['node']['actor_id'],
        'simulation_only': True}
    need(all(same(state.get(field), value) for field, value in expected.items())
        and type(state['created_at']) is str and bool(state['created_at'])
        and type(state['operation_data']) is dict
        and state['state_hash'] == digest({field: value for field, value in state.items() if field != 'state_hash'})
        and len(started) == 1 and started[0].actor_id == state['actor_id']
        and same(started[0].data, service._audit_data(state)))
    at = datetime.fromisoformat(state['created_at'].replace('Z', '+00:00'))
    need(at.tzinfo is not None and at.utcoffset() is not None)
    return copy.deepcopy(state)


def _source_proof(db, p, context, call, inputs, key, approval):
    value = call.data.get(variant.PROOF_KEY)
    expected = variant._proof_value(p, context, call, inputs, key, approval)  # Pure; uses workspace, not reader actor.
    rows = audits(db, p, variant.PROOF_AUDIT, call.id)
    need(type(value) is dict and set(value) == variant.PROOF_KEYS and same(value, expected)
        and len(rows) == 1 and rows[0].actor_id == context['node']['actor_id']
        and same(rows[0].data, variant._proof_anchor(expected)))
    return value


def checked_builtin_producer(db, p, task, value):
    """Phase-neutral original builtin receipt; never a new query or IO grant.

    Normal builtin SDK rows omit inputs/capability_snapshot and have no service
    approval. Their original Job/frozen graph receipt and PRODUCED audit carry
    those values. Present optional SDK metadata must agree, never be repaired.
    The original producer checker proves Goal/Q0/context without taking a Gap
    lock or rejecting a legitimately resolved Gap after independent reception.
    """
    producer = value['producer']
    job = fresh(db, p, Job, producer['job_id']); call = fresh(db, p, Invocation, producer['invocation_id'])
    cap = fresh(db, p, Capability, producer['capability_id']); reader.validate_manifest(cap.data)
    # The original checker uses scoped identity-map reads. Refresh all its
    # producer originals first, so stale cached SDK output cannot supply one
    # half of the independent PRODUCED proof while a fresh row supplies another.
    reader._checked_producer_context(db, p, value, task=task)
    need(producer['node_id'] == 'context_reader' and job.status == 'succeeded'
        and job.kind == 'execute_full_semantic_graph_node' and call.status == 'succeeded'
        and cap.content_hash == cap.validated_hash == digest(cap.data) == producer['capability_hash']
        and call.capability_id == cap.id and call.data.get('job_id') == job.id
        and call.data.get('role') == 'data' and call.data.get('side_effect') is False
        and call.data.get('approval') is None
        and call.idempotency_key == job.idempotency_key == job.data['invocation_key']
        and digest(job.data['inputs']) == job.data['input_hash'] == producer['input_hash'] == call.data.get('input_hash')
        and ('inputs' not in call.data or same(call.data['inputs'], job.data['inputs']))
        and ('capability_snapshot' not in call.data or same(call.data['capability_snapshot'], cap.data)))
    from .workflow_job_dependencies_v264 import parent_pin
    pin = parent_pin(db, p, task, job.id)
    need(pin['job_id'] == producer['job_id'] and pin['activation_id'] == producer['activation_id']
        and pin['invocation_id'] == producer['invocation_id'] and pin['actor_id'] == producer['actor_id']
        and pin['capability_id'] == producer['capability_id'] and pin['capability_hash'] == producer['capability_hash']
        and pin['input_hash'] == producer['input_hash'] and pin['output_hash'] == digest(call.data['output']))
    return copy.deepcopy(value)


def _producer(db, p, context, value):
    reader._check_context(value)
    producer = value['producer']; original_scope = value['scope']; task = context['task']; binding = context['binding']
    need(original_scope['workspace_id'] == p.workspace_id and original_scope['task_id'] == task.id
        and original_scope['instance_id'] == task.data['instance_id']
        and original_scope['release_id'] == context['release'].id
        and original_scope['package_id'] == task.package_id
        and original_scope['business_id'] == task.data['business_id']
        and original_scope['source_binding_hash'] == binding['binding_hash']
        and original_scope['graph_hash'] == digest(binding['graph']))
    if binding['schema'] in {'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286'}:
        from . import business_decision_sources_v276 as sources
        need(producer['node_id'] == 'data_packet')
        call = fresh(db, p, Invocation, producer['invocation_id'])
        reference = call.data['output']['packet']
        release, package_hash, data = sources._data(db, p, task, reference)
        original = data['context_result']; actual = original['evidence_package']
        need(release.id == context['release'].id and data['producer_job_id'] == producer['job_id']
            and data['invocation_id'] == call.id and same(actual['producer'], producer)
            and same(actual['scope'], original_scope)
            and package_hash == fresh(db, p, Capability, task.package_id).content_hash
            and any(same(value, candidate) for candidate in [original['resense_context'], *original['candidates']]
                if candidate is not None))
        # _data authenticates V199's command, real packet bytes, original
        # approval, completed Job/Invocation and both production audits. It
        # never reads a live query or impersonates V270's builtin Reader.
        return value
    need(binding['schema'] in {'authored-service-workflow-binding-v270', 'authored-goal-data-workflow-binding-v288'}
        and producer['node_id'] == 'context_reader')
    return checked_builtin_producer(db, p, task, value)


def _request(db, p, context, value, state):
    data = state['operation_data']
    need(set(data) == {'observation_request', 'acquisition_pins', 'source_contracts'}
        and same(data['observation_request'], value['gap']['observation_request']))
    request = data['observation_request']; task = context['task']
    run = fresh(db, p, DataRun, value['query']['query_id']); gap = fresh(db, p, DataGap, value['gap']['gap_id'])
    policy = fresh(db, p, MonitorPolicy, value['policy']['policy_id'])
    need(run.data.get('native_goal_task_id') == task.id and task.data.get('native_goal_query_id') == run.id
        and run.data.get('native_goal_context_hash') == value['scope']['native_goal_context_hash']
        and digest(run.data['request']) == value['query']['request_hash'] == request['query_request_hash']
        and same(run.data.get('goal_delegation'), request['goal_delegation'])
        and digest(request['goal_delegation']) == value['query']['goal_delegation_hash']
        and request['goal_delegation']['task_id'] == task.id
        and digest(request['goal_delegation']['goal_snapshot']) == value['scope']['goal_snapshot_hash']
        and digest(task.data.get('native_goal_context')) == value['scope']['native_goal_context_hash']
        and gap.query_id == run.id and gap.entity_id == request['entity_id'] == value['gap']['entity_id']
        and gap.id in run.data['data_gap_ids'] and gap.data.get('business_task_id') == task.id
        and same(gap.data['request'], request['request'])
        and request['entity_id'] in task.data['entity_ids']
        and request['entity_id'] in request['goal_delegation']['goal_snapshot']['spec']['entity_ids']
        and set(request['fields']) <= set(request['goal_delegation']['goal_snapshot']['spec']['required_fields']))
    return request, policy


def _contracts(db, p, context, state):
    from . import signal_graph_http_source_v177 as signal
    package = fresh(db, p, Capability, context['task'].package_id)
    need(package.content_hash == package.validated_hash == digest(package.data))
    result = {}
    rows = state['operation_data']['source_contracts']
    need(type(rows) is list and 0 < len(rows) <= service.MAX_ACQUISITIONS)
    for value in rows:
        need(type(value) is dict and set(value) == CONTRACT_KEYS and value['source_id'] not in result)
        source = fresh(db, p, Capability, value['source_id']); extractor = fresh(db, p, Capability, value['extractor_id'])
        need(source.kind == 'source' and source.content_hash == source.validated_hash == digest(source.data) == value['source_hash']
            and extractor.kind == 'tool' and extractor.content_hash == extractor.validated_hash == digest(extractor.data) == value['extractor_hash'])
        configured = source.data['specification']['http_observation_mapping_v177']; s = configured['scope']
        need(s['workspace_id'] == p.workspace_id and s['instance_id'] == context['task'].data['instance_id']
            and s['release_id'] == context['release'].id and s['package_id'] == package.id
            and s['package_hash'] == package.content_hash and value['environment_id'] == s['environment_id'])
        frozen = mapping.freeze_mapping(source.data, {**s, 'source_id': source.id},
            package.data['specification']['entity_schemas'], configured['links'])
        need(source.data['specification']['http_observation_extractor_id_v177'] == extractor.id
            and source.data['specification']['http_observation_extractor_hash_v177'] == extractor.content_hash)
        mapped._rule_shape(extractor, set(frozen['field_schemas']),
            {link['field_key']: link['unit'] for link in configured['links'].values()}, s)
        environment = fresh(db, p, Environment, s['environment_id'])
        created = audits(db, p, 'simulation.environment.created_v176', environment.id)
        need(environment.instance_id == s['instance_id'] and environment.release_id == s['release_id']
            and environment.data['package_id'] == package.id
            and environment.data['configuration_hash'] == context['release'].configuration_hash
            and len(created) == 1 and created[0].actor_id == environment.actor_id
            and created[0].data == {'record_hash': digest(environment.data)})
        contract = signal.checked_contract({'schema': signal.CONTRACT_SCHEMA, 'family': signal.FAMILY,
            'scope': copy.deepcopy(s), 'source_id': source.id, 'source_hash': source.content_hash,
            'extractor_id': extractor.id, 'extractor_hash': extractor.content_hash,
            'environment_hash': digest(environment.data), 'configuration_hash': context['release'].configuration_hash,
            'mapping': frozen, 'field_units': {link['field_key']: link['unit'] for link in configured['links'].values()},
            'simulation': True, 'test_only': True, 'field_verified': False})
        need(value['contract_hash'] == digest(contract) and value['environment_hash'] == contract['environment_hash']
            and value['configuration_hash'] == contract['configuration_hash']
            and value['development_endpoint_hash'] == digest(source.data['execution']['endpoint']))
        result[source.id] = value
    return result


def _acquisition(db, p, context, identifier, request, policy, policy_hash, contracts):
    acq = fresh(db, p, Acquisition, identifier)
    plan = fresh(db, p, ObservationPlan, acq.data['observation_plan_id'])
    need(acq.entity_id == request['entity_id'] and acq.data['policy_id'] == policy.id
        and acq.data['policy_hash'] == policy_hash and plan.policy_id == policy.id
        and plan.entity_id == request['entity_id'] and plan.data['policy_hash'] == policy_hash
        and same(plan.data['observation_request'], request) and acq.capability_id in contracts
        and acq.data['source_hash'] == contracts[acq.capability_id]['source_hash'])
    pin = {'acquisition_id': acq.id, 'request_key': acq.idempotency_key, 'entity_id': acq.entity_id,
        'source_id': acq.capability_id, 'source_hash': acq.data['source_hash'], 'policy_id': policy.id,
        'policy_hash': policy_hash, 'observation_plan_id': plan.id, 'observation_request_hash': digest(request)}
    return acq, plan, pin


def _observation(db, p, context, observation_id, state, contracts, *, acquisition=None):
    observation = fresh(db, p, Observation, observation_id)
    need(observation.status == 'processed' and observation.source_id in contracts)
    origin = mapped.guard(db, p, observation, execution=False, result=observation.data['extracted'])
    need(type(origin) is dict and origin['instance_id'] == context['task'].data['instance_id']
        and origin['release_id'] == context['release'].id and origin['entity_id'] == observation.entity_id)
    exchange = fresh(db, p, Exchange, origin['exchange_id'])
    need(exchange.observation_id == observation.id and exchange.acquisition_id == observation.data.get('acquisition_id'))
    if acquisition is not None:
        need(acquisition.status == 'succeeded' and acquisition.data.get('closing_observation_id') == observation.id
            and exchange.acquisition_id == acquisition.id and observation.entity_id == acquisition.entity_id
            and observation.source_id == acquisition.capability_id)
    events = list(db.scalars(select(Event).where(Event.workspace_id == p.workspace_id,
        Event.kind == 'observation.data', Event.idempotency_key == 'data:' + observation.id)
        .execution_options(populate_existing=True)))
    need(len(events) == 1 and events[0].status == 'received' and events[0].received_at is not None)
    received_hash = service._observation_receipt_hash(db, p, context, observation, state)
    return observation, received_hash


def _inventory(db, p, context, value, state, output):
    request, policy = _request(db, p, context, value, state); contracts = _contracts(db, p, context, state)
    source_pins = value['policy']['source_pins']
    need(type(source_pins) is list and len(source_pins) == len(contracts)
        and len({item['source_id'] for item in source_pins}) == len(source_pins)
        and same(sorted(source_pins, key=lambda item: item['source_id']),
            [{'source_id': identifier, 'source_hash': contracts[identifier]['source_hash']}
                for identifier in sorted(contracts)]))
    receipt = output['service_receipt']; pins = state['operation_data']['acquisition_pins']
    need(type(pins) is list and 0 < len(pins) <= service.MAX_ACQUISITIONS
        and len({pin['acquisition_id'] for pin in pins}) == len(pins))
    original = []; plan_ids = set(); original_ids = set()
    for pin in pins:
        acq, plan, actual = _acquisition(db, p, context, pin['acquisition_id'], request, policy,
            value['policy']['approved_hash'], contracts)
        need(same(actual, pin)); original_ids.add(acq.id); plan_ids.add(plan.id)
        observation, received_hash = _observation(db, p, context, acq.data.get('closing_observation_id'),
            state, contracts, acquisition=acq)
        original.append({'acquisition_id': acq.id, 'observation_id': observation.id, 'data_receipt_hash': received_hash})
    need(same(receipt['observations'], original) and receipt['policy_id'] == policy.id
        and receipt['gap_id'] == request['gap_id'])
    plan_receipts = receipt['plan_receipts']
    need(len(plan_receipts) == len(plan_ids) and [item['plan_id'] for item in plan_receipts] == sorted(plan_ids))
    followups = []
    for saved in plan_receipts:
        plan = fresh(db, p, ObservationPlan, saved['plan_id'])
        need(plan.status == 'completed' and plan.policy_id == policy.id and plan.entity_id == request['entity_id']
            and plan.data['policy_hash'] == value['policy']['approved_hash']
            and same(plan.data['observation_request'], request))
        mapping._hash(saved['evidence_hash'])  # Opaque original output hash; no fresh evidence query is fabricated.
        supporting = saved['supporting_observations']
        need(0 < len(supporting) <= service.MAX_ACQUISITIONS
            and len({item['observation_id'] for item in supporting}) == len(supporting))
        covered = set()
        for item in supporting:
            observation, received_hash = _observation(db, p, context, item['observation_id'], state, contracts)
            need(observation.entity_id == request['entity_id'] and item['data_receipt_hash'] == received_hash)
            covered.update(key for key, field in observation.data['extracted']['attributes'].items() if field is not None)
        need(set(request['fields']) <= covered)
        children = list(db.scalars(select(Acquisition).where(Acquisition.workspace_id == p.workspace_id,
            Acquisition.data['observation_plan_id'].as_string() == plan.id).order_by(Acquisition.id)
            .limit(service.MAX_ACQUISITIONS + 1).execution_options(populate_existing=True)))
        need(len(children) <= service.MAX_ACQUISITIONS)
        for child in children:
            actual, _, _ = _acquisition(db, p, context, child.id, request, policy,
                value['policy']['approved_hash'], contracts)
            if child.id in original_ids or child.status != 'succeeded':
                continue
            observation, received_hash = _observation(db, p, context, actual.data.get('closing_observation_id'),
                state, contracts, acquisition=actual)
            followups.append({'acquisition_id': actual.id, 'observation_id': observation.id, 'data_receipt_hash': received_hash})
    need(same(followups, receipt['followup_observations']))


@safe
def saved_receipt(db, p, task, job, call, cap, inputs, key, approval, binding):
    """Return only the original output after cold complete-receipt validation."""
    with db.no_autoflush:
        _reader_iam(db, p)
        need(isinstance(task, Task) and isinstance(job, Job) and isinstance(call, Invocation)
            and isinstance(cap, Capability) and type(binding) is dict)
        family = _adopted(binding)
        binding = family.check_binding(binding)
        need(family.service_delegations(binding).get('resense') == DELEGATION)
        task = fresh(db, p, Task, task.id); job = fresh(db, p, Job, job.id)
        call = fresh(db, p, Invocation, call.id); cap = fresh(db, p, Capability, cap.id)
        variant.validate_manifest(cap.data); variant._inputs(inputs)
        context, activation = _node_call(db, p, task, job, call, cap, inputs, approval, binding)
        need(context['node']['id'] == 'resense' and cap.data['execution']['operation'] == variant.OP
            and key == call.idempotency_key == job.data['invocation_key']
            and same(approval, service.make_approval(task, job, context['node'], inputs)))
        state = _state_value(call.data.get('service_state'), context, call, inputs, key, approval,
            audits(db, p, service.STARTED, call.id))
        proof = _source_proof(db, p, context, call, inputs, key, approval)
        value = _producer(db, p, context, inputs['context'])
        output = copy.deepcopy(call.data['output']); receipt = output['service_receipt']
        need(receipt['schema'] == variant.RECEIPT and receipt['operation'] == variant.OP
            and receipt['task_id'] == task.id and receipt['invocation_id'] == call.id
            and receipt['state_hash'] == state['state_hash'] and receipt['simulation_only'] is True
            and receipt['context_hash'] == proof['context_hash'] == value['context_hash']
            and receipt['producer_invocation_id'] == proof['producer_invocation_id']
            and receipt['receipt_hash'] == digest({field: item for field, item in receipt.items() if field != 'receipt_hash'}))
        _inventory(db, p, context, value, state, output)
        return output
