"""Pure v3 semantics: synthetic text only, no provider/DB/source I/O.

example_result is the portable, complete UI contract example.  It is not a
real report answer or a quality gold, and does not establish an actual role.
The root alone executes this file.
"""
import copy
import hashlib
from pathlib import Path

import pytest

from generic_agents.report_full_reading_semantics import (
    FULL_MERGE_TASK, FULL_READING_TASK, FullReportResult, validate_full_result,
)
from generic_agents import report_full_reading_semantics as semantics


def example_source_index():
    return {
        'w:p1': {'text': '记录单包含名称字段。名称为必填文本。记录单关联核对单。', 'kind': 'paragraph'},
        'w:p2': {'text': '核对单包含名称字段。名称为必填文本。数量（株）。', 'kind': 'paragraph'},
        'o:p1': {'text': '登记员登记记录后交核对员核对。核对员核对记录并形成核对单。仅在材料齐全时开始核对。', 'kind': 'ocr_page'},
    }


def example_result():
    """All fields are required; object/relation types retain the old capability."""
    def cite(ref, quote):
        return {'source_ref': ref, 'quote': quote}

    def evidence(quote):
        return {'status': 'explicit', 'citations': [cite('o:p1', quote)], 'explanation': ''}

    def name_field(ref):
        return {'label': '名称', 'kind': '文本', 'required': True, 'unit': None,
                'citations': [cite(ref, '名称为必填文本。')], 'inference_reason': ''}

    def step(key, name, role, output, next_key, quote):
        return {'key': key, 'name': name, 'purpose': name, 'agent': '人工确认',
                'role': role, 'output': output, 'next_key': next_key,
                'branch_condition': '', 'else_key': 'end',
                'citations': [cite('o:p1', quote)], 'inference_reason': ''}

    return {
        'schema': 'report-business-candidate-v3', 'domain': '合成登记核对示例',
        'objects': [
            {'name': '记录单', 'fields': [name_field('w:p1')],
             'citations': [cite('w:p1', '记录单包含名称字段。')], 'inference_reason': ''},
            {'name': '核对单', 'fields': [name_field('w:p2'),
                {'label': '数量', 'kind': '整数', 'required': False, 'unit': '株',
                 'citations': [cite('w:p2', '数量（株）。')], 'inference_reason': ''}],
             'citations': [cite('w:p2', '核对单包含名称字段。')], 'inference_reason': ''},
        ],
        'relationships': [{'name': '记录单关联核对单', 'description': '记录单关联核对单',
            'source_type': '记录单', 'target_type': '核对单', 'cardinality': 'unspecified',
            'citations': [cite('w:p1', '记录单关联核对单。')], 'inference_reason': ''}],
        'businesses': [{
            'operation': 'upsert', 'original_name': '', 'name': '登记核对',
            'trigger': '登记员登记记录', 'output': '核对单',
            'steps': [step('register', '登记记录', '登记员', '记录', 'check', '登记员登记记录后交核对员核对。'),
                      step('check', '核对记录', '核对员', '核对单', 'end', '核对员核对记录并形成核对单。')],
            'citations': [cite('o:p1', '登记员登记记录后交核对员核对。')], 'inference_reason': '',
            'participants': [
                {'key': 'registrar', 'agent': '登记员', 'responsibility': '登记记录',
                 'evidence': evidence('登记员登记记录后交核对员核对。')},
                {'key': 'checker', 'agent': '核对员', 'responsibility': '核对记录并形成核对单',
                 'evidence': evidence('核对员核对记录并形成核对单。')},
            ],
            'interactions': [{'key': 'send', 'from_participant': 'registrar', 'to_participant': 'checker',
                'purpose': '交核对员核对', 'condition': '登记记录后', 'step_keys': ['register', 'check'],
                'evidence': evidence('登记员登记记录后交核对员核对。')}],
            'conditions': [{'key': 'complete_materials', 'kind': 'precondition',
                'statement': '材料齐全', 'step_keys': ['check'],
                'evidence': evidence('仅在材料齐全时开始核对。')}],
            'coordination': [{'key': 'register_then_check', 'kind': 'sequence',
                'step_keys': ['register', 'check'], 'start_rule': '登记记录后交核对员核对',
                'wait_rule': '', 'join_rule': '',
                'evidence': evidence('登记员登记记录后交核对员核对。')}],
        }],
        'questions': [],
    }


