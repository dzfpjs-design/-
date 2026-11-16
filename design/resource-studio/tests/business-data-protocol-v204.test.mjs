import test from 'node:test';
import assert from 'node:assert/strict';
import {canonical,checkedBody,checkedPending,checkedRecovery,commandPath,locationLabel} from '../src/business-data-protocol-v204.mjs';
const id=n=>'20400000-0000-4000-8000-'+String(n).padStart(12,'0');
const scope={actor_id:id(1),workspace_id:id(2),instance_id:null};
const body={request_id:id(3),name:'业务台账',kind:'xlsx',endpoint:null,secret_ref:null};
const pending=()=>({schema:'business-data-outbox-v204',scope,kind:'create_source',context:{},method:'POST',body:structuredClone(body),raw:canonical(body),sha256:'a'.repeat(64),rejected:null});
test('XLSX is a first class source; no raw credentials, implicit file endpoint, float CAS or row identity',()=>{
 assert.equal(checkedBody('create_source',body),body);
 for(const bad of [{...body,endpoint:'https://provider.invalid/'},{...body,kind:'xls'},
  {...body,kind:'http_json',endpoint:'https://provider.invalid/?token=secret'},
  {...body,kind:'http_json',endpoint:'https://provider.invalid/',secret_ref:'real-secret-value'}])assert.throws(()=>checkedBody('create_source',bad));
 assert.throws(()=>checkedBody('read',{request_id:id(4),expected_version:1.5}));
 assert.throws(()=>checkedBody('confirm',{request_id:id(4),expected_version:true,confirm_hash:'a'.repeat(64),accept_partial:false}));
 const binding={request_id:id(4),source_id:id(5),source_expected_version:1,read_id:id(6),instance_id:id(7),release_id:id(8),object_type:'custom',schema_hash:'a'.repeat(64),record_set:'',external_id_path:'/编号',fields:[{target:'name',source:'/名称',conversion:'identity'}]};
 assert.equal(checkedBody('binding',binding).record_set,'');assert.throws(()=>checkedBody('binding',{...binding,record_set:null}));
});
test('canonical original binds exact closed command, scope, method and declared path; no arbitrary transport routes',()=>{
 const p=pending();assert.deepEqual(checkedPending(p,scope),p);
 assert.equal(canonical({a:true,b:1}),'{"a":true,"b":1}');assert.notEqual(canonical(true),canonical(1));
 assert.throws(()=>checkedPending({...p,body:{...body,name:'换过内容'}},scope));
 assert.throws(()=>checkedPending({...p,method:'DELETE'},scope));
 assert.throws(()=>checkedPending(p,{...scope,workspace_id:id(9)}));
 assert.throws(()=>commandPath('external_unsafe',{url:'https://outside.invalid'}));
 assert.equal(commandPath('retry_ack',{import_id:id(5),row_id:id(6)}),'/business-data/imports/'+id(5)+'/rows/'+id(6)+'/retry-data-delivery');
});
test('recovery requires complete original body and exact historical IDs; found:false never creates receipt',()=>{
 const p=pending();assert.equal(checkedRecovery({schema:'business-data-command-v204',found:false,body:null,result:null},p),null);
 assert.throws(()=>checkedRecovery({schema:'business-data-command-v204',found:false,body:null,result:{id:id(7)}},p));
 assert.throws(()=>checkedRecovery({schema:'business-data-command-v204',found:true,body:{...body,name:'其他'},result:{id:id(7)}},p));
 assert.equal(checkedRecovery({schema:'business-data-command-v204',found:true,body:{...body},result:{id:id(7)}},p).id,id(7));
 const edit={...p,kind:'edit_source',context:{source_id:id(7)},method:'PUT',body:{...body,expected_version:1}};edit.raw=canonical(edit.body);
 assert.throws(()=>checkedRecovery({schema:'business-data-command-v204',found:true,body:edit.body,result:{id:id(8)}},edit));
 assert.equal(locationLabel({sheet:'台账',row:3}),'工作表 台账 · 行 3');
});
