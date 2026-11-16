"""Real graph/gateway integration with explicit synthetic IO boundaries.

The original-answer parser/ledger are tested separately in V197. Here the real
process reducer, Invocation ownership check, gateway routing and atomic outcome
writer run against public fixtures. This is not PG, IAM or provider acceptance.
"""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import gateway, workflow_graph as graph
from generic_agents import workflow_graph_api as graph_api
from generic_agents import business_reasoning_v197 as reasoning
from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents import workflow_task_context_v196 as reader
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.contracts import digest
from generic_agents.models import Task, Job, Invocation, Audit, Memory
from tests.test_business_reasoning_v197 import scene as reasoning_scene, ident
from tests.test_full_semantic_graph_v1 import base, control, edge, guards
from tests.test_report_business_flow_wire_v184 import no_external
from tests.test_workflow_service_retry_v195 import scene as retry_io_scene


AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def denied(*args, **kwargs):
    raise AssertionError('Original-answer recovery must not dispatch, reserve or call a provider')


class GraphIO:
    def __init__(self, p, call, key):
        self.p, self.call, self.key = p, call, key
        self.added = []; self.commits = 0

    def scalar(self, statement):
        assert statement.column_descriptions[0]['entity'] is Invocation
        params = list(statement.compile().params.values())
        assert self.p.workspace_id in params and self.key in params
        return self.call

    def add(self, row):
        assert isinstance(row, (Audit, Memory)), 'Recovery cannot create a new Job or Invocation'
        self.added.append(row)

    def flush(self):
        for index, row in enumerate(self.added):
            if row.id is None: row.id = ident(300 + index)

    def commit(self): self.commits += 1


