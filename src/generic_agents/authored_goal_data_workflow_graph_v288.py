"""Frozen V288 graph: approved local acquisition and ACK precede reasoning.

Only the real modules supply schemas. No placeholder schema enables a missing
producer, and neither manual review path replaces the original Goal criteria.
"""
import copy

from . import authored_workflow_graph_v208 as human
from . import workflow_document_packet_v207 as packet
from . import workflow_service_context_v196 as resense
from . import workflow_task_context_v196 as reader
from .authored_goal_data_workflow_contracts_v288 import BINDING, OPERATIONS, TEMPLATE, configured_policy
from .contracts import digest
from .full_semantic_graph_v1 import validate_graph

ROLES = {'context_reader': 'data', 'local_prepare': 'business', 'resense': 'business',
    'evaluation_packet': 'data', 'packet': 'data', 'reasoning': 'business'}
POSTS = {'context_reader': '原目标资料核对人', 'local_prepare': '批准范围资料准备人',
    'resense': '独立收件补采执行人', 'evaluation_packet': '新目标评价保存人',
    'packet': '文档处理执行人', 'reasoning': '资料就绪业务分析人'}
REVIEW_POST = '资料与业务建议核对人'
REVIEW_NODE = 'review_decision'
DATA_REVIEW_NODE = 'review_data'
LABELS = {'context_reader': '读取原目标、原查询缺口与已批准策略',
    'local_prepare': '在本地核对固定资料准备策略并确定分支',
    'resense': '按原批准上下文补采并等待独立数据收件',
    'evaluation_packet': '保持原查询并保存新的同目标评价与真实查询',
    'packet': '保存已批准的运行文档摘录包', 'reasoning': '只根据已就绪评价资料比较候选'}
PIN_KEYS = {'capability_id', 'capability_hash', 'agent_role', 'input_bindings', 'arguments',
    'input_schema', 'output_schema', 'adapter', 'release_version', 'max_attempts'}
_DELEGATIONS = {
    'local_prepare': {'service_actor': 'execution_service', 'operation': 'plan_approved_goal_data_v288', 'agent_role': 'business'},
    'resense': {'service_actor': 'execution_service', 'operation': resense.OP, 'agent_role': 'business'},
    'evaluation_packet': {'service_actor': 'reader_service', 'operation': 'create_goal_evaluation_packet_v288', 'agent_role': 'data'},
}
same, need = human.same, human.need


def tool_role(business_name, operation):
    need(type(business_name) is str and 1 <= len(business_name) <= 100 and business_name.strip() and '\x00' not in business_name)
    need(operation in OPERATIONS)
    return POSTS[operation] + '（' + business_name + '·V288）'


def review_role(business_name):
    need(type(business_name) is str and 1 <= len(business_name) <= 100 and business_name.strip() and '\x00' not in business_name)
    return REVIEW_POST + '（' + business_name + '·V288）'


def linear_flow(flow):
    result = human.linear_flow(flow)
    need(not any(step['role'].startswith(prefix + '（') and step['role'].endswith('·V288）')
        for step in result['steps'] for prefix in (*POSTS.values(), REVIEW_POST)), '原人工岗位与资料准备保留岗位冲突')
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


def module_for(operation):
    # Fixed server imports only. Missing runtime modules fail closed; schemas
    # must never be guessed from a client pin or filled with permissive objects.
    if operation == 'local_prepare':
        from . import workflow_goal_data_route_runtime_v288
        return workflow_goal_data_route_runtime_v288
    if operation == 'evaluation_packet':
        from . import workflow_goal_evaluation_packet_v288
        return workflow_goal_evaluation_packet_v288
    if operation == 'reasoning':
        from . import business_goal_ready_runtime_v288
        return business_goal_ready_runtime_v288
    return {'context_reader': reader, 'resense': resense, 'packet': packet}[operation]


def _schemas(operation):
    module = module_for(operation)
    return module.input_schema(), module.output_schema()


def _execution(operation, original, bindings, arguments):
    need(type(original) is dict and set(original) == PIN_KEYS)
    inputs, outputs = _schemas(operation)
    adapter = {'context_reader': 'builtin', 'local_prepare': 'workflow_service',
        'resense': 'workflow_service', 'evaluation_packet': 'workflow_service',
        'packet': 'runtime_document_packet', 'reasoning': 'business_goal_ready_v288'}[operation]
    need(original['agent_role'] == ROLES[operation] and original['adapter'] == adapter
        and same(original['input_schema'], inputs) and same(original['output_schema'], outputs)
        and type(original['max_attempts']) is int and 1 <= original['max_attempts'] <= 5)
    if operation in {'local_prepare', 'evaluation_packet'}: need(original['max_attempts'] == 1)
    need(any(same(original['input_bindings'], value) for value in ({}, bindings))
        and any(same(original['arguments'], value) for value in ({}, arguments)), '不能注入结果、重复策略或更换原生产者')
    return {**copy.deepcopy(original), 'input_bindings': copy.deepcopy(bindings), 'arguments': copy.deepcopy(arguments)}


