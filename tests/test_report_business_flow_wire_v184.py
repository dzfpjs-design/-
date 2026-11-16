"""Real V184 compilation and unchanged V182 source/graph validation, pure only.

Public synthetic source/catalog helpers are reused. No model, original report,
database, permission, runtime execution or semantic quality is proved here.
"""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_flow_wire_v184 as subject
from generic_agents import report_business_flow_wire_v182 as previous
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v182 import scene as previous_scene


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('plan compilation must not contact a service, DB or process')
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine, 'connect', denied)
    monkeypatch.setattr(Session, 'execute', denied)
    monkeypatch.setattr(Session, 'commit', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def scene(count=208):
    old, source = previous_scene(count)
    template = copy.deepcopy(old['business']['flows'][0]['nodes'][0])
    del template['node_id']
    attribution = copy.deepcopy(template['attribution'])

    def action(name='核查原资料', **changes):
        return {**copy.deepcopy(template), 'name': name, **copy.deepcopy(changes)}

    def steps(n=1):
        return {'kind': 'steps', 'attribution': copy.deepcopy(attribution),
            'steps': [action('核验步骤' + str(i + 1)) for i in range(n)]}

    flow = {'name': '资料核验流程', 'trigger': copy.deepcopy(old['business']['flows'][0]['trigger']),
        'phase_order': {**copy.deepcopy(attribution), 'reason': '按原阶段顺序衔接，仍待业务核对。'},
        'phases': [steps()], 'completion': action('保存完成记录', outputs=['完成记录'])}
    plan = {'schema': subject.WIRE, 'binding_hash': digest(source['scope_binding']),
        'business': {'name': source['scope_binding']['name'], 'flows': [flow], 'questions': []}}
    return plan, source, action, steps, attribution


def phases(action, steps, attribution, mode='all'):
    a = lambda: copy.deepcopy(attribution)
    return [steps(2),
        {'kind': 'decision', 'attribution': a(), 'decision': action('判断核验条件'),
            'branches': [{'condition': '资料满足核验要求', 'attribution': a(), 'steps': []},
                {'condition': '资料需要补充核验', 'attribution': a(), 'steps': [action('补充核验资料')]}]},
        {'kind': 'parallel', 'attribution': a(), 'dispatch': action('安排并行核对'),
            'branches': [{'steps': [action('并行核对一'), action('保存并行记录一')]},
                {'steps': [action('并行核对二')]}], 'wait_mode': mode, 'join_attribution': a()},
        {'kind': 'repeat', 'attribution': a(), 'steps': [action('重新核验资料'), action('保存复核结果')],
            'check': action('判断复核是否完成'), 'repeat_if': '仍有需要再次核对的事项',
            'finish_if': '本轮核对要求已经满足'}]


def project(plan, source):
    return subject.build_projection(raw(plan), **source)


def at(value, pointer):
    for part in pointer.split('/')[1:]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def reseal(value):
    if 'compilation_manifest' in value:
        value['compilation_manifest_hash'] = digest(value['compilation_manifest'])
    value['projection_hash'] = digest({key: item for key, item in value.items() if key != 'projection_hash'})
    return value


def test_exact_schema_contains_a_small_plan_and_no_model_ids_quotes_or_edges():
    plan, source, _, _, _ = scene()
    schema = subject.output_schema(source['scope_binding'])
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(plan)) == []
    assert schema['$defs']['BusinessDefinitionV184']['properties']['name']['const'] == plan['business']['name']
    assert schema['properties']['binding_hash']['const'] == plan['binding_hash']
    assert subject._schema_for_shape(plan['business']['name'], plan['binding_hash']) == schema
    assert not any(key in canonical_json(schema) for key in ('"node_id"', '"flow_id"', '"evidence"', '"quote"', '"edges"'))
    old_fields = previous.NodeV182.model_json_schema()['properties']
    new_fields = subject.ActionV184.model_json_schema()['properties']
    assert new_fields == {key: val for key, val in old_fields.items() if key != 'node_id'}
    result = project(plan, source)
    assert len(result['wire']['business']['flows'][0]['nodes']) == 2
    assert result['selected_input_count'] == 208
    assert result['selected_input_claims'] == source['input_claims']


