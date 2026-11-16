"""Synthetic SELECT-boundary tests with the actual graph kernel/provenance checks.

The separate issued-source/PG GoalSpec journey proves durable authority. These
fixtures do not claim their in-memory release or invocation rows are real runs.
"""
import copy
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest

from generic_agents.api import app
from generic_agents import workflow_graph_goal_tools_v126 as tools
from generic_agents import workflow_graph, workflow_abilities, workspace_api
from generic_agents import instance_semantic_runtime_dispatch, native_goals, entity_release_compat
from generic_agents.contracts import digest
from generic_agents.models import Entity, Invocation, Job
from tests.test_full_semantic_graph_v1 import ability, base, control, edge, loop_graph, object_schema


def fixture(monkeypatch, mode='parallel', values=(1, 1)):
    data = {'description': '已登记统计能力', 'execution': {'adapter': 'builtin', 'side_effect': False},
        'input_schema': object_schema(approved={'type': 'boolean'}),
        'output_schema': object_schema(value={'type': 'number'}, again={'type': 'boolean'}),
        'allowed_agent_roles': ['data'], 'budget': {'max_attempts': 2}}
    cap = NS(id='registered_ability', workspace_id='workspace', kind='tool', data=data,
        content_hash=digest(data), validated_hash=digest(data), status='published', release_version='1.0.0')

    def node(identity):
        result = ability(identity, {})
        result['execution'].update(capability_hash=cap.content_hash, arguments={'approved': True},
            input_schema=copy.deepcopy(data['input_schema']), output_schema=copy.deepcopy(data['output_schema']))
        return result

    if mode == 'loop':
        graph = loop_graph(); graph['nodes'][0] = node('h')
    elif mode == 'parallel':
        graph = base([control('f', 'fork', join_id='j', branch_ids=['A', 'B']), node('a'), node('b'),
            control('j', 'join', fork_id='f', policy='all'), control('end', 'end')],
            [edge('f', 'a', 'branch', 'A'), edge('f', 'b', 'branch', 'B'),
             edge('a', 'j'), edge('b', 'j'), edge('j', 'end')], 'f', ['a', 'b'])
    else:
        graph = base([node('a'), node('b'), control('end', 'end')],
            [edge('a', 'b'), edge('b', 'end')], 'a', ['a', 'b'])
    nodes = [item for item in graph['nodes'] if item['kind'] == 'ability']
    business = {'id': 'business', 'name': '图统计业务', 'execution_mode': 'confirmed_graph_workflow',
        'full_semantic_graph_origin': {'synthetic': True},
        'flow': {'steps': [{'id': 'old_projection', 'name': '旧人工投影', 'agent': '人工确认'}]}}
    binding = {'schema': 'report-full-semantic-graph-binding-v3', 'graph': graph, 'candidate_index': 0,
        'source_business': {'steps': [{'name': '统计节点' + item['id']} for item in nodes]},
        'source_design': {'business_bindings': [{'step_ids': [item['source_step_id'] for item in nodes]}]},
        'capability_pins': {item['id']: copy.deepcopy(item['execution']) for item in nodes}}
    snapshot = {'businesses': [business]}
    release = NS(id='release', instance_id='instance', workspace_id='workspace', package_id='package',
        configuration_hash=digest(snapshot), data={'snapshot': snapshot})
    package = NS(id='package', kind='domain_package', status='published', data={
        'dependencies': [cap.id], 'specification': {'configuration_hash': release.configuration_hash}},
        content_hash='package-hash', validated_hash='package-hash')
    p = NS(workspace_id='workspace', user_id='formal_owner_not_the_node_actor')
    state = tools.pure.new_run(graph, {}, 10)
    jobs, calls, transports, candidates = {}, {}, {}, []
    index = 0
    while state['eligible_ability_ids']:
        for identity in list(state['eligible_ability_ids']):
            activation = state['activations'][identity]
            actual_node = next(item for item in nodes if item['id'] == activation['node_id'])
            inputs = tools.pure.ability_inputs(graph, state, identity)
            key = 'graph:task:' + identity + ':1'; job_id = 'job_' + str(index)
            call_id, lease = 'invocation_' + str(index), 'original_dispatch_lease_' + str(index)
            job = NS(id=job_id, workspace_id=p.workspace_id, kind=workflow_graph.KIND,
                idempotency_key=key, status='succeeded', lease_token=None, lease_until=None,
                data={'task_id': 'task', 'activation_id': identity, 'actor_id': actual_node['actor_id'],
                    'graph_hash': state['graph_hash'], 'cancel_epoch': 0, 'activation_version': 0,
                    'invocation_key': key, 'inputs': inputs, 'input_hash': digest(inputs), 'attempt': 1})
            output = {'value': values[index], 'again': mode == 'loop' and index == 0}
            call = NS(id=call_id, workspace_id=p.workspace_id, capability_id=cap.id, status='succeeded',
                idempotency_key=key, data={'job_id': job_id, 'lease_token': lease, 'input_hash': digest(inputs),
                    'role': actual_node['execution']['agent_role'], 'capability_hash': cap.content_hash,
                    'approval': None, 'side_effect': False, 'started_at': '2026-10-20T00:00:00+00:00',
                    'output': copy.deepcopy(output)})
            transports[identity] = {'job_id': job_id, 'invocation_key': key, 'input_hash': digest(inputs),
                'cancel_epoch': 0, 'attempt': 1, 'phase': 'succeeded', 'dispatch_committed': True,
                'invocation_id': call_id, 'output_hash': digest(output), 'dispatch_input_hash': digest(inputs),
                'dispatch_ownership': {'job_id': job_id, 'lease_token': lease},
                'dispatch_authorization': {'epoch': 0, 'job_id': job_id, 'lease_token': lease,
                    'input_hash': digest(inputs), 'capability_hash': cap.content_hash,
                    'actor_id': actual_node['actor_id'], 'authorized_at': '2026-10-20T00:00:01+00:00'}}
            receipt = {'request_id': job_id, 'step_id': actual_node['source_step_id'], 'activation_id': identity,
                'actor': actual_node['actor_id'], 'at': '2026-10-20T00:00:03+00:00',
                'execution_mode': 'registered_graph_ability', 'invocation_id': call_id,
                'capability_id': cap.id, 'capability_hash': cap.content_hash, 'release_version': cap.release_version,
                'input_snapshot': copy.deepcopy(inputs), 'input_hash': digest(inputs), 'output': copy.deepcopy(output),
                'entity_state_updated': False, 'candidate_only': True}
            state = tools.pure.complete(graph, state, identity, output, job_id, actual_node['actor_id'],
                activation['version'], 0, state['clock'] + 1)
            jobs[job_id] = job; calls[call_id] = call; candidates.append(receipt); index += 1
    assert state['status'] == 'succeeded' and len(candidates) == 2
    entity = NS(id='entity', workspace_id=p.workspace_id, version=1, data={'数量': 1})
    task = NS(id='task', workspace_id=p.workspace_id, owner_id=p.user_id, package_id=package.id,
        status='workflow_completed', data={'instance_id': 'instance', 'instance_release_id': release.id,
            'business_id': business['id'], 'graph_runtime': state, 'graph_jobs': transports,
            'workflow_runtime': {'schema': 'workflow-graph-projection-v1', 'current_step_id': None, 'receipts': candidates},
            'object_snapshots': [{'id': entity.id, 'version': entity.version, 'attributes': copy.deepcopy(entity.data)}],
            'goal_spec': {'criteria': [{'kind': 'tool_value', 'capability_id': cap.id,
                'field': 'value', 'operator': 'eq', 'expected': 1}]}})
    checks = []

    class ReadOnlyDB:
        def add(self, *args): pytest.fail('goal tools wrote a row')
        def flush(self): pytest.fail('goal tools flushed')
        def commit(self): pytest.fail('goal tools committed')
        def execute(self, *args): pytest.fail('goal tools acquired an execution lock')

    db = ReadOnlyDB()

    def published(database, principal, identity, kind=None):
        row = package if identity == package.id else cap
        if row.id != identity or row.status != 'published': raise HTTPException(409, 'not published')
        return row

    def scoped(database, model, identity, principal):
        row = jobs.get(identity) if model is Job else calls.get(identity) if model is Invocation else entity
        if row is None or row.id != identity or row.workspace_id != principal.workspace_id:
            raise HTTPException(404, 'missing actual row')
        return row

    def verify_release(*args, **kwargs):
        checks.append(('release', kwargs)); return {'synthetic_frozen_plan': True}

    monkeypatch.setattr(tools, 'published', published); monkeypatch.setattr(tools, 'scoped', scoped)
    monkeypatch.setattr(instance_semantic_runtime_dispatch, 'modern', lambda snapshot: NS(
        verify_release=verify_release, BUSINESS_MARKER='full_semantic_graph_origin'))
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *args: copy.deepcopy(binding))
    monkeypatch.setattr(workflow_graph, 'graph_for', lambda *args: copy.deepcopy(graph))
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *args: (task, release, business))
    monkeypatch.setattr(native_goals, 'verify_workflow_inputs', lambda *args, lock_sources=True: checks.append(('acceptance_inputs',)))
    monkeypatch.setattr(entity_release_compat, 'verify_task_objects', lambda *args, lock_sources=True: checks.append(('task_objects',)))
    monkeypatch.setattr(workflow_abilities, 'bind', lambda *args: pytest.fail('graph goal used legacy input binding'))
    return NS(db=db, p=p, task=task, release=release, business=business, cap=cap, package=package,
        binding=binding, graph=graph, state=state, jobs=jobs, calls=calls, transports=transports,
        candidates=candidates, entity=entity, checks=checks)


