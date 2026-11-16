"""Pure public synthetic inventories, not real-source or human approval proof.

The actual source/projection and new design checks are used on public generated
values. No DB, file, provider response, active ledger, source truth or execution
authorization is established. Root alone executes these authored tests.
"""
import copy
import json
import socket
import subprocess

import pytest

from generic_agents.contracts import digest
from generic_agents import report_full_reading_source as full
from generic_agents import report_full_semantic_configuration_v1 as old_design
from generic_agents import report_full_semantic_source_manifest_v1 as old_manifest
from generic_agents import report_object_configuration_v169 as design
from generic_agents import report_object_source_manifest_v170 as subject
from generic_agents.report_evidence_plan import build_evidence_source
from generic_agents.report_object_semantics_v166 import CANDIDATE, SCHEMA as MANUAL
from tests import test_report_full_semantic_source_manifest_v1_pure as public


PRIVATE_NOTE = 'PRIVATE_REVIEW_NOTE_NOT_IN_SHARED_MANIFEST_末字🙂'


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure V170 inventory checks cannot perform external IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene(snapshot=None):
    snapshot = public.source() if snapshot is None else snapshot
    projection, _ = full.project_full_source(snapshot)
    projected = public.legacy_tree(projection)
    catalog = build_evidence_source(digest(snapshot), [[block[0], block[3]] for block in projected['blocks']])
    result = public.candidate(digest(snapshot), catalog['descriptor']['catalog_hash'])
    result['schema'] = CANDIDATE
    for obj in result['objects']:
        for field in obj['fields']:
            field['required'] = False
        obj['business_identity'] = {'status': 'not_established', 'field_labels': [], 'citations': [],
            'explanation': '人工稿尚未明确业务标识，不代表原文没有标识。'}
    result['objects'][0]['business_identity'] = {'status': 'referenced', 'field_labels': ['名称'],
        'citations': [{'span_ref': 'w:p1/s1'}], 'explanation': '人工引用现有字段，尚未认定其业务唯一性。'}
    result['objects'][1]['fields'] = [{'label': '长度', 'kind': '数值', 'required': False, 'unit': '米',
        'citations': [{'span_ref': 'w:p1/s1'}], 'inference_reason': ''}]
    confirmations = [{'object_name': obj['name'], 'acknowledge_platform_identity': True}
        for obj in reversed(result['objects'])]
    manual_hash = digest({'schema': MANUAL, 'result': result, 'note': PRIVATE_NOTE,
        'object_identity_confirmations': confirmations})
    origin = {'schema': design.ORIGIN_SCHEMA, 'report_id': public.identity(51),
        'actor_id': public.identity(52), 'workspace_id': public.identity(3),
        'revision_id': public.identity(53), 'revision_version': 2,
        'revision_record_hash': '1' * 64, 'basis_hash': '2' * 64,
        'confirmed_command_id': public.identity(54), 'confirmed_command_hash': '3' * 64,
        'confirmed_wire_sha256': '4' * 64, 'confirmed_value_hash': manual_hash,
        'confirmed_audit_id': public.identity(55), 'confirmed_audit_hash': '5' * 64}
    value = design.build_design(result, confirmations, origin, handoff_id=public.identity(50),
        report_id=origin['report_id'], report_revision=3, review_receipt_hash=origin['confirmed_command_hash'],
        manual_value_hash=manual_hash, model_result_hash='6' * 64, source_hash=result['source_hash'],
        catalog_hash=result['catalog_hash'], context_hash='7' * 64)
    return value, catalog, snapshot, digest(projected)


def build(inputs):
    value, catalog, snapshot, projection_hash = inputs
    return subject.build_source_manifest(value, catalog, snapshot, privacy_projection_hash=projection_hash)


def check(value, inputs):
    design_value, catalog, snapshot, projection_hash = inputs
    return subject.check_source_manifest(value, design_value, catalog, snapshot, privacy_projection_hash=projection_hash)


def reseal(value):
    value['manifest_hash'] = digest({key: item for key, item in value.items() if key != 'manifest_hash'})


