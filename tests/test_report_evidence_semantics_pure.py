"""Synthetic V4 contract tests; no report/provider/DB/approval evidence.

Source is authored only. Root owns all interpreter and test execution.
"""
import copy

import pytest

from generic_agents.report_evidence_plan import build_evidence_source, validate_map_result
from generic_agents.report_evidence_semantics import (
    FullResultV4, FullMergeResultV4, validate_evidence_result, validate_evidence_merge,
)
from generic_agents import report_evidence_semantics as semantics


CONTEXT = {'object_names': [], 'businesses': []}


def catalog():
    return build_evidence_source('a' * 64, [
        ['w:p1', '记录单包含名称字段。名称为必填文本。记录单关联核对单。'],
        ['w:p2', '核对单包含名称字段。名称为必填文本。数量（株）。'],
        ['w:p3', '仅用于检查禁止汇总偷读未提供证据的旁支文字。'],
        ['o:p1', '登记员登记记录后交核对员核对。核对员核对记录并形成核对单。仅在材料齐全时开始核对。'],
    ])


def allowed(c):
    return [ref for ref in c['index'] if not ref.startswith('w:p3/')]


def claim(key='shared_roles', refs=None, kind='职责'):
    return {'key': key, 'kind': kind, 'statement': '合成登记与核对职责，仍需人工理解核对。',
            'span_refs': ['o:p1/s1'] if refs is None else refs,
            'status': 'explicit', 'explanation': ''}


def minimal(c, claims=None):
    return {'schema': 'report-evidence-candidate-v4',
            'source_hash': c['descriptor']['source_hash'],
            'catalog_hash': c['descriptor']['catalog_hash'],
            'global_claims': [] if claims is None else claims,
            'domain': '合成登记示例', 'objects': [], 'relationships': [],
            'businesses': [], 'questions': []}


def example(c):
    def cite(ref):
        return {'span_ref': ref}

    def evidence():
        return {'status': 'explicit', 'citations': [cite('o:p1/s1')], 'explanation': ''}

    def name_field(ref):
        return {'label': '名称', 'kind': '文本', 'required': True, 'unit': None,
                'citations': [cite(ref)], 'inference_reason': ''}

    def step(key, name, role, output, next_key):
        return {'key': key, 'name': name, 'purpose': name, 'agent': '人工确认',
                'role': role, 'output': output, 'next_key': next_key,
                'branch_condition': '', 'else_key': 'end',
                'citations': [cite('o:p1/s1')], 'inference_reason': ''}

    value = minimal(c, [claim()])
    value['objects'] = [
        {'name': '记录单', 'fields': [name_field('w:p1/s1')],
         'citations': [cite('w:p1/s1')], 'inference_reason': ''},
        {'name': '核对单', 'fields': [name_field('w:p2/s1'),
            {'label': '数量', 'kind': '整数', 'required': False, 'unit': '株',
             'citations': [cite('w:p2/s1')], 'inference_reason': ''}],
         'citations': [cite('w:p2/s1')], 'inference_reason': ''},
    ]
    value['relationships'] = [{'name': '记录单关联核对单', 'description': '记录单关联核对单',
        'source_type': '记录单', 'target_type': '核对单', 'cardinality': 'unspecified',
        'citations': [cite('w:p1/s1')], 'inference_reason': ''}]
    value['businesses'] = [{
        'operation': 'upsert', 'original_name': '', 'name': '登记核对',
        'trigger': '收到记录', 'output': '核对单',
        'steps': [step('register', '登记记录', '登记员', '记录', 'check'),
                  step('check', '核对记录', '核对员', '核对单', 'end')],
        'citations': [cite('o:p1/s1')], 'inference_reason': '',
        'participants': [
            {'key': 'registrar', 'agent': '登记员', 'responsibility': '登记记录', 'evidence': evidence()},
            {'key': 'checker', 'agent': '核对员', 'responsibility': '核对记录', 'evidence': evidence()},
        ],
        'interactions': [{'key': 'send', 'from_participant': 'registrar', 'to_participant': 'checker',
            'purpose': '交核对', 'condition': '登记后', 'step_keys': ['register', 'check'], 'evidence': evidence()}],
        'conditions': [{'key': 'ready', 'kind': 'precondition', 'statement': '材料齐全',
            'step_keys': ['check'], 'evidence': evidence()}],
        'coordination': [{'key': 'sequence', 'kind': 'sequence', 'step_keys': ['register', 'check'],
            'start_rule': '登记后核对', 'wait_rule': '', 'join_rule': '', 'evidence': evidence()}],
    }]
    return value


