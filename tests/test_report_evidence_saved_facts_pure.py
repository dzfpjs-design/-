"""New synthetic journal display only; no original answers or model calls."""
import copy
import json
import os

import pytest

from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_saved_facts as facts
from generic_agents.contracts import digest
from tests.test_report_evidence_pipeline_pure import offline, scene, store, map_value
from tests.test_report_evidence_generation_versions_pure import historical_scene


def prepared(version=2, value=None):
    p, _, _, row = scene() if version == 2 else historical_scene()[:4]
    stage = row.data['maps'][0]['chunk_id']; value = map_value(row, stage) if value is None else value
    store(row, stage, value, succeeded=False)
    catalog = pipeline._catalog(row.data['preview']); preview = row.data['preview']
    context = {'actor_id': row.actor_id, 'workspace_id': row.workspace_id, 'draft_id': row.id,
        'draft_revision': row.version, 'stage': stage, 'stage_status': 'failed',
        'generation_schema': preview['map_preparation']['generation_contract']['schema'],
        'source_hash': preview['source_hash'], 'catalog_hash': catalog['descriptor']['catalog_hash'],
        'messages_hash': pipeline._authorized(row, stage)['messages_hash'],
        'ocr_revision_kinds': {'o:p' + str(page['page']): page['revision_kind']
            for page in preview['body']['selection']['pages']}}
    return context, catalog, pipeline._authorized(row, stage)['span_refs'], value, pipeline._descriptor(row, stage)


def project(data):
    context, catalog, refs, value, descriptor = data
    return facts.project_saved_facts(context, catalog, refs, value, journal=descriptor)


def test_long_key_does_not_hide_unchanged_five_fields_or_wash_the_original_protocol(monkeypatch):
    data = prepared(); data[3]['claims'][0]['key'] = 'condition_unclear_machinery_library_location'
    before = copy.deepcopy(data)
    def no_io(*args, **kwargs): raise AssertionError('projector must be pure')
    monkeypatch.setattr(os, 'open', no_io)
    view = project(data); item = view['facts'][0]
    assert len(item['protocol_key']) == 44 and view['original_protocol_valid'] is False
    assert any(issue['path'].endswith('/key') for issue in view['protocol_issues'])
    assert item['field_constraints_valid'] is True and item['issues'] == []
    assert item['fields'] == {key: data[3]['claims'][0][key] for key in facts._FIELDS}
    assert item['meaning_review_status'] == view['meaning_review_status'] == 'unreviewed'
    assert view['eligible_for_reduce'] is view['can_apply'] is view['can_publish'] is False
    assert data == before and view['view_hash'] == digest({key: value for key, value in view.items() if key != 'view_hash'})


def test_v1_bad_kind_stays_original_and_is_never_mapped_to_other():
    data = prepared(version=1); data[3]['claims'][0]['kind'] = '流程'
    view = project(data)
    assert view['generation_schema'].endswith('-v1') and not view['original_protocol_valid']
    assert view['facts'][0]['fields']['kind'] == '流程' and not view['facts'][0]['field_constraints_valid']
    assert view['facts'][0]['references'][0]['text'] == data[1]['index'][data[2][0]]['text']


@pytest.mark.parametrize('ref,state', [('bad-ref', 'invalid'), ('w:p99999/s1', 'missing'), ('o:p1/s1', 'outside_batch')])
def test_bad_references_are_visible_without_replacement_or_borrowing_another_batch(ref, state):
    data = prepared(); data[3]['claims'][0]['span_refs'] = [ref]
    view = project(data); item = view['facts'][0]
    assert item['fields']['span_refs'] == [ref] and not item['field_constraints_valid']
    assert item['references'][0] == {'span_ref': ref, 'state': state, 'source_ref': None,
        'start': None, 'end': None, 'text': None, 'source_kind': None, 'source_number': None, 'revision_kind': None}


def test_duplicate_refs_stay_in_original_order_and_fail_constraints():
    data = prepared(); ref = data[2][0]; data[3]['claims'][0]['span_refs'] = [ref, ref]
    item = project(data)['facts'][0]
    assert not item['field_constraints_valid'] and [ref['span_ref'] for ref in item['references']] == [ref, ref]