def test_full_example_retains_every_object_relationship_and_semantic_field_without_mutation():
    value = example_result(); original = copy.deepcopy(value)
    result = validate_full_result(value, example_source_index(), {'object_names': [], 'businesses': []})
    assert result == original == value
    assert FullReportResult.model_validate(value).model_dump() == value
    assert result['objects'][1]['fields'][1]['unit'] == '株'
    assert result['businesses'][0]['interactions'][0]['from_participant'] == 'registrar'


def validate(value=None, sources=None, context=None):
    return validate_full_result(example_result() if value is None else value,
        example_source_index() if sources is None else sources,
        {'object_names': [], 'businesses': []} if context is None else context)


@pytest.mark.parametrize('path,value', [
    (('schema',), 'report-business-candidate-v2'),
    (('objects', 0, 'fields', 0, 'required'), 'true'),
    (('objects', 0, 'fields', 0, 'required'), 1),
    (('businesses', 0, 'operation'), 'remove'),
    (('businesses', 0, 'original_name'), '已有业务'),
    (('businesses', 0, 'steps', 0, 'next_key'), 'auto'),
    (('businesses', 0, 'steps', 0, 'next_key'), 'register'),
    (('businesses', 0, 'steps', 0, 'else_key'), 'missing'),
    (('businesses', 0, 'steps', 0, 'else_key'), 'check'),
    (('businesses', 0, 'steps', 0, 'role'), ''),
    (('businesses', 0, 'steps', 0, 'name'), ' '),
    (('businesses', 0, 'steps', 0, 'output'), ' '),
    (('businesses', 0, 'interactions', 0, 'to_participant'), 'missing'),
    (('businesses', 0, 'interactions', 0, 'to_participant'), 'registrar'),
    (('businesses', 0, 'conditions', 0, 'step_keys'), ['missing']),
    (('businesses', 0, 'conditions', 0, 'statement'), ' '),
    (('businesses', 0, 'interactions', 0, 'purpose'), ' '),
    (('businesses', 0, 'coordination', 0, 'step_keys'), ['register', 'register']),
    (('businesses', 0, 'coordination', 0, 'step_keys'), ['register']),
    (('businesses', 0, 'coordination', 0, 'kind'), 'dag'),
    (('businesses', 0, 'participants', 0, 'evidence', 'citations'), []),
    (('businesses', 0, 'participants', 0, 'evidence', 'status'), 'proven'),
    (('relationships', 0, 'target_type'), '不存在的对象'),
    (('relationships', 0, 'cardinality'), 'one_per_source'),
    (('relationships', 0, 'source_type'), '核对单'),
    (('objects', 1, 'fields', 1, 'label'), '长度'),
    (('objects', 1, 'fields', 1, 'unit'), '米'),
    (('objects', 1, 'fields', 1, 'kind'), '文本'),
    (('objects', 0, 'fields', 0, 'required'), False),
    (('questions',), ['   ']),
    (('domain',), '\x00'),
    (('domain',), '\ud800'),
    (('domain',), float('nan')),
    (('domain',), float('inf')),
])
def test_invalid_type_direction_reference_unit_or_unsafe_text_fails_without_editing_input(path, value):
    result = example_result(); node = result
    for key in path[:-1]: node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValueError): validate(result)
    # Validation never removes/repairs the offending field to force acceptance.
    assert node[path[-1]] is value