def mutate(value, path, replacement):
    current = value
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = replacement


def merge_case():
    c = catalog()
    first = validate_map_result({'schema': 'report-evidence-map-v1', 'claims': [
        claim('record', ['w:p1/s1'], '对象'), claim('check', ['w:p2/s1'], '对象'),
    ]}, c, ['w:p1/s1', 'w:p2/s1'])
    second = validate_map_result({'schema': 'report-evidence-map-v1', 'claims': [claim()]}, c, ['o:p1/s1'])
    inputs = {f'map-{number}/claims/{index}': original
              for number, batch in enumerate([first, second], 1)
              for index, original in enumerate(batch['claims'])}
    result = {'result': example(c), 'reconciliation': [
        {'input_id': 'map-1/claims/0', 'disposition': 'kept', 'output_kind': 'objects',
         'output_index': 0, 'reason': '保留独立对象及原证据。'},
        {'input_id': 'map-1/claims/1', 'disposition': 'kept', 'output_kind': 'objects',
         'output_index': 1, 'reason': '保留独立对象及原证据。'},
        {'input_id': 'map-2/claims/0', 'disposition': 'kept', 'output_kind': 'global_claims',
         'output_index': 0, 'reason': '全局职责不被塞进某一业务。'},
    ]}
    return c, inputs, result


def test_v4_preserves_full_objects_units_relationships_timing_and_global_claims_without_mutating_any_input():
    c = catalog(); value = example(c); refs = allowed(c)
    before = copy.deepcopy([value, c, refs, CONTEXT])
    result = validate_evidence_result(value, c, refs, CONTEXT)
    assert result == value == FullResultV4.model_validate(value).model_dump()
    assert [value, c, refs, CONTEXT] == before
    assert result['objects'][1]['fields'][1]['unit'] == '株'
    assert result['businesses'][0]['coordination'][0]['kind'] == 'sequence'
    assert result['global_claims'][0]['kind'] == '职责'
    assert 'quote' not in result['businesses'][0]['citations'][0]


@pytest.mark.parametrize('path,replacement', [
    (('objects', 0, 'fields', 0, 'required'), 'true'),
    (('objects', 0, 'fields', 0, 'required'), 1),
    (('objects', 0, 'fields', 0, 'required'), False),
    (('objects', 1, 'fields', 1, 'unit'), '米'),
    (('objects', 1, 'fields', 1, 'label'), '长度'),
    (('objects', 1, 'fields', 1, 'kind'), '文本'),
    (('relationships', 0, 'cardinality'), 'one_per_source'),
    (('relationships', 0, 'target_type'), '未定义对象'),
    (('relationships', 0, 'source_type'), '核对单'),
    (('businesses', 0, 'steps', 0, 'next_key'), 'auto'),
    (('businesses', 0, 'steps', 0, 'next_key'), 'register'),
    (('businesses', 0, 'steps', 0, 'else_key'), 'check'),
    (('businesses', 0, 'steps', 0, 'role'), ''),
    (('businesses', 0, 'interactions', 0, 'to_participant'), 'registrar'),
    (('businesses', 0, 'interactions', 0, 'to_participant'), 'missing'),
    (('businesses', 0, 'conditions', 0, 'step_keys'), ['missing']),
    (('businesses', 0, 'coordination', 0, 'step_keys'), ['register']),
    (('businesses', 0, 'participants', 0, 'evidence', 'citations'), []),
    (('businesses', 0, 'participants', 0, 'evidence', 'status'), 'approved'),
    (('global_claims', 0, 'span_refs'), []),
    (('global_claims', 0, 'statement'), ' '),
    (('global_claims', 0, 'kind'), '账户权限'),
    (('global_claims', 0, 'span_refs'), ['o:p1/s1', 'o:p1/s1']),
    (('questions',), [' ']),
    (('domain',), '\x00'),
    (('domain',), '\ud800'),
    (('domain',), float('nan')),
    (('domain',), float('inf')),
])
def test_v4_inherits_real_legacy_strict_types_unit_relation_and_direction_guards(path, replacement):
    c = catalog(); value = example(c); mutate(value, path, replacement)
    with pytest.raises(ValueError):
        validate_evidence_result(value, c, allowed(c), CONTEXT)


