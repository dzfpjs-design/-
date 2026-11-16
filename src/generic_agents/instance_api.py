"""Versioned initialization, honest partial enablement and human object forms."""
import copy
from datetime import timezone
from typing import Literal
from uuid import UUID
from pydantic import Field,model_validator
from fastapi import APIRouter,HTTPException
from sqlalchemy import select,func
from jsonschema import Draft202012Validator,FormatChecker
from .contracts import Contract,Manifest,EntityCreate,digest
from .models import DomainInstance,InstanceRelease,Entity,User,Membership,Task,Audit,uid,now
from .setup_api import P,DB,draft
from .instance_compiler import ObjectType,compile_configuration,verify_material_snapshots,material_ids_for_flow
from .object_type_contract_v170 import ObjectTypeV170
from .services import scoped,require,check_version,register,validate_capability,publish,published,create_entity,audit
from .instance_semantic_runtime_dispatch import pins as full_semantic_pins

router=APIRouter(prefix='/api/instances')

def view(row):
    # The overview is shared with viewers. Actor-private drafts and durable
    # command bodies remain behind their scoped draft/recovery endpoints.
    data=({key:row.data[key] for key in ['name','domain','enabled_business_ids','execution_scope'] if key in row.data}
        if isinstance(row,DomainInstance) else row.data)
    if isinstance(row,Task):
        data={key:value for key,value in data.items() if key not in {'object_definition_bindings','object_definition_bindings_hash',
            'workflow_service_notices_v195'}}
    value={'id':row.id,'version':row.version,'status':getattr(row,'status',None),'created_at':row.created_at.isoformat(),**data}
    if hasattr(row,'title'):value['title']=row.title
    if isinstance(row,Task):
        from .native_query_proposals import public_task
        value=public_task(value)
    return value

def release_view(row):return {**view(row),'created_at':row.created_at.astimezone(timezone.utc).isoformat(),
    'instance_id':row.instance_id,'release_number':row.release_number,
    'configuration_hash':row.configuration_hash,'package_id':row.package_id}

@router.get('/members')
def members(p:P,db:DB):
    return [{'id':u.id,'name':u.username,'role':m.role} for m,u in db.execute(select(Membership,User)
        .join(User,User.id==Membership.user_id).where(Membership.workspace_id==p.workspace_id,
            Membership.role.in_(['admin','operator']),User.active.is_(True)))]

class Compilation(Contract):
    revision:int=Field(ge=1)
    objects:list[ObjectType|ObjectTypeV170]=Field(max_length=40)
    role_bindings:dict[str,str]=Field(default_factory=dict)
    instance_id:str|None=None

def checked(db,p,body):
    configuration=draft(db,p,True);check_version(configuration,body.revision)
    if body.instance_id:scoped(db,DomainInstance,body.instance_id,p)
    snapshot,signature=compile_configuration(db,p,configuration.data,[x.model_dump() for x in body.objects],body.role_bindings,
        target_instance_id=body.instance_id)
    return configuration,snapshot,signature

@router.post('/check')
def preview(body:Compilation,p:P,db:DB):
    _,snapshot,signature=checked(db,p,body);db.commit()
    return {'configuration_hash':signature,**snapshot}

class Generate(Compilation):
    confirmed_configuration_hash:str
    confirm_configuration:Literal[True]
    generation_intent:Literal['new_instance','new_release']|None=None
    request_id:UUID|None=None

    @model_validator(mode='after')
    def explicit_intent(self):
        if (self.generation_intent is None)!=(self.request_id is None):
            raise ValueError('明确生成意图和请求编号必须同时提供')
        if self.generation_intent=='new_instance' and self.instance_id is not None:
            raise ValueError('生成新的实例不能指定已有实例')
        if self.generation_intent=='new_release' and (not self.instance_id or not self.instance_id.strip()):
            raise ValueError('生成新版本必须指定已有实例')
        return self

def generation_reply(db,p,body,row,*,commit=False):
    result=release_view(row)
    if body.generation_intent is not None:
        from .instance_generation import accept_request,receipt_view
        record=accept_request(db,p,body,result,release_actor_id=row.actor_id)
        result={**result,'generation_receipt':receipt_view(record)};commit=True
    if commit:db.commit()
    return result

