"""Pure exact raw-region mapping; caller authenticates sources and current pages.

Codepoint offsets are half-open, never PDF byte positions. A mapping proves only
exact text/interval consistency with supplied inputs. It neither confirms pages,
authenticates a material/version nor approves hidden content or business meaning.
Privacy-projected matches cover real raw text; a replacement marker is not a
literal quotation. Cross-page selections are continuous: first-page suffix,
last-page prefix and whole intermediate pages, including empty witnesses.
No headers or intervening characters may be omitted. No raw text, masked
quotation, note or authority is returned.
"""
import copy
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from uuid import UUID

from .contracts import digest
from .evidence_privacy_trace_v1 import build_privacy_trace, check_privacy_trace, raw_interval
from .report_full_semantic_source_manifest_v1 import _raw_location

SCHEMA = 'report-full-semantic-region-mapping-v1'
MAX_CANDIDATES = 100
_HASH = re.compile(r'^[a-f0-9]{64}$')
_COORD = re.compile(r'^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$')
_REF = set('path owner_path owner_value_hash span_ref source_ref visible_start visible_end text_hash privacy_relation source_anchor ref_hash'.split())
_ANCHOR = set('material_ref evidence_id raw_text_hash raw_start raw_end raw_location trace_hash contains_redaction partial_redaction'.split())
_META = ('page', 'unit_id', 'revision_id', 'revision_hash', 'revision_kind', 'image_sha256')
_PAGE = set(_META) | {'regions'}
_SELECTION = (_PAGE - {'regions'}) | {'start', 'end'}


class RegionMappingCapacityExceeded(ValueError):
    """Complete candidate enumeration exceeded the requested cap; none selected."""


def _need(ok, code):
    if not ok:
        raise ValueError('full_semantic_region_' + code)


