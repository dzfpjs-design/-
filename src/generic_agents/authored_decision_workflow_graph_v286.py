"""V286 source-preserving routes with explicit human review of other advice."""
import copy

from . import authored_workflow_graph_v208 as human
from . import business_decision_runtime_v284 as reasoning
from . import workflow_task_packet_v199 as data_packet
from . import workflow_document_packet_v207 as packet
from . import workflow_decision_tool_planner_v285 as planner
from . import workflow_service_context_v196 as resense
from . import workflow_task_context_v196 as reader
from .authored_decision_workflow_contracts_v286 import BINDING, OPERATIONS, TEMPLATE
from .contracts import digest
from .full_semantic_graph_v1 import validate_graph

ROLES = {'data_packet': 'data', 'packet': 'data', 'reasoning': 'business', 'planner': 'business', 'resense': 'business'}
POSTS = {'data_packet': '目标资料核对执行人', 'packet': '文档处理执行人', 'reasoning': '业务分析执行人',
    'planner': '业务计划执行人', 'resense': '补充观测执行人'}
REVIEW_POST = '业务建议核对人'
REVIEW_NODE = 'review_decision'
LABELS = {'data_packet': '保存正式目标原资料查询与收件来源', 'packet': '保存已批准的运行文档摘录包',
    'reasoning': '按原事实与登记能力比较候选', 'planner': '保存本地决策路由计划', 'resense': '按原上下文补采并等待独立收件'}
PIN_KEYS = {'capability_id', 'capability_hash', 'agent_role', 'input_bindings', 'arguments',
    'input_schema', 'output_schema', 'adapter', 'release_version', 'max_attempts'}
_DELEGATIONS = {'planner': {'service_actor': 'execution_service', 'operation': planner.OP, 'agent_role': 'business'},
    'resense': {'service_actor': 'execution_service', 'operation': resense.OP, 'agent_role': 'business'}}
same, need = human.same, human.need


def tool_role(business_name, operation):
    need(type(business_name) is str and 1 <= len(business_name) <= 100 and business_name.strip() and '\x00' not in business_name)
    need(operation in OPERATIONS)
    return POSTS[operation] + '（' + business_name + '）'


def review_role(business_name):
    need(type(business_name) is str and 1 <= len(business_name) <= 100 and business_name.strip() and '\x00' not in business_name)
    return REVIEW_POST + '（' + business_name + '）'


def linear_flow(flow):
    result = human.linear_flow(flow)
    need(not any(step['role'].startswith(prefix + '（') and step['role'].endswith('）')
        for step in result['steps'] for prefix in (*POSTS.values(), REVIEW_POST)), '原人工岗位与决策保留岗位冲突')
    return result


def declarations():
    return copy.deepcopy(_DELEGATIONS)


def service_delegations(binding):
    need(type(binding) is dict and binding.get('schema') == BINDING and binding.get('template') == TEMPLATE
        and binding.get('simulation_only') is True and same(binding.get('service_delegations'), _DELEGATIONS))
    nodes = {node['id']: node for node in binding['graph']['nodes']}
    actual = {key for key, node in nodes.items() if node['kind'] == 'ability' and node['execution']['adapter'] == 'workflow_service'}
    need(actual == set(_DELEGATIONS))
    for key, value in _DELEGATIONS.items(): need(nodes[key]['execution']['agent_role'] == value['agent_role'])
    return declarations()


def _schemas(operation):
    module = {'data_packet': data_packet, 'packet': packet, 'reasoning': reasoning, 'planner': planner, 'resense': resense}[operation]
    return module.input_schema(), module.output_schema()


def _execution(operation, original, bindings, arguments):
    need(type(original) is dict and set(original) == PIN_KEYS)
    inputs, outputs = _schemas(operation)
    adapter = {'data_packet': 'task_evidence_packet', 'packet': 'runtime_document_packet',
        'reasoning': reasoning.ADAPTER, 'planner': 'workflow_service', 'resense': 'workflow_service'}[operation]
    need(original['agent_role'] == ROLES[operation] and original['adapter'] == adapter
        and same(original['input_schema'], inputs) and same(original['output_schema'], outputs))
    need(any(same(original['input_bindings'], value) for value in ({}, bindings))
        and any(same(original['arguments'], value) for value in ({}, arguments)), '不能注入静态结果、上下文或更换原生产者')
    return {**copy.deepcopy(original), 'input_bindings': copy.deepcopy(bindings), 'arguments': copy.deepcopy(arguments)}


