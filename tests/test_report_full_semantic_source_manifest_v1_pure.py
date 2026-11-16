"""Root-run pure synthetic manifests; no DB/files/network/source approval.

All source values below are hand-built public strings. The source contract and
design validators are real; a self-consistent synthetic snapshot cannot prove
source bytes, OCR quality, an issued record, current head, or formal page review.
"""
import copy
import hashlib
import json
import re
from uuid import UUID

import pytest

from generic_agents.contracts import digest
from generic_agents import report_full_reading_source as full
from generic_agents import report_full_semantic_configuration_v1 as semantic
from generic_agents import report_full_semantic_source_manifest_v1 as manifest
from generic_agents.report_evidence_plan import build_evidence_source


def identity(n):
    return str(UUID('10000000-0000-4000-8000-' + f'{n:012x}'))


def seal(value, field):
    return {**value, field: digest(value)}


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def legacy_scrub(text):
    # Independent literal evidence-privacy-v1, not a patched trace or pipeline.
    text = re.sub(r'(?i)\b(?:sk-[a-z0-9_-]{10,}|bearer\s+[a-z0-9._-]{10,})', '[凭证已移除]', text)
    text = re.sub(r'(?i)(?:api[_ -]?key|password|secret|token|密码|密钥)\s*[:=：]\s*[^\s,，;；]+', '[凭证已移除]', text)
    if '@' in text:
        text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[邮箱已移除]', text)
    return re.sub(r'(?<!\d)(?:1[3-9]\d{9}|\d{17}[0-9Xx])(?!\d)', '[个人号码已移除]', text)


def legacy_tree(value):
    if type(value) is dict:
        return {legacy_scrub(str(k)): legacy_tree(v) for k, v in value.items()}
    if type(value) is list:
        return [legacy_tree(v) for v in value]
    return legacy_scrub(value) if type(value) is str else value


