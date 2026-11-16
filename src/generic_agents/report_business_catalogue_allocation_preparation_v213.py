"""Prepare one source-bound partial batch; no spending or current IAM proof."""
import copy
import hashlib
import json

from . import report_business_catalogue_allocation_native_v213 as native
from . import report_business_catalogue_allocation_plan_v213 as planning
from . import report_business_catalogue_allocation_wire_v213 as wire
from .cloud_input_profiles import measure_request_body
from .contracts import digest
from .gateway import CLOUD_JSON_SYSTEM_PREFIX, cloud_json_options, prepare_cloud_json
from .report_evidence_plan import EvidenceCapacityExceeded
from .report_full_reading_semantics import _safe
from .typed_payload_codec_v131 import canonical_json

PREPARATION = 'report-business-catalogue-allocation-preparation-v213'
GENERATION = 'report-business-catalogue-allocation-generation-v213'
PAYLOAD = native.REQUEST_PAYLOAD_SCHEMA
TASK_ID = native.TASK_ID
TASK = native.TASK
STAGE = native.STAGE
MAX_REQUEST_BYTES = native.MAX_INPUT_BYTES
ERROR = '本批原来源、人工稿、完整引用或原请求无法核对；未裁剪、发送、换号或重跑'
_FIELDS = set(('schema producer_id producer_revision source_snapshot_hash source_hash catalog_hash '
    'indexed_inputs_hash input_claims_hash input_order_hash plan_hash batch_hash batch_ordinal '
    'catalogue_review_hash base_draft_ref decision_count allocation_evidence allocation_evidence_hash '
    'generation_contract generation_contract_hash payload payload_hash messages_hash request_hash '
    'input_summary output_capacity call_count requires_separate_authorization semantic_quality_verified '
    'consumer_authorized can_apply can_publish preparation_hash').split())


def _need(value):
    if not value:
        raise ValueError(ERROR)


def _bytes(value):
    _safe(value)
    return canonical_json(value).encode('utf-8')


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _arguments(evidence):
    return {'plan': evidence['plan'], 'batch_ordinal': evidence['batch_ordinal'],
        'allocation_evidence': evidence}


def _variant_arguments(policy):
    _need(type(policy) is dict)
    if 'native_schema_variant' not in policy:
        return {}
    value = policy['native_schema_variant']
    _need(type(value) is str and value == native.NATIVE_SCHEMA_VARIANT)
    return {'native_schema_variant': value}


def _generation(cap, evidence):
    plan, ordinal = evidence['plan'], evidence['batch_ordinal']; batch = plan['batches'][ordinal]
    schema = wire.output_schema(plan, ordinal); execution = cap.data['execution']
    policy = {'single_batch': True, **native.input_policy(execution)}
    variant = _variant_arguments(policy)
    options = native.strict_options(schema, native.dispatch_options(execution,
        cloud_json_options(execution)), STAGE, **_arguments(evidence), **variant)
    return {'schema': GENERATION, 'task': TASK, 'task_hash': digest(TASK),
        'model_hash': digest(cap.data), 'source_pins': copy.deepcopy(plan['source_pins']),
        'base_draft_ref': copy.deepcopy(evidence['draft_ref']), 'plan_hash': plan['plan_hash'],
        'batch_hash': batch['batch_hash'], 'batch_ordinal': ordinal,
        'catalogue_review_hash': plan['catalogue_review_hash'],
        'allocation_evidence_hash': digest(evidence), 'output_schema': schema,
        'dispatch_options': copy.deepcopy(options['options']), 'options_hash': digest(options['options']),
        'input_policy': policy, 'input_policy_hash': digest(policy),
        'wire_result_schema': wire.WIRE, 'projection_schema': wire.PROJECTION,
        'evidence_stage': STAGE, 'delivery': native.DELIVERY, 'endpoint': native.ENDPOINT,
        'schema_proof': copy.deepcopy(options['schema_proof']),
        'gateway_prefix_hash': digest(native.system_prefix(STAGE, **variant)),
        'max_response_bytes': native.stage_policy(STAGE)['max_response_bytes']}


