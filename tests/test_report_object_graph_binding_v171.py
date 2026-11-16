"""Public synthetic graph contracts; no issued ledger or execution grant.

Actual V169/V170 builders, original geometry and the complete graph validators
run unchanged. The reused old fixture supplies invented graph choices, not an
old binding retagged into a new family. Root executes the authored tests.
"""
import copy

import pytest

from generic_agents.contracts import digest
from generic_agents import report_object_configuration_v169 as designs
from generic_agents import report_object_graph_binding_v171 as subject
from generic_agents import report_object_source_manifest_v170 as manifests
from generic_agents import report_full_semantic_graph_binding_v3 as legacy
from generic_agents import report_full_semantic_graph_binding_v4 as events
from generic_agents.graph_wait_sources_v118 import POLICY
from generic_agents.report_full_reading_source import project_full_source
from generic_agents.report_full_semantic_region_mapping_v1 import find_region_candidates
from generic_agents.report_object_semantics_v166 import CANDIDATE, SCHEMA as MANUAL
from tests import test_report_full_semantic_graph_binding_v3 as graph_scene
from tests import test_report_full_semantic_source_manifest_v1_pure as public
from tests import test_report_object_source_manifest_v170 as object_scene
from tests.test_graph_event_observation_sources import contract_fixture
from tests.test_report_full_semantic_schema_v1_pure import uid


def reseal(value, key):
    value[key] = digest({name: item for name, item in value.items() if name != key})
    return value