@pytest.mark.parametrize('change', ['extra', 'missing', 'old_condition', 'old_block', 'wrong_view', 'wrong_quote', 'space_quote'])
def test_closed_schema_and_exact_private_view_citations(change):
    result = example_result()
    step = result['businesses'][0]['steps'][0]
    if change == 'extra': result['can_apply'] = False
    if change == 'missing': del result['businesses'][0]['coordination']
    if change == 'old_condition': step['condition'] = step.pop('branch_condition')
    if change == 'old_block': step['citations'] = [{'material_id': 'id', 'block_id': 'p1', 'quote': '登记员'}]
    if change == 'wrong_view': step['citations'][0]['source_ref'] = 'w:p1'
    if change == 'wrong_quote': step['citations'][0]['quote'] = '登记员自动核对'
    if change == 'space_quote': step['citations'][0]['quote'] = '登记员 登记记录后交核对员核对。'
    with pytest.raises(ValueError): validate(result)


def test_cross_region_quote_matches_only_exact_joined_page_text_not_inserted_whitespace():
    result = example_result(); sources = example_source_index()
    sources['o:p1']['regions'] = [{'text': '登记员登记记录'}, {'text': '后交核对员核对。'},
                                {'text': '核对员核对记录并形成核对单。'}, {'text': '仅在材料齐全时开始核对。'}]
    assert sources['o:p1']['text'] == ''.join(region['text'] for region in sources['o:p1']['regions'])
    assert validate(result, sources) == result
    result['businesses'][0]['citations'][0]['quote'] = '登记员登记记录\n后交核对员核对。'
    with pytest.raises(ValueError): validate(result, sources)


def test_unknown_partial_work_is_retained_with_question_without_a_placeholder_step():
    result = example_result(); business = result['businesses'][0]
    business['steps'] = []; business['interactions'] = []; business['conditions'] = []
    business['coordination'] = []; business['inference_reason'] = '资料只明确职责，流程待核。'
    result['questions'] = ['需要核对登记与核对的具体流程。']
    assert validate(result) == result
    result['questions'] = []
    with pytest.raises(ValueError, match='questions'): validate(result)


@pytest.mark.parametrize('status', ['inferred', 'unknown'])
def test_semantic_uncertainty_needs_explanation_and_unknown_needs_a_question(status):
    result = example_result(); evidence = result['businesses'][0]['participants'][0]['evidence']
    evidence.update(status=status, citations=[], explanation='')
    with pytest.raises(ValueError): validate(result)
    evidence['explanation'] = '资料没有明确职责边界，供人工核对。'
    if status == 'unknown':
        with pytest.raises(ValueError, match='questions'): validate(result)
        result['questions'] = ['登记职责边界未明确。']
    assert validate(result) == result


def test_parallel_preconditions_and_post_completion_branch_are_distinct_and_not_inferred_from_order():
    result = example_result(); business = result['businesses'][0]
    # Private, explicitly labelled inference is representable; no runtime DAG is
    # created and the validator makes no semantic assertion about this example.
    coordination = business['coordination'][0]
    coordination.update(kind='parallel', start_rule='拟议同时开始，待人核对', wait_rule='互不等待，待核对', join_rule='均完成后合并，待核对',
        evidence={'status': 'inferred', 'citations': [], 'explanation': '可选协同建议，原文未明确并行。'})
    business['steps'][0]['branch_condition'] = '登记输出完整'
    business['steps'][0]['else_key'] = 'end'
    result['questions'] = ['是否允许这两项工作并行及如何汇合？']
    before = copy.deepcopy(result)
    assert validate(result) == before
    assert before['businesses'][0]['conditions'][0]['kind'] == 'precondition'
    assert before['businesses'][0]['steps'][0]['branch_condition'] == '登记输出完整'


def test_participant_labels_never_become_independent_accounts_or_step_executor_enums():
    result = example_result(); participants = result['businesses'][0]['participants']
    participants[1]['agent'] = participants[0]['agent']
    assert validate(result) == result  # Same role is not proof of distinct people.
    result['businesses'][0]['steps'][0]['agent'] = '登记员'
    with pytest.raises(ValueError): validate(result)


