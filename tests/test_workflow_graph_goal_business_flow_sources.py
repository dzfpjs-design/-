"""New-source catalog and real graph reductions at explicit SELECT boundaries.

The converter, binding validator, graph kernel, invocation and receipt checks
are real.  Release/adoption authority and DB selection are declared IO spies;
this does not claim a persisted release, device result or PostgreSQL journey.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException

from generic_agents import workflow_graph_goal_tools_v126 as tools
from generic_agents import workflow_graph, workspace_api, native_goals, entity_release_compat
from generic_agents import instance_semantic_runtime_dispatch as dispatch
from generic_agents import business_flow_execution_adoption as adoption
from generic_agents import business_flow_execution_graph as converter
from generic_agents.contracts import digest
from generic_agents.models import Entity, Invocation, Job
from tests.test_business_flow_execution_adoption import fixture as binding_fixture
from tests.test_business_flow_execution_graph import bindings


def uid(label):
    return str(uuid5(NAMESPACE_URL, 'public-goal-business-flow:' + label))


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('pure graph goal adapters cannot perform external IO')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    monkeypatch.setattr(subprocess, 'Popen', deny)


def source_binding(with_ability):
    b = binding_fixture(2)
    source = b['source']; value = source['qualification']['value']
    flow = value['business']['flows'][0]
    flow['trigger']['kind'] = 'event'
    flow['trigger']['description'] = '按已明确授权的观测异常发起核查。'
    flow['nodes'][0]['name'] = '核对观测指标并形成处置意见'
    if with_ability:
        flow['nodes'][0]['actor'] = 'data_agent'
    supplied = bindings(value)
    supplied['schema'] = converter.BINDINGS_V2
    supplied['trigger'] = {'mode': converter.EVENT_TRIGGER_MODE}
    actor = next(iter(b['role_bindings'].values()))['user_id']
    for item in supplied['nodes'].values():
        item['actor_id'] = actor
    cap = None
    if with_ability:
        execution = supplied['nodes'][flow['nodes'][0]['node_id']]['execution']
        data = {'description': '登记的本地指标整理能力', 'execution': {'adapter': 'builtin', 'side_effect': False},
            'input_schema': copy.deepcopy(execution['input_schema']),
            'output_schema': copy.deepcopy(execution['output_schema']),
            'allowed_agent_roles': ['data'], 'budget': {'max_attempts': 1}}
        cap = NS(id=uid('capability'), workspace_id=uid('workspace'), kind='tool', status='published',
            data=data, content_hash=digest(data), validated_hash=digest(data), release_version='1.0.0')
        execution.update(capability_id=cap.id, capability_hash=cap.content_hash)
    translated = converter.build_execution_graph(value, b['flow_id'], supplied,
        scope_binding=source['scope_binding'])
    assert not translated['issues'] and translated['graph'] is not None
    b.update(flow=adoption.derive_flow(source, b['flow_id']), graph=translated['graph'],
        execution_bindings=supplied, translation=translated,
        capability_pins={n['id']: copy.deepcopy(n['execution']) for n in translated['graph']['nodes']
            if n['kind'] == 'ability'})
    b['node_actions'] = {key: 'data' for key in b['capability_pins']}
    b = adoption.seal({key: val for key, val in b.items() if key != 'binding_hash'}, 'binding_hash')
    assert adoption.check_binding(b) == b
    return b, cap


def case(monkeypatch, *, with_ability=False):
    binding, cap = source_binding(with_ability)
    graph = binding['graph']; actor = next(iter(binding['role_bindings'].values()))['user_id']
    principal = NS(workspace_id=uid('workspace'), user_id=uid('goal-owner'))
    business = {'id': 'business_' + 'a' * 16, 'name': binding['business_name'],
        'flow': copy.deepcopy(binding['flow']), 'execution_mode': 'confirmed_graph_workflow',
        'full_semantic_graph_origin': {'synthetic_authority_boundary': True}}
    release = NS(id=uid('release'), instance_id=uid('instance'), workspace_id=principal.workspace_id,
        package_id=uid('package'), data={'snapshot': {'businesses': [business]}})
    data = {'dependencies': [cap.id] if cap else []}
    package = NS(id=release.package_id, workspace_id=principal.workspace_id, kind='domain_package',
        status='published', data=data, content_hash=digest(data), validated_hash=digest(data))
    rows = {}; probes = []; state = tools.pure.new_run(graph, {'source_input': '已保存的公开输入'}, 10)
    projection = []; transports = {}
    while state['ready_human_ids'] or state['eligible_ability_ids']:
        activation_id = (state['ready_human_ids'] or state['eligible_ability_ids'])[0]
        activation = copy.deepcopy(state['activations'][activation_id])
        node = next(n for n in graph['nodes'] if n['id'] == activation['node_id'])
        job_id = uid('result:' + activation_id)
        output_schema = node['execution']['output_schema'] if node['kind'] == 'ability' else node['output_schema']
        output = {field: '已保存原节点回执' for field in output_schema['required']}
        receipt = {'request_id': job_id, 'step_id': node['source_step_id'], 'activation_id': activation_id,
            'actor': node['actor_id'], 'at': '2026-10-24T00:00:03+00:00',
            'execution_mode': 'human_graph_result', 'output': copy.deepcopy(output), 'entity_state_updated': False}
        if node['kind'] == 'ability':
            inputs = tools.pure.ability_inputs(graph, state, activation_id)
            call_id = uid('call:' + activation_id); lease = 'original-lease:' + activation_id
            key = 'graph:' + uid('task') + ':' + activation_id + ':1'
            job = NS(id=job_id, workspace_id=principal.workspace_id, kind=workflow_graph.KIND,
                idempotency_key=key, status='succeeded', lease_token=None,
                data={'task_id': uid('task'), 'activation_id': activation_id, 'actor_id': actor,
                    'graph_hash': state['graph_hash'], 'cancel_epoch': activation['epoch'], 'activation_version': 0,
                    'invocation_key': key, 'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs), 'attempt': 1})
            call = NS(id=call_id, workspace_id=principal.workspace_id, capability_id=cap.id,
                idempotency_key=key, status='succeeded', data={'job_id': job_id, 'lease_token': lease,
                    'input_hash': digest(inputs), 'role': 'data', 'capability_hash': cap.content_hash,
                    'approval': None, 'side_effect': False, 'started_at': '2026-10-24T00:00:00+00:00',
                    'output': copy.deepcopy(output)})
            rows[(Job, job_id)] = job; rows[(Invocation, call_id)] = call
            transports[activation_id] = {'job_id': job_id, 'invocation_key': key, 'input_hash': digest(inputs),
                'cancel_epoch': activation['epoch'], 'attempt': 1, 'phase': 'succeeded', 'dispatch_committed': True,
                'invocation_id': call_id, 'output_hash': digest(output), 'dispatch_input_hash': digest(inputs),
                'dispatch_ownership': {'job_id': job_id, 'lease_token': lease},
                'dispatch_authorization': {'epoch': activation['epoch'], 'job_id': job_id, 'lease_token': lease,
                    'input_hash': digest(inputs), 'capability_hash': cap.content_hash,
                    'actor_id': actor, 'authorized_at': '2026-10-24T00:00:01+00:00'}}
            receipt.update(execution_mode='registered_graph_ability', invocation_id=call_id,
                capability_id=cap.id, capability_hash=cap.content_hash, release_version=cap.release_version,
                input_snapshot=copy.deepcopy(inputs), input_hash=digest(inputs), candidate_only=True)
        state = tools.pure.complete(graph, state, activation_id, output, job_id, node['actor_id'],
            activation['version'], activation['epoch'], state['clock'] + 1)
        projection.append(receipt)
    assert state['status'] == 'succeeded'
    task = NS(id=uid('task'), workspace_id=principal.workspace_id, owner_id=principal.user_id,
        status='workflow_completed', data={'instance_id': release.instance_id,
            'instance_release_id': release.id, 'business_id': business['id'], 'graph_runtime': state,
            'graph_jobs': transports, 'workflow_runtime': {'schema': 'workflow-graph-projection-v1',
                'current_step_id': None, 'receipts': projection}, 'object_snapshots': [],
            'goal_spec': {'criteria': [{'kind': 'capability_success', 'capability_id': cap.id}] if cap else []}})

    class ReadOnlyDB:
        def add(self, *args): pytest.fail('goal source checks must not write')
        def flush(self): pytest.fail('goal source checks must not flush')
        def commit(self): pytest.fail('goal source checks must not commit')
        def execute(self, *args): pytest.fail('SELECT boundaries must use explicit IO spies')

    db = ReadOnlyDB()

    def published(database, p, identity, kind=None):
        assert database is db and p is principal
        row = package if identity == package.id else cap
        probes.append(('published', identity, kind))
        if (row is None or row.id != identity or row.workspace_id != p.workspace_id
                or row.status != 'published' or row.content_hash != digest(row.data)
                or row.validated_hash != row.content_hash or kind is not None and row.kind != kind):
            raise HTTPException(409, 'actual registered capability boundary rejected')
        return row

    def scoped(database, model, identity, p):
        assert database is db and p is principal and model in {Job, Invocation, Entity}
        probes.append(('scoped', model, identity))
        row = rows.get((model, identity))
        if row is None or row.workspace_id != p.workspace_id:
            raise HTTPException(404, 'missing original execution row')
        return row

    def verify_release(database, p, selected, *, live):
        assert database is db and p is principal and selected is release and live is False
        probes.append(('historical_release', live))
        return {'synthetic_verified_release_boundary': True}

    monkeypatch.setattr(tools, 'published', published)
    monkeypatch.setattr(tools, 'scoped', scoped)
    monkeypatch.setattr(dispatch, 'modern', lambda _: NS(BUSINESS_MARKER='full_semantic_graph_origin', verify_release=verify_release))
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *_: copy.deepcopy(binding))
    monkeypatch.setattr(workflow_graph, 'graph_for', lambda *_: copy.deepcopy(graph))
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *_: (task, release, business))
    monkeypatch.setattr(native_goals, 'verify_workflow_inputs', lambda *_, lock_sources=True: probes.append(('acceptance_inputs',)))
    monkeypatch.setattr(entity_release_compat, 'verify_task_objects', lambda *_, lock_sources=True: probes.append(('task_objects',)))
    return NS(binding=binding, graph=graph, cap=cap, package=package, p=principal, db=db, business=business,
        release=release, task=task, rows=rows, probes=probes, projection=projection)


def test_two_human_event_flow_checks_full_new_source_and_returns_no_tool_receipts(monkeypatch):
    c = case(monkeypatch)
    before = copy.deepcopy(c.task.data)
    assert 'source_business' not in c.binding and 'source_design' not in c.binding
    assert tools._catalog(c.db, c.p, c.release, c.business, c.binding, c.graph, set()) == {}
    assert tools.catalog(c.db, c.p, c.release, c.business) == {}
    assert tools.receipts(c.db, c.p, c.task) == []
    assert ('historical_release', False) in c.probes
    assert ('acceptance_inputs',) in c.probes and ('task_objects',) in c.probes
    assert c.task.data == before


def test_ability_catalog_uses_real_source_node_name_and_actual_completed_receipt(monkeypatch):
    c = case(monkeypatch, with_ability=True)
    before = copy.deepcopy(c.task.data)
    entries = tools._catalog(c.db, c.p, c.release, c.business, c.binding, c.graph, {c.cap.id})
    assert entries[c.cap.id]['step_names'] == ['核对观测指标并形成处置意见']
    assert entries[c.cap.id]['capability'] is c.cap and entries[c.cap.id]['sources'] == ['workflow_step']
    result = tools.receipts(c.db, c.p, c.task)
    assert len(result) == 1 and result[0]['capability_hash'] == c.cap.content_hash
    assert result[0]['status'] == 'succeeded' and result[0]['method'] == 'registered_capability'
    assert result[0]['candidate_only'] is True and result[0]['physical_state_verified'] is False
    assert any(item[0] == 'scoped' and item[1] is Job for item in c.probes)
    assert any(item[0] == 'scoped' and item[1] is Invocation for item in c.probes)
    result[0]['output'].clear()
    assert c.task.data == before


@pytest.mark.parametrize('ability', [False, True])
@pytest.mark.parametrize('fault', ['missing_schema', 'unknown_schema', 'missing_source', 'source_node',
    'graph', 'flow', 'business_name', 'extra_pin'])
def test_even_zero_abilities_cannot_skip_source_graph_or_family_checks(monkeypatch, ability, fault):
    c = case(monkeypatch, with_ability=ability)
    if fault == 'missing_schema': c.binding.pop('schema')
    elif fault == 'unknown_schema': c.binding['schema'] = 'future-unregistered-family'
    elif fault == 'missing_source': c.binding.pop('source')
    elif fault == 'source_node': c.binding['flow']['source_flow']['nodes'][0]['name'] = '换掉原步骤'
    elif fault == 'graph': c.graph['nodes'][0]['source_step_id'] = 'not-an-original-node'
    elif fault == 'flow': c.business['flow']['steps'][0]['name'] = '静默换流程'
    elif fault == 'business_name': c.business['name'] = '另一个业务'
    else: c.binding['capability_pins']['unexpected'] = {}
    if 'binding_hash' in c.binding:
        c.binding['binding_hash'] = digest({k: v for k, v in c.binding.items() if k != 'binding_hash'})
    with pytest.raises(HTTPException):
        tools.receipts(c.db, c.p, c.task)


@pytest.mark.parametrize('fault', ['pin_hash', 'unpublished', 'dependency', 'role', 'schema', 'max_attempts'])
def test_capability_pin_publication_dependency_and_schema_remain_required(monkeypatch, fault):
    c = case(monkeypatch, with_ability=True)
    if fault == 'pin_hash':
        next(iter(c.binding['capability_pins'].values()))['capability_hash'] = 'f' * 64
        c.binding['binding_hash'] = digest({k: v for k, v in c.binding.items() if k != 'binding_hash'})
    elif fault == 'unpublished': c.cap.status = 'retired'
    elif fault == 'dependency':
        c.package.data['dependencies'] = []
        c.package.content_hash = c.package.validated_hash = digest(c.package.data)
    else:
        if fault == 'role': c.cap.data['allowed_agent_roles'] = ['business']
        elif fault == 'schema': c.cap.data['output_schema']['required'] = []
        else: c.cap.data['budget']['max_attempts'] = 2
        c.cap.content_hash = c.cap.validated_hash = digest(c.cap.data)
    with pytest.raises(HTTPException):
        tools.receipts(c.db, c.p, c.task)


@pytest.mark.parametrize('fault', ['missing_job', 'missing_call', 'unknown_call', 'candidate_projection',
    'receipt_output', 'input_hash', 'lease', 'receipt_physical'])
def test_candidate_projection_without_original_job_call_and_lease_cannot_satisfy_tool_success(monkeypatch, fault):
    c = case(monkeypatch, with_ability=True)
    receipt = next(item for item in c.projection if item['execution_mode'] == 'registered_graph_ability')
    job = c.rows[(Job, receipt['request_id'])]
    call = c.rows[(Invocation, receipt['invocation_id'])]
    if fault == 'missing_job': c.rows.pop((Job, job.id))
    elif fault == 'missing_call': c.rows.pop((Invocation, call.id))
    elif fault == 'unknown_call': call.status = 'unknown'
    elif fault == 'candidate_projection': receipt['execution_mode'] = 'model_candidate'
    elif fault == 'receipt_output': receipt['output'] = {'forged': '候选不能替代实际输出'}
    elif fault == 'input_hash': call.data['input_hash'] = 'f' * 64
    elif fault == 'lease': call.data['lease_token'] = 'unrelated-new-lease'
    else: receipt['candidate_only'] = False
    before = copy.deepcopy(c.task.data)
    with pytest.raises(HTTPException):
        tools.receipts(c.db, c.p, c.task)
    assert c.task.data == before


@pytest.mark.parametrize('status', ['queued', 'running', 'failed', 'blocked', 'cancelled'])
def test_valid_returned_invocation_waits_for_original_job_to_be_reaped(monkeypatch, status):
    c = case(monkeypatch, with_ability=True)
    job = next(row for (kind, _), row in c.rows.items() if kind is Job)
    job.status = status
    assert tools.receipts(c.db, c.p, c.task) == []
    job.status = 'succeeded'
    assert len(tools.receipts(c.db, c.p, c.task)) == 1
