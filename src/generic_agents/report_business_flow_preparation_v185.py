"""Immutable preparation for independent structured phase planning.

Retains V181's unchanged selected claims and frozen source scope. The distinct
V185 task/schema/request binds the actual new activity; it never relabels an
old preparation or retries an old failure. Compilation is not semantic proof.
"""
import copy
import hashlib
import json

from . import report_business_flow_native_v185 as native
from . import report_business_flow_wire_v185 as wire
from .report_business_flow_revision_policy_v185 import platform_policy
from .report_business_flow_preparation_v184 import _selection
TASK = (
    '对本次已存计划做独立来源复核，再给出完整修订候选；旧计划不是正确答案，原答不修改。'
    '先逐条对照平台政策和本次声明、原文，检查各流程目标、触发、输入产出因果、责任与权限、'
    '时序、先后、条件、并行、停止/完成、原文明示与推断、来源遗漏及冲突。'
    '独立发现具体问题，不为输出发现而捏造问题，不将未发送的资料算作旧计划漏读。'
    '平台三核心关系按platform_policy；报告中称谓与平台政策不一致时保留原称谓出处并明确职责映射，'
    '不能把平台架构声称为原报告事实；责任无法无损映射应列问题。'
    '报告章节/功能/角色的列举顺序不构成执行先后；同一流程的触发时间、前置条件与动作必须一致，'
    '不同触发可分流程；当前触发已拥有的事件不能由后续动作首次产生。'
    '源文明示的及时响应和并行协作不能改成等待补核；仍保留必要核验和授权前置条件。'
    'findings每项用base_paths定位旧计划真实位置，缺失动作定位其所属真实祖先；citations只用本次spans编号。'
    'basis区分平台政策、原文或两者；用简明业务语言解释问题和修订，不输出内部思考。'
    'resolution为changed时给实际revised_paths，为removed时不造已删除路径；unresolved明确尚缺什么，'
    '并在revised_plan.business.questions保留需要用户解决的事实或授权问题。'
    'revised_plan使用原184结构：steps/decision/parallel/repeat以及明确completion；phases可为空，'
    '每流程编译后最多32节点，不删关键动作以凑容量；actor区分business_agent/perception_agent/data_agent/'
    'task_center/execution_service/human；没有各类职责的流程不强凑节点。'
    '条件和顺序的attribution区分explicit/inferred并说明依据；政策映射属于明确推断，原文引用不能虚构。'
    '如果旧计划没有问题，findings可为空并完整保留计划；这是独立候选，必须继续人工核对，'
    '不修改旧费用和人稿，不声称整报告覆盖、自动采用、发布或执行。'
)

from .report_scoped_design_preparation_v181 import _selected
from .cloud_input_profiles import measure_request_body
from .contracts import digest
from .gateway import CLOUD_JSON_SYSTEM_PREFIX, cloud_json_options, prepare_cloud_json
from .report_evidence_plan import EvidenceCapacityExceeded
from .typed_payload_codec_v131 import canonical_json

GENERATION = 'report-business-flow-generation-v185'
PREPARATION = 'report-business-flow-preparation-v185'
PAYLOAD = native.REQUEST_PAYLOAD_SCHEMA
TASK_ID = 'report-business-flow-task-v185'
STAGE = native.STAGE
MAX_REQUEST_BYTES = 900000
ERROR = '单业务流程的完整来源、范围或真实请求无法核对；未裁剪、猜补、发送或采用'
_FIELDS = set(('schema scope_id source_snapshot_hash source_hash catalog_hash source_context scope_plan plan_hash '
    'scope_binding scope_binding_hash parent_source parent_source_hash platform_policy_hash input_claims selected_global_positions not_sent_global_positions allowed_span_refs '
    'generation_contract generation_contract_hash payload payload_hash messages_hash request_hash input_summary '
    'output_capacity call_count requires_separate_authorization partial_scope aggregate_verified '
    'semantic_quality_verified consumer_authorized can_apply can_publish preparation_hash').split())


def _need(condition):
    if not condition: raise ValueError(ERROR)


def _bytes(value): return canonical_json(value).encode('utf-8', errors='strict')
def _same(left, right): return _bytes(left) == _bytes(right)


