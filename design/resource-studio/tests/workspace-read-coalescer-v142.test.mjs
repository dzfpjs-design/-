import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspaceReadCoalescerV142,WORKSPACE_READ_STALE_V142} from '../src/workspace-read-coalescer-v142.mjs';

let sequence=0;
const scope=()=>({actor_id:'actor',workspace_id:'workspace',role:'operator',instance_id:'instance-'+(++sequence)});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const turn=async()=>{for(let index=0;index<8;index++)await Promise.resolve();};
const observed=promise=>promise.then(value=>({value}),error=>({error}));
const live=()=>true;
const staleResult=result=>assert.equal(result.error?.code,WORKSPACE_READ_STALE_V142);

test('same-scope mount and interval share only one actual read with independent bucket callbacks',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),gate=deferred(),first=[],second=[];let calls=0,emit;
 const read=callbacks=>{calls++;emit=callbacks;return gate.promise;};
 try{
  const one=reader.load(key,read,{isCurrent:live,onCurrent:value=>first.push(['current',value]),onHistory:value=>first.push(['history',value])});
  await turn();assert.equal(calls,1);emit.onHistory('history-first');
  const two=reader.load(key,()=>{throw Error('duplicate transport');},{isCurrent:live,onCurrent:value=>second.push(['current',value]),onHistory:value=>second.push(['history',value])});
  assert.deepEqual(second,[['history','history-first']]);emit.onCurrent('current-later');gate.resolve('complete');
  assert.deepEqual(await Promise.all([one,two]),['complete','complete']);
  assert.deepEqual(first,second);assert.equal(calls,1);
 }finally{reader.dispose();}
});

test('separate remount owners share the in-flight transport while disposed callbacks stay concealed',async()=>{
 const old=createWorkspaceReadCoalescerV142(),next=createWorkspaceReadCoalescerV142(),key=scope(),gate=deferred();let calls=0,emit;const displayed=[];
 try{
  const original=observed(old.load(key,callbacks=>{calls++;emit=callbacks;return gate.promise;},{isCurrent:live,onCurrent:()=>assert.fail('disposed owner displayed data')}));
  await turn();old.dispose();staleResult(await original);
  const restored=next.load(key,()=>assert.fail('remount duplicated GET'),{isCurrent:live,onCurrent:value=>displayed.push(value)});
  emit.onCurrent('same-original-read');gate.resolve('saved-read');
  assert.equal(await restored,'saved-read');assert.deepEqual(displayed,['same-original-read']);assert.equal(calls,1);
 }finally{old.dispose();next.dispose();gate.resolve();}
});

test('latest-start subscriber can apply both buckets while the earlier sequence is rejected',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),gate=deferred(),displayed=[];let revision=1,emit,calls=0;
 try{
  const old=observed(reader.load(key,callbacks=>{calls++;emit=callbacks;return gate.promise;},{isCurrent:()=>revision===1,
   onHistory:value=>displayed.push(['old-history',value]),onCurrent:()=>assert.fail('old sequence overwrote new display')}));
  await turn();emit.onHistory('saved history');revision=2;
  const next=reader.load(key,()=>assert.fail('latest subscriber duplicated transport'),{isCurrent:()=>revision===2,
   onHistory:value=>displayed.push(['new-history',value]),onCurrent:value=>displayed.push(['new-current',value])});
  emit.onCurrent('actual current');gate.resolve('complete');staleResult(await old);assert.equal(await next,'complete');
  assert.deepEqual(displayed,[['old-history','saved history'],['new-history','saved history'],['new-current','actual current']]);assert.equal(calls,1);
 }finally{reader.dispose();gate.resolve();}
});

test('independent settled bucket failures remain original failures rather than fabricated empty data',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),failure=Error('history unavailable');
 const history=[{status:'rejected',reason:failure},{status:'fulfilled',value:['original observation']},{status:'fulfilled',value:[]}];
 const current=[{status:'fulfilled',value:['current instance']},{status:'fulfilled',value:['current release']},{status:'fulfilled',value:['current object']}];
 const displayed=[];
 try{
  assert.equal(await reader.load(key,callbacks=>{callbacks.onHistory(history);callbacks.onCurrent(current);return 'both settled';},
   {isCurrent:live,onCurrent:value=>displayed.push(value),onHistory:value=>displayed.push(value)}),'both settled');
  assert.equal(displayed[0],history);assert.equal(displayed[0][0].reason,failure);assert.equal(displayed[1],current);
 }finally{reader.dispose();}
});