def _json(value):
    def safe(item, depth=0):
        _need(depth <= 48, 'json_depth')
        if type(item) is str:
            _need('\x00' not in item, 'nul')
            item.encode('utf-8', 'strict')
        elif type(item) is dict:
            for key, child in item.items():
                _need(type(key) is str, 'json_key')
                safe(key, depth + 1); safe(child, depth + 1)
        elif type(item) is list:
            for child in item:
                safe(child, depth + 1)
        else:
            _need(item is None or type(item) in (bool, int), 'json_type')
    try:
        safe(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
    except (UnicodeError, RecursionError, TypeError, OverflowError):
        raise ValueError('full_semantic_region_unsafe_json') from None


def _keys(value, expected, code):
    _need(type(value) is dict and set(value) == expected, code)


def _hash(value):
    _need(type(value) is str and _HASH.fullmatch(value), 'hash')


def _uuid(value):
    try:
        _need(type(value) is str and str(UUID(value)) == value, 'uuid')
    except (ValueError, AttributeError):
        raise ValueError('full_semantic_region_uuid') from None


def _text(value):
    _need(type(value) is str, 'text')
    _json(value)
    return value


def _sha(text):
    return hashlib.sha256(_text(text).encode('utf-8')).hexdigest()


def _int(value, minimum, maximum=None):
    _need(type(value) is int and minimum <= value and (maximum is None or value <= maximum), 'integer')


def _box(value):
    _need(type(value) is list and len(value) == 4, 'box')
    _need(all(type(v) is str and len(v) <= 64 and _COORD.fullmatch(v) for v in value), 'box_coordinate')
    try:
        numbers = [Decimal(v) for v in value]
    except InvalidOperation:
        raise ValueError('full_semantic_region_box_coordinate') from None
    _need(all(v.is_finite() for v in numbers) and min(numbers[:2]) >= 0
        and min(numbers[2:]) > 0, 'box_range')
    # Image dimensions and image-byte ownership are deliberately caller checks.


def _page(value):
    _keys(value, _PAGE, 'page_shape')
    _int(value['page'], 1)
    for key in ('unit_id', 'revision_id'):
        _uuid(value[key])
    for key in ('revision_hash', 'image_sha256'):
        _hash(value[key])
    _need(value['revision_kind'] in ('raw', 'edited', 'confirmed'), 'revision_kind')
    _need(type(value['regions']) is list and len(value['regions']) <= 500, 'regions')
    seen = set(); length = 0
    for region in value['regions']:
        _keys(region, {'id', 'text', 'box'}, 'region_shape')
        _need(type(region['id']) is str and 0 < len(region['id']) <= 100
            and region['id'] not in seen, 'region_id')
        _text(region['id']); seen.add(region['id'])
        length += len(_text(region['text'])); _box(region['box'])
    _need(length <= 20000, 'page_text_capacity')
    return ''.join(region['text'] for region in value['regions'])


def _pages(pages):
    _need(type(pages) is list and 1 <= len(pages) <= 500, 'pages')
    texts = [_page(page) for page in pages]
    first = pages[0]['page']
    _need([p['page'] for p in pages] == list(range(first, first + len(pages))), 'page_order')
    for key in ('unit_id', 'revision_id'):
        _need(len({p[key] for p in pages}) == len(pages), 'duplicate_' + key)
    return texts


def _source(ref, block):
    _json(ref); _json(block)
    _need(type(block) is dict, 'block_shape')
    _keys(ref, _REF, 'ref_shape')
    _need(type(ref['owner_path']) is str and ref['owner_path'].startswith('/')
        and type(ref['path']) is str and ref['path'].startswith(ref['owner_path'] + '/')
        and len(ref['path']) <= 500, 'ref_path')
    _need(type(ref['source_ref']) is str and re.fullmatch(r'[wo]:p[1-9]\d*', ref['source_ref'])
        and type(ref['span_ref']) is str
        and re.fullmatch(re.escape(ref['source_ref']) + r'/s[1-9]\d*', ref['span_ref']), 'source_ref')
    for key in ('owner_value_hash', 'text_hash', 'ref_hash'):
        _hash(ref[key])
    _need(ref['ref_hash'] == digest({k: v for k, v in ref.items() if k != 'ref_hash'}), 'ref_hash')
    anchor = ref['source_anchor']; _keys(anchor, _ANCHOR, 'anchor_shape')
    material = anchor['material_ref']; _keys(material, {'id', 'version', 'hash'}, 'material_ref')
    _uuid(material['id']); _int(material['version'], 1, 2 ** 63 - 1); _hash(material['hash'])
    _uuid(anchor['evidence_id'])
    for key in ('raw_text_hash', 'trace_hash'):
        _hash(anchor[key])
    _need(type(anchor['contains_redaction']) is bool and type(anchor['partial_redaction']) is bool, 'privacy_flags')
    kind = block.get('kind')
    _keys(block, {'text', 'kind', 'material_id', 'location'} | ({'regions'} if kind == 'ocr_page' else set()), 'block_shape')
    _need(kind in ('paragraph', 'table_paragraph', 'textbox_paragraph', 'ocr_page')
        and material['id'] == block['material_id'], 'source_material')
    raw = _text(block['text'])
    _need(type(block['location']) is dict, 'raw_location')
    if kind == 'ocr_page':
        location = block['location']
        _keys(location, {'page', 'run_id', 'unit_id', 'revision_id', 'revision_hash', 'revision_kind', 'image_sha256'}, 'ocr_location')
        _need(ref['source_ref'] == 'o:p' + str(location['page']), 'ocr_source_ref')
        _uuid(location['run_id'])
        _need(type(block['regions']) is list, 'source_regions')
        regions = []; cursor = 0
        for ordinal, region in enumerate(block['regions'], 1):
            _keys(region, {'id', 'text', 'box', 'score', 'source_region_id', 'ordinal', 'start', 'end'}, 'source_region_shape')
            _int(region['ordinal'], ordinal, ordinal); _int(region['start'], cursor, cursor)
            cursor += len(_text(region['text'])); _int(region['end'], cursor, cursor)
            _need(region['source_region_id'] is None or type(region['source_region_id']) is str, 'source_region_lineage')
            _need(region['score'] is None or type(region['score']) is str, 'source_region_score')
            regions.append({k: region[k] for k in ('id', 'text', 'box')})
        _need(_page({**{k: location[k] for k in _META}, 'regions': regions}) == raw, 'source_region_text')
    else:
        _need(ref['source_ref'].startswith('w:') and block['location'].get('view') == 'accepted_final_v1', 'word_source_ref')
    trace = build_privacy_trace(raw); check_privacy_trace(trace, raw)
    _int(ref['visible_start'], 0, len(trace['text'])); _int(ref['visible_end'], ref['visible_start'] + 1, len(trace['text']))
    visible = trace['text'][ref['visible_start']:ref['visible_end']]
    _need(bool(visible.strip()) and _sha(visible) == ref['text_hash'], 'visible_quote')
    interval = raw_interval(trace, ref['visible_start'], ref['visible_end'])
    _need(all(type(anchor[key]) is type(value) and anchor[key] == value for key, value in interval.items())
        and anchor['raw_text_hash'] == trace['raw_text_hash'] and anchor['trace_hash'] == trace['trace_hash'], 'source_trace')
    _need(_json(anchor['raw_location']) == _json(_raw_location(block, interval['raw_start'], interval['raw_end'])), 'source_raw_location')
    expected_relation = 'privacy_projected' if interval['contains_redaction'] else 'identity'
    _need(ref['privacy_relation'] == expected_relation, 'source_privacy_relation')
    needle = raw[interval['raw_start']:interval['raw_end']]
    _need(bool(needle) and (interval['contains_redaction'] or needle == visible), 'source_interval')
    return needle


def _masked_interval(trace, start, end):
    """Raw selection -> conservative whole replacement markers, never raw text."""
    pieces = []; contains = partial = False
    for item in trace['segments']:
        left, right = max(start, item['raw_start']), min(end, item['raw_end'])
        if left >= right:
            continue
        if item['redacted']:
            pieces.append(trace['text'][item['start']:item['end']]); contains = True
            partial |= left > item['raw_start'] or right < item['raw_end']
        else:
            offset = item['start'] + left - item['raw_start']
            pieces.append(trace['text'][offset:offset + right - left])
    return ''.join(pieces), contains, partial


def _target_segment(page, text, start, end):
    spans = []; cursor = 0
    metadata = {k: page[k] for k in _META}
    for region in page['regions']:
        stop = cursor + len(region['text']); left, right = max(start, cursor), min(end, stop)
        if left < right:
            spans.append({**metadata, 'region_id': region['id'], 'region_text_hash': _sha(region['text']),
                'box': copy.deepcopy(region['box']), 'start': left - cursor, 'end': right - cursor})
        cursor = stop
    _need(sum(s['end'] - s['start'] for s in spans) == end - start, 'target_region_gap')
    trace = build_privacy_trace(text); check_privacy_trace(trace, text)
    visible, contains, partial = _masked_interval(trace, start, end)
    return {**metadata, 'start': start, 'end': end, 'trace_hash': trace['trace_hash'],
        'visible_quote_hash': _sha(visible), 'contains_redaction': contains,
        'partial_redaction': partial, 'spans': spans}, visible


def build_region_mapping(ref, source_raw_block, pages, segments, *, copy_material_id):
    """Validate explicit selections. Caller checks real current-copy/page authority."""
    _uuid(copy_material_id)
    needle = _source(ref, source_raw_block); texts = _pages(pages)
    _need(type(segments) is list and 1 <= len(segments) <= len(pages), 'selections')
    by_page = {page['page']: (page, text) for page, text in zip(pages, texts)}
    result = []; raw_parts = []; visible_parts = []
    for index, selection in enumerate(segments):
        _keys(selection, _SELECTION, 'selection_shape')
        _int(selection['page'], 1)
        _need(selection['page'] in by_page, 'selection_page')
        if index:
            _need(selection['page'] == segments[index - 1]['page'] + 1, 'selection_order')
        page, text = by_page[selection['page']]
        _need(_json({k: selection[k] for k in _META})
            == _json({k: page[k] for k in _META}), 'selection_version')
        start, end = selection['start'], selection['end']
        _int(start, 0, len(text)); _int(end, start, len(text))
        _need(start < end or (text == '' and start == end == 0 and 0 < index < len(segments) - 1), 'empty_selection')
        if len(segments) > 1:
            _need((index == 0 or start == 0)
                and (index == len(segments) - 1 or end == len(text)), 'selection_page_seam')
        segment, visible = _target_segment(page, text, start, end)
        result.append(segment); raw_parts.append(text[start:end]); visible_parts.append(visible)
    _need(''.join(raw_parts) == needle, 'raw_quote_mismatch')
    contains = ref['source_anchor']['contains_redaction'] or any(s['contains_redaction'] for s in result)
    if not contains:
        _need(''.join(visible_parts) == needle, 'identity_quote_mismatch')
    value = {'schema': SCHEMA, 'ref_hash': ref['ref_hash'], 'ref_path': ref['path'],
        'owner_value_hash': ref['owner_value_hash'], 'source_anchor_hash': digest(ref['source_anchor']),
        'copy_material_id': copy_material_id, 'privacy_relation': 'privacy_projected' if contains else 'identity',
        'visible_quote_hash': ref['text_hash'], 'raw_quote_hash': _sha(needle), 'segments': result}
    return {**value, 'mapping_hash': digest(value)}


def check_region_mapping(value, ref, source_raw_block, pages, segments, *, copy_material_id):
    """Exact cold rebuild, including raw input hashes; a self-seal is insufficient."""
    expected = build_region_mapping(ref, source_raw_block, pages, segments, copy_material_id=copy_material_id)
    _need(_json(value) == _json(expected), 'does_not_cold_rebuild')
    return expected


def find_region_candidates(ref, source_raw_block, pages, *, copy_material_id, max_candidates=100):
    """Return every overlapping exact occurrence, or fail without truncation.

    Caller passes its full prepared range. This function does not prove that a
    supplied subrange contains every page of a document or choose an occurrence.
    Interior truly empty pages are retained as explicit zero-width witnesses.
    """
    _uuid(copy_material_id); _int(max_candidates, 1, MAX_CANDIDATES)
    needle = _source(ref, source_raw_block); texts = _pages(pages)
    joined = ''.join(texts); candidates = []; offset = 0; layout = []
    for text in texts:
        layout.append((offset, offset + len(text))); offset += len(text)
    cursor = 0
    while True:
        found = joined.find(needle, cursor)
        if found < 0:
            return candidates
        if len(candidates) >= max_candidates:
            raise RegionMappingCapacityExceeded('full_semantic_region_candidate_capacity')
        stop = found + len(needle)
        touched = [i for i, (a, b) in enumerate(layout) if max(a, found) < min(b, stop)]
        selections = []
        for index in range(touched[0], touched[-1] + 1):
            left, right = layout[index]; page = pages[index]
            selections.append({**{k: page[k] for k in _META},
                'start': max(found, left) - left if left < right else 0,
                'end': min(stop, right) - left if left < right else 0})
        candidates.append(build_region_mapping(ref, source_raw_block, pages, selections, copy_material_id=copy_material_id))
        cursor = found + 1
