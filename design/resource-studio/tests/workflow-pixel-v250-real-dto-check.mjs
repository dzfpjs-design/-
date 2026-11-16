// Root supplies an explicit actual HTTP capture. No fixture, network, token,
// database or synthetic fallback; output contains only checked type counts.
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {pixelNeed as need,pixelUUID,pixelCanonical} from '../src/pixel-change-v249-protocol.mjs';
import {registrationExact as exact,checkedPixelRegistrationOptions,checkedPixelRegistrationOriginal,checkedPixelRegistrationResult,checkedPixelRegistrationRecovery} from '../src/workflow-pixel-registration-v250-protocol.mjs';
import {taskPixelContext,checkedTaskPixelOptions,checkedTaskPixelPairSnapshot} from '../src/task-pixel-pair-v250.mjs';
import {checkedTaskPixelResults,checkedNativePixelEntry,checkedPixelMachineQuery,checkedPixelSettlementOriginal,checkedPixelSettlementResult,checkedPixelSettlementRecovery} from '../src/workflow-pixel-results-v250-protocol.mjs';
import {pixelRegistrationDay,checkedPixelMachineDaily,checkedDailyRebuildOriginal,checkedDailyRebuildResult,checkedDailyRebuildRecovery} from '../src/pixel-machine-daily-v250-protocol.mjs';
if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
need(process.argv.length===3,'Supply one explicit actual HTTP capture path');
const capture=JSON.parse(await readFile(process.argv[2],'utf8'));
need(exact(capture,['schema','required_checks','checks'])&&capture.schema==='workflow-pixel-real-capture-v250');
need(Array.isArray(capture.required_checks)&&capture.required_checks.length>0&&capture.required_checks.every(v=>typeof v==='string'&&v.length>0)&&new Set(capture.required_checks).size===capture.required_checks.length);
need(Array.isArray(capture.checks)&&capture.checks.length===capture.required_checks.length);
const kinds=new Set(['registration_options','registration_result','registration_recovery','task_options','pair_snapshot','task_results','native_entry','machine_query','machine_daily','settlement_result','settlement_recovery','daily_rebuild_result','daily_rebuild_recovery']),labels=new Set(),types={};
for(const row of capture.checks){
 need(exact(row,['label','kind','scope','parameters','original','value'])&&capture.required_checks.includes(row.label)&&!labels.has(row.label)&&kinds.has(row.kind));labels.add(row.label);
 const scope=row.scope,params=row.parameters;
 need(scope&&typeof scope==='object'&&!Array.isArray(scope)&&Object.hasOwn(scope,'instance_id')&&pixelUUID(scope.instance_id));
 if(row.kind==='task_options'){
  need(row.original===null&&params===null&&exact(scope,['instance_id','release_id','business_id']));checkedTaskPixelOptions(row.value,taskPixelContext(scope.instance_id,scope.release_id,scope.business_id));
 }else if(row.kind.startsWith('registration_')){
  need(exact(scope,['actor_id','workspace_id','instance_id','reference_run_id'])&&Object.values(scope).every(pixelUUID)&&params===null);
  if(row.kind==='registration_options'){need(row.original===null);checkedPixelRegistrationOptions(row.value,scope);}else{const p=await checkedPixelRegistrationOriginal(row.original,scope);if(row.kind==='registration_result')await checkedPixelRegistrationResult(row.value,scope,p);else await checkedPixelRegistrationRecovery(row.value,scope,p);}
 }else if(row.kind.startsWith('settlement_')){
  need(exact(scope,['actor_id','workspace_id','instance_id','task_id'])&&Object.values(scope).every(pixelUUID)&&params===null);const p=await checkedPixelSettlementOriginal(row.original,scope);if(row.kind==='settlement_result')await checkedPixelSettlementResult(row.value,scope,p);else await checkedPixelSettlementRecovery(row.value,scope,p);
 }else{
  need(exact(scope,['actor_id','workspace_id','instance_id',...(row.kind==='task_results'||row.kind==='pair_snapshot'?['task_id']:[])])&&Object.values(scope).every(pixelUUID));
  if(row.kind.startsWith('daily_rebuild_')){need(params===null);const p=await checkedDailyRebuildOriginal(row.original,scope);if(row.kind==='daily_rebuild_result')await checkedDailyRebuildResult(row.value,scope,p);else await checkedDailyRebuildRecovery(row.value,scope,p);}
  else{need(row.original===null);
   if(row.kind==='task_results'){need(params===null);await checkedTaskPixelResults(row.value,scope);}
   else if(row.kind==='pair_snapshot'){need(params===null);await checkedTaskPixelPairSnapshot(row.value);}
   else if(row.kind==='native_entry'){need(exact(params,['data_only','task_id'])&&typeof params.data_only==='boolean'&&(params.task_id===null||pixelUUID(params.task_id)));await checkedNativePixelEntry(row.value,scope,{dataOnly:params.data_only,taskId:params.task_id});}
   else if(row.kind==='machine_query'){need(exact(params,['task_id','entity_id'])&&Object.values(params).every(v=>v===null||pixelUUID(v)));await checkedPixelMachineQuery(row.value,scope,params);}
   else if(row.kind==='machine_daily'){need(exact(params,['registration_day']));await checkedPixelMachineDaily(row.value,scope,pixelRegistrationDay(params.registration_day));}
  }
 }
 types[row.kind]=(types[row.kind]||0)+1;
}
need(labels.size===capture.required_checks.length&&pixelCanonical([...labels].sort())===pixelCanonical([...capture.required_checks].sort()));
console.log(JSON.stringify({schema:'workflow-pixel-real-check-v250',checked:capture.checks.length,types,passed:true}));
