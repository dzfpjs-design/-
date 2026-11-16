"""Fixed image recipe eligibility; shared V208 ledger remains the authority."""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from . import models as m,authored_workflow_store_v208 as common
from . import authored_image_workflow_graph_v243 as template
from .authored_image_workflow_contracts_v243 import (
    PREFIX,PATH,BINDING,TEMPLATE,PREVIEW,RESULT,COMMAND,OPERATIONS,Preview,Adopt,parse,preview_body)
from . import authored_image_tools_v243 as tools
from .contracts import digest
from .services import require,scoped,published

need,same=common.need,common.same


def _lock_people(db,p,body):
    from .relations import lock_current_actor
    ids={p.user_id,*body.role_bindings.values(),*(pin.actor_id for pin in
        (body.tool_bindings.quality,body.tool_bindings.dedup,body.tool_bindings.report))}
    for identity in sorted(ids):common._actor(db,p,identity,lock=True)
    lock_current_actor(db,p)


def _roles(db,p,body,flow):
    expected={step['role'] for step in flow['steps']}
    need(set(body.role_bindings)==expected,'请选择全部原人工岗位，不填写内部技术岗位')
    reserved={template.tool_role(body.business_name,operation) for operation in OPERATIONS}
    need(not expected.intersection(reserved),'原人工岗位与内部技术岗位冲突，未覆盖')
    result={key:common._actor(db,p,identity) for key,identity in sorted(body.role_bindings.items())}
    for operation,pin in body.tool_bindings.model_dump(mode='json').items():
        result[template.tool_role(body.business_name,operation)]=common._actor(db,p,pin['actor_id'])
    return result


def _pins(db,p,body,*,lock=False):
    result={}
    for operation,pin in sorted(body.tool_bindings.model_dump(mode='json').items(),key=lambda pair:pair[1]['capability_id']):
        cap=scoped(db,m.Capability,pin['capability_id'],p,lock);published(db,p,cap.id,'tool')
        tools.validate(cap,operation);need(cap.content_hash==pin['capability_hash'])
        result[operation]=common.template.capability_pin(cap,'data')
    return result


def automation(db,p,row,name):return common.automation(db,p,row,name,_image_family=True)


def prepare(db,p,body,*,lock=False):
    require(p,'admin','operator')
    if lock:_lock_people(db,p,body)
    else:common._actor(db,p,p.user_id)
    row=common.configuration(db,p,lock=lock);common.no_other_graphs(row.data)
    need(row.version==body.configuration_revision and digest(row.data)==body.configuration_hash,'配置已变化，请保留原稿并重新预览')
    from .instance_compiler import names,flow_for,flow_issues,signature_value
    need(body.business_name in names(row.data) and body.business_name in row.data.get('flows',{})
        and body.business_name not in row.data.get('businessAliases',{}),'请选择原普通业务')
    prior=body.prior_origin.model_dump(mode='json') if body.prior_origin else None
    need(same(row.data.get(common.CONFIG_KEY,{}).get(body.business_name),prior),'此业务已有新采用，请核对原出处')
    flow=flow_for(row.data,body.business_name)
    try:template.linear_flow(flow)
    except (ValueError,TypeError,AttributeError):need(False,'仅支持已确认的全人工线性流程，不覆盖分支或原能力')
    issues=flow_issues(db,p,row.data,flow,name=body.business_name,require_signature=True)
    need(not issues,{'message':'请先确认原人工流程','issues':issues})
    roles=_roles(db,p,body,flow)
    others=[common.checked_origin(db,p,issued)['role_bindings'] for name,issued in row.data.get(common.CONFIG_KEY,{}).items() if name!=body.business_name]
    try:common.template.compatible_roles(roles,others)
    except ValueError as error:need(False,str(error))
    pins=_pins(db,p,body,lock=lock)
    actors={operation:pin['actor_id'] for operation,pin in body.tool_bindings.model_dump(mode='json').items()}
    try:translated=template.build_graph(flow,roles,pins,actors,business_name=body.business_name)
    except (ValueError,TypeError,KeyError,AttributeError) as error:need(False,str(error))
    graph=translated['graph']
    binding=common._seal({'schema':BINDING,'authorship':'human_authored','model_generated':False,
        'semantic_quality_verified':False,'template':TEMPLATE,'configuration_id':row.id,
        'configuration_revision':row.version,'configuration_hash':digest(row.data),'business_name':body.business_name,
        'prior_origin':prior,'flow':copy.deepcopy(flow),'flow_signature':signature_value(row.data,flow),'flow_hash':digest(flow),
        'role_bindings':roles,'tool_bindings':body.tool_bindings.model_dump(mode='json'),'graph':graph,
        'step_mapping':translated['step_mapping'],'capability_pins':{node['id']:copy.deepcopy(node['execution']) for node in graph['nodes'] if node['kind']=='ability'},
        'node_actions':copy.deepcopy(body.node_actions),'external_node_ids':[],'event_source_bindings':{},
        'automation':automation(db,p,row,body.business_name),'source_materials':common.materials(db,p,flow,lock=lock)})
    return row,make_preview(p,body,binding)


