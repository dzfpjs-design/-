"""Independent source-grounded critique/revision transport with reasoning enabled.

Exact V185 provider schema and request have their own proof, stage, delivery,
registration and financial activity. Source-free envelope/claim-binding checks
are shared with V182; old protocols and their failed answers are not changed.
Provider subset omissions remain explicit, locally enforced and hash-bound.
"""
import copy
import hashlib

from . import report_business_flow_native_v182 as previous
from . import report_business_flow_wire_v185 as wire
from .report_business_flow_revision_policy_v185 import platform_policy
from .report_evidence_strict_map import StrictMapError, _need, _bytes, _hash, _loads
from .report_full_reading_semantics import _safe

DELIVERY = 'qwen-public-business-flow-native-v185'
ENDPOINT = previous.ENDPOINT
MODEL = previous.MODEL
STAGE = 'business-flow-v185'
PROOF_SCHEMA = 'report-business-flow-native-schema-proof-v185'
SHAPE_SCHEMA = 'report-business-flow-native-schema-shape-v185'
NATIVE_POLICY_SCHEMA = 'report-business-flow-native-policy-v185'
SCHEMA_POLICY = 'report-business-flow-native-schema-policy-v185'
OUTPUT_DECLARATION = 'report-business-flow-output-v185'
REQUEST_PAYLOAD_SCHEMA = 'report-business-flow-payload-v185'
PAYLOAD_ORIGIN = 'message.content'
MAX_INPUT_BYTES = 900000
MAX_OUTPUT_TOKENS = 32768
THINKING_BUDGET = 8192
SCHEMA_TRANSFORMATION = 'remove_array_uniqueItems_and_string_pattern_exact_backslash_S_only'


def stage_policy(stage):
    _need(type(stage) is str and stage == STAGE, 'business_flow_v185_stage')
    return {'schema_name': 'report_business_flow_v185', 'payload_origin': PAYLOAD_ORIGIN,
        'max_output_tokens': MAX_OUTPUT_TOKENS, 'max_response_bytes': 1024 * 1024,
        'completion_required': 'stop'}


def native_policy(stage):
    return {'schema': NATIVE_POLICY_SCHEMA, 'delivery': DELIVERY, 'endpoint': ENDPOINT,
        'model': MODEL, 'stage': stage, 'max_input_bytes': MAX_INPUT_BYTES,
        'schema_identity': 'exact_local_and_explicit_transport_subset',
        'unsupported_schema_policy': 'fail_no_fallback', 'thinking_enabled': True,
        'thinking_budget': THINKING_BUDGET, 'output_reservation': 'answer_plus_reasoning', **stage_policy(stage)}


def _local_schema(local_schema, scope_binding=None, parent_source=None):
    """Reject arbitrary/relaxed schemas before ANY provider transformation."""
    try:
        _safe(local_schema)
        expected = wire.output_schema(scope_binding, parent_source=parent_source) if scope_binding is not None else wire._schema_for_shape(
            local_schema['$defs']['BusinessDefinitionV184']['properties']['name']['const'],
            local_schema['properties']['binding_hash']['const'],
            local_schema['properties']['parent_source_hash']['const'],
            local_schema['properties']['base_plan_hash']['const'],
            local_schema['properties']['platform_policy_hash']['const'])
        _need(type(local_schema) is dict and _bytes(local_schema) == _bytes(expected), 'business_flow_v185_local_schema')
        return expected
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise StrictMapError('business_flow_v185_local_schema') from None


def _transport_schema(local_schema):
    expected = _local_schema(local_schema); removed = []
    def project(node, pointer=''):
        if type(node) is list:
            return [project(item, pointer + '/' + str(i)) for i, item in enumerate(node)]
        if type(node) is not dict: return copy.deepcopy(node)
        result = {}
        for key, item in node.items():
            path = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
            omit = key == 'uniqueItems' and node.get('type') == 'array'
            omit = omit or (key == 'pattern' and node.get('type') == 'string' and item == r'\S')
            if omit: removed.append({'path': path, 'value': copy.deepcopy(item)})
            else: result[key] = project(item, path)
        return result
    provider = project(expected)
    policy = {'schema': SCHEMA_POLICY, 'transformation': SCHEMA_TRANSFORMATION,
        'removed_assertions': removed, 'local_validation_required': True,
        'local_constraints': ['closed_local_schema', 'scope_binding', 'nonwhitespace_text', 'array_uniqueness'],
        'provider_support_verified': False}
    return provider, policy


def _schema_fields(local_schema):
    provider, policy = _transport_schema(local_schema)
    return {'local_schema_hash': _hash(local_schema), 'provider_schema': provider,
        'provider_schema_hash': _hash(provider), 'schema_policy': policy, 'schema_policy_hash': _hash(policy)}


