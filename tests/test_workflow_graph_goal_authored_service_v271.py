"""Explicit authored-family SELECT seams, not a persisted execution claim.

The graph kernel, Job/Invocation matching and completion receipt checks stay
real. Database/release reads and the separately tested saved-receipt helper are
declared read-only spies; no report source or nested translation is invented.
"""
import copy
import socket
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest

from generic_agents import entity_release_compat, instance_semantic_runtime_dispatch, native_goals
from generic_agents import workflow_graph, workflow_graph_goal_tools_v126 as subject, workspace_api
from generic_agents import workflow_service_v195 as service
from generic_agents import authored_service_workflow_store_v270 as authored
from generic_agents.contracts import digest
from generic_agents.models import Invocation, Job
from tests.test_authored_service_workflow_v270 import cold_binding, command
from tests.test_full_semantic_graph_v1 import base, control, edge, guards, object_schema


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail('goal acceptance may not perform network IO')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)


def test_context_uses_actual_runtime_marker_and_historical_release_check(monkeypatch):
    binding = cold_binding(command())
    p = NS(user_id='reader', workspace_id='workspace')
    business = {'id': 'business', 'name': binding['business_name'], 'flow': binding['flow'],
        'execution_mode': 'confirmed_graph_workflow',
        'authored_workflow_origin': {'adoption_id': 'explicit-read-spy'}}
    release = NS(workspace_id=p.workspace_id, package_id='package',
        data={'snapshot': {'businesses': [business]}})
    calls = []

    def verify(db, principal, actual, *, live):
        assert principal is p and actual is release and live is False
        calls.append('historical_release')
        return {'verified_by_declared_read_spy': True}

    monkeypatch.setattr(instance_semantic_runtime_dispatch, 'modern', lambda value: NS(
        BUSINESS_MARKER='authored_workflow_origin', verify_release=verify))
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *args: copy.deepcopy(binding))
    ids = [pin['capability_id'] for pin in binding['capability_pins'].values()]
    monkeypatch.setattr(subject, 'published', lambda *args: NS(data={'dependencies': ids}))
    actual, graph, deps = subject._context(object(), p, release, business)
    assert actual == binding and graph == binding['graph'] and deps == set(ids)
    assert calls == ['historical_release'] and 'full_semantic_graph_origin' not in business
    business.pop('authored_workflow_origin')
    with pytest.raises(HTTPException):
        subject._context(object(), p, release, business)


def test_catalog_rebuilds_authored_binding_and_explicit_services_without_translation(monkeypatch):
    binding = cold_binding(command())
    business = {'name': binding['business_name'], 'flow': copy.deepcopy(binding['flow'])}
    probes = []

    def capability(db, p, node, dependencies, *, nested=False, delegation=None):
        probes.append((node['id'], nested, copy.deepcopy(delegation)))
        return NS(id=node['execution']['capability_id'], data={'description': '明确登记的' + node['id']})

    monkeypatch.setattr(subject, '_capability', capability)
    result = subject._catalog(object(), object(), object(), business, binding, binding['graph'], set())
    assert len(result) == 5 and 'translation' not in binding and 'source_design' not in binding
    assert all(strict for _, strict, _ in probes)
    delegated = {key: value for key, _, value in probes if value is not None}
    assert delegated == authored.service_delegations(binding)
    for operation in ('packet', 'reasoning', 'context_reader'):
        assert next(value for key, _, value in probes if key == operation) is None
    changed = copy.deepcopy(binding)
    changed['service_delegations']['resense']['operation'] = 'unregistered_service'
    changed.pop('binding_hash')
    changed = authored.common._seal(changed)
    with pytest.raises(HTTPException):
        subject._catalog(object(), object(), object(), business, changed, changed['graph'], set())


