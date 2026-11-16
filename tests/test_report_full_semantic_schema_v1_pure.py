"""Synthetic pure contracts only. Root runs these; no authority or runtime proof.

The source/manifest/mapping builders are real, with inline invented source text.
No original document, DB, network, provider or private review state is accessed.
"""
import copy
import json
from uuid import UUID

import pytest

from generic_agents.contracts import digest
from generic_agents.report_full_reading_source import project_full_source
from generic_agents.report_full_semantic_region_mapping_v1 import find_region_candidates
from generic_agents.report_full_semantic_schema_v1 import build_schema_adoption, check_schema_adoption
from tests.test_report_full_semantic_source_manifest_v1_pure import build as build_manifest, scene, source


def uid(number):
    return str(UUID(int=number))


def seal(value, key):
    value[key] = digest({k: v for k, v in value.items() if k != key})
    return value


def inputs(change=None):
    text = '甲对象关联乙对象。长度（米）。🙂 原文。'
    values = scene(source(word=text, pages=[[text]]), change=change)
    design, _, snapshot, _ = values
    manifest = build_manifest(values)
    raw_page = snapshot['pages'][0]
    page = {'page': raw_page['page'], 'unit_id': raw_page['unit_id'],
        'revision_id': raw_page['revision']['id'], 'revision_hash': raw_page['revision']['hash'],
        'revision_kind': raw_page['revision']['kind'], 'image_sha256': raw_page['image']['sha256'],
        'regions': [{k: region[k] for k in ('id', 'text', 'box')} for region in raw_page['regions']]}
    copy_ref = copy.deepcopy(snapshot['copy']['ref'])
    chosen = seal({'schema': 'report-full-semantic-region-source-selection-v1',
        'anchor': {'source_ref': copy.deepcopy(snapshot['original']['ref']),
            'source_evidence_id': snapshot['original']['evidence_id'], 'source_name': 'synthetic.docx',
            'copy_material_id': copy_ref['id'], 'copy_sha256': copy_ref['hash'],
            'copy_evidence_id': snapshot['copy']['evidence_id'], 'provenance_hash': 'a' * 64,
            'run_id': snapshot['copy']['run']['id'], 'generation': snapshot['copy']['run']['generation']},
        'copy_ref': copy_ref, 'pages': [{**{k: v for k, v in page.items() if k != 'regions'},
            'unit_version': raw_page['unit_version']}]}, 'selection_hash')
    _, index = project_full_source(snapshot)
    mappings = []
    for number, ref in enumerate(manifest['refs']):
        candidates = find_region_candidates(ref, index[ref['source_ref']], [page], copy_material_id=copy_ref['id'])
        mappings.append({'ref_hash': ref['ref_hash'], 'entry_id': uid(3000 + number),
            'entry_version': 1, 'mapping': candidates[0]})
    proof = seal({'schema': 'report-full-semantic-region-proof-v1', 'handoff_id': design['handoff_id'],
        'manifest_hash': manifest['manifest_hash'], 'configuration': {'revision': 5, 'hash': 'b' * 64},
        'session_id': uid(4000), 'selection': chosen, 'confirmation_id': uid(4001), 'mappings': mappings,
        'pending_semantic_owner_paths': [owner['path'] for owner in manifest['owners']],
        'source_status': 'citation_positions_confirmed_semantics_pending',
        'can_confirm': False, 'can_publish': False}, 'proof_hash')
    selection = {'object_names': [obj['name'] for obj in design['result']['objects']],
        'relationship_names': [relation['name'] for relation in design['result']['relationships']]}
    policies = {name: {'cardinality': 'many', 'allow_self': False} for name in selection['relationship_names']}
    return design, manifest, proof, selection, policies


def build(values):
    return build_schema_adoption(*values[:4], relationship_policies=values[4])


def check(value, values):
    return check_schema_adoption(value, *values[:4], relationship_policies=values[4])


def all_fields(result):
    fields = result['objects'][0]['fields']
    for label, kind, unit in [('数量', '整数', '株'), ('长度🙂', '数值', '米'),
            ('完成', '是否', None), ('日期', '日期', None)]:
        fields.append({'label': label, 'kind': kind, 'required': False, 'unit': unit,
            'citations': [{'span_ref': 'w:p1/s1'}], 'inference_reason': ''})


