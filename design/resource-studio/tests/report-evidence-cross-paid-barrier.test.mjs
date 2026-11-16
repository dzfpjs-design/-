// Synthetic client boundaries only; no server, provider, persistence or model proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture,entryFor,account as fixtureAccount,id,memory,tabFor} from './report-evidence-reading-fixture.mjs';
import {fixture as fullFixture,entryFor as fullEntry} from './report-full-reading-fixture.mjs';
import {digest} from '../src/report-draft-contract.mjs';
globalThis.sessionStorage??={getItem:()=>null};
const {evidenceReadingPaidPrefix,evidenceReadingPending}=await import('../src/report-evidence-reading-session.mjs');
const {guardEvidenceReading,reportDraftGuardedRequester}=await import('../src/report-draft-paid-adapter.mjs');
const {ReportFullReadingSession,fullReadingPaidPrefix}=await import('../src/report-full-reading-session.mjs');
const {UnderstandingPaidBarrier,paidBarrierKey}=await import('../src/understanding-paid-barrier.mjs');
const {understandingStartKey,writeStartPending}=await import('../src/setup-object-proposals.mjs');

const locks={request:async(_key,_options,fn)=>fn()};
const account={...fixtureAccount,username:'synthetic-evidence-operator'};
const missing=()=>Object.assign(Error('synthetic original receipt absent'),{status:404});
async function unknownEvidence(){
  const f=await fixture(),entry=await entryFor(f),cache=memory();
  cache.setItem(evidenceReadingPaidPrefix(account)+entry.body.request_id,JSON.stringify(entry));
  return {f,entry,cache};
}
async function local(cache,work){
  const previous=globalThis.localStorage;
  globalThis.localStorage=cache;
  try{return await work();}finally{globalThis.localStorage=previous;}
}

test('unknown evidence request blocks the old report guard without deleting or repairing its original command',async()=>{
  const {entry,cache}=await unknownEvidence(),key=evidenceReadingPaidPrefix(account)+entry.body.request_id,before=cache.getItem(key);
  await assert.rejects(guardEvidenceReading(account,cache),/原调用结果尚待核对/);
  assert.equal(cache.getItem(key),before);
  assert.equal(evidenceReadingPending(account,cache).length,1);
});

test('an old full-reading tab cannot claim a new paid slot while evidence outcome is unknown',async()=>{
  const {entry,cache}=await unknownEvidence(),f=await fullFixture(),calls=[];
  const session=new ReportFullReadingSession({account,materialID:f.original_ref.id,storage:cache,tabStorage:tabFor(account),locks,request:async(path,options={})=>{calls.push({path,options});throw Error('unexpected IO');}});
  try{await assert.rejects(session.claim(await fullEntry(f),()=>{}),/原调用结果尚待核对/);}finally{session.dispose();}
  assert.equal(calls.length,0);
  assert.equal(cache.getItem(fullReadingPaidPrefix(account)+f.command.request_id),null);
  assert.equal(evidenceReadingPending(account,cache)[0].command_hash,entry.command_hash);
});

test('legacy normal-understanding claim checks evidence under its shared lock before writing a fresh paid slot',async()=>{
  const {entry,cache}=await unknownEvidence();
  const pending={body:{revision:7,model_id:id(3),material_ids:[],instruction:'保留用户尚未发送的说明',contract_version:5,request_id:id(88)},preview_hash:'a'.repeat(64)};
  writeStartPending(cache,understandingStartKey(account),pending);
  const before=cache.getItem(understandingStartKey(account));
  const barrier=new UnderstandingPaidBarrier({account,storage:cache,tabStorage:tabFor(account),locks,request:async()=>{throw Error('unexpected IO');}});
  try{await assert.rejects(barrier.claim(await barrier.normalEntry(pending)),/原调用结果尚未核对/);}finally{barrier.dispose();}
  assert.equal(cache.getItem(paidBarrierKey(account)),null);
  assert.equal(cache.getItem(understandingStartKey(account)),before);
  assert.equal(evidenceReadingPending(account,cache)[0].command_hash,entry.command_hash);
});

test('legacy normal and follow-up HTTP adapters stop before POST when an evidence command is unresolved',async()=>{
  for(const path of ['/setup/understanding','/setup/clarifications/'+id(89)+'/followups/'+id(90)+'/start']){
    const {cache}=await unknownEvidence(),calls=[];
    await local(cache,async()=>{
      const request=async(p,options={})=>{calls.push({path:p,method:options.method});if(p.includes('/clarification-starts/'))throw missing();throw Error('unexpected IO');};
      await assert.rejects(reportDraftGuardedRequester(account,request)(path,{method:'POST',body:JSON.stringify({request_id:id(91),preview_hash:'a'.repeat(64),consent:true})}),/原调用结果尚待核对/);
    });
    assert.equal(calls.some(c=>c.method==='POST'),false);
  }
});

test('an already accepted legacy nonce is restored before any fresh evidence barrier',async()=>{
  const {cache}=await unknownEvidence(),calls=[],command={request_id:id(92),preview_hash:'a'.repeat(64),consent:true};
  const body={actor_id:account.user_id,workspace_id:account.workspace_id,request_id:command.request_id,kind:'normal',preview_hash:command.preview_hash,command_hash:await digest(command)},receipt={...body,receipt_hash:await digest(body)};
  await local(cache,async()=>{
    const request=async(path,options={})=>{calls.push({path,options});return path.includes('/clarification-starts/')?receipt:{restored:true};};
    assert.deepEqual(await reportDraftGuardedRequester(account,request)('/setup/understanding',{method:'POST',body:JSON.stringify(command)}),{restored:true});
  });
  assert.equal(calls.some(c=>c.path.endsWith('/active')),false);
  assert.equal(evidenceReadingPending(account,cache).length,1);
});

test('a corrupt evidence journal blocks fresh old calls without erasing either private input',async()=>{
  const cache=memory(),key=evidenceReadingPaidPrefix(account)+'fe_'+id(93),original='{"schema":"unrecognized-original"}';
  cache.setItem(key,original);
  await assert.rejects(guardEvidenceReading(account,cache));
  assert.equal(cache.getItem(key),original);
});

test('other account or workspace namespaces cannot block this user',async()=>{
  const {entry,cache}=await unknownEvidence();
  cache.removeItem(evidenceReadingPaidPrefix(account)+entry.body.request_id);
  cache.setItem(evidenceReadingPaidPrefix({...account,user_id:id(98)})+entry.body.request_id,'unrelated opaque value');
  cache.setItem(evidenceReadingPaidPrefix({...account,workspace_id:id(99)})+entry.body.request_id,'another opaque value');
  await guardEvidenceReading(account,cache);
  assert.equal(cache.length,2);
});
