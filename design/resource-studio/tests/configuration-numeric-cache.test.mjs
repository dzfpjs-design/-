import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {decodeConfigurationReply,savedConfigurationWire} from '../src/configuration-numeric-wire.mjs';
import {adoptSavedDraft,readDraftRecord,readDraftRecovery,draftOutboxKey,resolveDraftRecovery} from '../src/draft-cache-contract.mjs';
import {adoptExecutionConfiguration} from '../src/execution-configuration-adoption.mjs';
import {withoutSecrets} from '../src/catalog.js';
class Store{constructor(){this.data=new Map();}getItem(k){return this.data.get(k)??null;}setItem(k,v){this.data.set(k,String(v));}removeItem(k){this.data.delete(k);}key(i){return [...this.data.keys()][i]??null;}get length(){return this.data.size;}}
const locks={request:async(name,options,fn)=>fn()};
test('server numeric wire survives adopted cache, a refresh and subsequent actual DraftSync request/reply',async()=>{
 const previous={sessionStorage:Object.getOwnPropertyDescriptor(globalThis,'sessionStorage'),fetch:globalThis.fetch};
 const session=new Store();Object.defineProperty(globalThis,'sessionStorage',{value:session,configurable:true});
 const requests=[];let revision=3;
 globalThis.fetch=async(url,options)=>{
  assert.equal(url,'/api/setup/draft');assert.equal(options.method,'PUT');requests.push(options.body);
  const sent=decodeConfigurationReply(options.body);assert.equal(sent.revision,revision);
  const raw='{"revision":'+(++revision)+',"configuration":'+savedConfigurationWire(sent)+'}';
  return new Response(raw,{status:200,headers:{'Content-Type':'application/json'}});
 };
 try{
  const {DraftSync}=await import('../src/runtime-api.js');
  const storage=new Store(),key='owned-numeric-cache';
  const initial=decodeConfigurationReply('{"revision":3,"configuration":{"title":"已存","float":1.0,"zero":-0.0,"rows":[{"id":"a","value":1.0},{"id":"b","value":1}]}}');
  await adoptSavedDraft(storage,key,initial,locks);
  assert.match(storage.getItem(key),/"float":1\.0/);assert.match(storage.getItem(key),/"zero":-0\.0/);
  let record=readDraftRecord(storage,key);assert.ok(Object.is(record.configuration.zero,-0));
  const first=new DraftSync(storage,key,3,()=>{},{tabStorage:session,locks});
  first.save(withoutSecrets({...record.configuration,title:'明确修改名称'}));await first.wait();assert.equal(first.error,null);
  assert.match(requests[0],/"float":1\.0/);assert.match(requests[0],/"zero":-0\.0/);
  record=readDraftRecord(storage,key);assert.equal(record.revision,4);assert.equal(record.pending,false);
  const refreshed=new DraftSync(storage,key,4,()=>{},{tabStorage:session,locks});
  refreshed.save({...record.configuration,rows:[...record.configuration.rows].reverse()});await refreshed.wait();assert.equal(refreshed.error,null);
  assert.match(requests[1],/"rows":\[\{"id":"b","value":1\},\{"id":"a","value":1\.0\}\]/);
  const recovered=readDraftRecovery(storage,key,session);assert.equal(recovered.local,null);assert.equal(recovered.shared.pending,false);
  assert.equal(readDraftRecord(storage,key).revision,5);
 }finally{globalThis.fetch=previous.fetch;if(previous.sessionStorage)Object.defineProperty(globalThis,'sessionStorage',previous.sessionStorage);else delete globalThis.sessionStorage;}
});
test('a lossy plain adoption uses verified raw configuration and can settle without changing numeric types',async()=>{
 const storage=new Store(),origin={schema:'business-flow-execution-origin-v1',adoption_id:'record',business_name:'业务甲',binding_hash:'bound',command_hash:'command'};
 const raw='{"revision":2,"configuration":{"businessFlowExecutions":'+JSON.stringify({'业务甲':origin})+',"threshold":1.0,"zero":-0.0}}';
 const saved=JSON.parse(JSON.stringify(JSON.parse(raw))),receipt={schema:'business-flow-execution-adoption-result-v1',id:'record',origin,can_execute:false,accepted_revision:2};
 const result=await adoptExecutionConfiguration({saved,scope:{actor_id:'actor',workspace_id:'workspace'},account:{user_id:'actor',workspace_id:'workspace'},receipt,
  wire:{raw,requires_lossless_sync:true},checkEntry:()=>{},adoptServerDraft:next=>adoptSavedDraft(storage,'configured',next,locks)});
 assert.equal(result.accepted,true);assert.equal(result.preserved_numeric_tokens,true);
 assert.match(readDraftRecord(storage,'configured').configuration_wire,/"threshold":1\.0/);
 assert.match(storage.getItem('configured'),/"zero":-0\.0/);
});
test('typed cache readback refuses mismatched metadata and a silent storage failure preserves original state',async()=>{
 const storage=new Store();storage.setItem('bad','{"revision":1,"pending":false,"configuration":{"value":2},"configuration_wire":"{\\"value\\":1.0}"}');
 assert.throws(()=>readDraftRecord(storage,'bad'));
 const discard={getItem:()=>null,setItem:()=>{}};
 await assert.rejects(adoptSavedDraft(discard,'discard',decodeConfigurationReply('{"revision":1,"configuration":{"value":1.0}}'),locks),/读回/);
});
test('explicit recovery backup and original generation marker retain float and negative zero',async()=>{
 const session=new Store(),previous=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage');Object.defineProperty(globalThis,'sessionStorage',{value:session,configurable:true});
 try{
  const {DraftSync}=await import('../src/runtime-api.js'),storage=new Store(),key='recovery-numbers';
  const saved=decodeConfigurationReply('{"revision":1,"configuration":{"float":1.0,"zero":-0.0}}');
  await adoptSavedDraft(storage,key,saved,locks);const sync=new DraftSync(storage,key,1,()=>{},{tabStorage:session,locks});sync.stage({...saved.configuration,note:'保留的人工输入'});
  const original=readDraftRecovery(storage,key,session).local;storage.setItem(key,storage.getItem(draftOutboxKey(key,sync.owner)));
  await resolveDraftRecovery(storage,session,key,original,saved,false,locks);
  const backupKey=[...storage.data.keys()].find(k=>k.startsWith(key+':recovery-backup:')),backup=readDraftRecord(storage,backupKey);
  assert.ok(Object.is(backup.configuration.zero,-0));assert.match(backup.configuration_wire,/"float":1\.0/);assert.equal(backup.configuration.note,'保留的人工输入');
  const marker=JSON.parse(storage.getItem(draftOutboxKey(key,sync.owner)+':resolved:1'));assert.ok(Object.is(marker.original.configuration.zero,-0));assert.equal(readDraftRecord(storage,draftOutboxKey(key,sync.owner)).recovery_choice,'server');
 }finally{if(previous)Object.defineProperty(globalThis,'sessionStorage',previous);else delete globalThis.sessionStorage;}
});
