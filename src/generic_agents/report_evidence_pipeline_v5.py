"""V5 structured Map/Reduce adapter; never a provider or parallel executor.

The passed pipeline owns authenticated rows, immutable previews, journal IO,
ledger identity checks, leases, persistence and paid authorization. Every parse
here follows a read of the original durable journal. Original HTTP, journal,
arguments, slot-wire and internal codec values have separate hash identities.
This adapter cannot turn an old failed reply or a saved-facts view into V5.
"""
import copy
import hashlib
import json

from .contracts import digest
from .report_evidence_projection_v5 import build_map_projection
from .report_evidence_reduction_wire_v6 import build_reduction_projection, MAX_PROJECTION_BYTES
from .report_evidence_structured_tool_v2 import (
    DELIVERY, METADATA_KEYS, inspect_response as inspect_tool_response,
    parse_arguments,
)
from .report_full_reading_semantics import _safe


GENERATION_V5 = 'report-evidence-map-generation-v5'
GENERATION_V6 = 'report-evidence-map-generation-v6'
GENERATION_V7 = 'report-evidence-map-generation-v7'
REDUCE_TOOL_PROJECTION = 'report-evidence-reduction-tool-projection-v3'
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


def applies(preview):
    return (type(preview) is dict and type(preview.get('map_preparation')) is dict
        and type(preview['map_preparation'].get('generation_contract')) is dict
        and preview['map_preparation']['generation_contract'].get('schema') in {GENERATION_V5, GENERATION_V6, GENERATION_V7})


def native(preview):
    return applies(preview) and preview['map_preparation']['generation_contract']['schema'] == GENERATION_V7


def ref_arms(preview):
    return applies(preview) and preview['map_preparation']['generation_contract']['schema'] == GENERATION_V6


def tool(preview):
    _need(applies(preview), 'Unknown structured report generation')
    if native(preview):
        from . import report_evidence_qwen_json_schema_v1 as selected
    elif ref_arms(preview):
        from . import report_evidence_structured_tool_v3 as selected
    else:
        from . import report_evidence_structured_tool_v2 as selected
    return selected


def _stage_name(key, preview=None):
    if native(preview):
        return 'reduce-v5' if key == 'reduce' else 'map-v7'
    return ('reduce-v4' if key == 'reduce' else 'map-v6') if ref_arms(preview) else ('reduce-v3' if key == 'reduce' else 'map-v5')


def dispatch(preview, key, authorized):
    _need(applies(preview), 'Structured dispatch requires its original V5 preview')
    _need(type(authorized) is dict and type(authorized.get('request_hash')) is str,
        'Structured dispatch requires its full request hash')
    return {'schema': 'report-evidence-v5-dispatch-v1',
        'model_id': preview['body']['model_id'], 'authorized': authorized,
        'map_preparation_hash': preview['map_preparation']['preparation_hash'],
        'map_plan_hash': preview['map_preparation']['plan']['plan_hash'],
        'request_hash': authorized['request_hash']}


def _profile(pipeline, row, key):
    stage = _stage_name(key, row.data['preview'])
    policy = copy.deepcopy(pipeline._spec(row, key)['input_policy'])
    _need(type(policy) is dict and policy.pop('single_batch', None) is True
        and set(policy) == _PROFILE_KEYS, 'Structured response requires its closed single-response profile')
    _need(policy['stage'] == stage and policy['version'] == 'cloud-evidence-2026-10-17/' + stage
        and type(policy['max_input_bytes']) is int and policy['max_input_bytes'] == 900000
        and type(policy['transport_timeout_seconds']) is int and policy['transport_timeout_seconds'] == (600 if key == 'reduce' else 180)
        and type(policy['max_output_tokens']) is int and 128 <= policy['max_output_tokens'] <= (131072 if key == 'reduce' else 8192)
        and type(policy['max_images']) is int and policy['max_images'] == 0
        and type(policy['context_token_bound']) is int and policy['context_token_bound'] == 1000000
        and policy['full_report'] is True and policy['long_context_verified'] is True,
        'Structured response stage profile differs')
    return policy