@pytest.mark.parametrize('field', ['source_hash', 'catalog_hash', 'global_claims', 'questions'])
def test_v4_required_fields_are_never_defaulted_or_silently_deleted(field):
    c = catalog(); value = example(c); del value[field]
    with pytest.raises(ValueError):
        validate_evidence_result(value, c, allowed(c), CONTEXT)


def test_only_provided_original_spans_and_both_hash_scopes_are_accepted_with_no_quote_override():
    c = catalog(); value = example(c)
    for field in ['source_hash', 'catalog_hash']:
        bad = copy.deepcopy(value); bad[field] = 'b' * 64
        with pytest.raises(ValueError, match=field):
            validate_evidence_result(bad, c, allowed(c), CONTEXT)
    for refs in [None, tuple(allowed(c)), allowed(c)[:-1], [*allowed(c), allowed(c)[0]]]:
        with pytest.raises(ValueError):
            validate_evidence_result(value, c, refs, CONTEXT)
    for extra in [{'quote': '伪造文字'}, {'source_ref': 'w:p1'}]:
        bad = copy.deepcopy(value); bad['objects'][0]['citations'][0].update(extra)
        with pytest.raises(ValueError):
            validate_evidence_result(bad, c, allowed(c), CONTEXT)
    changed = build_evidence_source(c['descriptor']['source_hash'], [
        [ref, ''.join(item['text'] for item in c['index'].values() if item['source_ref'] == ref)]
        for ref in c['descriptor']['ordered_source_refs']], max_span_chars=20)
    with pytest.raises(ValueError, match='catalog_hash'):
        validate_evidence_result(value, changed, list(changed['index']), CONTEXT)


def test_unknown_and_inferred_global_claims_need_explanations_and_unknowns_still_need_questions():
    c = catalog()
    for status in ['inferred', 'unknown']:
        value = example(c); value['global_claims'][0]['status'] = status
        with pytest.raises(ValueError):
            validate_evidence_result(value, c, allowed(c), CONTEXT)
        value['global_claims'][0]['explanation'] = '仅为推断或尚未明确，仍须人工核对。'
        if status == 'unknown':
            with pytest.raises(ValueError, match='questions'):
                validate_evidence_result(value, c, allowed(c), CONTEXT)
            value['questions'] = ['请核对该职责的实际依据。']
        assert validate_evidence_result(value, c, allowed(c), CONTEXT) == value


def test_temporary_legacy_projection_keeps_distinct_equal_text_spans_and_never_joins_or_returns_aliases(monkeypatch):
    segment = '名称为必填文本。'
    c = build_evidence_source('a' * 64, [['w:p1', segment * 2], ['o:p1', '不提供的文字。']], len(segment))
    refs = ['w:p1/s1', 'w:p1/s2']; citations = [{'span_ref': ref} for ref in refs]
    value = minimal(c)
    value['objects'] = [{'name': '记录单', 'fields': [{'label': '名称', 'kind': '文本',
        'required': True, 'unit': None, 'citations': copy.deepcopy(citations), 'inference_reason': ''}],
        'citations': copy.deepcopy(citations), 'inference_reason': ''}]
    original = semantics.validate_full_result; seen = []

    def inspect(projected, index, context):
        seen.append(copy.deepcopy([projected, index]))
        assert len(index) == 2
        assert all(item['text'] == segment for item in index.values())
        assert len({cite['source_ref'] for cite in projected['objects'][0]['citations']}) == 2
        assert all(cite['quote'] == segment for cite in projected['objects'][0]['citations'])
        return original(projected, index, context)

    monkeypatch.setattr(semantics, 'validate_full_result', inspect)
    assert validate_evidence_result(value, c, refs, CONTEXT) == value
    assert seen
    bad = copy.deepcopy(value); bad['objects'][0]['citations'] = [citations[0], citations[0]]
    with pytest.raises(ValueError, match='重复'):
        validate_evidence_result(bad, c, refs, CONTEXT)