def process_scene(monkeypatch, *, status='blocked_model', capture=True, recover_error=None):
    from generic_agents import workspace_api, layered_approval_store_v175 as approvals
    from generic_agents import workflow_graph_goal_guard_v133 as goals, workflow_graph_scope, cloud_budget
    s = reasoning_scene(); checks = []
    node = {'id': 'analysis', 'kind': 'ability', 'source_step_id': 'public_analysis',
        'guards': guards(), 'actor_id': s.p.user_id, 'execution': {
            'capability_id': s.cap.id, 'capability_hash': s.cap.content_hash, 'agent_role': 'business',
            'input_bindings': {}, 'arguments': copy.deepcopy(s.inputs),
            'input_schema': reasoning.input_schema(), 'output_schema': reasoning.output_schema(),
            'adapter': reasoning.ADAPTER, 'release_version': '1.0.0', 'max_attempts': 1}}
    native = kernel.validate_graph(base([node, control('end', 'end')],
        [edge('analysis', 'end')], 'analysis', ['analysis']))
    state = kernel.new_run(native, {}, 10); identity = state['eligible_ability_ids'][0]
    activation = state['activations'][identity]
    task = Task(id=s.task.id, workspace_id=s.p.workspace_id, owner_id=s.p.user_id,
        package_id=ident(201), title='公开原业务分析恢复', status='workflow_waiting', version=3,
        created_at=AT, data={'graph_runtime': state, 'object_snapshots': [],
            'workflow_runtime': {'receipts': [], 'drafts': {}, 'data_receipt_id': None}})
    key = 'graph:' + task.id + ':' + identity + ':1'
    job = Job(id=s.job.id, workspace_id=s.p.workspace_id, kind=graph.KIND,
        idempotency_key=key, status='running', attempts=2, version=3, created_at=AT,
        lease_token=ident(202), data={'actor_id': s.p.user_id, 'task_id': task.id,
            'activation_id': identity, 'graph_hash': state['graph_hash'], 'cancel_epoch': state['cancel_epoch'],
            'activation_version': activation['version'], 'inputs': copy.deepcopy(s.inputs),
            'input_hash': digest(s.inputs), 'invocation_key': key, 'attempt': 1, 'recovery_only': True})
    original_owner = {'job_id': job.id, 'lease_token': ident(203)}
    original_transport = {'job_id': job.id, 'invocation_key': key, 'phase': 'queued', 'attempt': 1,
        'input_hash': digest(s.inputs), 'cancel_epoch': state['cancel_epoch'],
        'dispatch_committed': True, 'dispatch_ownership': original_owner,
        'dispatch_authorization': {**original_owner, 'actor_id': s.p.user_id,
            'epoch': state['cancel_epoch'], 'input_hash': digest(s.inputs),
            'capability_hash': s.cap.content_hash}}
    task.data = {**task.data, graph.TRANSPORT: {identity: original_transport}}
    call = Invocation(id=s.call.id, workspace_id=s.p.workspace_id, capability_id=s.cap.id,
        idempotency_key=key, status=status, version=1, created_at=AT,
        data={'input_hash': digest(s.inputs), 'capability_hash': s.cap.content_hash,
            'role': 'business', 'approval': None, 'side_effect': False, **original_owner,
            # This is a routing candidate; the real recover must validate it.
            reasoning.STATE_KEY: {'schema': reasoning.STATE, 'capture': {'synthetic_original': True} if capture else None}})
    db = GraphIO(s.p, call, key); release = NS(id=ident(204), package_id=task.package_id)
    business = {'id': 'public_business', 'name': '公开业务分析'}
    binding = {'schema': 'business-flow-nested-execution-binding-v195', 'graph': native}
    output = reasoning.check_result({'inputs': s.inputs}, s.output)
    ledger = NS(status='failed', charged_tokens=1234, data={'original_receipt': 'unchanged'})
    recoveries = []
    def saved(current_db, current_p, task_id, lock=False):
        assert current_db is db and current_p is s.p and task_id == task.id and lock is True
        kernel.check_run(native, task.data['graph_runtime'])
        checks.append('saved-context'); return task, release, business
    def published(current_db, current_p, capability_id, kind=None):
        assert current_db is db and current_p is s.p
        if kind == 'domain_package':
            assert capability_id == release.package_id
            return NS(data={'dependencies': [s.cap.id]})
        assert capability_id == s.cap.id; return s.cap
    def recover(current_db, current_p, cap, inputs, nonce, invocation=None):
        assert current_db is db and current_p is s.p and cap is s.cap and inputs == s.inputs
        assert nonce == key and invocation is call
        assert invocation.data[reasoning.STATE_KEY]['capture'] == {'synthetic_original': True}
        recoveries.append((call.id, job.id, nonce, copy.deepcopy(original_owner)))
        if recover_error: raise recover_error
        return copy.deepcopy(output)
    monkeypatch.setattr(workspace_api, 'saved_execution', saved)
    monkeypatch.setattr(approvals, 'guard', lambda *_args: checks.append('current-approval'))
    monkeypatch.setattr(graph, 'live', lambda *_args: checks.append('current-source'))
    monkeypatch.setattr(graph, 'published', published)
    monkeypatch.setattr(graph, 'business_binding', lambda *_args: binding)
    monkeypatch.setattr(graph, 'modality_inputs', lambda *_args: checks.append('original-inputs'))
    monkeypatch.setattr(graph, 'modality_result', lambda *_args: checks.append('original-result'))
    monkeypatch.setattr(goals, 'result_step', lambda *_args: checks.append('original-goal-result'))
    monkeypatch.setattr(goals, 'reserve', denied)
    monkeypatch.setattr(reasoning, 'recover', recover)
    monkeypatch.setattr(gateway, 'invoke', denied)
    monkeypatch.setattr(workflow_graph_scope, 'before_dispatch', denied)
    monkeypatch.setattr(transport, 'call', denied)
    monkeypatch.setattr(cloud_budget, 'reserve', denied)
    monkeypatch.setattr(cloud_budget, 'finish', denied)
    return NS(db=db, p=s.p, task=task, job=job, call=call, native=native, identity=identity,
        key=key, output=output, owner=copy.deepcopy(original_owner), transport=copy.deepcopy(original_transport),
        recoveries=recoveries, ledger=ledger, checks=checks)


