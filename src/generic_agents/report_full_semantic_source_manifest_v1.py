"""Exact citation occurrences in a caller-authenticated whole semantic design.

This pure inventory does not read source files/DB, approve OCR pages, authenticate
an issued design, or certify a statement/coverage/execution permission. The caller
must verify its active record and the original source bytes independently.
Offsets named visible are in the frozen privacy projection; raw offsets always
come from the checked privacy trace, including partial replacement markers.
No raw-source text/quote fields, private note or mutable formal-copy version is
returned. Human labels come only from the already supplied candidate values.
"""
import copy
import hashlib
import json
import re

from .contracts import digest
from .report_full_semantic_configuration_v1 import check_design
from .report_full_reading_semantics import _safe


SCHEMA = 'report-full-semantic-source-manifest-v1'
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_REFS = 65536
_HASH = re.compile(r'^[a-f0-9]{64}$')


class ManifestCapacityExceeded(ValueError):
    """Caller may map this explicit complete-inventory failure to HTTP413."""


def _need(condition, code):
    if not condition:
        raise ValueError(code)


def _bytes(value):
    try:
        _safe(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError('full_semantic_manifest_unsafe_json') from None


def _text_hash(text):
    return hashlib.sha256(text.encode('utf-8', 'strict')).hexdigest()


def _seal(value, key):
    return {**value, key: digest(value)}


def _privacy_tree(value, trace):
    # Exact pipeline.scrub_tree traversal, using the frozen four-filter trace.
    if type(value) is dict:
        return {trace(str(key))['text']: _privacy_tree(item, trace) for key, item in value.items()}
    if type(value) is list:
        return [_privacy_tree(item, trace) for item in value]
    return trace(value)['text'] if type(value) is str else value


def _source(design, catalog, source_snapshot, projection_hash):
    # Lazy imports avoid importing the report router/pipeline and its globals.
    from . import report_full_reading_source as full
    from .report_evidence_plan import build_evidence_source, check_evidence_source
    from .evidence_privacy_trace_v1 import build_privacy_trace

    _need(type(projection_hash) is str and _HASH.fullmatch(projection_hash),
        'full_semantic_manifest_projection_hash')
    try:
        snapshot = full.check_full_source(source_snapshot)
        projection, index = full.project_full_source(snapshot)
    except full.HTTPException as error:
        raise ValueError('full_semantic_manifest_source_snapshot') from error
    source_hash = digest(snapshot)
    _need(source_hash == design['source_hash'], 'full_semantic_manifest_source_changed')
    visible = _privacy_tree(projection, build_privacy_trace)
    _need(digest(visible) == projection_hash, 'full_semantic_manifest_privacy_projection_changed')
    check_evidence_source(catalog)
    expected = build_evidence_source(source_hash, [[b[0], b[3]] for b in visible['blocks']])
    _need(_bytes(catalog) == _bytes(expected), 'full_semantic_manifest_catalog_changed')
    _need(expected['descriptor']['catalog_hash'] == design['catalog_hash'],
        'full_semantic_manifest_design_catalog_changed')
    return snapshot, index, expected


def _raw_location(block, start, end):
    regions = []
    for region in block.get('regions', []):
        left, right = max(start, region['start']), min(end, region['end'])
        if left < right:
            regions.append({'region_id': region['id'], 'ordinal': region['ordinal'],
                'box': copy.deepcopy(region['box']), 'text_hash': _text_hash(region['text']),
                'page_start': region['start'], 'page_end': region['end'],
                'start': left - region['start'], 'end': right - region['start']})
    if block['kind'] == 'ocr_page':
        _need(sum(r['end'] - r['start'] for r in regions) == end - start,
            'full_semantic_manifest_raw_region_gap')
    return {'kind': block['kind'], 'location': copy.deepcopy(block['location']), 'regions': regions}


def _reference(path, owner_path, owner_hash, span_ref, catalog, snapshot, index, traces):
    from .evidence_privacy_trace_v1 import build_privacy_trace, check_privacy_trace, raw_interval

    _need(type(span_ref) is str and span_ref in catalog['index'],
        'full_semantic_manifest_reference_outside_source')
    item = catalog['index'][span_ref]
    _need(bool(item['text'].strip()), 'full_semantic_manifest_blank_reference')
    source_ref = item['source_ref']; block = index[source_ref]
    if source_ref not in traces:
        trace = build_privacy_trace(block['text'])
        check_privacy_trace(trace, block['text'])
        traces[source_ref] = trace
    trace = traces[source_ref]
    start, end = item['start'], item['end']
    _need(trace['text'][start:end] == item['text'], 'full_semantic_manifest_visible_span_changed')
    interval = raw_interval(trace, start, end)
    if not interval['contains_redaction']:
        _need(block['text'][interval['raw_start']:interval['raw_end']] == item['text'],
            'full_semantic_manifest_identity_interval_changed')
    material = snapshot['original' if source_ref.startswith('w:') else 'copy']
    anchor = {'material_ref': copy.deepcopy(material['ref']), 'evidence_id': material['evidence_id'],
        'raw_text_hash': trace['raw_text_hash'], 'raw_start': interval['raw_start'],
        'raw_end': interval['raw_end'],
        'raw_location': _raw_location(block, interval['raw_start'], interval['raw_end']),
        'trace_hash': trace['trace_hash'], 'contains_redaction': interval['contains_redaction'],
        'partial_redaction': interval['partial_redaction']}
    return _seal({'path': path, 'owner_path': owner_path, 'owner_value_hash': owner_hash,
        'span_ref': span_ref, 'source_ref': source_ref, 'visible_start': start, 'visible_end': end,
        'text_hash': item['text_hash'],
        'privacy_relation': 'privacy_projected' if interval['contains_redaction'] else 'identity',
        'source_anchor': anchor}, 'ref_hash')


def _owner_inputs(result):
    # Fixed typed paths, never recursive key-name guessing or reference dedupe.
    yield '/domain', 'domain', '领域与目标', result['domain'], None, []
    for i, claim in enumerate(result['global_claims']):
        yield f'/global_claims/{i}', 'global_claim', '全局声明：' + claim['statement'], claim, claim, [
            (f'/global_claims/{i}/span_refs/{j}', ref) for j, ref in enumerate(claim['span_refs'])]
    for i, business in enumerate(result['businesses']):
        path = f'/businesses/{i}'
        yield path, 'business', '业务：' + business['name'], business, None, _citations(path, business)
        for key, kind, title, label_key in (
            ('steps', 'step', '步骤', 'name'), ('participants', 'participant', '参与职责', 'agent'),
            ('interactions', 'interaction', '信息交接', 'purpose'),
            ('conditions', 'condition', '独立条件', 'statement'),
            ('coordination', 'coordination', '协同安排', 'kind')):
            for j, item in enumerate(business[key]):
                child = path + f'/{key}/{j}'
                evidence = item.get('evidence')
                refs = _citations(child, item) if evidence is None else _citations(child + '/evidence', evidence)
                yield child, kind, business['name'] + ' · ' + title + '：' + item[label_key], item, evidence, refs
    for i, obj in enumerate(result['objects']):
        path = f'/objects/{i}'
        yield path, 'object', '对象：' + obj['name'], obj, None, _citations(path, obj)
        for j, field in enumerate(obj['fields']):
            child = path + f'/fields/{j}'
            yield child, 'field', obj['name'] + ' · 字段：' + field['label'], field, None, _citations(child, field)
    for i, relation in enumerate(result['relationships']):
        path = f'/relationships/{i}'
        yield path, 'relationship', '对象关系：' + relation['name'], relation, None, _citations(path, relation)
    for i, question in enumerate(result['questions']):
        yield f'/questions/{i}', 'question', '待核问题：' + question, question, None, []


def _citations(path, item):
    return [(path + f'/citations/{i}', cite['span_ref']) for i, cite in enumerate(item['citations'])]


def build_source_manifest(design, catalog, source_snapshot, *, privacy_projection_hash):
    """Cold reconstruct all owners/occurrences; no approval or source authority."""
    if type(design) is dict and design.get('schema') == 'report-object-semantic-design-v169':
        from .report_object_source_manifest_v170 import build_source_manifest as build_objects
        return build_objects(design, catalog, source_snapshot,
            privacy_projection_hash=privacy_projection_hash)
    checked = check_design(design)
    _need(_bytes(checked) == _bytes(design), 'full_semantic_manifest_design_changed')
    design = checked
    snapshot, index, catalog = _source(design, catalog, source_snapshot, privacy_projection_hash)
    owners, refs, traces = [], [], {}
    for path, kind, label, value, evidence, sites in _owner_inputs(design['result']):
        if len(refs) + len(sites) > MAX_REFS:
            raise ManifestCapacityExceeded('full_semantic_manifest_reference_capacity')
        owner_hash = digest(value)
        current = [_reference(site, path, owner_hash, ref, catalog, snapshot, index, traces) for site, ref in sites]
        refs.extend(current)
        context = kind in {'domain', 'question'}
        status = 'context' if context else evidence['status'] if evidence is not None else 'not_declared'
        reasons = []
        if context:
            reasons.append('context_not_source_evidence')
        else:
            if not sites: reasons.append('no_citations')
            if status in {'inferred', 'unknown'}: reasons.append(status + '_evidence')
            if type(value) is dict and value.get('inference_reason', '').strip():
                reasons.append('inference_reason_requires_review')
            if sites: reasons.append('source_mapping_required')
            if any(ref['source_anchor']['contains_redaction'] for ref in current):
                reasons.append('privacy_projected_reference')
            if any(ref['source_anchor']['partial_redaction'] for ref in current):
                reasons.append('partial_redaction')
        owners.append({'path': path, 'kind': kind, 'label': label, 'value_hash': owner_hash,
            'ref_hashes': [ref['ref_hash'] for ref in current], 'evidence_status': status,
            'pending_reasons': reasons})
    manifest = _seal({'schema': SCHEMA, 'handoff_id': design['handoff_id'],
        'design_hash': design['design_hash'], 'result_hash': digest(design['result']),
        'source_hash': design['source_hash'], 'catalog_hash': design['catalog_hash'],
        'privacy': {'schema': 'evidence-privacy-v1', 'projection_hash': privacy_projection_hash},
        'owners': owners, 'refs': refs}, 'manifest_hash')
    if len(_bytes(manifest)) > MAX_MANIFEST_BYTES:
        raise ManifestCapacityExceeded('full_semantic_manifest_byte_capacity')
    return manifest


def check_source_manifest(value, design, catalog, source_snapshot, *, privacy_projection_hash):
    """A resealed value is insufficient: compare the entire external cold rebuild."""
    _need(type(value) is dict, 'full_semantic_manifest_object')
    if len(_bytes(value)) > MAX_MANIFEST_BYTES:
        raise ManifestCapacityExceeded('full_semantic_manifest_byte_capacity')
    expected = build_source_manifest(design, catalog, source_snapshot,
        privacy_projection_hash=privacy_projection_hash)
    _need(_bytes(value) == _bytes(expected), 'full_semantic_manifest_does_not_cold_rebuild')
    return expected
