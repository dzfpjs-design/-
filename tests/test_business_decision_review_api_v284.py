"""Synthetic readonly DTO/native-proof boundaries, not PG/IAM/API evidence.

The native frozen-workflow and runtime raw/SDK/Frame checks are explicit spies.
Real _job_matches, check_invocation, independent native/invoke audit matching and
the V284 pure contract still run when Root executes these tests. No spy is a
claim that a model, observation, grant or independent receipt exists.
"""
import ast
import copy
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from typing import get_type_hints

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import business_decision_review_api_v284 as subject
from generic_agents.business_reasoning_protocols_v276 import recovery_record
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, Invocation, Job, Membership, Task, User


def ident(n): return '28410000-0000-4000-8000-' + format(n, '012x')


def fixture(monkeypatch):
    p = NS(user_id=ident(1), workspace_id=ident(2), role='viewer')
    actor = ident(3); task_id = ident(4); activation_id = 'reasoning:original'
    source = {'source_ref': 'source_cap', 'kind': 'capability', 'source_id': ident(5),
        'content_hash': 'a' * 64, 'text': '登记能力可申请原温度字段。', 'readability': 'readable'}
    observation = {'source_ref': 'source_observation', 'kind': 'observation', 'source_id': ident(6),
        'content_hash': 'b' * 64, 'text': '登记温度原值为9007199254740993。', 'readability': 'readable'}
    def citation(row):
        return {'source_ref': row['source_ref'], 'quote_start': 0, 'quote_end': len(row['text']), 'quote': row['text']}
    interval = {'start': '2026-11-01T12:00:00+00:00', 'end': '2026-11-01T12:10:00+00:00'}
    requirements = [{'requirement_ref': 'need_temperature', 'entity_id': ident(7), 'field': 'temperature_c',
        'value_type': 'integer', 'unit': 'Cel', 'semantics': 'instantaneous', 'time_mode': 'bounded',
        'time_scope': interval, 'required_kind': 'observation'}]
    facts = [{'fact_ref': 'declaration_1', 'kind': 'capability_declaration', 'entity_id': ident(7),
        'field': 'capability_declaration', 'unit': None, 'semantics': 'statement',
        'time_scope': {'start': None, 'end': None}, 'value': '仅为已登记能力声明。', 'source_refs': [citation(source)]},
        {'fact_ref': 'fact_temperature', 'kind': 'observation', 'entity_id': ident(7), 'field': 'temperature_c',
            'unit': 'Cel', 'semantics': 'instantaneous',
            'time_scope': {'start': '2026-11-01T12:05:00+00:00', 'end': '2026-11-01T12:05:00+00:00'},
            'value': 9007199254740993, 'source_refs': [citation(observation)]}]
    decision_input = {'schema': subject.runtime.decision.INPUT, 'decision_intent': 'information_acquisition',
        'scope': {'task_id': task_id, 'activation_id': activation_id, 'goal_snapshot_hash': 'c' * 64,
            'entity_ids': [ident(7)]}, 'requirements': requirements, 'sources': [source, observation],
        'capabilities': [{'capability_ref': 'cap_resense', 'capability_id': source['source_id'],
            'capability_hash': source['content_hash'], 'release_version': '2.0.0', 'input_schema_hash': 'd' * 64,
            'output_schema_hash': 'e' * 64, 'source_ref': source['source_ref'],
            'action_kind': 'information_acquisition', 'output_ports': [], 'information_targets': [{
                'port_ref': 'port_temperature', 'pointer': '/requested_observation/temperature_c',
                'field': 'temperature_c', 'value_type': 'integer', 'unit': 'Cel'}]}],
        'candidates': [{'candidate_ref': 'candidate_1', 'proposal': '仅在原批准范围内获取温度资料。',
            'capability_refs': ['cap_resense'], 'requirement_mappings': [{'requirement_ref': 'need_temperature',
                'capability_ref': 'cap_resense', 'delivery': 'requested_observation', 'port_ref': 'port_temperature'}],
            'claims': []}], 'facts': facts, 'known_conflicts': []}
    model = {'schema': subject.runtime.decision.MODEL, 'candidate_reviews': [{
        'candidate_ref': 'candidate_1', 'suitability': 'suitable', 'reasoning': '按原声明核查方案，不授予执行权限。',
        'supporting_fact_refs': ['declaration_1'], 'blocking_conditions': []}],
        'source_reviews': [{'source_ref': row['source_ref'], 'assessment': 'reviewed', 'reason': '已核对原资料。'}
            for row in decision_input['sources']], 'conflicts': [],
        'recommendation': {'candidate_ref': 'candidate_1', 'reason': '此为资料获取提议。',
            'supporting_fact_refs': ['declaration_1']}}
    result = subject.runtime.decision.derive_result(decision_input, model)
    inputs = {'synthetic_original_graph_input': True}
    cap_data = {'kind': 'tool', 'version': '1.0.0', 'execution': {'adapter': subject.runtime.ADAPTER,
        'side_effect': False}, 'input_schema': {'type': 'object'}, 'output_schema': subject.runtime.output_schema()}
    cap = Capability(id=ident(8), workspace_id=p.workspace_id, kind='tool', name='synthetic', status='published',
        release_version='1.0.0', data=cap_data, content_hash=digest(cap_data), validated_hash=digest(cap_data))
    node = {'id': 'reasoning', 'kind': 'ability', 'actor_id': actor, 'execution': {
        'adapter': subject.runtime.ADAPTER, 'agent_role': 'business', 'capability_id': cap.id,
        'capability_hash': cap.content_hash, 'release_version': cap.release_version,
        'input_schema': copy.deepcopy(cap_data['input_schema']), 'output_schema': copy.deepcopy(cap_data['output_schema'])}}
    activation = {'id': activation_id, 'node_id': node['id'], 'status': 'completed', 'epoch': 0,
        'inputs_snapshot': {'arguments': copy.deepcopy(inputs)}, 'output': copy.deepcopy(result)}
    job = Job(id=ident(9), workspace_id=p.workspace_id, kind=subject.scope.KIND, status='succeeded',
        idempotency_key='original-paid-key', data={'task_id': task_id, 'activation_id': activation_id,
            'actor_id': actor, 'graph_hash': 'f' * 64, 'cancel_epoch': 0, 'activation_version': 0,
            'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs), 'invocation_key': 'original-paid-key'})
    call = Invocation(id=ident(10), workspace_id=p.workspace_id, capability_id=cap.id, status='succeeded',
        idempotency_key=job.idempotency_key, data={'job_id': job.id, 'input_hash': digest(inputs),
            'capability_hash': cap.content_hash, 'role': 'business', 'side_effect': False,
            'approval': None, 'lease_token': 'original-lease', 'output': copy.deepcopy(result)})
    owner = {'job_id': job.id, 'lease_token': 'original-lease'}
    transport = {'job_id': job.id, 'invocation_id': call.id, 'invocation_key': job.idempotency_key,
        'cancel_epoch': 0, 'input_hash': digest(inputs), 'phase': 'succeeded', 'dispatch_committed': True,
        'output_hash': digest(result), 'dispatch_ownership': owner, 'dispatch_authorization': {
            'job_id': job.id, 'lease_token': owner['lease_token'], 'actor_id': actor, 'epoch': 0,
            'input_hash': digest(inputs), 'capability_hash': cap.content_hash}}
    state = {'graph_hash': 'f' * 64, 'activations': {activation_id: activation}}
    task = NS(id=task_id, workspace_id=p.workspace_id, package_id=ident(11), owner_id=actor, title='公开合成边界',
        status='workflow_waiting', data={'instance_release_id': ident(12), 'graph_runtime': state,
            'graph_jobs': {activation_id: transport}})
    release = NS(id=ident(12), package_id=task.package_id)
    field = {'entity_id': ident(7), 'field': 'temperature_c', 'value_type': 'integer', 'unit': 'Cel',
        'schema': {'type': 'integer', 'title': '温度'}}
    frame = {'decision_input': decision_input,
        'sources': {'data': {'packet': {'simulation_only': True}}, 'LOCAL_ONLY': 'local-source-private-marker'},
        'capability_pins': {'fields': [field], 'policy': 'local-policy-private-marker'}}
    saved = {'capture': {'original': 'runtime-boundary'}, 'call_id': ident(13),
        'request': {'frame': frame, 'frame_hash': digest(frame)}}
    ledger = NS(id=ident(13), status='succeeded')
    seen = []
    def frozen(db, reader, actual_task, actual_activation):
        assert reader is p and actual_task == task.id and actual_activation == activation_id
        seen.append('saved_execution_and_frozen_boundary')
        return task, release, {}, {}, state, activation, node
    monkeypatch.setattr(subject.scope, '_frozen', frozen)
    monkeypatch.setattr(subject.runtime, '_manifest_shape', lambda data: data)  # explicit registry boundary
    def saved_result(db, reader, actual_task, actual_job, actual_call, actual_cap, original_inputs):
        assert reader is p and p.user_id != actor
        assert (actual_task, actual_job, actual_call, actual_cap, original_inputs) == (task, job, call, cap, inputs)
        seen.append('original_runtime_raw_sdk_frame_boundary')
        return copy.deepcopy(result)
    monkeypatch.setattr(subject.runtime, 'saved_result', saved_result)
    def checked_state(db, reader, actual_cap, original_inputs, key, actual_call, *, actor_id):
        assert reader is p and actor_id == actor != p.user_id
        assert actual_cap is cap and actual_call is call and key == job.idempotency_key and original_inputs == inputs
        seen.append('original_actor_state_boundary')
        return copy.deepcopy(saved)
    monkeypatch.setattr(subject.runtime, '_checked_state_for_actor', checked_state)
    monkeypatch.setattr(subject.runtime, '_cloud_call', lambda db, reader, identity:
        ledger if reader is p and identity == ledger.id else pytest.fail('other SDK call'))
    marker, recovery_action = recovery_record(subject.runtime.ADAPTER)
    audits = {'capability.invoke': [NS(actor_id=actor, data={'status': 'succeeded'})], recovery_action: [],
        'workflow.graph.node_completed': [NS(actor_id=actor, data={'activation_id': activation_id, 'invocation_id': call.id})],
        'workflow.graph.dispatch_authorized': [NS(actor_id=actor, data={'activation_id': activation_id,
            'job_id': job.id, 'epoch': 0, 'input_hash': digest(inputs)})]}
    def sql(query): return str(query.compile(compile_kwargs={'literal_binds': True}))
    queries = {sql(select(model).where(model.workspace_id == p.workspace_id, model.id == row.id)
        .execution_options(populate_existing=True)): [row] for model, row in [(Job, job), (Invocation, call), (Capability, cap)]}
    for action, rows in audits.items():
        query = select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == action,
            Audit.object_id == (task.id if action.startswith('workflow.graph.') else call.id))
        if action == 'workflow.graph.node_completed': query = query.where(Audit.data['activation_id'].as_string() == activation_id)
        if action == 'workflow.graph.dispatch_authorized': query = query.where(Audit.data['job_id'].as_string() == job.id)
        queries[sql(query.limit(2))] = rows
    iam_sql = sql(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role == p.role, Membership.role.in_(['admin', 'operator', 'viewer'])))
    class IO:
        iam = p.user_id
        @property
        def no_autoflush(self):
            @contextmanager
            def guard():
                seen.append('no_autoflush'); yield
            return guard()
        def scalar(self, query):
            assert sql(query) == iam_sql, 'unknown SQL is rejected'
            return self.iam
        def scalars(self, query):
            assert sql(query) in queries, 'unknown SQL is rejected'
            return queries[sql(query)]
        def add(self, *args): pytest.fail('readonly context added a row')
        def flush(self, *args): pytest.fail('readonly context flushed')
        def commit(self, *args): pytest.fail('readonly context committed')
        def execute(self, *args): pytest.fail('readonly context executed a write')
    return NS(p=p, actor=actor, task=task, job=job, call=call, cap=cap, node=node, state=state,
        activation=activation, activation_id=activation_id, transport=transport, frame=frame, saved=saved,
        result=result, ledger=ledger, seen=seen, db=IO(), audits=audits, marker=marker, recovery_action=recovery_action)


