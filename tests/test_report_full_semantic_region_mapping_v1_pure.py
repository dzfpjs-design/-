"""Inline synthetic unit tests only; Root executes pytest, not the source author.

These authenticate neither documents, page confirmations nor formal authority.
Opaque owner hashes stand for every manifest family; no source semantics guessed.
"""
import copy
import hashlib
import json
from uuid import UUID

import pytest

from generic_agents.contracts import digest
from generic_agents.evidence_privacy_trace_v1 import build_privacy_trace, raw_interval
from generic_agents.report_full_semantic_source_manifest_v1 import _raw_location
from generic_agents.report_full_semantic_region_mapping_v1 import (
    RegionMappingCapacityExceeded, build_region_mapping, check_region_mapping,
    find_region_candidates,
)


def uid(number):
    return str(UUID(int=number))


COPY = uid(900)


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def seal(value, key):
    return {**{k: v for k, v in value.items() if k != key}, key: digest({k: v for k, v in value.items() if k != key})}


def page(number, *texts):
    return {'page': number, 'unit_id': uid(number), 'revision_id': uid(1000 + number),
        'revision_hash': digest(['revision', number]), 'revision_kind': 'confirmed',
        'image_sha256': digest(['image', number]), 'regions': [
            {'id': f'r{i}', 'text': text, 'box': ['0', str(i), '12', '1']}
            for i, text in enumerate(texts)]}


def selection(p, start, end):
    return {**{k: v for k, v in p.items() if k != 'regions'}, 'start': start, 'end': end}


def source(raw, *, start=0, end=None, owner='/businesses/0/steps/0', ocr=False):
    if ocr:
        p = page(7, raw[:1], '', raw[1:]); offset = 0; regions = []
        for ordinal, region in enumerate(p['regions'], 1):
            stop = offset + len(region['text'])
            regions.append({**region, 'score': None, 'source_region_id': None,
                'ordinal': ordinal, 'start': offset, 'end': stop}); offset = stop
        block = {'text': raw, 'kind': 'ocr_page', 'material_id': COPY,
            'location': {**{k: v for k, v in p.items() if k != 'regions'}, 'run_id': uid(888)},
            'regions': regions}
        source_ref = 'o:p7'
    else:
        block = {'text': raw, 'kind': 'paragraph', 'material_id': uid(901),
            'location': {'view': 'accepted_final_v1', 'body_index': 1, 'paragraph_index': 1}}
        source_ref = 'w:p1'
    trace = build_privacy_trace(raw)
    end = len(trace['text']) if end is None else end
    interval = raw_interval(trace, start, end)
    anchor = {'material_ref': {'id': block['material_id'], 'version': 1, 'hash': digest('material')},
        'evidence_id': uid(902), 'raw_text_hash': trace['raw_text_hash'],
        'raw_start': interval['raw_start'], 'raw_end': interval['raw_end'],
        'raw_location': _raw_location(block, interval['raw_start'], interval['raw_end']),
        'trace_hash': trace['trace_hash'], 'contains_redaction': interval['contains_redaction'],
        'partial_redaction': interval['partial_redaction']}
    ref = seal({'path': owner + '/citations/0', 'owner_path': owner,
        'owner_value_hash': digest(['owner', owner]), 'span_ref': source_ref + '/s1',
        'source_ref': source_ref, 'visible_start': start, 'visible_end': end,
        'text_hash': sha(trace['text'][start:end]), 'privacy_relation':
            'privacy_projected' if interval['contains_redaction'] else 'identity',
        'source_anchor': anchor}, 'ref_hash')
    return ref, block


def build(ref, block, pages, selections):
    return build_region_mapping(ref, block, pages, selections, copy_material_id=COPY)


def find(ref, block, pages, **options):
    return find_region_candidates(ref, block, pages, copy_material_id=COPY, **options)


def test_identity_unicode_region_intersections_and_no_mutation():
    ref, block = source('甲😀乙0'); p = page(8, '前甲', '', '😀乙0后')
    selections = [selection(p, 1, 5)]; inputs = copy.deepcopy([ref, block, [p], selections])
    value = build(ref, block, [p], selections)
    assert [ref, block, [p], selections] == inputs
    assert set(value) == {'schema', 'ref_hash', 'ref_path', 'owner_value_hash', 'source_anchor_hash',
        'copy_material_id', 'privacy_relation', 'visible_quote_hash', 'raw_quote_hash', 'segments', 'mapping_hash'}
    assert value['privacy_relation'] == 'identity' and value['raw_quote_hash'] == sha('甲😀乙0')
    s = value['segments'][0]
    assert set(s) == {'page', 'unit_id', 'revision_id', 'revision_hash', 'revision_kind', 'image_sha256',
        'start', 'end', 'trace_hash', 'visible_quote_hash', 'contains_redaction', 'partial_redaction', 'spans'}
    assert (s['start'], s['end'], s['visible_quote_hash']) == (1, 5, sha('甲😀乙0'))
    assert [(x['region_id'], x['start'], x['end']) for x in s['spans']] == [('r0', 1, 2), ('r2', 0, 3)]
    expected_first = {**{k: v for k, v in p.items() if k != 'regions'}, 'region_id': 'r0',
        'region_text_hash': sha('前甲'), 'box': ['0', '0', '12', '1'], 'start': 1, 'end': 2}
    assert s['spans'][0] == expected_first
    assert check_region_mapping(value, ref, block, [p], selections, copy_material_id=COPY) == value
    assert '前甲' not in json.dumps(value, ensure_ascii=False)


