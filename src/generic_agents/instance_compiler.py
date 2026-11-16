"""Compile authenticated, confirmed configuration; never trust UI verification flags."""
import copy,json,re,hashlib
from pydantic import Field,model_validator
from sqlalchemy import select
from fastapi import HTTPException
from .contracts import Contract,digest
from .models import SetupMaterial,User,Membership,Capability
from .services import scoped,published
from .evidence import evidence_bytes
from .flow_sources import flow_source_ids
from .report_text_parse_scope import report_text_parse_scope

SENSOR_REVIEW_LIMIT=100

def sensor_source_snapshots(db,p):
    """Freeze only saved source configuration, never intake activity or credentials.

    Configuration fingerprints include credential references so changing authentication
    invalidates a prior check, but neither references nor their values enter the snapshot.
    This is an inventory, not a capability registration or permission to start intake.
    """
    from dataclasses import asdict
    from urllib.parse import urlsplit
    from .models import MqttGateway
    from .mqtt_binding_routes import association_snapshots
    from .mqtt_messages import SensorFields,decode_sensor_message
    rows=db.scalars(select(MqttGateway).where(MqttGateway.workspace_id==p.workspace_id)
        .order_by(MqttGateway.id)).all()
    snapshots=[];issues=[]
    if len(rows)>SENSOR_REVIEW_LIMIT:
        issues.append(f'已保存 {len(rows)} 个传感来源，超过本页建议逐项核对的 {SENSOR_REVIEW_LIMIT} 个；全部来源摘要已保留，请分批返回资源页核对。')
    for row in rows:
        config=row.data;signature=digest(config)
        item={'id':row.id,'name':row.name,'version':row.version,'status':row.status,
            'configuration_hash':signature,'access_mode':'read_only','connection_status':'unknown',
            'device_commands_supported':False,'summary_status':'configuration_saved'}
        try:
            # Validate the shape without checking network allowlists or resolving secrets:
            # a saved source need not be connected or associated with a business yet.
            if not isinstance(config,dict) or set(config)!={'broker','topics','mapping','fields','username_ref','password_ref'}:
                raise ValueError()
            origin=config['broker']
            if not isinstance(origin,str) or len(origin)>300:raise ValueError()
            broker=urlsplit(origin);port=broker.port
            if (broker.scheme not in {'mqtt','mqtts'} or not broker.hostname or not port
                or broker.username is not None or broker.password is not None or broker.path or broker.query or broker.fragment
                or origin!=f'{broker.scheme}://{broker.hostname}:{port}'):
                raise ValueError()
            topics=config['topics']
            if (not isinstance(topics,dict) or not 1<=len(topics)<=32
                or any(not isinstance(topic,str) or not topic or len(topic.encode())>256
                    or any(ord(c)<32 for c in topic) or any(c in topic for c in '#+') or topic.startswith('$')
                    or not isinstance(device,str) or not device.strip() or len(device)>128 or any(ord(c)<32 for c in device)
                    for topic,device in topics.items()) or len(set(topics.values()))!=len(topics)):
                raise ValueError()
            refs=(config['username_ref'],config['password_ref'])
            if (any(not isinstance(ref,str) or (ref and not re.fullmatch(r'GA_MQTT_[A-Z0-9_]{1,80}',ref)) for ref in refs)
                or bool(refs[0])!=bool(refs[1]) or not isinstance(config['fields'],dict)):
                raise ValueError()
            fields=SensorFields(**config['fields'])
            try:decode_sensor_message(b'{}',config['mapping'],fields)
            except ValueError as error:
                if str(error)!='缺少稳定的设备编号或数据编号，不能用到达顺序代替':raise
            associations=association_snapshots(db,p,row.id)
            confirmed=sorted({item['topic'] for item in associations
                if item.get('status')=='active' and item.get('config_hash')==signature and item.get('topic') in topics})
            item.update(broker=origin,topics=dict(sorted(topics.items())),devices=sorted(topics.values()),
                fields=asdict(fields),mapping={key:{'attribute':value['attribute'],'unit':value['unit']}
                    for key,value in sorted(config['mapping'].items())},bound_topics=confirmed,
                associations=associations, association_scope='saved_workspace_inventory_not_execution_authority',
                binding_status='unbound' if not confirmed else 'bound' if len(confirmed)==len(topics) else 'partially_bound')
            if row.status not in {'active','paused'}:raise ValueError()
        except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,HTTPException):
            # Keep the source identity/fingerprint, but never echo malformed raw data.
            item={key:item[key] for key in ['id','name','version','status','configuration_hash','access_mode',
                'connection_status','device_commands_supported']}
            item.update(summary_status='needs_review',binding_status='unknown')
            issues.append('传感来源“'+row.name+'”的配置异常或超出主题/字段上限，未展示未经核对的内容；请返回资源页修改。')
        snapshots.append(item)
    return snapshots,issues

