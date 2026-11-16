"""Deterministic registered-tool image review, independent of report parsing.

This pure recipe authenticates graph structure only. Store verifies actual
registry/IAM/configuration authority; normal Task execution verifies originals,
producer receipts and leases. It creates neither a Task nor a perception ACK.
"""
import copy
from .contracts import digest
from .full_semantic_graph_v1 import validate_graph
from . import authored_workflow_graph_v208 as human
from . import workflow_data_quality_v219 as quality
from . import workflow_quality_archive_v220 as archive

BINDING = 'authored-image-workflow-binding-v243'
TEMPLATE = 'authored-image-technical-human-linear-template-v243'
OPERATIONS = ('quality', 'dedup', 'report')
TOOL_POSTS = {'quality':'原件技术质检执行人', 'dedup':'原件同字节去重执行人', 'report':'内部技术报告执行人'}
TOOL_LABELS = {'quality':'本任务全部原件技术质检', 'dedup':'保留全部原件，按同字节分组',
    'report':'生成内部技术报告（质检与去重）'}
QUALITY_REF, DEDUP_REF = 'technical_quality_report', 'technical_dedup_report'
LIMITATIONS = ['检查范围为本任务全部冻结原件，包括图像及已有配置依据；不遍历工作区其他文件。',
    '同字节去重仅分组，全部原件继续保存。',
    '技术报告包含质检和去重结果；人工办理回执单独保留，正式层级审批遵循任务原策略。',
    '本配方使用本地技术检查；现场状态、物种与语义完整性仍须独立业务依据。']
_PIN_KEYS = {'capability_id','capability_hash','agent_role','input_bindings','arguments',
    'input_schema','output_schema','adapter','release_version','max_attempts'}
same, need = human.same, human.need


def tool_role(business_name, operation):
    need(type(business_name) is str and 1 <= len(business_name) <= 100
        and business_name.strip() and '\x00' not in business_name)
    need(operation in OPERATIONS, '请选择已支持的原件技术工具端口')
    return TOOL_POSTS[operation]+'（'+business_name+'·V243）'


def linear_flow(flow):
    value = human.linear_flow(flow)
    need(not any(step['role'].startswith(prefix+'（') and step['role'].endswith('·V243）')
        for step in value['steps'] for prefix in TOOL_POSTS.values()),
        '原人工岗位与技术工具保留岗位冲突；请明确原业务岗位')
    return value


def _bindings(operation):
    if operation == 'quality': return {}, {'scope_ref':quality.scope_ref()}
    if operation == 'dedup': return {'quality_report':QUALITY_REF}, {'scope_ref':quality.scope_ref()}
    return {'quality_report':QUALITY_REF, 'dedup_report':DEDUP_REF}, {}


def _execution(operation, original):
    need(type(original) is dict and set(original) == _PIN_KEYS,
        '技术工具Pin须完整固定注册版本、角色与输入输出合同')
    need(type(original['capability_id']) is str and quality.UUID.fullmatch(original['capability_id'])
        and type(original['capability_hash']) is str and quality.SHA.fullmatch(original['capability_hash'])
        and original['adapter'] == 'builtin' and original['agent_role'] == 'data'
        and type(original['release_version']) is str and bool(original['release_version'].strip())
        and type(original['max_attempts']) is int and original['max_attempts'] == 1,
        '技术配方只接受固定本地数据工具合同；未扩大执行或重试权限')
    inputs = (archive.input_schema() if operation == 'report'
        else quality.input_schema(quality.QUALITY if operation == 'quality' else quality.DEDUP))
    outputs = archive.output_schema() if operation == 'report' else quality.output_schema()
    need(same(original['input_schema'], inputs) and same(original['output_schema'], outputs),
        '所选工具端口的完整输入输出合同不匹配')
    bindings, arguments = _bindings(operation)
    # Accept original registry pins or this exact recipe's already-bound pins
    # for cold reconstruction. Never silently erase caller execution settings.
    need(any(same(original['input_bindings'], value) for value in ({}, bindings))
        and any(same(original['arguments'], value) for value in ({}, arguments)),
        '技术工具不能携带任意输入绑定、路径、来源摘要或执行参数')
    result = copy.deepcopy(original)
    result.update(input_bindings=bindings, arguments=arguments)
    return result