def read(s): return subject.context(s.db, s.p, s.task.id, s.activation_id)


def test_context_is_minimal_detached_and_keeps_current_reader_separate_from_original_actor(monkeypatch):
    s = fixture(monkeypatch); before = copy.deepcopy((s.task.data, s.call.data, s.job.data, s.frame))
    value = read(s)
    assert set(value) == {'schema', 'task_id', 'activation_id', 'invocation_id', 'input_hash', 'result',
        'decision_input', 'fields', 'fact_values', 'simulation_only'}
    assert value['schema'] == subject.SCHEMA and value['invocation_id'] == s.call.id
    assert value['input_hash'] == s.result['input_hash'] and value['result'] == s.result
    assert value['decision_input'] == s.frame['decision_input'] and value['simulation_only'] is True
    assert value['fields'] == [{'entity_id': ident(7), 'field': 'temperature_c', 'label': '温度',
        'value_type': 'integer', 'unit': 'Cel'}]
    assert {row['fact_ref']: row['text'] for row in value['fact_values']}['fact_temperature'] == '9007199254740993'
    assert s.seen == ['no_autoflush', 'saved_execution_and_frozen_boundary',
        'original_runtime_raw_sdk_frame_boundary', 'original_actor_state_boundary']
    assert (s.task.data, s.call.data, s.job.data, s.frame) == before
    encoded = json.dumps(value, ensure_ascii=False)
    assert 'local-source-private-marker' not in encoded and 'local-policy-private-marker' not in encoded
    value['decision_input']['facts'].clear()
    assert len(s.frame['decision_input']['facts']) == 2


