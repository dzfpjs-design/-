"""Complete-source planning with the actual per-chunk reference schema.

No provider, filesystem, account or budget access. The generation contract is
deliberately a dynamic policy, never a static dispatch schema/proof. All request
and capacity calculations use the actual chunk enum before planning is frozen.
"""
import copy
import hashlib
import json

from . import report_claim_array_v167 as array
from . import report_claim_enum_native_v172 as native
from .cloud_evidence_profiles import evidence_options, evidence_policy
from .cloud_input_profiles import measure_request_body
from .contracts import digest
from .gateway import cloud_json_options, prepare_cloud_json
from .report_evidence_plan import (EvidenceCapacityExceeded, PLAN_V2,
    check_evidence_source, evidence_payload, plan_evidence_reads)
from .report_evidence_wire_v3 import DERIVATION, MAP_WIRE, MapResultWireV3
from .report_full_reading_semantics import _safe
from .typed_payload_codec_v131 import canonical_json


GENERATION = 'report-claim-enum-map-generation-v172'
PREPARATION = 'report-claim-enum-map-preparation-v172'
TASK_ID = 'report-claim-enum-map-task-v172'
STAGE = 'map-v172'
MAP_PROJECTION = 'report-claim-enum-map-projection-v172'
MAX_MAP_PREPARED_BYTES = 100000
MAX_MAP_CHUNKS = 24
ERROR = '逐组引用枚举的完整来源、动态Schema或实际请求无法核对；未发送'
TASK = ('你执行独立report-claim-enum-map-task-v172。' + array.TASK +
    '每项span_refs只能取本组实际output_schema枚举；相邻分组存在的片段也不得借用。'
    'user_instruction仅限定关注范围，不得省略相关声明的前提、否定或限制。'
    '保留必要、仅当等前提和不具备等否定，区分现有、计划、建议及示例；'
    '数量、算术或表述矛盾须保留各方声明及引用，不替原文修正或选边。')
_KEYS = {'schema', 'catalog_descriptor', 'generation_contract', 'context', 'user_instruction',
    'plan', 'input_summary', 'can_apply', 'can_publish', 'preparation_hash'}


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _bytes(value):
    _safe(value)
    return canonical_json(value).encode('utf-8', errors='strict')


def _canonical(value):
    return json.loads(_bytes(value))


def _same(left, right):
    return _bytes(left) == _bytes(right)


def applies(value):
    """Only a dispatch hint, never validation of the source or frozen plan."""
    return (type(value) is dict and (value.get('schema') == GENERATION
        or type(value.get('generation_contract')) is dict
        and value['generation_contract'].get('schema') == GENERATION))


def map_generation(cap):
    execution = cap.data['execution']
    policy = evidence_policy(execution, STAGE)
    _need(policy['long_context_verified'] is True and policy['full_report'] is True)
    base = evidence_options(execution, cloud_json_options(execution), STAGE)
    # Validate the base options without pretending this probe schema belongs to
    # any real chunk. The probe and its proof are never returned or dispatched.
    native.strict_options(native.map_schema(['w:p1/s1']), base, STAGE)
    return {'schema': GENERATION, 'task': TASK, 'task_hash': digest(TASK),
        'base_schema': _canonical(MapResultWireV3.model_json_schema()),
        'schema_policy': native.SCHEMA_POLICY, 'base_options': copy.deepcopy(base),
        'gateway_prefix_hash': digest(native.system_prefix(STAGE)),
        'input_policy': {'single_batch': True, **copy.deepcopy(policy)},
        'max_prepared_bytes': MAX_MAP_PREPARED_BYTES, 'max_chunks': MAX_MAP_CHUNKS,
        'wire_schema': MAP_WIRE, 'projection_schema': MAP_PROJECTION,
        'id_policy': DERIVATION, 'evidence_stage': STAGE,
        'delivery': native.DELIVERY, 'endpoint': native.ENDPOINT}


