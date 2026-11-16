// Root-only synthetic response loss after a genuine API transaction commits.
// A fresh Storage object proves controller recovery, not browser process restart.
import assert from 'node:assert/strict';
import readline from 'node:readline';
import {GraphSourceDeliveryControllerV124} from '../src/graph-source-delivery-controller-v124.mjs';
globalThis.fetch=()=>{throw Error('Offline controller bridge cannot use network');};
const lines=readline.createInterface({input:process.stdin}),responses=new Map();let ready,seq=0;
const init=new Promise(resolve=>{ready=resolve;});
lines.on('line',line=>{const value=JSON.parse(line);if(value.kind==='init')ready(value);else{const pair=responses.get(value.id);assert(pair);responses.delete(value.id);pair(value);}});
const input=await init,scope={actor_id:input.me.user_id,workspace_id:input.me.workspace_id};
const store=()=>{const values=new Map();return {get length(){return values.size;},key:index=>[...values.keys()][index]??null,getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)};};
const storage=store(),base='/setup/graph-event-producers',postPath=base+'/intents/'+input.intent_id+'/retry';let replyLost=false;
const api=async(path,options={})=>{
 const id=++seq,pending=new Promise(resolve=>responses.set(id,resolve));process.stdout.write(JSON.stringify({kind:'api',id,path,options})+'\n');
 const response=await pending;if(response.status>=400){const error=Error('Actual isolated API rejected the controller command');error.status=response.status;throw error;}
 if(options.method==='POST'&&path===postPath&&!replyLost){replyLost=true;throw Error('Synthetic retry response lost after actual commit');}
 return response.body;
};
const make=()=>{const session=store();session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf',input.csrf);return new GraphSourceDeliveryControllerV124({api,scope,storage,session});};
let controller=make();await controller.open(input.producer_id);await controller.selectIntent(input.intent_id);
controller.editReason(input.mode==='queued'?'按这份原结果恢复投递，不重复提取信息':'核对已接纳原回执并修复后台投递状态');
await assert.rejects(()=>controller.submitRetry(),/Synthetic retry response lost/);assert(replyLost&&controller.pending);
const exact_body=controller.pending.exact_body,body=JSON.parse(exact_body);
controller.dispose();controller=make();assert.equal(controller.pending,null);await controller.open();assert.equal(controller.pending,null);
let candidate;
for(let index=0;index<40;index++){
 candidate=controller.recovery.items.find(row=>JSON.parse(row.raw).body?.request_id===body.request_id);
 if(candidate||controller.recovery.next_offset===null)break;await controller.discoverRecovery(true);
}
assert(candidate,'A new empty session must discover this exact scoped original outbox');
assert.equal(candidate.intent_id,input.intent_id);assert.equal(candidate.producer_id,input.producer_id);
const state=await controller.restoreRecovery(candidate);assert.equal(state.outcome,'saved');assert.equal(controller.pending,null);
const lookup=await controller.request(base+'/intents/'+input.intent_id+'/retry-requests/'+body.request_id);
assert.equal(lookup.found,true);assert.equal(lookup.legacy,null);assert(lookup.receipt);
assert.deepEqual(lookup.receipt.command,body);assert.equal(lookup.receipt.intent_id,input.intent_id);
assert.equal(lookup.receipt.producer_id,input.producer_id);assert.equal(lookup.receipt.actor_id,scope.actor_id);
assert.equal(lookup.receipt.result.outcome,input.mode);assert.equal(controller.lastReceipt.request_id,body.request_id);
process.stdout.write(JSON.stringify({kind:'done',body,exact_body,lookup,cold_session:true,original_reply_lost:true})+'\n');
controller.dispose();lines.close();
