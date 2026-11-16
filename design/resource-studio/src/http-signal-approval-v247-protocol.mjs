import {stableStringify} from './content-signature.js';

export const httpApprovalCanonical=stableStringify;
export const httpApprovalCopy=value=>structuredClone(value);
export const httpApprovalObject=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
export const httpApprovalUUID=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
export const httpApprovalHash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export const httpApprovalExact=(value,keys)=>httpApprovalObject(value)&&Object.keys(value).sort().join('|')===[...keys].sort().join('|');
export const httpApprovalText=(value,max=2000)=>typeof value==='string'&&Array.from(value).length<=max&&!/[\u0000\ud800-\udfff]/u.test(value);
export const httpApprovalNeed=(condition,message='原授权、来源或本机输入尚不能核对；原稿与原申请保留。')=>{if(!condition)throw Error(message);};

export async function httpApprovalSHA(raw,crypto=globalThis.crypto){
 httpApprovalNeed(typeof raw==='string'&&crypto?.subtle);
 const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)));
 return [...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
}
export function httpApprovalScope(account,instanceId){
 const value={actor_id:account?.user_id,workspace_id:account?.workspace_id,instance_id:instanceId};
 httpApprovalNeed(Object.values(value).every(httpApprovalUUID),'请先核对实际账号、工作区和业务实例，再读取保存的授权。');
 return value;
}
export function httpApprovalPrefix(scope){
 httpApprovalNeed(httpApprovalExact(scope,['actor_id','workspace_id','instance_id'])&&Object.values(scope).every(httpApprovalUUID));
 return 'ga-http-signal-approval-v247:'+httpApprovalCanonical(scope)+':';
}
export function parseHTTPApprovalLocal(raw){
 try{return JSON.parse(raw);}catch{throw Error('本机授权原稿或原申请格式尚不能核对；原件保留，未重置或换号。');}
}

