"""Pure two-facet intent/outer-budget tests, not DB source authorization.

The small native-shaped dictionaries below exercise only the outer contract;
production capture/history also call each original provider's complete checker.
"""
import copy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from generic_agents.contracts import digest
from generic_agents import multimodal_query_contracts_v262 as c
from generic_agents import pixel_question_contracts_v252 as old_pixel


def fixture():
    scope = {'instance_id': str(uuid4()), 'authorization_release_id': str(uuid4()), 'task_id': None, 'entity_id': None}
    workspace = str(uuid4()); actor = str(uuid4()); descriptors = {}
    for facet, (family, key) in c.FACETS.items():
        descriptors[facet] = {'facet_id': facet, 'source_family': family, 'data_plan_key': key,
            'data_plan_hash': 'a' * 64, 'bindings': [{'business_id': 'business', 'step_id': 'step', 'node_id': None,
                'capability_id': str(uuid4()), 'capability_hash': 'b' * 64, 'release_version': '1.0.0', 'output_schema_hash': 'c' * 64}]}
    registry = {'schema': c.REGISTRY, 'workspace_id': workspace, 'instance_id': scope['instance_id'],
        'authorization_release_id': scope['authorization_release_id'], 'configuration_hash': 'd' * 64, 'facets': descriptors}
    registry['registry_hash'] = digest(registry)
    facets = {}
    for facet in descriptors:
        facets[facet] = {'workspace_id': workspace, 'captured_by': actor, 'instance_id': scope['instance_id'],
            'scope': {'task_id': None, 'entity_id': None}, 'records': [], 'snapshot_hash': 'e' * 64,
            'inventory': {'run_ids' if facet == 'pixel' else 'scanned_run_ids': []},
            'captured_at' if facet == 'pixel' else 'as_of': '2026-10-31T00:00:00+00:00'}
    body = {'schema': c.SNAPSHOT, 'workspace_id': workspace, 'captured_by': actor, 'scope': scope,
        'as_of': '2026-10-31T00:00:00+00:00', 'registry': registry, 'facets': facets,
        'record_count': 0, 'expected_sources_verified': False}
    body['snapshot_hash'] = digest(body)
    return scope, registry, body


def sound_intent():
    return {'facet': 'task_sound', 'question_quote': '声音成果还有多少等待独立接收？',
        'intent': {'operation': 'count', 'metric': None, 'group_by': 'none',
        'time': {'basis': 'machine_result_registered', 'days': None, 'timezone': 'Asia/Shanghai'},
        'filters': {'reception': 'waiting_data'}, 'grounding': {'operation_quote': '多少',
            'metric_quote': None, 'time_quote': None, 'filter_quotes': ['等待']}, 'limitations': []}}


def pixel_intent(*, operation='sum', metric='candidate_pixels', operation_quote='合计', metric_quote='候选像素'):
    return {'facet': 'pixel', 'question_quote': '已接收成果的候选像素合计多少？',
        'intent': {'operation': operation, 'metric': metric, 'group_by': 'none',
        'time': {'basis': 'machine_result_registered', 'registration_days': None, 'timezone': 'Asia/Shanghai'},
        'filters': {'reception': 'received' if operation == 'sum' else 'any', 'human_completion': 'any'},
        'grounding': {'operation_quote': operation_quote, 'metric_quote': metric_quote,
            'time_quote': None, 'filter_quotes': ['已接收'] if operation == 'sum' else []}, 'limitations': []}}


def test_real_pixel_candidate_envelope_adapter_retains_exact_original_v252_ir():
    scope, registry, _ = fixture(); question = '已接收成果的候选像素合计多少？'
    subquery = pixel_intent(); value = {'schema': c.INTENT, 'subqueries': [subquery], 'unmet_requirements': []}
    expected = old_pixel.validate_intent({'status': 'candidate', 'ir': copy.deepcopy(subquery['intent']),
        'explanation': '单元测试显式验证输入，不是模型调用'}, question, scope)
    result = c.bind_plan(value, question, scope, registry)
    assert result['subqueries'][0] == {'facet': 'pixel', 'question_quote': question, 'ir': expected}
    assert digest(result['subqueries'][0]['ir']) == digest(expected)