def test_ocr_revision_kind_is_the_frozen_selection_not_a_claim_of_human_confirmation():
    data = prepared(); ref = next(ref for ref in data[1]['index'] if ref.startswith('o:'))
    data[2][:] = [ref]; data[3]['claims'][0]['span_refs'] = [ref]
    data[0]['ocr_revision_kinds']['o:p1'] = 'raw'
    item = project(data)['facts'][0]
    assert item['references'][0]['source_kind'] == 'ocr_page'
    assert item['references'][0]['source_number'] == 1 and item['references'][0]['revision_kind'] == 'raw'
    assert item['fields']['status'] == 'explicit' and item['meaning_review_status'] == 'unreviewed'


@pytest.mark.parametrize('availability', ['missing', 'quarantined', 'not_json'])
def test_unavailable_wire_has_fixed_human_state_and_no_claimed_protocol_or_fact_result(availability):
    data = prepared(); descriptor = None if availability == 'missing' else data[4]
    if availability == 'quarantined':
        descriptor = copy.deepcopy(descriptor); descriptor.update(consumable=False, quarantine_reasons=['wire_encoding_invalid'])
        descriptor['descriptor_hash'] = digest({key: value for key, value in descriptor.items() if key != 'descriptor_hash'})
    value = facts.project_saved_facts(data[0], data[1], data[2], journal=descriptor, availability=availability)
    assert value['availability'] == availability and value['facts'] == [] and value['original_protocol_valid'] is None
    assert value['meaning_review_status'] == 'unreviewed' and not value['eligible_for_reduce']


@pytest.mark.parametrize('wire', [[], {'claims': {}}, {'schema': 'wrong', 'claims': [{}] * 81}])
def test_unsupported_container_or_claim_count_is_explicit_and_never_cut_to_eighty(wire):
    data = prepared(); data = (*data[:3], wire, data[4]); view = project(data)
    assert view['availability'] == 'unsupported_structure' and view['facts'] == []
    assert view['original_protocol_valid'] is False and view['protocol_issues']


def test_unsupported_values_are_not_repr_or_arbitrary_raw_json():
    class Unsupported:
        def __repr__(self): raise AssertionError('must not repr unsupported objects')
    data = prepared(); data[3]['claims'][0].update(kind=Unsupported(), statement={'arbitrary': 'not returned'})
    item = project(data)['facts'][0]
    assert item['fields']['kind'] is None and item['fields']['statement'] is None
    assert not item['field_constraints_valid'] and 'arbitrary' not in json.dumps(item)


def test_display_id_is_deterministic_scoped_wire_position_identity_and_not_a_model_key():
    data = prepared(); first = project(data); assert project(copy.deepcopy(data)) == first
    data[3]['claims'].append(copy.deepcopy(data[3]['claims'][0])); second = project(data)
    assert second['facts'][0]['display_id'] != second['facts'][1]['display_id']
    expected = 'sf_' + digest({'schema': 'report-evidence-saved-fact-id-v1', 'binding_hash': data[4]['binding_hash'],
        'wire_sha256': data[4]['wire_sha256'], 'position': 0})
    assert first['facts'][0]['display_id'] == expected
    changed = copy.deepcopy(data); changed[4]['wire_sha256'] = 'f' * 64
    changed[4]['descriptor_hash'] = digest({key: value for key, value in changed[4].items() if key != 'descriptor_hash'})
    assert project(changed)['facts'][0]['display_id'] != expected


def test_descriptor_scope_tamper_is_rejected_even_with_a_recomputed_outer_hash():
    data = prepared(); data[4]['binding']['actor_id'] = '10000000-0000-4000-8000-000000000099'
    data[4]['binding_hash'] = digest(data[4]['binding'])
    data[4]['descriptor_hash'] = digest({key: value for key, value in data[4].items() if key != 'descriptor_hash'})
    with pytest.raises(ValueError): project(data)