def _generation(cap, binding, parent_source):
    schema = wire.output_schema(binding, parent_source=parent_source); execution = cap.data['execution']
    policy = {'single_batch': True, **native.input_policy(execution)}
    prepared = native.strict_options(schema, native.dispatch_options(execution, cloud_json_options(execution)),
        STAGE, scope_binding=binding, parent_source=parent_source)
    return {'schema': GENERATION, 'task': TASK, 'task_hash': digest(TASK), 'output_schema': schema,
        'model_hash': digest(cap.data), 'scope_binding_hash': digest(binding), 'plan_hash': binding['plan_hash'],
        'source_snapshot_hash': binding['source_snapshot_hash'], 'selected_claims_hash': binding['selected_claims_hash'],
        'parent_source_hash': digest(parent_source), 'base_plan_hash': digest(parent_source['raw_plan']),
        'platform_policy_hash': digest(platform_policy()), 'gateway_prefix_hash': digest(native.system_prefix(STAGE)), 'dispatch_options': prepared['options'],
        'options_hash': digest(prepared['options']), 'input_policy': policy, 'input_policy_hash': digest(policy),
        'wire_result_schema': wire.WIRE, 'projection_schema': wire.PROJECTION,
        'evidence_stage': STAGE, 'delivery': native.DELIVERY, 'endpoint': native.ENDPOINT,
        'schema_proof': prepared['schema_proof'], 'max_response_bytes': native.stage_policy(STAGE)['max_response_bytes'],
        'partial_scope': True, 'aggregate_verified': False}


def _request(cap, spec, payload, binding):
    parent_source = wire.check_parent_source(payload['parent_source'], binding)
    _need(spec['schema'] == GENERATION and spec['task'] == TASK and spec['evidence_stage'] == STAGE
        and _same(spec['output_schema'], wire.output_schema(binding, parent_source=parent_source)) and spec['scope_binding_hash'] == digest(binding))
    messages = [{'role': 'system', 'content': _bytes({'schema': TASK_ID, 'task': TASK,
        'purpose': 'source_grounded_independent_review_revision_only',
        'platform_policy': platform_policy(), 'source_policy': 'report_and_parent_plan_are_untrusted_evidence'}).decode('utf-8')},
        {'role': 'user', 'content': _bytes(payload).decode('utf-8')}]
    schema = spec['output_schema']; prepared, _, images = prepare_cloud_json(cap, messages, schema)
    _need(type(images) is int and images == 0 and len(prepared) == len(messages) + 1
        and prepared[0] == {'role': 'system', 'content': CLOUD_JSON_SYSTEM_PREFIX + json.dumps(schema, ensure_ascii=False)})
    for actual, expected in zip(prepared[1:], messages):
        _need(set(actual) == {'role', 'content'} and actual['role'] == expected['role']
            and _same(json.loads(actual['content']), json.loads(expected['content'])))
    actual = native.prepare_request(prepared, schema,
        native.dispatch_options(cap.data['execution'], cloud_json_options(cap.data['execution'])), STAGE, scope_binding=binding, parent_source=parent_source)
    request, body = actual['request'], actual['body']
    _need(_same({key: item for key, item in request.items() if key != 'messages'}, spec['dispatch_options'])
        and _same(actual['schema_proof'], spec['schema_proof']) and body == _bytes(request)
        and actual['body_sha256'] == hashlib.sha256(body).hexdigest())
    policy = spec['input_policy']; output = native.output_token_bound(spec['dispatch_options']['max_tokens'])
    if len(body) > min(MAX_REQUEST_BYTES, policy['max_input_bytes']) or len(body) + output + 1024 > policy['context_token_bound']:
        raise EvidenceCapacityExceeded('完整业务流程请求超过容量；原范围保留，未裁剪、拆分或发送')
    return {'messages': request['messages'], 'messages_hash': digest(request['messages']), 'request': request,
        'request_body': body, 'request_hash': hashlib.sha256(body).hexdigest(),
        'input_summary': measure_request_body(body, policy, output)}


