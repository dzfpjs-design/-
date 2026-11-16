import {enumerateGraphSourceDeliveryRecovery,readGraphSourceDeliveryRecoveryCandidate,GRAPH_SOURCE_DELIVERY_RECOVERY_MAX_RECORD_LENGTH as MAX} from './graph-source-delivery-recovery-v124.mjs';
import {enumerateGraphSourceDeliveryRetirementRecovery,readGraphSourceDeliveryRetirementRecoveryCandidate} from './graph-source-delivery-retirement-recovery-v125.mjs';

const BASE='/setup/graph-event-producers',UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,SHA=/^[a-f0-9]{64}$/;
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x),keys=(x,names)=>object(x)&&Object.keys(x).sort().join(',')===names.split(',').sort().join(',');
const integer=(x,min=1)=>Number.isSafeInteger(x)&&x>=min;
const safe=x=>typeof x==='string'&&!/[\u0000]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(x);
const text=(x,max=1000)=>safe(x)&&x.trim().length>0&&[...x].length<=max;
const need=(value,message='上次恢复操作与保存结果暂无法核对，原输入和原请求保留。')=>{if(!value)throw Object.assign(Error(message),{code:'DELIVERY_INPUT'});};
const clone=x=>JSON.parse(JSON.stringify(x)),normalize=x=>Array.isArray(x)?x.map(normalize):object(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,normalize(x[k])])):x;
const same=(a,b)=>JSON.stringify(normalize(a))===JSON.stringify(normalize(b));
const iso=x=>typeof x==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(x)&&Number.isFinite(Date.parse(x));
function scopeValue(s){need(keys(s,'actor_id,workspace_id')&&UUID.test(s.actor_id)&&UUID.test(s.workspace_id),'请先核对当前账号与工作区。');return clone(s);}
function retryBody(body){need(keys(body,'request_id,job_version,reason')&&UUID.test(body.request_id)&&integer(body.job_version)&&safe(body.reason)&&[...body.reason].length>=1&&[...body.reason].length<=1000);return body;}
export async function deliveryRetryCommandHash(scope,intentID,body,crypto=globalThis.crypto){
 scopeValue(scope);need(UUID.test(intentID));retryBody(body);const value={scope:'graph-event-retry-v1',workspace_id:scope.workspace_id,actor_id:scope.actor_id,intent_id:intentID,command:body};
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(normalize(value))));return Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join('');
}
export function validDeliveryRetirementReason(value){return safe(value)&&[...value].length>=1&&[...value].length<=1000&&/[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u.test(value);}
function retirementBody(body){need(keys(body,'schema,request_id,original,reason')&&body.schema==='graph-event-intent-retry-retirement-v1'&&UUID.test(body.request_id)&&validDeliveryRetirementReason(body.reason));retryBody(body.original);need(body.request_id!==body.original.request_id);return body;}
async function hashValue(value,crypto){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(normalize(value))));return Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join('');}
export async function deliveryRetirementCommandHash(scope,intentID,body,crypto=globalThis.crypto){scopeValue(scope);need(UUID.test(intentID));retirementBody(body);return hashValue({schema:'graph-event-intent-retry-retirement-command-hash-v1',workspace_id:scope.workspace_id,actor_id:scope.actor_id,intent_id:intentID,command:body},crypto);}
function retirementDrafts(value){need(object(value));for(const [intent,byNonce] of Object.entries(value)){need(UUID.test(intent)&&object(byNonce));for(const [nonce,reason] of Object.entries(byNonce))need(UUID.test(nonce)&&safe(reason)&&[...reason].length<=20000);}return clone(value);}
function sameOriginal(retirement,pending){return same(retirement.scope,pending.scope)&&retirement.intent_id===pending.intent_id&&retirement.producer_id===pending.producer_id&&retirement.original_window_id===pending.window_id&&same(retirement.body.original,pending.body);}
function samePendingOriginal(left,right){return same(left.scope,right.scope)&&left.intent_id===right.intent_id&&left.producer_id===right.producer_id&&left.window_id===right.window_id&&left.command_hash===right.command_hash&&left.exact_body===right.exact_body&&same(left.body,right.body);}
function checkedRetirementReasonRecord(raw,key,prefix,scope){
 need(typeof raw==='string'&&raw.length<=MAX);const r=JSON.parse(raw),slot=r.schema==='graph-delivery-retirement-reason-slot-v125';
 need(keys(r,slot?'schema,scope,window_id,owner,generation,values':'schema,scope,window_id,owner,values')&&(slot||r.schema==='graph-delivery-retirement-reason-entry-v125')
  &&same(r.scope,scope)&&UUID.test(r.owner)&&UUID.test(r.window_id)&&(!slot||integer(r.generation))&&key===prefix+(slot?'slot:'+r.owner+':'+(r.generation%2):'entry:'+r.owner));
 retirementDrafts(r.values);return r;
}
export function inspectDeliveryRecord(r,scope,{detail=false}={}){
 scopeValue(scope);need(keys(r,'schema,intent_id,producer_id,actor_id,intent_hash,source_label,object,observation_id,observed_at,candidate_only,source_time_verified,physical_state_verified,intent_status,admission,delivery,action,result')
  &&r.schema==='graph-event-delivery-record-v1'&&UUID.test(r.intent_id)&&UUID.test(r.producer_id)&&r.actor_id===scope.actor_id&&SHA.test(r.intent_hash)
  &&text(r.source_label,200)&&keys(r.object,'id,name')&&UUID.test(r.object.id)&&text(r.object.name,300)&&UUID.test(r.observation_id)
  &&(r.observed_at===null||iso(r.observed_at))&&typeof r.candidate_only==='boolean'&&r.source_time_verified===false&&r.physical_state_verified===false&&['pending','admitted'].includes(r.intent_status));
 if(r.admission!==null)need(keys(r.admission,'id,sequence,admitted_at')&&UUID.test(r.admission.id)&&integer(r.admission.sequence)&&iso(r.admission.admitted_at));
 need((r.intent_status==='admitted')===(r.admission!==null));
 if(r.delivery!==null)need(keys(r.delivery,'job_id,status,version,attempts')&&UUID.test(r.delivery.job_id)&&['queued','running','failed','blocked','succeeded','cancelled'].includes(r.delivery.status)&&integer(r.delivery.version)&&integer(r.delivery.attempts,0));
 const a=r.action;need(keys(a,'allowed,kind,reason_code,label,remaining_retries')&&typeof a.allowed==='boolean'&&(a.kind===null||['retry_admission','reconcile_receipt'].includes(a.kind))
  &&['in_progress','missing_job','no_permission','receipt_available','already_admitted','retry_limit','retry_available','registration_stopped','no_execution_permission','source_unavailable'].includes(a.reason_code)&&text(a.label,1000)&&integer(a.remaining_retries,0)&&a.remaining_retries<=32);
 need(a.allowed===(a.kind!==null));
 if(a.allowed)need(r.delivery!==null&&['failed','blocked'].includes(r.delivery.status)&&(a.kind==='reconcile_receipt'?a.reason_code==='receipt_available'&&r.intent_status==='admitted':a.reason_code==='retry_available'&&r.intent_status==='pending'&&a.remaining_retries>0));
 if(r.result!==null)need(detail&&keys(r.result,'schema,value')&&object(r.result.schema));
 return r;
}
function drafts(value){need(object(value));for(const [id,v] of Object.entries(value))need(UUID.test(id)&&keys(v,'producer_id,reason')&&UUID.test(v.producer_id)&&safe(v.reason)&&[...v.reason].length<=20000);return clone(value);}
function checkedReasonRecord(raw,key,prefix,scope){
 need(typeof raw==='string'&&raw.length<=MAX);const r=JSON.parse(raw),slot=r.schema==='graph-delivery-reason-slot-v124';
 need(keys(r,slot?'schema,scope,window_id,owner,generation,values':'schema,scope,window_id,owner,values')
  &&(slot||r.schema==='graph-delivery-reason-entry-v124')&&same(r.scope,scope)&&UUID.test(r.owner)&&UUID.test(r.window_id)
  &&key===prefix+(slot?'slot:'+r.owner+':'+(r.generation%2):'entry:'+r.owner)&&(!slot||integer(r.generation)));
 drafts(r.values);return r;
}
function admission(value,p,intentHash){
 need(keys(value,'schema,id,workspace_id,producer_id,intent_id,source_key,contract_hash,source_intent_hash,projection,sequence,admitted_at,admission_hash')
  &&value.schema==='graph-event-admission-v1'&&UUID.test(value.id)&&value.workspace_id===p.scope.workspace_id&&value.producer_id===p.producer_id&&value.intent_id===p.intent_id
  &&safe(value.source_key)&&SHA.test(value.contract_hash)&&value.source_intent_hash===intentHash&&object(value.projection)&&integer(value.sequence)&&iso(value.admitted_at)&&SHA.test(value.admission_hash));
}