def test_zero_phases_compiles_only_the_declared_completion_and_passes_real_manual_validation():
    plan, source, _, _, _ = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = []
    before = canonical_json(plan)
    schema = subject.output_schema(source['scope_binding'])
    assert list(Draft202012Validator(schema).iter_errors(plan)) == []
    result = project(plan, source)
    compiled = result['wire']['business']['flows'][0]
    assert len(compiled['nodes']) == 1 and compiled['edges'] == []
    node = compiled['nodes'][0]
    assert compiled['start_node'] == node['node_id'] and compiled['end_nodes'] == [node['node_id']]
    assert {key: value for key, value in node.items() if key != 'node_id'} == flow['completion']
    assert result['compilation_manifest']['flows'][0]['nodes'] == [
        {'node_id': node['node_id'], 'ast_path': '/business/flows/0/completion'}]
    assert result['compilation_manifest']['flows'][0]['edges'] == []
    assert previous.parse_wire(previous._bytes(result['wire']), scope_binding=source['scope_binding']) == result['wire']
    manual = subject.new_manual(result)
    assert subject.validate_value(manual, **source) == manual
    assert previous.validate_value(manual, **source) == manual
    assert result['raw_plan'] == plan and canonical_json(plan) == before
    assert result['can_apply'] is result['can_execute'] is False


@pytest.mark.parametrize('mode', ['all', 'any'])
def test_all_phase_rules_are_complete_and_finally_pass_real_v182_with_ast_maps(mode):
    plan, source, action, steps, attribution = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = phases(action, steps, attribution, mode)
    before = canonical_json([plan, source]); original = raw(plan)
    result = subject.build_projection(original, **source)
    compiled = result['wire']; graph = compiled['business']['flows'][0]
    manifest = result['compilation_manifest']; mapping = manifest['flows'][0]
    assert previous.parse_wire(previous._bytes(compiled), scope_binding=source['scope_binding']) == compiled
    assert subject.check_projection(result, original, **source) == result
    assert len(mapping['nodes']) == len(graph['nodes'])
    assert [item['edge_index'] for item in mapping['edges']] == list(range(len(graph['edges'])))
    for item in mapping['nodes']:
        node = next(row for row in graph['nodes'] if row['node_id'] == item['node_id'])
        assert node['name'] == at(plan, item['ast_path'])['name']
    for item in mapping['edges']:
        assert item['ast_paths']
        for path in item['ast_paths']:
            assert at(plan, path) is not None
    completion = next(item['node_id'] for item in mapping['nodes'] if item['ast_path'].endswith('/completion'))
    assert graph['end_nodes'] == [completion]
    assert not any(edge.get('from_node') == completion or completion in edge.get('from_nodes', []) for edge in graph['edges'])
    assert all(item['produced_by'] == [completion] for item in graph['deliverables'])
    assert all(item['description'] == flow['completion']['responsibility'] for item in graph['deliverables'])
    parallel = [item for item in graph['edges'] if item['kind'] == 'parallel']
    waits = [item for item in graph['edges'] if item['kind'] == 'wait']
    assert len(parallel) == 2 and len({item['group'] for item in parallel}) == 1
    assert len(waits) == 1 and waits[0]['mode'] == mode and len(waits[0]['from_nodes']) == 2
    check = next(item['node_id'] for item in mapping['nodes'] if item['ast_path'].endswith('/check'))
    exits = [item for item in graph['edges'] if item.get('from_node') == check]
    assert {item['condition'] for item in exits} == {flow['phases'][3]['repeat_if'], flow['phases'][3]['finish_if']}
    assert all(item['kind'] == 'condition' for item in exits)
    assert 'max_iterations' not in canonical_json(compiled), 'a repeat draft cannot invent execution permission'
    assert result['raw_plan'] == plan and canonical_json([plan, source]) == before
    assert all(result[key] is False for key in ('all_input_semantics_verified', 'semantic_quality_verified',
        'branch_completeness_verified', 'whole_source_coverage_verified', 'aggregate_verified',
        'provider_delivery_verified', 'source_authority_verified', 'can_apply', 'can_publish', 'can_execute'))


