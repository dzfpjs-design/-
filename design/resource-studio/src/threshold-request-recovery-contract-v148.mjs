import {hashBytes} from './runtime-api';
import {THRESHOLD_ROOT_V147,ThresholdFeedbackJournalV147,checkedThresholdPendingV147,thresholdLabelsV147} from './threshold-feedback-contract-v147.mjs';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,sha=/^[0-9a-f]{64}$/;
const own=(x,k)=>Object.prototype.hasOwnProperty.call(x,k),object=x=>!!x&&typeof x==='object'&&!Array.isArray(x),integer=x=>Number.isSafeInteger(x)&&x>=1;
const fail=()=>Error('原请求、停止接收结果或历史判断无法完整核对；原文和人稿保留。');
function need(ok){if(!ok)throw fail();}
function keys(value,list){need(object(value)&&Object.keys(value).sort().join('|')===[...list].sort().join('|'));}
function text(value,max=2000){need(typeof value==='string'&&value.length<=max&&!/[\u0000\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value));}
function canonical(value){if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';if(object(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';return JSON.stringify(value);}
const same=(a,b)=>canonical(a)===canonical(b),clone=value=>JSON.parse(JSON.stringify(value));
const digest=value=>hashBytes(new TextEncoder().encode(canonical(value)));
export const thresholdRequestStatePathV148=original=>THRESHOLD_ROOT_V147+'/requests/'+original.request_id+'/status?'+new URLSearchParams({expected_sha256:original.wire_sha256,original_method:original.method,original_path:original.path});

export async function checkedThresholdRequestStateV148(value,scope,original){
 await checkedThresholdPendingV147(original,scope);
 keys(value,['schema','original_request_id','original_method','original_path','original_sha256','outcome','saved','retirement']);
 need(value.schema==='threshold-request-state-v148'&&value.original_request_id===original.request_id&&value.original_method===original.method&&value.original_path===original.path&&value.original_sha256===original.wire_sha256);
 need(['saved','retired','unknown'].includes(value.outcome));
 if(value.outcome==='unknown'){need(value.saved===null&&value.retirement===null);return value;}
 if(value.outcome==='saved'){
  need(value.retirement===null);const saved=value.saved;keys(saved,['command_id','feedback_id','root_id','revision','version','status','result_hash']);
  need(['command_id','feedback_id','root_id'].every(key=>uuid.test(saved[key]||''))&&integer(saved.revision)&&integer(saved.version)&&['draft','submitted','cancelled'].includes(saved.status)&&sha.test(saved.result_hash||''));
  const body=JSON.parse(original.exact_body),tail=original.path.slice(('/api'+THRESHOLD_ROOT_V147).length);
  if(tail==='/feedback')need(saved.feedback_id===saved.root_id&&saved.revision===1&&saved.version===1&&saved.status==='draft');
  else{const [,identity,action]=tail.match(/^\/feedback\/([^/]+)(?:\/(.+))?$/);if(action==='revisions')need(saved.root_id===identity&&saved.revision===body.expected_head_revision+1&&saved.version===1&&saved.status==='draft');else need(saved.feedback_id===identity&&saved.version===body.expected_version+1&&saved.status===(action==='submit'?'submitted':action==='cancel'?'cancelled':'draft'));}return value;
 }
 need(value.saved===null);const receipt=value.retirement;
 keys(receipt,['schema','id','request_id','original_request_id','actor_id','workspace_id','original_method','original_path','original_sha256','reason','outcome','receipt_hash']);
 need(receipt.schema==='threshold-request-retirement-receipt-v148'&&uuid.test(receipt.id||'')&&uuid.test(receipt.request_id||'')&&receipt.request_id!==original.request_id&&receipt.original_request_id===original.request_id&&receipt.actor_id===scope.actor_id&&receipt.workspace_id===scope.workspace_id&&receipt.original_method===original.method&&receipt.original_path===original.path&&receipt.original_sha256===original.wire_sha256&&receipt.outcome==='retired'&&sha.test(receipt.receipt_hash||''));
 text(receipt.reason);need(!!receipt.reason.trim());const {receipt_hash,...unsigned}=receipt;need(receipt_hash===await digest(unsigned));return value;
}

