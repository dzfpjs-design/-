"""Deterministic authored-service graph; no report, provider or device action.

Design: packet -> actual reasoning -> registered plan -> actual context reader
-> context resense -> original human steps. References retain actual upstream
products. Observation extractors are declarations, never invented graph nodes.
"""
import copy

from . import authored_workflow_graph_v208 as human
from . import business_reasoning_v197 as reasoning
from . import workflow_document_packet_v207 as packet
from . import workflow_registered_tool_plan_v201 as planner
from . import workflow_service_context_v196 as resense
from . import workflow_task_context_v196 as reader
from .authored_service_workflow_contracts_v270 import BINDING, OPERATIONS, TEMPLATE
from .contracts import digest
from .full_semantic_graph_v1 import validate_graph


ROLES = {'packet': 'data', 'reasoning': 'business', 'planner': 'business',
    'context_reader': 'data', 'resense': 'business'}
POSTS = {'packet': '运行期资料处理人', 'reasoning': '候选方案分析人',
    'planner': '已批准候选动作执行人', 'context_reader': '当前任务资料核对人',
    'resense': '批准范围补采执行人'}
LABELS = {'packet': '保存本次已批准的运行期资料包', 'reasoning': '根据原证据比较完整候选',
    'planner': '提交原推荐候选的已登记模拟动作并保留回执',
    'context_reader': '核对当前任务缺口与已批准的补采策略',
    'resense': '依真实上下文提交补采并等待实际感知和数据回执'}
PIN_KEYS = {'capability_id', 'capability_hash', 'agent_role', 'input_bindings', 'arguments',
    'input_schema', 'output_schema', 'adapter', 'release_version', 'max_attempts'}
_DELEGATIONS = {
    'planner': {'service_actor': 'execution_service', 'operation': planner.OP, 'agent_role': 'business'},
    'resense': {'service_actor': 'execution_service', 'operation': resense.OP, 'agent_role': 'business'},
}
same, need = human.same, human.need


def tool_role(business_name, operation):
    need(type(business_name) is str and 1 <= len(business_name) <= 100
        and business_name.strip() and '\x00' not in business_name)
    need(operation in OPERATIONS, '请选择本配方真实存在的固定端口')
    return POSTS[operation] + '（' + business_name + '·V270）'


def linear_flow(flow):
    result = human.linear_flow(flow)
    need(not any(step['role'].startswith(prefix + '（') and step['role'].endswith('·V270）')
        for step in result['steps'] for prefix in POSTS.values()), '原人工岗位与服务配方保留岗位冲突')
    return result


def declarations():
    return copy.deepcopy(_DELEGATIONS)


def service_delegations(binding):
    """Pure explicit-family consistency, never saved-adoption/live authority."""
    need(type(binding) is dict and binding.get('schema') == BINDING
        and binding.get('template') == TEMPLATE and binding.get('simulation_only') is True)
    need(same(binding.get('service_delegations'), _DELEGATIONS), '服务委托缺失或不属于此固定配方')
    nodes = {node['id']: node for node in binding['graph']['nodes']}
    actual = {key for key, node in nodes.items() if node['kind'] == 'ability'
        and node['execution']['adapter'] == 'workflow_service'}
    need(actual == set(_DELEGATIONS), '服务委托与实际编译图节点不一致')
    for key, value in _DELEGATIONS.items():
        need(nodes[key]['execution']['agent_role'] == value['agent_role'])
    return declarations()


def _schemas(operation):
    modules = {'packet': packet, 'reasoning': reasoning, 'planner': planner,
        'context_reader': reader, 'resense': resense}
    module = modules[operation]
    return module.input_schema(), module.output_schema()


def _execution(operation, original, bindings, arguments):
    need(type(original) is dict and set(original) == PIN_KEYS, '须冻结完整已发布能力的实际端口合同')
    inputs, outputs = _schemas(operation)
    adapter = {'packet': 'runtime_document_packet', 'reasoning': 'business_reasoning',
        'planner': 'workflow_service', 'context_reader': 'builtin', 'resense': 'workflow_service'}[operation]
    need(original['agent_role'] == ROLES[operation] and original['adapter'] == adapter
        and same(original['input_schema'], inputs) and same(original['output_schema'], outputs),
        '实际能力角色、适配器或完整输入输出合同不符合此端口')
    need(any(same(original['input_bindings'], value) for value in ({}, bindings))
        and any(same(original['arguments'], value) for value in ({}, arguments)),
        '此配方不能替换真实生产者、注入模型结果或填写静态上下文')
    result = copy.deepcopy(original)
    result.update(input_bindings=copy.deepcopy(bindings), arguments=copy.deepcopy(arguments))
    return result


def _projection(producer, consumer, pointer='', *, nonnull=False):
    return {'schema': 'guarded-port-projection-v198', 'source': 'port_projection',
        'kind': 'object', 'nullable': False, 'unit': None, 'consumer_node_id': consumer,
        'origin': {'node_id': producer, 'port_ordinal': 0, 'output_pointer': ''},
        'projection': ({'op': 'select', 'pointer': pointer, 'require_non_null': True}
            if nonnull else {'op': 'copy', 'pointer': pointer})}


