import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewCopy} from '../src/business-flow-review-controller.mjs';
import {nestedAuthorReviewState as state,nestedAuthorReviewForm as form,editNestedAuthorReview as edit,previewNestedAuthorReview as preview,submitNestedAuthorReview as submit,recoverNestedAuthorReview as recover,archiveRejectedNestedAuthorReview as archive,loadNestedAuthorReviews as history} from '../src/nested-author-review.mjs';
import {fixture,open,id,digest} from './nested-author-review-fixture.mjs';
async function ready(c,mode='engineering_simulation'){edit(c,{mode});await preview(c);edit(c,{reason:mode==='engineering_simulation'?'公开合成工程模拟，非人工业务审查':'作者明确自审全部业务范围',ack_scope:true,ack_inferences:true});}

test('author is the default and unresolved questions prevent qualification even after both explicit acknowledgements',async()=>{
 const f=fixture(),c=await open(f);await assert.rejects(preview(c),/未解决的待核问题/);assert.equal(state(c).mode,'author_self_review');assert.equal(form(c).preview,null);edit(c,{reason:'尝试核对',ack_scope:true,ack_inferences:true});await assert.rejects(submit(c),/检查已保存版本/);assert.equal(f.calls.some(call=>call.path.includes('/nested-review-preview')),false);assert.equal(f.posts().length,0);assert.equal(state(c).pending,null);assert.deepEqual(f.detail,f.original);c.dispose();
});
test('author qualification needs a saved question-free version and a distinct strict non-simulation receipt',async()=>{
 const f=fixture({questions:0}),c=await open(f);await ready(c,'author_self_review');const result=await submit(c);assert.equal(result.status,'qualified');assert.equal(result.simulation_only,false);assert.equal(result.human_review,'author_self_reviewed');assert.equal(state(c).pending,null);assert.equal(f.posts().length,1);assert.ok(f.calls.findIndex(call=>call.path.includes('/nested-review-requests/'))<f.calls.findIndex(call=>call.method==='POST'));assert.equal(form(c).result.can_execute,false);assert.deepEqual(f.detail,f.original);c.dispose();
});
test('engineering simulation retains all unresolved questions, original failure and exact durable nine-key command',async()=>{
 const f=fixture(),c=await open(f);await ready(c);const result=await submit(c),post=f.posts()[0],body=JSON.parse(post.raw);assert.equal(Object.keys(body).length,9);assert.equal(body.review_mode,'engineering_simulation');assert.equal(result.status,'engineering_reviewed');assert.equal(result.human_review,'not_human_review');assert.equal(result.simulation_only,true);assert.equal(result.value.business.questions.length,1);assert.equal(state(c).pending,null);assert.equal(f.records.size,1);assert.deepEqual(c.form.value,f.original.value);assert.deepEqual(f.detail,f.original);c.dispose();
});
test('lost reply restores the same nonce on remount, GET-only recovery preserves later human edits and old journal',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.lost=true;await assert.rejects(submit(c));const pending=reviewCopy(state(c).pending),oldKey=c.key;assert.equal(pending.sha,digest(pending.raw));c.edit(c.form.value,'后来的人工作业说明');const changed=reviewCopy(c.form),oldJournal=f.storage.getItem(oldKey);c.dispose();const next=await open(f);assert.deepEqual(state(next).pending,pending);assert.equal(f.storage.getItem(oldKey),oldJournal,'remount retains the prior lifetime journal with its later human input');await recover(next);assert.equal(f.posts().length,1);assert.equal(state(next).pending,null);assert.equal(form(next).result.review_mode,'engineering_simulation');assert.deepEqual(next.form,changed);assert.equal(f.records.size,1);next.dispose();
});
test('unknown result never switches mode or archives and only deliberate retry sends the exact original raw',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.drop=true;await assert.rejects(submit(c));const pending=reviewCopy(state(c).pending);assert.throws(()=>edit(c,{mode:'author_self_review'}),/原核对结果/);assert.throws(()=>archive(c));await recover(c);assert.equal(f.posts().length,1);f.mode.drop=false;await recover(c,true);assert.equal(f.posts().length,2);assert.equal(f.posts()[1].raw,pending.raw);assert.equal(f.records.size,1);assert.equal(form(c).result.review_mode,'engineering_simulation');c.dispose();
});
test('only the exact new authoritative not-applied code permits archival and preserves the original bytes',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.rejection={code:'business_flow_author_review_source_not_applied',not_applied:true};await assert.rejects(submit(c));assert.equal(state(c).pending.rejection,null);assert.throws(()=>archive(c));f.mode.rejection={code:'nested_review_source_not_applied',not_applied:true};await assert.rejects(recover(c,true));const raw=state(c).pending.raw;archive(c);assert.equal(state(c).pending,null);assert.equal(state(c).archives[0].raw,raw);assert.equal(form(c).preview,null);assert.deepEqual(f.detail,f.original);c.dispose();
});
test('dirty or stale drafts, missing acknowledgements and storage failure cannot send a qualification',async()=>{
 const f=fixture(),c=await open(f);c.edit(c.form.value,'未保存');await assert.rejects(preview(c));assert.equal(f.posts().length,0);c.dispose();
 const stale=fixture(),s=await open(stale);await ready(s);stale.detail.version++;await s.loadDetail();await assert.rejects(submit(s),/这一版本/);assert.equal(stale.posts().length,0);s.dispose();
 const second=fixture({questions:0}),d=await open(second);await preview(d);await assert.rejects(submit(d));await ready(d);second.storage.fail=true;await assert.rejects(submit(d));assert.equal(second.posts().length,0);assert.equal(d.localSaved,false);assert.equal(d.safeToLeave(),false);assert.equal(form(d).reason,'公开合成工程模拟，非人工业务审查');second.storage.fail=false;assert.equal(d.persist(),true);await recover(d);assert.equal(second.posts().length,0,'retrying durable protection and GET never submits');await recover(d,true);assert.equal(second.posts().length,1);assert.equal(state(d).pending,null);d.dispose();
});
test('wrong actor or simulation flags cannot be accepted under the independent 195 result schema',async()=>{
 for(const malformed of [{reviewed_by:id(99)},{human_review:'author_self_reviewed'},{simulation_only:false},{schema:'author-review-result-v1'},{can_execute:true}]){const f=fixture(),c=await open(f);await ready(c);f.mode.malformed=malformed;await assert.rejects(submit(c));assert.ok(state(c).pending);assert.equal(form(c).result,null);assert.equal(f.posts().length,1);assert.deepEqual(f.detail,f.original);c.dispose();}
});
test('a retained direct receipt forbids another POST when the original query is temporarily unavailable',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.hideReceipt=true;await submit(c);assert.ok(state(c).pending.direct_result);assert.equal(form(c).result,null);const raw=state(c).pending.raw;await assert.rejects(recover(c,true),/直接回执/);assert.equal(state(c).pending.raw,raw);assert.equal(f.posts().length,1);f.mode.hideReceipt=false;await recover(c);assert.equal(state(c).pending,null);assert.equal(f.posts().length,1);c.dispose();
});
test('mode changes invalidate preview and acknowledgements while preserving separate notes and server history',async()=>{
 const f=fixture({questions:0}),c=await open(f);await ready(c,'author_self_review');await submit(c);edit(c,{mode:'engineering_simulation'});assert.equal(form(c).preview,null);assert.equal(form(c).ack_scope,false);await ready(c);await submit(c);edit(c,{mode:'author_self_review'});assert.equal(form(c).reason,'作者明确自审全部业务范围');assert.equal(form(c).preview,null);assert.equal(form(c).ack_scope,false);await history(c);assert.equal(state(c).history.length,2);assert.deepEqual(state(c).history.map(row=>row.human_review),['author_self_reviewed','not_human_review']);assert.deepEqual(f.detail,f.original);c.dispose();
});
test('GET-only original recovery works without write locks and identity changes preserve the old pending journal',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.lost=true;await assert.rejects(submit(c));c.locks=null;await recover(c);assert.equal(state(c).pending,null);assert.equal(f.posts().length,1);await assert.rejects(preview(c),/多窗口/);c.dispose();
 const other=fixture(),d=await open(other);await ready(d);other.mode.drop=true;await assert.rejects(submit(d));const before=other.storage.snapshot();other.session.setItem('ga-setup-csrf-user',id(99));await assert.rejects(recover(d));assert.deepEqual(other.storage.snapshot(),before);assert.equal(d.identityStatus,'concealed');assert.equal(other.posts().length,1);d.dispose();
});

