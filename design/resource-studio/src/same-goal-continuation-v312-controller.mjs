// Stage-one drafts only. Local storage contains human input and exact command
// envelopes, never the source evidence, model answer or a cached permission.
import {typedRequestJSON} from './typed-numbers-v132.mjs';
import {withScopedRequest} from './scope-request-budget-v314.mjs';

export const CONTINUATION_PREFIX='ga-same-goal-continuation-v312:';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA=/^[0-9a-f]{64}$/,FLAGS=['can_dispatch','execution_approved','goal_completion_verified','cloud_send_authorized'];
const FIELDS=['route','candidate_ref','selected_tools','purpose','work_description','expected_result','assignee_id','due_at'];
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v),copy=v=>JSON.parse(JSON.stringify(v));
const need=(v,message='原草案或申请暂不能核对，原输入保留。')=>{if(!v)throw Error(message);};
const positive=v=>Number.isSafeInteger(v)&&v>0;
const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const chars=(v,n)=>typeof v==='string'&&Array.from(v).length<=n;
const flags=v=>FLAGS.every(key=>v[key]===false);
const empty=()=>({route:null,candidate_ref:null,selected_tools:[],purpose:'',work_description:'',expected_result:'',assignee_id:null,due_at:null});
const blank=()=>({fields:empty(),due_text:'',due_edited:false,reason:'',withdraw_reason:''});
const reads=()=>({context:'idle',history:'idle',plan:'idle'});
export const continuationCanonical=typedRequestJSON;
export async function continuationHash(text,crypto=globalThis.crypto){
 need(crypto?.subtle,'浏览器不能核对原请求摘要，尚未提交。');
 return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))].map(v=>v.toString(16).padStart(2,'0')).join('');
}
export function continuationDueAt(text){
 need(typeof text==='string');if(!text)return null;
 const match=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(text);
 need(match,'请填写完整的北京时间，或清空可选期限。');
 const [year,month,day,hour,minute]=match.slice(1).map(Number),date=new Date(Date.UTC(year,month-1,day,hour,minute));
 need(year>=1900&&year<=9999&&date.getUTCFullYear()===year&&date.getUTCMonth()===month-1&&date.getUTCDate()===day&&date.getUTCHours()===hour&&date.getUTCMinutes()===minute,'期限不是有效的日期和时间，原输入保留。');
 return text+':00+08:00';
}
export function continuationDueText(value){
 if(value===null)return '';need(typeof value==='string'&&Number.isFinite(Date.parse(value))&&/(?:Z|[+-]\d{2}:\d{2})$/.test(value));
 return new Date(Date.parse(value)+8*3600000).toISOString().slice(0,16);
}
export function continuationHasContent(value){
 return ['purpose','work_description','expected_result'].some(k=>typeof value[k]==='string'&&value[k].trim())
  ||!!value.route||!!value.candidate_ref||value.selected_tools?.length>0||!!value.assignee_id||!!value.due_at;
}
function pin(value){need(exact(value,['id','content_hash','release_version'])&&UUID.test(value.id)&&SHA.test(value.content_hash)&&chars(value.release_version,200)&&value.release_version.length>0);return value;}
function draftFields(value){
 need(exact(value,FIELDS)&&[value.route,value.candidate_ref].every(v=>v===null||chars(v,200)&&v.length>0)
  &&chars(value.purpose,2000)&&chars(value.work_description,4000)&&chars(value.expected_result,2000)
  &&(value.assignee_id===null||UUID.test(value.assignee_id))&&Array.isArray(value.selected_tools)&&value.selected_tools.length<=6);
 value.selected_tools.forEach(pin);need(new Set(value.selected_tools.map(p=>p.id)).size===value.selected_tools.length);
 if(value.due_at!==null)continuationDueText(value.due_at);return copy(value);
}
function checkedContext(value,scope){
 need(value?.schema==='same-goal-continuation-context-v312'&&value.task_id===scope.task_id&&value.advice_plan_id===scope.advice_plan_id
  &&positive(value.task_version)&&SHA.test(value.source_hash)&&object(value.source)&&object(value.source.review)
  &&['accepted','rejected','needs_revision'].includes(value.source.review.decision)&&Array.isArray(value.source.limitations)
  &&value.source.origin_kind==='independent_advice'&&value.source.bridge_state==='not_bound'
  &&Array.isArray(value.candidates)&&Array.isArray(value.allowed_routes)&&Array.isArray(value.tools)&&Array.isArray(value.assignees)
  &&typeof value.permissions?.can_create==='boolean'&&typeof value.permissions?.can_preview==='boolean'&&Array.isArray(value.blocked_reasons)&&flags(value));
 need(value.candidates.length<=100&&value.tools.length<=1000&&value.assignees.length<=1000);
 for(const row of value.candidates)need(chars(row.candidate_ref,200)&&typeof row.proposal==='string');
 for(const row of value.tools){pin({id:row.id,content_hash:row.content_hash,release_version:row.release_version});need(typeof row.name==='string'&&typeof row.planning_available==='boolean');}
 for(const row of value.assignees)need(UUID.test(row.id)&&typeof row.name==='string');
 for(const row of value.blocked_reasons)need(typeof row.code==='string'&&typeof row.message==='string');
 return copy(value);
}
function checkedView(value,scope,id=null){
 need(value?.schema==='same-goal-continuation-view-v312'&&value.task_id===scope.task_id&&value.advice_plan_id===scope.advice_plan_id
  &&UUID.test(value.continuation_id)&&(!id||id===value.continuation_id)&&positive(value.version)&&['draft','withdrawn'].includes(value.phase)
  &&SHA.test(value.source_hash)&&value.source_ref?.advice_plan_id===scope.advice_plan_id&&value.source_ref?.source_hash===value.source_hash
  &&positive(value.revision?.version)&&SHA.test(value.revision.record_hash)&&UUID.test(value.revision.actor_id)
  &&typeof value.revision.reason==='string'&&typeof value.revision.saved_at==='string'&&Array.isArray(value.history)
  &&typeof value.permissions?.can_revise==='boolean'&&typeof value.permissions?.can_withdraw==='boolean'&&flags(value));
 draftFields(value.revision.draft);
 const versions=new Set();for(const row of value.history){need(positive(row.version)&&!versions.has(row.version)&&SHA.test(row.record_hash)&&UUID.test(row.actor_id)&&typeof row.saved_at==='string'&&typeof row.reason==='string');versions.add(row.version);if(Object.hasOwn(row,'draft'))draftFields(row.draft);}
 return copy(value);
}
function safeError(error){
 if(error?.status===401)return '登录已失效，原稿和原申请保留；请重新登录原工作区。';
 if(error?.status===403)return '当前账号无权完成此操作，原稿和原申请保留。';
 const text=typeof error?.message==='string'?error.message:'';
 if(!text||/Traceback|(?:TypeError|KeyError|AttributeError)|^\s*[\[{]|(?:secret|token|authorization|cookie)\s*[:=]/i.test(text))return '操作尚未核对完成，原稿和原申请保留，请读取原记录。';
 return text.replace(/\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/gi,'对应记录').replace(/\b[0-9a-f]{64}\b/gi,'原校验记录').slice(0,500);
}

export class SameGoalContinuationControllerV312{
 constructor({account,task,advicePlan,request,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,onChange=()=>{},beforeContinuationChange=()=>true}){
  this.scope={actor_id:account.user_id,workspace_id:account.workspace_id,task_id:task.id,release_id:task.instance_release_id,advice_plan_id:advicePlan.plan_id};
  need(Object.values(this.scope).every(v=>UUID.test(v))&&typeof request==='function');
  Object.assign(this,{storage,session,locks,crypto,onChange,beforeContinuationChange,role:account.role});
  this.key=CONTINUATION_PREFIX+continuationCanonical(this.scope);this.base='/workspace/tasks/'+task.id+'/business-advice/preparations/'+advicePlan.plan_id+'/continuations';
  this.csrf=session.getItem('ga-setup-csrf');this.active=true;this.suspended=false;this.denied=false;this.epoch=0;this.round=0;
  this.loading=true;this.busy=false;this.localSaved=false;this.error='';this.notice='';this.context=null;this.list=null;this.plan=null;this.preview=null;this.reads=reads();
  this.draft=blank();this.editId=null;this.selectedId=null;this.owner=null;this.pending=null;this.pendingRejected=null;this.needsCheck=false;this.unsettled=[];this.localDrafts=[];this.openPromise=null;
  this.requestAPI=(path,options)=>{
   const epoch=this.epoch;
   return withScopedRequest(()=>request(path,options),()=>{
    this._check(epoch);
    if(options?.method==='POST'&&path!==this.base+'/preview')this._requireChildSaved();
   });
  };
 }
 _requireChildSaved(){
  need(this.beforeContinuationChange(this.selectedId)===true,'审批与执行的本机输入尚未可靠保存。请先保存原输入或核对原申请。');
 }
 _check(epoch=this.epoch){
  need(this.active&&!this.suspended&&epoch===this.epoch,'此前页面已停止处理，原稿和原申请保留。');
  if(this.session.getItem('ga-setup-csrf-user')!==this.scope.actor_id||this.session.getItem('ga-setup-csrf-workspace')!==this.scope.workspace_id||this.session.getItem('ga-setup-csrf')!==this.csrf){this._deny(Error('账号或工作区已变化，原建议和草案已隐藏。'));}
  need(!this.denied,'当前会话已变化，原稿保留。');
 }
 _deny(error){this.denied=true;this.epoch++;this.context=this.list=this.plan=this.preview=null;this.reads=reads();this.error=safeError(error);this._emit();}
 _emit(){if(this.active)this.onChange(this.view());}
 view(){
  if(this.active&&!this.denied)try{this._check();}catch{}
  const hide=this.denied||!this.active;
  return {scopeKey:this.key,loading:this.loading,busy:this.busy,denied:this.denied,localSaved:this.localSaved,error:this.error,notice:this.notice,
   context:hide?null:copy(this.context),list:hide?null:copy(this.list),plan:hide?null:copy(this.plan),preview:hide?null:copy(this.preview),reads:copy(this.reads),
   draft:hide?null:copy(this.draft),editId:hide?null:this.editId,selectedId:hide?null:this.selectedId,pending:hide?null:copy(this.pending),
   pendingRejected:hide?null:copy(this.pendingRejected),needsCheck:this.needsCheck,unsettledCount:hide?0:this.unsettled.length,localDrafts:hide?[]:copy(this.localDrafts)};
 }
 async _identity(epoch=this.epoch){
  this._check(epoch);let me;try{me=await this.requestAPI('/me',{headers:this._headers()});}catch(error){this._check(epoch);if([401,403].includes(error.status))this._deny(error);throw error;}
  this._check(epoch);if(me?.user_id!==this.scope.actor_id||me?.workspace_id!==this.scope.workspace_id||me?.role!==this.role){this._deny(Error('账号、工作区或权限已变化，原建议和草案已隐藏。'));throw Error(this.error);}return me;
 }
 _headers(){return {'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.csrf};}
 async _request(path,options={},epoch=this.epoch){
  await this._identity(epoch);this._check(epoch);let result;
  try{result=await this.requestAPI(path,{...options,headers:{...this._headers(),...(options.headers||{})}});}
  catch(error){this._check(epoch);if(error.status===401||error.status===403&&(!options.method||options.method==='GET'))this._deny(error);throw error;}
  this._check(epoch);await this._identity(epoch);this._check(epoch);return result;
 }
 _put(key,value,immutable=false){
  const text=continuationCanonical(value),old=this.storage.getItem(key);
  need(!immutable||old===null||old===text,'本机原请求已有不同正文，未覆盖。');
  this.storage.setItem(key,text);need(this.storage.getItem(key)===text,'本机保存未读回，尚未提交。');
 }
 _keys(prefix){need(Number.isSafeInteger(this.storage.length)&&this.storage.length<=4096,'本机原记录过多，未把截断库存当作空记录。');const keys=[];for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(prefix))keys.push(key);}return keys;}
 _localRow(){return {schema:'same-goal-continuation-local-draft-v312',scope:this.scope,owner:this.owner,draft:this.draft,edit_id:this.editId,selected_id:this.selectedId};}
 _saveDraft(){
  try{need(this.owner);this._put(this.key+':draft:'+this.owner,this._localRow());this.localSaved=true;return true;}
  catch(error){this.localSaved=false;this.error=safeError(error);return false;}
 }
 _checkLocal(row){need(row?.schema==='same-goal-continuation-local-draft-v312'&&continuationCanonical(row.scope)===continuationCanonical(this.scope)&&UUID.test(row.owner));
  need(exact(row.draft,['fields','due_text','reason','withdraw_reason'])||exact(row.draft,['fields','due_text','due_edited','reason','withdraw_reason']));
  need(chars(row.draft.reason,2000)&&chars(row.draft.withdraw_reason,2000)&&chars(row.draft.due_text,32));draftFields(row.draft.fields);
  // Legacy local-only input has no edit bit. Preserve its exact original ISO
  // when the displayed minute is unchanged; keep any distinct typed text too.
  if(!Object.hasOwn(row.draft,'due_edited'))row.draft.due_edited=row.draft.due_text!==continuationDueText(row.draft.fields.due_at);
  need(typeof row.draft.due_edited==='boolean');
  need([row.edit_id,row.selected_id].every(v=>v===null||UUID.test(v)));return row;
 }
 _inventory(){
  const originalKeys=this._keys(this.key+':original:');need(originalKeys.length<=100,'本机原申请需要核对，未建立重复草案。');const unresolved=[];
  if(this.pending)need(originalKeys.includes(this.key+':original:'+this.pending.request_id),'原申请正文已无法读回，未将丢失原件当作从未提交。');
  const pointerRaw=this.storage.getItem(this.key+':pending');let pointer=null;
  if(pointerRaw!==null){pointer=JSON.parse(pointerRaw);need(exact(pointer,['schema','request_id','body_hash'])&&pointer.schema==='same-goal-continuation-local-pending-v312'&&UUID.test(pointer.request_id)&&SHA.test(pointer.body_hash)
    &&originalKeys.includes(this.key+':original:'+pointer.request_id),'本机原申请原件缺失，未创建替代申请。');}
  for(const key of originalKeys){const raw=this.storage.getItem(key),row=JSON.parse(raw);need(row.schema==='same-goal-continuation-local-command-v312'&&continuationCanonical(row.scope)===continuationCanonical(this.scope)
    &&UUID.test(row.request_id)&&SHA.test(row.body_hash)&&row.body.request_id===row.request_id&&['create','revise','withdraw'].includes(row.operation)
    &&key===this.key+':original:'+row.request_id&&raw===continuationCanonical(row));
   need(row.continuation_id===null&&row.operation==='create'||UUID.test(row.continuation_id)&&row.operation!=='create');
   const path=row.operation==='create'?this.base:this.base+'/'+row.continuation_id+(row.operation==='revise'?'/revisions':'/withdraw');need(row.path===path,'原申请路径不属于这份任务草案，未续传。');
   if(row.operation==='withdraw')need(exact(row.body,['request_id','expected_continuation_version','reason'])&&positive(row.body.expected_continuation_version));
   else{need(exact(row.body,['request_id','expected_task_version','source_hash','draft','reason','preview_hash',...(row.operation==='revise'?['expected_continuation_version']:[])])&&positive(row.body.expected_task_version)&&SHA.test(row.body.source_hash)&&SHA.test(row.body.preview_hash));draftFields(row.body.draft);if(row.operation==='revise')need(positive(row.body.expected_continuation_version));}
   need(chars(row.body.reason,2000)&&row.body.reason.trim());
   if(pointer?.request_id===row.request_id)need(pointer.body_hash===row.body_hash);
   const saved=this.storage.getItem(this.key+':settled:'+row.request_id);let state=null;
   if(saved!==null){state=JSON.parse(saved);need(exact(state,['schema','request_id','body_hash','state','receipt'])&&state.schema==='same-goal-continuation-local-disposition-v312'&&state.request_id===row.request_id&&state.body_hash===row.body_hash&&['received','needs_check','rejected'].includes(state.state));
    if(state.state==='rejected'){const rawRefusal=this.storage.getItem(this.key+':rejected-response:'+row.request_id);need(state.receipt===null&&rawRefusal!==null);const refusal=JSON.parse(rawRefusal);need(refusal.request_id===row.request_id&&refusal.body_hash===row.body_hash&&[400,403,409,413,422].includes(refusal.status));}
    else need(exact(state.receipt,['operation','continuation_id','body_hash','result_status'])&&state.receipt.operation===row.operation&&UUID.test(state.receipt.continuation_id)&&(!row.continuation_id||state.receipt.continuation_id===row.continuation_id)&&state.receipt.body_hash===row.body_hash&&state.receipt.result_status===(state.state==='received'?'available':'needs_check'));
   }
   if(!state||state.state==='needs_check')unresolved.push({row,state});
  }
  this.unsettled=unresolved;const selected=unresolved.find(v=>v.row.request_id===this.pending?.request_id)||unresolved[0];this.pending=selected?copy(selected.row):null;
  this.needsCheck=unresolved.some(v=>v.state?.state==='needs_check');
  this.pendingRejected=null;if(this.pending){const refused=this.storage.getItem(this.key+':rejected-response:'+this.pending.request_id);if(refused!==null){const value=JSON.parse(refused);need(exact(value,['request_id','body_hash','status'])&&value.request_id===this.pending.request_id&&value.body_hash===this.pending.body_hash&&[400,403,409,413,422].includes(value.status));this.pendingRejected={request_id:value.request_id,status:value.status};}}
  this.localDrafts=this._keys(this.key+':draft:').map(key=>{const row=this._checkLocal(JSON.parse(this.storage.getItem(key)));return {key,owner:row.owner,editing:row.edit_id!==null,has_content:continuationHasContent(row.draft.fields)||!!row.draft.reason.trim()};}).filter(row=>row.owner!==this.owner&&row.has_content);
 }
 async _load(){
  const last=this.session.getItem(this.key+':tab-draft');
  if(last){need(UUID.test(last));const raw=this.storage.getItem(this.key+':draft:'+last);need(raw!==null,'本机原稿指向的内容缺失，未将它替换成空白稿。');const row=this._checkLocal(JSON.parse(raw));this.draft=copy(row.draft);this.editId=row.edit_id;this.selectedId=row.selected_id;}
  this.owner=this.crypto.randomUUID();need(UUID.test(this.owner));need(this._saveDraft());this.session.setItem(this.key+':tab-draft',this.owner);need(this.session.getItem(this.key+':tab-draft')===this.owner);this._inventory();
  for(const {row}of this.unsettled)need(await continuationHash(continuationCanonical(row.body),this.crypto)===row.body_hash,'原申请正文与摘要不一致，未重新提交。');
 }
 open(){if(!this.openPromise)this.openPromise=(async()=>{try{await this._load();await this._identity();if(this.pending)await this._recover(false);await this.refresh();}catch(error){this.error=safeError(error);this.loading=false;this._emit();}})();return this.openPromise;}
 async refresh(){
  this._check();need(!this.busy,'当前申请尚在保存，请先核对原号。');const epoch=this.epoch,round=++this.round;this.loading=true;this.error='';this.context=this.list=this.plan=this.preview=null;this.reads=reads();
  const check=()=>{this._check(epoch);need(round===this.round,'此前读取已由新一轮替换。');};
  const read=async(name,path,validate)=>{this.reads[name]='loading';this._emit();try{const raw=await this._request(path,{},epoch);check();this[name==='history'?'list':name]=validate(raw);this.reads[name]='ready';this._emit();}
   catch(error){try{check();this.reads[name]='failed';this.error=safeError(error);this._emit();}catch{}}};
  this._emit();const work=[read('context',this.base+'/context',value=>checkedContext(value,this.scope)),read('history',this.base,value=>{
   need(value?.schema==='same-goal-continuation-view-v312/list'&&value.task_id===this.scope.task_id&&value.advice_plan_id===this.scope.advice_plan_id&&Array.isArray(value.records));value.records.forEach(row=>checkedView(row,this.scope));return copy(value);})];
  if(this.selectedId)work.push(read('plan',this.base+'/'+this.selectedId,value=>checkedView(value,this.scope,this.selectedId)));
  await Promise.allSettled(work);try{check();this._inventory();this.loading=false;this._emit();}catch{}
 }
 updateDraft(patch){
  this._check();need(!this.busy&&!this.pending&&!this.needsCheck,'原申请尚未核对，不能更换它的正文。');need(object(patch));
  const next=copy(this.draft);for(const[key,value]of Object.entries(patch)){
   need([...FIELDS,'due_text','reason','withdraw_reason'].includes(key));if(FIELDS.includes(key))next.fields[key]=copy(value);else next[key]=value;
  }
  if(Object.hasOwn(patch,'due_at')){next.due_text=continuationDueText(next.fields.due_at);next.due_edited=false;}
  if(Object.hasOwn(patch,'due_text'))next.due_edited=true;
  draftFields(next.fields);need(chars(next.reason,2000)&&chars(next.withdraw_reason,2000)&&chars(next.due_text,32));
  if(this.editId&&this.context?.permissions.can_create!==true&&this.role!=='admin'&&Object.hasOwn(patch,'assignee_id'))need(patch.assignee_id===this.plan?.revision.draft.assignee_id,'受指派办理人不能将草案改派给他人。');
  this.draft=next;this.preview=null;this.notice='';this._saveDraft();this._emit();
 }
 async selectContinuation(id){this._check();this._requireChildSaved();need(UUID.test(id)&&!this.busy&&!this.loading&&this.guard());this.selectedId=id;this._saveDraft();await this.refresh();}
 _replace(fields,editId,reason=''){
  this._requireChildSaved();
  need(this.guard()&&!this.pending&&!this.needsCheck&&!this.busy);this.owner=this.crypto.randomUUID();this.draft={fields:draftFields(fields),due_text:continuationDueText(fields.due_at),due_edited:false,reason,withdraw_reason:''};this.editId=editId;
  this.preview=null;need(this._saveDraft());this.session.setItem(this.key+':tab-draft',this.owner);this._inventory();this._emit();
 }
 continueEditing(){this._check();need(this.plan?.permissions.can_revise===true&&this.plan.phase==='draft');this._replace(this.plan.revision.draft,this.plan.continuation_id);this.notice='原本机稿已另行保留；正在编辑这份已保存草案，尚未保存新版本。';this._emit();}
 restoreRevision(version){
  this._check();need(!this.loading&&this.reads.context==='ready'&&this.context&&this.plan&&positive(version));
  const revisions=this.plan.history.filter(row=>row.version===version);need(revisions.length===1&&Object.hasOwn(revisions[0],'draft'),'此版本的原稿正文尚未读回，未用当前版本替代。');
  const revision=revisions[0],revise=this.plan.phase==='draft'&&this.plan.permissions.can_revise===true;
  need(revise||this.context.permissions.can_create===true&&this.context.source.review.decision!=='rejected','当前只能查看此版本；没有修改原草案或另存新草案的权限。');
  if(revise&&this.role!=='admin'&&this.context.permissions.can_create!==true)need(revision.draft.assignee_id===this.scope.actor_id&&this.plan.revision.draft.assignee_id===this.scope.actor_id,'此旧版本指派的是其他办理人，请原业务负责人或管理员处理；未自动改派。');
  this._replace(revision.draft,revise?this.plan.continuation_id:null,revision.reason);
  this.notice='已保留当前本机稿，复制第'+version+'版原文和原保存说明到编辑区。请核对本次说明；保存将'+(revise?'新增修订版本':'另建草案')+'，原版本和撤回状态均不改写。';this._emit();
 }
 newDraft(){this._check();need(this.context?.permissions.can_create===true&&this.context.source.review.decision!=='rejected');this._replace(empty(),null);this.notice='已保留原本机稿，当前是另一份尚未保存的草案。';this._emit();}
 async restoreDraft(key){this._check();need(this.localDrafts.some(row=>row.key===key));const row=this._checkLocal(JSON.parse(this.storage.getItem(key)));this._replace(row.draft.fields,row.edit_id,row.draft.reason);this.draft.due_text=row.draft.due_text;this.draft.due_edited=row.draft.due_edited;this.draft.withdraw_reason=row.draft.withdraw_reason;this.selectedId=row.edit_id||row.selected_id;this._saveDraft();this._emit();await this.refresh();}
 _ready(){this._check();need(!this.loading&&!this.busy&&this.localSaved&&!this.pending&&!this.needsCheck&&this.unsettled.length===0&&this.reads.context==='ready'&&this.context,'先完成本轮原记录读取与本机保存，再提交草案。');}
 _fields(){const value=copy(this.draft.fields);if(this.draft.due_edited)value.due_at=continuationDueAt(this.draft.due_text);return draftFields(value);}
 _previewRequest(){const fields=this._fields();need(continuationHasContent(fields),'请至少写一项办理内容或主动选择一项安排，尚未创建空白草案。');const reason=this.draft.reason.trim()?this.draft.reason:this.editId?'系统记录：保存草案新版本，原来源保留':'系统记录：保存办理草案，尚未审批或执行';return {request_id:this.crypto.randomUUID(),expected_task_version:this.context.task_version,source_hash:this.context.source_hash,draft:fields,reason};}
 async preparePreview(){
  this._ready();need(this.context.permissions.can_preview===true,'当前不能核对新草案，原稿保留。');const epoch=this.epoch,body=this._previewRequest();this.busy=true;this.error='';this.preview=null;this._emit();
  try{const hash=await continuationHash(continuationCanonical(body),this.crypto);this._check(epoch);this._put(this.key+':preview-original:'+body.request_id,{schema:'same-goal-continuation-local-preview-v312',scope:this.scope,body,body_hash:hash},true);
   const value=await this._request(this.base+'/preview',{method:'POST',body:continuationCanonical(body)},epoch);
   need(value?.schema==='same-goal-continuation-preview-v312'&&value.task_id===this.scope.task_id&&value.advice_plan_id===this.scope.advice_plan_id&&value.task_version===body.expected_task_version&&value.source_hash===body.source_hash&&SHA.test(value.preview_hash)&&flags(value)&&Array.isArray(value.missing_fields));
   draftFields(value.normalized_draft);need(continuationCanonical(value.normalized_draft)===continuationCanonical(body.draft),'服务返回了不同草案，未将它用作保存依据。');
   const payload=copy(value);delete payload.preview_hash;need(await continuationHash(continuationCanonical(payload),this.crypto)===value.preview_hash,'草案预览摘要不一致，尚未保存。');this._check(epoch);this.preview=copy(value);this.notice='草案内容已核对；缺少的可选办理信息仍可留空保存，尚未批准或派发。';
  }catch(error){this.error=safeError(error);}finally{if(this.active&&!this.denied&&epoch===this.epoch){this.busy=false;this._emit();}}
 }
 _disposition(original,state,receipt=null){this._put(this.key+':settled:'+original.request_id,{schema:'same-goal-continuation-local-disposition-v312',request_id:original.request_id,body_hash:original.body_hash,state,receipt:receipt?{operation:receipt.operation,continuation_id:receipt.continuation_id,body_hash:receipt.body_hash,result_status:receipt.result_status}:null});if(state!=='needs_check'){const raw=this.storage.getItem(this.key+':pending');if(raw&&JSON.parse(raw).request_id===original.request_id)this.storage.removeItem(this.key+':pending');}this._inventory();}
 async _receipt(original){
  need(await continuationHash(continuationCanonical(original.body),this.crypto)===original.body_hash,'本机原申请已变化，尚未续传。');const value=await this._request(this.base+'/operations/'+original.request_id);
  need(value?.schema==='same-goal-continuation-receipt-v312'&&value.found===true&&value.operation===original.operation&&value.task_id===this.scope.task_id&&value.advice_plan_id===this.scope.advice_plan_id&&value.actor_id===this.scope.actor_id
   &&value.request_id===original.request_id&&value.body_hash===original.body_hash&&UUID.test(value.continuation_id)&&(!original.continuation_id||value.continuation_id===original.continuation_id)&&['available','needs_check'].includes(value.result_status));
  if(value.result_status==='needs_check'){need(value.result===null);this._disposition(original,'needs_check',value);this.notice='原申请已保存，结果原件仍需核对；请读取原号，暂不提交另一份替代草案。';return false;}
  const plan=checkedView(value.result,this.scope,value.continuation_id);
  this._requireChildSaved();
  this._disposition(original,'received',value);this.pendingRejected=null;this.selectedId=plan.continuation_id;this.plan=plan;this.preview=null;if(original.operation!=='withdraw')this.editId=plan.continuation_id;this._saveDraft();this.notice=original.operation==='withdraw'?'撤回已保存，原草案和修改记录仍保留。':'草案已保存，可核对适用的审批与执行入口。';return true;
 }
 async _recover(show=true){
  this._inventory();if(!this.pending){if(show)this.notice='本机没有待核对的原申请；仍可读取后台草案。';return true;}
  try{return await this._receipt(this.pending);}catch(error){if(error.status===404){if(show)this.notice='后台尚未找到这份原申请，可显式续传同一编号和完整正文。';return false;}throw error;}
 }
 async _locked(run){need(typeof this.locks?.request==='function','浏览器暂不能安全协调多个窗口，原稿已保留，尚未提交。');return this.locks.request(this.key+':write',{mode:'exclusive'},run);}
 async _postOriginal(original){
  this._requireChildSaved();
  try{await this._request(original.path,{method:'POST',body:continuationCanonical(original.body)});}
  catch(error){if([400,403,409,413,422].includes(error.status)){this._put(this.key+':rejected-response:'+original.request_id,{request_id:original.request_id,body_hash:original.body_hash,status:error.status},true);this.pendingRejected={request_id:original.request_id,status:error.status};}throw error;}
  await this._receipt(original);
 }
 async _write(operation,body,id=null){
  this._requireChildSaved();
  const epoch=this.epoch;this.busy=true;this.error='';this._emit();
  try{await this._locked(async()=>{this._check(epoch);this._inventory();need(this.unsettled.length===0,'另一个窗口的原申请尚未核对，未创建重复申请。');
   const path=operation==='create'?this.base:this.base+'/'+id+(operation==='revise'?'/revisions':'/withdraw');
   const original={schema:'same-goal-continuation-local-command-v312',scope:this.scope,owner:this.owner,operation,continuation_id:id,request_id:body.request_id,path,body,body_hash:await continuationHash(continuationCanonical(body),this.crypto)};
   this._check(epoch);this._requireChildSaved();need(this._saveDraft());this._put(this.key+':original:'+body.request_id,original,true);this._put(this.key+':pending',{schema:'same-goal-continuation-local-pending-v312',request_id:body.request_id,body_hash:original.body_hash});this._inventory();this._emit();await this._postOriginal(original);
  });}catch(error){if(this.active&&!this.denied&&epoch===this.epoch)this.error=safeError(error);}
  finally{if(this.active&&!this.denied&&epoch===this.epoch){this.busy=false;this._emit();}}
 }
 async save(){
  this._ready();const revise=this.editId!==null;need(revise?this.plan?.continuation_id===this.editId&&this.plan?.permissions.can_revise===true:this.context.permissions.can_create===true,'当前没有保存这份草案的权限，原稿保留。');
  need(this.context.source.review.decision!=='rejected'&&this.preview,'请先核对当前草案内容，尚未保存。');
  const body=this._previewRequest();need(this.preview.task_version===body.expected_task_version&&this.preview.source_hash===body.source_hash&&continuationCanonical(body.draft)===continuationCanonical(this.preview.normalized_draft),'草案或来源已变化，请重新核对草案。');
  body.preview_hash=this.preview.preview_hash;if(revise)body.expected_continuation_version=this.plan.version;
  await this._write(revise?'revise':'create',body,this.editId);
 }
 async withdraw(){this._ready();need(this.plan?.permissions.can_withdraw===true&&this.draft.withdraw_reason.trim(),'请核对撤回权限并填写撤回说明。');await this._write('withdraw',{request_id:this.crypto.randomUUID(),expected_continuation_version:this.plan.version,reason:this.draft.withdraw_reason},this.plan.continuation_id);}
 async recover(){this._check();need(!this.busy&&!this.loading);this.busy=true;this.error='';this._emit();try{await this._locked(()=>this._recover());}catch(error){this.error=safeError(error);}finally{if(this.active&&!this.denied){this.busy=false;this._emit();}}}
 async resendOriginal(){
  this._check();need(!this.busy&&!this.loading);this.busy=true;this.error='';this._emit();
  try{await this._locked(async()=>{this._inventory();need(this.pending&&!this.needsCheck);const original=copy(this.pending);if(!await this._recover(false)){need(!this.needsCheck);await this._postOriginal(original);}});}
  catch(error){this.error=safeError(error);}finally{if(this.active&&!this.denied){this.busy=false;this._emit();}}
 }
 async releaseRejected(){
  this._check();need(!this.busy&&!this.loading&&this.pendingRejected&&this.pendingRejected.request_id===this.pending?.request_id,'只有明确被拒的原申请才能在核对后恢复编辑。');this.busy=true;this._emit();
  try{await this._locked(async()=>{this._inventory();const original=copy(this.pending);try{await this._receipt(original);return;}catch(error){if(error.status!==404)throw error;}
   this._disposition(original,'rejected');this.pendingRejected=null;this.preview=null;this.notice='被拒的原申请和完整正文已保留，可修改原稿并重新核对。';});}
  catch(error){this.error=safeError(error);}finally{if(this.active&&!this.denied){this.busy=false;this._emit();}}
 }
 retryLocalSave(){this._check();this._saveDraft();this._emit();}
 storageChanged(){if(!this.active||this.suspended)return;try{this._check();this._inventory();}catch(error){this.error=safeError(error);this.localSaved=false;}this._emit();}
 guard(){try{this._check();if(this.busy||!this.owner||!this._saveDraft())return false;this._requireChildSaved();this._inventory();for(const{row}of this.unsettled)need(this.storage.getItem(this.key+':original:'+row.request_id)===continuationCanonical(row));return true;}catch{return false;}}
 dispose(){this.active=false;this.suspended=true;this.epoch++;this.round++;this.context=this.list=this.plan=this.preview=null;this.onChange=()=>{};}
}