def test_complete_new_inventory_preserves_all_optional_units_and_real_confirmation_order(monkeypatch):
    inputs = scene(); before = copy.deepcopy(inputs)
    def forbidden(*args, **kwargs):
        pytest.fail('New inventory must not convert objects through old design validation')
    monkeypatch.setattr(old_design, '_result', forbidden)
    value = build(inputs); owners = {item['path']: item for item in value['owners']}
    assert set(value) == {'schema', 'handoff_id', 'design_hash', 'result_hash', 'source_hash',
        'catalog_hash', 'privacy', 'owners', 'refs', 'manifest_hash'}
    assert value['schema'] == 'report-object-source-manifest-v170'
    assert value['design_hash'] == inputs[0]['design_hash'] and value['result_hash'] == digest(inputs[0]['result'])
    assert [item['path'] for item in value['owners']][-2:] == [
        '/object_identity_confirmations/0', '/object_identity_confirmations/1']
    assert subject.owner_value(inputs[0], '/object_identity_confirmations/0')['object_name'] == '乙对象'
    for index, obj in enumerate(inputs[0]['result']['objects']):
        path = '/objects/' + str(index)
        assert path + '/business_identity' in owners
        assert owners[path]['value_hash'] == digest(obj)
        assert all(field['required'] is False for field in obj['fields'])
    assert subject.owner_value(inputs[0], '/objects/1/fields/0')['unit'] == '米'
    assert check(value, inputs) == value == build(inputs) and inputs == before
    assert PRIVATE_NOTE not in json.dumps(value, ensure_ascii=False)
    assert not any(token in json.dumps(value) for token in ('"quote"', '"raw_text"', '"note"', 'can_apply'))


def test_unchanged_base_owner_metadata_exactly_matches_the_old_contract():
    old_inputs = public.scene(); old = public.build(old_inputs)
    by_path = {item['path']: item for item in old['owners']}
    for path, kind, label, item, evidence, sites in old_manifest._owner_inputs(old_inputs[0]['result']):
        refs = [ref for ref in old['refs'] if ref['owner_path'] == path]
        assert subject.owner_metadata(kind, item, evidence, sites, refs) == {
            key: by_path[path][key] for key in ('evidence_status', 'pending_reasons')}


def test_identity_descriptions_are_unknown_or_pending_claims_and_acknowledgement_is_human_context():
    value = build(scene()); owners = {owner['path']: owner for owner in value['owners']}
    cited = owners['/objects/0/business_identity']
    assert cited['evidence_status'] == 'not_declared' and len(cited['ref_hashes']) == 1
    assert 'identity_claim_requires_review' in cited['pending_reasons']
    unknown = owners['/objects/1/business_identity']
    assert unknown['evidence_status'] == 'unknown' and unknown['ref_hashes'] == []
    assert 'unknown_evidence' in unknown['pending_reasons']
    for index in range(2):
        owner = owners['/object_identity_confirmations/' + str(index)]
        assert owner['kind'] == 'platform_identity_confirmation'
        assert owner['evidence_status'] == 'context' and owner['ref_hashes'] == []
        assert owner['pending_reasons'] == ['human_confirmation_not_source_evidence']


def test_each_identity_citation_occurrence_has_its_own_path_and_original_anchor():
    inputs = scene(); value = build(inputs)
    identity_ref = next(ref for ref in value['refs'] if ref['owner_path'] == '/objects/0/business_identity')
    object_ref = next(ref for ref in value['refs'] if ref['owner_path'] == '/objects/0')
    assert identity_ref['path'] == '/objects/0/business_identity/citations/0'
    assert identity_ref['span_ref'] == object_ref['span_ref'] == 'w:p1/s1'
    assert identity_ref['ref_hash'] != object_ref['ref_hash']
    assert identity_ref['owner_value_hash'] == digest(inputs[0]['result']['objects'][0]['business_identity'])
    assert identity_ref['source_anchor'] == object_ref['source_anchor']
    assert len({ref['ref_hash'] for ref in value['refs']}) == len(value['refs'])


def test_owner_values_and_inventory_are_detached_and_hash_identical_without_generic_path_access():
    inputs = scene(); before = copy.deepcopy(inputs); manifest = build(inputs)
    entries = subject.owner_inputs(inputs[0])
    assert [entry[0] for entry in entries] == [owner['path'] for owner in manifest['owners']]
    for owner in manifest['owners']:
        value = subject.owner_value(inputs[0], owner['path'])
        assert digest(value) == owner['value_hash']
    entries[-1][3]['object_name'] = '只改返回的清单值'
    copied = subject.owner_value(inputs[0], '/objects/0/business_identity')
    copied['citations'].clear()
    assert inputs == before


@pytest.mark.parametrize('path', ['/note', '/review_origin', '/object_identity_confirmations',
    '/object_identity_confirmations/0/object_name', '/objects/0/fields/0/label', '/objects/00', '../objects/0'])
def test_only_exact_complete_inventory_owner_paths_can_be_resolved(path):
    with pytest.raises(ValueError): subject.owner_value(scene()[0], path)


