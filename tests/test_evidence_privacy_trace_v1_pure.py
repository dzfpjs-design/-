"""Synthetic privacy intervals only. Root executes; no original or real secrets."""
import copy
import hashlib
import json

import pytest

from generic_agents.contracts import digest
from generic_agents.evidence_privacy_trace_v1 import (
    SCHEMA, PRIVACY_VERSION, build_privacy_trace, check_privacy_trace, raw_interval,
)


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def resign(value):
    value['trace_hash'] = digest({key: item for key, item in value.items() if key != 'trace_hash'})
    return value


@pytest.mark.parametrize('raw', [
    '', '甲🙂乙\n 保留首尾空白 ', 'literal [凭证已移除] is not a redaction',
    'SK-PUBLIC_FAKE_0123456789', 'Bearer PUBLIC_FAKE_0123456789',
    'api_key=PUBLIC_FAKE_VALUE; password：PUBLIC_FAKE_VALUE，密钥=PUBLIC_FAKE_VALUE',
    'fake.public+tag@example.invalid', '合成号码13800000000与00000000000000000X',
    '🙂secret=sk-public_fake_0123456789;fake.public@example.invalid 13800000000',
    'a@b.invalid13800000000', 'ordinary\x00text',
    'sk-short bearer short invalid@local 0138000000000',
    'password=Bearer \nPUBLIC_FAKE_0123456789;尾',
])
def test_exact_parity_with_unchanged_production_scrub(raw):
    # Importing/calling is reserved for Root's isolated pure-test execution.
    from generic_agents.report_evidence_pipeline import scrub, PRIVACY_VERSION as production_version
    trace = build_privacy_trace(raw)
    assert trace['schema'] == SCHEMA and trace['privacy_version'] == PRIVACY_VERSION == production_version
    assert trace['text'] == scrub(raw) and trace['raw_text_hash'] == sha(raw)
    assert set(trace) == {'schema', 'privacy_version', 'text', 'segments', 'trace_hash', 'raw_text_hash'}
    assert trace['trace_hash'] == digest({k: v for k, v in trace.items() if k != 'trace_hash'})
    assert check_privacy_trace(trace, raw) is None


def test_identity_uses_unicode_codepoints_and_exact_literal_slices():
    raw = ' 甲🙂e\u0301乙\n'
    trace = build_privacy_trace(raw)
    assert trace['segments'] == [{'start': 0, 'end': len(raw), 'raw_start': 0, 'raw_end': len(raw), 'redacted': False}]
    for start in range(len(raw) + 1):
        for end in range(start, len(raw) + 1):
            mapped = raw_interval(trace, start, end)
            assert mapped == {'raw_start': start, 'raw_end': end, 'contains_redaction': False, 'partial_redaction': False}
            assert raw[mapped['raw_start']:mapped['raw_end']] == trace['text'][start:end]


def test_later_regex_composes_original_prefix_and_already_redacted_credential():
    raw = '🙂secret=sk-public_fake_0123456789;尾'
    trace = build_privacy_trace(raw); marker = '[凭证已移除]'; end = raw.index(';')
    assert trace['text'] == '🙂' + marker + ';尾'
    assert trace['segments'] == [
        {'start': 0, 'end': 1, 'raw_start': 0, 'raw_end': 1, 'redacted': False},
        {'start': 1, 'end': 1 + len(marker), 'raw_start': 1, 'raw_end': end, 'redacted': True},
        {'start': 1 + len(marker), 'end': len(trace['text']), 'raw_start': end, 'raw_end': len(raw), 'redacted': False}]
    assert raw_interval(trace, 1, 1 + len(marker)) == {
        'raw_start': 1, 'raw_end': end, 'contains_redaction': True, 'partial_redaction': False}
    assert raw_interval(trace, 2, 4) == {
        'raw_start': 1, 'raw_end': end, 'contains_redaction': True, 'partial_redaction': True}


def test_independent_adjacent_redactions_are_not_merged_and_all_passes_stay_ordered():
    raw = 'a@b.invalid13800000000'
    trace = build_privacy_trace(raw); first, second = '[邮箱已移除]', '[个人号码已移除]'
    assert trace['text'] == first + second and len(trace['segments']) == 2
    assert raw_interval(trace, 0, len(first)) == {
        'raw_start': 0, 'raw_end': len('a@b.invalid'), 'contains_redaction': True, 'partial_redaction': False}
    assert raw_interval(trace, len(first), len(first + second)) == {
        'raw_start': len('a@b.invalid'), 'raw_end': len(raw), 'contains_redaction': True, 'partial_redaction': False}
    multi = 'sk-public_fake_0123456789; token=PUBLIC_FAKE_VALUE; a@b.invalid; 13800000000'
    mapped = build_privacy_trace(multi)
    assert [mapped['text'][s['start']:s['end']] for s in mapped['segments'] if s['redacted']] == [
        '[凭证已移除]', '[凭证已移除]', '[邮箱已移除]', '[个人号码已移除]']
    assert raw_interval(mapped, 0, len(mapped['text'])) == {
        'raw_start': 0, 'raw_end': len(multi), 'contains_redaction': True, 'partial_redaction': False}


