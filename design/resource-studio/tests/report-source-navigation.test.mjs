import test from 'node:test';
import assert from 'node:assert/strict';
import {runtimeFunctionSource} from './helpers/runtime-functions.mjs';
import {sameSetupSession} from '../src/setup-session-contract.mjs';
const actor={user_id:'A',workspace_id:'W',role:'operator',username:'operator-A'};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
function actual({name='report.docx',guardWait,saveWait}={}){
 const state={copy:[],ocr:[],errors:[],urls:[]},accountRef={current:actor},bootEpoch={current:1},sync={current:{wait:()=>saveWait?.promise||Promise.resolve()}},guard={current:()=>guardWait?.promise||Promise.resolve(true)};
 const context={materials:[{id:'source-A',name,archived:false}],account:actor,accountRef,bootEpoch,bootMounted:{current:true},sync,guard,sameSetupSession,reviewBlocked:()=>false,
  setDocumentCopyMaterial:id=>state.copy.push(id),setOCRMaterial:id=>state.ocr.push(id),setError:error=>state.errors.push(error),
  location:{search:'?view=business'},history:{replaceState:(a,b,url)=>state.urls.push(url)}};
 const fn=new Function(...Object.keys(context),'return ('+runtimeFunctionSource('openReportSource')+');')(...Object.values(context));
 return {fn,state,accountRef,bootEpoch,sync};
}
for(const phase of ['guard','save'])for(const change of ['actor','workspace','epoch','synchronizer'])test(`actual source navigation discards ${change} replacement after ${phase} wait`,async()=>{
 const wait=deferred(),a=actual(phase==='guard'?{guardWait:wait}:{saveWait:wait});const pending=a.fn('source-A');await Promise.resolve();await Promise.resolve();
 if(change==='actor')a.accountRef.current={...actor,user_id:'B',username:'operator-B'};
 if(change==='workspace')a.accountRef.current={...actor,workspace_id:'W-B'};
 if(change==='epoch')a.bootEpoch.current++;
 if(change==='synchronizer')a.sync.current={wait:async()=>{}};
 wait.resolve(true);await pending;assert.deepEqual(a.state,{copy:[],ocr:[],errors:[],urls:[]});
});
test('actual source navigation checks the originating business again after saving',async()=>{
 const wait=deferred(),a=actual({saveWait:wait});let current=true;const pending=a.fn('source-A',()=>{if(!current)throw Error('原业务已切换');});await Promise.resolve();await Promise.resolve();current=false;wait.resolve();await pending;
 assert.deepEqual(a.state.copy,[]);assert.deepEqual(a.state.ocr,[]);assert.match(a.state.errors[0],/业务已切换/);
});
test('actual current Word source opens its copy workspace without leaving business',async()=>{
 const a=actual();await a.fn('source-A');assert.deepEqual(a.state.copy,['source-A']);assert.deepEqual(a.state.ocr,[]);assert.equal(a.state.urls.length,1);assert.match(a.state.urls[0],/view=business/);assert.match(a.state.urls[0],/document_copy=source-A/);
});
test('actual current PDF source opens OCR with the business context retained',async()=>{
 const a=actual({name:'report.pdf'});await a.fn('source-A');assert.deepEqual(a.state.ocr,['source-A']);assert.deepEqual(a.state.copy,[]);assert.deepEqual(a.state.urls,[]);
});