def inspect_response(pipeline, row, key, loaded, descriptor):
    import sys
    return _inspect_scoped_response(pipeline, row, key, loaded, descriptor, sys.modules[__name__])


def _inspect_scoped_response(pipeline, row, key, loaded, descriptor, family):
    """Re-read envelope metadata, never semantic arguments, after durable IO.

    Invalid envelopes remain inspectable if their original journal is intact;
    parse_arguments alone applies the envelope/JSON adoption gate afterwards.
    Missing trustworthy usage is financial uncertainty, not a semantic veto.
    """
    _need(family.applies(row.data['preview']), 'Structured response requires its original V5 preview')
    _need(type(loaded) is dict and type(loaded.get('content')) is str and type(loaded.get('receipt')) is dict,
        'Structured response requires original journal text and receipt')
    journal = pipeline._journal_for(row, key)
    stage = pipeline._stage(row, key)
    expected = pipeline.journal_binding(row, key, stage['reserved_call_id'])
    expected = journal.JournalBinding.model_validate(expected).model_dump()
    _need(type(descriptor) is dict and set(descriptor) == _DESCRIPTOR_KEYS,
        'Structured journal descriptor shape differs')
    _need(descriptor['schema'] == 'report-evidence-journal-descriptor-v' + ('2' if key == 'reduce' else '1')
        and _bytes(descriptor['binding']) == _bytes(expected) and descriptor['binding_hash'] == digest(expected)
        and descriptor['descriptor_hash'] == digest({name: value for name, value in descriptor.items() if name != 'descriptor_hash'})
        and _bytes(loaded.get('descriptor')) == _bytes(descriptor)
        and descriptor['wire_kind'] == 'text' and descriptor['encoding'] == 'utf-8-surrogatepass-v1'
        and descriptor['consumable'] is True and descriptor['quarantine_reasons'] == []
        and descriptor['receipt_status'] == 'saved', 'Structured journal scope or integrity differs')
    content = loaded['content']
    journal_wire = content.encode('utf-8', errors='surrogatepass')
    _need(type(descriptor['wire_bytes']) is int and len(journal_wire) == descriptor['wire_bytes']
        and hashlib.sha256(journal_wire).hexdigest() == descriptor['wire_sha256'], 'Structured original journal bytes differ')
    body = content.encode('utf-8', errors='surrogateescape')
    receipt = loaded['receipt']; status = receipt.get('http_status')
    _need(type(status) is int, 'Structured original HTTP status missing')
    pipeline._check_receipt_identity(row, key, receipt)
    flags = {}
    if family.native(row.data['preview']):
        from . import report_evidence_native_bridge_v1 as bridge
        _need(set(receipt) == set(METADATA_KEYS) | {'usage', 'original_usage_shape', 'usage_unavailable',
            'usage_unavailable_reason', 'provider', 'model', 'request_id', 'call_id', 'evidence_profile',
            'response_complete', 'http_content_encoding', 'capture_hash'}
            and type(receipt['capture_hash']) is str and len(receipt['capture_hash']) == 64,
            'Native original receipt flags or shape missing')
        capture = bridge.load_verified(pipeline, row, key, receipt.get('capture_hash'))
        _need(capture['ready'] is True and capture['content_encoding_safe'] is True
            and capture['response_complete'] is True
            and capture['body_sha256'] == hashlib.sha256(body).hexdigest()
            and capture['body_bytes'] == len(body)
            and capture['http_status'] == status and capture['request_id'] == receipt['request_id']
            and receipt.get('response_complete') is True
            and receipt.get('http_content_encoding') == capture['http_content_encoding'],
            'Native original capture is incomplete or differs')
        flags = {'response_complete': True, 'http_content_encoding': capture['http_content_encoding']}
    inspected = family.tool(row.data['preview']).inspect_response(body, status, family._stage_name(key, row.data['preview']), **flags)
    metadata = inspected['metadata']
    _need(all(field in receipt for field in METADATA_KEYS)
        and _bytes({field: receipt[field] for field in METADATA_KEYS}) == _bytes(metadata),
        'Structured original tool metadata differs')
    policy = family._profile(pipeline, row, key)
    _need(type(receipt.get('evidence_profile')) is dict
        and _bytes(receipt['evidence_profile']) == _bytes(policy)
        and metadata['delivery'] == family.tool(row.data['preview']).DELIVERY, 'Structured original evidence profile differs')
    return body, inspected


