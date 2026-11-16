"""Complete V169 design inventory with separate human identity provenance.

This pure layer verifies supplied frozen source/projection structures, never
real source bytes, the adoption ledger or semantic correctness. Business
identity descriptions keep their own citation occurrences. Acknowledging a
platform record identity is human context and never a source citation or a
claim of business uniqueness. Private notes are not accepted or returned.
"""
import copy

from .contracts import digest
from .report_full_reading_semantics import _safe
from .report_object_configuration_v169 import check_design
from .typed_payload_codec_v131 import canonical_json
from . import report_full_semantic_source_manifest_v1 as legacy


SCHEMA = 'report-object-source-manifest-v170'
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_REFS = 65536
ManifestCapacityExceeded = legacy.ManifestCapacityExceeded
_KEYS = {'schema', 'handoff_id', 'design_hash', 'result_hash', 'source_hash',
    'catalog_hash', 'privacy', 'owners', 'refs', 'manifest_hash'}
_KINDS = {'domain', 'global_claim', 'business', 'step', 'participant', 'interaction',
    'condition', 'coordination', 'object', 'field', 'relationship', 'question',
    'business_identity', 'platform_identity_confirmation'}


def _need(condition, code):
    if not condition:
        raise ValueError('object_manifest_v170_' + code)


def _bytes(value):
    try:
        _safe(value)
        return canonical_json(value).encode('utf-8', 'strict')
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError('object_manifest_v170_unsafe_json') from None


def _inputs(design):
    result = design['result']
    for item in legacy._owner_inputs(result):
        yield item
        path, kind, _, value, _, _ = item
        if kind == 'object':
            identity = value['business_identity']
            child = path + '/business_identity'
            yield child, 'business_identity', value['name'] + ' · 业务标识说明', identity, None, legacy._citations(child, identity)
    # Preserve the actual confirmation-array order, independently of object order.
    for index, item in enumerate(design['object_identity_confirmations']):
        path = '/object_identity_confirmations/' + str(index)
        yield path, 'platform_identity_confirmation', item['object_name'] + ' · 技术记录身份确认', item, None, []


def owner_inputs(design):
    """Return detached six-part typed owners in complete deterministic order."""
    return copy.deepcopy(list(_inputs(check_design(design))))


def owner_metadata(kind, value, evidence, sites, refs):
    """Derive status/reasons only; no private review can upgrade source truth."""
    _need(type(kind) is str and kind in _KINDS and type(sites) is list and type(refs) is list
        and len(sites) == len(refs), 'metadata_shape')
    if kind == 'platform_identity_confirmation':
        _need(not sites and not refs, 'confirmation_has_no_source_refs')
        return {'evidence_status': 'context',
            'pending_reasons': ['human_confirmation_not_source_evidence']}
    context = kind in {'domain', 'question'}
    if kind == 'business_identity':
        _need(type(value) is dict and value.get('status') in {'not_established', 'referenced'}, 'identity_status')
        status = 'unknown' if value['status'] == 'not_established' else 'not_declared'
    else:
        status = 'context' if context else evidence['status'] if evidence is not None else 'not_declared'
    reasons = []
    if context:
        reasons.append('context_not_source_evidence')
    else:
        if not sites:
            reasons.append('no_citations')
        if status in {'inferred', 'unknown'}:
            reasons.append(status + '_evidence')
        if type(value) is dict and value.get('inference_reason', '').strip():
            reasons.append('inference_reason_requires_review')
        if sites:
            reasons.append('source_mapping_required')
        if any(ref['source_anchor']['contains_redaction'] for ref in refs):
            reasons.append('privacy_projected_reference')
        if any(ref['source_anchor']['partial_redaction'] for ref in refs):
            reasons.append('partial_redaction')
        if kind == 'business_identity':
            reasons.append('identity_claim_requires_review')
    return {'evidence_status': status, 'pending_reasons': reasons}


def owner_value(design, path):
    """Resolve only a complete-inventory owner, returning its unchanged value.

    Callers with a supplied manifest must additionally compare this value's
    digest with that cold-verified owner's value_hash. No arbitrary JSON path
    (including private review_origin fields) is accepted as an owner.
    """
    checked = check_design(design)
    _need(type(path) is str, 'owner_path')
    matches = [item for item in _inputs(checked) if item[0] == path]
    _need(len(matches) == 1, 'owner_path')
    expected = matches[0][3]
    value = checked if path.startswith('/object_identity_confirmations/') else checked['result']
    try:
        for key in path[1:].split('/'):
            value = value[int(key)] if type(value) is list else value[key]
    except (KeyError, IndexError, ValueError, TypeError):
        raise ValueError('object_manifest_v170_owner_path') from None
    _need(digest(value) == digest(expected) and _bytes(value) == _bytes(expected), 'owner_value_hash')
    return copy.deepcopy(value)


def build_source_manifest(design, catalog, source_snapshot, *, privacy_projection_hash):
    """Rebuild all citation occurrences; no source/permission approval is given."""
    checked = check_design(design)
    _need(_bytes(checked) == _bytes(design), 'design_changed')
    snapshot, index, catalog = legacy._source(checked, catalog, source_snapshot, privacy_projection_hash)
    owners, refs, traces = [], [], {}
    for path, kind, label, value, evidence, sites in _inputs(checked):
        if len(refs) + len(sites) > MAX_REFS:
            raise ManifestCapacityExceeded('object_manifest_v170_reference_capacity')
        owner_hash = digest(value)
        current = [legacy._reference(site, path, owner_hash, ref, catalog, snapshot, index, traces)
            for site, ref in sites]
        refs.extend(current)
        owners.append({'path': path, 'kind': kind, 'label': label, 'value_hash': owner_hash,
            'ref_hashes': [ref['ref_hash'] for ref in current],
            **owner_metadata(kind, value, evidence, sites, current)})
    value = {'schema': SCHEMA, 'handoff_id': checked['handoff_id'], 'design_hash': checked['design_hash'],
        'result_hash': digest(checked['result']), 'source_hash': checked['source_hash'],
        'catalog_hash': checked['catalog_hash'],
        'privacy': {'schema': 'evidence-privacy-v1', 'projection_hash': privacy_projection_hash},
        'owners': owners, 'refs': refs}
    value['manifest_hash'] = digest(value)
    if len(_bytes(value)) > MAX_MANIFEST_BYTES:
        raise ManifestCapacityExceeded('object_manifest_v170_byte_capacity')
    return value


def check_source_manifest(value, design, catalog, source_snapshot, *, privacy_projection_hash):
    """Self-resealing cannot replace a complete independent cold reconstruction."""
    _need(type(value) is dict and set(value) == _KEYS and value.get('schema') == SCHEMA, 'manifest_family_or_keys')
    if len(_bytes(value)) > MAX_MANIFEST_BYTES:
        raise ManifestCapacityExceeded('object_manifest_v170_byte_capacity')
    expected = build_source_manifest(design, catalog, source_snapshot,
        privacy_projection_hash=privacy_projection_hash)
    _need(_bytes(value) == _bytes(expected), 'does_not_cold_rebuild')
    return expected