@pytest.mark.parametrize('mode', ['sequence', 'parallel', 'loop'])
def test_catalog_and_actual_kernel_receipts_keep_every_repeated_capability_activation(monkeypatch, mode):
    f = fixture(monkeypatch, mode=mode)
    before = copy.deepcopy(f.task.data)
    catalog = tools.catalog(f.db, f.p, f.release, f.business)
    assert set(catalog) == {f.cap.id} and catalog[f.cap.id]['capability'] is f.cap
    assert catalog[f.cap.id]['sources'] == ['workflow_step']
    assert '旧人工投影' not in catalog[f.cap.id]['step_names']
    results = tools.receipts(f.db, f.p, f.task)
    assert len(results) == 2 and len({r['invocation_id'] for r in results}) == 2
    assert all(set(r) == {'status', 'method', 'capability_id', 'capability_name', 'capability_hash',
        'invocation_id', 'input_hash', 'output', 'workflow_step_id', 'workflow_receipt_hash',
        'candidate_only', 'physical_state_verified'} for r in results)
    assert all(r['status'] == 'succeeded' and r['method'] == 'registered_capability'
        and r['candidate_only'] is True and r['physical_state_verified'] is False for r in results)
    assert all(('release', {'live': False}) == item for item in f.checks if item[0] == 'release')
    assert ('acceptance_inputs',) in f.checks and ('task_objects',) in f.checks
    results[0]['output']['value'] = 999
    assert f.task.data == before