test('manual and write-following loads merge one successor and cannot reuse a pre-write result',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),old=deferred(),fresh=deferred(),events=[];let calls=0,emit;
 const read=callbacks=>{calls++;emit=callbacks;return calls===1?old.promise:fresh.promise;};
 try{
  const polling=reader.load(key,read,{isCurrent:live});await turn();
  const manual=reader.load(key,read,{freshAfterCurrent:true,isCurrent:live,onCurrent:value=>events.push(['manual',value])});
  const afterWrite=reader.load(key,read,{freshAfterCurrent:true,isCurrent:live,onCurrent:value=>events.push(['write',value])});
  const laterPoll=reader.load(key,read,{isCurrent:live,onCurrent:value=>events.push(['poll',value])});
  emit.onCurrent('old');assert.deepEqual(events,[]);assert.equal(calls,1);
  old.resolve('before-write');assert.equal(await polling,'before-write');await turn();assert.equal(calls,2);
  emit.onCurrent('after-write');fresh.resolve('fresh');
  assert.deepEqual(await Promise.all([manual,afterWrite,laterPoll]),['fresh','fresh','fresh']);
  assert.deepEqual(events,[['manual','after-write'],['write','after-write'],['poll','after-write']]);
 }finally{reader.dispose();old.resolve();fresh.resolve();}
});

test('a write after the successor has started requires another later read',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),gates=[deferred(),deferred(),deferred()];let calls=0;
 const read=()=>gates[calls++].promise;
 try{
  const a=reader.load(key,read,{isCurrent:live});await turn();
  const b=reader.load(key,read,{freshAfterCurrent:true,isCurrent:live});gates[0].resolve('a');await a;await turn();assert.equal(calls,2);
  const c=reader.load(key,read,{freshAfterCurrent:true,isCurrent:live});gates[1].resolve('b');await b;await turn();assert.equal(calls,3);
  gates[2].resolve('c');assert.equal(await c,'c');
 }finally{reader.dispose();gates.forEach(gate=>gate.resolve());}
});

test('fresh callers can join a round that has not started any GET yet',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope();let calls=0;
 try{
  const read=()=>{calls++;return 'started-after-both-callers';};
  const a=reader.load(key,read,{isCurrent:live});
  const b=reader.load(key,read,{freshAfterCurrent:true,isCurrent:live});
  assert.equal(calls,0);assert.deepEqual(await Promise.all([a,b]),['started-after-both-callers','started-after-both-callers']);assert.equal(calls,1);
 }finally{reader.dispose();}
});

test('a queued old scope never starts through a changed identity or writes late data',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),gate=deferred();let current=true,oldCalls=0;const displayed=[];
 try{
  const a=observed(reader.load(key,callbacks=>{oldCalls++;gate.callbacks=callbacks;return gate.promise;},{isCurrent:()=>current,onCurrent:value=>displayed.push(value)}));
  await turn();
  const b=observed(reader.load(key,()=>{oldCalls++;assert.fail('stale queued identity was used');},{freshAfterCurrent:true,isCurrent:()=>current}));
  current=false;
  const next=reader.load({...key,actor_id:'different-actor'},()=>({actor:'new'}),{isCurrent:live});
  assert.deepEqual(await next,{actor:'new'});gate.callbacks.onCurrent('private old data');gate.resolve('old');
  staleResult(await a);staleResult(await b);assert.deepEqual(displayed,[]);assert.equal(oldCalls,1);
 }finally{reader.dispose();gate.resolve();}
});

for(const changed of ['actor_id','workspace_id','role','instance_id'])test('in-flight sharing isolates '+changed,async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),gate=deferred();let calls=0;
 try{
  const a=reader.load(key,()=>{calls++;return gate.promise;},{isCurrent:live});await turn();
  const b=reader.load({...key,[changed]:'another-value'},()=>{calls++;return 'other-scope';},{isCurrent:live});
  assert.equal(await b,'other-scope');assert.equal(calls,2);gate.resolve('original');assert.equal(await a,'original');
 }finally{reader.dispose();gate.resolve();}
});

test('an original transport error stays an error while an explicitly queued fresh read still runs once',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),gate=deferred(),error=Error('actual scoped read failed');let calls=0;
 const read=()=>++calls===1?gate.promise:'explicit fresh result';
 try{
  const a=observed(reader.load(key,read,{isCurrent:live}));await turn();
  const b=reader.load(key,read,{freshAfterCurrent:true,isCurrent:live});gate.reject(error);
  assert.equal((await a).error,error);assert.equal(await b,'explicit fresh result');assert.equal(calls,2);
 }finally{reader.dispose();}
});

