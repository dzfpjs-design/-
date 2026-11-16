"""Pure, version-bound adoption of a whole design's workspace data structures.

The caller authenticates the active design, cold-rebuilt source manifest,
published position proof, current formal source and explicit adoption consent.
This module checks supplied structures and their complete cross references;
self-consistent hashes cannot establish those external facts. Adoption is a
workspace schema decision, never source-semantic approval or execution authority.
No source text, private review note, database or provider is read here.
"""
import copy
import json
import math
import re
from uuid import UUID

from .contracts import digest
from .report_full_semantic_configuration_v1 import check_design
from .report_full_semantic_source_manifest_v1 import _owner_inputs, MAX_REFS
from .report_full_semantic_region_mapping_v1 import _box


SCHEMA = 'report-full-semantic-schema-adoption-v1'
OBJECT_SCHEMA = 'report-object-schema-adoption-v170'
MAX_BYTES = 24 * 1024 * 1024
_HASH = re.compile(r'^[a-f0-9]{64}$')
_KINDS = {'文本', '整数', '数值', '是否', '日期'}
_CARDINALITIES = {'many', 'one_per_source', 'one_per_target'}
_META = {'page', 'unit_id', 'revision_id', 'revision_hash', 'revision_kind', 'image_sha256'}
_SOURCE = {'owner_path', 'owner_value_hash', 'ref_hashes', 'declared_evidence_status', 'pending_reasons'}
_OUTPUT = {'schema', 'design_hash', 'manifest_hash', 'proof_hash', 'selection',
    'relationship_policies', 'semantic_mode', 'owner_paths', 'pending_evidence_owner_paths',
    'objects', 'relationships', 'object_sources', 'relationship_sources', 'plan_hash'}


def _need(ok, code):
    if not ok:
        raise ValueError('full_semantic_schema_' + code)


def _keys(value, expected, code):
    _need(type(value) is dict and set(value) == set(expected), code)


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
            _need(item is None or type(item) in (bool, int)
                or type(item) is float and math.isfinite(item), 'json_type')
    try:
        safe(value)
        data = json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
        _need(len(data) <= MAX_BYTES, 'capacity')
        return data
    except (UnicodeError, RecursionError, TypeError, OverflowError):
        raise ValueError('full_semantic_schema_unsafe_json') from None


def _hash(value):
    _need(type(value) is str and bool(_HASH.fullmatch(value)), 'hash')


def _sealed(value, key):
    _hash(value[key])
    _need(value[key] == digest({k: v for k, v in value.items() if k != key}), key)


def _uuid(value):
    _need(type(value) is str, 'uuid')
    try:
        _need(str(UUID(value)) == value, 'uuid')
    except (ValueError, AttributeError):
        raise ValueError('full_semantic_schema_uuid') from None


def _int(value, minimum=0, maximum=None):
    _need(type(value) is int and value >= minimum
        and (maximum is None or value <= maximum), 'integer')


def _material(value):
    _keys(value, {'id', 'version', 'hash'}, 'material_shape')
    _uuid(value['id']); _int(value['version'], 1); _hash(value['hash'])


def _metadata(value):
    _int(value['page'], 1, 500)
    for key in ('unit_id', 'revision_id'):
        _uuid(value[key])
    for key in ('revision_hash', 'image_sha256'):
        _hash(value[key])
    _need(value['revision_kind'] in ('raw', 'edited', 'confirmed'), 'revision_kind')


