"""Actual resolver/pins/receipt checks with explicit current-authority IO seams.

These synthetic graph, IAM, registry and SQL boundaries are not PostgreSQL,
source-adoption, provider or real Task proof. No device or mutation is allowed.
Root separately verifies the normal registered capability and leased worker.
"""
import copy
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from generic_agents import workflow_task_context_v196 as reader
from generic_agents import models as m
from generic_agents import native_goals, goal_queries, observation_requests, data_gaps, workflow_graph
from generic_agents import goal_conflict_observation_service_v259 as conflict
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.contracts import DataRequest, EvidenceRequirement, digest
from tests.test_report_business_flow_wire_v184 import no_external
from tests.test_full_semantic_graph_v1 import ability, base, control, edge


def identity(name): return str(uuid5(NAMESPACE_URL, 'public-task-context-v196:' + name))


def same_sql(actual, expected):
    a = actual.compile(dialect=postgresql.dialect()); b = expected.compile(dialect=postgresql.dialect())
    return str(a) == str(b) and a.params == b.params


class IO:
    def __init__(self): self.rows = {}; self.policies = []; self.events = []; self.current_actor = True; self.fail = None
    def add(self, *args): raise AssertionError('Read-only resolver cannot add data')
    def flush(self): raise AssertionError('Read-only resolver cannot flush data')
    def commit(self): raise AssertionError('Read-only resolver cannot commit or dispatch')
    def scalar(self, statement):
        if self.fail: raise self.fail
        s = self.scene; p = s.p
        if same_sql(statement, select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
                m.Invocation.idempotency_key == s.key).execution_options(populate_existing=True)):
            return s.call
        expected = select(m.DataRun).where(m.DataRun.workspace_id == p.workspace_id,
            m.DataRun.id == s.run.id).execution_options(populate_existing=True)
        if same_sql(statement, expected) and statement.get_execution_options() == expected.get_execution_options():
            return self.rows.get((m.DataRun, s.run.id))
        expected = select(m.User.id).join(m.Membership, m.Membership.user_id == m.User.id).where(
            m.User.id == s.producer_actor, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
            m.Membership.role.in_(['admin', 'operator']))
        assert same_sql(statement, expected), 'Unexpected authority SQL'
        return s.producer_actor if self.current_actor else None
    def scalars(self, statement):
        if self.fail: raise self.fail
        s = self.scene
        if same_sql(statement, select(m.MonitorPolicy).where(m.MonitorPolicy.workspace_id == s.p.workspace_id,
                m.MonitorPolicy.status == 'active')):
            return [row for row in self.policies if row.workspace_id == s.p.workspace_id and row.status == 'active']
        expected = select(conflict.Command).where(conflict.Command.workspace_id == s.p.workspace_id,
            conflict.Command.branch_query_id == s.run.id, conflict.Command.outcome == 'accepted').limit(2)
        if same_sql(statement, expected):
            return []  # These original resolver fixtures have no V259 branch.
        for action, object_id in ((reader.PRODUCED, s.call.id), ('workflow.graph.node_completed', s.task.id)):
            expected = select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
                m.Audit.action == action, m.Audit.object_id == object_id)
            if same_sql(statement, expected):
                return [event for event in self.events if event.workspace_id == s.p.workspace_id
                    and event.action == action and event.object_id == object_id]
        raise AssertionError('Unexpected resolver inventory SQL')