def source(word='甲对象关联乙对象。长度（米）。🙂 原文。', pages=None):
    pages = [['采集', '记录🙂'], ['另一页']] if pages is None else pages
    original_ref = {'id': identity(1), 'version': 1, 'hash': digest('synthetic-word-bytes')}
    copy_ref = {'id': identity(2), 'version': 3, 'hash': digest('synthetic-pdf-bytes')}
    blocks = [{'id': 'p1', 'text': word, 'kind': 'paragraph',
        'location': {'view': 'accepted_final_v1', 'body_index': 1, 'paragraph_index': 1}}]
    reading = {'schema': 'report-body-draft-source-v1', 'source_sha256': original_ref['hash'],
        'normalized_sha256': digest(word), 'revision_policy': 'accepted_final_v1',
        'hidden_content_policy': 'reveal_retained_text_v1', 'blocks': blocks,
        'coverage': {'scope': 'accepted_view_main_body_text', 'status': 'body_text_only', 'gaps': [],
            'counts': {'source_bytes': 23, 'paragraphs_read': 1, 'characters': len(word),
                'utf8_text_bytes': len(word.encode('utf-8')), 'paragraphs_total': 1, 'paragraphs_empty': 0}},
        'warnings': [], 'full_report_confirmed': False, 'can_apply': False}
    receipt = {'source_sha256': original_ref['hash'], 'pdf_sha256': copy_ref['hash'],
        'engine_id': 'libreoffice-docx-final-v1', 'manifest_hash': 'c' * 64,
        'revision_policy': 'accepted_final_v1', 'page_count': len(pages), 'warnings': []}
    provenance = seal({'contract_version': 1, 'copy_material_id': identity(2), 'workspace_id': identity(3),
        'copy_evidence_id': identity(4), 'copy_name': 'synthetic.pdf', 'source_material_id': identity(1),
        'source_sha256': original_ref['hash'], 'source_material_version': 1, 'source_name': 'synthetic.docx',
        'source_evidence_id': identity(5), 'request_id': identity(6), 'job_id': identity(7),
        'request_body_hash': 'd' * 64, 'revision_policy': 'accepted_final_v1',
        'engine_id': receipt['engine_id'], 'engine_manifest_hash': 'c' * 64,
        'pdf_sha256': copy_ref['hash'], 'receipt': receipt}, 'integrity_hash')
    coverage = seal({'contract_version': 2, 'source_sha256': copy_ref['hash'], 'status': 'needs_ocr',
        'unit_count': len(pages), 'units': [{'index': n, 'kind': 'pdf_page', 'page': n, 'body_index': None,
            'has_native_text': True, 'has_image': False, 'has_annotations': False} for n in range(1, len(pages) + 1)],
        'gaps': [], 'reason': None, 'profile': {'kind': 'document_copy_full_pages_v1',
            'provenance_hash': provenance['integrity_hash']}, 'force_all_pages': True}, 'hash')
    choices, entries = [], []
    for n, texts in enumerate(pages, 1):
        kind = 'raw' if n == 1 else 'confirmed'
        chosen = {'page': n, 'unit_id': identity(100 + n), 'unit_version': 1,
            'revision_id': identity(1000 + n), 'revision_hash': digest(['revision', n]), 'revision_kind': kind}
        choices.append(chosen)
        entries.append(seal({'page': n, 'unit_id': chosen['unit_id'], 'unit_version': 1,
            'revision': {'id': chosen['revision_id'], 'number': 1, 'kind': kind, 'hash': chosen['revision_hash']},
            'image': {'evidence_id': identity(2000 + n), 'sha256': digest(['image', n]), 'width': 96, 'height': 90},
            'recognition_engine_hash': 'e' * 64,
            'regions': [{'id': 'r' + str(i), 'text': text, 'box': ['1.0', str(2 + i * 12), '50', '10'],
                'score': None, 'source_region_id': None if kind == 'raw' else 'r' + str(i)}
                for i, text in enumerate(texts, 1)]}, 'page_hash'))
    selection = {'source_mode': full.MODE, 'original_ref': original_ref, 'copy_ref': copy_ref,
        'provenance_hash': provenance['integrity_hash'], 'run_id': identity(8), 'generation': 2,
        'run_version': 5, 'pages': choices}
    return seal({'schema': full.SOURCE, 'source_mode': full.MODE, 'workspace_id': identity(3), 'selection': selection,
        'original': {'ref': original_ref, 'name': 'synthetic.docx', 'evidence_id': identity(5),
            'evidence_sha256': original_ref['hash'], 'evidence_size': 23, 'reading': reading},
        'copy': {'ref': copy_ref, 'name': 'synthetic.pdf', 'evidence_id': identity(4), 'provenance': provenance,
            'run': {'id': identity(8), 'generation': 2, 'version': 5, 'status': 'review', 'coverage': coverage,
                'renderer': {'manifest_hash': 'f' * 64, 'summary_hash': 'a' * 64}}},
        'pages': entries, 'coverage': full._coverage(reading, entries)}, 'snapshot_hash')


def candidate(source_hash, catalog_hash):
    w, o = [{'span_ref': 'w:p1/s1'}], [{'span_ref': 'o:p1/s1'}]
    evidence = lambda status, cites: {'status': status, 'citations': copy.deepcopy(cites),
        'explanation': '' if status == 'explicit' else '人工合成未知或推断，待核对'}
    business = {'operation': 'upsert', 'original_name': '', 'name': '合成业务', 'trigger': '原触发',
        'output': '原产出', 'citations': copy.deepcopy(w), 'inference_reason': '',
        'steps': [{'name': name, 'purpose': '原目的', 'agent': '人工确认', 'role': '核对职责',
            'output': '原产出🙂', 'next_index': target, 'else_index': None, 'branch_condition': '',
            'citations': copy.deepcopy(o if i == 0 else w), 'inference_reason': ''}
            for i, (name, target) in enumerate([('甲步', 1), ('乙步', 0)])],
        'participants': [{'agent': name, 'responsibility': '原职责',
            'evidence': evidence(status, w if status == 'explicit' else [])}
            for name, status in [('参与甲', 'explicit'), ('参与乙', 'unknown')]],
        'interactions': [{'from_participant_index': 0, 'to_participant_index': 1, 'purpose': '原交接',
            'condition': '', 'step_indices': [0], 'evidence': evidence('inferred', o)}],
        'conditions': [{'kind': 'stop', 'statement': '原停止条件', 'step_indices': [1],
            'evidence': evidence('unknown', [])}, {'kind': 'trigger', 'statement': '原触发条件',
            'step_indices': [0], 'evidence': evidence('explicit', w)}],
        'coordination': [{'kind': 'parallel', 'step_indices': [1, 0], 'start_rule': '原开始',
            'wait_rule': '原等待', 'join_rule': '原汇合', 'evidence': evidence('explicit', o)}]}
    return {'schema': 'report-evidence-candidate-wire-v5', 'source_hash': source_hash, 'catalog_hash': catalog_hash,
        'domain': '原领域🙂\n', 'global_claims': [
            {'kind': '治理', 'statement': '原独立约束', 'span_refs': ['w:p1/s1', 'o:p1/s1'],
                'status': 'explicit', 'explanation': ''},
            {'kind': '冲突', 'statement': '原未知内容不删除', 'span_refs': [], 'status': 'unknown', 'explanation': '仍待核'}],
        'objects': [{'name': name, 'fields': [{'label': '名称', 'kind': '文本', 'required': True,
            'unit': None, 'citations': copy.deepcopy(w), 'inference_reason': ''}],
            'citations': copy.deepcopy(w), 'inference_reason': ''} for name in ('甲对象', '乙对象')],
        'relationships': [{'name': '关联', 'description': '原对象关系', 'source_type': '甲对象', 'target_type': '乙对象',
            'cardinality': 'unspecified', 'citations': copy.deepcopy(w), 'inference_reason': ''}],
        'businesses': [business], 'questions': ['原具体待核问题🙂\n']}


