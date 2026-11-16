import {stableStringify} from './content-signature.js';

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH=/^[0-9a-f]{64}$/;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const need=(value,message='原检索记录无法核对，输入和原请求保留，未发送。')=>{if(!value)throw Error(message);};
const copy=value=>JSON.parse(JSON.stringify(value));
const safe=value=>typeof value==='string'&&!/[\u0000\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
const same=(left,right)=>stableStringify(left)===stableStringify(right);
const memoryDrafts=new Map();
const memoryDraftScopes=new Map();
let memoryDraftWindow=null;
const protectMemoryDrafts=event=>{if(memoryDrafts.size){event.preventDefault();event.returnValue='';}};
function refreshMemoryDraftProtection(){
 const target=memoryDrafts.size&&typeof globalThis.window?.addEventListener==='function'?globalThis.window:null;
 if(memoryDraftWindow===target)return;
 memoryDraftWindow?.removeEventListener('beforeunload',protectMemoryDrafts);
 memoryDraftWindow=target;
 memoryDraftWindow?.addEventListener('beforeunload',protectMemoryDrafts);
}
export const candidateQueryStatusV139={queued:'已保存，等待本地检索',running:'正在检索已接收资料',sufficient:'本次检索完成',insufficient:'本次检索没有匹配资料',blocked_evidence:'原依据暂无法核对',failed:'检索未完成，原请求保留',cancelled:'本次检索已停止'};
export const candidateReviewTextV139=value=>value==='confirmed'?'已核查':value==='draft_saved'?'已有草稿':'待核查';
export const emptyCandidateQueryV139=()=>({field:'search_text',text:''});
export function candidateQueryScopeV139(account,instance,release,entity){
 const value={actor_id:account?.user_id,workspace_id:account?.workspace_id,instance_id:instance,release_id:release,entity_id:entity};
 need(Object.values(value).every(item=>typeof item==='string'&&UUID.test(item)),'请选择一个业务对象和已发行版本，再检索已接收资料。');return Object.freeze(value);
}
export const candidateQueryPrefixV139=scope=>'ga-native-candidate-query-v139:'+Object.values(scope).join(':')+':';
export function checkCandidateQueryFormV139(value){
 need(exact(value,['field','text'])&&['search_text','review_status'].includes(value.field)&&typeof value.text==='string'&&Array.from(value.text).length<=200,'检索文字无法保存，请保留输入并使用不超过200字的文字。');return copy(value);
}
export function candidateQueryCommandV139(scope,form,nonce){
 checkCandidateQueryFormV139(form);need(UUID.test(nonce)&&safe(form.text)&&form.text.trim(),'请填写有效的检索文字或人工核查状态；原输入保留，未发送。');
 return {request_id:nonce,release_id:scope.release_id,entity_id:scope.entity_id,field:form.field,text:form.text};
}
export function checkCandidateQueryCommandV139(body,scope){
 need(exact(body,['request_id','release_id','entity_id','field','text'])&&UUID.test(body.request_id)&&body.release_id===scope.release_id&&body.entity_id===scope.entity_id);
 candidateQueryCommandV139(scope,{field:body.field,text:body.text},body.request_id);return body;
}
export async function candidateQueryHashV139(body){
 const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(stableStringify(body)));return [...new Uint8Array(hash)].map(value=>value.toString(16).padStart(2,'0')).join('');
}
export function checkCandidateQueryViewV139(value,scope,original=null){
 need(exact(value,['schema','id','version','status','request_id','request','job_id','integrity_error','source_changed','not_authoritative','candidate_only','records','truncated'])&&value.schema==='native-candidate-query-view-v139'&&UUID.test(value.id)&&UUID.test(value.job_id)&&Number.isSafeInteger(value.version)&&value.version>=1&&Object.hasOwn(candidateQueryStatusV139,value.status));
 checkCandidateQueryCommandV139(value.request,scope);need(value.request_id===value.request.request_id&&(!original||same(value.request,original)));
 need((value.integrity_error===null||safe(value.integrity_error))&&typeof value.source_changed==='boolean'&&value.not_authoritative===true&&value.candidate_only===true&&typeof value.truncated==='boolean'&&Array.isArray(value.records));
 need(!(value.integrity_error||value.source_changed)||value.records.length===0,'原依据已变化，服务器不应继续返回旧候选值。');
 need(value.records.length<=200,'检索返回超过可核对范围，未显示部分结果。');
 for(const row of value.records){
  need(exact(row,['observation_id','entity_id','task_id','name','attributes','review_state','source_time_verified','physical_state_verified'])&&UUID.test(row.observation_id)&&row.entity_id===scope.entity_id&&UUID.test(row.task_id)&&safe(row.name)&&object(row.attributes)&&safe(row.review_state)&&row.source_time_verified===false&&row.physical_state_verified===false);
 }
 return value;
}
export function checkCandidateQueryListV139(value,scope){
 need(exact(value,['schema','queries'])&&value.schema==='native-candidate-query-list-v139'&&Array.isArray(value.queries)&&value.queries.length<=100);
 // The endpoint may retain other objects/releases. They are never displayed
 // as results for this selection, and do not authorize a new request.
 return value.queries.filter(row=>row?.request?.entity_id===scope.entity_id&&row?.request?.release_id===scope.release_id).map(row=>checkCandidateQueryViewV139(row,scope));
}
export function checkCandidateQueryLookupV139(value,scope,body){
 need(exact(value,['found','query'])&&typeof value.found==='boolean');
 if(!value.found){need(value.query===null);return null;}
 return checkCandidateQueryViewV139(value.query,scope,body);
}
function checkedDraftChannel(value){need(value===''||value==='object-state','候选输入的保存入口无法核对，原稿保留。');return value;}
function memoryDraftKey(scope,draftChannel=''){const channel=checkedDraftChannel(draftChannel);return candidateQueryPrefixV139(scope)+(channel?'draft-channel:'+channel+':':'');}
export function rememberCandidateQueryDraftV139(scope,form,draftChannel=''){
 const key=memoryDraftKey(scope,draftChannel),value=checkCandidateQueryFormV139(form),savedScope=copy(scope);
 memoryDrafts.set(key,value);memoryDraftScopes.set(key,savedScope);refreshMemoryDraftProtection();
}
export function rememberedCandidateQueryDraftV139(scope,draftChannel=''){const value=memoryDrafts.get(memoryDraftKey(scope,draftChannel));return value?copy(value):null;}
export function forgetCandidateQueryDraftV139(scope,draftChannel=''){const key=memoryDraftKey(scope,draftChannel);memoryDrafts.delete(key);memoryDraftScopes.delete(key);refreshMemoryDraftProtection();}
export function assertCandidateQueryDraftSavedV140(account,instanceId){
 if(!account)return true;
 for(const scope of memoryDraftScopes.values())if(scope.actor_id===account.user_id&&scope.workspace_id===account.workspace_id&&scope.instance_id===instanceId)
  throw Error('本实例的候选检索输入尚未完整保存在本机，请先返回原对象并重试保存，再切换或离开。');
 return true;
}