def test_bad_value_in_second_activation_is_preserved_for_existing_all_matching_semantics(monkeypatch):
    f = fixture(monkeypatch, values=(1, 9))
    result = tools.receipts(f.db, f.p, f.task)
    assert [item['output']['value'] for item in result] == [1, 9]


def test_original_dispatch_proof_survives_current_lease_clearance_and_cross_clock_skew(monkeypatch):
    f = fixture(monkeypatch)
    for job in f.jobs.values():
        job.lease_token = 'a_later_worker_recovery_lease'
    for transport in f.transports.values():
        transport['dispatch_authorization']['authorized_at'] = '2026-10-21T00:00:01+00:00'
    # No comparison of application timestamps with the independent DB clock,
    # nor current Job lease with the frozen original Invocation lease.
    assert len(tools.receipts(f.db, f.p, f.task)) == 2


@pytest.mark.parametrize('status', ['running', 'queued', 'failed', 'blocked', 'cancelled'])
def test_committed_result_with_unreaped_original_job_keeps_whole_capability_pending(monkeypatch, status):
    f = fixture(monkeypatch, values=(1, 9))
    second = list(f.jobs.values())[1]; second.status = status
    # The first result alone must never satisfy the tool_value criterion.
    assert tools.receipts(f.db, f.p, f.task) == []
    second.status = 'succeeded'
    assert [item['output']['value'] for item in tools.receipts(f.db, f.p, f.task)] == [1, 9]


