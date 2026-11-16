"""Deterministic two-facet arithmetic over an already bound, frozen source.

Content/hash checks grant no source, query, SDK, or database authority. A future
lifecycle must verify its real Query/source Audit before freezing this output.
This module opens no Session, reads no files, and changes no source or old IR.
Sound coverage is registered Runs only, never proof of expected-source receipt.
"""
import copy
import re
from datetime import date, timedelta, timezone

from .contracts import digest
from . import multimodal_query_contracts_v262 as c
from . import pixel_question_source_contract_v252 as pixel
from . import task_sound_query_frozen_v262 as sound

SCHEMA = 'multimodal-query-computation-v262'
SOUND_SCHEMA = 'task-sound-query-computation-v262'
CST = timezone(timedelta(hours=8))
PLAN_KEYS = {'schema', 'scope', 'registry_hash', 'question_hash', 'subqueries', 'unmet_requirements', 'plan_hash'}
TIME_FIELDS = {'machine_result_registered': 'registered_at', 'independent_data_received': 'received_at',
    'model_completed': 'model_completed_at'}
VERIFICATION = {'frozen_content_checked': True, 'source_authority_checked': False,
    'expected_sources_verified': False, 'semantic_quality_verified': False,
    'physical_state_verified': False, 'source_time_verified': False}
SOUND_LIMITATIONS = (
    '仅核对本次冻结范围内已登记的环境声音Run，不证明预期原音、设备或全部来源已齐全',
    '环境声音候选不是ASR、声纹或物种识别；原分数未校准，不是风险概率',
    '候选窗口可重叠；窗口数不是独立声音事件数、声源数、录音时长或现场异常次数',
    '类别合计是逐Run候选类别条目数之和，不是跨Run去重类别、动物数或物种识别',
    '登记、独立接收和模型处理时间是行政时间；原音实际录制时刻未知，不以查询截面替代',
)


def _checked_plan(value, snapshot):
    c.need(type(value) is dict and set(value) == PLAN_KEYS and value['schema'] == c.PLAN)
    scope = c.Scope.model_validate(value['scope']).model_dump(mode='json')
    c.need(scope == value['scope'] == snapshot['scope'] and c.hashed(value['question_hash'])
        and value['registry_hash'] == snapshot['registry']['registry_hash']
        and type(value['subqueries']) is list and 1 <= len(value['subqueries']) <= 2
        and value['plan_hash'] == digest({k: v for k, v in value.items() if k != 'plan_hash'}))
    intents = []
    for subquery in value['subqueries']:
        c.need(type(subquery) is dict and set(subquery) == {'facet', 'question_quote', 'ir'}
            and subquery['facet'] in snapshot['facets'] and type(subquery['ir']) is dict)
        facet = subquery['facet']; ir = subquery['ir']
        c.need(ir.get('scope') == scope)
        if facet == 'pixel':
            c.need(ir.get('schema') == 'pixel-question-ir-v252'
                and ir.get('source_facet') == 'received_pixel_machine_results_v250')
            native = {k: v for k, v in ir.items() if k not in {'schema', 'source_facet', 'scope'}}
        else:
            c.need(facet == 'task_sound' and ir.get('schema') == 'task-sound-query-ir-v262')
            native = {k: v for k, v in ir.items() if k not in {'schema', 'scope'}}
        intents.append({'facet': facet, 'question_quote': subquery['question_quote'], 'intent': native})
    model = c.ModelIntent.model_validate({'schema': c.INTENT, 'subqueries': intents,
        'unmet_requirements': value['unmet_requirements']})
    for subquery in model.subqueries:
        fragment = subquery.question_quote
        c.need(bool(fragment.strip()))
        for key, quote in subquery.intent['grounding'].items():
            quotes = quote if key == 'filter_quotes' else [quote]
            c.need(all(item is None or bool(item.strip()) and item in fragment for item in quotes),
                '已绑定子计划的逐字依据不在其原问题片段中')
        if subquery.facet == 'task_sound':
            intent = subquery.intent
            c.need(intent['time']['days'] is None or intent['grounding']['time_quote'] is not None)
            c.need(intent['operation'] != 'sum' or intent['grounding']['metric_quote'] is not None)
    c.wire(value)
    # The full original question is deliberately not reconstructed from quotes.
    # Its hash binds the prior plan; semantic coverage is not established here.
    return copy.deepcopy(value)


