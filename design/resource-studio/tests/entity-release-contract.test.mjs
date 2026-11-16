import test from 'node:test';
import assert from 'node:assert/strict';
import {taskObjectState,taskObjectSelectionIssues,assertTaskObjectSelection} from '../src/entity-release-contract.mjs';

const release='release-new';
const object=(changes={})=>({id:'original-entity',version:7,package_id:'package-original',entity_type:'object-same-name',attributes:{field_name:'原对象',field_value:20},object_compatibility:{usable:true,status:'compatible',reason:'',target_release_id:release,source_release_number:1,source_label:'原对象',source_type:'测量对象'},...changes});

test('only server eligibility for the exact target release permits current and compatible objects',()=>{
 const original=object();assert.equal(taskObjectState(original,release).usable,true);
 assert.equal(taskObjectState(object({object_compatibility:{...original.object_compatibility,status:'current'}}),release).usable,true);
 for(const proof of [undefined,null,{usable:true},{...original.object_compatibility,usable:'true'},{...original.object_compatibility,status:'incompatible'},{...original.object_compatibility,status:'unknown'},{...original.object_compatibility,target_release_id:'other-release'}])assert.equal(taskObjectState(object({package_id:'package-new',object_compatibility:proof}),release).usable,false);
 assert.equal(taskObjectState(original,null).usable,false);
});

test('reusing original IDs never rewrites original package, version or attributes',()=>{
 const original=object(),before=structuredClone(original),ids=[original.id];
 assert.equal(assertTaskObjectSelection([original],ids,release),ids);
 assert.deepEqual(original,before);assert.equal(original.package_id,'package-original');
 const state=taskObjectState(original,release);assert.match(state.description,/可沿用.*来源第 1 版.*对象第 7 版/);
 assert.equal(state.label,'原对象');assert.equal(state.type,'测量对象');
});

test('same names, attributes or source package cannot synthesize compatibility',()=>{
 const original=object({object_compatibility:undefined}),ids=[original.id],before=structuredClone(original);
 assert.throws(()=>assertTaskObjectSelection([original],ids,release),/尚未按当前实例版本核实/);
 assert.deepEqual(original,before);assert.deepEqual(ids,['original-entity']);
});

test('changed schema and unavailable sources retain selections with clear server reasons',()=>{
 const original=object(),unavailable=object({object_compatibility:{...original.object_compatibility,usable:false,status:'incompatible',reason:'字段单位已变化，不能沿用',source_label:null,source_type:null,source_release_number:null}}),ids=[original.id,'not-loaded'];
 const issues=taskObjectSelectionIssues([unavailable],ids,release);
 assert.equal(issues.length,2);assert.equal(issues[0].label,'已保存对象');assert.match(issues[0].reason,/单位已变化/);assert.match(issues[1].reason,/原选择保留/);
 assert.throws(()=>assertTaskObjectSelection([unavailable],ids,release),/原输入和选择已保留/);
 assert.deepEqual(ids,['original-entity','not-loaded']);assert.equal(taskObjectState(unavailable,release).label,'已保存对象');
});

test('reload and target release changes preserve the original draft but block stale qualification',()=>{
 const original=object(),draft={title:'尚未提交的人稿',selectedObjects:[original.id]},saved=JSON.stringify(draft),restored=JSON.parse(saved);
 assert.throws(()=>assertTaskObjectSelection([original],restored.selectedObjects,'release-later'),/当前实例版本核实/);
 assert.equal(JSON.stringify(restored),saved);
 assert.throws(()=>assertTaskObjectSelection([original],[],release),/请选择/);
 assert.throws(()=>assertTaskObjectSelection([original],[original.id,original.id],release),/重复/);
});
