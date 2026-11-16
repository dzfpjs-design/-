"""Synthetic pure review-ledger tests: no files, provider, DB or semantic grade.

The caller's candidate-validation/authentication/receipt responsibilities remain
outside this module. CandidateWireV6 below supplies real typed test structures;
these tests do not claim that those synthetic statements describe any document.
"""
import copy
import hashlib

import pytest

from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import build_evidence_source
from generic_agents.report_evidence_reduction_wire_v6 import CandidateWireV6
from generic_agents.report_evidence_manual_v5 import ManualReviewValueV5, MANUAL_VALUE
from generic_agents.report_source_requirements import (
    CHECK, CHECKED, TARGET_ROOTS, build_source_units, check_requirement_record, requirement_record_status, _target,
)


def scene():
    catalog = build_evidence_source('a' * 64, [
        ['w:p3', '首句。后句。'], ['o:p1', ''], ['w:p1', ' \t '], ['o:p2', '相同。']], max_span_chars=4)
    citation = {'span_ref': 'w:p3/s1'}
    evidence = {'status': 'explicit', 'citations': [citation], 'explanation': ''}
    def step(name, role, destination):
        return {'name': name, 'agent': '人工确认', 'role': role, 'purpose': '公开合成动作', 'output': '公开合成结果',
            'citations': [citation], 'inference_reason': '', 'branch_condition': '',
            'next_index': destination, 'else_index': {'kind': 'end'}}
    result = CandidateWireV6.model_validate({'schema': 'report-evidence-candidate-wire-v6',
        'source_hash': catalog['descriptor']['source_hash'], 'catalog_hash': catalog['descriptor']['catalog_hash'],
        'domain': '公开合成领域', 'objects': [], 'relationships': [], 'questions': [],
        'global_claims': [{'kind': '职责', 'statement': '公开合成要求', 'span_refs': ['w:p3/s1'],
                           'status': 'explicit', 'explanation': ''}],
        'businesses': [{'operation': 'upsert', 'original_name': '', 'name': '公开合成流程', 'trigger': '合成触发',
            'output': '合成产出', 'citations': [citation], 'inference_reason': '',
            'participants': [{'agent': name, 'responsibility': '合成责任', 'evidence': evidence} for name in ('甲方', '乙方')],
            'steps': [step('登记', '甲方人员', {'kind': 'step', 'index': 1}), step('核对', '乙方人员', {'kind': 'end'})],
            'conditions': [], 'coordination': [], 'interactions': [{'from_participant_index': 0,
                'to_participant_index': 1, 'purpose': '合成交接', 'condition': '', 'step_indices': [0, 1], 'evidence': evidence}]}]
        }).model_dump(mode='json')
    return catalog, result


def record(catalog, candidate, **changes):
    value = {'schema': CHECK, 'requirement': '保留这项公开合成业务要求。', 'source_unit_ids': ['w:p3'],
        'disposition': 'represented' if candidate is not None else 'needs_evidence', 'reason': '这是人工待核的合成比较。',
        'targets': ['/businesses/0/interactions/0/to_participant_index'] if candidate is not None else [],
        'candidate_hash': digest(candidate) if candidate is not None else None,
        'source_hash': catalog['descriptor']['source_hash'], 'catalog_hash': catalog['descriptor']['catalog_hash']}
    return {**value, **changes}


def check(value, catalog, candidate):
    return check_requirement_record(value, catalog=catalog, candidate_result=candidate,
                                    candidate_hash=digest(candidate) if candidate is not None else None)


def status(value, catalog, candidate):
    return requirement_record_status(value, catalog=catalog, candidate_result=candidate,
                                     candidate_hash=digest(candidate) if candidate is not None else None)


