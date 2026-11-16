import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {webcrypto} from 'node:crypto';
import {needRecoveryFixture,initializeNeedController,id,copy,sha} from './workspace-need-recovery-v248-fixture.mjs';
import {needCanonical} from '../src/workspace-need-v246-protocol.mjs';
import {needRequestLookupPath,checkedNeedRequestRecovery,needRecoveryMatchesView} from '../src/workspace-need-recovery-v248.mjs';

async function harness(run){
 const f=needRecoveryFixture(),dom=new JSDOM('<!doctype html>',{url:'https://need-recovery-unit.invalid'}),globals={window:dom.window,localStorage:f.storage,sessionStorage:f.session},prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
 try{await initializeNeedController();await run(f);}finally{dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}
test('V248 typed request recovery validates the original full body, actor, semantic hash and query-only path',()=>harness(async f=>{
 const p=await f.seedOriginal(),reply=await f.api(needRequestLookupPath(f.scope,p)),checked=await checkedNeedRequestRecovery(reply,f.scope,p,webcrypto);
 assert.equal(checked.request.evidence_level,'typed_body_receipt');assert.deepEqual(checked.request.typed_body,p.body);assert.equal(checked.body_hash,p.body_hash);assert.notEqual(p.sha256,p.body_hash);assert.equal(checked.query_path,'/api'+f.base+'/'+id(92));assert.ok(!Object.hasOwn(checked,'raw'));assert.ok(!Object.hasOwn(checked,'query'));
 for(const edit of [value=>{value.request.actor_id=id(90);},value=>{value.body_hash=p.sha256;},value=>{value.request.typed_body.max_age_hours++;},value=>{value.query_path='/api'+f.base+'/'+id(99);},value=>{value.unrecognized=true;}]){const broken=copy(reply);edit(broken);await assert.rejects(checkedNeedRequestRecovery(broken,f.scope,p,webcrypto));}
}));
test('V248 legacy archive proof stays a legacy record and never invents a full body or collapsed archive controls',()=>harness(async f=>{
 f.state.lookupEvidence='legacy_job_context';const p=await f.seedOriginal({bodyOverrides:{source:'archive',confirmation:'unverified_extraction',max_age_hours:48}}),reply=await checkedNeedRequestRecovery(await f.api(needRequestLookupPath(f.scope,p)),f.scope,p,webcrypto);
 assert.equal(reply.request.typed_body,null);assert.equal(reply.request.receipt_hash,null);assert.equal(reply.request.typed_receipt_audit_id,null);assert.equal(reply.request.evidence_level,'legacy_job_context');const v=f.view(p.body,id(92),1,'sufficient');assert.equal(v.copy_descriptor.conditions.confirmation,null);assert.equal(v.copy_descriptor.conditions.max_age_hours,null);needRecoveryMatchesView(reply.request,v,p);const forged=copy(reply);forged.request.typed_body=p.body;await assert.rejects(checkedNeedRequestRecovery(forged,f.scope,p,webcrypto));
}));
test('V248 historical request slug is encoded as a query parameter rather than treated as a UUID path segment',()=>harness(async f=>{
 const p=await f.seedOriginal({requestID:'Legacy_request-248'}),path=needRequestLookupPath(f.scope,p),url=new URL(path,'https://need.invalid');assert.equal(url.pathname,'/workspace/instances/'+f.instanceId+'/data-need-requests');assert.equal(url.searchParams.get('request_id'),'Legacy_request-248');assert.equal(url.searchParams.get('expected_body_hash'),p.body_hash);await checkedNeedRequestRecovery(await f.api(path),f.scope,p,webcrypto);assert.equal(f.recovery.postAttempts.length,0);
}));
test('V248 automatic readonly entry restores an unknown saved request outside the recent list before paused options can block it',()=>harness(async f=>{
 const p=await f.seedOriginal(),raw=JSON.stringify(p);f.recovery.paused=true;f.recovery.hideRecent=true;const c=f.make();await c.start();assert.equal(f.recovery.lookups,1);assert.equal(c.pending,null);assert.equal(c.detail.id,id(92));assert.equal(c.detail.purpose,p.body.purpose);assert.equal(c.detail.scope_valid,false);assert.equal(c.optionsCurrent,false);assert.equal(c.requestProof.evidence_level,'typed_body_receipt');assert.equal(f.storage.getItem(f.prefix+'original:'+p.body.request_id),raw);assert.equal(f.recovery.postAttempts.length,0);assert.equal(c.guard(),true);c.dispose();
}));
test('V248 found false remains unknown and an explicit original retry cannot bypass the paused create gate',()=>harness(async f=>{
 const p=await f.seedOriginal({saved:false}),raw=JSON.stringify(p);f.recovery.paused=true;f.recovery.hideRecent=true;const c=f.make();await c.start();assert.equal(c.notFound,true);assert.match(c.notice,/仍未知/);assert.equal(f.recovery.postAttempts.length,0);assert.equal(f.storage.getItem(f.prefix+'pending'),raw);assert.equal(c.guard(),true);await assert.rejects(c.recover(true),/已暂停/);assert.equal(f.recovery.postAttempts.length,1);assert.equal(f.recovery.postAttempts[0].raw,p.raw);assert.equal(f.storage.getItem(f.prefix+'pending'),raw);assert.equal(c.pending.body.request_id,p.body.request_id);assert.equal(c.guard(),true);c.dispose();
}));
test('V248 404 or damaged recovery proof preserves the exact pending original and does not accept a misleading query',()=>harness(async f=>{
 const p=await f.seedOriginal(),raw=JSON.stringify(p);f.recovery.lookupOverride=()=>{throw Object.assign(Error('原申请暂不可读取'),{status:404});};const c=f.make();await c.start();assert.equal(c.detail,null);assert.equal(f.storage.getItem(f.prefix+'pending'),raw);assert.equal(c.guard(),true);f.recovery.lookupOverride=reply=>({...reply,request:{...reply.request,body_hash:'a'.repeat(64)}});await assert.rejects(c.recover());assert.equal(c.detail,null);assert.equal(c.pending.body.request_id,p.body.request_id);assert.equal(f.recovery.postAttempts.length,0);c.dispose();
}));
test('V248 normal query version growth after lookup is allowed while request, context and prepared-query pins remain fixed',()=>harness(async f=>{
 await f.seedOriginal();f.recovery.detailOverride=value=>{const v=copy(value);v.version=2;v.copy_descriptor.source.query_version=2;const d=v.copy_descriptor;delete d.descriptor_hash;d.descriptor_hash=sha(needCanonical(d));return v;};const c=f.make();await c.start();assert.equal(c.pending,null);assert.equal(c.detail.version,2);assert.equal(c.requestProof.query_version,1);assert.equal(f.recovery.postAttempts.length,0);c.dispose();
}));
test('V248 actor changes during readonly lookup conceal the late result without clearing original raw or local work',()=>harness(async f=>{
 const p=await f.seedOriginal(),raw=JSON.stringify(p);let release;f.recovery.lookupGate=new Promise(resolve=>release=resolve);const c=f.make(),call=c.start();for(let turn=0;turn<100&&!f.recovery.lookups;turn++)await new Promise(resolve=>setTimeout(resolve,0));assert.equal(f.recovery.lookups,1);f.session.setItem('ga-setup-csrf-user',id(99));release();await call;assert.equal(c.denied,true);assert.equal(c.detail,null);assert.equal(f.storage.getItem(f.prefix+'pending'),raw);assert.equal(f.storage.getItem(f.prefix+'received:'+p.body.request_id),null);assert.equal(f.recovery.postAttempts.length,0);c.dispose();
}));
test('V248 local recovery-receipt failure keeps the original recoverable until the same readonly lookup is saved successfully',()=>harness(async f=>{
 const p=await f.seedOriginal(),raw=JSON.stringify(p);f.storage.failSet=key=>key===f.prefix+'lookup:'+p.body.request_id;const c=f.make();await c.start();assert.equal(c.pending.body.request_id,p.body.request_id);assert.equal(f.storage.getItem(f.prefix+'pending'),raw);assert.equal(c.detail,null);assert.equal(c.guard(),true);f.storage.failSet=null;await c.recover();assert.equal(c.pending,null);assert.equal(c.detail.id,id(92));assert.equal(f.recovery.postAttempts.length,0);assert.equal(f.storage.getItem(f.prefix+'original:'+p.body.request_id),raw);c.dispose();
}));
test('V248 finding a historical submitted query does not overwrite another unsubmitted human draft',()=>harness(async f=>{
 const p=await f.seedOriginal({formQuestion:'另一份仍在填写的人稿'}),human=f.storage.getItem(f.prefix+'form');f.recovery.hideRecent=true;const c=f.make();await c.start();assert.equal(c.pending,null);assert.equal(c.detail.purpose,p.body.purpose);assert.equal(c.draft.form.purpose,'另一份仍在填写的人稿');assert.equal(f.storage.getItem(f.prefix+'form'),human);assert.equal(c.view().submitted,false);assert.equal(f.recovery.postAttempts.length,0);c.dispose();
}));
test('V248 saved query identity and evidence level survive another cold entry without depending on the recent list or a second lookup',()=>harness(async f=>{
 await f.seedOriginal();f.recovery.paused=true;f.recovery.hideRecent=true;const c=f.make();await c.start();const readCount=f.recovery.lookups;assert.equal(c.pending,null);c.dispose();f.recovery.lookupOverride=()=>{throw Error('A second nonce lookup is not needed for an already known query');};const next=f.make();await next.start();assert.equal(next.detail.id,id(92));assert.equal(next.requestProof.evidence_level,'typed_body_receipt');assert.equal(f.recovery.lookups,readCount);assert.equal(next.guard(),true);assert.equal(f.recovery.postAttempts.length,0);next.dispose();
}));
