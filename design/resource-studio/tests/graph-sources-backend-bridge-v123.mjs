// Root forwards only these explicit controller calls to its real isolated API.
// A cold session here is a new Storage object; this is not a browser proof.
import assert from 'node:assert/strict';
import readline from 'node:readline';
import {GraphSourcesControllerV122} from '../src/graph-sources-controller-v122.mjs';
globalThis.fetch=()=>{throw Error('Offline bridge cannot use network');};
const lines=readline.createInterface({input:process.stdin}),responses=new Map();let ready,seq=0;
const init=new Promise(resolve=>{ready=resolve;});
lines.on('line',line=>{const v=JSON.parse(line);if(v.kind==='init')ready(v);else{const pair=responses.get(v.id);assert(pair);responses.delete(v.id);pair(v);}});
const input=await init,scope={actor_id:input.me.user_id,workspace_id:input.me.workspace_id};
const store=()=>{const values=new Map();return {get length(){return values.size;},key:i=>[...values.keys()][i]??null,getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)};};
const storage=store(),base='/setup/graph-event-producers',retiring=input.mode.startsWith('retire_');
const kind=input.mode==='register'||input.mode==='retire_register'?'register':'revoke';let sourceReplyLost=false,retirementReplyLost=false;
const api=async(path,options={})=>{
 const sourcePost=options.method==='POST'&&path===(kind==='register'?base:base+'/revocations');
 if(retiring&&sourcePost&&!sourceReplyLost){sourceReplyLost=true;throw Error('Synthetic source request not delivered');}
 const id=++seq,pending=new Promise(resolve=>responses.set(id,resolve));process.stdout.write(JSON.stringify({kind:'api',id,path,options})+'\n');
 const response=await pending;if(response.status>=400){const error=Error('Actual isolated backend rejected request');error.status=response.status;throw error;}
 if(!retiring&&sourcePost&&!sourceReplyLost){sourceReplyLost=true;throw Error('Synthetic source reply lost after actual commit');}
 if(retiring&&options.method==='POST'&&path===base+'/command-retirements'&&!retirementReplyLost){retirementReplyLost=true;throw Error('Synthetic retirement reply lost after actual commit');}
 return response.body;
};
const make=()=>{const session=store();session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf',input.csrf);return new GraphSourcesControllerV122({api,scope,storage,session});};
let c=make();await c.open();
if(kind==='register'){
 for(const [type,id] of [['source',input.source_id],['extractor',input.extractor_id]]){
  for(let n=0;!c.pages[type].items.some(row=>row.id===id)&&c.pages[type].next_after&&n<40;n++)await c.loadMore(type);
  c.chooseCapability(type,id);
 }
 c.edit({label:input.label,adapter_id:'processed-observation-v1'});await c.previewRegistration();
 await assert.rejects(()=>c.register(),/Synthetic source/);
}else{await c.selectRegistration(input.producer_id);c.setReason('本次合成处理结果停用核对');await assert.rejects(()=>c.revoke(),/Synthetic source/);}
assert(c.pending);const original=c.pending.exact_body,body=JSON.parse(original);
async function coldRestore(type,nonce){
 c.dispose();c=make();assert.equal(c.pending,null);assert.equal(c.form.label,'');await c.open();assert.equal(c.pending,null);assert.equal(c.form.label,'');
 let candidate;
 for(let n=0;n<40;n++){
  candidate=c.recovery.items.find(row=>row.kind===type&&JSON.parse(row.raw).body?.request_id===nonce);
  if(candidate||c.recovery.next_offset===null)break;await c.discoverRecovery(true);
 }
 assert(candidate,'The original scoped local record must be discoverable with an empty session');await c.restoreRecovery(candidate);
}
await coldRestore('request',body.request_id);
if(retiring){
 assert(c.pending&&c.pendingNotFound);assert.equal(c.pending.exact_body,original);
 c.setRetirementReason('保留原正文并停止这次尚未送达的合成操作');
 await assert.rejects(()=>c.retireOriginal(),/Synthetic retirement reply lost/);assert(c.retirement);
 const retirementBody=JSON.parse(c.retirement.exact_body);await coldRestore('retirement',retirementBody.request_id);
 assert.equal(c.pending,null);assert.equal(c.retirement,null);
 const state=await c.request(base+'/command-requests/'+kind+'/'+body.request_id);
 assert.equal(state.outcome,'retired');assert.deepEqual(state.retirement.command,retirementBody);
 process.stdout.write(JSON.stringify({kind:'done',body,retirement_body:retirementBody,retirement:state.retirement,recovered_original:true,cold_session:true})+'\n');
}else{
 assert.equal(c.pending,null);const state=await c.request(base+'/command-requests/'+kind+'/'+body.request_id);assert.equal(state.outcome,'saved');
 if(kind==='register'){assert.equal(state.saved.label,input.label);process.stdout.write(JSON.stringify({kind:'done',body,producer_id:state.saved.id,recovered_original:true,cold_session:true})+'\n');}
 else{assert.equal(state.saved.result.status,'revoked');process.stdout.write(JSON.stringify({kind:'done',body,receipt:state.saved,recovered_original:true,cold_session:true})+'\n');}
}
c.dispose();lines.close();