def _request(cap, spec, payload, evidence):
    _need(spec['schema'] == GENERATION and spec['task'] == TASK and spec['evidence_stage'] == STAGE
        and _same(payload, native.payload_for(evidence)))
    variant = _variant_arguments(native.input_policy(cap.data['execution']))
    _need(_same(variant, _variant_arguments(spec['input_policy'])))
    requested = [{'role': 'system', 'content': _bytes(native.task_envelope()).decode('utf-8')},
        {'role': 'user', 'content': _bytes(payload).decode('utf-8')}]
    schema = copy.deepcopy(spec['output_schema'])
    messages, _, images = prepare_cloud_json(cap, requested, schema)
    _need(type(images) is int and images == 0 and len(messages) == 3
        and messages[0] == {'role': 'system', 'content': CLOUD_JSON_SYSTEM_PREFIX + json.dumps(schema, ensure_ascii=False)})
    for actual, expected in zip(messages[1:], requested):
        _need(type(actual) is dict and set(actual) == {'role', 'content'}
            and actual['role'] == expected['role']
            and _same(json.loads(actual['content']), json.loads(expected['content'])))
    actual = native.prepare_request(messages, schema, native.dispatch_options(cap.data['execution'],
        cloud_json_options(cap.data['execution'])), STAGE, **_arguments(evidence), **variant)
    request, body = actual['request'], actual['body']
    options = {key: value for key, value in request.items() if key != 'messages'}
    _need(_same(options, spec['dispatch_options']) and _same(actual['schema_proof'], spec['schema_proof'])
        and type(body) is bytes and body == _bytes(request)
        and actual['body_bytes'] == len(body) and actual['body_sha256'] == hashlib.sha256(body).hexdigest())
    policy, output = spec['input_policy'], options['max_tokens']
    _need(policy['full_report'] is False)
    if (len(body) > min(MAX_REQUEST_BYTES, policy['max_input_bytes'])
            or len(body) + output + 1024 > policy['context_token_bound']):
        raise EvidenceCapacityExceeded('本批完整请求超过容量，原声明和引用保留；未裁剪、换模型或新建调用')
    return {'messages': copy.deepcopy(request['messages']), 'messages_hash': digest(request['messages']),
        'request_hash': actual['body_sha256'], 'request_body': body, 'request': copy.deepcopy(request),
        'input_summary': measure_request_body(body, policy, output)}


def build_preparation(cap, source_context, *, plan, batch_ordinal, document, draft_ref,
        draft_origin, name_origin=None, note=''):
    evidence = native.build_allocation_evidence(source_context, plan=plan, batch_ordinal=batch_ordinal,
        document=document, draft_ref=draft_ref, draft_origin=draft_origin, name_origin=name_origin, note=note)
    plan = evidence['plan']; batch = plan['batches'][batch_ordinal]
    snapshot = evidence['source_context']['snapshot']
    spec = _generation(cap, evidence); payload = native.payload_for(evidence)
    request = _request(cap, spec, payload, evidence)
    capacity = {'schema': 'report-business-catalogue-allocation-output-capacity-v213',
        'max_output_tokens': spec['dispatch_options']['max_tokens'],
        'max_wire_value_bytes': wire.MAX_WIRE_BYTES, 'max_response_body_bytes': spec['max_response_bytes'],
        'completion_required': 'stop', 'decision_count': len(batch['decision_keys']),
        'full_report': False, 'fit_guaranteed': False, 'overflow_behavior': 'preserve_response_and_fail'}
    value = {'schema': PREPARATION, 'producer_id': snapshot['producer']['id'],
        'producer_revision': snapshot['producer']['revision'], 'source_snapshot_hash': snapshot['snapshot_hash'],
        'source_hash': snapshot['source_hash'], **copy.deepcopy(plan['source_pins']),
        'plan_hash': plan['plan_hash'], 'batch_hash': batch['batch_hash'], 'batch_ordinal': batch_ordinal,
        'catalogue_review_hash': plan['catalogue_review_hash'], 'base_draft_ref': copy.deepcopy(draft_ref),
        'decision_count': len(batch['decision_keys']), 'allocation_evidence': evidence,
        'allocation_evidence_hash': digest(evidence), 'generation_contract': spec,
        'generation_contract_hash': digest(spec), 'payload': payload, 'payload_hash': digest(payload),
        'messages_hash': request['messages_hash'], 'request_hash': request['request_hash'],
        'input_summary': request['input_summary'], 'output_capacity': capacity,
        'call_count': 1, 'requires_separate_authorization': True, 'semantic_quality_verified': False,
        'consumer_authorized': False, 'can_apply': False, 'can_publish': False}
    # input_count is fixed by the full inventory and remains server-side.
    value.pop('input_count', None)
    return {**value, 'preparation_hash': digest(value)}