const humanKeys=['id','version','root_id','revision','previous_id','status','actor_id','label','reason','cancel_reason'];
function human(value){
 keys(value,humanKeys);need(['id','root_id','actor_id'].every(key=>uuid.test(value[key]||''))&&integer(value.version)&&integer(value.revision)&&(value.previous_id===null||uuid.test(value.previous_id||''))&&['draft','submitted','cancelled'].includes(value.status));
 need(value.label===null||own(thresholdLabelsV147,value.label));text(value.reason);if(value.cancel_reason!==null)text(value.cancel_reason);
 need(value.status==='cancelled'?!!value.cancel_reason?.trim():value.cancel_reason===null);if(value.status==='submitted')need(value.label!==null&&!!value.reason.trim());return value;
}
export function checkedThresholdHistoryV148(value,scope,ruleID=null,capabilityID=null){
 keys(value,['schema','entry','history','latest_submitted','basis_reference','basis_available','issue','eligible_for_candidate','can_edit','can_cancel','can_submit','can_revise']);
 need(value.schema==='threshold-evolution-feedback-history-v148'&&typeof value.basis_available==='boolean'&&value.eligible_for_candidate===false&&['can_edit','can_cancel','can_submit','can_revise'].every(key=>value[key]===false));
 if(value.basis_available)need(value.issue===null);else{text(value.issue);need(!!value.issue.trim());}
 const reference=value.basis_reference;keys(reference,['observation_id','instance_id','release_id','baseline_capability_id','rule_id','basis_hash']);
 need(reference.observation_id===scope.observation_id&&reference.instance_id===scope.instance_id&&['observation_id','instance_id','release_id','baseline_capability_id'].every(key=>uuid.test(reference[key]||''))&&sha.test(reference.basis_hash||''));text(reference.rule_id,80);need(!!reference.rule_id&&(ruleID===null||reference.rule_id===ruleID)&&(capabilityID===null||reference.baseline_capability_id===capabilityID));
 human(value.entry);need(Array.isArray(value.history)&&value.history.length>0);let previous=null;const ids=new Set();
 for(const [index,entry]of value.history.entries()){human(entry);need(!ids.has(entry.id)&&entry.root_id===value.entry.root_id&&entry.revision===index+1&&entry.previous_id===(previous?.id||null));if(previous)need(previous.status!=='draft');ids.add(entry.id);previous=entry;}
 need(value.history[0].id===value.entry.root_id&&same(value.history.find(entry=>entry.id===value.entry.id),value.entry));const submitted=value.history.filter(entry=>entry.status==='submitted');need(same(value.latest_submitted,submitted.at(-1)||null));return value;
}
export function checkedThresholdHistoryLookupV148(value,scope,ruleID=null,capabilityID=null){
 keys(value,['schema','found','history']);need(value.schema==='threshold-feedback-history-lookup-v148'&&typeof value.found==='boolean');if(!value.found){need(value.history===null);return null;}return checkedThresholdHistoryV148(value.history,scope,ruleID,capabilityID);
}

async function checkedRetirement(value,scope){
 keys(value,['schema','scope','owner','original','request_id','method','path','exact_body','wire_sha256']);
 need(value.schema==='threshold-feedback-retirement-pending-v148'&&same(value.scope,scope)&&uuid.test(value.owner||'')&&uuid.test(value.request_id||'')&&value.method==='POST'&&sha.test(value.wire_sha256||''));
 await checkedThresholdPendingV147(value.original,scope);need(value.path==='/api'+THRESHOLD_ROOT_V147+'/requests/'+value.original.request_id+'/retire'&&value.request_id!==value.original.request_id);text(value.exact_body,512*1024);need(new TextEncoder().encode(value.exact_body).length<=512*1024);
 const body=JSON.parse(value.exact_body);keys(body,['schema','request_id','original_method','original_path','original_wire_text','original_sha256','reason','confirm']);
 need(body.schema==='threshold-request-retirement-command-v148'&&body.request_id===value.request_id&&body.original_method===value.original.method&&body.original_path===value.original.path&&body.original_wire_text===value.original.exact_body&&body.original_sha256===value.original.wire_sha256&&body.confirm===true);text(body.reason);need(!!body.reason.trim()&&value.exact_body===JSON.stringify(body)&&value.wire_sha256===await hashBytes(new TextEncoder().encode(value.exact_body)));return value;
}