def fixture(**flags):
    old = graph_scene.fixture(**flags)
    text = '甲对象关联乙对象。长度（米）。🙂 原文。'
    seed, catalog, snapshot, projection_hash = object_scene.scene(public.source(word=text, pages=[[text]]))
    result = copy.deepcopy(old['design']['result']); result['schema'] = CANDIDATE
    assert (result['source_hash'], result['catalog_hash']) == (seed['source_hash'], seed['catalog_hash'])
    for obj, proposed in zip(result['objects'], seed['result']['objects']):
        obj['fields'] = copy.deepcopy(proposed['fields'])
        obj['business_identity'] = copy.deepcopy(proposed['business_identity'])
    confirmations = copy.deepcopy(seed['object_identity_confirmations'])
    manual_hash = digest({'schema': MANUAL, 'result': result, 'note': '',
        'object_identity_confirmations': confirmations})
    origin = copy.deepcopy(seed['review_origin']); origin['confirmed_value_hash'] = manual_hash
    context = {key: seed[key] for key in designs.CONTEXT_KEYS}; context['manual_value_hash'] = manual_hash
    design = designs.build_design(result, confirmations, origin, **context)
    manifest = manifests.build_source_manifest(design, catalog, snapshot, privacy_projection_hash=projection_hash)
    raw = snapshot['pages'][0]
    page = {'page': raw['page'], 'unit_id': raw['unit_id'], 'revision_id': raw['revision']['id'],
        'revision_hash': raw['revision']['hash'], 'revision_kind': raw['revision']['kind'],
        'image_sha256': raw['image']['sha256'],
        'regions': [{key: region[key] for key in ('id', 'text', 'box')} for region in raw['regions']]}
    _, index = project_full_source(snapshot)
    mappings = []
    for number, ref in enumerate(manifest['refs']):
        found = find_region_candidates(ref, index[ref['source_ref']], [page],
            copy_material_id=snapshot['copy']['ref']['id'])
        assert found, 'Every public source occurrence needs actual geometry'
        mappings.append({'ref_hash': ref['ref_hash'], 'entry_id': uid(3000 + number),
            'entry_version': 1, 'mapping': found[0]})
    proof = reseal({**copy.deepcopy(old['proof']), 'schema': 'report-object-region-proof-v170',
        'handoff_id': design['handoff_id'], 'manifest_hash': manifest['manifest_hash'], 'mappings': mappings,
        'pending_semantic_owner_paths': [owner['path'] for owner in manifest['owners']]}, 'proof_hash')
    graph = copy.deepcopy(old['graph'])
    source_ids = old['design']['business_bindings'][0]['step_ids']
    new_ids = design['business_bindings'][0]['step_ids']
    for node in graph['nodes']:
        if node['source_step_id'] is not None:
            node['source_step_id'] = new_ids[source_ids.index(node['source_step_id'])]

    def target(name, field=None, collection='objects'):
        index, item = next((index, item) for index, item in enumerate(result[collection]) if item['name'] == name)
        return {'kind': 'schema_origin', 'id': name, 'field': field,
            'field_type': None if field is None else next(declared['kind'] for declared in item['fields'] if declared['label'] == field),
            'origin': {'schema': 'report-object-schema-origin-v170', 'adoption_id': uid(950),
                'plan_hash': 'd' * 64, 'kind': 'object' if collection == 'objects' else 'relationship',
                'name': name, 'owner_path': '/' + collection + '/' + str(index), 'owner_value_hash': digest(item)}}

    previous = {row['owner_path']: row for row in old['owner_bindings']}; owners = []
    inventory = subject.required_owner_fields(design)
    for spec, source in zip(inventory, manifest['owners']):
        path, kind = spec['owner_path'], spec['kind']
        assert path == source['path']
        if kind in {'business_identity', 'platform_identity_confirmation'}:
            name = (result['objects'][int(path.split('/')[2])]['name'] if kind == 'business_identity'
                else confirmations[int(path.split('/')[2])]['object_name'])
            row = {'resolution': 'typed', 'reason': '仅采用完整结构说明，不认定来源事实或业务唯一性',
                'scope_businesses': [old['business_name']],
                'clauses': [{'source_field': field, 'targets': [target(name)]} for field in spec['source_fields']]}
        else:
            row = copy.deepcopy(previous[path])
            for clause in row['clauses']:
                updated = []
                for item in clause['targets']:
                    if item['kind'] == 'schema_origin':
                        collection = 'objects' if item['origin']['kind'] == 'object' else 'relationships'
                        field = result['objects'][int(path.split('/')[2])]['fields'][int(path.split('/')[4])]['label'] if kind == 'field' else item['field']
                        item = target(item['id'], field, collection)
                    # The optional field is not rewritten into a non-nullable
                    # input requirement. The explicit global clause below
                    # accounts for the wait's independent correlation input.
                    if flags.get('wait') and kind == 'field' and item['kind'] == 'value_ref': continue
                    updated.append(item)
                clause['targets'] = updated
            if flags.get('wait') and path == '/global_claims/0':
                next(clause for clause in row['clauses'] if clause['source_field'] == 'statement')['targets'].append(
                    graph_scene.ref('value_ref', 'case'))
        owners.append({**row, 'owner_path': path, 'value_hash': source['value_hash'],
            'ref_hashes': copy.deepcopy(source['ref_hashes'])})
    return {'design': design, 'manifest': manifest, 'proof': proof, 'business_name': old['business_name'],
        'graph': graph, 'owner_bindings': owners, 'proof_id': old['proof_id'],
        'capability_pins': copy.deepcopy(old['capability_pins']), 'role_bindings': copy.deepcopy(old['role_bindings'])}


def build(values, *, sources=None, policy=None):
    return subject.build_binding(**values, event_source_bindings={} if sources is None else sources,
        event_admission_policy=copy.deepcopy(POLICY) if policy is None else policy)


def wait_values():
    values = fixture(wait=True); contract = contract_fixture(); subscription = values['graph']['subscriptions'][0]
    subscription.update(event_type=contract['event_kind'], output_schema=copy.deepcopy(contract['output_schema']))
    sources = {subscription['id']: {'producer_id': uid(11901), 'contract_hash': digest(contract), 'contract': contract,
        'scope': {'entity_policy': 'frozen_task_entities', 'correlation_ref': subscription['correlation_ref'],
            'source_policy': 'exact_registered_source'}}}
    return values, sources


