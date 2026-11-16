import test from 'node:test';
import assert from 'node:assert/strict';
import {checkedBusinessDataHistoryV204,businessDataHistoryPathV204} from '../src/business-data-history-v204.mjs';
import {canonical,checkedPending,checkedRecovery,commandPath} from '../src/business-data-protocol-v204.mjs';
const id=n=>'20400000-0000-4000-8000-'+String(n).padStart(12,'0');
const history=()=>({schema:'business-data-history-v204',subject_type:'read',subject_id:id(1),entries:[{id:id(2),at:'2026-10-25T01:02:03+00:00',actor:'原操作人',label:'读取未完成',detail:'原资料保留',status:'failed'}],next_cursor:'opaque+/==',continuation:{count:null,notice:'历史次数尚不能核实'}});
test('history remains closed read-only human view with exact subject and opaque encoded pagination',()=>{
 const value=history();assert.equal(checkedBusinessDataHistoryV204(value,'read',id(1)),value);
 assert.equal(businessDataHistoryPathV204('read',id(1),'opaque+/=='),'/business-data/reads/'+id(1)+'/history?cursor=opaque%2B%2F%3D%3D');
 for(const bad of [{...value,subject_type:'import'},{...value,subject_id:id(3)},{...value,entries:[{...value.entries[0],status:'unknown'}]},{...value,continuation:{count:-1,notice:null}},{...value,next_cursor:''},{...value,entries:[...value.entries,...value.entries]}])assert.throws(()=>checkedBusinessDataHistoryV204(bad,'read',id(1)));
 assert.equal(value.continuation.count,null,'unknown does not become zero');assert.throws(()=>businessDataHistoryPathV204('POST',id(1)));
});
test('day ACK command binds exact source/event and recovered version progression with unchanged original reason',()=>{
 const scope={actor_id:id(1),workspace_id:id(2),instance_id:null},body={request_id:id(3),expected_version:2,reason:'恢复原每日记录的接收'},context={source_id:id(4),event_id:id(5)},p={schema:'business-data-outbox-v204',scope,kind:'retry_day_ack',context,method:'POST',body,raw:canonical(body),sha256:'a'.repeat(64),rejected:null};
 assert.deepEqual(checkedPending(p,scope),p);assert.equal(commandPath(p.kind,context),'/business-data/sources/'+id(4)+'/days/'+id(5)+'/retry-data-delivery');
 const result={id:id(5),source_id:id(4),version:3,status:'pending_delivery',data_delivery:{event_id:id(5),version:3,status:'pending_delivery',can_retry:false}};
 const response={schema:'business-data-command-v204',found:true,body,result};assert.deepEqual(checkedRecovery(response,p),result);
 for(const bad of [{...result,id:id(6)},{...result,source_id:id(6)},{...result,version:2},{...result,data_delivery:{...result.data_delivery,event_id:id(6)}},{...result,data_delivery:{...result.data_delivery,version:2}}])assert.throws(()=>checkedRecovery({...response,result:bad},p));
 assert.throws(()=>commandPath('retry_day_ack',{...context,event_id:'not-an-event'}));assert.equal(checkedRecovery({schema:'business-data-command-v204',found:false,body:null,result:null},p),null);
});