KINDS={'文本':{'type':'string','maxLength':2000},'整数':{'type':'integer'},'数值':{'type':'number'},'是否':{'type':'boolean'},
    '日期':{'type':'string','format':'date'}}

class ObjectField(Contract):
    label:str=Field(min_length=1,max_length=80)
    kind:str
    required:bool=False
    unit:str|None=Field(default=None,max_length=40)
    @model_validator(mode='after')
    def valid(self):
        if self.kind not in KINDS or not self.label.strip():raise ValueError('请填写字段名称并选择支持的类型')
        self.label=self.label.strip()
        if self.unit is not None:
            self.unit=self.unit.strip() or None
        if self.kind not in {'整数','数值'} and self.unit is not None:raise ValueError('仅数值和整数字段可声明单位')
        return self

class ObjectType(Contract):
    name:str=Field(min_length=1,max_length=100)
    fields:list[ObjectField]=Field(min_length=1,max_length=40)
    @model_validator(mode='after')
    def valid(self):
        self.name=self.name.strip()
        if not self.name or len({x.label for x in self.fields})!=len(self.fields):raise ValueError('对象类型和字段名称不能为空或重复')
        if not any(x.required and x.kind=='文本' for x in self.fields):raise ValueError('每种对象需至少一个必填文本字段以便识别')
        return self

def identifier(prefix,label):return prefix+'_'+hashlib.sha256(label.encode()).hexdigest()[:16]

def names(configuration):
    value=configuration.get('businesses',[])
    rows=[x if isinstance(x,str) else x['name'] for x in value]
    if len(rows)>20 or any(not x.strip() or len(x)>100 for x in rows) or len(set(rows))!=len(rows):
        raise HTTPException(422,'业务名称不能为空或重复，当前最多20个业务')
    return rows

def _graph_role_snapshots(graph_bindings, requested, members):
    """Keep approved graph history even when the proposed setup is blocked.

    These snapshots come only from validated bindings, never from requested
    replacements. Their presence is not current membership or enablement.
    """
    snapshots={};issues={}
    for name,binding in graph_bindings.items():
        current=[]
        for role,actor in binding['role_bindings'].items():
            original={key:copy.deepcopy(actor[key]) for key in ('user_id','username')}
            if role in snapshots and snapshots[role]!=original:
                raise HTTPException(409,role+'：不同已批准业务的同名岗位负责人不一致，请先核对原采用版本；不能合并或替换负责人')
            snapshots[role]=original
            if requested.get(role)!=actor['user_id']:
                current.append(role+'：请使用原已批准的负责人；本次输入未替换原岗位绑定')
            if actor['user_id'] not in members:
                current.append(role+'：原已批准负责人已停用或失去当前工作区办理权限；原绑定仅保留供核对')
        issues[name]=current
    return snapshots,issues


def _retain_graph_role_snapshots(bindings, snapshots):
    """A legacy role with the same name cannot overwrite an approved graph."""
    for role,original in snapshots.items():
        if role in bindings and bindings[role]!=original:
            raise HTTPException(409,role+'：线性业务与已批准执行图的同名岗位负责人不一致，请明确区分岗位；不能统一为另一负责人')
    bindings.update(copy.deepcopy(snapshots))


def flow_for(config,name):
    seen=set()
    while name and name not in seen:
        seen.add(name)
        if name in config.get('flows',{}):return copy.deepcopy(config['flows'][name])
        name=config.get('businessAliases',{}).get(name)
    return {}