def _sound_day(record, basis):
    raw = record['timing'][TIME_FIELDS[basis]]
    return None if raw is None else c.instant(raw).astimezone(CST).date().isoformat()


def _sound_content(snapshot):
    sound.validate_snapshot(snapshot)
    for record in snapshot['records']:
        c.need(snapshot['scope']['task_id'] is None or record['task_id'] == snapshot['scope']['task_id'])
        if snapshot['scope']['entity_id'] is not None and record['scope_matched'] is True:
            c.need(record['entity_id'] == snapshot['scope']['entity_id'])
        for name, raw in record['timing'].items():
            if name not in {'registration_day', 'recorded_at'} and raw is not None:
                c.instant(raw)
        day = record['timing']['registration_day']
        c.need(day is None or type(day) is str and date.fromisoformat(day).isoformat() == day)
        c.need(day == _sound_day(record, 'machine_result_registered'),
            '声音登记日与其原登记时刻不一致，不能借用其他时钟')


def _time_issue(ir, as_of):
    """Only explicit day lists and today/yesterday are locally resolvable here."""
    days = ir['time']['days']; quote = ir['grounding']['time_quote']
    if days is None and quote is None: return None
    if days is not None:
        text = quote.strip()
        date_token = r'\d{4}-\d{2}-\d{2}'
        if re.fullmatch(date_token + r'(?:\s*(?:、|,|，|和|及)\s*' + date_token + r')*', text):
            if set(re.findall(date_token, text)) == set(days): return None
        elif text in {'今天', '今日', '昨天', '昨日'}:
            today = c.instant(as_of).astimezone(CST).date()
            expected = today - timedelta(days=int(text in {'昨天', '昨日'}))
            if days == [expected.isoformat()]: return None
    return '声音时间要求尚未形成与原引用一致的明确CST行政日期集合；未忽略该条件，数值保留null，请澄清日期'


def _sound_compute(snapshot, ir, as_of):
    basis = ir['time']['basis']; days = ir['time']['days']; reception = ir['filters']['reception']
    time_issue = _time_issue(ir, as_of)
    relevant = [r for r in snapshot['records'] if reception == 'any' or r['reception']['state'] == reception]
    unknown = [r for r in relevant if days is not None and _sound_day(r, basis) is None]
    selected = [r for r in relevant if days is None or _sound_day(r, basis) in days]
    states = {state: sum(r['reception']['state'] == state for r in selected) for state in sorted(sound.STATES)}
    buckets = {}
    for record in selected:
        group = ir['group_by']
        key = None if group == 'none' else record['timing']['registration_day'] if group == 'registration_day' else record[group]
        buckets.setdefault(key, []).append(record)
    if not buckets and ir['group_by'] == 'none' and ir['operation'] != 'read':
        buckets[None] = []
    groups = []
    for key in sorted(buckets, key=lambda item: (item is not None, item or '')):
        rows = buckets[key]; number = None
        if not unknown and time_issue is None and ir['operation'] == 'count':
            number = len(rows)
        elif not unknown and time_issue is None and ir['operation'] == 'sum' and rows:
            c.need(all(r['reception']['state'] == 'received' and r['values'] is not None for r in rows))
            number = sum(r['values'][ir['metric']] for r in rows)
        groups.append({'key': key, 'run_ids': [r['run_id'] for r in rows],
            'matched_records': len(rows), 'value': number})
    unmet = []
    if time_issue is not None: unmet.append(time_issue)
    if unknown:
        unmet.append('所选' + basis + '行政时钟有' + str(len(unknown))
            + '条相关登记记录未知，无法确定日期筛选总体；数值保留null，请澄清范围或补核原收件依据')
    limits = list(ir['limitations'])
    for item in SOUND_LIMITATIONS:
        if item not in limits: limits.append(item)
    if ir['operation'] == 'sum' and not selected:
        limits.append('no_received_matching_results')
    references = []
    matched_ids = {r['run_id'] for r in selected}; unknown_ids = {r['run_id'] for r in unknown}
    for record in relevant:
        if record['run_id'] not in matched_ids | unknown_ids: continue
        references.append({'run_id': record['run_id'], 'task_id': record['task_id'], 'entity_id': record['entity_id'],
            'record_hash': record['record_hash'], 'selection': 'matched' if record['run_id'] in matched_ids else 'unknown_time',
            'reception': copy.deepcopy(record['reception']), 'timing': copy.deepcopy(record['timing']),
            'result_hash': record['reference']['result_hash'],
            'values': None if record['values'] is None else {key: record['values'][key] for key in
                ('window_count', 'candidate_class_count', 'candidate_window_count')}})
    return {'schema': SOUND_SCHEMA, 'status': 'clarification_required' if unmet else 'computed',
        'source_snapshot_hash': snapshot['snapshot_hash'], 'operation': ir['operation'], 'metric': ir['metric'],
        'group_by': ir['group_by'], 'time': copy.deepcopy(ir['time']), 'filters': copy.deepcopy(ir['filters']),
        'coverage': {'basis': 'registered_run_scan', 'complete_registered_inventory': True,
            'scanned_records': len(snapshot['records']), 'reception_matched_records': len(relevant),
            'matched_records': len(selected), 'unknown_time_records': len(unknown),
            'time_filter_resolved': not unknown and time_issue is None,
            'expected_sources_verified': False, 'limit': snapshot['inventory']['limit']},
        'state_counts': states, 'groups': groups, 'references': references,
        'unit': 'registered_runs' if ir['operation'] == 'count' else 'none' if ir['operation'] == 'read' else
            'summed_candidate_window_entries' if ir['metric'] == 'candidate_window_count' else 'summed_candidate_class_entries',
        'metrics_basis': 'independently_received_results_only', 'candidate_only': True,
        'not_authoritative': True, 'physical_state_verified': False, 'source_time_verified': False,
        'unmet_requirements': unmet, 'limitations': limits}