@pytest.mark.parametrize('collection,key', [('objects', 'name'), ('relationships', 'name'), ('businesses', 'name')])
def test_duplicate_top_level_names_are_not_silently_merged(collection, key):
    result = example_result(); result[collection].append(copy.deepcopy(result[collection][0]))
    with pytest.raises(ValueError): validate(result)


def test_existing_object_types_cannot_be_overwritten_but_can_be_relation_endpoints():
    result = example_result()
    with pytest.raises(ValueError): validate(result, context={'object_names': ['记录单']})
    result['objects'] = result['objects'][1:]
    assert validate(result, context={'object_names': ['记录单']}) == result


def test_unit_declaration_cannot_be_negated_or_borrowed_from_another_field():
    result = example_result(); sources = example_source_index()
    field = result['objects'][1]['fields'][1]
    for quote in ['数量单位未知，株是另一个字段的示例。', '长度（米）；数量待定。']:
        sources['w:p3'] = {'text': quote}
        field['citations'] = [{'source_ref': 'w:p3', 'quote': quote}]
        with pytest.raises(ValueError, match='unit'): validate(result, sources)


def merge_example():
    from generic_agents.report_full_reading_semantics import validate_full_merge
    result = example_result()
    inputs = {f'extract-1/{kind}/{index}': copy.deepcopy(item)
        for kind in ('objects', 'relationships', 'businesses', 'questions')
        for index, item in enumerate(result[kind])}
    merged = {'result': result, 'reconciliation': [
        {'input_id': key, 'disposition': 'kept', 'output_kind': key.split('/')[1],
         'output_index': int(key.split('/')[2]), 'reason': '保留该合成抽取条目。'} for key in inputs]}
    return validate_full_merge, inputs, merged


def test_merge_exhaustively_accounts_for_saved_inputs_and_preserves_unknown_as_question():
    validator, inputs, merged = merge_example()
    assert validator(merged, example_source_index(), {}, inputs) == merged
    inputs['extract-2/businesses/0'] = copy.deepcopy(merged['result']['businesses'][0])
    merged['reconciliation'].append({'input_id': 'extract-2/businesses/0', 'disposition': 'duplicate',
        'output_kind': 'businesses', 'output_index': 0, 'reason': '两个合成批次的相同条目合并，仍需人核语义。'})
    assert validator(merged, example_source_index(), {}, inputs) == merged
    inputs['extract-2/questions/0'] = '两个抽取条目是否存在职责冲突？'
    merged['result']['questions'].append(inputs['extract-2/questions/0'])
    merged['reconciliation'].append({'input_id': 'extract-2/questions/0', 'disposition': 'uncertain',
        'output_kind': 'questions', 'output_index': 0, 'reason': '保留待人工核对的问题。'})
    assert validator(merged, example_source_index(), {}, inputs) == merged


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'extra', 'index', 'bool_index', 'kind', 'uncertain_not_question', 'empty_reason'])
def test_merge_rejects_dropped_twice_counted_or_nonexistent_destinations(change):
    validator, inputs, merged = merge_example()
    first = merged['reconciliation'][0]
    if change == 'missing': merged['reconciliation'].pop()
    if change == 'duplicate': merged['reconciliation'].append(copy.deepcopy(first))
    if change == 'extra': first['input_id'] = 'extract-2/objects/0'
    if change == 'index': first['output_index'] = 40
    if change == 'bool_index': first['output_index'] = True
    if change == 'kind': first.update(output_kind='businesses', output_index=0)
    if change == 'uncertain_not_question': first['disposition'] = 'uncertain'
    if change == 'empty_reason': first['reason'] = ' '
    with pytest.raises(ValueError): validator(merged, example_source_index(), {}, inputs)