def signature_value(config,flow):
    sources=[]
    for identity in flow_source_ids(flow):
        material=next((x for x in config.get('materials',[]) if x['id']==identity),None)
        item={'id':identity,'archived':bool(material and material.get('archived')),'missing':material is None}
        if material:item.update(name=material['name'],hash=material['hash'])
        sources.append(item)
    value={'domain':config.get('instanceProfile',{}).get('domain',''),'goal':config.get('instanceProfile',{}).get('goal',''),
        'trigger':flow.get('trigger'),'output':flow.get('output'),'steps':flow.get('steps'),'sources':sources}
    if flow.get('citations') or flow.get('inferenceReason'):
        value.update(citations=flow.get('citations',[]),inferenceReason=flow.get('inferenceReason',''))
    if 'reportSource' in flow:value['reportSource']=copy.deepcopy(flow['reportSource'])
    from .report_full_semantic_configuration_v1 import signature_context
    semantic=signature_context(config,flow)
    if semantic is not None:
        value['fullSemanticSource']=copy.deepcopy(flow['fullSemanticSource'])
        value['fullSemanticContext']=semantic
    return value


def _citation_issues(db,p,citations,label,*,flow=None,path=None,name=None):
    issues=[]
    for index,citation in enumerate(citations):
        if not isinstance(citation,dict) or any(not isinstance(citation.get(key),str) or not citation[key]
                for key in ('material_id','block_id','quote')):
            issues.append(label+'：原文引用不完整');continue
        if 'span_binding_id' in citation:
            from .report_region_projection import citation_valid
            if (flow is None or path is None or not isinstance(citation['span_binding_id'],str)
                    or not citation_valid(db,p,flow,'current_flow',path+'/citations/'+str(index),citation,name=name)):
                issues.append(label+'：跨区域原文引用的正式来源或当前位置无法核对')
            continue
        try:ref=scoped(db,SetupMaterial,citation['material_id'],p)
        except HTTPException:
            issues.append(label+'：原文引用不存在或不属于当前工作区');continue
        block=next((x for x in ref.data.get('blocks',[]) if x['id']==citation['block_id']),None)
        if ref.archived or ref.status!='parsed' or not block or not citation['quote'].strip() or citation['quote'] not in block['text']:
            issues.append(label+'：原文引用已失效')
        else:
            try:evidence_bytes(db,p,ref.evidence_id)
            except HTTPException:issues.append(label+'：引用原件完整性检查未通过')
    return issues

def flow_issues(db,p,config,flow,*,name=None,require_signature=True):
    issues=[];steps=flow.get('steps',[]);ids=[s.get('id') for s in steps]
    if not flow.get('trigger','').strip() or not flow.get('output','').strip():issues.append('请补齐启动条件与最终产出')
    if not 1<=len(steps)<=16 or len(set(ids))!=len(ids) or any(not isinstance(x,str) or not x for x in ids):
        issues.append('需要1—16个具有唯一标识的步骤')
    try:
        if require_signature and (json.loads(flow.get('confirmedSignature') or 'null')!=signature_value(config,flow) or not flow.get('confirmedAt')):
            issues.append('流程或资料已变化，请重新确认')
    except (ValueError,TypeError):issues.append('流程确认记录无效，请重新确认')
    for step_index,step in enumerate(steps):
        label=step.get('name') or '未命名步骤'
        if not step.get('name','').strip() or not step.get('output','').strip():issues.append(label+'：需填写名称和输出')
        if step.get('agent') not in ['人工确认','业务智能体','多模感知智能体','数据管理智能体']:issues.append(label+'：负责角色无效')
        if step.get('agent')=='人工确认' and not step.get('role','').strip():issues.append(label+'：需指定人工核对角色')
        if step.get('nextId','auto') not in [*ids,'auto','end'] or step.get('nextId')==step.get('id'):issues.append(label+'：下一步骤无效')
        if step.get('condition','').strip() and (step.get('elseId') not in [*ids,'end'] or step.get('elseId')==step.get('id')):issues.append(label+'：请指定条件不满足的去向')
        if step.get('sourceId'):
            try:material=scoped(db,SetupMaterial,step['sourceId'],p)
            except HTTPException:
                issues.append(label+'：所引用的资料不存在或不属于当前工作区');continue
            cached=next((x for x in config.get('materials',[]) if x['id']==material.id),None)
            if not cached or cached['hash']!=material.sha256 or material.archived or material.status!='parsed':
                issues.append(label+'：资料不存在、已归档、未解析完成或版本不一致')
            else:
                try:evidence_bytes(db,p,material.evidence_id)
                except HTTPException:issues.append(label+'：原件完整性检查未通过，请恢复原件')
        issues.extend(_citation_issues(db,p,step.get('citations',[]),label,flow=flow,path='/steps/'+str(step_index),name=name))
    issues.extend(_citation_issues(db,p,flow.get('citations',[]),'流程整体依据',flow=flow,path='/business',name=name))
    return list(dict.fromkeys(issues))