def _reference(ref):
    _keys(ref, {'path', 'owner_path', 'owner_value_hash', 'span_ref', 'source_ref',
        'visible_start', 'visible_end', 'text_hash', 'privacy_relation', 'source_anchor', 'ref_hash'}, 'ref_shape')
    _sealed(ref, 'ref_hash')
    for key in ('owner_value_hash', 'text_hash'):
        _hash(ref[key])
    _need(type(ref['source_ref']) is str and bool(re.fullmatch(r'[wo]:p[1-9]\d*', ref['source_ref']))
        and type(ref['span_ref']) is str and bool(re.fullmatch(re.escape(ref['source_ref']) + r'/s[1-9]\d*', ref['span_ref'])), 'source_ref')
    _int(ref['visible_start']); _int(ref['visible_end'], ref['visible_start'] + 1)
    anchor = ref['source_anchor']
    _keys(anchor, {'material_ref', 'evidence_id', 'raw_text_hash', 'raw_start', 'raw_end',
        'raw_location', 'trace_hash', 'contains_redaction', 'partial_redaction'}, 'anchor_shape')
    _material(anchor['material_ref']); _uuid(anchor['evidence_id'])
    for key in ('raw_text_hash', 'trace_hash'):
        _hash(anchor[key])
    _int(anchor['raw_start']); _int(anchor['raw_end'], anchor['raw_start'] + 1)
    _need(type(anchor['contains_redaction']) is bool and type(anchor['partial_redaction']) is bool
        and (not anchor['partial_redaction'] or anchor['contains_redaction']), 'privacy_flags')
    _need(ref['privacy_relation'] == ('privacy_projected' if anchor['contains_redaction'] else 'identity'), 'privacy_relation')
    location = anchor['raw_location']
    _keys(location, {'kind', 'location', 'regions'}, 'raw_location')
    _need(location['kind'] in ('paragraph', 'table_paragraph', 'textbox_paragraph', 'ocr_page')
        and type(location['location']) is dict and type(location['regions']) is list, 'raw_location')
    if location['kind'] == 'ocr_page':
        raw = location['location']
        _keys(raw, _META | {'run_id'}, 'ocr_location'); _metadata(raw); _uuid(raw['run_id'])
        _need(ref['source_ref'] == 'o:p' + str(raw['page']), 'ocr_source_ref')
    else:
        raw = location['location']
        allowed = {'view', 'body_index', 'paragraph_index', 'table_index', 'row_index',
            'cell_index', 'table_depth', 'compatibility_branch'}
        _need({'view', 'body_index', 'paragraph_index'} <= set(raw) <= allowed
            and raw['view'] == 'accepted_final_v1' and ref['source_ref'].startswith('w:')
            and not location['regions'], 'word_location')
        for key in set(raw) - {'view', 'compatibility_branch'}:
            _int(raw[key], 1)
        if 'compatibility_branch' in raw:
            _need(type(raw['compatibility_branch']) is str, 'compatibility_branch')
    for region in location['regions']:
        _keys(region, {'region_id', 'ordinal', 'box', 'text_hash', 'page_start', 'page_end', 'start', 'end'}, 'raw_region')
        _need(type(region['region_id']) is str and bool(region['region_id']), 'region_id')
        _int(region['ordinal'], 1); _box(region['box']); _hash(region['text_hash'])
        _int(region['page_start']); _int(region['page_end'], region['page_start'] + 1)
        _int(region['start']); _int(region['end'], region['start'] + 1)
        _need(region['end'] <= region['page_end'] - region['page_start'], 'raw_region_range')


