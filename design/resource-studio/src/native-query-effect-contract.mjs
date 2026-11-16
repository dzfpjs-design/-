import {queryGroundingState} from './native-query-grounding-contract.mjs';
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const canonical=value=>Array.isArray(value)?value.map(canonical):object(value)?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const equal=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
const protocolVersion=row=>row?.wire_contract_version===undefined?1:row?.wire_contract_version;
const field=value=>object(value)&&typeof value.key==='string'&&typeof value.label==='string'&&(value.type===null||typeof value.type==='string')&&(value.unit===null||typeof value.unit==='string');
const normalizedField=value=>({key:value.key,label:Object.hasOwn(value,'label')?value.label:value.key,type:value.type??null,unit:value.unit??null});
const limits=value=>Array.isArray(value)&&value.every(item=>object(item)&&typeof item.step_id==='string'&&item.method==='fulltext'&&object(item.parameters)&&typeof item.parameters.field==='string'&&Number.isInteger(item.parameters.top_k)&&item.parameters.top_k>0&&item.parameters.top_k<=20&&Number.isFinite(item.parameters.min_score)&&['all_terms','any_terms'].includes(item.parameters.match_mode));
function frame(value){return object(value)&&typeof value.id==='string'&&['records','aggregate_record','unknown'].includes(value.shape)&&['evidence_records','aggregate_rows','unknown'].includes(value.record_unit)&&['zero_or_more','one_on_success','unknown'].includes(value.cardinality)&&Array.isArray(value.fields)&&value.fields.every(field)&&[true,false,null].includes(value.ranked_subset)&&limits(value.subset_limits);}
const sourceFrame=fields=>({id:'evidence',shape:'records',record_unit:'evidence_records',cardinality:'zero_or_more',fields,ranked_subset:false,subset_limits:[]});
const invalid={legacy:false,effect:null,valid:false,reviewAllowed:false};

// This checks the explanation against the saved plan; only the server authorizes actions.
function derivedFramesMatch(row,effect){
 const declared=new Map((row.catalog?.fields||[]).map(item=>[item.key,item]));
 if(!row.candidate.fields.every(key=>declared.has(key)))return false;
 const selected=row.candidate.fields.map(key=>normalizedField(declared.get(key)));
 if(!equal(effect.selected_fields,selected)||effect.unknown_reasons.length)return false;
 const producers=new Map([['evidence',sourceFrame(selected)]]);
 for(let index=0;index<row.candidate.tool_steps.length;index++){
  const actual=row.candidate.tool_steps[index],current=effect.steps[index];
  const tool=row.catalog.query_tools?.find(item=>item.id===actual.capability_id);
  const input=producers.get(actual.input_from),parameters=actual.parameters;
  const prop=input?.fields.find(item=>item.key===parameters.field);
  if(!tool||!input||!prop||!equal(current.input,input)||producers.has(actual.id))return false;
  let output={...input,id:actual.id},denominator=null;
  if(['keyword','fulltext'].includes(tool.method)){
   output={...output,cardinality:'zero_or_more'};
   if(tool.method==='fulltext')output={...output,ranked_subset:true,subset_limits:[...input.subset_limits,{step_id:actual.id,method:'fulltext',parameters:Object.fromEntries(['field','top_k','min_score','match_mode'].map(key=>[key,parameters[key]]))}]};
  }else if(tool.method==='aggregate'&&['count','sum','mean','min','max'].includes(parameters.operation)){
   const registered=tool.output_fields;
   if(!Array.isArray(registered)||registered.length!==4||!registered.every(object)||!equal(Object.fromEntries(registered.map(item=>[item.key,item.type])),{value:'number',sample_count:'integer',field:'string',operation:'string'}))return false;
   const fields=registered.map(item=>{const field=normalizedField(item);return field.key==='value'?{...field,unit:parameters.operation==='count'?'条证据记录':prop.unit}:field;});
   output={id:actual.id,shape:'aggregate_record',record_unit:'aggregate_rows',cardinality:'one_on_success',fields,ranked_subset:input.ranked_subset,subset_limits:input.subset_limits};
   denominator={frame_id:input.id,record_unit:input.record_unit,ranked_subset:input.ranked_subset,subset_limits:input.subset_limits,on_success:'all_input_rows',empty_input:'error_no_statistic',value_requirement:parameters.operation==='count'?'present_non_null_finite':'finite_number_not_boolean',missing_values:'error_no_silent_skip'};
  }else return false;
  if(!equal(current.output,output)||!equal(current.denominator,denominator))return false;
  producers.set(actual.id,output);
 }
 const last=row.candidate.tool_steps.at(-1)?.id||'evidence';
 return equal(effect.final,producers.get(last));
}