@router.post('',status_code=201)
def generate(body:Generate,p:P,db:DB):
    require(p,'admin')
    if body.request_id is not None:
        from .instance_generation import lock_request,request_record,recover_request
        lock_request(db,body.request_id);record=request_record(db,body.request_id)
        if record is not None:
            restored=recover_request(db,p,body.request_id,release_view,body=body,record=record)
            db.commit()
            return {**restored['release'],'generation_receipt':{key:value for key,value in restored.items() if key!='release'}}
    configuration,snapshot,signature=checked(db,p,body)
    if signature!=body.confirmed_configuration_hash:raise HTTPException(409,'配置、资料或账号状态已变化，请重新检查')
    if not snapshot['profile'].get('name','').strip() or not snapshot['profile'].get('domain','').strip():
        raise HTTPException(422,'需填写实例名称和领域才能保存生成版本')
    from .instance_native_graph_scope_v136 import require_snapshot_instance
    require_snapshot_instance(snapshot,body.instance_id)
    if body.instance_id:instance=scoped(db,DomainInstance,body.instance_id,p,True)
    else:
        if body.generation_intent is None:
            prior=db.scalar(select(InstanceRelease).where(InstanceRelease.workspace_id==p.workspace_id,
                InstanceRelease.configuration_hash==signature))
            if prior:return release_view(prior)
        identity=uid();instance=DomainInstance(id=identity,workspace_id=p.workspace_id,namespace='instance_'+identity.replace('-',''),
            data={'name':snapshot['profile']['name'],'domain':snapshot['profile']['domain']});db.add(instance);db.flush()
    prior=db.scalar(select(InstanceRelease).where(InstanceRelease.instance_id==instance.id,InstanceRelease.configuration_hash==signature))
    if prior:return generation_reply(db,p,body,prior)
    number=(db.scalar(select(func.max(InstanceRelease.release_number)).where(InstanceRelease.instance_id==instance.id)) or 0)+1
    package=None
    if snapshot['entity_schemas'] and not snapshot['general_issues']:
        from .native_query_catalog import ensure as ensure_query_tools, pins as query_tool_pins
        query_tools=ensure_query_tools(db,p)
        from .pixel_question_catalog_v252 import ensure as ensure_pixel_questions, pins as pixel_question_pins
        pixel_question_tools=ensure_pixel_questions(db,p,snapshot)
        from .report_full_semantic_graph_runtime_v3 import dependencies as graph_dependencies
        if snapshot.get('semantic_runtime_plan',{}).get('schema') == 'instance-full-semantic-runtime-plan-v4':
            from .report_full_semantic_graph_runtime_v4 import dependencies as graph_dependencies
        elif snapshot.get('semantic_runtime_plan',{}).get('schema') == 'instance-object-graph-runtime-plan-v171':
            from .report_object_graph_runtime_v171 import dependencies as graph_dependencies
        elif snapshot.get('semantic_runtime_plan',{}).get('schema') == 'instance-business-flow-graph-runtime-v1':
            from .business_flow_execution_runtime import dependencies as graph_dependencies
        elif snapshot.get('semantic_runtime_plan',{}).get('schema') == 'instance-authored-workflow-runtime-v208':
            from .authored_workflow_runtime_v208 import dependencies as graph_dependencies
        manifest=Manifest(kind='domain_package',namespace=instance.namespace,name='business_domain',version=f'1.0.{number}',
            owner=p.user_id,description=snapshot['profile']['name']+'：由确认的配置生成',allowed_agent_roles=['business','perception','data'],
            dependencies=list(dict.fromkeys([s['execution']['capability_id'] for b in snapshot['businesses'] if b['available'] and b['execution_mode']!='confirmed_graph_workflow' for s in b['flow']['steps'] if s['agent']!='人工确认']+graph_dependencies(snapshot)+
                [g['capability_id'] for b in snapshot['businesses'] if b['available'] for s in b['flow']['steps'] if s['agent']=='人工确认' for g in s.get('planning_tools',[])]+[x.id for x in query_tools+pixel_question_tools])),
            specification={'entity_schemas':snapshot['entity_schemas'],'business_definitions':snapshot['businesses'],
                'object_catalog':snapshot['object_types'],'data_plan':snapshot['data_plan'],'configuration_hash':signature,
                'data_requirements_schema':snapshot['data_requirements']['schema'],
                'data_requirements_hash':snapshot['data_requirements']['requirements_hash'],
                **full_semantic_pins(snapshot),
                **({'relation_types':snapshot['relation_types']} if 'relation_types' in snapshot else {}),
                'data_tool_ids':[x.id for x in query_tools+pixel_question_tools],'native_query_catalog':query_tool_pins(query_tools),
                **pixel_question_pins(snapshot,pixel_question_tools),
                **({'planning_grants':{b['id']:{s['id']:s['planning_tools'] for s in b['flow']['steps'] if s['agent']=='人工确认' and 'planning_tools' in s} for b in snapshot['businesses'] if b['available']},
                    'business_tool_ids':list(dict.fromkeys(g['capability_id'] for b in snapshot['businesses'] if b['available'] for s in b['flow']['steps'] if s['agent']=='人工确认' for g in s.get('planning_tools',[])))}
                    if any(s['agent']=='人工确认' and 'planning_tools' in s for b in snapshot['businesses'] for s in b['flow']['steps']) else {})})
        package=register(db,p,manifest);validate_capability(db,p,package.id,package.version)
        publish(db,p,package.id,package.version,'用户核对配置后生成版本，运行仍需另行启用')
    lineage_parent={'schema':'instance-object-parent-v1','instance_id':instance.id,'parent_release_id':instance.active_release_id}
    release=InstanceRelease(workspace_id=p.workspace_id,instance_id=instance.id,release_number=number,configuration_hash=signature,
        package_id=package.id if package else None,actor_id=p.user_id,status='prepared' if package else 'draft',
        data={'snapshot':snapshot,'configuration_revision':configuration.version,'confirmed_by':p.user_id,
            'data_requirements_schema':snapshot['data_requirements']['schema'],
            **full_semantic_pins(snapshot),
            'object_lineage_parent':lineage_parent,'object_lineage_parent_hash':digest(lineage_parent),
            'available_business_ids':[x['id'] for x in snapshot['businesses'] if x['available']]})
    db.add(release);db.flush();audit(db,p,'instance.generated',release.id,{'number':number,'hash':signature})
    return generation_reply(db,p,body,release,commit=True)