def _manifest(design, manifest):
    _keys(manifest, {'schema', 'handoff_id', 'design_hash', 'result_hash', 'source_hash',
        'catalog_hash', 'privacy', 'owners', 'refs', 'manifest_hash'}, 'manifest_shape')
    _sealed(manifest, 'manifest_hash')
    objects = design['schema'] == 'report-object-semantic-design-v169'
    expected_schema = 'report-object-source-manifest-v170' if objects else 'report-full-semantic-source-manifest-v1'
    _need(manifest['schema'] == expected_schema
        and all(manifest[key] == design[key] for key in ('handoff_id', 'design_hash', 'source_hash', 'catalog_hash'))
        and manifest['result_hash'] == digest(design['result']), 'manifest_design')
    _keys(manifest['privacy'], {'schema', 'projection_hash'}, 'privacy_shape')
    _need(manifest['privacy']['schema'] == 'evidence-privacy-v1', 'privacy_schema')
    _hash(manifest['privacy']['projection_hash'])
    _need(type(manifest['owners']) is list and type(manifest['refs']) is list
        and len(manifest['refs']) <= MAX_REFS, 'manifest_collections')
    if objects:
        from .report_object_source_manifest_v170 import owner_inputs, owner_metadata
        expected = owner_inputs(design)
    else:
        expected = list(_owner_inputs(design['result']))
    _need(len(expected) == len(manifest['owners']), 'owner_inventory')
    cursor = 0; seen_refs = set(); owners = {}
    for actual, (path, kind, label, value, evidence, sites) in zip(manifest['owners'], expected):
        _keys(actual, {'path', 'kind', 'label', 'value_hash', 'ref_hashes', 'evidence_status', 'pending_reasons'}, 'owner_shape')
        context = kind in {'domain', 'question'}
        status = 'context' if context else evidence['status'] if evidence is not None else 'not_declared'
        refs = manifest['refs'][cursor:cursor + len(sites)]; cursor += len(sites)
        _need(len(refs) == len(sites), 'ref_inventory')
        for ref, (site, span_ref) in zip(refs, sites):
            _reference(ref)
            _need(ref['path'] == site and ref['owner_path'] == path
                and ref['owner_value_hash'] == digest(value) and ref['span_ref'] == span_ref
                and ref['ref_hash'] not in seen_refs, 'ref_owner')
            seen_refs.add(ref['ref_hash'])
        reasons = []
        if context:
            reasons.append('context_not_source_evidence')
        else:
            if not sites: reasons.append('no_citations')
            if status in {'inferred', 'unknown'}: reasons.append(status + '_evidence')
            if type(value) is dict and value.get('inference_reason', '').strip():
                reasons.append('inference_reason_requires_review')
            if sites: reasons.append('source_mapping_required')
            if any(ref['source_anchor']['contains_redaction'] for ref in refs): reasons.append('privacy_projected_reference')
            if any(ref['source_anchor']['partial_redaction'] for ref in refs): reasons.append('partial_redaction')
        metadata = owner_metadata(kind, value, evidence, sites, refs) if objects else {
            'evidence_status': status, 'pending_reasons': reasons}
        rebuilt = {'path': path, 'kind': kind, 'label': label, 'value_hash': digest(value),
            'ref_hashes': [ref['ref_hash'] for ref in refs], **metadata}
        _need(_json(actual) == _json(rebuilt), 'owner_changed')
        _need(path not in owners, 'duplicate_owner'); owners[path] = actual
    _need(cursor == len(manifest['refs']), 'extra_refs')
    return owners


def _proof_selection(value):
    _keys(value, {'schema', 'anchor', 'copy_ref', 'pages', 'selection_hash'}, 'proof_selection_shape')
    _sealed(value, 'selection_hash')
    _need(value['schema'] == 'report-full-semantic-region-source-selection-v1', 'proof_selection_schema')
    _material(value['copy_ref']); anchor = value['anchor']
    _keys(anchor, {'source_ref', 'source_evidence_id', 'source_name', 'copy_material_id',
        'copy_sha256', 'copy_evidence_id', 'provenance_hash', 'run_id', 'generation'}, 'copy_anchor')
    _material(anchor['source_ref'])
    for key in ('source_evidence_id', 'copy_material_id', 'copy_evidence_id', 'run_id'): _uuid(anchor[key])
    for key in ('copy_sha256', 'provenance_hash'): _hash(anchor[key])
    _int(anchor['generation'], 1)
    _need(type(anchor['source_name']) is str and 1 <= len(anchor['source_name']) <= 250
        and anchor['copy_material_id'] == value['copy_ref']['id']
        and anchor['copy_sha256'] == value['copy_ref']['hash'], 'copy_anchor_binding')
    _need(type(value['pages']) is list and 1 <= len(value['pages']) <= 500, 'proof_pages')
    ids = set(); revisions = set()
    for index, page in enumerate(value['pages'], 1):
        _keys(page, _META | {'unit_version'}, 'proof_page'); _metadata(page); _int(page['unit_version'], 1)
        _need(page['page'] == index and page['unit_id'] not in ids
            and page['revision_id'] not in revisions, 'proof_page_order')
        ids.add(page['unit_id']); revisions.add(page['revision_id'])