def test_empty_decision_branches_keep_their_conditions_without_an_invented_else():
    plan, source, action, _, attr = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [{'kind': 'decision', 'attribution': copy.deepcopy(attr), 'decision': action('判断条件'),
        'branches': [{'condition': '已有明确资料', 'attribution': copy.deepcopy(attr), 'steps': []},
            {'condition': '仍缺明确资料', 'attribution': copy.deepcopy(attr), 'steps': []}]}]
    result = project(plan, source); compiled = result['wire']['business']['flows'][0]
    assert len(compiled['nodes']) == 2 and len(compiled['edges']) == 2
    assert all(edge['kind'] == 'condition' and edge['to_node'] == compiled['end_nodes'][0] for edge in compiled['edges'])
    assert [edge['condition'] for edge in compiled['edges']] == ['已有明确资料', '仍缺明确资料']


def test_duplicate_display_names_and_declared_sets_are_stable_without_changing_raw_text():
    plan, source, _, _, _ = scene()
    flow = plan['business']['flows'][0]
    ref = source['allowed_span_refs'][0]
    flow['name'] = '同一核验流程'
    flow['phases'][0]['steps'][0].update(inputs=['原资料', '原资料', ' 原资料 '], outputs=['核验记录', '核验记录'])
    flow['phases'][0]['steps'][0]['attribution']['citations'] = [ref, ref]
    flow['completion']['outputs'] = ['完成记录', '完成记录']
    plan['business']['flows'] = [flow, copy.deepcopy(flow), {**copy.deepcopy(flow), 'name': flow['name'] + '（流程1）'}]
    plan['business']['questions'] = [{'question': '此事项仍待核对', 'reason': '保留原问题，不猜测结论', 'citations': [ref, ref]}]
    before = canonical_json(plan); result = project(plan, source)
    compiled = result['wire']['business']['flows']
    assert len({item['name'] for item in compiled}) == 3
    assert compiled[2]['name'] == plan['business']['flows'][2]['name']
    assert result['compilation_manifest']['flows'][0]['raw_name'] == flow['name']
    assert compiled[0]['nodes'][0]['inputs'] == ['原资料', ' 原资料 ']
    assert compiled[0]['nodes'][0]['outputs'] == ['核验记录']
    assert compiled[0]['nodes'][0]['attribution']['citations'] == [ref]
    assert len(compiled[0]['deliverables']) == 1
    assert result['wire']['business']['questions'][0]['citations'] == [ref]
    assert result['raw_plan'] == plan and canonical_json(plan) == before


def test_unused_but_valid_raw_attributions_do_not_become_a_fake_quote_bibliography():
    plan, source, action, _, attr = scene()
    first, second = source['allowed_span_refs'][:2]
    flow = plan['business']['flows'][0]
    flow['phase_order']['citations'] = [second]
    flow['phases'] = [{'kind': 'decision', 'attribution': {**copy.deepcopy(attr), 'citations': [second]},
        'decision': action(), 'branches': [{'condition': '条件一满足', 'attribution': copy.deepcopy(attr), 'steps': []},
            {'condition': '条件二满足', 'attribution': copy.deepcopy(attr), 'steps': []}]}]
    result = project(plan, source)
    assert result['used_refs'] == [first]
    assert [item['span_ref'] for item in result['wire']['evidence']] == [first]
    resolved = previous.resolve_evidence(source['catalog'], [first], source['allowed_span_refs'])
    assert result['wire']['evidence'][0]['quote'] == resolved[0]['text']
    assert result['raw_plan']['business']['flows'][0]['phase_order']['citations'] == [second]


