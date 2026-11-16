// Fixed business-advice commands. Only request metadata and human drafts are
// cached locally; source text, model messages and answers are read from API.
import {typedRequestJSON} from './typed-numbers-v132.mjs';
export const ADVICE_PREFIX='ga-same-goal-advice-v310:';
const DRAFT='same-goal-advice-local-draft-v310',COMMAND='same-goal-advice-local-command-v310';
const RECEIVED='same-goal-advice-local-receipt-v310';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const SHA=/^[a-f0-9]{64}$/;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const copy=value=>JSON.parse(JSON.stringify(value));
const need=(value,message='原业务建议或原申请暂不能核对，输入与记录保留。')=>{if(!value)throw Error(message);};
const integer=value=>Number.isSafeInteger(value)&&value>=1;
const exact=(value,keys)=>object(value)&&Object.keys(value).sort().join(',')===[...keys].sort().join(',');
const text=value=>typeof value==='string'&&Array.from(value).length<=2000;
const blank=()=>({selected_document_source:null,prepare_reason:'',consent_reasons:{},consent_checked:{},preview_reason:'',preview_checked:false,
 start_reason:'',review_decision:'',review_reason:'',cancel_reason:'',quota_cost:'',quota_reason:'',price_reason:''});
const PERMISSIONS=['can_prepare','can_authorize','can_preview','can_start','can_review','can_configure_quota','can_register_price'];
const PHASES=['queued','preparing','awaiting_consent','send_queued','generating','closing','ready_for_review','reviewed','needs_review','cancelled'];
const FLAGS=['can_dispatch','permissions_verified','source_authenticity_verified','current_outcome_verified','goal_completion_verified','data_reception_verified'];
const emptyReads=()=>({context:'idle',history:'idle',plan:'idle',documents:'idle',preview:'idle'});

function selection(value){
 need(object(value));
 if(value.kind==='approved_source_set')need(exact(value,['kind','source_set_ref'])
  &&exact(value.source_set_ref,['source_set_id','source_set_hash'])&&UUID.test(value.source_set_ref.source_set_id)&&SHA.test(value.source_set_ref.source_set_hash));
 else need(value.kind==='document_packet_v207'&&exact(value,['kind','packet'])
  &&exact(value.packet,['id','version','evidence_id','sha256','source_set_id','source_set_hash'])
  &&[value.packet.id,value.packet.evidence_id,value.packet.source_set_id].every(v=>UUID.test(v))
  &&value.packet.version===1&&SHA.test(value.packet.sha256)&&SHA.test(value.packet.source_set_hash));
 return value;
}
function checkedContext(value,scope){
 need(value?.schema==='same-goal-advice-context-v310'&&value.task_id===scope.task_id&&integer(value.task_version)
  &&typeof value.title==='string'&&object(value.permissions)&&PERMISSIONS.every(key=>typeof value.permissions[key]==='boolean')
  &&Array.isArray(value.document_sources)&&value.document_sources.length<=20&&Array.isArray(value.blocked_reasons));
 for(const row of value.document_sources){selection(row.selection);need(typeof row.title==='string'&&typeof row.description==='string'&&integer(row.excerpt_count));}
 for(const key of ['goal_hash','mature_head_hash'])need(value[key]===null||SHA.test(value[key]));
 for(const reason of value.blocked_reasons)need(object(reason)&&typeof reason.code==='string'&&typeof reason.message==='string'&&typeof reason.section==='string');
 return copy(value);
}
function checkedPlan(value,scope,identity=null){
 need(value?.schema==='same-goal-advice-operation-v302'&&value.task_id===scope.task_id&&UUID.test(value.plan_id)
  &&(!identity||identity===value.plan_id)&&integer(value.version)&&UUID.test(value.request_id)&&SHA.test(value.preparation_snapshot_hash)
  &&PHASES.includes(value.phase)&&typeof value.human_review_available==='boolean'&&Array.isArray(value.reviews)&&value.reviews.length<=1);
 if(value.result!==null){const result=value.result;need(result?.schema==='same-goal-advice-result-v302'
  &&result.plan_ref?.id===value.plan_id&&result.review_state==='ready_for_review'&&FLAGS.every(key=>result[key]===false));}
 if(value.phase==='ready_for_review')need(value.human_review_available===true&&value.result!==null);
 if(value.human_review_available)need(value.phase==='ready_for_review'&&value.result!==null);
 if(Object.hasOwn(value,'cancel_available'))need(typeof value.cancel_available==='boolean');
 if(Object.hasOwn(value,'continues_stopped_reasoning'))need(typeof value.continues_stopped_reasoning==='boolean');
 if(Object.hasOwn(value,'stopped_reasoning_unlinked'))need(typeof value.stopped_reasoning_unlinked==='boolean');
 need(!(value.continues_stopped_reasoning===true&&value.stopped_reasoning_unlinked===true));
 return copy(value);
}
async function checkedDocuments(value,scope,planId,crypto){
 need(value?.schema==='same-goal-advice-documents-v310'&&value.task_id===scope.task_id&&value.plan_id===planId
  &&integer(value.plan_version)&&object(value.model_pin)&&UUID.test(value.model_pin.id)&&SHA.test(value.model_pin.content_hash)
  &&Array.isArray(value.documents)&&value.documents.length===2&&Array.isArray(value.restrictions));
 const ids=new Set();
 for(const doc of value.documents){const ref=doc.evidence_ref;
  need(['data','business_document'].includes(doc.role)&&typeof doc.title==='string'&&typeof doc.text==='string'
   &&object(ref)&&UUID.test(ref.evidence_id)&&!ids.has(ref.evidence_id)&&SHA.test(ref.sha256)&&ref.media_type==='text/plain'
   &&integer(ref.size_bytes)&&ref.size_bytes<=20000&&integer(doc.version)
   &&['evaluation_text','approved_excerpt'].includes(doc.source_scope)&&object(doc.consent)
   &&['authorized','not_authorized','model_unavailable'].includes(doc.consent.status)&&Array.isArray(doc.consent.grants));
  ids.add(ref.evidence_id);const bytes=new TextEncoder().encode(doc.text);
  need(bytes.length===ref.size_bytes&&await adviceHash(doc.text,crypto)===ref.sha256,'原文与保存的文件摘要不一致，未作为授权内容。');
 }
 need(new Set(value.documents.map(doc=>doc.role)).size===2);return copy(value);
}
function checkedPreview(value,planId){
 need(value?.schema==='same-goal-advice-preview-view-v307'&&value.plan_id===planId&&UUID.test(value.request_id)
  &&SHA.test(value.preview_hash)&&integer(value.plan_version)&&integer(value.preview_generation)&&typeof value.latest_for_plan==='boolean'
  &&Array.isArray(value.messages)&&value.messages.length>=2&&object(value.options)&&integer(value.options.max_tokens)
  &&object(value.model)&&UUID.test(value.model.id)&&integer(value.maximum_cost_minor_units)
  &&integer(value.configured_ceiling_minor_units)&&value.maximum_cost_minor_units<=value.configured_ceiling_minor_units
  &&value.preview_generated_cloud_calls===0);
 return copy(value);
}