@pytest.mark.parametrize('flags', [{}, {'branch': True}, {'wait': True}])
def test_new_family_builds_its_own_base_and_preserves_complete_graph_and_source_owners(flags):
    values, sources = wait_values() if flags.get('wait') else (fixture(**flags), {})
    before = copy.deepcopy(values); base = subject.build_base_binding(**values); value = build(values, sources=sources)
    assert base['schema'] == subject.BASE_SCHEMA and value['schema'] == subject.SCHEMA
    assert value['base_binding_hash'] == base['binding_hash']
    assert value['source_design'] == values['design'] and value['source_manifest'] == values['manifest']
    assert value['owner_bindings'] == values['owner_bindings'] and value['graph'] == values['graph']
    assert subject.check_binding(value) == value == legacy.check_binding(value)
    assert values == before and value['source_semantics_status'] == 'not_certified'
    assert not {'can_apply', 'can_execute', 'source_verified', 'business_unique'} & set(value)
    checked = subject.check_binding(value); checked['owner_bindings'][0]['clauses'].clear()
    assert value['owner_bindings'] == before['owner_bindings']


def test_complete_identity_and_confirmation_clause_inventory_retains_actual_indices_and_optional_fields():
    values = fixture(); design = values['design']; specs = subject.required_owner_fields(design)
    assert [item['owner_path'] for item in specs] == [owner['path'] for owner in values['manifest']['owners']]
    expected = {'business_identity': ['status', 'field_labels', 'explanation'],
        'platform_identity_confirmation': ['object_name', 'acknowledge_platform_identity']}
    rows = {row['owner_path']: row for row in values['owner_bindings']}
    for spec in specs:
        if spec['kind'] not in expected: continue
        assert spec['source_fields'] == expected[spec['kind']]
        row = rows[spec['owner_path']]
        for clause in row['clauses']:
            assert len(clause['targets']) == 1
            ref = clause['targets'][0]
            assert ref['field'] is ref['field_type'] is None and ref['origin']['schema'] == 'report-object-schema-origin-v170'
            name = design['result']['objects'][int(spec['owner_path'].split('/')[2])]['name'] if spec['kind'] == 'business_identity' else design['object_identity_confirmations'][int(spec['owner_path'].split('/')[2])]['object_name']
            assert ref['id'] == name and ref['origin']['owner_value_hash'] == digest(next(
                obj for obj in design['result']['objects'] if obj['name'] == name))
    assert design['object_identity_confirmations'][0]['object_name'] == '乙对象'
    assert all(not field['required'] for obj in design['result']['objects'] for field in obj['fields'])
    assert design['result']['objects'][1]['fields'][0]['kind'] == '数值'
    assert design['result']['objects'][1]['fields'][0]['unit'] == '米'
    assert build(values)['blocked_businesses'] == []


@pytest.mark.parametrize('operation', ['old-builder', 'old-event-builder', 'old-inventory', 'new-builder-on-old'])
def test_public_family_entry_points_cannot_relabel_old_and_new_contracts(operation):
    values = fixture()
    with pytest.raises(ValueError):
        if operation == 'old-builder': legacy.build_binding(**values)
        elif operation == 'old-event-builder':
            events.build_binding(**values, event_source_bindings={}, event_admission_policy=copy.deepcopy(POLICY))
        elif operation == 'old-inventory': legacy.required_owner_fields(values['design'])
        else: build(graph_scene.fixture())


@pytest.mark.parametrize('fault', ['old-origin', 'other-object', 'field-only', 'missing-clause',
    'informational', 'wrong-value', 'wrong-reference', 'dropped-owner'])
def test_identity_clauses_cannot_borrow_an_origin_or_drop_original_owner_coverage(fault):
    values = fixture(); row = next(row for row in values['owner_bindings'] if row['owner_path'] == '/objects/0/business_identity')
    ref = row['clauses'][0]['targets'][0]
    if fault == 'old-origin': ref['origin']['schema'] = 'report-full-semantic-schema-origin-v1'
    elif fault == 'other-object':
        other = next(row for row in values['owner_bindings'] if row['owner_path'] == '/objects/1/business_identity')
        row['clauses'][0]['targets'] = copy.deepcopy(other['clauses'][0]['targets'])
    elif fault == 'field-only': ref.update(field='名称', field_type='文本')
    elif fault == 'missing-clause': row['clauses'].pop()
    elif fault == 'informational': row['resolution'] = 'informational'
    elif fault == 'wrong-value': row['value_hash'] = 'f' * 64
    elif fault == 'wrong-reference': row['ref_hashes'] = []
    else: values['owner_bindings'].remove(row)
    before = copy.deepcopy(values)
    with pytest.raises(ValueError): build(values)
    assert values == before


