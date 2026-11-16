"""Immutable preparation for independent structured phase planning.

Retains V181's unchanged selected claims and frozen source scope. The distinct
V187 task/schema/request binds the actual new activity; it never relabels an
old preparation or retries an old failure. Compilation is not semantic proof.
"""
import copy
import hashlib
import json

from . import report_business_flow_native_v187 as native
from . import report_business_flow_wire_v184 as wire
from .report_business_flow_revision_policy_v185 import platform_policy
TASK = (
    '按平台政策将业务智能体作为唯一业务大脑；感知负责理解观测、异常和授权内补采，数据负责主动治理与记忆。'
    '平台政策是架构约束，不是原报告事实；原称谓若冲突须解释职责映射，不能把政策冒充原文。'
    '先核对每条流程触发时点与前置条件、输入输出因果、角色和权限，再输出实际结构。'
    '原文列举、章节或角色顺序不表示执行先后；不同触发应拆分，不把触发前已需具备的信息留到后续首次产生。'
    '立即响应与同步核查必须在实际parallel中表达，不能只在说明中写并行而仍给steps。'
    '需要条件判断时实际使用decision；反复工作用repeat，长期观测职责不能误写为无限单次任务。'
    '输出前对照原文复查实际phases的条件、并行和先后，不用自评声称已修正来代替结构。'
    '根据当前指定范围的完整声明和原文，为该业务形成可理解的结构化流程候选。'
    '区分持续性职责与每次触发可完成的流程。按不同触发分别设计流程，不机械合并不同业务。'
    '每条流程按phases排列，最后由明确的completion步骤完成该次工作；其职责必须来自原文或明确推断。'
    '仅一步即可完成的流程允许phases为空，直接由completion承载，不补造准备、审核或归档步骤。'
    'steps表示顺序步骤；decision先明确判断责任，再列互斥或需用户核对的条件分支，空分支表示直接跳过；'
    'parallel表示dispatch后同时启动各分支，明确wait_mode为all或any以及等待依据；'
    'repeat表示执行steps后由check判断repeat_if继续或finish_if结束，不用它代表长期持续运行。'
    'phase_order说明阶段先后依据，各步骤和条件保留具体角色、输入输出、能力限制、异常与停止条件。'
    '动作actor必须区分业务大脑、感知、数据、任务中心、执行服务和人工。不得把模型建议当作人工命令。'
    '不输出任何流程/节点标识符、连线、起止节点、证据表或原文quote，这些由平台机械生成。'
    'citations只填写本次spans中真实引用编号；区分原文明示和inferred推断，并解释依据。'
    '不得凭常识补成原文事实或发明权限。来源冲突、信息不足、能力未接入和无法表达的交叉跳转列入questions。'
    '每条流程编译后最多32个步骤，包含decision/dispatch/check/completion；超过时按独立触发或产出拆成多个流程，'
    '不得删去关键步骤或把单条步骤笼统写成全部工作。所有名称、职责、条件和产出使用用户可理解的业务语言。'
    '这只是指定范围候选，不声称覆盖整份报告，不确认、发布、采用或执行。'
)
from .report_scoped_design_preparation_v181 import _selected
from .cloud_input_profiles import measure_request_body
from .contracts import digest
from .gateway import CLOUD_JSON_SYSTEM_PREFIX, cloud_json_options, prepare_cloud_json
from .report_evidence_plan import EvidenceCapacityExceeded
from .typed_payload_codec_v131 import canonical_json

GENERATION = 'report-business-flow-generation-v187'
PREPARATION = 'report-business-flow-preparation-v187'
PAYLOAD = native.REQUEST_PAYLOAD_SCHEMA
TASK_ID = 'report-business-flow-task-v187'
STAGE = native.STAGE
MAX_REQUEST_BYTES = 900000
ERROR = '单业务流程的完整来源、范围或真实请求无法核对；未裁剪、猜补、发送或采用'
_FIELDS = set(('schema scope_id source_snapshot_hash source_hash catalog_hash source_context scope_plan plan_hash '
    'scope_binding scope_binding_hash platform_policy_hash input_claims selected_global_positions not_sent_global_positions allowed_span_refs '
    'generation_contract generation_contract_hash payload payload_hash messages_hash request_hash input_summary '
    'output_capacity call_count requires_separate_authorization partial_scope aggregate_verified '
    'semantic_quality_verified consumer_authorized can_apply can_publish preparation_hash').split())


