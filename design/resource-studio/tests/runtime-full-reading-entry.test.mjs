import test from 'node:test';
import assert from 'node:assert/strict';
import {runtimeFunctionSource as handler} from './helpers/runtime-functions.mjs';
import {setupSessionIdentity,sameSetupSession} from '../src/setup-session-contract.mjs';

// Execute the actual entry handlers with controlled IO promises. This proves
// stale-entry fencing, not rendering, model quality or browser persistence.
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
function setup({holdVerify=false}={}){
 const user={user_id:'10000000-0000-4000-8000-000000000001',username:'synthetic-entry-owner',workspace_id:'10000000-0000-4000-8000-000000000002',role:'operator'},network=deferred(),validation=deferred(),enteredVerify=deferred(),opened=[],errors=[],guard=()=>false;
 const capture=()=>({schema:'report-full-reading-working-v1',owner:'synthetic-initial-owner',generation:0,scope:{actor_id:user.user_id,workspace_id:user.workspace_id},form:{note:'未能保存的末字'},lastWord:'未能保存的末字'});
 const deps={accountRef:{current:user},bootEpoch:{current:1},fullOpenEpoch:{current:0},fullDialogEpoch:{current:0},materials:[{id:'A'},{id:'B'}],fullReading:null,fullMemoryDrafts:{current:[]},setFullMemoryVersion(){},fullCloseGuard:{current:guard},fullCaptureWorking:{current:capture},setupSessionIdentity,sameSetupSession,setFullReading:value=>opened.push(value),setError:e=>errors.push(e),json:()=>network.promise,verifyFullDraft:async()=>{enteredVerify.resolve();if(holdVerify)await validation.promise;}};
 const make=new Function('deps','const {accountRef,bootEpoch,fullOpenEpoch,fullDialogEpoch,materials,fullMemoryDrafts,setFullMemoryVersion,fullCloseGuard,fullCaptureWorking,setupSessionIdentity,sameSetupSession,setError,json,verifyFullDraft}=deps;let fullReading=deps.fullReading;function setFullReading(value){fullReading=value;deps.fullReading=value;deps.setFullReading(value);}\n'+['reportEntryCurrent','fullEntryCurrent','openFullReading','openFullReadingRecord','closeFullReading','captureFullMemory','availableFullMemory'].map(name=>handler(name)).join('\n')+';return {openFullReading,openFullReadingRecord,closeFullReading,captureFullMemory,availableFullMemory};');
 // A real modal clears the previous registrations on open, then its newly
 // mounted panel registers its own guard/capture. Keep that lifecycle explicit.
 const registerCurrentDialog=()=>{deps.fullCloseGuard.current=guard;deps.fullCaptureWorking.current=capture;};
 return {deps,network,validation,enteredVerify,opened,errors,guard,capture,registerCurrentDialog,...make(deps)};
}
const row={preview:{body:{material_refs:[{id:'B'}]}}};
test('late original-record GET cannot replace a newly opened draft or clear unsaved capture/guard',async()=>{
 const s=setup(),pending=s.openFullReadingRecord('recordB');s.openFullReading('A');s.registerCurrentDialog();s.network.resolve(row);await pending;
 assert.deepEqual(s.opened.map(x=>x.material.id),['A']);assert.equal(s.deps.fullCloseGuard.current,s.guard);assert.equal(s.deps.fullCaptureWorking.current,s.capture);assert.equal(s.deps.fullCaptureWorking.current().lastWord,'未能保存的末字');assert.deepEqual(s.errors,[]);
});
test('late verification also cannot replace newer input, and a later close invalidates pending entry',async()=>{
 const s=setup({holdVerify:true}),pending=s.openFullReadingRecord('recordB');s.network.resolve(row);await s.enteredVerify.promise;s.openFullReading('A');s.registerCurrentDialog();s.validation.resolve();await pending;assert.deepEqual(s.opened.map(x=>x.material.id),['A']);
 const t=setup();t.openFullReading('A');t.registerCurrentDialog();const openedEntry=t.deps.fullReading;t.opened.length=0;const later=t.openFullReadingRecord('recordB');t.closeFullReading(true,openedEntry);t.network.resolve(row);await later;assert.deepEqual(t.opened,[null]);
});
test('current successful original-record reply opens its verified material once',async()=>{
 const s=setup(),pending=s.openFullReadingRecord('recordB');s.network.resolve(row);await pending;assert.equal(s.opened.length,1);assert.equal(s.opened[0].material.id,'B');assert.equal(s.opened[0].draftID,'recordB');
});
test('actual runtime memory bridge retains latest unsaved work and hides it from another actor/workspace',()=>{
 const s=setup(),actor=s.deps.accountRef.current,work={owner:'synthetic-owner',generation:2,scope:{actor_id:actor.user_id,workspace_id:actor.workspace_id},form:{note:'未落盘末字'}};
 s.deps.fullCaptureWorking.current=()=>structuredClone(work);s.captureFullMemory();work.generation=1;work.form.note='迟到旧输入';s.captureFullMemory();assert.equal(s.availableFullMemory()[0].form.note,'未落盘末字');
 s.deps.accountRef.current={...actor,user_id:'another-actor'};assert.deepEqual(s.availableFullMemory(),[]);s.deps.accountRef.current={...actor,workspace_id:'another-workspace'};assert.deepEqual(s.availableFullMemory(),[]);s.deps.accountRef.current=actor;assert.equal(s.availableFullMemory().length,1);
});
