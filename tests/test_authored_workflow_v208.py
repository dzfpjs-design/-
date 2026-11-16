"""Bounded pure adversarial contracts; never proof of a real business runtime."""
import copy
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import authored_workflow_graph_v208 as graph
from generic_agents import authored_workflow_store_v208 as store
from generic_agents import authored_workflow_runtime_v208 as runtime
from generic_agents import workflow_document_packet_v207 as packet, business_reasoning_v197 as reasoning
from generic_agents.contracts import digest

ID = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
HASH = 'a' * 64
NAME = '人工资料核对'


def flow():
    return {'trigger': '人工发起核对', 'output': '保存实际人工结论', 'confirmedAt': '2026-10-25T00:00:00Z',
        'confirmedSignature': '{}', 'steps': [
            {'id': 'first', 'name': '核对原文', 'purpose': '逐处核对', 'agent': '人工确认', 'role': '核对人',
                'output': '人工核对记录', 'condition': '', 'elseId': 'end', 'nextId': 'second'},
            {'id': 'second', 'name': '保存结论', 'purpose': '保存实际结论', 'agent': '人工确认', 'role': '核对人',
                'output': '尚未核实也须说明的结论', 'condition': '', 'elseId': 'end', 'nextId': 'end'}]}


def pin(kind='packet'):
    return {'capability_id': ID if kind == 'packet' else OTHER, 'capability_hash': HASH,
        'agent_role': 'data' if kind == 'packet' else 'business', 'adapter': packet.ADAPTER if kind == 'packet' else reasoning.ADAPTER,
        'input_bindings': {}, 'arguments': {}, 'release_version': '1.0.0', 'max_attempts': 1,
        'input_schema': packet.input_schema() if kind == 'packet' else reasoning.input_schema(),
        'output_schema': packet.output_schema() if kind == 'packet' else reasoning.output_schema()}


def roles(*, name=NAME, model=False):
    actor = {'user_id': ID, 'username': '公开测试处理人', 'membership_role': 'operator'}
    return {'核对人': actor, graph.packet_role(name): copy.deepcopy(actor),
        **({graph.reasoning_role(name): copy.deepcopy(actor)} if model else {})}


def built(*, model=False, name=NAME):
    return graph.build_graph(flow(), roles(name=name, model=model), pin(), ID,
        reasoning_pin=pin('reasoning') if model else None, reasoning_actor=ID if model else None,
        candidate_proposals=['候选是提案，尚未事实核实'] if model else [], business_name=name)


def body(*, model=False):
    return {'configuration_revision': 1, 'configuration_hash': HASH, 'business_name': NAME,
        'packet_capability_id': ID, 'packet_capability_hash': HASH, 'packet_actor_id': ID,
        'role_bindings': {'核对人': ID}, 'reasoning': {'capability_id': OTHER, 'capability_hash': HASH, 'actor_id': ID} if model else None,
        'candidate_proposals': ['候选提案😀'] if model else [],
        'node_actions': {'packet': 'data', **({'reasoning': 'data'} if model else {})},
        'external_node_ids': ['reasoning'] if model else [], 'prior_origin': None}


def test_server_template_keeps_original_human_flow_and_frozen_source_input():
    original = flow(); before = copy.deepcopy(original)
    value = graph.build_graph(original, roles(), pin(), ID, business_name=NAME)
    assert original == before
    actual = value['graph']; source = actual['input_schema']['properties'][graph.INPUT_KEY]
    assert source['x-runtime-document-source-set-v207'] is True and actual['input_schema']['required'] == [graph.INPUT_KEY]
    producer = actual['nodes'][0]
    assert producer['execution']['input_bindings'] == {'source_set_ref': 'selected_sources'}
    assert actual['references']['selected_sources'] == {'kind': 'object', 'nullable': False,
        'source': 'input', 'field': graph.INPUT_KEY, 'node_id': None}
    humans = [node for node in actual['nodes'] if node['kind'] == 'human']
    assert [node['source_step_id'] for node in humans] == ['first', 'second']
    assert [row['name'] for row in value['step_mapping']] == [row['name'] for row in before['steps']]
    assert all(node['execution']['adapter'] != reasoning.ADAPTER for node in actual['nodes'] if node['kind'] == 'ability')


def test_optional_analysis_uses_real_packet_output_and_task_question_not_hidden_source_text():
    value = built(model=True)['graph']; node = next(node for node in value['nodes'] if node['id'] == 'reasoning')
    assert value['input_schema']['properties']['analysis_question'] == {'type': 'string', 'minLength': 1,
        'maxLength': 4000, 'pattern': r'\S', 'title': '本次需要分析的问题'}
    assert 'analysis_question' in value['input_schema']['required']
    assert node['execution']['input_bindings'] == {'question': 'analysis_question', 'evidence_ids': 'packet_evidence'}
    assert value['references']['packet_evidence']['node_id'] == 'packet'
    assert node['execution']['arguments'] == {'candidates': [{'candidate_ref': 'candidate_1',
        'summary': '候选是提案，尚未事实核实', 'context': None}]}


