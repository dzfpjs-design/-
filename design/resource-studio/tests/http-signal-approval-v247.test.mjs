import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {webcrypto} from 'node:crypto';
import {initializeHTTPApprovalController,httpApprovalFixture,id,copy} from './http-signal-approval-v247-fixture.mjs';
import {checkedHTTPApprovalOptions,checkedHTTPApprovalTaskSource,checkedHTTPApprovalRecovery,checkedHTTPApprovalResult} from '../src/http-signal-approval-v247-protocol.mjs';

async function harness(run){
 const f=httpApprovalFixture(),dom=new JSDOM('<!doctype html>',{url:'https://http-approval-unit.invalid'}),globals={window:dom.window,localStorage:f.storage,sessionStorage:f.session},prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
 try{await initializeHTTPApprovalController();await run(f);}finally{dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}
test('V247 closed directory preserves blocked business reason and rejects hash, source simulation or unknown-key changes',()=>harness(async f=>{
 const options=await checkedHTTPApprovalOptions(f.options(),f.scope,webcrypto);assert.equal(options.businesses[1].can_create_approval_draft,false);assert.match(options.businesses[1].blocking_reason,/没有原图像/);
 for(const edit of [v=>{v.sources[0].simulation=false;},v=>{v.businesses[0].can_create_approval_draft=false;},v=>{v.unknown='not a capability';}]){const broken=copy(options);edit(broken);await assert.rejects(checkedHTTPApprovalOptions(broken,f.scope,webcrypto));}
}));
test('V247 explicit bounded grant saves the exact scope, expiry, total quota and only-draft promise without any Task before ACK',()=>harness(async f=>{
 const c=await f.ready();assert.equal(f.state.posts.length,0);await c.enable();assert.equal(f.state.posts.length,1);const body=f.state.posts[0].body;assert.deepEqual(body.entity_ids,[f.entityID]);assert.equal(body.valid_for_hours,6);assert.equal(body.max_tasks,3);assert.equal(body.confirm_approval_drafts,true);assert.equal(body.reason,c.draft.form.reason);assert.equal(c.pending,null);assert.equal(c.listing.routes[0].used_tasks,0);assert.deepEqual(c.occurrences,[]);assert.equal(c.completed[0].cloud_calls,0);assert.equal(c.completed[0].device_actions,false);c.dispose();
}));
test('V247 unavailable image business and changed options never gain a grant from apparently human steps',()=>harness(async f=>{
 const c=await f.ready();c.edit({business_id:'business_image_v243'});c.edit({confirmed:true});await assert.rejects(c.enable(),/原图像/);assert.equal(f.state.posts.length,0);c.edit({business_id:f.businessID});c.edit({confirmed:true});f.state.version++;await assert.rejects(c.enable(),/范围已变化/);assert.equal(f.state.posts.length,0);assert.equal(c.draft.form.business_id,f.businessID);c.dispose();
}));
test('V247 lost committed response survives cold remount and missing lookup without clearing or changing its original raw nonce',()=>harness(async f=>{
 const c=await f.ready();f.state.mode='lost';await assert.rejects(c.enable(),/回复丢失/);const raw=f.storage.getItem(f.prefix+'pending');assert.ok(raw);assert.equal(c.guard(),true);c.dispose();f.state.recoveryVisible=false;const next=f.make();await next.start();await next.recover(false);assert.equal(next.pendingNotFound,true);assert.equal(f.storage.getItem(f.prefix+'pending'),raw);assert.equal(f.state.posts.length,1);f.state.recoveryVisible=true;await next.recover(false);assert.equal(next.pending,null);assert.equal(f.state.posts.length,1);assert.equal(next.completed.length,1);next.dispose();
}));
test('V247 explicit retry reuses immutable raw body and a mismatched original cannot be replayed',()=>harness(async f=>{
 const c=await f.ready();f.state.mode='unknown';await assert.rejects(c.enable());const pending=copy(c.pending);await c.recover(false);f.state.mode='normal';await c.recover(true);assert.equal(f.state.posts.length,2);assert.equal(f.state.posts[1].raw,f.state.posts[0].raw);await assert.rejects(checkedHTTPApprovalResult(c.completed[0],f.scope,{...pending,raw_sha256:'9'.repeat(64)},webcrypto));const reply={schema:'signal-approval-recovery-v247',found:false,request_id:pending.body.request_id,raw:null,raw_sha256:null,result:null,current:null};await checkedHTTPApprovalRecovery(reply,f.scope,pending,webcrypto);c.dispose();
}));
test('V247 recovery keeps the accepted enable receipt separate from a later server pause',()=>harness(async f=>{
 const c=await f.ready();f.state.mode='lost';await assert.rejects(c.enable());f.state.routes[0]={...f.state.routes[0],status:'paused'};f.state.version++;f.state.mode='normal';await c.recover();assert.equal(c.completed[0].route.status,'active');assert.equal(c.listing.routes[0].status,'paused');assert.equal(c.pending,null);assert.equal(f.state.posts.length,1);c.dispose();
}));
test('V247 loss of new-execution eligibility still permits explicitly pausing a saved authorization',()=>harness(async f=>{
 const c=await f.ready();await c.enable();c.enabled=false;c.edit({pause_reason:'实例暂停期间停止之后的新草稿'});assert.equal(c.localSaved,true);await c.pause(f.routeID);assert.equal(c.listing.routes[0].status,'paused');assert.equal(f.state.posts[1].body.schema,'signal-approval-pause-v247');assert.equal(f.state.posts[1].body.reason,'实例暂停期间停止之后的新草稿');await assert.rejects(c.enable());assert.equal(f.state.posts.length,2);c.dispose();
}));
test('V247 scope change while an original POST completes preserves its original and prevents accepting the other login result',()=>harness(async f=>{
 const c=await f.ready();let release;f.state.postGate=new Promise(resolve=>release=resolve);const call=c.enable();for(let turn=0;turn<100&&!f.state.posts.length;turn++)await new Promise(resolve=>setTimeout(resolve,0));assert.equal(f.state.posts.length,1);const raw=f.storage.getItem(f.prefix+'pending');f.session.setItem('ga-setup-csrf-user',id(90));release();await assert.rejects(call);assert.equal(c.denied,true);assert.equal(f.storage.getItem(f.prefix+'pending'),raw);assert.equal(f.storage.getItem(f.prefix+'saved:'+JSON.parse(raw).body.request_id),null);c.dispose();
}));
test('V247 failed local persistence retains attempted input, allows correction and can preserve an independent draft',()=>harness(async f=>{
 const c=await f.ready();f.storage.failSet=key=>key===f.prefix+'form';c.edit({reason:'写失败的启用理由'});assert.equal(c.draft.form.reason,'写失败的启用理由');assert.equal(c.guard(),false);await assert.rejects(c.enable());c.edit({reason:'仍能纠正的新理由'});await c.retainDraft();assert.equal(c.guard(),true);const key=c.history.find(row=>row.draft.form.reason==='仍能纠正的新理由').key;f.storage.failSet=null;await c.restoreDraft(key);assert.equal(c.draft.form.reason,'仍能纠正的新理由');assert.equal(c.guard(),true);assert.equal(f.state.posts.length,0);c.dispose();
}));
test('V247 two windows never overwrite another human draft and independent restoration preserves both',()=>harness(async f=>{
 const c=await f.ready(),other=f.make();await other.start();other.edit({reason:'另一窗口的新输入'});c.edit({reason:'本窗口要保留的输入'});assert.equal(c.localSaved,false);assert.equal(JSON.parse(f.storage.getItem(f.prefix+'form')).form.reason,'另一窗口的新输入');await c.retainDraft();assert.equal(c.guard(),true);const key=c.history.find(row=>row.draft.form.reason==='本窗口要保留的输入').key;await c.restoreDraft(key);assert.ok(c.history.some(row=>row.draft.form.reason==='另一窗口的新输入'));assert.equal(c.draft.form.reason,'本窗口要保留的输入');assert.equal(f.state.posts.length,0);c.dispose();other.dispose();
}));
test('V247 refresh preserves previously read history while advancing from the new head cursor without missing intermediate records',()=>harness(async f=>{
 const c=await f.ready(),a=f.occurrence({n:20}),b=f.occurrence({n:21,created_at:'2026-10-29T01:00:00+00:00'}),d=f.occurrence({n:22,created_at:'2026-10-29T00:59:00+00:00'}),newHead=f.occurrence({n:23,created_at:'2026-10-29T01:02:00+00:00'});
 f.state.pages=new Map([['head',{items:[a],next_cursor:a.id}],[a.id,{items:[b,d],next_cursor:null}]]);await c.readHead();await c.loadMore();assert.equal(c.occurrences.length,3);f.state.pages.set('head',{items:[newHead],next_cursor:newHead.id});f.state.pages.set(newHead.id,{items:[{...a,version:2,reason:'原发生进度已更新'},b,d],next_cursor:null});await c.readHead();assert.equal(c.occurrences.length,4);assert.equal(c.nextCursor,newHead.id);await c.loadMore();assert.equal(c.occurrences.length,4);assert.equal(c.occurrences.find(row=>row.id===a.id).version,2);assert.equal(c.nextCursor,null);c.dispose();
}));
test('V247 retry only queues the same blocked occurrence using its current version and cannot claim ACK or draft creation',()=>harness(async f=>{
 const c=await f.ready();await c.enable();const item=f.occurrence({status:'blocked_integrity',version:4,reason:'独立Memory凭据缺失'});f.state.occurrences=[item];await c.readHead();c.edit({retry_reason:'原凭据由操作员修复后再核对'});await c.retry(item.id);assert.equal(f.state.posts[1].body.expected_version,4);assert.equal(f.state.posts[1].path,f.base+'/occurrences/'+item.id+'/retry');assert.equal(c.occurrences[0].status,'blocked_integrity');assert.equal(c.occurrences[0].task_id,null);assert.equal(c.listing.routes[0].used_tasks,0);c.dispose();
}));
test('V247 opening the actual saved draft checks original occurrence and carries a live scope guard through parent navigation',()=>harness(async f=>{
 const c=await f.ready(),item=f.occurrence({status:'approval_draft',task_id:f.taskID});f.state.occurrences=[item];let count=0;await c.openTask(item.id,async(taskID,checkEntry)=>{checkEntry();assert.equal(taskID,f.taskID);count++;});assert.equal(count,1);await assert.rejects(c.openTask(item.id,async(_id,checkEntry)=>{f.session.setItem('ga-setup-csrf-workspace',id(91));checkEntry();}));assert.equal(f.state.posts.length,0);c.dispose();
}));
test('V247 Task source accepts independent received numeric evidence, hides non-V247 tasks and blocks broken or mismatched source',()=>harness(async f=>{
 const value=checkedHTTPApprovalTaskSource(f.sourceView(),f.task());assert.equal(value.source.fields[0].value,27);assert.equal(value.source.data_received,true);assert.equal(value.source.simulation,true);assert.equal(value.source.field_verified,false);f.state.sourceFound=false;assert.equal(checkedHTTPApprovalTaskSource(f.sourceView(),f.task()).found,false);f.state.sourceFound=true;f.state.sourceIssue='原V247发生存在，但Task来源marker丢失';const blocked=checkedHTTPApprovalTaskSource(f.sourceView(),f.task());assert.equal(blocked.found,true);assert.equal(blocked.source,null);f.state.sourceIssue=null;const wrong=f.sourceView();wrong.source.instance_id=id(92);assert.throws(()=>checkedHTTPApprovalTaskSource(wrong,f.task()));
}));