def _projection(producer, consumer, pointer='', *, nonnull=False):
    return {'schema': 'guarded-port-projection-v198', 'source': 'port_projection', 'kind': 'object',
        'nullable': False, 'unit': None, 'consumer_node_id': consumer,
        'origin': {'node_id': producer, 'port_ordinal': 0, 'output_pointer': ''},
        'projection': {'op': 'select', 'pointer': pointer, 'require_non_null': True} if nonnull
            else {'op': 'copy', 'pointer': pointer}}


def build_graph(flow, role_bindings, tool_pins, tool_actors, *, business_name, candidate_specs, reviewer_id,
                decision_intent='information_acquisition'):
    from .business_decision_frame_v276 import CandidateSpec
    from .runtime_task_documents import ref_schema
    flow = linear_flow(flow)
    specs = [CandidateSpec.model_validate(item).model_dump(mode='json') for item in candidate_specs]
    need(same(specs, candidate_specs) and 1 <= len(specs) <= 16 and decision_intent == 'information_acquisition')
    need(type(tool_pins) is dict and set(tool_pins) == set(OPERATIONS)
        and type(tool_actors) is dict and set(tool_actors) == set(OPERATIONS))
    posts = {key: tool_role(business_name, key) for key in OPERATIONS}
    review_post = review_role(business_name)
    need(type(role_bindings) is dict
        and set(role_bindings) == {step['role'] for step in flow['steps']} | set(posts.values()) | {review_post})
    for key, post in posts.items(): need(role_bindings[post]['user_id'] == tool_actors[key])
    need(role_bindings[review_post]['user_id'] == reviewer_id)
    fixed = {'data_packet': ({'policy_pin': 'policy_pin'}, {}),
        'packet': ({'source_set_ref': 'selected_sources'}, {}),
        'reasoning': ({'question': 'analysis_question', 'data_packet': 'original_data_packet', 'document_packet': 'original_document_packet'},
            {'candidate_specs': specs, 'decision_intent': decision_intent}),
        'planner': ({'decision_result': 'decision_result'}, {}), 'resense': ({'context': 'planned_context'}, {})}
    nodes = [{'id': key, 'kind': 'ability', 'source_step_id': None, 'guards': human.guards(),
        'actor_id': tool_actors[key], 'execution': _execution(key, tool_pins[key], *fixed[key])} for key in OPERATIONS]
    nodes.append({'id': 'route_acquisition', 'kind': 'decision', 'source_step_id': None,
        'guards': human.guards(), 'predicate_id': 'needs_acquisition'})
    nodes.append({'id': 'route_continue', 'kind': 'decision', 'source_step_id': None,
        'guards': human.guards(), 'predicate_id': 'can_continue'})
    nodes.append({'id': REVIEW_NODE, 'kind': 'human', 'source_step_id': None,
        'guards': human.guards(), 'actor_id': reviewer_id, 'role': review_post,
        'output_schema': {'type': 'object', 'properties': {'result': {'type': 'string', 'minLength': 1,
            'maxLength': 12000, 'pattern': r'\S', 'title': '业务建议核对意见'}},
            'required': ['result'], 'additionalProperties': False}})
    step_mapping = []
    for step in flow['steps']:
        identity = 'human_' + digest(step['id'])[:24]
        nodes.append({'id': identity, 'kind': 'human', 'source_step_id': step['id'], 'guards': human.guards(),
            'actor_id': role_bindings[step['role']]['user_id'], 'role': step['role'],
            'output_schema': {'type': 'object', 'properties': {'result': {'type': 'string', 'minLength': 1,
                'maxLength': 12000, 'pattern': r'\S', 'title': step['output']}}, 'required': ['result'], 'additionalProperties': False}})
        step_mapping.append({'source_step_id': step['id'], 'node_id': identity, 'name': step['name'], 'role': step['role'], 'output': step['output']})
    nodes.append({'id': 'end', 'kind': 'end', 'source_step_id': None, 'guards': human.guards()})
    humans = [item['node_id'] for item in step_mapping]; need(bool(humans))
    links = [('data_packet', 'packet', 'success'), ('packet', 'reasoning', 'success'), ('reasoning', 'planner', 'success'),
        ('planner', 'route_acquisition', 'success'), ('route_acquisition', 'resense', 'true'),
        ('route_acquisition', 'route_continue', 'false'), ('resense', humans[0], 'success'),
        ('route_continue', humans[0], 'true'), ('route_continue', REVIEW_NODE, 'false'),
        (REVIEW_NODE, humans[0], 'success')]
    links += [(source, target, 'success') for source, target in zip(humans, [*humans[1:], 'end'])]
    properties = {human.INPUT_KEY: ref_schema(), 'analysis_question': {'type': 'string', 'minLength': 1,
        'maxLength': 4000, 'pattern': r'\S', 'title': '本次决策问题'},
        'policy_pin': copy.deepcopy(reader.input_schema()['properties']['policy_pin'])}
    graph = {'schema': 'full-semantic-graph-v1', 'id': 'authored_decision_review_v286_' + digest({
        'flow': flow, 'business': business_name, 'candidate_specs': specs})[:24], 'entry': 'data_packet',
        'input_schema': {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False},
        'references': {'selected_sources': {'kind': 'object', 'nullable': False, 'source': 'input', 'field': human.INPUT_KEY, 'node_id': None},
            'analysis_question': {'kind': 'string', 'nullable': False, 'source': 'input', 'field': 'analysis_question', 'node_id': None},
            'policy_pin': {'kind': 'object', 'nullable': True, 'source': 'input', 'field': 'policy_pin', 'node_id': None},
            'original_data_packet': _projection('data_packet', 'reasoning', '/packet'),
            'original_document_packet': _projection('packet', 'reasoning', '/packet'),
            'decision_result': _projection('reasoning', 'planner'),
            'plan_route': {'kind': 'string', 'nullable': False, 'source': 'output', 'field': 'route', 'node_id': 'planner'},
            'planned_context': _projection('planner', 'resense', '/resense_context', nonnull=True)},
        'predicates': {'needs_acquisition': {'op': 'eq', 'left': {'op': 'ref', 'key': 'plan_route'},
            'right': {'op': 'value', 'kind': 'string', 'value': 'acquire'}},
            'can_continue': {'op': 'eq', 'left': {'op': 'ref', 'key': 'plan_route'},
                'right': {'op': 'value', 'kind': 'string', 'value': 'continue'}}},
        'guards': human.guards(), 'nodes': nodes,
        'edges': [{'id': 'edge_' + str(index), 'from': source, 'to': target, 'on': on, 'branch_id': None, 'loop_id': None}
            for index, (source, target, on) in enumerate(links, 1)],
        'loops': [], 'subscriptions': [], 'goals': [{'id': 'business_complete',
            'required_nodes': ['data_packet', 'packet', 'reasoning', 'planner', *humans]}]}
    return {'template': TEMPLATE, 'graph': validate_graph(graph), 'step_mapping': step_mapping, 'service_delegations': declarations()}