export class GraphSourceDeliveryControllerV124{
 constructor({api,scope,storage=globalThis.localStorage,session=globalThis.sessionStorage,crypto=globalThis.crypto,onChange=()=>{}}){
  Object.assign(this,{api,scope:scopeValue(scope),storage,session,crypto,onChange});
  this.prefix='ga-graph-delivery-v124:'+this.scope.workspace_id+':'+this.scope.actor_id+':';this.draftPrefix='ga-graph-delivery-drafts-v124:'+this.scope.workspace_id+':'+this.scope.actor_id+':';
  this.retirementPrefix='ga-graph-delivery-retirement-v125:'+this.scope.workspace_id+':'+this.scope.actor_id+':';this.retirementDraftPrefix='ga-graph-delivery-retirement-drafts-v125:'+this.scope.workspace_id+':'+this.scope.actor_id+':';
  this.identityStatus='unverified';this.identityEpoch=0;this.identityRequested=false;this.identityRole=null;this.busy=false;this.disposed=false;this.corrupt=false;this.localSaved=true;this.error='';this.notice='';
  this.pending=null;this.pendingNotFound=false;this.lastReceipt=null;this.lastLegacy=null;this.lastOutcome=null;this.lastOriginal=null;
  this.retirement=null;this.retirementNotFound=false;this.lastRetirement=null;this.retirementRecovery={items:[],next_offset:null,problem_count:0,loaded:false};
  this.reasonSaved=true;this.retirementReasonSaved=true;this.retirementReasons={};this.retirementDraftOwner=crypto.randomUUID();this.retirementDraftGeneration=0;this.retirementReasonRecoveries={items:[],next_offset:null,problem_count:0};
  this.list={producer_id:null,items:[],next_after:null,loaded:false};this.detail=null;this.requestedProducer=undefined;
  this.recovery={items:[],next_offset:null,problem_count:0,loaded:false};this.reasonRecoveries={items:[],next_offset:null,problem_count:0};
  this.reasons={};this.draftOwner=crypto.randomUUID();this.draftGeneration=0;
  try{
   this.sessionPin={actor:session.getItem('ga-setup-csrf-user'),workspace:session.getItem('ga-setup-csrf-workspace'),token:session.getItem('ga-setup-csrf')};need(this.sessionPin.actor===this.scope.actor_id&&this.sessionPin.workspace===this.scope.workspace_id&&text(this.sessionPin.token));
   this.windowID=session.getItem(this.prefix+'window');if(this.windowID===null){this.windowID=crypto.randomUUID();this._write(session,this.prefix+'window',this.windowID);}need(UUID.test(this.windowID));
   const raw=session.getItem(this.draftPrefix+'head');if(raw!==null){const h=JSON.parse(raw);need(keys(h,'schema,scope,window_id,owner,generation,key,values')&&h.schema==='graph-delivery-reason-head-v124'&&same(h.scope,this.scope)&&h.window_id===this.windowID&&UUID.test(h.owner)&&integer(h.generation)&&h.key===this.draftPrefix+'slot:'+h.owner+':'+(h.generation%2));this.reasons=drafts(h.values);}
   this.entryReasons=drafts(this.reasons);const key=session.getItem(this.prefix+'pending');if(key!==null)this.pending=this._readPending(key);
   const endRaw=session.getItem(this.retirementDraftPrefix+'head');if(endRaw!==null){const h=JSON.parse(endRaw);need(keys(h,'schema,scope,window_id,owner,generation,key,values')&&h.schema==='graph-delivery-retirement-reason-head-v125'&&same(h.scope,this.scope)&&h.window_id===this.windowID&&UUID.test(h.owner)&&integer(h.generation)&&h.key===this.retirementDraftPrefix+'slot:'+h.owner+':'+(h.generation%2));this.retirementReasons=retirementDrafts(h.values);}
   this.entryRetirementReasons=retirementDrafts(this.retirementReasons);const ending=session.getItem(this.retirementPrefix+'pending');if(ending!==null)this.retirement=this._readRetirement(ending);
  }catch{this.corrupt=true;this.localSaved=false;this.error='本机送达恢复记录暂无法读取，未覆盖原内容；请核对浏览器存储。';}
 }
 notify(){if(!this.disposed)this.onChange(this);}
 dispose(){this.disposed=true;}
 active(identityOnly=false){
  need(!this.disposed&&(!this.corrupt||identityOnly),'页面或恢复记录暂无法核对，原输入保留。');
  const valid=this.session.getItem('ga-setup-csrf-user')===this.scope.actor_id&&this.session.getItem('ga-setup-csrf-workspace')===this.scope.workspace_id&&this.session.getItem('ga-setup-csrf')===this.sessionPin?.token;
  if(!valid){this.identityStatus='concealed';this.notify();}need(valid,'登录账号或工作区已变化，原记录保留。');
 }
 _write(store,key,raw){try{need(raw.length<=MAX,'本机单份内容超过恢复范围，当前输入保留；请缩短说明，暂不能提交或离开。');store.setItem(key,raw);need(store.getItem(key)===raw,'本机恢复记录未完整写入并读回，未提交。');}catch(error){this.localSaved=false;this.notify();throw error;}}
 _once(key,value){const raw=JSON.stringify(value),old=this.storage.getItem(key);need(old===null||old===raw,'上次恢复操作已有不同内容，未覆盖。');this._write(this.storage,key,raw);}
 _origin(){const id=this.session.getItem(this.prefix+'origin-window')||this.windowID;need(UUID.test(id));return id;}
 _readPending(key,origin=this._origin()){
  const p=JSON.parse(this.storage.getItem(key));need(keys(p,'schema,scope,window_id,intent_id,producer_id,body,exact_body,command_hash')&&p.schema==='graph-source-delivery-outbox-v124'
   &&same(p.scope,this.scope)&&p.window_id===origin&&UUID.test(origin)&&UUID.test(p.intent_id)&&UUID.test(p.producer_id)&&SHA.test(p.command_hash)&&p.exact_body===JSON.stringify(p.body));retryBody(p.body);need(key===this.prefix+'outbox:'+p.body.request_id);return p;
 }
 async request(path,options={}){this.active(path==='/me');try{const r=await this.api(path,{...options,headers:{...options.headers,'X-Workspace-ID':this.scope.workspace_id,'X-CSRF-Token':this.sessionPin.token}});this.active(path==='/me');return r;}
  catch(error){if(error?.status===401||error?.status===403){this.identityStatus='concealed';this.notify();}throw error;}}
 async identity(){
  const epoch=this.identityEpoch;this.identityStatus='checking';this.notify();
  try{const me=await this.request('/me');need(epoch===this.identityEpoch&&!this.identityRequested,'登录核对已更新，正在重新核对；原记录保留。');need(me.user_id===this.scope.actor_id&&me.workspace_id===this.scope.workspace_id,'当前登录身份不一致，原记录保留。');
   this.identityRole=me.role;this.identityStatus='verified';this.notify();return me;
  }catch(error){if(epoch===this.identityEpoch&&!this.identityRequested)this.identityStatus='concealed';this.notify();throw error;}
 }
 async _run(callback,{identityOnly=false}={}){
  this.active(identityOnly);need(!this.busy,'请先等待当前核对完成。');this.busy=true;this.error='';this.notify();
  try{await this.identity();return await callback();}
  catch(error){if(!this.disposed)this.error=error?.code==='DELIVERY_INPUT'?error.message:['GRAPH_SOURCE_DELIVERY_RECOVERY_CHANGED','GRAPH_SOURCE_DELIVERY_RECOVERY_STORAGE','GRAPH_SOURCE_DELIVERY_RECOVERY_RECORD'].includes(error?.code)?'本机恢复记录暂无法完整读取，原记录保留；请重新读取或核对浏览器存储。':error?.status===403?'当前账号不能恢复送达；原输入与原操作保留。':error?.status===409?'原结果或来源状态已变化，原请求保留；请先核对上次恢复结果。':'送达情况或上次恢复结果暂未核对完成，原输入与原请求保留；请检查连接或存储后继续。';throw error;}
  finally{this.busy=false;this.notify();if(this.identityRequested&&!this.disposed){this.identityRequested=false;this.recheckIdentity().catch(()=>{});}else if(this.requestedProducer!==undefined&&!this.disposed){const id=this.requestedProducer;this.requestedProducer=undefined;this.selectProducer(id).catch(()=>{});}}
 }
 recheckIdentity(){this.identityEpoch++;this.identityStatus='checking';this.notify();if(this.busy){this.identityRequested=true;return Promise.resolve(false);}return this._run(async()=>true,{identityOnly:true});}
 saveLocal(){
  try{
   this.active();const values=drafts(this.reasons),generation=this.draftGeneration+1,key=this.draftPrefix+'slot:'+this.draftOwner+':'+(generation%2);
   this._once(this.draftPrefix+'entry:'+this.draftOwner,{schema:'graph-delivery-reason-entry-v124',scope:this.scope,window_id:this.windowID,owner:this.draftOwner,values:this.entryReasons});
   const prior=this.storage.getItem(key);if(prior!==null){const old=JSON.parse(prior);need(old.schema==='graph-delivery-reason-slot-v124'&&same(old.scope,this.scope)&&old.window_id===this.windowID&&old.owner===this.draftOwner&&integer(old.generation)&&old.generation<=generation);drafts(old.values);}
   const row={schema:'graph-delivery-reason-slot-v124',scope:this.scope,window_id:this.windowID,owner:this.draftOwner,generation,values};need(this.storage.getItem(key)===prior);this._write(this.storage,key,JSON.stringify(row));
   this._write(this.session,this.draftPrefix+'head',JSON.stringify({schema:'graph-delivery-reason-head-v124',scope:this.scope,window_id:this.windowID,owner:this.draftOwner,generation,key,values}));this.draftGeneration=generation;this.reasonSaved=true;this.localSaved=this.retirementReasonSaved;this.notify();return true;
  }catch{this.reasonSaved=false;this.localSaved=false;this.error='恢复说明尚未完整保存到本机，当前输入保留；请恢复存储或缩短说明后重试，暂不能提交或离开。';this.notify();return false;}
 }
 editReason(value){this.active();need(this.detail&&typeof value==='string');this.reasons={...this.reasons,[this.detail.intent_id]:{producer_id:this.detail.producer_id,reason:value}};this.notice='';this.saveLocal();}
 get reason(){return this.detail?this.reasons[this.detail.intent_id]?.reason||'':'';}
 get retirementReason(){return this.pending?this.retirementReasons[this.pending.intent_id]?.[this.pending.body.request_id]||'':'';}
 saveRetirementReason(){
  try{this.active();const values=retirementDrafts(this.retirementReasons),generation=this.retirementDraftGeneration+1,key=this.retirementDraftPrefix+'slot:'+this.retirementDraftOwner+':'+(generation%2);
   this._once(this.retirementDraftPrefix+'entry:'+this.retirementDraftOwner,{schema:'graph-delivery-retirement-reason-entry-v125',scope:this.scope,window_id:this.windowID,owner:this.retirementDraftOwner,values:this.entryRetirementReasons});
   const prior=this.storage.getItem(key);if(prior!==null){const old=checkedRetirementReasonRecord(prior,key,this.retirementDraftPrefix,this.scope);need(old.owner===this.retirementDraftOwner&&old.window_id===this.windowID&&old.generation<=generation);}
   need(this.storage.getItem(key)===prior);this._write(this.storage,key,JSON.stringify({schema:'graph-delivery-retirement-reason-slot-v125',scope:this.scope,window_id:this.windowID,owner:this.retirementDraftOwner,generation,values}));
   this._write(this.session,this.retirementDraftPrefix+'head',JSON.stringify({schema:'graph-delivery-retirement-reason-head-v125',scope:this.scope,window_id:this.windowID,owner:this.retirementDraftOwner,generation,key,values}));this.retirementDraftGeneration=generation;this.retirementReasonSaved=true;this.localSaved=this.reasonSaved;this.notify();return true;
  }catch{this.retirementReasonSaved=false;this.localSaved=false;this.error='结束理由尚未完整保存到本机，当前输入保留；请恢复存储或缩短理由后重试，暂不能提交或离开。';this.notify();return false;}
 }
 setRetirementReason(value){this.active();need(this.pending&&typeof value==='string');const p=this.pending;this.retirementReasons={...this.retirementReasons,[p.intent_id]:{...this.retirementReasons[p.intent_id],[p.body.request_id]:value}};this.saveRetirementReason();}
 saveAllLocal(){const a=this.saveLocal(),b=this.saveRetirementReason();return a&&b;}
 _discover(more=false){const offset=more?this.recovery.next_offset:0;need(offset!==null,'本次本机记录已读取完。');const page=enumerateGraphSourceDeliveryRecovery({storage:this.storage,scope:this.scope,offset,limit:20,maxScan:200});this.recovery={items:more?[...this.recovery.items,...page.items]:page.items,next_offset:page.next_offset,problem_count:(more?this.recovery.problem_count:0)+page.problem_count,loaded:true};this.notify();return this.recovery;}
 discoverRecovery(more=false){return this._run(async()=>this._discover(more));}
 discoverRetirements(more=false){return this._run(async()=>{const offset=more?this.retirementRecovery.next_offset:0;need(offset!==null);const page=enumerateGraphSourceDeliveryRetirementRecovery({storage:this.storage,scope:this.scope,offset,limit:20,maxScan:200});this.retirementRecovery={items:more?[...this.retirementRecovery.items,...page.items]:page.items,next_offset:page.next_offset,problem_count:(more?this.retirementRecovery.problem_count:0)+page.problem_count,loaded:true};this.notify();return this.retirementRecovery;});}
 async _loadProducer(id,more=false){
  need(id===null||UUID.test(id));if(id===null){this.list={producer_id:null,items:[],next_after:null,loaded:true};this.notify();return this.list;}
  const cursor=more?this.list.next_after:null;need(!more||(this.list.producer_id===id&&cursor!==null));
  const page=await this.request(BASE+'/'+id+'/delivery-records?limit=20'+(cursor?'&after='+encodeURIComponent(cursor):''));need(keys(page,'schema,producer_id,items,next_after')&&page.schema==='graph-event-delivery-page-v1'&&page.producer_id===id&&Array.isArray(page.items)&&page.items.length<=20&&(page.next_after===null||UUID.test(page.next_after))&&(!cursor||page.next_after!==cursor));
  for(const row of page.items){inspectDeliveryRecord(row,this.scope);need(row.producer_id===id);}need(new Set(page.items.map(r=>r.intent_id)).size===page.items.length);
  this.list={producer_id:id,items:more?[...this.list.items,...page.items.filter(r=>!this.list.items.some(old=>old.intent_id===r.intent_id))]:page.items,next_after:page.next_after,loaded:true};this.notify();return this.list;
 }
 open(producerID=null){return this._run(async()=>{this._discover();return this._loadProducer(producerID);});}
 selectProducer(id){need(id===null||UUID.test(id));if(this.busy){this.requestedProducer=id;return Promise.resolve(false);}return this._run(async()=>this._loadProducer(id));}
 loadMore(){return this._run(async()=>this._loadProducer(this.list.producer_id,true));}
 _clearLastResult(){this.lastReceipt=null;this.lastLegacy=null;this.lastRetirement=null;this.lastOutcome=null;this.lastOriginal=null;this.notice='';}
 async _detail(id,producer=null){need(UUID.test(id));const r=await this.request(BASE+'/intents/'+id+'/delivery-record');inspectDeliveryRecord(r,this.scope,{detail:true});need(r.intent_id===id&&(producer===null||r.producer_id===producer));if(this.lastOriginal&&(this.lastOriginal.intent_id!==r.intent_id||this.lastOriginal.producer_id!==r.producer_id))this._clearLastResult();this.detail=r;this.reasonRecoveries={items:[],next_offset:null,problem_count:0};this.notify();return r;}
 selectIntent(id){return this._run(async()=>{need(this.saveLocal(),'当前说明尚未保存，未切换原结果详情。');return this._detail(id);});}
 async _validatePending(p){this.active();need(p.command_hash===await deliveryRetryCommandHash(this.scope,p.intent_id,p.body,this.crypto));const key=this.prefix+'outbox:'+p.body.request_id;need(this.session.getItem(this.prefix+'pending')===key&&same(this._readPending(key),p));}
 _link(p){
  this.active();need(!this.pending||same(this.pending,p),'本页还有另一项恢复操作待核对，未覆盖。');const key=this.prefix+'outbox:'+p.body.request_id;need(same(this._readPending(key,p.window_id),p));
  need(!this.retirement||sameOriginal(this.retirement,p),'本页还有另一项结束操作待核对，原内容保留。');
  this._write(this.session,this.prefix+'origin-window',p.window_id);this._write(this.session,this.prefix+'pending',key);this.pending=this._readPending(key);this.pendingNotFound=false;
  if(this.lastOriginal&&!samePendingOriginal(this.lastOriginal,this.pending))this._clearLastResult();this.notify();
 }
 async _checkedReceipt(r,p){
  need(keys(r,'schema,id,request_id,actor_id,intent_id,producer_id,job_id,intent_hash,command_hash,command,result,receipt_hash')&&r.schema==='graph-event-intent-retry-receipt-v1'&&UUID.test(r.id)&&r.request_id===p.body.request_id&&r.actor_id===this.scope.actor_id
   &&r.intent_id===p.intent_id&&r.producer_id===p.producer_id&&UUID.test(r.job_id)&&SHA.test(r.intent_hash)&&r.command_hash===p.command_hash&&same(r.command,p.body)&&SHA.test(r.receipt_hash));
  const result=r.result;need(keys(result,'outcome,accepted_job_version,reconciled,admission')&&['queued','already_admitted'].includes(result.outcome)&&integer(result.accepted_job_version)&&typeof result.reconciled==='boolean');
  if(result.outcome==='queued')need(result.accepted_job_version===p.body.job_version+1&&result.reconciled===false&&result.admission===null);
  else admission(result.admission,p,r.intent_hash);
  // C cold-checks the complete receipt hash and original Job FK. A plain JSON
  // client cannot re-hash Python float spelling in the nested admission proof.
  return r;
 }
 _checkedLegacy(l,p){need(keys(l,'origin,job_id,intent_id,producer_id,retry')&&l.origin==='job_history_v1'&&UUID.test(l.job_id)&&l.intent_id===p.intent_id&&l.producer_id===p.producer_id);const r=l.retry;
  need(keys(r,'request_id,actor_id,command_hash,command,accepted_job_version')&&r.request_id===p.body.request_id&&r.actor_id===this.scope.actor_id&&r.command_hash===p.command_hash&&same(r.command,p.body)&&r.accepted_job_version===p.body.job_version+1);return l;}
 _completion(p){const completed={...clone(p),schema:'graph-source-delivery-completed-v124'},key=this.prefix+'completed:'+p.body.request_id,old=this.storage.getItem(key);
  if(old!==null)need(same(JSON.parse(old),completed));else this._write(this.storage,key,JSON.stringify(completed));
 }
 _clearPending(p){
  this.active();need(this.session.getItem(this.prefix+'pending')===this.prefix+'outbox:'+p.body.request_id);this.session.removeItem(this.prefix+'pending');need(this.session.getItem(this.prefix+'pending')===null);this.session.removeItem(this.prefix+'origin-window');
  this.pending=null;this.pendingNotFound=false;
 }
 async _finish(p,value){
  await this._validatePending(p);this._completion(p);this._clearPending(p);this.lastOriginal=clone(p);this.lastRetirement=null;this.lastReceipt=value.receipt;this.lastLegacy=value.legacy;this.lastOutcome=value.receipt?.result.outcome||'legacy_saved';
  this.notice=value.legacy?'已找到上次恢复记录；这是旧版保存记录，没有独立回执或保存时间。':value.receipt.result.outcome==='already_admitted'?'上次恢复操作已保存。结果已送达；是否用于某个任务、任务是否完成，请到任务详情核对。':'上次恢复操作已保存，当时已安排送达；当前进度请看下方送达情况。';
  return value;
 }
 async _refreshOriginal(p){try{return await this._detail(p.intent_id,p.producer_id);}catch{if(!this.disposed)this.notice+=' 原来源详情暂未更新，已核对的上次恢复结果仍保留。';return null;}}
 async _lookup(p,expectedRetirement=null){
  this.pendingNotFound=false;await this._validatePending(p);const ending=this.retirement;if(ending){need(sameOriginal(ending,p),'原恢复与结束操作的保存范围不一致，全部原记录保留；请重新读取本机原记录。');await this._validateRetirement(ending);}const value=await this.request(BASE+'/intents/'+p.intent_id+'/retry-command-requests/'+p.body.request_id);
  need(keys(value,'schema,intent_id,request_id,outcome,command_hash,saved,legacy,retirement')&&value.schema==='graph-event-intent-retry-request-state-v1'&&value.intent_id===p.intent_id&&value.request_id===p.body.request_id&&['not_found','saved','retired'].includes(value.outcome));
  if(expectedRetirement)need(value.outcome==='retired'&&same(value.retirement,expectedRetirement));
  if(value.outcome==='not_found'){need(value.command_hash===null&&value.saved===null&&value.legacy===null&&value.retirement===null);this.pendingNotFound=true;this.notice='没有找到上次恢复操作的保存记录。即使结果已经送达，也需要单独核对这次恢复操作，不能根据当前状态认定它已保存。';await this._refreshOriginal(p);return value;}
  need(value.command_hash===p.command_hash);
  if(value.outcome==='saved'){
   need(value.retirement===null&&(value.saved===null)!==(value.legacy===null));if(value.saved)await this._checkedReceipt(value.saved,p);else this._checkedLegacy(value.legacy,p);
   await this._finish(p,{receipt:value.saved,legacy:value.legacy});if(ending){await this._clearRetirement(ending);this.notice+=' 原恢复操作已保存，本次结束没有生效；没有记录结束成功。';}
  }else{
   need(value.saved===null&&value.legacy===null);await this._checkedRetirementReceipt(value.retirement,p);
   if(ending&&same(ending.body,value.retirement.command)){await this._checkedRetirementReceipt(value.retirement,p,ending);this._retirementCompletion(ending);}
   await this._finishRetired(p,value.retirement);if(ending)await this._clearRetirement(ending);
  }
  await this._refreshOriginal(p);return value;
 }
 recover(){return this._run(async()=>{need(this.pending);return this._lookup(this.pending);});}
 async restoreRecovery(candidate){return this._run(async()=>{
  const selected=readGraphSourceDeliveryRecoveryCandidate({storage:this.storage,scope:this.scope,candidate}),r=selected.record,key=this.prefix+'outbox:'+r.body.request_id;
  const p=this._readPending(key,r.window_id);need(p.intent_id===r.intent_id&&p.producer_id===r.producer_id&&p.exact_body===r.exact_body&&p.command_hash===r.command_hash&&p.command_hash===await deliveryRetryCommandHash(this.scope,p.intent_id,p.body,this.crypto));
  readGraphSourceDeliveryRecoveryCandidate({storage:this.storage,scope:this.scope,candidate});need(this.saveLocal(),'当前说明尚未保存，未替换原请求入口。');this._link(p);return this._lookup(p);
 });}
 async _send(p){
  need(!this.retirement,'结束操作尚未核对，不能同时重发原恢复操作。');
  const me=await this.identity();need(['admin','operator'].includes(me.role),'当前账号不能恢复送达；仍可核对上次恢复结果。');await this._validatePending(p);this.pendingNotFound=false;
  await this.request(BASE+'/intents/'+p.intent_id+'/retry',{method:'POST',body:p.exact_body});return this._lookup(p);
 }
 submitRetry(){return this._run(async()=>{
  need(!this.pending&&!this.retirement&&this.localSaved&&this.detail&&this.detail.action.allowed&&['admin','operator'].includes(this.identityRole),'请先核对当前结果是否允许恢复，并处理原操作。');need(this.saveLocal());const selected=this.detail,reason=this.reason;need(text(reason),'请填写不超过1000个字符的恢复说明。');
  const current=await this._detail(selected.intent_id,selected.producer_id);need(current.intent_hash===selected.intent_hash&&current.delivery?.job_id===selected.delivery?.job_id&&current.delivery?.version===selected.delivery?.version&&current.action.allowed&&current.action.kind===selected.action.kind,'送达情况已变化，已更新详情；请核对后再恢复送达。');
  const body={request_id:this.crypto.randomUUID(),job_version:current.delivery.version,reason};retryBody(body);
  const p={schema:'graph-source-delivery-outbox-v124',scope:clone(this.scope),window_id:this.windowID,intent_id:current.intent_id,producer_id:current.producer_id,body,exact_body:JSON.stringify(body),command_hash:await deliveryRetryCommandHash(this.scope,current.intent_id,body,this.crypto)};
  this.active();this._once(this.prefix+'outbox:'+body.request_id,p);this._link(p);return this._send(p);
 });}
 resend(){return this._run(async()=>{need(this.pending&&this.pendingNotFound,'请先只核对原操作，明确未找到后再按原内容重试。');return this._send(this.pending);});}
 loadReasonDrafts(more=false){return this._run(async()=>{
  need(this.detail);let offset=more?this.reasonRecoveries.next_offset:0;need(offset!==null);const items=[],length=this.storage.length;let scanned=0,problems=0;
  while(offset<length&&scanned++<200&&items.length<20){const key=this.storage.key(offset++);if(!key?.startsWith(this.draftPrefix))continue;const raw=this.storage.getItem(key);try{const r=checkedReasonRecord(raw,key,this.draftPrefix,this.scope),v=r.values[this.detail.intent_id];if(v&&v.producer_id===this.detail.producer_id&&v.reason)items.push({key,raw,reason:v.reason,intent_id:this.detail.intent_id,producer_id:this.detail.producer_id});}catch{problems++;}}
  this.reasonRecoveries={items:more?[...this.reasonRecoveries.items,...items]:items,next_offset:offset<length?offset:null,problem_count:(more?this.reasonRecoveries.problem_count:0)+problems};return this.reasonRecoveries;
 });}
 restoreReason(candidate){return this._run(async()=>{
  need(this.detail&&candidate.intent_id===this.detail.intent_id&&candidate.producer_id===this.detail.producer_id&&candidate.key.startsWith(this.draftPrefix)&&this.storage.getItem(candidate.key)===candidate.raw,'保留说明已变化，请重新读取后明确选择。');
  const r=checkedReasonRecord(candidate.raw,candidate.key,this.draftPrefix,this.scope);const value=r.values[this.detail.intent_id];need(value&&value.producer_id===this.detail.producer_id&&value.reason===candidate.reason);need(this.saveLocal());
  this.draftOwner=this.crypto.randomUUID();this.draftGeneration=0;this.reasons={...this.reasons,[this.detail.intent_id]:clone(value)};this.entryReasons=drafts(this.reasons);need(this.saveLocal());this.notice='已恢复所选说明，原说明另行保留；没有自动恢复送达。';
 });}
 _readRetirement(key,origin=this.session.getItem(this.retirementPrefix+'origin-window')||this.windowID){
  const r=JSON.parse(this.storage.getItem(key));need(keys(r,'schema,scope,window_id,intent_id,producer_id,original_window_id,body,exact_body,command_hash')&&r.schema==='graph-source-delivery-retirement-outbox-v125'
   &&same(r.scope,this.scope)&&r.window_id===origin&&UUID.test(origin)&&UUID.test(r.intent_id)&&UUID.test(r.producer_id)&&UUID.test(r.original_window_id)&&SHA.test(r.command_hash)&&r.exact_body===JSON.stringify(r.body));retirementBody(r.body);need(key===this.retirementPrefix+'outbox:'+r.body.request_id);return r;
 }
 async _validateRetirement(r){this.active();need(r.command_hash===await deliveryRetirementCommandHash(this.scope,r.intent_id,r.body,this.crypto));const key=this.retirementPrefix+'outbox:'+r.body.request_id;need(this.session.getItem(this.retirementPrefix+'pending')===key&&same(this._readRetirement(key),r));}
 _linkRetirement(r){this.active();need(!this.retirement||same(this.retirement,r),'另一次结束操作尚未核对，原内容保留。');const key=this.retirementPrefix+'outbox:'+r.body.request_id;need(same(this._readRetirement(key,r.window_id),r));need(this.pending&&sameOriginal(r,this.pending));this._write(this.session,this.retirementPrefix+'origin-window',r.window_id);this._write(this.session,this.retirementPrefix+'pending',key);this.retirement=this._readRetirement(key);this.retirementNotFound=false;this.notify();}
 async _originalForRetirement(r){const p=this._readPending(this.prefix+'outbox:'+r.body.original.request_id,r.original_window_id);need(p.intent_id===r.intent_id&&p.producer_id===r.producer_id&&same(p.body,r.body.original)&&p.command_hash===await deliveryRetryCommandHash(this.scope,p.intent_id,p.body,this.crypto));this._link(p);return p;}
 async _checkedRetirementReceipt(receipt,p,r=null){
  need(keys(receipt,'schema,id,request_id,actor_id,intent_id,producer_id,job_id,intent_hash,original_request_id,original_command_hash,command_hash,command,result,receipt_hash')
   &&receipt.schema==='graph-event-intent-retry-retirement-receipt-v1'&&UUID.test(receipt.id)&&UUID.test(receipt.request_id)&&receipt.actor_id===this.scope.actor_id&&receipt.intent_id===p.intent_id&&receipt.producer_id===p.producer_id&&UUID.test(receipt.job_id)&&SHA.test(receipt.intent_hash)
   &&receipt.original_request_id===p.body.request_id&&receipt.original_command_hash===p.command_hash&&SHA.test(receipt.command_hash)&&SHA.test(receipt.receipt_hash));
  retirementBody(receipt.command);need(receipt.command.request_id===receipt.request_id&&same(receipt.command.original,p.body)&&receipt.command_hash===await deliveryRetirementCommandHash(this.scope,p.intent_id,receipt.command,this.crypto));
  need(keys(receipt.result,'outcome,original_request_id,original_command_hash')&&receipt.result.outcome==='retired'&&receipt.result.original_request_id===p.body.request_id&&receipt.result.original_command_hash===p.command_hash);
  const {receipt_hash,...unsigned}=receipt;need(receipt_hash===await hashValue(unsigned,this.crypto));if(r)need(receipt.request_id===r.body.request_id&&receipt.command_hash===r.command_hash&&same(receipt.command,r.body));return receipt;
 }
 _retirementCompletion(r){const completed={...clone(r),schema:'graph-source-delivery-retirement-completed-v125'},key=this.retirementPrefix+'completed:'+r.body.request_id,old=this.storage.getItem(key);if(old!==null)need(same(JSON.parse(old),completed));else this._write(this.storage,key,JSON.stringify(completed));}
 async _clearRetirement(r){await this._validateRetirement(r);this.session.removeItem(this.retirementPrefix+'pending');need(this.session.getItem(this.retirementPrefix+'pending')===null);this.session.removeItem(this.retirementPrefix+'origin-window');this.retirement=null;this.retirementNotFound=false;}
 async _finishRetired(p,receipt){await this._validatePending(p);await this._checkedRetirementReceipt(receipt,p);this._completion(p);this._clearPending(p);this.lastOriginal=clone(p);this.lastReceipt=null;this.lastLegacy=null;this.lastOutcome='retired';this.lastRetirement=receipt;this.notice='这次未完成的恢复操作已结束，原说明和记录保留。没有撤销处理结果或已送达消息，也没有停止来源；如需恢复送达，请重新核对当前结果详情。';}
 retireOriginal(){return this._run(async()=>{
  const p=this.pending;need(p&&this.pendingNotFound&&!this.retirement&&this.localSaved&&this.saveRetirementReason(),'请先核对原恢复操作尚未找到，保存结束理由后再确认。');await this._validatePending(p);const reason=this.retirementReason;need(validDeliveryRetirementReason(reason),'请填写不超过1000个字符的结束理由。');
  const body={schema:'graph-event-intent-retry-retirement-v1',request_id:this.crypto.randomUUID(),original:clone(p.body),reason};retirementBody(body);
  const r={schema:'graph-source-delivery-retirement-outbox-v125',scope:clone(this.scope),window_id:this.windowID,intent_id:p.intent_id,producer_id:p.producer_id,original_window_id:p.window_id,body,exact_body:JSON.stringify(body),command_hash:await deliveryRetirementCommandHash(this.scope,p.intent_id,body,this.crypto)};
  this.active();this._once(this.retirementPrefix+'outbox:'+body.request_id,r);this._linkRetirement(r);return this._sendRetirement(r,p);
 });}
 async _sendRetirement(r,p){
  const me=await this.identity();need(['admin','operator'].includes(me.role),'当前账号不能结束恢复操作；仍可核对已保存结果。');await this._validateRetirement(r);await this._validatePending(p);this.retirementNotFound=false;
  try{const value=await this.request(BASE+'/intents/'+r.intent_id+'/retry-retirements',{method:'POST',body:r.exact_body});need(keys(value,'retirement,replayed')&&typeof value.replayed==='boolean');await this._checkedRetirementReceipt(value.retirement,p,r);return this._lookupRetirement(r);}
  catch(error){if(error?.status===409){const original=await this._lookup(p);if(original.outcome!=='not_found')return original;}throw error;}
 }
 async _lookupRetirement(r){
  this.retirementNotFound=false;await this._validateRetirement(r);const p=await this._originalForRetirement(r),value=await this.request(BASE+'/intents/'+r.intent_id+'/retry-retirements/requests/'+r.body.request_id);need(keys(value,'found,retirement')&&typeof value.found==='boolean');
  if(value.found){await this._checkedRetirementReceipt(value.retirement,p,r);return this._lookup(p,value.retirement);}
  need(value.retirement===null);const original=await this._lookup(p);if(original.outcome==='not_found'){this.retirementNotFound=true;this.notice='尚未找到这次结束操作或原恢复操作。可继续核对，或明确按原内容重试结束；不会另建替代操作。';}return original;
 }
 recoverRetirement(){return this._run(async()=>{need(this.retirement);return this._lookupRetirement(this.retirement);});}
 resendRetirement(){return this._run(async()=>{need(this.retirement&&this.retirementNotFound,'请先核对这次结束与原恢复操作都尚未找到。');const r=this.retirement,p=await this._originalForRetirement(r);return this._sendRetirement(r,p);});}
 restoreRetirement(candidate){return this._run(async()=>{
  const selected=readGraphSourceDeliveryRetirementRecoveryCandidate({storage:this.storage,scope:this.scope,candidate}),row=selected.record,r=this._readRetirement(this.retirementPrefix+'outbox:'+row.body.request_id,row.window_id);
  need(r.intent_id===row.intent_id&&r.producer_id===row.producer_id&&r.original_window_id===row.original_window_id&&r.exact_body===row.exact_body&&r.command_hash===row.command_hash&&r.command_hash===await deliveryRetirementCommandHash(this.scope,r.intent_id,r.body,this.crypto));
  readGraphSourceDeliveryRetirementRecoveryCandidate({storage:this.storage,scope:this.scope,candidate});need(this.saveAllLocal(),'当前输入尚未保存，未替换结束操作的核对入口。');await this._originalForRetirement(r);this._linkRetirement(r);return this._lookupRetirement(r);
 });}
 loadRetirementReasonDrafts(more=false){return this._run(async()=>{
  const p=this.pending;need(p);let offset=more?this.retirementReasonRecoveries.next_offset:0;need(offset!==null);const items=[],length=this.storage.length;let scanned=0,problems=0;
  while(offset<length&&scanned++<200&&items.length<20){const key=this.storage.key(offset++);if(!key?.startsWith(this.retirementDraftPrefix))continue;const raw=this.storage.getItem(key);try{const r=checkedRetirementReasonRecord(raw,key,this.retirementDraftPrefix,this.scope),reason=r.values[p.intent_id]?.[p.body.request_id];if(reason)items.push({key,raw,intent_id:p.intent_id,request_id:p.body.request_id,reason});}catch{problems++;}}
  this.retirementReasonRecoveries={items:more?[...this.retirementReasonRecoveries.items,...items]:items,next_offset:offset<length?offset:null,problem_count:(more?this.retirementReasonRecoveries.problem_count:0)+problems};this.notify();return this.retirementReasonRecoveries;
 });}
 restoreRetirementReason(candidate){return this._run(async()=>{
  const p=this.pending;need(p&&candidate.intent_id===p.intent_id&&candidate.request_id===p.body.request_id&&candidate.key.startsWith(this.retirementDraftPrefix)&&this.storage.getItem(candidate.key)===candidate.raw,'保留的结束理由已变化，请重新读取。');const record=checkedRetirementReasonRecord(candidate.raw,candidate.key,this.retirementDraftPrefix,this.scope),reason=record.values[p.intent_id]?.[p.body.request_id];need(reason===candidate.reason&&this.saveRetirementReason());
  this.retirementDraftOwner=this.crypto.randomUUID();this.retirementDraftGeneration=0;this.retirementReasons={...this.retirementReasons,[p.intent_id]:{...this.retirementReasons[p.intent_id],[p.body.request_id]:reason}};this.entryRetirementReasons=retirementDrafts(this.retirementReasons);need(this.saveRetirementReason());this.notice='已恢复所选结束理由，原输入另行保留；没有自动结束恢复操作。';
 });}
}
