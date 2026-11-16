import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json as runtimeJSON} from './runtime-api.js';
import {stableStringify as canonical} from './content-signature.js';
import {PixelOwnedFormV250} from './pixel-owned-form-v250.mjs';
import LocalProposalReviewV261,{inspectLocalProposalSource,localProposalReviewPayload} from './LocalProposalReviewV261.jsx';

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA=/^[0-9a-f]{64}$/;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const need=(value,message='私人核对稿或原申请无法核对；已有人稿与原文保留。')=>{if(!value)throw Error(message);};
const copy=value=>JSON.parse(JSON.stringify(value));
const same=(a,b)=>canonical(a)===canonical(b);
const integer=(value,min=1)=>Number.isSafeInteger(value)&&value>=min;
const uuid=value=>typeof value==='string'&&value.length===36&&UUID.test(value);
const hash=value=>typeof value==='string'&&value.length===64&&SHA.test(value);
const sourceID=record=>record?.id||record?.analysis_id;
const draftBase=id=>'/setup/local-review-drafts/'+encodeURIComponent(id);
const historyBase=id=>'/setup/understanding/'+encodeURIComponent(id)+'/local-review-drafts';
const validText=value=>typeof value==='string'&&!/[\u0000\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
const sourceRef=value=>({analysis_id:value.analysis_id,source_version:value.source_version,raw_hash:value.raw_hash,contract_version:value.contract_version});
export async function localReviewDigestV261(value){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value)));return [...new Uint8Array(bytes)].map(byte=>byte.toString(16).padStart(2,'0')).join('');}
async function rawDigest(raw){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));return [...new Uint8Array(bytes)].map(byte=>byte.toString(16).padStart(2,'0')).join('');}

function privateValue(value,source){
 need(exact(value,['source','edits'])&&exact(value.source,['analysis_id','source_version','raw_hash','contract_version'])&&uuid(value.source.analysis_id)&&integer(value.source.source_version)&&hash(value.source.raw_hash)&&[4,5].includes(value.source.contract_version)&&Array.isArray(value.edits)&&value.edits.length<=100);
 if(source)need(same(value.source,sourceRef(source)));
 const top=/^\/(businesses|objects|relationships)\/(0|[1-9][0-9]*)$/;
 const citation=/^\/(?:businesses\/(?:0|[1-9][0-9]*)(?:\/steps\/(?:0|[1-9][0-9]*))?|objects\/(?:0|[1-9][0-9]*)(?:\/fields\/(?:0|[1-9][0-9]*))?|relationships\/(?:0|[1-9][0-9]*))\/citations\/(?:0|[1-9][0-9]*)$/;
 for(const edit of value.edits){
  need(object(edit)&&typeof edit.path==='string'&&validText(edit.reason)&&Array.from(edit.reason).length<=1000);
  if(edit.op==='discard_item')need(exact(edit,['op','path','reason'])&&top.exec(edit.path)?.[0]===edit.path);
  else need(edit.op==='replace_citation'&&exact(edit,['op','path','reason','material_id','block_id','start_text','end_text'])&&citation.exec(edit.path)?.[0]===edit.path&&['material_id','block_id'].every(key=>edit[key]===null||validText(edit[key]))&&['start_text','end_text'].every(key=>validText(edit[key])&&Array.from(edit[key]).length<=30));
  need(value.source.contract_version===5||!edit.path.startsWith('/relationships/'));
 }
 need(new TextEncoder().encode(canonical(value)).byteLength<=256*1024,'私人草稿超过256KiB；原输入保留，请先核对修改范围。');return copy(value);
}

export async function checkedPrivateReviewDraftV261(value,account,analysisID){
 need(exact(value,['schema','id','version','workspace_id','owner_id','source_id','source_version','raw_hash','open_request_id','status','created_at','updated_at','value','value_hash','source_state','can_submit','local_submission_available'])&&value.schema==='local-proposal-review-draft-v261'&&value.can_submit===false&&typeof value.local_submission_available==='boolean');
 need(uuid(value.id)&&integer(value.version)&&value.workspace_id===account.workspace_id&&value.owner_id===account.user_id&&value.source_id===analysisID&&integer(value.source_version)&&hash(value.raw_hash)&&uuid(value.open_request_id)&&['editing','submitted'].includes(value.status)&&typeof value.created_at==='string'&&Number.isFinite(Date.parse(value.created_at))&&typeof value.updated_at==='string'&&Number.isFinite(Date.parse(value.updated_at))&&value.source_state==='not_revalidated'&&typeof value.can_submit==='boolean');
 privateValue(value.value);need(value.value.source.analysis_id===value.source_id&&value.value.source.source_version===value.source_version&&value.value.source.raw_hash===value.raw_hash&&hash(value.value_hash)&&await localReviewDigestV261(value.value)===value.value_hash);return copy(value);
}

async function checkedSaveReply(reply,original,account){
 need(exact(reply,['schema','save_receipt','current_draft','current_matches'])&&reply.schema==='local-proposal-review-draft-save-v261');
 const r=reply.save_receipt,b=JSON.parse(original.raw);
 need(exact(r,['schema','draft_id','workspace_id','owner_id','request_id','body_hash','version_before','version_after','value_hash','receipt_hash'])&&r.schema==='local-proposal-review-draft-save-receipt-v261'&&r.draft_id===original.scope.draft_id&&r.workspace_id===account.workspace_id&&r.owner_id===account.user_id&&r.request_id===b.request_id&&r.body_hash===original.body_hash&&r.version_before===b.version&&r.version_after===b.version+1&&r.value_hash===await localReviewDigestV261(b.value)&&hash(r.receipt_hash));
 const{receipt_hash,...body}=r;need(await localReviewDigestV261(body)===receipt_hash);
 const current=await checkedPrivateReviewDraftV261(reply.current_draft,account,original.scope.analysis_id);
 need(current.id===r.draft_id&&same(current.value.source,original.source_ref)&&current.version>=r.version_after&&typeof reply.current_matches==='boolean'&&reply.current_matches===(current.version===r.version_after&&current.value_hash===r.value_hash));return {...copy(reply),current_draft:current};
}