// A separate local draft/pending channel. No V147 original/draft schema is
// upgraded, and these cached records never stand in for a server decision.
const unsavedReasons=new Map(),activeOwners=new Set();let unloadTarget=null,unloadHandler=null;
function warning(){const target=globalThis.window;if(unloadTarget&&unloadTarget!==target){unloadTarget.removeEventListener('beforeunload',unloadHandler);unloadTarget=null;}if(unsavedReasons.size&&!unloadTarget&&target){unloadTarget=target;unloadHandler=event=>{if(unsavedReasons.size){event.preventDefault();event.returnValue='';}};target.addEventListener('beforeunload',unloadHandler);}if(!unsavedReasons.size&&unloadTarget){unloadTarget.removeEventListener('beforeunload',unloadHandler);unloadTarget=null;}}
export class ThresholdFeedbackRecoveryJournalV148 extends ThresholdFeedbackJournalV147{
 constructor(scope,options){super(scope,options);this.retirementKey=this.prefix+':retirement-active';this.retirementReason='';this.recoveryReady=false;}
 open(){
  const prior=this.session.getItem(this.prefix+':head');let reason='';
  if(prior&&uuid.test(prior)){const raw=this.storage.getItem(this.prefix+':retirement-form:'+prior);if(raw!==null){const saved=JSON.parse(raw);keys(saved,['schema','scope','owner','reason']);need(saved.schema==='threshold-feedback-retirement-form-v148'&&same(saved.scope,this.scope)&&saved.owner===prior);text(saved.reason);reason=saved.reason;}const memory=unsavedReasons.get(prior);if(memory&&memory.session===this.session&&same(memory.scope,this.scope))reason=memory.reason;}
  this.retirementReason=reason;
  let form;try{form=super.open();}catch(cause){if(uuid.test(this.owner||'')){activeOwners.add(this.owner);unsavedReasons.set(this.owner,{scope:this.scope,session:this.session,reason:this.retirementReason});warning();}throw cause;}
  activeOwners.add(this.owner);this.saveRetirementReason(reason);if(prior&&!activeOwners.has(prior))unsavedReasons.delete(prior);warning();this.recoveryReady=true;return form;
 }
 saveRetirementReason(reason){
  need(uuid.test(this.owner||''));this.retirementReason=reason;
  try{text(reason);this.exact(this.storage,this.prefix+':retirement-form:'+this.owner,JSON.stringify({schema:'threshold-feedback-retirement-form-v148',scope:this.scope,owner:this.owner,reason}));unsavedReasons.delete(this.owner);warning();return reason;}
  catch(cause){unsavedReasons.set(this.owner,{scope:this.scope,session:this.session,reason});warning();throw cause;}
 }
 retryPersistence(){
  need(uuid.test(this.owner||''));this.recoveryReady=false;activeOwners.add(this.owner);
  // Both original owner slots and the session pointer must actually survive
  // exact write/readback. A flag cannot stand in for either saved human draft.
  super.save(this.form);this.saveRetirementReason(this.retirementReason);
  need(!super.isUnsaved()&&!unsavedReasons.has(this.owner));this.recoveryReady=true;return this.form;
 }
 canSend(){return this.recoveryReady&&super.canSend()&&!unsavedReasons.has(this.owner);}
 isUnsaved(){return super.isUnsaved()||unsavedReasons.has(this.owner);}
 dispose(){super.dispose();activeOwners.delete(this.owner);}
 async install(method,path,body,check){return super.install(method,path,body,async()=>{await check();need(this.storage.getItem(this.retirementKey)===null);});}
 async retirement(){const raw=this.storage.getItem(this.retirementKey);return raw===null?null:checkedRetirement(JSON.parse(raw),this.scope);}
 async installRetirement(original,reason,check){
  need(this.canSend());text(reason);need(!!reason.trim());return this.locks.request(this.prefix+':original',{mode:'exclusive'},async()=>{
   await check();await checkedThresholdPendingV147(original,this.scope);const active=await this.pending();need(active!==null&&same(active,original)&&this.storage.getItem(this.retirementKey)===null);
   const request_id=this.crypto.randomUUID(),body={schema:'threshold-request-retirement-command-v148',request_id,original_method:original.method,original_path:original.path,original_wire_text:original.exact_body,original_sha256:original.wire_sha256,reason,confirm:true},exact_body=JSON.stringify(body);
   const value={schema:'threshold-feedback-retirement-pending-v148',scope:this.scope,owner:this.owner,original:clone(original),request_id,method:'POST',path:'/api'+THRESHOLD_ROOT_V147+'/requests/'+original.request_id+'/retire',exact_body,wire_sha256:await hashBytes(new TextEncoder().encode(exact_body))};
   await checkedRetirement(value,this.scope);await check();need(this.canSend());this.exact(this.storage,this.retirementKey,JSON.stringify(value));return value;
  });
 }
 async withRetirement(value,check,callback){
  need(!!this.locks?.request);return this.locks.request(this.prefix+':original',{mode:'exclusive'},async()=>{await check();await checkedRetirement(value,this.scope);const active=await this.retirement(),original=await this.pending();need((active===null||same(active,value))&&(original===null||same(original,value.original)));await check();return callback(active!==null);});
 }
 // Called only inside the original channel lock, after actual status GET and
 // validation. Archive/readback precedes either CAS removal.
 resolveOriginal(original,state,retirement=null){
  need(['saved','retired'].includes(state.outcome)&&state.original_request_id===original.request_id&&state.original_sha256===original.wire_sha256);
  const active=this.storage.getItem(this.active),retiring=this.storage.getItem(this.retirementKey);need(active===null||active===JSON.stringify(original));
  if(retirement)need(retiring===null||retiring===JSON.stringify(retirement));else need(retiring===null);
  const archive={schema:'threshold-feedback-request-resolution-v148',scope:this.scope,original,state,retirement},key=this.prefix+':resolution:'+original.request_id,prior=this.storage.getItem(key);
  if(prior===null)this.exact(this.storage,key,JSON.stringify(archive));else{const saved=JSON.parse(prior);need(same(saved.scope,this.scope)&&same(saved.original,original)&&same(saved.state,state));if(retirement&&saved.retirement===null)this.exact(this.storage,key,JSON.stringify(archive));}
  if(active!==null){need(this.storage.getItem(this.active)===active);this.storage.removeItem(this.active);need(this.storage.getItem(this.active)===null);}
  if(retiring!==null){need(this.storage.getItem(this.retirementKey)===retiring);this.storage.removeItem(this.retirementKey);need(this.storage.getItem(this.retirementKey)===null);}
 }
}
