"""Real binding checks over public fixtures, not source/DB authority proof."""
import copy
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_execution_adoption as a
from generic_agents import business_flow_execution_graph as graph
from generic_agents.contracts import digest
from tests.test_business_flow_execution_graph import chain, bindings


def fixture(count=17):
    value, context = chain(count); supplied = bindings(value)
    actor = str(uuid4())
    for node in supplied['nodes'].values(): node['actor_id'] = actor
    translated = graph.build_execution_graph(value, value['business']['flows'][0]['flow_id'], supplied,
        scope_binding=context['scope_binding'])
    assert not translated['issues']
    q = {'id': str(uuid4()), 'qualification_hash': 'a' * 64, 'value': value}
    package = {'schema': 'business-flow-execution-source-v1', 'qualification': q,
        'scope_binding': context['scope_binding'], 'material_snapshot': {}}
    name = value['business']['name']; flow_id = value['business']['flows'][0]['flow_id']
    role = value['business']['flows'][0]['nodes'][0]['role']
    binding = a.seal({'schema': a.BINDING, 'business_name': name, 'qualification_id': q['id'],
        'qualification_hash': q['qualification_hash'], 'flow_id': flow_id, 'flow': a.derive_flow(package, flow_id),
        'graph': translated['graph'], 'role_bindings': {role: {'user_id': actor, 'username': '公开合成人员',
            'membership_role': 'operator'}}, 'capability_pins': {}, 'event_source_bindings': {},
        'event_admission_policy': None, 'source_materials': [], 'source': package,
        'execution_bindings': supplied, 'translation': translated, 'node_actions': {}, 'external_node_ids': []}, 'binding_hash')
    return binding


def test_real_converter_binding_preserves_seventeen_nodes_full_source_and_no_legacy_proof():
    b = fixture(); original = copy.deepcopy(b)
    assert a.check_binding(b) == b and b == original
    assert len(b['flow']['steps']) == 17
    assert len([n for n in b['graph']['nodes'] if n['kind'] == 'human']) == 17
    assert 'source_design' not in b and 'source_manifest' not in b and 'source_proof' not in b
    assert b['translation']['source_value'] == b['source']['qualification']['value']


@pytest.mark.parametrize('fault', ['flow', 'graph', 'role', 'qualification', 'translation', 'unbound_source', 'event'])
def test_resealed_inconsistent_binding_cannot_turn_into_a_new_valid_source(fault):
    b = fixture(2)
    if fault == 'flow': b['flow']['steps'].pop()
    elif fault == 'graph': b['graph']['nodes'].pop()
    elif fault == 'role': next(iter(b['role_bindings'].values()))['user_id'] = str(uuid4())
    elif fault == 'qualification': b['qualification_hash'] = 'b' * 64
    elif fault == 'translation': b['translation']['source_mapping'] = {}
    elif fault == 'unbound_source': b['source']['scope_binding']['name'] += '另一个范围'
    else: b['event_admission_policy'] = {'invented': True}
    b = a.seal({k: v for k, v in b.items() if k != 'binding_hash'}, 'binding_hash')
    with pytest.raises((HTTPException, ValueError)): a.check_binding(b)


def command(binding):
    return {'schema': 'business-flow-execution-adopt-command-v1', 'qualification_id': binding['qualification_id'],
        'qualification_hash': binding['qualification_hash'], 'flow_id': binding['flow_id'],
        'business_name': binding['business_name'], 'configuration_revision': 1, 'configuration_hash': 'c' * 64,
        'execution_bindings': binding['execution_bindings'],
        'role_bindings': {k: v['user_id'] for k, v in binding['role_bindings'].items()},
        'node_actions': {}, 'external_node_ids': [], 'request_id': str(uuid4()), 'preview_hash': 'd' * 64,
        'consent': True, 'share_source': True}


@pytest.mark.parametrize('fault', ['duplicate', 'numeric_ack', 'bool_version', 'extra', 'missing', 'nonfinite'])
def test_exact_command_parser_has_no_bool_coercion_duplicate_or_extra_scope(fault):
    body = command(fixture(1))
    if fault == 'numeric_ack': body['share_source'] = 1
    elif fault == 'bool_version': body['configuration_revision'] = True
    elif fault == 'extra': body['execute_now'] = True
    elif fault == 'missing': body.pop('external_node_ids')
    elif fault == 'nonfinite': body['execution_bindings']['invalid'] = float('nan')
    raw = json.dumps(body, ensure_ascii=False).encode()
    if fault == 'duplicate': raw = raw[:-1] + b',"consent":true}'
    with pytest.raises(HTTPException) as error: a.parse(raw, a.Adopt)
    assert error.value.status_code == 422


def test_proposal_is_exact_append_without_changing_old_flow_or_confirming_automation():
    b = fixture(2); old = {'businesses': ['旧业务'], 'flows': {'旧业务': {'steps': []}},
        'automation': {'policies': {'旧业务': {'unchanged': True}}}}
    before = copy.deepcopy(old); origin = a.origin(str(uuid4()), b, 'b' * 64)
    result = a.proposed(old, b['business_name'], b, origin)
    assert old == before and result['flows']['旧业务'] == before['flows']['旧业务']
    assert result['automation'] == before['automation']
    assert result[a.CONFIG_KEY][b['business_name']] == origin
    assert result['flows'][b['business_name']]['confirmedAt'] is None
    with pytest.raises(HTTPException): a.proposed(result, b['business_name'], b, origin)


def test_ordinary_config_cannot_forge_or_remove_new_origin_without_any_source_io():
    p = NS(workspace_id=str(uuid4())); db = NS(scalars=lambda _: [])
    with pytest.raises(HTTPException): a.protect_configuration(db, p, {}, {a.CONFIG_KEY: {'fake': {}}})
    with pytest.raises(HTTPException): a.protect_configuration(db, p, {a.CONFIG_KEY: {'fake': {}}}, {})
    with pytest.raises(HTTPException): a.protect_configuration(db, p, {}, {'flows': {'fake': {'businessFlowSource': {"present": True}}}})


def test_adopt_original_receipt_returns_before_any_fresh_private_or_current_source(monkeypatch):
    body = command(fixture(1)); raw = json.dumps(body).encode(); p = NS(role='operator')
    expected = {'immutable_original': True}
    monkeypatch.setattr(a, 'recover', lambda *args: {'found': True, 'result': expected})
    def forbidden(*args): raise AssertionError('original adoption recovery must not require renewed source')
    monkeypatch.setattr(a, 'prepare', forbidden)
    assert a.adopt(object(), p, raw) == expected


def test_ordinary_put_detects_history_grant_after_both_adoption_and_audit_rows_are_missing():
    issued = {'schema': a.ORIGIN, 'adoption_id': str(uuid4()), 'business_name': '原已采用业务',
        'binding_hash': 'a' * 64, 'command_hash': 'b' * 64}
    class DB:
        def scalars(self, statement):
            entity = statement.column_descriptions[0]['entity']
            if entity is a.SetupRevision: return [{a.CONFIG_KEY: {'原已采用业务': issued}}]
            if entity is a.SetupDraft: return [{}]
            assert entity in {a.Adoption, a.Audit}
            return []
    with pytest.raises(HTTPException) as error:
        a.protect_configuration(DB(), NS(workspace_id=str(uuid4())), {}, {})
    assert error.value.status_code == 409
