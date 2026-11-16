"""Strict-Map delivery adapter, not a provider, executor or authorization gate.

The pipeline supplies an already checked frozen preview and original journal.
HTTP body bytes, journal UTF-8 surrogatepass bytes, and tool arguments bytes are
different identities. Only the original arguments enter the unchanged V3 wire
validator/ID derivation. This version deliberately has no Reduce adapter.
"""
import copy

from .contracts import digest
from .report_evidence_wire_v3 import build_map_projection
from .report_evidence_strict_map import (DELIVERY, METADATA_KEYS,
    inspect_strict_map_response, parse_strict_map_arguments)


GENERATION_V4 = 'report-evidence-map-generation-v4'
MAP_TOOL_PROJECTION = 'report-evidence-map-tool-projection-v4'


def applies(preview):
    return preview['map_preparation']['generation_contract']['schema'] == GENERATION_V4


def dispatch(preview, key, authorized):
    if key == 'reduce':
        raise ValueError('Strict Map V4 does not authorize Reduce')
    return {'schema': 'report-evidence-v4-dispatch-v1',
        'model_id': preview['body']['model_id'], 'authorized': authorized,
        'map_preparation_hash': preview['map_preparation']['preparation_hash'],
        'map_plan_hash': preview['map_preparation']['plan']['plan_hash'],
        'request_hash': authorized['request_hash']}


def inspect_response(pipeline, row, key, loaded, descriptor):
    """Verify original envelope metadata without parsing semantic arguments.

    Missing trustworthy usage is financial uncertainty, not a semantic veto.
    No caller may release the corresponding conservative reservation here.
    """
    if key == 'reduce' or type(loaded.get('content')) is not str:
        raise ValueError('Strict Map requires its original HTTP body string')
    receipt = loaded['receipt']; status = receipt.get('http_status')
    if type(status) is not int:
        raise ValueError('Strict Map original HTTP status missing')
    body = loaded['content'].encode('utf-8', errors='surrogateescape')
    inspected = inspect_strict_map_response(body, status)
    metadata = inspected['metadata']
    if (any(field not in receipt for field in METADATA_KEYS)
            or digest({field: receipt[field] for field in METADATA_KEYS}) != digest(metadata)):
        raise ValueError('Strict Map original HTTP/tool metadata differs')
    policy = copy.deepcopy(pipeline._spec(row, key)['input_policy'])
    if (policy.pop('single_batch', None) is not True
            or receipt.get('evidence_profile') != policy
            or metadata['delivery'] != DELIVERY):
        raise ValueError('Strict Map original delivery profile differs')
    return body, inspected


def parsed_response(pipeline, row, key, loaded, descriptor):
    body, inspected = inspect_response(pipeline, row, key, loaded, descriptor)
    value = parse_strict_map_arguments(body, inspected['metadata']['http_status'])
    return {'descriptor': descriptor, 'receipt': loaded['receipt'], 'value': value}


def build_projection(wire, *, binding, expected_binding, descriptor,
                     metadata, evidence_profile, catalog, allowed_span_refs):
    """Pure wrapper; arguments SHA is provided only by verified raw inspection.

    It proves deterministic consistency, not provider origin or permission.
    The caller must have verified the original HTTP body/receipt above.
    """
    if (type(metadata) is not dict or set(metadata) != set(METADATA_KEYS)
            or metadata['envelope_valid'] is not True or metadata['delivery'] != DELIVERY
            or type(evidence_profile) is not dict or evidence_profile.get('stage') != 'map-v4'
            or evidence_profile.get('version') != 'cloud-evidence-2026-10-17/map-v4'
            or descriptor['binding'] != binding or descriptor['binding_hash'] != digest(binding)):
        raise ValueError('Strict Map projection context differs')
    inner = build_map_projection(wire, binding=binding, expected_binding=expected_binding,
        wire_sha256=metadata['arguments_sha256'], catalog=catalog,
        allowed_span_refs=allowed_span_refs)
    value = {'schema': MAP_TOOL_PROJECTION, 'delivery': DELIVERY,
        'evidence_profile': copy.deepcopy(evidence_profile),
        'http_body_sha256': metadata['response_body_sha256'],
        'http_body_bytes': metadata['response_body_bytes'],
        'http_body_encoding': metadata['response_body_encoding'],
        'journal_wire_sha256': descriptor['wire_sha256'],
        'arguments_sha256': metadata['arguments_sha256'], 'arguments_bytes': metadata['arguments_bytes'],
        'tool_metadata': copy.deepcopy(metadata), 'tool_metadata_hash': digest(metadata),
        'arguments_projection': inner, 'claims': copy.deepcopy(inner['claims'])}
    return {**value, 'projection_hash': digest(value)}


def stage_projection(pipeline, row, key, catalog):
    stage = pipeline._stage(row, key); descriptor = pipeline._descriptor(row, key)
    loaded = pipeline.journal.read_response(pipeline._journal_root(),
        pipeline.journal_binding(row, key, stage['reserved_call_id']), descriptor)
    pipeline._check_receipt_identity(row, key, loaded['receipt'])
    body, inspected = inspect_response(pipeline, row, key, loaded, descriptor)
    wire = parse_strict_map_arguments(body, inspected['metadata']['http_status'])
    return build_projection(wire, binding=descriptor['binding'],
        expected_binding=pipeline.journal_binding(row, key, stage['reserved_call_id']),
        descriptor=descriptor, metadata=inspected['metadata'], evidence_profile=loaded['receipt']['evidence_profile'], catalog=catalog,
        allowed_span_refs=pipeline._authorized(row, key)['span_refs'])
