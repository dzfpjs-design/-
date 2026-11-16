"""Actual V201 schemas/outbox/receipts with explicit authority and transport IO.

No real adopted/175 Task, HTTP request, paid model, PG constraint or physical
action is proven here. Root runs independent actual registry/worker/PG checks.
"""
import base64
import copy
import hashlib
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import models as m
from generic_agents import workflow_registered_tool_plan_v201 as subject
from generic_agents import gateway, connectors, goal_queries
from generic_agents.contracts import digest
from generic_agents.leases import LeaseLost
from generic_agents.security import Principal
from tests.test_business_reasoning_v197 import manifest as base_manifest, scene as reasoning_scene
from tests.test_workflow_monitor_schedule_v200 import IO as PreviousIO, AT, ident


class IO(PreviousIO):
    def __init__(self):
        super().__init__(); self.info = {}; self.commits = []; self.on_commit = None
    def add(self, row):
        assert type(row) in {subject.Plan, subject.Item, m.Audit}
        self.put(row)
    def _matches(self, row, clause):
        if hasattr(clause, 'left'):
            left, right = clause.left, clause.right
            if hasattr(left, 'right') and getattr(left.right, 'value', None) == 'parent_invocation_id':
                return row.data.get('parent_invocation_id') == right.value
            if getattr(left, 'key', None) in {'parent_invocation_id', 'plan_id'}:
                return getattr(row, left.key) == right.value
        return super()._matches(row, clause)
    def commit(self):
        self.commits.append({kind.__name__: {key: copy.deepcopy(row.data)
            for key, row in rows.items() if hasattr(row, 'data')} for kind, rows in self.rows.items()})
        if self.on_commit: self.on_commit()


def tool_manifest(name):
    return {**base_manifest(), 'kind': 'tool', 'namespace': 'registered_tool_test', 'name': name,
        'input_schema': {'type': 'object', 'additionalProperties': False, 'required': ['level', 'task_id', 'request_key'],
            'properties': {'level': {'type': 'string'}, 'task_id': {'type': 'string'}, 'request_key': {'type': 'string'}}},
        'output_schema': {'type': 'object', 'additionalProperties': False, 'required': ['result'],
            'properties': {'result': {'type': 'string'}}},
        'execution': {'adapter': 'http', 'endpoint': 'https://synthetic.invalid/' + name,
            'receipt_endpoint': 'https://synthetic.invalid/' + name + '/receipts',
            'receipt_protocol': subject.RECEIPT, 'approval_policy': 'admin', 'side_effect': True,
            'dispatch_scope': 'registered_tool_plan_v201',
            'input_bindings': {'task_id': 'task_id', 'request_key': 'request_key'}},
        'data_policy': {'cloud_allowed': False, 'external_allowed': True},
        'budget': {'max_attempts': 1, 'max_concurrency': 1}, 'dependencies': [],
        'specification': {'simulation_only': True}}