def compile_configuration(db,p,configuration,objects,role_bindings,*,target_instance_id=None):
    from .setup_api import validate_configuration
    validate_configuration(configuration)
    config=copy.deepcopy(configuration);profile=config.get('instanceProfile',{})
    general=[]
    if not profile.get('name','').strip() or not profile.get('domain','').strip():general.append('请填写实例名称和业务领域')
    business_names=names(config)
    from .workflow_abilities import require_binding_reviewed
    for name in business_names:
        for step in flow_for(config,name).get('steps',[]):
            require_binding_reviewed(step)
    from .authored_workflow_runtime_v208 import verify_configuration as verify_authored_configuration
    authored_items=verify_authored_configuration(db,p,configuration)
    from .report_configuration_handoff import configuration_source_issues,configuration_source_snapshots
    report_source_issues=configuration_source_issues(db,p,config)
    general.extend(issue for values in report_source_issues.values() for issue in values)
    if not business_names:general.append('请先确定业务清单')
    automation=config.get('automation')
    if not isinstance(automation,dict) or not isinstance(automation.get('policies'),dict):general.append('请保存并确认自动化范围')
    if not isinstance(automation,dict):automation=None
    from .report_full_semantic_schema_protection import require_compiled_objects
    require_compiled_objects(config,objects)
    from .object_type_contract_v170 import ObjectTypeV170, SCHEMA as object_schema, check_object_type
    types=[]
    for value in objects:
        if type(value) is dict and value.get('schema') == object_schema:
            checked = check_object_type(value)
            origin = config.get('instanceObjectOrigins', {}).get(checked['name'], {})
            if origin.get('schema') != 'report-object-schema-origin-v170':
                raise HTTPException(409, '新对象需先明确采用完整数据结构并核对真实来源；标记本身不能授予编译权限')
            types.append(ObjectTypeV170.model_validate(checked))
        else:
            types.append(ObjectType.model_validate(value))
    from .setup_object_proposals import verify_origins, origin_material_ids
    verify_origins(db,p,config,[x.name for x in types])
    if not 1<=len(types)<=40 or len({x.name for x in types})!=len(types):general.append('需要1—40种名称不重复的业务对象')
    schemas={};catalog=[]
    for obj in types:
        if isinstance(obj, ObjectTypeV170):
            from .object_type_contract_v170 import object_contract
            contract=object_contract(obj.model_dump())
            key=contract['key'];schemas[key]=contract.pop('schema')
            catalog.append({**contract, 'contract_schema': object_schema})
            continue
        key=identifier('object',obj.name);fields=[];properties={};required=[]
        for field in obj.fields:
            fid=identifier('field',field.label);properties[fid]={**KINDS[field.kind],'title':field.label}
            if field.unit is not None:properties[fid]['unit']=field.unit
            if field.required:
                required.append(fid)
                if field.kind=='文本':properties[fid].update(minLength=1,pattern=r'\S')
            fields.append({'key':fid,**field.model_dump()})
        schemas[key]={'type':'object','title':obj.name,'properties':properties,'required':required,'additionalProperties':False}
        catalog.append({'key':key,'name':obj.name,'fields':fields})
    relation_snapshot={};relation_material_ids=set()
    if 'instanceRelationTypes' in config or 'instanceRelationOrigins' in config:
        from .instance_relation_types import compile_types
        relation_types,relation_issues,relation_source_ids=compile_types(db,p,config,{x.name for x in types})
        general.extend(relation_issues);relation_material_ids=set(relation_source_ids)
        relation_snapshot={'relation_types':relation_types,'relation_material_ids':sorted(relation_material_ids),
            'relation_definitions':copy.deepcopy(config.get('instanceRelationTypes',[])),
            'relation_origins':{item['name']:copy.deepcopy(config['instanceRelationOrigins'][item['name']])
                for item in config.get('instanceRelationTypes',[]) if item['name'] in config.get('instanceRelationOrigins',{})}}
    members={m.user_id:u for m,u in db.execute(select(Membership,User).join(User,User.id==Membership.user_id).where(
        Membership.workspace_id==p.workspace_id,Membership.role.in_(['admin','operator']),User.active.is_(True)))}
    resources=copy.deepcopy(config.get('resources',[]));resource_issues={}
    for resource in resources:
        resource.pop('backend_source_proof',None)
        resource.pop('backend_audio_proof',None)
        try:
            from .audio_stream_data_plan import compile_resource as compile_audio_resource
            audio_proof=compile_audio_resource(db,p,resource)
            if audio_proof is not None:resource['backend_audio_proof']=audio_proof
        except (HTTPException,ValueError,KeyError,TypeError,AttributeError) as error:
            resource.pop('backend_audio_proof',None)
            resource_issues[resource.get('id')]='音轨资源尚未通过服务器登记核对：'+(str(error.detail) if isinstance(error,HTTPException) else '来源结构无效')
        if resource.get('category')=='maps' and resource.get('protocol')=='WMS':
            try:
                from .wms_source import resource_proof
                proof=resource_proof(db,p,resource)
                resource['backend_source_proof']=proof
            except (HTTPException,ValueError,KeyError,TypeError,AttributeError) as error:
                resource.pop('backend_source_proof',None)
                resource_issues[resource.get('id')]='地图来源尚未通过服务器登记核对：'+(str(error.detail) if isinstance(error,HTTPException) else '来源结构无效')
        if resource.get('category')=='maps' and resource.get('protocol') in ('WMTS','ArcGIS MapServer','ArcGIS ImageServer'):
            try:
                from .map_saved_source import resource_proof
                resource['backend_source_proof']=resource_proof(db,p,resource)
            except (HTTPException,ValueError,KeyError,TypeError,AttributeError) as error:
                resource.pop('backend_source_proof',None)
                resource_issues[resource.get('id')]='已保存地图尚未通过服务器登记核对：'+(str(error.detail) if isinstance(error,HTTPException) else '来源结构无效')
        if resource.get('category')=='maps' and resource.get('protocol')=='ArcGIS FeatureServer':
            try:
                from .feature_gateway_api import resource_proof
                resource['backend_source_proof']=resource_proof(db,p,resource)
            except (HTTPException,ValueError,KeyError,TypeError,AttributeError) as error:
                resource.pop('backend_source_proof',None)
                resource_issues[resource.get('id')]='已保存要素尚未通过服务器登记核对：'+(str(error.detail) if isinstance(error,HTTPException) else '来源结构无效')
        if resource.get('protocol')=='PUBLIC_OBSERVATION':
            try:
                from .public_observations import resource_proof
                resource['backend_source_proof']=resource_proof(db,p,resource)
            except (HTTPException,ValueError,KeyError,TypeError,AttributeError) as error:
                resource.pop('backend_source_proof',None)
                resource_issues[resource.get('id')]='公开观测来源尚未通过服务器登记核对：'+(str(error.detail) if isinstance(error,HTTPException) else '来源结构无效')
        if resource.get('category')=='video' and resource.get('protocol')=='RTSP / H.264':
            try:
                from .instance_observation_catalog import media_resource_proof
                resource['backend_source_proof']=media_resource_proof(db,p,resource)
            except (HTTPException,ValueError,KeyError,TypeError,AttributeError) as error:
                resource.pop('backend_source_proof',None)
                resource_issues[resource.get('id')]='视频来源尚未通过服务器登记核对：'+(str(error.detail) if isinstance(error,HTTPException) else '来源结构无效')
    bindings={};businesses=[]
    graph_bindings={};new_graph_names=set()
    if config.get('fullSemanticGraphBindings'):
        from .report_full_semantic_graph_runtime_v4 import for_configuration
        graph_runtime=for_configuration(config)
        graph_bindings={a['binding']['business_name']:a['binding'] for a in graph_runtime.validated_bindings(db,p,config)}
    if config.get('businessFlowExecutions'):
        from . import business_flow_execution_runtime as graph_runtime
        new_bindings={a['binding']['business_name']:a['binding'] for a in graph_runtime.validated_bindings(db,p,config)}
        if set(new_bindings)&set(graph_bindings):raise HTTPException(409,'同一业务不能同时采用不同来源的执行图')
        graph_bindings.update(new_bindings);new_graph_names=set(new_bindings)
    if authored_items:
        from . import authored_workflow_runtime_v208 as graph_runtime
        authored_bindings={a['binding']['business_name']:a['binding'] for a in authored_items}
        if graph_bindings or config.get('fullSemanticRuntimeBindings'):
            raise HTTPException(409,'人工编制模板当前不能与其他执行图计划混合；原配置和出处保留')
        graph_bindings.update(authored_bindings);new_graph_names.update(authored_bindings)
    if any(flow_for(config,name).get('businessFlowSource') and name not in new_graph_names for name in business_names):
        raise HTTPException(409,'已核人工流程缺少真实采用出处，不能退回旧线性执行')
    graph_roles,graph_role_issues=_graph_role_snapshots(graph_bindings,role_bindings,members)
    bindings.update(copy.deepcopy(graph_roles))
    for name in business_names:
        flow=flow_for(config,name)
        if name in new_graph_names:
            # This facade is a lossless display/index of a typed graph, not a
            # legacy <=16-step workflow. Its whole source and graph were cold
            # verified above, including all native quote/port/edge mappings.
            if flow!=graph_bindings[name]['flow']:raise HTTPException(409,'业务流程与真实采用的完整图不同')
            issues=[]
        else:
            issues=flow_issues(db,p,config,flow,name=name,
                require_signature=name not in config.get('fullSemanticRuntimeBindings',{}) and name not in graph_bindings)
        issues.extend(report_source_issues.get(name,[]))
        for resource in resources:
            if name in resource.get('businesses',[]) and resource.get('id') in resource_issues:issues.append(resource.get('name','地图来源')+'：'+resource_issues[resource.get('id')])
        policy=(automation or {}).get('policies',{}).get(name)
        from .setup_automation import review_state
        if review_state(config,name)['status']=='needs_review':issues.append('业务流程或自动化范围已变化，请回第4步重新核对并明确确认')
        if not isinstance(policy,dict) or not isinstance(policy.get('rules'),dict):issues.append('此业务的自动化范围尚未确认')
        else:
            for action,rule in policy['rules'].items():
                resource_audio_observe=(action=='observe' and isinstance(rule,dict) and rule.get('mode')=='自动运行'
                    and isinstance(rule.get('resources'),list)
                    and any(type(resource.get('backend_audio_proof')) is dict
                        and name in resource.get('businesses',[]) and resource['id'] in rule.get('resources',[])
                        for resource in resources))
                if not isinstance(rule,dict) or rule.get('mode') not in ['自动运行','执行前确认','仅给出建议']:
                    issues.append('自动化动作设置无效')
                elif name not in graph_bindings and rule['mode']!='仅给出建议' and not resource_audio_observe and action not in {s.get('execution',{}).get('automation_action') for s in flow.get('steps',[]) if isinstance(s.get('execution'),dict) and s.get('agent')!='人工确认'}:issues.append('自动化动作“'+action+'”的实例执行入口尚未接通；此版本不能启用该动作')
        if name in graph_bindings:
            binding=graph_bindings[name]
            from .instance_native_graph_scope_v136 import binding_issues
            issues.extend(binding_issues(binding,target_instance_id))
            issues.extend(graph_role_issues[name])
            businesses.append({'id':identifier('business',name),'name':name,'flow':flow,
                'issues':list(dict.fromkeys(issues)), 'available':not issues,
                'execution_mode':'confirmed_graph_workflow' if not issues else 'blocked'})
            continue
        for step in flow.get('steps',[]):
            if step.get('agent')=='人工确认':
                if 'planning_tools' in step:
                    try:
                        from .native_planning_grants import pin as pin_planning
                        step['planning_tools']=pin_planning(db,p,step['planning_tools'])
                    except HTTPException as error:
                        issues.append(step.get('name','未命名步骤')+'：'+str(error.detail))
                role=step.get('role','');actor_id=role_bindings.get(role)
                if actor_id not in members:issues.append(role+'：请关联有执行权限的真实账号')
                else:bindings[role]={'user_id':actor_id,'username':members[actor_id].username}
            else:
                if step.get('planning_tools'):
                    issues.append(step.get('name','未命名步骤')+'：规划工具授权只能设置在人工确认步骤')
                try:
                    from .workflow_abilities import pin
                    step['execution']=pin(db,p,step,policy)
                    for binding in step['execution'].get('bindings',{}).values():
                        if binding.get('source')=='previous_output':
                            previous=next((x for x in flow['steps'] if x['id']==binding['step_id']),None)
                            if not previous or previous['id']==step['id'] or previous['agent']=='人工确认':raise HTTPException(422,'前序输入需引用另一个智能节点的真实输出')
                except HTTPException as error:issues.append(step.get('name','未命名步骤')+'：'+str(error.detail))
        businesses.append({'id':identifier('business',name),'name':name,'flow':flow,'issues':list(dict.fromkeys(issues)),
            'available':not issues,'execution_mode':('registered_ability_workflow' if any(s.get('agent')!='人工确认' for s in flow.get('steps',[])) else 'confirmed_human_workflow') if not issues else 'blocked'})
    _retain_graph_role_snapshots(bindings,graph_roles)
    # Data management declares the actual stores used, not imaginary per-domain DBs.
    from .instance_data_plan import derive_plan
    plan=derive_plan(catalog,businesses,resources)
    if relation_snapshot.get('relation_types'):
        plan.append({'name':'对象关系与核查依据','storage':'PostgreSQL关系记录、版本及核查历史',
            'status':'versioned_storage_available','details':'按已确认的关系类型登记具体对象联系，保留原文或观测依据及人工核查。类型出处不代表具体关系事实；冲突、断链或来源变化作为缺口，不自动认定现场状态。原业务任务可在已选对象范围内查询关系链并计算。'})
    object_material_ids=origin_material_ids(config,[x.name for x in types])
    report_sources=configuration_source_snapshots(db,p,config)
    new_source_ids={m['id'] for name in new_graph_names for m in graph_bindings[name]['source_materials']}
    flow_source_names={name for name in new_graph_names if graph_bindings[name]['schema'] in
        {'business-flow-execution-binding-v1','business-flow-nested-execution-binding-v195'}}
    authored_source_names=new_graph_names-flow_source_names
    flow_material_ids={m['id'] for name in flow_source_names for m in graph_bindings[name]['source_materials']}
    new_source_origins={identity:[copy.deepcopy(config['businessFlowExecutions'][name]) for name in sorted(flow_source_names)
        if identity in {m['id'] for m in graph_bindings[name]['source_materials']}] for identity in flow_material_ids}
    from .authored_workflow_materials_v243 import source_origins as authored_material_origins
    authored_source_origins=authored_material_origins(db,p,config,graph_bindings,authored_source_names)
    material_ids={identity for b in businesses for identity in material_ids_for_flow(b['flow'])}|object_material_ids|relation_material_ids|set(report_sources)|new_source_ids
    materials=[{'id':x.id,'version':x.version,'hash':x.sha256,'evidence_id':x.evidence_id,'status':x.status,'archived':x.archived,
        **({'business_flow_execution_sources':new_source_origins[x.id]} if x.id in new_source_origins else {}),
        **({'authored_workflow_sources':authored_source_origins[x.id]} if x.id in authored_source_origins else {}),
        **({'blocks_hash':digest(x.data.get('blocks',[]))} if x.id in object_material_ids|relation_material_ids|set(report_sources)|new_source_ids else {}),
        **({'report_sources':report_sources[x.id]} if x.id in report_sources else {})}
        for x in db.scalars(select(SetupMaterial).where(SetupMaterial.workspace_id==p.workspace_id,SetupMaterial.id.in_(material_ids)))]
    sensor_sources,sensor_source_issues=sensor_source_snapshots(db,p)
    snapshot={'profile':profile,'businesses':businesses,'object_types':catalog,'entity_schemas':schemas,'roles':bindings,
        'materials':materials,'data_plan':plan,'automation':config.get('automation'),
        'resources':resources,'equipment':config.get('equipment'),'general_issues':general,
        'sensor_sources':sensor_sources,'sensor_source_issues':sensor_source_issues,
        'object_material_ids':sorted(object_material_ids),
        'deployment':{'foundation':'cloud_qwen_deepseek','specialists':'local'},**relation_snapshot}
    if graph_bindings:
        snapshot['semantic_runtime_plan']=graph_runtime.compile_plan(db,p,
            configuration if authored_items else config,businesses,bindings)
        graph_runtime.verify_plan(snapshot)
        snapshot['data_plan']=derive_plan(catalog,businesses,resources)
        snapshot['data_plan'].extend(copy.deepcopy(item) for item in plan
            if item.get('name') == '对象关系与核查依据')
    elif config.get('fullSemanticRuntimeBindings'):
        from .report_full_semantic_runtime_v2 import compile_plan
        snapshot['semantic_runtime_plan']=compile_plan(db,p,config,businesses)
        from .report_full_semantic_runtime_v2 import verify_plan
        verify_plan(snapshot)
        # Recompute from final supported/blocked status; no stale pre-runtime view.
        snapshot['data_plan']=derive_plan(catalog,businesses,resources)
        snapshot['data_plan'].extend(copy.deepcopy(item) for item in plan
            if item.get('name') == '对象关系与核查依据')
    else:
        from .instance_full_semantics import compile_plan
        semantic_plan=compile_plan(db,p,config,businesses)
        if semantic_plan is not None:
            snapshot['semantic_plan']=semantic_plan
            snapshot['general_issues'].append('完整业务设计已保留；正式来源和完整协同执行仍待核对，当前不能启用。')
    if config.get('fullSemanticSchemaAdoptions') or 'semantic_runtime_plan' in snapshot:
        snapshot.update(object_origins=copy.deepcopy(config.get('instanceObjectOrigins',{})),
            full_semantic_schema_adoptions=copy.deepcopy(config.get('fullSemanticSchemaAdoptions',[])))
        snapshot.setdefault('relation_types',{})
        snapshot.setdefault('relation_origins',{})
    from .audio_stream_data_plan import build as build_audio_plan,verify as verify_audio_plan,KEY as AUDIO_PLAN
    audio_plan=build_audio_plan(snapshot)
    if audio_plan is not None:snapshot[AUDIO_PLAN]=audio_plan
    verify_audio_plan(snapshot)
    from .instance_data_contract_dispatch import build_requirements
    snapshot['data_requirements']=build_requirements(snapshot)
    # Only a new, explicitly confirmed publication can acquire this entry
    # declaration. Existing releases are never upgraded by a runtime fallback.
    from .business_automation_admission import compile_admission, KEY as AUTOMATION_ADMISSION
    automation_admission=compile_admission(snapshot,config)
    if automation_admission is not None:snapshot[AUTOMATION_ADMISSION]=automation_admission
    from .native_pixel_data_plan_v250 import build as build_pixel_plan, verify as verify_pixel_plan
    pixel_plan=build_pixel_plan(snapshot)
    if pixel_plan is not None:snapshot['native_pixel_results_v250']=pixel_plan
    verify_pixel_plan(snapshot)
    from .native_sound_data_plan import build as build_sound_plan, verify as verify_sound_plan
    sound_plan=build_sound_plan(snapshot)
    if sound_plan is not None:snapshot['native_sound_results']=sound_plan
    verify_sound_plan(snapshot)
    from .pixel_question_catalog_v252 import build as build_pixel_question_facet
    question_facet=build_pixel_question_facet(snapshot)
    if question_facet is not None:snapshot['pixel_question_facet_v252']=question_facet
    return snapshot,digest(snapshot)


