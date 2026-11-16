import {emptyRelationScope,buildRelationQuery} from './relation-contract.mjs';
import {buildNativeQuery,emptyQueryCriterion,nativeQueryMetadata,queryTypedValue} from './native-query-contract.mjs';
import {freshNumber,numericValue} from './typed-numbers-v132.mjs';
export const defaultNativeGoal=(purpose='')=>({purpose,fields:[],source:'archive',confirmation:'confirmed',hours:24,allowNew:false,targetField:'',targetOperator:'eq',targetValue:'',toolCriteria:[],querySteps:[],queryCriterion:emptyQueryCriterion(),relationScope:emptyRelationScope(),reason:'',deadline:''});

const primitiveKinds=new Set(['number','integer','string','boolean']);
const comparisonOperators=new Set(['eq','gt','gte','lt','lte']);
const toolSources=new Set(['planning_tool','workflow_step']);
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const nonemptyString=value=>typeof value==='string'&&!!value.trim();
const stringList=value=>Array.isArray(value)&&value.every(nonemptyString);
const metadataError=value=>typeof value==='string'?value:value?'工具清单暂时无法核对':null;

// Keep unavailable or malformed metadata visible; it must never erase a saved draft.
export function nativeGoalToolMetadata(metadata={}){
 if(Array.isArray(metadata))metadata={tools:metadata};
 if(!record(metadata))return {tools:[],error:'工具清单格式无法核对'};
 const envelope=Object.hasOwn(metadata,'goal_tools')?metadata.goal_tools:undefined;
 const error=metadataError(metadata.goal_tools_error)||metadataError(record(envelope)?envelope.error:null)||(!Object.hasOwn(metadata,'fields')?metadataError(metadata.error):null);
 const tools=Object.hasOwn(metadata,'tools')?metadata.tools:envelope===undefined?[]:Array.isArray(envelope)?envelope:record(envelope)?envelope.tools:undefined;
 if(!Array.isArray(tools))return {tools:[],error:error||'工具清单格式无法核对'};
 const ids=new Set();
 for(const tool of tools){
  if(!record(tool)||!nonemptyString(tool.id)||ids.has(tool.id)||!nonemptyString(tool.name)||!nonemptyString(tool.content_hash)
   ||!(nonemptyString(tool.version)||Number.isInteger(tool.version)&&tool.version>0)
   ||!stringList(tool.step_names)||!stringList(tool.sources)||tool.sources.some(source=>!toolSources.has(source))||!Array.isArray(tool.output_fields))return {tools:[],error:error||'工具清单格式无法核对'};
  ids.add(tool.id);const fields=new Set();
  for(const field of tool.output_fields){
   if(!record(field)||!nonemptyString(field.key)||fields.has(field.key)||!nonemptyString(field.label)||!primitiveKinds.has(field.kind)||(field.unit!==null&&typeof field.unit!=='string'))return {tools:[],error:error||'工具输出字段格式无法核对'};
   fields.add(field.key);
  }
 }
 return {tools,error};
}

function toolExpected(value,kind,options={}){
 if(options.typedNumbers&&['number','integer'].includes(kind))return queryTypedValue(value,kind,'期望结果',options);
 if(kind==='integer')return queryTypedValue(value,kind,'期望结果',options);
 if(kind==='string'){
  if(typeof value!=='string'||!value.trim())throw Error('请填写期望内容，不能使用对象或数组');
  return value;
 }
 if(kind==='boolean'){
  if(value===true||value==='true')return true;
  if(value===false||value==='false')return false;
  throw Error('请选择“是”或“否”作为期望结果');
 }
 if(typeof value!=='number'&&(typeof value!=='string'||!/^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/.test(value.trim())))throw Error('请填写有效的有限数值');
 const expected=typeof value==='number'?value:Number(value.trim());
 if(!Number.isFinite(expected)||kind==='integer'&&!Number.isInteger(expected))throw Error(kind==='integer'?'期望结果必须是有限整数':'期望结果必须是有限数值');
 return expected;
}

export function buildToolGoalCriteria(form,metadata,baseCriterionCount=2+(form.targetField?1:0),options={}){
 const rows=Object.hasOwn(form,'toolCriteria')?form.toolCriteria:[];
 if(!Array.isArray(rows))throw Error('原工具条件草稿无法恢复，请保留原记录并核对');
 if(!rows.length)return [];
 if(rows.length+baseCriterionCount>12)throw Error('完成条件合计最多12项，请明确移除多余的工具条件');
 const {tools,error}=nativeGoalToolMetadata(metadata);
 if(error)throw Error('工具清单核对失败，原工具条件已保留：'+error);
 const ids=new Set();
 return rows.map((row,index)=>{
  const prefix='工具条件'+(index+1)+'：';
  if(!record(row)||typeof row.id!=='string'||!row.id||ids.has(row.id))throw Error(prefix+'原草稿无法恢复，请保留原记录并核对');
  ids.add(row.id);
  if(!['capability_success','tool_value'].includes(row.kind))throw Error(prefix+'请选择完成条件类型');
  const tool=tools.find(item=>item.id===row.capabilityId);
  if(!tool)throw Error(prefix+'所选工具已不属于当前业务可用清单，请重新核对');
  const criterion={id:'tool_'+(index+1),kind:row.kind,capability_id:tool.id,description:tool.name+'需成功完成并核对真实结果'};
  if(row.kind==='capability_success')return criterion;
  const field=tool.output_fields.find(item=>item.key===row.field);
  if(!field)throw Error(prefix+'请选择当前工具登记的单值输出字段');
  if(!comparisonOperators.has(row.operator))throw Error(prefix+'请选择有效的比较方式');
  if(!['number','integer'].includes(field.kind)&&row.operator!=='eq')throw Error(prefix+'文本和是否结果只支持等于标准');
  let expected;try{expected=toolExpected(row.expected,field.kind,options);}catch(e){throw Error(prefix+e.message);}
  return {...criterion,description:tool.name+'的'+(field.label||field.key)+'应符合已确认标准',field:field.key,operator:row.operator,expected};
 });
}

