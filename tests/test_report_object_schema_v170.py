"""Public synthetic V169 -> V170 schema plans, not real-source approval.

The new design/manifest and original geometry builders run unchanged. Invented
Word/PDF text and geometry prove only structural/ref preservation; no ledger,
formal-source authority, instance publication or real report quality is claimed.
Root alone executes the authored tests.
"""
import copy
import json
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

from generic_agents.contracts import digest
from generic_agents.report_full_reading_source import project_full_source
from generic_agents.report_full_semantic_region_mapping_v1 import find_region_candidates
from generic_agents import report_full_semantic_schema_v1 as subject
from generic_agents import report_object_source_manifest_v170 as manifests
from generic_agents.object_type_contract_v170 import check_object_type, build_attribute_schema
from tests import test_report_full_semantic_source_manifest_v1_pure as public
from tests import test_report_full_semantic_schema_v1_pure as legacy_schema
from tests import test_report_object_source_manifest_v170 as object_scene


def uid(number):
    return str(UUID(int=number))


def reseal(value, key):
    value[key] = digest({name: item for name, item in value.items() if name != key})
    return value


def inputs():
    text = '甲对象关联乙对象。长度（米）。🙂 原文。'
    design, catalog, snapshot, projection_hash = object_scene.scene(
        public.source(word=text, pages=[[text]]))
    manifest = manifests.build_source_manifest(design, catalog, snapshot,
        privacy_projection_hash=projection_hash)
    raw_page = snapshot['pages'][0]
    page = {'page': raw_page['page'], 'unit_id': raw_page['unit_id'],
        'revision_id': raw_page['revision']['id'], 'revision_hash': raw_page['revision']['hash'],
        'revision_kind': raw_page['revision']['kind'], 'image_sha256': raw_page['image']['sha256'],
        'regions': [{key: region[key] for key in ('id', 'text', 'box')} for region in raw_page['regions']]}
    copy_ref = copy.deepcopy(snapshot['copy']['ref'])
    chosen = reseal({'schema': 'report-full-semantic-region-source-selection-v1',
        'anchor': {'source_ref': copy.deepcopy(snapshot['original']['ref']),
            'source_evidence_id': snapshot['original']['evidence_id'], 'source_name': 'synthetic.docx',
            'copy_material_id': copy_ref['id'], 'copy_sha256': copy_ref['hash'],
            'copy_evidence_id': snapshot['copy']['evidence_id'], 'provenance_hash': 'a' * 64,
            'run_id': snapshot['copy']['run']['id'], 'generation': snapshot['copy']['run']['generation']},
        'copy_ref': copy_ref, 'pages': [{**{key: item for key, item in page.items() if key != 'regions'},
            'unit_version': raw_page['unit_version']}]}, 'selection_hash')
    _, index = project_full_source(snapshot)
    mappings = []
    for number, ref in enumerate(manifest['refs']):
        candidates = find_region_candidates(ref, index[ref['source_ref']], [page], copy_material_id=copy_ref['id'])
        assert candidates, 'The public fixture must have actual geometry for each original reference'
        mappings.append({'ref_hash': ref['ref_hash'], 'entry_id': uid(3000 + number),
            'entry_version': 1, 'mapping': candidates[0]})
    proof = reseal({'schema': 'report-object-region-proof-v170', 'handoff_id': design['handoff_id'],
        'manifest_hash': manifest['manifest_hash'], 'configuration': {'revision': 5, 'hash': 'b' * 64},
        'session_id': uid(4000), 'selection': chosen, 'confirmation_id': uid(4001), 'mappings': mappings,
        'pending_semantic_owner_paths': [owner['path'] for owner in manifest['owners']],
        'source_status': 'citation_positions_confirmed_semantics_pending',
        'can_confirm': False, 'can_publish': False}, 'proof_hash')
    selected = {'object_names': [obj['name'] for obj in design['result']['objects']],
        'relationship_names': [relation['name'] for relation in design['result']['relationships']]}
    policies = {name: {'cardinality': 'many', 'allow_self': False} for name in selected['relationship_names']}
    return design, manifest, proof, selected, policies


def build(values):
    return subject.build_schema_adoption(*values[:4], relationship_policies=values[4])


def check(plan, values):
    return subject.check_schema_adoption(plan, *values[:4], relationship_policies=values[4])


