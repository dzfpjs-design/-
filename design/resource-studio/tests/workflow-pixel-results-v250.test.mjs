import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {webcrypto} from 'node:crypto';
import {id,copy} from './pixel-change-v249-fixture.mjs';
import {initializeTaskPixelResults,taskPixelResultsFixture} from './workflow-pixel-results-v250-fixture.mjs';
import {checkedTaskPixelResults,checkedNativePixelEntry,checkedPixelMachineQuery,checkedWorkflowPixelRun,checkedPixelSettlementProof} from '../src/workflow-pixel-results-v250-protocol.mjs';
async function harness(run){const dom=new JSDOM('<!doctype html>',{url:'https://pixel-results-unit.invalid'}),globals={window:dom.window,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto},prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});try{await initializeTaskPixelResults();await run(taskPixelResultsFixture());}finally{dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}}
test('V250 Task results show actual zero-candidate machine output before ACK but never claim received values',()=>harness(async f=>{
 const v=await checkedTaskPixelResults(f.taskResults(),f.scope);assert.equal(v.records[0].run.result.candidate_pixels,0);assert.equal(v.records[0].data_values_available,false);assert.equal(v.records[0].reception.state,'waiting_data');assert.equal(v.records[0].reception.memory_id,null);
}));
test('V250 query redaction is explicit and cannot be accepted as the complete Task result contract',()=>harness(async f=>{
 const row=f.entry({dataOnly:true});await checkedNativePixelEntry(row,f.scope,{dataOnly:true});await assert.rejects(checkedNativePixelEntry(row,f.scope));const malformed=f.entry();await assert.rejects(checkedNativePixelEntry(malformed,f.scope,{dataOnly:true}));const query={schema:'native-pixel-machine-results-v250',workspace_id:f.account.workspace_id,instance_id:f.instanceId,task_id:null,entity_id:null,records:[row],next_before_id:null,has_more:false,not_authoritative:true,coordinate_space:'pixel',association_basis:'task_entity_association_not_verified_coverage'};await checkedPixelMachineQuery(query,f.scope);assert.equal(query.records[0].run.result,null);
}));
test('V250 received result requires independent receipt, unique Memory and exact source/model/artifact pins',()=>harness(async f=>{
 f.state.ack=true;const row=f.entry();await checkedNativePixelEntry(row,f.scope);for(const edit of [v=>{v.reception.memory_id=null;},v=>{v.reception.receipt.workspace_id=id(999);},v=>{v.reception.receipt.model_trace_hash='0'.repeat(64);},v=>{v.reception.receipt.result_hash='0'.repeat(64);},v=>{v.reception.receipt.artifacts[0].sha256='0'.repeat(64);},v=>{v.reception.receipt.declared_outputs[0].capability_id=id(999);},v=>{v.reception.receipt.registration_day='2026-10-29';}]){const bad=copy(row);edit(bad);await assert.rejects(checkedNativePixelEntry(bad,f.scope));}
}));
test('V250 approval draft with no model run still contains the immutable A/B pair and both original read paths',()=>harness(async f=>{
 f.state.hasRun=false;const c=f.make();await c.start();assert.equal(c.value.records.length,0);assert.equal(c.value.pixel_pair_snapshot.before.id,f.before.id);assert.ok((await c.input('before',f.pair.before)).byteLength>0);assert.ok((await c.input('after',f.pair.after)).byteLength>0);assert.equal(f.state.posts.length,0);c.dispose();
}));
test('V250 genuine old Task empty family has no invented pair or model result',()=>harness(async f=>{
 f.state.hasFamily=false;const value=await checkedTaskPixelResults(f.taskResults(),f.scope);assert.equal(value.pixel_pair_snapshot,null);assert.deepEqual(value.records,[]);
}));
test('V250 result execution-scope, pixel coordinate and candidate artifact contradictions are rejected',()=>harness(async f=>{
 for(const edit of [v=>{v.execution_scope.task_id=id(999);},v=>{v.execution_scope.capability_hash='0'.repeat(64);},v=>{v.result.coordinate_space='map';},v=>{v.result.object_state_updated=true;},v=>{v.result.candidate_pixels=v.result.total_pixels+1;},v=>{v.result.artifacts.pop();}]){const value=f.run();edit(value);assert.throws(()=>checkedWorkflowPixelRun(value));}const value=f.run();value.result.inference_seconds_text='1e-7';checkedWorkflowPixelRun(value);
}));
test('V250 normal failed-producer settlement saves only original handoff and does not change failed Job or claim ACK',()=>harness(async f=>{
 f.state.producerFailed=true;f.state.issued=false;const c=f.make();await c.start();assert.equal(c.value.records[0].reception.state,'producer_failed');await c.settle(f.runId);assert.equal(c.pending,null);assert.equal(c.saved.length,1);assert.equal(f.state.producerFailed,true);assert.equal(c.value.records[0].reception.state,'waiting_data');assert.equal(c.value.records[0].data_values_available,false);assert.equal(f.sourceState.posts.length,0);assert.match(c.notice,/没有重新推理/);f.state.ack=true;await c.refresh();assert.equal(c.value.records[0].reception.state,'received');assert.equal(c.value.records[0].reception.receipt.producer_status,'failed');c.dispose();
}));
test('V250 lost settlement response is recovered on cold entry readonly, even if current source later fails cold verification',()=>harness(async f=>{
 f.state.producerFailed=true;f.state.issued=false;f.state.mode='lost';const c=f.make();await c.start();await assert.rejects(c.settle(f.runId));const p=copy(c.pending);c.dispose();f.state.resultsError=409;const next=f.make();await next.start();assert.equal(next.pending,null);assert.equal(next.saved[0].request_id,p.body.request_id);assert.equal(next.value,null);assert.equal(f.state.posts.length,1);assert.equal(next.guard(),true);assert.equal(f.storage.getItem(f.settlementPrefix+'original:'+p.body.request_id),JSON.stringify(p));next.dispose();
}));
test('V250 unknown settlement is never automatically resubmitted and explicit resend preserves exact raw',()=>harness(async f=>{
 f.state.producerFailed=true;f.state.issued=false;f.state.mode='unknown';const c=f.make();await c.start();await assert.rejects(c.settle(f.runId));const p=copy(c.pending);c.dispose();const next=f.make();await next.start();await next.recover();assert.equal(f.state.posts.length,1);assert.equal(next.pending.raw,p.raw);assert.equal(next.guard(),true);f.state.mode='normal';await next.resend();assert.equal(f.state.posts.length,2);assert.equal(f.state.posts[1].raw,p.raw);assert.equal(f.sourceState.posts.length,0);next.dispose();
}));
test('V250 settlement original storage failure supports same-page no-send repair and keeps one command identifier',()=>harness(async f=>{
 f.state.producerFailed=true;f.state.issued=false;const c=f.make();await c.start();f.storage.failSet=key=>key===f.settlementPrefix+'pending';await assert.rejects(c.settle(f.runId));const p=copy(c.pending);assert.equal(c.guard(),false);assert.equal(f.state.posts.length,0);f.storage.failSet=null;await c.repair();assert.equal(c.guard(),true);assert.equal(c.pending.body.request_id,p.body.request_id);assert.equal(f.state.posts.length,0);await c.resend();assert.equal(f.state.posts[0].raw,p.raw);c.dispose();
}));
test('V250 bad settlement receipt cannot impersonate the original failed producer or actor',()=>harness(async f=>{
 const body={schema:'workflow-pixel-settlement-v250',request_id:'原交接/保留?同一请求250',version:7},proof=f.proof(body);await checkedPixelSettlementProof(proof,{run:f.run(),actorId:f.account.user_id,body});for(const edit of [p=>{p.actor_id=id(999);},p=>{p.job_status='succeeded';},p=>{p.sources_hash='0'.repeat(64);},p=>{p.body.version++;}]){const p=copy(proof);edit(p);await assert.rejects(checkedPixelSettlementProof(p,{run:f.run(),actorId:f.account.user_id,body}));}
}));
test('V250 changing actor while settlement lookup is pending hides late receipt without clearing original command',()=>harness(async f=>{
 f.state.producerFailed=true;f.state.issued=false;f.state.mode='lost';const c=f.make();await c.start();await assert.rejects(c.settle(f.runId));const p=copy(c.pending);c.dispose();let release;f.state.lookupGate=new Promise(resolve=>release=resolve);const next=f.make(),starting=next.start();for(let turn=0;turn<100&&!f.state.calls.some(row=>row.path.includes('/settlement-requests?'));turn++)await new Promise(resolve=>setTimeout(resolve,0));f.session.setItem('ga-setup-csrf-user',id(999));release();await starting;assert.equal(next.denied,true);assert.deepEqual(next.saved,[]);assert.equal(f.storage.getItem(f.settlementPrefix+'pending'),JSON.stringify(p));assert.equal(f.state.posts.length,1);next.dispose();
}));
test('V250 Task artifact content is verified and a corrupted file never becomes a successful binary preview',()=>harness(async f=>{
 const c=f.make();await c.start();const run=c.value.records[0].run;for(const kind of ['probability_data','probability_preview','candidate_overlay','binary_mask','valid_mask','manifest'])assert.ok((await c.artifactBytes(run,kind)).byteLength>0);f.sourceState.binaryCorrupt=true;await assert.rejects(c.originalBytes(run,'before'),/内容与保存记录不一致/);c.dispose();
}));