def test_whole_fields_unicode_source_hashes_and_policy_origins_survive_without_mutation():
    values = inputs(all_fields); before = copy.deepcopy(values); value = build(values)
    assert set(value) == {'schema', 'design_hash', 'manifest_hash', 'proof_hash', 'selection',
        'relationship_policies', 'semantic_mode', 'owner_paths', 'pending_evidence_owner_paths',
        'objects', 'relationships', 'object_sources', 'relationship_sources', 'plan_hash'}
    assert value['schema'] == 'report-full-semantic-schema-adoption-v1'
    assert value['semantic_mode'] == 'workspace_schema_design'
    assert [field['kind'] for field in value['objects'][0]['fields']] == ['文本', '整数', '数值', '是否', '日期']
    assert value['objects'][0]['fields'][2]['unit'] == '米'
    assert value['owner_paths'] == ['/objects/0', *[f'/objects/0/fields/{i}' for i in range(5)],
        '/objects/1', '/objects/1/fields/0', '/relationships/0']
    object_source = value['object_sources']['甲对象']
    assert object_source['owner_value_hash'] == digest(values[0]['result']['objects'][0])
    assert [field['owner_path'] for field in object_source['fields']] == [f'/objects/0/fields/{i}' for i in range(5)]
    assert all(field['declared_evidence_status'] == 'not_declared' for field in object_source['fields'])
    relation_source = value['relationship_sources']['关联']
    assert relation_source['declared_cardinality'] == 'unspecified'
    assert relation_source['cardinality_origin'] == relation_source['allow_self_origin'] == 'workspace_policy'
    assert value['relationships'][0]['allow_self'] is False
    assert check(value, values) == value == build(values) and values == before
    assert '原文' not in json.dumps(value, ensure_ascii=False)
    value['objects'][0]['fields'].clear(); value['object_sources']['甲对象']['fields'].clear()
    assert build(values)['objects'][0]['fields'] and values == before


def test_uncited_or_inferred_definitions_remain_pending_workspace_decisions():
    def change(result):
        result['objects'][0]['citations'] = []
        result['objects'][0]['inference_reason'] = '这是设计建议，尚无来源结论'
        result['objects'][0]['fields'][0]['citations'] = []
        result['objects'][1]['fields'][0]['inference_reason'] = '推断字段，采用不是事实确认'
        result['relationships'][0]['inference_reason'] = '关系类型仅作为结构设计'
    values = inputs(change); value = build(values)
    assert value['pending_evidence_owner_paths'] == ['/objects/0', '/objects/0/fields/0',
        '/objects/1/fields/0', '/relationships/0']
    assert value['object_sources']['甲对象']['ref_hashes'] == []
    assert 'no_citations' in value['object_sources']['甲对象']['pending_reasons']
    assert value['object_sources']['甲对象']['declared_evidence_status'] == 'not_declared'
    assert 'needs_review' not in value  # No extra execution/confirmation grant is invented.
    assert check(value, values) == value


def test_subset_selection_preserves_original_paths_and_known_cardinality():
    values = list(inputs(lambda result: result['relationships'][0].update(cardinality='one_per_target')))
    values[4]['关联']['cardinality'] = 'one_per_target'
    assert build(values)['relationship_sources']['关联']['cardinality_origin'] == 'source_design'
    values[3] = {'object_names': ['乙对象'], 'relationship_names': []}; values[4] = {}
    value = build(values)
    assert value['owner_paths'] == ['/objects/1', '/objects/1/fields/0']
    assert list(value['object_sources']) == ['乙对象'] and not value['relationships']


@pytest.mark.parametrize('selection', [None, {}, {'object_names': [], 'relationship_names': []},
    {'object_names': ['乙对象', '甲对象'], 'relationship_names': ['关联']},
    {'object_names': ['甲对象', '甲对象'], 'relationship_names': []},
    {'object_names': ['陌生对象'], 'relationship_names': []},
    {'object_names': ['甲对象', True], 'relationship_names': []},
    {'object_names': ['甲对象'], 'relationship_names': ['关联', '关联']},
    {'object_names': ['甲对象'], 'relationship_names': [], 'field_names': []},
    {'object_names': ['甲对象'] * 41, 'relationship_names': []},
    {'object_names': ['甲对象'], 'relationship_names': ['关联'] * 31}])
def test_selection_is_closed_ordered_unique_complete_and_bounded(selection):
    values = list(inputs()); values[3] = selection
    with pytest.raises(ValueError): build(values)