def build_graph(flow, role_bindings, tool_pins, tool_actors, *, business_name,
                candidate_proposals, candidate_plans):
    from .authored_service_workflow_contracts_v270 import CandidatePlan
    from .runtime_task_documents import ref_schema
    flow = linear_flow(flow)
    need(type(tool_pins) is dict and set(tool_pins) == set(OPERATIONS)
        and type(tool_actors) is dict and set(tool_actors) == set(OPERATIONS))
    need(type(candidate_proposals) is list and 1 <= len(candidate_proposals) <= 16
        and all(type(value) is str and value.strip() and len(value) <= 2000 for value in candidate_proposals))
    plans = [CandidatePlan.model_validate(value).model_dump(mode='json') for value in candidate_plans]
    need(same(plans, candidate_plans) and [value['candidate_ref'] for value in plans]
        == ['candidate_' + str(i) for i in range(1, len(candidate_proposals) + 1)], '候选说明与实际动作计划必须逐项同号')
    posts = {key: tool_role(business_name, key) for key in OPERATIONS}
    need(type(role_bindings) is dict and set(role_bindings)
        == {step['role'] for step in flow['steps']} | set(posts.values()))
    for key, post in posts.items():
        need(role_bindings[post]['user_id'] == tool_actors[key], '固定节点执行人与真实岗位不一致')
    candidates = [{'candidate_ref': value['candidate_ref'], 'summary': text, 'context': None}
        for value, text in zip(plans, candidate_proposals, strict=True)]
    fixed = {
        'packet': ({'source_set_ref': 'selected_sources'}, {}),
        'reasoning': ({'question': 'analysis_question', 'evidence_ids': 'packet_evidence'}, {'candidates': candidates}),
        'planner': ({'reasoning_result': 'reasoning_result'}, {'candidate_plans': plans}),
        'context_reader': ({'policy_pin': 'policy_pin'}, {}),
        'resense': ({'context': 'reader_context'}, {}),
    }
    executions = {key: _execution(key, tool_pins[key], *fixed[key]) for key in OPERATIONS}
    nodes = [{'id': key, 'kind': 'ability', 'source_step_id': None, 'guards': human.guards(),
        'actor_id': tool_actors[key], 'execution': executions[key]} for key in OPERATIONS]
    mapping = []
    for step in flow['steps']:
        identity = 'human_' + digest(step['id'])[:24]
        nodes.append({'id': identity, 'kind': 'human', 'source_step_id': step['id'], 'guards': human.guards(),
            'actor_id': role_bindings[step['role']]['user_id'], 'role': step['role'],
            'output_schema': {'type': 'object', 'properties': {'result': {'type': 'string', 'minLength': 1,
                'maxLength': 12000, 'pattern': r'\S', 'title': step['output']}},
                'required': ['result'], 'additionalProperties': False}})
        mapping.append({'source_step_id': step['id'], 'node_id': identity,
            'name': step['name'], 'role': step['role'], 'output': step['output']})
    order = [node['id'] for node in nodes]
    nodes.append({'id': 'end', 'kind': 'end', 'source_step_id': None, 'guards': human.guards()})
    properties = {human.INPUT_KEY: ref_schema(),
        'analysis_question': {'type': 'string', 'minLength': 1, 'maxLength': 4000,
            'pattern': r'\S', 'title': '本次需要分析的问题'},
        'policy_pin': copy.deepcopy(reader.input_schema()['properties']['policy_pin'])}
    graph = {'schema': 'full-semantic-graph-v1', 'id': 'authored_service_' + digest({
        'flow': flow, 'business': business_name, 'candidates': plans})[:24], 'entry': 'packet',
        'input_schema': {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False},
        'references': {'selected_sources': {'kind': 'object', 'nullable': False,
                'source': 'input', 'field': human.INPUT_KEY, 'node_id': None},
            'analysis_question': {'kind': 'string', 'nullable': False,
                'source': 'input', 'field': 'analysis_question', 'node_id': None},
            'packet_evidence': {'kind': 'array', 'nullable': False,
                'source': 'output', 'field': 'evidence_ids', 'node_id': 'packet'},
            'reasoning_result': _projection('reasoning', 'planner'),
            'policy_pin': {'kind': 'object', 'nullable': True,
                'source': 'input', 'field': 'policy_pin', 'node_id': None},
            'reader_context': _projection('context_reader', 'resense', '/resense_context', nonnull=True)},
        'predicates': {}, 'guards': human.guards(), 'nodes': nodes,
        'edges': [{'id': 'edge_' + str(i), 'from': key, 'to': target, 'on': 'success',
            'branch_id': None, 'loop_id': None}
            for i, (key, target) in enumerate(zip(order, [*order[1:], 'end']), 1)],
        'loops': [], 'subscriptions': [], 'goals': [{'id': 'business_complete', 'required_nodes': order}]}
    return {'template': TEMPLATE, 'graph': validate_graph(graph), 'step_mapping': mapping,
        'service_delegations': declarations()}


def projection(binding):
    need(binding.get('schema') == BINDING and binding.get('template') == TEMPLATE)
    mapping = {item['node_id']: item for item in binding['step_mapping']}
    return {'authorship': 'human_authored', 'model_generated': False,
        'semantic_quality_verified': False, 'simulation_only': True,
        'source_explanation': '依据当前已确认的人工流程、真实已发布能力和明确候选编制；无报告理解或现场状态声明。',
        'business_name': binding['business_name'], 'trigger': binding['flow']['trigger'], 'output': binding['flow']['output'],
        'steps': [{'node_id': node['id'], 'kind': node['kind'],
            'name': LABELS[node['id']] if node['kind'] == 'ability' else mapping[node['id']]['name'],
            'actor_id': node['actor_id']} for node in binding['graph']['nodes'] if node['kind'] in {'ability', 'human'}],
        'observation_extractors': [{'capability_id': key, 'executes_in_this_graph': False}
            for key in sorted(binding['observation_extractor_pins'])],
        'uses_model': True, 'can_execute': False, 'cloud_authorized': False, 'physical_state_verified': False,
        'limitations': ['首版仅支持模拟范围；供应方持久采样回执不等于现场观测。',
            '补采上下文仅采用本任务实际读节点输出；为空或受阻时不提交补采。',
            '异步感知提取声明不表示图节点已执行；实际观测和数据回执仍由原运行链核验。']}