@pytest.mark.parametrize('where', ['unused_order', 'unused_phase', 'question'])
def test_unknown_ref_is_rejected_even_in_an_attribution_not_compiled_into_an_edge(where):
    plan, source, action, _, attr = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [{'kind': 'decision', 'attribution': copy.deepcopy(attr), 'decision': action(),
        'branches': [{'condition': '条件一满足', 'attribution': copy.deepcopy(attr), 'steps': []},
            {'condition': '条件二满足', 'attribution': copy.deepcopy(attr), 'steps': []}]}]
    if where == 'unused_order': flow['phase_order']['citations'] = ['w:p999999/s1']
    elif where == 'unused_phase': flow['phases'][0]['attribution']['citations'] = ['w:p999999/s1']
    else: plan['business']['questions'] = [{'question': '未知事项', 'reason': '不得猜配原文', 'citations': ['w:p999999/s1']}]
    before = canonical_json(plan)
    with pytest.raises(ValueError):
        project(plan, source)
    assert canonical_json(plan) == before


def test_node_capacity_is_per_flow_and_excess_is_explicitly_blocked_without_truncation():
    plan, source, _, steps, _ = scene()
    flow = plan['business']['flows'][0]; flow['phases'] = [steps(16), steps(15)]
    plan['business']['flows'].append({**copy.deepcopy(flow), 'name': '另一独立流程'})
    result = project(plan, source)
    assert [len(item['nodes']) for item in result['wire']['business']['flows']] == [32, 32]
    flow['phases'] = [steps(16), steps(16)]; before = canonical_json(plan)
    with pytest.raises(subject.BusinessFlowPlanCapacityError, match='32'):
        project(plan, source)
    assert canonical_json(plan) == before and len(flow['phases'][1]['steps']) == 16


@pytest.mark.parametrize('fault', ['id', 'evidence', 'binding', 'name', 'actor', 'empty_steps', 'empty_role', 'wait_mode'])
def test_strict_plan_never_admits_old_graph_fields_coercion_or_invalid_structure(fault):
    plan, source, action, steps, attr = scene(); flow = plan['business']['flows'][0]
    if fault == 'id': flow['phases'][0]['steps'][0]['node_id'] = 'model_chosen_id'
    elif fault == 'evidence': plan['evidence'] = []
    elif fault == 'binding': plan['binding_hash'] = '0' * 64
    elif fault == 'name': plan['business']['name'] = '另一业务'
    elif fault == 'actor': flow['completion']['actor'] = 'unknown'
    elif fault == 'empty_steps': flow['phases'][0]['steps'] = []
    elif fault == 'empty_role': flow['completion']['role'] = ' '
    else: flow['phases'] = [phases(action, steps, attr)[2]]; flow['phases'][0]['wait_mode'] = True
    with pytest.raises(ValueError):
        subject.parse_plan(raw(plan), scope_binding=source['scope_binding'])


@pytest.mark.parametrize('fault', ['algorithm', 'node_path', 'edge_path', 'raw_hash', 'wire', 'flag'])
def test_resealed_manifest_or_compiled_mutation_cannot_replace_cold_recompilation(fault):
    plan, source, _, _, _ = scene(); original = raw(plan); value = project(plan, source)
    if fault == 'algorithm': value['compilation_manifest']['algorithm'] = 'another_compiler'
    elif fault == 'node_path': value['compilation_manifest']['flows'][0]['nodes'][0]['ast_path'] = '/business/name'
    elif fault == 'edge_path': value['compilation_manifest']['flows'][0]['edges'][0]['ast_paths'] = ['/business/name']
    elif fault == 'raw_hash': value['raw_plan_bytes_sha256'] = '0' * 64
    elif fault == 'wire': value['wire']['business']['flows'][0]['nodes'][0]['name'] = '替换原步骤'
    else: value['can_apply'] = True
    reseal(value)
    with pytest.raises(ValueError):
        subject.check_projection(value, original, **source)