def test_real_process_uses_captured_original_invocation_and_original_dispatch_owner(monkeypatch):
    s = process_scene(monkeypatch); old_job = copy.deepcopy(s.job.data)
    old_ledger = copy.deepcopy(vars(s.ledger)); old_authority = copy.deepcopy(s.transport['dispatch_authorization'])
    assert s.job.lease_token != s.owner['lease_token']
    assert graph.process(s.db, s.p, s.job) == 'succeeded'
    assert s.recoveries == [(s.call.id, s.job.id, s.key, s.owner)]
    assert s.call.status == 'succeeded' and s.call.idempotency_key == s.key and s.job.data == old_job
    assert s.call.data['output'] == s.output
    assert s.call.data['original_answer_recovery_v197']['previous_status'] == 'blocked_model'
    current = s.task.data[graph.TRANSPORT][s.identity]
    assert current['phase'] == 'succeeded' and current['job_id'] == s.job.id
    assert current['invocation_key'] == s.key and current['dispatch_ownership'] == s.owner
    assert current['dispatch_authorization'] == old_authority
    assert vars(s.ledger) == old_ledger and s.ledger.status == 'failed'
    assert s.task.status == 'workflow_completed'
    assert kernel.check_run(s.native, s.task.data['graph_runtime'])['status'] == 'succeeded'
    assert [row.action for row in s.db.added if isinstance(row, Audit)].count(
        'workflow.business_reasoning.original_answer_recovered_v197') == 1
    assert s.task.data['workflow_runtime']['receipts'][0]['invocation_id'] == s.call.id
    assert not any(isinstance(row, (Job, Invocation)) for row in s.db.added)


@pytest.mark.parametrize('error', [gateway.ModelBlocked('原回答不能核对'), HTTPException(409, '原审计不匹配')])
def test_original_recovery_failure_blocks_original_graph_without_new_call(monkeypatch, error):
    s = process_scene(monkeypatch, recover_error=error)
    old_call = copy.deepcopy(s.call.data); old_state = copy.deepcopy(s.task.data['graph_runtime'])
    old_ledger = copy.deepcopy(vars(s.ledger))
    assert graph.process(s.db, s.p, s.job) == 'blocked'
    assert len(s.recoveries) == 1 and s.call.status == 'blocked_model' and s.call.data == old_call
    assert s.task.data['graph_runtime'] == old_state and vars(s.ledger) == old_ledger
    current = s.task.data[graph.TRANSPORT][s.identity]
    assert current['phase'] == 'blocked' and current['invocation_id'] == s.call.id
    assert current['job_id'] == s.job.id and current['dispatch_ownership'] == s.owner
    assert [row.action for row in s.db.added] == ['workflow.graph.node_blocked']
    assert s.db.commits == 0


@pytest.mark.parametrize('marker', [None, 'malformed', [], {'schema': reasoning.STATE, 'capture': None}])
def test_prior_running_without_captured_answer_never_enters_dispatch_or_new_reservation(monkeypatch, marker):
    s = process_scene(monkeypatch, status='running', capture=False)
    s.call.data = {**s.call.data, reasoning.STATE_KEY: copy.deepcopy(marker)}
    before = copy.deepcopy(s.call.data)
    assert graph.process(s.db, s.p, s.job) == 'blocked'
    assert s.recoveries == [] and s.call.status == 'running' and s.call.data == before
    assert s.task.data[graph.TRANSPORT][s.identity]['phase'] == 'blocked' and s.db.commits == 0


@pytest.mark.parametrize('provider,secret', [('qwen', 'DASHSCOPE_API_KEY'), ('deepseek', 'DEEPSEEK_API_KEY')])
def test_reasoning_stage_routes_only_after_exact_provider_secret_resolution(monkeypatch, provider, secret):
    s = reasoning_scene(); s.model.data['execution'].update(provider=provider, secret_ref=secret,
        model='public_' + provider)
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'public-qwen-routing-marker')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'public-deepseek-routing-marker')
    monkeypatch.setattr(gateway, 'settings', lambda: NS(dashscope_api_key=None, deepseek_api_key=None))
    routed = []; reserved = lambda *_args: None; response = lambda *_args: None
    def call(cap, messages, schema, p, *, key, on_reserved, on_response):
        assert cap is s.model and p is s.p and messages == [{'role': 'user', 'content': '公开资料'}]
        assert schema == {'type': 'object'} and on_reserved is reserved and on_response is response
        assert key == 'public-' + provider + '-routing-marker'
        routed.append(provider); return {'result': 'original'}, {'provider': provider}
    monkeypatch.setattr(transport, 'call', call)
    for name in ('_cloud_native_report', '_cloud_structured_report', '_cloud_strict_evidence_map'):
        monkeypatch.setattr(gateway, name, denied)
    result = gateway._cloud_json(s.model, [{'role': 'user', 'content': '公开资料'}], {'type': 'object'}, s.p,
        on_reserved=reserved, on_response=response, evidence_stage=transport.STAGE)
    assert result == ({'result': 'original'}, {'provider': provider}) and routed == [provider]
    s.model.data['execution']['secret_ref'] = 'DEEPSEEK_API_KEY' if provider == 'qwen' else 'DASHSCOPE_API_KEY'
    with pytest.raises(gateway.ModelBlocked):
        gateway._cloud_json(s.model, [], {}, s.p, on_reserved=reserved, on_response=response,
            evidence_stage=transport.STAGE)
    assert routed == [provider]


