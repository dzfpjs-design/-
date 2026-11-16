import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {BusinessFlowReviewController,REVIEW_BASE,reviewCopy} from '../src/business-flow-review-controller.mjs';
import {nestedCapacity} from '../src/nested-flow-review.mjs';
import {nestedPlan,diagnostics} from './nested-flow-review-fixture.mjs';
export const id=n=>'70000000-0000-4000-8000-'+n.toString(16).padStart(12,'0');
export const digest=raw=>createHash('sha256').update(raw).digest('hex');
export const flags={semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};
export function memory(){const values=new Map();return {fail:false,getItem:key=>values.get(key)??null,setItem(key,value){if(this.fail)throw Error('synthetic local quota');values.set(key,value);},removeItem:key=>values.delete(key),snapshot:()=>Object.fromEntries(values)};}
export function fixture({questions=1}={}){
 const storage=memory(),session=memory(),scope={producerId:id(1),actorId:id(2),workspaceId:id(3)},calls=[],records=new Map(),mode={lost:false,drop:false,foreign:false,rejection:null,malformed:null,hideReceipt:false};
 session.setItem('ga-setup-csrf','public-nested-fixture');session.setItem('ga-setup-csrf-user',scope.actorId);session.setItem('ga-setup-csrf-workspace',scope.workspaceId);
 const value=nestedPlan();if(!questions)value.business.questions=[];
 const detail={attempt_id:id(4),name:value.business.name,version:2,saved:true,value:reviewCopy(value),baseline_value:reviewCopy(value),note:'公开合成的递归修订稿',source_available:true,diagnostics:diagnostics(value),source_binding:{schema:'business-flow-nested-component-review-source-v194',generation_profile:'flow-v193',value_schema:value.schema,original_protocol_valid:false},generation_recovery:{schema:'business-flow-nested-component-view-v194',component_only:true,component_path:'/plan',original_protocol_valid:false,issues:[]},...flags};
 const original=reviewCopy(detail),api=async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,raw:options.body});if(path==='/me')return {user_id:mode.foreign?id(99):scope.actorId,workspace_id:scope.workspaceId,role:'operator'};
  assert.equal(options.headers['X-Workspace-ID'],scope.workspaceId);assert.equal(options.headers['X-CSRF-Token'],'public-nested-fixture');
  if(path===REVIEW_BASE+'/candidates?producer_id='+scope.producerId)return {items:[{attempt_id:detail.attempt_id,name:detail.name,status:'failed',saved_version:detail.version}]};
  if(path===REVIEW_BASE+'/attempts/'+detail.attempt_id)return reviewCopy(detail);
  if(path.startsWith(REVIEW_BASE+'/attempts/'+detail.attempt_id+'/nested-review-preview?review_mode=')){
   assert.equal(method,'GET');const reviewMode=new URLSearchParams(path.split('?')[1]).get('review_mode');assert.ok(['author_self_review','engineering_simulation'].includes(reviewMode));assert.ok(reviewMode==='engineering_simulation'||detail.value.business.questions.length===0,'the real author preview cannot accept unresolved questions');const counts=nestedCapacity(detail.value);
   return {schema:'business-flow-nested-review-preview-v195',attempt_id:detail.attempt_id,expected_version:detail.version,save_request_id:id(8),value_hash:'a'.repeat(64),source_snapshot_hash:'b'.repeat(64),business_name:detail.value.business.name,flows:detail.value.business.flows.map((flow,index)=>({flow_index:index,name:flow.name,action_count:counts[index].actions})),open_question_count:detail.value.business.questions.length,review_mode:reviewMode,simulation_only:reviewMode==='engineering_simulation',value_schema:detail.value.schema,...flags};
  }
  if(path===REVIEW_BASE+'/attempts/'+detail.attempt_id+'/nested-reviews')return [...records.values()].map(record=>reviewCopy(record.result));
  if(path.startsWith(REVIEW_BASE+'/nested-review-requests/')){assert.equal(method,'GET');const nonce=path.split('/nested-review-requests/')[1].split('?')[0],sha=new URLSearchParams(path.split('?')[1]).get('expected_sha256'),record=mode.hideReceipt?null:records.get(nonce);if(record)assert.equal(sha,record.sha);return {schema:'business-flow-nested-review-original-result-v195',request_id:nonce,body_sha256:sha,found:!!record,result:record?reviewCopy(record.result):null};}
  assert.equal(path,REVIEW_BASE+'/attempts/'+detail.attempt_id+'/qualify-nested');assert.equal(method,'POST');const body=JSON.parse(options.body);
  assert.deepEqual(Object.keys(body),['schema','request_id','expected_version','save_request_id','value_hash','ack_scope','ack_inferences','reason','review_mode']);assert.equal(body.schema,'business-flow-nested-review-command-v195');
  const journal=JSON.parse(storage.getItem(session.getItem(`business-flow-review:v1:${scope.workspaceId}:${scope.actorId}:${scope.producerId}:tab-pointer`))),pending=journal.nestedAuthorReviews[detail.attempt_id].pending;assert.equal(pending.raw,options.body);assert.equal(pending.sha,digest(options.body));
  if(mode.drop)throw Error('synthetic submission did not arrive');if(mode.rejection)throw Object.assign(Error('synthetic explicit refusal'),{status:409,detail:reviewCopy(mode.rejection)});
  const old=records.get(body.request_id);if(old){assert.equal(old.raw,options.body);return reviewCopy(old.result);}
  const simulation=body.review_mode==='engineering_simulation',result={schema:'business-flow-nested-review-result-v195',id:id(100+records.size),version:1,status:simulation?'engineering_reviewed':'qualified',attempt_id:detail.attempt_id,draft_version:body.expected_version,save_request_id:body.save_request_id,request_id:body.request_id,value_hash:body.value_hash,qualification_hash:'c'.repeat(64),source_snapshot_hash:'b'.repeat(64),review_record_hash:'d'.repeat(64),review_mode:body.review_mode,simulation_only:simulation,human_review:simulation?'not_human_review':'author_self_reviewed',reviewed_by:scope.actorId,reviewed_at:'2026-10-24T12:00:00+00:00',value_schema:detail.value.schema,value:reviewCopy(detail.value),source_binding:reviewCopy(detail.source_binding),ack_scope:true,ack_inferences:true,reason:body.reason,...flags,...(mode.malformed||{})};
  records.set(body.request_id,{raw:options.body,sha:digest(options.body),result:reviewCopy(result)});if(mode.lost){mode.lost=false;throw Error('synthetic response lost after committed');}return reviewCopy(result);
 };
 const options={...scope,api,storage,session,crypto:webcrypto,locks:{request:async(_key,options,fn)=>{assert.equal(options.mode,'exclusive');return fn();}}};
 return {scope,storage,session,calls,records,mode,detail,original,options,posts:()=>calls.filter(call=>call.method==='POST')};
}
export async function open(f){const c=new BusinessFlowReviewController(f.options);await c.open();await c.select(f.detail.attempt_id);return c;}