def test_route_uses_snapshot_authentication_and_the_existing_rr_ro_dependency():
    assert subject.router.routes[0].path == '/api/workspace/tasks/{task_id}/decision-review-context-v284'
    annotations = get_type_hints(subject.get_context, include_extras=True)
    assert annotations['p'].__metadata__[0].dependency is subject.authenticated
    assert annotations['db'].__metadata__[0].dependency is subject.read_session


@pytest.mark.parametrize('damage', ['job_running', 'call_failed', 'sdk_reserved', 'sdk_failed', 'old_adapter',
    'node_actor', 'cap_version', 'input_schema', 'output_schema', 'input_hash', 'original_lease',
    'dispatch_actor', 'transport_phase', 'transport_output', 'activation_output'])
def test_unfinished_wrong_original_or_old_protocol_cannot_become_display_context(monkeypatch, damage):
    s = fixture(monkeypatch)
    if damage == 'job_running': s.job.status = 'running'
    elif damage == 'call_failed': s.call.status = 'blocked_model'
    elif damage == 'sdk_reserved': s.ledger.status = 'reserved'
    elif damage == 'sdk_failed': s.ledger.status = 'failed'
    elif damage == 'old_adapter': s.node['execution']['adapter'] = 'business_decision_v276'
    elif damage == 'node_actor': s.node['actor_id'] = s.p.user_id
    elif damage == 'cap_version': s.cap.release_version = '9.0.0'
    elif damage == 'input_schema': s.node['execution']['input_schema'] = {'type': 'string'}
    elif damage == 'output_schema': s.node['execution']['output_schema'] = {'type': 'string'}
    elif damage == 'input_hash': s.call.data['input_hash'] = '0' * 64
    elif damage == 'original_lease': s.call.data['lease_token'] = 'another'
    elif damage == 'dispatch_actor': s.transport['dispatch_authorization']['actor_id'] = s.p.user_id
    elif damage == 'transport_phase': s.transport['phase'] = 'running'
    elif damage == 'transport_output': s.transport['output_hash'] = '0' * 64
    else: s.activation['output'] = {'forged': True}
    with pytest.raises(HTTPException): read(s)


