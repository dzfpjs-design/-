// Offline production controller -> Root-forwarded actual isolated ASGI API.
// No hashes, receipts, authority, capability responses or semantic gates mocked.
import assert from 'node:assert/strict';
import readline from 'node:readline';
import {webcrypto} from 'node:crypto';
import {FullSemanticGraphAdoptionController} from '../src/full-semantic-graph-adoption-controller.mjs';
import {applyNumericEdit,updateGraphDraft} from '../src/full-semantic-graph-editor-model.mjs';
import {freshNumber,numericValue} from '../src/typed-numbers-v132.mjs';

Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
globalThis.fetch=()=>{throw Error('Offline graph controller bridge cannot use network');};
const lines=readline.createInterface({input:process.stdin}),responses=new Map();let initialize,serial=0;
const initialized=new Promise(resolve=>{initialize=resolve;});
lines.on('line',line=>{const value=JSON.parse(line);if(value.kind==='init')initialize(value);else{assert.equal(value.kind,'response');const pair=responses.get(value.id);assert.ok(pair);responses.delete(value.id);pair(value);}});
const input=await initialized;
function memory(){const rows=new Map();return {get length(){return rows.size;},key:index=>[...rows.keys()][index]??null,getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,String(value)),removeItem:key=>rows.delete(key)};}
const storage=memory(),makeSession=()=>{const value=memory();value.setItem('ga-setup-csrf-user',input.me.user_id);value.setItem('ga-setup-csrf-workspace',input.me.workspace_id);value.setItem('ga-setup-csrf',input.csrf);return value;};
const tails=new Map(),locks={request(name,options,fn){assert.equal(options.mode,'exclusive');const previous=tails.get(name)||Promise.resolve(),next=previous.then(fn);tails.set(name,next.catch(()=>{}));return next;}};
const base='/setup/full-semantic-graph-wire/'+input.handoff_id;let dropped=false;
const calls=[];
const api=async(path,options={})=>{
 const id=++serial,pending=new Promise(resolve=>responses.set(id,resolve));calls.push({path,method:options.method||'GET',body:options.body});
 process.stdout.write(JSON.stringify({kind:'api',id,path,options})+'\n');
 const response=await pending;if(response.status>=400){const error=Error(typeof response.body?.detail==='string'?response.body.detail:'Actual isolated backend rejected graph request');error.status=response.status;throw error;}
 if(!dropped&&path===base+'/v4/revisions/adopt'&&options.method==='POST'){dropped=true;throw Error('Synthetic response loss after actual graph revision commit');}
 return response.body;
};
const make=()=>new FullSemanticGraphAdoptionController({api,handoffId:input.handoff_id,actorId:input.me.user_id,workspaceId:input.me.workspace_id,contextFamily:'v4',storage,tabStorage:makeSession(),locks});
let controller=make();await controller.load();controller.chooseBusiness(input.business_name);
assert.deepEqual(controller.issues(),[]);const original=controller.state.value;
assert.deepEqual(original.parent,input.parent,'the revision must use the actual current adopted head');
assert.equal(original.graph.predicates.p_after.op,'gte');assert.equal(original.graph.predicates.p_after.right.kind,'number');
const draft=applyNumericEdit(original,{key:'predicate:p_after/right',text:input.raw,kind:'number',label:'人工明确修改后续步骤门槛'},value=>updateGraphDraft(value,next=>{
 next.graph.predicates.p_after.right.value=numericValue(freshNumber(input.raw,'number'));
 const owner=next.owner_bindings.find(row=>row.clauses.some(clause=>clause.targets.some(target=>target.kind==='predicate'&&target.id==='p_after')));assert.ok(owner,'the actual source clause covering this predicate stays explicit');
 owner.reason+=' 人工在本工作区明确修改此门槛，保持原来源条款及完整并行安排；不认定原文或现场事实。';
}));
controller.edit(draft);assert.deepEqual(controller.issues(),[]);await controller.makePreview();
assert.equal(controller.preview.kind,'revision');assert.equal(controller.preview.family,'v4');
await assert.rejects(()=>controller.submit(true),/Synthetic response loss after actual graph revision commit/);
assert.ok(dropped);let pending=controller.readPending();assert.ok(pending);assert.equal(pending.receipt,null);assert.equal(pending.schema,'full-semantic-graph-adoption-pending-v3');
const exact=pending.exact_body,nonce=pending.body.request_id,commandHash=pending.command_hash;
assert.ok(exact.includes('"right":{"kind":"number","op":"value","value":1e+21}')||exact.includes('"right":{"kind":"number","op":"value","value":1e21}'));
assert.ok(exact.includes('"minimum":-0.0'),'the untouched adopted schema retains its original signed float');
assert.deepEqual(pending.body.parent,input.parent);assert.equal(calls.filter(call=>call.path===base+'/v4/revisions/adopt').length,1);
controller.dispose();controller=make();assert.equal(controller.readPending().exact_body,exact);const coldStart=calls.length;
assert.equal(await controller.recover(),true);pending=controller.readPending();assert.ok(pending.receipt);assert.equal(pending.exact_body,exact);assert.equal(pending.body.request_id,nonce);
assert.ok(calls.slice(coldStart).every(call=>call.method==='GET'||call.path===base+'/v4/command-hash'),'cold restore only rechecks the original body hash and original server receipt');
assert.ok(calls.slice(coldStart).some(call=>call.path===base+'/v4/revisions/requests/'+nonce));
const saved=pending.receipt;
await controller.acceptCurrent(async(_scope,settle)=>settle(await controller.api(base+'/v4/configuration')));
assert.equal(controller.pending,null);const archived=JSON.parse(storage.getItem(controller.prefix+'accepted:'+nonce));assert.equal(archived.exact_body,exact);assert.equal(archived.command_hash,commandHash);
assert.equal(calls.filter(call=>call.path===base+'/v4/revisions/adopt').length,1);
process.stdout.write(JSON.stringify({kind:'done',request_id:nonce,exact_body:exact,command_hash:commandHash,parent:input.parent,receipt:saved,recovered_original:true,adopt_posts:1})+'\n');
controller.dispose();lines.close();