def test_new_plan_retains_optional_fields_exact_units_and_both_independent_identity_sources():
    values = inputs(); before = copy.deepcopy(values); plan = build(values)
    assert plan['schema'] == 'report-object-schema-adoption-v170'
    assert plan['semantic_mode'] == 'workspace_schema_design'
    assert plan['owner_paths'] == ['/objects/0', '/objects/0/fields/0',
        '/objects/0/business_identity', '/object_identity_confirmations/1',
        '/objects/1', '/objects/1/fields/0', '/objects/1/business_identity',
        '/object_identity_confirmations/0', '/relationships/0']
    owners = {owner['path']: owner for owner in values[1]['owners']}
    for index, (original, selected) in enumerate(zip(values[0]['result']['objects'], plan['objects'])):
        assert set(selected) == {'schema', 'name', 'fields', 'business_identity', 'platform_identity_confirmation'}
        assert selected['schema'] == 'report-object-type-v170' and check_object_type(selected) == selected
        assert selected['fields'] == [{key: field[key] for key in ('label', 'kind', 'required', 'unit')}
            for field in original['fields']]
        assert all(field['required'] is False for field in selected['fields'])
        assert selected['business_identity'] == original['business_identity']
        ack_index = next(i for i, item in enumerate(values[0]['object_identity_confirmations'])
            if item['object_name'] == original['name'])
        identity_path = f'/objects/{index}/business_identity'
        ack_path = f'/object_identity_confirmations/{ack_index}'
        saved_source = plan['object_sources'][original['name']]
        assert set(saved_source) == {'owner_path', 'owner_value_hash', 'ref_hashes',
            'declared_evidence_status', 'pending_reasons', 'fields',
            'business_identity', 'platform_identity_confirmation'}
        for member, path in [('business_identity', identity_path), ('platform_identity_confirmation', ack_path)]:
            assert saved_source[member]['owner_path'] == path
            assert saved_source[member]['owner_value_hash'] == owners[path]['value_hash']
            assert saved_source[member]['ref_hashes'] == owners[path]['ref_hashes']
            assert saved_source[member]['pending_reasons'] == owners[path]['pending_reasons']
            assert path in plan['owner_paths'] and path in plan['pending_evidence_owner_paths']
        assert saved_source['platform_identity_confirmation']['declared_evidence_status'] == 'context'
        assert saved_source['platform_identity_confirmation']['ref_hashes'] == []
        schema = build_attribute_schema(selected)
        Draft202012Validator.check_schema(schema)
        assert list(Draft202012Validator(schema).iter_errors({})) == []
    assert plan['objects'][0]['fields'][0]['unit'] is None
    assert plan['objects'][1]['fields'][0]['unit'] == '米'
    assert values[2]['pending_semantic_owner_paths'] == [owner['path'] for owner in values[1]['owners']]
    assert check(plan, values) == plan == build(values) and values == before
    assert object_scene.PRIVATE_NOTE not in json.dumps(plan, ensure_ascii=False)
    assert not {'can_execute', 'can_publish', 'can_confirm', 'source_verified', 'business_unique'} & set(plan)


def test_numeric_only_subset_keeps_original_paths_ack_array_index_and_empty_attribute_validity():
    values = list(inputs()); values[3] = {'object_names': ['乙对象'], 'relationship_names': []}; values[4] = {}
    before = copy.deepcopy(values); plan = build(values)
    assert plan['owner_paths'] == ['/objects/1', '/objects/1/fields/0',
        '/objects/1/business_identity', '/object_identity_confirmations/0']
    assert len(plan['objects']) == 1 and not plan['relationships']
    obj = plan['objects'][0]
    assert obj['name'] == '乙对象' and [field['kind'] for field in obj['fields']] == ['数值']
    assert obj['platform_identity_confirmation'] == values[0]['object_identity_confirmations'][0]
    schema = build_attribute_schema(obj)
    assert schema['required'] == [] and list(Draft202012Validator(schema).iter_errors({})) == []
    assert check(plan, values) == plan and values == before
    plan['objects'][0]['business_identity']['explanation'] = '返回值变化'
    plan['object_sources']['乙对象']['business_identity']['pending_reasons'].clear()
    assert values == before


def test_new_plan_does_not_temporarily_convert_optional_objects_to_old_required_text(monkeypatch):
    values = inputs()
    from generic_agents import report_full_semantic_configuration_v1 as old_design
    from generic_agents.instance_compiler import ObjectType
    def forbidden(*args, **kwargs):
        raise AssertionError('V170 schema plan must not enter an old object hard gate')
    monkeypatch.setattr(old_design, '_result', forbidden)
    monkeypatch.setattr(ObjectType, 'model_validate', forbidden)
    plan = build(values)
    assert plan['schema'] == 'report-object-schema-adoption-v170'
    assert all(not field['required'] for obj in plan['objects'] for field in obj['fields'])


