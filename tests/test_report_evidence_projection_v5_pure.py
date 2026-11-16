"""Pure, synthetic slot-origin proofs. No HTTP/DB/journal authenticity simulated.

The deliberately fabricated complete descriptor/metadata is a caller input to
the pure function, not evidence that a provider delivered anything. Root alone
executes these checks. No real report, account, original paid reply or gold.
"""
import copy
import hashlib
import json
import socket
import subprocess
from uuid import UUID

import pytest

from generic_agents import report_evidence_map_slots_v5 as slots
from generic_agents import report_evidence_projection_v5 as subject
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import build_evidence_source
from generic_agents.report_evidence_strict_map import METADATA_KEYS


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure projection cannot connect or launch')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def identity(number):
    return str(UUID('10000000-0000-4000-8000-' + f'{number:012x}'))


def sha(value):
    return hashlib.sha256(value).hexdigest()


def claim(number):
    return {'kind': '职责', 'statement': f' 合成职责{number}＋+；逐字保留。 ',
        'span_refs': ['w:p1/s1'], 'status': 'explicit', 'explanation': ''}


def scene(positions=(0, 2, 79)):
    catalog = build_evidence_source('a' * 64,
        [('w:p1', '合成来源一：登记并核对。'), ('w:p2', '另一合成来源。'), ('o:p1', '合成OCR读取视图。')])
    wire = {'schema': slots.WIRE_SCHEMA, 'slots': {name: {'state': 'absent'} for name in slots.SLOT_NAMES}}
    for position in positions:
        wire['slots'][slots.SLOT_NAMES[position]] = {'state': 'present', 'claim': claim(position)}
    arguments = json.dumps(wire, ensure_ascii=False, indent=2)
    tools = [{'type': 'function', 'id': 'synthetic_tool_1', 'function': {
        'name': subject.TOOL_NAME, 'arguments': arguments}}]
    body = json.dumps({'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': tools}}]},
        ensure_ascii=False).encode('utf-8')
    binding = {'schema': 'report-evidence-journal-binding-v1', 'actor_id': identity(1),
        'workspace_id': identity(2), 'draft_id': identity(3), 'reserved_call_id': identity(4),
        'stage': 'map-1', 'source_hash': catalog['descriptor']['source_hash'],
        'catalog_hash': catalog['descriptor']['catalog_hash'], 'generation_hash': 'b' * 64,
        'dispatch_hash': 'c' * 64, 'messages_hash': 'd' * 64}
    receipt = json.dumps({'call_id': binding['reserved_call_id']},
        sort_keys=True, separators=(',', ':')).encode('ascii')
    envelope = {'schema': 'report-evidence-journal-envelope-v1', 'binding': copy.deepcopy(binding),
        'binding_hash': digest(binding), 'wire_kind': 'text', 'encoding': 'utf-8-surrogatepass-v1',
        'wire_bytes': len(body), 'wire_sha256': sha(body), 'receipt_status': 'saved',
        'receipt_bytes': len(receipt), 'receipt_sha256': sha(receipt), 'consumable': True, 'quarantine_reasons': []}
    envelope_bytes = json.dumps(envelope, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')
    descriptor = {**envelope, 'schema': 'report-evidence-journal-descriptor-v1',
        'envelope_bytes': len(envelope_bytes), 'envelope_sha256': sha(envelope_bytes)}
    descriptor['descriptor_hash'] = digest(descriptor)
    metadata = {'delivery': subject.DELIVERY, 'response_body_sha256': sha(body), 'response_body_bytes': len(body),
        'response_body_encoding': 'utf-8-surrogateescape-v1', 'http_status': 200, 'finish_reason': 'tool_calls',
        'response_choice_count': 1, 'tool_call_count': 1, 'tool_call_id': tools[0]['id'],
        'tool_name': subject.TOOL_NAME, 'tool_type': 'function', 'tool_calls_hash': digest(tools),
        'arguments_sha256': sha(arguments.encode('utf-8')), 'arguments_bytes': len(arguments.encode('utf-8')),
        'arguments_encoding': 'utf-8', 'envelope_valid': True, 'envelope_errors': [],
        'usage_available': False, 'usage_present': False}
    assert set(metadata) == set(METADATA_KEYS)
    profile = {'version': 'cloud-evidence-2026-10-17/map-v5', 'stage': 'map-v5',
        'max_input_bytes': 900000, 'transport_timeout_seconds': 180, 'max_output_tokens': 8192,
        'max_images': 0, 'context_token_bound': 1000000, 'full_report': True, 'long_context_verified': True}
    return wire, {'binding': binding, 'expected_binding': copy.deepcopy(binding), 'descriptor': descriptor,
        'metadata': metadata, 'evidence_profile': profile, 'catalog': catalog, 'allowed_span_refs': ['w:p1/s1']}


def test_slot_origin_ids_holes_order_five_fields_and_all_input_values_are_preserved():
    wire, inputs = scene(); before = copy.deepcopy((wire, inputs))
    wire['slots'] = dict(reversed(list(wire['slots'].items())))
    projection = subject.build_map_projection(wire, **inputs)
    assert projection['slot_positions'] == [0, 2, 79]
    context = {'domain': subject.DERIVATION, 'binding_hash': digest(inputs['binding']),
        'arguments_sha256': inputs['metadata']['arguments_sha256'], 'codec': slots.CODEC, 'wire_schema': slots.WIRE_SCHEMA}
    for position, item in zip([0, 2, 79], projection['claims']):
        assert set(item) == {'key', 'kind', 'statement', 'span_refs', 'status', 'explanation'}
        assert {key: value for key, value in item.items() if key != 'key'} == claim(position)
        assert item['key'] == 'mc_' + digest({**context, 'path': f'/slots/slot_{position + 1:03d}/claim'})[:32]
        assert item['key'] != 'mc_' + digest({**context, 'path': f'/claims/{position}'})[:32]
    assert len({item['key'] for item in projection['claims']}) == 3
    assert (wire, inputs) == before
    assert subject.check_map_projection(projection, wire, **inputs) == projection


def test_original_http_journal_arguments_wire_value_and_codec_hash_domains_remain_explicit():
    wire, inputs = scene(); value = subject.build_map_projection(wire, **inputs)
    decoded = slots.decode(wire, arguments_sha256=inputs['metadata']['arguments_sha256'])
    assert value['http_body_sha256'] == inputs['metadata']['response_body_sha256']
    # Equal byte digests for a valid UTF-8 body do not merge the two identities.
    assert value['journal_wire_sha256'] == inputs['descriptor']['wire_sha256'] == value['http_body_sha256']
    assert value['arguments_sha256'] == inputs['metadata']['arguments_sha256'] != value['wire_value_hash']
    assert value['wire_value_hash'] == digest(wire)
    assert value['view_hash'] == digest(decoded['view']) and value['codec_hash'] == decoded['codec_hash']
    assert value['tool_metadata_hash'] == digest(inputs['metadata'])
    assert value['evidence_profile_hash'] == digest(inputs['evidence_profile'])
    assert value['journal_descriptor_hash'] == inputs['descriptor']['descriptor_hash']
    assert value['projection_hash'] == digest({key: item for key, item in value.items() if key != 'projection_hash'})
    assert not {'wire', 'view', 'arguments_projection'} & set(value)


@pytest.mark.parametrize('positions', [(), tuple(range(80))])
def test_empty_and_eighty_slots_are_preserved_without_invented_or_dropped_claims(positions):
    wire, inputs = scene(positions); value = subject.build_map_projection(wire, **inputs)
    assert len(value['claims']) == len(positions) and value['slot_positions'] == list(positions)


@pytest.mark.parametrize('field', ['actor_id', 'workspace_id', 'draft_id', 'reserved_call_id', 'source_hash',
    'catalog_hash', 'generation_hash', 'dispatch_hash', 'messages_hash', 'stage'])
def test_binding_changes_cannot_replace_frozen_original_scope(field):
    wire, inputs = scene()
    inputs['binding'][field] = identity(99) if field.endswith('_id') else 'map-2' if field == 'stage' else 'e' * 64
    with pytest.raises(ValueError): subject.build_map_projection(wire, **inputs)


@pytest.mark.parametrize('field', ['source_hash', 'catalog_hash'])
def test_resealed_matching_bindings_cannot_change_actual_frozen_catalog(field):
    wire, inputs = scene()
    for name in ('binding', 'expected_binding'): inputs[name][field] = 'e' * 64
    with pytest.raises(ValueError): subject.build_map_projection(wire, **inputs)


@pytest.mark.parametrize('mode', ['outside_batch', 'missing_source', 'duplicate_allowlist', 'damaged_offset', 'damaged_text', 'old_array_88'])
def test_exact_span_allowlist_offsets_source_text_and_old_failed_wire_are_not_repaired(mode):
    wire, inputs = scene()
    if mode == 'outside_batch': wire['slots']['slot_001']['claim']['span_refs'] = ['w:p2/s1']
    elif mode == 'missing_source': wire['slots']['slot_001']['claim']['span_refs'] = ['w:p999/s1']
    elif mode == 'duplicate_allowlist': inputs['allowed_span_refs'] *= 2
    elif mode == 'damaged_offset': inputs['catalog']['index']['w:p1/s1']['start'] += 1
    elif mode == 'damaged_text': inputs['catalog']['index']['w:p1/s1']['text'] += '改字'
    else: wire = {'schema': 'report-evidence-map-v3', 'claims': [claim(i) for i in range(88)]}
    before = copy.deepcopy((wire, inputs))
    with pytest.raises(ValueError): subject.build_map_projection(wire, **inputs)
    assert (wire, inputs) == before


@pytest.mark.parametrize('field,bad', [('delivery', 'deepseek-strict-map-tool-v1'), ('http_status', 400),
    ('response_choice_count', 2), ('response_choice_count', True), ('tool_call_count', 2), ('tool_call_count', True),
    ('tool_name', 'submit_report_evidence_reduction'), ('tool_type', 'other'), ('tool_call_id', ' '),
    ('finish_reason', 'stop'), ('envelope_valid', False), ('envelope_errors', ['bad']),
    ('response_body_sha256', 'e' * 64), ('response_body_bytes', True), ('arguments_sha256', 'not_sha'),
    ('arguments_encoding', 'other'), ('usage_available', 0), ('usage_present', 0)])
def test_exact_closed_tool_metadata_and_original_body_binding_fail_closed(field, bad):
    wire, inputs = scene(); inputs['metadata'][field] = bad
    with pytest.raises(ValueError): subject.build_map_projection(wire, **inputs)


@pytest.mark.parametrize('field,bad', [('stage', 'map-v4'), ('version', 'cloud-evidence-2026-10-17/map-v4'),
    ('transport_timeout_seconds', 600), ('max_output_tokens', 8193), ('max_output_tokens', True),
    ('max_images', True), ('context_token_bound', 999999), ('full_report', False), ('long_context_verified', False)])
def test_profile_never_falls_back_to_old_map_or_reduce_or_noninteger_values(field, bad):
    wire, inputs = scene(); inputs['evidence_profile'][field] = bad
    with pytest.raises(ValueError): subject.build_map_projection(wire, **inputs)


@pytest.mark.parametrize('mode', ['missing', 'unknown', 'hash', 'foreign_binding', 'wire_size', 'quarantine', 'bytes_kind'])
def test_descriptor_integrity_scope_and_consumability_are_required(mode):
    wire, inputs = scene(); descriptor = inputs['descriptor']
    if mode == 'missing': del descriptor['receipt_sha256']
    elif mode == 'unknown': descriptor['extra'] = 'cannot ignore'
    elif mode == 'hash': descriptor['descriptor_hash'] = 'e' * 64
    elif mode == 'foreign_binding': descriptor['binding']['actor_id'] = identity(99)
    elif mode == 'wire_size': descriptor['wire_bytes'] += 1
    elif mode == 'quarantine': descriptor.update(consumable=False, quarantine_reasons=['receipt_invalid'])
    else: descriptor.update(wire_kind='bytes', encoding='opaque-bytes-v1')
    if mode not in {'missing', 'unknown', 'hash'}:
        descriptor['descriptor_hash'] = digest({key: value for key, value in descriptor.items() if key != 'descriptor_hash'})
    with pytest.raises(ValueError): subject.build_map_projection(wire, **inputs)


@pytest.mark.parametrize('mode', ['claim', 'slot_positions', 'slot_bool', 'args_hash', 'wire_hash', 'view_hash', 'codec_hash', 'metadata', 'profile', 'binding', 'extra'])
def test_public_resealing_cannot_replace_original_wire_or_frozen_projection_context(mode):
    wire, inputs = scene(); value = subject.build_map_projection(wire, **inputs)
    if mode == 'claim': value['claims'][0]['statement'] = 'changed'
    elif mode == 'slot_positions': value['slot_positions'] = [1, 3, 78]
    elif mode == 'slot_bool': value['slot_positions'][0] = False
    elif mode == 'metadata': value['tool_metadata']['tool_name'] = 'another_tool'
    elif mode == 'profile': value['evidence_profile']['stage'] = 'map-v4'
    elif mode == 'binding': value['binding']['actor_id'] = identity(99)
    elif mode == 'extra': value['extra'] = True
    else: value[{'args_hash': 'arguments_sha256', 'wire_hash': 'wire_value_hash', 'view_hash': 'view_hash', 'codec_hash': 'codec_hash'}[mode]] = 'e' * 64
    value['projection_hash'] = digest({key: item for key, item in value.items() if key != 'projection_hash'})
    with pytest.raises(ValueError): subject.check_map_projection(value, wire, **inputs)


def test_unavailable_usage_does_not_turn_valid_semantic_values_into_failure():
    wire, inputs = scene()
    assert inputs['metadata']['usage_available'] is inputs['metadata']['usage_present'] is False
    value = subject.build_map_projection(wire, **inputs)
    assert len(value['claims']) == 3
    inputs['metadata']['usage_present'] = True
    assert len(subject.build_map_projection(wire, **inputs)['claims']) == 3


def test_final_capacity_includes_seal_and_does_not_mutate_original_value(monkeypatch):
    wire, inputs = scene(); value = subject.build_map_projection(wire, **inputs)
    size = len(subject._bytes(value))
    monkeypatch.setattr(subject, 'MAX_PROJECTION_BYTES', size - 1)
    before = copy.deepcopy((wire, inputs))
    with pytest.raises(ValueError): subject.build_map_projection(wire, **inputs)
    assert (wire, inputs) == before
    monkeypatch.setattr(subject, 'MAX_PROJECTION_BYTES', size)
    assert subject.build_map_projection(wire, **inputs) == value