@router.get('/generation-requests/{request_id}')
def generation_request(request_id:UUID,p:P,db:DB):
    from .instance_generation import recover_request
    return recover_request(db,p,request_id,release_view)

@router.get('')
def instances(p:P,db:DB):
    return [{**view(x),'active_release_id':x.active_release_id} for x in db.scalars(select(DomainInstance)
        .where(DomainInstance.workspace_id==p.workspace_id).order_by(DomainInstance.created_at.desc(),DomainInstance.id.desc()).limit(100))]

@router.get('/{identity}/releases')
def releases(identity:str,p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    scoped(db,DomainInstance,identity,p)
    return [release_view(x) for x in db.scalars(select(InstanceRelease).where(InstanceRelease.workspace_id==p.workspace_id,
        InstanceRelease.instance_id==identity).order_by(InstanceRelease.release_number.desc()).limit(100))]

class Activate(Contract):
    version:int=Field(ge=1)
    release_id:str
    business_ids:list[str]=Field(min_length=1,max_length=20)
    confirm_human_workflow_scope:bool=False
    confirm_workflow_scope:bool=False

@router.post('/{identity}/activate')
def activate(identity:str,body:Activate,p:P,db:DB):
    require(p,'admin');instance=scoped(db,DomainInstance,identity,p,True);check_version(instance,body.version)
    release=scoped(db,InstanceRelease,body.release_id,p)
    if release.instance_id!=instance.id or release.status!='prepared':raise HTTPException(409,'生成版本尚未就绪或不属于此实例')
    published(db,p,release.package_id,'domain_package');snapshot=release.data['snapshot']
    from .instance_native_graph_scope_v136 import require_snapshot_instance
    require_snapshot_instance(snapshot,instance.id)
    from .instance_data_receipts import verify_release
    verify_release(db,p,release)
    from .instance_semantic_runtime_dispatch import verify_release as verify_full_semantics
    verify_full_semantics(db,p,release)
    if snapshot['general_issues'] or any(x not in release.data['available_business_ids'] for x in body.business_ids):
        raise HTTPException(409,'所选业务仍有执行缺口，不能启用')
    if len(set(body.business_ids))!=len(body.business_ids):raise HTTPException(422,'业务不能重复')
    automatic=any(b['id'] in body.business_ids and b['execution_mode']!='confirmed_human_workflow' for b in snapshot['businesses'])
    if not body.confirm_workflow_scope and (automatic or not body.confirm_human_workflow_scope):
        raise HTTPException(422,'请明确确认所选业务的实际能力执行范围')
    # Re-check actual accounts; the frozen role names alone confer no permission.
    for business in snapshot['businesses']:
        if business['id'] not in body.business_ids:continue
        if business['execution_mode']=='confirmed_graph_workflow':
            # verify_full_semantics above checks the exact graph ledger, every
            # actual member and capability; the original source flow is never
            # rewritten to contain legacy sequential execution pins.
            continue
        for step in business['flow']['steps']:
            if step['agent']!='人工确认':
                from .workflow_abilities import pin
                if pin(db,p,step,snapshot['automation']['policies'][business['name']])!=step['execution']:
                    raise HTTPException(409,'已确认的能力设置已变化，请重新核对实例版本')
                continue
            actor=snapshot['roles'][step['role']]['user_id']
            if not db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(User.id==actor,
                User.active.is_(True),Membership.workspace_id==p.workspace_id,Membership.role.in_(['admin','operator']))):
                raise HTTPException(409,'负责人账号已停用或失去执行权限，请重新生成版本')
    source_ids={identity for b in snapshot['businesses'] if b['id'] in body.business_ids for identity in material_ids_for_flow(b['flow'])}|set(snapshot.get('object_material_ids',[]))|set(snapshot.get('relation_material_ids',[]))
    verify_material_snapshots(db,p,[x for x in snapshot['materials'] if x['id'] in source_ids])
    instance.active_release_id=release.id;instance.status='partially_active';instance.version+=1
    instance.data={**instance.data,'enabled_business_ids':body.business_ids,'execution_scope':'confirmed_registered_workflows',
        'name':snapshot['profile']['name'],'domain':snapshot['profile']['domain']}
    audit(db,p,'instance.activated',identity,{'release':release.id,'business_ids':body.business_ids,'device_control':False})
    db.commit();return {**view(instance),'active_release_id':instance.active_release_id}

