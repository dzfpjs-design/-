"""Future public planner instructions, not a semantic-quality/model benchmark."""
import json
from uuid import uuid4

from generic_agents import multimodal_question_service_v262 as service
from generic_agents import multimodal_query_contracts_v262 as contract
from tests.test_multimodal_query_contracts_v262 import fixture, pixel_intent, sound_intent


def test_public_scope_rules_do_not_invent_date_or_identifier_requirements_or_send_source_values():
    identifiers = [str(uuid4()) for _ in range(5)]
    private = {'records': [{'class_scores': [0.25], 'pixel_values': [99], 'note': '不外发的人工说明'}],
        'scope': {'workspace_id': identifiers[0], 'instance_id': identifiers[1], 'task_id': identifiers[2]},
        'registry_hash': identifiers[3], 'release_id': identifiers[4]}
    registry = {**private, 'facets': {'pixel': {'private': private}, 'task_sound': {'private': private}}}
    question = '本实例已接收的影像成果有多少条？本实例已接收的声音处理有多少份？'
    for task_limited, entity_limited in ((False, False), (True, True)):
        messages = service._messages(question, registry, '2026-10-31', task_limited, entity_limited)
        assert [m['role'] for m in messages] == ['system', 'user']
        system = json.loads(messages[0]['content']); user = json.loads(messages[1]['content'])
        assert user == {'question': question, 'selected_scope': {'instance': '当前真实实例',
            'task_limited': task_limited, 'entity_limited': entity_limited}}
        rules = system['rules']
        assert 'selected_scope由服务器完成真实鉴权并注入，当前实例已确定；不得索要内部实例、任务、对象UUID或其映射，不得把未向模型公开内部标识列为unmet_requirements。' in rules
        assert '原问题未要求日期时查询授权范围内全部已登记成果，不加日期过滤；days与time_quote都null，不得把没有行政日期列为缺项或unmet_requirements。' in rules
        assert 'task_limited/entity_limited为false表示不按任务/对象额外筛选，不是缺少定位；为true表示服务器已选定该任务/对象，也不需要用户提供标识或映射。' in rules
        assert system['catalog']['facets'] == ['pixel', 'task_sound']
        wire = json.dumps(messages, ensure_ascii=False)
        assert all(identity not in wire for identity in identifiers)
        assert all(value not in wire for value in ('records', 'class_scores', 'pixel_values', '不外发的人工说明'))
        # Scope clarity does not authorize invented dates, physical capture
        # clocks, expected-source completeness or deletion of real unmet needs.
        assert any('未知拍摄/录音/采集时间' in rule for rule in rules)
        assert any('原要求未覆盖必须完整列unmet_requirements' in rule for rule in rules)


def test_received_result_counts_are_public_record_semantics_without_extra_independent_or_date_words():
    scope, registry, _ = fixture()
    clauses = ('本实例已接收的影像成果有多少条？', '本实例已接收的声音处理有多少份？')
    question = ''.join(clauses)
    system = json.loads(service._messages(question, registry, '2026-10-31', False, False)[0]['content'])
    for family in ('pixel', 'task_sound'):
        description = system['catalog'][family]
        assert description['count_semantics']['operation'] == 'count' and description['count_semantics']['metric'] is None
        assert description['received_semantics'] == {'reception': 'received',
            'meaning': '已接收表示真实独立数据ACK，无须用户额外说独立'}
        date = description['no_date_semantics']
        assert date['basis'] == 'machine_result_registered' and date['time_quote'] is None
        assert date['registration_days' if family == 'pixel' else 'days'] is None
    rules = system['rules']
    assert any('count不依赖候选数值' in rule for rule in rules)
    assert '声音处理成果Run记录不等于原音文件数、独立声音事件数、声源或动物数量；这些要求不能改为Run计数，应保留unmet_requirements或澄清。' in rules
    assert any('接收状态筛选与日期筛选相互独立' in rule for rule in rules)
    assert any('明确要求的日期无法确定时仍须澄清' in rule for rule in rules)
    assert any('窗口可重叠不是时长、事件或声源数' in rule for rule in rules)
    assert any('声类不是ASR、声纹、物种或风险概率' in rule for rule in rules)
    pixel = pixel_intent(operation='count', metric=None, operation_quote='多少', metric_quote=None)
    sound = sound_intent()
    for subquery, quote in zip((pixel, sound), clauses):
        subquery['question_quote'] = quote
        subquery['intent']['filters']['reception'] = 'received'
        subquery['intent']['grounding'].update(operation_quote='多少', filter_quotes=['已接收'])
    # A model must still produce its own typed plan. This only establishes
    # that ordinary received/count wording has a valid unchanged local IR.
    bound = contract.bind_plan({'schema': contract.INTENT, 'subqueries': [pixel, sound],
        'unmet_requirements': []}, question, scope, registry, reference_day='2026-10-31')
    assert [item['facet'] for item in bound['subqueries']] == ['pixel', 'task_sound']
    assert all(item['ir']['operation'] == 'count' and item['ir']['metric'] is None
        and item['ir']['filters']['reception'] == 'received' for item in bound['subqueries'])
    assert bound['unmet_requirements'] == []
