"""Synthetic display/reader unit tests; no actual journal or provider proof."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_evidence_saved_facts_v2 as subject
from generic_agents import report_evidence_structured_tool_v2 as tool
from generic_agents.contracts import digest
from tests.test_report_evidence_projection_v5_pure import scene as projection_scene


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure saved-facts test cannot connect or execute')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene(wire=None):
    original, inputs = projection_scene((0, 79))
    wire = copy.deepcopy(original if wire is None else wire)
    arguments = json.dumps(wire, ensure_ascii=True)
    body = json.dumps({'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
        {'id': 'synthetic_slot_call', 'type': 'function', 'function': {
            'name': 'submit_report_evidence_map_slots', 'arguments': arguments}}]}}]},
        ensure_ascii=True).encode('ascii')
    metadata = tool.inspect_response(body, 200, 'map-v5')['metadata']
    descriptor = copy.deepcopy(inputs['descriptor'])
    descriptor.update(wire_bytes=len(body), wire_sha256=hashlib.sha256(body).hexdigest())
    descriptor['descriptor_hash'] = digest({key: value for key, value in descriptor.items() if key != 'descriptor_hash'})
    binding = descriptor['binding']
    context = {key: binding[key] for key in ('actor_id', 'workspace_id', 'draft_id', 'stage',
        'source_hash', 'catalog_hash', 'messages_hash')}
    context.update(draft_revision=7, stage_status='failed', generation_schema=subject.GENERATION,
        ocr_revision_kinds={'o:p1': 'raw'})
    return wire, {'context': context, 'catalog': inputs['catalog'],
        'allowed_span_refs': inputs['allowed_span_refs'], 'journal': descriptor, 'tool_metadata': metadata}


def projected(wire, values, **extra):
    return subject.project_saved_facts_v2(wire_value=wire, **values, **extra)


def test_closed_v2_shape_slot_origin_original_positions_and_display_ids_are_not_adoption():
    wire, values = scene(); before = copy.deepcopy((wire, values))
    result = projected(wire, values)
    assert len(result) == 24 and result['schema'] == 'report-evidence-saved-facts-v2'
    assert result['original_protocol_valid'] is True
    assert result['slot_origin'] == {'wire_schema': subject.WIRE_SCHEMA, 'codec': subject.DISPLAY_CODEC,
        'wire_value_hash': digest(wire), 'arguments_sha256': values['tool_metadata']['arguments_sha256'],
        'arguments_bytes': values['tool_metadata']['arguments_bytes'], 'slot_positions': [0, 79]}
    assert [fact['position'] for fact in result['facts']] == [0, 79]
    assert [fact['slot_name'] for fact in result['facts']] == ['slot_001', 'slot_080']
    for fact in result['facts']:
        assert len(fact) == 9 and fact['protocol_key'] is None
        assert fact['display_id'] == 'sf_' + digest({'schema': subject.DISPLAY_IDS,
            'binding_hash': values['journal']['binding_hash'],
            'arguments_sha256': values['tool_metadata']['arguments_sha256'],
            'path': '/slots/' + fact['slot_name'] + '/claim'})
        assert fact['meaning_review_status'] == 'unreviewed' and fact['field_constraints_valid'] is True
    assert result['eligible_for_reduce'] is result['can_apply'] is result['can_publish'] is False
    assert result['view_hash'] == digest({key: value for key, value in result.items() if key != 'view_hash'})
    assert (wire, values) == before


@pytest.mark.parametrize('mode', ['kind', 'status', 'long', 'missing', 'extra', 'unsupported_type', 'nul', 'surrogate'])
def test_bad_one_claim_stays_visible_without_rejecting_the_other_or_repairing_fields(mode):
    wire, _ = scene(); claim = wire['slots']['slot_001']['claim']
    if mode == 'kind': claim['kind'] = '原有不支持分类'
    elif mode == 'status': claim['status'] = 'confirmed'
    elif mode == 'long': claim['statement'] = '甲' * 2001
    elif mode == 'missing': del claim['explanation']
    elif mode == 'extra': claim['key'] = 'must_not_be_adopted'
    elif mode == 'unsupported_type': claim['statement'] = {'private_value': 'not raw JSON display'}
    elif mode == 'nul': claim['statement'] = '不能安全显示\x00但原答仍保留'
    else: claim['statement'] = '\ud800'
    wire, values = scene(wire); result = projected(wire, values)
    assert result['availability'] == 'available' and result['original_protocol_valid'] is False
    assert len(result['facts']) == 2
    first, second = result['facts']
    assert first['field_constraints_valid'] is False and first['issues']
    assert second['field_constraints_valid'] is True
    if mode in {'unsupported_type', 'nul', 'surrogate'}:
        assert first['fields']['statement'] is None
    elif mode == 'missing': assert first['fields']['explanation'] is None
    elif mode == 'extra': assert first['protocol_key'] is None and 'key' not in first['fields']
    else:
        field = 'kind' if mode == 'kind' else 'status' if mode == 'status' else 'statement'
        assert first['fields'][field] == claim[field]
    assert result['slot_origin']['arguments_sha256'] == values['tool_metadata']['arguments_sha256']
    assert result['can_apply'] is False


def test_references_preserve_order_duplicates_and_each_failure_without_substituting_text():
    wire, _ = scene()
    refs = ['w:p1/s1', 'w:p1/s1', 'w:p2/s1', 'w:p999/s1', 'w:p1/s0']
    wire['slots']['slot_001']['claim']['span_refs'] = refs
    wire, values = scene(wire); result = projected(wire, values)
    fact = result['facts'][0]
    assert fact['fields']['span_refs'] == refs
    assert [item['span_ref'] for item in fact['references']] == refs
    assert [item['state'] for item in fact['references']] == ['resolved', 'resolved', 'outside_batch', 'missing', 'invalid']
    for item in fact['references'][2:]:
        assert all(value is None for key, value in item.items() if key not in {'span_ref', 'state'})
    assert fact['field_constraints_valid'] is False and result['original_protocol_valid'] is False


@pytest.mark.parametrize('mode', ['missing', 'extra_81', 'unknown_state', 'absent_extra',
    'present_extra', 'claim_not_object', 'top_extra', 'old_array', 'unknown_wire'])
def test_nonclosed_or_incomplete_slots_are_visible_as_unsupported_without_partial_extraction(mode):
    wire, _ = scene()
    if mode == 'missing': del wire['slots']['slot_080']
    elif mode == 'extra_81': wire['slots']['slot_081'] = {'state': 'absent'}
    elif mode == 'unknown_state': wire['slots']['slot_002']['state'] = 'maybe'
    elif mode == 'absent_extra': wire['slots']['slot_002']['claim'] = {}
    elif mode == 'present_extra': wire['slots']['slot_001']['extra'] = True
    elif mode == 'claim_not_object': wire['slots']['slot_001']['claim'] = []
    elif mode == 'top_extra': wire['extra'] = True
    elif mode == 'unknown_wire': wire['schema'] = 'unknown'
    else: wire = {'schema': 'report-evidence-map-v3', 'claims': [{}] * 88}
    wire, values = scene(wire); result = projected(wire, values)
    assert result['availability'] == 'unsupported_structure' and result['original_protocol_valid'] is False
    assert result['facts'] == [] and result['slot_origin'] is None and result['protocol_issues']


@pytest.mark.parametrize('availability', ['missing', 'quarantined', 'not_json'])
def test_unavailable_states_have_complete_closed_truthful_read_only_dtos(availability):
    _, values = scene()
    if availability == 'missing': values.update(journal=None, tool_metadata=None)
    elif availability == 'quarantined':
        values['journal'].update(consumable=False, quarantine_reasons=['wire_capacity'])
        values['journal']['descriptor_hash'] = digest({key: value for key, value in values['journal'].items() if key != 'descriptor_hash'})
        values['tool_metadata'] = None
    else:
        values['tool_metadata'].update(envelope_valid=False, finish_reason='length', envelope_errors=['finish_reason'])
    result = subject.project_saved_facts_v2(**values, availability=availability)
    assert len(result) == 24 and result['facts'] == [] and result['slot_origin'] is None
    assert result['availability'] == availability
    assert result['original_protocol_valid'] is (False if availability == 'not_json' else None)
    if availability == 'not_json': assert '未完整结束' in result['message'] and result['tool_metadata']['finish_reason'] == 'length'
    assert result['eligible_for_reduce'] is result['can_apply'] is result['can_publish'] is False


@pytest.mark.parametrize('key,value', [('generation_schema', 'report-evidence-map-generation-v4'),
    ('stage', 'reduce'), ('draft_revision', True), ('source_hash', 'f' * 64)])
def test_context_cannot_borrow_old_generations_reduce_stage_or_other_source(key, value):
    wire, values = scene(); values['context'][key] = value
    with pytest.raises(ValueError): projected(wire, values)


def test_usage_absence_and_raw_ocr_labels_are_never_promoted_to_formal_or_meaning_approval():
    wire, _ = scene(); wire['slots']['slot_001']['claim']['span_refs'] = ['o:p1/s1']
    wire, values = scene(wire); values['allowed_span_refs'] = ['w:p1/s1', 'o:p1/s1']
    result = projected(wire, values)
    assert result['tool_metadata']['usage_available'] is False
    assert result['facts'][0]['references'][0]['revision_kind'] == 'raw'
    assert result['facts'][0]['references'][0]['source_kind'] == 'ocr_page'
    assert result['meaning_review_status'] == 'unreviewed' and result['can_publish'] is False


def test_object_key_order_cannot_move_slots_and_view_capacity_includes_seal(monkeypatch):
    wire, values = scene(); expected = projected(wire, values)
    wire['slots'] = dict(reversed(list(wire['slots'].items())))
    assert projected(wire, values) == expected
    size = len(subject._bytes(expected))
    monkeypatch.setattr(subject, 'MAX_VIEW_BYTES', size - 1)
    with pytest.raises(ValueError): projected(wire, values)


@pytest.mark.parametrize('mode', ['length', 'multi', 'bad_kind', 'valid'])
def test_read_helper_checks_original_whole_http_but_never_salvages_first_tool_or_mutates_state(mode, monkeypatch):
    from tests.test_report_evidence_pipeline_v5_pure import Pipeline
    pipeline = Pipeline(); key = 'map-1'
    wire = copy.deepcopy(pipeline.responses[key])
    # Obtain the original synthetic wire before replacing the envelope only.
    body = wire['content'].encode('utf-8')
    value = tool.parse_arguments(body, 200, 'map-v5')
    if mode == 'bad_kind': value['slots']['slot_003']['claim']['kind'] = '不支持的分类'
    finish, choices = ('length' if mode == 'length' else 'tool_calls'), (2 if mode == 'multi' else 1)
    pipeline.install_response(key, value, finish=finish, choices=choices)
    loaded = pipeline.responses[key]
    pipeline.states[key].update(status='failed', journal=copy.deepcopy(loaded['descriptor']), result_hash=None, result_count=None)
    context = {key: pipeline.bindings['map-1'][key] for key in ('actor_id', 'workspace_id', 'draft_id',
        'stage', 'source_hash', 'catalog_hash', 'messages_hash')}
    context.update(draft_revision=1, stage_status='failed', generation_schema=subject.GENERATION,
        ocr_revision_kinds={'o:p1': 'raw'})
    pipeline.v5 = __import__('generic_agents.report_evidence_pipeline_v5', fromlist=['inspect_response'])
    pipeline._catalog = lambda preview: pipeline.catalog
    pipeline._stage_journal_root = pipeline._journal_root
    pipeline.map_journal.read_descriptor = lambda root, binding: copy.deepcopy(
        pipeline.responses[binding['stage']]['descriptor'])
    before = copy.deepcopy((pipeline.row, pipeline.states, pipeline.responses))
    def forbidden(*args, **kwargs): raise AssertionError('Read-only facts must not settle, consume, audit or call')
    for name in ('_consume', '_settle', 'audit', 'cloud_json'):
        monkeypatch.setattr(pipeline, name, forbidden, raising=False)
    result = subject.read_saved_facts(pipeline, pipeline.row, 'map-1', context)
    assert result['availability'] == ('not_json' if mode in {'length', 'multi'} else 'available')
    if mode in {'length', 'multi'}: assert result['facts'] == []
    else: assert len(result['facts']) == 1 and result['facts'][0]['slot_name'] == 'slot_003'
    # Multiple choices have no single trusted finish_reason: do not select
    # the first choice merely to expose its metadata.
    assert result['tool_metadata']['finish_reason'] == (None if mode == 'multi' else finish)
    if mode == 'multi':
        assert result['tool_metadata']['response_choice_count'] == 2
    assert result['original_protocol_valid'] is (mode == 'valid')
    assert pipeline.row.__dict__ == before[0].__dict__ and pipeline.states == before[1] and pipeline.responses == before[2]