@pytest.mark.parametrize('owner', ['/global_claims/0', '/businesses/0', '/businesses/0/steps/0',
    '/businesses/0/participants/0', '/businesses/0/interactions/0', '/businesses/0/conditions/0',
    '/businesses/0/coordination/0', '/objects/0', '/objects/0/fields/0', '/relationships/0'])
def test_every_cited_owner_is_an_opaque_exact_binding(owner):
    ref, block = source('合成事实', owner=owner); p = page(1, '合成事实')
    value = build(ref, block, [p], [selection(p, 0, 4)])
    assert value['ref_path'] == owner + '/citations/0'
    assert value['owner_value_hash'] == ref['owner_value_hash']
    changed = copy.deepcopy(ref); changed['owner_value_hash'] = digest('other owner'); changed = seal(changed, 'ref_hash')
    with pytest.raises(ValueError):
        check_region_mapping(value, changed, block, [p], [selection(p, 0, 4)], copy_material_id=COPY)


def test_ocr_source_anchor_uses_original_region_offsets():
    ref, block = source('甲😀乙', ocr=True); p = page(10, '甲😀乙')
    value = build(ref, block, [p], [selection(p, 0, 3)])
    assert value['source_anchor_hash'] == digest(ref['source_anchor'])
    assert [r['region_id'] for r in ref['source_anchor']['raw_location']['regions']] == ['r0', 'r2']
    wrong = copy.deepcopy(block); wrong['regions'][2]['start'] = 0
    with pytest.raises(ValueError):
        build(ref, wrong, [p], [selection(p, 0, 3)])


def test_identity_after_redaction_uses_raw_not_visible_offsets():
    raw = '前 a@b.example 后甲😀'; trace = build_privacy_trace(raw)
    start = trace['text'].index('后'); ref, block = source(raw, start=start)
    p = page(4, '开头 a@b.example 后甲😀')
    found = find(ref, block, [p])
    assert len(found) == 1 and found[0]['privacy_relation'] == 'identity'
    segment = found[0]['segments'][0]
    assert segment['start'] == ''.join(r['text'] for r in p['regions']).index('后')
    assert segment['start'] > start and segment['contains_redaction'] is False


def test_partial_source_marker_maps_covering_secret_and_never_returns_it():
    secret = 'person@example.invalid'; raw = '前 ' + secret + ' 后'
    trace = build_privacy_trace(raw); marker = trace['text'].index('[邮箱')
    ref, block = source(raw, start=marker + 1, end=marker + 3)
    p = page(2, '另 ' + secret + ' 尾'); results = find(ref, block, [p])
    assert len(results) == 1 and ref['source_anchor']['partial_redaction'] is True
    value = results[0]; segment = value['segments'][0]
    assert value['privacy_relation'] == 'privacy_projected'
    assert segment['visible_quote_hash'] == sha('[邮箱已移除]')
    assert segment['contains_redaction'] is True and segment['partial_redaction'] is False
    assert value['raw_quote_hash'] == sha(secret)
    assert secret not in json.dumps(value, ensure_ascii=False)
    other = page(2, '另 different@example.invalid 尾')
    assert find(ref, block, [other]) == []
    with pytest.raises(ValueError):
        build(ref, block, [other], [selection(other, 2, 2 + len('different@example.invalid'))])


def test_partial_target_redaction_is_covering_not_literal():
    ref, block = source('example'); p = page(1, 'person@example.invalid')
    value = find(ref, block, [p])[0]; segment = value['segments'][0]
    assert value['privacy_relation'] == 'privacy_projected'
    assert segment['contains_redaction'] is True and segment['partial_redaction'] is True
    assert segment['visible_quote_hash'] == sha('[邮箱已移除]') != value['visible_quote_hash']