def scene(snapshot=None, *, change=None):
    snapshot = source() if snapshot is None else snapshot
    projection, _ = full.project_full_source(snapshot)
    projected = legacy_tree(projection)
    catalog = build_evidence_source(digest(snapshot), [[b[0], b[3]] for b in projected['blocks']])
    result = candidate(digest(snapshot), catalog['descriptor']['catalog_hash'])
    if change: change(result)
    design = semantic.build_design(result, handoff_id=identity(50), report_id=identity(51), report_revision=3,
        review_receipt_hash='c' * 64, manual_value_hash='d' * 64, model_result_hash='e' * 64,
        source_hash=result['source_hash'], catalog_hash=result['catalog_hash'], context_hash='f' * 64)
    return design, catalog, snapshot, digest(projected)


def build(values):
    design, catalog, snapshot, projection_hash = values
    return manifest.build_source_manifest(design, catalog, snapshot, privacy_projection_hash=projection_hash)


def check(value, values):
    design, catalog, snapshot, projection_hash = values
    return manifest.check_source_manifest(value, design, catalog, snapshot, privacy_projection_hash=projection_hash)


def test_every_family_and_occurrence_is_preserved_in_fixed_order_without_raw_text_or_note():
    inputs = scene(); before = copy.deepcopy(inputs); value = build(inputs)
    assert set(value) == {'schema', 'handoff_id', 'design_hash', 'result_hash', 'source_hash', 'catalog_hash',
        'privacy', 'owners', 'refs', 'manifest_hash'}
    assert [o['path'] for o in value['owners']] == ['/domain', '/global_claims/0', '/global_claims/1',
        '/businesses/0', '/businesses/0/steps/0', '/businesses/0/steps/1', '/businesses/0/participants/0',
        '/businesses/0/participants/1', '/businesses/0/interactions/0', '/businesses/0/conditions/0',
        '/businesses/0/conditions/1', '/businesses/0/coordination/0', '/objects/0', '/objects/0/fields/0', '/objects/1', '/objects/1/fields/0',
        '/relationships/0', '/questions/0']
    paths = [r['path'] for r in value['refs']]
    assert paths == ['/global_claims/0/span_refs/0', '/global_claims/0/span_refs/1', '/businesses/0/citations/0',
        '/businesses/0/steps/0/citations/0', '/businesses/0/steps/1/citations/0',
        '/businesses/0/participants/0/evidence/citations/0', '/businesses/0/interactions/0/evidence/citations/0',
        '/businesses/0/conditions/1/evidence/citations/0', '/businesses/0/coordination/0/evidence/citations/0', '/objects/0/citations/0', '/objects/0/fields/0/citations/0',
        '/objects/1/citations/0', '/objects/1/fields/0/citations/0', '/relationships/0/citations/0']
    assert len({r['ref_hash'] for r in value['refs']}) == len(paths)
    assert len({r['span_ref'] for r in value['refs']}) == 2  # Identical refs at different paths remain separate.
    for owner in value['owners']:
        assert set(owner) == {'path', 'kind', 'label', 'value_hash', 'ref_hashes', 'evidence_status', 'pending_reasons'}
        assert owner['ref_hashes'] == [r['ref_hash'] for r in value['refs'] if r['owner_path'] == owner['path']]
    assert not any(key in json.dumps(value) for key in ('"quote"', '"text"', '"raw_text"', '"note"', 'can_apply'))
    assert check(value, inputs) == build(inputs) == value and inputs == before
    value['owners'][0]['label'] = 'only returned copy'
    assert inputs == before


