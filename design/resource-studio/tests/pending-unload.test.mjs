import {savedConfigurationWire} from '../src/configuration-numeric-wire.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';
import {reviewJournalKey,saveReviewRequest,readReviewJournal,checkReviewView} from '../src/automation-review-contract.mjs';
import {shouldPreventSetupUnload} from '../src/setup-unload-contract.mjs';
import {setupSessionIdentity,sameSetupSession} from '../src/setup-session-contract.mjs';
import {withoutSecrets} from '../src/catalog.js';
import {runtimeFunctionSource} from './helpers/runtime-functions.mjs';
const account={workspace_id:'w',username:'human',user_id:'user-human',role:'operator'},draftKey='ga-setup:human:w';
const pending={body:{revision:17,business_id:'business_0123456789abcdef',request_id:'11111111-2222-4333-8444-555555555555'},business_name:'资料补核',created_at:'original'};
const storage=()=>{const values=new Map();return {values,getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};
const source=await readFile(new URL('../src/RuntimeSetup.jsx',import.meta.url),'utf8');
const listener=source.match(/const fn=(e=>\{[^\n]+?\});window\.addEventListener\('beforeunload'/)[1];
async function actualListener(options={}){
 const store=options.storage||storage();if(!options.storage)saveReviewRequest(store,reviewJournalKey(account),pending);
 const sync={current:{key:draftKey,busy:false,next:null,error:null,...options.sync}},reviewLock={current:{pending,...options.review}},accountRef={current:options.account||account},key={current:options.key||draftKey},unloadExtras={current:{voice:false,modal:false,importing:false,...options.extras}},unloadDraftError={current:!!options.stageFailed};
 const contract=await import('../src/setup-unload-contract.mjs').catch(()=>({}));
 const handler=new Function('sync','reviewLock','accountRef','key','unloadExtras','unloadDraftError','location','localStorage','shouldPreventSetupUnload','reviewBlocked','return ('+listener+');')(sync,reviewLock,accountRef,key,unloadExtras,unloadDraftError,{search:options.search||''},store,contract.shouldPreventSetupUnload,()=>!!(reviewLock.current.pending||reviewLock.current.busy||reviewLock.current.error));
 const event={prevented:false,preventDefault(){this.prevented=true;}};handler(event);return {event,store};
}
test('actual unload handler permits idle current-scope durable unknown original request without deleting it',async()=>{const {event,store}=await actualListener();assert.equal(event.prevented,false);assert.deepEqual(readReviewJournal(store,reviewJournalKey(account)),pending);});
test('actual unload handler permits POST waiting only when original request is already durable and draft idle',async()=>{const {event,store}=await actualListener({review:{busy:true}});assert.equal(event.prevented,false);assert.deepEqual(readReviewJournal(store,reviewJournalKey(account)),pending);});
test('actual unload handler still protects configuration busy, next or error',async()=>{for(const sync of [{busy:true},{next:{value:'human'}},{error:Error('failed')}])assert.equal((await actualListener({sync})).event.prevented,true);});
test('missing, corrupt, incomplete or different persisted request cannot permit unload',async()=>{
 for(const raw of [null,'{broken',JSON.stringify({...pending,body:{...pending.body,request_id:'invalid'}}),JSON.stringify({...pending,body:{...pending.body,revision:'17'}}),JSON.stringify({...pending,body:{...pending.body,unexpected:true}}),JSON.stringify({...pending,business_name:'different'}),JSON.stringify({...pending,created_at:'changed'})]){
  const store=storage();if(raw!==null)store.setItem(reviewJournalKey(account),raw);assert.equal((await actualListener({storage:store})).event.prevented,true);assert.equal(store.getItem(reviewJournalKey(account)),raw);
 }
});
test('whole request equality permits JSON key reordering while preserving complete original bytes',async()=>{
 const store=storage(),raw=JSON.stringify({created_at:pending.created_at,business_name:pending.business_name,body:{request_id:pending.body.request_id,business_id:pending.body.business_id,revision:17}});store.setItem(reviewJournalKey(account),raw);assert.equal((await actualListener({storage:store})).event.prevented,false);assert.equal(store.getItem(reviewJournalKey(account)),raw);
});
test('changed principal, draft key, missing identity or suspended draft keeps unload protection',async()=>{
 for(const options of [{account:{...account,workspace_id:'other'}},{account:{...account,username:'other'}},{account:{...account,user_id:''}},{account:{...account,role:'unknown'}},{key:'old-scope'},{sync:{key:'old-scope'}},{sync:{suspended:true}}])assert.equal((await actualListener(options)).event.prevented,true);
 assert.equal(shouldPreventSetupUnload({draftSync:null,draftKey,account,review:{pending},storage:storage()}),true);
});
test('journal read failure, review storage error and busy without a durable request remain protected',async()=>{
 assert.equal((await actualListener({storage:{getItem(){throw Error('storage denied');}}})).event.prevented,true);
 for(const review of [{error:'quota or corruption'},{pending:null,busy:true}])assert.equal((await actualListener({review})).event.prevented,true);
});
test('open voice dialog conservatively protects its recording or unsaved file even with durable request',async()=>{const {event,store}=await actualListener({extras:{voice:true}});assert.equal(event.prevented,true);assert.deepEqual(readReviewJournal(store,reviewJournalKey(account)),pending);});
test('foreign durable confirmation cannot permit unload while unverified modal, business editor or import holds input',async()=>{
 for(const options of [{extras:{modal:true}},{extras:{importing:true}},{search:'?view=automation&business_list_edit=1'}])assert.equal((await actualListener(options)).event.prevented,true);
});
test('actual commit quota failure remains a leave risk until another input is successfully staged',async()=>{
 const reviewLock={current:{pending:null,error:''}},unloadDraftError={current:false},live={current:{}},sync={current:{save(){throw Error('quota');}}};let error='';
 const commit=new Function('recovery','reviewLock','accountRef','sync','unloadDraftError','withoutSecrets','live','setW','setError',source.match(/function commit\(next\)\{[^\n]+\}/)[0]+';return commit;')(null,reviewLock,{current:account},sync,unloadDraftError,withoutSecrets,live,()=>{},value=>error=value);
 assert.equal(commit({instanceProfile:{goal:'未落盘人稿'}}),false);assert.equal(error,'quota');assert.equal(unloadDraftError.current,true);assert.equal((await actualListener({stageFailed:unloadDraftError.current})).event.prevented,true);
 sync.current.save=()=>{};assert.equal(commit({instanceProfile:{goal:'已重新保存人稿'}}),true);assert.equal(unloadDraftError.current,false);
});
test('idle without a pending request needs no leave warning',async()=>{assert.equal((await actualListener({review:{pending:null,busy:false,error:''}})).event.prevented,false);});
test('allowing unload does not unlock actual commit or go while original confirmation is unknown',async()=>{
 const reviewLock={current:{pending,busy:false,error:''}},reviewBlocked=new Function('reviewLock',source.match(/function reviewBlocked\(\)\{[^\n]+\}/)[0]+';return reviewBlocked;')(reviewLock);let writes=0,steps=0,error='';
 const commit=new Function('recovery','reviewLock','accountRef','sync','unloadDraftError',source.match(/function commit\(next\)\{[^\n]+\}/)[0]+';return commit;')(null,reviewLock,{current:account},{current:{save(){writes++;}}},{current:false});assert.equal(commit({human:'edit'}),false);
 const go=new Function('reviewBlocked','guard','sync','setStep','history','setError',source.match(/async function go\(next\)\{[^\n]+\}/)[0]+';return go;')(reviewBlocked,{current:null},{current:{wait(){writes++;}}},()=>steps++,{pushState(){steps++;}},value=>error=value);await go(4);assert.equal(writes,0);assert.equal(steps,0);assert.match(error,/核对原自动化确认请求/);
});
test('actual fresh boot restores same journal to Step4 without any POST, new UUID or journal mutation',async()=>{
 const store=storage(),journalKey=reviewJournalKey(account);saveReviewRequest(store,journalKey,pending);store.setItem('foreign-human-outbox','preserve');const bytes=store.getItem(journalKey),calls=[],state={},noop=()=>{},reviewLock={current:{}},guard={current:()=>false};
 const record={revision:17,configuration:{businesses:[pending.business_name],materials:[]},automation_review:{version:1,businesses:[{business_id:pending.body.business_id,business_name:pending.business_name,status:'needs_review',reason:'尚待核对',flow_hash:'a'.repeat(64),policy_hash:'b'.repeat(64)}]}};
 const dependencies={bootEpoch:{current:0},bootMounted:{current:true},setLoading:noop,setError:value=>state.error=value,json:async(path,options)=>{assert.ok(!options?.method||options.method==='GET');calls.push(path);if(path==='/me')return account;if(path==='/setup/draft')return record;if(path==='/setup/materials?limit=500')return [];throw Error('unexpected '+path);},sessionStorage:store,setupSessionIdentity,sameSetupSession,accountRef:{current:null},prepareSetupSession:async()=>{},setSession:noop,key:{current:null},readDraftRecovery:()=>({local:null,candidates:[]}),localStorage:store,fresh:value=>value,readReviewJournal,reviewJournalKey,pendingReview:value=>{reviewLock.current.pending=value;state.pending=value;},busyReview:value=>reviewLock.current.busy=value,reviewLock,setReviewStorageError:noop,setDraftView:noop,checkReviewView,guard,setStep:value=>state.step=value,history:{replaceState:(_a,_b,value)=>state.url=value},configureMaterialBackend:noop,uploadRemote:noop,readRemote:noop,remoteURL:noop,sync:{current:null},DraftSync:class{constructor(_s,k,r){this.key=k;this.revision=r;}},setSave:noop,setMaterials:noop,setMaterialReads:noop,setAccount:value=>state.account=value,setW:value=>state.config=value,live:{current:null},setRecovery:noop,setReauth:noop,detachSetupView(){throw Error('unexpected detach');}};
 Object.assign(dependencies,{readRuntimeDraft:()=>dependencies.json('/setup/draft'),savedConfigurationWire,fullOpenEpoch:{current:0},fullCloseGuard:{current:null},fullCaptureWorking:{current:null},fullMemoryDrafts:{current:[]},setFullReading:value=>state.fullReading=value,setFullMemoryVersion:update=>state.fullMemoryVersion=typeof update==='function'?update(state.fullMemoryVersion||0):update,quoteCaptureWorking:{current:null},quoteMemoryDrafts:{current:[]},setQuoteMemoryVersion:()=>{},reportCaptureWorking:{current:null},reportMemoryDrafts:{current:[]},setReportMemoryVersion:()=>{},ocrCaptureWorking:{current:null},ocrMemoryDrafts:{current:[]},setOCRMaterial:value=>state.ocrMaterial=value,setOCRMemoryVersion:update=>state.ocrMemoryVersion=typeof update==='function'?update(state.ocrMemoryVersion||0):update});
 const block=runtimeFunctionSource('boot',source),captureBlock=runtimeFunctionSource('captureQuoteMemory',source)+';'+runtimeFunctionSource('captureReportMemory',source)+';'+runtimeFunctionSource('captureFullMemory',source)+';'+runtimeFunctionSource('captureOCRMemory',source),boot=new Function(...Object.keys(dependencies),captureBlock+';return '+block)(...Object.values(dependencies));await boot();
 assert.equal(state.error,'');assert.equal(state.step,3);assert.equal(new URLSearchParams(state.url.slice(1)).get('view'),'automation');assert.equal(new URLSearchParams(state.url.slice(1)).get('business'),pending.business_name);assert.deepEqual(state.pending,pending);assert.equal(guard.current,null);assert.equal(store.getItem(journalKey),bytes);assert.equal(store.getItem('foreign-human-outbox'),'preserve');assert.deepEqual(calls,['/me','/setup/draft','/setup/materials?limit=500','/me']);
});
test('independent actual voice unload handler still protects recording, permission and busy',async()=>{
 const voice=await readFile(new URL('../src/SetupVoiceDialog.jsx',import.meta.url),'utf8'),text=voice.match(/const handler=(e=>\{[^\n]+?\});window\.addEventListener\('beforeunload'/)[1];
 for(const states of [[true,false,false],[false,true,false],[false,false,true]]){const handler=new Function('recording','permission','busy','return ('+text+');')(...states),event={prevented:false,preventDefault(){this.prevented=true;}};handler(event);assert.equal(event.prevented,true);}
});