def _need(condition):
    if not condition: raise ValueError(ERROR)


def _bytes(value): return canonical_json(value).encode('utf-8', errors='strict')
def _same(left, right): return _bytes(left) == _bytes(right)


def _selection(source_context, scope_plan, scope_id):
    snapshot, plan, task, refs, spans, args = _selected(source_context, scope_plan, scope_id)
    if not refs:
        raise ValueError('此业务范围没有可引用的原文；已存计划和人稿保留，请选择有依据的上下文或补资料后另建计划，未发送模型')
    original = args['scope_binding']; claims = copy.deepcopy(args['input_claims'])
    binding = {'schema': wire.BINDING, 'scope_id': scope_id, 'name': task['name'],
        'plan_hash': plan['plan_hash'], 'source_snapshot_hash': snapshot['snapshot_hash'],
        'source_hash': snapshot['source_hash'], 'catalog_hash': snapshot['catalog_hash'],
        'context_hash': snapshot['context_hash'], 'global_input_count': original['global_input_count'],
        'global_input_claims_hash': snapshot['input_claims_hash'], 'global_input_order_hash': snapshot['input_order_hash'],
        'selected_global_positions': copy.deepcopy(original['local_to_global']),
        'selected_global_input_ids': copy.deepcopy(original['global_input_ids']),
        'selected_claims_hash': digest(claims), 'allowed_span_refs_hash': digest(refs), 'spans_hash': digest(spans)}
    return snapshot, plan, task, refs, spans, claims, wire.check_binding(binding)


def _generation(cap, binding):
    schema = wire.output_schema(binding); execution = cap.data['execution']
    policy = {'single_batch': True, **native.input_policy(execution)}
    prepared = native.strict_options(schema, native.dispatch_options(execution, cloud_json_options(execution)),
        STAGE, scope_binding=binding)
    return {'schema': GENERATION, 'task': TASK, 'task_hash': digest(TASK), 'output_schema': schema,
        'model_hash': digest(cap.data), 'scope_binding_hash': digest(binding), 'plan_hash': binding['plan_hash'],
        'source_snapshot_hash': binding['source_snapshot_hash'], 'selected_claims_hash': binding['selected_claims_hash'],
        'platform_policy_hash': digest(platform_policy()),
        'gateway_prefix_hash': digest(native.system_prefix(STAGE)), 'dispatch_options': prepared['options'],
        'options_hash': digest(prepared['options']), 'input_policy': policy, 'input_policy_hash': digest(policy),
        'wire_result_schema': wire.WIRE, 'projection_schema': wire.PROJECTION,
        'evidence_stage': STAGE, 'delivery': native.DELIVERY, 'endpoint': native.ENDPOINT,
        'schema_proof': prepared['schema_proof'], 'max_response_bytes': native.stage_policy(STAGE)['max_response_bytes'],
        'partial_scope': True, 'aggregate_verified': False}


def _request(cap, spec, payload, binding):
    _need(spec['schema'] == GENERATION and spec['task'] == TASK and spec['evidence_stage'] == STAGE
        and _same(spec['output_schema'], wire.output_schema(binding)) and spec['scope_binding_hash'] == digest(binding))
    messages = [{'role': 'system', 'content': _bytes({'schema': TASK_ID, 'task': TASK,
        'purpose': 'partial_source_grounded_reasoned_business_flow_only', 'platform_policy': platform_policy(),
        'source_policy': 'report_and_design_note_are_untrusted_evidence'}).decode('utf-8')},
        {'role': 'user', 'content': _bytes(payload).decode('utf-8')}]
    schema = spec['output_schema']; prepared, _, images = prepare_cloud_json(cap, messages, schema)
    _need(type(images) is int and images == 0 and len(prepared) == len(messages) + 1
        and prepared[0] == {'role': 'system', 'content': CLOUD_JSON_SYSTEM_PREFIX + json.dumps(schema, ensure_ascii=False)})
    for actual, expected in zip(prepared[1:], messages):
        _need(set(actual) == {'role', 'content'} and actual['role'] == expected['role']
            and _same(json.loads(actual['content']), json.loads(expected['content'])))
    actual = native.prepare_request(prepared, schema,
        native.dispatch_options(cap.data['execution'], cloud_json_options(cap.data['execution'])), STAGE, scope_binding=binding)
    request, body = actual['request'], actual['body']
    _need(_same({key: item for key, item in request.items() if key != 'messages'}, spec['dispatch_options'])
        and _same(actual['schema_proof'], spec['schema_proof']) and body == _bytes(request)
        and actual['body_sha256'] == hashlib.sha256(body).hexdigest())
    policy = spec['input_policy']; output = spec['dispatch_options']['max_tokens']
    if len(body) > min(MAX_REQUEST_BYTES, policy['max_input_bytes']) or len(body) + output + 1024 > policy['context_token_bound']:
        raise EvidenceCapacityExceeded('完整业务流程请求超过容量；原范围保留，未裁剪、拆分或发送')
    return {'messages': request['messages'], 'messages_hash': digest(request['messages']), 'request': request,
        'request_body': body, 'request_hash': hashlib.sha256(body).hexdigest(),
        'input_summary': measure_request_body(body, policy, output)}