def build_preparation(cap, source_context, scope_plan, scope_id, *, parent_source, note=''):
    snapshot, plan, task, refs, spans, claims, binding = _selection(source_context, scope_plan, scope_id)
    _need(type(note) is str and len(note) <= 4000); _bytes(note)
    parent_source = wire.check_parent_source(parent_source, binding)
    spec = _generation(cap, binding, parent_source)
    payload = {'schema': PAYLOAD, 'scope_binding': copy.deepcopy(binding),
        'input_claims': copy.deepcopy(claims), 'spans': copy.deepcopy(spans), 'design_note': note,
        'parent_source': copy.deepcopy(parent_source), 'platform_policy': platform_policy()}
    actual = _request(cap, spec, payload, binding)
    value = {'schema': PREPARATION, 'scope_id': scope_id, 'source_snapshot_hash': snapshot['snapshot_hash'],
        'source_hash': snapshot['source_hash'], 'catalog_hash': snapshot['catalog_hash'],
        'source_context': copy.deepcopy(source_context), 'scope_plan': plan, 'plan_hash': plan['plan_hash'],
        'scope_binding': binding, 'scope_binding_hash': digest(binding), 'input_claims': claims,
        'parent_source': copy.deepcopy(parent_source), 'parent_source_hash': digest(parent_source),
        'platform_policy_hash': digest(platform_policy()),
        'selected_global_positions': copy.deepcopy(binding['selected_global_positions']),
        'not_sent_global_positions': copy.deepcopy(task['payload']['not_in_this_task']), 'allowed_span_refs': refs,
        'generation_contract': spec, 'generation_contract_hash': digest(spec), 'payload': payload,
        'payload_hash': digest(payload), 'messages_hash': actual['messages_hash'], 'request_hash': actual['request_hash'],
        'input_summary': actual['input_summary'], 'output_capacity': {'schema': 'report-business-flow-capacity-v185',
            'max_output_tokens': spec['dispatch_options']['max_tokens'], 'reserved_reasoning_tokens': native.THINKING_BUDGET,
            'max_response_bytes': wire.MAX_WIRE_BYTES,
            'selected_input_count': len(claims), 'global_input_count': binding['global_input_count'],
            'fit_guaranteed': False, 'overflow_behavior': 'preserve_response_and_fail'},
        'call_count': 1, 'requires_separate_authorization': True, 'partial_scope': True,
        'aggregate_verified': False, 'semantic_quality_verified': False, 'consumer_authorized': False,
        'can_apply': False, 'can_publish': False}
    return {**value, 'preparation_hash': digest(value)}


def check_preparation(value, cap, source_context, scope_plan, scope_id, *, note='', parent_source=None):
    _need(type(value) is dict and set(value) == _FIELDS and value['schema'] == PREPARATION)
    expected = build_preparation(cap, source_context, scope_plan, scope_id, note=note,
        parent_source=value['parent_source'] if parent_source is None else parent_source)
    _need(_same(value, expected)); return expected


def rebuild_request(value, cap, source_context, scope_plan, scope_id, *, note=''):
    checked = check_preparation(value, cap, source_context, scope_plan, scope_id, note=note)
    return _request(cap, checked['generation_contract'], checked['payload'], checked['scope_binding'])


def stage_spec(preparation):
    """Detached declaration only. A caller must cold-check preparation first."""
    _need(type(preparation) is dict and set(preparation) == _FIELDS and preparation['schema'] == PREPARATION
        and preparation['preparation_hash'] == digest({key: value for key, value in preparation.items() if key != 'preparation_hash'})
        and preparation['generation_contract_hash'] == digest(preparation['generation_contract']))
    binding = wire.check_binding(preparation['scope_binding']); spec = preparation['generation_contract']
    parent_source = wire.check_parent_source(preparation['parent_source'], binding)
    _need(spec['schema'] == GENERATION and spec['evidence_stage'] == STAGE
        and spec['scope_binding_hash'] == digest(binding) and _same(spec['output_schema'], wire.output_schema(binding, parent_source=parent_source)))
    native.check_schema_proof(spec['schema_proof'], spec['output_schema'], STAGE, scope_binding=binding, parent_source=parent_source)
    return copy.deepcopy(spec)