test('a superseded read failure cannot surface as the current scope error',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),gate=deferred();
 const oldFailure=Error('older read failed after a newer refresh was requested');let revision=1,calls=0;
 try{
  const previous=observed(reader.load(key,()=>{calls++;return gate.promise;},{isCurrent:()=>revision===1}));
  await turn();revision=2;
  const fresh=observed(reader.load(key,()=>{calls++;return 'actual current result';},
   {freshAfterCurrent:true,isCurrent:()=>revision===2}));
  gate.reject(oldFailure);
  const [oldResult,currentResult]=await Promise.all([previous,fresh]);
  staleResult(oldResult);assert.notEqual(oldResult.error,oldFailure);
  assert.deepEqual(currentResult,{value:'actual current result'});assert.equal(calls,2);
 }finally{reader.dispose();}
});

test('settled success and failures are not cached or automatically retried',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),error=Error('not a successful empty list');let calls=0;
 try{
  assert.equal(await reader.load(key,()=>++calls,{isCurrent:live}),1);
  const failed=await observed(reader.load(key,()=>{calls++;throw error;},{isCurrent:live}));assert.equal(failed.error,error);
  await turn();assert.equal(calls,2);
  assert.equal(await reader.load(key,()=>++calls,{isCurrent:live}),3);
 }finally{reader.dispose();}
});

test('dispose rejects queued owners without aborting the actual shared read or running abandoned callbacks',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),other=createWorkspaceReadCoalescerV142(),key=scope(),gate=deferred();let calls=0;
 try{
  const a=observed(reader.load(key,()=>{calls++;return gate.promise;},{isCurrent:live}));await turn();
  const queued=observed(reader.load(key,()=>{calls++;assert.fail('disposed queued callback ran');},{freshAfterCurrent:true,isCurrent:live}));
  reader.dispose();staleResult(await a);staleResult(await queued);
  const b=other.load(key,()=>{calls++;return 'remaining owner';},{isCurrent:live});
  gate.resolve('actual request finished');assert.equal(await b,'remaining owner');assert.equal(calls,2);
  staleResult(await observed(reader.load(key,()=>assert.fail('disposed reader resumed'),{isCurrent:live})));
 }finally{reader.dispose();other.dispose();gate.resolve();}
});

test('a scope invalidated before dispatch makes no request and cannot masquerade as an empty success',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope();let current=true,calls=0;
 try{
  const value=observed(reader.load(key,()=>{calls++;return [];},{isCurrent:()=>current}));current=false;
  staleResult(await value);assert.equal(calls,0);
 }finally{reader.dispose();}
});

test('subscriber callback failures are explicit and do not corrupt another owner',async()=>{
 const a=createWorkspaceReadCoalescerV142(),b=createWorkspaceReadCoalescerV142(),key=scope(),gate=deferred(),error=Error('display refused original data');let emit;
 try{
  const failed=observed(a.load(key,callbacks=>{emit=callbacks;return gate.promise;},{isCurrent:live,onCurrent:()=>{throw error;}}));await turn();
  const good=b.load(key,()=>assert.fail('duplicate read'),{isCurrent:live,onCurrent:value=>assert.equal(value,'original')});
  emit.onCurrent('original');gate.resolve('done');assert.equal((await failed).error,error);assert.equal(await good,'done');
 }finally{a.dispose();b.dispose();gate.resolve();}
});

test('a late bucket callback after completion cannot write stale data',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope(),displayed=[];let callbacks;
 try{
  await reader.load(key,value=>{callbacks=value;return 'complete';},{isCurrent:live,onHistory:value=>displayed.push(value)});
  callbacks.onHistory('not part of the completed round');assert.deepEqual(displayed,[]);
 }finally{reader.dispose();}
});

test('malformed scope or callback options never start a transport',async()=>{
 const reader=createWorkspaceReadCoalescerV142(),key=scope();let calls=0,getterCalls=0;const read=()=>{calls++;};
 try{
  const getter={...key};Object.defineProperty(getter,'actor_id',{get(){getterCalls++;return key.actor_id;},enumerable:true});
  for(const bad of [{...key,extra:'open-scope'},{...key,actor_id:''},getter])await assert.rejects(reader.load(bad,read,{isCurrent:live}));
  await assert.rejects(reader.load(key,read,{}));
  await assert.rejects(reader.load(key,read,{isCurrent:live,freshAfterCurrent:1}));
  assert.equal(calls,0);assert.equal(getterCalls,0);
 }finally{reader.dispose();}
});
