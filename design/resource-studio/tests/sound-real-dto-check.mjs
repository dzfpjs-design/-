// Actual API capture only. No fixtures, network, private-directory discovery or token input.
// Outer: {schema:'sound-real-capture-v1',required_checks:[unique labels],checks:[Row]}.
// Row: {label,kind,account:{user_id,workspace_id},scope:{instance_id,task_id,release_id,business_id},original,run,value}.
// Scope fields not used by a kind are null. Original is the exact saved envelope, never rebuilt from server data.
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {pixelNeed as need,pixelUUID as uuid} from '../src/pixel-change-v249-protocol.mjs';
import {soundScope,checkedSoundOriginal,checkedSoundOptions,checkedSoundList,checkedSoundInput,checkedSoundRecovery} from '../src/sound-input-protocol.mjs';
import {soundTaskScope,soundRunScope,checkedTaskSounds,checkedSoundRun,checkedNativeSoundRecord,checkedSoundReviewOriginal,checkedSoundReviewReceipt,checkedSoundReviewRecovery} from '../src/task-sound-result-protocol.mjs';
import {settlementScope,checkedSettlementOriginal,checkedSettlementReceipt,checkedSettlementRecovery,checkedSettlementStatus} from '../src/sound-settlement-protocol.mjs';
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
need(process.argv.length===3,'Supply one explicit actual API capture path');if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const capture=JSON.parse(await readFile(process.argv[2],'utf8')),kinds=new Set(['input_options','input_list','input','upload_recovery','task','run','native','review_receipt','review_recovery','settlement_status','settlement_receipt','settlement_recovery']);
need(exact(capture,['schema','required_checks','checks'])&&capture.schema==='sound-real-capture-v1'&&Array.isArray(capture.required_checks)&&capture.required_checks.length>0&&capture.required_checks.every(label=>typeof label==='string'&&label.length>0)&&new Set(capture.required_checks).size===capture.required_checks.length&&Array.isArray(capture.checks)&&capture.checks.length===capture.required_checks.length);
const labels=new Set(),totals={};
for(const row of capture.checks){
 need(exact(row,['label','kind','account','scope','original','run','value'])&&capture.required_checks.includes(row.label)&&!labels.has(row.label)&&kinds.has(row.kind)&&exact(row.account,['user_id','workspace_id'])&&Object.values(row.account).every(uuid)&&exact(row.scope,['instance_id','task_id','release_id','business_id'])&&['instance_id','task_id','release_id'].every(key=>row.scope[key]===null||uuid(row.scope[key]))&&(row.scope.business_id===null||typeof row.scope.business_id==='string'&&row.scope.business_id.length>0));labels.add(row.label);
 const s=row.scope,account=row.account;
 if(['input_options','input_list','input','upload_recovery'].includes(row.kind)){
  const scope=soundScope(account,s.instance_id);need(row.run===null);const original=row.original===null?null:await checkedSoundOriginal(row.original,scope);
  if(row.kind==='input_options'){need(original===null);checkedSoundOptions(row.value,scope);}else if(row.kind==='input_list'){need(original===null);checkedSoundList(row.value,scope,row.value.offset);}else if(row.kind==='input')checkedSoundInput(row.value,scope,original);else{need(original!==null);await checkedSoundRecovery(row.value,original);}
 }else if(row.kind==='task'){need(row.original===null&&row.run===null);const value=await checkedTaskSounds(row.value,soundTaskScope(account,s.task_id));need(value.instance_id===s.instance_id&&value.release_id===s.release_id&&value.business_id===s.business_id);}
 else if(row.kind==='native'){need(row.original===null&&row.run===null);await checkedNativeSoundRecord(row.value,{workspace_id:account.workspace_id,instance_id:s.instance_id});}
 else if(row.kind==='run'){need(row.original===null&&row.run===null);await checkedSoundRun(row.value,{...soundTaskScope(account,s.task_id),instance_id:s.instance_id});}
 else{
  need(row.run!==null&&row.run.task_id===s.task_id);await checkedSoundRun(row.run,{...soundTaskScope(account,s.task_id),instance_id:s.instance_id});
  if(row.kind.startsWith('review_')){const scope=soundRunScope(account,s.instance_id,s.task_id,row.run.id),original=await checkedSoundReviewOriginal(row.original,scope);if(row.kind==='review_receipt')await checkedSoundReviewReceipt(row.value,row.run,original);else await checkedSoundReviewRecovery(row.value,original);}
  else{const scope=settlementScope(account,s,row.run);if(row.kind==='settlement_status'){need(row.original===null);checkedSettlementStatus(row.value,scope,row.run);}else{const original=await checkedSettlementOriginal(row.original,scope);if(row.kind==='settlement_receipt')checkedSettlementReceipt(row.value,scope,original);else await checkedSettlementRecovery(row.value,original,row.run);}}
 }
 totals[row.kind]=(totals[row.kind]||0)+1;
}
need(labels.size===capture.required_checks.length);console.log(JSON.stringify({schema:'sound-real-dto-check-v1',checked:labels.size,types:totals,passed:true}));