@pytest.mark.parametrize('declaration', ['report-business-flow-output-v184', 'report-business-flow-output-v193'])
def test_original_report_profile_cannot_be_repurposed_by_reasoning_stage(monkeypatch, declaration):
    s = reasoning_scene(); s.model.data['execution']['report_evidence_output'] = {'schema': declaration}
    monkeypatch.setattr(transport, 'call', denied)
    monkeypatch.setattr(gateway, '_cloud_native_report', denied)
    with pytest.raises(gateway.ModelBlocked):
        gateway._cloud_json(s.model, [], {}, s.p, on_reserved=lambda *_args: None,
            on_response=lambda *_args: None, evidence_stage=transport.STAGE)


class GatewayIO:
    def __init__(self, job, owner):
        self.job = job; self.info = {'worker_lease': owner}; self.added = []; self.snapshots = []
        self.invocation = None; self.committed_count = 0

    def scalar(self, statement):
        if statement.column_descriptions[0]['entity'] is Invocation:
            return None
        assert 'count(' in str(statement)
        return 0

    def add(self, row):
        assert isinstance(row, (Invocation, Audit)); self.added.append(row)
        if isinstance(row, Invocation): self.invocation = row

    def flush(self):
        for index, row in enumerate(self.added):
            if row.id is None: row.id = ident(400 + index)

    def commit(self):
        self.snapshots.append({'status': self.invocation.status,
            'data': copy.deepcopy(self.invocation.data), 'audits': [
                (row.action, copy.deepcopy(row.data)) for row in self.added[self.committed_count:]
                if isinstance(row, Audit)]})
        self.committed_count = len(self.added)


def reader_gateway_scene(monkeypatch, failure):
    from generic_agents import native_goal_queries, native_task_queries, workflow_graph_scope
    from generic_agents import layered_approval_store_v175 as approvals
    s = reasoning_scene(); key = 'graph:' + ident(501)
    cap = NS(id=ident(502), workspace_id=s.p.workspace_id, content_hash='a' * 64,
        data={'allowed_agent_roles': ['data'], 'input_schema': reader.input_schema(),
            'output_schema': reader.output_schema(), 'budget': {'max_concurrency': 1},
            'execution': {'adapter': 'builtin', 'handler': reader.HANDLER, 'side_effect': False}})
    job = Job(id=ident(503), workspace_id=s.p.workspace_id, kind=graph.KIND, status='running',
        data={'task_id': ident(504), 'invocation_key': key, 'input_hash': digest({'policy_pin': None})})
    owner = {'job_id': job.id, 'lease_token': ident(505)}; db = GatewayIO(job, owner)
    output = {'schema': reader.RESULT, 'status': 'waiting_goal', 'evidence_package': None,
        'resense_context': None, 'candidates': [], 'issues': [], **reader.FLAGS}
    reader.checked_result(output)
    monkeypatch.setattr(gateway, 'published', lambda *_args: cap)
    monkeypatch.setattr(gateway, 'scoped', lambda *_args: cap)
    monkeypatch.setattr(native_goal_queries, 'applies', lambda *_args: False)
    monkeypatch.setattr(native_task_queries, 'applies', lambda *_args: False)
    monkeypatch.setattr(workflow_graph_scope, 'owned_graph_job', lambda *_args: job)
    monkeypatch.setattr(workflow_graph_scope, 'before_dispatch', lambda *_args: None)
    monkeypatch.setattr(gateway, 'checkpoint', lambda *_args: None)
    monkeypatch.setattr(approvals, 'before_workflow_dispatch', lambda *_args: False)
    def execute(current_db, p, current_cap, inputs, nonce):
        assert current_db is db and p is s.p and current_cap is cap and nonce == key
        if failure == 'model': raise gateway.ModelBlocked('本地读取未形成成功产物')
        if failure == 'evidence': raise HTTPException(409, '本地来源已变化')
        if failure == 'schema': return {**output, 'can_dispatch': True}
        return copy.deepcopy(output)
    monkeypatch.setattr(reader, 'execute', execute)
    return NS(db=db, p=s.p, cap=cap, job=job, key=key, output=output)