def parsed_response(pipeline, row, key, loaded, descriptor):
    body, inspected = inspect_response(pipeline, row, key, loaded, descriptor)
    value = _parse(row.data['preview'], key, body, inspected)
    return {'descriptor': descriptor, 'receipt': loaded['receipt'], 'value': value}


def _parse(preview, key, body, inspected):
    flags = ({'response_complete': inspected['response_complete'],
        'http_content_encoding': inspected['http_content_encoding']} if native(preview) else {})
    return tool(preview).parse_arguments(body, inspected['metadata']['http_status'], _stage_name(key, preview), **flags)


def _read(pipeline, row, key):
    stage = pipeline._stage(row, key); descriptor = pipeline._descriptor(row, key)
    _need(type(descriptor) is dict and stage['reserved_call_id'] is not None,
        'Structured stage has no complete original reservation/journal')
    binding = pipeline.journal_binding(row, key, stage['reserved_call_id'])
    loaded = pipeline._journal_for(row, key).read_response(pipeline._journal_root(row, key), binding, descriptor)
    body, inspected = inspect_response(pipeline, row, key, loaded, descriptor)
    return descriptor, loaded, body, inspected, binding


def _map_input(pipeline, row, key, catalog):
    _need(key != 'reduce', 'A Map input cannot be a reduction')
    descriptor, loaded, body, inspected, expected = _read(pipeline, row, key)
    wire = _parse(row.data['preview'], key, body, inspected)
    allowed = pipeline._authorized(row, key)['span_refs']
    projection = build_map_projection(wire, binding=descriptor['binding'], expected_binding=expected,
        descriptor=descriptor, metadata=inspected['metadata'], evidence_profile=loaded['receipt']['evidence_profile'],
        catalog=catalog, allowed_span_refs=allowed, protocol_version=7 if native(row.data['preview']) else 6 if ref_arms(row.data['preview']) else 5)
    return {'wire': wire, 'binding': copy.deepcopy(descriptor['binding']), 'expected_binding': copy.deepcopy(expected),
        'descriptor': copy.deepcopy(descriptor), 'metadata': copy.deepcopy(inspected['metadata']),
        'evidence_profile': copy.deepcopy(loaded['receipt']['evidence_profile']),
        'allowed_span_refs': copy.deepcopy(allowed), 'projection': projection}


def map_inputs(pipeline, row, catalog):
    """Cold-read exactly every succeeded frozen Map, never a partial prefix."""
    _need(applies(row.data['preview']), 'Map collection requires its original V5 preview')
    chunks = row.data['preview']['map_preparation']['plan']['chunks']; stages = row.data['maps']
    _need(type(chunks) is list and 1 <= len(chunks) <= 24 and type(stages) is list
        and [chunk['chunk_id'] for chunk in chunks] == [f'map-{index + 1}' for index in range(len(chunks))]
        and [stage['chunk_id'] for stage in stages] == [chunk['chunk_id'] for chunk in chunks]
        and all(stage['status'] == 'succeeded' and stage['inflight'] is False for stage in stages),
        'Reduction requires every frozen Map exactly once in order')
    result = []
    for chunk, stage in zip(chunks, stages):
        item = _map_input(pipeline, row, chunk['chunk_id'], catalog)
        _need(_bytes(stage['journal']) == _bytes(item['descriptor'])
            and digest(item['projection']) == stage['result_hash']
            and type(stage['result_count']) is int and stage['result_count'] == len(item['projection']['claims']),
            'Map projection differs from its saved original stage')
        result.append(item)
    return result


