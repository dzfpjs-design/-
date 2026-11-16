"""Local capacity/read-only proof at explicit ORM/IAM boundaries, not PG.

Real local manifests and the actual gateway pre-Invocation path are used. No
adapter dispatch, model call, receipt recovery or database write is performed.
"""
import copy
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.elements import BooleanClauseList

from generic_agents import workflow_local_transaction_capacity_v201 as subject
from generic_agents import workflow_task_packet_v199 as packet
from generic_agents import workflow_task_archive_v198 as archive
from generic_agents import workflow_registered_tool_plan_v201 as plans
from generic_agents import gateway, business_tools, native_goal_queries, native_task_queries
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, Invocation, Job, Task
from tests.test_business_reasoning_v197 import manifest, scene as reasoning_scene
from tests.test_report_business_flow_wire_v184 import no_external


def uid(name): return str(uuid5(NAMESPACE_URL, 'public-local-capacity-v201:' + name))


class ReadIO:
    """Select boundary with typed ORM filters; deliberately no write interface."""
    def __init__(self): self.rows = {}; self.reads = []
    def clause_matches(self, row, clause):
        if isinstance(clause, BooleanClauseList):
            assert clause.operator.__name__ == 'and_'
            return all(self.clause_matches(row, child) for child in clause.clauses)
        left, right = clause.left, clause.right
        if hasattr(left, 'right') and getattr(left.right, 'value', None) == 'parent_invocation_id':
            actual = row.data.get('parent_invocation_id')
        else:
            name = getattr(left, 'key', None)
            assert name in {'workspace_id', 'capability_id', 'id', 'idempotency_key', 'status',
                'parent_invocation_id', 'plan_id', 'action'}
            actual = getattr(row, name)
        if clause.operator.__name__ == 'in_op': return actual in right.value
        assert clause.operator.__name__ == 'eq'
        return actual == right.value
    def matches(self, statement):
        columns = statement.column_descriptions
        counted = columns[0]['name'] == 'count'
        cls = Invocation if counted else columns[0]['entity']
        self.reads.append((cls, counted))
        rows = list(self.rows.get(cls, []))
        if statement.whereclause is not None:
            rows = [row for row in rows if self.clause_matches(row, statement.whereclause)]
        return rows, counted
    def scalar(self, statement):
        rows, counted = self.matches(statement)
        if counted: return len(rows)
        assert len(rows) <= 1
        return rows[0] if rows else None
    def scalars(self, statement): return self.matches(statement)[0]
    def add(self, *args): pytest.fail('capacity checks must not add records')
    def flush(self): pytest.fail('capacity checks must not flush')
    def commit(self): pytest.fail('capacity checks must not commit or settle unknowns')


def scene(adapter='task_evidence_packet'):
    handler = packet if adapter == packet.ADAPTER else archive
    p = NS(user_id=uid('actor'), workspace_id=uid('workspace'), role='admin')
    data = {**manifest(), 'kind': 'tool', 'name': 'public_' + adapter, 'allowed_agent_roles': ['data'],
        'input_schema': handler.input_schema(), 'output_schema': handler.output_schema(),
        'execution': {'adapter': adapter, 'side_effect': True}, 'data_policy': {'cloud_allowed': False},
        'dependencies': [], 'budget': {'max_attempts': 1, 'max_concurrency': 1},
        'specification': {'schema': 'workflow-task-evidence-packet-contract-v199'
            if adapter == packet.ADAPTER else 'workflow-task-archive-contract-v198',
            'operation': handler.OP, 'candidate_only': True}}
    assert handler.validate_manifest(data) == data
    cap = NS(id=uid('capability'), workspace_id=p.workspace_id, kind='tool', status='published',
        data=data, content_hash=digest(data), validated_hash=digest(data), release_version='1.0.0')
    inputs = {'policy_pin': None} if adapter == packet.ADAPTER else {
        'record_type': '候选归集', 'title': '本任务候选档案', 'summary': '待独立人工核对，未确认事实'}
    key = 'graph:' + uid('old-task') + ':a_old:1'; token = 'original-ended-dispatch-token'
    job = NS(id=uid('old-job'), workspace_id=p.workspace_id, kind='execute_full_semantic_graph_node',
        status='blocked', idempotency_key=key, data={'task_id': uid('old-task'), 'actor_id': p.user_id,
            'activation_id': 'a_old', 'graph_hash': 'a' * 64, 'cancel_epoch': 0, 'attempt': 1,
            'activation_version': 0, 'invocation_key': key, 'input_hash': digest(inputs), 'inputs': copy.deepcopy(inputs)})
    task = NS(id=uid('old-task'), workspace_id=p.workspace_id, data={
        'graph_runtime': {'graph_hash': 'a' * 64, 'activations': {'a_old': {'node_id': 'source_node', 'epoch': 0}}},
        'graph_jobs': {'a_old': {'job_id': job.id, 'invocation_key': key, 'cancel_epoch': 0, 'attempt': 1,
            'input_hash': digest(inputs), 'dispatch_input_hash': digest(inputs), 'dispatch_committed': True,
            'dispatch_ownership': {'job_id': job.id, 'lease_token': token}, 'phase': 'blocked'}}})
    node = {'execution': {'capability_id': cap.id, 'capability_hash': cap.content_hash}}
    approval = handler.make_approval(task, job, node, inputs)
    call = NS(id=uid('old-call'), workspace_id=p.workspace_id, capability_id=cap.id, idempotency_key=key,
        status='unknown', data={'role': 'data', 'side_effect': True, 'capability_hash': cap.content_hash,
            'capability_snapshot': copy.deepcopy(data), 'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs),
            'job_id': job.id, 'lease_token': token, 'approval': approval})
    task.data['graph_jobs']['a_old']['invocation_id'] = call.id
    db = ReadIO(); db.rows = {Invocation: [call], Job: [job], Task: [task]}
    return NS(db=db, p=p, cap=cap, job=job, task=task, call=call, inputs=inputs, key=key, approval=approval)


