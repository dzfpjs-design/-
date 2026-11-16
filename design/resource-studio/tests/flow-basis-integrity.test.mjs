import test from 'node:test';
import assert from 'node:assert/strict';
import {synchronizeFlowBasis,retireFlowBasis} from '../src/flow-basis-integrity.mjs';
import {saveFlow,flowConfirmed} from '../src/setup-schema.js';
import {flowSourceIds} from '../src/flow-sources.mjs';

const cite=id=>({material_id:id,block_id:'block-'+id,quote:'原文-'+id});
const step=id=>({id,name:'核对'+id,purpose:'核对原件',agent:'人工确认',role:'负责人',output:'记录',sourceId:'A',citation:'原说明-A',citations:[cite('A')],inferenceReason:'模型解释-A',nextId:'end'});
const flow=()=>({trigger:'人工',output:'结论',citations:[cite('C')],inferenceReason:'业务模型说明-C',steps:[step('one'),{...step('two'),sourceId:'C',citations:[cite('C')]}]});
const workspace=value=>({instanceProfile:{domain:'核查',goal:'核对资料'},materials:['A','B','C'].map(id=>({id,name:id+'.txt',hash:'hash-'+id})),flows:{核查:value}});

test('source and manual basis replacement archive the original, leaving other step/business evidence current',()=>{
 const before=flow(),proposed=structuredClone(before);Object.assign(proposed.steps[0],{sourceId:'B',citation:'人工填写的新依据'});
 const saved=saveFlow(workspace(before),'核查',proposed,true,'confirmed').flows.核查,current=saved.steps[0];
 assert.equal(current.sourceId,'B');assert.equal(current.citation,'人工填写的新依据');assert.deepEqual(current.citations,[]);assert.equal(current.inferenceReason,'');assert.equal(current.basisKind,'manual');
 assert.equal(current.basisHistory.length,1);assert.deepEqual(current.basisHistory[0].citations,[cite('A')]);assert.equal(current.basisHistory[0].inferenceReason,'模型解释-A');
 assert.deepEqual(saved.steps[1],before.steps[1]);assert.deepEqual(saved.citations,before.citations);assert.equal(saved.inferenceReason,before.inferenceReason);
 assert.deepEqual(flowSourceIds(saved),['B','C']);assert.equal(flowConfirmed(workspace(saved),saved),true);assert.deepEqual(before,flow());
});

test('basis text edits and factual edits cannot retain copied structured evidence or model inference',()=>{
 for(const patch of [{citation:'新人工说明'},{role:'新负责人'},{output:'新产出'}]){
  const before=flow(),proposed=structuredClone(before);Object.assign(proposed.steps[0],patch);
  const current=synchronizeFlowBasis(before,proposed).steps[0];
  assert.deepEqual(current.citations,[]);assert.equal(current.inferenceReason,'');assert.equal(current.basisHistory[0].citation,'原说明-A');
  if(!('citation' in patch)){assert.equal(current.citation,'');assert.equal(current.sourceId,'');}
 }
 const before=flow(),proposed=structuredClone(before);proposed.trigger='新启动条件';
 const current=synchronizeFlowBasis(before,proposed);assert.deepEqual(current.citations,[]);assert.equal(current.inferenceReason,'');assert.deepEqual(current.steps,before.steps);assert.deepEqual(current.basisHistory[0].citations,[cite('C')]);
});

test('source-only legacy replacement also clears the previous material text; correctly replaced v6 references stay current',()=>{
 const before=flow(),legacy=structuredClone(before);legacy.steps[0].sourceId='B';
 assert.equal(synchronizeFlowBasis(before,legacy).steps[0].citation,'');
 const revised=structuredClone(before);Object.assign(revised.steps[0],{role:'新负责人',sourceId:'B',citation:'block-B：原文-B',citations:[cite('B')],inferenceReason:'本次模型解释-B'});
 const current=synchronizeFlowBasis(before,revised).steps[0];assert.deepEqual(current.citations,[cite('B')]);assert.equal(current.inferenceReason,'本次模型解释-B');assert.equal(current.sourceId,'B');
});

test('typing/reconfirmation is idempotent and history is bounded while retaining the original suggestion',()=>{
 let value=flow(),original=structuredClone(value.steps[0]);
 for(let i=0;i<80;i++){const proposed=structuredClone(value);proposed.steps[0].citation='人工说明 '+i;value=synchronizeFlowBasis(value,proposed);}
 assert.equal(value.steps[0].basisHistory.length,1);assert.deepEqual(value.steps[0].basisHistory[0].citations,original.citations);assert.deepEqual(synchronizeFlowBasis(flow(),value),value);
 let item=step('one');for(let i=0;i<35;i++){item={...item,sourceId:'A'+i,citation:'说明'+i};item=retireFlowBasis(item);}
 assert.equal(item.basisHistory.length,20);assert.equal(item.basisHistory[0].citation,'说明0');assert.ok(item.basisHistoryOmitted>0);
});

test('an explicit withdrawal is saved as history and does not resurrect on a later save',()=>{
 const before=flow(),proposed=retireFlowBasis(before,'business');const saved=synchronizeFlowBasis(before,proposed);
 assert.deepEqual(saved.citations,[]);assert.equal(saved.inferenceReason,'');assert.deepEqual(saved.steps,before.steps);assert.equal(saved.basisHistory.length,1);
 assert.deepEqual(synchronizeFlowBasis(saved,saved),saved);assert.deepEqual(saved.basisHistory[0].citations,[cite('C')]);
});


test('reordering/duplicating old citations or adding metadata cannot disguise stale evidence as a new reference',()=>{
 const before=flow();before.steps[0].citations=[cite('A'),cite('C')];const proposed=structuredClone(before);
 Object.assign(proposed.steps[0],{sourceId:'B',citation:'人工新说明',citations:[{...cite('C'),note:'new irrelevant metadata'},cite('A'),cite('A')]});
 const current=synchronizeFlowBasis(before,proposed).steps[0];assert.deepEqual(current.citations,[]);assert.equal(current.sourceId,'B');assert.equal(current.citation,'人工新说明');assert.deepEqual(current.basisHistory[0].citations,[cite('A'),cite('C')]);
});


test('dropping one old reference does not make the remaining old reference fresh after manual replacement',()=>{
 const before=flow();before.steps[0].citations=[cite('A'),cite('C')];const proposed=structuredClone(before);
 Object.assign(proposed.steps[0],{sourceId:'B',citation:'人工新说明',citations:[cite('A')]});
 assert.deepEqual(synchronizeFlowBasis(before,proposed).steps[0].citations,[]);
});