@pytest.mark.parametrize('change', ['missing_receipt', 'duplicate_receipt', 'legacy_receipt', 'unknown_activation',
    'wrong_node', 'receipt_output', 'receipt_inputs', 'receipt_invocation', 'receipt_actor', 'candidate',
    'entity_updated', 'job_kind', 'job_task', 'job_actor', 'job_key', 'job_inputs', 'job_epoch', 'job_attempt',
    'transport_phase', 'transport_output', 'transport_call', 'dispatch_missing', 'dispatch_owner',
    'authorization_missing', 'authorization_lease', 'authorization_actor', 'authorization_epoch',
    'call_unknown', 'call_capability', 'call_key', 'call_job', 'call_lease', 'call_role', 'call_hash',
    'call_inputs', 'call_output', 'call_approval', 'call_side_effect', 'authorization_time', 'state_hash',
    'task_status', 'projection'])
def test_unmatched_or_forged_graph_results_never_satisfy_goal_tools(monkeypatch, change):
    f = fixture(monkeypatch)
    receipt = f.candidates[0]; job = f.jobs[receipt['request_id']]
    call = f.calls[receipt['invocation_id']]; transport = f.transports[receipt['activation_id']]
    if change == 'missing_receipt': f.candidates.pop()
    elif change == 'duplicate_receipt': f.candidates.append(copy.deepcopy(receipt))
    elif change == 'legacy_receipt': receipt['execution_mode'] = 'registered_ability'
    elif change == 'unknown_activation': receipt['activation_id'] = 'missing'
    elif change == 'wrong_node': receipt['step_id'] = 'source_other'
    elif change == 'receipt_output': receipt['output']['value'] = 8
    elif change == 'receipt_inputs': receipt['input_snapshot']['approved'] = False
    elif change == 'receipt_invocation': receipt['invocation_id'] = f.candidates[1]['invocation_id']
    elif change == 'receipt_actor': receipt['actor'] = f.p.user_id
    elif change == 'candidate': receipt['candidate_only'] = False
    elif change == 'entity_updated': receipt['entity_state_updated'] = True
    elif change == 'job_kind': job.kind = 'execute_workflow_step'
    elif change == 'job_task': job.data['task_id'] = 'other_task'
    elif change == 'job_actor': job.data['actor_id'] = f.p.user_id
    elif change == 'job_key': job.idempotency_key = 'legacy_key'
    elif change == 'job_inputs': job.data['inputs'] = {'approved': False}; job.data['input_hash'] = digest(job.data['inputs'])
    elif change == 'job_epoch': job.data['cancel_epoch'] = 1
    elif change == 'job_attempt': job.data['attempt'] = 2
    elif change == 'transport_phase': transport['phase'] = 'late_result_preserved'
    elif change == 'transport_output': transport['output_hash'] = 'f' * 64
    elif change == 'transport_call': transport['invocation_id'] = f.candidates[1]['invocation_id']
    elif change == 'dispatch_missing': transport.pop('dispatch_committed')
    elif change == 'dispatch_owner': transport['dispatch_ownership']['lease_token'] = 'unrelated_lease'
    elif change == 'authorization_missing': transport.pop('dispatch_authorization')
    elif change == 'authorization_lease': transport['dispatch_authorization']['lease_token'] = 'unrelated_lease'
    elif change == 'authorization_actor': transport['dispatch_authorization']['actor_id'] = f.p.user_id
    elif change == 'authorization_epoch': transport['dispatch_authorization']['epoch'] = 1
    elif change == 'call_unknown': call.status = 'unknown'
    elif change == 'call_capability': call.capability_id = 'other_capability'
    elif change == 'call_key': call.idempotency_key = 'other_invocation_key'
    elif change == 'call_job': call.data['job_id'] = 'other_job'
    elif change == 'call_lease': call.data['lease_token'] = 'unrelated_lease'
    elif change == 'call_role': call.data['role'] = 'business'
    elif change == 'call_hash': call.data['capability_hash'] = 'f' * 64
    elif change == 'call_inputs': call.data['input_hash'] = 'f' * 64
    elif change == 'call_output': call.data['output']['value'] = 8
    elif change == 'call_approval': call.data['approval'] = {'some_other_plan': True}
    elif change == 'call_side_effect': call.data['side_effect'] = True
    elif change == 'authorization_time': transport['dispatch_authorization']['authorized_at'] = '2026-10-20T00:00:01'
    elif change == 'state_hash': f.task.data['graph_runtime']['state_hash'] = 'f' * 64
    elif change == 'task_status': f.task.status = 'workflow_waiting'
    else: f.task.data['workflow_runtime']['schema'] = 'legacy_projection'
    before = copy.deepcopy(f.task.data)
    with pytest.raises(HTTPException) as caught: tools.receipts(f.db, f.p, f.task)
    assert caught.value.status_code in {404, 409} and f.task.data == before


