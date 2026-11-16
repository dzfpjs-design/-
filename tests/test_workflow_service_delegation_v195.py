"""Actual pure compiler/eligibility boundaries, not service execution evidence.

No registry, member, approval, Task, handler, network, acquisition or delivery is
created here. Root runs these tests; synthetic pins establish only this contract.
"""
import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_nested_execution_v195 as compiler
from generic_agents import business_flow_nested_execution_v192 as previous
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import workflow_abilities as abilities
from generic_agents.contracts import Manifest, digest
from tests.test_business_flow_nested_execution_v195 import scene, build


def service_scene(actor='task_center'):
    values = scene()
    plan, _, bindings, sidecar, paths = values
    path = paths['通知责任人员']
    action = previous._at(plan, path)
    action.update(actor=actor, role='任务中心' if actor == 'task_center' else '执行服务')
    row = bindings['nodes'][path]
    row.update(service_actor=actor, operation=compiler.SERVICE_OPERATIONS[actor])
    row['execution'].update(adapter='workflow_service', agent_role='business')
    sidecar['source_plan_hash'] = digest(plan)
    return values, path


def capability(actor='task_center'):
    operation = compiler.SERVICE_OPERATIONS[actor]
    data = Manifest(kind='tool', namespace='workflow_services', name=operation,
        version='1.0.0', owner='公开合成服务登记', description='仅用于静态合同测试的服务能力',
        allowed_agent_roles=['business'],
        input_schema={'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
        output_schema={'type': 'object', 'properties': {'result': {'type': 'string'}},
            'required': ['result'], 'additionalProperties': False},
        execution={'adapter': 'workflow_service', 'service_actor': actor,
            'operation': operation, 'side_effect': True}, budget={'max_attempts': 1}).model_dump()
    return SimpleNamespace(id='public_service_capability', kind='tool', data=data, status='published',
        content_hash=digest(data), validated_hash=digest(data), release_version='1.0.0')


def reseal(cap):
    cap.content_hash = cap.validated_hash = digest(cap.data)


@pytest.mark.parametrize('actor', ['task_center', 'execution_service'])
def test_explicit_service_uses_existing_kernel_without_renaming_original_actor(actor):
    values, path = service_scene(actor)
    before = copy.deepcopy(values)
    result = build(values)
    mapping = next(item for item in result['source_mapping']['actions'] if item['ast_path'] == path)
    node = next(item for item in result['graph']['nodes'] if item['id'] == mapping['graph_node_id'])
    assert mapping['source_action']['actor'] == actor
    assert mapping['source_action_hash'] == digest(previous._at(values[0], path))
    assert mapping['service_delegation'] == {'service_actor': actor,
        'operation': compiler.SERVICE_OPERATIONS[actor], 'agent_role': 'business', 'side_effect': True}
    assert node['kind'] == 'ability' and node['execution']['agent_role'] == 'business'
    assert node['execution']['adapter'] == 'workflow_service'
    assert set(node['execution']) == {'capability_id', 'capability_hash', 'agent_role',
        'input_bindings', 'arguments', 'input_schema', 'output_schema', 'adapter',
        'release_version', 'max_attempts'}
    assert kernel.validate_graph(result['graph']) == result['graph']
    assert result['execution_bindings']['nodes'][path] == values[2]['nodes'][path]
    assert all(result[key] is False for key in compiler.FLAGS)
    assert values == before
    checked = compiler.check_execution_graph(result, values[0], 0, values[2],
        scope_binding=values[1]['scope_binding'], causal_bindings=values[3])
    checked['source_mapping']['actions'].clear()
    assert result['source_mapping']['actions']


@pytest.mark.parametrize('fault', ['missing_delegation', 'wrong_actor', 'wrong_operation',
    'extra_field', 'ordinary_adapter', 'wrong_delegate', 'source_renamed', 'execution_extra'])
def test_service_compilation_requires_exact_source_delegation_and_original_native_shape(fault):
    values, path = service_scene()
    row = values[2]['nodes'][path]
    if fault == 'missing_delegation': del row['service_actor']
    elif fault == 'wrong_actor': row['service_actor'] = 'execution_service'
    elif fault == 'wrong_operation': row['operation'] = 'resense_with_receipt'
    elif fault == 'extra_field': row['allow_service'] = True
    elif fault == 'ordinary_adapter': row['execution']['adapter'] = 'builtin'
    elif fault == 'wrong_delegate': row['execution']['agent_role'] = 'perception'
    elif fault == 'source_renamed':
        previous._at(values[0], path)['actor'] = 'business_agent'
        values[3]['source_plan_hash'] = digest(values[0])
        del row['service_actor']; del row['operation']
    else: row['execution']['side_effect'] = False
    with pytest.raises(ValueError): build(values)


def test_frozen_v192_does_not_gain_a_service_adapter_path():
    values, _ = service_scene()
    values[2]['schema'] = previous.BINDINGS
    with pytest.raises(ValueError, match='service_actor_not_registered'):
        previous.build_execution_graph(values[0], 0, values[2],
            scope_binding=values[1]['scope_binding'], causal_bindings=values[3])


def test_service_metadata_is_not_decoration_that_can_be_relabelled_after_compilation():
    values, path = service_scene()
    result = build(values)
    mapping = next(item for item in result['source_mapping']['actions'] if item['ast_path'] == path)
    mapping['service_delegation']['side_effect'] = False
    with pytest.raises(ValueError, match='projection_changed'):
        compiler.check_execution_graph(result, values[0], 0, values[2],
            scope_binding=values[1]['scope_binding'], causal_bindings=values[3])


@pytest.mark.parametrize('actor', ['task_center', 'execution_service'])
def test_only_actual_published_matching_side_effecting_tool_is_service_eligible(actor, monkeypatch):
    cap = capability(actor)
    before = copy.deepcopy(cap.__dict__)
    def forbidden(*args, **kwargs):
        raise AssertionError('eligibility must not publish, dispatch, audit or create a receipt')
    for name in ('published', 'validate', 'audit'):
        monkeypatch.setattr(abilities, name, forbidden)
    assert abilities.eligible_service(cap, 'business', service_actor=actor,
        operation=compiler.SERVICE_OPERATIONS[actor]) is None
    assert cap.__dict__ == before
    assert 'workflow_service' not in abilities.ADAPTERS
    with pytest.raises(HTTPException) as rejected:
        abilities.eligible(cap, 'business')
    assert rejected.value.status_code == 409


@pytest.mark.parametrize('fault', ['draft', 'invalidated', 'content_changed', 'kind', 'manifest_kind',
    'wrong_role', 'no_role', 'wrong_adapter', 'wrong_actor', 'wrong_operation', 'extra_execution',
    'false', 'zero', 'one', 'null', 'string_true', 'attempt_float', 'attempt_bool',
    'attempt_zero', 'attempt_six', 'bad_schema', 'external_ref'])
def test_service_registration_cannot_turn_false_or_unverified_capability_into_write_permission(fault):
    cap = capability()
    execution = cap.data['execution']
    role = 'business'
    if fault == 'draft': cap.status = 'draft'
    elif fault == 'invalidated': cap.validated_hash = None
    elif fault == 'content_changed': cap.data['description'] += '未重新发布'
    elif fault == 'kind': cap.kind = 'specialist_model'
    elif fault == 'manifest_kind': cap.data['kind'] = 'specialist_model'
    elif fault == 'wrong_role': role = 'data'
    elif fault == 'no_role': cap.data['allowed_agent_roles'] = ['perception']
    elif fault == 'wrong_adapter': execution['adapter'] = 'http'
    elif fault == 'wrong_actor': execution['service_actor'] = 'execution_service'
    elif fault == 'wrong_operation': execution['operation'] = 'resense_with_receipt'
    elif fault == 'extra_execution': execution['handler'] = 'fallback_gateway'
    elif fault in {'false', 'zero', 'one', 'null', 'string_true'}:
        execution['side_effect'] = {'false': False, 'zero': 0, 'one': 1,
            'null': None, 'string_true': 'true'}[fault]
    elif fault.startswith('attempt_'):
        cap.data['budget']['max_attempts'] = {'attempt_float': 1.0, 'attempt_bool': True,
            'attempt_zero': 0, 'attempt_six': 6}[fault]
    elif fault == 'bad_schema': cap.data['output_schema']['properties']['result']['type'] = 'invented'
    else: cap.data['input_schema']['properties']['external'] = {'$ref': 'https://invalid.example/schema'}
    if fault not in {'invalidated', 'content_changed'}: reseal(cap)
    with pytest.raises(HTTPException) as rejected:
        abilities.eligible_service(cap, role, service_actor='task_center', operation='in_app_notice')
    assert rejected.value.status_code == 409


def test_no_service_actor_can_be_delegated_to_the_other_operation_or_external_messaging():
    cap = capability()
    for actor, operation in [('task_center', 'send_email'), ('execution_service', 'in_app_notice'),
        ('business_agent', 'in_app_notice'), ('task_center', 'in_app_notice '), (None, 'in_app_notice')]:
        with pytest.raises(HTTPException):
            abilities.eligible_service(cap, 'business', service_actor=actor, operation=operation)


def test_existing_read_only_capability_eligibility_is_unchanged_and_is_not_service_eligible():
    cap = capability()
    cap.data['execution'] = {'adapter': 'builtin', 'handler': 'aggregate_values', 'side_effect': False}
    reseal(cap)
    assert abilities.eligible(cap, 'business') is None
    with pytest.raises(HTTPException):
        abilities.eligible_service(cap, 'business', service_actor='task_center', operation='in_app_notice')


def test_public_action_ids_are_the_exact_compiler_source_mapping_not_a_ui_guess():
    values, _ = service_scene()
    plan, source = values[:2]
    before = copy.deepcopy(plan)
    identities = compiler.graph_action_ids(plan, 0, scope_binding=source['scope_binding'])
    result = build(values)
    assert identities == {item['ast_path']: item['graph_node_id']
        for item in result['source_mapping']['actions']}
    assert plan == before
    identities.clear()
    assert compiler.graph_action_ids(plan, 0, scope_binding=source['scope_binding'])


def test_compiler_itself_consumes_public_action_id_function(monkeypatch):
    values, _ = service_scene()
    actual = compiler.graph_action_ids
    calls = []
    def observed(plan, flow_index, *, scope_binding):
        calls.append((digest(plan), flow_index, digest(scope_binding)))
        return actual(plan, flow_index, scope_binding=scope_binding)
    monkeypatch.setattr(compiler, 'graph_action_ids', observed)
    result = build(values)
    assert calls == [(digest(values[0]), 0, digest(values[1]['scope_binding']))]
    assert result['graph']['schema'] == kernel.SCHEMA


@pytest.mark.parametrize('index', [True, -1, 1, '0', None])
def test_public_action_ids_reject_invalid_flow_instead_of_returning_an_empty_or_foreign_map(index):
    values, _ = service_scene()
    with pytest.raises(ValueError):
        compiler.graph_action_ids(values[0], index, scope_binding=values[1]['scope_binding'])


def test_public_action_ids_cannot_bypass_source_plan_shape_or_binding_check():
    values, _ = service_scene()
    values[0]['business']['flows'][0]['completion']['unexpected'] = True
    with pytest.raises(ValueError):
        compiler.graph_action_ids(values[0], 0, scope_binding=values[1]['scope_binding'])
