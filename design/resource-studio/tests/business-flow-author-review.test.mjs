import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {BusinessFlowReviewController,REVIEW_BASE,reviewCopy} from '../src/business-flow-review-controller.mjs';
import {authorReviewState,editAuthorReview,previewAuthorReview,submitAuthorReview,recoverAuthorReview,archiveRejectedAuthorReview,loadAuthorReviews} from '../src/business-flow-author-review.mjs';
const id=n=>'20000000-0000-4000-8000-'+n.toString(16).padStart(12,'0'),h=s=>createHash('sha256').update(s).digest('hex');
const flags={semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};
function memory(){const values=new Map();return {fail:false,getItem:k=>values.get(k)??null,setItem(k,v){if(this.fail)throw Error('synthetic quota');values.set(k,v);},removeItem:k=>values.delete(k)};}
function fixture(){
 const storage=memory(),session=memory(),scope={producerId:id(1),actorId:id(2),workspaceId:id(3)},calls=[],records=new Map(),mode={lost:false,drop:false,conflict:false,foreign:false,malformed:false};
 session.setItem('ga-setup-csrf','public-fixture-token');session.setItem('ga-setup-csrf-user',scope.actorId);session.setItem('ga-setup-csrf-workspace',scope.workspaceId);
 const value={business:{name:'公开合成业务',flows:[{flow_id:'one',name:'公开流程',trigger:{description:'人工发起'},nodes:[],edges:[]}],questions:[]}};
 const detail={attempt_id:id(4),version:3,saved:true,value,baseline_value:{old:'unchanged'},note:'原人工说明',source_available:true,diagnostics:{},...flags};
 const preview={schema:'business-flow-author-review-preview-v1',attempt_id:id(4),expected_version:3,save_request_id:id(8),value_hash:'a'.repeat(64),source_snapshot_hash:'b'.repeat(64),business_name:'公开合成业务',flows:[],current_source_checked:true,...flags};
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',raw:options.body});
  if(path==='/me')return {user_id:mode.foreign?id(99):scope.actorId,workspace_id:scope.workspaceId,role:'operator'};
  assert.equal(options.headers['X-Workspace-ID'],scope.workspaceId);
  if(path.includes('/candidates?'))return {items:[{attempt_id:id(4),name:'公开候选',status:'failed',saved_version:3}]};
  if(path===REVIEW_BASE+'/attempts/'+id(4))return reviewCopy(detail);
  if(path.endsWith('/author-review-preview'))return reviewCopy(preview);
  if(path.endsWith('/author-reviews'))return [...records.values()].map(r=>reviewCopy(r.result));
  if(path.includes('/author-review-requests/')){const nonce=path.split('/author-review-requests/')[1].split('?')[0],sha=new URLSearchParams(path.split('?')[1]).get('expected_sha256'),record=records.get(nonce);if(record)assert.equal(record.sha,sha);return {schema:'author-review-original-result-v1',request_id:nonce,found:!!record,body_sha256:sha,result:record?.result||null};}
  assert.ok(path.endsWith('/qualify'));assert.equal(options.method,'POST');const body=JSON.parse(options.body);
  if(mode.drop)throw Error('synthetic request never arrived');
  if(mode.conflict)throw Object.assign(Error('synthetic proven version conflict'),{status:409,detail:{code:'business_flow_author_review_version_conflict',not_applied:true,current_version:4}});
  const result={schema:'author-review-result-v1',id:id(10),version:1,status:'qualified',attempt_id:id(4),draft_id:id(9),draft_version:body.expected_version,save_request_id:body.save_request_id,request_id:body.request_id,value_hash:body.value_hash,qualification_hash:'c'.repeat(64),source_snapshot_hash:'b'.repeat(64),review_record_hash:'d'.repeat(64),human_review:'author_self_reviewed',reviewed_by:scope.actorId,reviewed_at:'2026-10-24T12:00:00+00:00',value:reviewCopy(value),source_binding:{},baseline_hash:'e'.repeat(64),source_snapshot:{},ack_scope:true,ack_inferences:true,reason:body.reason,...flags};
  records.set(body.request_id,{raw:options.body,sha:h(options.body),result});
  if(mode.lost){mode.lost=false;throw Error('synthetic response lost after committed');}
  return mode.malformed?{...result,reviewed_by:id(99)}:reviewCopy(result);
 };
 const options={...scope,api,storage,session,crypto:webcrypto,locks:{request:async(k,o,f)=>f()}};
 return {options,storage,scope,session,calls,mode,detail,records,posts:()=>calls.filter(x=>x.method==='POST')};
}
async function open(f){const c=new BusinessFlowReviewController(f.options);await c.open();await c.select(id(4));return c;}
async function ready(c){await previewAuthorReview(c);editAuthorReview(c,{reason:'公开软件接口核验，不代表真实业务验收',ack_scope:true,ack_inferences:true});}

