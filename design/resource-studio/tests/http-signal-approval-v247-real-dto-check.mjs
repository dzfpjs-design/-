// Root validates explicitly supplied actual API captures. No fixture, network,
// credential loading or fallback. Missing evidence never becomes a checked item.
// Usage: node tests/http-signal-approval-v247-real-dto-check.mjs /absolute/capture.json
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {isAbsolute} from 'node:path';
import {webcrypto} from 'node:crypto';
import {httpApprovalScope,checkedHTTPApprovalOptions,checkedHTTPApprovalRoutes,checkedHTTPApprovalOccurrences,checkedHTTPApprovalTaskSource,checkedHTTPApprovalOriginal,checkedHTTPApprovalResult,checkedHTTPApprovalRecovery} from '../src/http-signal-approval-v247-protocol.mjs';
const file=process.argv[2];assert.ok(file&&isAbsolute(file),'Supply an explicit absolute path to actual API captures');
const capture=JSON.parse(await readFile(file,'utf8'));
const allowed=['schema','account','instance_id','required_checks','options','routes','original_command','command_result','command_recovery','waiting_occurrences','created_occurrences','occurrence_id','task','task_source'];
assert.ok(capture&&typeof capture==='object'&&Object.keys(capture).every(key=>allowed.includes(key)),'Unknown capture metadata is not permitted');
assert.equal(capture.schema,'http-signal-approval-real-capture-v247');
const kinds=['options','routes','enable_receipt','raw_recovery','waiting_data','approval_draft','task_source'];
assert.ok(Array.isArray(capture.required_checks)&&capture.required_checks.length>0&&capture.required_checks.every(kind=>kinds.includes(kind))&&new Set(capture.required_checks).size===capture.required_checks.length);
const scope=httpApprovalScope(capture.account,capture.instance_id),checked=[],values={};
if(capture.options!==undefined){values.options=await checkedHTTPApprovalOptions(capture.options,scope,webcrypto);checked.push('options');}
if(capture.routes!==undefined){values.routes=checkedHTTPApprovalRoutes(capture.routes,scope);checked.push('routes');}
if(capture.original_command!==undefined)values.original=await checkedHTTPApprovalOriginal(capture.original_command,scope,webcrypto);
if(capture.command_result!==undefined){
 assert.ok(values.original,'An actual enable receipt must be compared with the preserved original command');
 assert.equal(values.original.operation,'enable');
 values.result=await checkedHTTPApprovalResult(capture.command_result,scope,values.original,webcrypto);checked.push('enable_receipt');
}
if(capture.command_recovery!==undefined){
 assert.ok(values.original,'Raw recovery needs the preserved exact original');
 values.recovery=await checkedHTTPApprovalRecovery(capture.command_recovery,scope,values.original,webcrypto);
 assert.equal(values.recovery.found,true,'A missing result is unknown, not a proved recovered command');checked.push('raw_recovery');
}
for(const[kind,key,status]of [['waiting_data','waiting_occurrences','waiting_data'],['approval_draft','created_occurrences','approval_draft']]){
 if(capture[key]===undefined)continue;
 const page=checkedHTTPApprovalOccurrences(capture[key],scope),item=page.items.find(value=>value.id===capture.occurrence_id);
 assert.ok(item,'The requested actual exception record must be present in this captured page');assert.equal(item.status,status);
 if(status==='waiting_data')assert.equal(item.task_id,null,'Waiting must not pretend a draft has been saved');else assert.ok(item.task_id);
 if(values.routes)assert.ok(values.routes.routes.some(route=>route.id===item.route_id),'The record must belong to the actual captured route');
 values[kind]=item;checked.push(kind);
}
if(values.waiting_data&&values.approval_draft){
 for(const key of ['id','route_id','signal_id','observation_id','source_identity_hash'])assert.equal(values.waiting_data[key],values.approval_draft[key],'Waiting and saved draft must preserve the same original exception identity');
}
if(capture.task_source!==undefined){
 assert.ok(capture.task,'Task source proof requires its actual Task identity');
 const view=checkedHTTPApprovalTaskSource(capture.task_source,capture.task);assert.equal(view.found,true);assert.ok(view.source&&view.integrity_issue===null,'Only a successfully verified original source proves data receipt');
 assert.equal(view.source.instance_id,scope.instance_id);assert.equal(view.source.simulation,true);assert.equal(view.source.field_verified,false);assert.equal(view.source.source_time_verified,false);assert.equal(view.source.data_received,true);
 if(values.approval_draft){assert.equal(view.task_id,values.approval_draft.task_id);assert.equal(view.source.signal_id,values.approval_draft.signal_id);assert.equal(view.source.observation_id,values.approval_draft.observation_id);}
 checked.push('task_source');
}
for(const kind of capture.required_checks)assert.ok(checked.includes(kind),'Required actual evidence missing: '+kind);
assert.ok(checked.length>0);
console.log(JSON.stringify({checked_count:checked.length,checked_types:checked,required_count:capture.required_checks.length,required_evidence_present:true,status:'verified_captured_contracts_only'}));