def test_slices_crossing_marker_edges_and_empty_positions_are_explicitly_conservative():
    raw = '甲13800000000乙'; trace = build_privacy_trace(raw)
    marker = next(s for s in trace['segments'] if s['redacted'])
    a, b = marker['start'], marker['end']
    assert raw_interval(trace, 0, b) == {'raw_start': 0, 'raw_end': len(raw)-1,
        'contains_redaction': True, 'partial_redaction': False}
    assert raw_interval(trace, a+1, len(trace['text'])) == {'raw_start': 1, 'raw_end': len(raw),
        'contains_redaction': True, 'partial_redaction': True}
    for point, raw_point in ((0, 0), (a, 1), (b, len(raw)-1), (len(trace['text']), len(raw))):
        assert raw_interval(trace, point, point) == {'raw_start': raw_point, 'raw_end': raw_point,
            'contains_redaction': False, 'partial_redaction': False}
    assert raw_interval(trace, a+1, a+1) == {'raw_start': 1, 'raw_end': len(raw)-1,
        'contains_redaction': True, 'partial_redaction': True}
    empty = build_privacy_trace('')
    assert empty['segments'] == [] and raw_interval(empty, 0, 0) == {
        'raw_start': 0, 'raw_end': 0, 'contains_redaction': False, 'partial_redaction': False}


@pytest.mark.parametrize('start,end', [(-1, 1), (0, 4), (2, 1), (True, 1), (0, False), (0.0, 1), ('0', 1), (None, 1)])
def test_invalid_ranges_and_boolean_coercion_fail_closed(start, end):
    with pytest.raises(ValueError):
        raw_interval(build_privacy_trace('甲🙂乙'), start, end)


@pytest.mark.parametrize('mutation', ['text', 'raw-hash', 'coverage', 'coordinate', 'flag', 'extra', 'schema', 'split'])
def test_self_resigned_hashes_cannot_replace_cold_reconstruction(mutation):
    raw = '甲13800000000乙'; trace = build_privacy_trace(raw)
    damaged = copy.deepcopy(trace)
    if mutation == 'text': damaged['text'] = '丙' + damaged['text'][1:]
    elif mutation == 'raw-hash': damaged['raw_text_hash'] = '0' * 64
    elif mutation == 'coverage': damaged['segments'].pop()
    elif mutation == 'coordinate': damaged['segments'][1]['raw_end'] -= 1
    elif mutation == 'flag': damaged['segments'][1]['redacted'] = False
    elif mutation == 'extra': damaged['raw_text'] = 'synthetic forbidden raw field'
    elif mutation == 'schema': damaged['privacy_version'] = 'evidence-privacy-v2'
    else:
        # A semantically identical but noncanonical split is still not this trace.
        original = build_privacy_trace('甲🙂乙'); damaged = copy.deepcopy(original)
        damaged['segments'] = [
            {'start': 0, 'end': 1, 'raw_start': 0, 'raw_end': 1, 'redacted': False},
            {'start': 1, 'end': 3, 'raw_start': 1, 'raw_end': 3, 'redacted': False}]
        raw = '甲🙂乙'
    resign(damaged)
    with pytest.raises(ValueError): check_privacy_trace(damaged, raw)
    assert trace == build_privacy_trace('甲13800000000乙')


def test_literal_marker_is_identity_not_redacted_and_raw_is_not_returned():
    raw = 'literal [凭证已移除] then sk-public_fake_0123456789'
    trace = build_privacy_trace(raw); saved = copy.deepcopy(trace)
    literal = trace['text'].index('[凭证已移除]')
    assert raw_interval(trace, literal+1, literal+2)['contains_redaction'] is False
    check_privacy_trace(trace, raw)
    raw_interval(trace, 0, len(trace['text']))
    assert trace == saved and 'sk-public_fake_0123456789' not in json.dumps(trace, ensure_ascii=False)
    assert all(set(s) == {'start', 'end', 'raw_start', 'raw_end', 'redacted'} for s in trace['segments'])


def test_same_masked_text_and_intervals_do_not_authenticate_a_different_original():
    first, second = '甲13800000000乙', '甲13900000000乙'
    trace, other = build_privacy_trace(first), build_privacy_trace(second)
    assert trace['text'] == other['text'] and trace['segments'] == other['segments']
    assert trace['raw_text_hash'] != other['raw_text_hash'] and trace['trace_hash'] != other['trace_hash']
    with pytest.raises(ValueError): check_privacy_trace(trace, second)
    assert check_privacy_trace(trace, first) is None


def test_large_literal_input_is_interval_sized_not_codepoint_sized():
    raw = '甲🙂' * 500000 + '13800000000Z'
    trace = build_privacy_trace(raw)
    assert len(trace['segments']) == 3
    assert raw_interval(trace, 999999, 1000000) == {'raw_start': 999999, 'raw_end': 1000000,
        'contains_redaction': False, 'partial_redaction': False}
    repeated = build_privacy_trace('plain secret=PUBLIC_FAKE_VALUE;' * 1000)
    assert len(repeated['segments']) == 2001
    assert repeated['text'] == 'plain [凭证已移除];' * 1000
    assert check_privacy_trace(repeated, 'plain secret=PUBLIC_FAKE_VALUE;' * 1000) is None


@pytest.mark.parametrize('raw', [None, b'synthetic', 1, True, '\ud800'])
def test_nontext_or_invalid_utf8_cannot_produce_a_trace(raw):
    with pytest.raises(ValueError): build_privacy_trace(raw)