def test_unresolved_identity_retains_full_clauses_and_blocks_only_explicit_business_scope():
    values = fixture(extra_business=True); names = [item['name'] for item in values['design']['result']['businesses']]
    row = next(row for row in values['owner_bindings'] if row['owner_path'] == '/objects/1/business_identity')
    row.update(resolution='unresolved', reason='另一个业务的标识说明仍待核对', scope_businesses=[names[1]])
    for clause in row['clauses']: clause['targets'] = []
    value = build(values)
    assert value['blocked_businesses'] == [names[1]] and names[0] not in value['blocked_businesses']
    assert next(item for item in value['owner_bindings'] if item['owner_path'] == row['owner_path']) == row


@pytest.mark.parametrize('fault', ['manifest-family', 'proof-family', 'ack-false', 'ack-name'])
def test_resealed_sources_cannot_revert_family_or_replace_original_acknowledgement(fault):
    values = fixture()
    if fault == 'manifest-family':
        values['manifest']['schema'] = 'report-full-semantic-source-manifest-v1'; reseal(values['manifest'], 'manifest_hash')
        values['proof']['manifest_hash'] = values['manifest']['manifest_hash']; reseal(values['proof'], 'proof_hash')
    elif fault == 'proof-family':
        values['proof']['schema'] = 'report-full-semantic-region-proof-v1'; reseal(values['proof'], 'proof_hash')
    else:
        ack = values['design']['object_identity_confirmations'][0]
        if fault == 'ack-false': ack['acknowledge_platform_identity'] = False
        else: ack['object_name'] = '甲对象'
        reseal(values['design'], 'design_hash')
    with pytest.raises(ValueError): build(values)


@pytest.mark.parametrize('fault', ['missing-source', 'contract-hash', 'scope', 'policy-type',
    'base-hash', 'unknown-marker', 'old-marker', 'extra-field'])
def test_event_layer_and_new_base_hash_are_rebuilt_even_after_final_binding_reseal(fault):
    values, sources = wait_values(); value = build(values, sources=sources)
    if fault == 'missing-source': value['event_source_bindings'].clear()
    elif fault == 'contract-hash': value['event_source_bindings']['sub']['contract_hash'] = 'f' * 64
    elif fault == 'scope': value['event_source_bindings']['sub']['scope']['entity_policy'] = 'workspace_all'
    elif fault == 'policy-type': value['event_admission_policy']['source_time_verified'] = 0
    elif fault == 'base-hash': value['base_binding_hash'] = legacy.build_binding(**graph_scene.fixture())['binding_hash']
    elif fault == 'unknown-marker': value['schema'] = 'report-object-graph-binding-v172'
    elif fault == 'old-marker': value['schema'] = 'report-full-semantic-graph-binding-v4'
    else: value['can_execute'] = True
    reseal(value, 'binding_hash'); before = copy.deepcopy(value)
    for checker in (subject.check_binding, legacy.check_binding):
        with pytest.raises(ValueError): checker(value)
    assert value == before


def test_legacy_v3_and_v4_still_rebuild_the_original_hashes_without_new_metadata():
    values = graph_scene.fixture(); before = copy.deepcopy(values); original = legacy.build_binding(**values)
    value = events.build_binding(**values, event_source_bindings={}, event_admission_policy=copy.deepcopy(POLICY))
    assert original['schema'] == legacy.SCHEMA and value['base_binding_hash'] == original['binding_hash']
    assert legacy.check_binding(original) == original and legacy.check_binding(value) == value
    assert values == before
