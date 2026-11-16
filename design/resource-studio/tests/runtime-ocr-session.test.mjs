import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runtimeSource as source,runtimeFunctionSource as actualFunction} from './helpers/runtime-functions.mjs';
import {setupSessionIdentity,sameSetupSession} from '../src/setup-session-contract.mjs';

const A={user_id:'60000000-0000-4000-8000-000000000001',workspace_id:'60000000-0000-4000-8000-000000000002',username:'synthetic-A',role:'operator'};
const B={...A,user_id:'60000000-0000-4000-8000-000000000003',username:'synthetic-B'};

const names=[...source.matchAll(/^ (?:async )?function (\w+)\(/gm)].map(m=>m[1]).filter(n=>/OCR|Memory/i.test(n)||['boot','logout','reauthenticate','detachSetupView','reviewBlocked','pendingReview','busyReview'].includes(n));
const functions=names.map(name=>actualFunction(name)).join('\n');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};

function fixture({blockLeave=true,logout403=false,delayWait=false,delayApi=null,delayMe=null,me=B,revokeError=null,revokeReply={status:200,revoked:true},meError=null,restoreError=null}={}){
 const state={account:A,ocr:'material-A',loading:false,error:'',clearCalls:0,apiCalls:[],apiOptions:[],jsonCalls:[],jsonOptions:[],events:[],intent:null},refs={};
 for(const m of source.matchAll(/(\w+)\s*=\s*useRef\((\[\]|null|true|false|0)/g))refs[m[1]]={current:m[2]==='[]'?[]:m[2]==='true'?true:m[2]==='false'?false:m[2]==='0'?0:null};
 let working={token:'token-A',owner:'owner-A',generation:1,actor_id:A.user_id,workspace_id:A.workspace_id,material_id:'material-A',run_id:'run-A',material_name:'private material A',cache:{drafts:{'unit-A':{run_id:'run-A',regions:[{id:'r1',text:'memory-only original A'}]}},pending:{body:{request_id:'original-save-nonce'}}},baseline:{drafts:{}}};
 const capture=()=>{state.events.push('capture');return structuredClone(working);};
 const closeGuard=options=>{state.intent=options;return !blockLeave;};
 const unregister=()=>{for(const [name,ref] of Object.entries(refs))if(/ocr/i.test(name)&&/capture|working/i.test(name)&&!Array.isArray(ref.current))ref.current=null;refs.ocrCloseGuard.current=null;};
 const wait=deferred();
 const deps={...refs,account:A,ocrMaterial:'material-A',documentCopyMaterial:null,setupSessionIdentity,sameSetupSession,
  bootEpoch:refs.bootEpoch||{current:0},bootMounted:refs.bootMounted||{current:true},accountRef:refs.accountRef||{current:A},
  ocrCloseGuard:refs.ocrCloseGuard||{current:closeGuard},ocrMemoryDrafts:refs.ocrMemoryDrafts||{current:[]},
  key:{current:'scope-A'},guard:{current:()=>true},sync:{current:{suspend(){state.events.push('suspend');},wait:()=>delayWait?wait.promise:Promise.resolve()}},
  live:{current:{}},unloadDraftError:{current:false},reviewLock:{current:{pending:null,busy:false,error:''}},materialRequests:{current:0},foregroundMaterialRequests:{current:0},
  localStorage:{getItem:()=>null,setItem(){throw Error('storage unavailable');},removeItem(){}},sessionStorage:{getItem:key=>key==='ga-setup-csrf-user'?A.user_id:key==='ga-setup-csrf-workspace'?A.workspace_id:key==='ga-setup-csrf'?'synthetic-csrf-token-for-handler-only':null},
  setLoading(value){state.events.push('loading:'+value);state.loading=value;if(value)unregister();},
  setAccount(value){state.account=value;},setOCRMaterial(value){state.ocr=value;if(value===null)unregister();},setOCRMemoryToken(value){state.memoryToken=value;},setError(value){state.error=value;},
  setReauth(value){state.reauth=value;},setW(value){state.w=value;},setReviewStorageError(){},setDraftView(){},setRecovery(){},setMaterials(){},setDocumentCopyMaterial(){},setMaterialRefreshing(){},setHistoryRows(){},setUnderstanding(){},setVoice(){},setWorkspaceInstance(){},setSave(){},setMaterialReads(){},setStep(){},
  clearSession(){state.clearCalls++;},configureMaterialBackend(){},uploadRemote(){},readRemote(){},remoteURL(){},
  async api(path,options){state.apiCalls.push(path);state.apiOptions.push(options);if(delayApi)await delayApi.promise;if(logout403)throw Object.assign(Error('CSRF validation failed'),{status:403});if(revokeError)throw revokeError;return {status:revokeReply.status,async json(){return revokeReply;}};},
  async json(path,options){state.jsonCalls.push(path);state.jsonOptions.push(options);if(path==='/me'){if(delayMe)await delayMe.promise;if(meError)throw meError;return me;}if(path==='/session/restore'){if(restoreError)throw restoreError;return {...me,csrf_token:'synthetic-restored-token-not-adopted'};}if(path==='/setup/draft')return {configuration:{},revision:1};if(path.startsWith('/setup/materials'))return [];throw Error('unexpected '+path);},
  async prepareSetupSession(){},setSession(){},readDraftRecovery:()=>({local:null,candidates:[]}),fresh:()=>({}),readReviewJournal:()=>null,reviewJournalKey:()=>'',checkReviewView:x=>x,
  history:{replaceState(){}},location:{search:''},DraftSync:class{wait(){return Promise.resolve();}suspend(){}}
 };
 deps.accountRef.current=A;deps.bootMounted.current=true;deps.ocrCloseGuard.current=closeGuard;
 for(const [name,ref] of Object.entries(refs)){
  if(/ocr/i.test(name)&&/capture|working/i.test(name))ref.current=capture;
  if(/ocr/i.test(name)&&/material/i.test(name))ref.current='material-A';
 }
 const scope=new Proxy(deps,{has:(obj,k)=>k in obj||k in globalThis||/^set[A-Z]/.test(String(k)),get:(obj,k)=>k in obj?obj[k]:k in globalThis?globalThis[k]:()=>{}});
 const actual=new Function('scope','with(scope){'+functions+';return {'+names.join(',')+'};}')(scope);
 return {actual,state,refs:deps,wait,setLeaveBlocked(value){blockLeave=value;},setWorking(text){working={...working,generation:working.generation+1,cache:{...working.cache,drafts:{'unit-A':{run_id:'run-A',regions:[{id:'r1',text}]}}}};}};
}

test('actual logout is blocked by the registered unsaved OCR guard before transport or teardown',async()=>{
 const f=fixture();await f.actual.logout();assert.deepEqual(f.state.apiCalls,[]);assert.equal(f.state.clearCalls,0);assert.equal(f.state.ocr,'material-A');assert.equal(f.state.account,A);assert.ok(f.state.intent,'leave intent must be exposed');
});
test('actual reauthenticate is blocked before session clear, boot GET or memory teardown',async()=>{
 const f=fixture();await f.actual.reauthenticate();assert.equal(f.state.clearCalls,0);assert.deepEqual(f.state.jsonCalls,[]);assert.equal(f.state.ocr,'material-A');assert.equal(f.state.account,A);
});
test('actual forced CSRF loss hides old scope and captures the latest edit even when close guard would block',async()=>{
 const f=fixture({blockLeave:false,logout403:true,delayWait:true});const pending=f.actual.logout();await Promise.resolve();f.setWorking('memory-only later B');f.setLeaveBlocked(true);f.wait.resolve();await pending;
 assert.equal(f.state.account,null);assert.equal(f.state.ocr,null);assert.equal(f.state.reauth,true);assert.ok(JSON.stringify(f.refs.ocrMemoryDrafts.current).includes('memory-only later B'),'forced hide must retain latest owner text in volatile memory');
});
test('actual boot captures unsaved OCR before loading unmount clears child registrations',async()=>{
 const f=fixture();await f.actual.boot();assert.equal(f.state.account,null);assert.equal(f.state.ocr,null);assert.ok(f.state.events.indexOf('capture')>=0);assert.ok(f.state.events.indexOf('capture')<f.state.events.indexOf('loading:true'));assert.ok(JSON.stringify(f.refs.ocrMemoryDrafts.current).includes('memory-only original A'));
});
test('a delayed active-A leave confirmation cannot log out or clear a newer B scope',async()=>{
 const f=fixture();await f.actual.logout();assert.equal(typeof f.state.intent?.proceed,'function');f.refs.accountRef.current=B;f.refs.bootEpoch.current++;await f.state.intent.proceed();assert.deepEqual(f.state.apiCalls,[]);assert.equal(f.state.clearCalls,0);
});
test('volatile A drafts are absent from B and other-workspace recovery lists and cannot be settled there',()=>{
 const f=fixture();f.actual.captureOCRMemory();assert.equal(f.actual.availableOCRMemory().length,1);f.refs.accountRef.current=B;assert.deepEqual(f.actual.availableOCRMemory(),[]);f.actual.settleOCRMemory('token-A');assert.equal(f.refs.ocrMemoryDrafts.current.length,1);f.refs.accountRef.current={...A,workspace_id:'60000000-0000-4000-8000-000000000004'};assert.deepEqual(f.actual.availableOCRMemory(),[]);f.actual.settleOCRMemory('token-A');assert.equal(f.refs.ocrMemoryDrafts.current.length,1);f.refs.accountRef.current=A;assert.ok(JSON.stringify(f.actual.availableOCRMemory()).includes('memory-only original A'));
});
test('same-owner volatile recovery only opens the explicit draft after fresh identity and never writes it',async()=>{
 const f=fixture({me:A});f.actual.captureOCRMemory();const draft=f.actual.availableOCRMemory()[0];f.state.ocr=null;await f.actual.restoreOCRMemory(draft);assert.equal(f.state.memoryToken,'token-A');assert.equal(f.state.ocr,'material-A');assert.deepEqual(f.state.jsonCalls,['/me']);assert.deepEqual(f.state.apiCalls,[]);assert.equal(f.refs.ocrMemoryDrafts.current[0].cache.pending.body.request_id,'original-save-nonce');
});
test('a late same-owner recovery identity result cannot reopen A after B takes the view',async()=>{
 const delay=deferred(),f=fixture({me:A,delayMe:delay});f.actual.captureOCRMemory();const draft=f.actual.availableOCRMemory()[0],pending=f.actual.restoreOCRMemory(draft);await Promise.resolve();f.refs.accountRef.current=B;f.refs.bootEpoch.current++;f.state.ocr='material-B';delay.resolve();await pending;assert.equal(f.state.ocr,'material-B');assert.notEqual(f.state.memoryToken,'token-A');assert.deepEqual(f.state.apiCalls,[]);
});
test('in-flight A logout waiting for config cannot send or clear B after a newer scope is adopted',async()=>{
 const f=fixture({blockLeave:false,delayWait:true});const pending=f.actual.logout();await Promise.resolve();f.refs.accountRef.current=B;f.refs.bootEpoch.current++;f.state.account=B;f.state.ocr='material-B';f.refs.ocrCloseGuard.current=null;f.refs.ocrCaptureWorking.current=null;f.wait.resolve();await pending;assert.deepEqual(f.state.apiCalls,[],'old A cannot post logout after B is current');assert.equal(f.state.account,B);assert.equal(f.state.ocr,'material-B');assert.equal(f.state.clearCalls,0);
});
for(const logout403 of [false,true])test('late A logout '+(logout403?'CSRF failure':'success')+' cannot clear B adopted while HTTP was pending',async()=>{
 const response=deferred(),f=fixture({blockLeave:false,delayApi:response,logout403}),pending=f.actual.logout();for(let i=0;i<5&&!f.state.apiCalls.length;i++)await Promise.resolve();assert.deepEqual(f.state.apiCalls,['/setup/session/revoke']);f.refs.accountRef.current=B;f.refs.bootEpoch.current++;f.state.account=B;f.state.ocr='material-B';f.refs.ocrCloseGuard.current=null;f.refs.ocrCaptureWorking.current=null;response.resolve();await pending;assert.equal(f.state.account,B);assert.equal(f.state.ocr,'material-B');assert.equal(f.state.clearCalls,0);
});

const statusError=status=>Object.assign(Error('synthetic HTTP '+status),{status});
const onlyRevoke=f=>{assert.deepEqual(f.state.apiCalls,['/setup/session/revoke']);assert.equal(f.state.apiOptions[0].method,'POST');assert.equal(f.state.apiOptions[0].body,undefined);assert.equal(f.state.apiOptions[0].headers['X-Workspace-ID'],A.workspace_id);assert.equal(f.state.apiOptions[0].headers['X-CSRF-Token'],'synthetic-csrf-token-for-handler-only');};
const keptOCR=f=>assert.ok(JSON.stringify(f.refs.ocrMemoryDrafts.current).includes('memory-only original A'));
const noAdopt=f=>assert.equal(f.state.clearCalls,0,'failed revoke cannot clear a possibly newer local credential');

test('actual successful logout uses only scoped revoke and requires its exact acknowledgement',async()=>{
 const f=fixture({blockLeave:false,me:A});await f.actual.logout();onlyRevoke(f);assert.equal(f.state.account,null);assert.equal(f.state.ocr,null);assert.equal(f.state.clearCalls,1);assert.deepEqual(f.state.jsonCalls,[]);keptOCR(f);
});
test('actual revoke 401 verifies revoked cookie by two GETs without replaying POST or adopting credentials',async()=>{
 const f=fixture({blockLeave:false,revokeError:statusError(401),meError:statusError(401),restoreError:statusError(401)});await f.actual.logout();onlyRevoke(f);assert.deepEqual(f.state.jsonCalls,['/me','/session/restore']);assert.equal(f.state.jsonOptions[0].cache,'no-store');assert.equal(f.state.jsonOptions[0].headers['X-Workspace-ID'],A.workspace_id);assert.equal(f.state.jsonOptions[1].headers['X-Session-Restore'],'1');assert.equal(f.state.account,null);assert.equal(f.state.ocr,null);assert.equal(f.state.reauth,false);assert.match(f.state.error,/会话已失效/);noAdopt(f);keptOCR(f);
});
test('actual revoke 401 with still-readable A remains unconfirmed and cannot auto retry or adopt restored token',async()=>{
 const f=fixture({blockLeave:false,revokeError:statusError(401),me:A});await f.actual.logout();onlyRevoke(f);assert.deepEqual(f.state.jsonCalls,['/me','/session/restore']);assert.equal(f.state.account,null);assert.equal(f.state.reauth,true);assert.match(f.state.error,/尚未确认/);noAdopt(f);keptOCR(f);
});
for(const error of [Error('lost reply'),statusError(500)])test('unknown revoke '+(error.status||'network')+' hides old scope and cannot declare success or fallback',async()=>{
 const f=fixture({blockLeave:false,revokeError:error,meError:statusError(503),restoreError:statusError(503)});await f.actual.logout();onlyRevoke(f);assert.deepEqual(f.state.jsonCalls,['/me','/session/restore']);assert.equal(f.state.account,null);assert.equal(f.state.reauth,true);assert.match(f.state.error,/尚未确认/);noAdopt(f);keptOCR(f);
});
for(const status of [403,404])test('actual revoke HTTP '+status+' does not fake successful logout or fall back',async()=>{
 const f=fixture({blockLeave:false,revokeError:statusError(status),me:A});await f.actual.logout();onlyRevoke(f);assert.deepEqual(f.state.jsonCalls,[]);assert.equal(f.state.account,null);assert.equal(f.state.ocr,null);assert.equal(f.state.reauth,true);assert.match(f.state.error,status===404?/安全退出.*不可用/:/尚未确认/);noAdopt(f);keptOCR(f);
});
for(const reply of [{status:200,revoked:false},{status:204,revoked:true},{status:200,status_text:'signed_out'}])test('nonmatching revoke acknowledgement '+JSON.stringify(reply)+' is unknown rather than success',async()=>{
 const f=fixture({blockLeave:false,revokeReply:reply,me:A});await f.actual.logout();onlyRevoke(f);assert.equal(f.state.account,null);assert.equal(f.state.reauth,true);assert.match(f.state.error,/尚未确认/);noAdopt(f);keptOCR(f);
});
test('late 401 verification result cannot change B after old A is hidden',async()=>{
 const delay=deferred(),f=fixture({blockLeave:false,revokeError:statusError(401),meError:statusError(401),restoreError:statusError(401),delayMe:delay});const pending=f.actual.logout();for(let i=0;i<12&&!f.state.jsonCalls.length;i++)await Promise.resolve();assert.deepEqual(f.state.jsonCalls,['/me']);assert.equal(f.state.account,null);f.refs.accountRef.current=B;f.refs.bootEpoch.current++;f.state.account=B;f.state.ocr='material-B';f.state.error='B message';f.state.reauth=false;delay.resolve();await pending;onlyRevoke(f);assert.deepEqual(f.state.jsonCalls,['/me']);assert.equal(f.state.account,B);assert.equal(f.state.ocr,'material-B');assert.equal(f.state.error,'B message');assert.equal(f.state.reauth,false);noAdopt(f);
});
test('changed scoped CSRF during config wait hides A without sending a revoke with B credentials',async()=>{
 const f=fixture({blockLeave:false,delayWait:true});const pending=f.actual.logout();await Promise.resolve();f.refs.sessionStorage.getItem=()=>null;f.wait.resolve();await pending;assert.deepEqual(f.state.apiCalls,[]);assert.equal(f.state.account,null);assert.equal(f.state.reauth,true);keptOCR(f);noAdopt(f);
});

test('actual wrapper sends captured old csrf once even when module defaults belong to same actor session2',async()=>{
 const f=fixture({blockLeave:false,revokeError:statusError(403)});await f.actual.logout();onlyRevoke(f);
 const wrapper=await readFile(new URL('../src/runtime-api.js',import.meta.url),'utf8'),begin=wrapper.indexOf('export async function api('),end=wrapper.indexOf('export async function json(',begin),body=wrapper.slice(begin,end).replace('export async function','async function');let sent;
 const actualApi=new Function('csrf','workspace','fetch',body+';return api;')('synthetic-new-session2-default','different-module-default-workspace',async(path,options)=>{sent={path,options};return {ok:true};});
 await actualApi('/setup/session/revoke',f.state.apiOptions[0]);assert.equal(sent.path,'/api/setup/session/revoke');assert.equal(sent.options.credentials,'same-origin');assert.equal(sent.options.headers['X-CSRF-Token'],'synthetic-csrf-token-for-handler-only');assert.equal(sent.options.headers['X-Workspace-ID'],A.workspace_id);assert.deepEqual(Object.keys(sent.options.headers).filter(k=>k.toLowerCase()==='x-csrf-token'),['X-CSRF-Token']);assert.deepEqual(Object.keys(sent.options.headers).filter(k=>k.toLowerCase()==='x-workspace-id'),['X-Workspace-ID']);assert.equal(f.state.account,null);assert.equal(f.state.clearCalls,0);
});
for(const logout403 of [false,true])test('late same-actor session1 '+(logout403?'403':'success')+' cannot clear adopted same-actor session2 epoch',async()=>{
 const response=deferred(),f=fixture({blockLeave:false,delayApi:response,logout403}),pending=f.actual.logout();for(let i=0;i<8&&!f.state.apiCalls.length;i++)await Promise.resolve();onlyRevoke(f);f.refs.bootEpoch.current++;f.refs.accountRef.current=A;f.state.account=A;f.state.ocr='material-A-session2';f.state.error='session2 message';const prior=f.refs.sessionStorage.getItem;f.refs.sessionStorage.getItem=key=>key==='ga-setup-csrf'?'synthetic-new-session2-default':prior(key);response.resolve();await pending;assert.equal(f.state.account,A);assert.equal(f.state.ocr,'material-A-session2');assert.equal(f.state.error,'session2 message');assert.equal(f.state.clearCalls,0);
});

test('in-flight 404 from an untrusted old service hides A after cookie tags change without claiming revocation',async()=>{
 const response=deferred(),f=fixture({blockLeave:false,delayApi:response,revokeError:statusError(404)}),pending=f.actual.logout();for(let i=0;i<8&&!f.state.apiCalls.length;i++)await Promise.resolve();onlyRevoke(f);f.setWorking('memory-only latest 404 edit');f.refs.sessionStorage.getItem=key=>key==='ga-setup-csrf-user'?B.user_id:key==='ga-setup-csrf-workspace'?B.workspace_id:key==='ga-setup-csrf'?'synthetic-session2-cookie-correspondence':null;response.resolve();await pending;assert.equal(f.state.account,null);assert.equal(f.state.ocr,null);assert.equal(f.state.reauth,true);assert.match(f.state.error,/安全退出.*不可用/);assert.match(f.state.error,/尚未确认/);assert.deepEqual(f.state.jsonCalls,[]);assert.equal(f.state.clearCalls,0);assert.ok(JSON.stringify(f.refs.ocrMemoryDrafts.current).includes('memory-only latest 404 edit'));onlyRevoke(f);
});
