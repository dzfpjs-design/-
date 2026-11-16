"""Independent adversarial checks for local, exact source-span expansion.

No cloud, database, saved answer or fixed benchmark gold is used or modified.
Run with --noconftest because these are pure module/strict-validator checks.
"""
import copy

import pytest

from generic_agents.citation_expansion import expand_citations
from generic_agents.contracts import digest
from generic_agents.setup_understanding import ResultV2, validate_result


def source(text, *, material='m1', block='b1'):
    return {'material_id': material, 'block_id': block, 'text': text,
            'location': {'body_index': 1}}


def citation(quote, *, material='m1', block='b1'):
    return {'material_id': material, 'block_id': block, 'quote': quote}


def proposal(quote):
    ref = citation(quote)
    return {'domain': '设备管理', 'objects': [], 'questions': [], 'businesses': [{
        'operation': 'upsert', 'original_name': '', 'name': '异常复核',
        'trigger': '收到资料', 'output': '核查记录', 'citations': [ref],
        'inference_reason': '', 'steps': [{
            'key': 'check', 'name': '人工复核', 'purpose': '核查原文',
            'agent': '人工确认', 'role': '值班人员', 'output': '核查结论',
            'next_key': 'end', 'condition': '', 'else_key': 'end',
            'citations': [copy.deepcopy(ref)], 'inference_reason': ''}]}]}


def business_quote(value):
    return value['businesses'][0]['citations'][0]['quote']


def strict(value, blocks):
    return validate_result(value, blocks, {'businesses': []}, ResultV2)


@pytest.mark.parametrize('ellipsis', ['…', '……', '...'])
def test_unique_same_block_expands_actual_contiguous_source_with_audit(ellipsis):
    text = '前言。发现设备异常，须由值班人员人工核查并确认后，再记录归档。附记。'
    abbreviated = '发现设备异常' + ellipsis + '再记录归档'
    raw, blocks = proposal(abbreviated), [source(text)]
    before_raw, before_blocks = copy.deepcopy(raw), copy.deepcopy(blocks)
    actual, audit = expand_citations(raw, blocks)
    expected = '发现设备异常，须由值班人员人工核查并确认后，再记录归档'
    assert actual is not raw and business_quote(actual) == expected
    assert actual['businesses'][0]['steps'][0]['citations'][0]['quote'] == expected
    assert raw == before_raw and blocks == before_blocks
    assert len(audit) == 2
    for entry in audit:
        assert entry['original_quote'] == abbreviated
        assert entry['expanded_quote'] == expected
        assert entry['source_block_hash'] == digest(blocks[0])
        assert entry['path'] and entry['policy']
    assert strict(actual, blocks)['businesses'][0]['name'] == '异常复核'


@pytest.mark.parametrize('text,quote', [
    ('发现设备异常。发现设备异常，再记录归档。', '发现设备异常…再记录归档'),
    ('发现设备异常，再记录归档。再记录归档。', '发现设备异常…再记录归档'),
    ('机台机台机台，必须人工归档。', '机台机台…必须人工归档'),
    ('再记录归档。发现设备异常。', '发现设备异常…再记录归档'),
    ('发现设备异常，再记录归档。', '发现设备异样…再记录归档'),
    ('发现设备异常，再记录归档。', '发现设备异常 再记录归档'),
    ('发现设备异常，再记录归档。', '发现设备异常⋯再记录归档'),
    ('发现设备异常，再记录归档。', '发现设备异常..再记录归档'),
    ('发现设备异常，再记录归档。', '发现设备异常…归档'),
    ('发现设备异常，再记录归档。', '异常…再记录归档'),
    ('发现设备异常，再记录归档。', '…发现设备异常…再记录归档'),
    ('发现设备异常，再记录归档。', '发现设备异常…再记录归档…'),
    ('发现设备异常，再记录归档。', '...发现设备异常...再记录归档'),
    ('发现设备异常，再记录归档。', '发现设备异常...再记录归档...'),
])
def test_ambiguous_out_of_order_changed_or_unanchored_quotes_stay_rejected(text, quote):
    raw, blocks = proposal(quote), [source(text)]
    expanded, audit = expand_citations(raw, blocks)
    assert expanded == raw and audit == []
    with pytest.raises(ValueError):
        strict(expanded, blocks)


def test_expansion_never_searches_other_material_or_block():
    quote = '发现设备异常…再记录归档'
    for blocks in [
        [source('发现设备异常。'), source('再记录归档。', block='b2')],
        [source('未提供指定证据。'), source('发现设备异常，再记录归档。', block='b2')],
        [source('未提供指定证据。'), source('发现设备异常，再记录归档。', material='m2')],
    ]:
        raw = proposal(quote)
        expanded, audit = expand_citations(raw, blocks)
        assert expanded == raw and audit == []
        with pytest.raises(ValueError):
            strict(expanded, blocks)


def test_conflicting_same_source_key_cannot_select_convenient_variant():
    raw = proposal('发现设备异常…再记录归档')
    blocks = [source('发现设备异常，人工核查，再记录归档。'),
              source('发现设备异常，禁止归档，继续排查。')]
    expanded, audit = expand_citations(raw, blocks)
    assert expanded == raw and audit == []
    with pytest.raises(ValueError):
        strict(expanded, blocks)


