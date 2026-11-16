// State boundaries on Root-reviewed public synthetic serializers. Requests
// below are explicit test doubles, not proof of API/auth/provider persistence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {copy,digest,evidenceCommandHash,evidenceReviewValue,verifyEvidenceDraft} from '../src/report-evidence-reading-contract.mjs';
import {evidenceV5ManualAvailability} from '../src/report-evidence-v5-contract.mjs';
import {memory,tabFor,missing,reseal} from './report-evidence-v4-fixture.mjs';
const priorSession=globalThis.sessionStorage;globalThis.sessionStorage??=tabFor();
const {ReportEvidenceReadingSession,evidenceReadingPending,verifyEvidenceReadingJournals}=await import('../src/report-evidence-reading-session.mjs');
test.after(()=>{if(priorSession===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=priorSession;});
const base='/setup/report-evidence-readings',directory=new URL('./fixtures/report-evidence-v5/',import.meta.url);
async function fixture(){const f={};for(const name of ['preview','start','mapped','reduced','human-save'])f[name]=JSON.parse(await readFile(new URL(name+'.json',directory),'utf8'));f.account={user_id:f.preview.actor_id,workspace_id:f.preview.workspace_id,role:'admin'};return f;}
async function reviewReply(row,body,account){
 const receipt=await reseal({schema:'report-evidence-review-receipt-v1',kind:'report_evidence_review',actor_id:account.user_id,workspace_id:account.workspace_id,draft_id:row.id,request_id:body.request_id,accepted_revision:body.revision+1,value_hash:await digest(body.value),command_hash:await evidenceCommandHash(account,'review',row.id,body)},'receipt_hash');
 const draft=copy(row);draft.revision=body.revision+1;draft.review_value=copy(body.value);draft.review_history.push({receipt,value:copy(body.value)});await reseal(draft,'view_hash');return {draft,receipt,current_matches:true};
}
async function environment(run){
 const f=await fixture(),storage=memory(),tab=tabFor(f.account),calls=[],control={row:copy(f.reduced),reply:null,mode:'save',body:null},session=new ReportEvidenceReadingSession({account:f.account,materialID:f.preview.body.selection.original_ref.id,storage,tabStorage:tab,locks:{request:async(_name,_options,fn)=>fn()},request:async(path,options={})=>{
  calls.push({path,...options});if(path==='/me')return f.account;if(path===base+'/'+control.row.id)return control.row;
  if(path.includes('/review-receipts/')){if(control.reply&&path.endsWith(control.reply.receipt.request_id))return control.reply;throw missing();}
  if(path.endsWith('/review')&&options.method==='PUT'){control.body=JSON.parse(options.body);if(control.mode==='hold')await new Promise(resolve=>{control.release=resolve;});if(control.mode==='unknown')throw Object.assign(Error('synthetic unknown transport'),{status:0});control.reply=await reviewReply(control.row,control.body,f.account);control.row=control.reply.draft;return control.reply;}
  if(path.includes('/by-request/'))return f.start;
  throw missing();
 }});
 try{await session.load(control.row.id);await run({f,session,storage,tab,calls,control});}finally{session.dispose();}
}

test('Gen5 session creates independent manual5, saves command3 and reloads every semantic field with exact server receipt',async()=>environment(async({f,session,calls,control})=>{
 const original=copy(f.reduced.model_result),value=evidenceReviewValue(f.reduced);assert.equal(value.schema,'report-evidence-manual-review-v5');
 value.note='本人说明末字🙂';value.result.businesses[0].steps[0].role='人工核对职责';value.result.businesses[0].steps[0].purpose='保留职责与原依据';value.result.global_claims[0].statement='人工核对跨业务职责';session.editValue(value);
 await session.save();assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.equal(calls.filter(c=>c.method==='POST').length,0);assert.deepEqual(control.body.value,value);assert.equal(session.state.pending,null);assert.equal(session.state.dirty,false);
 assert.deepEqual(session.state.value.result.global_claims,value.result.global_claims);for(const field of ['participants','interactions','conditions','coordination'])assert.deepEqual(session.state.value.result.businesses[0][field],value.result.businesses[0][field]);
 await verifyEvidenceDraft(control.row,f.account);await session.load(control.row.id);assert.deepEqual(session.state.value,value);assert.deepEqual(control.row.model_result,original);
}));

test('unknown private save keeps the original command3 body and nonce through later edit and GET-only retries',async()=>environment(async({f,session,control,calls,storage})=>{
 const value=copy(session.state.value);value.note='原输入A';session.editValue(value);control.mode='unknown';await assert.rejects(session.save());
 const original=copy(session.state.pending),serialized=JSON.stringify(original);assert.equal(original.schema,'report-evidence-reading-command-v3');assert.equal(original.binding.map_inputs_hash,null);assert.equal(original.binding.map_plan_hash,f.preview.map_preparation.plan.plan_hash);
 const later=copy(session.state.value);later.note='后来输入B';session.editValue(later);await assert.rejects(session.save());assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.equal(JSON.stringify(session.state.pending),serialized);
 control.reply=await reviewReply(control.row,original.body,f.account);control.row=control.reply.draft;await session.save({probeOnly:true});assert.equal(session.state.pending,null);assert.equal(session.state.value.note,'后来输入B');assert.equal(session.state.dirty,true);assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.ok(Array.from({length:storage.length},(_,i)=>storage.getItem(storage.key(i))).some(v=>v.includes('后来输入B')));
}));

test('a late accepted save does not erase a later complete manual value',async()=>environment(async({session,control,calls})=>{
 const initial=copy(session.state.value);initial.note='先保存A';session.editValue(initial);control.mode='hold';const save=session.save();
 for(let i=0;i<100&&!control.release;i++)await new Promise(resolve=>setImmediate(resolve));assert.ok(control.release,'real PUT handler must be reached');
 const later=copy(session.state.value);later.note='后来B';later.result.businesses[0].participants[0].responsibility='后来人工职责';session.editValue(later);control.release();await save;
 assert.equal(control.body.value.note,'先保存A');assert.deepEqual(session.state.value,later);assert.equal(session.state.dirty,true);assert.equal(session.state.pending,null);assert.equal(calls.filter(c=>c.method==='PUT').length,1);
}));

test('unreliable local human input never sends a private request and remains recoverable in memory',async()=>environment(async({session,storage,calls})=>{
 const value=copy(session.state.value);value.note='容量故障末字R';storage.setItem=()=>{throw Error('synthetic quota');};assert.equal(session.editValue(value),false);await assert.rejects(session.save());assert.equal(calls.some(c=>c.method==='PUT'||c.method==='POST'),false);assert.equal(session.state.value.note,'容量故障末字R');assert.equal(session.capture().value.note,'容量故障末字R');assert.ok(session.state.pending);
}));

test('paid command3 scanner preserves original exact scope/body and rejects mixed binding without overwriting it',async()=>{
 const f=await fixture(),storage=memory(),body={...copy(f.preview.body),preview_hash:f.preview.preview_hash,consent:true},entry={schema:'report-evidence-reading-command-v3',scope:{actor_id:f.account.user_id,workspace_id:f.account.workspace_id},kind:'extract',draft_id:null,body,command_hash:await evidenceCommandHash(f.account,'extract',null,body),binding:{material_id:body.selection.original_ref.id,source_hash:f.preview.source_hash,preview_hash:f.preview.preview_hash,map_inputs_hash:null,map_plan_hash:f.preview.map_preparation.plan.plan_hash,remaining_chunks:[]},receipt:null,rejection:null,resolution:null};
 const s=new ReportEvidenceReadingSession({account:f.account,materialID:body.selection.original_ref.id,storage,tabStorage:tabFor(f.account),request:async()=>{assert.fail('scanner must not make a request');},locks:{request:async(_n,_o,fn)=>fn()}});try{
  const key=s.entryKey(entry),bytes=JSON.stringify(entry);storage.setItem(key,bytes);assert.deepEqual(evidenceReadingPending(f.account,storage),[entry]);await verifyEvidenceReadingJournals(f.account,storage);assert.equal(storage.getItem(key),bytes);
  const changed=copy(entry);changed.binding.map_results_hash=null;storage.setItem(key,JSON.stringify(changed));assert.throws(()=>evidenceReadingPending(f.account,storage));assert.equal(storage.getItem(key),JSON.stringify(changed));
 }finally{s.dispose();}
});

test('model structural readability is independent of the unchanged 512 KiB human-value save cap',async()=>{
 const f=await fixture(),row=copy(f.reduced),claim=row.model_result.global_claims[0];
 // Synthetic capacity-only input to this pure classifier. This is not an
 // invented valid journal/projection; the independent server bridge checks
 // original large projections and their complete proofs separately.
 row.model_result.global_claims=Array.from({length:160},(_,i)=>({...copy(claim),key:'gc_'+i.toString(16).padStart(32,'0'),statement:'字'.repeat(2000),explanation:'核'.repeat(1000)}));
 const before=JSON.stringify(row.model_result),availability=evidenceV5ManualAvailability(row);assert.equal(availability.allowed,false);assert.match(availability.reason,/512 KiB/);assert.equal(evidenceReviewValue(row),null);assert.equal(row.model_result.global_claims.length,160);assert.equal(JSON.stringify(row.model_result),before);
});