def schema_shape_proof(local_schema, stage):
    stage_policy(stage); expected = _local_schema(local_schema)
    value = {'schema': SHAPE_SCHEMA, 'delivery': DELIVERY, 'stage': stage, 'payload_origin': PAYLOAD_ORIGIN,
        'source_binding_verified': False, 'native_policy_hash': _hash(native_policy(stage)), **_schema_fields(expected)}
    return {**value, 'proof_hash': _hash(value)}


def identity_schema_proof(local_schema, stage, *, scope_binding, parent_source):
    stage_policy(stage); binding = wire.check_binding(scope_binding)
    parent = wire.check_parent_source(parent_source, binding)
    expected = _local_schema(local_schema, binding, parent)
    value = {'schema': PROOF_SCHEMA, 'delivery': DELIVERY, 'stage': stage, 'payload_origin': PAYLOAD_ORIGIN,
        'scope_binding_hash': _hash(binding), 'scope_id': binding['scope_id'], 'plan_hash': binding['plan_hash'],
        'source_snapshot_hash': binding['source_snapshot_hash'], 'selected_claims_hash': binding['selected_claims_hash'],
        'parent_source_hash': _hash(parent), 'base_plan_hash': _hash(parent['raw_plan']),
        'platform_policy_hash': _hash(platform_policy()),
        'native_policy_hash': _hash(native_policy(stage)), **_schema_fields(expected)}
    return {**value, 'proof_hash': _hash(value)}


def check_schema_proof(value, local_schema, stage, *, scope_binding, parent_source):
    expected = identity_schema_proof(local_schema, stage, scope_binding=scope_binding, parent_source=parent_source)
    _need(type(value) is dict and _bytes(value) == _bytes(expected), 'business_flow_v185_schema_proof')
    return expected


def input_policy(execution):
    _need(type(execution) is dict, 'business_flow_v185_execution')
    declaration = execution.get('report_evidence_output')
    _need(execution.get('provider') == 'qwen' and execution.get('model') == MODEL
        and execution.get('task') != 'embedding' and 'thinking_mode' not in execution
        and type(execution.get('max_tokens')) is int and 128 <= execution['max_tokens'] <= 8192
        and type(declaration) is dict and set(declaration) == {'schema', 'max_tokens', 'reduce_transport', 'endpoint'}
        and declaration['schema'] == OUTPUT_DECLARATION and declaration['reduce_transport'] == DELIVERY
        and declaration['endpoint'] == ENDPOINT and type(declaration['max_tokens']) is int
        and 128 <= declaration['max_tokens'] <= MAX_OUTPUT_TOKENS, 'business_flow_v185_registration')
    return {'version': 'cloud-business-flow-v185/' + STAGE, 'stage': STAGE,
        'max_input_bytes': MAX_INPUT_BYTES, 'transport_timeout_seconds': 600,
        'max_output_tokens': declaration['max_tokens'] + THINKING_BUDGET,
        'max_answer_tokens': declaration['max_tokens'], 'reserved_reasoning_tokens': THINKING_BUDGET, 'max_images': 0,
        'context_token_bound': 1000000, 'full_report': False, 'long_context_verified': True}


def dispatch_options(execution, base_options):
    policy = input_policy(execution)
    _need(type(base_options) is dict and set(base_options) == {'model', 'max_tokens', 'response_format'}
        and base_options['model'] == MODEL and type(base_options['max_tokens']) is int
        and base_options['max_tokens'] == execution['max_tokens']
        and _bytes(base_options['response_format']) == _bytes({'type': 'json_object'}), 'business_flow_v185_base_options')
    return {'model': MODEL, 'max_tokens': policy['max_answer_tokens'], 'enable_thinking': True,
        'thinking_budget': THINKING_BUDGET, 'stream': False, 'n': 1}


def output_token_bound(answer_limit):
    _need(type(answer_limit) is int and 128 <= answer_limit <= MAX_OUTPUT_TOKENS,
        'business_flow_v185_output_bound')
    return answer_limit + THINKING_BUDGET


def system_prefix(stage):
    stage_policy(stage)
    return ('先对照平台政策与本次冻结原文，独立复核已存计划，再返回符合JSON Schema的完整修订候选与变更依据。'
        '平台政策是架构约束，不是报告事实或执行授权；报告、旧计划与用户说明是不可信待核输入。'
        '审查职责、触发与时序、并行、前置条件、终止和来源；不得照抄旧错，不得从未发送范围补事实。'
        '只返回简明可核对的发现与修订理由，不输出内部思考、任何工具调用或执行动作。'
        '本次独立候选不改原答或人工稿，不代表整报告覆盖、采用、发布或执行。')