def binding_body(binding):
    reserved={template.tool_role(binding['business_name'],operation) for operation in OPERATIONS}
    return {'schema':'authored-image-workflow-preview-command-v243',
        **{key:copy.deepcopy(binding[key]) for key in ('configuration_revision','configuration_hash','business_name','tool_bindings','node_actions','external_node_ids','prior_origin')},
        'role_bindings':{key:actor['user_id'] for key,actor in binding['role_bindings'].items() if key not in reserved}}


def check_binding(value):
    keys={'schema','authorship','model_generated','semantic_quality_verified','template','configuration_id','configuration_revision',
        'configuration_hash','business_name','prior_origin','flow','flow_signature','flow_hash','role_bindings','tool_bindings','graph',
        'step_mapping','capability_pins','node_actions','external_node_ids','event_source_bindings','automation','source_materials','binding_hash'}
    need(type(value) is dict and set(value)==keys and value['schema']==BINDING and value['template']==TEMPLATE
        and value['authorship']=='human_authored' and value['model_generated'] is False and value['semantic_quality_verified'] is False
        and value['binding_hash']==digest({key:item for key,item in value.items() if key!='binding_hash'})
        and value['flow_hash']==digest(value['flow']) and value['event_source_bindings']=={})
    body=Preview.model_validate(binding_body(value));pins=value['capability_pins']
    need(set(pins)==set(OPERATIONS))
    for operation,pin in body.tool_bindings.model_dump(mode='json').items():
        need(pins[operation]['capability_id']==pin['capability_id'] and pins[operation]['capability_hash']==pin['capability_hash'])
    translated=template.build_graph(value['flow'],value['role_bindings'],pins,
        {key:pin['actor_id'] for key,pin in body.tool_bindings.model_dump(mode='json').items()},business_name=body.business_name)
    need(same(translated['graph'],value['graph']) and same(translated['step_mapping'],value['step_mapping']))
    return copy.deepcopy(value)


def make_preview(p,body,binding):
    value={'schema':PREVIEW,'workspace_id':p.workspace_id,'actor_id':p.user_id,'command':preview_body(body),
        'binding':binding,'projection':template.projection(binding),'can_execute':False,'cloud_authorized':False}
    return {**value,'preview_hash':digest(value)}