def test_context_unknown_uncited_and_inferred_owners_never_disappear_or_become_source_facts():
    value = build(scene()); owners = {o['path']: o for o in value['owners']}
    for path in ('/domain', '/questions/0'):
        assert owners[path]['evidence_status'] == 'context'
        assert owners[path]['pending_reasons'] == ['context_not_source_evidence']
    for path in ('/global_claims/1', '/businesses/0/participants/1', '/businesses/0/conditions/0'):
        assert owners[path]['evidence_status'] == 'unknown'
        assert owners[path]['ref_hashes'] == []
        assert owners[path]['pending_reasons'] == ['no_citations', 'unknown_evidence']
    assert owners['/businesses/0/interactions/0']['evidence_status'] == 'inferred'
    assert owners['/businesses/0/interactions/0']['pending_reasons'] == ['inferred_evidence', 'source_mapping_required']
    assert owners['/objects/0']['evidence_status'] == 'not_declared'


def test_source_anchors_keep_original_versions_and_exact_unicode_cross_region_ranges():
    inputs = scene(); value = build(inputs); design, catalog, snapshot, _ = inputs
    for ref in value['refs']:
        assert set(ref) == {'path', 'owner_path', 'owner_value_hash', 'span_ref', 'source_ref', 'visible_start',
            'visible_end', 'text_hash', 'privacy_relation', 'source_anchor', 'ref_hash'}
        anchor = ref['source_anchor']
        assert set(anchor) == {'material_ref', 'evidence_id', 'raw_text_hash', 'raw_start', 'raw_end',
            'raw_location', 'trace_hash', 'contains_redaction', 'partial_redaction'}
        assert ref['privacy_relation'] == 'identity' and anchor['contains_redaction'] is anchor['partial_redaction'] is False
        assert ref['text_hash'] == catalog['index'][ref['span_ref']]['text_hash']
        assert set(anchor['raw_location']) == {'kind', 'location', 'regions'}
        if ref['source_ref'].startswith('w:'):
            assert anchor['material_ref'] == snapshot['original']['ref']
            assert anchor['raw_location']['regions'] == [] and 'page' not in anchor['raw_location']['location']
        else:
            assert anchor['material_ref'] == snapshot['copy']['ref'] and anchor['material_ref']['version'] == 3
            assert anchor['raw_location']['location']['revision_kind'] == 'raw'
            assert [(r['page_start'], r['page_end'], r['start'], r['end'])
                for r in anchor['raw_location']['regions']] == [(0, 2, 0, 2), (2, 5, 0, 3)]
            assert anchor['raw_location']['regions'][0]['box'] == ['1.0', '14', '50', '10']
    assert snapshot['coverage']['full_report_confirmed'] is False  # Manifest is valid even before formal confirmation.
    assert value['result_hash'] == digest(design['result'])