def build_preparation(cap, source_context, scope_plan, scope_id, *, note=''):
    snapshot, plan, task, refs, spans, claims, binding = _selection(source_context, scope_plan, scope_id)
    _need(type(note) is str and len(note) <= 4000); _bytes(note)
    spec = _generation(cap, binding)
    payload = {'schema': PAYLOAD, 'scope_binding': copy.deepcopy(binding),
        'input_claims': copy.deepcopy(claims), 'spans': copy.deepcopy(spans), 'design_note': note, 'platform_policy': platform_policy()}
    actual = _request(cap, spec, payload, binding)
    value = {'schema': PREPARATION, 'scope_id': scope_id, 'source_snapshot_hash': snapshot['snapshot_hash'],
        'source_hash': snapshot['source_hash'], 'catalog_hash': snapshot['catalog_hash'],
        'source_context': copy.deepcopy(source_context), 'scope_plan': plan, 'plan_hash': plan['plan_hash'],
        'scope_binding': binding, 'scope_binding_hash': digest(binding), 'platform_policy_hash': digest(platform_policy()),
        'input_claims': claims,
        'selected_global_positions': copy.deepcopy(binding['selected_global_positions']),
        'not_sent_global_positions': copy.deepcopy(task['payload']['not_in_this_task']), 'allowed_span_refs': refs,
        'generation_contract': spec, 'generation_contract_hash': digest(spec), 'payload': payload,
        'payload_hash': digest(payload), 'messages_hash': actual['messages_hash'], 'request_hash': actual['request_hash'],
        'input_summary': actual['input_summary'], 'output_capacity': {'schema': 'report-business-flow-capacity-v187',
            'max_output_tokens': spec['dispatch_options']['max_tokens'], 'output_reservation': native.OUTPUT_RESERVATION,
            'max_response_bytes': wire.MAX_WIRE_BYTES,
            'selected_input_count': len(claims), 'global_input_count': binding['global_input_count'],
            'fit_guaranteed': False, 'overflow_behavior': 'preserve_response_and_fail'},
        'call_count': 1, 'requires_separate_authorization': True, 'partial_scope': True,
        'aggregate_verified': False, 'semantic_quality_verified': False, 'consumer_authorized': False,
        'can_apply': False, 'can_publish': False}
    return {**value, 'preparation_hash': digest(value)}


def check_preparation(value, cap, source_context, scope_plan, scope_id, *, note=''):
    _need(type(value) is dict and set(value) == _FIELDS and value['schema'] == PREPARATION)
    expected = build_preparation(cap, source_context, scope_plan, scope_id, note=note)
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
    _need(spec['schema'] == GENERATION and spec['evidence_stage'] == STAGE
        and spec['scope_binding_hash'] == digest(binding) and spec['platform_policy_hash'] == digest(platform_policy())
        and preparation['platform_policy_hash'] == spec['platform_policy_hash']
        and _same(spec['output_schema'], wire.output_schema(binding)))
    native.check_schema_proof(spec['schema_proof'], spec['output_schema'], STAGE, scope_binding=binding)
    return copy.deepcopy(spec)