// The original wire is immutable. Drafts and shared active requests have
// separate slots, so a newly mounted empty form cannot replace an outbox.
export class CandidateQueryJournalV139{
 constructor(scope,{storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,draftChannel=''}={}){Object.assign(this,{scope,storage,session,locks});this.draftChannel=checkedDraftChannel(draftChannel);this.prefix=candidateQueryPrefixV139(scope);this.draftHeadKey=this.prefix+'draft-head'+(this.draftChannel?':'+this.draftChannel:'');this.owner=crypto.randomUUID();this.generation=0;this.tabReady=false;}
 write(store,key,raw){need(typeof raw==='string'&&raw.length<=16384);store.setItem(key,raw);need(store.getItem(key)===raw,'本机保存后未完整读回，输入保留，暂不发送。');}
 lock(callback){need(typeof this.locks?.request==='function','此浏览器缺少安全的多窗口协调能力；原记录可查，暂不能提交。');return this.locks.request(this.prefix+'lock',{mode:'exclusive'},callback);}
 canSend(){return this.tabReady&&typeof this.locks?.request==='function';}
 readOriginal(key){
  need(typeof key==='string'&&key.startsWith(this.prefix+'request:'));const raw=this.storage.getItem(key);need(typeof raw==='string'&&raw.length<=16384);const value=JSON.parse(raw);
  need(exact(value,['schema','scope','body','exact_body','body_hash'])&&value.schema==='native-candidate-query-request-v139'&&same(value.scope,this.scope)&&typeof value.exact_body==='string'&&HASH.test(value.body_hash));
  checkCandidateQueryCommandV139(value.body,this.scope);need(value.exact_body===stableStringify(value.body)&&key===this.prefix+'request:'+value.body.request_id);return {key,raw,value};
 }
 head(){const raw=this.storage.getItem(this.prefix+'active');if(raw===null)return null;const value=JSON.parse(raw);need(exact(value,['key','request_id'])&&UUID.test(value.request_id));const original=this.readOriginal(value.key);need(original.value.body.request_id===value.request_id);return {...original,headRaw:raw};}
 savedOriginal(){const raw=this.storage.getItem(this.prefix+'saved-head');if(raw===null)return null;const hint=JSON.parse(raw);need(exact(hint,['key','request_id','query_id'])&&UUID.test(hint.request_id)&&UUID.test(hint.query_id));const record=this.readOriginal(hint.key);need(record.value.body.request_id===hint.request_id);return record;}
 open(){
  const pending=this.head();let form=emptyCandidateQueryV139(),storage_error='';
  try{
   const pointer=this.session.getItem(this.draftHeadKey);
   if(pointer!==null){const head=JSON.parse(pointer);need(exact(head,['key','owner','generation'])&&UUID.test(head.owner)&&Number.isSafeInteger(head.generation)&&head.generation>=1&&head.key===this.prefix+'draft:'+head.owner+':'+head.generation%2);const raw=this.storage.getItem(head.key);need(typeof raw==='string');const row=JSON.parse(raw);need(exact(row,['schema','scope','owner','generation','form'])&&row.schema==='native-candidate-query-draft-v139'&&same(row.scope,this.scope)&&row.owner===head.owner&&row.generation===head.generation);form=checkCandidateQueryFormV139(row.form);}
  }catch{this.draftBlocked=true;storage_error='本窗口的输入恢复指针无法核对。原记录保留，仍可只核对共享原请求，暂不能覆盖输入或提交。';}
  const remembered=rememberedCandidateQueryDraftV139(this.scope,this.draftChannel);return {form:remembered||form,pending,saved:this.savedOriginal(),memory:!!remembered,storage_error};
 }
 save(form){
  form=checkCandidateQueryFormV139(form);need(!this.draftBlocked,'本窗口的原输入恢复指针待核对，原记录未覆盖。');this.session.getItem(this.draftHeadKey);const generation=this.generation+1,key=this.prefix+'draft:'+this.owner+':'+generation%2;
  const row={schema:'native-candidate-query-draft-v139',scope:this.scope,owner:this.owner,generation,form};this.write(this.storage,key,JSON.stringify(row));
  this.write(this.session,this.draftHeadKey,JSON.stringify({key,owner:this.owner,generation}));this.generation=generation;this.tabReady=true;forgetCandidateQueryDraftV139(this.scope,this.draftChannel);return form;
 }
 async install(body,authorize){
  checkCandidateQueryCommandV139(body,this.scope);need(this.canSend(),'本窗口尚未完整保存输入或缺少多窗口协调，暂不提交。');
  return this.lock(async()=>{
   await authorize();need(!this.head(),'另一窗口已有待核对的原请求；本页输入保留，请先核对原请求。');
   const value={schema:'native-candidate-query-request-v139',scope:this.scope,body:copy(body),exact_body:stableStringify(body),body_hash:await candidateQueryHashV139(body)};
   const key=this.prefix+'request:'+body.request_id,raw=JSON.stringify(value);need(this.storage.getItem(key)===null,'该原请求保存槽已经存在，未覆盖。');this.write(this.storage,key,raw);
   this.write(this.storage,this.prefix+'active',JSON.stringify({key,request_id:body.request_id}));return this.head();
  });
 }
 async withOriginal(record,authorize,callback){
  need(this.canSend(),'输入恢复指针或多窗口协调不可用，原请求保留，暂不重发。');
  return this.lock(async()=>{await authorize();const current=this.head();need(current&&current.key===record.key&&current.raw===record.raw,'原请求已被其他窗口核对或保存，未发送。');need(await candidateQueryHashV139(current.value.body)===current.value.body_hash);return callback(current);});
 }
 async settle(record,query,authorize){
  checkCandidateQueryViewV139(query,this.scope,record.value.body);
  return this.lock(async()=>{await authorize();const current=this.head();need(current&&current.key===record.key&&current.raw===record.raw,'原请求保存槽已变化，未结清。');need(await candidateQueryHashV139(current.value.body)===current.value.body_hash);
   this.write(this.storage,this.prefix+'saved:'+query.request_id,JSON.stringify({schema:'native-candidate-query-saved-hint-v139',query_id:query.id,request_id:query.request_id,body_hash:record.value.body_hash}));
   this.write(this.storage,this.prefix+'saved-head',JSON.stringify({key:record.key,request_id:query.request_id,query_id:query.id}));
   need(this.storage.getItem(this.prefix+'active')===current.headRaw);this.storage.removeItem(this.prefix+'active');need(this.storage.getItem(this.prefix+'active')===null,'原请求仍待本机核对，保存依据保留。');
  });
 }
}