def test_redaction_length_change_partial_marker_and_identity_span_after_redaction_use_trace_offsets():
    raw = '甲' * 238 + ' ' + 'a@example.test' + '尾🙂' * 200
    snapshot = source(word=raw)
    inputs = scene(snapshot, change=lambda r: r['global_claims'][0].update(span_refs=['w:p1/s1', 'w:p1/s2', 'w:p1/s3']))
    value = build(inputs); catalog = inputs[1]
    refs = {r['span_ref']: r for r in value['refs'] if r['owner_path'] == '/global_claims/0'}
    first, last = refs['w:p1/s1'], refs['w:p1/s3']
    assert first['source_anchor']['contains_redaction'] is first['source_anchor']['partial_redaction'] is True
    assert first['privacy_relation'] == 'privacy_projected'
    assert first['source_anchor']['raw_end'] == 239 + len('a@example.test')
    assert last['source_anchor']['contains_redaction'] is False and last['privacy_relation'] == 'identity'
    assert last['source_anchor']['raw_start'] != last['visible_start']
    for ref in refs.values():
        anchor = ref['source_anchor']; assert anchor['raw_text_hash'] == sha(raw)
        if not anchor['contains_redaction']:
            assert raw[anchor['raw_start']:anchor['raw_end']] == catalog['index'][ref['span_ref']]['text']
    assert 'a@example.test' not in json.dumps(value, ensure_ascii=False)
    assert 'partial_redaction' in value['owners'][1]['pending_reasons']


def test_redaction_crossing_ocr_regions_maps_covering_raw_intervals_not_visible_offsets():
    snapshot = source(pages=[['前a@exa', 'mple.test后🙂'], ['另一页']])
    inputs = scene(snapshot); value = build(inputs)
    ref = next(r for r in value['refs'] if r['span_ref'] == 'o:p1/s1')
    anchor = ref['source_anchor']; assert anchor['contains_redaction'] is True
    assert anchor['raw_start'] == 0 and anchor['raw_end'] == len('前a@example.test后🙂')
    assert [(r['start'], r['end']) for r in anchor['raw_location']['regions']] == [(0, 6), (0, 11)]
    assert [r['text_hash'] for r in anchor['raw_location']['regions']] == [sha('前a@exa'), sha('mple.test后🙂')]
    assert 'example.test' not in json.dumps(value)


@pytest.mark.parametrize('raw', [
    '合成 sk-not_a_real_key_0123456789 末字🙂',
    '合成 password=PUBLIC_SYNTHETIC_ONLY 末字🙂',
    '合成 synthetic@example.test 末字🙂',
    '合成 13812345678 末字🙂',
])
def test_each_immutable_privacy_filter_parity_is_bound_before_span_resolution(raw):
    inputs = scene(source(word=raw)); value = build(inputs)
    assert inputs[1]['index']['w:p1/s1']['text'] == legacy_scrub(raw)
    ref = next(r for r in value['refs'] if r['span_ref'] == 'w:p1/s1')
    assert ref['source_anchor']['raw_text_hash'] == sha(raw) and ref['privacy_relation'] == 'privacy_projected'
    assert raw not in json.dumps(value, ensure_ascii=False)


def test_complete_object_field_value_including_unit_is_bound_without_claiming_unit_truth():
    def numeric(result):
        result['objects'][0]['fields'].append({'label': '长度', 'kind': '数值', 'required': False, 'unit': '米',
            'citations': [{'span_ref': 'w:p1/s1'}], 'inference_reason': ''})
    inputs = scene(change=numeric); value = build(inputs)
    owner = next(o for o in value['owners'] if o['path'] == '/objects/0/fields/1')
    assert owner['value_hash'] == digest(inputs[0]['result']['objects'][0]['fields'][1])
    changed = copy.deepcopy(inputs[0]); changed['result']['objects'][0]['fields'][1]['unit'] = '厘米'
    changed = semantic.build_design(changed['result'], **{k: changed[k] for k in semantic.CONTEXT_KEYS})
    with pytest.raises(ValueError): check(value, (changed, *inputs[1:]))
    # This inventories exact values; original-source unit semantics remain the outer validated-candidate gate.