def stage_projection(pipeline, row, key, catalog):
    if key != 'reduce':
        return _map_input(pipeline, row, key, catalog)['projection']
    from .report_evidence_reduction_v3 import reduction_inputs_v3, check_reduction_preparation
    preview = row.data['preview']; prep = preview['map_preparation']; cap = pipeline._cap(preview)
    maps = map_inputs(pipeline, row, catalog)
    authorized = pipeline._authorized(row, key)
    check_reduction_preparation(authorized, catalog, cap, prep, maps)
    inputs = reduction_inputs_v3(prep, catalog, cap, maps)
    _need(_bytes(inputs['scope']) == _bytes({'actor_id': row.actor_id, 'workspace_id': row.workspace_id, 'draft_id': row.id})
        and inputs['map_inputs_hash'] == authorized['map_inputs_hash']
        and inputs['input_order'] == authorized['input_order']
        and inputs['allowed_span_refs'] == [item[0] for item in authorized['payload']['spans']],
        'Reduction original Map scope, inputs or frozen evidence differs')
    descriptor, loaded, body, inspected, expected = _read(pipeline, row, key)
    # Enforce the original envelope before extracting its exact arguments bytes.
    _parse(preview, key, body, inspected)
    arguments = inspected['arguments'].encode('utf-8', errors='strict')
    reduced = build_reduction_projection(arguments, catalog=catalog,
        allowed_span_refs=inputs['allowed_span_refs'], context=inputs['context'],
        input_order=inputs['input_order'], input_items=inputs['input_items'])
    _need(reduced['wire_bytes_sha256'] == inspected['metadata']['arguments_sha256']
        and reduced['wire_bytes'] == inspected['metadata']['arguments_bytes'], 'Reduction original arguments bytes differ')
    value = {'schema': REDUCE_TOOL_PROJECTION, 'delivery': tool(preview).DELIVERY,
        'binding': copy.deepcopy(expected), 'binding_hash': digest(expected),
        'journal_descriptor_hash': descriptor['descriptor_hash'], 'journal_wire_sha256': descriptor['wire_sha256'],
        'http_body_sha256': inspected['metadata']['response_body_sha256'],
        'http_body_bytes': inspected['metadata']['response_body_bytes'],
        'http_body_encoding': inspected['metadata']['response_body_encoding'],
        'arguments_sha256': inspected['metadata']['arguments_sha256'], 'arguments_bytes': len(arguments),
        'tool_metadata': copy.deepcopy(inspected['metadata']), 'tool_metadata_hash': digest(inspected['metadata']),
        'evidence_profile': copy.deepcopy(loaded['receipt']['evidence_profile']),
        'evidence_profile_hash': digest(loaded['receipt']['evidence_profile']),
        'reduction_preparation_hash': authorized['preparation_hash'],
        'map_preparation_hash': prep['preparation_hash'], 'map_plan_hash': prep['plan']['plan_hash'],
        'map_inputs_hash': inputs['map_inputs_hash'], 'arguments_projection_hash': reduced['projection_hash'],
        'reduction_projection_metadata': {name: copy.deepcopy(item) for name, item in reduced.items()
            if name not in {'result', 'reconciliation'}},
        'result': copy.deepcopy(reduced['result']), 'reconciliation': copy.deepcopy(reduced['reconciliation'])}
    value['projection_hash'] = digest(value)
    _need(len(_bytes(value)) <= MAX_PROJECTION_BYTES, 'Structured reduction wrapper exceeds unchanged projection capacity')
    return value