export function queryEffectState(row){
 const version=protocolVersion(row),effect=row?.query_effect;
 if(Number.isInteger(version)&&version>=1&&version<5)return effect==null?{legacy:true,effect:null,valid:true,reviewAllowed:true}:invalid;
 if(![5,6].includes(version)||typeof row.original_question!=='string'||!row.original_question.trim()||!hash(row.question_hash)||!hash(row.candidate_hash)||!object(effect)||effect.schema!=='native-query-effect-v1'||!['derived','unknown','not_applicable'].includes(effect.status)||effect.semantic_equivalence_verified!==false||!Array.isArray(effect.selected_fields)||!effect.selected_fields.every(field)||!object(effect.source_scope)||!['selected_task_entities','relation_result_set'].includes(effect.source_scope.kind)||effect.source_scope.resolved_entities!==false||!object(effect.provenance)||!equal(effect.provenance.evidence_metadata,['entity_id','version','observation_id','source'])||!equal(effect.provenance.tool_record_metadata,['entity_ids','evidence_refs'])||effect.provenance.requires_business_name_field!==false||!Array.isArray(effect.steps)||!Array.isArray(effect.unknown_reasons)||!effect.unknown_reasons.every(item=>typeof item==='string')||!Array.isArray(effect.limitations)||!effect.limitations.every(item=>typeof item==='string'))return invalid;
 if(version===6&&!queryGroundingState(row).valid)return invalid;
 if(effect.status==='not_applicable')return row.candidate?.status==='needs_clarification'&&effect.steps.length===0&&effect.selected_fields.length===0&&effect.final===null?{legacy:false,effect,valid:true,reviewAllowed:false}:invalid;
 if(!object(row.catalog)||!Array.isArray(row.catalog.fields)||!row.catalog.fields.every(object)||!Array.isArray(row.catalog.query_tools)||!row.catalog.query_tools.every(object))return invalid;
 const steps=row.candidate?.tool_steps;
 if(row.candidate?.status!=='candidate'||!Array.isArray(steps)||!Array.isArray(row.candidate.fields)||!row.candidate.fields.length||!row.candidate.fields.every(key=>typeof key==='string')||steps.length!==effect.steps.length||!frame(effect.final)||!equal(effect.selected_fields.map(item=>item.key),row.candidate.fields)||!equal(effect.source_scope.relation_query,row.candidate.relation_query??null)||effect.source_scope.kind!==(row.candidate.relation_query?'relation_result_set':'selected_task_entities'))return invalid;
 const producers=new Map();
 for(let index=0;index<steps.length;index++){
  const current=effect.steps[index],actual=steps[index];
  if(!object(actual)||typeof actual.id!=='string'||typeof actual.input_from!=='string'||typeof actual.capability_id!=='string'||!object(actual.parameters))return invalid;
  const method=row.catalog?.query_tools?.find(tool=>tool.id===actual.capability_id)?.method;
  if(!object(current)||current.id!==actual.id||current.input_from!==actual.input_from||current.capability_id!==actual.capability_id||current.method!==method||current.operation!==(method==='aggregate'?actual.parameters.operation:null)||current.field!==actual.parameters.field||!frame(current.input)||!frame(current.output)||current.input.id!==current.input_from||current.output.id!==current.id||current.input_from!=='evidence'&&!producers.has(current.input_from)||current.input_from!=='evidence'&&!equal(current.input,producers.get(current.input_from))||current.denominator!==null&&(!object(current.denominator)||current.denominator.frame_id!==current.input_from||current.denominator.record_unit!==current.input.record_unit||!limits(current.denominator.subset_limits)))return invalid;
  producers.set(current.id,current.output);
 }
 if(steps.length&&!equal(effect.final,effect.steps.at(-1).output)||!steps.length&&effect.final.id!=='evidence')return invalid;
 if(effect.status==='derived'&&!derivedFramesMatch(row,effect))return invalid;
 return {legacy:false,effect,valid:true,reviewAllowed:effect.status==='derived'&&queryGroundingState(row).reviewAllowed};
}
export function effectReviewKey(row){return JSON.stringify([row.id,protocolVersion(row),row.candidate_hash??null,row.question_hash??null]);}
