"""Pure frozen metadata arithmetic, not real sources, authorization or NL success.

The pixel metadata helpers were already declared synthetic by V252. These
sound dictionaries likewise represent no saved Run/SDK/ACK. The separate PG
test consumes actual CPU/SDK/source receipts before calling the same function.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents import multimodal_query_contracts_v262 as c
from generic_agents import multimodal_query_compute_v262 as subject
from generic_agents import pixel_question_source_contract_v252 as pixel
from generic_agents import task_sound_query_frozen_v262 as sound
from tests.test_multimodal_query_contracts_v262 import fixture as outer_fixture, pixel_intent, sound_intent
from tests.test_pixel_question_sources_v252 import record as pixel_record, snapshot as pixel_snapshot, uid

AT = '2026-10-30T15:59:59+00:00'  # 23:59:59 CST; the next second is a different CST day.
AFTER = '2026-10-30T16:00:00+00:00'
AS_OF = '2026-10-31T12:00:00+00:00'


def sound_record(number=500, *, state='received', registered_at=AT, received_at=AFTER, completed_at=AT,
                 classes=2, windows=4):
    received = state == 'received'
    reference = {key: None for key in sound.REFERENCE}
    reference.update(source_family=c.FACETS['task_sound'][0], run_id=uid(number), task_id=uid(80),
        producer_release_id=uid(82), job_id=uid(number + 1000), invocation_id=uid(number + 2000),
        result_hash=digest(['synthetic sound result', number]), review_receipt_hashes=[],
        detail_path='/api/sounds/runs/' + uid(number), task_path='/api/workspace/tasks/' + uid(80))
    body = {'source_family': c.FACETS['task_sound'][0], 'run_id': uid(number), 'task_id': uid(80),
        'run_version': 1, 'run_status': 'succeeded', 'entity_id': uid(81), 'scope_matched': True,
        'producer_release_id': uid(82), 'producer_status': 'succeeded', 'candidate_available': True,
        'data_values_available': received,
        'values': {'window_count': 6, 'candidate_class_count': classes, 'candidate_window_count': windows,
            'candidates': [{'class_index': index, 'window_indices': [0, 1, 2, 3]} for index in range(classes)]} if received else None,
        'reception': {'state': state, 'observation_id': uid(number + 3000), 'event_id': uid(number + 4000),
            'memory': {'id': uid(number + 5000), 'version': 1, 'data_hash': digest(['memory', number])} if received else None,
            'receipt_hash': digest(['receipt', number]) if received else None},
        'timing': {'run_started_at': AT, 'model_completed_at': completed_at, 'registered_at': registered_at,
            'registration_day': c.instant(registered_at).astimezone(subject.CST).date().isoformat() if registered_at else None,
            'received_at': received_at if received else None, 'recorded_at': None},
        'reference': reference, 'quality': copy.deepcopy(sound.QUALITY),
        'association_basis': 'task_entity_association_not_verified_sound_attribution', 'integrity_issue': None}
    return {**body, 'record_hash': digest(body)}


def reseal(value):
    for facet, native in value['facets'].items():
        if facet == 'pixel': value['facets'][facet] = pixel.seal_snapshot(native)
        else:
            for record in native['records']:
                record['record_hash'] = digest({k: v for k, v in record.items() if k != 'record_hash'})
            native['source_integrity_complete'] = all(r['reception']['state'] != 'blocked_integrity' for r in native['records'])
            native['snapshot_hash'] = digest({k: v for k, v in native.items() if k != 'snapshot_hash'})
    value['record_count'] = sum(len(native['records']) for native in value['facets'].values())
    value['snapshot_hash'] = digest({k: v for k, v in value.items() if k != 'snapshot_hash'})
    return value


def source(sound_rows=(), pixel_rows=()):
    scope, registry, outer = outer_fixture()
    scope.update(instance_id=uid(2), authorization_release_id=uid(82))
    registry.update(instance_id=uid(2), authorization_release_id=uid(82), workspace_id=uid(1))
    registry['registry_hash'] = digest({k: v for k, v in registry.items() if k != 'registry_hash'})
    rows = sorted(copy.deepcopy(list(sound_rows)), key=lambda r: r['run_id'])
    native = {'schema': 'task-sound-query-evidence-snapshot-v1', 'source_family': c.FACETS['task_sound'][0],
        'workspace_id': uid(1), 'captured_by': uid(3), 'instance_id': uid(2),
        'scope': {'task_id': None, 'entity_id': None}, 'as_of': AS_OF,
        'consistency': 'caller_current_repeatable_read_read_only',
        'inventory': {'basis': 'registered_run_scan', 'complete': True, 'limit': 1000,
            'scanned_run_ids': [r['run_id'] for r in rows], 'returned_run_ids': [r['run_id'] for r in rows],
            'expected_sources_verified': False}, 'source_integrity_complete': True, 'records': rows,
        'time_basis': 'machine_result_registered', 'time_zone': 'Asia/Shanghai', 'recording_time_verified': False,
        'candidate_only': True, 'not_authoritative': True, 'physical_state_verified': False, 'limitations': []}
    outer.update(workspace_id=uid(1), captured_by=uid(3), scope=scope, as_of=AS_OF, registry=registry,
        facets={'pixel': pixel_snapshot(pixel_rows, at=AS_OF), 'task_sound': native})
    return reseal(outer)


def sound_query(operation='count', *, reception='any', metric=None, basis='machine_result_registered', days=None, group='none'):
    query = sound_intent()
    prefix = {'none': '', 'task_id': '按任务', 'entity_id': '按对象', 'registration_day': '按登记日'}[group]
    clock = {'machine_result_registered': '登记', 'independent_data_received': '独立接收', 'model_completed': '模型处理'}[basis]
    time_quote = '、'.join(days) if days is not None else None
    state = {'any': '', 'received': '已独立接收', 'waiting_data': '等待独立接收', 'producer_failed': '生产失败',
        'local_handoff_pending': '等待本地交付', 'blocked_integrity': '收件受阻'}[reception]
    metric_quote = '候选类别' if metric == 'candidate_class_count' else '候选窗口' if metric is not None else None
    target = prefix + ((time_quote + clock) if time_quote else '') + state + '声音登记成果'
    query['question_quote'] = ('列出' + target) if operation == 'read' else (target + '有多少份') if operation == 'count' else (
        target + '的' + metric_quote + '合计多少')
    query['intent'].update(operation=operation, metric=metric, group_by=group)
    query['intent']['filters']['reception'] = reception
    query['intent']['time'].update(basis=basis, days=days)
    query['intent']['grounding'].update(operation_quote='多少' if operation == 'count' else '合计' if operation == 'sum' else '列出',
        metric_quote=metric_quote, time_quote=time_quote, filter_quotes=[state] if state else [])
    return query


def plan(snapshot, *queries, unmet=None):
    question = '；'.join(q['question_quote'] for q in queries)
    return c.bind_plan({'schema': c.INTENT, 'subqueries': list(queries), 'unmet_requirements': unmet or []},
        question, snapshot['scope'], snapshot['registry'])


def sound_result(value):
    return next(item['computation'] for item in value['facets'] if item['facet'] == 'task_sound')


def test_real_old_pixel_compute_is_unchanged_inside_mixed_units_and_refs():
    snapshot = source([sound_record(), sound_record(501)], [pixel_record(pixels=17)])
    bound = plan(snapshot, pixel_intent(), sound_query('sum', reception='received', metric='candidate_window_count'),
        unmet=['声学物种与现场风险未由这些来源证明'])
    original = copy.deepcopy((snapshot, bound)); result = subject.compute(snapshot, bound)
    first, second = result['facets']
    assert first['computation'] == pixel.compute(snapshot['facets']['pixel'], bound['subqueries'][0]['ir'])
    assert first['computation']['groups'][0]['value_text'] == '17'
    assert second['computation']['groups'][0]['value'] == 8  # Per-Run windows, not cross-Run index deduplication.
    assert second['computation']['unit'] == 'summed_candidate_window_entries'
    assert 'total' not in result and result['status'] == 'computed_with_unmet_requirements'
    for index, item in enumerate(result['facets']):
        native = snapshot['facets'][item['facet']]
        assert item['as_of'] == AS_OF and item['outer_source_snapshot_hash'] == snapshot['snapshot_hash']
        assert item['inner_source_snapshot_hash'] == native['snapshot_hash']
        assert item['ir'] == bound['subqueries'][index]['ir']
        assert item['question_quote'] == bound['subqueries'][index]['question_quote']
        assert item['source_record_refs'] == [{'run_id': r['run_id'], 'record_hash': r['record_hash']} for r in native['records']]
    assert result['unmet_requirements'] == bound['unmet_requirements']
    assert result['verification']['source_authority_checked'] is False
    assert result['verification']['expected_sources_verified'] is False
    assert (snapshot, bound) == original
    assert subject.checked_result(snapshot, bound, result) == result


def test_counts_are_registered_runs_and_pending_blocked_read_values_remain_null():
    rows = [sound_record(500), sound_record(501, state='waiting_data'), sound_record(502, state='blocked_integrity'),
        sound_record(503, state='producer_failed'), sound_record(504, state='local_handoff_pending')]
    snapshot = source(rows); bound = plan(snapshot, sound_query())
    value = sound_result(subject.compute(snapshot, bound))
    assert value['groups'][0]['value'] == 5 and value['unit'] == 'registered_runs'
    assert value['state_counts'] == {name: 1 for name in sound.STATES}
    assert value['coverage']['expected_sources_verified'] is False
    read = sound_result(subject.compute(snapshot, plan(snapshot, sound_query('read'))))
    assert read['groups'][0]['value'] is None
    assert all(r['values'] is None for r in read['references'] if r['reception']['state'] != 'received')
    assert 'all_registered_received' not in read and 'expected_sources_verified' not in read['state_counts']


@pytest.mark.parametrize('metric,expected', [('candidate_window_count', 8), ('candidate_class_count', 4)])
def test_only_ack_counts_contribute_and_cross_run_entries_are_not_physical_entities(metric, expected):
    snapshot = source([sound_record(), sound_record(501), sound_record(502, state='waiting_data')])
    answer = sound_result(subject.compute(snapshot, plan(snapshot, sound_query('sum', reception='received', metric=metric))))
    assert answer['groups'][0]['value'] == expected and answer['coverage']['matched_records'] == 2
    assert all(r['reception']['state'] == 'received' for r in answer['references'])
    assert any('不是' in item for item in answer['limitations'])


def test_no_received_sum_is_null_but_real_received_zero_and_empty_run_count_are_distinct():
    empty = source(); waiting = source([sound_record(state='waiting_data')])
    for snapshot in (empty, waiting):
        answer = sound_result(subject.compute(snapshot, plan(snapshot,
            sound_query('sum', reception='received', metric='candidate_class_count'))))
        assert answer['groups'][0]['value'] is None and 'no_received_matching_results' in answer['limitations']
    zero = source([sound_record(classes=0, windows=0)])
    assert sound_result(subject.compute(zero, plan(zero, sound_query('sum', reception='received',
        metric='candidate_window_count'))))['groups'][0]['value'] == 0
    assert sound_result(subject.compute(empty, plan(empty, sound_query())))['groups'][0]['value'] == 0


@pytest.mark.parametrize('basis,expected', [('machine_result_registered', 1), ('independent_data_received', 0), ('model_completed', 1)])
def test_cst_midnight_uses_only_the_selected_administrative_clock(basis, expected):
    snapshot = source([sound_record()])
    answer = sound_result(subject.compute(snapshot, plan(snapshot, sound_query(reception='received', basis=basis, days=['2026-10-30']))))
    assert answer['groups'][0]['value'] == expected
    assert answer['references'][0]['timing']['recorded_at'] is None if expected else not answer['references']


def test_unknown_selected_clock_requires_clarification_not_a_known_subset_total_or_another_clock():
    snapshot = source([sound_record(), sound_record(501, state='waiting_data', registered_at=None)])
    bound = plan(snapshot, sound_query(days=['2026-10-30']))
    value = subject.compute(snapshot, bound); answer = sound_result(value)
    assert value['status'] == answer['status'] == 'clarification_required'
    assert answer['coverage']['matched_records'] == 1 and answer['coverage']['unknown_time_records'] == 1
    assert answer['groups'][0]['value'] is None and value['unmet_requirements']
    assert {r['selection'] for r in answer['references']} == {'matched', 'unknown_time'}
    # Reception is applied first: an irrelevant unreceived clock does not block an ACK-only result.
    ack_only = plan(snapshot, sound_query(reception='received', days=['2026-10-30']))
    assert sound_result(subject.compute(snapshot, ack_only))['groups'][0]['value'] == 1


@pytest.mark.parametrize('days,quote', [(None, '2026-10-30'), (['2026-10-31'], '2026-10-30'),
    (['2026-10-30'], '2026-10-30以来'), (['2020-01-01'], '今天')])
def test_unresolved_or_falsely_bound_time_condition_is_never_silently_ignored(days, quote):
    snapshot = source([sound_record()]); query = sound_query(days=days)
    query['question_quote'] += quote; query['intent']['grounding']['time_quote'] = quote
    answer = sound_result(subject.compute(snapshot, plan(snapshot, query)))
    assert answer['status'] == 'clarification_required' and answer['unmet_requirements']
    assert answer['coverage']['time_filter_resolved'] is False
    assert all(group['value'] is None for group in answer['groups'])


def test_yesterday_is_a_cst_administrative_day_relative_to_frozen_as_of_not_recording_clock():
    snapshot = source([sound_record()]); query = sound_query(days=['2026-10-30'])
    query['question_quote'] += '昨日'; query['intent']['grounding']['time_quote'] = '昨日'
    answer = sound_result(subject.compute(snapshot, plan(snapshot, query)))
    assert answer['status'] == 'computed' and answer['groups'][0]['value'] == 1
    assert answer['references'][0]['timing']['recorded_at'] is None


def test_unknown_group_remains_null_and_old_pixel_unknown_semantics_do_not_change():
    snapshot = source([sound_record(state='waiting_data', registered_at=None)],
        [pixel_record(), pixel_record(101, state='waiting_data', registered=False)])
    sound_plan = plan(snapshot, sound_query(group='registration_day'))
    answer = sound_result(subject.compute(snapshot, sound_plan))
    assert answer['groups'][0]['key'] is None and answer['groups'][0]['value'] == 1
    query = pixel_intent(operation='count', metric=None, operation_quote='多少', metric_quote=None)
    query['question_quote'] = '2026-10-30登记的像素成果有多少份？'
    query['intent']['time']['registration_days'] = ['2026-10-30']
    query['intent']['grounding']['time_quote'] = '2026-10-30'
    bound = plan(snapshot, query)
    result = subject.compute(snapshot, bound)['facets'][0]['computation']
    assert result == pixel.compute(snapshot['facets']['pixel'], bound['subqueries'][0]['ir'])
    assert result['coverage']['unknown_registration_day_excluded'] == 1


@pytest.mark.parametrize('damage', ['plan_hash', 'scope', 'registry', 'facet', 'extra_plan', 'quote',
    'unsupported_clock', 'unknown_filter', 'unack_sum', 'sql', 'bad_inner_hash', 'bool_value', 'pending_value', 'other_clock'])
def test_rehashed_malformed_plan_or_source_cannot_supply_numbers(damage):
    snapshot = source([sound_record()]); bound = plan(snapshot, sound_query())
    ir = bound['subqueries'][0]['ir']; native = snapshot['facets']['task_sound']; record = native['records'][0]
    if damage == 'plan_hash': bound['plan_hash'] = '0' * 64
    elif damage == 'scope': ir['scope'] = {**ir['scope'], 'task_id': uid(999)}
    elif damage == 'registry': bound['registry_hash'] = '0' * 64
    elif damage == 'facet': bound['subqueries'][0]['facet'] = 'video'
    elif damage == 'extra_plan': bound['published'] = True
    elif damage == 'quote': bound['subqueries'][0]['question_quote'] = '改写问题'
    elif damage == 'unsupported_clock': ir['time']['basis'] = 'recorded_at'
    elif damage == 'unknown_filter': ir['filters']['minimum_probability'] = .5
    elif damage == 'unack_sum': ir.update(operation='sum', metric='candidate_window_count'); ir['filters']['reception'] = 'waiting_data'
    elif damage == 'sql': ir['sql'] = 'select * from unrelated'
    elif damage == 'bad_inner_hash': native['snapshot_hash'] = '0' * 64
    elif damage == 'bool_value': record['values']['candidate_window_count'] = True; reseal(snapshot)
    elif damage == 'pending_value': record['reception']['state'] = 'waiting_data'; reseal(snapshot)
    else: record['timing']['registration_day'] = '2026-10-31'; reseal(snapshot)
    if damage != 'plan_hash': bound['plan_hash'] = digest({k: v for k, v in bound.items() if k != 'plan_hash'})
    snapshot['snapshot_hash'] = digest({k: v for k, v in snapshot.items() if k != 'snapshot_hash'})
    with pytest.raises((ValueError, HTTPException)): subject.compute(snapshot, bound)


@pytest.mark.parametrize('damage', ['amount', 'hash', 'authority', 'quote', 'outer_hash'])
def test_result_checker_recomputes_source_arithmetic_and_fixed_false_authority(damage):
    snapshot = source([sound_record()]); bound = plan(snapshot, sound_query()); answer = subject.compute(snapshot, bound)
    if damage == 'amount': answer['facets'][0]['computation']['groups'][0]['value'] = 900
    elif damage == 'hash': answer['result_hash'] = '0' * 64
    elif damage == 'authority': answer['verification']['source_authority_checked'] = True
    elif damage == 'quote': answer['facets'][0]['question_quote'] = '伪造原句'
    else: answer['source_snapshot_hash'] = '0' * 64
    if damage != 'hash': answer['result_hash'] = digest({k: v for k, v in answer.items() if k != 'result_hash'})
    with pytest.raises(ValueError): subject.checked_result(snapshot, bound, answer)