test('new human edits during asynchronous command hashing remain saved locally and prevent a stale request from being staged',async()=>{
 const f=fixture(),c=await open(f);await ready(c);let release,entered;const gate=new Promise(done=>{release=done;}),started=new Promise(done=>{entered=done;}),native=c.crypto;
 c.crypto={randomUUID:native.randomUUID.bind(native),subtle:{digest:async(...args)=>{entered();await gate;return native.subtle.digest(...args);}}};const work=submit(c);await started;c.edit(c.form.value,'摘要准备期间的新人工输入');release();await assert.rejects(work,/准备期间/);assert.equal(state(c).pending,null);assert.equal(f.posts().length,0);assert.equal(c.form.note,'摘要准备期间的新人工输入');assert.equal(c.form.dirty,true);c.dispose();
});

test('restored original bytes with a foreign protocol cannot be replayed through the nested endpoint',async()=>{
 const f=fixture(),c=await open(f);await ready(c);f.mode.drop=true;await assert.rejects(submit(c));const p=state(c).pending;p.body.schema='business-flow-author-review-command-v1';p.raw=JSON.stringify(p.body);p.sha=digest(p.raw);assert.equal(c.persist(),true);const before=f.posts().length;await assert.rejects(recover(c,true),/协议/);assert.equal(f.posts().length,before);assert.ok(state(c).pending);c.dispose();
});

test('the shared operation lock rejects a duplicate submit while preserving one logical nested review',async()=>{
 const f=fixture(),c=await open(f);await ready(c);const first=submit(c);await assert.rejects(submit(c),/正在核对另一项/);await first;assert.equal(f.posts().length,1);assert.equal(f.records.size,1);assert.equal(state(c).pending,null);c.dispose();
});