def test_mixed_pixel_and_sound_read_subqueries_are_both_bound_to_actual_server_scope():
    scope, registry, _ = fixture(); question = '列出像素成果，并查看声音成果。'
    pixel = pixel_intent(operation='read', metric=None, operation_quote='列出', metric_quote=None)
    sound = sound_intent(); sound['intent']['operation'] = 'read'; sound['intent']['filters']['reception'] = 'any'
    pixel['question_quote'] = '列出像素成果'; sound['question_quote'] = '查看声音成果'
    sound['intent']['grounding'].update(operation_quote='查看', filter_quotes=[])
    value = {'schema': c.INTENT, 'subqueries': [pixel, sound], 'unmet_requirements': []}
    result = c.bind_plan(value, question, scope, registry)
    assert [item['facet'] for item in result['subqueries']] == ['pixel', 'task_sound']
    assert all(item['ir']['scope'] == scope for item in result['subqueries'])


def test_real_mixed_sum_and_waiting_count_use_original_contiguous_clauses_not_rewritten_questions():
    scope, registry, _ = fixture(); pixel = pixel_intent(); sound = sound_intent()
    question = pixel['question_quote'] + sound['question_quote'] + '还需要判断动物物种。'
    unsupported = ['物种识别不受当前两个机器候选来源支持']
    value = {'schema': c.INTENT, 'subqueries': [pixel, sound], 'unmet_requirements': unsupported}
    candidate = {'status': 'candidate', 'ir': copy.deepcopy(pixel['intent']), 'explanation': '明确单元输入'}
    # The old whole-question guard correctly rejects a waiting-data clause in
    # a pixel sum. It remains unchanged; the new wrapper selects actual clauses.
    with pytest.raises(ValueError): old_pixel.validate_intent(candidate, question, scope)
    native = old_pixel.validate_intent(candidate, pixel['question_quote'], scope)
    plan = c.bind_plan(value, question, scope, registry)
    assert plan['subqueries'][0]['ir'] == native
    assert plan['subqueries'][1]['ir']['filters']['reception'] == 'waiting_data'
    assert [item['question_quote'] for item in plan['subqueries']] == [pixel['question_quote'], sound['question_quote']]
    assert plan['question_hash'] == digest(question) and plan['unmet_requirements'] == unsupported
    assert 'semantic_coverage_verified' not in plan  # No structural check is a semantic-completeness claim.


@pytest.mark.parametrize('fragment', ['', '   ', '声音成果等待的数量？',
    '声音成果还有多少……等待独立接收？', '声音成果还有多少等 待独立接收？',
    '声音成果还有多少等待独立接收？附加伪造要求', '声音', True, 1, '字' * 4001])
def test_fragment_is_required_literal_contiguous_and_cannot_be_forged_or_paraphrased(fragment):
    scope, registry, _ = fixture(); query = sound_intent(); query['question_quote'] = fragment
    value = {'schema': c.INTENT, 'subqueries': [query], 'unmet_requirements': []}
    with pytest.raises((ValueError, ValidationError)):
        c.bind_plan(value, '声音成果还有多少等待独立接收？', scope, registry)


def test_new_sound_time_bases_are_explicit_and_do_not_replace_unknown_recording_time():
    for basis in ('machine_result_registered', 'independent_data_received', 'model_completed'):
        subquery = sound_intent(); subquery['intent']['time']['basis'] = basis
        assert c.SoundIntent.model_validate(subquery).intent['time']['basis'] == basis
    subquery['intent']['time']['basis'] = 'recorded_at'
    with pytest.raises((ValueError, ValidationError)): c.SoundIntent.model_validate(subquery)


def test_closed_sound_intent_server_scope_and_registry_are_injected_without_mutating_input():
    scope, registry, _ = fixture()
    model = {'schema': c.INTENT, 'subqueries': [sound_intent()], 'unmet_requirements': []}
    before = copy.deepcopy(model)
    plan = c.bind_plan(model, '声音成果还有多少等待独立接收？', scope, registry)
    assert plan['schema'] == c.PLAN and plan['scope'] == scope and plan['registry_hash'] == registry['registry_hash']
    assert plan['subqueries'][0]['ir']['scope'] == scope
    assert plan['plan_hash'] == digest({key: value for key, value in plan.items() if key != 'plan_hash'})
    assert model == before


