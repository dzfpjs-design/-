// Root forwards API calls to the genuine isolated server. The first retry is
// deliberately not delivered; the retirement loses its response after commit.
import assert from 'node:assert/strict';
import readline from 'node:readline';
import {GraphSourceDeliveryControllerV124} from '../src/graph-source-delivery-controller-v124.mjs';
globalThis.fetch=()=>{throw Error('Offline controller bridge cannot use network');};
const lines=readline.createInterface({input:process.stdin}),responses=new Map();let ready,seq=0;
const init=new Promise(resolve=>{ready=resolve;});
lines.on('line',line=>{const value=JSON.parse(line);if(value.kind==='init')ready(value);else{const pair=responses.get(value.id);assert(pair);responses.delete(value.id);pair(value);}});
const input=await init,scope={actor_id:input.me.user_id,workspace_id:input.me.workspace_id};
const store=()=>{const values=new Map();return {get length(){return values.size;},key:index=>[...values.keys()][index]??null,getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)};};
const storage=store(),base='/setup/graph-event-producers/intents/'+input.intent_id;let originalDropped=false,retirementReplyLost=false;
const api=async(path,options={})=>{
 if(options.method==='POST'&&path===base+'/retry'&&!originalDropped){
  originalDropped=true;process.stdout.write(JSON.stringify({kind:'phase',phase:'original_not_sent',body:JSON.parse(options.body),exact_body:options.body})+'\n');
  throw Error('Synthetic original retry not delivered');
 }
 const id=++seq,pending=new Promise(resolve=>responses.set(id,resolve));process.stdout.write(JSON.stringify({kind:'api',id,path,options})+'\n');
 const response=await pending;if(response.status>=400){const error=Error('Actual isolated API rejected the command');error.status=response.status;throw error;}
 if(options.method==='POST'&&path===base+'/retry-retirements'&&!retirementReplyLost){retirementReplyLost=true;throw Error('Synthetic retirement response lost after actual commit');}
 return response.body;
};
const make=()=>{const session=store();session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf',input.csrf);return new GraphSourceDeliveryControllerV124({api,scope,storage,session});};
let controller=make();await controller.open(input.producer_id);await controller.selectIntent(input.intent_id);
controller.editReason('先前页面按当时状态申请恢复这份原结果');
await assert.rejects(()=>controller.submitRetry(),/Synthetic original retry not delivered/);assert(originalDropped&&controller.pending);
const original_exact_body=controller.pending.exact_body,original_body=JSON.parse(original_exact_body);
assert.equal((await controller.recover()).outcome,'not_found');assert.equal(controller.pending.exact_body,original_exact_body);
await assert.rejects(()=>controller.resend(),error=>error.status===409);assert.equal(controller.pending.exact_body,original_exact_body);
assert.equal((await controller.recover()).outcome,'not_found');
controller.setRetirementReason(' 后台版本已经变化，结束这次未保存的旧恢复操作 ');
await assert.rejects(()=>controller.retireOriginal(),/Synthetic retirement response lost/);assert(controller.retirement&&controller.pending);
const retirement_exact_body=controller.retirement.exact_body,retirement_body=JSON.parse(retirement_exact_body);
assert.deepEqual(retirement_body.original,original_body);assert.notEqual(retirement_body.request_id,original_body.request_id);
controller.dispose();controller=make();assert.equal(controller.pending,null);assert.equal(controller.retirement,null);await controller.open();
assert.equal(controller.pending,null);assert.equal(controller.retirement,null);await controller.discoverRetirements();let candidate;
for(let index=0;index<40;index++){
 candidate=controller.retirementRecovery.items.find(row=>JSON.parse(row.raw).body?.request_id===retirement_body.request_id);
 if(candidate||controller.retirementRecovery.next_offset===null)break;await controller.discoverRetirements(true);
}
assert(candidate,'A fresh empty session must discover the exact retirement outbox');
await controller.restoreRetirement(candidate);assert.equal(controller.pending,null);assert.equal(controller.retirement,null);
assert.equal(controller.lastRetirement.request_id,retirement_body.request_id);assert.equal(controller.lastReceipt,null);
const retired_state=await controller.request(base+'/retry-command-requests/'+original_body.request_id);
assert.equal(retired_state.outcome,'retired');assert.deepEqual(retired_state.retirement.command,retirement_body);
const retirement_lookup=await controller.request(base+'/retry-retirements/requests/'+retirement_body.request_id);
assert.equal(retirement_lookup.found,true);assert.deepEqual(retirement_lookup.retirement,retired_state.retirement);
await controller.selectIntent(input.intent_id);assert(controller.detail.delivery.version>original_body.job_version);
controller.editReason('旧操作已安全结束，按当前状态恢复同一份处理结果');await controller.submitRetry();assert.equal(controller.pending,null);
const fresh_receipt=controller.lastReceipt;assert(fresh_receipt&&fresh_receipt.request_id!==original_body.request_id);
assert(fresh_receipt.command.job_version>original_body.job_version);
const fresh_state=await controller.request(base+'/retry-command-requests/'+fresh_receipt.request_id);
assert.equal(fresh_state.outcome,'saved');assert.deepEqual(fresh_state.saved,fresh_receipt);
process.stdout.write(JSON.stringify({kind:'done',original_body,original_exact_body,retirement_body,retirement_exact_body,
 retired_state,retirement_lookup,fresh_body:fresh_receipt.command,fresh_state,cold_session:true,original_request_not_sent:true,original_version_advanced:true})+'\n');
controller.dispose();lines.close();