def build_graph(flow, role_bindings, tool_pins, tool_actors, *, business_name):
    flow = linear_flow(flow)
    need(type(tool_pins) is dict and set(tool_pins) == set(OPERATIONS)
        and type(tool_actors) is dict and set(tool_actors) == set(OPERATIONS))
    posts = {operation:tool_role(business_name, operation) for operation in OPERATIONS}
    human_roles = {step['role'] for step in flow['steps']}
    need(type(role_bindings) is dict and set(role_bindings) == human_roles | set(posts.values()),
        '须明确全部原人工岗位及三个技术工具执行岗位')
    for role in role_bindings:
        actor = role_bindings[role]
        need(type(actor) is dict and type(actor.get('user_id')) is str
            and quality.UUID.fullmatch(actor['user_id']), '岗位须绑定真实用户编号')
    for operation, post in posts.items():
        need(role_bindings[post]['user_id'] == tool_actors[operation], '技术工具执行人与保留岗位不一致')
    executions = {operation:_execution(operation, tool_pins[operation]) for operation in OPERATIONS}
    need(len({pin['capability_id'] for pin in executions.values()}) == 3,
        '三个不同语义端口须分别选择符合合同的注册能力')
    nodes = [{'id':operation, 'kind':'ability', 'source_step_id':None, 'guards':human.guards(),
        'actor_id':tool_actors[operation], 'execution':executions[operation]} for operation in ('quality','dedup')]
    mapping = []
    for step in flow['steps']:
        identity = 'human_'+digest(step['id'])[:24]
        nodes.append({'id':identity, 'kind':'human', 'source_step_id':step['id'], 'guards':human.guards(),
            'actor_id':role_bindings[step['role']]['user_id'], 'role':step['role'],
            'output_schema':{'type':'object', 'properties':{'result':{'type':'string', 'minLength':1,
                'maxLength':12000, 'pattern':r'\S', 'title':step['output']}},
                'required':['result'], 'additionalProperties':False}})
        mapping.append({'source_step_id':step['id'], 'node_id':identity, 'name':step['name'],
            'role':step['role'], 'output':step['output']})
    nodes.append({'id':'report', 'kind':'ability', 'source_step_id':None, 'guards':human.guards(),
        'actor_id':tool_actors['report'], 'execution':executions['report']})
    order = [node['id'] for node in nodes]
    nodes.append({'id':'end', 'kind':'end', 'source_step_id':None, 'guards':human.guards()})
    edges = [{'id':'edge_'+str(index), 'from':identity, 'to':target, 'on':'success',
        'branch_id':None, 'loop_id':None} for index,(identity,target) in
        enumerate(zip(order,[*order[1:],'end']),1)]
    graph = {'schema':'full-semantic-graph-v1', 'id':'authored_image_'+digest({'flow':flow,'business':business_name})[:24],
        'entry':'quality', 'input_schema':{'type':'object', 'properties':{
            'task_image_ids':{'type':'array', 'minItems':1, 'maxItems':3, 'uniqueItems':True, 'items':quality.ident()},
            'entity_ids':{'type':'array', 'minItems':1, 'maxItems':20, 'uniqueItems':True, 'items':quality.ident()}},
            'required':['task_image_ids','entity_ids'], 'additionalProperties':False},
        'references':{QUALITY_REF:{'kind':'object','nullable':False,'source':'output','field':'report','node_id':'quality'},
            DEDUP_REF:{'kind':'object','nullable':False,'source':'output','field':'report','node_id':'dedup'}},
        'predicates':{}, 'guards':human.guards(), 'nodes':nodes, 'edges':edges, 'loops':[],
        'subscriptions':[], 'goals':[{'id':'business_complete','required_nodes':order}]}
    return {'template':TEMPLATE, 'graph':validate_graph(graph), 'step_mapping':mapping}


def check_graph(value, flow, role_bindings, tool_pins, tool_actors, *, business_name):
    """Cold structure check only; never actual registry or execution authority."""
    need(type(value) is dict and set(value) == {'template','graph','step_mapping'})
    rebuilt = build_graph(flow,role_bindings,tool_pins,tool_actors,business_name=business_name)
    need(same(value,rebuilt), '保存的技术工具图、报告来源或原人工步骤无法完整核对')
    return copy.deepcopy(rebuilt)


def projection(binding):
    need(type(binding) is dict and binding.get('schema') == BINDING and binding.get('template') == TEMPLATE)
    mapping = {item['node_id']:item for item in binding['step_mapping']}
    return {'authorship':'human_authored', 'model_generated':False, 'semantic_quality_verified':False,
        'source_explanation':'依据当前已确认的人工流程和工作区已发布工具编制，保存图像及全部任务原件的技术复核结果。',
        'business_name':binding['business_name'], 'trigger':binding['flow']['trigger'], 'output':binding['flow']['output'],
        'steps':[{'node_id':node['id'], 'kind':node['kind'],
            'name':TOOL_LABELS[node['id']] if node['kind'] == 'ability' else mapping[node['id']]['name'],
            'actor_id':node['actor_id']} for node in binding['graph']['nodes'] if node['kind'] in {'ability','human'}],
        'uses_model':False, 'can_execute':False, 'cloud_authorized':False, 'physical_state_verified':False,
        'limitations':list(LIMITATIONS)}