def result_case(monkeypatch, operation='resense_from_context'):
    """Actual kernel completion plus declared DB row readers, no saved grant."""
    p = NS(workspace_id='workspace', user_id='independent_goal_reviewer')
    inputs, output = {}, {'result': '已保留原收件输出'}
    data = {'description': '明确登记的只读验收来源',
        'execution': {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': operation, 'side_effect': True},
        'input_schema': object_schema(), 'output_schema': object_schema(result={'type': 'string'}),
        'allowed_agent_roles': ['business'], 'budget': {'max_attempts': 1}}
    cap = NS(id='service_capability', content_hash=digest(data), validated_hash=digest(data),
        data=data, release_version='1.0.0')
    execution = {'capability_id': cap.id, 'capability_hash': cap.content_hash, 'agent_role': 'business',
        'input_bindings': {}, 'arguments': {}, 'input_schema': data['input_schema'],
        'output_schema': data['output_schema'], 'adapter': 'workflow_service',
        'release_version': cap.release_version, 'max_attempts': 1}
    node = {'id': 'resense', 'kind': 'ability', 'source_step_id': None,
        'guards': guards(), 'actor_id': 'original_node_executor', 'execution': execution}
    graph = base([node, control('end', 'end')], [edge('resense', 'end')], 'resense', ['resense'])
    state = subject.pure.new_run(graph, {}, 10)
    activation_id = state['eligible_ability_ids'][0]
    activation = state['activations'][activation_id]
    key = 'graph:task:' + activation_id + ':1'
    job_id, call_id, lease = 'original_job', 'original_invocation', 'original_dispatch_lease'
    job = NS(id=job_id, kind=workflow_graph.KIND, status='succeeded', idempotency_key=key,
        workspace_id=p.workspace_id, data={'task_id': 'task', 'activation_id': activation_id,
            'actor_id': node['actor_id'], 'graph_hash': state['graph_hash'], 'cancel_epoch': activation['epoch'],
            'activation_version': 0, 'invocation_key': key, 'inputs': inputs,
            'input_hash': digest(inputs), 'attempt': 1})
    transport = {'job_id': job_id, 'invocation_key': key, 'input_hash': digest(inputs),
        'cancel_epoch': activation['epoch'], 'attempt': 1, 'phase': 'succeeded',
        'dispatch_committed': True, 'invocation_id': call_id, 'output_hash': digest(output),
        'dispatch_input_hash': digest(inputs), 'dispatch_ownership': {'job_id': job_id, 'lease_token': lease},
        'dispatch_authorization': {'epoch': activation['epoch'], 'job_id': job_id, 'lease_token': lease,
            'input_hash': digest(inputs), 'capability_hash': cap.content_hash, 'actor_id': node['actor_id'],
            'authorized_at': '2026-11-01T12:00:01+00:00'}}
    task = NS(id='task', workspace_id=p.workspace_id, data={'graph_jobs': {activation_id: transport}})
    approval = service.make_approval(task, job, node, inputs)
    call = NS(id=call_id, workspace_id=p.workspace_id, capability_id=cap.id,
        idempotency_key=key, status='succeeded', data={'job_id': job_id, 'lease_token': lease,
            'input_hash': digest(inputs), 'role': 'business', 'capability_hash': cap.content_hash,
            'approval': approval, 'side_effect': True, 'output': copy.deepcopy(output),
            'started_at': '2026-11-01T12:00:00+00:00'})
    receipt = {'request_id': job_id, 'step_id': node['id'], 'activation_id': activation_id,
        'actor': node['actor_id'], 'at': '2026-11-01T12:00:02+00:00',
        'execution_mode': 'registered_graph_ability', 'invocation_id': call_id, 'capability_id': cap.id,
        'capability_hash': cap.content_hash, 'release_version': cap.release_version,
        'input_snapshot': inputs, 'input_hash': digest(inputs), 'output': copy.deepcopy(output),
        'entity_state_updated': False, 'candidate_only': True}
    state = subject.pure.complete(graph, state, activation_id, output, job_id,
        node['actor_id'], activation['version'], activation['epoch'], state['clock'] + 1)
    task.data['graph_runtime'] = state
    binding = {'schema': subject._AUTHORED_SERVICE_BINDING, 'graph': graph}
    probes = []

    class ReadOnlyDB:
        def add(self, *args): pytest.fail('goal tool wrote a row')
        def flush(self): pytest.fail('goal tool flushed')
        def commit(self): pytest.fail('goal tool committed')
        def execute(self, *args): pytest.fail('goal tool attempted undeclared IO')

    db = ReadOnlyDB()

    def scoped(database, model, identity, principal):
        assert database is db and principal is p
        row = job if model is Job else call if model is Invocation else None
        assert row is not None and row.id == identity
        probes.append(('row', model))
        return row

    monkeypatch.setattr(subject, 'scoped', scoped)
    return NS(db=db, p=p, task=task, graph=graph, state=state, nodes={'resense': node},
        cap=cap, job=job, call=call, receipt=receipt, key=key, approval=approval,
        output=output, binding=binding, probes=probes)


def actual(case, **options):
    return subject._actual_result(case.db, case.p, case.task, case.graph, case.state,
        case.nodes, case.receipt, {'capability': case.cap}, authored=True, binding=case.binding, **options)


def test_resense_reads_only_saved_original_after_strict_provenance_and_keeps_reader_identity(monkeypatch):
    from generic_agents import workflow_resense_goal_receipts_v271 as helper
    from generic_agents import workflow_registered_tool_plan_v201 as plans
    c = result_case(monkeypatch)

    def saved(db, p, task, job, call, cap, inputs, key, approval, binding):
        assert db is c.db and p is c.p and task is c.task and job is c.job and call is c.call
        assert cap is c.cap and inputs == c.receipt['input_snapshot'] and key == c.key
        assert approval == c.approval and binding is c.binding
        assert p.user_id != c.nodes['resense']['actor_id']
        assert ('row', Job) in c.probes and ('row', Invocation) in c.probes
        c.probes.append(('saved_resense',))
        return copy.deepcopy(c.output)

    monkeypatch.setattr(helper, 'saved_receipt', saved)
    monkeypatch.setattr(service, 'execute', lambda *args, **kwargs: pytest.fail('acceptance dispatched a service'))
    monkeypatch.setattr(plans, 'reconcile', lambda *args, **kwargs: pytest.fail('acceptance continued a plan'))
    value, reaped = actual(c)
    assert reaped and value['output'] == c.output and ('saved_resense',) in c.probes


@pytest.mark.parametrize('fault', ['unknown', 'input', 'output', 'lease', 'approval', 'authorization'])
def test_incomplete_or_mismatched_invocation_never_reaches_saved_resense_helper(monkeypatch, fault):
    from generic_agents import workflow_resense_goal_receipts_v271 as helper
    c = result_case(monkeypatch)
    monkeypatch.setattr(helper, 'saved_receipt', lambda *args: pytest.fail('bad provenance reached helper'))
    if fault == 'unknown': c.call.status = 'unknown'
    elif fault == 'input': c.call.data['input_hash'] = '0' * 64
    elif fault == 'output': c.call.data['output'] = {'result': '另一个结果'}
    elif fault == 'lease': c.call.data['lease_token'] = 'another-dispatch-lease'
    elif fault == 'approval': c.call.data['approval'] = None
    else: c.task.data['graph_jobs'][c.receipt['activation_id']]['dispatch_authorization']['actor_id'] = 'another-actor'
    with pytest.raises(HTTPException):
        actual(c)


def test_saved_resense_output_must_equal_exact_original_receipt(monkeypatch):
    from generic_agents import workflow_resense_goal_receipts_v271 as helper
    c = result_case(monkeypatch)
    monkeypatch.setattr(helper, 'saved_receipt', lambda *args: {'result': '替换输出'})
    with pytest.raises(HTTPException):
        actual(c)


def test_unknown_service_operation_is_not_a_generic_saved_receipt_bypass(monkeypatch):
    c = result_case(monkeypatch, operation='not_installed')
    with pytest.raises(HTTPException):
        actual(c)


def test_v201_keeps_its_existing_saved_receipt_handler(monkeypatch):
    from generic_agents import workflow_registered_tool_plan_v201 as plans
    c = result_case(monkeypatch, operation=plans.OP)
    seen = []

    def saved(db, p, cap, inputs, key, approval, call):
        seen.append((db, p, cap, inputs, key, approval, call))
        return copy.deepcopy(c.output)

    monkeypatch.setattr(plans, 'saved_receipt', saved)
    assert actual(c)[1] is True
    assert seen == [(c.db, c.p, c.cap, c.receipt['input_snapshot'], c.key, c.approval, c.call)]


def test_even_unlisted_authored_node_must_finish_its_original_job_before_graph_acceptance(monkeypatch):
    from generic_agents import workflow_resense_goal_receipts_v271 as helper
    c = result_case(monkeypatch)
    c.job.status = 'running'
    c.task.status = 'workflow_completed'
    c.task.data.update(workflow_runtime={'schema': 'workflow-graph-projection-v1',
        'current_step_id': None, 'receipts': [c.receipt]}, object_snapshots=[], goal_spec={'criteria': []})
    release, business = object(), object()
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *args: (c.task, release, business))
    monkeypatch.setattr(subject, '_context', lambda *args: (c.binding, c.graph, {c.cap.id}))
    monkeypatch.setattr(subject, '_catalog', lambda *args: {c.cap.id: {'capability': c.cap}})
    monkeypatch.setattr(native_goals, 'verify_workflow_inputs', lambda *args, **kwargs: None)
    monkeypatch.setattr(entity_release_compat, 'verify_task_objects', lambda *args, **kwargs: None)
    monkeypatch.setattr(helper, 'saved_receipt', lambda *args: copy.deepcopy(c.output))
    with pytest.raises(HTTPException, match='Job'):
        subject.receipts(c.db, c.p, c.task)