def test_units_preserve_original_block_order_complete_spans_empty_and_whitespace_without_generating_ids():
    catalog, _ = scene(); original = copy.deepcopy(catalog)
    units = build_source_units(catalog)
    assert [unit['source_unit_id'] for unit in units['units']] == ['w:p3', 'o:p1', 'w:p1', 'o:p2']
    first, empty, whitespace, _ = units['units']
    assert [span['span_ref'] for span in first['spans']] == ['w:p3/s1', 'w:p3/s2']
    assert [(span['start'], span['end']) for span in first['spans']] == [(0, 4), (4, 6)]
    assert first['text_hash'] == hashlib.sha256('首句。后句。'.encode()).hexdigest()
    assert empty['is_empty'] is True and empty['spans'] == [] and empty['codepoints'] == 0
    assert empty['text_hash'] == hashlib.sha256(b'').hexdigest()
    assert whitespace['is_empty'] is False and whitespace['codepoints'] == 3
    for unit in units['units']:
        assert unit['source_hash'] == units['source_hash'] and unit['catalog_hash'] == units['catalog_hash']
        assert unit['unit_hash'] == digest({key: value for key, value in unit.items() if key != 'unit_hash'})
        assert all('text' not in span for span in unit['spans'])
    assert units['units_hash'] == digest(units['units']) and build_source_units(catalog) == units
    assert catalog == original


def test_empty_catalog_and_multiple_records_never_invent_complete_unit_or_semantic_approval():
    empty = build_evidence_source('a' * 64, [])
    assert build_source_units(empty)['units'] == []
    catalog, candidate = scene(); first = check(record(catalog, candidate), catalog, candidate)
    second = check(record(catalog, candidate, requirement='同一来源的另一项独立要求。'), catalog, candidate)
    assert first['source_unit_bindings'] == second['source_unit_bindings'] and first['check_hash'] != second['check_hash']
    for value in (first, second):
        current = status(value, catalog, candidate)
        assert current == {'status': 'current', 'reasons': []}
        assert 'coverage' not in current


def test_closed_checked_record_binds_exact_source_candidate_and_target_value_without_mutation():
    catalog, candidate = scene(); command = record(catalog, candidate)
    original = copy.deepcopy((catalog, candidate, command)); checked = check(command, catalog, candidate)
    assert set(checked) == {'schema', 'record', 'record_hash', 'source_unit_bindings', 'target_bindings', 'check_hash'}
    assert checked['schema'] == CHECKED and checked['record'] == command
    assert checked['record_hash'] == digest(command)
    assert checked['target_bindings'] == [{'pointer': command['targets'][0], 'target_value_hash': digest(1)}]
    assert checked['source_unit_bindings'][0]['unit_hash'] == build_source_units(catalog)['units'][0]['unit_hash']
    assert status(checked, catalog, candidate)['reasons'] == []
    assert (catalog, candidate, command) == original
    checked['record']['reason'] = 'changed'
    assert command['reason'] == original[2]['reason']


@pytest.mark.parametrize('pointer', ['', 'businesses/0', '/result/domain', '/data_objects', '/relations', '/schema',
    '/businesses/-', '/businesses/00', '/businesses/+0', '/businesses/999', '/businesses/0.0',
    '/businesses/0/name/0', '/businesses/0/absent', '/domain/length', '/domain~', '/objects/~2'])
def test_invalid_unknown_alias_out_of_range_or_scalar_continuation_pointers_reject(pointer):
    catalog, candidate = scene()
    with pytest.raises(ValueError): check(record(catalog, candidate, targets=[pointer]), catalog, candidate)


def test_json_pointer_escape_decoder_is_exact_not_url_decoding_or_unicode_normalization():
    # Parser unit only: this dictionary is not presented as a validated Candidate.
    value = {'objects': [{'a/b~c': ['原值'], '': '空key', 'é': '组合字符'}]}
    assert _target(value, '/objects/0/a~1b~0c/0') == '原值'
    assert _target(value, '/objects/0/') == '空key'
    assert _target(value, '/objects/0/é') == '组合字符'
    for pointer in ('/objects/0/a%2Fb~0c', '/objects/0/e\u0301', '/objects/0/a~01b~0c', '/objects/0/a~1b~0c/-'):
        with pytest.raises(ValueError): _target(value, pointer)


@pytest.mark.parametrize('mode', ['extra', 'old_schema', 'empty_requirement', 'long_requirement', 'blank_reason',
    'long_reason', 'empty_units', 'duplicate_units', 'unknown_unit', 'duplicate_target', 'wrong_source',
    'wrong_catalog', 'wrong_candidate', 'bool_unit', 'target_object', 'nul'])