def test_repeated_and_overlapping_occurrences_all_returned():
    ref, block = source('aa'); p = page(1, 'aaaa')
    values = find(ref, block, [p])
    assert [(v['segments'][0]['start'], v['segments'][0]['end']) for v in values] == [(0, 2), (1, 3), (2, 4)]
    assert len({v['mapping_hash'] for v in values}) == 3
    with pytest.raises(RegionMappingCapacityExceeded):
        find(ref, block, [p], max_candidates=2)


def test_cross_page_no_insertions_and_interior_blank_witness():
    ref, block = source('甲😀乙0'); pages = [page(11, '前甲😀'), page(12), page(13, '乙0后')]
    values = find(ref, block, pages)
    assert len(values) == 1
    segments = values[0]['segments']
    assert [(s['page'], s['start'], s['end']) for s in segments] == [(11, 1, 3), (12, 0, 0), (13, 0, 2)]
    assert segments[1]['spans'] == [] and segments[1]['visible_quote_hash'] == sha('')
    assert segments[1]['trace_hash'] == build_privacy_trace('')['trace_hash']
    assert segments[1]['contains_redaction'] is segments[1]['partial_redaction'] is False
    selections = [selection(pages[0], 1, 3), selection(pages[1], 0, 0), selection(pages[2], 0, 2)]
    assert build(ref, block, pages, selections) == values[0]
    with pytest.raises(ValueError):
        build(ref, block, pages, [selections[0], selections[2]])
    newline_ref, newline_block = source('甲😀\n乙0')
    assert find(newline_ref, newline_block, pages) == []


def test_explicit_cross_page_must_not_omit_headers_or_intervening_characters():
    ref, block = source('甲乙'); pages = [page(1, '甲X'), page(2, 'Y乙')]
    assert find(ref, block, pages) == []
    with pytest.raises(ValueError):
        build(ref, block, pages, [selection(pages[0], 0, 1), selection(pages[1], 1, 2)])
    pages = [page(1, '页眉甲'), page(2, '页眉乙尾')]
    with pytest.raises(ValueError):
        build(ref, block, pages, [selection(pages[0], 2, 3), selection(pages[1], 2, 3)])
    pages = [page(1, '前甲'), page(2, '乙后')]
    value = build(ref, block, pages, [selection(pages[0], 1, 2), selection(pages[1], 0, 1)])
    assert value['raw_quote_hash'] == sha('甲乙')


def test_nonempty_interior_page_cannot_be_partially_omitted():
    ref, block = source('甲中乙'); pages = [page(1, '甲'), page(2, 'X中Y'), page(3, '乙')]
    with pytest.raises(ValueError):
        build(ref, block, pages, [selection(pages[0], 0, 1), selection(pages[1], 1, 2), selection(pages[2], 0, 1)])


@pytest.mark.parametrize('which', ['empty_first', 'empty_last', 'nonblank_empty', 'reverse', 'duplicate', 'gap'])
def test_empty_edges_nonblank_empties_and_nonconsecutive_pages_rejected(which):
    ref, block = source('甲乙'); pages = [page(1, '甲'), page(2, ''), page(3, '乙')]
    selections = [selection(pages[0], 0, 1), selection(pages[1], 0, 0), selection(pages[2], 0, 1)]
    if which == 'empty_first':
        pages = [page(1, ''), page(2, '甲乙')]; selections = [selection(pages[0], 0, 0), selection(pages[1], 0, 2)]
    elif which == 'empty_last':
        pages = [page(1, '甲乙'), page(2, '')]; selections = [selection(pages[0], 0, 2), selection(pages[1], 0, 0)]
    elif which == 'nonblank_empty':
        pages[1]['regions'] = [{'id': 'r0', 'text': '页眉', 'box': ['0', '0', '1', '1']}]
    elif which == 'reverse':
        selections.reverse()
    elif which == 'duplicate':
        selections.insert(1, copy.deepcopy(selections[0]))
    else:
        pages = [pages[0], pages[2]]; selections = [selections[0], selections[2]]
    with pytest.raises(ValueError):
        build(ref, block, pages, selections)


@pytest.mark.parametrize('field,value', [('start', True), ('end', 1.0), ('start', -1), ('end', 99),
    ('revision_hash', '0' * 64), ('revision_id', uid(333)), ('image_sha256', '1' * 64), ('extra', 0)])
def test_explicit_selection_types_versions_and_closed_shape(field, value):
    ref, block = source('甲'); p = page(1, '甲'); chosen = selection(p, 0, 1); chosen[field] = value
    with pytest.raises(ValueError):
        build(ref, block, [p], [chosen])