def _stage(spec, refs, chunk_id):
    schema = native.map_schema(refs)
    prepared = native.strict_options(schema, spec['base_options'], STAGE)
    return {'schema': GENERATION, 'task': spec['task'], 'task_hash': spec['task_hash'],
        'output_schema': schema, 'schema_proof': prepared['schema_proof'],
        'dispatch_options': prepared['options'], 'input_policy': copy.deepcopy(spec['input_policy']),
        'gateway_prefix_hash': spec['gateway_prefix_hash'], 'evidence_stage': STAGE,
        'delivery': prepared['delivery'], 'endpoint': prepared['endpoint'],
        'wire_schema': MAP_WIRE, 'projection_schema': MAP_PROJECTION, 'id_policy': DERIVATION,
        'chunk_id': chunk_id, 'allowed_span_refs': copy.deepcopy(refs), 'allowed_span_refs_hash': digest(refs)}


def stage_spec(map_preparation, chunk_id):
    """Derive a chunk's real schema; caller still cold-checks the whole plan.

    Self-hashes are corruption checks only. check_map_preparation with the actual
    catalog and registered capability is required before this dispatch helper.
    """
    value = map_preparation
    _need(type(value) is dict and set(value) == _KEYS and value['schema'] == PREPARATION
        and value['preparation_hash'] == digest({k: v for k, v in value.items() if k != 'preparation_hash'})
        and value['can_apply'] is False and value['can_publish'] is False
        and type(chunk_id) is str)
    spec = value['generation_contract']; plan = value['plan']
    _need(type(spec) is dict and spec['schema'] == GENERATION and spec['task'] == TASK
        and spec['task_hash'] == digest(TASK) and spec['schema_policy'] == native.SCHEMA_POLICY
        and _same(spec['base_schema'], MapResultWireV3.model_json_schema())
        and spec['delivery'] == native.DELIVERY and spec['endpoint'] == native.ENDPOINT
        and spec['evidence_stage'] == STAGE and 'output_schema' not in spec
        and 'schema_proof' not in spec and 'dispatch_options' not in spec
        and type(plan) is dict and plan['schema'] == PLAN_V2
        and plan['plan_hash'] == digest({k: v for k, v in plan.items() if k != 'plan_hash'}))
    chunks = plan['chunks']
    _need(type(chunks) is list and 1 <= len(chunks) <= MAX_MAP_CHUNKS
        and [c['chunk_id'] for c in chunks] == [f'map-{i + 1}' for i in range(len(chunks))])
    matches = [c for c in chunks if c['chunk_id'] == chunk_id]
    _need(len(matches) == 1)
    chunk = matches[0]
    _need(chunk['chunk_hash'] == digest({k: v for k, v in chunk.items() if k != 'chunk_hash'}))
    return _stage(spec, chunk['span_refs'], chunk_id)


def _requested(catalog, refs, context, user_instruction):
    _need(type(context) is dict and type(user_instruction) is str and len(user_instruction) <= 4000)
    original = array.build_preparation(catalog, refs, context, user_instruction)
    payload = copy.deepcopy(original['input_body'])
    payload.update(generation=GENERATION, task_id=TASK_ID)
    trusted = {'schema': TASK_ID, 'task': TASK, 'purpose': 'candidate_only',
        'source_policy': 'untrusted_evidence', 'full_report_confirmed': False}
    messages = [{'role': 'system', 'content': _bytes(trusted).decode('utf-8')},
        {'role': 'user', 'content': _bytes(payload).decode('utf-8')}]
    schema = native.map_schema(refs)
    if len(_bytes({'messages': messages, 'output_schema': schema})) > array.MAX_INPUT_BYTES:
        raise EvidenceCapacityExceeded('完整原文与本组引用枚举超过100000字节；未截断或发送')
    return messages, schema