def test_builtin_reader_success_and_independent_produced_anchor_share_the_outcome_commit(monkeypatch):
    s = reader_gateway_scene(monkeypatch, None)
    call = gateway.invoke(s.db, s.p, s.cap.id, 'data', {'policy_pin': None}, s.key)
    assert call.status == 'succeeded' and call.data['output'] == s.output
    assert len(s.db.snapshots) == 2 and s.db.snapshots[0]['status'] == 'running'
    assert s.db.snapshots[0]['audits'] == []
    final = s.db.snapshots[1]
    assert final['status'] == 'succeeded' and final['data']['output'] == s.output
    assert [action for action, _data in final['audits']] == [reader.PRODUCED, 'capability.invoke']
    assert final['audits'][0][1] == reader.receipt_audit_data(call, s.job, s.output)
    assert final['audits'][1][1]['status'] == 'succeeded'


@pytest.mark.parametrize('failure,status', [('model', 'blocked_model'), ('evidence', 'blocked_evidence'),
    ('schema', 'blocked_evidence')])
def test_non_success_reader_outcome_cannot_anchor_a_produced_context(monkeypatch, failure, status):
    s = reader_gateway_scene(monkeypatch, failure)
    call = gateway.invoke(s.db, s.p, s.cap.id, 'data', {'policy_pin': None}, s.key)
    assert call.status == status and 'output' not in call.data
    assert len(s.db.snapshots) == 2
    assert all(action != reader.PRODUCED for snap in s.db.snapshots for action, _data in snap['audits'])
    assert s.db.snapshots[-1]['audits'] == [('capability.invoke', {'status': status})]


def reasoning_retry_scene(monkeypatch):
    """Reuse only row/IAM persistence seams; actual API/kernel/DTO stay real."""
    s = retry_io_scene(monkeypatch, adapter=reasoning.ADAPTER, status='blocked_model', state=False)
    r = reasoning_scene(); inputs = copy.deepcopy(r.inputs)
    for candidate in inputs['candidates']: candidate['context'] = None
    # validate_graph returns an independent copy. Configure the actual graph
    # node read by retry/context, rather than only the fixture's source node.
    s.node = next(node for node in s.graph['nodes'] if node['id'] == s.node['id'])
    s.node['execution'].update(input_schema=reasoning.input_schema(), output_schema=reasoning.output_schema(),
        arguments=inputs, max_attempts=1, capability_id=r.cap.id, capability_hash=r.cap.content_hash)
    native_state = kernel.new_run(s.graph, {}, 10)
    assert native_state['eligible_ability_ids'] == [s.activation['id']]
    s.activation = native_state['activations'][s.activation['id']]
    s.inputs = inputs
    s.job.data = {**s.job.data, 'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs),
        'graph_hash': native_state['graph_hash'], 'activation_version': s.activation['version']}
    original = copy.deepcopy(s.task.data[graph.TRANSPORT][s.activation['id']])
    original['input_hash'] = digest(inputs)
    original['dispatch_input_hash'] = digest(inputs)
    original['dispatch_authorization'] = {**original['dispatch_authorization'],
        'input_hash': digest(inputs), 'capability_hash': r.cap.content_hash}
    s.transport = original
    s.task.data = {**s.task.data, 'graph_runtime': native_state,
        graph.TRANSPORT: {s.activation['id']: original}}
    s.call.capability_id = r.cap.id
    s.call.data = {**s.call.data, 'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs),
        'job_id': s.job.id, 'capability_hash': r.cap.content_hash, 'approval': None, 'side_effect': False,
        reasoning.STATE_KEY: {'schema': reasoning.STATE,
            'request': {'pin': {'workspace_id': s.p.workspace_id, 'actor_id': s.p.user_id,
                'job_id': s.job.id, 'task_id': s.task.id, 'input_hash': digest(inputs),
                'capability_id': r.cap.id, 'capability_hash': r.cap.content_hash}},
            # Routing metadata, not a full validated capture or execution proof.
            'capture': {'synthetic_original': True}}}
    s.body = s.body.model_copy(update={'confirm_repeat_read': False})
    from generic_agents import cloud_budget, workflow_graph_scope
    monkeypatch.setattr(cloud_budget, 'reserve', denied)
    monkeypatch.setattr(transport, 'call', denied)
    monkeypatch.setattr(gateway, 'invoke', denied)
    monkeypatch.setattr(workflow_graph_scope, 'before_dispatch', denied)
    return s


