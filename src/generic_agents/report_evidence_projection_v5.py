"""Pure slot-origin Map projection, not an HTTP/journal authenticity verifier.

The caller must first durably read and validate the actual original HTTP body,
receipt and arguments bytes. Metadata, parsed wire and a matching public hash
cannot establish their origin. This module independently enforces the closed
protocol, exact expected binding, descriptor, evidence profile and source scope.

Claims preserve the original five fields. Generated IDs address original fixed
slot paths, not the compact internal V3 array. Original wire/view are not copied
into persisted projections; cold verification reconstructs both from the caller's
verified original arguments. No IO, model, budget, grant, repair or fallback.
"""
import copy
import json
import re

from .contracts import digest
from .report_evidence_journal import JournalBinding
from .report_evidence_map_slots_v5 import CODEC, WIRE_SCHEMA, SLOT_NAMES, decode
from .report_evidence_plan import check_evidence_source, resolve_evidence, validate_map_result
from .report_evidence_strict_map import METADATA_KEYS
from .report_evidence_wire_v3 import MAX_PROJECTION_BYTES
from .report_full_reading_semantics import _safe


MAP_PROJECTION = 'report-evidence-map-slots-projection-v5'
DERIVATION = 'report-evidence-slot-ids-v1'
DELIVERY = 'deepseek-strict-report-tool-v2'
TOOL_NAME = 'submit_report_evidence_map_slots'
MAX_RESPONSE_BYTES = 256 * 1024
_HASH = re.compile(r'^[a-f0-9]{64}$')
_DESCRIPTOR_KEYS = {'schema', 'binding', 'binding_hash', 'wire_kind', 'encoding',
    'wire_bytes', 'wire_sha256', 'receipt_status', 'receipt_bytes', 'receipt_sha256',
    'consumable', 'quarantine_reasons', 'envelope_bytes', 'envelope_sha256', 'descriptor_hash'}