export function adviceCanonical(value){
 return typedRequestJSON(value);
}
export async function adviceHash(value,crypto=globalThis.crypto){
 need(crypto?.subtle,'当前浏览器不能核对原请求摘要，尚未提交。');
 return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(v=>v.toString(16).padStart(2,'0')).join('');
}
export function adviceMinorUnits(raw){
 need(typeof raw==='string'&&raw.length<=40,'请按金额填写，最多两位小数。');
 const value=raw.trim(),match=/^(0|[1-9]\d{0,7})(?:\.(\d{1,2}))?$/.exec(value);
 need(match,'金额只接受十进制数和最多两位小数，不接受货币符号、逗号或科学计数。');
 const minor=BigInt(match[1])*100n+BigInt((match[2]||'').padEnd(2,'0'));
 need(minor>=1n&&minor<=1000000000n,'本次费用上限需在0.01至10000000.00之间。');
 return Number(minor); // Range was checked as integers; no float multiplication or rounding.
}
function userError(error){
 if(error?.status===401)return '会话已失效，原输入和请求保留；重新登录后可回到原任务核对。';
 if(error?.status===403)return '当前账号无权完成此操作，原输入与申请保留。';
 const value=typeof error?.message==='string'?error.message:'';
 if(!value||/Traceback|\b(?:TypeError|KeyError|AttributeError|SyntaxError)\b|^\s*[\[{]|(?:secret_ref|lease_token|invocation_id)|\b(?:api_key|authorization|bearer|cookie|secret)\s*[:=]/i.test(value))
  return '原操作暂未核对完成，输入与原请求保留，请重新读取原记录。';
 return value.replace(/\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b/gi,'对应记录').replace(/\b[a-f0-9]{64}\b/gi,'原校验记录').slice(0,600);
}

import {withScopedRequest} from './scope-request-budget-v314.mjs';

export class SameGoalAdviceControllerV310{
 constructor({account,task,request,storage=globalThis.localStorage,session=globalThis.sessionStorage,
  locks=globalThis.navigator?.locks,crypto=globalThis.crypto,onChange=()=>{},beforePlanChange=()=>true}){
  this.scope={actor_id:account.user_id,workspace_id:account.workspace_id,task_id:task.id,
   instance_id:task.instance_id,release_id:task.instance_release_id};
  need(Object.values(this.scope).every(value=>typeof value==='string'&&UUID.test(value))&&typeof request==='function');
  need(typeof beforePlanChange==='function');
  Object.assign(this,{requestAPI:request,storage,session,locks,crypto,onChange,beforePlanChange,role:account.role});
  this.key=ADVICE_PREFIX+adviceCanonical(this.scope);this.base='/workspace/tasks/'+task.id+'/business-advice';
  this.csrf=session.getItem('ga-setup-csrf');this.active=true;this.suspended=false;this.denied=false;this.generation=0;
  this.loading=true;this.busy=false;this.localSaved=false;this.error='';this.notice='';this.pending=null;this.history=[];
  this.draft=blank();this.planId='';this.context=null;this.list=null;this.plan=null;this.documents=null;this.preview=null;
  this.notFound=false;this.owner=null;this.acknowledgements={};this.previewAcknowledgement=null;this.openPromise=null;this.needsCheck=false;
  this.pendingRejected=null;this.unsettled=[];this.reads=emptyReads();this.readGeneration=0;this.readActive=0;this.readQueue=[];
  this.requestAPI=(path,options)=>{const epoch=this.generation;return withScopedRequest(
   ()=>request(path,options),()=>this.check(epoch));};
 }
 view(){
  try{
   if(this.session.getItem('ga-setup-csrf-user')!==this.scope.actor_id||this.session.getItem('ga-setup-csrf-workspace')!==this.scope.workspace_id
    ||this.session.getItem('ga-setup-csrf')!==this.csrf){this.denied=true;this.context=this.list=this.plan=this.documents=this.preview=null;
    this.error='账号或会话已变化，原资料与回答已隐藏，原稿保留。';}
  }catch{this.denied=true;this.context=this.list=this.plan=this.documents=this.preview=null;}
  const hidden=this.denied||!this.active;
  return {scopeKey:this.key,loading:this.loading,busy:this.busy,denied:this.denied,localSaved:this.localSaved,
   error:this.error,notice:this.notice,pending:hidden?null:copy(this.pending),history:hidden?[]:copy(this.history),
   draft:hidden?null:copy(this.draft),selectedPlanId:hidden?'':this.planId,context:hidden?null:copy(this.context),
   list:hidden?null:copy(this.list),plan:hidden?null:copy(this.plan),documents:hidden?null:copy(this.documents),
   preview:hidden?null:copy(this.preview),reads:copy(this.reads),notFound:this.notFound,needsCheck:this.needsCheck,
   pendingRejected:hidden?null:copy(this.pendingRejected)};
 }
 emit(){if(this.active)this.onChange(this.view());}
 requirePlanChange(id){
  if(id!==this.planId)need(this.beforePlanChange(this.planId,id)===true,
   '后续办理草案尚未可靠保存，原输入和原申请保留；请先恢复草案保存，再切换原申请。');
 }
 deny(error){this.denied=true;this.context=this.list=this.plan=this.documents=this.preview=null;this.reads=emptyReads();this.error=userError(error);this.emit();}
 check(epoch=this.generation){
  if(!this.active||this.suspended||epoch!==this.generation){const error=Error('此前页面已停止处理，原申请保留。');error.code='ADVICE_STALE';throw error;}
  need(!this.denied,'当前会话已变化，原输入保留。');
  if(this.session.getItem('ga-setup-csrf-user')!==this.scope.actor_id||this.session.getItem('ga-setup-csrf-workspace')!==this.scope.workspace_id
   ||this.session.getItem('ga-setup-csrf')!==this.csrf){const error=Error('账号、工作区或会话已变化，原资料与回答已隐藏，原稿保留。');this.deny(error);throw error;}
 }
 async identity(epoch=this.generation){
  this.check(epoch);let me;
  try{me=await this.requestAPI('/me',{headers:{'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.csrf}});}
  catch(error){if([401,403].includes(error.status))this.deny(error);throw error;}
  this.check(epoch);
  if(me?.user_id!==this.scope.actor_id||me?.workspace_id!==this.scope.workspace_id||me?.role!==this.role){
   const error=Error('登录账号、工作区或权限已变化，原资料与回答已隐藏，原稿保留。');this.deny(error);throw error;}
  return me;
 }
 async request(path,options={},epoch=this.generation){
  const run=async()=>{
   await this.identity(epoch);this.check(epoch);let value;
   try{value=await this.requestAPI(path,{...options,headers:{...options.headers,'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.csrf}});}
   catch(error){this.check(epoch);if(error.status===401||error.status===403&&(!options.method||options.method==='GET'))this.deny(error);throw error;}
   this.check(epoch);await this.identity(epoch);return value;
  };
  if(options.method&&options.method!=='GET')return run();
  // Include both IAM checks in the slot: this card never occupies more than
  // three concurrent read connections, including a slow context request.
  if(this.readActive>=3)await new Promise(resolve=>this.readQueue.push(resolve));else this.readActive++;
  try{this.check(epoch);return await run();}
  finally{const next=this.readQueue.shift();if(next)next();else this.readActive--;}
 }
 write(key,value){
  this.check();const raw=JSON.stringify(value);this.storage.setItem(key,raw);
  need(this.storage.getItem(key)===raw,'本机原输入尚未可靠保存，未提交服务器。');return raw;
 }
 checkedDraft(row){
  need(exact(row,['schema','scope','owner','plan_id','draft'])&&row.schema===DRAFT&&adviceCanonical(row.scope)===adviceCanonical(this.scope)
   &&UUID.test(row.owner)&&(row.plan_id===''||UUID.test(row.plan_id)));
  const previous=Object.keys(blank()).filter(key=>!['quota_cost','quota_reason','price_reason'].includes(key));
  need(exact(row.draft,Object.keys(blank()))||exact(row.draft,previous));
  row={...row,draft:{...blank(),...row.draft}};
  const value=row.draft;
  if(value.selected_document_source!==null)selection(value.selected_document_source);
  need(['prepare_reason','preview_reason','start_reason','review_reason','cancel_reason','quota_reason','price_reason'].every(key=>text(value[key]))
   &&typeof value.quota_cost==='string'&&value.quota_cost.length<=40
   &&['','accepted','rejected','needs_revision'].includes(value.review_decision)&&typeof value.preview_checked==='boolean'
   &&object(value.consent_reasons)&&object(value.consent_checked)&&Object.keys(value.consent_reasons).length<=2
   &&Object.keys(value.consent_checked).length<=2);
  for(const [id,reason]of Object.entries(value.consent_reasons))need(UUID.test(id)&&text(reason));
  for(const [id,checked]of Object.entries(value.consent_checked))need(UUID.test(id)&&typeof checked==='boolean');
  return copy(row);
 }
 initialize(){
  const slot='ga-same-goal-advice-v310-tab';let owner=this.session.getItem(slot);
  if(owner===null){owner=this.crypto.randomUUID();this.session.setItem(slot,owner);need(this.session.getItem(slot)===owner);}
  need(UUID.test(owner));this.owner=owner;
  const own=this.storage.getItem(this.key+':draft:'+owner),raw=own??this.storage.getItem(this.key+':draft-latest');
  if(raw!==null){const value=this.checkedDraft(JSON.parse(raw));need(this.storage.getItem(this.key+':draft:'+value.owner)===raw);
   this.planId=value.plan_id;this.draft=value.draft;}
  // Confirmation belongs to the freshly displayed text, never a restored tick.
  this.draft.consent_checked={};this.draft.preview_checked=false;
  return this.persist();
 }
 persist(){
  try{need(this.owner);const row=this.checkedDraft({schema:DRAFT,scope:this.scope,owner:this.owner,plan_id:this.planId,draft:this.draft});
   this.write(this.key+':draft:'+this.owner,row);
   if(this.planId)this.write(this.key+':plan-draft:'+this.owner+':'+this.planId,row);
   this.write(this.key+':draft-latest',row);this.localSaved=true;return true;
  }catch(error){this.localSaved=false;this.error=userError(error);return false;}
 }
 async operation(action){
  this.check();need(!this.busy,'正在核对原操作，请稍候。');this.busy=true;this.error='';this.notice='';this.emit();const epoch=this.generation;
  try{return await action(epoch);}catch(error){if(this.active&&epoch===this.generation&&error.code!=='ADVICE_STALE')this.error=userError(error);throw error;}
  finally{if(this.active&&epoch===this.generation){this.busy=false;this.emit();}}
 }
 async locked(action){need(this.locks?.request,'当前浏览器不能可靠保护并发申请；仍可读取已保存记录。');
  return this.locks.request(this.key,{mode:'exclusive'},async()=>{this.check();return action();});}
 commandPath(operation,planId,evidenceId){
  if(['quota','price'].includes(operation)){need(planId===null&&evidenceId===null);return this.base+'/'+operation+'-options';}
  if(operation==='prepare'){need(planId===null&&evidenceId===null);return this.base+'/preparations';}
  if(operation==='authorize'){need(UUID.test(planId)&&UUID.test(evidenceId));return '/evidence/'+evidenceId+'/authorize';}
  need(['preview','start','review','cancel'].includes(operation)&&UUID.test(planId)&&evidenceId===null);
  return this.base+'/preparations/'+planId+'/'+operation;
 }
 checkedCommand(row){
  need(exact(row,['schema','scope','operation','plan_id','evidence_id','request_id','path','body','raw','body_hash','expected'])
   &&row.schema===COMMAND&&adviceCanonical(row.scope)===adviceCanonical(this.scope)&&UUID.test(row.request_id)
   &&row.path===this.commandPath(row.operation,row.plan_id,row.evidence_id)&&object(row.body)
   &&row.raw===adviceCanonical(row.body)&&SHA.test(row.body_hash)&&object(row.expected));
  if(row.operation==='authorize')need(exact(row.body,['model_profile_id','expected_version','reason'])
   &&UUID.test(row.body.model_profile_id)&&integer(row.body.expected_version)&&text(row.body.reason));
  else{
   const fields={prepare:['request_id','expected_goal_hash','expected_mature_head_hash','document_source','reason'],
    preview:['request_id','expected_plan_version','expected_goal_hash','expected_mature_head_hash','reason'],
    start:['request_id','expected_plan_version','expected_goal_hash','expected_mature_head_hash','preview_hash','cloud_authorized','reason'],
    review:['request_id','expected_plan_version','result_hash','decision','reason'],cancel:['request_id','expected_plan_version','reason'],
    quota:['request_id','expected_goal_hash','expected_mature_head_hash','max_cost_minor_units','currency','validity_seconds','reason'],
    price:['request_id','model_pin','validity_seconds','reason']};
   need(exact(row.body,fields[row.operation])&&row.body.request_id===row.request_id&&text(row.body.reason)&&/\S/u.test(row.body.reason));
   if(row.operation==='prepare')selection(row.body.document_source);
   else if(!['quota','price'].includes(row.operation))need(integer(row.body.expected_plan_version));
   if(['prepare','preview','start','quota'].includes(row.operation))need(SHA.test(row.body.expected_goal_hash)&&SHA.test(row.body.expected_mature_head_hash));
   if(row.operation==='start')need(row.body.cloud_authorized===true&&SHA.test(row.body.preview_hash));
   if(row.operation==='review')need(['accepted','rejected','needs_revision'].includes(row.body.decision)&&SHA.test(row.body.result_hash));
   if(row.operation==='quota')need(integer(row.body.max_cost_minor_units)&&row.body.max_cost_minor_units<=1000000000
    &&['CNY','USD'].includes(row.body.currency)&&row.body.validity_seconds===7200);
   if(row.operation==='price')need(exact(row.body.model_pin,['id','content_hash','release_version'])&&UUID.test(row.body.model_pin.id)
    &&SHA.test(row.body.model_pin.content_hash)&&typeof row.body.model_pin.release_version==='string'&&row.body.validity_seconds===3600);
  }
  return copy(row);
 }
 async original(){
  this.check();const raw=this.storage.getItem(this.key+':pending');
  if(raw===null){need(this.pending,'原请求尚待核对，未推断为已完成。');return this.storedOriginal(this.pending);}
  const row=this.checkedCommand(JSON.parse(raw));need(this.storage.getItem(this.key+':original:'+row.request_id)===raw
   &&await adviceHash(row.raw,this.crypto)===row.body_hash,'本机原请求正文或摘要已变化，未发送替代申请。');
  this.check();if(this.pending)need(adviceCanonical(this.pending)===adviceCanonical(row),'另一窗口已更新原申请，保留本页人稿。');
  this.pending=row;return row;
 }
 async stage(operation,body,{planId=this.planId||null,evidenceId=null,expected={}}={}){
  // A crashed tab may have preserved the independent original before the
  // shared pointer. Rebuild only the local read inventory, never a new POST.
  await this.reloadLocal();
  need(this.localSaved&&this.persist()&&this.storage.getItem(this.key+':pending')===null&&!this.pending,
   '原输入未保存或已有申请待核对，未创建第二次操作。');
  const requestId=operation==='authorize'?this.crypto.randomUUID():body.request_id,raw=adviceCanonical(body);
  const row=this.checkedCommand({schema:COMMAND,scope:this.scope,operation,plan_id:planId,evidence_id:evidenceId,
   request_id:requestId,path:this.commandPath(operation,planId,evidenceId),body:copy(body),raw,
   body_hash:await adviceHash(raw,this.crypto),expected:copy(expected)});
  need(this.storage.getItem(this.key+':original:'+requestId)===null,'原申请编号已存在，未覆盖。');
  this.write(this.key+':original:'+requestId,row);this.write(this.key+':pending',row);this.pending=row;this.emit();return row;
 }
 permit(name){need(!this.loading&&this.reads.context==='ready'&&this.context?.permissions?.[name]===true&&!this.needsCheck&&!this.pending&&this.localSaved,
  '当前条件尚未核对或原操作仍待确认，请先处理页面中的原因，原输入保留。');}
 reason(name){const value=this.draft[name];need(text(value)&&/\S/u.test(value),'请先填写本次操作的理由。');return value;}
 updateDraft(patch){
  this.check();need(!this.busy&&!this.pending&&object(patch)&&Object.keys(patch).every(key=>Object.hasOwn(blank(),key)));
  const next={...copy(this.draft),...copy(patch)};
  if(Object.hasOwn(patch,'selected_document_source')&&patch.selected_document_source!==null){
   selection(patch.selected_document_source);need(this.context?.document_sources.some(row=>adviceCanonical(row.selection)===adviceCanonical(patch.selected_document_source)));}
  for(const key of ['consent_reasons','consent_checked'])if(Object.hasOwn(patch,key))
   need(object(patch[key])&&Object.keys(patch[key]).every(id=>this.documents?.documents.some(doc=>doc.evidence_ref.evidence_id===id)));
  if(Object.hasOwn(patch,'consent_reasons'))for(const [id,reason]of Object.entries(patch.consent_reasons))
   if(reason!==this.draft.consent_reasons[id]){next.consent_checked[id]=false;delete this.acknowledgements[id];}
  if(Object.hasOwn(patch,'consent_checked'))for(const [id,ticked]of Object.entries(patch.consent_checked)){
   need(typeof ticked==='boolean');if(ticked){const doc=this.document(id);need(doc.consent.status==='not_authorized');
    this.acknowledgements[id]=this.consentPin(doc);}else delete this.acknowledgements[id];}
  if(Object.hasOwn(patch,'preview_checked')){
   need(typeof patch.preview_checked==='boolean');if(patch.preview_checked)need(this.preview?.latest_for_plan===true&&this.plan?.version===this.preview.plan_version);
   this.previewAcknowledgement=patch.preview_checked?adviceCanonical(this.preview):null;
  }
  this.checkedDraft({schema:DRAFT,scope:this.scope,owner:this.owner,plan_id:this.planId,draft:next});
  this.draft=next;need(this.persist());this.emit();
 }
 selectDocumentSource(value){return this.updateDraft({selected_document_source:value});}
 document(id){need(UUID.test(id));const doc=this.documents?.documents.find(row=>row.evidence_ref.evidence_id===id);
  need(doc,'本次资料尚未完整读回，请先重新读取原资料。');return doc;}
 consentPin(doc){return adviceCanonical({ref:doc.evidence_ref,version:doc.version,model:this.documents.model_pin});}
 async readPlan(id,epoch=this.generation){return checkedPlan(await this.request(this.base+'/preparations/'+id,{},epoch),this.scope,id);}
 async refreshState(epoch=this.generation){
  this.check(epoch);const round=++this.readGeneration,knownPlan=this.planId,issues={};let planRead=null;
  const current=()=>{this.check(epoch);need(round===this.readGeneration,'此前读取已被新一轮核对替代，未采用迟到资料。');};
  this.loading=true;this.reads=emptyReads();this.context=this.list=this.plan=this.documents=this.preview=null;this.emit();
  const branch=async(name,read,save)=>{
   current();this.reads[name]='loading';this.emit();
   try{const value=await read();current();save(value);this.reads[name]='ready';this.emit();return value;}
   catch(error){current();this.reads[name]='failed';issues[name]=error;this.emit();throw error;}
  };
  const loadPlan=id=>{
   if(planRead)return planRead;
   planRead=(async()=>{
    const plan=await branch('plan',()=>this.readPlan(id,epoch),value=>{this.plan=value;});
    if(plan.document_preparation===null)return;
    await Promise.allSettled([
     branch('documents',async()=>checkedDocuments(await this.request(this.base+'/preparations/'+id+'/documents',{},epoch),this.scope,id,this.crypto),value=>{this.documents=value;}),
     branch('preview',async()=>{
      try{return checkedPreview(await this.request(this.base+'/preparations/'+id+'/preview',{},epoch),id);}
      catch(error){if(error.status===404)return null;throw error;}
     },value=>{this.preview=value;})]);
   })();return planRead;
  };
  try{
   const context=branch('context',async()=>checkedContext(await this.request(this.base+'/context',{},epoch),this.scope),value=>{this.context=value;});
   const history=branch('history',async()=>{
    const list=await this.request(this.base+'/preparations',{},epoch);
    need(list?.schema==='same-goal-advice-operation-v302/list'&&list.task_id===this.scope.task_id&&Array.isArray(list.records)&&list.records.length<=1000);
    const seen=new Set();for(const value of list.records){checkedPlan(value,this.scope);need(!seen.has(value.plan_id));seen.add(value.plan_id);}return copy(list);
   },value=>{this.list=value;}).then(list=>{
    if(!knownPlan){const active=list.records.filter(row=>row.status!=='cancelled');
     if(active.length===1){current();this.requirePlanChange(active[0].plan_id);this.planId=active[0].plan_id;return loadPlan(this.planId);}}
   });
   // The retained Plan has its own authenticated read, independent of the
   // slow configuration/list branches; the same round reads it only once.
   await Promise.allSettled([context,history,knownPlan?loadPlan(knownPlan):Promise.resolve()]);current();
   for(const id of Object.keys(this.acknowledgements)){
    const doc=this.documents?.documents.find(row=>row.evidence_ref.evidence_id===id);
    if(!doc||this.consentPin(doc)!==this.acknowledgements[id]){delete this.acknowledgements[id];this.draft.consent_checked[id]=false;}
   }
   if(!this.preview||this.previewAcknowledgement!==adviceCanonical(this.preview)){this.previewAcknowledgement=null;this.draft.preview_checked=false;}
   if(Object.keys(issues).length)this.error=Object.values(issues).map(userError).join(' ');
   if(issues.history||issues.plan)throw issues.history||issues.plan;
  }finally{if(this.active&&epoch===this.generation&&round===this.readGeneration){this.loading=false;this.emit();}}
  return this.view();
 }
 async open(){
  this.check();if(this.openPromise)return this.openPromise;
  this.openPromise=this.operation(async epoch=>{
   let localError='';try{this.initialize();await this.reloadLocal();}catch(error){this.localSaved=false;localError=userError(error);}
   let recoveryError='';
   // A protected original command can be acknowledged and durably finished
   // before any expensive current-context read. Opening never resends it.
   if(this.pending)try{await this.recoverOriginal({deferRefresh:true});}catch(error){recoveryError=userError(error);}
   await this.refreshState(epoch);
   if(recoveryError)this.error=recoveryError;
   if(localError)this.error=localError;
   this.loading=false;this.emit();return this.view();
  }).finally(()=>{if(this.active){this.loading=false;this.emit();}});return this.openPromise;
 }
 async reloadLocal(){
  need(this.storage.length<=4096,'本机原请求库存超过单次完整核对上限，原件保留，暂不能提交新操作。');
  const originals=[],receipts=[];
  for(let index=0;index<this.storage.length;index++){
   const key=this.storage.key(index);
   if(key?.startsWith(this.key+':original:')){
    const row=this.checkedCommand(JSON.parse(this.storage.getItem(key)));await this.storedOriginal(row);originals.push(row);
   }else if(key?.startsWith(this.key+':received:'))receipts.push(JSON.parse(this.storage.getItem(key)));
  }
  this.history=[];
  for(const row of receipts){const original=originals.find(value=>value.request_id===row.request_id);
   need(original&&row.schema===RECEIVED&&adviceCanonical(row.scope)===adviceCanonical(this.scope)
    &&row.operation===original.operation&&row.body_hash===original.body_hash
    &&(['quota','price'].includes(row.operation)?row.plan_id===null:UUID.test(row.plan_id))
    &&['available','needs_check'].includes(row.result_status)&&object(row.proof));this.history.push(row);
  }
  this.unsettled=[];
  for(const row of originals){
   if(this.history.some(receipt=>receipt.request_id===row.request_id))continue;
   const retained=this.storage.getItem(this.key+':not-applied:'+row.request_id);
   if(retained!==null){const value=JSON.parse(retained);need(adviceCanonical(value.original)===adviceCanonical(row)
    &&[400,403,409,413,422].includes(value.rejection?.status));continue;}
   this.unsettled.push(row);
  }
  const raw=this.storage.getItem(this.key+':pending');
  if(raw!==null){this.pending=this.checkedCommand(JSON.parse(raw));await this.original();}
  else this.pending=this.unsettled.find(row=>row.request_id===this.pending?.request_id)||this.unsettled[0]||null;
  this.pendingRejected=null;
  if(this.pending){const rejected=this.storage.getItem(this.key+':rejected:'+this.pending.request_id);
   if(rejected!==null){const value=JSON.parse(rejected);need([400,403,409,413,422].includes(value.status)&&typeof value.message==='string');this.pendingRejected=value;}}
  this.needsCheck=this.history.some(row=>row.result_status==='needs_check');
 }
 refresh(){return this.operation(async epoch=>{await this.reloadLocal();return this.refreshState(epoch);});}
 selectPlan(id){return this.operation(async epoch=>{
  need(!this.pending&&UUID.test(id)&&this.list?.records.some(row=>row.plan_id===id));this.requirePlanChange(id);need(this.persist());
  this.write(this.key+':retained:'+this.crypto.randomUUID(),{schema:DRAFT,scope:this.scope,owner:this.owner,plan_id:this.planId,draft:this.draft});
  const raw=this.storage.getItem(this.key+':plan-draft:'+this.owner+':'+id);this.draft=raw===null?blank():this.checkedDraft(JSON.parse(raw)).draft;
  this.planId=id;this.draft.consent_checked={};this.draft.preview_checked=false;this.acknowledgements={};this.previewAcknowledgement=null;
  need(this.persist());return this.refreshState(epoch);
 });}
 async receipt(row,value){
  need(value?.schema==='same-goal-advice-command-receipt-v310'&&value.found===true&&value.operation===row.operation
   &&value.task_id===this.scope.task_id&&value.request_id===row.request_id&&value.actor_id===this.scope.actor_id
   &&value.body_hash===row.body_hash&&(['quota','price'].includes(row.operation)?value.plan_id===null:UUID.test(value.plan_id))
   &&(!row.plan_id||value.plan_id===row.plan_id)
   &&['available','needs_check'].includes(value.result_status));
  if(value.result_status==='needs_check')need(value.result===null);
  else{
   if(['prepare','cancel'].includes(row.operation))checkedPlan(value.result,this.scope,value.plan_id);
   if(row.operation==='preview')need(checkedPreview(value.result,value.plan_id).request_id===row.request_id);
   if(row.operation==='start')need(value.result?.schema==='same-goal-advice-start-view-v308'&&value.result.plan_id===value.plan_id&&value.result.request_id===row.request_id);
   if(row.operation==='review')need(value.result?.request_id===row.request_id&&value.result.actor_id===this.scope.actor_id
    &&value.result.result_hash===row.body.result_hash&&value.result.decision===row.body.decision&&value.result.reason===row.body.reason);
   if(row.operation==='quota')need(value.result?.schema==='same-goal-advice-quota-authorization-v304'
    &&value.result.task_id===this.scope.task_id&&value.result.request_id===row.request_id
    &&value.result.configuration_only===true&&value.result.pricing_verified===false&&value.result.cloud_send_authorized===false);
   if(row.operation==='price')need(value.result?.schema==='same-goal-advice-official-price-registration-v307/read'
    &&value.result.money_reserved===false&&value.result.cloud_send_authorized===false);
  }
  return value;
 }
 async storedOriginal(row){
  this.checkedCommand(row);need(this.storage.getItem(this.key+':original:'+row.request_id)===JSON.stringify(row)
   &&await adviceHash(row.raw,this.crypto)===row.body_hash,'原请求独立正文已变化，未确认或重发。');this.check();return row;
 }
 async finish(row,planId,resultStatus,proof,{deferRefresh=false}={}){
  await this.storedOriginal(row);const pending=this.storage.getItem(this.key+':pending');
  need(pending===null||pending===JSON.stringify(row),'另一窗口已有不同原请求，未清除或覆盖。');
  if(planId!==null)this.requirePlanChange(planId);
  const record={schema:RECEIVED,scope:this.scope,operation:row.operation,request_id:row.request_id,body_hash:row.body_hash,
   plan_id:planId,result_status:resultStatus,proof:copy(proof)};
  // Confirm the protected command before removing pending, including a
  // durable needs_check obligation when the command's result cannot be read.
  this.write(this.key+':received:'+row.request_id,record);if(planId!==null)this.planId=planId;need(this.persist());
  if(pending!==null){need(this.storage.getItem(this.key+':pending')===JSON.stringify(row));this.storage.removeItem(this.key+':pending');}
  need(this.storage.getItem(this.key+':pending')===null,'服务器已保存，本机原请求仍待保护，请继续核对原记录。');
  this.pending=null;this.notFound=false;await this.reloadLocal();
  this.notice=resultStatus==='needs_check'?'原申请已保存；结果原件仍需核对，不能据此继续新的操作。':'本次原申请已在服务器保存，原理由和恢复编号仍保留。';
  if(!deferRefresh)try{await this.refreshState();}catch(error){this.error='原申请已保存，但当前进度尚未读回。'+userError(error);}
  this.emit();return record;
 }
 async acknowledge(row,value,options={}){
  await this.receipt(row,value);
  await this.finish(row,value.plan_id,value.result_status,{kind:'server_command_receipt',body_hash:value.body_hash},options);
  return value;
 }
 async lookup(row){
  try{return await this.receipt(row,await this.request(this.base+'/operations/'+row.request_id));}
  catch(error){if(error.status===404){this.notFound=true;return null;}throw error;}
 }
 async recoverAuthorization(row,options={}){
  const documents=await checkedDocuments(await this.request(this.base+'/preparations/'+row.plan_id+'/documents'),this.scope,row.plan_id,this.crypto);
  const doc=documents.documents.find(value=>value.evidence_ref.evidence_id===row.evidence_id);
  need(doc&&doc.evidence_ref.sha256===row.expected.sha256&&documents.model_pin.id===row.body.model_profile_id);
  const matches=doc.consent.grants.filter(grant=>grant.action==='evidence.cloud_authorize'&&grant.actor_id===this.scope.actor_id
   &&grant.model_profile_id===row.body.model_profile_id&&grant.evidence_sha256===row.expected.sha256
   &&grant.version_after===row.body.expected_version+1&&grant.reason===row.body.reason);
  if(matches.length!==1){this.notice='尚未核对到与原账号、理由和版本完全一致的授权回执；原申请保留，不自动重发。';this.documents=documents;this.emit();return null;}
  // Generic Evidence authorization has no server nonce. Only this exact real
  // cloud_authorize Audit acknowledges the original CAS body and human reason.
  const grant=matches[0];need(UUID.test(grant.audit_id)&&SHA.test(grant.audit_hash));
  await this.finish(row,row.plan_id,'available',{kind:'evidence_authorize_audit',audit_id:grant.audit_id,audit_hash:grant.audit_hash},options);
  return copy(grant);
 }
 async recoverOriginal(options={}){const row=this.storage.getItem(this.key+':pending')===null
   ?await this.storedOriginal(this.pending):await this.original();
  if(row.operation==='authorize')return this.recoverAuthorization(row,options);
  const receipt=await this.lookup(row);if(!receipt){this.notice='尚未找到原申请的服务器回执；原正文保留，可继续核对或明确按原编号提交。';this.emit();return null;}
  return this.acknowledge(row,receipt,options);
 }
 recover(){return this.operation(()=>this.locked(async()=>{
  if(this.pending||this.storage.getItem(this.key+':pending')!==null)return this.recoverOriginal();
  const retained=this.history.find(row=>row.result_status==='needs_check');need(retained,'当前没有待核对的原申请。');
  const row=this.checkedCommand(JSON.parse(this.storage.getItem(this.key+':original:'+retained.request_id)));
  need(await adviceHash(row.raw,this.crypto)===row.body_hash);const value=await this.lookup(row);need(value!==null);
  const updated={...retained,result_status:value.result_status};this.write(this.key+':received:'+row.request_id,updated);
  await this.reloadLocal();await this.refreshState();return value;
 }));}
 async sendOriginal(row){
  const actual=await this.original();need(adviceCanonical(actual)===adviceCanonical(row));
  if(this.storage.getItem(this.key+':pending')===null)this.write(this.key+':pending',row);
  try{await this.request(row.path,{method:'POST',body:row.raw});}
  catch(error){
   if([400,403,409,413,422].includes(error.status))this.write(this.key+':rejected:'+row.request_id,{status:error.status,message:userError(error)});
   if([400,403,409,413,422].includes(error.status))this.pendingRejected={status:error.status,message:userError(error)};
   this.notice='原申请已保留，结果尚待核对；不会创建新编号或自动重发模型。';throw error;
  }
  return this.recoverOriginal();
 }
 resendOriginal(){return this.operation(()=>this.locked(async()=>{
  const row=await this.original();
  if(row.operation!=='authorize'){const receipt=await this.lookup(row);if(receipt)return this.acknowledge(row,receipt);}
  else{const confirmed=await this.recoverAuthorization(row);if(confirmed)return confirmed;}
  return this.sendOriginal(row);
 }));}
 releaseRejected(){return this.operation(()=>this.locked(async()=>{
  const row=await this.original(),rejected=JSON.parse(this.storage.getItem(this.key+':rejected:'+row.request_id)||'null');
  need(rejected&&[400,403,409,413,422].includes(rejected.status),'原结果仍未知，不能更换编号或正文。');
  if(row.operation==='authorize'){
   if(await this.recoverAuthorization(row))return;
  }else{const result=await this.lookup(row);if(result)return this.acknowledge(row,result);}
  this.write(this.key+':not-applied:'+row.request_id,{original:row,rejection:rejected});
  const pending=this.storage.getItem(this.key+':pending');need(pending===null||pending===JSON.stringify(row));
  if(pending!==null)this.storage.removeItem(this.key+':pending');
  need(this.storage.getItem(this.key+':pending')===null);this.pending=null;this.pendingRejected=null;this.notFound=false;
  this.notice='被拒的原正文与理由已独立保留；请核对当前状态后修改输入，没有自动重新提交。';
  await this.refreshState();return this.view();
 }));}
 retryLocalSave(){this.check();const saved=this.persist();this.emit();return saved;}
 configureQuota(){return this.operation(()=>this.locked(async()=>{
  need(this.role==='admin','当前账号不能配置费用上限。');this.permit('can_configure_quota');
  await this.refreshState();this.permit('can_configure_quota');
  need(this.context.price?.status==='ready'&&['CNY','USD'].includes(this.context.price.currency),
   '请先更新并核对真实价格依据，再按其币种配置本次费用上限。');
  const body={request_id:this.crypto.randomUUID(),expected_goal_hash:this.context.goal_hash,
   expected_mature_head_hash:this.context.mature_head_hash,max_cost_minor_units:adviceMinorUnits(this.draft.quota_cost),
   currency:this.context.price.currency,validity_seconds:7200,reason:this.reason('quota_reason')};
  return this.sendOriginal(await this.stage('quota',body,{planId:null}));
 }));}
 registerPrice(){return this.operation(()=>this.locked(async()=>{
  need(this.role==='admin','当前账号不能更新价格依据。');this.permit('can_register_price');
  await this.refreshState();this.permit('can_register_price');
  need(this.context.model?.available===true&&this.context.model_pin,'当前发布模型尚未完整核对，未读取或登记替代模型价格。');
  const body={request_id:this.crypto.randomUUID(),model_pin:copy(this.context.model_pin),validity_seconds:3600,reason:this.reason('price_reason')};
  return this.sendOriginal(await this.stage('price',body,{planId:null}));
 }));}
 prepare(){return this.operation(()=>this.locked(async()=>{
  this.permit('can_prepare');await this.refreshState();this.permit('can_prepare');
  const source=this.draft.selected_document_source;
  need(source!==null&&this.context.document_sources.some(row=>adviceCanonical(row.selection)===adviceCanonical(source)),
   '请先确认本任务已批准的资料范围，未使用空资料包或替代来源。');
  const body={request_id:this.crypto.randomUUID(),expected_goal_hash:this.context.goal_hash,
   expected_mature_head_hash:this.context.mature_head_hash,document_source:copy(source),reason:this.reason('prepare_reason')};
  return this.sendOriginal(await this.stage('prepare',body,{planId:null}));
 }));}
 authorizeEvidence(id){return this.operation(()=>this.locked(async()=>{
  this.permit('can_authorize');const original=this.document(id),pin=this.acknowledgements[id];
  need(this.draft.consent_checked[id]===true&&pin===this.consentPin(original));
  const reason=this.draft.consent_reasons[id];need(text(reason)&&/\S/u.test(reason),'请填写允许发送这份原文的理由。');
  await this.refreshState();this.permit('can_authorize');const doc=this.document(id);
  need(doc.consent.status==='not_authorized'&&pin===this.consentPin(doc),'原资料或模型版本已变化，请重新核对并勾选。');
  const body={model_profile_id:this.documents.model_pin.id,expected_version:doc.version,reason};
  return this.sendOriginal(await this.stage('authorize',body,{evidenceId:id,expected:{sha256:doc.evidence_ref.sha256}}));
 }));}
 preparePreview(){return this.operation(()=>this.locked(async()=>{
  this.permit('can_preview');await this.refreshState();this.permit('can_preview');
  need(this.plan?.phase==='awaiting_consent'&&this.documents?.documents.every(doc=>doc.consent.status==='authorized'),
   '请先核对两份原文并取得实际模型授权，尚未生成发送预览。');
  const body={request_id:this.crypto.randomUUID(),expected_plan_version:this.plan.version,
   expected_goal_hash:this.context.goal_hash,expected_mature_head_hash:this.context.mature_head_hash,reason:this.reason('preview_reason')};
  return this.sendOriginal(await this.stage('preview',body));
 }));}
 startAnalysis(){return this.operation(()=>this.locked(async()=>{
  this.permit('can_start');const acknowledged=this.previewAcknowledgement;
  need(this.draft.preview_checked===true&&acknowledged===adviceCanonical(this.preview),'请先核对完整匿名全文、费用上限并明确勾选。');
  await this.refreshState();this.permit('can_start');
  need(this.plan?.phase==='awaiting_consent'&&this.preview?.latest_for_plan===true&&this.preview.start_endpoint_available===true
   &&this.preview.plan_version===this.plan.version&&acknowledged===adviceCanonical(this.preview),
   '原发送预览或执行条件已变化，请保留原稿并重新核对，未启动替代分析。');
  const body={request_id:this.crypto.randomUUID(),expected_plan_version:this.plan.version,
   expected_goal_hash:this.context.goal_hash,expected_mature_head_hash:this.context.mature_head_hash,
   preview_hash:this.preview.preview_hash,cloud_authorized:true,reason:this.reason('start_reason')};
  return this.sendOriginal(await this.stage('start',body));
 }));}
 review(){return this.operation(()=>this.locked(async()=>{
  this.permit('can_review');await this.refreshState();this.permit('can_review');
  need(this.plan?.human_review_available===true&&this.plan.phase==='ready_for_review'&&this.plan.result!==null
   &&this.documents!==null&&['accepted','rejected','needs_revision'].includes(this.draft.review_decision),
   '原回答或依据尚未完整读回，请先核对后明确选择审核结论。');
  const body={request_id:this.crypto.randomUUID(),expected_plan_version:this.plan.version,
   result_hash:await adviceHash(adviceCanonical(this.plan.result),this.crypto),decision:this.draft.review_decision,reason:this.reason('review_reason')};
  return this.sendOriginal(await this.stage('review',body));
 }));}
 cancel(){return this.operation(()=>this.locked(async()=>{
  need(['admin','operator'].includes(this.role)&&this.localSaved&&!this.pending&&!this.needsCheck);await this.refreshState();
  need(this.plan?.cancel_available===true,
   '当前分析已提交或状态需核查，不能撤回已发生的模型调用或退还未知费用。');
  const body={request_id:this.crypto.randomUUID(),expected_plan_version:this.plan.version,reason:this.reason('cancel_reason')};
  return this.sendOriginal(await this.stage('cancel',body));
 }));}
 storageChanged(){
  try{this.check();const raw=this.storage.getItem(this.key+':pending');
   if(raw!==null){const next=this.checkedCommand(JSON.parse(raw));if(this.pending)need(adviceCanonical(next)===adviceCanonical(this.pending));this.pending=next;}
   else need(!this.pending,'另一窗口改变了原申请记录，当前输入仍保留，请核对。');
  }catch(error){this.error=userError(error);}this.emit();
 }
 guard(){if(this.busy)return false;
  try{if(this.pending){
    // Independent originals form a bounded cold-start inventory even when a
    // shared pointer is missing; a missing/changed original cannot be left.
    need(this.storage.getItem(this.key+':original:'+this.pending.request_id)===JSON.stringify(this.pending));
   }if(this.denied||!this.active)return this.localSaved;
   this.check();return this.localSaved||this.persist();}catch(error){this.error=userError(error);this.localSaved=false;return false;}}
 dispose(){this.active=false;this.suspended=true;this.generation++;this.context=this.list=this.plan=this.documents=this.preview=null;this.onChange=()=>{};}
}
