const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const text=v=>typeof v==='string'&&v.trim().length>0;
const instant=v=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(v)&&Number.isFinite(Date.parse(v));
const need=(ok,message='观测策略与本次对象或发布版本无法核对；原选择保留，请重新明确选择。')=>{if(!ok)throw Error(message);};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const annotation=v=>Object.fromEntries(Object.entries(v).filter(([key])=>!['title','description'].includes(key)));
const idPatterns=['^[0-9a-f-]{36}$','^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'];

export function monitorPolicyPinInputs(schema){
 if(!object(schema?.properties)||!Object.hasOwn(schema.properties,'policy_pin'))return [];
 need(Array.isArray(schema.required||[]),'观测策略输入的必填声明无法核对；原任务草稿保留。');
 const original=schema.properties.policy_pin;need(object(original),'观测策略输入合同无法核对，原任务草稿保留。');
 for(const name of ['title','description'])need(original[name]===undefined||typeof original[name]==='string');
 let spec=annotation(original),nullable=false;
 if(Object.hasOwn(spec,'anyOf')){
  need(exact(spec,['anyOf'])&&Array.isArray(spec.anyOf)&&spec.anyOf.length===2);
  const nulls=spec.anyOf.filter(row=>exact(row,['type'])&&row.type==='null');
  need(nulls.length===1);spec=spec.anyOf.find(row=>row!==nulls[0]);nullable=true;
 }else if(Array.isArray(spec.type)){
  need(spec.type.length===2&&new Set(spec.type).size===2&&spec.type.includes('object')&&spec.type.includes('null'));
  spec={...spec,type:'object'};nullable=true;
 }
 need(exact(spec,['type','properties','required','additionalProperties'])&&spec.type==='object'
  &&spec.additionalProperties===false&&exact(spec.properties,['policy_id','approved_hash'])
  &&Array.isArray(spec.required)&&spec.required.length===2&&new Set(spec.required).size===2
  &&spec.required.includes('policy_id')&&spec.required.includes('approved_hash'));
 const id=spec.properties.policy_id,approved=spec.properties.approved_hash;
 need(exact(id,['type','pattern'])&&id.type==='string'&&idPatterns.includes(id.pattern)
  &&exact(approved,['type','pattern'])&&approved.type==='string'&&approved.pattern==='^[0-9a-f]{64}$');
 return [{key:'policy_pin',label:text(original.title)?original.title:'补充观测策略',nullable,
  required:(schema.required||[]).includes('policy_pin')}];
}