def _request(catalog, cap, spec, payload, context, user_instruction):
    refs = [item[0] for item in payload['spans']]
    _need(_same(payload, evidence_payload(catalog, refs)))
    actual_spec = _stage(spec, refs, None)
    requested, schema = _requested(catalog, refs, context, user_instruction)
    _need(_same(schema, actual_spec['output_schema']))
    prepared, _, images = prepare_cloud_json(cap, requested, schema)
    _need(not images and type(prepared) is list and len(prepared) == len(requested) + 1)
    for actual, expected in zip(prepared[1:], requested):
        _need(type(actual) is dict and set(actual) == {'role', 'content'}
            and actual['role'] == expected['role'] and type(actual['content']) is str
            and _same(json.loads(actual['content']), json.loads(expected['content'])))
    actual = native.prepare_request(prepared, schema, spec['base_options'], STAGE)
    request, body = actual['request'], actual['body']
    _need(_same({k: v for k, v in request.items() if k != 'messages'}, actual_spec['dispatch_options'])
        and _same(actual['schema_proof'], actual_spec['schema_proof'])
        and actual['delivery'] == spec['delivery'] and actual['endpoint'] == spec['endpoint']
        and type(body) is bytes and body == _bytes(request)
        and type(actual['body_bytes']) is int and actual['body_bytes'] == len(body)
        and actual['body_sha256'] == hashlib.sha256(body).hexdigest())
    policy, output = spec['input_policy'], request['max_tokens']
    if (len(body) > min(policy['max_input_bytes'], MAX_MAP_PREPARED_BYTES)
            or policy['context_token_bound'] is not None
            and len(body) + output + 1024 > policy['context_token_bound']):
        raise EvidenceCapacityExceeded('含实际动态Schema的完整请求超过容量；未截断或发送')
    return {'messages': copy.deepcopy(request['messages']), 'messages_hash': digest(request['messages']),
        'request_hash': actual['body_sha256'], 'request_body': body,
        'request': copy.deepcopy(request), 'input_summary': measure_request_body(body, policy, output)}


def build_map_preparation(catalog, cap, context, user_instruction=''):
    check_evidence_source(catalog)
    _need(bool(catalog['index']))
    context = _canonical(context)
    spec = map_generation(cap)
    def prepare(payload):
        result = _request(catalog, cap, spec, payload, context, user_instruction)
        return {key: result[key] for key in ('messages_hash', 'request_hash', 'input_summary')}
    plan = plan_evidence_reads(catalog, prepare, MAX_MAP_PREPARED_BYTES, MAX_MAP_CHUNKS, plan_version=PLAN_V2)
    value = {'schema': PREPARATION, 'catalog_descriptor': copy.deepcopy(catalog['descriptor']),
        'generation_contract': spec, 'context': context, 'user_instruction': user_instruction,
        'plan': plan, 'input_summary': {'map_call_count': len(plan['chunks']),
            'map_token_bound': sum(c['input_summary']['token_bound'] for c in plan['chunks']),
            'prepared_request_bytes': sum(c['input_summary']['prepared_request_bytes'] for c in plan['chunks']),
            'maximum_total_calls': len(plan['chunks']) + 1, 'full_report_confirmed': False},
        'can_apply': False, 'can_publish': False}
    return {**value, 'preparation_hash': digest(value)}


def check_map_preparation(value, catalog, cap):
    from .evidence_request_scope_v191 import _map_preparation
    return _map_preparation(value, catalog, cap, _check_map_preparation_uncached)


def _check_map_preparation_uncached(value, catalog, cap):
    _need(type(value) is dict and set(value) == _KEYS and value['schema'] == PREPARATION)
    expected = build_map_preparation(catalog, cap, value['context'], value['user_instruction'])
    _need(_same(value, expected))
    return value


def rebuild_map_request(value, catalog, cap, chunk_id):
    check_map_preparation(value, catalog, cap)
    spec = stage_spec(value, chunk_id)
    chunk = next(c for c in value['plan']['chunks'] if c['chunk_id'] == chunk_id)
    payload = evidence_payload(catalog, spec['allowed_span_refs'])
    result = _request(catalog, cap, value['generation_contract'], payload,
        value['context'], value['user_instruction'])
    _need(digest(payload) == chunk['payload_hash'] and result['messages_hash'] == chunk['messages_hash']
        and result['request_hash'] == chunk['request_hash']
        and _same(result['input_summary'], chunk['input_summary'])
        and _same(result['request']['response_format']['json_schema']['schema'], spec['output_schema']))
    return result