def _projection(producer, consumer, pointer='', *, nonnull=False):
    return {'schema': 'guarded-port-projection-v198', 'source': 'port_projection', 'kind': 'object',
        'nullable': False, 'unit': None, 'consumer_node_id': consumer,
        'origin': {'node_id': producer, 'port_ordinal': 0, 'output_pointer': ''},
        'projection': {'op': 'select', 'pointer': pointer, 'require_non_null': True} if nonnull
            else {'op': 'copy', 'pointer': pointer}}


def build_graph(flow, role_bindings, tool_pins, tool_actors, *, business_name, candidate_specs,
                preparation_policy, reviewer_id, decision_intent='information_acquisition'):
    from .business_decision_frame_v276 import CandidateSpec
    from .runtime_task_documents import ref_schema
    flow = linear_flow(flow)
    policy = configured_policy(preparation_policy)
    specs = [CandidateSpec.model_validate(item).model_dump(mode='json') for item in candidate_specs]
    need(same(specs, candidate_specs) and 1 <= len(specs) <= 16 and decision_intent in {'information_acquisition', 'operational_action', 'business_conclusion'})
    need(type(tool_pins) is dict and set(tool_pins) == set(OPERATIONS)
        and type(tool_actors) is dict and set(tool_actors) == set(OPERATIONS))
    posts = {key: tool_role(business_name, key) for key in OPERATIONS}; review_post = review_role(business_name)
    need(type(role_bindings) is dict
        and set(role_bindings) == {step['role'] for step in flow['steps']} | set(posts.values()) | {review_post})
    for key, post in posts.items(): need(role_bindings[post]['user_id'] == tool_actors[key])
    need(role_bindings[review_post]['user_id'] == reviewer_id)
    fixed = {'context_reader': ({'policy_pin': 'policy_pin'}, {}),
        'local_prepare': ({'context_result': 'original_reader_result'}, {}),
        'resense': ({'context': 'planned_context'}, {}),
        'evaluation_packet': ({'local_plan': 'local_data_plan'}, {}),
        'packet': ({'source_set_ref': 'selected_sources'}, {}),
        'reasoning': ({'question': 'analysis_question', 'data_packet': 'original_goal_evaluation_packet',
            'document_packet': 'original_document_packet'}, {'candidate_specs': specs, 'decision_intent': decision_intent})}
    nodes = [{'id': key, 'kind': 'ability', 'source_step_id': None, 'guards': human.guards(),
        'actor_id': tool_actors[key], 'execution': _execution(key, tool_pins[key], *fixed[key])} for key in OPERATIONS]
    for identity, predicate in (('route_acquisition', 'needs_acquisition'), ('route_ready', 'data_ready'),
            ('route_evaluated', 'evaluation_ready')):
        nodes.append({'id': identity, 'kind': 'decision', 'source_step_id': None,
            'guards': human.guards(), 'predicate_id': predicate})
    for identity, title in ((DATA_REVIEW_NODE, '资料准备核对意见'), (REVIEW_NODE, '业务建议核对意见')):
        nodes.append({'id': identity, 'kind': 'human', 'source_step_id': None,
            'guards': human.guards(), 'actor_id': reviewer_id, 'role': review_post,
            'output_schema': {'type': 'object', 'properties': {'result': {'type': 'string', 'minLength': 1,
                'maxLength': 12000, 'pattern': r'\S', 'title': title}}, 'required': ['result'], 'additionalProperties': False}})
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
    links = [('context_reader', 'local_prepare', 'success'), ('local_prepare', 'route_acquisition', 'success'),
        ('route_acquisition', 'resense', 'true'), ('route_acquisition', 'route_ready', 'false'),
        ('resense', 'evaluation_packet', 'success'), ('route_ready', 'evaluation_packet', 'true'),
        ('route_ready', DATA_REVIEW_NODE, 'false'), ('evaluation_packet', 'route_evaluated', 'success'),
        ('route_evaluated', 'packet', 'true'), ('route_evaluated', DATA_REVIEW_NODE, 'false'),
        ('packet', 'reasoning', 'success'), ('reasoning', REVIEW_NODE, 'success'),
        (REVIEW_NODE, humans[0], 'success'), (DATA_REVIEW_NODE, humans[0], 'success')]
    links += [(source, target, 'success') for source, target in zip(humans, [*humans[1:], 'end'])]
    properties = {human.INPUT_KEY: ref_schema(), 'analysis_question': {'type': 'string', 'minLength': 1,
        'maxLength': 4000, 'pattern': r'\S', 'title': '本次业务问题'},
        'policy_pin': copy.deepcopy(reader.input_schema()['properties']['policy_pin'])}
    graph = {'schema': 'full-semantic-graph-v1', 'id': 'authored_goal_data_v288_' + digest({
        'flow': flow, 'business': business_name, 'candidate_specs': specs, 'preparation_policy': policy})[:24], 'entry': 'context_reader',
        'input_schema': {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False},
        'references': {'selected_sources': {'kind': 'object', 'nullable': False, 'source': 'input', 'field': human.INPUT_KEY, 'node_id': None},
            'analysis_question': {'kind': 'string', 'nullable': False, 'source': 'input', 'field': 'analysis_question', 'node_id': None},
            'policy_pin': {'kind': 'object', 'nullable': True, 'source': 'input', 'field': 'policy_pin', 'node_id': None},
            'original_reader_result': _projection('context_reader', 'local_prepare'),
            'local_data_plan': _projection('local_prepare', 'evaluation_packet'),
            'planned_context': _projection('local_prepare', 'resense', '/resense_context', nonnull=True),
            'plan_route': {'kind': 'string', 'nullable': False, 'source': 'output', 'field': 'route', 'node_id': 'local_prepare'},
            'evaluation_route': {'kind': 'string', 'nullable': False, 'source': 'output', 'field': 'route', 'node_id': 'evaluation_packet'},
            'original_goal_evaluation_packet': _projection('evaluation_packet', 'reasoning', '/packet'),
            'original_document_packet': _projection('packet', 'reasoning', '/packet')},
        'predicates': {'needs_acquisition': {'op': 'eq', 'left': {'op': 'ref', 'key': 'plan_route'},
                'right': {'op': 'value', 'kind': 'string', 'value': 'acquire'}},
            'data_ready': {'op': 'eq', 'left': {'op': 'ref', 'key': 'plan_route'},
                'right': {'op': 'value', 'kind': 'string', 'value': 'data_ready'}},
            'evaluation_ready': {'op': 'eq', 'left': {'op': 'ref', 'key': 'evaluation_route'},
                'right': {'op': 'value', 'kind': 'string', 'value': 'data_ready'}}},
        'guards': human.guards(), 'nodes': nodes,
        'edges': [{'id': 'edge_' + str(index), 'from': source, 'to': target, 'on': on, 'branch_id': None, 'loop_id': None}
            for index, (source, target, on) in enumerate(links, 1)], 'loops': [], 'subscriptions': [],
        'goals': [{'id': 'business_complete', 'required_nodes': ['context_reader', 'local_prepare',
            'evaluation_packet', 'packet', 'reasoning', REVIEW_NODE, *humans]}]}
    return {'template': TEMPLATE, 'graph': validate_graph(graph), 'step_mapping': step_mapping, 'service_delegations': declarations()}