@pytest.mark.parametrize('policy', [None, {}, {'其他': {'cardinality': 'many', 'allow_self': False}},
    {'关联': {'cardinality': 'unspecified', 'allow_self': False}},
    {'关联': {'cardinality': 'many'}}, {'关联': {'cardinality': 'many', 'allow_self': 0}},
    {'关联': {'cardinality': 'many', 'allow_self': False, 'confirmed': True}}])
def test_relationship_policies_require_exact_explicit_choices(policy):
    values = list(inputs()); values[4] = policy
    with pytest.raises(ValueError): build(values)


def test_known_cardinality_cannot_change_and_selected_endpoints_are_required():
    values = list(inputs(lambda result: result['relationships'][0].update(cardinality='one_per_source')))
    with pytest.raises(ValueError): build(values)
    values = list(inputs()); values[3]['object_names'] = ['甲对象']
    with pytest.raises(ValueError): build(values)


@pytest.mark.parametrize('mutation', ['extra', 'hash', 'missing_field', 'changed_required', 'source',
    'origin', 'relation_origin', 'pending', 'policy', 'null', 'unit'])
def test_resealing_the_output_never_hides_omitted_fields_or_forged_origins(mutation):
    values = inputs(all_fields); value = build(values)
    if mutation == 'extra': value['can_execute'] = True
    elif mutation == 'hash': value['proof_hash'] = '0' * 64
    elif mutation == 'missing_field': value['objects'][0]['fields'].pop()
    elif mutation == 'changed_required': value['objects'][0]['fields'][0]['required'] = 1
    elif mutation == 'source': value['object_sources']['甲对象']['declared_evidence_status'] = 'explicit'
    elif mutation == 'origin': value['object_sources']['甲对象']['analysis_id'] = uid(1)
    elif mutation == 'relation_origin': value['relationship_sources']['关联']['allow_self_origin'] = 'source_fact'
    elif mutation == 'pending': value['owner_paths'].pop()
    elif mutation == 'policy': value['relationships'][0]['allow_self'] = True
    elif mutation == 'null': value = None
    elif mutation == 'unit': value['objects'][0]['fields'][2]['unit'] = '厘米'
    if value is not None: seal(value, 'plan_hash')
    with pytest.raises(ValueError): check(value, values)


@pytest.mark.parametrize('mutation', ['owner_hash', 'owner_refs', 'owner_extra', 'owner_missing',
    'owner_order', 'ref_hash', 'ref_owner', 'ref_missing', 'ref_extra', 'anchor_extra', 'privacy'])
def test_manifest_cannot_lose_or_move_an_owner_or_occurrence_even_when_resealed(mutation):
    values = list(inputs()); manifest = values[1]; refs = manifest['refs']
    if mutation == 'owner_hash': manifest['owners'][0]['value_hash'] = '0' * 64
    elif mutation == 'owner_refs': manifest['owners'][0]['ref_hashes'] = [refs[0]['ref_hash']]
    elif mutation == 'owner_extra': manifest['owners'][0]['approved'] = True
    elif mutation == 'owner_missing': manifest['owners'].pop()
    elif mutation == 'owner_order': manifest['owners'].reverse()
    elif mutation == 'ref_hash': refs[0]['ref_hash'] = '0' * 64
    elif mutation == 'ref_owner': refs[0]['owner_path'] = '/objects/0'; seal(refs[0], 'ref_hash')
    elif mutation == 'ref_missing': refs.pop()
    elif mutation == 'ref_extra': refs.append(copy.deepcopy(refs[0]))
    elif mutation == 'anchor_extra': refs[0]['source_anchor']['private_note'] = '不得泄露'; seal(refs[0], 'ref_hash')
    elif mutation == 'privacy': manifest['privacy']['schema'] = 'legacy_privacy'
    seal(manifest, 'manifest_hash'); values[2]['manifest_hash'] = manifest['manifest_hash']; seal(values[2], 'proof_hash')
    with pytest.raises(ValueError): build(values)


@pytest.mark.parametrize('mutation', ['manifest', 'extra', 'missing', 'duplicate', 'order', 'pending',
    'can_confirm', 'version_bool', 'mapping_owner', 'mapping_hash', 'mapping_extra', 'page_version',
    'span_extra', 'raw_hash', 'selection_hash', 'selection_extra'])