def _mapping(mapping, ref, selection):
    _keys(mapping, {'schema', 'ref_hash', 'ref_path', 'owner_value_hash', 'source_anchor_hash',
        'copy_material_id', 'privacy_relation', 'visible_quote_hash', 'raw_quote_hash', 'segments', 'mapping_hash'}, 'mapping_shape')
    _sealed(mapping, 'mapping_hash')
    _need(mapping['schema'] == 'report-full-semantic-region-mapping-v1'
        and mapping['ref_hash'] == ref['ref_hash'] and mapping['ref_path'] == ref['path']
        and mapping['owner_value_hash'] == ref['owner_value_hash']
        and mapping['source_anchor_hash'] == digest(ref['source_anchor'])
        and mapping['copy_material_id'] == selection['copy_ref']['id']
        and mapping['visible_quote_hash'] == ref['text_hash'], 'mapping_reference')
    _hash(mapping['raw_quote_hash'])
    _need(type(mapping['segments']) is list and 1 <= len(mapping['segments']) <= len(selection['pages']), 'mapping_segments')
    last = None; contains = ref['source_anchor']['contains_redaction']
    for index, segment in enumerate(mapping['segments']):
        _keys(segment, _META | {'start', 'end', 'trace_hash', 'visible_quote_hash', 'contains_redaction', 'partial_redaction', 'spans'}, 'mapping_segment')
        _metadata(segment); _int(segment['start']); _int(segment['end'], segment['start'])
        _need(segment['page'] <= len(selection['pages'])
            and (last is None or segment['page'] == last + 1), 'mapping_page_order')
        page = selection['pages'][segment['page'] - 1]
        _need(all(segment[key] == page[key] for key in _META), 'mapping_page_version'); last = segment['page']
        for key in ('trace_hash', 'visible_quote_hash'): _hash(segment[key])
        _need(type(segment['contains_redaction']) is bool and type(segment['partial_redaction']) is bool
            and (not segment['partial_redaction'] or segment['contains_redaction']), 'mapping_privacy')
        contains = contains or segment['contains_redaction']
        _need(type(segment['spans']) is list and len(segment['spans']) <= 500, 'mapping_spans')
        _need(segment['start'] < segment['end'] or 0 < index < len(mapping['segments']) - 1, 'mapping_empty_edge')
        length = 0; regions = set()
        for span in segment['spans']:
            _keys(span, _META | {'region_id', 'region_text_hash', 'box', 'start', 'end'}, 'mapping_span')
            _need(all(span[key] == segment[key] for key in _META), 'mapping_span_version')
            _need(type(span['region_id']) is str and bool(span['region_id']) and span['region_id'] not in regions, 'mapping_region')
            regions.add(span['region_id']); _hash(span['region_text_hash']); _box(span['box'])
            _int(span['start']); _int(span['end'], span['start'] + 1); length += span['end'] - span['start']
        _need(length == segment['end'] - segment['start'], 'mapping_span_coverage')
    _need(mapping['privacy_relation'] == ('privacy_projected' if contains else 'identity'), 'mapping_privacy_relation')
    if not contains:
        _need(mapping['raw_quote_hash'] == mapping['visible_quote_hash'], 'mapping_identity_hash')