def test_closed_input_scope_duplicates_and_text_limits_reject_without_repair(mode):
    catalog, candidate = scene(); value = record(catalog, candidate)
    if mode == 'extra': value['approved'] = True
    elif mode == 'old_schema': value['schema'] = 'report-source-requirement-check-v0'
    elif mode == 'empty_requirement': value['requirement'] = ' \n'
    elif mode == 'long_requirement': value['requirement'] = '字' * 2001
    elif mode == 'blank_reason': value['reason'] = '\t'
    elif mode == 'long_reason': value['reason'] = '字' * 2001
    elif mode == 'empty_units': value['source_unit_ids'] = []
    elif mode == 'duplicate_units': value['source_unit_ids'] *= 2
    elif mode == 'unknown_unit': value['source_unit_ids'] = ['w:p9']
    elif mode == 'duplicate_target': value['targets'] *= 2
    elif mode == 'wrong_source': value['source_hash'] = 'f' * 64
    elif mode == 'wrong_catalog': value['catalog_hash'] = 'f' * 64
    elif mode == 'wrong_candidate': value['candidate_hash'] = 'f' * 64
    elif mode == 'bool_unit': value['source_unit_ids'] = [True]
    elif mode == 'target_object': value['targets'] = [{'pointer': '/domain'}]
    else: value['reason'] = '不保存\x00'
    before = copy.deepcopy(value)
    with pytest.raises(ValueError): check(value, catalog, candidate)
    assert value == before


def test_dispositions_require_targets_only_where_defined_and_preserve_exact_human_text():
    catalog, candidate = scene()
    for disposition in ('represented', 'incorrect'):
        with pytest.raises(ValueError): check(record(catalog, candidate, disposition=disposition, targets=[]), catalog, candidate)
    with pytest.raises(ValueError): check(record(catalog, candidate, disposition='out_of_scope'), catalog, candidate)
    for disposition in ('missing', 'needs_evidence', 'out_of_scope'):
        checked = check(record(catalog, candidate, disposition=disposition, targets=[], requirement=' 前后空格保留 ', reason='\n具体理由\n'), catalog, candidate)
        assert checked['record']['requirement'] == ' 前后空格保留 ' and checked['record']['reason'] == '\n具体理由\n'


def test_exact_twenty_units_twentyfour_targets_and_twothousand_char_boundaries():
    catalog, candidate = scene()
    catalog = build_evidence_source('a' * 64, [[f'w:p{i}', '占位'] for i in range(1, 22)])
    candidate = {**candidate, 'source_hash': catalog['descriptor']['source_hash'], 'catalog_hash': catalog['descriptor']['catalog_hash']}
    paths = []
    def collect(node, path):
        paths.append(path)
        if type(node) is dict:
            for key, item in node.items(): collect(item, path + '/' + key.replace('~', '~0').replace('/', '~1'))
        elif type(node) is list:
            for index, item in enumerate(node): collect(item, path + '/' + str(index))
    for key in TARGET_ROOTS: collect(candidate[key], '/' + key)
    value = record(catalog, candidate, source_unit_ids=[f'w:p{i}' for i in range(1, 21)],
                   requirement='字' * 2000, reason='理' * 2000, targets=paths[:24])
    assert len(check(value, catalog, candidate)['target_bindings']) == 24
    with pytest.raises(ValueError): check({**value, 'source_unit_ids': [f'w:p{i}' for i in range(1, 22)]}, catalog, candidate)
    with pytest.raises(ValueError): check({**value, 'targets': paths[:25]}, catalog, candidate)


def test_same_refs_but_changed_recipient_stales_whole_candidate_and_exact_target_hash():
    catalog, candidate = scene(); checked = check(record(catalog, candidate), catalog, candidate)
    original = copy.deepcopy(checked); changed = copy.deepcopy(candidate)
    changed['businesses'][0]['interactions'][0]['from_participant_index'] = 1
    changed['businesses'][0]['interactions'][0]['to_participant_index'] = 0
    assert changed['businesses'][0]['interactions'][0]['evidence'] == candidate['businesses'][0]['interactions'][0]['evidence']
    observed = status(checked, catalog, changed)
    assert observed['status'] == 'stale' and observed['reasons'] == ['candidate_changed', 'target_values_changed']
    with pytest.raises(ValueError):
        requirement_record_status(checked, catalog=catalog, candidate_result=changed, candidate_hash=digest(candidate))
    assert checked == original