@pytest.mark.parametrize('fault', ['manifest-old', 'proof-old', 'legacy-manifest-retagged', 'legacy-proof-retagged'])
def test_new_design_never_accepts_old_or_retagged_legacy_manifest_and_proof(fault):
    values = list(inputs())
    if fault == 'manifest-old': values[1]['schema'] = 'report-full-semantic-source-manifest-v1'
    elif fault == 'proof-old': values[2]['schema'] = 'report-full-semantic-region-proof-v1'
    elif fault == 'legacy-manifest-retagged':
        old = legacy_schema.inputs()[1]
        old.update(schema='report-object-source-manifest-v170', handoff_id=values[0]['handoff_id'],
            design_hash=values[0]['design_hash'], result_hash=digest(values[0]['result']))
        values[1] = old
    else:
        old = legacy_schema.inputs()[2]
        old.update(schema='report-object-region-proof-v170', handoff_id=values[0]['handoff_id'])
        values[2] = old
    reseal(values[1], 'manifest_hash')
    values[2]['manifest_hash'] = values[1]['manifest_hash']; reseal(values[2], 'proof_hash')
    before = copy.deepcopy(values)
    with pytest.raises(ValueError): build(values)
    assert values == before


@pytest.mark.parametrize('fault', ['identity', 'ack', 'identity-source', 'ack-source',
    'identity-path', 'pending-identity-path', 'unit', 'required', 'old-plan-marker'])
def test_resealed_output_cannot_omit_identity_ack_or_change_any_original_field(fault):
    values = inputs(); plan = build(values)
    if fault == 'identity': plan['objects'][0].pop('business_identity')
    elif fault == 'ack': plan['objects'][0].pop('platform_identity_confirmation')
    elif fault == 'identity-source': plan['object_sources']['甲对象'].pop('business_identity')
    elif fault == 'ack-source': plan['object_sources']['甲对象'].pop('platform_identity_confirmation')
    elif fault == 'identity-path': plan['owner_paths'].remove('/objects/0/business_identity')
    elif fault == 'pending-identity-path': plan['pending_evidence_owner_paths'].remove('/objects/1/business_identity')
    elif fault == 'unit': plan['objects'][1]['fields'][0]['unit'] = '厘米'
    elif fault == 'required': plan['objects'][0]['fields'][0]['required'] = True
    else: plan['schema'] = 'report-full-semantic-schema-adoption-v1'
    reseal(plan, 'plan_hash'); before = copy.deepcopy((values, plan))
    with pytest.raises(ValueError): check(plan, values)
    assert (values, plan) == before


@pytest.mark.parametrize('fault', ['missing-identity-owner', 'missing-ack-owner',
    'identity-value-hash', 'ack-as-source-evidence', 'missing-identity-citation'])
def test_resealed_manifest_still_requires_every_actual_identity_and_ack_owner(fault):
    values = list(inputs()); manifest = values[1]
    identity = next(owner for owner in manifest['owners'] if owner['path'] == '/objects/0/business_identity')
    ack = next(owner for owner in manifest['owners'] if owner['path'] == '/object_identity_confirmations/0')
    if fault == 'missing-identity-owner': manifest['owners'].remove(identity)
    elif fault == 'missing-ack-owner': manifest['owners'].remove(ack)
    elif fault == 'identity-value-hash': identity['value_hash'] = 'f' * 64
    elif fault == 'ack-as-source-evidence': ack['evidence_status'] = 'explicit'
    else:
        manifest['refs'] = [ref for ref in manifest['refs'] if ref['owner_path'] != identity['path']]
        identity['ref_hashes'] = []
    reseal(manifest, 'manifest_hash')
    values[2]['manifest_hash'] = manifest['manifest_hash']
    values[2]['pending_semantic_owner_paths'] = [owner['path'] for owner in manifest['owners']]
    reseal(values[2], 'proof_hash'); before = copy.deepcopy(values)
    with pytest.raises(ValueError): build(values)
    assert values == before


@pytest.mark.parametrize('fault', ['missing', 'borrowed-object-mapping'])
def test_identity_reference_needs_its_own_actual_geometry_mapping(fault):
    values = inputs(); manifest, proof = values[1:3]
    identity_ref = next(ref for ref in manifest['refs'] if ref['owner_path'] == '/objects/0/business_identity')
    item = next(mapping for mapping in proof['mappings'] if mapping['ref_hash'] == identity_ref['ref_hash'])
    if fault == 'missing': proof['mappings'].remove(item)
    else:
        object_ref = next(ref for ref in manifest['refs'] if ref['owner_path'] == '/objects/0')
        other = next(mapping for mapping in proof['mappings'] if mapping['ref_hash'] == object_ref['ref_hash'])
        item['mapping'] = copy.deepcopy(other['mapping'])
    reseal(proof, 'proof_hash'); before = copy.deepcopy(values)
    with pytest.raises(ValueError): build(values)
    assert values == before