def wrapper_manifest(tools):
    return {**base_manifest(), 'kind': 'tool', 'namespace': 'registered_tool_test', 'name': 'execute_tool_plan',
        'input_schema': subject.input_schema(), 'output_schema': subject.output_schema(),
        'execution': {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': subject.OP, 'side_effect': True}, 'data_policy': {'cloud_allowed': False},
        'budget': {'max_attempts': 1}, 'dependencies': sorted(tool.id for tool in tools),
        'specification': {'schema': subject.CONTRACT, 'tool_pins': [{'capability_id': tool.id,
            'capability_hash': tool.content_hash, 'release_version': tool.release_version} for tool in tools]}}


def cap(data, name, p):
    return m.Capability(id=ident('v201-' + name), workspace_id=p.workspace_id, kind='tool',
        namespace=data['namespace'], name=data['name'], release_version='1.0.0', status='published',
        data=data, content_hash=digest(data), validated_hash=digest(data))


@pytest.fixture
def scene(monkeypatch):
    s = NS(db=IO(), posts=[], gets=[], post_inputs={}, receipt_status='pending', invalid_json=False,
        absent_receipt=False, sync_output=None, receipt_output=None, authority_error=None, extra_calls=0,
        crash_before_gateway=False, fail_capture=False)
    s.p = Principal(ident('v201-actor'), ident('v201-workspace'), 'admin', 'explicit-synthetic-source-seam')
    s.tools = [cap(tool_manifest(name), name, s.p) for name in ('focus', 'patrol')]
    s.cap = cap(wrapper_manifest(s.tools), 'wrapper', s.p)
    original = reasoning_scene()
    for candidate in original.inputs['candidates']: candidate['context'] = None
    result = subject.reasoning.check_result({'inputs': original.inputs}, original.output)
    def step(tool, level): return {'capability_id': tool.id, 'capability_hash': tool.content_hash,
        'agent_role': 'business', 'arguments': {'level': level}, 'allow_external_inputs': True}
    s.inputs = {'reasoning_result': result, 'candidate_plans': [
        {'candidate_ref': 'proposal_a', 'steps': [step(s.tools[0], '关注A'), step(s.tools[1], '巡护A')]},
        {'candidate_ref': 'proposal_b', 'steps': [step(s.tools[1], '巡护B')]}]}
    s.producer = {'job_id': ident('v201-original-producer'), 'invocation_id': ident('v201-original-model-call'),
        'actor_id': s.p.user_id, 'request_key_hash': 'b' * 64, 'candidates': copy.deepcopy(original.inputs['candidates']),
        'output_hash': digest(result)}
    s.task = m.Task(id=ident('v201-task'), workspace_id=s.p.workspace_id, owner_id=s.p.user_id,
        package_id=ident('v201-package'), title='合成已限定工具方案的当前任务', status='workflow_running',
        data={'entity_ids': [ident('v201-entity')], 'goal_spec': {'max_tool_calls': 20}})
    s.key = 'graph:' + ident('v201-parent-job')
    s.job = m.Job(id=ident('v201-parent-job'), workspace_id=s.p.workspace_id,
        kind='execute_full_semantic_graph_node', idempotency_key=s.key, status='running', attempts=1,
        data={'task_id': s.task.id, 'actor_id': s.p.user_id, 'activation_id': 'source-action',
            'graph_hash': 'a' * 64, 'cancel_epoch': 0, 'input_hash': digest(s.inputs),
            'inputs': copy.deepcopy(s.inputs), 'invocation_key': s.key})
    s.token = str(uuid4()); s.db.info['worker_lease'] = {'job_id': s.job.id, 'lease_token': s.token}
    node = {'execution': {'capability_id': s.cap.id, 'capability_hash': s.cap.content_hash}}
    s.approval = subject.service.make_approval(s.task, s.job, node, s.inputs)
    s.parent = m.Invocation(id=ident('v201-parent-call'), workspace_id=s.p.workspace_id, capability_id=s.cap.id,
        idempotency_key=s.key, status='running', data={'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs),
            'role': 'business', 'side_effect': True, 'job_id': s.job.id, 'lease_token': s.token,
            'capability_snapshot': copy.deepcopy(s.cap.data), 'capability_hash': s.cap.content_hash,
            'approval': copy.deepcopy(s.approval)})
    # Absence of invocation_id is real during the first parent gateway call.
    s.task.data = {**s.task.data, 'graph_jobs': {'source-action': {'job_id': s.job.id, 'invocation_key': s.key,
        'dispatch_ownership': {'job_id': s.job.id, 'lease_token': s.token},
        'dispatch_authorization': {'job_id': s.job.id, 'lease_token': s.token, 'actor_id': s.p.user_id,
            'epoch': 0, 'input_hash': digest(s.inputs), 'capability_hash': s.cap.content_hash}}}}
    s.context = {'task': s.task, 'job': s.job, 'capability': s.cap,
        'binding': {'binding_hash': 'c' * 64, 'simulation_only': False}}
    for row in [s.task, s.job, s.parent, s.cap, *s.tools]: s.db.put(row)
    def published(db, p, identity, *args):
        row = db.rows.get(m.Capability, {}).get(identity)
        subject.need(row is not None and row.workspace_id == p.workspace_id and row.status == 'published'
            and row.content_hash == row.validated_hash == digest(row.data))
        return row
    monkeypatch.setattr(subject, 'published', published)
    def authority(db, p, current, role, value, key, approval):
        subject.need(s.authority_error is None)
        assert current.id == s.cap.id and role == 'business' and value == s.inputs and key == s.key and approval == s.approval
        assert p.workspace_id == s.p.workspace_id and p.user_id == s.p.user_id
        return s.context
    monkeypatch.setattr(subject, 'validate_call_approval', authority)
    monkeypatch.setattr(subject.service, '_call', lambda db, p, context, key, value, approval, **kwargs: s.parent)
    s.actual_producer_wait_pin = subject.producer_wait_pin
    monkeypatch.setattr(subject, 'producer_wait_pin', lambda *args: copy.deepcopy(s.producer))
    monkeypatch.setattr(goal_queries, 'call_count', lambda db, p, task:
        s.extra_calls + len([row for row in s.db.rows[m.Invocation].values() if row.workspace_id == p.workspace_id]))
    def endpoint(execution):
        assert execution['endpoint'].startswith('https://synthetic.invalid/')
        return execution['endpoint']
    monkeypatch.setattr(gateway, 'validate_endpoint', endpoint)
    def invoke(db, p, target_id, role, value, key, *, approval):
        if s.crash_before_gateway: raise RuntimeError('synthetic crash before invocation creation')
        target = published(db, p, target_id)
        # Real V201 validation runs before the synthetic gateway creates a call.
        subject.validate_child_approval(db, p, target, role, value, key, approval)
        child = m.Invocation(workspace_id=p.workspace_id, capability_id=target_id, idempotency_key=key,
            status='running', data={'inputs': copy.deepcopy(value), 'input_hash': digest(value), 'role': role,
                'capability_hash': target.content_hash, 'capability_snapshot': copy.deepcopy(target.data),
                'side_effect': True, 'job_id': s.job.id, 'lease_token': s.token, 'approval': copy.deepcopy(approval)})
        db.put(child); db.commit()
        subject.before_child_dispatch(db, p, target, role, value, key, approval); db.commit()
        # At the actual POST boundary both original intent and authorization
        # are already in a committed snapshot, not merely in a local dict.
        last = db.commits[-1]['WorkflowRegisteredToolPlanItemV201']
        assert any(item['status'] == 'dispatching' and item['invocation_id'] == child.id
            and item['dispatch_authorized'] is True for item in last.values())
        s.posts.append(key); s.post_inputs[key] = copy.deepcopy(value)
        child.status = 'waiting_receipt' if s.sync_output is None else 'succeeded'
        if s.sync_output is not None: child.data = {**child.data, 'output': copy.deepcopy(s.sync_output)}
        db.put(m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action='capability.invoke',
            object_id=child.id, data={'status': child.status})); db.commit()
        return child
    monkeypatch.setattr(gateway, 'invoke', invoke)
    def query(execution, key, *, on_response):
        s.gets.append(key)
        if s.absent_receipt: raise HTTPException(404, 'original receipt not found')
        item = next(row for row in s.db.rows[subject.Item].values() if row.request_key == key)
        value = {'schema': subject.RECEIPT, 'idempotency_key': key, 'input_hash': item.input_hash,
            'status': s.receipt_status, 'terminal': s.receipt_status in {'succeeded', 'failed', 'not_executed'}}
        if s.receipt_status == 'succeeded': value['output'] = copy.deepcopy(s.receipt_output or {'result': '原号结果 ' + str(item.ordinal)})
        if s.receipt_status == 'failed': value['error'] = '真实供应商明确失败'
        raw = b'{invalid original json' if s.invalid_json else json.dumps(value, ensure_ascii=False).encode('utf-8')
        on_response(raw, {'status_code': 200, 'content_type': 'application/json'})
        if s.fail_capture: raise ValueError('synthetic decode failure after capture')
        # Callback must have committed the exact first bytes before parse.
        last = s.db.commits[-1]['WorkflowRegisteredToolPlanItemV201'][item.id]
        assert base64.b64decode(last['receipt_checks'][-1]['raw_base64']) == raw
        return json.loads(raw)
    monkeypatch.setattr(connectors, 'query_receipt', query)
    return s


