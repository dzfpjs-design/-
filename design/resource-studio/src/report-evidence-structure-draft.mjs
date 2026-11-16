// Unsubmitted human input is local working state, not a model/server candidate.
import {fullKeys,copy} from './report-full-reading-contract.mjs';
import {evidenceManualValidationView} from './report-evidence-manual-v5.mjs';

export const EVIDENCE_WORK_V2='report-evidence-reading-working-v2';
const fields={business:'name,reason',step:'name,purpose,output,agent,role,next_index,reason',participant:'agent,responsibility,reason',interaction:'from_participant_index,to_participant_index,purpose,condition,step_indices,reason',condition:'kind,statement,step_indices,reason',coordination:'kind,start_rule,wait_rule,join_rule,step_indices,reason'};
const need=value=>{if(!value)throw Error('未提交补充项的范围或本机保存格式无法核对，原记录保留。');};
export function structureDraftKey(kind,index){
 need(Object.hasOwn(fields,kind));
 need(kind==='business'?index===null:Number.isSafeInteger(index)&&index>=0&&index<20&&!Object.is(index,-0));
 return kind+':'+(kind==='business'?'new':index);
}
export function checkStructureDraft(value){
 if(value===null)return null;
 need(fullKeys(value,'key,kind,business_index,base_value,fields'));
 need(value.key===structureDraftKey(value.kind,value.business_index));
 evidenceManualValidationView(value.base_value);
 need(value.base_value.schema==='report-evidence-manual-review-v5');
 if(value.kind!=='business')need(value.business_index<value.base_value.result.businesses.length);
 need(fullKeys(value.fields,fields[value.kind]));
 for(const [name,item] of Object.entries(value.fields)){
  if(name==='step_indices')need(Array.isArray(item)&&item.length<=16&&new Set(item).size===item.length&&item.every(n=>Number.isSafeInteger(n)&&n>=0&&n<16&&!Object.is(n,-0)));
  else need(typeof item==='string'&&item.length<=2000);
 }
 need(new TextEncoder().encode(JSON.stringify(value)).length<=1024*1024);
 return value;
}
export function copyStructureDraft(value){return copy(checkStructureDraft(value));}
export function hasStructureDraft(state){return state?.structure_form!==null&&state?.structure_form!==undefined;}