def test_raw_spelling_and_compiled_bytes_have_distinct_hashes_and_old_answers_cannot_enter():
    plan, source, _, _, _ = scene(); a = raw(plan); b = json.dumps(plan, ensure_ascii=False, indent=2).encode()
    left = subject.build_projection(a, **source); right = subject.build_projection(b, **source)
    assert left['raw_plan_bytes_sha256'] == hashlib.sha256(a).hexdigest()
    assert right['raw_plan_bytes_sha256'] == hashlib.sha256(b).hexdigest()
    assert left['raw_plan_bytes_sha256'] != right['raw_plan_bytes_sha256']
    assert left['compiled_wire_bytes_sha256'] == right['compiled_wire_bytes_sha256']
    assert left['compiled_wire_bytes_sha256'] == hashlib.sha256(previous._bytes(left['wire'])).hexdigest()
    assert 'wire_bytes_sha256' not in left
    with pytest.raises(ValueError): subject.check_projection(left, b, **source)
    old, _ = previous_scene()
    with pytest.raises(ValueError): subject.parse_plan(raw(old), scope_binding=source['scope_binding'])
    with pytest.raises(ValueError): previous.parse_wire(a, scope_binding=source['scope_binding'])


def test_final_v182_validator_is_executed_and_cannot_be_replaced_by_compiler_self_hash(monkeypatch):
    plan, source, _, _, _ = scene(); actual = previous.build_projection; seen = []
    def checked(raw_bytes, **kwargs):
        seen.append(raw_bytes)
        return actual(raw_bytes, **kwargs)
    monkeypatch.setattr(previous, 'build_projection', checked)
    result = project(plan, source)
    assert seen == [previous._bytes(result['wire'])]
    compile_plan = subject._compile
    def broken(*args):
        value, mappings, refs = compile_plan(*args)
        flow = value['business']['flows'][0]
        flow['edges'].append({'kind': 'sequence', 'from_node': flow['end_nodes'][0], 'to_node': flow['start_node'],
            'attribution': copy.deepcopy(flow['trigger']['attribution'])})
        return value, mappings, refs
    monkeypatch.setattr(subject, '_compile', broken)
    with pytest.raises(ValueError): project(plan, source)


@pytest.mark.parametrize('fault', ['claims', 'context', 'source_hash'])
def test_actual_independent_source_inputs_are_still_required(fault):
    plan, source, _, _, _ = scene()
    if fault == 'claims': source['input_claims'][0]['statement'] = '替换原声明'
    elif fault == 'context': source['context']['unexpected'] = '改变原配置背景'
    else:
        source['scope_binding']['source_hash'] = '0' * 64
        plan['binding_hash'] = digest(source['scope_binding'])
    with pytest.raises(ValueError): project(plan, source)


def test_existing_manual_contract_remains_partial_unconfirmed_and_does_not_grant_execution():
    plan, source, _, _, _ = scene()
    plan['business']['flows'][0]['completion']['attribution']['inferred'] = True
    projection = project(plan, source)
    value = subject.new_manual(projection)
    assert value['schema'] == previous.MANUAL and value['result'] == projection['wire']
    assert value['acknowledge_partial_scope'] is value['acknowledge_inferences'] is False
    assert subject.validate_value(value, **source) == value
    with pytest.raises(ValueError): subject.confirm_value(value)
    value['acknowledge_partial_scope'] = True
    with pytest.raises(ValueError): subject.confirm_value(value)
    value['acknowledge_inferences'] = True
    assert subject.confirm_value(value) == value
    assert projection['can_execute'] is projection['can_apply'] is False