def test_original_codepoints_up_to250_are_used_and_blank_evidence_is_not_approved():
    c = build_evidence_source('a' * 64, [['w:p1', '甲' * 250]], 250)
    value = minimal(c, [claim(refs=['w:p1/s1'])])
    assert validate_evidence_result(value, c, ['w:p1/s1'], CONTEXT) == value
    assert len(c['index']['w:p1/s1']['text']) == 250
    with pytest.raises(ValueError):
        build_evidence_source('a' * 64, [['w:p1', '甲' * 251]], 251)
    blank = build_evidence_source('a' * 64, [['w:p1', ' \n ']])
    with pytest.raises(ValueError, match='空白'):
        validate_evidence_result(minimal(blank, [claim(refs=['w:p1/s1'])]), blank, ['w:p1/s1'], CONTEXT)


def test_merge_has_exact_accounting_and_independent_global_destinations_without_mutating_saved_input_claims():
    c, inputs, merged = merge_case(); before = copy.deepcopy([c, inputs, merged])
    assert validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs) == merged
    assert [c, inputs, merged] == before
    assert merged['reconciliation'][2]['output_kind'] == 'global_claims'
    business = copy.deepcopy(merged); business['reconciliation'][2]['output_kind'] = 'businesses'
    assert validate_evidence_merge(business, c, allowed(c), CONTEXT, inputs) == business


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'extra', 'range', 'kind', 'index', 'bool', 'reason'])
def test_merge_rejects_incomplete_duplicate_invalid_destinations_and_non_strict_indexes(mode):
    c, inputs, merged = merge_case(); entry = merged['reconciliation'][0]
    if mode == 'missing': merged['reconciliation'].pop()
    elif mode == 'duplicate': merged['reconciliation'].append(copy.deepcopy(entry))
    elif mode == 'extra': merged['reconciliation'].append({**entry, 'input_id': 'map-3/claims/0'})
    elif mode == 'range': entry['input_id'] = 'map-25/claims/0'
    elif mode == 'kind': entry['output_kind'] = 'questions'
    elif mode == 'index': entry['output_index'] = 99
    elif mode == 'bool': entry['output_index'] = True
    else: entry['reason'] = ' '
    with pytest.raises(ValueError):
        validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs)


def test_merge_cannot_replace_all_original_refs_or_keep_only_one_of_multiple_input_refs():
    c, inputs, merged = merge_case()
    target = merged['result']['objects'][0]
    target['citations'] = [{'span_ref': 'w:p2/s1'}]
    target['fields'][0]['citations'] = [{'span_ref': 'w:p2/s1'}]
    assert validate_evidence_result(merged['result'], c, allowed(c), CONTEXT)
    with pytest.raises(ValueError, match='全部证据'):
        validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs)
    c, inputs, merged = merge_case(); inputs['map-1/claims/0']['span_refs'].append('o:p1/s1')
    with pytest.raises(ValueError, match='全部证据'):
        validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs)
    merged['result']['objects'][0]['citations'].append({'span_ref': 'o:p1/s1'})
    assert validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs) == merged


def test_merge_uncertain_questions_keep_original_evidence_in_fixed_input_and_never_become_semantic_resolution():
    c, inputs, merged = merge_case(); before = copy.deepcopy(inputs)
    merged['result']['questions'] = ['原对象含义仍待人工核对，原声明及证据保存在汇总输入。']
    merged['reconciliation'][0].update(disposition='uncertain', output_kind='questions', output_index=0)
    assert validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs) == merged
    assert inputs == before
    inputs['map-2/claims/0'].update(status='unknown', explanation='实际职责未知，不能变成已知。')
    merged['reconciliation'][0].update(disposition='kept', output_kind='objects', output_index=0)
    merged['result']['global_claims'][0].update(status='unknown', explanation='实际职责仍未知。')
    merged['result']['questions'] = []
    with pytest.raises(ValueError, match='questions'):
        validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs)
    merged['result']['questions'] = ['保留职责待核问题。']
    merged['reconciliation'][0].update(disposition='uncertain', output_kind='objects')
    with pytest.raises(ValueError):
        validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs)


