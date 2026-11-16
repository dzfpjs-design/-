// Pure manual representation, never an original model answer or proof.
// The caller must first verify the platform projection and its journal binding.
// The temporary V4 view is only structural-validation input: do not persist or
// send its synthetic keys. Source/meaning/permissions need the outer validators.
import {copy,fullKeys,fullText,fullSha} from './report-full-reading-contract.mjs';

const MANUAL='report-evidence-manual-review-v5',WIRE='report-evidence-candidate-wire-v5',PROJECTION='report-evidence-candidate-projection-v5';
const resultKeys='schema,source_hash,catalog_hash,domain,global_claims,objects,relationships,businesses,questions';
const claimKeys='kind,statement,span_refs,status,explanation',businessKeys='operation,original_name,name,trigger,output,steps,citations,inference_reason,participants,interactions,conditions,coordination';
const stepBase='name,purpose,agent,role,output,branch_condition,citations,inference_reason';
const kinds=['职责','来源','治理','互动','业务','步骤','条件','协同','对象','关系','冲突','其他'],statuses=['explicit','inferred','unknown'];
const span=/^[wo]:p[1-9]\d*\/s[1-9]\d*$/;
const need=(condition,message='人工稿字段或业务局部索引无法核对；未补值、换目标或修改原记录。')=>{if(!condition)throw Error(message);};
const text=(v,max,min=0)=>fullText(v,max)&&Array.from(v).length>=min;
const nonempty=(v,max)=>text(v,max,1)&&!!v.trim();
const array=(v,max,min=0)=>Array.isArray(v)&&v.length>=min&&v.length<=max;
const unique=v=>new Set(v).size===v.length;
function safe(value,depth=0){need(depth<=48);if(value===null||typeof value==='boolean')return;if(typeof value==='string'){need(text(value,8*1024*1024));return;}if(typeof value==='number'){need(Number.isFinite(value));return;}if(Array.isArray(value)){for(const item of value)safe(item,depth+1);return;}need(value&&typeof value==='object'&&[Object.prototype,null].includes(Object.getPrototypeOf(value)));for(const [key,item] of Object.entries(value)){need(text(key,8*1024*1024));safe(item,depth+1);}}
function index(value,length){need(typeof value==='number'&&Number.isSafeInteger(value)&&!Object.is(value,-0)&&value>=0&&value<length);return value;}
function indices(value,length){need(array(value,16)&&unique(value));return value.map(i=>index(i,length));}
function citations(value,min=0){need(array(value,4,min));const refs=value.map(c=>{need(fullKeys(c,'span_ref')&&text(c.span_ref,100,1)&&span.test(c.span_ref));return c.span_ref;});need(unique(refs));}
function evidence(value){need(fullKeys(value,'status,citations,explanation')&&statuses.includes(value.status)&&text(value.explanation,1000));citations(value.citations);need(value.status==='explicit'?value.citations.length>0:!!value.explanation.trim());}
function claim(value){need(fullKeys(value,claimKeys)&&kinds.includes(value.kind)&&nonempty(value.statement,2000)&&statuses.includes(value.status)&&text(value.explanation,1000)&&array(value.span_refs,16)&&unique(value.span_refs)&&value.span_refs.every(ref=>text(ref,100,1)&&span.test(ref)));need(value.status==='explicit'?value.span_refs.length>0:!!value.explanation.trim());}
function objectsAndRelationships(result){
 need(array(result.objects,40)&&array(result.relationships,30));
 for(const o of result.objects){need(fullKeys(o,'name,fields,citations,inference_reason')&&nonempty(o.name,100)&&array(o.fields,40,1)&&text(o.inference_reason,500));citations(o.citations);for(const f of o.fields){need(fullKeys(f,'label,kind,required,unit,citations,inference_reason')&&nonempty(f.label,80)&&['文本','整数','数值','是否','日期'].includes(f.kind)&&typeof f.required==='boolean'&&(f.unit===null||nonempty(f.unit,40)&&['整数','数值'].includes(f.kind))&&text(f.inference_reason,500));citations(f.citations);}need(o.fields.some(f=>f.kind==='文本'&&f.required===true),'对象尚缺明确的必填文本识别字段，保留当前修改并补充核对。');}
 for(const r of result.relationships){need(fullKeys(r,'name,description,source_type,target_type,cardinality,citations,inference_reason')&&nonempty(r.name,100)&&nonempty(r.description,1000)&&nonempty(r.source_type,100)&&nonempty(r.target_type,100)&&['many','one_per_source','one_per_target','unspecified'].includes(r.cardinality)&&text(r.inference_reason,500));citations(r.citations,1);}
}
function body(value){need(fullKeys(value,resultKeys)&&value.schema===WIRE&&fullSha.test(value.source_hash)&&fullSha.test(value.catalog_hash)&&text(value.domain,200)&&array(value.global_claims,160)&&array(value.businesses,20)&&array(value.questions,30)&&value.questions.every(q=>text(q,2000,1)));value.global_claims.forEach(claim);objectsAndRelationships(value);}
function platformKeys(items,prefix){need(Array.isArray(items));const keys=items.map(item=>{need(typeof item?.key==='string'&&new RegExp('^'+prefix+'[a-f0-9]{32}$').test(item.key));return item.key;});need(unique(keys));return new Map(keys.map((key,i)=>[key,i]));}
function target(key,keys){if(key==='end')return null;need(typeof key==='string'&&keys.has(key));return keys.get(key);}
function keyedIndices(keys,available){need(array(keys,16)&&unique(keys));return keys.map(key=>{need(typeof key==='string'&&available.has(key));return available.get(key);});}