def run(s): return subject.execute(s.db, s.p, s.cap, copy.deepcopy(s.inputs), s.key, s.approval)
def recover(s): return subject.reconcile(s.db, s.p, s.cap, copy.deepcopy(s.inputs), s.key, s.approval, s.parent)


def simulation_targets(s, values):
    """Freeze a synthetic newly registered version, not mutate an old pin."""
    for tool, value in zip(s.tools, values):
        data = copy.deepcopy(tool.data)
        if value == 'missing': data['specification'].pop('simulation_only', None)
        else: data['specification']['simulation_only'] = value
        tool.data = data; tool.content_hash = tool.validated_hash = digest(data)
        for candidate in s.inputs['candidate_plans']:
            for step in candidate['steps']:
                if step['capability_id'] == tool.id: step['capability_hash'] = tool.content_hash
    s.cap.data = wrapper_manifest(s.tools); s.cap.content_hash = s.cap.validated_hash = digest(s.cap.data)
    s.approval.update(capability_hash=s.cap.content_hash, input_hash=digest(s.inputs))
    s.job.data = {**s.job.data, 'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs)}
    s.parent.data = {**s.parent.data, 'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs),
        'capability_hash': s.cap.content_hash, 'capability_snapshot': copy.deepcopy(s.cap.data),
        'approval': copy.deepcopy(s.approval)}
    transport = copy.deepcopy(s.task.data['graph_jobs'])
    transport['source-action']['dispatch_authorization'].update(input_hash=digest(s.inputs), capability_hash=s.cap.content_hash)
    s.task.data = {**s.task.data, 'graph_jobs': transport}


def original_pre_gate_prepared(s, monkeypatch):
    """Seed actual full original outbox/Audits under the known pre-fix behavior.

    This fixture bypasses only the newly added SIM target restriction while
    constructing the historical record. Recovery executes every current gate.
    No child exists and no gateway transport is used to seed the original.
    """
    actual = subject._target
    def old_target(db, p, cap, step, *, simulation_only):
        return actual(db, p, cap, step, simulation_only=False)
    with monkeypatch.context() as old:
        old.setattr(subject, '_target', old_target)
        return subject._create(s.db, s.p, s.context, s.parent, copy.deepcopy(s.inputs), s.key, s.approval)


@pytest.mark.parametrize('invalid', [False, 'missing', 1])
def test_engineering_scope_rejects_non_sim_target_even_in_unselected_candidate_before_any_child(scene, invalid):
    s = scene; s.context['binding']['simulation_only'] = True
    # Candidate A uses only SIM target 0; target 1 occurs only in unselected B.
    s.inputs['candidate_plans'][0]['steps'] = s.inputs['candidate_plans'][0]['steps'][:1]
    simulation_targets(s, [True, invalid])
    with pytest.raises(HTTPException) as caught: run(s)
    assert '工程模拟流程只能调用' in caught.value.detail
    assert not s.posts and not s.gets and len(s.db.rows[m.Invocation]) == 1
    assert not s.db.rows.get(subject.Plan) and not s.db.rows.get(subject.Item)


def test_engineering_all_actual_targets_explicit_true_can_complete_same_original_plan(scene):
    s = scene; s.context['binding']['simulation_only'] = True
    with pytest.raises(subject.service.PendingService): run(s)
    s.receipt_status = 'succeeded'
    result = recover(s)
    assert result['plan_receipt']['simulation_only'] is True and len(s.posts) == 2
    assert all(s.db.rows[m.Capability][row.capability_id].data['specification']['simulation_only'] is True
        for row in subject._inventory(s.db, s.p, s.parent.id)[1])


def test_nonengineering_approved_scope_keeps_explicit_physical_targets_distinct_from_sim(scene):
    s = scene; assert s.context['binding']['simulation_only'] is False
    simulation_targets(s, [False, False])
    s.receipt_status = 'succeeded'
    result = run(s)
    assert len(s.posts) == 2 and result['plan_receipt']['simulation_only'] is False
    assert result['plan_receipt']['physical_state_verified'] is False


@pytest.mark.parametrize('invalid', [False, 'missing', 1])
def test_cold_prepared_mixed_plan_cannot_dispatch_sim_prefix_before_rejecting_unsafe_target(scene, monkeypatch, invalid):
    s = scene; s.context['binding']['simulation_only'] = True
    simulation_targets(s, [True, invalid])
    plan = original_pre_gate_prepared(s, monkeypatch)
    before = copy.deepcopy(plan.data)
    assert plan.data['source']['simulation_only'] is True  # Old OR label is not permission.
    assert [item.status for item in subject._inventory(s.db, s.p, s.parent.id)[1]] == ['prepared', 'prepared']
    with pytest.raises(HTTPException): recover(s)
    assert len(s.db.rows[m.Invocation]) == 1 and not s.posts and not s.gets
    assert plan.data == before and all(item.status == 'prepared'
        for item in subject._inventory(s.db, s.p, s.parent.id)[1])


@pytest.mark.parametrize('entrypoint', ['validate_child_approval', 'before_child_dispatch'])
def test_engineering_child_admission_and_final_dispatch_recheck_actual_parent_scope_not_or_label(scene, monkeypatch, entrypoint):
    s = scene; s.context['binding']['simulation_only'] = True
    simulation_targets(s, [False, True])
    plan = original_pre_gate_prepared(s, monkeypatch)
    item = subject._inventory(s.db, s.p, s.parent.id)[1][0]
    subject._write_item(s.db, s.p, plan, item, status='dispatching',
        dispatch_owner={'job_id': s.job.id, 'lease_token': s.token})
    blueprint = plan.data['steps'][0]
    approval = subject.make_child_approval(plan, item)
    with pytest.raises(HTTPException): getattr(subject, entrypoint)(s.db, s.p, s.tools[0],
        blueprint['agent_role'], blueprint['inputs'], blueprint['request_key'], approval)
    assert len(s.db.rows[m.Invocation]) == 1 and not s.posts and not s.gets
    assert item.invocation_id is None and item.data['dispatch_authorized'] is False


def test_registration_and_inputs_are_closed_fixed_dependencies(scene):
    s = scene
    assert subject.validate_manifest(s.cap.data) == s.cap.data
    assert subject._inputs(s.inputs) == s.inputs
    for mutate in ('operation', 'pin', 'side_effect'):
        value = copy.deepcopy(s.cap.data)
        if mutate == 'operation': value['execution']['operation'] = 'resense_with_receipt'
        if mutate == 'pin': value['specification']['tool_pins'][0]['capability_hash'] = 'broken'
        if mutate == 'side_effect': value['execution']['side_effect'] = False
        with pytest.raises(HTTPException): subject.validate_manifest(value)
    value = copy.deepcopy(s.inputs); value['candidate_plans'][0]['steps'][0]['allow_external_inputs'] = False
    with pytest.raises(HTTPException): subject._inputs(value)


def test_first_post_durable_outbox_pending_then_original_receipt_runs_only_unsubmitted_next_step(scene):
    s = scene
    with pytest.raises(subject.service.PendingService): run(s)
    assert len(s.posts) == len(s.gets) == 1
    plan, items = subject._inventory(s.db, s.p, s.parent.id)
    assert [item.status for item in items] == ['waiting', 'prepared']
    first_key = items[0].request_key
    assert s.post_inputs[first_key]['task_id'] == s.task.id and s.post_inputs[first_key]['request_key'] == first_key
    assert subject.retained_marker_shape(s.parent) is True
    with pytest.raises(subject.service.PendingService): recover(s)
    assert s.posts == [first_key] and s.gets == [first_key, first_key]
    s.receipt_status = 'succeeded'
    result = recover(s)
    assert len(s.posts) == 2 and s.posts[0] == first_key and len(set(s.posts)) == 2
    assert result['plan_receipt']['simulation_only'] is True
    assert result['plan_receipt']['physical_state_verified'] is result['plan_receipt']['independent_acceptance'] is False
    original_posts = list(s.posts); original_gets = list(s.gets)
    assert recover(s) == result and s.posts == original_posts and s.gets == original_gets
    for item in subject._inventory(s.db, s.p, s.parent.id)[1]:
        child = s.db.rows[m.Invocation][item.invocation_id]
        settlement = child.data[subject.KEY + '/settled']
        raw = base64.b64decode(settlement['raw_receipt']['raw_base64'], validate=True)
        assert hashlib.sha256(raw).hexdigest() == settlement['raw_receipt']['raw_sha256']
        assert settlement['original_gateway_outcome']['status'] == 'waiting_receipt'
        assert child.status == 'succeeded'
        original = [entry for entry in s.db.rows[m.Audit].values() if entry.action == 'capability.invoke' and entry.object_id == child.id]
        assert len(original) == 1 and original[0].data == {'status': 'waiting_receipt'}


@pytest.mark.parametrize('state', ['needs_information', 'conflicting_evidence'])
def test_uncertain_model_result_never_creates_plan_or_tool_call(scene, state):
    s = scene; s.inputs['reasoning_result']['review_state'] = state
    with pytest.raises(HTTPException): run(s)
    assert not s.db.rows.get(subject.Plan) and not s.posts


@pytest.mark.parametrize('alternative_assessment', ['insufficient', 'supported'])
def test_only_unselected_information_gap_does_not_veto_supported_selected_plan(scene, alternative_assessment):
    s = scene; original = reasoning_scene()
    for candidate in original.inputs['candidates']: candidate['context'] = None
    original.output['candidate_assessments'][1].update(assessment=alternative_assessment,
        missing_information=['此未选方案仍缺少足够覆盖资料'])
    result = subject.reasoning.check_result({'inputs': original.inputs}, original.output)
    assert result['review_state'] == 'needs_information'
    s.inputs['reasoning_result'] = copy.deepcopy(result); s.producer['output_hash'] = digest(result)
    simulation_targets(s, [True, True])  # Re-freeze only this synthetic new input.
    s.receipt_status = 'succeeded'; output = run(s)
    assert output['plan_receipt']['candidate_ref'] == 'proposal_a' and len(s.posts) == 2
    plan, _items = subject._inventory(s.db, s.p, s.parent.id)
    assert plan.data['inputs']['reasoning_result'] == result == s.inputs['reasoning_result']
    assert result['review_state'] == 'needs_information'
    assert result['permissions_verified'] is result['can_dispatch'] is False


@pytest.mark.parametrize('fault', ['selected_gap', 'selected_insufficient', 'selected_conflicting',
    'unselected_conflicting', 'unselected_conflict_with_gap', 'selected_no_evidence',
    'recommendation_no_evidence', 'recommendation_mismatch', 'no_recommendation',
    'duplicate_selected', 'inconsistent_ready_state', 'inconsistent_conflict_state'])
def test_selected_option_gate_rejects_incomplete_conflicting_or_inconsistent_results(scene, fault):
    result = copy.deepcopy(scene.inputs['reasoning_result'])
    selected, other = result['analysis']['candidate_assessments']; recommendation = result['analysis']['recommendation']
    if fault == 'selected_gap':
        selected['missing_information'] = ['所选方案仍缺执行依据']; result['review_state'] = 'needs_information'
    elif fault == 'selected_insufficient':
        selected.update(assessment='insufficient', missing_information=['所选方案不足']); result['review_state'] = 'needs_information'
    elif fault == 'selected_conflicting':
        selected['assessment'] = 'conflicting'; result['review_state'] = 'conflicting_evidence'
    elif fault in {'unselected_conflicting', 'unselected_conflict_with_gap'}:
        other['assessment'] = 'conflicting'; result['review_state'] = 'conflicting_evidence'
        if fault == 'unselected_conflict_with_gap':
            other['missing_information'] = ['冲突且缺资料']; result['review_state'] = 'needs_information'
    elif fault == 'selected_no_evidence': selected['evidence_refs'] = []
    elif fault == 'recommendation_no_evidence': recommendation['evidence_refs'] = []
    elif fault == 'recommendation_mismatch': recommendation['candidate_ref'] = other['candidate_ref']
    elif fault == 'no_recommendation': result['recommended_candidate_ref'] = recommendation['candidate_ref'] = None
    elif fault == 'duplicate_selected': other['candidate_ref'] = selected['candidate_ref']
    elif fault == 'inconsistent_ready_state': other.update(assessment='insufficient', missing_information=['未选缺项'])
    elif fault == 'inconsistent_conflict_state': result['review_state'] = 'conflicting_evidence'
    before = copy.deepcopy(result)
    with pytest.raises(HTTPException): subject._selected_candidate(result)
    assert result == before and not scene.posts and not scene.db.rows.get(subject.Plan)


@pytest.mark.parametrize('eligible', [True, False])
def test_producer_preflight_checks_selected_option_after_original_answer_proof(scene, monkeypatch, eligible):
    from generic_agents import workflow_graph_scope, full_semantic_graph_v1, workflow_monitor_schedule_v200
    s = scene; trace = []
    if not eligible:
        s.inputs['reasoning_result']['analysis']['candidate_assessments'][0]['missing_information'] = ['所选仍缺资料']
        s.inputs['reasoning_result']['review_state'] = 'needs_information'
        simulation_targets(s, [True, True])
    monkeypatch.setattr(workflow_graph_scope, '_frozen', lambda *args:
        (s.task, None, None, {}, {}, {'id': 'source-action'}, {}))
    monkeypatch.setattr(workflow_graph_scope, '_job_matches', lambda *args: None)
    monkeypatch.setattr(full_semantic_graph_v1, 'ability_inputs', lambda *args: copy.deepcopy(s.inputs))
    def original_proof(*args):
        trace.append('original_paid_answer_proven'); return copy.deepcopy(s.producer)
    monkeypatch.setattr(workflow_monitor_schedule_v200, '_producer', original_proof)
    if eligible:
        assert s.actual_producer_wait_pin(s.db, s.p, s.task, s.job, s.inputs) == s.producer
    else:
        with pytest.raises(HTTPException): s.actual_producer_wait_pin(s.db, s.p, s.task, s.job, s.inputs)
    assert trace == ['original_paid_answer_proven']
    assert not s.posts and not s.db.rows.get(subject.Plan) and len(s.db.rows[m.Invocation]) == 1


def test_selected_candidate_cannot_bypass_failed_original_producer_proof(scene, monkeypatch):
    s = scene
    def rejected(*args): raise HTTPException(409, '原始已付费答复、证据或审计无法核对')
    monkeypatch.setattr(subject, 'producer_wait_pin', rejected)
    with pytest.raises(HTTPException): run(s)
    assert not s.posts and not s.db.rows.get(subject.Plan)


def test_candidate_order_is_actual_producer_order_not_matching_display_name(scene):
    s = scene; s.producer['candidates'].reverse()
    with pytest.raises(HTTPException): run(s)
    assert not s.posts and not s.db.rows.get(subject.Plan)


@pytest.mark.parametrize('bad', ['mcp', 'readonly', 'no_external', 'missing_receipt', 'missing_scope', 'mixed_protocol', 'unsupported_binding'])
def test_target_registration_drift_or_unsupported_transport_blocks_before_any_post(scene, bad):
    s = scene; tool = s.tools[0]; data = copy.deepcopy(tool.data)
    if bad == 'mcp': data['execution']['adapter'] = 'mcp'
    if bad == 'readonly': data['execution']['side_effect'] = False
    if bad == 'no_external': data['data_policy']['external_allowed'] = False
    if bad == 'missing_receipt': data['execution'].pop('receipt_protocol')
    if bad == 'missing_scope': data['execution'].pop('dispatch_scope')
    if bad == 'mixed_protocol': data['execution']['receipt_protocol'] = 'legacy-tool-receipt-v1'
    if bad == 'unsupported_binding': data['execution']['input_bindings']['task_id'] = 'archive_records'
    # Rebind the new actual fixture version throughout the frozen plan so this
    # reaches the real registration rule, not merely the stale-hash gate.
    tool.data = data; tool.content_hash = tool.validated_hash = digest(data)
    for candidate in s.inputs['candidate_plans']:
        for step in candidate['steps']:
            if step['capability_id'] == tool.id: step['capability_hash'] = tool.content_hash
    s.cap.data = wrapper_manifest(s.tools); s.cap.content_hash = s.cap.validated_hash = digest(s.cap.data)
    s.approval['capability_hash'] = s.cap.content_hash
    s.parent.data = {**s.parent.data, 'capability_hash': s.cap.content_hash, 'capability_snapshot': copy.deepcopy(s.cap.data),
        'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs), 'approval': copy.deepcopy(s.approval)}
    with pytest.raises(HTTPException): run(s)
    assert not s.posts and not s.db.rows.get(subject.Plan)


def test_crash_after_intent_and_missing_child_never_replays_or_becomes_not_executed(scene):
    s = scene; s.crash_before_gateway = True
    with pytest.raises(RuntimeError): run(s)
    plan, items = subject._inventory(s.db, s.p, s.parent.id)
    assert items[0].status == 'dispatching' and items[0].invocation_id is None
    assert s.db.commits and len(s.db.rows[m.Invocation]) == 1 and not s.posts
    s.crash_before_gateway = False; s.absent_receipt = True
    with pytest.raises(subject.service.PendingService): recover(s)
    assert subject._inventory(s.db, s.p, s.parent.id)[1][0].status == 'unknown'
    assert len(s.db.rows[m.Invocation]) == 1 and not s.posts


def test_invalid_original_json_is_durable_before_decode_failure_and_only_get_recovers(scene):
    s = scene; s.invalid_json = True
    with pytest.raises(subject.service.PendingService): run(s)
    plan, items = subject._inventory(s.db, s.p, s.parent.id)
    raw = items[0].data['receipt_checks'][0]
    assert base64.b64decode(raw['raw_base64']) == b'{invalid original json'
    assert items[0].status == 'unknown' and len(s.posts) == 1
    s.invalid_json = False; s.receipt_status = 'succeeded'
    assert recover(s)['plan_receipt']['status'] == 'succeeded'
    assert len(s.posts) == 2 and s.gets[0] == s.gets[1]


def test_sync_success_conflicting_terminal_receipt_is_blocked_and_original_answer_kept(scene):
    s = scene; s.sync_output = {'result': '同步原答'}; s.receipt_status = 'succeeded'; s.receipt_output = {'result': '冲突终态'}
    with pytest.raises(HTTPException): run(s)
    plan, items = subject._inventory(s.db, s.p, s.parent.id)
    assert items[0].status == 'conflict' and items[1].status == 'prepared'
    child = s.db.rows[m.Invocation][items[0].invocation_id]
    assert child.data['output'] == s.sync_output and subject.KEY + '/settled' not in child.data
    assert any(entry.get('error') and '冲突' in entry['error'] for entry in items[0].data['receipt_checks'])
    posts, gets = list(s.posts), list(s.gets)
    with pytest.raises(HTTPException): recover(s)
    assert (s.posts, s.gets) == (posts, gets)


@pytest.mark.parametrize('terminal', ['failed', 'not_executed'])
def test_terminal_non_success_stops_closed_plan_without_fresh_key(scene, terminal):
    s = scene; s.receipt_status = terminal
    with pytest.raises(HTTPException): run(s)
    plan, items = subject._inventory(s.db, s.p, s.parent.id)
    assert items[0].status == terminal and items[1].status == 'prepared'
    assert len(s.posts) == 1
    with pytest.raises(HTTPException): recover(s)
    assert len(s.posts) == 1


def test_independent_create_or_item_audit_loss_prevents_rebuilding_marker(scene):
    s = scene
    with pytest.raises(subject.service.PendingService): run(s)
    original = list(s.posts)
    event = next(row for row in s.db.rows[m.Audit].values() if row.action == subject.CREATED)
    del s.db.rows[m.Audit][event.id]
    with pytest.raises(HTTPException): recover(s)
    with pytest.raises(HTTPException): run(s)
    assert s.posts == original and len(s.db.rows[subject.Plan]) == 1


def test_current_authority_and_target_publication_are_rechecked_before_next_unsubmitted_item(scene):
    s = scene
    with pytest.raises(subject.service.PendingService): run(s)
    s.receipt_status = 'succeeded'; s.tools[1].status = 'retired'
    with pytest.raises(HTTPException): recover(s)
    assert len(s.posts) == 1
    assert subject._inventory(s.db, s.p, s.parent.id)[1][0].status == 'succeeded'
    s.authority_error = 'revoked'
    before = list(s.gets)
    with pytest.raises(HTTPException): recover(s)
    assert s.gets == before and len(s.posts) == 1


def test_goal_budget_race_after_intent_rechecks_real_child_key_before_creation(scene):
    s = scene; limit = s.task.data['goal_spec']['max_tool_calls']
    def race():
        if s.db.rows.get(subject.Item) and not s.posts:
            s.extra_calls = limit - 1  # Another graph action used the remaining allowance.
    s.db.on_commit = race
    with pytest.raises(HTTPException): run(s)
    assert len(s.db.rows[m.Invocation]) == 1 and not s.posts
    assert subject._inventory(s.db, s.p, s.parent.id)[1][0].status == 'dispatching'


def test_existing_child_is_counted_once_at_exact_budget_before_http(scene):
    s = scene; s.inputs['candidate_plans'][0]['steps'] = s.inputs['candidate_plans'][0]['steps'][:1]
    s.parent.data = {**s.parent.data, 'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs)}
    s.approval['input_hash'] = digest(s.inputs); s.parent.data = {**s.parent.data, 'approval': copy.deepcopy(s.approval)}
    s.task.data = {**s.task.data, 'goal_spec': {'max_tool_calls': 2}}
    s.receipt_status = 'succeeded'
    result = run(s)
    assert len(s.posts) == 1 and len(s.db.rows[m.Invocation]) == 2
    assert result['plan_receipt']['status'] == 'succeeded'


def test_history_child_checker_does_not_call_live_and_supports_first_parent_without_result_link(scene, monkeypatch):
    s = scene
    with pytest.raises(subject.service.PendingService): run(s)
    plan, items = subject._inventory(s.db, s.p, s.parent.id)
    child = s.db.rows[m.Invocation][items[0].invocation_id]
    monkeypatch.setattr(subject, '_parent_context', lambda *args: (_ for _ in ()).throw(AssertionError('history must not execute live')))
    checked = subject.checked_child_call(s.db, s.p, s.task, child)
    assert checked['parent_invocation'].id == s.parent.id and checked['job'].id == s.job.id
    changed = copy.deepcopy(s.task.data); changed['graph_jobs']['source-action']['invocation_id'] = ident('wrong-parent')
    s.task.data = changed
    with pytest.raises(HTTPException): subject.checked_child_call(s.db, s.p, s.task, child)
    changed['graph_jobs']['source-action']['invocation_id'] = None
    s.task.data = copy.deepcopy(changed)
    with pytest.raises(HTTPException): subject.checked_child_call(s.db, s.p, s.task, child)


def test_receipt_capture_lease_loss_is_not_converted_into_recoverable_success(scene, monkeypatch):
    s = scene
    def lose(*args, **kwargs): raise LeaseLost('original lease lost')
    monkeypatch.setattr(connectors, 'query_receipt', lose)
    with pytest.raises(LeaseLost): run(s)
    assert len(s.posts) == 1 and subject._inventory(s.db, s.p, s.parent.id)[1][0].data['receipt'] is None


def test_saved_parent_history_requires_complete_actual_child_inventory_without_live_or_http(scene, monkeypatch):
    s = scene; s.receipt_status = 'succeeded'; result = run(s)
    s.parent.status = 'succeeded'; s.parent.data = {**s.parent.data, 'output': copy.deepcopy(result)}
    before = (list(s.posts), list(s.gets))
    def forbidden(*args, **kwargs): raise AssertionError('saved parent history must not execute authority or IO')
    monkeypatch.setattr(subject, '_parent_context', forbidden)
    monkeypatch.setattr(gateway, 'invoke', forbidden); monkeypatch.setattr(connectors, 'query_receipt', forbidden)
    assert subject.saved_receipt(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.parent) == result
    assert subject.saved_receipt(s.db, s.p, copy.deepcopy(s.cap.data), s.inputs, s.key, s.approval, s.parent) == result
    plan, rows = subject._inventory(s.db, s.p, s.parent.id)
    for row in rows: del s.db.rows[m.Invocation][row.invocation_id]
    with pytest.raises(HTTPException):
        subject.saved_receipt(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.parent)
    assert (s.posts, s.gets) == before


@pytest.mark.parametrize('wrong', ['key', 'input_hash', 'output', 'temporary_not_executed'])
def test_receipt_cannot_replace_original_identity_or_terminal_contract(scene, wrong):
    s = scene
    with pytest.raises(subject.service.PendingService): run(s)
    plan, rows = subject._inventory(s.db, s.p, s.parent.id); step = plan.data['steps'][0]
    receipt = {'schema': subject.RECEIPT, 'idempotency_key': step['request_key'], 'input_hash': step['input_hash'],
        'status': 'succeeded', 'terminal': True, 'output': {'result': '正确原包'}}
    if wrong == 'key': receipt['idempotency_key'] += ':new'
    if wrong == 'input_hash': receipt['input_hash'] = '0' * 64
    if wrong == 'output': receipt['output'] = {'unknown': '不能冒充登记输出'}
    if wrong == 'temporary_not_executed': receipt.update(status='not_executed', terminal=False)
    with pytest.raises(HTTPException): subject._receipt_value(receipt, step)