// Every original has an immutable, owner-scoped key. A known receipt is kept in
// a separate resolution entry before the original stops blocking new writes.
// Human drafts use the existing per-tab OwnedForm journal; no legacy key moves.
export class LocalProposalReviewDialogControllerV261{
 constructor({account,sourceRecord,requester=runtimeJSON,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks,current=()=>true,onChange=()=>{},onClose=()=>{},onSaved=()=>{},debounce=450}){
  need(uuid(account?.user_id)&&uuid(account?.workspace_id)&&uuid(sourceID(sourceRecord)));
  Object.assign(this,{account:copy(account),sourceRecord:copy(sourceRecord),requester,storage,tabStorage,locks,current,onChange,onClose,onSaved,debounce});
  this.scope={actor_id:account.user_id,workspace_id:account.workspace_id,analysis_id:sourceID(sourceRecord)};this.prefix='ga-local-proposal-review-v261:'+encodeURIComponent(canonical(this.scope))+':';
  this.token=tabStorage.getItem('ga-setup-csrf');this.active=false;this.epoch=0;this.generation=0;this.role=null;this.loading=false;this.working=false;this.rows=[];this.pending=[];this.draft=null;this.source=null;this.value=null;this.form=null;this.baseVersion=null;this.configRevision=null;this.error='';this.notice='';this.durableState='unknown';this.conflict=null;this.closePrompt=false;this.chain=Promise.resolve();this.originalSequence=0;
 }
 emit(){if(this.active&&this.current())this.onChange(this);}
 check(epoch=this.epoch){need(this.active&&epoch===this.epoch&&this.current(),'账号、原建议或页面已变化；原请求与人工稿保留。');need(this.token&&this.tabStorage.getItem('ga-setup-csrf')===this.token&&this.tabStorage.getItem('ga-setup-csrf-user')===this.account.user_id&&this.tabStorage.getItem('ga-setup-csrf-workspace')===this.account.workspace_id,'登录范围已变化，原稿保留，请在原账号恢复。');}
 headers(){return {'X-Workspace-ID':this.account.workspace_id,'X-CSRF-Token':this.token};}
 async request(path,options={},epoch=this.epoch){
  this.check(epoch);const me=await this.requester('/me',{headers:this.headers(),cache:'no-store'});this.check(epoch);need(me.user_id===this.account.user_id&&me.workspace_id===this.account.workspace_id,'当前账号或工作区已变化，未采用旧回执。');this.role=me.role;
  if(options.method&&options.method!=='GET')need(['admin','operator'].includes(this.role),'当前账号只读，可恢复本人原稿与原申请，不能新写。');
  const result=await this.requester(path,{...options,headers:{...options.headers,...this.headers()},cache:'no-store'});this.check(epoch);
  const after=await this.requester('/me',{headers:this.headers(),cache:'no-store'});this.check(epoch);need(after.user_id===this.account.user_id&&after.workspace_id===this.account.workspace_id);this.role=after.role;return result;
 }
 canWrite(){return !this.loading&&!this.reading&&['admin','operator'].includes(this.role)&&this.draft?.status==='editing'&&!this.submitted&&!!this.source;}
 immutable(key,raw){this.check();const old=this.storage.getItem(key);if(old!==null){need(old===raw,'同一原申请的本机原文已变化，未覆盖。');return;}this.storage.setItem(key,raw);need(this.storage.getItem(key)===raw,'原申请尚未可靠保存在本机，未发送。');}
 async makeOriginal(kind,body,draftID=null,carry=null){
  const raw=JSON.stringify(body),path=kind==='open'?historyBase(this.scope.analysis_id):draftBase(draftID)+(kind==='submit'?'/submissions':'');
  return {schema:'local-proposal-review-original-v261',scope:{...this.scope,draft_id:draftID},source_ref:kind==='open'?null:copy(this.draft.value.source),carry_value:carry===null?null:privateValue(carry),kind,path,method:kind==='save'?'PUT':'POST',raw,raw_sha256:await rawDigest(raw),body_hash:await localReviewDigestV261(body)};
 }
 async checkedOriginal(value){
  need(exact(value,['schema','scope','source_ref','carry_value','kind','path','method','raw','raw_sha256','body_hash'])&&value.schema==='local-proposal-review-original-v261'&&exact(value.scope,['actor_id','workspace_id','analysis_id','draft_id'])&&['actor_id','workspace_id','analysis_id'].every(key=>value.scope[key]===this.scope[key])&&['open','save','submit'].includes(value.kind)&&typeof value.raw==='string'&&hash(value.raw_sha256)&&hash(value.body_hash));
  const body=JSON.parse(value.raw);need(uuid(body.request_id)&&await rawDigest(value.raw)===value.raw_sha256&&await localReviewDigestV261(body)===value.body_hash);
  if(value.carry_value!==null)need(value.kind==='open'&&privateValue(value.carry_value).source.analysis_id===this.scope.analysis_id);
  if(value.kind==='open')need(value.source_ref===null&&value.scope.draft_id===null&&exact(body,['request_id','source_version','raw_hash'])&&integer(body.source_version)&&hash(body.raw_hash));
  else{need(uuid(value.scope.draft_id)&&integer(body.version));privateValue({source:value.source_ref,edits:[]});need(value.source_ref.analysis_id===this.scope.analysis_id);if(value.kind==='save'){need(exact(body,['version','request_id','value']));privateValue(body.value);need(same(body.value.source,value.source_ref));}else need(exact(body,['version','request_id','value_hash','revision','operations','confirm_local_changes'])&&hash(body.value_hash)&&integer(body.revision)&&Array.isArray(body.operations)&&body.operations.length>0&&body.confirm_local_changes===true);}
  const path=value.kind==='open'?historyBase(this.scope.analysis_id):draftBase(value.scope.draft_id)+(value.kind==='submit'?'/submissions':'');need(value.path===path&&value.method===(value.kind==='save'?'PUT':'POST'));return copy(value);
 }
 originalKey(original){return this.prefix+'original:'+original.kind+':'+JSON.parse(original.raw).request_id;}
 resolutionKey(original){return this.prefix+'resolved:'+original.kind+':'+JSON.parse(original.raw).request_id;}
 async scan(epoch=this.epoch){
  const rows=[],carries=new Map();for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(!key?.startsWith(this.prefix+'original:'))continue;const original=await this.checkedOriginal(JSON.parse(this.storage.getItem(key)));this.check(epoch);need(key===this.originalKey(original));const resolved=this.storage.getItem(this.resolutionKey(original));if(resolved!==null){const proof=JSON.parse(resolved);need(exact(proof,['schema','raw_sha256','body_hash','receipt'])&&proof.schema==='local-proposal-review-resolution-v261'&&proof.raw_sha256===original.raw_sha256&&proof.body_hash===original.body_hash);await this.verifyResolution(original,proof.receipt);this.check(epoch);if(original.kind==='open'&&original.carry_value)carries.set(proof.receipt.id,copy(original.carry_value));}else rows.push(original);}
  this.check(epoch);this.carries=carries;this.pending=rows;return rows;
 }
 async resolve(original,receipt){
  const epoch=this.epoch;await this.verifyResolution(original,receipt);this.check(epoch);const value={schema:'local-proposal-review-resolution-v261',raw_sha256:original.raw_sha256,body_hash:original.body_hash,receipt:copy(receipt)},key=this.resolutionKey(original),old=this.storage.getItem(key);
  if(old!==null){const prior=JSON.parse(old);need(exact(prior,['schema','raw_sha256','body_hash','receipt'])&&prior.schema==='local-proposal-review-resolution-v261');await this.verifyResolution(original,prior.receipt);this.check(epoch);need(this.storage.getItem(key)===old&&prior.raw_sha256===value.raw_sha256&&prior.body_hash===value.body_hash&&same(this.resolutionAnchor(original,prior.receipt),this.resolutionAnchor(original,receipt)));}else this.immutable(key,JSON.stringify(value));await this.scan(epoch);
 }
 async verifyResolution(original,receipt){
  if(original.kind==='open'){const view=await checkedPrivateReviewDraftV261(receipt,this.account,this.scope.analysis_id),body=JSON.parse(original.raw);need(view.open_request_id===body.request_id&&view.source_version===body.source_version&&view.raw_hash===body.raw_hash);return view;}
  if(original.kind==='save')return receipt?.schema==='local-proposal-review-save-lookup-v261'?this.checkedSaveLookup(receipt,original,true):checkedSaveReply(receipt,original,this.account);
  return receipt?.schema==='local-proposal-review-submission-lookup-v261'?this.checkedSubmissionLookup(receipt,original):this.checkedSubmission(receipt,original);
 }
 resolutionAnchor(original,receipt){if(original.kind==='open')return Object.fromEntries(['id','workspace_id','owner_id','source_id','source_version','raw_hash','open_request_id'].map(key=>[key,receipt[key]]));if(original.kind==='save')return receipt.schema==='local-proposal-review-save-lookup-v261'?{obsolete:true,draft_id:receipt.current_draft.id}:receipt.save_receipt;return receipt.schema==='local-proposal-review-submission-lookup-v261'?receipt.rejection:receipt.receipt;}
 async checkedSaveLookup(value,original,forResolution=false){
  need(exact(value,['schema','found','save_reply','current_draft','version_state','absence_is_non_execution'])&&value.schema==='local-proposal-review-save-lookup-v261'&&typeof value.found==='boolean'&&value.absence_is_non_execution===false);
  const current=await checkedPrivateReviewDraftV261(value.current_draft,this.account,this.scope.analysis_id),version=JSON.parse(original.raw).version;
  need(current.id===original.scope.draft_id&&same(current.value.source,original.source_ref)&&value.version_state===(current.version===version?'same':current.version>version?'advanced':'ahead'));
  if(value.found){need(value.save_reply!==null&&!forResolution);const reply=await checkedSaveReply(value.save_reply,original,this.account);need(same(reply.current_draft,current));}else need(value.save_reply===null);
  if(forResolution)need(!value.found&&value.version_state==='advanced','尚无原CAS已失效的正面依据，原申请不能归档。');return copy(value);
 }
 async checkedSubmission(value,original,{otherNonce=false}={}){
  need(exact(value,['schema','receipt','analysis','verification_state'])&&value.schema==='local-proposal-review-submission-v261'&&value.verification_state==='saved_not_live_revalidated');
  const r=value.receipt,b=JSON.parse(original.raw),keys=['schema','submission_id','workspace_id','owner_id','draft_id','draft_version','request_id','body_hash','value_hash','source_id','source_raw_hash','source_request_hash','configuration_revision','configuration_hash','operations_hash','policy_hash','derived_analysis_id','derived_result_hash','receipt_hash'];
  need(exact(r,keys)&&r.schema==='local-proposal-review-submission-receipt-v261'&&uuid(r.submission_id)&&r.workspace_id===this.account.workspace_id&&r.owner_id===this.account.user_id&&r.draft_id===original.scope.draft_id&&r.draft_version===b.version&&uuid(r.request_id)&&r.value_hash===b.value_hash&&r.source_id===this.scope.analysis_id&&r.source_raw_hash===original.source_ref.raw_hash&&hash(r.source_request_hash)&&integer(r.configuration_revision)&&hash(r.configuration_hash)&&r.operations_hash===await localReviewDigestV261(b.operations)&&hash(r.policy_hash)&&uuid(r.derived_analysis_id)&&hash(r.derived_result_hash)&&hash(r.receipt_hash));
  const expected=otherNonce?{...b,request_id:r.request_id,revision:r.configuration_revision}:b;need(r.body_hash===await localReviewDigestV261(expected)&&(!otherNonce?r.request_id===b.request_id&&r.configuration_revision===b.revision:true));
  const{receipt_hash,...body}=r;need(await localReviewDigestV261(body)===receipt_hash);const analysis=value.analysis;need(object(analysis)&&analysis.id===r.derived_analysis_id&&integer(analysis.version)&&typeof analysis.status==='string'&&analysis.result_hash===r.derived_result_hash&&[4,5].includes(analysis.contract_version)&&analysis.source_revision===r.configuration_revision);return copy(value);
 }
 async checkedSubmissionLookup(value,original){
  need(exact(value,['schema','found','submission','rejection','absence_is_non_execution'])&&value.schema==='local-proposal-review-submission-lookup-v261'&&typeof value.found==='boolean'&&value.absence_is_non_execution===false);
  if(!value.found){need(value.submission===null&&value.rejection===null);return copy(value);}
  const b=JSON.parse(original.raw);
  if(value.rejection!==null){const r=value.rejection;need(exact(r,['schema','submission_id','workspace_id','owner_id','draft_id','draft_version','request_id','body_hash','code','message','saved_submission_id','receipt_hash'])&&r.schema==='local-proposal-review-rejection-receipt-v261'&&uuid(r.submission_id)&&r.workspace_id===this.account.workspace_id&&r.owner_id===this.account.user_id&&r.draft_id===original.scope.draft_id&&r.draft_version===b.version&&r.request_id===b.request_id&&r.body_hash===original.body_hash&&typeof r.code==='string'&&r.code.length>0&&typeof r.message==='string'&&r.message.length>0&&(r.saved_submission_id===null||uuid(r.saved_submission_id))&&hash(r.receipt_hash));const{receipt_hash,...body}=r;need(await localReviewDigestV261(body)===receipt_hash);if(value.submission!==null){need(r.code==='already_saved');await this.checkedSubmission(value.submission,original,{otherNonce:true});need(r.saved_submission_id===value.submission.receipt.submission_id);}else need(r.saved_submission_id===null);}
  else{need(value.submission!==null);await this.checkedSubmission(value.submission,original);}
  return copy(value);
 }
 async commandLock(work){const epoch=this.epoch;this.check(epoch);if(this.locks?.request)return this.locks.request(this.prefix+'commands',{mode:'exclusive'},async()=>{this.check(epoch);return work();});throw Error('当前环境无法独占核对原申请；原稿已保留，暂不新写。');}
 async stage(original){
  const epoch=this.epoch;await this.scan(epoch);this.check(epoch);need(this.pending.length===0,'还有原申请结果待核对，不能另起新号。');this.leaveApproved=null;this.immutable(this.originalKey(original),JSON.stringify(original));await this.scan(epoch);
 }
 async loadRows(epoch=this.epoch){const raw=await this.request(historyBase(this.scope.analysis_id),{},epoch);need(Array.isArray(raw));const rows=[];for(const row of raw)rows.push(await checkedPrivateReviewDraftV261(row,this.account,this.scope.analysis_id));this.check(epoch);need(new Set(rows.map(row=>row.id)).size===rows.length);this.rows=rows;return rows;}
 async start(){
  this.active=true;const epoch=++this.epoch;this.loading=true;this.working=false;this.reading=0;this.error='';this.emit();
  try{await this.loadRows(epoch);await this.scan(epoch);this.check(epoch);const original=this.pending[0];if(original?.kind==='open'){await this.recover(original);this.check(epoch);}else{const target=original?.scope.draft_id||this.rows.find(row=>row.status==='editing')?.id;if(target)await this.loadDraft(target,epoch);if(original)await this.recover(original);}}
  catch(cause){if(this.active&&epoch===this.epoch&&this.current()){this.error=cause.message;this.durableState='error';}}
  finally{if(this.active&&epoch===this.epoch&&this.current()){this.loading=false;this.emit();}}
 }
 async loadDraft(id,epoch=this.epoch){
  const selection=(this.selection||0)+1;this.selection=selection;const current=()=>{this.check(epoch);need(this.selection===selection,'已选择另一份私人稿，未采用晚到的旧稿。');};
  const view=await checkedPrivateReviewDraftV261(await this.request(draftBase(id),{},epoch),this.account,this.scope.analysis_id);current();
  const prefix=this.prefix+'draft:'+id+':',scope={...this.scope,draft_id:id};
  const form=new PixelOwnedFormV250({prefix,scope,storage:this.storage,session:this.tabStorage,locks:this.locks,check:()=>this.check(epoch),validate:state=>{need(exact(state,['base_version','value'])&&integer(state.base_version));privateValue(state.value,view.value.source);return copy(state);}});
  const local=form.open({base_version:view.version,value:view.value});current();
  const sameContent=same(local.value,view.value);
  this.draft=view;this.form=form;this.value=local.value;this.baseVersion=sameContent?view.version:local.base_version;this.source=null;this.submitted=view.status==='submitted';this.pendingCarry=this.carries?.get(id)||null;this.generation++;this.conflict=!sameContent||form.detached?view:null;this.durableState=this.conflict?'error':'unknown';
  this.emit();
  try{const source=await this.request(draftBase(id)+'/source',{},epoch);inspectLocalProposalSource(source);current();need(same(sourceRef(source),view.value.source));this.source=source;const configuration=await this.request('/setup/draft',{},epoch);need(integer(configuration.revision));current();this.configRevision=configuration.revision;this.durableState=this.conflict||this.pending.some(row=>row.scope.draft_id===id)?'error':'saved';}
  catch(cause){current();this.error=cause.message;this.durableState='error';}
  this.emit();return view;
 }
 snapshot(){return {base_version:this.baseVersion,value:copy(this.value)};}
 localRemember(){need(this.form&&this.value);this.form.remember(this.snapshot());}
 change(value){
  this.check();if(!this.canWrite()||this.working)return;need(object(value)&&same(value.source,this.draft.value.source));this.leaveApproved=null;this.value=copy(value);this.generation++;this.durableState='saving';this.error='';
  try{privateValue(this.value,this.draft.value.source);this.localRemember();}catch(cause){this.durableState='error';this.error=cause.message;this.emit();return;}
  this.emit();clearTimeout(this.timer);this.timer=setTimeout(()=>{this.flush().catch(()=>{});},this.debounce);
 }
 async flush(){
  clearTimeout(this.timer);const epoch=this.epoch,target=this.draft?.id;const operation=async()=>{
   this.check(epoch);need(this.draft?.id===target&&this.value&&this.source);if(same(this.value,this.draft.value)&&this.baseVersion===this.draft.version&&!this.pending.length){this.durableState='saved';this.emit();return true;}
   need(this.canWrite()&&!this.conflict,'本页与服务器有两份不同草稿，请明确保留并选择，不覆盖服务器人稿。');
   await this.commandLock(async()=>{
    await this.scan();need(!this.pending.length,'原保存或提交申请仍未知，请先只读核对原号。');
    const epoch=this.epoch,generation=this.generation,state=this.snapshot();need(await this.form.save(state),'另一页面的人稿已保留，请先处理本机稿冲突。');this.check(epoch);
    const original=await this.makeOriginal('save',{version:this.baseVersion,request_id:crypto.randomUUID(),value:state.value},this.draft.id);this.check(epoch);await this.stage(original);this.durableState='saving';this.emit();
    try{const reply=await this.request(original.path,{method:original.method,body:original.raw},epoch);await this.acceptSave(reply,original,generation);}
    catch(cause){this.check(epoch);this.error=cause.message;this.durableState='error';try{const remote=await checkedPrivateReviewDraftV261(await this.request(draftBase(this.draft.id),{},epoch),this.account,this.scope.analysis_id);if(remote.version!==this.baseVersion)this.conflict=remote;}catch{}this.emit();throw cause;}
   });return true;
  };
  const guarded=async()=>{try{return await operation();}catch(cause){if(this.active&&epoch===this.epoch&&this.current()){this.error=cause.message;this.durableState='error';this.emit();}throw cause;}};
  const next=this.chain.then(guarded,guarded);this.chain=next.catch(()=>{});return next;
 }
 async acceptSave(value,original,sentGeneration){
  const epoch=this.epoch,reply=await checkedSaveReply(value,original,this.account);this.check(epoch);await this.resolve(original,reply);this.check(epoch);
  need(this.draft?.id===reply.current_draft.id);const newer=sentGeneration!==undefined&&this.generation!==sentGeneration;this.draft=reply.current_draft;this.baseVersion=reply.current_draft.version;
  if(!reply.current_matches){this.conflict=reply.current_draft;this.durableState='error';this.notice='原保存已核对，但服务器已有后续版本；两份人稿保留，请明确选择。';}
  else{this.conflict=null;this.localRemember();if(!await this.form.save(this.snapshot())){this.conflict=reply.current_draft;this.durableState='error';throw Error('服务器保存已核对，本机另一页副本仍保留，请明确选择人稿。');}this.check(epoch);this.durableState=newer||!same(this.value,reply.current_draft.value)?'saving':'saved';this.notice='人工核对稿已保存到服务器；原回答与当前配置未改变。';}
  this.emit();if(this.durableState==='saving'){clearTimeout(this.timer);this.timer=setTimeout(()=>this.flush().catch(()=>{}),this.debounce);}
 }
 async openNew(carry=null,preserved=false){
  if(this.working)return;this.working=true;this.error='';this.emit();const epoch=this.epoch;
  try{if(!preserved&&this.value&&!same(this.value,this.draft.value))await this.flush();await this.commandLock(async()=>{await this.scan();need(!this.pending.length,'原申请结果尚待核对，不能另开新稿。');const head=await this.request('/setup/understanding/'+encodeURIComponent(this.scope.analysis_id),{},epoch);need(head.id===this.scope.analysis_id&&integer(head.version)&&hash(head.raw_response_hash),'原回答版本或摘要尚未保存，暂不能开稿。');const original=await this.makeOriginal('open',{request_id:crypto.randomUUID(),source_version:head.version,raw_hash:head.raw_response_hash},null,carry);this.check(epoch);await this.stage(original);const view=await this.request(original.path,{method:'POST',body:original.raw},epoch);await this.resolve(original,view);this.check(epoch);await this.loadRows(epoch);await this.loadDraft(view.id,epoch);this.pendingCarry=original.carry_value;});}
  catch(cause){if(this.active&&epoch===this.epoch){this.error=cause.message;this.durableState='error';}}
  finally{if(this.active&&epoch===this.epoch){this.working=false;this.emit();}}
 }
 async recover(original=this.pending[0]){
  if(!original)return;const epoch=this.epoch;this.reading=(this.reading||0)+1;this.emit();try{await this.checkedOriginal(original);this.check(epoch);this.error='';
  if(original.kind==='open'){const body=JSON.parse(original.raw),lookup=await this.request(historyBase(this.scope.analysis_id)+'/by-request/'+encodeURIComponent(body.request_id)+'?expected_body_hash='+original.body_hash,{},epoch);need(exact(lookup,['schema','found','draft','absence_is_non_execution'])&&lookup.schema==='local-proposal-review-open-lookup-v261'&&typeof lookup.found==='boolean'&&lookup.absence_is_non_execution===false);if(lookup.found){need(lookup.draft!==null);await this.resolve(original,lookup.draft);this.check(epoch);await this.loadDraft(lookup.draft.id,epoch);this.pendingCarry=original.carry_value;}else{need(lookup.draft===null);this.notice='尚未找到原开稿回执；结果仍未知，原号与原文保留。';}}
  else if(original.kind==='save'){const body=JSON.parse(original.raw),lookup=await this.checkedSaveLookup(await this.request(draftBase(original.scope.draft_id)+'/saves/by-request/'+encodeURIComponent(body.request_id)+'?expected_body_hash='+original.body_hash+'&expected_version='+body.version,{},epoch),original);this.check(epoch);if(this.draft?.id!==original.scope.draft_id)await this.loadDraft(original.scope.draft_id,epoch);if(lookup.found)await this.acceptSave(lookup.save_reply,original);else{if(lookup.version_state==='advanced'){this.conflict=lookup.current_draft;this.expired={original:copy(original),proof:lookup};}this.notice='原保存回执尚未找到，不能据此认定没有执行；原号、原文与本页说明保留。';}}
  else await this.recoverSubmission(original,epoch);
  this.check(epoch);this.emit();}finally{if(this.active&&epoch===this.epoch){this.reading=Math.max(0,this.reading-1);this.emit();}}
 }
 async resend(original=this.pending[0]){
  if(!original||this.working)return;this.working=true;this.emit();const epoch=this.epoch;
  try{await this.commandLock(async()=>{await this.scan();need(this.pending.some(row=>row.raw===original.raw&&row.raw_sha256===original.raw_sha256),'另一页面的原申请已变化，未发送本页旧选择。');await this.recover(original);this.check(epoch);if(!this.pending.some(row=>row.raw===original.raw&&row.raw_sha256===original.raw_sha256))return;const result=await this.request(original.path,{method:original.method,body:original.raw},epoch);if(original.kind==='open'){await this.resolve(original,result);await this.loadDraft(result.id,epoch);}else if(original.kind==='save')await this.acceptSave(result,original);else await this.acceptSubmission(result,original);});}
  catch(cause){if(this.active&&epoch===this.epoch){this.error=cause.message;this.durableState='error';}}
  finally{if(this.active&&epoch===this.epoch){this.working=false;this.emit();}}
 }
 async choose(id){if(this.loading||this.working||id===this.draft?.id)return;const epoch=this.epoch;try{if(!this.guard()){this.closePrompt=true;this.error='请先保存本页人稿或核对原申请，再打开另一份稿。';this.emit();return;}this.loading=true;this.emit();await this.scan(epoch);need(!this.pending.length,'还有原申请待核对，未切换草稿。');await this.loadDraft(id,epoch);}catch(cause){if(this.active&&epoch===this.epoch){this.error=cause.message;}}finally{if(this.active&&epoch===this.epoch){this.loading=false;this.emit();}}}
 async submit(payload){
  if(this.working)return;this.working=true;this.error='';this.notice='';this.emit();const epoch=this.epoch,generation=this.generation;
  try{
   need(this.canWrite()&&this.draft.local_submission_available&&integer(this.configRevision),'当前只支持保存私人稿，另存建议入口尚未启用或当前账号只读。');
   await this.flush();this.check(epoch);need(generation===this.generation&&same(payload,localProposalReviewPayload(this.source,this.value)),'保存期间人稿已继续编辑，请先核对当前修改，再明确另存。');
   await this.commandLock(async()=>{await this.scan();need(!this.pending.length,'原申请仍待核对，未另起提交编号。');need(this.durableState==='saved'&&same(this.value,this.draft.value));
    const original=await this.makeOriginal('submit',{version:this.draft.version,request_id:crypto.randomUUID(),value_hash:this.draft.value_hash,revision:this.configRevision,operations:payload.operations,confirm_local_changes:true},this.draft.id);this.check(epoch);await this.stage(original);
    try{const reply=await this.request(original.path,{method:'POST',body:original.raw},epoch);await this.acceptSubmission(reply,original);}
    catch(cause){this.check(epoch);this.error=cause.message;this.durableState='error';try{await this.recoverSubmission(original,epoch);}catch(readError){this.check(epoch);this.error=cause.message+' '+readError.message;}this.emit();}
   });
  }catch(cause){if(this.active&&epoch===this.epoch&&this.current()){this.error=cause.message;this.emit();}}
  finally{if(this.active&&epoch===this.epoch&&this.current()){this.working=false;this.emit();}}
 }
 async acceptSubmission(value,original){
  const epoch=this.epoch,saved=await this.checkedSubmission(value,original);this.check(epoch);await this.resolve(original,saved);this.check(epoch);this.savedSubmission=saved;this.submitted=true;if(same(this.value,this.draft.value))this.conflict=null;this.durableState='saved';this.error='';this.notice='已另存为本工作区待核对建议；原回答、人稿与原申请均保留。未调用云、应用配置或授权设备。';this.emit();
  try{this.onSaved(copy(saved.analysis));}catch{this.error='建议已另存，但页面尚未打开该记录；请使用下方已保存记录入口。';this.emit();}
 }
 async recoverSubmission(original,epoch=this.epoch){
  const b=JSON.parse(original.raw),reply=await this.checkedSubmissionLookup(await this.request(draftBase(original.scope.draft_id)+'/submissions/by-request/'+encodeURIComponent(b.request_id)+'?expected_body_hash='+original.body_hash,{},epoch),original);this.check(epoch);
  if(!reply.found){this.notice='原另存申请结果仍未知；原号与原文保留，请先核对，不自动再次创建。';this.durableState='error';return;}
  if(reply.rejection===null){await this.acceptSubmission(reply.submission,original);return;}
  await this.resolve(original,reply);this.check(epoch);this.rejection=reply.rejection;this.relatedSubmission=reply.submission;this.error='此次另存已明确拒绝：'+reply.rejection.message;this.notice='原申请与拒绝回执已保留，人工稿没有清空；修改并可靠保存后可明确另行提交。';
  const remote=await checkedPrivateReviewDraftV261(await this.request(draftBase(original.scope.draft_id),{},epoch),this.account,this.scope.analysis_id);this.check(epoch);need(same(remote.value.source,original.source_ref));this.draft=remote;this.baseVersion=remote.version;this.submitted=remote.status==='submitted';this.durableState=same(this.value,remote.value)?'saved':'error';this.emit();
 }
 async preserveAndChoose(mode){
  if(this.working||!this.form||!this.conflict)return;this.working=true;this.error='';this.emit();const epoch=this.epoch,kept=this.snapshot();let carry=null;
  try{await this.commandLock(async()=>{
   await this.scan();const original=this.expired?.original;
   if(this.pending.length){need(original&&this.pending.length===1&&this.pending[0].raw===original.raw,'仍有未知申请，不能换稿或另开编号。');await this.recover(original);this.check(epoch);if(this.pending.length){need(this.expired?.original.raw===original.raw);await this.checkedSaveLookup(this.expired.proof,original,true);await this.resolve(original,this.expired.proof);}}
   this.form.archive(kept);this.check(epoch);this.expired=null;
   if(mode==='server'){
    const remote=await checkedPrivateReviewDraftV261(await this.request(draftBase(this.draft.id),{},epoch),this.account,this.scope.analysis_id);this.check(epoch);need(same(remote.value.source,this.draft.value.source));
    need(await this.form.save({base_version:remote.version,value:remote.value},{replace:true}),'服务器稿尚未可靠保存在本机，原人稿保留。');this.check(epoch);this.value=copy(remote.value);this.baseVersion=remote.version;this.conflict=null;await this.loadDraft(remote.id,epoch);this.notice='已保留本页副本，并恢复服务器稿；没有替换旧申请正文。';
   }else{need(mode==='copy');carry=kept.value;this.notice='本页副本已独立保留，将明确另开一份私人稿。';}
  });}
  catch(cause){if(this.active&&epoch===this.epoch){this.error=cause.message;carry=null;}}
  finally{if(this.active&&epoch===this.epoch){this.working=false;this.emit();}}
  if(carry&&this.active&&epoch===this.epoch)await this.openNew(carry,true);
 }
 applyCarry(){
  try{this.check();need(this.pendingCarry&&this.canWrite()&&!this.working&&!this.pending.length&&this.value.edits.length===0,'新稿已有输入或仍有原申请，未覆盖任何人稿。');const prior=this.pendingCarry,current=this.draft.value.source;need(prior.source.analysis_id===current.analysis_id&&prior.source.raw_hash===current.raw_hash&&prior.source.contract_version===current.contract_version,'新稿对应原答已变化，原副本仍保留，未静默换绑编辑意图。');this.change({source:copy(current),edits:copy(prior.edits)});this.pendingCarry=null;this.emit();}catch(cause){this.error=cause.message;this.emit();}
 }
 async retrySource(){const epoch=this.epoch,id=this.draft?.id;if(!id||this.working||this.loading)return;this.working=true;this.error='';this.emit();try{const source=await this.request(draftBase(id)+'/source',{},epoch);inspectLocalProposalSource(source);this.check(epoch);need(this.draft?.id===id&&same(sourceRef(source),this.draft.value.source));this.source=source;if(this.configRevision===null){const value=await this.request('/setup/draft',{},epoch);need(integer(value.revision));this.check(epoch);this.configRevision=value.revision;}this.durableState=this.pending.length||this.conflict?'error':same(this.value,this.draft.value)?'saved':'saving';}catch(cause){if(this.active&&epoch===this.epoch){this.source=null;this.error=cause.message;this.durableState='error';}}finally{if(this.active&&epoch===this.epoch){this.working=false;this.emit();}}}
 async openSaved(){const epoch=this.epoch;try{this.check(epoch);const saved=this.savedSubmission||this.relatedSubmission;need(saved);const analysis=await this.request('/setup/understanding/'+encodeURIComponent(saved.analysis.id),{},epoch);need(analysis.id===saved.analysis.id&&analysis.result_hash===saved.receipt.derived_result_hash);this.check(epoch);this.onSaved(copy(analysis));}catch(cause){if(this.active&&epoch===this.epoch){this.error=cause.message;this.emit();}}}
 async refreshRevision(){const epoch=this.epoch;try{const value=await this.request('/setup/draft',{},epoch);need(integer(value.revision));this.check(epoch);this.configRevision=value.revision;this.notice='已读取当前配置第'+value.revision+'版；提交仍需服务器检查版本与权限。';this.emit();}catch(cause){if(this.active&&epoch===this.epoch){this.error=cause.message;this.emit();}}}
 guard(){try{this.check();if(this.leaveApproved?.generation===this.generation&&this.form?.guard(this.snapshot())&&this.leaveApproved.pending===canonical(this.pending)&&this.pending.every(row=>this.storage.getItem(this.originalKey(row))===JSON.stringify(row)))return true;if(!this.draft)return !this.pending.length;return !this.pending.length&&!this.conflict&&same(this.value,this.draft.value)&&this.durableState==='saved';}catch{return false;}}
 close(){if(this.guard()){this.onClose();return;}this.closePrompt=true;this.notice='尚有未保存或待核对的内容，请在本页继续保存；关闭不会取消已发送的申请。';this.emit();}
 async saveAndClose(){const epoch=this.epoch;try{await this.flush();this.check(epoch);if(this.guard())this.onClose();else{this.closePrompt=true;this.emit();}}catch(cause){if(this.active&&epoch===this.epoch){this.error=cause.message;this.closePrompt=true;this.emit();}}}
 async preserveAndClose(){const epoch=this.epoch;try{this.check(epoch);if(this.value){this.localRemember();need(this.form.guard(this.snapshot()),'本机副本尚未可靠读回，请继续保留页面。');}await this.scan(epoch);this.check(epoch);this.leaveApproved={generation:this.generation,pending:canonical(this.pending)};this.onClose();}catch(cause){if(this.active&&epoch===this.epoch){this.error=cause.message;this.emit();}}}
 dispose(){clearTimeout(this.timer);this.active=false;this.epoch++;}
}