def _proof(design, manifest, proof):
    _keys(proof, {'schema', 'handoff_id', 'manifest_hash', 'configuration', 'session_id', 'selection',
        'confirmation_id', 'mappings', 'pending_semantic_owner_paths', 'source_status', 'can_confirm', 'can_publish', 'proof_hash'}, 'proof_shape')
    _sealed(proof, 'proof_hash')
    schema = ('report-object-region-proof-v170' if manifest['schema'] == 'report-object-source-manifest-v170'
        else 'report-full-semantic-region-proof-v1')
    _need(proof['schema'] == schema
        and proof['handoff_id'] == design['handoff_id'] and proof['manifest_hash'] == manifest['manifest_hash']
        and proof['source_status'] == 'citation_positions_confirmed_semantics_pending'
        and proof['can_confirm'] is False and proof['can_publish'] is False
        and proof['pending_semantic_owner_paths'] == [owner['path'] for owner in manifest['owners']], 'proof_binding')
    _keys(proof['configuration'], {'revision', 'hash'}, 'proof_configuration')
    _int(proof['configuration']['revision'], 1); _hash(proof['configuration']['hash'])
    _uuid(proof['session_id']); _uuid(proof['confirmation_id']); _proof_selection(proof['selection'])
    _need(type(proof['mappings']) is list and len(proof['mappings']) == len(manifest['refs'])
        and bool(manifest['refs']), 'proof_inventory')
    entries = set()
    for item, ref in zip(proof['mappings'], manifest['refs']):
        _keys(item, {'ref_hash', 'entry_id', 'entry_version', 'mapping'}, 'proof_mapping')
        _uuid(item['entry_id']); _int(item['entry_version'], 1, 100)
        _need(item['ref_hash'] == ref['ref_hash'] and item['entry_id'] not in entries, 'proof_ref_order')
        entries.add(item['entry_id']); _mapping(item['mapping'], ref, proof['selection'])


def _source(owner):
    return {'owner_path': owner['path'], 'owner_value_hash': owner['value_hash'],
        'ref_hashes': copy.deepcopy(owner['ref_hashes']), 'declared_evidence_status': owner['evidence_status'],
        'pending_reasons': copy.deepcopy(owner['pending_reasons'])}