def test_merge_can_keep_input_business_and_add_a_new_cross_extraction_conflict_question():
    validator, inputs, merged = merge_example()
    inputs['extract-2/businesses/0'] = copy.deepcopy(merged['result']['businesses'][0])
    merged['reconciliation'].append({'input_id': 'extract-2/businesses/0', 'disposition': 'duplicate',
        'output_kind': 'businesses', 'output_index': 0, 'reason': '保留业务，批次之间的解释分歧另列问题。'})
    merged['result']['questions'].append('两批抽取对登记职责边界是否存在不同解释？')
    # An input's one accounting entry must not force deletion of either the
    # useful business or a new question raised by comparing two extractions.
    assert validator(merged, example_source_index(), {}, inputs) == merged
    assert merged['result']['businesses'] and merged['result']['questions']


def test_required_schema_and_generic_tasks_do_not_introduce_legacy_defaults_or_report_gold():
    schema = FullReportResult.model_json_schema()
    assert set(schema['required']) == {'schema', 'domain', 'objects', 'relationships', 'businesses', 'questions'}
    step = schema['$defs']['FullStep']
    assert 'branch_condition' in step['required'] and 'condition' not in step['properties']
    assert 'auto' not in FULL_READING_TASK.split('next_key只')[-1].split('。')[0].replace('禁止auto', '')
    assert 'reconciliation' in FULL_MERGE_TASK
    assert all(text not in FULL_READING_TASK + FULL_MERGE_TASK for text in ['F11', '漳州', '林场', '235', '192'])
    assert 'source_ref' in FULL_READING_TASK and '不是执行器' in FULL_READING_TASK


def test_v1_literal_declarations_remain_byte_exact_and_v2_only_adds_generic_contract():
    # This independent pin was taken from the two original task declarations
    # before versioning; inverse identifier renames must recover those bytes.
    source = Path(semantics.__file__).read_text(encoding='utf-8')
    original = source[source.index('FULL_READING_TASK_V1 = ('):source.index('_TASK_CONTRACT_V2 = (')]
    original = original.replace('FULL_READING_TASK_V1', 'FULL_READING_TASK').replace(
        'FULL_MERGE_TASK_V1', 'FULL_MERGE_TASK').rstrip() + '\n'
    assert hashlib.sha256(original.encode()).hexdigest() == 'dc00430ca923ff7022a985c45784edf1fba0e1cc26f6ee8275dea044b2fc9ea6'
    assert FULL_READING_TASK == semantics.FULL_READING_TASK_V2
    assert FULL_MERGE_TASK == semantics.FULL_MERGE_TASK_V2
    for old, current in [(semantics.FULL_READING_TASK_V1, FULL_READING_TASK),
                         (semantics.FULL_MERGE_TASK_V1, FULL_MERGE_TASK)]:
        assert current.startswith(old) and current != old
        addition = current[len(old):]
        assert 'next_key和else_key' in addition and 'else_key必须填end' in addition
        assert 'context.businesses' in addition and '仅供参考' in addition
        assert all(token not in addition for token in ['F11', '林场', '235', '192', '立即响应'])


def test_empty_else_key_is_still_rejected_without_normalizing_original_result():
    result = example_result()
    result['businesses'][0]['steps'][0]['else_key'] = ''
    original = copy.deepcopy(result)
    with pytest.raises(ValueError):
        validate_full_result(result, example_source_index(), {})
    assert result == original


def test_v2_citation_contract_requires_short_contiguous_verbatim_same_block_or_uncertainty():
    for old, current in [(semantics.FULL_READING_TASK_V1, FULL_READING_TASK),
                         (semantics.FULL_MERGE_TASK_V1, FULL_MERGE_TASK)]:
        addition = current[len(old):]
        assert 'source_ref必须精确来自给定矩阵块' in addition
        assert 'quote选择同一块内短而连续的原文片段' in addition
        assert '保留原文字词和标点' in addition
        assert '不改写或概括，不拼接不连续片段' in addition
        assert '找不到直接支持文本时保留questions或unknown，不能伪造引文' in addition
        assert 'source_ref必须精确来自给定矩阵块' not in old