class Pause(Contract):version:int=Field(ge=1)

@router.post('/{identity}/pause')
def pause(identity:str,body:Pause,p:P,db:DB):
    require(p,'admin');instance=scoped(db,DomainInstance,identity,p,True);check_version(instance,body.version)
    instance.status='paused';instance.version+=1;audit(db,p,'instance.paused',identity);db.commit();return view(instance)

def active(db,p,identity,lock=False):
    instance=scoped(db,DomainInstance,identity,p,lock)
    if instance.status!='partially_active' or not instance.active_release_id:raise HTTPException(409,'实例尚未启用或已暂停')
    release=scoped(db,InstanceRelease,instance.active_release_id,p);published(db,p,release.package_id,'domain_package')
    from .instance_semantic_runtime_dispatch import verify_release as verify_full_semantics
    verify_full_semantics(db,p,release)
    return instance,release

@router.get('/{identity}/objects')
def objects(identity:str,p:P,db:DB):
    instance=scoped(db,DomainInstance,identity,p)
    context=None;release=None
    if instance.status=='partially_active' and instance.active_release_id:
        try:
            from .entity_release_compat import EntityReleaseContext
            release=scoped(db,InstanceRelease,instance.active_release_id,p)
            context=EntityReleaseContext(db,p,instance,release)
        except HTTPException:
            pass
    def compatibility(entity):
        result=context.view(entity) if context else {'usable':False,'status':'incompatible',
            'reason':'当前实例版本尚不能核实，请先核对已启用版本','source_release_number':None,'source_label':None,'source_type':None}
        return {**result,'target_release_id':release.id if release else None}
    return [{**view(x),'entity_type':x.entity_type,'package_id':x.schema_capability_id,'attributes':x.data,
        'object_compatibility':compatibility(x)} for x in db.scalars(select(Entity)
        .where(Entity.workspace_id==p.workspace_id,Entity.namespace==instance.namespace).order_by(Entity.created_at.desc()).limit(200))]

def created_object_view(db,p,instance,release,row):
    from .entity_release_compat import EntityReleaseContext
    try:compatibility=EntityReleaseContext(db,p,instance,release).view(row)
    except HTTPException:
        compatibility={'usable':False,'status':'incompatible','reason':'对象已保存，当前版本的可用范围待核实，请刷新记录',
            'source_release_number':None,'source_label':None,'source_type':None}
    return {**view(row),'entity_type':row.entity_type,'package_id':row.schema_capability_id,'attributes':row.data,
        'object_compatibility':{**compatibility,'target_release_id':release.id}}

class ObjectCreate(Contract):
    release_id:str
    object_type:str
    attributes:dict
    reference:str=Field(default='',max_length=100)
    request_id:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9_-]{12,100}$')