_PROFILE_KEYS = {'version', 'stage', 'max_input_bytes', 'transport_timeout_seconds',
    'max_output_tokens', 'max_images', 'context_token_bound', 'full_report', 'long_context_verified'}


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _bytes(value):
    _safe(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8', errors='strict')


def _hash(value):
    return type(value) is str and bool(_HASH.fullmatch(value))


def _binding(binding, expected_binding, catalog, allowed_span_refs):
    _need(type(binding) is dict and type(expected_binding) is dict, 'Map binding and expectation required')
    actual = JournalBinding.model_validate(binding).model_dump()
    expected = JournalBinding.model_validate(expected_binding).model_dump()
    _need(_bytes(actual) == _bytes(binding) == _bytes(expected) == _bytes(expected_binding),
        'Map binding differs from frozen expectation')
    _need(re.fullmatch(r'map-(?:[1-9]|1[0-9]|2[0-4])', actual['stage']) is not None,
        'Map binding has a different stage')
    check_evidence_source(catalog)
    resolve_evidence(catalog, allowed_span_refs, allowed_span_refs)
    _need(actual['source_hash'] == catalog['descriptor']['source_hash']
        and actual['catalog_hash'] == catalog['descriptor']['catalog_hash'], 'Map source/catalog differs')
    return actual


def _descriptor(value, binding):
    _need(type(value) is dict and set(value) == _DESCRIPTOR_KEYS,
        'Map journal descriptor shape differs')
    _safe(value)
    _need(value['schema'] == 'report-evidence-journal-descriptor-v1'
        and _bytes(value['binding']) == _bytes(binding) and value['binding_hash'] == digest(binding)
        and value['descriptor_hash'] == digest({key: item for key, item in value.items() if key != 'descriptor_hash'})
        and value['wire_kind'] == 'text' and value['encoding'] == 'utf-8-surrogatepass-v1'
        and type(value['wire_bytes']) is int and 1 <= value['wire_bytes'] <= MAX_RESPONSE_BYTES
        and value['receipt_status'] == 'saved' and type(value['receipt_bytes']) is int and value['receipt_bytes'] > 0
        and value['consumable'] is True and value['quarantine_reasons'] == []
        and type(value['envelope_bytes']) is int and 1 <= value['envelope_bytes'] <= 8192
        and all(_hash(value[field]) for field in ('wire_sha256', 'receipt_sha256', 'envelope_sha256', 'descriptor_hash')),
        'Map journal descriptor or binding differs')


def _metadata(value, descriptor, protocol_version=5):
    if protocol_version == 7:
        return _native_metadata(value, descriptor)
    _need(type(value) is dict and set(value) == set(METADATA_KEYS), 'Map tool metadata shape differs')
    _safe(value)
    _need(value['delivery'] == (DELIVERY if protocol_version == 5 else 'deepseek-strict-report-tool-v3') and type(value['http_status']) is int and value['http_status'] == 200
        and value['finish_reason'] == 'tool_calls' and type(value['response_choice_count']) is int and value['response_choice_count'] == 1
        and type(value['tool_call_count']) is int and value['tool_call_count'] == 1
        and value['tool_type'] == 'function' and value['tool_name'] == TOOL_NAME
        and type(value['tool_call_id']) is str and bool(value['tool_call_id'].strip())
        and value['envelope_valid'] is True and value['envelope_errors'] == []
        and type(value['usage_available']) is bool and type(value['usage_present']) is bool
        and value['response_body_encoding'] == 'utf-8-surrogateescape-v1' and value['arguments_encoding'] == 'utf-8'
        and type(value['response_body_bytes']) is int and 1 <= value['response_body_bytes'] <= MAX_RESPONSE_BYTES
        and type(value['arguments_bytes']) is int and 1 <= value['arguments_bytes'] <= value['response_body_bytes']
        and all(_hash(value[field]) for field in ('response_body_sha256', 'tool_calls_hash', 'arguments_sha256'))
        # A consumable successful envelope is strict UTF-8, so the journal's
        # surrogatepass re-encoding must match the original body bytes exactly.
        and value['response_body_sha256'] == descriptor['wire_sha256']
        and value['response_body_bytes'] == descriptor['wire_bytes'], 'Map tool envelope or original body binding differs')


def _profile(value, protocol_version=5):
    _need(type(value) is dict and set(value) == _PROFILE_KEYS, 'Map evidence profile shape differs')
    _safe(value)
    stage = {5: 'map-v5', 6: 'map-v6', 7: 'map-v7'}[protocol_version]
    _need(value['version'] == 'cloud-evidence-2026-10-17/' + stage and value['stage'] == stage
        and type(value['max_input_bytes']) is int and value['max_input_bytes'] == 900000
        and type(value['transport_timeout_seconds']) is int and value['transport_timeout_seconds'] == 180
        and type(value['max_output_tokens']) is int and 128 <= value['max_output_tokens'] <= 8192
        and type(value['max_images']) is int and value['max_images'] == 0
        and type(value['context_token_bound']) is int and value['context_token_bound'] == 1000000
        and value['full_report'] is True and value['long_context_verified'] is True,
        'Map evidence profile differs from the fixed stage policy')


def build_map_projection(wire, *, binding, expected_binding, descriptor,
                         metadata, evidence_profile, catalog, allowed_span_refs, protocol_version=5):
    _need(type(protocol_version) is int and protocol_version in {5, 6, 7}, 'Unknown Map projection protocol')
    actual = _binding(binding, expected_binding, catalog, allowed_span_refs)
    _descriptor(descriptor, actual); _metadata(metadata, descriptor, protocol_version); _profile(evidence_profile, protocol_version)
    decoded = decode(wire, arguments_sha256=metadata['arguments_sha256'])
    id_context = {'domain': DERIVATION, 'binding_hash': digest(actual),
        'arguments_sha256': metadata['arguments_sha256'], 'codec': CODEC, 'wire_schema': WIRE_SCHEMA}
    claims = []
    for position, claim in zip(decoded['slot_positions'], decoded['view']['claims']):
        path = '/slots/' + SLOT_NAMES[position] + '/claim'
        key = 'mc_' + digest({**id_context, 'path': path})[:32]
        claims.append({'key': key, **copy.deepcopy(claim)})
    internal = {'schema': 'report-evidence-map-v1', 'claims': claims}
    _need(_bytes(validate_map_result(internal, catalog, allowed_span_refs)) == _bytes(internal),
        'Internal Map validation view changed')
    value = {'schema': MAP_PROJECTION, 'delivery': metadata['delivery'], 'wire_schema': WIRE_SCHEMA,
        'codec': CODEC, 'derivation_version': DERIVATION,
        'binding': copy.deepcopy(actual), 'binding_hash': digest(actual),
        'journal_descriptor_hash': descriptor['descriptor_hash'],
        'journal_wire_sha256': descriptor['wire_sha256'],
        'http_body_sha256': metadata['response_body_sha256'], 'http_body_bytes': metadata['response_body_bytes'],
        'http_body_encoding': metadata['response_body_encoding'],
        'arguments_sha256': metadata['arguments_sha256'], 'arguments_bytes': metadata['arguments_bytes'],
        'wire_value_hash': decoded['wire_value_hash'], 'view_hash': decoded['view_hash'],
        'codec_hash': decoded['codec_hash'], 'slot_positions': copy.deepcopy(decoded['slot_positions']),
        'tool_metadata': copy.deepcopy(metadata), 'tool_metadata_hash': digest(metadata),
        'evidence_profile': copy.deepcopy(evidence_profile), 'evidence_profile_hash': digest(evidence_profile),
        'allowed_span_refs_hash': digest(allowed_span_refs), 'claims': claims}
    sealed = {**value, 'projection_hash': digest(value)}
    _need(len(_bytes(sealed)) <= MAX_PROJECTION_BYTES, 'Map projection exceeds unchanged capacity')
    return sealed


def check_map_projection(projection, wire, **expected_inputs):
    expected = build_map_projection(wire, **expected_inputs)
    _need(type(projection) is dict and _bytes(projection) == _bytes(expected),
        'Map projection does not rebuild from original slots and frozen context')
    return expected


def _native_metadata(value, descriptor):
    _need(type(value) is dict and set(value) == set(METADATA_KEYS), 'Native Map metadata shape differs')
    _safe(value)
    _need(value['delivery'] == 'qwen-tokenplan-native-json-schema-v1'
        and type(value['http_status']) is int and value['http_status'] == 200
        and value['finish_reason'] == 'stop'
        and type(value['response_choice_count']) is int and value['response_choice_count'] == 1
        and type(value['tool_call_count']) is int and value['tool_call_count'] == 0
        and all(value[key] is None for key in ('tool_call_id', 'tool_name', 'tool_type', 'tool_calls_hash'))
        and value['envelope_valid'] is True and value['envelope_errors'] == []
        and type(value['usage_present']) is bool and type(value['usage_available']) is bool
        and value['response_body_encoding'] == 'utf-8-surrogateescape-v1' and value['arguments_encoding'] == 'utf-8'
        and type(value['response_body_bytes']) is int and 1 <= value['response_body_bytes'] <= MAX_RESPONSE_BYTES
        and type(value['arguments_bytes']) is int and 1 <= value['arguments_bytes'] <= value['response_body_bytes']
        and all(_hash(value[field]) for field in ('response_body_sha256', 'arguments_sha256'))
        and value['response_body_sha256'] == descriptor['wire_sha256']
        and value['response_body_bytes'] == descriptor['wire_bytes'], 'Native Map original body/content differs')