const need=httpApprovalNeed,exact=httpApprovalExact,uuid=httpApprovalUUID,hash=httpApprovalHash,text=httpApprovalText,canonical=httpApprovalCanonical,copy=httpApprovalCopy;
const key=value=>text(value,300)&&value.length>0;
const positive=value=>Number.isSafeInteger(value)&&value>=1;
const instant=value=>text(value,100)&&/T.*(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(value)&&Number.isFinite(Date.parse(value));
const unique=(values,validator,max=100)=>Array.isArray(values)&&values.length<=max&&values.every(validator)&&new Set(values).size===values.length;
const providers=['none','registered_archives','received_observation_records'];
function cap(value){need(exact(value,['id','name','version','content_hash'])&&uuid(value.id)&&text(value.name)&&positive(value.version)&&hash(value.content_hash));return value;}
function entity(value){need(exact(value,['id','name','definition_hash'])&&uuid(value.id)&&text(value.name)&&hash(value.definition_hash));return value;}
function policy(value){need(exact(value,['id','version_id','hash'])&&uuid(value.id)&&uuid(value.version_id)&&hash(value.hash));return value;}
async function sealed(value,field,crypto){need(hash(value[field]));const body={...value};delete body[field];need(await httpApprovalSHA(canonical(body),crypto)===value[field],'服务器回执或目录摘要与已展示内容不一致，原输入保留。');return value;}
export function httpApprovalBase(scope){return '/workspace/instances/'+scope.instance_id+'/signal-approval-routing';}

export async function checkedHTTPApprovalOptions(value,scope,crypto=globalThis.crypto){
 need(exact(value,['schema','instance_id','instance_version','release_id','sources','extractors','entities','businesses','limits','can_enable','blocking_reason','options_hash'])&&value.schema==='signal-approval-options-v247'&&value.instance_id===scope.instance_id&&positive(value.instance_version)&&uuid(value.release_id)&&typeof value.can_enable==='boolean'&&(value.blocking_reason===null||text(value.blocking_reason)));
 need(Array.isArray(value.sources)&&Array.isArray(value.extractors)&&Array.isArray(value.entities)&&Array.isArray(value.businesses));
 for(const source of value.sources){need(exact(source,['id','name','version','content_hash','extractor_id','simulation','field_verified'])&&uuid(source.extractor_id)&&source.simulation===true&&source.field_verified===false);cap({id:source.id,name:source.name,version:source.version,content_hash:source.content_hash});}
 value.extractors.forEach(cap);value.entities.forEach(entity);
 for(const business of value.businesses){need(exact(business,['id','name','execution_mode','input_provider','required_inputs','can_create_approval_draft','blocking_reason','target_hash','approval_policy'])&&key(business.id)&&text(business.name)&&key(business.execution_mode)&&providers.includes(business.input_provider)&&unique(business.required_inputs,key)&&typeof business.can_create_approval_draft==='boolean'&&(business.blocking_reason===null||text(business.blocking_reason))&&(business.target_hash===null||hash(business.target_hash)));if(business.approval_policy!==null)policy(business.approval_policy);if(business.can_create_approval_draft)need(hash(business.target_hash)&&business.approval_policy!==null&&business.blocking_reason===null);}
 for(const rows of [value.sources,value.extractors,value.entities,value.businesses])need(new Set(rows.map(row=>row.id)).size===rows.length);
 need(value.sources.every(source=>value.extractors.some(extractor=>extractor.id===source.extractor_id)));
 const limits=value.limits;need(exact(limits,['valid_for_hours_default','valid_for_hours_max','max_tasks_default','max_tasks_max'])&&positive(limits.valid_for_hours_default)&&positive(limits.valid_for_hours_max)&&limits.valid_for_hours_max<=24&&limits.valid_for_hours_default<=limits.valid_for_hours_max&&positive(limits.max_tasks_default)&&positive(limits.max_tasks_max)&&limits.max_tasks_max<=100&&limits.max_tasks_default<=limits.max_tasks_max);
 await sealed(value,'options_hash',crypto);return copy(value);
}
export function checkedHTTPApprovalRoute(value){
 need(exact(value,['id','release_id','status','source','extractor','entities','business','enabled_at','expires_at','max_tasks','used_tasks','reason','grant_hash'])&&uuid(value.id)&&uuid(value.release_id)&&['active','paused','expired','exhausted'].includes(value.status)&&instant(value.enabled_at)&&instant(value.expires_at)&&Date.parse(value.expires_at)>Date.parse(value.enabled_at)&&positive(value.max_tasks)&&value.max_tasks<=100&&Number.isSafeInteger(value.used_tasks)&&value.used_tasks>=0&&value.used_tasks<=value.max_tasks&&text(value.reason)&&hash(value.grant_hash));
 cap(value.source);cap(value.extractor);need(Array.isArray(value.entities)&&value.entities.length>=1&&value.entities.length<=20);value.entities.forEach(entity);need(new Set(value.entities.map(row=>row.id)).size===value.entities.length);
 need(exact(value.business,['id','name','execution_mode'])&&key(value.business.id)&&text(value.business.name)&&key(value.business.execution_mode));return copy(value);
}
export function checkedHTTPApprovalRoutes(value,scope){need(exact(value,['schema','instance_id','instance_version','routes'])&&value.schema==='signal-approval-routes-v247'&&value.instance_id===scope.instance_id&&positive(value.instance_version)&&Array.isArray(value.routes)&&value.routes.length<=100);value.routes.forEach(checkedHTTPApprovalRoute);need(new Set(value.routes.map(row=>row.id)).size===value.routes.length);return copy(value);}
export function checkedHTTPApprovalOccurrence(value){
 need(exact(value,['id','version','route_id','signal_id','observation_id','source_identity_hash','status','reason','created_at','updated_at','task_id','task_path','receipt_hash'])&&uuid(value.id)&&positive(value.version)&&uuid(value.route_id)&&uuid(value.signal_id)&&uuid(value.observation_id)&&hash(value.source_identity_hash)&&['frozen','waiting_data','blocked_integrity','blocked_authority','approval_draft','linked_existing_task'].includes(value.status)&&(value.reason===null||text(value.reason,6000))&&instant(value.created_at)&&instant(value.updated_at)&&(value.task_id===null||uuid(value.task_id))&&(value.receipt_hash===null||hash(value.receipt_hash)));
 need(value.task_id===null?value.task_path===null:value.task_path==='/api/workspace/tasks/'+value.task_id);
 if(['approval_draft','linked_existing_task'].includes(value.status))need(uuid(value.task_id));else need(value.task_id===null);
 return copy(value);
}
export function checkedHTTPApprovalOccurrences(value,scope){need(exact(value,['schema','instance_id','items','next_cursor'])&&value.schema==='signal-approval-occurrences-v247'&&value.instance_id===scope.instance_id&&Array.isArray(value.items)&&value.items.length<=50&&(value.next_cursor===null||uuid(value.next_cursor)));value.items.forEach(checkedHTTPApprovalOccurrence);need(new Set(value.items.map(row=>row.id)).size===value.items.length);return copy(value);}
export function checkedHTTPApprovalBody(value,operation){
 if(operation==='enable'){
  need(exact(value,['schema','request_id','expected_version','release_id','source_id','extractor_id','entity_ids','business_id','valid_for_hours','max_tasks','reason','options_hash','confirm_approval_drafts'])&&value.schema==='signal-approval-enable-v247'&&uuid(value.release_id)&&uuid(value.source_id)&&uuid(value.extractor_id)&&unique(value.entity_ids,uuid,20)&&value.entity_ids.length>0&&key(value.business_id)&&positive(value.valid_for_hours)&&value.valid_for_hours<=24&&positive(value.max_tasks)&&value.max_tasks<=100&&hash(value.options_hash)&&value.confirm_approval_drafts===true);
 }else need(['pause','retry'].includes(operation)&&exact(value,['schema','request_id','expected_version','reason'])&&value.schema==='signal-approval-'+operation+'-v247');
 need(uuid(value.request_id)&&positive(value.expected_version)&&text(value.reason)&&value.reason.trim());return value;
}
export async function checkedHTTPApprovalResult(value,scope,original=null,crypto=globalThis.crypto){
 need(exact(value,['schema','instance_id','instance_version','route','receipt','approval_draft_only','device_actions','cloud_calls'])&&value.schema==='signal-approval-command-result-v247'&&value.instance_id===scope.instance_id&&positive(value.instance_version)&&value.approval_draft_only===true&&value.device_actions===false&&value.cloud_calls===0);checkedHTTPApprovalRoute(value.route);
 const r=value.receipt;need(exact(r,['request_id','operation','route_id','actor_id','body_hash','raw_sha256','accepted_instance_version','accepted_at','receipt_hash'])&&uuid(r.request_id)&&['enable','pause','retry'].includes(r.operation)&&r.route_id===value.route.id&&r.actor_id===scope.actor_id&&hash(r.body_hash)&&hash(r.raw_sha256)&&positive(r.accepted_instance_version)&&value.instance_version>=r.accepted_instance_version&&instant(r.accepted_at));
 await sealed(r,'receipt_hash',crypto);
 if(original)need(r.request_id===original.body.request_id&&r.operation===original.operation&&r.raw_sha256===original.raw_sha256&&r.body_hash===original.body_hash&&(original.operation!=='pause'||r.route_id===original.target_id),'原请求号、正文、操作者或路由与回执不一致；原申请保留。');
 return copy(value);
}
export async function checkedHTTPApprovalRecovery(value,scope,original,crypto=globalThis.crypto){
 need(exact(value,['schema','found','request_id','raw','raw_sha256','result','current'])&&value.schema==='signal-approval-recovery-v247'&&typeof value.found==='boolean'&&value.request_id===original.body.request_id);
 if(value.found){need(value.raw===original.raw&&value.raw_sha256===original.raw_sha256);await checkedHTTPApprovalResult(value.result,scope,original,crypto);need(value.current!==null);checkedHTTPApprovalRoute(value.current);need(value.current.id===value.result.route.id);}else need(value.raw===null&&value.raw_sha256===null&&value.result===null&&value.current===null);
 return copy(value);
}
export function checkedHTTPApprovalTaskSource(value,task){
 need(exact(value,['schema','task_id','found','source','integrity_issue'])&&value.schema==='signal-approval-source-view-v247'&&value.task_id===task.id&&typeof value.found==='boolean'&&(value.integrity_issue===null||text(value.integrity_issue,6000)));
 if(!value.found)need(value.source===null&&value.integrity_issue===null);
 else if(value.source===null)need(!!value.integrity_issue);
 else{
  const source=value.source;need(value.integrity_issue===null&&exact(source,['instance_id','signal_id','observation_id','source','object','observed_at','source_time_verified','simulation','field_verified','data_received','receipt_hash','fields'])&&uuid(source.instance_id)&&(!task.instance_id||source.instance_id===task.instance_id)&&uuid(source.signal_id)&&uuid(source.observation_id)&&instant(source.observed_at)&&source.source_time_verified===false&&source.simulation===true&&source.field_verified===false&&source.data_received===true&&hash(source.receipt_hash));
  for(const reference of [source.source,source.object])need(exact(reference,['id','name'])&&uuid(reference.id)&&text(reference.name));
  need(Array.isArray(source.fields)&&source.fields.length>0&&source.fields.length<=100);
  for(const field of source.fields)need(exact(field,['key','label','unit','value'])&&key(field.key)&&text(field.label)&&(field.unit===null||text(field.unit,300))&&typeof field.value==='number'&&Number.isFinite(field.value)&&(!Number.isInteger(field.value)||Number.isSafeInteger(field.value)));
  need(new Set(source.fields.map(field=>field.key)).size===source.fields.length);
 }
 return copy(value);
}

export const blankHTTPApprovalForm=()=>({source_id:'',extractor_id:'',entity_ids:[],business_id:'',valid_for_hours:'24',max_tasks:'10',reason:'',pause_reason:'',retry_reason:'',confirmed:false});
export const blankHTTPApprovalDraft=scope=>({schema:'http-signal-approval-form-v247',scope:copy(scope),pins:null,form:blankHTTPApprovalForm()});
export function checkedHTTPApprovalDraft(value,scope){
 need(exact(value,['schema','scope','pins','form'])&&value.schema==='http-signal-approval-form-v247'&&canonical(value.scope)===canonical(scope));
 if(value.pins!==null)need(exact(value.pins,['instance_version','release_id','options_hash'])&&positive(value.pins.instance_version)&&uuid(value.pins.release_id)&&hash(value.pins.options_hash));
 const f=value.form;need(exact(f,['source_id','extractor_id','entity_ids','business_id','valid_for_hours','max_tasks','reason','pause_reason','retry_reason','confirmed'])&&text(f.source_id,100)&&text(f.extractor_id,100)&&unique(f.entity_ids,uuid,20)&&text(f.business_id,300)&&text(f.valid_for_hours,40)&&text(f.max_tasks,40)&&text(f.reason,8000)&&text(f.pause_reason,8000)&&text(f.retry_reason,8000)&&typeof f.confirmed==='boolean');return value;
}
export const meaningfulHTTPApprovalDraft=value=>!!(value.form.source_id||value.form.extractor_id||value.form.entity_ids.length||value.form.business_id||value.form.reason||value.form.pause_reason||value.form.retry_reason||value.form.valid_for_hours!=='24'||value.form.max_tasks!=='10'||value.form.confirmed);
export function httpApprovalEnableBody(draft,options,releaseID,requestID){
 checkedHTTPApprovalDraft(draft,draft.scope);const f=draft.form,p=draft.pins;
 need(options&&options.can_enable,'当前来源、业务或审批策略不具备启用资格；请核对明确原因。');
 need(p&&p.options_hash===options.options_hash&&p.instance_version===options.instance_version&&p.release_id===options.release_id&&options.release_id===releaseID,'目录或当前发布范围已变化；保留原选择，请明确采用当前范围后重新确认。');
 const source=options.sources.find(row=>row.id===f.source_id),business=options.businesses.find(row=>row.id===f.business_id);
 need(source&&source.extractor_id===f.extractor_id&&options.extractors.some(row=>row.id===f.extractor_id),'原来源或提取规则已变化，未静默换用其他来源。');
 need(business?.can_create_approval_draft&&business.approval_policy,'所选业务当前不能创建正式审批草稿：'+(business?.blocking_reason||'业务或审批策略尚未核对'));
 need(f.entity_ids.length>0&&f.entity_ids.every(id=>options.entities.some(row=>row.id===id)),'请明确选择当前可用对象；失效原选择不会自动丢弃。');
 need(/^[1-9][0-9]{0,2}$/.test(f.valid_for_hours)&&/^[1-9][0-9]{0,2}$/.test(f.max_tasks),'期限和草稿总额度须为完整整数，原输入保留。');
 need(Number(f.valid_for_hours)<=options.limits.valid_for_hours_max&&Number(f.max_tasks)<=options.limits.max_tasks_max&&f.confirmed,'请核对本次期限、总额度并明确确认仅创建审批草稿。');
 return checkedHTTPApprovalBody({schema:'signal-approval-enable-v247',request_id:requestID,expected_version:p.instance_version,release_id:p.release_id,source_id:f.source_id,extractor_id:f.extractor_id,entity_ids:[...f.entity_ids],business_id:f.business_id,valid_for_hours:Number(f.valid_for_hours),max_tasks:Number(f.max_tasks),reason:f.reason,options_hash:p.options_hash,confirm_approval_drafts:true},'enable');
}
export async function makeHTTPApprovalOriginal(scope,operation,targetId,body,crypto=globalThis.crypto){checkedHTTPApprovalBody(body,operation);need(operation==='enable'?targetId===null:uuid(targetId));const raw=JSON.stringify(body);return {schema:'http-signal-approval-original-v247',scope:copy(scope),operation,target_id:targetId,body:copy(body),raw,raw_sha256:await httpApprovalSHA(raw,crypto),body_hash:await httpApprovalSHA(canonical(body),crypto)};}
export async function checkedHTTPApprovalOriginal(value,scope,crypto=globalThis.crypto){need(exact(value,['schema','scope','operation','target_id','body','raw','raw_sha256','body_hash'])&&value.schema==='http-signal-approval-original-v247'&&canonical(value.scope)===canonical(scope)&&value.raw===JSON.stringify(value.body)&&hash(value.raw_sha256)&&hash(value.body_hash));checkedHTTPApprovalBody(value.body,value.operation);need(value.operation==='enable'?value.target_id===null:uuid(value.target_id));need(await httpApprovalSHA(value.raw,crypto)===value.raw_sha256&&await httpApprovalSHA(canonical(value.body),crypto)===value.body_hash,'本机原请求正文或摘要不一致，未重发。');return copy(value);}