@pytest.fixture
def scene(monkeypatch):
    s = NS(); s.p = NS(user_id=identity('data-actor'), workspace_id=identity('workspace'), role='operator')
    s.producer_actor = s.p.user_id; s.key = 'graph:' + identity('invocation-key'); s.db = IO(); s.db.scene = s
    s.calls = []; s.entity_id = identity('entity'); s.binding = {'schema': 'business-flow-nested-execution-binding-v195', 'binding_hash': 'b' * 64}
    s.release = NS(id=identity('release'), instance_id=identity('instance'), package_id=identity('package'),
        workspace_id=s.p.workspace_id, data={'snapshot': {'businesses': [{'id': 'business-one'}]}})
    spec = {'entity_ids': [s.entity_id], 'required_fields': ['temperature_c'], 'observation_mode': 'allow_new',
        'deadline': None, 'max_tool_calls': 12}
    window = {'mode': 'latest', 'reference_at': '2026-01-01T00:00:00+00:00'}
    s.task = NS(id=identity('task'), workspace_id=s.p.workspace_id, package_id=s.release.package_id,
        status='workflow_running', data={'instance_id': s.release.instance_id, 'instance_release_id': s.release.id,
            'business_id': 'business-one', 'entity_ids': [s.entity_id], 'goal_spec': spec, 'goal_hash': digest(spec),
            'goal_confirmed_by': identity('goal-author'), 'goal_version': 1, 'goal_window': window,
            'workflow_runtime': {'receipts': []}})
    goal = {'instance_id': s.release.instance_id, 'release_id': s.release.id, 'business_id': 'business-one',
        'entity_ids': [s.entity_id], 'goal_snapshot': native_goals.snapshot(s.task)}
    s.task.data.update(native_goal_context=goal, native_goal_context_hash=digest(goal))
    requirement = EvidenceRequirement(source='observations', minimum_verification='unverified_extraction',
        time={'mode': 'latest', 'timezone': 'Asia/Shanghai', 'max_age_seconds': 600}).model_dump()
    request = DataRequest(question='读取原任务确认的资料', entity_ids=[s.entity_id], fields=['temperature_c'],
        mode='structured', model_profile_id=None, tool_steps=[], allow_external_tools=False,
        evidence_requirement=requirement).model_dump()
    request.update(resolved_time=copy.deepcopy(window), reference_at=window['reference_at'], clarifications=[])
    result = {'status': 'insufficient', 'result': {'evidence': [], 'observation_evidence': []}}
    s.run = NS(id=identity('query'), workspace_id=s.p.workspace_id, owner_id=identity('goal-check-actor'),
        version=4, status='insufficient', data={'native_goal_task_id': s.task.id,
            'native_goal_context_hash': digest(goal), 'native_goal_request_hash': digest(request), 'request': request,
            'goal_delegation': {'task_id': s.task.id, 'goal_snapshot': native_goals.snapshot(s.task)},
            'result': result, 'native_goal_result_hash': digest(result), 'data_gap_ids': [identity('gap')]})
    s.task.data['native_goal_query_id'] = s.run.id
    s.gap = NS(id=identity('gap'), workspace_id=s.p.workspace_id, query_id=s.run.id, entity_id=s.entity_id,
        version=2, status='unresolved', data={'business_task_id': s.task.id,
            'request': copy.deepcopy(request), 'missing_fields': ['temperature_c']})
    s.source = NS(id=identity('source'), content_hash='c' * 64)
    s.policy = NS(id=identity('policy'), workspace_id=s.p.workspace_id, version=3,
        status='active', approved_by=identity('approver'), data={'approved_hash': 'a' * 64,
            'entity_ids': [s.entity_id], 'fields': ['temperature_c'], 'source_ids': [s.source.id],
            'selection_requirement': {'minimum_verification': 'unverified_extraction'},
            'max_age_seconds': 600, 'timezone': 'Asia/Shanghai'})
    s.db.policies = [s.policy]
    s.cap = NS(id=identity('context-capability'), content_hash='d' * 64, data={
        'kind': 'tool', 'allowed_agent_roles': ['data'],
        'execution': {'adapter': 'builtin', 'handler': reader.HANDLER, 'side_effect': False},
        'input_schema': reader.input_schema(), 'output_schema': reader.output_schema()})
    s.inputs = {'policy_pin': None}
    s.frozen = {'activation_id': 'activation-one', 'node_id': 'node-one', 'cancel_epoch': 0, 'graph_hash': 'e' * 64}
    s.node = {'id': 'node-one', 'actor_id': s.producer_actor, 'execution': {
        'agent_role': 'data', 'capability_id': s.cap.id, 'capability_hash': s.cap.content_hash}}
    s.job = NS(id=identity('producer-job'), workspace_id=s.p.workspace_id, kind=reader.graph_scope.KIND,
        status='running', data={'task_id': s.task.id, 'activation_id': 'activation-one', 'actor_id': s.producer_actor,
            'graph_hash': 'e' * 64, 'cancel_epoch': 0, 'activation_version': 0,
            'invocation_key': s.key, 'inputs': s.inputs, 'input_hash': digest(s.inputs)})
    s.call = NS(id=identity('call'), workspace_id=s.p.workspace_id, capability_id=s.cap.id, idempotency_key=s.key,
        status='running', data={'job_id': s.job.id, 'input_hash': digest(s.inputs), 'role': 'data', 'capability_hash': s.cap.content_hash})
    s.activation = {'id': 'activation-one', 'epoch': 0, 'status': 'ready', 'inputs_snapshot': {'arguments': s.inputs}}
    s.state = {'graph_hash': 'e' * 64}; s.business = {'id': 'business-one'}
    s.task.data['graph_jobs'] = {'activation-one': {'job_id': s.job.id, 'invocation_key': s.key,
        'cancel_epoch': 0, 'input_hash': digest(s.inputs)}}
    def scoped_boundary(db, model, identifier, p, *args):
        assert db is s.db
        row = s.db.rows.get((model, identifier))
        if row is None or getattr(row, 'workspace_id', p.workspace_id) != p.workspace_id: raise HTTPException(404, 'synthetic scoped row unavailable')
        return row
    for model, row in ((m.Task, s.task), (m.InstanceRelease, s.release), (m.DataRun, s.run),
            (m.DataGap, s.gap), (m.MonitorPolicy, s.policy), (m.Invocation, s.call), (m.Job, s.job)):
        s.db.rows[model, row.id] = row
    monkeypatch.setattr(reader, 'scoped', scoped_boundary)
    for module in (observation_requests, data_gaps, goal_queries): monkeypatch.setattr(module, 'scoped', scoped_boundary)
    def graph_boundary(db, p, key, cap):
        assert (db, key, cap) == (s.db, s.key, s.cap)
        if p.user_id != s.producer_actor: raise HTTPException(409, 'synthetic original actor mismatch')
        s.calls.append('graph_context'); return s.task, s.job, s.node, s.frozen
    monkeypatch.setattr(reader.graph_scope, 'graph_context', graph_boundary)
    def goal_boundary(db, p, task, key=None, **kwargs):
        assert db is s.db and task is s.task; s.calls.append('native_goal_guard')
        if getattr(s, 'goal_error', None): raise s.goal_error
        return task.data['native_goal_context']
    monkeypatch.setattr(native_goals, 'guard', goal_boundary)
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda release, business: copy.deepcopy(s.binding))
    monkeypatch.setattr(reader, 'published', lambda db, p, identifier, kind: s.source if identifier == s.source.id and kind == 'source' else (_ for _ in ()).throw(HTTPException(404, 'synthetic source unavailable')))
    from generic_agents import perception, sensing_selection
    def authorized(db, p, policy):
        s.calls.append(('monitor', policy.id))
        if getattr(policy, 'rejected', False): raise HTTPException(409, 'synthetic revoked monitor')
    monkeypatch.setattr(perception, 'authorized_monitor', authorized)
    monkeypatch.setattr(sensing_selection, 'validate_policy', lambda db, p, data: s.calls.append('current_policy_scope'))
    monkeypatch.setattr(perception, 'propose_acquisitions', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Resolver cannot propose')))
    monkeypatch.setattr(reader.graph_scope, '_frozen', lambda db, p, task_id, activation_id:
        (s.task, s.release, s.business, s.graph, s.state, s.activation, s.node))
    kernel_ready(s)
    return s


def kernel_ready(s):
    """Real kernel construction, port freezing and final status; IO is separate."""
    node = ability('context-node', {})
    node['actor_id'] = s.producer_actor
    node['execution'].update(capability_id=s.cap.id, capability_hash=s.cap.content_hash,
        agent_role='data', input_schema=reader.input_schema(), output_schema=reader.output_schema(),
        input_bindings={}, arguments=copy.deepcopy(s.inputs))
    graph = base([node, control('context-end', 'end')], [edge('context-node', 'context-end')],
        'context-node', ['context-node'])
    s.graph = graph; s.state = kernel.new_run(graph, {}, 10); s.node = node
    activation_id = s.state['eligible_ability_ids'][0]; s.activation = s.state['activations'][activation_id]
    assert kernel.ability_inputs(graph, s.state, activation_id) == s.inputs
    s.frozen.update(activation_id=activation_id, node_id=node['id'], graph_hash=s.state['graph_hash'])
    s.job.data.update(activation_id=activation_id, graph_hash=s.state['graph_hash'],
        inputs=copy.deepcopy(s.inputs), input_hash=digest(s.inputs))
    s.call.data['input_hash'] = digest(s.inputs)
    s.task.data['graph_runtime'] = copy.deepcopy(s.state)
    s.task.data['graph_jobs'] = {activation_id: {'job_id': s.job.id, 'invocation_key': s.key,
        'cancel_epoch': 0, 'input_hash': digest(s.inputs)}}


def run(s, pin=None):
    s.inputs['policy_pin'] = pin; kernel_ready(s)
    return reader.execute(s.db, s.p, s.cap, s.inputs, s.key)


def settled(s, output):
    s.call.status = 'succeeded'; s.call.data['output'] = copy.deepcopy(output)
    identity = s.activation['id']
    s.state = kernel.complete(s.graph, s.state, identity, output, s.job.id, s.producer_actor,
        s.activation['version'], s.state['cancel_epoch'], 11)
    s.activation = s.state['activations'][identity]
    assert s.activation['status'] == 'completed' and s.state['status'] == 'succeeded'
    assert kernel.check_run(s.graph, copy.deepcopy(s.state)) == s.state
    s.task.data['graph_runtime'] = copy.deepcopy(s.state); s.job.status = 'succeeded'
    s.db.events = [NS(workspace_id=s.p.workspace_id, actor_id=s.producer_actor, action=reader.PRODUCED,
        object_id=s.call.id, data=reader.receipt_audit_data(s.call, s.job, output)),
        NS(workspace_id=s.p.workspace_id, actor_id=s.producer_actor, action='workflow.graph.node_completed',
            object_id=s.task.id, data={'activation_id': identity, 'invocation_id': s.call.id})]
    return output


def test_null_pin_lists_complete_real_context_without_choosing_or_mutating(scene):
    s = scene; before = copy.deepcopy((s.task.data, s.run.data, s.gap.data, s.policy.data))
    result = run(s)
    assert result['status'] == 'needs_selection' and result['resense_context'] is None and len(result['candidates']) == 1
    context = result['candidates'][0]
    assert context['scope']['task_id'] == s.task.id and context['query']['query_id'] == s.run.id
    assert context['gap']['gap_id'] == s.gap.id and context['policy']['policy_id'] == s.policy.id
    assert context['gap']['observation_request']['goal_delegation']['task_id'] == s.task.id
    assert context['producer']['invocation_id'] == s.call.id and context['producer']['actor_id'] == s.producer_actor
    assert context['policy']['source_pins'] == [{'source_id': s.source.id, 'source_hash': s.source.content_hash}]
    assert all(result[key] is False and context[key] is False for key in reader.FLAGS)
    assert before == (s.task.data, s.run.data, s.gap.data, s.policy.data)
    assert 'endpoint' not in str(result) and 'headers' not in str(result)


def test_explicit_current_pin_and_one_gap_only_are_ready_and_original_candidates_are_selectable(scene):
    s = scene; result = run(s, {'policy_id': s.policy.id, 'approved_hash': s.policy.data['approved_hash']})
    assert result['status'] == 'ready' and result['resense_context'] == result['candidates'][0]
    settled(s, result)
    # A different current consumer actor is legitimate; it is not the producer.
    s.p.user_id = identity('business-consumer')
    assert reader.checked_context(s.db, s.p, result['resense_context'], task=s.task) == result['resense_context']


def test_null_selection_candidate_is_an_original_receipt_not_a_forged_ready_flag(scene):
    s = scene; result = settled(s, run(s)); context = result['candidates'][0]
    assert reader.checked_context(s.db, s.p, context, task=s.task) == context
    context['policy']['approved_hash'] = 'f' * 64
    context['context_hash'] = digest({k: v for k, v in context.items() if k != 'context_hash'})
    with pytest.raises(HTTPException): reader.checked_context(s.db, s.p, context, task=s.task)


def test_multiple_gaps_never_choose_the_first_even_with_an_explicit_policy(scene):
    s = scene; other = copy.deepcopy(s.gap); other.id = identity('gap-two')
    s.db.rows[m.DataGap, other.id] = other; s.run.data['data_gap_ids'].append(other.id)
    result = run(s, {'policy_id': s.policy.id, 'approved_hash': s.policy.data['approved_hash']})
    assert result['status'] == 'needs_selection' and result['resense_context'] is None
    assert {row['gap']['gap_id'] for row in result['candidates']} == {s.gap.id, other.id}


@pytest.mark.parametrize('status', ['resolved', 'cancelled', 'needs_review', 'pending_delivery'])
def test_terminal_conflicted_or_not_received_gaps_are_not_ready(scene, status):
    s = scene; s.gap.status = status
    result = run(s, {'policy_id': s.policy.id, 'approved_hash': s.policy.data['approved_hash']})
    assert result['status'] == 'no_gap' and result['resense_context'] is None and result['candidates'] == []


@pytest.mark.parametrize('fault', ['task_id_input', 'pin_extra', 'bool_pin', 'wrong_hash', 'no_goal', 'no_run', 'existing_only'])
def test_closed_input_and_nonready_states_never_fabricate_context(scene, fault):
    s = scene
    if fault == 'task_id_input': s.inputs['task_id'] = s.task.id
    elif fault == 'pin_extra': s.inputs['policy_pin'] = {'policy_id': s.policy.id, 'approved_hash': 'a' * 64, 'gap_id': s.gap.id}
    elif fault == 'bool_pin': s.inputs['policy_pin'] = False
    elif fault == 'wrong_hash': s.inputs['policy_pin'] = {'policy_id': s.policy.id, 'approved_hash': 'f' * 64}
    elif fault == 'no_goal': s.task.data.pop('native_goal_context')
    elif fault == 'no_run': s.task.data['native_goal_query_id'] = None
    else:
        s.task.data['goal_spec']['observation_mode'] = 'existing_only'
        s.task.data['goal_hash'] = digest(s.task.data['goal_spec'])
        s.task.data['native_goal_context']['goal_snapshot'] = native_goals.snapshot(s.task)
        s.task.data['native_goal_context_hash'] = digest(s.task.data['native_goal_context'])
        s.run.data['native_goal_context_hash'] = s.task.data['native_goal_context_hash']
        s.run.data['goal_delegation']['goal_snapshot'] = native_goals.snapshot(s.task)
    if fault in {'task_id_input', 'pin_extra', 'bool_pin'}:
        with pytest.raises(HTTPException): reader.execute(s.db, s.p, s.cap, s.inputs, s.key)
    else:
        if fault == 'wrong_hash': kernel_ready(s)
        result = reader.execute(s.db, s.p, s.cap, s.inputs, s.key)
        assert result['resense_context'] is None and result['status'] != 'ready'


@pytest.mark.parametrize('fault', ['run_task', 'run_context', 'run_request', 'delegation', 'result', 'gap_query', 'gap_task', 'gap_field', 'duplicate_gap'])
def test_actual_original_task_query_and_gap_linkage_is_not_replaceable(scene, fault):
    s = scene
    if fault == 'run_task': s.run.data['native_goal_task_id'] = identity('other-task')
    elif fault == 'run_context': s.run.data['native_goal_context_hash'] = 'f' * 64
    elif fault == 'run_request': s.run.data['request']['fields'] = ['other_field']
    elif fault == 'delegation': s.run.data['goal_delegation']['task_id'] = identity('other-task')
    elif fault == 'result': s.run.data['result']['status'] = 'sufficient'
    elif fault == 'gap_query': s.gap.query_id = identity('other-query')
    elif fault == 'gap_task': s.gap.data['business_task_id'] = identity('other-task')
    elif fault == 'gap_field': s.gap.data['missing_fields'] = ['other_field']
    else: s.run.data['data_gap_ids'].append(s.gap.id)
    with pytest.raises(HTTPException): run(s)


def test_conflict_branch_marker_without_original_command_is_rejected_by_real_guard(scene):
    s = scene
    s.run.data[conflict.KEY] = {'command_id': identity('missing-conflict-command')}
    with pytest.raises(HTTPException) as error:
        run(s)
    assert error.value.status_code == 409 and error.value.detail == conflict.ERROR


def test_new_conflict_io_does_not_accept_unscoped_or_unbounded_queries(scene):
    s = scene
    with pytest.raises(AssertionError, match='Unexpected authority SQL'):
        s.db.scalar(select(m.DataRun).where(m.DataRun.workspace_id == s.p.workspace_id,
            m.DataRun.id == s.run.id))  # The real _row requires populate_existing.
    with pytest.raises(AssertionError, match='Unexpected resolver inventory SQL'):
        s.db.scalars(select(conflict.Command).where(conflict.Command.workspace_id == s.p.workspace_id,
            conflict.Command.branch_query_id == s.run.id, conflict.Command.outcome == 'accepted'))


def test_unrelated_revoked_policy_does_not_block_a_real_candidate_and_no_source_details_escape(scene):
    s = scene; unrelated = copy.deepcopy(s.policy); unrelated.id = identity('unrelated-policy')
    unrelated.data['entity_ids'] = [identity('other-entity')]; unrelated.rejected = True
    s.db.policies.insert(0, unrelated)
    result = run(s)
    assert len(result['candidates']) == 1 and ('monitor', unrelated.id) not in s.calls
    s.policy.rejected = True
    assert run(s)['candidates'] == []


def test_expired_goal_and_sql_failures_propagate_without_mutation_or_false_output(scene):
    s = scene; s.goal_error = HTTPException(409, 'synthetic deadline expired')
    with pytest.raises(HTTPException) as error: run(s)
    assert error.value is s.goal_error
    s.goal_error = None; s.db.fail = RuntimeError('synthetic SQL boundary failure')
    with pytest.raises(RuntimeError) as error: run(s)
    assert error.value is s.db.fail


@pytest.mark.parametrize('fault', ['missing_produced', 'missing_completed', 'duplicate_produced', 'actor', 'output_hash',
    'call_unknown', 'job_failed', 'activation', 'source', 'producer_member', 'modified_context', 'borrowed_task'])
def test_cold_producer_requires_independent_audits_original_call_and_actual_completed_activation(scene, fault):
    s = scene; result = settled(s, run(s)); context = copy.deepcopy(result['candidates'][0])
    if fault == 'missing_produced': s.db.events.pop(0)
    elif fault == 'missing_completed': s.db.events.pop()
    elif fault == 'duplicate_produced': s.db.events.append(copy.deepcopy(s.db.events[0]))
    elif fault == 'actor': s.db.events[0].actor_id = identity('other-actor')
    elif fault == 'output_hash': s.db.events[0].data['output_hash'] = 'f' * 64
    elif fault == 'call_unknown': s.call.status = 'unknown'
    elif fault == 'job_failed': s.job.status = 'failed'
    elif fault == 'activation': s.activation['status'] = 'ready'
    elif fault == 'source': s.source.content_hash = 'f' * 64
    elif fault == 'producer_member': s.db.current_actor = False
    elif fault == 'modified_context': context['gap']['version_at_read'] += 1; context['context_hash'] = digest({k: v for k, v in context.items() if k != 'context_hash'})
    else: s.task.id = identity('other-task')
    with pytest.raises(HTTPException): reader.checked_context(s.db, s.p, context, task=s.task)


def test_running_producer_with_both_success_proofs_only_waits_and_incomplete_running_does_not(scene):
    s = scene; result = settled(s, run(s)); s.job.status = 'running'; context = result['candidates'][0]
    with pytest.raises(reader.ProducerPending) as error: reader.checked_context(s.db, s.p, context, task=s.task)
    assert (error.value.job_id, error.value.invocation_id) == (s.job.id, s.call.id)
    s.db.events.pop()
    with pytest.raises(HTTPException): reader.checked_context(s.db, s.p, context, task=s.task)


def test_historical_query_versions_can_change_but_original_request_remains_exact_and_resolved_is_read_only(scene):
    s = scene; result = settled(s, run(s)); context = result['candidates'][0]
    s.run.version += 1; s.run.data['result'] = {'status': 'sufficient', 'result': {}}
    s.run.data['native_goal_result_hash'] = digest(s.run.data['result']); s.gap.status = 'resolved'
    assert reader.checked_context(s.db, s.p, context, task=s.task, dispatch=False) == context
    with pytest.raises(HTTPException): reader.checked_context(s.db, s.p, context, task=s.task, dispatch=True)


def test_bounds_reject_whole_inventory_without_truncating_and_registration_is_exact(scene):
    s = scene; original = copy.deepcopy(s.cap.data)
    assert reader.validate_manifest(original) == original
    for change in ({'adapter': 'builtin', 'handler': reader.HANDLER, 'side_effect': 0},
            {'adapter': 'http', 'handler': reader.HANDLER, 'side_effect': False}):
        bad = copy.deepcopy(original); bad['execution'] = change
        with pytest.raises(HTTPException): reader.validate_manifest(bad)
    s.run.data['data_gap_ids'] = [identity('gap-' + str(index)) for index in range(reader.MAX_GAPS + 1)]
    with pytest.raises(HTTPException): run(s)
