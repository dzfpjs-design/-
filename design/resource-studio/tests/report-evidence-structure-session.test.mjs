import test from 'node:test';
import assert from 'node:assert/strict';
import {appendEvidenceStructure} from '../src/report-evidence-structure.mjs';
import {EVIDENCE_WORK_V2} from '../src/report-evidence-structure-draft.mjs';
import {fixture,account,memory,tabFor,id} from './report-evidence-reading-fixture.mjs';
const priorSession=globalThis.sessionStorage;globalThis.sessionStorage??=tabFor();
const {ReportEvidenceReadingSession}=await import('../src/report-evidence-reading-session.mjs');
test.after(()=>{if(priorSession===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=priorSession;});

const clone=structuredClone;
function manual(){return {schema:'report-evidence-manual-review-v5',note:'原人工说明',result:{schema:'report-evidence-candidate-wire-v5',source_hash:'a'.repeat(64),catalog_hash:'b'.repeat(64),domain:'合成核对',global_claims:[],objects:[],relationships:[],businesses:[],questions:['原待核事项']}};}
function create(storage=memory(),role='admin'){
 const calls=[],a=new ReportEvidenceReadingSession({account:{...account,role},materialID:id(1),storage,tabStorage:tabFor(),request:async(...args)=>{calls.push(args);throw Error('No HTTP in working-state test');}});
 a.state={...a.state,draft_id:id(90),revision:4,value:manual()};a.row={id:id(90)};a.persist();
 return {a,calls,storage};
}
const draft=value=>({key:'business:new',kind:'business',business_index:null,base_value:clone(value),fields:{name:'',reason:''}});

test('legacy v1 remains exact until a form is edited; v2 partial raw input survives new owner restore',()=>{
 const {a,storage}=create(),old=clone(a.state);assert.equal(a.read(a.key).schema,'report-evidence-reading-working-v1');
 const form=draft(a.state.value);form.fields.name='漏项业务末字🙂';form.fields.reason='尚未填完\n尾字Z\0';assert.equal(a.editStructureForm(form),true);
 assert.equal(a.state.schema,EVIDENCE_WORK_V2);assert.deepEqual(a.read(a.key).structure_form,form);
 const b=create(storage).a,foreign=b.foreign().find(s=>s.owner===a.owner);assert.ok(foreign);b.restore(foreign);
 assert.deepEqual(b.state.structure_form,form);assert.equal(b.state.value.note,old.value.note);assert.notEqual(b.owner,a.owner);
 a.dispose();b.dispose();
});
test('join is one state transition: full manual value consumes form without touching original model',()=>{
 const {a}=create(),model=clone(a.state.value);a.row.model_result=clone(model.result);
 const form=draft(a.state.value);form.fields={name:'新增人工业务',reason:'此业务需核对依据'};a.editStructureForm(form);const before=a.state.generation;
 const next=appendEvidenceStructure(a.state.value,{kind:'business',payload:form.fields});assert.equal(a.applyStructureValue(next),true);
 assert.equal(a.state.generation,before+1);assert.equal(a.state.structure_form,null);assert.deepEqual(a.read(a.key).value,next);
 assert.deepEqual(a.row.model_result,model.result);assert.deepEqual(next.result.questions,['原待核事项','此业务需核对依据']);a.dispose();
});
test('stale base rejects join with all input retained; explicit rebase keeps fields and admits current manual',()=>{
 const {a}=create(),form=draft(a.state.value);form.fields={name:'补充业务',reason:'尚需核对'};a.editStructureForm(form);
 a.editValue({...a.state.value,note:'另一处人工改字末字Z'});
 const next=appendEvidenceStructure(a.state.value,{kind:'business',payload:form.fields});assert.throws(()=>a.applyStructureValue(next),/人工稿已变化/);
 assert.deepEqual(a.state.structure_form.fields,form.fields);a.editStructureForm({...form,base_value:clone(a.state.value)});a.applyStructureValue(next);
 assert.equal(a.state.value.note,'另一处人工改字末字Z');a.dispose();
});
test('unjoined form blocks a new server save before identity, RPC or nonce creation',async()=>{
 const {a,calls}=create();a.editStructureForm(draft(a.state.value));await assert.rejects(a.save(),/尚未加入/);assert.equal(calls.length,0);assert.equal(a.state.pending,null);a.dispose();
});
test('quota failure keeps pending fields in memory and memory capture restores v2 without a provider',()=>{
 const {a,storage}=create(),set=storage.setItem.bind(storage);a.editStructureForm(draft(a.state.value));storage.setItem=()=>{throw Error('quota');};
 const form=clone(a.state.structure_form);form.fields.reason='无法写入时的末字🙂';assert.equal(a.editStructureForm(form),false);assert.equal(a.localSaved,false);
 const captured=a.capture();assert.deepEqual(captured.structure_form,form);storage.setItem=set;
 const b=create().a;b.restoreMemory(captured);assert.deepEqual(b.read(b.key).structure_form,form);a.dispose();b.dispose();
});
test('join quota failure retains entire joined value in memory, exposes local error and never appends twice',()=>{
 const {a,storage}=create(),form=draft(a.state.value);form.fields={name:'需保留业务',reason:'理由末字Z'};a.editStructureForm(form);
 const next=appendEvidenceStructure(a.state.value,{kind:'business',payload:form.fields});storage.setItem=()=>{throw Error('quota');};
 assert.equal(a.applyStructureValue(next),false);assert.equal(a.localSaved,false);assert.match(a.localError,/未可靠保存/);
 assert.deepEqual(a.capture().value,next);assert.equal(a.state.structure_form,null);assert.throws(()=>a.applyStructureValue(next));assert.equal(a.state.value.result.businesses.length,1);a.dispose();
});
test('readonly, foreign source, competing form key and pending command cannot replace human input',()=>{
 const {a}=create(),form=draft(a.state.value);a.editStructureForm(form);const before=clone(a.state);
 a.account.role='viewer';assert.throws(()=>a.editStructureForm(null),/只读/);assert.deepEqual(a.state,before);a.account.role='admin';
 const foreign=clone(form);foreign.base_value.result.source_hash='c'.repeat(64);assert.throws(()=>a.editStructureForm(foreign),/不属于/);
 a.state.pending={kind:'review'};assert.throws(()=>a.applyStructureValue(a.state.value),/尚待核对/);assert.deepEqual(a.state.structure_form,form);a.dispose();
});
test('closed v2 working data rejects unknown fields without deleting the original saved bytes',()=>{
 const {a,storage}=create();a.editStructureForm(draft(a.state.value));const raw=storage.getItem(a.key),bad=JSON.parse(raw);bad.structure_form.fields.unrecognized='x';storage.setItem(a.key,JSON.stringify(bad));
 assert.throws(()=>a.read(a.key));assert.equal(storage.getItem(a.key),JSON.stringify(bad));storage.setItem(a.key,raw);assert.ok(a.read(a.key));a.dispose();
});
test('changing draft and choosing server conflict preserves old owner form while new owner has none',async()=>{
 const f=await fixture(),{a,storage}=create(),form=draft(a.state.value);form.fields.name='旧稿尚未加入末字Z';a.editStructureForm(form);const oldKey=a.key;
 const row={id:id(91),view_hash:'other-view',preview:{body:{selection:f.selection,model_id:id(12),note:''}},review_value:manual()};
 // Only the scoped-adopt consumer is under test; load() independently validates server DTOs.
 a.materialID=f.selection.original_ref.id;a.state.material_id=a.materialID;a.persist();
 a.adopt(row,{force:true,catalog:f.catalog});assert.equal(a.state.structure_form,null);
 assert.deepEqual(JSON.parse(storage.getItem(oldKey)).structure_form,form);
 const nextForm=draft(a.state.value);nextForm.fields.name='冲突前待核字段';a.editStructureForm(nextForm);const before=a.key;
 a.conflict={...row,revision:8,review_value:{...manual(),note:'服务器最新稿'}};a.resolveConflict(false);
 assert.equal(a.state.structure_form,null);assert.equal(a.state.value.note,'服务器最新稿');assert.deepEqual(JSON.parse(storage.getItem(before)).structure_form,nextForm);a.dispose();
});