def registered_scene():
    """A retained ended orchestrator, without inventing a supplier outcome."""
    s = scene(); s.targets = []
    for name in ('focus', 'patrol'):
        data = {**manifest(), 'kind': 'tool', 'name': 'public_registered_' + name,
            'allowed_agent_roles': ['business'], 'input_schema': {'type': 'object'},
            'output_schema': {'type': 'object'}, 'execution': {'adapter': 'http', 'side_effect': True,
                'dispatch_scope': 'registered_tool_plan_v201', 'approval_policy': 'admin',
                'endpoint': 'https://synthetic.invalid/' + name,
                'receipt_endpoint': 'https://synthetic.invalid/' + name + '/receipt',
                'receipt_protocol': plans.RECEIPT}, 'data_policy': {'external_allowed': True},
            'budget': {'max_attempts': 1, 'max_concurrency': 1}, 'dependencies': [],
            'specification': {'simulation_only': True}}
        s.targets.append(NS(id=uid('registered-' + name), workspace_id=s.p.workspace_id,
            kind='tool', status='published', release_version='1.0.0', data=data,
            content_hash=digest(data), validated_hash=digest(data)))
    data = {**manifest(), 'kind': 'tool', 'name': 'public_registered_orchestrator',
        'allowed_agent_roles': ['business'], 'input_schema': plans.input_schema(),
        'output_schema': plans.output_schema(), 'execution': {'adapter': 'workflow_service',
            'service_actor': 'execution_service', 'operation': plans.OP, 'side_effect': True},
        'data_policy': {'cloud_allowed': False}, 'budget': {'max_attempts': 1, 'max_concurrency': 1},
        'dependencies': sorted(target.id for target in s.targets),
        'specification': {'schema': plans.CONTRACT, 'tool_pins': [{'capability_id': target.id,
            'capability_hash': target.content_hash, 'release_version': target.release_version} for target in s.targets]}}
    assert plans.validate_manifest(data) == data
    original = reasoning_scene()
    for candidate in original.inputs['candidates']: candidate['context'] = None
    s.inputs = {'reasoning_result': plans.reasoning.check_result({'inputs': original.inputs}, original.output),
        'candidate_plans': [{'candidate_ref': candidate['candidate_ref'], 'steps': [{
            'capability_id': target.id, 'capability_hash': target.content_hash, 'agent_role': 'business',
            'arguments': {}, 'allow_external_inputs': True}]} for candidate, target in zip(original.inputs['candidates'], s.targets)]}
    s.cap.data = data; s.cap.content_hash = s.cap.validated_hash = digest(data)
    s.job.data = {**s.job.data, 'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs)}
    node = {'execution': {'capability_id': s.cap.id, 'capability_hash': s.cap.content_hash}}
    s.approval = plans.service.make_approval(s.task, s.job, node, s.inputs)
    s.call.data = {**s.call.data, 'role': 'business', 'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs),
        'capability_hash': s.cap.content_hash, 'capability_snapshot': copy.deepcopy(data), 'approval': copy.deepcopy(s.approval)}
    s.task.data['graph_jobs']['a_old'].update(input_hash=digest(s.inputs), dispatch_input_hash=digest(s.inputs))
    s.task.data['graph_jobs']['a_old']['dispatch_authorization'] = {'epoch': 0, 'job_id': s.job.id,
        'lease_token': s.call.data['lease_token'], 'actor_id': s.job.data['actor_id'],
        'input_hash': digest(s.inputs), 'capability_hash': s.cap.content_hash,
        'authorized_at': '2026-10-24T00:00:00+00:00'}
    s.db.rows[Capability] = s.targets
    return s


def refreeze_registered(s):
    """Freeze a distinct synthetic contract variant to exercise its real guard."""
    for target in s.targets:
        target.content_hash = target.validated_hash = digest(target.data)
        for pin in s.cap.data['specification']['tool_pins']:
            if pin['capability_id'] == target.id: pin['capability_hash'] = target.content_hash
        for candidate in s.inputs['candidate_plans']:
            for step in candidate['steps']:
                if step['capability_id'] == target.id: step['capability_hash'] = target.content_hash
    s.cap.content_hash = s.cap.validated_hash = digest(s.cap.data)
    node = {'execution': {'capability_id': s.cap.id, 'capability_hash': s.cap.content_hash}}
    s.approval = plans.service.make_approval(s.task, s.job, node, s.inputs)
    s.job.data = {**s.job.data, 'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs)}
    s.call.data = {**s.call.data, 'inputs': copy.deepcopy(s.inputs), 'input_hash': digest(s.inputs),
        'capability_hash': s.cap.content_hash, 'capability_snapshot': copy.deepcopy(s.cap.data), 'approval': copy.deepcopy(s.approval)}
    s.task.data['graph_jobs']['a_old'].update(input_hash=digest(s.inputs), dispatch_input_hash=digest(s.inputs))
    if 'dispatch_authorization' in s.task.data['graph_jobs']['a_old']:
        s.task.data['graph_jobs']['a_old']['dispatch_authorization'].update(
            input_hash=digest(s.inputs), capability_hash=s.cap.content_hash)


@pytest.mark.parametrize('ended', ['failed', 'blocked', 'cancelled'])
def test_exact_registered_orchestrator_releases_only_ended_unknown_cpu_slot_without_settlement(ended):
    s = registered_scene(); s.job.status = ended
    before = copy.deepcopy((s.call.data, s.job.data, s.task.data, [target.data for target in s.targets]))
    assert subject.count_active(s.db, s.p, s.cap) == 0
    assert s.call.status == 'unknown' and (s.call.data, s.job.data, s.task.data,
        [target.data for target in s.targets]) == before


@pytest.mark.parametrize('fault', ['target_missing', 'target_workspace', 'target_unpublished', 'target_kind',
    'target_hash', 'target_release', 'target_not_validated', 'target_http_adapter', 'target_dispatch_scope',
    'target_receipt_protocol', 'target_receipt_missing', 'target_endpoint_missing', 'target_readonly',
    'target_no_external', 'target_no_admin', 'target_attempts', 'target_concurrency', 'target_bool_concurrency',
    'target_input_binding', 'target_role', 'target_malformed_schema', 'parent_role', 'parent_approval', 'parent_execution_extra',
    'parent_not_canonical_operation', 'parent_null_call_link', 'parent_missing_authorization', 'parent_missing_job', 'parent_running_job',
    'parent_running_call', 'parent_waiting_call', 'orphan_marker', 'orphan_item', 'orphan_audit'])
def test_registered_orchestrator_missing_drifted_or_noncanonical_proof_holds_parent_slot(fault):
    s = registered_scene(); target = s.targets[0]; execution = target.data['execution']
    refreeze = True
    if fault == 'target_missing': s.db.rows[Capability] = []; refreeze = False
    elif fault == 'target_workspace': target.workspace_id = uid('other-workspace')
    elif fault == 'target_unpublished': target.status = 'draft'
    elif fault == 'target_kind': target.kind = 'source'
    elif fault == 'target_hash': target.content_hash = 'f' * 64; refreeze = False
    elif fault == 'target_release': target.release_version = '2.0.0'
    elif fault == 'target_not_validated': target.validated_hash = 'f' * 64; refreeze = False
    elif fault == 'target_http_adapter': execution['adapter'] = 'mcp'
    elif fault == 'target_dispatch_scope': execution['dispatch_scope'] = 'another_plan'
    elif fault == 'target_receipt_protocol': execution['receipt_protocol'] = 'other-receipt'
    elif fault == 'target_receipt_missing': execution['receipt_endpoint'] = ''
    elif fault == 'target_endpoint_missing': execution['endpoint'] = ''
    elif fault == 'target_readonly': execution['side_effect'] = False
    elif fault == 'target_no_external': target.data['data_policy']['external_allowed'] = False
    elif fault == 'target_no_admin': execution['approval_policy'] = 'operator'
    elif fault == 'target_attempts': target.data['budget']['max_attempts'] = 2
    elif fault == 'target_concurrency': target.data['budget']['max_concurrency'] = 2
    elif fault == 'target_bool_concurrency': target.data['budget']['max_concurrency'] = True
    elif fault == 'target_input_binding': execution['input_bindings'] = {'unknown_field': 'task_evidence'}
    elif fault == 'target_role': target.data['allowed_agent_roles'] = ['data']
    elif fault == 'target_malformed_schema': target.data['input_schema'] = {'type': 'invalid-json-schema-type'}
    elif fault == 'parent_role': s.call.data['role'] = 'data'
    elif fault == 'parent_approval': s.call.data['approval']['schema'] = packet.APPROVAL; refreeze = False
    elif fault == 'parent_execution_extra': s.cap.data['execution']['endpoint'] = 'https://synthetic.invalid/not-local'
    elif fault == 'parent_not_canonical_operation': s.cap.data['execution']['operation'] = 'dispatch_tasks'
    elif fault == 'parent_null_call_link': s.task.data['graph_jobs']['a_old']['invocation_id'] = None
    elif fault == 'parent_missing_authorization': s.task.data['graph_jobs']['a_old'].pop('dispatch_authorization')
    elif fault == 'parent_missing_job': s.db.rows[Job].clear()
    elif fault == 'parent_running_job': s.job.status = 'running'
    elif fault == 'parent_running_call': s.call.status = 'running'
    elif fault == 'parent_waiting_call': s.call.status = 'waiting_receipt'
    elif fault == 'orphan_marker': s.call.data[plans.KEY] = {'schema': plans.PLAN, 'plan_id': uid('missing-plan'), 'plan_hash': 'a' * 64}
    elif fault == 'orphan_item':
        s.db.rows[plans.Item] = [NS(workspace_id=s.p.workspace_id, parent_invocation_id=s.call.id)]
    elif fault == 'orphan_audit':
        s.db.rows[Audit] = [NS(workspace_id=s.p.workspace_id, action=plans.CREATED,
            data={'parent_invocation_id': s.call.id})]
    if refreeze: refreeze_registered(s)
    assert subject.count_active(s.db, s.p, s.cap) == 1 and s.call.status in subject.ACTIVE


def test_actual_unknown_registered_http_child_still_holds_its_target_slot():
    s = registered_scene(); target = s.targets[0]
    child = NS(id=uid('unknown-child'), workspace_id=s.p.workspace_id, capability_id=target.id,
        idempotency_key='registered-tool-plan-v201:' + s.call.id + ':0', status='unknown',
        data={'side_effect': True, 'approval': {'schema': plans.CHILD_APPROVAL, 'parent_invocation_id': s.call.id}})
    s.db.rows[Invocation].append(child); before = copy.deepcopy(child.data)
    assert subject.count_active(s.db, s.p, s.cap) == 0
    assert subject.count_active(s.db, s.p, target) == 1
    assert child.status == s.call.status == 'unknown' and child.data == before


def test_registered_orchestrator_old_key_returns_original_unknown_before_capacity(monkeypatch):
    s = registered_scene(); gateway_boundaries(monkeypatch, s); before = copy.deepcopy(s.call.data)
    assert gateway.invoke(s.db, s.p, s.cap.id, 'business', s.inputs, s.key, approval=s.approval) is s.call
    assert s.call.status == 'unknown' and s.call.data == before
    assert not any(cls is Job for cls, _ in s.db.reads)


def test_registered_new_task_needs_own_approval_and_unknown_child_still_prevents_target_post(monkeypatch):
    s = registered_scene(); gateway_boundaries(monkeypatch, s); seen = []
    def own_approval(db, p, cap, role, inputs, key, approval):
        seen.append(key); raise HTTPException(403, 'new Task requires its own approved authority')
    monkeypatch.setattr(business_tools, 'validate_call_approval', own_approval)
    fresh_key = 'graph:' + uid('new-independent-task') + ':a_new:1'
    with pytest.raises(HTTPException) as caught:
        gateway.invoke(s.db, s.p, s.cap.id, 'business', s.inputs, fresh_key, approval=s.approval)
    assert caught.value.status_code == 403 and seen == [fresh_key] and len(s.db.rows[Invocation]) == 1
    target = s.targets[0]
    s.db.rows[Invocation].append(NS(id=uid('held-target-child'), workspace_id=s.p.workspace_id,
        capability_id=target.id, idempotency_key='registered-tool-plan-v201:' + s.call.id + ':0', status='unknown', data={}))
    monkeypatch.setattr(gateway, 'published', lambda *_: target)
    monkeypatch.setattr(gateway, 'scoped', lambda *_: target)
    seen.clear()
    with pytest.raises(HTTPException) as held:
        gateway.invoke(s.db, s.p, target.id, 'business', {}, fresh_key + ':child',
            approval={'schema': plans.CHILD_APPROVAL})
    assert held.value.status_code == 409 and '该能力正在运行' in held.value.detail and not seen
    assert len(s.db.rows[Invocation]) == 2 and s.call.status == 'unknown'


@pytest.mark.parametrize('adapter', [packet.ADAPTER, archive.ADAPTER])
@pytest.mark.parametrize('ended', ['failed', 'blocked', 'cancelled'])
def test_only_actual_ended_local_unknown_releases_capacity_without_mutating_old_records(adapter, ended):
    s = scene(adapter); s.job.status = ended
    before = copy.deepcopy((s.call.data, s.job.data, s.task.data))
    assert subject.count_active(s.db, s.p, s.cap) == 0
    assert s.call.status == 'unknown' and (s.call.data, s.job.data, s.task.data) == before


@pytest.mark.parametrize('state', ['queued', 'running', 'succeeded', 'waiting_service'])
def test_any_nonqualifying_job_state_keeps_unknown_capacity(state):
    s = scene(); s.job.status = state
    assert subject.count_active(s.db, s.p, s.cap) == 1


@pytest.mark.parametrize('status', ['running', 'waiting_receipt'])
def test_running_or_waiting_call_is_not_released_even_with_terminal_job(status):
    s = scene(); s.call.status = status
    assert subject.count_active(s.db, s.p, s.cap) == 1
    assert not any(cls is Job for cls, _ in s.db.reads)


@pytest.mark.parametrize('fault', ['missing_job', 'job_workspace', 'job_key', 'job_inputs', 'job_kind',
    'approval', 'snapshot', 'missing_task', 'graph', 'owner', 'duplicate_transport', 'authorization', 'null_call_link'])
def test_missing_or_mixed_original_proof_keeps_capacity_instead_of_inventing_non_execution(fault):
    s = scene(); transport = s.task.data['graph_jobs']['a_old']
    if fault == 'missing_job': s.db.rows[Job].clear()
    elif fault == 'job_workspace': s.job.workspace_id = uid('another-workspace')
    elif fault == 'job_key': s.job.data['invocation_key'] += ':different'
    elif fault == 'job_inputs': s.job.data['inputs'] = {'policy_pin': {'untrusted': True}}
    elif fault == 'job_kind': s.job.kind = 'dispatch_acquisition'
    elif fault == 'approval': s.call.data['approval']['task_id'] = uid('another-task')
    elif fault == 'snapshot': s.call.data['capability_snapshot']['execution']['side_effect'] = False
    elif fault == 'missing_task': s.db.rows[Task].clear()
    elif fault == 'graph': s.task.data['graph_runtime']['graph_hash'] = 'b' * 64
    elif fault == 'owner': transport['dispatch_ownership']['lease_token'] = 'unrelated-token'
    elif fault == 'duplicate_transport': transport['previous'] = [copy.deepcopy(transport)]
    elif fault == 'null_call_link': transport['invocation_id'] = None
    else: transport['dispatch_authorization'] = {'job_id': s.job.id, 'lease_token': 'unproven'}
    assert subject.count_active(s.db, s.p, s.cap) == 1
    assert s.call.status == 'unknown'


def test_existing_dispatch_authorization_must_match_original_owner_actor_and_hashes():
    s = scene(); transport = s.task.data['graph_jobs']['a_old']
    transport['dispatch_authorization'] = {'epoch': 0, 'job_id': s.job.id,
        'lease_token': s.call.data['lease_token'], 'actor_id': s.job.data['actor_id'],
        'input_hash': digest(s.inputs), 'capability_hash': s.cap.content_hash,
        'authorized_at': '2026-10-24T00:00:00+00:00'}
    assert subject.count_active(s.db, s.p, s.cap) == 0
    transport['dispatch_authorization']['actor_id'] = uid('different-actor')
    assert subject.count_active(s.db, s.p, s.cap) == 1


@pytest.mark.parametrize('adapter', ['http', 'mcp', 'business_reasoning', 'workflow_service', 'cloud_extractor'])
def test_external_or_other_adapters_keep_original_aggregate_count_and_do_not_read_job(adapter):
    s = scene(); s.cap.data['execution']['adapter'] = adapter
    assert subject.count_active(s.db, s.p, s.cap) == 1
    assert s.db.reads == [(Invocation, True)]


def test_local_adapter_label_with_noncanonical_registered_contract_keeps_old_count():
    s = scene(); s.cap.data['execution']['endpoint'] = 'https://untrusted.invalid/not-local'
    assert subject.count_active(s.db, s.p, s.cap) == 1
    assert s.db.reads == [(Invocation, True)]


def gateway_boundaries(monkeypatch, s):
    from generic_agents import device_gateway_sources_v270 as managed_sources

    def non_managed_source(database, principal, actual):
        # Synthetic local/orchestrator/HTTP-tool objects are not managed device
        # Source rows. This explicit IO seam does not waive production linkage.
        assert database is s.db and principal is s.p
        assert actual.kind == 'tool' and actual.workspace_id == s.p.workspace_id
        assert actual.data.get('execution', {}).get('adapter') in {
            'task_archive', 'task_evidence_packet', 'runtime_document_packet', 'workflow_service', 'http'}
        assert not managed_sources.applies(actual.data)
        return False

    monkeypatch.setattr(managed_sources, 'managed', non_managed_source)
    monkeypatch.setattr(gateway, 'published', lambda *_: s.cap)
    monkeypatch.setattr(gateway, 'scoped', lambda *_: s.cap)
    monkeypatch.setattr(native_goal_queries, 'applies', lambda *_: False)
    monkeypatch.setattr(native_task_queries, 'applies', lambda *_: False)


def test_actual_gateway_old_key_still_returns_original_unknown_without_settlement_or_capacity_recheck(monkeypatch):
    s = scene(); gateway_boundaries(monkeypatch, s)
    before = copy.deepcopy(s.call.data)
    assert gateway.invoke(s.db, s.p, s.cap.id, 'data', s.inputs, s.key, approval=s.approval) is s.call
    assert s.call.status == 'unknown' and s.call.data == before
    assert not any(cls is Job for cls, _ in s.db.reads)


def test_new_independent_key_reaches_actual_approval_gate_but_does_not_borrow_old_authority(monkeypatch):
    s = scene(); gateway_boundaries(monkeypatch, s); seen = []
    def approval_gate(db, p, cap, role, inputs, key, approval):
        seen.append(key)
        # Deliberately stop at the new request's actual authority boundary.
        # Releasing capacity never creates a new approval or Invocation.
        raise HTTPException(403, 'new Task still requires its own current approval')
    monkeypatch.setattr(business_tools, 'validate_call_approval', approval_gate)
    fresh_key = 'graph:' + uid('different-task') + ':a_new:1'
    with pytest.raises(HTTPException) as caught:
        gateway.invoke(s.db, s.p, s.cap.id, 'data', s.inputs, fresh_key, approval=s.approval)
    assert caught.value.status_code == 403 and seen == [fresh_key]
    assert len(s.db.rows[Invocation]) == 1 and s.call.status == 'unknown'
    s.job.status = 'running'; seen.clear()
    with pytest.raises(HTTPException) as held:
        gateway.invoke(s.db, s.p, s.cap.id, 'data', s.inputs, fresh_key, approval=s.approval)
    assert held.value.status_code == 409 and '该能力正在运行' in held.value.detail and not seen