@pytest.mark.parametrize('damage', ['missing_invoke', 'duplicate_invoke', 'invoke_actor',
    'missing_completion', 'missing_dispatch', 'missing_recovery'])
def test_original_native_success_or_same_answer_recovery_needs_its_independent_audits(monkeypatch, damage):
    s = fixture(monkeypatch)
    if damage == 'missing_invoke': s.audits['capability.invoke'].clear()
    elif damage == 'duplicate_invoke': s.audits['capability.invoke'].append(copy.deepcopy(s.audits['capability.invoke'][0]))
    elif damage == 'invoke_actor': s.audits['capability.invoke'][0].actor_id = s.p.user_id
    elif damage == 'missing_completion': s.audits['workflow.graph.node_completed'].clear()
    elif damage == 'missing_dispatch': s.audits['workflow.graph.dispatch_authorized'].clear()
    else:
        s.call.data[s.marker] = {'previous_status': 'blocked_model', 'output_hash': digest(s.result), 'recovered_at': 'original-time'}
        s.audits['capability.invoke'][0].data = {'status': 'blocked_model'}
    with pytest.raises(HTTPException): read(s)


def test_original_successful_recovery_is_read_without_reconcile_or_new_capture(monkeypatch):
    s = fixture(monkeypatch)
    s.call.data[s.marker] = {'previous_status': 'blocked_model', 'output_hash': digest(s.result), 'recovered_at': 'original-time'}
    s.audits['capability.invoke'][0].data = {'status': 'blocked_model'}
    s.audits[s.recovery_action].append(NS(actor_id=s.actor, data={'job_id': s.job.id,
        'activation_id': s.activation_id, 'previous_status': 'blocked_model', 'output_hash': digest(s.result)}))
    monkeypatch.setattr(subject.runtime, 'recover', lambda *a, **k: pytest.fail('GET reconciled a response'))
    monkeypatch.setattr(subject.runtime, 'execute', lambda *a, **k: pytest.fail('GET called the model'))
    assert read(s)['result'] == s.result