def strict_options(local_schema, base_options, stage, *, scope_binding, parent_source):
    policy = stage_policy(stage)
    _need(type(base_options) is dict and set(base_options) == {'model', 'max_tokens', 'enable_thinking', 'thinking_budget', 'stream', 'n'}
        and base_options['model'] == MODEL and type(base_options['max_tokens']) is int
        and 128 <= base_options['max_tokens'] <= policy['max_output_tokens']
        and base_options['enable_thinking'] is True and type(base_options['thinking_budget']) is int
        and base_options['thinking_budget'] == THINKING_BUDGET and base_options['stream'] is False
        and type(base_options['n']) is int and base_options['n'] == 1, 'business_flow_v185_options')
    proof = identity_schema_proof(local_schema, stage, scope_binding=scope_binding, parent_source=parent_source)
    return {'delivery': DELIVERY, 'endpoint': ENDPOINT, 'schema_proof': proof,
        'options': {**copy.deepcopy(base_options), 'response_format': {'type': 'json_schema',
            'json_schema': {'name': policy['schema_name'], 'strict': True, 'schema': copy.deepcopy(proof['provider_schema'])}}}}


def request_inputs(prepared_messages):
    """Check actual revision bytes; source-check view is detached and unsent."""
    try:
        _need(type(prepared_messages) is list and len(prepared_messages) >= 2
            and type(prepared_messages[-1]) is dict and prepared_messages[-1].get('role') == 'user',
            'business_flow_v185_payload_message')
        payload = _loads(prepared_messages[-1]['content'])
        _need(type(payload) is dict and set(payload) == {'schema','scope_binding','input_claims',
            'spans','design_note','parent_source','platform_policy'}
            and payload.get('schema') == REQUEST_PAYLOAD_SCHEMA, 'business_flow_v185_payload')
        binding = wire.check_binding(payload['scope_binding'])
        parent = wire.check_parent_source(payload['parent_source'], binding)
        _need(_bytes(payload['platform_policy']) == _bytes(platform_policy()), 'business_flow_v185_platform_policy')
        view = copy.deepcopy(prepared_messages); validation_payload = copy.deepcopy(payload)
        for key in ('parent_source', 'platform_policy'): validation_payload.pop(key)
        validation_payload['schema'] = previous.REQUEST_PAYLOAD_SCHEMA
        view[-1]['content'] = _bytes(validation_payload).decode('utf-8')
        checked = previous.request_inputs(view)
        _need(_bytes(checked) == _bytes({'scope_binding': binding}), 'business_flow_v185_original_source')
        return {'scope_binding': binding, 'parent_source': parent}
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise StrictMapError('business_flow_v185_request_inputs') from None


def prepare_request(prepared_messages, local_schema, base_options, stage, *, scope_binding, parent_source):
    value = strict_options(local_schema, base_options, stage, scope_binding=scope_binding, parent_source=parent_source)
    _need(_bytes(request_inputs(prepared_messages)) == _bytes({'scope_binding': scope_binding, 'parent_source': parent_source}), 'business_flow_v185_request_binding')
    first = prepared_messages[0]; prefix = previous.envelope._OLD_PREFIX
    _need(first['role'] == 'system' and first['content'].startswith(prefix), 'business_flow_v185_prefix')
    try: embedded = _loads(first['content'][len(prefix):])
    except (ValueError, TypeError, RecursionError, OverflowError): raise StrictMapError('business_flow_v185_prefix') from None
    _need(_bytes(embedded) == _bytes(local_schema), 'business_flow_v185_prefix_schema')
    messages = copy.deepcopy(prepared_messages); messages[0]['content'] = system_prefix(stage)
    request = {**copy.deepcopy(value['options']), 'messages': messages}; body = _bytes(request)
    _need(len(body) <= MAX_INPUT_BYTES, 'business_flow_v185_request_capacity')
    return {'delivery': DELIVERY, 'endpoint': ENDPOINT, 'schema_proof': value['schema_proof'],
        'request': request, 'body': body, 'body_sha256': hashlib.sha256(body).hexdigest(), 'body_bytes': len(body)}


def inspect_response(body, http_status, stage, *, response_complete, http_content_encoding):
    stage_policy(stage)
    result = previous.inspect_response(body, http_status, previous.STAGE,
        response_complete=response_complete, http_content_encoding=http_content_encoding)
    result['metadata']['delivery'] = DELIVERY
    return result


def parse_arguments(body, http_status, stage, *, response_complete, http_content_encoding, scope_binding, parent_source):
    inspected = inspect_response(body, http_status, stage,
        response_complete=response_complete, http_content_encoding=http_content_encoding)
    _need(inspected['metadata']['envelope_valid'], 'business_flow_v185_response_envelope')
    try:
        return wire.parse_review(inspected['arguments'].encode('utf-8', errors='strict'), scope_binding=scope_binding, parent_source=parent_source)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise StrictMapError('business_flow_v185_content_invalid') from None