export function evidenceManualFromProjection(result,note=''){
 safe(result);need(fullKeys(result,resultKeys)&&result.schema===PROJECTION&&array(result.global_claims,160)&&array(result.businesses,20));platformKeys(result.global_claims,'gc_');const manual={schema:MANUAL,result:copy(result),note};manual.result.schema=WIRE;
 for(const c of manual.result.global_claims){need(fullKeys(c,'key,'+claimKeys));delete c.key;}
 for(const b of manual.result.businesses){need(fullKeys(b,businessKeys));const steps=platformKeys(b.steps,'st_'),people=platformKeys(b.participants,'pa_');
  for(const s of b.steps){need(fullKeys(s,'key,'+stepBase+',next_key,else_key'));s.next_index=target(s.next_key,steps);s.else_index=target(s.else_key,steps);delete s.key;delete s.next_key;delete s.else_key;}
  for(const p of b.participants){need(fullKeys(p,'key,agent,responsibility,evidence'));delete p.key;}
  for(const [collection,prefix] of [['interactions','in_'],['conditions','co_'],['coordination','cd_']]){platformKeys(b[collection],prefix);for(const item of b[collection]){
   const fields=collection==='interactions'?'key,from_participant,to_participant,purpose,condition,step_keys,evidence':collection==='conditions'?'key,kind,statement,step_keys,evidence':'key,kind,step_keys,start_rule,wait_rule,join_rule,evidence';need(fullKeys(item,fields));item.step_indices=keyedIndices(item.step_keys,steps);delete item.step_keys;delete item.key;
   if(collection==='interactions'){need(people.has(item.from_participant)&&people.has(item.to_participant));item.from_participant_index=people.get(item.from_participant);item.to_participant_index=people.get(item.to_participant);delete item.from_participant;delete item.to_participant;}
  }}
 }
 evidenceManualValidationView(manual);return manual;
}

export function evidenceManualValidationView(value){
 safe(value);need(fullKeys(value,'schema,result,note')&&value.schema===MANUAL&&text(value.note,12000));body(value.result);const result=copy(value.result);result.schema='report-evidence-candidate-v4';result.global_claims.forEach((c,i)=>{c.key='g_'+i;});
 for(const [bi,b] of result.businesses.entries()){
  need(fullKeys(b,businessKeys)&&b.operation==='upsert'&&b.original_name===''&&nonempty(b.name,100)&&text(b.trigger,1000)&&text(b.output,1000)&&text(b.inference_reason,500)&&array(b.steps,16)&&array(b.participants,40)&&array(b.interactions,40)&&array(b.conditions,40)&&array(b.coordination,40));citations(b.citations);
  const steps=b.steps.map((_,i)=>'s_'+bi+'_'+i),people=b.participants.map((_,i)=>'p_'+bi+'_'+i);
  for(const [si,s] of b.steps.entries()){
   need(fullKeys(s,stepBase+',next_index,else_index')&&nonempty(s.name,120)&&text(s.purpose,1000)&&['业务智能体','多模感知智能体','数据管理智能体','人工确认'].includes(s.agent)&&text(s.role,100)&&nonempty(s.output,1000)&&text(s.branch_condition,500)&&text(s.inference_reason,500));citations(s.citations);
   const next=s.next_index===null?null:index(s.next_index,steps.length),otherwise=s.else_index===null?null:index(s.else_index,steps.length);need(next!==si&&otherwise!==si&&(!s.branch_condition.trim()?otherwise===null:true));s.key=steps[si];s.next_key=next===null?'end':steps[next];s.else_key=otherwise===null?'end':steps[otherwise];delete s.next_index;delete s.else_index;
  }
  for(const [pi,p] of b.participants.entries()){need(fullKeys(p,'agent,responsibility,evidence')&&nonempty(p.agent,100)&&nonempty(p.responsibility,1000));evidence(p.evidence);p.key=people[pi];}
  for(const [collection,prefix] of [['interactions','i_'],['conditions','c_'],['coordination','d_']])for(const [i,item] of b[collection].entries()){
   const fields=collection==='interactions'?'from_participant_index,to_participant_index,purpose,condition,step_indices,evidence':collection==='conditions'?'kind,statement,step_indices,evidence':'kind,step_indices,start_rule,wait_rule,join_rule,evidence';need(fullKeys(item,fields));const selected=indices(item.step_indices,steps.length);evidence(item.evidence);
   if(collection==='interactions'){const from=index(item.from_participant_index,people.length),to=index(item.to_participant_index,people.length);need(from!==to&&nonempty(item.purpose,1000)&&text(item.condition,1000));item.from_participant=people[from];item.to_participant=people[to];delete item.from_participant_index;delete item.to_participant_index;}
   else if(collection==='conditions')need(['precondition','trigger','stop','exception'].includes(item.kind)&&nonempty(item.statement,1000));
   else need(['sequence','branch','parallel','unspecified'].includes(item.kind)&&text(item.start_rule,1000)&&text(item.wait_rule,1000)&&text(item.join_rule,1000)&&(item.kind==='unspecified'||selected.length>=2));
   item.key=prefix+bi+'_'+i;item.step_keys=selected.map(si=>steps[si]);delete item.step_indices;
  }
 }
 return {result,note:value.note};
}