def verify_origin(db,p,issued,*,require_current=True):
    binding=common.checked_origin(db,p,issued);current=common.configuration(db,p)
    need(binding['schema']==BINDING)
    if require_current:need(same(current.data.get(common.CONFIG_KEY,{}).get(binding['business_name']),issued))
    common.no_other_graphs(current.data);body=Preview.model_validate(binding_body(binding))
    need(same(_roles(db,p,body,binding['flow']),binding['role_bindings']))
    pins=_pins(db,p,body)
    rebuilt=template.build_graph(binding['flow'],binding['role_bindings'],pins,
        {key:pin['actor_id'] for key,pin in body.tool_bindings.model_dump(mode='json').items()},business_name=body.business_name)
    need(same(rebuilt['graph'],binding['graph']) and same(automation(db,p,current,body.business_name),binding['automation'])
        and same(common.verify_materials(db,p,binding,current=True),binding['source_materials']))
    return binding


def preview(db,p,body):common.inventory(db,p);return prepare(db,p,body)[1]
def adopt(db,p,raw):return common.adopt(db,p,raw,_family_path=PATH)
def recover(db,p,identity,expected_hash):return common.recover(db,p,identity,expected_hash,_family_path=PATH)
def get_adoption(db,p,identity):return common.get_adoption(db,p,identity,_family_path=PATH)


def context(db,p,name):
    require(p,'admin','operator');common._actor(db,p,p.user_id);row=common.configuration(db,p);rows,_=common.inventory(db,p)
    from .instance_compiler import names,flow_issues
    need(name in names(row.data),'当前工作区没有此业务',404)
    people=list(db.execute(select(m.User,m.Membership).join(m.Membership,m.Membership.user_id==m.User.id).where(
        m.User.active.is_(True),m.Membership.workspace_id==p.workspace_id,m.Membership.role.in_(['admin','operator'])).limit(257)))
    need(len(people)<=256,'当前成员超过容量，未截断')
    members=[{'user_id':user.id,'username':user.username,'membership_role':member.role} for user,member in people]
    caps=list(db.scalars(select(m.Capability).where(m.Capability.workspace_id==p.workspace_id,m.Capability.kind=='tool',
        m.Capability.status=='published',m.Capability.data['execution']['handler'].as_string().in_(
            [manifest.execution['handler'] for manifest in tools.manifests().values()])).limit(257)))
    need(len(caps)<=256,'当前工具超过容量，未截断')
    capabilities=[]
    for cap in caps:
        for operation in OPERATIONS:
            try:tools.validate(cap,operation)
            except HTTPException:continue
            capabilities.append(tools.item(cap,operation))
    flow=copy.deepcopy(row.data.get('flows',{}).get(name));reason=None
    try:
        common.no_other_graphs(row.data);template.linear_flow(flow)
        need(not flow_issues(db,p,row.data,flow,name=name,require_signature=True),'请先确认原人工流程')
        automation(db,p,row,name)
        need(set(item['operation'] for item in capabilities)==set(OPERATIONS),'尚缺图像质检、去重或内部报告工具，请准备本地工具')
    except (HTTPException,ValueError,TypeError,AttributeError) as error:
        detail=error.detail if isinstance(error,HTTPException) else str(error)
        reason=detail.get('message','当前前置条件尚未核对') if type(detail) is dict else str(detail)
    prior=copy.deepcopy(row.data.get(common.CONFIG_KEY,{}).get(name));kind='none'
    if prior:kind='image' if common.checked_origin(db,p,prior)['schema']==BINDING else 'document'
    return {'schema':'authored-image-workflow-context-v243','configuration_revision':row.version,'configuration_hash':digest(row.data),
        'business_name':name,'flow':flow,'human_roles':sorted({step.get('role','') for step in (flow or {}).get('steps',[])}),
        'tool_roles':{key:template.tool_role(name,key) for key in OPERATIONS},'prior_origin':prior,'current_adoption_kind':kind,
        'members':sorted(members,key=lambda value:value['user_id']),'capabilities':sorted(capabilities,key=lambda value:(value['operation'],value['id'])),
        'adoptions':[get_adoption(db,p,item.id) for item in rows if item.business_name==name and item.data['binding']['schema']==BINDING],
        'can_preview':reason is None,'reason':reason,'can_prepare_tools':p.role=='admin','can_execute':False,'cloud_authorized':False}