@pytest.mark.parametrize('change', ['raw_text', 'offset', 'trace_hash', 'raw_location', 'flag', 'text_hash', 'material', 'ref_extra'])
def test_resigned_source_anchor_is_not_a_cold_raw_proof(change):
    ref, block = source('甲😀乙'); p = page(1, '甲😀乙')
    if change == 'raw_text':
        block['text'] = '甲😀丙'
    elif change == 'offset':
        ref['source_anchor']['raw_start'] = 1
    elif change == 'trace_hash':
        ref['source_anchor']['trace_hash'] = '0' * 64
    elif change == 'raw_location':
        ref['source_anchor']['raw_location']['location']['body_index'] = 2
    elif change == 'flag':
        ref['source_anchor']['contains_redaction'] = 1
    elif change == 'text_hash':
        ref['text_hash'] = sha('其他')
    elif change == 'material':
        ref['source_anchor']['material_ref']['id'] = uid(333)
    else:
        ref['extra'] = True
    ref = seal(ref, 'ref_hash')
    with pytest.raises(ValueError):
        build(ref, block, [p], [selection(p, 0, 3)])


@pytest.mark.parametrize('change', ['span', 'bool_offset', 'segment_flag', 'extra', 'copy'])
def test_self_signed_mapping_tamper_rejected_by_complete_rebuild(change):
    ref, block = source('甲'); p = page(1, '甲'); selections = [selection(p, 0, 1)]
    value = build(ref, block, [p], selections)
    if change == 'span':
        value['segments'][0]['spans'][0]['box'][0] = '1'
    elif change == 'bool_offset':
        value['segments'][0]['start'] = False
    elif change == 'segment_flag':
        value['segments'][0]['contains_redaction'] = 0
    elif change == 'copy':
        value['copy_material_id'] = uid(444)
    else:
        value['candidate_accepted'] = True
    value = seal(value, 'mapping_hash')
    with pytest.raises(ValueError):
        check_region_mapping(value, ref, block, [p], selections, copy_material_id=COPY)


@pytest.mark.parametrize('change', ['region_duplicate', 'region_extra', 'box_number', 'box_nonfinite',
    'page_bool', 'revision_kind', 'unit_duplicate', 'revision_duplicate', 'surrogate', 'nul', 'page_extra'])
def test_malformed_descriptors_and_unsafe_unicode_rejected(change):
    ref, block = source('甲'); pages = [page(1, '甲'), page(2, '乙')]
    if change == 'region_duplicate':
        pages[0]['regions'].append(copy.deepcopy(pages[0]['regions'][0]))
    elif change == 'region_extra':
        pages[0]['regions'][0]['score'] = None
    elif change == 'box_number':
        pages[0]['regions'][0]['box'][0] = 0
    elif change == 'box_nonfinite':
        pages[0]['regions'][0]['box'][0] = 'NaN'
    elif change == 'page_bool':
        pages[0]['page'] = True
    elif change == 'revision_kind':
        pages[0]['revision_kind'] = 'unconfirmed_fake'
    elif change == 'unit_duplicate':
        pages[1]['unit_id'] = pages[0]['unit_id']
    elif change == 'revision_duplicate':
        pages[1]['revision_id'] = pages[0]['revision_id']
    elif change == 'surrogate':
        pages[0]['regions'][0]['text'] = '\ud800'
    elif change == 'nul':
        pages[0]['regions'][0]['text'] = '甲\x00'
    else:
        pages[0]['confirmed'] = True
    with pytest.raises(ValueError):
        find(ref, block, pages)


def test_candidate_and_page_capacities_are_complete_failures():
    ref, block = source('甲'); p = page(1, '甲' * 101)
    with pytest.raises(RegionMappingCapacityExceeded):
        find(ref, block, [p])
    for invalid in (0, True, 101, 1.0):
        with pytest.raises(ValueError):
            find(ref, block, [p], max_candidates=invalid)
    with pytest.raises(ValueError):
        find(ref, block, [page(1, '甲' * 20001)])
    with pytest.raises(ValueError):
        find(ref, block, [page(1, *([''] * 501))])
    assert len(find(ref, block, [page(1, '甲' * 100)])) == 100
    with pytest.raises(ValueError):
        find(ref, block, [page(n, '') for n in range(1, 502)])


def test_no_match_is_empty_and_inputs_returned_values_are_not_shared():
    ref, block = source('甲'); p = page(1, '乙')
    assert find(ref, block, [p]) == []
    p = page(1, '甲'); value = find(ref, block, [p])[0]
    value['segments'][0]['spans'][0]['box'][0] = '999'
    assert p['regions'][0]['box'][0] == '0'
    assert find(ref, block, [p])[0]['segments'][0]['spans'][0]['box'][0] == '0'


def test_supplied_range_can_start_at_any_positive_page_without_claiming_completeness():
    ref, block = source('甲乙'); pages = [page(900, '甲'), page(901, '乙')]
    result = find(ref, block, pages)
    assert [s['page'] for s in result[0]['segments']] == [900, 901]