def check_preparation(value, cap, source_context, *, plan, batch_ordinal, document, draft_ref,
        draft_origin, name_origin=None, note=''):
    _need(type(value) is dict and set(value) == _FIELDS and value['schema'] == PREPARATION)
    expected = build_preparation(cap, source_context, plan=plan, batch_ordinal=batch_ordinal,
        document=document, draft_ref=draft_ref, draft_origin=draft_origin, name_origin=name_origin, note=note)
    _need(_same(value, expected))
    return expected


def rebuild_request(value, cap, source_context, *, plan, batch_ordinal, document, draft_ref,
        draft_origin, name_origin=None, note=''):
    expected = check_preparation(value, cap, source_context, plan=plan, batch_ordinal=batch_ordinal,
        document=document, draft_ref=draft_ref, draft_origin=draft_origin, name_origin=name_origin, note=note)
    actual = _request(cap, expected['generation_contract'], expected['payload'], expected['allocation_evidence'])
    _need(actual['messages_hash'] == expected['messages_hash'] and actual['request_hash'] == expected['request_hash']
        and _same(actual['input_summary'], expected['input_summary']))
    return actual


def stage_spec(value):
    """Frozen pure declaration only; callers still need actual-source verification."""
    _need(type(value) is dict and set(value) == _FIELDS and value['schema'] == PREPARATION
        and value['preparation_hash'] == digest({k: v for k, v in value.items() if k != 'preparation_hash'})
        and value['generation_contract_hash'] == digest(value['generation_contract']))
    evidence = native.check_allocation_evidence(value['allocation_evidence'])
    spec = value['generation_contract']; args = _arguments(evidence)
    variant = _variant_arguments(spec['input_policy'])
    plan = evidence['plan']; batch = plan['batches'][evidence['batch_ordinal']]
    _need(spec['schema'] == GENERATION and spec['evidence_stage'] == STAGE
        and spec['delivery'] == native.DELIVERY and spec['endpoint'] == native.ENDPOINT
        and spec['task'] == TASK and spec['task_hash'] == digest(TASK)
        and spec['input_policy']['full_report'] is False
        and spec['input_policy']['max_input_bytes'] == MAX_REQUEST_BYTES
        and spec['input_policy']['context_token_bound'] == 1000000
        and spec['max_response_bytes'] == native.stage_policy(STAGE)['max_response_bytes']
        and 128 <= spec['dispatch_options']['max_tokens'] <= 32768
        and value['allocation_evidence_hash'] == digest(evidence)
        and spec['allocation_evidence_hash'] == digest(evidence)
        and value['plan_hash'] == spec['plan_hash'] == plan['plan_hash']
        and value['batch_hash'] == spec['batch_hash'] == batch['batch_hash']
        and value['batch_ordinal'] == spec['batch_ordinal'] == evidence['batch_ordinal']
        and value['catalogue_review_hash'] == spec['catalogue_review_hash'] == plan['catalogue_review_hash']
        and _same(value['base_draft_ref'], evidence['draft_ref'])
        and _same(spec['base_draft_ref'], evidence['draft_ref'])
        and _same(spec['source_pins'], plan['source_pins'])
        and value['producer_id'] == evidence['source_context']['snapshot']['producer']['id']
        and value['producer_revision'] == evidence['source_context']['snapshot']['producer']['revision']
        and value['source_hash'] == evidence['source_context']['snapshot']['source_hash']
        and type(value['batch_ordinal']) is int and type(value['decision_count']) is int
        and value['decision_count'] == len(batch['decision_keys'])
        and all(value[key] == item for key, item in plan['source_pins'].items() if key != 'input_count')
        and type(value['call_count']) is int and value['call_count'] == 1 and value['requires_separate_authorization'] is True
        and all(value[key] is False for key in ('consumer_authorized', 'semantic_quality_verified', 'can_apply', 'can_publish'))
        and value['payload_hash'] == digest(value['payload'])
        and _same(value['payload'], native.payload_for(evidence))
        and _same(spec['output_schema'], wire.output_schema(evidence['plan'], evidence['batch_ordinal'])))
    native.check_schema_proof(spec['schema_proof'], spec['output_schema'], STAGE, **args, **variant)
    options = native.strict_options(spec['output_schema'],
        {key: item for key, item in spec['dispatch_options'].items() if key != 'response_format'}, STAGE, **args, **variant)
    _need(_same(options['options'], spec['dispatch_options']))
    return copy.deepcopy(spec)
