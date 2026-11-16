"""Service handlers over real pure graph state with explicit authority IO spies.

No real registration, IAM, approval, PG, HTTP delivery, physical observation or
data-agent receipt is proven here. Those boundaries are independently exercised
by Root. The parser, graph inputs, service state/Audit checks and handlers remain
the actual product functions; no handler or whole validator is replaced.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import workflow_service_v195 as service
from generic_agents import business_flow_nested_execution_v195 as compiler
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.contracts import digest
from tests.test_business_flow_nested_adoption_v195 import identity
from tests.test_business_flow_nested_execution_v192 import finish
from tests.test_workflow_service_delegation_v195 import service_scene, capability, reseal
from tests.test_report_business_flow_wire_v184 import no_external


AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


class IO:
    """Bounded row/lease/time adapter only, not a SQL correctness proof."""
    def __init__(self):
        self.rows = {}; self.events = []; self.commits = 0; self.flushes = 0
        self.people = []; self.reused = []; self.lease = None; self.job = None; self.task = None

    def scalar(self, statement):
        entity = statement.column_descriptions[0].get('entity')
        if entity is service.Job:
            job = self.job
            if (job.status == 'running' and job.kind == 'execute_full_semantic_graph_node'
                and job.lease_token == self.lease['lease_token'] and job.lease_until > AT):
                return job
            return None
        if entity is service.Task: return self.task
        if entity is service.Invocation: return self.call
        if entity is service.User: return self.rows.get((service.User, identity('service-actor')))
        return AT

    def scalars(self, statement):
        entity = statement.column_descriptions[0]['entity']
        if entity is service.Audit: return list(self.events)
        assert entity is service.Acquisition
        return [row for (model, _), row in self.rows.items() if model is service.Acquisition]

    def execute(self, statement):
        entities = {item['entity'] for item in statement.column_descriptions}
        if entities == {service.User, service.Membership}: return NS(all=lambda: list(self.people))
        assert entities == {service.Acquisition, service.ObservationPlan}
        return NS(all=lambda: list(self.reused))

    def flush(self): self.flushes += 1
    def commit(self): self.commits += 1


def scene(monkeypatch, actor='task_center'):
    from generic_agents import workflow_graph_scope as scope, workflow_graph as workflow
    from generic_agents import leases, relations, layered_approval_store_v175 as approvals
    from generic_agents import workflow_graph_goal_guard_v133 as goals
    values, path = service_scene(actor); plan, source, bindings, sidecar, paths = values
    cap = capability(actor); cap.id = identity('service-capability')
    p = NS(user_id=identity('service-actor'), workspace_id=identity('service-workspace'), role='operator')
    inputs = ({'recipient_ids': [identity('recipient')], 'message': '请核对原任务中已保存的业务依据。'}
        if actor == 'task_center' else {'policy_id': identity('monitor'), 'gap_id': identity('gap')})
    cap.data['input_schema'] = service.input_schema(cap.data['execution']['operation'])
    cap.data['output_schema'] = service.output_schema(cap.data['execution']['operation'])
    reseal(cap)
    node_binding = bindings['nodes'][path]; node_binding['actor_id'] = p.user_id
    node_binding['execution'].update(capability_id=cap.id, capability_hash=cap.content_hash,
        input_schema=copy.deepcopy(cap.data['input_schema']), output_schema=copy.deepcopy(cap.data['output_schema']),
        arguments=copy.deepcopy(inputs))
    translated = compiler.build_execution_graph(plan, 0, bindings,
        scope_binding=source['scope_binding'], causal_bindings=sidecar)
    graph = translated['graph']; mapped = {row['ast_path']: row['graph_node_id'] for row in translated['source_mapping']['actions']}
    state = kernel.new_run(graph, {}, 10)
    state = finish(graph, state, mapped[paths['确认修复建议']], {'approved': True})
    state = finish(graph, state, mapped[paths['组织通知与补核']])
    activation = next(row for row in state['activations'].values() if row['node_id'] == mapped[path] and row['status'] == 'ready')
    node = next(row for row in graph['nodes'] if row['id'] == activation['node_id'])
    task = NS(id=identity('service-task'), workspace_id=p.workspace_id, package_id=identity('package'), version=1,
        data={'graph_runtime': state, 'entity_ids': [identity('entity')], 'instance_id': identity('instance'),
            'instance_release_id': identity('release'), 'goal_spec': {'entity_ids': [identity('entity')],
                'required_fields': ['temperature_c']}})
    key = 'graph:' + task.id + ':' + activation['id'] + ':1'
    job = NS(id=identity('service-job'), workspace_id=p.workspace_id, kind=scope.KIND, status='running',
        lease_token=identity('current-lease'), lease_until=AT + timedelta(minutes=3), idempotency_key=key,
        data={'task_id': task.id, 'actor_id': p.user_id, 'activation_id': activation['id'], 'graph_hash': state['graph_hash'],
            'cancel_epoch': activation['epoch'], 'activation_version': 0, 'inputs': copy.deepcopy(inputs),
            'input_hash': digest(inputs), 'invocation_key': key, 'attempt': 1})
    original_owner = {'job_id': job.id, 'lease_token': identity('original-dispatch-lease')}
    transport = {'job_id': job.id, 'invocation_key': key, 'cancel_epoch': activation['epoch'], 'attempt': 1,
        'input_hash': digest(inputs), 'phase': 'running', 'dispatch_ownership': original_owner,
        'dispatch_authorization': {'job_id': job.id, 'lease_token': original_owner['lease_token'],
            'actor_id': p.user_id, 'input_hash': digest(inputs), 'capability_hash': cap.content_hash,
            'epoch': activation['epoch']}}
    task.data['graph_jobs'] = {activation['id']: transport}
    binding = {'schema': service.adoption.BINDING, 'translation': translated, 'binding_hash': digest(translated),
        'simulation_only': False}
    release = NS(id=task.data['instance_release_id']); business = {'name': '公开合成业务'}
    db = IO(); db.job = job; db.task = task
    db.lease = {'job_id': job.id, 'lease_token': job.lease_token}
    approval = service.make_approval(task, job, node, inputs)
    call = NS(id=identity('service-invocation'), workspace_id=p.workspace_id, capability_id=cap.id,
        idempotency_key=key, status='running', data={'input_hash': digest(inputs), 'inputs': copy.deepcopy(inputs),
            'role': 'business', 'capability_hash': cap.content_hash, 'approval': copy.deepcopy(approval),
            'side_effect': True, **original_owner})
    db.call = call
    checks = []
    monkeypatch.setattr(service, 'published', lambda db, current, cap_id: cap)
    monkeypatch.setattr(leases, 'ownership', lambda db: copy.deepcopy(db.lease))
    def frozen_boundary(db, current, task_id, activation_id):
        assert task_id == task.id and activation_id == activation['id']
        kernel.check_run(graph, state)
        return task, release, business, graph, state, activation, node
    monkeypatch.setattr(scope, '_frozen', frozen_boundary)
    monkeypatch.setattr(workflow, 'business_binding', lambda *args: binding)
    monkeypatch.setattr(workflow, 'live', lambda *args: checks.append('current-source-and-release'))
    monkeypatch.setattr(approvals, 'guard', lambda *args, **kwargs: checks.append('actual-task-approval'))
    monkeypatch.setattr(goals, 'before_dispatch', lambda *args: checks.append('current-original-goal'))
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *args: checks.append('actual-current-IAM'))
    def scoped_boundary(db, model, row_id, current, *args, **kwargs):
        row = db.rows[(model, row_id)]
        assert row.workspace_id == current.workspace_id
        return row
    monkeypatch.setattr(service, 'scoped', scoped_boundary)
    def audit_boundary(db, current, action, object_id, data):
        assert action == service.STARTED and object_id == call.id
        db.events.append(NS(actor_id=current.user_id, data=copy.deepcopy(data)))
    monkeypatch.setattr(service, 'audit', audit_boundary)
    db.rows[(service.Invocation, call.id)] = call
    db.rows[(service.User, p.user_id)] = NS(id=p.user_id, username='已核对的发送账号')
    db.people = [(NS(id=inputs.get('recipient_ids', [p.user_id])[0]), NS(role='viewer'))]
    if actor == 'execution_service':
        from generic_agents import data_gaps
        monkeypatch.setattr(data_gaps, 'locked_gap', lambda *args: None)
    return NS(db=db, p=p, cap=cap, task=task, job=job, node=node, state=state,
        transport=transport, binding=binding, approval=approval, call=call, inputs=inputs, key=key, checks=checks)


def test_approval_is_only_a_description_and_actual_scope_gates_run_with_current_lease(monkeypatch):
    s = scene(monkeypatch)
    assert set(s.approval) == service.APPROVAL_KEYS and 'lease_token' not in s.approval
    checked = service.validate_call_approval(s.db, s.p, s.cap, 'business', s.inputs, s.key, s.approval)
    assert checked['job'] is s.job and checked['node'] is s.node
    assert s.checks == ['actual-task-approval', 'current-source-and-release', 'current-original-goal', 'actual-current-IAM']
    # Polling uses the new lease of the same Job while preserving the original
    # Invocation/dispatch owner. It never rewrites historical ownership.
    assert s.call.data['lease_token'] != s.db.lease['lease_token']
    assert service._call(s.db, s.p, checked, s.key, s.inputs, s.approval) is s.call


@pytest.mark.parametrize('fault', ['viewer', 'expired', 'queued', 'wrong_kind', 'wrong_lease',
    'wrong_input', 'wrong_epoch', 'wrong_key', 'ordinary_binding', 'missing_delegation'])
def test_actual_validator_rejects_self_consistent_claims_without_current_owned_scope(monkeypatch, fault):
    s = scene(monkeypatch)
    if fault == 'viewer': s.p.role = 'viewer'
    elif fault == 'expired': s.job.lease_until = AT - timedelta(seconds=1)
    elif fault == 'queued': s.job.status = 'queued'
    elif fault == 'wrong_kind': s.job.kind = 'execute_step'
    elif fault == 'wrong_lease': s.db.lease['lease_token'] = identity('foreign-lease')
    elif fault == 'wrong_input': s.inputs['message'] += '被替换'
    elif fault == 'wrong_epoch': s.approval['cancel_epoch'] += 1
    elif fault == 'wrong_key': s.key += ':other'
    elif fault == 'ordinary_binding': s.binding['schema'] = 'business-flow-execution-binding-v1'
    else:
        for row in s.binding['translation']['source_mapping']['actions']:
            row.pop('service_delegation', None)
    with pytest.raises((HTTPException, ValueError)):
        service.validate_call_approval(s.db, s.p, s.cap, 'business', s.inputs, s.key, s.approval)
    assert s.db.commits == 0 and 'service_state' not in s.call.data


def test_notice_is_persisted_for_real_selected_members_with_an_independent_receipt_not_a_read_claim(monkeypatch):
    s = scene(monkeypatch); before_key = s.call.idempotency_key
    output = service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert output['service_receipt']['delivery_status'] == 'delivered_to_in_app'
    assert output['service_receipt']['read_status'] == 'not_tracked'
    assert output['service_receipt']['recipient_ids'] == s.inputs['recipient_ids']
    assert len(s.db.events) == 1 and len(s.task.data[service.NOTICES]) == 1
    assert s.call.idempotency_key == before_key and s.db.commits == 0
    again = service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert again == output and len(s.db.events) == 1 and len(s.task.data[service.NOTICES]) == 1
    s.call.status = 'succeeded'; s.call.data['output'] = output
    recipient = NS(workspace_id=s.p.workspace_id, user_id=s.inputs['recipient_ids'][0])
    rows = service.notices(s.db, recipient, s.task)
    assert rows[0]['message'] == s.inputs['message'] and rows[0]['read_status'] == 'not_tracked'
    assert rows[0]['sender_name'] == '已核对的发送账号'
    assert service.notices(s.db, NS(workspace_id=s.p.workspace_id, user_id=identity('non-recipient')), s.task) == []


@pytest.mark.parametrize('fault', ['recipient_missing', 'orphan_audit', 'missing_audit', 'changed_notice',
    'changed_state', 'changed_original_owner'])
def test_notice_does_not_reapply_or_display_a_forged_delivery(monkeypatch, fault):
    s = scene(monkeypatch)
    if fault == 'recipient_missing':
        s.db.people = []
    elif fault == 'orphan_audit':
        s.db.events.append(NS(actor_id=s.p.user_id, data={}))
    else:
        service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
        if fault == 'missing_audit': s.db.events.clear()
        elif fault == 'changed_notice': s.task.data[service.NOTICES][s.call.id]['message'] += '伪造'
        elif fault == 'changed_state': s.call.data['service_state']['operation_data']['notice_hash'] = 'f' * 64
        else: s.call.data['lease_token'] = identity('forged-original-owner')
    with pytest.raises(HTTPException): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert s.db.commits == 0


def resense_boundaries(monkeypatch, s, *, reuse=False):
    from generic_agents import (observation_requests, perception, observation_integrity,
        instance_data_receipts, sensing_selection, data_gaps)
    request = {'gap_id': s.inputs['gap_id'], 'entity_id': s.task.data['entity_ids'][0],
        'fields': ['temperature_c'], 'goal_delegation': {'task_id': s.task.id}}
    policy = NS(id=s.inputs['policy_id'], workspace_id=s.p.workspace_id, data={'approved_hash': 'a' * 64,
        'selection_requirement': {'required': True}})
    gap = NS(id=request['gap_id'], workspace_id=s.p.workspace_id)
    plan = NS(id=identity('observation-plan'), workspace_id=s.p.workspace_id, policy_id=policy.id, status='selecting',
        entity_id=request['entity_id'], data={'observation_request': copy.deepcopy(request), 'policy_hash': policy.data['approved_hash']})
    acq = NS(id=identity('acquisition'), workspace_id=s.p.workspace_id, entity_id=request['entity_id'],
        capability_id=identity('http-source'), idempotency_key='original-equipment-request', status='authorized',
        data={'observation_plan_id': plan.id, 'policy_id': policy.id, 'policy_hash': policy.data['approved_hash'], 'source_hash': 'b' * 64})
    observation = NS(id=identity('observation'), workspace_id=s.p.workspace_id, entity_id=acq.entity_id,
        source_id=acq.capability_id, status='processed', data={'acquisition_id': acq.id})
    receipt = {'data_reception': {'observation_id': observation.id, 'instance_id': s.task.data['instance_id'],
        'release_id': s.task.data['instance_release_id'], 'package_id': s.task.package_id}}
    for model, row in ((service.DataGap, gap), (service.MonitorPolicy, policy),
        (service.ObservationPlan, plan), (service.Acquisition, acq), (service.Observation, observation)):
        s.db.rows[(model, row.id)] = row
    calls = []
    monkeypatch.setattr(observation_requests, 'pin', lambda *args: copy.deepcopy(request))
    monkeypatch.setattr(observation_requests, 'guard', lambda *args, **kwargs: (gap, object()))
    monkeypatch.setattr(observation_requests, 'effective', lambda *args, **kwargs: policy)
    monkeypatch.setattr(perception, 'authorized_monitor', lambda *args: calls.append('monitor-current'))
    # Dynamic source/environment/device authority is a separate explicit IO
    # boundary here. Its real pre-propose helper is exercised below and by PG.
    monkeypatch.setattr(service, '_policy_sources', lambda *args: [{'source_id': acq.capability_id,
        'source_hash': acq.data['source_hash']}])
    def propose_boundary(db, p, policy_id, **options):
        calls.append(('propose-once', policy_id, copy.deepcopy(options)))
        return [] if reuse else [acq.id]
    monkeypatch.setattr(perception, 'propose_acquisitions', propose_boundary)
    monkeypatch.setattr(observation_integrity, 'checked_report', lambda *args, **kwargs: calls.append('original-observation-integrity'))
    monkeypatch.setattr(instance_data_receipts, 'observation_receipt', lambda *args: copy.deepcopy(receipt))
    complete_evidence = {'status': 'sufficient', 'observation_evidence': [{'observation_id': observation.id}]}
    monkeypatch.setattr(sensing_selection, 'evidence', lambda *args, **kwargs: copy.deepcopy(complete_evidence))
    monkeypatch.setattr(data_gaps, 'receipts_ready', lambda *args: True)
    s.db.reused = [(acq, plan)] if reuse else []
    return NS(request=request, policy=policy, plan=plan, acq=acq, observation=observation,
        receipt=receipt, calls=calls, complete_evidence=complete_evidence)


@pytest.mark.parametrize('reuse', [False, True])
def test_resense_proposes_once_or_reuses_exact_original_plan_then_only_reconciles_frozen_ids(monkeypatch, reuse):
    s = scene(monkeypatch, 'execution_service'); b = resense_boundaries(monkeypatch, s, reuse=reuse)
    with pytest.raises(service.PendingService): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert s.db.commits == 1 and len(s.db.events) == 1
    stored = copy.deepcopy(s.call.data['service_state'])
    assert stored['operation_data']['acquisition_pins'][0]['acquisition_id'] == b.acq.id
    assert len([row for row in b.calls if isinstance(row, tuple)]) == 1
    s.call.status = 'waiting_receipt'; s.transport['phase'] = 'waiting_service'
    with pytest.raises(service.PendingService): service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    b.acq.status = 'succeeded'; b.acq.data['closing_observation_id'] = b.observation.id
    b.plan.status = 'completed'
    output = service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert output['service_receipt']['observations'] == [{'acquisition_id': b.acq.id,
        'observation_id': b.observation.id, 'data_receipt_hash': digest(b.receipt)}]
    assert len([row for row in b.calls if isinstance(row, tuple)]) == 1
    assert s.call.data['service_state'] == stored and s.db.commits == 1
    assert output['service_receipt']['simulation_only'] is False
    assert output['service_receipt']['followup_observations'] == []
    assert output['service_receipt']['plan_receipts'][0]['plan_id'] == b.plan.id


@pytest.mark.parametrize('fault', ['empty', 'foreign_plan', 'foreign_parent', 'wrong_policy'])
def test_empty_or_unrelated_requests_cannot_become_a_successful_supplement(monkeypatch, fault):
    s = scene(monkeypatch, 'execution_service'); b = resense_boundaries(monkeypatch, s, reuse=True)
    if fault == 'empty': s.db.reused = []
    elif fault == 'foreign_plan': b.plan.data['observation_request'] = {'gap_id': identity('foreign-gap')}
    elif fault == 'foreign_parent': b.request['goal_delegation']['task_id'] = identity('foreign-task')
    else: b.acq.data['policy_hash'] = 'f' * 64
    with pytest.raises(HTTPException): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert s.db.commits == 0 and 'service_state' not in s.call.data


@pytest.mark.parametrize('fault', ['missing_closing', 'different_acquisition', 'unprocessed', 'missing_ack',
    'foreign_release', 'cancelled', 'sim_without_mark'])
def test_original_acquisition_is_not_done_until_its_bound_processed_observation_and_real_ack(monkeypatch, fault):
    s = scene(monkeypatch, 'execution_service'); b = resense_boundaries(monkeypatch, s)
    if fault == 'sim_without_mark': s.binding['simulation_only'] = True
    with pytest.raises(service.PendingService): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    b.acq.status = 'succeeded'; b.acq.data['closing_observation_id'] = b.observation.id
    if fault == 'missing_closing': b.acq.data.pop('closing_observation_id')
    elif fault == 'different_acquisition': b.observation.data['acquisition_id'] = identity('foreign-acq')
    elif fault == 'unprocessed': b.observation.status = 'received'
    elif fault == 'missing_ack': b.receipt.clear()
    elif fault == 'foreign_release': b.receipt['data_reception']['release_id'] = identity('foreign-release')
    elif fault == 'cancelled': b.acq.status = 'cancelled'
    with pytest.raises(HTTPException): service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert len([row for row in b.calls if isinstance(row, tuple)]) == 1


def test_original_goal_or_current_approval_revocation_propagates_without_new_request(monkeypatch):
    from generic_agents import workflow_graph_goal_guard_v133 as goals
    s = scene(monkeypatch, 'execution_service'); b = resense_boundaries(monkeypatch, s)
    with pytest.raises(service.PendingService): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    def stopped(*args): raise HTTPException(409, 'original goal expired')
    monkeypatch.setattr(goals, 'before_dispatch', stopped)
    with pytest.raises(HTTPException, match='original goal expired'):
        service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert len([row for row in b.calls if isinstance(row, tuple)]) == 1 and s.db.commits == 1


@pytest.mark.parametrize('fault', ['missing_output', 'extra_input', 'wide_input', 'wrong_service', 'side_effect', 'extra_role'])
def test_registration_rejects_schemas_the_real_handler_cannot_honestly_fulfill(monkeypatch, fault):
    s = scene(monkeypatch); manifest = copy.deepcopy(s.cap.data)
    assert service.validate_manifest(manifest) == manifest
    if fault == 'missing_output': manifest['output_schema']['required'].remove('service_receipt')
    elif fault == 'extra_input': manifest['input_schema']['properties']['execute_now'] = {'type': 'boolean'}
    elif fault == 'wide_input': manifest['input_schema']['additionalProperties'] = True
    elif fault == 'wrong_service': manifest['execution']['operation'] = 'resense_with_receipt'
    elif fault == 'side_effect': manifest['execution']['side_effect'] = False
    else: manifest['allowed_agent_roles'].append('perception')
    with pytest.raises((HTTPException, ValueError)): service.validate_manifest(manifest)


def test_unknown_original_with_state_only_reads_original_ids_and_missing_state_cannot_recreate(monkeypatch):
    s = scene(monkeypatch, 'execution_service'); b = resense_boundaries(monkeypatch, s, reuse=True)
    b.acq.status = 'unknown'
    with pytest.raises(service.PendingService): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    s.call.status = 'unknown'
    with pytest.raises(HTTPException): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    with pytest.raises(service.PendingService): service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    b.acq.status = 'succeeded'; b.acq.data['closing_observation_id'] = b.observation.id; b.plan.status = 'completed'
    assert service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)['service_receipt']['observations']
    assert len([row for row in b.calls if isinstance(row, tuple)]) == 1
    s.call.data.pop('service_state')
    with pytest.raises(HTTPException): service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert len([row for row in b.calls if isinstance(row, tuple)]) == 1


def test_first_partial_acquisition_is_not_full_plan_success_and_followups_keep_original_ids(monkeypatch):
    from generic_agents import instance_data_receipts
    s = scene(monkeypatch, 'execution_service'); b = resense_boundaries(monkeypatch, s)
    with pytest.raises(service.PendingService): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    original = copy.deepcopy(s.call.data['service_state'])
    b.acq.status = 'succeeded'; b.acq.data['closing_observation_id'] = b.observation.id
    # Only field X is received. The original plan's worker is still collecting Y.
    with pytest.raises(service.PendingService): service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    second = copy.deepcopy(b.acq); second.id = identity('followup-acquisition'); second.idempotency_key = 'original-plan-followup'
    observation = copy.deepcopy(b.observation); observation.id = identity('followup-observation')
    observation.data['acquisition_id'] = second.id; second.data['closing_observation_id'] = observation.id
    s.db.rows[(service.Acquisition, second.id)] = second; s.db.rows[(service.Observation, observation.id)] = observation
    b.plan.status = 'completed'
    b.complete_evidence['observation_evidence'].append({'observation_id': observation.id})
    def receipt_boundary(db, p, row):
        result = copy.deepcopy(b.receipt); result['data_reception']['observation_id'] = row.id; return result
    monkeypatch.setattr(instance_data_receipts, 'observation_receipt', receipt_boundary)
    output = service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert [row['acquisition_id'] for row in output['service_receipt']['followup_observations']] == [second.id]
    assert len(output['service_receipt']['plan_receipts'][0]['supporting_observations']) == 2
    assert s.call.data['service_state'] == original
    assert len([row for row in b.calls if isinstance(row, tuple)]) == 1


@pytest.mark.parametrize('engineering', [True, False])
def test_actual_sim_origin_cannot_be_added_or_removed_by_qualification_mode(monkeypatch, engineering):
    s = scene(monkeypatch, 'execution_service'); b = resense_boundaries(monkeypatch, s)
    s.binding['simulation_only'] = engineering
    b.receipt['data_reception']['simulation_origin'] = {'simulation': True, 'test_only': True, 'field_verified': False}
    with pytest.raises(service.PendingService): service.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    b.acq.status = 'succeeded'; b.acq.data['closing_observation_id'] = b.observation.id; b.plan.status = 'completed'
    if engineering:
        result = service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
        assert result['service_receipt']['simulation_only'] is True
    else:
        with pytest.raises(HTTPException): service.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert len([row for row in b.calls if isinstance(row, tuple)]) == 1


def policy_scope_scene(monkeypatch):
    from generic_agents import signal_graph_http_source_v177 as http_sources
    from generic_agents.config import settings
    p = NS(workspace_id=identity('policy-workspace'))
    source = NS(id=identity('policy-source'), content_hash='a' * 64,
        data={'specification': {'simulation_environment_id': identity('environment'),
                'http_observation_extractor_id_v177': identity('policy-extractor')},
            'data_policy': {'simulation': True, 'test_only': True, 'deployment': 'local', 'cloud_allowed': False},
            'execution': {'adapter': 'http', 'endpoint': 'http://127.0.0.1:9777/acquisitions'}})
    extractor = NS(id=identity('policy-extractor'), content_hash='b' * 64)
    task = NS(data={'instance_id': identity('policy-instance'), 'instance_release_id': identity('policy-release')})
    context = {'binding': {'simulation_only': True}, 'task': task, 'release': NS(id=task.data['instance_release_id'])}
    policy = NS(data={'source_ids': [source.id]}); request = {'entity_id': identity('policy-entity')}
    contract = {'scope': {'instance_id': task.data['instance_id'], 'release_id': task.data['instance_release_id'],
        'environment_id': identity('environment')}, 'environment_hash': 'c' * 64, 'configuration_hash': 'd' * 64}
    calls = []
    monkeypatch.setattr(settings(), 'development_connector_urls', source.data['execution']['endpoint'])
    monkeypatch.setattr(service, 'published', lambda db, current, key, kind: source if kind == 'source' else extractor)
    monkeypatch.setattr(service, 'scoped', lambda *args: NS(id=task.data['instance_id']))
    monkeypatch.setattr(http_sources, 'configured', lambda current: bool(current.data['specification'].get('simulation_environment_id')))
    def prepare_boundary(db, current, instance, release, actual_source, actual_extractor, entities):
        calls.append((instance.id, release.id, actual_source.id, actual_extractor.id, entities))
        return copy.deepcopy(contract)
    monkeypatch.setattr(http_sources, 'prepare', prepare_boundary)
    return NS(p=p, source=source, extractor=extractor, context=context, policy=policy,
        request=request, contract=contract, calls=calls)


def test_engineering_pre_propose_requires_current_real_scope_contract_and_server_local_endpoint(monkeypatch):
    s = policy_scope_scene(monkeypatch)
    pins = service._policy_sources(object(), s.p, s.context, s.policy, s.request)
    assert s.calls == [(s.context['task'].data['instance_id'], s.context['release'].id,
        s.source.id, s.extractor.id, [s.request['entity_id']])]
    assert pins[0]['contract_hash'] == digest(s.contract)
    assert pins[0]['development_endpoint_hash'] == digest(s.source.data['execution']['endpoint'])
    assert pins[0]['environment_id'] == s.contract['scope']['environment_id']


@pytest.mark.parametrize('drift', ['missing', 'hash', 'version', 'schema', None])
def test_nested_managed_source_requires_exact_extractor_on_current_business(monkeypatch, drift):
    from generic_agents.business_flow_nested_adoption_v195 import BINDING
    from generic_agents.device_gateway_sources_v270 import BINDING_KEY

    s = policy_scope_scene(monkeypatch)
    s.source.data['specification'][BINDING_KEY] = {}
    s.extractor.release_version = '1.0.0'
    s.extractor.data = {'output_schema': {'type': 'object'}}
    pin = {'capability_hash': s.extractor.content_hash,
           'release_version': s.extractor.release_version,
           'output_schema': s.extractor.data['output_schema']}
    if drift == 'hash':
        pin['capability_hash'] = 'a' * 64
    if drift == 'version':
        pin['release_version'] = '2.0.0'
    if drift == 'schema':
        pin['output_schema'] = {'type': 'string'}
    s.context['binding'].update(schema=BINDING,
        observation_extractor_pins={} if drift == 'missing' else {s.extractor.id: pin})
    if drift:
        with pytest.raises(HTTPException, match='此业务发布版本未授权'):
            service._policy_sources(object(), s.p, s.context, s.policy, s.request)
        assert not s.calls
    else:
        pins = service._policy_sources(object(), s.p, s.context, s.policy, s.request)
        assert len(s.calls) == 1 and pins[0]['extractor_id'] == s.extractor.id


@pytest.mark.parametrize('fault', ['production_source', 'cloud_source', 'no_server_allowlist', 'remote_endpoint',
    'query_endpoint', 'wrong_release', 'revoked_environment', 'author_using_sim'])
def test_dynamic_source_cannot_send_engineering_work_to_a_production_or_unbound_connector(monkeypatch, fault):
    from generic_agents.config import settings
    from generic_agents import signal_graph_http_source_v177 as http_sources
    s = policy_scope_scene(monkeypatch)
    if fault == 'production_source': s.source.data['data_policy']['test_only'] = False
    elif fault == 'cloud_source': s.source.data['data_policy']['cloud_allowed'] = True
    elif fault == 'no_server_allowlist': monkeypatch.setattr(settings(), 'development_connector_urls', '')
    elif fault == 'remote_endpoint':
        s.source.data['execution']['endpoint'] = 'https://production.invalid/acquisitions'
        monkeypatch.setattr(settings(), 'development_connector_urls', s.source.data['execution']['endpoint'])
    elif fault == 'query_endpoint':
        s.source.data['execution']['endpoint'] += '?mode=simulate'
        monkeypatch.setattr(settings(), 'development_connector_urls', s.source.data['execution']['endpoint'])
    elif fault == 'wrong_release': s.contract['scope']['release_id'] = identity('foreign-release')
    elif fault == 'revoked_environment':
        def revoked(*args): raise HTTPException(409, 'environment audit or active release revoked')
        monkeypatch.setattr(http_sources, 'prepare', revoked)
    else: s.context['binding']['simulation_only'] = False
    with pytest.raises(HTTPException): service._policy_sources(object(), s.p, s.context, s.policy, s.request)
    if fault not in {'wrong_release', 'revoked_environment'}:
        assert s.calls == []