def compute(snapshot, bound_plan):
    """Pure calculation, NOT a saved/authorized query or independent ACK."""
    source = c.checked_snapshot(snapshot); plan = _checked_plan(bound_plan, source)
    # Check every included native payload, including a facet not selected by the
    # plan. The old pixel validator and its arithmetic are called unchanged.
    for name, native in source['facets'].items():
        if name == 'pixel': pixel.validate_snapshot(native)
        else: _sound_content(native)
    outputs = []; unmet = list(plan['unmet_requirements']); clarification = False
    for subquery in plan['subqueries']:
        name = subquery['facet']; native = source['facets'][name]; ir = subquery['ir']
        result = pixel.compute(native, ir) if name == 'pixel' else _sound_compute(native, ir, source['as_of'])
        status = 'computed' if name == 'pixel' else result['status']
        clarification = clarification or status == 'clarification_required'
        for item in result.get('unmet_requirements', []):
            if item not in unmet: unmet.append(item)
        outputs.append({'facet': name, 'question_quote': subquery['question_quote'], 'ir': copy.deepcopy(ir),
            'as_of': source['as_of'], 'outer_source_snapshot_hash': source['snapshot_hash'],
            'inner_source_snapshot_hash': native['snapshot_hash'], 'status': status,
            'source_record_refs': [{'run_id': r['run_id'], 'record_hash': r['record_hash']} for r in native['records']],
            'computation': result})
    value = {'schema': SCHEMA, 'workspace_id': source['workspace_id'], 'captured_by': source['captured_by'],
        'scope': copy.deepcopy(source['scope']), 'as_of': source['as_of'], 'plan_hash': plan['plan_hash'],
        'question_hash': plan['question_hash'], 'registry_hash': plan['registry_hash'],
        'source_snapshot_hash': source['snapshot_hash'],
        'status': 'clarification_required' if clarification else 'computed_with_unmet_requirements' if unmet else 'computed',
        'facets': outputs, 'unmet_requirements': unmet, 'verification': copy.deepcopy(VERIFICATION)}
    c.wire(value)
    return {**value, 'result_hash': digest(value)}


def checked_result(snapshot, bound_plan, value):
    """Recompute all arithmetic/provenance; a caller-supplied hash alone is insufficient."""
    c.wire(value)
    expected = compute(snapshot, bound_plan)
    c.need(type(value) is dict and digest(value) == digest(expected), '本地计算结果或其原来源、问题计划无法复核')
    return copy.deepcopy(value)
