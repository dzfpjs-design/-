// Offline controller -> Root's real isolated API. No browser/network authority.
import assert from 'node:assert/strict';
import readline from 'node:readline';
import {GraphSourcesControllerV122} from '../src/graph-sources-controller-v122.mjs';
globalThis.fetch=()=>{throw Error('Offline controller bridge cannot use network');};
const lines=readline.createInterface({input:process.stdin});
const responses=new Map();let ready,seq=0;
const initialized=new Promise(resolve=>{ready=resolve;});
lines.on('line',line=>{const value=JSON.parse(line);if(value.kind==='init')ready(value);else{const pair=responses.get(value.id);if(!pair)throw Error('Unexpected bridge response');responses.delete(value.id);pair(value);}});
const input=await initialized,store=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)};};
const storage=store(),session=store(),scope={actor_id:input.me.user_id,workspace_id:input.me.workspace_id};
session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf',input.csrf);
const base='/setup/graph-event-producers';let dropped=false;
const api=async(path,options={})=>{
 const id=++seq,pending=new Promise(resolve=>responses.set(id,resolve));
 process.stdout.write(JSON.stringify({kind:'api',id,path,options})+'\n');
 const response=await pending;if(response.status>=400){const error=Error('Actual isolated backend rejected request');error.status=response.status;throw error;}
 if(!dropped&&options.method==='POST'&&path===(input.mode==='register'?base:base+'/revocations')){dropped=true;throw Error('Synthetic response loss after actual commit');}
 return response.body;
};
const make=()=>new GraphSourcesControllerV122({api,scope,storage,session});
let controller=make();await controller.open();
if(input.mode==='register'){
 for(const [kind,identity] of [['source',input.source_id],['extractor',input.extractor_id]]){
  for(let limit=0;!controller.pages[kind].items.some(row=>row.id===identity)&&controller.pages[kind].next_after&&limit<40;limit++)await controller.loadMore(kind);
  controller.chooseCapability(kind,identity);
 }
 controller.edit({label:input.label,adapter_id:'processed-observation-v1'});
 await controller.previewRegistration();
 await assert.rejects(()=>controller.register(),/Synthetic response loss/);
 assert(controller.pending);const original=controller.pending.exact_body;
 controller.dispose();controller=make();assert.equal(controller.pending.exact_body,original);
 const producer=await controller.recover();assert.equal(controller.pending,null);assert.equal(producer.label,input.label);
 process.stdout.write(JSON.stringify({kind:'done',producer_id:producer.id,recovered_original:true,body:JSON.parse(original)})+'\n');
}else if(input.mode==='revoke'){
 await controller.selectRegistration(input.producer_id);controller.setReason('已完成本次实际合成旅程，明确停止继续供业务使用');
 await assert.rejects(()=>controller.revoke(),/Synthetic response loss/);assert(controller.pending);
 const original=controller.pending.exact_body;controller.dispose();controller=make();assert.equal(controller.pending.exact_body,original);
 const receipt=await controller.recover();assert.equal(controller.pending,null);assert.equal(receipt.result.status,'revoked');
 process.stdout.write(JSON.stringify({kind:'done',receipt,recovered_original:true,body:JSON.parse(original)})+'\n');
}else throw Error('Unknown explicit controller bridge mode');
controller.dispose();lines.close();
