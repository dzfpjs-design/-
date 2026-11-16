import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyFlow,flowFor,flowSignature,flowProblems,flowConfirmed,saveFlow,setMaterialArchived} from '../src/setup-schema.js';
import {stableStringify} from '../src/content-signature.js';
import {withoutSecrets} from '../src/catalog.js';

const cite=id=>({material_id:id,block_id:'block-'+id,quote:'资料依据 '+id});
const step=(id,sourceId='A',citations=[])=>({id,name:'核对 '+id,agent:'人工确认',role:'负责人',output:'核对结论',sourceId,citations,nextId:'end'});
const flow=()=>({...emptyFlow(),trigger:'人工发起',output:'核对记录',steps:[step('first','A',[cite('A'),cite('B')])]});
function workspace(){return {instanceProfile:{domain:'设备运维',goal:'核对设备'},materials:['A','B','C'].map(id=>({id,name:id+'.txt',hash:'hash-'+id,archived:false})),flows:{}};}
function confirmed(value=flow()){return saveFlow(workspace(),'资料核对',value,true,'fixed-time');}
const current=w=>flowFor(w,'资料核对');
function legacySignature(w,value){
 return stableStringify(withoutSecrets({domain:w.instanceProfile?.domain||'',goal:w.instanceProfile?.goal||'',trigger:value.trigger,output:value.output,steps:value.steps,sources:value.steps.filter(s=>s.sourceId).map(s=>{const material=w.materials?.find(m=>m.id===s.sourceId);return {id:s.sourceId,name:material?.name,hash:material?.hash,archived:!!material?.archived,missing:!material};})}));
}

test('归档第二份步骤引用使相关确认失效，第一份引用和原件记录保留',()=>{
 let w=confirmed();w=saveFlow(w,'仅A',{...flow(),steps:[step('only-A','A')]},true,'fixed-time');
 const changed=setMaterialArchived(w,'B',true);
 assert.equal(flowConfirmed(changed,current(changed)),false);
 assert.equal(current(changed).confirmedSignature,null);
 assert.equal(current(changed).confirmedAt,null);
 assert.equal(flowConfirmed(changed,flowFor(changed,'仅A')),true);
 assert.equal(current(changed).steps[0].sourceId,'A');
 assert.deepEqual(current(changed).steps[0].citations,[cite('A'),cite('B')]);
 assert.equal(w.materials.find(m=>m.id==='B').archived,false);
 assert.equal(changed.materials.find(m=>m.id==='B').hash,'hash-B');
});

test('归档后恢复第二份引用不会自动重新确认，必须显式确认并可序列化恢复',()=>{
 const w=setMaterialArchived(setMaterialArchived(confirmed(),'B',true),'B',false);
 assert.deepEqual(flowProblems(w,current(w)),[]);
 assert.equal(flowConfirmed(JSON.parse(JSON.stringify(w)),current(w)),false);
 const approved=saveFlow(w,'资料核对',current(w),true,'reconfirmed');
 assert.equal(flowConfirmed(approved,current(approved)),true);
});

test('第二份步骤引用缺失时阻止确认，摘要变化使原确认失效',()=>{
 const w=confirmed();
 const missing={...w,materials:w.materials.filter(m=>m.id!=='B')};
 assert.equal(flowConfirmed(missing,current(missing)),false);
 assert.ok(flowProblems(missing,current(missing)).some(message=>message.includes('资料依据')));
 assert.throws(()=>saveFlow(missing,'资料核对',current(missing),true),/资料依据/);
 const changed={...w,materials:w.materials.map(m=>m.id==='B'?{...m,hash:'new-B'}:m)};
 assert.equal(flowConfirmed(changed,current(changed)),false);
});

test('无关资料C的归档和摘要变化不影响A+B流程',()=>{
 const w=setMaterialArchived(confirmed(),'C',true);
 w.materials=w.materials.map(m=>m.id==='C'?{...m,hash:'new-C'}:m);
 assert.equal(flowConfirmed(w,current(w)),true);
});

test('业务级引用B参与签名和归档失效，即使步骤只引用A',()=>{
 const w=confirmed({...flow(),steps:[step('first','A')],citations:[cite('B')],inferenceReason:'原业务说明'});
 assert.deepEqual(JSON.parse(flowSignature(w,current(w))).sources.map(m=>m.id),['A','B']);
 const changed=setMaterialArchived(w,'B',true);
 assert.equal(flowConfirmed(changed,current(changed)),false);
 assert.throws(()=>saveFlow(changed,'资料核对',current(changed),true),/资料依据/);
});

test('仅业务级引用也检查缺失和摘要变化',()=>{
 const w=confirmed({...flow(),steps:[step('manual','')],citations:[cite('B')]});
 for(const materials of [w.materials.filter(m=>m.id!=='B'),w.materials.map(m=>m.id==='B'?{...m,hash:'new-B'}:m)]){
  const changed={...w,materials};assert.equal(flowConfirmed(changed,current(changed)),false);
 }
});

test('业务级引用正文和推断说明改变使确认失效',()=>{
 const w=confirmed({...flow(),citations:[cite('B')],inferenceReason:'说明一'});
 for(const extra of [{citations:[{...cite('B'),quote:'改变引用'}]},{inferenceReason:'说明二'}]){
  assert.equal(flowConfirmed(w,{...current(w),...extra}),false);
 }
 const signature=JSON.parse(flowSignature(w,current(w)));
 assert.deepEqual(signature.citations,[cite('B')]);assert.equal(signature.inferenceReason,'说明一');
});

test('旧单sourceId及重复sourceId签名字节兼容，空业务级依据不扩展签名',()=>{
 const w=workspace();const value={...flow(),steps:[step('first','A'),step('second','A')]};
 for(const extra of [{},{citations:[]},{inferenceReason:''},{citations:[],inferenceReason:''}]){
  const old={...value,...extra};assert.equal(flowSignature(w,old),legacySignature(w,old));
 }
 assert.deepEqual(JSON.parse(flowSignature(w,value)).sources.map(m=>m.id),['A','A']);
});

test('非空业务级推断也绑定显式空引用，来源元数据结构不变',()=>{
 const w=workspace(),value={...flow(),inferenceReason:'尚待核对'};
 const signature=JSON.parse(flowSignature(w,value));
 assert.deepEqual(signature.citations,[]);assert.equal(signature.inferenceReason,'尚待核对');
 assert.deepEqual(signature.sources,[{id:'A',name:'A.txt',hash:'hash-A',archived:false,missing:false},{id:'B',name:'B.txt',hash:'hash-B',archived:false,missing:false}]);
});

test('依赖顺序先保留全部legacy重复，再追加步骤引用与业务级新引用',async()=>{
 const {flowSourceIds}=await import('../src/flow-sources.mjs');
 const value={steps:[step('one','A',[cite('B'),cite('A')]),step('two','A',[cite('D')]),step('three','C',[cite('B')])],citations:[cite('E'),cite('B')]};
 const before=JSON.stringify(value);
 assert.deepEqual(flowSourceIds(value),['A','A','C','B','D','E']);
 assert.equal(JSON.stringify(value),before);
 assert.deepEqual(flowSourceIds({steps:[],citations:[]}),[]);
 assert.deepEqual(flowSourceIds({steps:[step('literal',' A ',[{material_id:''},cite('B')]),step('empty','')],citations:[cite('B')]}),[' A ','B']);
});