@pytest.mark.parametrize('change', ['condition', 'jump', 'early_end', 'automatic', 'old_origin', 'old_step_origin', 'duplicate', 'reserved'])
def test_no_linearization_of_unsupported_or_borrowed_source(change):
    value = flow()
    if change == 'condition': value['steps'][0]['condition'] = '看起来可用'
    elif change == 'jump': value['steps'][1]['nextId'] = 'first'
    elif change == 'early_end': value['steps'][0]['nextId'] = 'end'
    elif change == 'automatic': value['steps'][0]['agent'] = '业务智能体'
    elif change == 'old_origin': value['fullSemanticSource'] = {'id': ID}
    elif change == 'old_step_origin': value['steps'][0]['businessFlowSource'] = {'id': ID}
    elif change == 'duplicate': value['steps'][1]['id'] = 'first'
    elif change == 'reserved': value['steps'][0]['role'] = graph.packet_role(NAME)
    before = copy.deepcopy(value)
    with pytest.raises(ValueError): graph.build_graph(value, roles(), pin(), ID, business_name=NAME)
    assert value == before


def test_business_scoped_automatic_posts_allow_independent_actors_but_human_posts_do_not():
    first, second = roles(name='甲业务'), roles(name='乙业务')
    second[graph.packet_role('乙业务')] = {'user_id': OTHER, 'username': '另一个处理人', 'membership_role': 'operator'}
    graph.compatible_roles(second, [first])
    second['核对人'] = copy.deepcopy(second[graph.packet_role('乙业务')])
    with pytest.raises(ValueError, match='同名人工岗位'): graph.compatible_roles(second, [first])


@pytest.mark.parametrize('change', ['bool_cas', 'graph', 'executable', 'schema', 'implicit_external', 'proposal_without_model', 'blank', 'numeric_consent'])
def test_closed_normal_http_body_does_not_accept_internal_graph_or_implicit_authority(change):
    value = {**body(), 'request_id': OTHER, 'preview_hash': HASH, 'consent': True}
    if change == 'bool_cas': value['configuration_revision'] = True
    elif change in {'graph', 'executable', 'schema'}: value[change] = {'escape': True}
    elif change == 'implicit_external': value['external_node_ids'] = ['reasoning']
    elif change == 'proposal_without_model': value['candidate_proposals'] = ['自己填写的事实']
    elif change == 'blank': value = {**body(model=True), 'candidate_proposals': ['  '], 'request_id': OTHER, 'preview_hash': HASH, 'consent': True}
    elif change == 'numeric_consent': value['consent'] = 1
    with pytest.raises(HTTPException): store.parse(json.dumps(value).encode(), store.Adopt)


def test_raw_command_bytes_and_unknown_prior_fields_are_not_reconstructed():
    value = {**body(), 'request_id': OTHER, 'preview_hash': HASH, 'consent': True}
    raw = ('  ' + json.dumps(value, ensure_ascii=False) + '\n').encode()
    parsed, exact, hashed = store.parse(raw, store.Adopt)
    assert exact.encode() == raw and parsed.prior_origin is None
    assert hashed != digest(parsed.model_dump(mode='json'))
    value['prior_origin'] = {'schema': store.ORIGIN, 'adoption_id': ID, 'business_name': NAME,
        'binding_hash': HASH, 'command_hash': HASH, 'approved': True}
    with pytest.raises(HTTPException): store.parse(json.dumps(value).encode(), store.Adopt)


def test_head_inventory_rejects_missing_parent_fork_and_rollback_without_rewriting_history():
    first = NS(id=ID, configuration_id=ID, business_name=NAME, ordinal=1, prior_adoption_id=None)
    second = NS(id=OTHER, configuration_id=ID, business_name=NAME, ordinal=2, prior_adoption_id=ID)
    assert store.heads([second, first])[(ID, NAME)] is second
    with pytest.raises(HTTPException): store.heads([second])
    fork = NS(id='33333333-3333-4333-8333-333333333333', configuration_id=ID, business_name=NAME, ordinal=2, prior_adoption_id=ID)
    with pytest.raises(HTTPException): store.heads([first, second, fork])
    assert first.prior_adoption_id is None and second.prior_adoption_id == ID


class MissingPrimary:
    def __init__(self): self.writes = []
    def scalars(self, statement):
        from generic_agents import models as m
        model = statement.column_descriptions[0]['entity']
        return [NS(id=ID, action=store.ADOPTED_AUDIT, object_id=OTHER)] if model is m.Audit else []
    def execute(self, statement, *args): return None
    def add(self, value): self.writes.append(value)


def test_deleted_adoption_and_command_with_independent_audit_are_not_an_unseen_nonce():
    db = MissingPrimary(); p = NS(workspace_id=ID, user_id=ID)
    with pytest.raises(HTTPException): store.recover(db, p, OTHER, HASH)
    assert db.writes == []


def test_unmarked_legacy_compile_is_read_only_but_an_inverse_audit_prevents_downgrade():
    class Boundary(MissingPrimary):
        def __init__(self, orphan): super().__init__(); self.orphan = orphan; self.probes = 0
        def scalar(self, statement):
            from generic_agents import models as m
            self.probes += 1
            return ID if self.orphan and statement.column_descriptions[0]['entity'] is m.Audit else None
    p = NS(workspace_id=ID, user_id=ID)
    legacy = Boundary(False)
    assert runtime.verify_configuration(legacy, p, {'flows': {}, 'businesses': []}) == []
    assert legacy.probes == 3 and legacy.writes == []
    orphan = Boundary(True)
    with pytest.raises(HTTPException): runtime.verify_configuration(orphan, p, {'flows': {}, 'businesses': []})
    assert orphan.writes == []


@pytest.mark.parametrize('key', store.OTHER_GRAPH_KEYS)
def test_existing_graph_runtime_family_is_not_composed_or_stripped(key):
    value = {'businesses': [], 'flows': {}, key: {'original': True}}
    before = copy.deepcopy(value)
    with pytest.raises(HTTPException): store.no_other_graphs(value)
    assert value == before