def projection(binding):
    need(binding.get('schema') == BINDING and binding.get('template') == TEMPLATE)
    mapping = {item['node_id']: item for item in binding['step_mapping']}
    return {'authorship': 'human_authored', 'model_generated': False, 'semantic_quality_verified': False,
        'simulation_only': True, 'business_name': binding['business_name'], 'trigger': binding['flow']['trigger'],
        'output': binding['flow']['output'], 'preparation_policy': copy.deepcopy(binding['preparation_policy']),
        'source_explanation': '先读取原目标与批准策略，本地准备资料并独立收件，再保存同目标新评价；原查询不改写。',
        'steps': [{'node_id': node['id'], 'kind': node['kind'], 'actor_id': node['actor_id'],
            'name': LABELS[node['id']] if node['kind'] == 'ability' else '核对资料准备' if node['id'] == DATA_REVIEW_NODE
                else '核对业务建议' if node['id'] == REVIEW_NODE else mapping[node['id']]['name']}
            for node in binding['graph']['nodes'] if node['kind'] in {'ability', 'human'}],
        'observation_extractors': [{'capability_id': key, 'executes_in_this_graph': False} for key in sorted(binding['observation_extractor_pins'])],
        'conditional_resense': {'data_ready': 'evaluation_packet', 'acquire': 'resense', 'review_required': DATA_REVIEW_NODE},
        'uses_model': True, 'can_execute': False, 'cloud_authorized': False, 'physical_state_verified': False,
        'limitations': ['当前采用仅支持明确模拟范围；运行仍须独立审批。',
            '资料准备策略须与本次实际Goal相容，固定历史窗口不滑动。',
            '资料不足或冲突先进入人工核查；人工意见不替代原目标完成条件。',
            '补采、独立ACK、新查询及新评价均须核对真实持久化记录，摘要不构成真实性证明。']}
