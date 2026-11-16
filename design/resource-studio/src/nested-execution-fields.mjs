// Explicit proposed fields. Only the server supplies source hashes and graph IDs.
import {fieldType,outputSchema} from './business-flow-execution-binding-fields.mjs';
import {graphWireClone,graphWireRequestJSON} from './full-semantic-graph-wire.mjs';
import {freshNumber} from './typed-numbers-v132.mjs';
import {resolveSchema} from './full-semantic-graph-editor-model.mjs';
export const nestedCopy=graphWireClone;
export const nestedObject=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
export const NESTED_KINDS=Object.freeze({string:'文字',integer:'整数',number:'数值',boolean:'是否',date:'日期',object:'一组字段',array:'多条记录'});
export const NESTED_ACTORS=Object.freeze({human:'人工岗位',business_agent:'业务智能体',perception_agent:'多模感知智能体',data_agent:'数据管理智能体',task_center:'任务中心',execution_service:'执行服务'});
export const NESTED_GUARDS=Object.freeze({start:'开始条件',precondition:'前置条件',stop:'停止条件',exception:'例外条件'});
const ROLES={business_agent:'business',perception_agent:'perception',data_agent:'data',task_center:'business',execution_service:'business'};
const SERVICES={task_center:['in_app_notice'],execution_service:['resense_with_receipt','resense_from_context']};
const need=(ok,message)=>{if(!ok)throw Error(message||'执行字段尚未填写完整，可以先保存草稿。');};
const objectSchema=()=>({type:'object',properties:{},required:[],additionalProperties:false});
const guards=()=>Object.fromEntries(Object.keys(NESTED_GUARDS).map(key=>[key,null]));
const list=v=>Array.isArray(v)?v:[];
const same=(a,b)=>graphWireRequestJSON(a)===graphWireRequestJSON(b);
const at=(value,path)=>path.split('/').slice(1).reduce((row,key)=>row?.[key.replaceAll('~1','/').replaceAll('~0','~')],value);
export const nestedAt=at;
export function nestedSchemaFields(schema){try{const root=resolveSchema(schema,schema);need(root?.type==='object'&&nestedObject(root.properties),'登记合同的根结构需为实际对象字段。');return Object.entries(root.properties);}catch{throw Error('当前登记结构尚不能完整读取，原输入保留，不能跳过实际字段。');}}
export function newNestedShape(title=''){return {title,kind:'',nullable:null,unit:'',children:[],item:null};}
export function nestedSchema(shape,label='资料字段'){
 need(nestedObject(shape)&&Object.hasOwn(NESTED_KINDS,shape.kind)&&typeof shape.nullable==='boolean',label+'：请选择实际类型和是否允许空值。');
 const type=shape.kind==='date'?'string':shape.kind,result={type:shape.nullable?[type,'null']:type};
 if(shape.title)result.title=shape.title;if(shape.unit)result['x-unit']=shape.unit;
 if(shape.kind==='date')result.format='date';
 if(shape.kind==='object'){
  result.properties={};result.required=[];result.additionalProperties=false;
  for(const child of list(shape.children)){need(typeof child.key==='string'&&/^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(child.key)&&!Object.hasOwn(result.properties,child.key),label+'：子字段名称需唯一且完整。');result.properties[child.key]=nestedSchema(child,label+' · '+(child.title||'子字段'));if(child.required!==false)result.required.push(child.key);}
 }
 if(shape.kind==='array'){need(nestedObject(shape.item),label+'：请说明每条记录的实际结构。');result.items=nestedSchema(shape.item,label+' · 每条记录');}
 return result;
}
export function initialNestedConfiguration(ctx){
 const source=ctx.source_value,ports={};
 for(const action of ctx.all_action_ports)for(const port of [...action.input_ports,...action.output_ports])ports[port.path]={contract:{data_type:'',unit:null},kind:'',nullable:null,sources:[]};
 return {selected_flow_index:ctx.flow_index,causal_bindings:null,flows:source.business.flows.map((flow,flow_index)=>({flow_index,business_name:flow.name,execution_bindings:null,role_bindings:{},node_actions:{},external_node_ids:[],entry_mode:null,buffers:{nodes:{},controls:{},guards:{}}})),buffers:{entries:[],ports},recovery:[]};
}
export function nestedFlowForm(value,index){return value?.flows?.find(flow=>flow.flow_index===index)||null;}
const extractorId=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const extractorHash=/^[0-9a-f]{64}$/;
const extractorFields=(row,keys)=>nestedObject(row)&&Object.keys(row).length===keys.length&&keys.every(key=>Object.hasOwn(row,key));
export function validObservationExtractorOptions(value){
 return Array.isArray(value)&&new Set(value.map(row=>row?.capability_id)).size===value.length&&value.every(row=>extractorFields(row,['capability_id','capability_hash','release_version','name'])&&extractorId.test(row.capability_id||'')&&extractorHash.test(row.capability_hash||'')&&typeof row.release_version==='string'&&!!row.release_version&&typeof row.name==='string'&&!!row.name);
}
export function validObservationExtractorDraft(value){
 return Array.isArray(value)&&value.length<=8&&new Set(value.map(row=>row?.capability_id)).size===value.length&&value.every(row=>extractorFields(row,['capability_id','capability_hash','actor_id'])&&extractorId.test(row.capability_id||'')&&extractorHash.test(row.capability_hash||'')&&(row.actor_id===''||extractorId.test(row.actor_id||'')));
}
function requireObservationResense(ctx,flow){
 if(!list(flow.observation_extractors).length)return;
 const configured=list(ctx.action_ports).some(action=>{
  if(action.actor!=='execution_service')return false;
  const node=flow.buffers?.nodes?.[action.path];
  return !!node&&eligibleNestedCapabilities(ctx,action).some(cap=>cap.id===node.capability_id
   &&cap.hash===node.capability_hash&&cap.release_version===node.release_version
   &&['resense_with_receipt','resense_from_context'].includes(cap.operation));
 });
 need(configured,'已保留观测提取工具。请先在本流程配置一个已发布的补采服务步骤，再检查采用。');
}
export function compileObservationExtractors(ctx,flow){
 const selection=Object.hasOwn(flow,'observation_extractors')?flow.observation_extractors:[];
 need(validObservationExtractorDraft(selection),'已保留的观测提取选择无法核对。');
 if(!selection.length)return {};
 const options=ctx.observation_extractor_options??[];
 need(ctx.simulation_only===true&&validObservationExtractorOptions(options),'当前流程未开放工程模拟观测提取；原选择保留。');
 for(const row of selection){
  need(options.some(option=>option.capability_id===row.capability_id&&option.capability_hash===row.capability_hash),'已选观测提取工具或发布版本已变化，请明确重新选择；原选择保留。');
  need(ctx.members.some(member=>member.user_id===row.actor_id),'请为每项观测提取选择仍可用的负责人；原选择保留。');
 }
 requireObservationResense(ctx,flow);
 return {observation_extractors:nestedCopy(selection)};
}
export function nestedNodeForm(flow,action){
 return flow?.buffers?.nodes?.[action.path]||{actor_id:'',capability_id:'',outputs:Object.fromEntries(action.output_ports.map(port=>[String(port.ordinal),action.actor==='human'?newNestedShape(port.label):''])),inputs:{},guards:{},review_required:false};
}
export function eligibleNestedCapabilities(ctx,action){return list(ctx.capabilities).filter(cap=>list(cap.eligible_agent_roles).includes(ROLES[action.actor])&&(SERVICES[action.actor]?cap.adapter==='workflow_service'&&cap.service_actor===action.actor&&SERVICES[action.actor].includes(cap.operation):cap.adapter!=='workflow_service'));}
export function nestedSourceOptions(ctx,value,flowIndex){
 const entries=list(value.buffers?.entries).flatMap((entry,index)=>entry.flow_index===flowIndex?[{value:'/entry_ports/'+index,label:'流程启动资料：'+(entry.label||'待命名资料 '+(index+1))}]:[]);
 return [...entries,...ctx.all_action_ports.filter(action=>action.flow_index===flowIndex).flatMap(action=>action.output_ports.map(port=>({value:port.path,label:action.name+' · 结果 '+(port.ordinal+1)+'：'+port.label})))];
}
export function nestedReferenceSources(ctx,value,port){return nestedSourceOptions(ctx,value,ctx.all_action_ports.find(action=>action.input_ports.some(item=>item.path===port.path))?.flow_index).filter(item=>list(value.buffers?.ports?.[port.path]?.sources).includes(item.value));}
function contract(row,label){need(nestedObject(row?.contract)&&typeof row.contract.data_type==='string'&&row.contract.data_type.trim()&&(row.contract.unit===null||typeof row.contract.unit==='string'&&row.contract.unit.trim()),label+'：请说明数据含义及明确单位（无单位也须确认）。');need(Object.hasOwn(NESTED_KINDS,row.kind)&&typeof row.nullable==='boolean',label+'：请明确实际数据类型和空值规则。');return nestedCopy(row.contract);}
export function nestedCausalBindings(ctx,value){
 const entries=list(value.buffers.entries),ports=value.buffers.ports||{},entry_ports=entries.map((entry,index)=>({path:'/entry_ports/'+index,flow_path:'/business/flows/'+entry.flow_index,contract:contract(entry,entry.label||'启动资料 '+(index+1))})),output_contracts=[],input_bindings=[];
 for(const action of ctx.all_action_ports){
  for(const port of action.output_ports)output_contracts.push({path:port.path,contract:contract(ports[port.path],action.name+' · 结果 '+(port.ordinal+1))});
  for(const port of action.input_ports){const row=ports[port.path],sources=list(row?.sources),choices=nestedSourceOptions(ctx,value,action.flow_index);need(sources.length&&new Set(sources).size===sources.length&&sources.every(source=>choices.some(choice=>choice.value===source)),action.name+' · 资料 '+(port.ordinal+1)+'：请选择同一流程的完整来源；多个来源按备选处理。');input_bindings.push({input_path:port.path,contract:contract(row,action.name+' · 资料 '+(port.ordinal+1)),sources:[...sources]});}
 }
 return {schema:'business-flow-nested-causal-bindings-v192',source_plan_hash:ctx.source_plan_hash,scope_binding_hash:ctx.scope_binding_hash,entry_ports,output_contracts,input_bindings};
}
function scalar(raw,kind,label){if(kind==='boolean'){need(['true','false'].includes(raw),label+'：请选择是或否。');return raw==='true';}if(['number','integer'].includes(kind))return freshNumber(raw,kind,label);need(['string','date'].includes(kind)&&typeof raw==='string',label+'：请选择可比较的标量字段。');return raw;}
function literal(spec,schema,label,root=schema,depth=0){
 need(depth<=12,label+'：参数结构过深，原输入保留。');schema=resolveSchema(schema,root);need(nestedObject(schema),label+'：登记的参数结构暂不能核对。');
 need(nestedObject(spec)&&['value','null'].includes(spec.mode),label+'：请选择明确值或明确空值，空白不代表未提供。');
 const types=Array.isArray(schema.type)?schema.type:[schema.type],kind=types.find(type=>type!=='null');if(spec.mode==='null'){need(types.includes('null'),label+'：登记合同不允许空值。');return null;}
 if(schema.enum){need(/^\d+$/.test(spec.raw)&&Number(spec.raw)<schema.enum.length,label+'：请选择登记选项。');return nestedCopy(schema.enum[Number(spec.raw)]);}
 if(kind==='object'){const result={};for(const[key,child]of Object.entries(schema.properties||{})){if((!spec.fields?.[key]||spec.fields[key].mode==='unset')&&!list(schema.required).includes(key))continue;result[key]=literal(spec.fields?.[key],child,label+' · '+(child.title||key),root,depth+1);}return result;}
 if(kind==='array')return list(spec.items).map((item,index)=>literal(item,schema.items,label+' · 第 '+(index+1)+' 条',root,depth+1));
 return scalar(spec.raw,kind,label);
}
export {literal as nestedLiteral};
export function compileNestedExecution(ctx,value){
 const flow=nestedFlowForm(value,ctx.flow_index);need(flow,'本流程草稿缺失，原内容仍保留。');const sidecar=nestedCausalBindings(ctx,value),bindings={schema:'business-flow-nested-execution-bindings-v195',input_schema:objectSchema(),references:{},predicates:{},guards:guards(),nodes:{},entries:{},conditions:[],loops:[],contract_schemas:[]},entries=list(value.buffers.entries),portRows=value.buffers.ports;
 const role_bindings={},node_actions={},external_node_ids=[];
 const addRef=(key,source,kind,nullable)=>{bindings.references[key]={kind,nullable,...source};return key;};
 entries.forEach((entry,index)=>{if(entry.flow_index!==ctx.flow_index)return;need(entry.label?.trim(),'请为本流程的启动资料填写名称。');const field=entry.field||'entry_'+(index+1);need(/^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(field)&&!Object.hasOwn(bindings.input_schema.properties,field),'启动资料的实际字段重复或不完整。');const shape={...entry.shape,title:entry.label,kind:entry.kind,nullable:entry.nullable,unit:entry.contract.unit||''};bindings.input_schema.properties[field]=nestedSchema(shape,entry.label);bindings.input_schema.required.push(field);bindings.entries['/entry_ports/'+index]=addRef('entry_'+index,{source:'input',field,node_id:null},entry.kind,entry.nullable);});
 for(const action of ctx.action_ports){
  const node=nestedNodeForm(flow,action),actor=flow.role_bindings[action.role];need(actor&&ctx.members.some(member=>member.user_id===actor),action.name+'：请选择真实岗位负责人。');need(!node.actor_id||node.actor_id===actor,action.name+'：步骤负责人和岗位负责人不一致，请明确核对。');role_bindings[action.role]=actor;need(node.review_required!==true,action.name+'：更换能力后请重新核对全部参数和端口。');
  const row={kind:action.actor==='human'?'human':'ability',actor_id:actor,guards:guards(),input_ports:[],output_ports:[]};
  if(action.actor==='human'){
   row.role=action.role;row.output_schema=objectSchema();for(const port of action.output_ports){const spec=node.outputs?.[port.ordinal];need(nestedObject(spec),action.name+'：请配置第 '+(port.ordinal+1)+' 项人工结果。');const field='output_'+(port.ordinal+1);row.output_schema.properties[field]=nestedSchema(spec,action.name+' · '+port.label);row.output_schema.required.push(field);row.output_ports.push({ordinal:port.ordinal,field});}
  }else{
   const cap=eligibleNestedCapabilities(ctx,action).find(cap=>cap.id===node.capability_id);need(cap,action.name+'：请选择当前已发布且适用于此职责的能力。');need(node.capability_hash===cap.hash&&node.release_version===cap.release_version,action.name+'：能力版本已变化，请重新选择和核对。');
   row.execution={capability_id:cap.id,capability_hash:cap.hash,agent_role:ROLES[action.actor],input_bindings:{},arguments:{},input_schema:nestedCopy(cap.input_schema),output_schema:nestedCopy(cap.output_schema),adapter:cap.adapter,release_version:cap.release_version,max_attempts:cap.max_attempts};
   for(const port of action.output_ports){const field=node.outputs?.[port.ordinal];need(typeof field==='string'&&nestedSchemaFields(cap.output_schema).some(([key])=>key===field),action.name+' · 结果 '+(port.ordinal+1)+'：请选择登记的实际输出字段。');row.output_ports.push({ordinal:port.ordinal,field});}
   if(SERVICES[action.actor]){row.service_actor=action.actor;row.operation=ctx.capabilities.find(cap=>cap.id===node.capability_id).operation;}
   need(['observe','state','data','learn','supplement','write'].includes(flow.node_actions[action.graph_node_id]),action.name+'：请明确动作范围。');node_actions[action.graph_node_id]=flow.node_actions[action.graph_node_id];
   if(flow.external_node_ids.includes(action.graph_node_id)){need(cap.external===true,action.name+'：当前能力不属于登记外发节点。');external_node_ids.push(action.graph_node_id);}
  }
  need(new Set(row.output_ports.map(port=>port.field)).size===row.output_ports.length,action.name+'：原数组的不同结果须对应不同实际字段，不能按同名合并。');bindings.nodes[action.path]=row;
 }
 const output=(source,label)=>{const action=ctx.action_ports.find(action=>action.output_ports.some(port=>port.path===source));need(action,label+'：来源不在本流程。');const port=action.output_ports.find(port=>port.path===source),node=bindings.nodes[action.path],field=node.output_ports.find(item=>item.ordinal===port.ordinal)?.field;need(field,label+'：来源结果尚未映射。');return {node_id:action.path,field};};
 for(const action of ctx.action_ports){const row=bindings.nodes[action.path],node=nestedNodeForm(flow,action);
  for(const port of action.input_ports){const info=portRows[port.path],sources=info.sources,key='input_'+ctx.action_ports.indexOf(action)+'_'+port.ordinal;let ref;
   if(sources.length===1&&sources[0].startsWith('/entry_ports/')){const refkey=bindings.entries[sources[0]];need(refkey,action.name+'：启动资料的实际字段尚未配置。');ref=nestedCopy(bindings.references[refkey]);}
   else if(sources.length===1)ref={kind:info.kind,nullable:info.nullable,source:'output',...output(sources[0],action.name)};
   else{need(sources.every(source=>!source.startsWith('/entry_ports/')),action.name+'：启动资料和步骤结果不能混合作为互斥备选。');ref={schema:'exclusive-output-reference-v195',source:'exclusive_output',kind:info.kind,nullable:info.nullable,unit:info.contract.unit,consumer_node_id:action.path,candidates:sources.map(source=>output(source,action.name))};}
   bindings.references[key]=ref;row.input_ports.push({ordinal:port.ordinal,reference:key});
  }
  if(row.kind==='ability')for(const[field,spec]of nestedSchemaFields(row.execution.input_schema)){
   const choice=node.inputs?.[field],label=action.name+' · '+(spec.title||field);if(!choice||choice.mode==='unset'){need(!list(row.execution.input_schema.required).includes(field),label+'：请选择资料来源或填写实际参数。');continue;}
   if(choice.mode==='port'){need(/^\d+$/.test(String(choice.ordinal)),label+'：请选择原始资料项。');const port=row.input_ports.find(port=>port.ordinal===Number(choice.ordinal));need(port,label+'：原资料项不存在。');row.execution.input_bindings[field]=port.reference;}
   else{need(!Object.hasOwn(ctx.capabilities.find(cap=>cap.id===node.capability_id)?.registered_input_bindings||{},field),label+'：登记的固定来源不能改成手填值。');row.execution.arguments[field]=literal(choice.value,spec,label,row.execution.input_schema);}
  }
 }
 function predicate(rule,key,reference,label){need(rule&&['eq','ne','lt','lte','gt','gte'].includes(rule.op),label+'：请选择实际比较方式。');const ref=bindings.references[reference];need(ref&&!['object','array'].includes(ref.kind),label+'：需要实际标量输入或结果。');bindings.predicates[key]={op:rule.op,left:{op:'ref',key:reference},right:{op:'value',kind:ref.kind,value:scalar(rule.raw,ref.kind,label)}};return key;}
 ctx.controls.forEach((control,index)=>{if(control.kind==='parallel'){need(control.wait_mode==='all','此并行阶段要求任一分支先完成；当前执行桥仅支持全部分支汇合，请返回业务流程修订或保留草稿。');return;}const rule=flow.buffers.controls?.[control.path],actionPath=control.path+(control.kind==='decision'?'/decision':'/check'),row=bindings.nodes[actionPath],port=row?.output_ports.find(port=>String(port.ordinal)===String(rule?.output_ordinal));need(port,(at(ctx.source_value,actionPath)?.name||'条件核对')+'：请选择本步骤实际产出的条件结果。');const info=fieldType(outputSchema(row),port.field);need(info,'条件结果字段的类型尚不能核对。');const ref=addRef('condition_result_'+index,{source:'output',node_id:actionPath,field:port.field},info.kind,info.nullable),pid=predicate(rule,'condition_'+index,ref,at(ctx.source_value,actionPath)?.name||'条件核对');if(control.kind==='decision'){need(['0','1'].includes(String(rule.true_branch_index))&&['0','1'].includes(String(rule.false_branch_index))&&String(rule.true_branch_index)!==String(rule.false_branch_index),'请将条件成立和不成立明确对应不同分支。');bindings.conditions.push({path:control.path,predicate_id:pid,true_branch_index:Number(rule.true_branch_index),false_branch_index:Number(rule.false_branch_index)});}else{need(['true','false'].includes(rule.repeat_on)&&/^\d+$/.test(rule.max_iterations)&&Number(rule.max_iterations)>=1&&Number(rule.max_iterations)<=100,'循环需明确何时重复及1至100次的执行上限。');bindings.loops.push({path:control.path,predicate_id:pid,repeat_on:rule.repeat_on,max_iterations:Number(rule.max_iterations)});}});
 Object.keys(NESTED_GUARDS).forEach((guard,index)=>{const rule=flow.buffers.guards?.[guard];if(!rule?.enabled)return;const reference=bindings.entries['/entry_ports/'+rule.entry_index];need(reference,'整个流程的'+NESTED_GUARDS[guard]+'：请选择本流程真实声明的启动资料。');bindings.guards[guard]=predicate(rule,'flow_guard_'+index,reference,'流程'+NESTED_GUARDS[guard]);});
 for(const action of ctx.action_ports){const node=nestedNodeForm(flow,action),row=bindings.nodes[action.path];Object.keys(NESTED_GUARDS).forEach((guard,index)=>{const rule=node.guards?.[guard];if(!rule?.enabled)return;const port=row.input_ports.find(port=>String(port.ordinal)===String(rule.input_ordinal));need(port,action.name+' · '+NESTED_GUARDS[guard]+'：请选择本步骤明确声明的输入。');row.guards[guard]=predicate(rule,'guard_'+ctx.action_ports.indexOf(action)+'_'+index,port.reference,action.name+' · '+NESTED_GUARDS[guard]);});}
 const contracts=new Map();for(const entry of entries.filter(entry=>entry.flow_index===ctx.flow_index))contracts.set(graphWireRequestJSON(entry.contract),entry);for(const action of ctx.action_ports)for(const port of [...action.input_ports,...action.output_ports]){const info=portRows[port.path],key=graphWireRequestJSON(info.contract),old=contracts.get(key);need(!old||old.kind===info.kind&&old.nullable===info.nullable,action.name+'：同一资料含义的类型或空值规则不一致。');contracts.set(key,info);}bindings.contract_schemas=[...contracts.values()].map(info=>({contract:nestedCopy(info.contract),kind:info.kind,nullable:info.nullable}));
 return {execution_bindings:bindings,causal_bindings:sidecar,role_bindings,node_actions,external_node_ids,...compileObservationExtractors(ctx,flow)};
}
export function nestedConfigurationIssues(ctx,value){
 const issues=[],check=operation=>{try{operation();}catch(error){issues.push(error.message);}},flow=nestedFlowForm(value,ctx.flow_index);
 if(flow)check(()=>requireObservationResense(ctx,flow));
 list(value.buffers?.entries).forEach((entry,index)=>check(()=>contract(entry,entry.label||'启动资料 '+(index+1))));
 for(const action of ctx.all_action_ports)for(const port of [...action.input_ports,...action.output_ports]){const label=ctx.source_value.business.flows[action.flow_index].name+' · '+action.name+' · '+(action.input_ports.includes(port)?'资料 ':'结果 ')+(port.ordinal+1);check(()=>contract(value.buffers?.ports?.[port.path],label));if(action.input_ports.includes(port)&&!list(value.buffers?.ports?.[port.path]?.sources).length)issues.push(label+'：尚未选择完整来源。');}
 if(!flow)issues.push('当前流程尚无执行草稿。');else{for(const role of ctx.roles)if(!ctx.members.some(member=>member.user_id===flow.role_bindings[role]))issues.push('岗位“'+role+'”：尚未选择实际负责人。');for(const action of ctx.action_ports){const node=nestedNodeForm(flow,action);if(action.actor!=='human'&&!eligibleNestedCapabilities(ctx,action).some(cap=>cap.id===node.capability_id))issues.push(action.name+'：尚未选择适用的已发布能力。');if(node.review_required)issues.push(action.name+'：变更能力后尚未确认重新核对。');for(const port of action.output_ports){const output=node.outputs?.[port.ordinal];if(action.actor==='human')check(()=>nestedSchema(output,action.name+' · 人工结果 '+(port.ordinal+1)));else if(!output)issues.push(action.name+' · 结果 '+(port.ordinal+1)+'：尚未映射实际输出字段。');}}if(!ctx.allowed_entry_modes.includes(flow.entry_mode))issues.push('尚未明确选择本流程允许的启动方式。');}
 if(!issues.length)check(()=>compileNestedExecution(ctx,value));return [...new Set(issues)];
}
export function selectNestedCapability(flow,action,cap){
 const next=nestedCopy(flow),node=nestedCopy(nestedNodeForm(flow,action));next.buffers.nodes??={};next.buffers.previous_capabilities??=[];if(node.capability_id)next.buffers.previous_capabilities.push({path:action.path,value:nestedCopy(node)});Object.assign(node,{capability_id:cap?.id||'',capability_hash:cap?.hash||'',release_version:cap?.release_version||'',review_required:true});next.buffers.nodes[action.path]=node;next.external_node_ids=next.external_node_ids.filter(id=>id!==action.graph_node_id);next.execution_bindings=null;return next;
}