@pytest.mark.parametrize('mutate', [
    lambda v: v['refs'].reverse(),
    lambda v: v['owners'].pop(),
    lambda v: v['owners'][0].update(label='伪造标签'),
    lambda v: v['owners'][1].update(evidence_status='unknown'),
    lambda v: v['refs'][0].update(path='/objects/0/citations/0'),
    lambda v: v['refs'][0].update(text_hash='0' * 64),
    lambda v: v['refs'][0]['source_anchor'].update(raw_start=False),  # bool must not compare equal to integer0.
    lambda v: v['refs'][0]['source_anchor'].update(raw_end=1),
    lambda v: v['refs'][0]['source_anchor'].update(trace_hash='0' * 64),
    lambda v: v['refs'][0]['source_anchor'].update(contains_redaction=True),
    lambda v: v['refs'][0]['source_anchor']['material_ref'].update(version=4),
    lambda v: v['refs'][0]['source_anchor'].update(raw_text='secret injected into output'),
    lambda v: v.update(source_hash='0' * 64),
    lambda v: v['privacy'].update(projection_hash='0' * 64),
])
def test_self_resealed_tamper_never_replaces_external_whole_cold_rebuild(mutate):
    inputs = scene(); value = build(inputs); mutate(value)
    for ref in value['refs']:
        ref['ref_hash'] = digest({k: v for k, v in ref.items() if k != 'ref_hash'})
    for owner in value['owners']:
        owner['ref_hashes'] = [r['ref_hash'] for r in value['refs'] if r['owner_path'] == owner['path']]
    value['manifest_hash'] = digest({k: v for k, v in value.items() if k != 'manifest_hash'})
    with pytest.raises(ValueError): check(value, inputs)


def test_same_refs_changed_recipient_changes_owner_and_occurrence_hash_and_old_manifest_is_stale():
    inputs = scene(); previous = build(inputs); design = copy.deepcopy(inputs[0])
    interaction = design['result']['businesses'][0]['interactions'][0]
    interaction['from_participant_index'], interaction['to_participant_index'] = 1, 0
    design = semantic.build_design(design['result'], **{key: design[key] for key in semantic.CONTEXT_KEYS})
    current = (design, *inputs[1:]); changed = build(current)
    path = '/businesses/0/interactions/0/evidence/citations/0'
    old, new = [next(r for r in value['refs'] if r['path'] == path) for value in (previous, changed)]
    assert old['span_ref'] == new['span_ref'] and old['text_hash'] == new['text_hash']
    assert old['owner_value_hash'] != new['owner_value_hash'] and old['ref_hash'] != new['ref_hash']
    with pytest.raises(ValueError): check(previous, current)
    assert check(previous, inputs) == previous  # Historical caller bindings remain readable.


def test_wrong_source_projection_catalog_or_outside_ref_fails_without_any_repair():
    inputs = scene(); design, catalog, snapshot, projection = inputs
    with pytest.raises(ValueError):
        manifest.build_source_manifest(design, catalog, source(word='相同ID但正文已变化'), privacy_projection_hash=projection)
    with pytest.raises(ValueError):
        manifest.build_source_manifest(design, catalog, snapshot, privacy_projection_hash='0' * 64)
    with pytest.raises(ValueError):
        build(scene(change=lambda r: r['global_claims'][0].update(span_refs=['w:p99/s1'])))
    with pytest.raises(ValueError):
        build(scene(source(word='甲' * 240 + ' ' * 241), change=lambda r: r['global_claims'][0].update(span_refs=['w:p1/s2'])))
    projected, _ = full.project_full_source(snapshot)
    alternate = build_evidence_source(digest(snapshot), [[b[0], b[3]] for b in legacy_tree(projected)['blocks']], 250)
    result = copy.deepcopy(design['result']); result['catalog_hash'] = alternate['descriptor']['catalog_hash']
    metadata = {key: design[key] for key in semantic.CONTEXT_KEYS}; metadata['catalog_hash'] = result['catalog_hash']
    other = semantic.build_design(result, **metadata)
    with pytest.raises(ValueError): manifest.build_source_manifest(other, alternate, snapshot, privacy_projection_hash=projection)


@pytest.mark.parametrize('limit', ['refs', 'bytes'])
def test_capacity_exact_boundary_then_rejects_whole_inventory_without_truncation(monkeypatch, limit):
    inputs = scene(); before = copy.deepcopy(inputs); value = build(inputs)
    name, exact = ('MAX_REFS', len(value['refs'])) if limit == 'refs' else (
        'MAX_MANIFEST_BYTES', len(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')))
    monkeypatch.setattr(manifest, name, exact)
    assert build(inputs) == value
    monkeypatch.setattr(manifest, name, exact - 1)
    with pytest.raises(manifest.ManifestCapacityExceeded): build(inputs)
    assert inputs == before