def test_unrelated_candidate_edit_and_valid_old_candidate_wire_version_still_stale():
    catalog, candidate = scene(); checked = check(record(catalog, candidate), catalog, candidate)
    edited = copy.deepcopy(candidate); edited['domain'] += '更新'
    assert status(checked, catalog, edited)['reasons'] == ['candidate_changed']
    old = copy.deepcopy(candidate); old['schema'] = 'report-evidence-candidate-wire-v5'
    for step in old['businesses'][0]['steps']:
        for key in ('next_index', 'else_index'):
            step[key] = None if step[key]['kind'] == 'end' else step[key]['index']
    old = ManualReviewValueV5.model_validate({'schema': MANUAL_VALUE, 'result': old, 'note': ''}).model_dump()['result']
    assert status(checked, catalog, old)['status'] == 'stale'


def test_source_or_catalog_change_makes_history_stale_even_if_unit_ids_and_target_stay_the_same():
    catalog, candidate = scene(); checked = check(record(catalog, candidate), catalog, candidate)
    changed = build_evidence_source('b' * 64, [['w:p3', '改后。'], ['o:p1', ''], ['w:p1', ' \t '], ['o:p2', '相同。']], max_span_chars=4)
    result = status(checked, changed, candidate)
    assert result['status'] == 'stale' and result['reasons'] == ['source_changed', 'catalog_changed', 'source_units_changed']
    corrupted = copy.deepcopy(changed); corrupted['index']['w:p3/s1']['text'] = '不能伪重签'
    with pytest.raises(ValueError): status(checked, corrupted, candidate)


@pytest.mark.parametrize('mode', ['record', 'unit', 'target', 'check_hash', 'old_checked_schema', 'extra'])
def test_stale_never_excuses_damaged_historical_record_or_unknown_protocol(mode):
    catalog, candidate = scene(); checked = check(record(catalog, candidate), catalog, candidate)
    if mode == 'record': checked['record']['reason'] = '篡改'
    elif mode == 'unit': checked['source_unit_bindings'][0]['unit_hash'] = 'f' * 64
    elif mode == 'target': checked['target_bindings'][0]['target_value_hash'] = 'f' * 64
    elif mode == 'check_hash': checked['check_hash'] = 'f' * 64
    elif mode == 'old_checked_schema': checked['schema'] = 'report-source-requirement-checked-v0'
    else: checked['approved'] = True
    changed = copy.deepcopy(candidate); changed['domain'] += '新版'
    with pytest.raises(ValueError): status(checked, catalog, changed)


def test_resealed_current_target_binding_cannot_replace_original_value_and_cold_comparison():
    catalog, candidate = scene(); checked = check(record(catalog, candidate), catalog, candidate)
    checked['target_bindings'][0]['target_value_hash'] = 'f' * 64
    checked['check_hash'] = digest({key: value for key, value in checked.items() if key != 'check_hash'})
    # Same original bindings permit a full cold rebuild: this is corruption,
    # not a new candidate and not a valid historical stale confirmation.
    with pytest.raises(ValueError): status(checked, catalog, candidate)


def test_pending_candidate_preserves_work_then_requires_new_review_when_candidate_appears():
    catalog, candidate = scene(); value = record(catalog, None); checked = check(value, catalog, None)
    assert checked['record']['candidate_hash'] is None and checked['target_bindings'] == []
    assert status(checked, catalog, None)['status'] == 'pending_candidate'
    assert status(checked, catalog, candidate)['status'] == 'stale'
    assert status(checked, catalog, candidate)['reasons'] == ['candidate_changed']
    for disposition in ('represented', 'missing', 'incorrect', 'out_of_scope'):
        with pytest.raises(ValueError): check({**value, 'disposition': disposition}, catalog, None)
    with pytest.raises(ValueError): check({**value, 'targets': ['/domain']}, catalog, None)
    with pytest.raises(ValueError):
        check_requirement_record(value, catalog=catalog, candidate_result=None, candidate_hash=digest(candidate))
    with pytest.raises(ValueError):
        check_requirement_record(record(catalog, candidate), catalog=catalog, candidate_result=candidate, candidate_hash=None)
    assert value == record(catalog, None)