def test_actual_retry_api_requeues_same_captured_reasoning_job_at_one_attempt_and_replays_nonce(monkeypatch):
    s = reasoning_retry_scene(monkeypatch)
    old_call = copy.deepcopy(s.call.data); old_job = copy.deepcopy(s.job.data)
    old_transport = copy.deepcopy(s.transport); old_graph = copy.deepcopy(s.task.data['graph_runtime'])
    result = graph_api.retry(s.task.id, s.body, s.p, s.db)
    assert s.node['execution']['max_attempts'] == 1 and s.job.data['attempt'] == 1
    assert s.job.id == old_transport['job_id'] and s.job.idempotency_key == s.key
    assert s.job.data == {**old_job, 'recovery_only': True} and s.job.status == 'queued'
    assert s.call.status == 'blocked_model' and s.call.data == old_call
    assert s.task.data['graph_runtime'] == old_graph
    assert s.task.data[graph.TRANSPORT][s.activation['id']] == {**old_transport, 'phase': 'queued', 'reason': None}
    assert not any(isinstance(row, (Job, Invocation)) for row in s.db.added)
    assert not any(check[0] == 'new-read-attempt' for check in s.checks)
    before = (s.job.version, s.db.commits, len(s.db.added), copy.deepcopy(s.job.data))
    assert graph_api.retry(s.task.id, s.body, s.p, s.db) == result
    assert (s.job.version, s.db.commits, len(s.db.added), s.job.data) == before
    assert s.db.commits == 1


@pytest.mark.parametrize('fault', ['no_capture', 'wrong_marker', 'wrong_job', 'wrong_task',
    'wrong_capability', 'wrong_input'])
def test_actual_retry_api_rejects_unbound_reasoning_candidates_without_new_job_or_charge(monkeypatch, fault):
    s = reasoning_retry_scene(monkeypatch); state = s.call.data[reasoning.STATE_KEY]
    if fault == 'no_capture': state['capture'] = None
    elif fault == 'wrong_marker': state['schema'] = 'other-family'
    elif fault == 'wrong_job': state['request']['pin']['job_id'] = ident(601)
    elif fault == 'wrong_task': state['request']['pin']['task_id'] = ident(602)
    elif fault == 'wrong_capability': s.call.capability_id = ident(603)
    else: s.call.data['input_hash'] = '0' * 64
    before = copy.deepcopy((s.task.data, s.job.data, s.call.data))
    with pytest.raises(HTTPException) as error: graph_api.retry(s.task.id, s.body, s.p, s.db)
    assert error.value.status_code == 409
    assert (s.task.data, s.job.data, s.call.data) == before
    assert s.job.status == 'failed' and s.job.data['attempt'] == 1
    assert s.db.commits == 0 and s.db.added == []
    assert not any(check[0] == 'new-read-attempt' for check in s.checks)


def test_fake_capture_dictionary_only_schedules_original_read_check_not_a_new_execution(monkeypatch):
    s = reasoning_retry_scene(monkeypatch)
    s.call.data[reasoning.STATE_KEY]['capture'] = {}
    original_call = copy.deepcopy(s.call.data); original_key = s.job.data['invocation_key']
    graph_api.retry(s.task.id, s.body, s.p, s.db)
    assert s.job.data['invocation_key'] == original_key == s.key
    assert s.job.data['attempt'] == 1 and s.job.data['recovery_only'] is True
    assert s.call.status == 'blocked_model' and s.call.data == original_call
    assert [row.action for row in s.db.added] == ['workflow.graph.recovery_requested']
    assert not any(isinstance(row, (Job, Invocation)) for row in s.db.added)
    # The process failure cases above cover its independent recover rejection;
    # this marker intentionally cannot stand in for captured bytes/Audit proof.
