import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {generationCanonical,generationHash,generationBody,generationScope,generationKey,listGenerations,readGeneration,stageGeneration,requireGenerationIdentity,verifyGenerationReceipt,confirmGeneration,settleGeneration} from '../src/instance-generation.mjs';

const account={workspace_id:'workspace-a',user_id:'admin-a',role:'admin'},scope=generationScope(account),nonce='00000000-0000-4000-8000-000000000001';
export const memory=()=>{const rows=new Map();return {get length(){return rows.size;},key:index=>[...rows.keys()][index]??null,getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,value),removeItem:key=>rows.delete(key)};};
const body=(patch={})=>generationBody({revision:5,objects:[{name:' 样地 ',fields:[{label:' 面积 ',kind:'数值',unit:' 公顷 '}]}],confirmed_configuration_hash:'a'.repeat(64),request_id:nonce,...patch});
const release=(value,patch={})=>({id:'release-a',instance_id:value.instance_id||'instance-a',release_number:1,status:'draft',configuration_hash:value.confirmed_configuration_hash,snapshot:{businesses:[]},available_business_ids:[],...patch});
async function accepted(journal,patch={}){const row=release(journal.value.body),receipt={saved:true,request_id:journal.value.body.request_id,request_hash:await generationHash(journal.value.body),actor_id:scope.actor_id,workspace_id:scope.workspace_id,generation_intent:journal.value.body.generation_intent,instance_id:row.instance_id,release_id:row.id,release_number:row.release_number,configuration_hash:row.configuration_hash,...patch};return {...receipt,release:row};}

test('full generation body matches Python model defaults, strip and Unicode key ordering',async()=>{
 const value=body({request_id:nonce.toUpperCase(),objects:[{name:'\u0085样地\u001c',fields:[{label:'\ufeff标签\u0085',kind:'文本',unit:'\u0085'}]}],role_bindings:{'𐀀':'a','\ue000':'b'}});
 assert.equal(value.objects[0].name,'样地');assert.equal(value.objects[0].fields[0].label,'\ufeff标签');assert.equal(value.objects[0].fields[0].unit,null);assert.equal(value.objects[0].fields[0].required,false);assert.equal(value.instance_id,null);assert.equal(value.generation_intent,'new_instance');assert.equal(value.request_id,nonce);
 assert.equal(generationCanonical(value.role_bindings),'{"":"b","𐀀":"a"}');
 assert.equal(await generationHash(value.role_bindings),createHash('sha256').update('{"":"b","𐀀":"a"}').digest('hex'));
 for(const value of ['\ud800',{'\ud800':'role'},2.5,undefined])assert.throws(()=>generationCanonical(value));
});

test('two explicit new requests keep distinct independent journals and exact bodies',async()=>{
 const storage=memory(),a=await stageGeneration(storage,scope,body()),b=await stageGeneration(storage,scope,body({request_id:'00000000-0000-4000-8000-000000000002'}));assert.equal(listGenerations(storage,scope).length,2);assert.notEqual(a.value.body_hash,b.value.body_hash);assert.equal(a.value.exact_body,JSON.stringify(a.value.body));assert.equal(a.value.selection.instance_id,null);assert.equal(a.value.preview.revision,5);assert.equal(b.value.status,'pending');await assert.rejects(stageGeneration(storage,scope,body()),/另一页面/);
});

test('original journal survives reload and new instance selection never modifies it',async()=>{
 const storage=memory(),a=await stageGeneration(storage,scope,body());await stageGeneration(storage,scope,body({instance_id:'existing',request_id:'00000000-0000-4000-8000-000000000002'}));assert.deepEqual(readGeneration(storage,scope,nonce),a);assert.equal(listGenerations(storage,scope).find(x=>x.value.body.instance_id==='existing').value.body.generation_intent,'new_release');assert.deepEqual(listGenerations(storage,{...scope,actor_id:'other'}),[]);assert.deepEqual(listGenerations(storage,{...scope,workspace_id:'other'}),[]);
});

test('write quota or changed session prevents any usable generation record',async()=>{
 const storage=memory();storage.setItem=()=>{throw Error('quota');};await assert.rejects(stageGeneration(storage,scope,body()),/quota/);assert.equal(storage.length,0);await assert.rejects(stageGeneration(memory(),scope,body(),{},()=>{throw Error('account changed');}),/account changed/);
});

