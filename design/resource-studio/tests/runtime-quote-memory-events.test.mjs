import test from 'node:test';
import assert from 'node:assert/strict';
import {runtimeSource as source,runtimeFunctionSource as actualFunction} from './helpers/runtime-functions.mjs';
import {setupSessionIdentity,sameSetupSession} from '../src/setup-session-contract.mjs';

const A={user_id:'60000000-0000-4000-8000-000000000001',workspace_id:'60000000-0000-4000-8000-000000000002',username:'synthetic-A',role:'operator'};
const B={...A,user_id:'60000000-0000-4000-8000-000000000003',username:'synthetic-B'};
const functions=['captureQuoteMemory','availableQuoteMemory','settleQuoteMemory','captureFullMemory','captureOCRMemory','detachSetupView','reauthenticate'].map(name=>actualFunction(name)).join('\n');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};

function fixture(){
 const refs={};for(const m of source.matchAll(/(\w+)\s*=\s*useRef\((\[\]|null|true|false|0)/g))refs[m[1]]={current:m[2]==='[]'?[]:m[2]==='true'?true:m[2]==='false'?false:m[2]==='0'?0:null};
 const state={clear:0,boot:0,hidden:0,suspended:0},working={schema:'quote-binding-memory-v1',token:'synthetic-token-A',owner:'synthetic-owner-A',generation:3,scope:{actor_id:A.user_id,workspace_id:A.workspace_id,handoff_id:'60000000-0000-4000-8000-000000000004',candidate_index:0},form:{ranges:{selection:{start:'尚未写入本机的末字🙂'}}}};
 refs.accountRef.current=A;refs.quoteCaptureWorking.current=()=>structuredClone(working);refs.guard.current=()=>false;refs.sync={current:{suspend(){state.suspended++;}}};refs.key={current:'synthetic-key'};refs.live={current:{}};refs.reviewLock={current:{error:''}};
 const deps={...refs,setFullMemoryVersion(update){state.fullMemoryVersion=typeof update==='function'?update(state.fullMemoryVersion||0):update;},setFullReading(value){state.fullReading=value;},setupSessionIdentity,sameSetupSession,captureReportMemory(){},requestOCRLeave:()=>true,clearSession(){state.clear++;},boot(){state.boot++;},configureMaterialBackend(){},pendingReview(){},busyReview(){},setAccount(v){if(v===null)state.hidden++;}};
 for(const name of ['setQuoteMemoryVersion','setOCRMemoryVersion','setReportDraft','setReportNotice','setOCRMemoryToken','setW','setMaterials','setOCRMaterial','setDocumentCopyMaterial','setMaterialRefreshing','setRecovery','setHistoryRows','setUnderstanding','setVoice','setWorkspaceInstance','setReviewStorageError','setDraftView','setReauth','setError'])deps[name]=()=>{};
 const actual=Function(...Object.keys(deps),functions+'\nreturn {captureQuoteMemory,availableQuoteMemory,settleQuoteMemory,detachSetupView,reauthenticate};')(...Object.values(deps));
 return {refs,state,working,actual};
}

test('actual voluntary reauthentication cannot discard quote adjustments after local storage fails',async()=>{
 const f=fixture();await f.actual.reauthenticate();assert.equal(f.state.clear,0);assert.equal(f.state.hidden,0);assert.equal(f.state.boot,0);assert.deepEqual(f.refs.accountRef.current,A);assert.match(f.working.form.ranges.selection.start,/末字/);
});
test('a late quote leave decision cannot detach a newer authenticated actor',async()=>{
 const f=fixture(),gate=deferred();f.refs.guard.current=()=>gate.promise;const pending=f.actual.reauthenticate();f.refs.accountRef.current=B;f.refs.bootEpoch.current++;gate.resolve(true);await pending;assert.equal(f.state.clear,0);assert.equal(f.state.hidden,0);assert.equal(f.state.boot,0);assert.deepEqual(f.refs.accountRef.current,B);
});
test('actual forced detach hides the old actor and retains only owner-scoped quote memory for explicit recovery',()=>{
 const f=fixture();f.actual.detachSetupView();assert.equal(f.state.hidden,1);assert.equal(f.state.suspended,1);assert.equal(f.refs.quoteCaptureWorking.current,null);assert.equal(f.refs.accountRef.current,null);assert.equal(f.refs.quoteMemoryDrafts.current.length,1);assert.deepEqual(f.refs.quoteMemoryDrafts.current[0],f.working);
 f.refs.accountRef.current=B;assert.deepEqual(f.actual.availableQuoteMemory(),[]);f.actual.settleQuoteMemory(f.working.token);assert.equal(f.refs.quoteMemoryDrafts.current.length,1);
 f.refs.accountRef.current=A;assert.deepEqual(f.actual.availableQuoteMemory(),[f.working]);f.actual.settleQuoteMemory(f.working.token);assert.equal(f.refs.quoteMemoryDrafts.current.length,0);
});
test('separate mount tokens preserve both unsaved quote forms and older captures cannot replace later input',()=>{
 const f=fixture();f.actual.captureQuoteMemory();f.working.generation=4;f.working.form.ranges.selection.end='更新末字';f.actual.captureQuoteMemory();f.working.generation=2;delete f.working.form.ranges.selection.end;f.actual.captureQuoteMemory();assert.equal(f.refs.quoteMemoryDrafts.current[0].generation,4);assert.equal(f.refs.quoteMemoryDrafts.current[0].form.ranges.selection.end,'更新末字');
 f.working.token='synthetic-next-mount';f.working.generation=1;f.actual.captureQuoteMemory();assert.equal(f.refs.quoteMemoryDrafts.current.length,2);
});