def test_position_proof_stays_complete_typed_and_linked_without_semantic_grants(mutation):
    values = list(inputs()); proof = values[2]; item = proof['mappings'][0]; mapping = item['mapping']
    if mutation == 'manifest': proof['manifest_hash'] = '0' * 64
    elif mutation == 'extra': proof['approved_semantics'] = True
    elif mutation == 'missing': proof['mappings'].pop()
    elif mutation == 'duplicate': proof['mappings'][1] = copy.deepcopy(item)
    elif mutation == 'order': proof['mappings'].reverse()
    elif mutation == 'pending': proof['pending_semantic_owner_paths'] = []
    elif mutation == 'can_confirm': proof['can_confirm'] = 0
    elif mutation == 'version_bool': item['entry_version'] = True
    elif mutation == 'mapping_owner': mapping['owner_value_hash'] = '0' * 64; seal(mapping, 'mapping_hash')
    elif mutation == 'mapping_hash': mapping['mapping_hash'] = '0' * 64
    elif mutation == 'mapping_extra': mapping['raw_text'] = '不允许'; seal(mapping, 'mapping_hash')
    elif mutation == 'page_version': mapping['segments'][0]['revision_id'] = uid(99999); seal(mapping, 'mapping_hash')
    elif mutation == 'span_extra': mapping['segments'][0]['spans'][0]['note'] = '私记'; seal(mapping, 'mapping_hash')
    elif mutation == 'raw_hash': mapping['raw_quote_hash'] = '0' * 64; seal(mapping, 'mapping_hash')
    elif mutation == 'selection_hash': proof['selection']['selection_hash'] = '0' * 64
    elif mutation == 'selection_extra': proof['selection']['private_note'] = '私记'; seal(proof['selection'], 'selection_hash')
    seal(proof, 'proof_hash')
    with pytest.raises(ValueError): build(values)


@pytest.mark.parametrize('mutation', ['field_unit_empty', 'field_required_integer', 'field_missing',
    'unknown_kind', 'field_capacity', 'object_capacity', 'relation_capacity', 'unicode', 'nul'])
def test_source_design_unsupported_values_and_caps_fail_without_repair(mutation):
    values = list(inputs()); design = values[0]; result = design['result']; field = result['objects'][0]['fields'][0]
    if mutation == 'field_unit_empty': field['unit'] = ''
    elif mutation == 'field_required_integer': field['required'] = 1
    elif mutation == 'field_missing': field.pop('unit')
    elif mutation == 'unknown_kind': field['kind'] = '任意JSON'
    elif mutation == 'field_capacity': result['objects'][0]['fields'] = [copy.deepcopy(field) for _ in range(41)]
    elif mutation == 'object_capacity': result['objects'] = [copy.deepcopy(result['objects'][0]) for _ in range(41)]
    elif mutation == 'relation_capacity': result['relationships'] = [copy.deepcopy(result['relationships'][0]) for _ in range(31)]
    elif mutation == 'unicode': field['label'] = '\ud800'
    elif mutation == 'nul': field['label'] = '名称\x00'
    if mutation != 'unicode': seal(design, 'design_hash')
    before = copy.deepcopy(values)
    with pytest.raises(ValueError): build(values)
    assert values == before


def test_each_missing_input_or_output_field_is_rejected():
    originals = inputs()
    for input_index in range(5):
        for key in originals[input_index]:
            values = copy.deepcopy(list(originals)); values[input_index].pop(key)
            with pytest.raises(ValueError): build(values)
    value = build(originals)
    for key in value:
        missing = copy.deepcopy(value); missing.pop(key)
        with pytest.raises(ValueError): check(missing, originals)


def test_dictionary_order_is_irrelevant_but_list_order_and_external_hashes_are_not():
    values = inputs(); first = build(values)
    def reordered(value):
        if type(value) is dict: return {key: reordered(value[key]) for key in reversed(value)}
        if type(value) is list: return [reordered(item) for item in value]
        return value
    assert build(reordered(list(values))) == first
    broken = copy.deepcopy(first); broken['plan_hash'] = '0' * 64
    with pytest.raises(ValueError): check(broken, values)
    cyclic = {}; cyclic['cycle'] = cyclic
    with pytest.raises(ValueError): build_schema_adoption(cyclic, *values[1:4], relationship_policies=values[4])
