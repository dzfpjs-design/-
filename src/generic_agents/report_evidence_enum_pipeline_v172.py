"""Explicit array/native family within the existing authenticated report runner.

This adapter never reserves, invokes a provider, invents a Job or reinterprets
old failed replies. It reads the same original capture/journal through its
caller before parsing; hashes do not establish semantic entailment or grants.
"""
import copy
import hashlib
import json
import sys

from .contracts import digest
from .report_evidence_plan import validate_map_result, resolve_evidence
from .report_evidence_wire_v3 import MapResultWireV3
from .report_full_reading_semantics import _safe
from . import report_claim_enum_native_v172 as native_tool
from . import report_evidence_enum_map_v172 as enum_maps
from . import report_evidence_pipeline_v5 as legacy

GENERATION = 'report-claim-enum-map-generation-v172'
MAP_PROJECTION = 'report-claim-enum-map-projection-v172'
REDUCE_PROJECTION = 'report-claim-enum-reduce-wrapper-v172'


def _bytes(value):
    _safe(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')


def _need(condition, message='新报告的原调用、消息、来源或结构无法核对；原记录保留'):
    if not condition:
        raise ValueError(message)


def applies(preview):
    return (type(preview) is dict and type(preview.get('map_preparation')) is dict
        and type(preview['map_preparation'].get('generation_contract')) is dict
        and preview['map_preparation']['generation_contract'].get('schema') == GENERATION)


def native(preview):
    return applies(preview)


def ref_arms(preview):
    return False


def tool(preview):
    _need(applies(preview))
    return native_tool


def _stage_name(key, preview=None):
    _need(applies(preview))
    return 'reduce-v172' if key == 'reduce' else 'map-v172'


def dispatch(preview, key, authorized):
    _need(applies(preview) and type(authorized) is dict and type(authorized.get('request_hash')) is str)
    return {'schema': 'report-claim-enum-dispatch-v172', 'model_id': preview['body']['model_id'],
        'authorized': copy.deepcopy(authorized), 'request_hash': authorized['request_hash'],
        'map_preparation_hash': preview['map_preparation']['preparation_hash'],
        'map_plan_hash': preview['map_preparation']['plan']['plan_hash']}


def _profile(pipeline, row, key):
    from .cloud_evidence_profiles import evidence_policy
    policy = copy.deepcopy(pipeline._spec(row, key)['input_policy'])
    _need(policy.pop('single_batch', None) is True)
    stage = _stage_name(key, row.data['preview'])
    expected = evidence_policy(pipeline._cap(row.data['preview']).data['execution'], stage)
    _need(_bytes(policy) == _bytes(expected))
    return policy


def inspect_response(pipeline, row, key, loaded, descriptor):
    return legacy._inspect_scoped_response(pipeline, row, key, loaded, descriptor, sys.modules[__name__])


def _parse(preview, key, body, inspected):
    return tool(preview).parse_arguments(body, inspected['metadata']['http_status'], _stage_name(key, preview),
        response_complete=inspected['response_complete'], http_content_encoding=inspected['http_content_encoding'])


def parsed_response(pipeline, row, key, loaded, descriptor):
    body, inspected = inspect_response(pipeline, row, key, loaded, descriptor)
    return {'descriptor': descriptor, 'receipt': loaded['receipt'], 'value': _parse(row.data['preview'], key, body, inspected)}


def _read(pipeline, row, key):
    stage = pipeline._stage(row, key); descriptor = pipeline._descriptor(row, key)
    _need(type(descriptor) is dict and stage['reserved_call_id'] is not None)
    expected = pipeline.journal_binding(row, key, stage['reserved_call_id'])
    loaded = pipeline._journal_for(row, key).read_response(pipeline._stage_journal_root(row, key), expected, descriptor)
    body, inspected = inspect_response(pipeline, row, key, loaded, descriptor)
    return descriptor, loaded, body, inspected, expected


def build_map_projection(wire, *, binding, expected_binding, descriptor, metadata,
                         evidence_profile, catalog, allowed_span_refs, map_preparation,
                         http_body, response_complete, http_content_encoding, published_model_hash):
    from .report_evidence_journal import JournalBinding
    actual = JournalBinding.model_validate(binding).model_dump()
    _need(_bytes(actual) == _bytes(binding) == _bytes(expected_binding)
        and binding['stage'] != 'reduce' and binding['source_hash'] == catalog['descriptor']['source_hash']
        and binding['catalog_hash'] == catalog['descriptor']['catalog_hash'])
    _need(type(descriptor) is dict and set(descriptor) == legacy._DESCRIPTOR_KEYS
        and descriptor['schema'] == 'report-evidence-journal-descriptor-v1'
        and descriptor['descriptor_hash'] == digest({k: v for k, v in descriptor.items() if k != 'descriptor_hash'})
        and _bytes(descriptor['binding']) == _bytes(binding) and descriptor['binding_hash'] == digest(binding)
        and descriptor['consumable'] is True and descriptor['quarantine_reasons'] == []
        and descriptor['receipt_status'] == 'saved' and descriptor['wire_kind'] == 'text'
        and descriptor['encoding'] == 'utf-8-surrogatepass-v1')
    _need(type(http_body) is str and type(response_complete) is bool)
    raw_body = http_body.encode('utf-8', 'strict')
    _need(type(descriptor['wire_bytes']) is int and descriptor['wire_bytes'] == len(raw_body)
        and descriptor['wire_sha256'] == hashlib.sha256(raw_body).hexdigest())
    inspected = native_tool.inspect_response(raw_body, metadata['http_status'], 'map-v172',
        response_complete=response_complete, http_content_encoding=http_content_encoding)
    _need(inspected['metadata']['envelope_valid'] is True and _bytes(metadata) == _bytes(inspected['metadata']))
    original_wire = native_tool.parse_arguments(raw_body, metadata['http_status'], 'map-v172',
        response_complete=response_complete, http_content_encoding=http_content_encoding)
    _need(_bytes(wire) == _bytes(original_wire))
    _need(type(map_preparation) is dict and map_preparation['generation_contract']['schema'] == GENERATION
        and map_preparation['catalog_descriptor'] == catalog['descriptor'])
    chunks = [c for c in map_preparation['plan']['chunks'] if c['chunk_id'] == binding['stage']]
    _need(len(chunks) == 1 and chunks[0]['span_refs'] == allowed_span_refs
        and chunks[0]['messages_hash'] == binding['messages_hash'])
    actual_spec = enum_maps.stage_spec(map_preparation, binding['stage'])
    _need(_bytes(actual_spec['allowed_span_refs']) == _bytes(allowed_span_refs)
        and actual_spec['allowed_span_refs_hash'] == digest(allowed_span_refs)
        and actual_spec['chunk_id'] == binding['stage'])
    native_tool.check_schema_proof(actual_spec['schema_proof'], actual_spec['output_schema'], 'map-v172')
    _need(type(published_model_hash) is str and len(published_model_hash) == 64
        and all(char in '0123456789abcdef' for char in published_model_hash)
        and binding['generation_hash'] == digest({'published_model_hash': published_model_hash,
            'contract': actual_spec}))
    profile = copy.deepcopy(actual_spec['input_policy'])
    _need(profile.pop('single_batch', None) is True and _bytes(profile) == _bytes(evidence_profile)
        and type(metadata) is dict and set(metadata) == set(native_tool.METADATA_KEYS)
        and metadata['envelope_valid'] is True and metadata['delivery'] == native_tool.DELIVERY
        and metadata['envelope_errors'] == [] and metadata['http_status'] == 200
        and metadata['finish_reason'] == 'stop')
    checked = MapResultWireV3.model_validate(wire).model_dump()
    _need(_bytes(checked) == _bytes(wire))
    resolve_evidence(catalog, allowed_span_refs, allowed_span_refs)
    temporary = {'schema': 'report-evidence-map-v1', 'claims': [
        {'key': 'mc_' + digest({'family': MAP_PROJECTION, 'binding': binding, 'position': i})[:32], **copy.deepcopy(c)}
        for i, c in enumerate(checked['claims'])]}
    _need(_bytes(validate_map_result(temporary, catalog, allowed_span_refs)) == _bytes(temporary))
    value = {'schema': MAP_PROJECTION, 'delivery': native_tool.DELIVERY,
        'response_complete': response_complete, 'http_content_encoding': http_content_encoding,
        'binding': copy.deepcopy(binding), 'binding_hash': digest(binding),
        'journal_descriptor_hash': descriptor['descriptor_hash'], 'journal_wire_sha256': descriptor['wire_sha256'],
        'http_body_sha256': metadata['response_body_sha256'], 'http_body_bytes': metadata['response_body_bytes'],
        'http_body_encoding': metadata['response_body_encoding'],
        'arguments_sha256': metadata['arguments_sha256'], 'arguments_bytes': metadata['arguments_bytes'],
        'tool_metadata': copy.deepcopy(metadata), 'tool_metadata_hash': digest(metadata),
        'evidence_profile': copy.deepcopy(evidence_profile), 'evidence_profile_hash': digest(evidence_profile),
        'map_preparation_hash': map_preparation['preparation_hash'], 'map_plan_hash': map_preparation['plan']['plan_hash'],
        'stage_spec_hash': digest(actual_spec), 'output_schema_hash': digest(actual_spec['output_schema']),
        'schema_proof_hash': digest(actual_spec['schema_proof']),
        'published_model_hash': published_model_hash,
        'allowed_span_refs_hash': digest(allowed_span_refs), 'wire': copy.deepcopy(wire),
        'wire_value_hash': digest(wire), 'claims': copy.deepcopy(wire['claims'])}
    _need(len(_bytes(value)) <= 8 * 1024 * 1024)
    return {**value, 'projection_hash': digest(value)}


def check_map_projection(projection, wire, **expected):
    value = build_map_projection(wire, **expected)
    _need(_bytes(projection) == _bytes(value))
    return value


def _map_input(pipeline, row, key, catalog):
    _need(key != 'reduce' and applies(row.data['preview']))
    descriptor, loaded, body, inspected, expected = _read(pipeline, row, key)
    wire = _parse(row.data['preview'], key, body, inspected)
    arguments = inspected['arguments'].encode('utf-8', 'strict')
    _need(hashlib.sha256(arguments).hexdigest() == inspected['metadata']['arguments_sha256']
        and len(arguments) == inspected['metadata']['arguments_bytes'])
    allowed = pipeline._authorized(row, key)['span_refs']
    item = {'wire': wire, 'binding': copy.deepcopy(descriptor['binding']), 'expected_binding': copy.deepcopy(expected),
        'descriptor': copy.deepcopy(descriptor), 'metadata': copy.deepcopy(inspected['metadata']),
        'evidence_profile': copy.deepcopy(loaded['receipt']['evidence_profile']), 'allowed_span_refs': copy.deepcopy(allowed),
        'http_body': body.decode('utf-8', 'strict'), 'response_complete': inspected['response_complete'],
        'http_content_encoding': inspected['http_content_encoding'],
        'published_model_hash': row.data['preview']['published_model_hash']}
    item['projection'] = build_map_projection(**item, catalog=catalog, map_preparation=row.data['preview']['map_preparation'])
    return item


def map_inputs(pipeline, row, catalog):
    _need(applies(row.data['preview']))
    chunks = row.data['preview']['map_preparation']['plan']['chunks']; stages = row.data['maps']
    _need(type(chunks) is list and 1 <= len(chunks) <= 24 and len(chunks) == len(stages)
        and [c['chunk_id'] for c in chunks] == [f'map-{i+1}' for i in range(len(chunks))]
        and [c['chunk_id'] for c in chunks] == [s['chunk_id'] for s in stages]
        and all(s['status'] == 'succeeded' and s['inflight'] is False for s in stages))
    result = []
    for chunk, stage in zip(chunks, stages):
        item = _map_input(pipeline, row, chunk['chunk_id'], catalog)
        _need(_bytes(stage['journal']) == _bytes(item['descriptor']) and digest(item['projection']) == stage['result_hash']
            and type(stage['result_count']) is int and stage['result_count'] == len(item['wire']['claims']))
        result.append(item)
    return result


def preflight_projection(pipeline, row, key, wire, descriptor, body, inspected, binding, receipt, catalog):
    if key != 'reduce':
        return build_map_projection(wire, binding=binding, expected_binding=binding, descriptor=descriptor,
            metadata=inspected['metadata'], evidence_profile=receipt['evidence_profile'], catalog=catalog,
            allowed_span_refs=pipeline._authorized(row, key)['span_refs'], map_preparation=row.data['preview']['map_preparation'],
            http_body=body.decode('utf-8', 'strict'), response_complete=inspected['response_complete'],
            http_content_encoding=inspected['http_content_encoding'],
            published_model_hash=row.data['preview']['published_model_hash'])
    from . import report_evidence_enum_reduction_v172 as reducer
    from .report_evidence_enum_wire_v172 import build_reduction_projection
    prep = row.data['preview']['map_preparation']; cap = pipeline._cap(row.data['preview'])
    maps = map_inputs(pipeline, row, catalog)
    reducer.check_reduction_preparation(pipeline._authorized(row, key), catalog, cap, prep, maps)
    inputs = reducer.reduction_inputs_v3(prep, catalog, cap, maps)
    _need(inputs['scope'] == {'actor_id': row.actor_id, 'workspace_id': row.workspace_id, 'draft_id': row.id})
    return build_reduction_projection(inspected['arguments'].encode('utf-8', 'strict'), catalog=catalog,
        allowed_span_refs=inputs['allowed_span_refs'], context=inputs['context'],
        input_order=inputs['input_order'], input_items=inputs['input_items'])


def manual_value_from_candidate(candidate, catalog, allowed_span_refs, context):
    from .report_object_semantics_v166 import SCHEMA, validate_value
    value = {'schema': SCHEMA, 'result': copy.deepcopy(candidate), 'note': '',
        'object_identity_confirmations': [{'object_name': obj['name'], 'acknowledge_platform_identity': False}
            for obj in candidate['objects']]}
    return validate_value(value, catalog, allowed_span_refs, context)


def stage_projection(pipeline, row, key, catalog):
    if key != 'reduce':
        return _map_input(pipeline, row, key, catalog)['projection']
    from . import report_evidence_enum_reduction_v172 as reducer
    from .report_evidence_enum_wire_v172 import build_reduction_projection
    preview = row.data['preview']; prep = preview['map_preparation']; cap = pipeline._cap(preview)
    maps = map_inputs(pipeline, row, catalog); authorized = pipeline._authorized(row, key)
    reducer.check_reduction_preparation(authorized, catalog, cap, prep, maps)
    inputs = reducer.reduction_inputs_v3(prep, catalog, cap, maps)
    _need(inputs['scope'] == {'actor_id': row.actor_id, 'workspace_id': row.workspace_id, 'draft_id': row.id})
    descriptor, loaded, body, inspected, expected = _read(pipeline, row, key)
    _parse(preview, key, body, inspected)
    arguments = inspected['arguments'].encode('utf-8', 'strict')
    reduced = build_reduction_projection(arguments, catalog=catalog, allowed_span_refs=inputs['allowed_span_refs'],
        context=inputs['context'], input_order=inputs['input_order'], input_items=inputs['input_items'])
    _need(reduced['wire_bytes_sha256'] == inspected['metadata']['arguments_sha256']
        and reduced['wire_bytes'] == inspected['metadata']['arguments_bytes'])
    value = {'schema': REDUCE_PROJECTION, 'delivery': native_tool.DELIVERY,
        'binding': copy.deepcopy(expected), 'binding_hash': digest(expected),
        'journal_descriptor_hash': descriptor['descriptor_hash'], 'journal_wire_sha256': descriptor['wire_sha256'],
        'http_body_sha256': inspected['metadata']['response_body_sha256'], 'http_body_bytes': inspected['metadata']['response_body_bytes'],
        'http_body_encoding': inspected['metadata']['response_body_encoding'],
        'arguments_sha256': inspected['metadata']['arguments_sha256'], 'arguments_bytes': len(arguments),
        'tool_metadata': copy.deepcopy(inspected['metadata']), 'tool_metadata_hash': digest(inspected['metadata']),
        'evidence_profile': copy.deepcopy(loaded['receipt']['evidence_profile']),
        'evidence_profile_hash': digest(loaded['receipt']['evidence_profile']),
        'reduction_preparation_hash': authorized['preparation_hash'], 'map_preparation_hash': prep['preparation_hash'],
        'map_plan_hash': prep['plan']['plan_hash'], 'map_inputs_hash': inputs['map_inputs_hash'],
        'arguments_projection_hash': reduced['projection_hash'],
        'reduction_projection_metadata': {k: copy.deepcopy(v) for k, v in reduced.items() if k not in {'result', 'reconciliation'}},
        'result': copy.deepcopy(reduced['result']), 'reconciliation': copy.deepcopy(reduced['reconciliation'])}
    _need(len(_bytes(value)) <= 8 * 1024 * 1024)
    return {**value, 'projection_hash': digest(value)}