def _build(design, manifest, proof, selection, relationship_policies):
    for value in (design, manifest, proof, selection, relationship_policies): _json(value)
    design = check_design(design); owners = _manifest(design, manifest); _proof(design, manifest, proof)
    _keys(selection, {'object_names', 'relationship_names'}, 'selection_shape')
    collections = {'object_names': design['result']['objects'], 'relationship_names': design['result']['relationships']}
    for key, items in collections.items():
        selected = selection[key]; cap = 40 if key == 'object_names' else 30
        _need(type(selected) is list and len(selected) <= cap
            and all(type(name) is str and name.strip() and name == name.strip() for name in selected)
            and len(selected) == len(set(selected)), 'selection_names')
        _need(selected == [item['name'] for item in items if item['name'] in selected], 'selection_order_or_unknown')
    _need(bool(selection['object_names']), 'objects_required')
    _keys(relationship_policies, set(selection['relationship_names']), 'relationship_policies')
    new_objects = design['schema'] == 'report-object-semantic-design-v169'
    confirmations = {item['object_name']: (index, item) for index, item in
        enumerate(design['object_identity_confirmations'])} if new_objects else {}
    objects = []; relationships = []; object_sources = {}; relationship_sources = {}; paths = []
    for index, obj in enumerate(design['result']['objects']):
        if obj['name'] not in selection['object_names']: continue
        path = f'/objects/{index}'; paths.append(path); fields = []; sources = []
        _need(1 <= len(obj['fields']) <= 40, 'field_capacity')
        for field_index, field in enumerate(obj['fields']):
            _need(field['kind'] in _KINDS and type(field['required']) is bool, 'field_type')
            child = path + f'/fields/{field_index}'; paths.append(child); sources.append(_source(owners[child]))
            fields.append({key: copy.deepcopy(field[key]) for key in ('label', 'kind', 'required')})
            fields[-1]['unit'] = copy.deepcopy(field['unit']) if new_objects else field['unit'] or None
        item = {'name': obj['name'], 'fields': fields}
        if new_objects:
            from .object_type_contract_v170 import check_object_type
            ack_index, ack = confirmations[obj['name']]
            item.update(schema='report-object-type-v170', business_identity=copy.deepcopy(obj['business_identity']),
                platform_identity_confirmation=copy.deepcopy(ack))
            item = check_object_type(item)
        objects.append(item)
        object_sources[obj['name']] = {**_source(owners[path]), 'fields': sources}
        if new_objects:
            identity_path, ack_path = path + '/business_identity', f'/object_identity_confirmations/{ack_index}'
            paths.extend((identity_path, ack_path))
            object_sources[obj['name']].update(business_identity=_source(owners[identity_path]),
                platform_identity_confirmation=_source(owners[ack_path]))
    for index, relation in enumerate(design['result']['relationships']):
        if relation['name'] not in selection['relationship_names']: continue
        policy = relationship_policies[relation['name']]
        _keys(policy, {'cardinality', 'allow_self'}, 'relationship_policy')
        _need(policy['cardinality'] in _CARDINALITIES and type(policy['allow_self']) is bool, 'relationship_policy_value')
        _need(relation['cardinality'] == 'unspecified' or policy['cardinality'] == relation['cardinality'], 'known_cardinality_changed')
        _need(relation['source_type'] in selection['object_names']
            and relation['target_type'] in selection['object_names'], 'relationship_endpoints')
        path = f'/relationships/{index}'; paths.append(path)
        relationships.append({**{key: relation[key] for key in ('name', 'description', 'source_type', 'target_type')}, **copy.deepcopy(policy)})
        relationship_sources[relation['name']] = {**_source(owners[path]),
            'declared_cardinality': relation['cardinality'],
            'cardinality_origin': 'workspace_policy' if relation['cardinality'] == 'unspecified' else 'source_design',
            'allow_self_origin': 'workspace_policy'}
    pending = [path for path in paths if not owners[path]['ref_hashes']
        or owners[path]['evidence_status'] in {'unknown', 'inferred'}
        or 'inference_reason_requires_review' in owners[path]['pending_reasons']
        or new_objects and 'identity_claim_requires_review' in owners[path]['pending_reasons']]
    result = {'schema': OBJECT_SCHEMA if new_objects else SCHEMA, 'design_hash': design['design_hash'], 'manifest_hash': manifest['manifest_hash'],
        'proof_hash': proof['proof_hash'], 'selection': copy.deepcopy(selection),
        'relationship_policies': copy.deepcopy(relationship_policies), 'semantic_mode': 'workspace_schema_design',
        'owner_paths': paths, 'pending_evidence_owner_paths': pending, 'objects': objects, 'relationships': relationships,
        'object_sources': object_sources, 'relationship_sources': relationship_sources}
    _json(result)
    return {**result, 'plan_hash': digest(result)}


def build_schema_adoption(design, manifest, proof, selection, *, relationship_policies):
    """Return an isolated plan; supplied ledger authority remains the caller's job."""
    try:
        return _build(design, manifest, proof, selection, relationship_policies)
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        raise ValueError('full_semantic_schema_invalid_input') from None


def check_schema_adoption(value, design, manifest, proof, selection, *, relationship_policies):
    """A present plan must exactly cold-rebuild; never repair or accept new origins."""
    _keys(value, _OUTPUT, 'plan_shape')
    expected = build_schema_adoption(design, manifest, proof, selection, relationship_policies=relationship_policies)
    _need(_json(value) == _json(expected), 'does_not_cold_rebuild')
    return expected