@pytest.mark.parametrize('fault', ['drop_identity', 'drop_confirmation', 'confirmation_label',
    'confirmation_explicit', 'unknown_explicit', 'identity_hash', 'reference_path',
    'reference_owner', 'private_top', 'private_owner'])
def test_resealed_manifest_changes_cannot_replace_the_complete_inventory(fault):
    inputs = scene(); value = build(inputs)
    owners = {owner['path']: owner for owner in value['owners']}
    if fault == 'drop_identity': value['owners'].remove(owners['/objects/0/business_identity'])
    elif fault == 'drop_confirmation': value['owners'].pop()
    elif fault == 'confirmation_label': value['owners'][-1]['label'] = '改名后不能重封绕过'
    elif fault == 'confirmation_explicit': value['owners'][-1]['evidence_status'] = 'explicit'
    elif fault == 'unknown_explicit': owners['/objects/1/business_identity']['evidence_status'] = 'explicit'
    elif fault == 'identity_hash': owners['/objects/0/business_identity']['value_hash'] = '0' * 64
    elif fault == 'reference_path': value['refs'][-1]['path'] = '/object_identity_confirmations/0/citations/0'
    elif fault == 'reference_owner': value['refs'][-1]['owner_path'] = '/objects/0/business_identity'
    elif fault == 'private_top': value['note'] = PRIVATE_NOTE
    else: value['owners'][-1]['note'] = PRIVATE_NOTE
    reseal(value)
    with pytest.raises(ValueError): check(value, inputs)


@pytest.mark.parametrize('fault', ['old_manifest', 'old_design'])
def test_new_checker_never_accepts_an_old_or_mixed_family(fault):
    inputs = scene(); value = build(inputs)
    if fault == 'old_manifest':
        value['schema'] = old_manifest.SCHEMA; reseal(value)
    else:
        inputs[0]['schema'] = old_design.SCHEMA
        inputs[0]['design_hash'] = digest({key: item for key, item in inputs[0].items() if key != 'design_hash'})
    with pytest.raises(ValueError): check(value, inputs)


@pytest.mark.parametrize('fault', ['source_snapshot', 'catalog', 'projection_hash'])
def test_real_source_projection_algorithms_reject_changed_supplied_source_structures(fault):
    inputs = list(scene()); value = build(inputs)
    if fault == 'source_snapshot': inputs[2] = public.source(word='另一原件，不能沿用原冻结摘要。')
    elif fault == 'catalog': inputs[1]['descriptor']['catalog_hash'] = '0' * 64
    else: inputs[3] = '0' * 64
    with pytest.raises(ValueError): check(value, inputs)


def test_identity_citation_privacy_metadata_uses_the_same_exact_raw_interval_algorithm():
    inputs = scene(public.source(word='长度（米）。联系 public.person@example.test，正文🙂。'))
    value = build(inputs)
    owner = next(owner for owner in value['owners'] if owner['path'] == '/objects/0/business_identity')
    ref = next(ref for ref in value['refs'] if ref['owner_path'] == owner['path'])
    assert ref['source_anchor']['contains_redaction'] is True
    assert 'privacy_projected_reference' in owner['pending_reasons']
    assert 'public.person@example.test' not in json.dumps(value, ensure_ascii=False)
    assert check(value, inputs) == value


@pytest.mark.parametrize('cap', ['refs', 'bytes'])
def test_manifest_capacity_is_explicit_and_never_trims_input(monkeypatch, cap):
    assert subject.MAX_MANIFEST_BYTES == 16 * 1024 * 1024 and subject.MAX_REFS == 65536
    inputs = scene(); before = copy.deepcopy(inputs)
    monkeypatch.setattr(subject, 'MAX_REFS' if cap == 'refs' else 'MAX_MANIFEST_BYTES', 1)
    with pytest.raises(subject.ManifestCapacityExceeded): build(inputs)
    assert inputs == before


def test_identity_metadata_does_not_allow_source_references_for_platform_acknowledgement():
    with pytest.raises(ValueError):
        subject.owner_metadata('platform_identity_confirmation', {}, None,
            [('/object_identity_confirmations/0/citations/0', 'w:p1/s1')], [{}])
    with pytest.raises(ValueError): subject.owner_metadata('unrecognized_kind', {}, None, [], [])


def test_extra_private_design_note_is_rejected_before_source_inventory_building():
    inputs = scene(); inputs[0]['note'] = PRIVATE_NOTE
    inputs[0]['design_hash'] = digest({key: value for key, value in inputs[0].items() if key != 'design_hash'})
    with pytest.raises(ValueError): build(inputs)
