"""One deterministic authored template, never a report or execution grant."""
import copy
import re
from .contracts import digest
from .full_semantic_graph_v1 import validate_graph

TEMPLATE = 'authored-document-human-linear-template-v208'
INPUT_KEY = 'runtime_document_sources'
PACKET_NODE = 'packet'
REASONING_NODE = 'reasoning'
PACKET_ROLE = '运行期资料处理人'
REASONING_ROLE = '运行期资料分析人'
SOURCE_MARKERS = {'fullSemanticSource', 'reportSource', 'businessFlowSource',
    'authoredWorkflowSource', 'full_semantic_graph_origin', 'authored_workflow_origin'}
_ID = re.compile(r'[-A-Za-z0-9_][-A-Za-z0-9_.:]{0,199}')


def need(value, message='人工流程不属于本片明确支持的线性范围'):
    if not value: raise ValueError(message)


def same(left, right):
    from .typed_payload_codec_v131 import canonical_json
    return canonical_json(left) == canonical_json(right)


def packet_role(name): return PACKET_ROLE + '（' + name + '·V208）'
def reasoning_role(name): return REASONING_ROLE + '（' + name + '·V208）'


def compatible_roles(selected, previous):
    """Same named human posts retain the compiler's workspace-wide meaning."""
    for roles in previous:
        for key in set(selected) & set(roles):
            need(same({field: selected[key][field] for field in ('user_id', 'username')},
                {field: roles[key][field] for field in ('user_id', 'username')}),
                '同名人工岗位在其他当前业务已有不同负责人；未采用。请核对全局岗位，批量变更不在本片范围。')


def linear_flow(flow):
    need(type(flow) is dict and not SOURCE_MARKERS.intersection(flow))
    steps = flow.get('steps')
    need(type(steps) is list and 1 <= len(steps) <= 16)
    ids = [step.get('id') for step in steps if type(step) is dict]
    need(len(ids) == len(steps) and len(set(ids)) == len(ids)
        and all(type(key) is str and _ID.fullmatch(key) for key in ids), '原步骤标识不符合真实图合同；未自动改名')
    for position, step in enumerate(steps):
        need(not SOURCE_MARKERS.intersection(step) and step.get('agent') == '人工确认'
            and all(type(step.get(key)) is str and step[key].strip() for key in ('name', 'role', 'output'))
            and not any(step['role'].startswith(prefix + '（') and step['role'].endswith('·V208）')
                for prefix in (PACKET_ROLE, REASONING_ROLE))
            and not step.get('condition', '').strip() and step.get('elseId', '') in {'', 'end'}
            and not any(key in step for key in ('execution', 'planning_tools', 'planningTools')),
            '仅支持原全人工线性步骤；分支、原能力或保留角色须另行设计')
        target = ids[position + 1] if position + 1 < len(ids) else 'end'
        need(step.get('nextId', 'auto') in {'auto', target}, '不支持跳步、回边或提前结束；原去向未改写')
    need(type(flow.get('trigger')) is str and flow['trigger'].strip()
        and type(flow.get('output')) is str and flow['output'].strip())
    return copy.deepcopy(flow)


def guards(): return {'start': None, 'precondition': None, 'stop': None, 'exception': None}


def capability_pin(capability, role):
    data = capability.data
    return {'capability_id': capability.id, 'capability_hash': capability.content_hash,
        'agent_role': role, 'input_bindings': {}, 'arguments': {},
        'input_schema': copy.deepcopy(data['input_schema']), 'output_schema': copy.deepcopy(data['output_schema']),
        'adapter': data['execution']['adapter'], 'release_version': capability.release_version,
        'max_attempts': data['budget'].get('max_attempts', 2)}