def object_request_state(db,p,identity,body,*,lock=False):
    """Original request history, independent of today's source activation."""
    require(p,'admin','operator')
    instance=scoped(db,DomainInstance,identity,p,lock)
    release=scoped(db,InstanceRelease,body.release_id,p)
    if release.instance_id!=instance.id or not body.request_id:
        raise HTTPException(422,'请保留原登记编号、版本和完整内容')
    signature=digest(body.model_dump())
    rows=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.actor_id==p.user_id,Audit.action.in_(['instance.object.created','instance.object.retired']),
        Audit.data['instance_id'].as_string()==identity,Audit.data['request_id'].as_string()==body.request_id).limit(2)))
    if len(rows)>1:raise HTTPException(409,'原登记存在不同历史，请保留原请求核对')
    row=rows[0] if rows else None
    if row and row.data.get('request_hash')!=signature:
        raise HTTPException(409,'同一对象登记编号不能更换字段、数值类型或版本')
    return instance,release,row,{'schema':'object-request-recovery-v132','actor_id':p.user_id,
        'workspace_id':p.workspace_id,'instance_id':identity,'request_id':body.request_id,
        'request_hash':signature,'found':bool(row and row.action=='instance.object.created'),
        'object':created_object_view(db,p,instance,release,scoped(db,Entity,row.object_id,p))
            if row and row.action=='instance.object.created' else None,
        'retirement':row.data.get('retirement') if row and row.action=='instance.object.retired' else None}


@router.post('/{identity}/objects/requests/lookup-v132')
def lookup_object_request(identity:str,body:ObjectCreate,p:P,db:DB):
    return object_request_state(db,p,identity,body)[3]


class ObjectRetirement(Contract):
    original:ObjectCreate
    reason:str=Field(min_length=1,max_length=1000)


@router.post('/{identity}/objects/requests/retire-v132')
def retire_object_request(identity:str,body:ObjectRetirement,p:P,db:DB):
    instance,release,prior,result=object_request_state(db,p,identity,body.original,lock=True)
    if prior:return result
    retirement={'request_id':body.original.request_id,'request_hash':result['request_hash'],
        'actor_id':p.user_id,'reason':body.reason,'at':now().isoformat()}
    audit(db,p,'instance.object.retired',instance.id,{'instance_id':identity,
        'request_id':body.original.request_id,'request_hash':result['request_hash'],'retirement':retirement})
    db.commit()
    return {**result,'retirement':retirement}

@router.post('/{identity}/objects',status_code=201)
def add_object(identity:str,body:ObjectCreate,p:P,db:DB):
    require(p,'admin','operator');instance,release=active(db,p,identity,True)
    if release.id!=body.release_id:raise HTTPException(409,'实例版本已变化，请核对对象字段')
    if body.request_id:
        retired=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.actor_id==p.user_id,
            Audit.action=='instance.object.retired',Audit.data['instance_id'].as_string()==identity,
            Audit.data['request_id'].as_string()==body.request_id))
        if retired:raise HTTPException(409,'原对象登记请求已停止，请保留记录；旧请求不会再建立对象')
        prior=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.actor_id==p.user_id,Audit.action=='instance.object.created',
            Audit.data['instance_id'].as_string()==identity,Audit.data['request_id'].as_string()==body.request_id))
        if prior:
            if prior.data['request_hash']!=digest(body.model_dump()):raise HTTPException(409,'同一对象请求不能更换字段内容')
            row=scoped(db,Entity,prior.object_id,p);return created_object_view(db,p,instance,release,row)
    schema=release.data['snapshot']['entity_schemas'].get(body.object_type)
    if not schema:raise HTTPException(422,'对象类型不在当前实例版本中')
    errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(body.attributes))
    if errors:raise HTTPException(422,'对象字段不符合确认的名称、必填项或类型；请核对输入')
    if body.reference.strip() and db.scalar(select(Entity.id).where(Entity.workspace_id==p.workspace_id,
        Entity.namespace==instance.namespace,Entity.entity_type==body.object_type,Entity.external_id==body.reference.strip())):
        raise HTTPException(409,'此业务编号的对象已存在，请核对已有记录')
    row=create_entity(db,p,EntityCreate(package_id=release.package_id,entity_type=body.object_type,
        external_id=body.reference.strip() or 'auto_'+uid(),attributes=body.attributes))
    audit(db,p,'instance.object.created',row.id,{'release':release.id,'instance_id':identity,'request_id':body.request_id,'request_hash':digest(body.model_dump())});db.commit()
    return created_object_view(db,p,instance,release,row)