def test_expanded_span_over_quote_schema_limit_remains_invalid():
    text = '发现设备异常' + '中间限制不得省略。' * 40 + '再记录归档'
    raw, blocks = proposal('发现设备异常…再记录归档'), [source(text)]
    expanded, audit = expand_citations(raw, blocks)
    assert expanded == raw and audit == []
    with pytest.raises(ValueError):
        strict(expanded, blocks)


def test_exact_quotes_including_original_ellipsis_are_not_reinterpreted():
    quote = '发现设备异常……再记录归档'
    raw, blocks = proposal(quote), [source('原文写明：' + quote + '。')]
    expanded, audit = expand_citations(raw, blocks)
    assert expanded == raw and expanded is not raw and audit == []
    assert business_quote(strict(expanded, blocks)) == quote


def test_three_ordered_anchors_expand_without_dropping_middle_restrictions():
    text = '设备异常报告：禁止自动启动设备，必须人工复核；完成审核后，记录归档结论。'
    raw, blocks = proposal('设备异常报告…必须人工复核…记录归档结论'), [source(text)]
    expanded, audit = expand_citations(raw, blocks)
    assert business_quote(expanded) == text[:-1]
    assert '禁止自动启动设备' in business_quote(expanded)
    assert audit and strict(expanded, blocks)


def test_five_unique_ordered_anchors_are_safe_within_the_same_quote_limit():
    text = '设备异常报告：禁止自动启动设备，必须人工复核，确认资料完整后，记录归档结论。'
    raw = proposal('设备异常报告…禁止自动启动…必须人工复核…确认资料完整…记录归档结论')
    expanded, audit = expand_citations(raw, [source(text)])
    assert business_quote(expanded) == text[:-1]
    assert audit and strict(expanded, [source(text)])


@pytest.mark.parametrize('text,quoted,unit', [
    ('设备编号：文本，必填。温度字段：数值，单位℃；振动字段：数值，未说明单位，需人工核对。',
     '温度字段…需人工核对', '℃'),
    ('设备编号：文本，必填。振动字段：数值，单位不是℃，需人工核对。',
     '振动字段…需人工核对', '℃'),
    ('设备编号：文本，必填。振动字段：数值，示例单位℃，需人工核对。',
     '振动字段…需人工核对', '℃'),
    ('设备编号：文本，必填。振动字段：数值，单位待确认，禁止猜为℃，需人工核对。',
     '振动字段…需人工核对', '℃'),
])
def test_expanded_exact_source_cannot_promote_unit_from_negation_example_or_other_field(text, quoted, unit):
    raw = proposal('设备编号：文本，必填。')
    raw['objects'] = [{'name': '设备', 'citations': [citation('设备编号：文本，必填。')],
        'inference_reason': '', 'fields': [
            {'label': '设备编号', 'kind': '文本', 'required': True, 'unit': None,
             'citations': [citation('设备编号：文本，必填。')], 'inference_reason': ''},
            {'label': '振动字段', 'kind': '数值', 'required': False, 'unit': unit,
             'citations': [citation(quoted)], 'inference_reason': ''}]}]
    blocks = [source(text)]
    expanded, audit = expand_citations(raw, blocks)
    assert audit and expanded['objects'][0]['fields'][1]['unit'] == unit
    assert raw['objects'][0]['fields'][1]['citations'][0]['quote'] == quoted
    with pytest.raises(ValueError, match='单位'):
        strict(expanded, blocks)


def test_positive_same_field_unit_remains_explicit_and_can_pass_strict_validation():
    text = '设备编号：文本，必填。温度字段：数值，单位℃，必须原单位保存。'
    raw = proposal('设备编号：文本，必填。')
    raw['objects'] = [{'name': '设备', 'citations': [citation('设备编号：文本，必填。')],
        'inference_reason': '', 'fields': [
            {'label': '设备编号', 'kind': '文本', 'required': True, 'unit': None,
             'citations': [citation('设备编号：文本，必填。')], 'inference_reason': ''},
            {'label': '温度字段', 'kind': '数值', 'required': False, 'unit': '℃',
             'citations': [citation('温度字段…必须原单位保存')], 'inference_reason': ''}]}]
    expanded, audit = expand_citations(raw, [source(text)])
    assert audit and expanded['objects'][0]['fields'][1]['unit'] == '℃'
    assert strict(expanded, [source(text)])['objects'][0]['fields'][1]['unit'] == '℃'


def test_audit_does_not_rewrite_other_proposal_fields_or_invent_missing_text():
    text = '发现设备异常，禁止自动启动设备，必须人工复核，再记录归档。'
    raw = proposal('发现设备异常…再记录归档')
    raw['businesses'][0]['steps'][0]['purpose'] = 'purpose must remain untouched'
    raw['questions'] = ['发现设备异常…再记录归档']
    expanded, audit = expand_citations(raw, [source(text)])
    expected = copy.deepcopy(raw)
    expected['businesses'][0]['citations'][0]['quote'] = text[:-1]
    expected['businesses'][0]['steps'][0]['citations'][0]['quote'] = text[:-1]
    assert expanded == expected and audit
    again, again_audit = expand_citations(expanded, [source(text)])
    assert again == expanded and again_audit == []

