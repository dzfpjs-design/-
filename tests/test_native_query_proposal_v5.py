"""Synthetic protocol/producer tests, no DB, provider, subprocess or paid oracle.

Run only against the staged source with --noconftest and plugin autoload off.
Registered native manifests and actual pure builtin handlers are used below.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import generic_agents
from generic_agents import native_query_proposal_contract as v1
from generic_agents import native_query_proposal_v2 as v2
from generic_agents import native_query_proposal_v3 as v3
from generic_agents import native_query_proposal_v4 as v4
from generic_agents import native_query_proposal_v5 as v5
from generic_agents import native_query_proposals as service
from generic_agents.business_tools import arguments_schema
from generic_agents.contracts import EvidenceRequirement, digest
from generic_agents.data_tools import builtin
from generic_agents.native_query_catalog import manifests

SOURCE = Path(__file__).resolve().parents[1] / 'src'
EVIDENCE = EvidenceRequirement(source='archive').model_dump()
QUESTION = '标题含热；数量；均值；计数；再计数；原资料合计；全文热；检查方式；'
LEGACY_HASHES = {
    'native_query_proposal_contract.py': 'e665e858bc1ff343d8ca20bba12a9212daf1d8a02bb1022b3f8ef3310ee567f1',
    'native_query_proposal_v2.py': '02ebc2f0261ca9fb34788a91b0a039ef22ecc3e631aaca5c13f8f03b339495ab',
    'native_query_proposal_v3.py': '569a0c36339ddf44e2e028e1832f9ceeb94ce018d246aca59d648e78c60e0e33',
    'native_query_proposal_v4.py': 'bcbadaa7c4a59c1ae2212651fa66dfaf4767ba0d32d97676609d20278ca62f08',
}


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('DB/network/subprocess forbidden in v5 pure tests')
    import socket
    import subprocess
    from generic_agents import db
    monkeypatch.setattr(db, 'engine', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def catalog():
    tools = []
    for manifest in manifests():
        data = manifest.model_dump(mode='json')
        properties = data['output_schema']['properties']['records']['items']['properties']['values'].get('properties', {})
        tools.append({'id': manifest.specification['method'], 'method': manifest.specification['method'],
            'name': manifest.description, 'content_hash': digest(data), 'version': manifest.version,
            'parameters_schema': arguments_schema(SimpleNamespace(data=data)),
            'output_fields': [{'key': key, 'type': prop['type'],
                'unit': '条证据记录' if key == 'sample_count' else None} for key, prop in properties.items()]})
    return {'schema': v1.CATALOG_SCHEMA, 'objects': [
        {'id': identity, 'label': label, 'type': 'record', 'type_label': '档案'}
        for identity, label in [('root', '枢纽'), ('one', '一站'), ('two', '二站')]],
        'fields': [{'key': 'title', 'label': '标题', 'type': 'string', 'unit': None, 'entity_types': ['record']},
            {'key': 'quantity', 'label': '数量', 'type': 'number', 'unit': '件', 'entity_types': ['record']}],
        'relation_types': [{'id': 'route', 'title': '联络', 'description': '测试联络',
            'source_types': ['record'], 'target_types': ['record']}],
        'query_tools': tools, 'field_conflicts': []}


def candidate(operations=None):
    return {'status': 'candidate', 'summary': '待人工核对的合成候选。',
        'fields': [{'key': 'title', 'quote': '标题'}, {'key': 'quantity', 'quote': '数量'}],
        'relation_query': None, 'operations': operations or [], 'result_criterion': None, 'limitations': []}


def aggregate(operation='mean', source=0, field='quantity', quote='均值'):
    return {'capability_id': 'aggregate', 'input_from': source,
        'parameters': {'field': field, 'operation': operation}, 'quote': quote}


def text_step(method='fulltext', source=0, field='title', quote='全文热'):
    parameters = {'field': field, 'text': '热'}
    if method == 'fulltext':
        parameters.update(top_k=3, min_score=0.1, match_mode='all_terms')
    return {'capability_id': method, 'input_from': source, 'parameters': parameters, 'quote': quote}


def validate(wire=None, description=None, question=QUESTION):
    return v5.validate_candidate(candidate() if wire is None else wire, question,
        catalog() if description is None else description, EVIDENCE)


def units(frame):
    return {item['key']: item['unit'] for item in frame['fields']}


def record(values, identity='one'):
    return {'values': values, 'entity_ids': [identity], 'evidence_refs': ['entity:' + identity + ':1']}


def test_source_identity_and_all_legacy_bytes_unchanged():
    assert Path(generic_agents.__file__).resolve().parent == SOURCE / 'generic_agents'
    for name, expected in LEGACY_HASHES.items():
        assert hashlib.sha256((SOURCE / 'generic_agents' / name).read_bytes()).hexdigest() == expected


def test_v5_wire_exactly_delegates_v4_without_extra_model_fields():
    description = catalog(); before = copy.deepcopy(description)
    assert v5.result_schema(description) == v4.result_schema(description)
    assert description == before
    wire = candidate(); wire['query_effect'] = {'status': 'derived'}
    with pytest.raises(HTTPException):
        validate(wire)


@pytest.mark.parametrize('version,module', [(1, v1), (2, v2), (3, v3), (4, v4), (5, v5)])
def test_frozen_parser_restores_original_versions(version, module):
    frozen = {'proposal_contract_version': version, 'question': 'old immutable question'}
    before = copy.deepcopy(frozen)
    assert service._wire_parser(frozen) is module and frozen == before
    if version in {1, 2}:
        wire = {'status': 'candidate', 'summary': '旧版本原稿', 'fields': ['quantity'],
            'relation_query': None, 'result_criterion': None, 'questions': [], 'limitations': [],
            'claims': [{'kind': 'field', 'key': 'quantity', 'quote': '数量'}],
            'tool_steps' if version == 1 else 'operations': []}
    else:
        wire = candidate()
    result = module.validate_candidate(wire, QUESTION, catalog(), EVIDENCE)
    assert ('query_effect' in result) is (version == 5)


@pytest.mark.parametrize('version', [0, 7, True, '5', None])
def test_unknown_frozen_version_fails_closed_without_dispatch(version):
    with pytest.raises(HTTPException):
        service._wire_parser({'proposal_contract_version': version})
    assert service._wire_parser({}) is v1


def test_new_preview_is_current_v6_stable_and_contains_generic_rules(monkeypatch):
    description = catalog()
    cap = SimpleNamespace(id='pure-profile', content_hash='pure-hash',
        data={'execution': {'provider': 'qwen', 'model': 'pure-test'}, 'data_policy': {}})
    draft = {'question': QUESTION, 'evidence': EVIDENCE, 'revision': 1}
    monkeypatch.setattr(service, '_current', lambda *args: ({'mode': 'query'}, description, draft))
    monkeypatch.setattr(service, 'model', lambda *args: cap)
    monkeypatch.setattr(service, 'model_view', lambda *args: {'id': cap.id})
    frozen = service.preview(None, None, None, cap.id)
    assert frozen == service.preview(None, None, None, cap.id)
    assert frozen['proposal_contract_version'] == 6
    from generic_agents import native_query_proposal_v6 as v6
    assert frozen['schema'] == v6.result_schema(description)
    body = json.loads(frozen['request_messages'][0]['content'])
    for text in ('operations=[]', 'metadata', '逐一指定终点', 'count', 'sum', '量字'):
        assert text in body['instruction']
    assert '空结果' in body['query_rules']['evidence']
    assert frozen['preview_hash'] == digest({key: value for key, value in frozen.items() if key != 'preview_hash'})


def test_read_effect_has_no_computation_or_invented_name_or_result_count():
    wire = candidate(); wire['fields'] = wire['fields'][1:]
    result = validate(wire); effect = result['query_effect']
    assert effect['status'] == 'derived' and effect['semantic_equivalence_verified'] is False
    assert effect['selected_fields'] == [{'key': 'quantity', 'label': '数量', 'type': 'number', 'unit': '件'}]
    assert effect['steps'] == [] and effect['final']['shape'] == 'records'
    assert effect['final']['cardinality'] == 'zero_or_more'
    assert effect['provenance']['requires_business_name_field'] is False
    assert 'evidence_refs' in effect['provenance']['tool_record_metadata']
    assert 'actual_count' not in effect['final']


@pytest.mark.parametrize('operation,unit', [('count', '条证据记录'), ('mean', '件'), ('sum', '件'), ('min', '件'), ('max', '件')])
def test_aggregate_units_shape_and_complete_input_denominator(operation, unit):
    effect = validate(candidate([aggregate(operation)]))['query_effect']
    step = effect['steps'][0]
    assert units(step['output']) == {'value': unit, 'sample_count': '条证据记录', 'field': None, 'operation': None}
    assert step['output']['shape'] == 'aggregate_record' and step['output']['cardinality'] == 'one_on_success'
    assert step['denominator']['frame_id'] == 'evidence'
    assert step['denominator']['record_unit'] == 'evidence_records'
    assert step['denominator']['missing_values'] == 'error_no_silent_skip'
    assert step['denominator']['empty_input'] == 'error_no_statistic'


def test_prior_aggregate_count_uses_aggregate_rows_without_rewriting_registered_unit():
    effect = validate(candidate([aggregate(), aggregate('count', 1, 'value', '再计数')]))['query_effect']
    second = effect['steps'][1]
    assert second['input']['id'] == 'operation_1' and second['input']['shape'] == 'aggregate_record'
    assert second['denominator']['record_unit'] == 'aggregate_rows'
    assert units(second['output'])['value'] == '条证据记录'
    original = builtin('data_record_aggregate', {'records': [record({'quantity': 12}), record({'quantity': 18}, 'two')],
        'field': 'quantity', 'operation': 'mean'})
    counted = builtin('data_record_aggregate', {'records': original['records'], 'field': 'value', 'operation': 'count'})
    assert counted['records'][0]['values']['value'] == counted['records'][0]['values']['sample_count'] == 1


def test_ranked_limits_follow_exact_producer_and_bypass_does_not_inherit_them():
    effect = validate(candidate([text_step(), aggregate('mean', 1),
        aggregate('sum', 0, 'quantity', '原资料合计')]))['query_effect']
    ranked = effect['steps'][1]
    assert ranked['denominator']['ranked_subset'] is True
    assert ranked['output']['subset_limits'] == [{'step_id': 'operation_1', 'method': 'fulltext',
        'parameters': {'field': 'title', 'top_k': 3, 'min_score': 0.1, 'match_mode': 'all_terms'}}]
    bypass = effect['steps'][2]
    assert bypass['input']['id'] == 'evidence'
    assert bypass['output']['ranked_subset'] is False and bypass['output']['subset_limits'] == []


@pytest.mark.parametrize('method', ['keyword', 'fulltext'])
def test_filters_preserve_actual_fields_and_source_references(method):
    effect = validate(candidate([text_step(method, quote='标题含热')]))['query_effect']
    step = effect['steps'][0]
    assert step['input']['fields'] == step['output']['fields']
    rows = [record({'title': '热', 'quantity': 7}), record({'title': '冷', 'quantity': 9}, 'two')]
    args = {'records': rows, **text_step(method)['parameters']}
    actual = builtin('data_keyword_filter' if method == 'keyword' else 'data_fulltext_search', args)
    assert actual['records'] == [rows[0]]
    assert actual['records'][0]['evidence_refs'] == rows[0]['evidence_refs']


@pytest.mark.parametrize('values', [{}, {'quantity': None}, {'quantity': float('nan')}, {'quantity': float('inf')}])
def test_actual_count_missing_or_nonfinite_never_silently_skips(values):
    with pytest.raises(HTTPException):
        builtin('data_record_aggregate', {'records': [record(values)], 'field': 'quantity', 'operation': 'count'})


@pytest.mark.parametrize('operation', ['count', 'mean', 'sum', 'min', 'max'])
def test_actual_empty_aggregate_never_fabricates_zero(operation):
    with pytest.raises(HTTPException):
        builtin('data_record_aggregate', {'records': [], 'field': 'quantity', 'operation': operation})


def test_actual_count_counts_present_scalars_while_arithmetic_rejects_boolean():
    rows = [record({'quantity': True}), record({'quantity': 'declared scalar'}, 'two')]
    assert builtin('data_record_aggregate', {'records': rows, 'field': 'quantity', 'operation': 'count'})['records'][0]['values']['value'] == 2
    with pytest.raises(HTTPException):
        builtin('data_record_aggregate', {'records': rows[:1], 'field': 'quantity', 'operation': 'mean'})


def test_relation_effect_is_selector_set_not_resolved_catalog_neighbors():
    wire = candidate(); wire['fields'] = wire['fields'][1:]
    wire['relation_query'] = {'roots': [{'key': 'root', 'quote': '枢纽'}],
        'types': [{'key': 'route', 'quote': '联络'}], 'direction': 'outgoing',
        'max_hops': 1, 'include_roots': False, 'at': None}
    effect = validate(wire, question='从枢纽沿联络向外一跳读取数量')['query_effect']
    assert effect['source_scope']['kind'] == 'relation_result_set'
    assert effect['source_scope']['resolved_entities'] is False
    assert effect['source_scope']['relation_query']['root_ids'] == ['root']
    assert 'one' not in json.dumps(effect) and 'two' not in json.dumps(effect)


def test_semantically_unjustified_count_is_explained_not_keyword_rejected_or_certified():
    # Deliberately questionable meaning: structural validity is not a semantic oracle.
    wire = candidate([aggregate('count', quote='数量')]); wire['fields'] = wire['fields'][1:]
    effect = validate(wire, question='逐条读取数量，不计算')['query_effect']
    assert effect['status'] == 'derived' and effect['final']['shape'] == 'aggregate_record'
    assert units(effect['final'])['value'] == '条证据记录'
    assert effect['semantic_equivalence_verified'] is False


@pytest.mark.parametrize('change,reason', [('method', 'producer_method_unknown'), ('shape', 'aggregate_output_shape_unknown'), ('field', 'input_field_or_shape_unknown')])
def test_unsafe_explanations_stay_unknown_without_repair(change, reason):
    checked = validate(candidate([aggregate()]))
    plan = copy.deepcopy(checked['candidate']); description = catalog()
    if change == 'method':
        next(tool for tool in description['query_tools'] if tool['id'] == 'aggregate')['method'] = 'unknown'
    elif change == 'shape':
        next(tool for tool in description['query_tools'] if tool['id'] == 'aggregate')['output_fields'] = []
    else:
        plan['tool_steps'][0]['parameters']['field'] = 'absent'
    effect = v5.derive_query_effect(plan, description)
    assert effect['status'] == 'unknown' and effect['final']['shape'] == 'unknown'
    assert any(item.startswith(reason + ':') for item in effect['unknown_reasons'])


def test_clarification_has_no_executable_effect():
    wire = {'status': 'needs_clarification', 'summary': '需要明确尚缺条件',
        'questions': ['哪一种登记字段？'], 'limitations': []}
    effect = validate(wire)['query_effect']
    assert effect['status'] == 'not_applicable' and effect['steps'] == [] and effect['final'] is None


def test_v4_gate_precedes_effect_and_no_false_producer_is_repaired(monkeypatch):
    wire = candidate([aggregate(), aggregate('mean', 1, 'quantity', '再计数')])
    monkeypatch.setattr(v5, 'derive_query_effect', lambda *args: pytest.fail('invalid v4 reached explanation'))
    with pytest.raises(HTTPException):
        validate(wire)


def test_effect_and_question_are_digest_bound_and_inputs_unmodified():
    wire = candidate([text_step(), aggregate('count', 1, 'quantity', '；计数；')])
    description = catalog(); before = copy.deepcopy((wire, description))
    result = validate(wire, description)
    old_hash = digest(result)
    assert result['question_hash'] == digest(QUESTION) and result['raw_hash'] == digest(wire)
    result['query_effect']['steps'][0]['output']['fields'][0]['label'] = 'changed'
    assert result['query_effect']['steps'][1]['input']['fields'][0]['label'] == '标题'
    assert result['query_effect']['final']['fields'][0]['key'] == 'value'
    assert digest(result) != old_hash and (wire, description) == before


@pytest.mark.parametrize('version', [1, 4, 5])
def test_row_readback_uses_frozen_question_and_saved_effect_never_recomputes(version, monkeypatch):
    checked = validate() if version == 5 else v4.validate_candidate(candidate(), QUESTION, catalog(), EVIDENCE)
    frozen = {'context': {'mode': 'query'}, 'question': QUESTION, 'catalog': catalog(), 'proposal_contract_version': version}
    row = SimpleNamespace(id='saved', version=2, status='needs_review', created_at=datetime.now(timezone.utc),
        data={'request_id': 'original-request', 'frozen': frozen, 'validated': checked})
    monkeypatch.setattr(v5, 'derive_query_effect', lambda *args: pytest.fail('GET must not recompute'))
    view = service.row_view(row)
    assert view['original_question'] == QUESTION and view['wire_contract_version'] == version
    assert view['question_hash'] == digest(QUESTION) and view['candidate_hash'] == digest(checked)
    assert (view['query_effect'] is not None) is (version == 5)
    if version == 5:
        view['query_effect']['selected_fields'].clear()
        assert checked['query_effect']['selected_fields']
