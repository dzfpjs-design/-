import test from 'node:test';
import assert from 'node:assert/strict';
import {acquireDraft,readDraft} from '../src/uav-simulation-draft.mjs';

function memory(){const m=new Map();return {getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v)}}
function locks(){const held=new Set();return {request:async(name,options,fn)=>{
  if(held.has(name))return fn(null);
  held.add(name);try{return await fn({name})}finally{held.delete(name)}
}}}

test('draft and original pending request survive reopening and stay scoped by account',async()=>{
  const storage=memory(),shared=locks(),key='workspace1:user1';
  const first=await acquireDraft({storage,locks:shared,key});
  const pending={request_id:'original-1',event_id:'烟雾'};
  first.save({...first.value,fields:{...first.value.fields,event:'烟雾',startLon:'117.6'},pending});
  first.release();await new Promise(resolve=>setImmediate(resolve));
  const reopened=await acquireDraft({storage,locks:shared,key});
  assert.equal(reopened.value.fields.startLon,'117.6');assert.deepEqual(reopened.value.pending,pending);
  const other=await acquireDraft({storage,locks:shared,key:'workspace1:user2'});
  assert.equal(other.value.fields.event,'');assert.equal(other.value.pending,null);
  reopened.release();other.release();
});

test('a second tab is read-only until the first releases its lock',async()=>{
  const storage=memory(),shared=locks(),key='workspace:user';
  const first=await acquireDraft({storage,locks:shared,key});
  await assert.rejects(acquireDraft({storage,locks:shared,key}),/另一标签页/);
  first.release();await new Promise(resolve=>setImmediate(resolve));
  const next=await acquireDraft({storage,locks:shared,key});next.release();
});

test('invalid draft or unavailable lock/storage prevents editing and submission',async()=>{
  const storage=memory(),shared=locks();storage.setItem('broken','{"version":2}');
  assert.throws(()=>readDraft(storage,'broken'),/无法核对/);
  await assert.rejects(acquireDraft({storage,locks:shared,key:'broken'}),/无法核对/);
  await assert.rejects(acquireDraft({storage,locks:null,key:'x'}),/不支持/);
  const full={getItem:()=>null,setItem:()=>{throw Error('storage full')}};
  const locked=await acquireDraft({storage:full,locks:shared,key:'full'});
  assert.throws(()=>locked.save(locked.value),/storage full/);locked.release();
});