const Button=props=><button type="button" {...props}/>;
const time=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});
export default function LocalProposalReviewDialogV261({sourceRecord,account,requester=runtimeJSON,onClose=()=>{},onSaved=()=>{},onRegisterGuard,storage,tabStorage,locks=globalThis.navigator?.locks,debounce=450,Btn=Button,embedded=false}){
 const owner=canonical({actor_id:account?.user_id,workspace_id:account?.workspace_id,analysis_id:sourceID(sourceRecord)}),scope=useRef(owner);scope.current=owner;const [,render]=useState(0);
 const holder=useMemo(()=>{try{return {controller:new LocalProposalReviewDialogControllerV261({sourceRecord,account,requester,storage:storage??globalThis.localStorage,tabStorage:tabStorage??globalThis.sessionStorage,locks,debounce,current:()=>scope.current===owner,onChange:()=>render(value=>value+1),onClose,onSaved})};}catch(cause){return {error:cause.message};}},[owner,requester,storage,tabStorage,locks]);
 const c=holder.controller,callbacks=useRef({onClose,onSaved});callbacks.current={onClose,onSaved};if(c){c.onClose=()=>callbacks.current.onClose();c.onSaved=value=>callbacks.current.onSaved(value);}
 useEffect(()=>{if(!c)return;c.start();return()=>c.dispose();},[c]);
 useEffect(()=>{if(!onRegisterGuard)return;const check=()=>c?c.guard():false;onRegisterGuard(check);return()=>onRegisterGuard(null,check);},[c,onRegisterGuard]);
 function action(work){Promise.resolve().then(work).catch(cause=>{if(c?.active&&c.current()){c.error=cause.message;c.emit();}});}
 return <section role={embedded?'region':'dialog'} aria-modal={embedded?undefined:true} aria-label="私人核对原建议">
  <h2>仅在本地核对原建议</h2><p>未提交的私人稿仅本人可见。另存后是本工作区的待核对建议；原回答保留，不调用云、不应用配置、不授权设备。</p>
  {holder.error&&<p role="alert">{holder.error}</p>}
  {c&&<>
   {c.loading&&<p role="status">正在读取本人已保存的核对稿与原申请…</p>}{c.error&&<p role="alert">{c.error}</p>}{c.notice&&<p role="status">{c.notice}</p>}
   {c.role&&!['admin','operator'].includes(c.role)&&<p>当前账号只读，可恢复本人已保存的人稿与原回执，不新写。</p>}
   <section aria-label="本人服务器核对稿"><h3>本人已保存的核对稿 · 本次返回{c.rows.length}份</h3>{c.rows.length?<ol>{c.rows.map((row,index)=><li key={row.id}>第{index+1}份私人稿 · 原理解第{row.source_version}版 · {row.status==='editing'?'尚未提交':'已另存建议'} · {time(row.updated_at)}<Btn type="button" disabled={c.loading||c.working||row.id===c.draft?.id} onClick={()=>action(()=>c.choose(row.id))}>恢复这份私人核对稿</Btn></li>)}</ol>:<p>{c.loading?'正在读取…':'尚未返回本人核对稿；这不表示没有在途申请。'}</p>}<Btn type="button" disabled={c.loading||c.working||c.pending.length>0||!['admin','operator'].includes(c.role)} onClick={()=>action(()=>c.openNew())}>明确另开一份私人核对稿</Btn></section>
   {c.pending.length>0&&<section aria-label="待核对的原申请"><h3>原申请结果待核对 · {c.pending.length}份</h3><p>未找到回执不代表没有执行；不自动换号或再次创建。</p>{c.pending.map((original,index)=><article key={original.raw_sha256}><p>原申请{index+1}：{({open:'开立私人稿',save:'保存私人稿',submit:'另存待核对建议'})[original.kind]}</p><Btn type="button" disabled={c.loading||c.working} onClick={()=>action(()=>c.recover(original))}>只读核对原申请</Btn><Btn type="button" disabled={c.loading||c.working||!['admin','operator'].includes(c.role)} onClick={()=>action(()=>c.resend(original))}>明确按原文重试同一次申请</Btn></article>)}</section>}
   {c.conflict&&<section aria-label="本页与服务器两份稿"><h3>两份人稿均保留，请明确选择</h3><p>本页说明和服务器第{c.conflict.version}版稿不同，未自动覆盖。{c.expired&&'服务器已证明旧CAS目前失效；这仍不证明原申请从未执行。'}</p><Btn type="button" disabled={c.working||c.pending.length>0&&!c.expired} onClick={()=>action(()=>c.preserveAndChoose('server'))}>保留本页副本，恢复服务器稿</Btn><Btn type="button" disabled={c.working||c.pending.length>0&&!c.expired||!['admin','operator'].includes(c.role)} onClick={()=>action(()=>c.preserveAndChoose('copy'))}>保留本页副本，另开新稿</Btn></section>}
   {c.pendingCarry&&<section aria-label="另开前保留的本页副本"><p>另开前的说明已独立保留；复制前仍须对应同一原答，不自动换绑。</p><Btn type="button" disabled={c.working||!c.canWrite()||c.value?.edits.length>0||c.pending.length>0} onClick={()=>c.applyCarry()}>将保留的本页说明复制到此新稿</Btn></section>}
   {c.draft&&<section aria-label="当前私人核对稿"><p>当前私人稿第{c.draft.version}版 · {c.durableState==='saved'?'已核对服务器保存':c.durableState==='saving'?'正在保存人工稿':'保存状态待核对'}</p><p>另存所用当前配置：{c.configRevision?'第'+c.configRevision+'版':'尚未读取'}。配置变化时服务器会拒绝，不会静默采用新版本。</p><Btn type="button" disabled={c.working||c.loading} onClick={()=>action(()=>c.refreshRevision())}>重新核对当前配置版本</Btn><Btn type="button" disabled={c.working||!c.canWrite()||c.pending.length>0} onClick={()=>action(()=>c.flush())}>继续保存私人稿</Btn>{!c.draft.local_submission_available&&<p>当前服务尚未启用另存待核对建议；私人稿仍可正常保存。</p>}
    {!integer(c.configRevision)&&<p>当前配置版本尚未读取，私人稿仍保留；另存前请重新核对配置版本。</p>}
    {c.source?<LocalProposalReviewV261 source={c.source} draft={c.value} onDraftChange={c.canWrite()?value=>c.change(value):undefined} durableState={c.loading?'unknown':c.durableState} busy={c.working} onSubmit={c.canWrite()&&c.draft.local_submission_available&&integer(c.configRevision)?value=>c.submit(value):undefined} Btn={Btn}/>:<><p role="status">原建议或授权原文尚未完成核对，已存人稿保留，暂不编辑或提交。</p><Btn type="button" disabled={c.working||c.loading} onClick={()=>action(()=>c.retrySource())}>重新读取原建议与授权原文</Btn></>}
   </section>}
   {(c.savedSubmission||c.relatedSubmission)&&<Btn type="button" disabled={c.working} onClick={()=>action(()=>c.openSaved())}>打开已保存的待核对建议</Btn>}
   {c.closePrompt&&<section aria-label="关闭前保留人工稿"><p>尚有未保存或待核对内容。关闭不取消在途请求；本机副本可靠读回后才允许保留并关闭。</p><Btn type="button" disabled={c.working} onClick={()=>action(()=>c.saveAndClose())}>继续保存并关闭</Btn><Btn type="button" disabled={c.working} onClick={()=>action(()=>c.preserveAndClose())}>保留本机副本和原申请并关闭</Btn><Btn type="button" onClick={()=>{c.closePrompt=false;c.emit();}}>继续核对</Btn></section>}
  </>}
  <Btn type="button" onClick={()=>c?c.close():onClose()}>返回原建议</Btn>
 </section>;
}
