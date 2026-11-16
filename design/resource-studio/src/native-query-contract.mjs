import {freshNumber,isTypedNumber,numericToken,numericValue} from './typed-numbers-v132.mjs';

const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const text=value=>typeof value==='string'&&!!value.trim();
const kinds=new Set(['number','integer','string','boolean']);
const numeric=kind=>['number','integer'].includes(kind);
const methods={keyword:['field','text'],aggregate:['field','operation'],fulltext:['field','text','top_k','min_score','match_mode']};
const rowID=/^[a-z][a-z0-9_]{0,39}$/;
export const queryOperationLabels={mean:'平均值',sum:'合计',min:'最小值',max:'最大值',count:'证据记录数',all_terms:'包含全部关键词',any_terms:'包含任一关键词'};
export const queryMethodLabels={keyword:'按文字筛选',fulltext:'按内容查找并排序',aggregate:'计算登记值'};
export const emptyQueryCriterion=()=>({enabled:false,field:'',operator:'eq',expected:''});

export function nativeQueryMetadata(metadata={}){
 if(!record(metadata))return {tools:[],error:'本机查询清单格式无法核对'};
 const error=metadata.query_tools_error?typeof metadata.query_tools_error==='string'?metadata.query_tools_error:'本机查询清单暂时无法核对':null;
 const tools=Object.hasOwn(metadata,'query_tools')?metadata.query_tools:[];
 if(!Array.isArray(tools))return {tools:[],error:error||'本机查询清单格式无法核对'};
 const ids=new Set();
 for(const tool of tools){
  if(!record(tool)||!text(tool.id)||ids.has(tool.id)||!text(tool.name)||!text(tool.content_hash)||!(text(tool.version)||Number.isInteger(tool.version)&&tool.version>0)||!Object.hasOwn(methods,tool.method)||tool.external!==false||!record(tool.parameters_schema)||tool.parameters_schema.type!=='object'||!record(tool.parameters_schema.properties)||!Array.isArray(tool.parameters_schema.required)||!Array.isArray(tool.output_fields))return {tools:[],error:error||'本机查询清单格式无法核对'};
  ids.add(tool.id);const schema=tool.parameters_schema,allowed=methods[tool.method],keys=Object.keys(schema.properties);
  if(keys.some(key=>!allowed.includes(key))||allowed.some(key=>!keys.includes(key)||!schema.required.includes(key))||schema.required.some(key=>!keys.includes(key))||new Set(schema.required).size!==schema.required.length)return {tools:[],error:error||'本机查询参数目录无法核对'};
  for(const value of Object.values(schema.properties)){
   if(!record(value)||!kinds.has(value.type))return {tools:[],error:error||'本机查询参数类型无法核对'};
   const badEnum=value.enum!==undefined&&(!Array.isArray(value.enum)||!value.enum.length||value.enum.some(item=>item===null||typeof item!==(numeric(value.type)?'number':value.type)||typeof item==='number'&&(!Number.isFinite(item)||value.type==='integer'&&!Number.isInteger(item))));
   if(badEnum||['minimum','maximum','minLength','maxLength'].some(key=>Object.hasOwn(value,key)&&!Number.isFinite(value[key]))||value.minimum!==undefined&&value.maximum!==undefined&&value.minimum>value.maximum)return {tools:[],error:error||'本机查询参数类型无法核对'};
  }
  if(schema.properties.field.type!=='string'||tool.method==='aggregate'&&(schema.properties.operation.type!=='string'||!Array.isArray(schema.properties.operation.enum)||schema.properties.operation.enum.some(value=>!['mean','sum','min','max','count'].includes(value)))||tool.method!=='aggregate'&&(schema.properties.text.type!=='string'||tool.preserves_input_fields!==undefined&&tool.preserves_input_fields!==true)||tool.method==='fulltext'&&(schema.properties.top_k.type!=='integer'||schema.properties.min_score.type!=='number'||schema.properties.match_mode.type!=='string'||!Array.isArray(schema.properties.match_mode.enum)||schema.properties.match_mode.enum.some(value=>!['all_terms','any_terms'].includes(value))))return {tools:[],error:error||'本机查询方式不符合已实现契约'};
  if(tool.method==='fulltext'&&(!Number.isInteger(schema.properties.top_k.minimum)||!Number.isInteger(schema.properties.top_k.maximum)||schema.properties.top_k.minimum<1||schema.properties.top_k.maximum>20||!Number.isFinite(schema.properties.min_score.minimum)||!Number.isFinite(schema.properties.min_score.maximum)||schema.properties.min_score.minimum<0||schema.properties.min_score.maximum>50))return {tools:[],error:error||'全文检索范围尚未有界登记'};
  const fields=new Set();for(const field of tool.output_fields){if(!record(field)||!text(field.key)||fields.has(field.key)||!text(field.label)||!kinds.has(field.kind)||(field.unit!==null&&typeof field.unit!=='string'))return {tools:[],error:error||'查询输出字段格式无法核对'};fields.add(field.key);}
 }
 return {tools,error};
}

export function queryTypedValue(value,kind,label='期望结果',options={}){
 if(kind==='string'){if(typeof value!=='string'||!value.trim())throw Error(label+'需填写有效文字');return value;}
 if(kind==='boolean'){if(value===true||value==='true')return true;if(value===false||value==='false')return false;throw Error(label+'请选择是或否');}
 if(options.typedNumbers&&numeric(kind))return freshNumber(isTypedNumber(value)?numericToken(value):value,kind,label);
 if(kind==='integer'){try{return numericValue(freshNumber(isTypedNumber(value)?numericToken(value):value,'integer',label));}catch{throw Error(label+'必须是有限整数，且可准确保存；请填写完整原数值');}}
 if(!numeric(kind)||typeof value!=='number'&&(typeof value!=='string'||!/^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/.test(value.trim())))throw Error(label+'需填写有限数值');
 const result=typeof value==='number'?value:Number(value.trim());if(!Number.isFinite(result)||kind==='integer'&&!Number.isInteger(result))throw Error(label+(kind==='integer'?'必须是有限整数':'必须是有限数值'));return result;
}