@pytest.mark.parametrize('damage', ['missing_capture', 'frame_hash', 'result_scope', 'false_simulation_type',
    'duplicate_field', 'field_unit', 'fake_authority'])
def test_frame_and_dto_metadata_cannot_be_substituted_or_repaired(monkeypatch, damage):
    s = fixture(monkeypatch)
    if damage == 'missing_capture': s.saved['capture'] = None
    elif damage == 'frame_hash': s.saved['request']['frame_hash'] = '0' * 64
    elif damage == 'result_scope': s.frame['decision_input']['scope']['task_id'] = ident(99)
    elif damage == 'false_simulation_type': s.frame['sources']['data']['packet']['simulation_only'] = 1
    elif damage == 'duplicate_field': s.frame['capability_pins']['fields'].append(copy.deepcopy(s.frame['capability_pins']['fields'][0]))
    elif damage == 'field_unit': s.frame['capability_pins']['fields'][0]['unit'] = 'other'
    else: s.result['can_dispatch'] = True; s.call.data['output'] = copy.deepcopy(s.result); s.activation['output'] = copy.deepcopy(s.result); s.transport['output_hash'] = digest(s.result)
    with pytest.raises(HTTPException): read(s)


def test_current_reader_revocation_is_not_original_actor_impersonation(monkeypatch):
    s = fixture(monkeypatch); s.db.iam = None
    with pytest.raises(HTTPException): read(s)
    assert s.seen == ['no_autoflush']


def test_runtime_raw_or_sdk_failure_is_not_suppressed_into_a_partial_context(monkeypatch):
    s = fixture(monkeypatch)
    def blocked(*args): raise HTTPException(409, 'original raw/SDK proof rejected')
    monkeypatch.setattr(subject.runtime, 'saved_result', blocked)
    with pytest.raises(HTTPException, match='original raw/SDK proof rejected'): read(s)


def test_labels_only_use_frozen_titles_and_never_current_configuration(monkeypatch):
    s = fixture(monkeypatch); field=s.frame['capability_pins']['fields'][0]
    field['schema'].pop('title'); s.saved['request']['frame_hash'] = digest(s.frame)
    assert read(s)['fields'][0]['label'] == 'temperature_c'


def test_server_scalar_json_keeps_big_integer_negative_zero_and_declared_empty_values():
    values = [9007199254740993, -0.0, True, None, '', '原文🌲']
    result = subject._fact_values({'facts': [{'fact_ref': 'fact_'+str(i), 'value': value} for i, value in enumerate(values)]})
    assert [row['text'] for row in result] == ['9007199254740993', '-0.0', 'true', 'null', '""', '"原文🌲"']
    with pytest.raises(ValueError): subject._fact_values({'facts': [{'fact_ref': 'bad', 'value': float('nan')}]})


def test_source_contains_no_writer_provider_or_fake_principal_path():
    tree = ast.parse(Path(subject.__file__).read_text())
    forbidden = {'commit', 'flush', 'add', 'delete', 'execute', 'recover', 'reconcile',
        'prepare_request', 'graph_context', 'propose_acquisitions', 'receive', 'audit', 'Principal'}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call): continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
        if name in forbidden:
            # Local set.add is not a database mutation.
            assert name == 'add' and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'seen'