@pytest.mark.parametrize('damage', ['model_scope', 'fake_facet', 'duplicate_facet', 'recording_time',
    'risk_metric', 'sum_pending', 'date_axis_group', 'bad_date', 'unsupported_id'])
def test_model_cannot_choose_platform_scope_or_invent_time_probability_or_provider(damage):
    scope, registry, _ = fixture(); sub = sound_intent()
    value = {'schema': c.INTENT, 'subqueries': [sub], 'unmet_requirements': []}
    if damage == 'model_scope': value['scope'] = scope
    elif damage == 'fake_facet': sub['facet'] = 'video'
    elif damage == 'duplicate_facet': value['subqueries'].append(copy.deepcopy(sub))
    elif damage == 'recording_time': sub['intent']['time']['basis'] = 'recorded_at'
    elif damage == 'risk_metric': sub['intent']['metric'] = 'risk_probability'
    elif damage == 'sum_pending': sub['intent'].update(operation='sum', metric='candidate_window_count')
    elif damage == 'date_axis_group':
        sub['intent']['group_by'] = 'registration_day'; sub['intent']['time']['basis'] = 'independent_data_received'
    elif damage == 'bad_date': sub['intent']['time']['days'] = ['2026-02-31']
    else: sub['intent']['capability_id'] = str(uuid4())
    with pytest.raises((ValueError, ValidationError)):
        c.ModelIntent.model_validate(value)


def test_real_source_catalog_absence_is_not_zero_and_plan_grounding_must_quote_question():
    scope, registry, _ = fixture(); value = {'schema': c.INTENT, 'subqueries': [sound_intent()], 'unmet_requirements': []}
    registry['facets'].pop('task_sound'); registry['registry_hash'] = digest({k: v for k, v in registry.items() if k != 'registry_hash'})
    with pytest.raises(ValueError): c.bind_plan(value, '声音成果还有多少等待独立接收？', scope, registry)
    _, registry, _ = fixture(); registry.update(instance_id=scope['instance_id'], authorization_release_id=scope['authorization_release_id'])
    registry['registry_hash'] = digest({k: v for k, v in registry.items() if k != 'registry_hash'})
    value['subqueries'][0]['intent']['grounding']['operation_quote'] = '不在原问题里的字符串'
    with pytest.raises(ValueError): c.bind_plan(value, '声音成果还有多少等待独立接收？', scope, registry)


def test_total_inventory_limit_is_shared_not_one_thousand_for_each_facet():
    _, _, value = fixture()
    for facet in value['facets']:
        ids = [str(uuid4()) for _ in range(500)]
        value['facets'][facet]['inventory']['run_ids' if facet == 'pixel' else 'scanned_run_ids'] = ids
    value['snapshot_hash'] = digest({k: v for k, v in value.items() if k != 'snapshot_hash'})
    assert c.checked_snapshot(value) == value
    value['facets']['task_sound']['inventory']['scanned_run_ids'].append(str(uuid4()))
    value['snapshot_hash'] = digest({k: v for k, v in value.items() if k != 'snapshot_hash'})
    with pytest.raises(ValueError): c.checked_snapshot(value)


@pytest.mark.parametrize('damage', ['other_instant', 'other_scope', 'false_as_one', 'extra', 'bad_hash', 'shared_bytes'])
def test_outer_snapshot_is_closed_same_instant_same_scope_and_combined_bytes_bounded(damage):
    _, _, value = fixture()
    if damage == 'other_instant': value['facets']['task_sound']['as_of'] = '2026-10-31T00:00:01+00:00'
    elif damage == 'other_scope': value['facets']['task_sound']['scope']['task_id'] = str(uuid4())
    elif damage == 'false_as_one': value['expected_sources_verified'] = 0
    elif damage == 'extra': value['release_authorized'] = True
    elif damage == 'bad_hash': value['snapshot_hash'] = '0' * 64
    else:
        # Each native payload is individually below 16MiB; together they are not.
        value['facets']['pixel']['padding'] = 'x' * (8 * 1024 * 1024)
        value['facets']['task_sound']['padding'] = 'x' * (8 * 1024 * 1024)
    if damage != 'bad_hash': value['snapshot_hash'] = digest({k: v for k, v in value.items() if k != 'snapshot_hash'})
    with pytest.raises(ValueError): c.checked_snapshot(value)