def projection(binding):
    need(binding.get('schema') == BINDING and binding.get('template') == TEMPLATE)
    mapping = {item['node_id']: item for item in binding['step_mapping']}
    return {'authorship': 'human_authored', 'model_generated': False, 'semantic_quality_verified': False,
        'simulation_only': True, 'business_name': binding['business_name'], 'trigger': binding['flow']['trigger'],
        'output': binding['flow']['output'],
        'source_explanation': '两份实际来源包、业务建议与本地计划；“核对业务建议”为平台新增人工核对，不是来源文档原步骤；无现场事实或执行权限声明。',
        'steps': [{'node_id': node['id'], 'kind': node['kind'], 'actor_id': node['actor_id'],
            'name': LABELS[node['id']] if node['kind'] == 'ability' else
                '核对业务建议' if node['id'] == REVIEW_NODE else mapping[node['id']]['name']}
            for node in binding['graph']['nodes'] if node['kind'] in {'ability', 'human'}],
        'observation_extractors': [{'capability_id': key, 'executes_in_this_graph': False} for key in sorted(binding['observation_extractor_pins'])],
        'conditional_resense': {'acquire': 'resense', 'continue': '原人工步骤',
            'no_action': REVIEW_NODE, 'review_required': REVIEW_NODE}, 'uses_model': True,
        'can_execute': False, 'cloud_authorized': False, 'physical_state_verified': False,
        'limitations': ['当前仅支持明确模拟范围和信息补采提议。', '原事实无缺口时不调用补采服务。',
            '无行动建议及待核对建议须经明确指派的人员核对后进入原人工步骤。',
            '人工核对不替代原目标完成条件、执行审批、实际感知与独立数据收件。']}