@pytest.mark.parametrize('original_status,target_status', [
    ('unknown', 'explicit'), ('unknown', 'inferred'), ('inferred', 'explicit'),
])
def test_merge_cannot_wash_unknown_or_inferred_global_claims_into_stronger_status(original_status, target_status):
    c, inputs, merged = merge_case()
    inputs['map-2/claims/0'].update(status=original_status, explanation='原声明尚未确证。')
    target = merged['result']['global_claims'][0]
    target.update(status=target_status, explanation='仍需核对。')
    merged['result']['questions'] = ['原不确定性仍待核。']
    with pytest.raises(ValueError, match='升级'):
        validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs)
    target['status'] = original_status
    assert validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs) == merged


@pytest.mark.parametrize('status', ['inferred', 'unknown'])
def test_merge_nonexplicit_business_destination_keeps_inference_explanation_and_unknown_question(status):
    c, inputs, merged = merge_case()
    inputs['map-2/claims/0'].update(status=status, explanation='原声明不确定。')
    merged['reconciliation'][2]['output_kind'] = 'businesses'
    merged['result']['questions'] = ['未知职责待核。'] if status == 'unknown' else []
    with pytest.raises(ValueError, match='仍须解释'):
        validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs)
    merged['result']['businesses'][0]['inference_reason'] = '该候选业务仍基于未确证声明，请人工核对。'
    assert validate_evidence_merge(merged, c, allowed(c), CONTEXT, inputs) == merged


def test_merge_allowlist_is_exactly_actual_input_claim_refs_not_the_whole_catalog_or_a_missing_subset():
    c, inputs, merged = merge_case()
    for refs in [None, list(c['index']), allowed(c)[:-1]]:
        with pytest.raises(ValueError):
            validate_evidence_merge(merged, c, refs, CONTEXT, inputs)
    for bad_identity in ['map-0/claims/0', 'map-25/claims/0', 'map-1/claims/80', 'map-1/claims/01', 'map-1/claims/-1']:
        bad = copy.deepcopy(inputs); bad[bad_identity] = bad.pop('map-1/claims/0')
        with pytest.raises(ValueError):
            validate_evidence_merge(merged, c, allowed(c), CONTEXT, bad)
    holes = copy.deepcopy(inputs); holes.pop('map-1/claims/0')
    with pytest.raises(ValueError, match='连续'):
        validate_evidence_merge(merged, c, allowed(c), CONTEXT, holes)


def test_final_global_limit160_and_full24_by80_accounting_are_bounded_without_truncation_or_defaults():
    c = build_evidence_source('a' * 64, [['w:p1', '合成共享职责声明。']])
    value = minimal(c, [claim('c' + str(i), ['w:p1/s1']) for i in range(160)])
    assert validate_evidence_result(value, c, ['w:p1/s1'], CONTEXT) == value
    value['global_claims'].append(claim('overflow', ['w:p1/s1']))
    with pytest.raises(ValueError): FullResultV4.model_validate(value)
    inputs = {f'map-{batch}/claims/{i}': claim('c' + str(i), ['w:p1/s1'])
              for batch in range(1, 25) for i in range(80)}
    merged = {'result': minimal(c, [claim(refs=['w:p1/s1'])]), 'reconciliation': [
        {'input_id': identity, 'disposition': 'duplicate', 'output_kind': 'global_claims',
         'output_index': 0, 'reason': '原输入独立保留；结构去重不证明语义等价。'} for identity in inputs]}
    result = validate_evidence_merge(merged, c, ['w:p1/s1'], CONTEXT, inputs)
    assert len(result['reconciliation']) == 1920
    merged['reconciliation'].append(copy.deepcopy(merged['reconciliation'][0]))
    with pytest.raises(ValueError): FullMergeResultV4.model_validate(merged)