export function chinaDeadline(value){
 if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))throw Error('请填写完整的北京时间截止日期和分钟');
 const date=new Date(value+':00+08:00');
 if(!Number.isFinite(date.getTime())||new Date(date.getTime()+8*3600*1000).toISOString().slice(0,16)!==value)throw Error('截止日期或时间无效');
 return date.toISOString();
}

export function buildNativeGoal(form,metadata,options={}){
 if(!form.purpose?.trim()||!Array.isArray(form.fields)||!form.fields.length)throw Error('请选择原业务字段并填写目标用途');
 if(!metadata.entity_ids?.length)throw Error('请先选择任务对象');
 if(form.fields.some(field=>!Object.hasOwn(metadata.fields,field)))throw Error('目标字段已不属于所选对象，请重新核对字段');
 const criteria=[{id:'enough',kind:'evidence_sufficient',description:'原目标要求的资料充分且无冲突'},{id:'human',kind:'human_confirmation',description:'原业务步骤结束后明确验收目标'}];
 if(form.targetField){
  if(!form.fields.includes(form.targetField))throw Error('完成标准必须属于目标所需字段');
  const type=metadata.field_kinds[form.targetField];
  if(!comparisonOperators.has(form.targetOperator))throw Error('请选择有效的字段比较方式');
  if(!['number','integer'].includes(type)&&form.targetOperator!=='eq')throw Error('此业务字段只支持等于标准');
  const expected=queryTypedValue(form.targetValue,type,'完成标准',options);
  criteria.push({id:'value',kind:'field_value',description:(metadata.fields[form.targetField]||'登记值')+'应符合已确认标准',field:form.targetField,operator:form.targetOperator,expected});
 }
 const query=buildNativeQuery(form,metadata,options),relation=buildRelationQuery(form,metadata);
 if(query.criterion)criteria.push(query.criterion);
 criteria.push(...buildToolGoalCriteria(form,metadata,criteria.length,options));
 if(criteria.length>12)throw Error('完成条件合计最多12项');
 let hours=24;if(form.source!=='archive'){try{hours=numericValue(freshNumber(form.hours,'integer','资料有效小时数'));}catch{throw Error('请填写1至720之间的整数小时');}if(hours<1||hours>720)throw Error('请填写1至720之间的整数小时');}
 return {purpose:form.purpose.trim(),entity_ids:metadata.entity_ids,required_fields:form.fields,
  subgoals:[{id:'original',description:'核对原业务目标所需资料并完成原流程',criterion_ids:criteria.map(c=>c.id)}],criteria,
  evidence:{source:form.source,minimum_verification:form.source==='archive'?'recorded':form.confirmation,time:form.source==='archive'?{mode:'archive',timezone:'Asia/Shanghai'}:{mode:'latest',max_age_seconds:hours*3600,timezone:'Asia/Shanghai'}},
  observation_mode:form.source==='archive'||!form.allowNew?'existing_only':'allow_new',max_steps:12,max_tool_calls:12,stop_on_conflict:true,deadline:form.deadline?chinaDeadline(form.deadline):null,...(query.dataQuery?{data_query:query.dataQuery}:{}),...(relation?{relation_query:relation}:{})};
}

export function taskGoalMetadata(catalog,objects,options={}){
 const fields={},field_kinds={},field_units={};
 for(const obj of objects){const type=catalog.find(t=>t.key===obj.entity_type);for(const field of type?.fields||[]){fields[field.key]=field.label;field_kinds[field.key]=({'数值':'number','数字':'number','整数':'integer','是否':'boolean','布尔':'boolean'}[field.kind]||'string');field_units[field.key]=typeof field.unit==='string'?field.unit:null;}}
 const {tools,error}=nativeGoalToolMetadata(options);
 const query=nativeQueryMetadata(record(options)?options:{});
 return {entity_ids:objects.map(obj=>obj.id),fields,field_kinds,field_units,tools,goal_tools_error:error,query_tools:query.tools,query_tools_error:query.error,...(record(options)?{package_id:options.package_id,relation_types:options.relation_types,relation_types_error:options.relation_types_error,entities:options.entities,entities_truncated:options.entities_truncated}:{} )};
}