@pytest.mark.parametrize('change', ['object_version', 'object_data', 'cap_unpublished', 'cap_hash',
    'cap_release', 'cap_schema', 'dependency', 'pin', 'business', 'workspace'])
def test_current_objects_capabilities_dependencies_and_frozen_business_still_gate_acceptance(monkeypatch, change):
    f = fixture(monkeypatch)
    if change == 'object_version': f.entity.version += 1
    elif change == 'object_data': f.entity.data['数量'] = 3
    elif change == 'cap_unpublished': f.cap.status = 'retired'
    elif change == 'cap_hash': f.cap.content_hash = 'f' * 64
    elif change == 'cap_release': f.cap.release_version = '2.0.0'
    elif change == 'cap_schema': f.cap.data['output_schema']['required'] = []
    elif change == 'dependency': f.package.data['dependencies'] = []
    elif change == 'pin': f.binding['capability_pins']['a']['capability_hash'] = 'f' * 64
    elif change == 'business': f.business['execution_mode'] = 'legacy_workflow'
    else: f.release.workspace_id = 'other_workspace'
    with pytest.raises(HTTPException): tools.receipts(f.db, f.p, f.task)


def test_cancelled_graph_preserves_old_invocations_without_contributing_goal_results(monkeypatch):
    f = fixture(monkeypatch)
    # A succeeded graph cannot be cancelled through the kernel. Use a valid
    # still-running parallel frontier, finish one branch, then cancel it.
    state = tools.pure.new_run(f.graph, {}, 10)
    first = f.candidates[0]; activation = state['activations'][first['activation_id']]
    state = tools.pure.complete(f.graph, state, activation['id'], first['output'], first['request_id'],
        first['actor'], activation['version'], 0, 11)
    state = tools.pure.cancel(f.graph, state, 'cancel_request', f.p.user_id, state['version'], 0, 12)
    f.task.data['graph_runtime'] = state; f.task.status = 'workflow_cancelled'; f.candidates.pop()
    assert tools.receipts(f.db, f.p, f.task) == []
    # A late result never became an adopted completed activation or receipt.
    f.candidates.append(copy.deepcopy(first)); f.candidates[-1]['activation_id'] = 'late_unadopted'
    with pytest.raises(HTTPException): tools.receipts(f.db, f.p, f.task)


@pytest.mark.parametrize('gate', ['acceptance', 'objects', 'release'])
def test_existing_acceptance_and_actual_release_gates_are_not_replaced_by_self_consistent_receipts(monkeypatch, gate):
    f = fixture(monkeypatch)
    def reject(*args, **kwargs): raise HTTPException(409, 'actual frozen authority is unavailable')
    if gate == 'acceptance': monkeypatch.setattr(native_goals, 'verify_workflow_inputs', reject)
    elif gate == 'objects': monkeypatch.setattr(entity_release_compat, 'verify_task_objects', reject)
    else: monkeypatch.setattr(instance_semantic_runtime_dispatch, 'modern', lambda snapshot: NS(
        verify_release=lambda *a, **k: None, BUSINESS_MARKER='full_semantic_graph_origin'))
    with pytest.raises(HTTPException): tools.receipts(f.db, f.p, f.task)


def test_missing_or_damaged_graph_marker_does_not_call_any_legacy_binding(monkeypatch):
    f = fixture(monkeypatch); f.task.data['graph_runtime'] = None
    with pytest.raises(HTTPException): tools.receipts(f.db, f.p, f.task)