test('receipt validates local canonical hash, actor, workspace, IDs, intent and full release',async()=>{
 const journal=await stageGeneration(memory(),scope,body()),response=await accepted(journal);assert.ok(await verifyGenerationReceipt(journal,response));
 for(const patch of [{request_hash:'b'.repeat(64)},{actor_id:'other'},{workspace_id:'other'},{request_id:'00000000-0000-4000-8000-000000000002'},{generation_intent:'new_release'},{instance_id:'other'},{release_id:'other'},{configuration_hash:'b'.repeat(64)},{release_number:2}])await assert.rejects(verifyGenerationReceipt(journal,{...response,...patch}),/无法核对/);
 const original=await stageGeneration(memory(),scope,body({instance_id:'fixed'}));await assert.rejects(verifyGenerationReceipt(original,{...await accepted(original),instance_id:'changed',release:release(original.value.body,{instance_id:'changed'})}),/无法核对/);
 assert.equal(await verifyGenerationReceipt(journal,{saved:false,request_id:nonce}),null);await assert.rejects(verifyGenerationReceipt(journal,{saved:false,request_id:'other'}));
});

test('POST receipt and GET receipt resolve the same verified release',async()=>{
 const journal=await stageGeneration(memory(),scope,body()),{release:row,...receipt}=await accepted(journal);assert.deepEqual(await verifyGenerationReceipt(journal,{...row,generation_receipt:receipt}),await verifyGenerationReceipt(journal,{...receipt,release:row}));
});

test('confirmation preserves original request until exact server release readback',async()=>{
 const storage=memory(),journal=await stageGeneration(storage,scope,body()),verified=await verifyGenerationReceipt(journal,await accepted(journal)),confirmed=confirmGeneration(storage,journal,verified);assert.equal(confirmed.value.status,'confirmed');assert.equal(confirmed.value.exact_body,journal.value.exact_body);assert.throws(()=>settleGeneration(storage,confirmed,{...verified.release,snapshot:{businesses:[],extra:'changed'}}),/不一致/);assert.equal(readGeneration(storage,scope,nonce).value.status,'confirmed');const saved=settleGeneration(storage,confirmed,verified.release);assert.equal(saved.value.status,'saved');assert.equal(saved.value.body.request_id,nonce);
});

test('late response cannot replace another tab record, even after successful receipt',async()=>{
 const storage=memory(),journal=await stageGeneration(storage,scope,body()),verified=await verifyGenerationReceipt(journal,await accepted(journal));const replacement={...journal.value,selection:{...journal.value.selection,name:'other tab'}};storage.setItem(generationKey(scope,nonce),JSON.stringify(replacement));assert.throws(()=>confirmGeneration(storage,journal,verified),/另一页面/);assert.deepEqual(readGeneration(storage,scope,nonce).value,replacement);
});

test('journal corruption is retained and blocks recovery rather than silently dropping human state',async()=>{
 const storage=memory(),journal=await stageGeneration(storage,scope,body()),key=generationKey(scope,nonce);for(const patch of [{exact_body:'{}'},{scope:{...scope,actor_id:'other'}},{preview:{revision:9,configuration_hash:'a'.repeat(64)}},{body:{...journal.value.body,request_id:'other'}},{release:{snapshot:{businesses:[null]}}}]){const raw=JSON.stringify({...journal.value,...patch});storage.setItem(key,raw);assert.throws(()=>listGenerations(storage,scope),/无法核对/);assert.equal(storage.getItem(key),raw);}
});

test('fresh same actor may read without admin, while changed actor/workspace cannot post or read',()=>{
 assert.equal(requireGenerationIdentity({...account,role:'viewer'},scope).role,'viewer');assert.throws(()=>requireGenerationIdentity({...account,role:'viewer'},scope,true),/无生成权限/);for(const patch of [{user_id:'other'},{workspace_id:'other'}])assert.throws(()=>requireGenerationIdentity({...account,...patch},scope),/已改变/);
});

test('finite decimal snapshot settings can be compared on exact readback without weakening request hash rules',async()=>{
 const storage=memory(),journal=await stageGeneration(storage,scope,body()),response=await accepted(journal);response.release.snapshot.threshold=0.25;const verified=await verifyGenerationReceipt(journal,response),confirmed=confirmGeneration(storage,journal,verified),saved=settleGeneration(storage,confirmed,structuredClone(response.release));assert.equal(saved.value.status,'saved');assert.equal(saved.value.release.snapshot.threshold,0.25);assert.throws(()=>generationCanonical({revision:0.25}));
});