test('explicit author check persists exact outbox before GET-first POST and never mutates original flow',async()=>{
 const f=fixture(),c=await open(f),before=reviewCopy(c.form.value);await ready(c);const result=await submitAuthorReview(c);
 assert.equal(f.posts().length,1);assert.equal(result.human_review,'author_self_reviewed');assert.equal(result.can_execute,false);assert.equal(authorReviewState(c).pending,null);
 const post=f.posts()[0],nonce=JSON.parse(post.raw).request_id;assert.ok(f.calls.findIndex(x=>x.path.includes('/author-review-requests/'+nonce))<f.calls.indexOf(post));assert.deepEqual(c.form.value,before);assert.equal(c.form.dirty,false);c.dispose();
});
test('lost response survives reload and original recovery does not POST or overwrite later edits',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.lost=true;await assert.rejects(submitAuthorReview(c));const raw=authorReviewState(c).pending.raw;c.edit(c.form.value,'后来输入须保留');c.dispose();
 const next=await open(f);assert.equal(authorReviewState(next).pending.raw,raw);await recoverAuthorReview(next);assert.equal(f.posts().length,1);assert.equal(next.form.note,'后来输入须保留');assert.equal(next.form.dirty,true);assert.equal(authorReviewState(next).result.draft_version,3);next.dispose();
});
test('unknown receipt requires explicit same-original retry and cannot be archived',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.drop=true;await assert.rejects(submitAuthorReview(c));const raw=authorReviewState(c).pending.raw;await recoverAuthorReview(c);assert.equal(f.posts().length,1);assert.throws(()=>archiveRejectedAuthorReview(c));
 f.mode.drop=false;await recoverAuthorReview(c,true);assert.equal(f.posts().length,2);assert.equal(f.posts()[1].raw,raw);c.dispose();
});
test('only explicit proven version conflict is archived and original raw is retained',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.conflict=true;await assert.rejects(submitAuthorReview(c));const raw=authorReviewState(c).pending.raw;archiveRejectedAuthorReview(c);assert.equal(authorReviewState(c).pending,null);assert.equal(authorReviewState(c).archives[0].raw,raw);assert.equal(authorReviewState(c).preview,null);c.dispose();
});
test('dirty flow, missing acknowledgements and local persistence failure cannot send',async()=>{
 const f=fixture(),c=await open(f);c.edit(c.form.value,'未保存的后来输入');await assert.rejects(previewAuthorReview(c));assert.equal(f.posts().length,0);c.form.dirty=false;await previewAuthorReview(c);await assert.rejects(submitAuthorReview(c));editAuthorReview(c,{reason:'明确核对',ack_scope:true,ack_inferences:true});f.storage.fail=true;await assert.rejects(submitAuthorReview(c));assert.equal(f.posts().length,0);assert.equal(c.localSaved,false);c.dispose();
});
test('wrong actor result is not accepted or presented as a completed review',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.malformed=true;await assert.rejects(submitAuthorReview(c));assert.ok(authorReviewState(c).pending);assert.equal(authorReviewState(c).result,null);c.dispose();
});
test('server history can be read in a fresh page and remains version specific',async()=>{
 const f=fixture(),c=await open(f);await ready(c);await submitAuthorReview(c);f.session.removeItem(c.pointer);c.dispose();
 const next=await open(f);await loadAuthorReviews(next);assert.equal(authorReviewState(next).history.length,1);assert.equal(authorReviewState(next).history[0].draft_version,3);next.dispose();
});
test('different account cannot continue cached author review or send original request',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.session.setItem('ga-setup-csrf-user',id(99));await assert.rejects(submitAuthorReview(c));assert.equal(f.posts().length,0);c.dispose();
});
test('read-only original recovery still works when this browser has no write locks',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.lost=true;await assert.rejects(submitAuthorReview(c));c.locks=null;await recoverAuthorReview(c);assert.equal(authorReviewState(c).pending,null);assert.equal(f.posts().length,1);await assert.rejects(previewAuthorReview(c),/多窗口/);c.dispose();
});
