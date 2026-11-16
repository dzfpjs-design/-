// Pure authoring helpers. Options, typed data and hashes grant no authority.
import {graphWireClone,graphWireSetNumberToken,graphWireRestoreNumericTokens,graphWireTypedCopy,graphWireRequestJSON} from './full-semantic-graph-wire.mjs';
import {freshNumber,numericValue,numericToken} from './typed-numbers-v132.mjs';
import {resolveSchema,schemaKind,supportedServerInput,SERVER_INPUTS} from './full-semantic-graph-editor-model.mjs';

export const BINDINGS_SCHEMA='business-flow-execution-bindings-v1';
export const EVENT_BINDINGS_SCHEMA='business-flow-execution-bindings-v2';
export const ANOMALY_TRIGGER_MODE='signal_graph_anomaly_v134';
export const TYPES={string:'文字',integer:'整数',number:'数值',boolean:'是否',date:'日期',object:'一组字段',array:'多条记录'};
export const SOURCE_ROLES={business_agent:'business',perception_agent:'perception',data_agent:'data'};
export const AGENT_LABELS={business:'业务智能体',perception:'多模感知智能体',data:'数据管理智能体'};
export const GUARDS={start:'开始条件',precondition:'前置条件',stop:'停止条件',exception:'例外条件'};
export const copy=graphWireClone;
export const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
export const array=v=>Array.isArray(v)?v:[];
const own=(v,k)=>Object.prototype.hasOwnProperty.call(v||{},k);
const requireValue=(v,message)=>{if(!v)throw Error(message);};
export const technicalId=kind=>kind+'_'+crypto.randomUUID().replaceAll('-','');
export const emptyObjectSchema=()=>({type:'object',properties:{},required:[],additionalProperties:false});
export const emptyGuards=()=>({start:null,precondition:null,stop:null,exception:null});
export function selectedFlow(sourceValue,flowId){return array(sourceValue?.business?.flows).find(flow=>flow?.flow_id===flowId)||null;}
export function newBindings(flow){return {schema:flow?.trigger?.kind==='event'?EVENT_BINDINGS_SCHEMA:BINDINGS_SCHEMA,input_schema:emptyObjectSchema(),references:{},predicates:{},guards:emptyGuards(),nodes:Object.fromEntries(array(flow?.nodes).map(node=>[node.node_id,null])),conditions:[],parallel_groups:[],loops:[],goals:[],...(flow?.trigger?.kind==='event'?{trigger:{mode:''}}:{})};}
export function selectAnomalyTrigger(value,flow,mode){requireValue(flow?.trigger?.kind==='event'&&object(value)&&['',ANOMALY_TRIGGER_MODE].includes(mode),'请对照原事件流程，明确选择已支持的异常信号启动方式。');return {...copy(value),schema:EVENT_BINDINGS_SCHEMA,trigger:{mode}};}
export function cloneBindings(value,buffers={}){
 const next=copy(value);if(object(next)&&object(buffers.number_tokens))graphWireRestoreNumericTokens(next,buffers.number_tokens,{matchingOnly:true});return next;
}
// A plain draft plus separate numeric buffers is the durable form. The old
// wire encoder deliberately requires typed atoms at its request boundary.
// Materialize those only when preparing an exact save/preview request.
export function bindingWireValue(value,buffers={}){return graphWireTypedCopy(cloneBindings(value,buffers));}
export function bindingRequestJSON(value,buffers={}){return graphWireRequestJSON(bindingWireValue(value,buffers));}
export function setAt(value,path,item){
 requireValue(Array.isArray(path)&&path.length>0&&path.every(key=>typeof key==='string'||Number.isSafeInteger(key)&&key>=0),'字段路径不可用');
 const next=copy(value);let parent=next;
 const write=(target,key,val)=>Object.defineProperty(target,key,{value:val,enumerable:true,writable:true,configurable:true});
 for(let i=0;i<path.length-1;i++){
  const key=path[i];requireValue(object(parent)||Array.isArray(parent),'字段容器不可用');
  if(!own(parent,key)||!object(parent[key])&&!Array.isArray(parent[key]))write(parent,key,typeof path[i+1]==='number'?[]:{});
  parent=parent[key];
 }
 const key=path.at(-1);requireValue(object(parent)||Array.isArray(parent),'字段容器不可用');write(parent,key,copy(item));
 if(typeof item==='number')graphWireSetNumberToken(parent,key,Object.is(item,-0)?'-0.0':String(item));return next;
}
export function clearNumberEdits(buffers,path){
 const ui=copy(buffers),prefix='/'+path.map(key=>String(key).replaceAll('~','~0').replaceAll('/','~1')).join('/');
 for(const kind of ['number_texts','number_tokens'])if(object(ui[kind]))for(const[key,entry]of Object.entries(ui[kind]))if(key===prefix||key.startsWith(prefix+'/')){ui.retained_number_edits??=[];ui.retained_number_edits.push({kind,path:key,value:copy(entry)});delete ui[kind][key];}return ui;
}
export function patchBindings(value,buffers,path,item){
 const ui=clearNumberEdits(buffers,path);let before=value;for(const key of path)before=before?.[key];
 if(object(before)&&object(item)&&before.op&&item.op&&before.op!==item.op){ui.previous_expressions??={};const key='/'+path.join('/');ui.previous_expressions[key]=[...array(ui.previous_expressions[key]),copy(before)];}
 return {value:setAt(value,path,item),buffers:ui};
}
export function removeBindingArrayItem(value,buffers,path,index){
 let current=value;for(const key of path)current=current?.[key];requireValue(Array.isArray(current)&&Number.isSafeInteger(index)&&index>=0&&index<current.length,'原条目已经变化，未删除');
 const next=setAt(value,path,current.filter((_,at)=>at!==index)),ui=copy(buffers),prefix='/'+path.map(key=>String(key).replaceAll('~','~0').replaceAll('/','~1')).join('/')+'/';
 ui.removed_items=[...array(ui.removed_items),{path:copy(path),index,item:copy(current[index])}];
 for(const kind of ['number_texts','number_tokens'])if(object(ui[kind])){const result={};for(const[key,entry]of Object.entries(ui[kind])){if(!key.startsWith(prefix)){result[key]=entry;continue;}const rest=key.slice(prefix.length),part=rest.split('/')[0],at=Number(part);if(!/^\d+$/.test(part)){result[key]=entry;continue;}if(at===index){ui.retained_number_edits??=[];ui.retained_number_edits.push({kind,path:key,value:copy(entry)});}else result[prefix+(at>index?at-1:at)+rest.slice(part.length)]=entry;}ui[kind]=result;}return {value:next,buffers:ui};
}
export function fieldType(schema,field){const root=resolveSchema(schema,schema),spec=resolveSchema(root?.properties?.[field],schema),kind=schemaKind(spec,schema);return kind?{kind:kind.kind,nullable:kind.nullable===true,spec}:null;}
export function outputSchema(binding){return binding?.kind==='ability'?binding.execution?.output_schema:binding?.kind==='human'?binding.output_schema:null;}
export function referenceLabel(key,value,flow){const ref=value?.references?.[key];if(!ref)return '原引用当前不存在';if(ref.source==='input'){const spec=value.input_schema?.properties?.[ref.field];return (spec?.title||'业务输入')+' · '+(TYPES[ref.kind]||'待核类型');}const node=array(flow?.nodes).find(node=>node.node_id===ref.node_id),spec=outputSchema(value.nodes?.[ref.node_id])?.properties?.[ref.field];return (node?.name||'原步骤当前不存在')+' · '+(spec?.title||'处理结果')+' · '+(TYPES[ref.kind]||'待核类型');}
export function ensureNode(value,node){
 const next=copy(value);if(object(next.nodes?.[node.node_id]))return next;
 requireValue(node.actor==='human'||own(SOURCE_ROLES,node.actor),'此职责尚无实际运行适配，不能改成其他职责来执行');next.nodes??={};
 const ports={input_ports:Object.fromEntries(array(node.inputs).map(label=>[label,''])),output_ports:Object.fromEntries(array(node.outputs).map(label=>[label,''])),guards:emptyGuards()};
 next.nodes[node.node_id]=node.actor==='human'?{kind:'human',actor_id:'',role:node.role,output_schema:emptyObjectSchema(),...ports}:{kind:'ability',actor_id:'',execution:{capability_id:'',capability_hash:'',agent_role:SOURCE_ROLES[node.actor],input_bindings:{},arguments:{},input_schema:emptyObjectSchema(),output_schema:emptyObjectSchema(),adapter:'',release_version:'',max_attempts:null},...ports};return next;
}
export function eligibleAbilities(abilities,node){const role=SOURCE_ROLES[node?.actor];return array(abilities).filter(cap=>role&&(!cap.status||cap.status==='published')&&array(cap.agents).some(agent=>agent===role||agent===AGENT_LABELS[role]));}
export function selectPublishedAbility(value,buffers,node,cap){
 requireValue(eligibleAbilities([cap],node).length===1&&typeof cap.id==='string'&&/^[a-f0-9]{64}$/.test(cap.content_hash||'')&&typeof cap.release_version==='string'&&object(cap.input_schema)&&object(cap.output_schema),'请使用当前登记且合同完整的已发布能力');
 const next=ensureNode(value,node),ui=copy(buffers),binding=next.nodes[node.node_id],old=copy(binding.execution),attempts=cap.max_attempts??cap.budget?.max_attempts;
 requireValue(Number.isSafeInteger(attempts)&&attempts>=1&&attempts<=5,'登记能力的重试上限不可用');
 ui.previous_executions??={};ui.previous_executions[node.node_id]??=[];if(old.capability_id)ui.previous_executions[node.node_id].push(old);
 binding.execution={...old,capability_id:cap.id,capability_hash:cap.content_hash,agent_role:SOURCE_ROLES[node.actor],input_bindings:copy(old.input_bindings||{}),arguments:copy(old.arguments||{}),input_schema:copy(cap.input_schema),output_schema:copy(cap.output_schema),adapter:cap.adapter,release_version:cap.release_version,max_attempts:attempts};
 ui.node_reviews={...ui.node_reviews,[node.node_id]:{required:true,reason:'能力已变化，原参数、输入来源和产出对应保留，需重新核对。'}};
 ui.clear_external_node_ids=[...new Set([...array(ui.clear_external_node_ids),node.node_id])];return {value:next,buffers:ui};
}
export function addReference(value,{source,field,node_id=null},id=technicalId('ref')){
 const next=copy(value),schema=source==='input'?next.input_schema:outputSchema(next.nodes?.[node_id]),kind=fieldType(schema,field);
 requireValue(['input','output'].includes(source)&&kind,'该输入或真实结果字段类型尚不能核对');
 const existing=Object.entries(next.references||{}).find(([,ref])=>ref.source===source&&ref.field===field&&ref.node_id===(source==='input'?null:node_id)&&ref.kind===kind.kind&&ref.nullable===kind.nullable);
 if(existing)return {value:next,key:existing[0]};next.references??={};next.references[id]={kind:kind.kind,nullable:kind.nullable,source,field,node_id:source==='input'?null:node_id};return {value:next,key:id};
}
export function addBusinessInput(value,label,kind,{field=technicalId('input'),ref=technicalId('ref')}={}){
 requireValue(typeof label==='string'&&label.trim()&&own(TYPES,kind),'请明确填写输入名称和类型');const next=copy(value),schema=kind==='date'?{type:'string',format:'date',title:label}:kind==='array'?{type:'array',items:{type:'string'},title:label}:kind==='object'?{type:'object',properties:{},additionalProperties:false,title:label}:{type:kind,title:label};
 requireValue(!['object','array'].includes(kind),'组合输入需由已登记的结构合同提供，不能猜测其子字段');next.input_schema??=emptyObjectSchema();next.input_schema.properties??={};next.input_schema.required??=[];next.input_schema.properties[field]=schema;next.input_schema.required.push(field);return addReference(next,{source:'input',field},ref);
}
export function addHumanOutput(value,node,label,kind,field=technicalId('result')){
 requireValue(node.actor==='human'&&array(node.outputs).includes(label)&&own(TYPES,kind)&&!['object','array'].includes(kind),'请选定原人工产出并明确实际支持的字段类型');const next=ensureNode(value,node),binding=next.nodes[node.node_id];
 const spec=kind==='date'?{type:'string',format:'date',title:label}:{type:kind,title:label};binding.output_schema.properties[field]=spec;binding.output_schema.required.push(field);binding.output_ports[label]=field;return next;
}
export function adoptFixedInput(value,node,cap,field){
 const fixed=cap.fixed_bindings?.[field],spec=cap.input_schema?.properties?.[field];requireValue(fixed&&supportedServerInput(fixed)&&object(spec)&&!JSON.stringify(spec).includes('"$ref"'),'此固定输入尚不能由当前表单连接，不能用常量替代');
 const next=copy(value),existing=next.input_schema.properties[fixed];requireValue(!existing||JSON.stringify(existing)===JSON.stringify(spec),'当前同名任务来源合同不同，原输入保留');next.input_schema.properties[fixed]=copy(spec);if(!next.input_schema.required.includes(fixed))next.input_schema.required.push(fixed);
 const result=addReference(next,{source:'input',field:fixed});result.value.nodes[node.node_id].execution.input_bindings[field]=result.key;delete result.value.nodes[node.node_id].execution.arguments[field];return result.value;
}
export function setCapabilityInput(value,nodeId,field,mode,item){
 const next=copy(value),execution=next.nodes[nodeId].execution;delete execution.arguments[field];delete execution.input_bindings[field];
 if(mode==='reference')execution.input_bindings[field]=item;else if(mode==='literal'){execution.arguments[field]=copy(item);if(typeof item==='number')graphWireSetNumberToken(execution.arguments,field,Object.is(item,-0)?'-0.0':String(item));}else requireValue(mode==='unset','输入方式不可用');return next;
}
export function numericEdit(value,buffers,path,raw,kind='number'){
 const ui=copy(buffers),key='/'+path.map(k=>String(k).replaceAll('~','~0').replaceAll('/','~1')).join('/');ui.number_texts={...ui.number_texts,[key]:{text:raw,kind}};let next=setAt(value,path,null);
 try{const atom=freshNumber(raw,kind);let parent=next;for(const part of path.slice(0,-1))parent=parent[part];const leaf=path.at(-1);parent[leaf]=numericValue(atom);graphWireSetNumberToken(parent,leaf,numericToken(atom));ui.number_tokens={...ui.number_tokens,[key]:numericToken(atom)};delete ui.number_texts[key].error;}
 catch(error){ui.number_texts[key].error=error.message;ui.number_tokens={...ui.number_tokens};delete ui.number_tokens[key];}return {value:next,buffers:ui};
}
export const fixedInputLabel=key=>SERVER_INPUTS[key]||'登记合同的固定来源';