export function queryInputFields(form,metadata,index,tools=nativeQueryMetadata(metadata).tools){
 const base=Object.fromEntries((Array.isArray(form.fields)?form.fields:[]).filter(key=>Object.hasOwn(metadata.fields||{},key)).map(key=>[key,{key,label:metadata.fields[key],kind:metadata.field_kinds?.[key]||'string',unit:metadata.field_units?.[key]||null}]));
 const steps=Array.isArray(form.querySteps)?form.querySteps:[],frames={evidence:base};
 for(let i=0;i<index;i++){const row=steps[i];if(!record(row)||!text(row.id))continue;const tool=tools.find(item=>item.id===row.capabilityId),input=frames[row.inputFrom];frames[row.id]=tool&&input?tool.method==='aggregate'?Object.fromEntries(tool.output_fields.map(field=>[field.key,field])):{...input}:{};}
 return frames[steps[index]?.inputFrom]||{};
}

export function queryOutputFields(form,metadata,index,tools=nativeQueryMetadata(metadata).tools){
 const row=Array.isArray(form.querySteps)?form.querySteps[index]:null,tool=tools.find(item=>item.id===row?.capabilityId);
 return tool?tool.method==='aggregate'?tool.output_fields:Object.values(queryInputFields(form,metadata,index,tools)):[];
}

export function buildNativeQuery(form,metadata,options={}){
 const rows=Object.hasOwn(form,'querySteps')?form.querySteps:[];
 const condition=Object.hasOwn(form,'queryCriterion')?form.queryCriterion:emptyQueryCriterion();
 if(!Array.isArray(rows)||!record(condition)||typeof condition.enabled!=='boolean')throw Error('原查询草稿无法恢复，请保留并核对');
 if(!rows.length){if(condition.enabled)throw Error('派生结果标准需要先设置查询步骤');return {dataQuery:null,criterion:null};}
 if(rows.length>6)throw Error('原目标查询最多6步，受原任务12步及累计调用预算约束');
 const {tools,error}=nativeQueryMetadata(metadata);if(error)throw Error('查询清单核对失败，原输入保留：'+error);
 const seen=new Set(['evidence']);
 const steps=rows.map((row,index)=>{
  const prefix='查询步骤'+(index+1)+'：';if(!record(row)||!rowID.test(row.id)||seen.has(row.id)||!seen.has(row.inputFrom)||!record(row.parameters))throw Error(prefix+'原步骤或前序依赖无法核对，请保留原稿');
  const tool=tools.find(item=>item.id===row.capabilityId);if(!tool)throw Error(prefix+'原选查询方式不属于当前业务版本，请重新核对');
  const schema=tool.parameters_schema,parameters={};if(Object.keys(row.parameters).some(key=>!Object.hasOwn(schema.properties,key)))throw Error(prefix+'保留的参数不属于当前查询方式，请明确重置后核对');
  for(const [key,definition] of Object.entries(schema.properties)){
   const value=row.parameters[key];if(value===undefined||value===''){if(schema.required.includes(key))throw Error(prefix+(definition.title||'查询参数')+'尚未填写');continue;}
   const result=queryTypedValue(value,definition.type,definition.title||'查询参数',options),comparable=isTypedNumber(result)?numericValue(result):result;
   if(definition.enum&&!definition.enum.includes(comparable)||typeof comparable==='number'&&(definition.minimum!==undefined&&comparable<definition.minimum||definition.maximum!==undefined&&comparable>definition.maximum)||typeof comparable==='string'&&(definition.minLength!==undefined&&comparable.length<definition.minLength||definition.maxLength!==undefined&&comparable.length>definition.maxLength))throw Error(prefix+(definition.title||'查询参数')+'不符合登记范围');parameters[key]=result;
  }
  const field=queryInputFields(form,metadata,index,tools)[parameters.field];if(!field)throw Error(prefix+'字段不属于已选来源或当前输入步骤');
  if(tool.method==='aggregate'?parameters.operation!=='count'&&!numeric(field.kind):field.kind!=='string')throw Error(prefix+(tool.method==='aggregate'?'此计算方式只支持有限数值字段':'文字筛选和全文检索只支持文本字段'));
  seen.add(row.id);return {id:row.id,capability_id:tool.id,input_from:row.inputFrom,parameters};
 });
 let criterion=null;
 if(condition.enabled){const final=tools.find(item=>item.id===rows.at(-1).capabilityId),field=queryOutputFields(form,metadata,rows.length-1,tools).find(item=>item.key===condition.field);if(!field)throw Error('请选择最后一步登记的派生输出字段');
  if(!['eq','gt','gte','lt','lte'].includes(condition.operator)||!numeric(field.kind)&&condition.operator!=='eq')throw Error('派生输出比较方式不符合字段类型，文字和是否仅支持等于');
  criterion={id:'query_result',kind:'query_value',description:final.name+'的'+field.label+'应符合已确认标准',field:field.key,operator:condition.operator,expected:queryTypedValue(condition.expected,field.kind,'期望结果',options)};
 }
 return {dataQuery:{tool_steps:steps,max_tool_seconds:60,allow_external_tools:false},criterion};
}