export function checkedMonitorPolicyPin(value){
 need(exact(value,['policy_id','approved_hash'])&&uuid(value.policy_id)&&hash(value.approved_hash),
  '原观测策略选择的完整性无法核对；草稿保留，请重新选择。');
 return {policy_id:value.policy_id,approved_hash:value.approved_hash};
}
export function monitorPolicyDraft(pin){
 if(pin===null)return {mode:'null',raw:''};
 return {mode:'value',raw:JSON.stringify(checkedMonitorPolicyPin(pin))};
}
export function monitorPolicyDraftValue(value,{nullable=true,required=false}={}){
 need(exact(value,['mode','raw'])&&typeof value.raw==='string'&&['unset','null','value'].includes(value.mode));
 if(value.mode==='unset'){need(value.raw===''&&!required,'请选择补充观测策略，或明确选择不自动补充观测。');return undefined;}
 if(value.mode==='null'){need(nullable&&value.raw==='','此流程要求明确选择观测策略。');return null;}
 let pin;try{pin=JSON.parse(value.raw);}catch{need(false,'原策略选择无法完整读回；原草稿保留，请重新选择。');}
 return checkedMonitorPolicyPin(pin);
}
export function monitorPolicyRequestScope(scope,selectedObjects,role){
 need(object(scope)&&['actor_id','workspace_id','instance_id','release_id'].every(key=>uuid(scope[key]))
  &&['admin','operator','viewer'].includes(role),'当前账号、角色或发布版本尚未核对；未读取观测策略。');
 need(Array.isArray(selectedObjects)&&selectedObjects.length>0&&selectedObjects.length<=200
  &&selectedObjects.every(uuid)&&new Set(selectedObjects).size===selectedObjects.length,
  '请先明确选择本次任务对象，再读取补充观测策略。');
 return {actor_id:scope.actor_id,workspace_id:scope.workspace_id,role,instance_id:scope.instance_id,
  release_id:scope.release_id,entity_ids:[...selectedObjects].sort()};
}
export function monitorPolicyScopeKey(scope,selectedObjects,role){return JSON.stringify(monitorPolicyRequestScope(scope,selectedObjects,role));}
export function checkedMonitorPolicyOptions(value,scope,selectedObjects,role){
 const request=monitorPolicyRequestScope(scope,selectedObjects,role);
 need(exact(value,['schema','instance_id','release_id','entity_ids','options','can_dispatch'])
  &&value.schema==='monitor-policy-options-v287'&&value.instance_id===request.instance_id
  &&value.release_id===request.release_id&&value.can_dispatch===false
  &&Array.isArray(value.entity_ids)&&value.entity_ids.every(uuid)&&new Set(value.entity_ids).size===value.entity_ids.length
  &&same([...value.entity_ids].sort(),request.entity_ids)&&Array.isArray(value.options));
 const seen=new Set();for(const row of value.options){
  need(exact(row,['policy_id','approved_hash','title','fields','valid_until','scope_label']));checkedMonitorPolicyPin({policy_id:row.policy_id,approved_hash:row.approved_hash});
  need(!seen.has(row.policy_id)&&text(row.title)&&text(row.scope_label)&&instant(row.valid_until)
   &&Array.isArray(row.fields)&&row.fields.length>0);
  seen.add(row.policy_id);const fields=new Set();for(const field of row.fields){
   need(exact(field,['key','label'])&&text(field.key)&&text(field.label)&&!fields.has(field.key));fields.add(field.key);
  }
 }
 return JSON.parse(JSON.stringify(value));
}
export async function loadMonitorPolicyOptions(requestJSON,scope,selectedObjects,role,{signal,isCurrent=()=>true}={}){
 const request=monitorPolicyRequestScope(scope,selectedObjects,role),key=monitorPolicyScopeKey(scope,selectedObjects,role);
 need(typeof requestJSON==='function'&&!signal?.aborted&&isCurrent(key),'本次策略读取范围已变化；原选择保留。');
 const path='/workspace/instances/'+encodeURIComponent(request.instance_id)+'/monitor-policy-options-v287?release_id='
  +encodeURIComponent(request.release_id)+'&entity_ids='+encodeURIComponent(request.entity_ids.join(','));
 const value=await requestJSON(path,{method:'GET',signal});
 need(!signal?.aborted&&isCurrent(key),'账号、对象或版本已变化；未采用旧策略响应，原选择保留。');
 return checkedMonitorPolicyOptions(value,scope,selectedObjects,role);
}
export function checkedMonitorPolicySelection(value,field,scope,selectedObjects,role,record,{now=Date.now()}={}){
 const pin=monitorPolicyDraftValue(value,field);if(pin==null)return pin;
 need(record?.scope_key===monitorPolicyScopeKey(scope,selectedObjects,role)&&record?.value,
  '原策略选择仍保留，但本次对象的可用策略尚未核对；请刷新后重新明确选择。');
 const options=checkedMonitorPolicyOptions(record.value,scope,selectedObjects,role);
 const match=options.options.find(row=>row.policy_id===pin.policy_id&&row.approved_hash===pin.approved_hash);
 need(match&&Date.parse(match.valid_until)>now,'原策略已过期、变更或不再适用于本次对象；原选择保留，请重新明确选择。');
 return pin;
}
export function monitorPolicyHumanText(value){
 return String(value??'').replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi,'（记录标识已隐藏）')
  .replace(/\b[0-9a-f]{64}\b/gi,'（摘要已隐藏）');
}
export function monitorPolicyOptionLabel(option){return monitorPolicyHumanText(option.title)+' · '+option.fields.map(field=>monitorPolicyHumanText(field.label)).join('、');}