def material_ids_for_flow(flow):
    return set(flow_source_ids(flow))

@report_text_parse_scope()
def verify_material_snapshots(db,p,materials,allow_archived=False):
    for source in materials:
        if 'report_sources' in source:
            from .report_configuration_handoff import verify_report_material_snapshot
            verify_report_material_snapshot(db,p,source)
        if 'authored_workflow_sources' in source:
            from .authored_workflow_materials_v243 import verify_material_snapshot as verify_authored_material
            verify_authored_material(db,p,source)
        qualified_source='business_flow_execution_sources' in source
        if qualified_source:
            from .business_flow_execution_source import verify_material_snapshot
            verify_material_snapshot(db,p,source)
        row=scoped(db,SetupMaterial,source['id'],p)
        source_status_ok=row.status=='parsed' or (qualified_source and row.status==source.get('status')=='needs_input')
        if row.sha256!=source['hash'] or row.evidence_id!=source['evidence_id'] or not source_status_ok or row.archived and not allow_archived:
            raise HTTPException(409,'任务所引用的资料已变化或不可用，请核对原件；原处理记录保留')
        if 'blocks_hash' in source and (row.version!=source['version'] or digest(row.data.get('blocks',[]))!=source['blocks_hash']):
            raise HTTPException(409,'对象字段或关系定义引用的解析版本已变化，请重新核对生成版本；原记录保留')
        evidence_bytes(db,p,row.evidence_id)
