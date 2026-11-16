"""Actual nested compiler/reducer and cold archive inventory at explicit IO seams.

Publication, release/IAM and service-ledger selection are named boundaries, not
PostgreSQL, a real 175 approval, device execution or independent acceptance.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import workflow_graph_goal_tools_v126 as tools
from generic_agents import business_flow_nested_adoption_v195 as nested
from generic_agents import workflow_graph, workflow_service_v195 as service
from generic_agents import workflow_registered_tool_plan_v201 as plans
from generic_agents import workflow_task_packet_v199 as packet
from generic_agents import workflow_task_archive_v198 as archive
from generic_agents import business_reasoning_v197 as reasoning
from generic_agents.contracts import digest
from generic_agents.models import Audit, Invocation, Job, Task, CloudCall
from tests.test_business_flow_nested_adoption_v195 import fixture, reseal, identity
from tests.test_business_reasoning_v197 import manifest
from tests.test_full_semantic_graph_v1 import base, control, edge, guards, object_schema
from tests.test_report_business_flow_wire_v184 import no_external
from tests.test_workflow_task_archive_v198 import scene as archive_scene
from tests.test_business_reasoning_v197 import scene as reasoning_scene, install as reasoning_io


def forbidden(*args, **kwargs):
    raise AssertionError('historical Goal checks cannot dispatch, reconcile or commit')


@pytest.fixture(autouse=True)
def no_dispatch(monkeypatch):
    from generic_agents import gateway, connectors
    monkeypatch.setattr(gateway, 'invoke', forbidden)
    monkeypatch.setattr(connectors, 'query_receipt', forbidden)
    monkeypatch.setattr(service, 'reconcile', forbidden)
    monkeypatch.setattr(plans, 'reconcile', forbidden)
    monkeypatch.setattr(packet, 'reconcile', forbidden)
    monkeypatch.setattr(workflow_graph, 'live', forbidden)


def catalog_case(monkeypatch, *, service_node=False):
    """Actual recompiled nested source; only registry row selection is a spy."""
    binding = fixture(service=service_node)
    p = NS(user_id=identity('reviewer'), workspace_id=identity('workspace'), role='operator')
    caps = {}
    for path, row in binding['execution_bindings']['nodes'].items():
        if row['kind'] != 'ability':
            continue
        spec = row['execution']; role = spec['agent_role']
        execution = {'adapter': 'builtin', 'side_effect': False}
        if 'service_actor' in row:
            execution = {'adapter': 'workflow_service', 'side_effect': True,
                'service_actor': row['service_actor'], 'operation': row['operation']}
        data = {**manifest(), 'kind': 'tool', 'name': 'source_' + str(len(caps)),
            'description': '公开合成的实际发布边界', 'allowed_agent_roles': [role],
            'execution': execution, 'input_schema': copy.deepcopy(spec['input_schema']),
            'output_schema': copy.deepcopy(spec['output_schema']), 'budget': {'max_attempts': 1}}
        cap = NS(id=identity(path), workspace_id=p.workspace_id, data=data, content_hash=digest(data),
            validated_hash=digest(data), kind='tool', status='published', release_version='1.0.0')
        caps[cap.id] = cap
        spec.update(capability_id=cap.id, capability_hash=cap.content_hash)
    translated = nested.compiler.build_execution_graph(binding['source']['qualification']['value'], 0,
        binding['execution_bindings'], scope_binding=binding['source']['scope_binding'],
        causal_bindings=binding['causal_bindings'])
    binding.update(translation=translated, graph=translated['graph'],
        flow=nested.derive_flow(binding['source'], 0, translated),
        capability_pins={node['id']: copy.deepcopy(node['execution']) for node in translated['graph']['nodes']
            if node['kind'] == 'ability'})
    binding = reseal(binding); assert nested.check_binding(binding) == binding
    business = {'name': binding['business_name'], 'flow': copy.deepcopy(binding['flow'])}
    db = NS(); probes = []
    def published(database, principal, key):
        assert database is db and principal is p
        probes.append(key); row = caps[key]
        if row.status != 'published':
            raise HTTPException(409, 'actual publication boundary')
        return row
    monkeypatch.setattr(tools, 'published', published)
    return NS(db=db, p=p, binding=binding, business=business, graph=binding['graph'], caps=caps, probes=probes)


def catalog(s):
    return tools._catalog(s.db, s.p, None, s.business, s.binding, s.graph, set(s.caps))


def test_actual_nested_catalog_uses_recursive_original_action_names_and_service_delegation(monkeypatch):
    s = catalog_case(monkeypatch, service_node=True); before = copy.deepcopy(s.binding)
    result = catalog(s)
    mapping = s.binding['translation']['source_mapping']['actions']
    for row in mapping:
        node = next(node for node in s.graph['nodes'] if node['id'] == row['graph_node_id'])
        if node['kind'] == 'ability':
            assert result[node['execution']['capability_id']]['step_names'] == [row['source_action']['name']]
    assert any(cap.data['execution']['side_effect'] is True for cap in s.caps.values())
    assert s.binding == before and set(s.probes) == set(s.caps)


def acceptance_boundaries(monkeypatch, s, binding, *, completed=True):
    from generic_agents import workspace_api, native_goals, entity_release_compat
    release = NS()
    business = copy.deepcopy(s.business) if hasattr(s, 'business') else {}
    s.task.status = 'workflow_completed' if completed else 'workflow_waiting'
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *_: (s.task, release, business))
    monkeypatch.setattr(tools, '_context', lambda *_: (binding, s.graph, set()))
    checks = []
    monkeypatch.setattr(native_goals, 'verify_workflow_inputs',
        lambda *_, lock_sources=True: checks.append(('source', lock_sources)))
    monkeypatch.setattr(entity_release_compat, 'verify_task_objects',
        lambda *_, lock_sources=True: checks.append(('objects', lock_sources)))
    return checks


def test_initial_goal_before_first_tool_receipt_uses_actual_nested_catalog_and_is_not_false_pending(monkeypatch):
    s = catalog_case(monkeypatch, service_node=True)
    s.task = NS(id=identity('initial-task'), workspace_id=s.p.workspace_id, data={
        'graph_runtime': tools.pure.new_run(s.graph, {}, 10), 'object_snapshots': [],
        'workflow_runtime': {'schema': 'workflow-graph-projection-v1', 'current_step_id': None, 'receipts': []},
        'goal_spec': {'criteria': []}})
    acceptance_boundaries(monkeypatch, s, s.binding, completed=False)
    # Release/Task lookup is the declared IO boundary; _catalog still invokes
    # the real nested source/compiler and every current capability gate.
    monkeypatch.setattr(tools, '_context', lambda *_: (s.binding, s.graph, set(s.caps)))
    assert tools.receipts(s.db, s.p, s.task, lock_sources=False) == []
    assert set(s.probes) == set(s.caps)


@pytest.mark.parametrize('fault', ['source', 'path', 'name', 'graph', 'pins', 'unknown_schema'])
def test_resealed_source_translation_or_inventory_changes_fail_before_registry_io(monkeypatch, fault):
    s = catalog_case(monkeypatch)
    if fault == 'source': s.binding.pop('source')
    elif fault == 'path': s.binding['translation']['source_mapping']['actions'][0]['ast_path'] += '/other'
    elif fault == 'name': s.binding['translation']['source_mapping']['actions'][0]['source_action']['name'] = '伪造原名'
    elif fault == 'graph': s.binding['graph']['nodes'][0]['source_step_id'] = 'unrelated'
    elif fault == 'pins': s.binding['capability_pins'].pop(next(iter(s.binding['capability_pins'])))
    else: s.binding['schema'] = 'unknown-nested-binding'
    s.binding = reseal(s.binding)
    with pytest.raises(HTTPException): catalog(s)
    assert not s.probes


@pytest.mark.parametrize('fault', ['read_only_disguise', 'wrong_actor', 'retired', 'dependency'])
def test_service_eligibility_is_real_and_does_not_relax_legacy_read_only_catalog(monkeypatch, fault):
    s = catalog_case(monkeypatch, service_node=True)
    node = next(node for node in s.graph['nodes'] if node.get('execution', {}).get('adapter') == 'workflow_service')
    cap = s.caps[node['execution']['capability_id']]
    if fault == 'read_only_disguise': cap.data['execution']['side_effect'] = False
    elif fault == 'wrong_actor': cap.data['execution']['service_actor'] = 'execution_service'
    elif fault == 'retired': cap.status = 'retired'
    else: s.caps.pop(cap.id)
    with pytest.raises(HTTPException): catalog(s)
    with pytest.raises(HTTPException): tools._capability(s.db, s.p, node, set(s.caps))


def result_case(monkeypatch, adapter):
    """Actual kernel completion/Job/Invocation; ledger reads are explicit spies."""
    p = NS(workspace_id=identity('workspace'), user_id=identity('independent-acceptor'), role='operator')
    actor = identity('node-executor'); task_id = identity('completed-task')
    data = {**manifest(), 'kind': 'tool', 'description': '已登记的原节点', 'allowed_agent_roles': ['data'],
        'execution': {'adapter': adapter, 'side_effect': True}, 'input_schema': object_schema(),
        'output_schema': object_schema(result={'type': 'string'}), 'budget': {'max_attempts': 1}}
    output = {'result': '真实节点已保存的候选产物'}
    if adapter == 'workflow_service':
        data['allowed_agent_roles'] = ['business']
        data['execution'].update(service_actor='execution_service', operation=plans.OP)
    if adapter == 'task_evidence_packet':
        data['output_schema'] = object_schema(context_result={'type': 'object'})
        output = {'context_result': {'original_context': '历史原包，不授予新执行权限'}}
    cap = NS(id=identity('actual-capability'), workspace_id=p.workspace_id, data=data,
        content_hash=digest(data), validated_hash=digest(data), release_version='1.0.0', kind='tool', status='published')
    node = {'id': 'actual_node', 'source_step_id': 'actual_node', 'kind': 'ability', 'actor_id': actor,
        'guards': guards(), 'execution': {'capability_id': cap.id, 'capability_hash': cap.content_hash,
            'adapter': adapter, 'agent_role': data['allowed_agent_roles'][0], 'input_bindings': {}, 'arguments': {},
            'input_schema': data['input_schema'], 'output_schema': data['output_schema'], 'max_attempts': 1,
            'release_version': '1.0.0'}}
    graph = tools.pure.validate_graph(base([node, control('end', 'end')], [edge(node['id'], 'end')],
        node['id'], [node['id']]))
    state = tools.pure.new_run(graph, {}, 10); activation = state['activations'][state['eligible_ability_ids'][0]]
    aid = activation['id']; job_id = identity('original-job'); call_id = identity('original-call')
    key = 'graph:' + task_id + ':' + aid + ':1'; lease = 'original-dispatch-token'
    job = NS(id=job_id, workspace_id=p.workspace_id, kind=workflow_graph.KIND, status='succeeded',
        idempotency_key=key, data={'task_id': task_id, 'actor_id': actor, 'activation_id': aid,
            'activation_version': 0, 'graph_hash': state['graph_hash'], 'cancel_epoch': 0, 'attempt': 1,
            'invocation_key': key, 'inputs': {}, 'input_hash': digest({})})
    task = NS(id=task_id, workspace_id=p.workspace_id, data={})
    module = {'workflow_service': service, 'task_evidence_packet': packet, 'task_archive': archive}[adapter]
    approval = module.make_approval(task, job, node, {})
    call = NS(id=call_id, workspace_id=p.workspace_id, capability_id=cap.id, idempotency_key=key,
        status='succeeded', data={'job_id': job.id, 'role': node['execution']['agent_role'],
            'capability_hash': cap.content_hash, 'input_hash': digest({}), 'lease_token': lease,
            'approval': approval, 'side_effect': True, 'output': copy.deepcopy(output),
            'started_at': '2026-10-24T00:00:00+00:00'})
    receipt = {'request_id': job.id, 'step_id': node['id'], 'activation_id': aid, 'actor': actor,
        'at': '2026-10-24T00:00:02+00:00', 'execution_mode': 'registered_graph_ability',
        'invocation_id': call.id, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'release_version': '1.0.0', 'input_snapshot': {}, 'input_hash': digest({}), 'output': copy.deepcopy(output),
        'candidate_only': True, 'entity_state_updated': False}
    transport = {'job_id': job.id, 'invocation_key': key, 'input_hash': digest({}), 'cancel_epoch': 0,
        'attempt': 1, 'phase': 'succeeded', 'dispatch_committed': True, 'invocation_id': call.id,
        'output_hash': digest(output), 'dispatch_input_hash': digest({}),
        'dispatch_ownership': {'job_id': job.id, 'lease_token': lease},
        'dispatch_authorization': {'job_id': job.id, 'lease_token': lease, 'input_hash': digest({}),
            'capability_hash': cap.content_hash, 'epoch': 0, 'actor_id': actor,
            'authorized_at': '2026-10-24T00:00:01+00:00'}}
    state = tools.pure.complete(graph, state, aid, output, job.id, actor, activation['version'], 0, 11)
    assert tools.pure.check_run(graph, state) == state
    task.data = {'graph_runtime': state, 'graph_jobs': {aid: transport}}
    rows = {(Job, job.id): job, (Invocation, call.id): call}
    def scoped(db, cls, key, principal):
        assert principal is p
        if (cls, key) not in rows: raise HTTPException(404, 'missing historical original')
        return rows[cls, key]
    monkeypatch.setattr(tools, 'scoped', scoped)
    reads = []
    def saved(db, principal, actual_cap, inputs, original_key, original_approval, original_call):
        assert principal is p and p.user_id != actor and actual_cap is cap and inputs == {}
        assert original_key == key and original_approval == approval and original_call is call
        reads.append('full_plan_inventory'); return copy.deepcopy(output)
    def packet_read(db, principal, actual_call):
        assert principal is p and actual_call is call
        reads.append('full_packet_inventory'); return copy.deepcopy(output['context_result'])
    def archive_read(db, principal, actual_call):
        assert principal is p and actual_call is call
        reads.append('full_archive_inventory'); return copy.deepcopy(output)
    monkeypatch.setattr(plans, 'saved_receipt', saved)
    monkeypatch.setattr(packet, 'read_original_context', packet_read)
    monkeypatch.setattr(archive, 'read_original_receipt', archive_read)
    return NS(db=NS(), p=p, task=task, graph=graph, state=state, nodes={n['id']: n for n in graph['nodes']},
        receipt=receipt, entry={'capability': cap}, call=call, job=job, rows=rows, reads=reads, approval=approval)


def actual(s):
    return tools._actual_result(s.db, s.p, s.task, s.graph, s.state, s.nodes, s.receipt, s.entry, nested=True)


@pytest.mark.parametrize('adapter', ['workflow_service', 'task_evidence_packet', 'task_archive'])
def test_actual_original_approval_and_full_history_are_checked_under_independent_reviewer(monkeypatch, adapter):
    s = result_case(monkeypatch, adapter); before = copy.deepcopy(s.call.data)
    value, reaped = actual(s)
    assert reaped is True and value['output'] == s.receipt['output'] and len(s.reads) == 1
    assert s.call.data == before
    s.call.data['approval'] = None
    with pytest.raises(HTTPException): actual(s)
    assert len(s.reads) == 1  # No inventory or recovery after invalid approval.


@pytest.mark.parametrize('fault', ['missing_job', 'missing_call', 'missing_inventory', 'unsupported_service'])
def test_a_completed_projection_never_substitutes_for_original_inventory_or_dispatch_proof(monkeypatch, fault):
    s = result_case(monkeypatch, 'workflow_service')
    if fault == 'missing_job': s.rows.pop((Job, s.job.id))
    elif fault == 'missing_call': s.rows.pop((Invocation, s.call.id))
    elif fault == 'missing_inventory':
        def missing(*args): raise HTTPException(409, 'original Plan/Items/Audit inventory missing')
        monkeypatch.setattr(plans, 'saved_receipt', missing)
    else: s.entry['capability'].data['execution']['operation'] = 'resense_with_receipt'
    with pytest.raises(HTTPException): actual(s)
    assert not s.reads


@pytest.mark.parametrize('status', ['running', 'queued', 'failed', 'blocked', 'cancelled'])
def test_unrequired_completed_ability_still_needs_original_job_success_before_whole_graph_acceptance(monkeypatch, status):
    s = result_case(monkeypatch, 'workflow_service')
    s.task.data.update(workflow_runtime={'schema': 'workflow-graph-projection-v1', 'current_step_id': None,
        'receipts': [s.receipt]}, object_snapshots=[], goal_spec={'criteria': []})
    checks = acceptance_boundaries(monkeypatch, s, {'schema': nested.BINDING})
    monkeypatch.setattr(tools, '_catalog', lambda *_: {s.entry['capability'].id: s.entry})
    s.job.status = status
    with pytest.raises(HTTPException) as caught: tools.receipts(s.db, s.p, s.task, lock_sources=False)
    assert caught.value.status_code == 409 and '尚未成功收尾' in caught.value.detail
    assert checks == [('source', False), ('objects', False)]
    assert len(s.reads) == 1
    s.job.status = 'succeeded'
    assert tools.receipts(s.db, s.p, s.task, lock_sources=False) == []  # No named criterion, complete inventory verified.


def stored_archive(monkeypatch):
    s = archive_scene(monkeypatch)
    response = archive.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    s.call.status = 'succeeded'
    s.call.data = {**s.call.data, 'output': copy.deepcopy(response), 'capability_snapshot': copy.deepcopy(s.cap.data)}
    s.job.idempotency_key = s.key
    aid = s.job.data['activation_id']
    s.task.data = {**s.task.data, 'graph_jobs': {**s.task.data['graph_jobs'], aid: {
        'job_id': s.job.id, 'invocation_id': s.call.id, 'invocation_key': s.key}}}
    reviewer = NS(user_id=identity('independent-archive-reviewer'), workspace_id=s.p.workspace_id, role='operator')
    return s, response, reviewer


def test_archive_original_receipt_cold_checks_real_saved_versions_refs_and_audit_across_actor(monkeypatch):
    s, response, reviewer = stored_archive(monkeypatch)
    before = len(s.db.added); call_before = copy.deepcopy(s.call.data)
    monkeypatch.setattr(archive, 'validate_call_approval', forbidden)
    assert reviewer.user_id != s.job.data['actor_id']
    assert archive.read_original_receipt(s.db, reviewer, s.call) == response
    assert len(s.db.added) == before and s.db.commits == 0 and s.call.data == call_before
    # Historical Goal reads did not broaden the existing original executor's
    # reconcile permission to an unrelated reviewer.
    with pytest.raises(HTTPException): archive.reconcile(s.db, reviewer, s.cap, s.inputs, s.key, s.approval, s.call)


@pytest.mark.parametrize('fault', ['command_actor', 'body', 'missing_refs', 'missing_audit', 'output', 'job_actor'])
def test_archive_history_missing_or_tampered_inventory_cannot_be_reconstructed_or_written(monkeypatch, fault):
    s, _, reviewer = stored_archive(monkeypatch)
    command = s.db.rows[archive.Command][0]
    if fault == 'command_actor': command.actor_id = reviewer.user_id
    elif fault == 'body': command.exact_body += ' '
    elif fault == 'missing_refs': s.db.rows[archive.Reference].clear()
    elif fault == 'missing_audit': s.db.rows[Audit] = [row for row in s.db.rows[Audit] if row.action != archive.SAVED]
    elif fault == 'output': s.call.data = {**s.call.data, 'output': {**s.call.data['output'], 'version': 99}}
    else: s.job.data = {**s.job.data, 'actor_id': reviewer.user_id}
    before = len(s.db.added)
    monkeypatch.setattr(archive, 'validate_call_approval', forbidden)
    with pytest.raises(HTTPException): archive.read_original_receipt(s.db, reviewer, s.call)
    assert len(s.db.added) == before and s.db.commits == 0


def captured_reasoning(monkeypatch):
    s = reasoning_scene()
    s.job.kind = workflow_graph.KIND; s.job.idempotency_key = s.key
    s.job.data.update(graph_hash='a' * 64, cancel_epoch=0)
    aid = s.job.data['activation_id']
    s.task.data = {'graph_runtime': {'graph_hash': 'a' * 64,
        'activations': {aid: {'node_id': s.node['id'], 'epoch': 0}}},
        'graph_jobs': {aid: {'job_id': s.job.id, 'invocation_id': s.call.id}}}
    s.scope = {'schema': tools.graph_scope.SCHEMA, 'task_id': s.task.id, 'graph_hash': 'a' * 64,
        'activation_id': aid, 'node_id': s.node['id'], 'cancel_epoch': 0, 'invocation_key': s.key,
        'input_hash': digest(s.inputs), 'capability_id': s.cap.id, 'capability_hash': s.cap.content_hash}
    env = reasoning_io(monkeypatch, s)
    output = reasoning.execute(env.db, s.p, s.cap, s.inputs, s.key)
    # The old public cloud spy uses an arbitrary reservation. This new ledger
    # reader fixture represents the actual transport's frozen token bound and
    # its conservative settlement when the reply supplies no usage total.
    s.ledger.reserved_tokens = s.call.data[reasoning.STATE_KEY]['request']['prepared']['token_bound']
    s.ledger.charged_tokens = s.ledger.reserved_tokens
    s.call.status = 'succeeded'
    s.call.data = {**s.call.data, 'side_effect': False, 'approval': None, 'output': copy.deepcopy(output)}
    owner_scoped = reasoning.scoped
    def scoped(db, cls, key, principal, lock=False):
        assert lock is False and principal.workspace_id == s.p.workspace_id
        if cls is Invocation:
            if key != s.call.id: raise HTTPException(404, 'original Invocation missing')
            return s.call
        if cls is Task:
            if key != s.task.id: raise HTTPException(404, 'original Task missing')
            return s.task
        return owner_scoped(db, cls, key, principal, lock=lock)
    monkeypatch.setattr(reasoning, 'scoped', scoped)
    monkeypatch.setattr(reasoning, '_current', forbidden)
    monkeypatch.setattr(reasoning, 'recover', forbidden)
    monkeypatch.setattr(reasoning, '_settle_capture', forbidden)
    from generic_agents import gateway
    monkeypatch.setattr(gateway, 'cloud_json', forbidden)
    env.db.read_only = True
    reviewer = NS(user_id=identity('reasoning-acceptor'), workspace_id=s.p.workspace_id, role='operator')
    return s, env, output, reviewer


def test_reasoning_history_reparses_exact_paid_capture_and_all_three_independent_audits_for_reviewer(monkeypatch):
    s, env, output, reviewer = captured_reasoning(monkeypatch)
    before = (copy.deepcopy(s.call.data), copy.deepcopy(s.ledger.data), env.db.commits)
    assert reviewer.user_id != s.job.data['actor_id']
    assert reasoning.saved_result(env.db, reviewer, s.task, s.job, s.call, s.cap, s.inputs) == output
    assert (s.call.data, s.ledger.data, env.db.commits) == before and len(env.posts) == 1
    # The ordinary original-request checker still enforces original identity.
    with pytest.raises(HTTPException): reasoning._checked_state(env.db, reviewer, s.cap, s.inputs, s.key, s.call)


@pytest.mark.parametrize('fault', ['prepared_audit', 'reserved_audit', 'captured_audit', 'audit_actor',
    'raw_bytes', 'cloud_model', 'cloud_receipt', 'output', 'job_actor', 'reserved_tokens',
    'charged_tokens', 'bool_reserved', 'bool_charged'])
def test_reasoning_missing_or_corrupt_original_chain_is_not_success_and_has_zero_new_io(monkeypatch, fault):
    s, env, _, reviewer = captured_reasoning(monkeypatch)
    actions = {'prepared_audit': reasoning.PREPARED, 'reserved_audit': reasoning.RESERVED,
        'captured_audit': reasoning.CAPTURED}
    if fault in actions: env.db.events = [row for row in env.db.events if row.action != actions[fault]]
    elif fault == 'audit_actor': env.db.events[0].actor_id = reviewer.user_id
    elif fault == 'raw_bytes': s.call.data[reasoning.STATE_KEY]['capture']['raw_base64'] = 'e30='
    elif fault == 'cloud_model': s.ledger.capability_id = identity('wrong-model')
    elif fault == 'cloud_receipt': s.ledger.data['receipt']['model'] = 'wrong-model'
    elif fault == 'output': s.call.data['output']['analysis']['summary'] = '没有原首答支持的改写'
    elif fault == 'reserved_tokens': s.ledger.reserved_tokens += 1
    elif fault == 'charged_tokens': s.ledger.charged_tokens += 1
    elif fault == 'bool_reserved': s.ledger.reserved_tokens = True
    elif fault == 'bool_charged': s.ledger.charged_tokens = True
    else: s.job.data['actor_id'] = reviewer.user_id
    before = (env.db.commits, len(env.posts), len(env.db.events))
    with pytest.raises(HTTPException): reasoning.saved_result(env.db, reviewer, s.task, s.job, s.call, s.cap, s.inputs)
    assert (env.db.commits, len(env.posts), len(env.db.events)) == before