def build_graph(flow, role_bindings, packet_pin, packet_actor, *, reasoning_pin=None,
        reasoning_actor=None, candidate_proposals=None, business_name):
    """Only server-generated pins enter; store independently verifies registry/IAM."""
    from .runtime_task_documents import ref_schema
    flow = linear_flow(flow); proposals = candidate_proposals or []
    human_roles = {step['role'] for step in flow['steps']}
    packet_post, reasoning_post = packet_role(business_name), reasoning_role(business_name)
    need(not human_roles.intersection({packet_post, reasoning_post}), '原人工岗位与本业务保留执行岗位冲突；未覆盖岗位')
    expected_roles = human_roles | {packet_post} | ({reasoning_post} if reasoning_pin else set())
    need(type(role_bindings) is dict and set(role_bindings) == expected_roles)
    need(role_bindings[packet_post]['user_id'] == packet_actor)
    need(bool(reasoning_pin) == bool(reasoning_actor)
        and (1 <= len(proposals) <= 16 if reasoning_pin else proposals == []))
    if reasoning_pin:
        need(role_bindings[reasoning_post]['user_id'] == reasoning_actor)
        need(all(type(value) is str and value.strip() and len(value) <= 2000 for value in proposals))
    properties = {INPUT_KEY: ref_schema()}
    references = {'selected_sources': {'kind': 'object', 'nullable': False,
        'source': 'input', 'field': INPUT_KEY, 'node_id': None}}
    packet = copy.deepcopy(packet_pin)
    need(packet['adapter'] == 'runtime_document_packet' and packet['agent_role'] == 'data')
    packet.update(input_bindings={'source_set_ref': 'selected_sources'}, arguments={})
    nodes = [{'id': PACKET_NODE, 'kind': 'ability', 'source_step_id': None, 'guards': guards(),
        'actor_id': packet_actor, 'execution': packet}]
    if reasoning_pin:
        properties['analysis_question'] = {'type': 'string', 'minLength': 1, 'maxLength': 4000,
            'pattern': r'\S', 'title': '本次需要分析的问题'}
        references.update(analysis_question={'kind': 'string', 'nullable': False,
            'source': 'input', 'field': 'analysis_question', 'node_id': None},
            packet_evidence={'kind': 'array', 'nullable': False, 'source': 'output',
                'field': 'evidence_ids', 'node_id': PACKET_NODE})
        reasoning = copy.deepcopy(reasoning_pin)
        need(reasoning['adapter'] == 'business_reasoning' and reasoning['agent_role'] == 'business')
        reasoning.update(input_bindings={'question': 'analysis_question', 'evidence_ids': 'packet_evidence'},
            arguments={'candidates': [{'candidate_ref': 'candidate_' + str(i), 'summary': value, 'context': None}
                for i, value in enumerate(proposals, 1)]})
        nodes.append({'id': REASONING_NODE, 'kind': 'ability', 'source_step_id': None,
            'guards': guards(), 'actor_id': reasoning_actor, 'execution': reasoning})
    mapping = []
    for step in flow['steps']:
        key = 'human_' + digest(step['id'])[:24]
        nodes.append({'id': key, 'kind': 'human', 'source_step_id': step['id'], 'guards': guards(),
            'actor_id': role_bindings[step['role']]['user_id'], 'role': step['role'],
            'output_schema': {'type': 'object', 'properties': {'result': {'type': 'string',
                'minLength': 1, 'maxLength': 12000, 'pattern': r'\S', 'title': step['output']}},
                'required': ['result'], 'additionalProperties': False}})
        mapping.append({'source_step_id': step['id'], 'node_id': key, 'name': step['name'],
            'role': step['role'], 'output': step['output']})
    order = [node['id'] for node in nodes]
    nodes.append({'id': 'end', 'kind': 'end', 'source_step_id': None, 'guards': guards()})
    edges = [{'id': 'edge_' + str(i), 'from': key, 'to': target, 'on': 'success',
        'branch_id': None, 'loop_id': None} for i, (key, target) in enumerate(zip(order, [*order[1:], 'end']), 1)]
    graph = {'schema': 'full-semantic-graph-v1', 'id': 'authored_' + digest(flow)[:24],
        'entry': PACKET_NODE, 'input_schema': {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}, 'references': references,
        'predicates': {}, 'guards': guards(), 'nodes': nodes, 'edges': edges, 'loops': [],
        'subscriptions': [], 'goals': [{'id': 'business_complete', 'required_nodes': order}]}
    return {'template': TEMPLATE, 'graph': validate_graph(graph), 'step_mapping': mapping}


def projection(binding):
    """Human-readable review; the full actual graph is also returned by preview."""
    return {'authorship': 'human_authored', 'model_generated': False, 'semantic_quality_verified': False,
        'source_explanation': '依据当前用户确认的工作区流程；不声称模型理解或报告覆盖。',
        'business_name': binding['business_name'], 'trigger': binding['flow']['trigger'],
        'output': binding['flow']['output'], 'steps': [{'node_id': node['id'],
            'kind': node['kind'], 'name': ('保存已批准的运行期资料包' if node['id'] == PACKET_NODE
                else '按具体证据比较候选，仍需授权与人工核对' if node['id'] == REASONING_NODE
                else next(item['name'] for item in binding['step_mapping'] if item['node_id'] == node['id'])),
            'actor_id': node['actor_id']} for node in binding['graph']['nodes'] if node['kind'] in {'ability', 'human'}],
        'uses_model': binding['reasoning'] is not None, 'can_execute': False,
        'cloud_authorized': False, 'physical_state_verified': False}
