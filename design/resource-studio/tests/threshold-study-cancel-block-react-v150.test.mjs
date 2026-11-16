// Actual Study component, Journal and runtime client; all HTTP below is synthetic.
// These cases prove cancellation/recovery UI contracts, not SQL, Job or browser authority.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash,webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'threshold-study-cancel-block-v150-'));
  const entry=join(temporary,'entry.mjs');
  await writeFile(entry,'export {default as Study} from '+JSON.stringify(join(project,'src/ThresholdStudyV149.jsx'))+';\nexport {ThresholdStudyJournalV149,studyScopeV149,emptyStudyFormV149} from '+JSON.stringify(join(project,'src/threshold-study-contract-v149.mjs'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
  const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
  const output=result.output.find(item=>item.type==='chunk'&&item.isEntry),path=join(temporary,'component.mjs');
  assert.ok(output,'actual component bundle exists');
  await writeFile(path,output.code);return import(pathToFileURL(path).href);
 })();
 return bundle;
}
const id=n=>'15020000-0000-4000-8000-'+String(n).padStart(12,'0');
const copy=value=>JSON.parse(JSON.stringify(value));
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-cancel-operator',role:'operator'};
const instance=id(3),record={id:id(4),version:1,extractor_id:id(5)},candidateID=id(60),evaluationID=id(70);
const rootPath='/api/local-rules/evolution',csrf='synthetic-study-cancel-csrf';
const sourceIssue='原来源已停用，历史人工理由保留，机器数值和比较结果暂不可用';
const blockedReason='当前原依据不可用于新的研究执行';
const rationale='已冻结方案的人工作业说明',limitations='只保留原比较记录，不批准生产试用';
const baseline={id:record.extractor_id,title:'原登记规则',field:'count',field_label:'数量',operator:'gt',threshold:20,unit:'株'};
const proposed={...baseline,id:id(6),title:'原冻结候选规则',threshold:18};
const training={id:id(10),version:1,root_id:id(11),observation_id:id(12),entity_id:id(13),entity_label:'原训练对象',label:'should_alert',reason:'原已提交的人工作业理由',classification:'unknown',field_label:'数量',value:null,unit:'株',eligible:false,issue:sourceIssue};
const sample={...training,id:id(20),root_id:id(21),observation_id:id(22),entity_id:id(23),entity_label:'独立评估对象',reason:'原独立样本的人工作业理由'};
const savedCandidate={schema:'threshold-study-candidate-view-v149',workspace_id:account.workspace_id,id:candidateID,version:2,status:'frozen',actor_id:account.user_id,title:'已保存的原候选方案',rationale,baseline,candidate:proposed,training:[training],freeze_hash:'c'.repeat(64),current_issue:sourceIssue,can_edit:false,can_freeze:false,eligible_for_trial:false};
function evaluation(status){return {schema:'threshold-study-evaluation-view-v149',workspace_id:account.workspace_id,id:evaluationID,version:4,status,actor_id:account.user_id,title:'已保存的原评估',candidate_id:candidateID,baseline,candidate:proposed,training:[training],samples:[sample],criteria:{max_additional_reviews:0},purpose:'mechanism_check',limitations,freeze_hash:'e'.repeat(64),job:['queued','running'].includes(status)?{id:id(80),status,attempts:status==='running'?1:0,error:null}:null,progress:{completed:0,total:2,reserved:0},result:null,error:null,current_issue:sourceIssue,can_edit:false,can_freeze:false,can_run:false,can_cancel:true,eligible_for_trial:false};}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources');}}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
function exclusiveLocks(){
 const tails=new Map();
 return {request(name,options,callback){
  assert.equal(options.mode,'exclusive');
  const previous=tails.get(name)||Promise.resolve(),result=previous.then(()=>callback({name,mode:'exclusive'})),tail=result.then(()=>{},()=>{});
  tails.set(name,tail);
  return result.finally(()=>{if(tails.get(name)===tail)tails.delete(name);});
 }};
}

async function fixture(status,run,{serverVersion=4}={}){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://threshold-study-cancel.invalid/',pretendToBeVisual:true,resources:new NoResources()});
 const calls=[],errors=[],violations=[];
 let root,activeKey,authStatus=200,current={...evaluation(status),version:serverVersion},saved=null,acceptedNonce=null,reason='';
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:exclusiveLocks()});
 const response=(value,httpStatus=200)=>new Response(JSON.stringify(value),{status:httpStatus,headers:{'Content-Type':'application/json'}});
 const active=()=>{const raw=dom.window.localStorage.getItem(activeKey);assert.ok(raw,'actual Journal persisted the original request before HTTP');return JSON.parse(raw);};
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';
  calls.push({method,path:url.pathname,search:url.search,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin,'same-origin fixture only');
   assert.equal(options.credentials,'same-origin');
   const headers=new Headers(options.headers);
   assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);
   if(url.pathname==='/api/me'){
    assert.equal(method,'GET');
    return authStatus===200?response(account):response({detail:'当前身份没有读取权限'},authStatus);
   }
   if(method==='GET'){
    if(url.pathname===rootPath+'/threshold-study-options'){
     assert.equal(url.searchParams.get('baseline_capability_id'),record.extractor_id);
     return response({detail:'原基线当前不可用于新研究'},409);
    }
    if(url.pathname===rootPath+'/threshold-candidates/'+candidateID)return response(copy(savedCandidate));
    if(url.pathname===rootPath+'/threshold-evaluations/'+evaluationID)return response(copy(current));
    if(url.pathname===rootPath+'/threshold-candidates'){
     assert.equal(url.searchParams.get('baseline_capability_id'),record.extractor_id);
     return response({schema:'threshold-study-candidate-list-v149',workspace_id:account.workspace_id,candidates:[copy(savedCandidate)],next_after:null});
    }
    if(url.pathname===rootPath+'/threshold-evaluations'){
     assert.equal(url.searchParams.get('candidate_id'),candidateID);
     return response({schema:'threshold-study-evaluation-list-v149',workspace_id:account.workspace_id,evaluations:[copy(current)],next_after:null});
    }
    if(url.pathname.startsWith(rootPath+'/threshold-study-requests/')){
     const pending=active(),nonce=url.pathname.slice((rootPath+'/threshold-study-requests/').length);
     assert.equal(nonce,pending.request_id);
     assert.equal(url.searchParams.get('expected_sha256'),pending.wire_sha256);
     assert.equal(url.searchParams.get('original_method'),'POST');
     assert.equal(url.searchParams.get('original_path'),rootPath+'/threshold-evaluations/'+evaluationID+'/cancel');
     assert.equal(pending.path,url.searchParams.get('original_path'));
     return response({schema:'threshold-study-request-state-v149',found:saved!==null,saved:copy(saved),rejected:null});
    }
   }
   if(method==='POST'&&url.pathname===rootPath+'/threshold-evaluations/'+evaluationID+'/cancel'){
    assert.equal(authStatus,200,'current identity must remain authorized');
    assert.equal(headers.get('X-CSRF-Token'),csrf);
    assert.equal(typeof options.body,'string');
    const pending=active(),body=JSON.parse(options.body);
    assert.equal(pending.exact_body,options.body,'actual immutable Journal wire is sent verbatim');
    assert.equal(pending.method,'POST');assert.equal(pending.path,url.pathname);
    assert.equal(pending.scope.actor_id,account.user_id);assert.equal(pending.scope.workspace_id,account.workspace_id);
    assert.equal(pending.scope.instance_id,instance);
    assert.equal(pending.wire_sha256,createHash('sha256').update(options.body).digest('hex'));
    assert.deepEqual(Object.keys(body).sort(),['schema','request_id','expected_version','reason'].sort());
    assert.equal(body.schema,'threshold-study-cancel-v149');assert.equal(body.request_id,pending.request_id);
    assert.equal(body.expected_version,serverVersion,'cancellation uses the latest independently read server version');assert.equal(body.reason,reason);assert.equal(saved,null,'one explicit cancellation only');
    acceptedNonce=body.request_id;
    current={...current,version:serverVersion+1,status:'cancelled',error:reason,can_cancel:false};
    saved={command_id:id(90),target_kind:'evaluation',target_id:evaluationID,version:current.version,result_hash:createHash('sha256').update(JSON.stringify(current)).digest('hex')};
    return response(copy(current));
   }
   throw Error('Unexpected study cancellation HTTP '+method+' '+url.pathname);
  }catch(error){violations.push(error.stack);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};
 const prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 dom.window.fetch=fetch;
 try{
  const actual=await component();assert.equal(actual.BundledReact,React,'same actual React runtime');
  actual.setSession(csrf,account.workspace_id,account.user_id,account.username);
  const journal=new actual.ThresholdStudyJournalV149(actual.studyScopeV149(account,instance,record));
  journal.open();activeKey=journal.active;
  const form=actual.emptyStudyFormV149();
  form.candidate={id:candidateID,version:2,capability_id:proposed.id,title:savedCandidate.title,rationale,feedback_refs:[{feedback_id:training.id,expected_version:1}]};
  form.evaluation={...form.evaluation,id:evaluationID,version:4,title:current.title,feedback_refs:[{feedback_id:sample.id,expected_version:1}],limitations,confirm_compatible_objects:true};
  journal.save(form);journal.dispose();
  const container=dom.window.document.getElementById('root');
  const settle=async()=>{for(let i=0;i<8;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});};
  const wait=async(predicate,label)=>{for(let i=0;i<80;i++){if(predicate())return;await settle();}assert.ok(predicate(),label+'; '+container.textContent.slice(0,650));};
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label);
  const click=async label=>{const node=button(label);assert.ok(node,'actual '+label+' exists');assert.equal(node.disabled,false,label+' is available');await act(async()=>node.click());await settle();};
  const render=async blocked=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Study,{record,instanceId:instance,account,Btn:Button,blockedReason:blocked}))));await settle();};
  root=createRoot(container,{onUncaughtError:error=>errors.push(error.message),onCaughtError:error=>errors.push(error.message)});
  await render('');assert.equal(calls.length,0,'closed Study never starts HTTP');
  await click('比较阈值候选');
  await wait(()=>container.querySelector('[aria-label="取消后续比较理由"]')&&container.textContent.includes(limitations)&&button('刷新候选与判断')?.disabled===false,'saved source-blocked evaluation is cold-read');
  assert.ok(container.textContent.includes(training.reason));assert.ok(container.textContent.includes(sample.reason));assert.ok(container.textContent.includes(sourceIssue));
  assert.equal(container.querySelectorAll('table').length,0,'source-blocked result remains null, without old machine result fallback');
  await render(blockedReason);
  const input=container.querySelector('[aria-label="取消后续比较理由"]');
  assert.ok(input,'original cancellation reason field remains visible');
  assert.equal(input.disabled,false,'source unavailability must not prevent protecting the original evaluation by cancellation');
  for(const label of ['保存候选方案','冻结候选方案','保存评估草稿','冻结评估样本','开始真实比较'])assert.ok(!button(label)||button(label).disabled,'fresh study write stays blocked: '+label);
  const setReason=async value=>{reason=value;await act(async()=>{input.value=value;input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();assert.equal(input.value,value,'human cancellation reason remains in the actual input');};
  const storedReason=()=>Array.from({length:dom.window.localStorage.length},(_,index)=>dom.window.localStorage.getItem(dom.window.localStorage.key(index))).filter(Boolean).some(raw=>{try{return JSON.parse(raw).form?.evaluation?.cancel_reason===reason;}catch{return false;}});
  await run({container,calls,click,wait,setReason,storedReason,button,activeKey,storage:dom.window.localStorage,setAuth:value=>{authStatus=value;},getCurrent:()=>current,getAcceptedNonce:()=>acceptedNonce});
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();
  for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}
 }
}

for(const status of ['frozen','queued'])test('V150 '+status+' saved evaluation can cancel with options409 and a same-scope execution block',async()=>{
 await fixture(status,async({container,calls,click,wait,setReason,storedReason,activeKey,storage,getCurrent,getAcceptedNonce})=>{
  await setReason('原来源停用，明确取消本次后续比较，保留原记录与人工理由');
  assert.equal(storedReason(),true,'actual Journal durably saves the cancellation reason before sending');
  await click('取消后续比较');
  await wait(()=>container.textContent.includes('已取消后续比较')&&storage.getItem(activeKey)===null,'original cancellation is independently read back and settled');
  const mutations=calls.filter(call=>call.method!=='GET');
  assert.equal(mutations.length,1,'no freeze, run, create or draft write is dispatched');
  assert.equal(mutations[0].method,'POST');assert.equal(mutations[0].path,rootPath+'/threshold-evaluations/'+evaluationID+'/cancel');
  const nonce=getAcceptedNonce(),lookups=calls.filter(call=>call.path===rootPath+'/threshold-study-requests/'+nonce);
  assert.equal(lookups.length,2,'the same original nonce is checked before POST and independently after it');
  assert.equal(storage.getItem(activeKey),null,'only a verified original result settles the active request');
  assert.ok(Array.from({length:storage.length},(_,index)=>storage.key(index)).some(key=>key.endsWith(':resolution:'+nonce)),'original cancellation resolution remains archived');
  assert.equal(getCurrent().result,null);assert.equal(getCurrent().samples[0].value,null);assert.equal(getCurrent().eligible_for_trial,false);
 });
});

test('V150 cancellation uses running server v7 while preserving the original local v4 reason and frozen criteria',async()=>{
 await fixture('running',async({container,calls,click,wait,setReason,storedReason,storage,activeKey,getCurrent})=>{
  await setReason('本机仍保留原第四版人稿，明确取消已读回的第七版后续比较');
  assert.equal(storedReason(),true);
  await click('取消后续比较');
  await wait(()=>container.textContent.includes('已取消后续比较')&&storage.getItem(activeKey)===null,'latest independently read version can cancel without rewriting old human work');
  const mutations=calls.filter(call=>call.method!=='GET');assert.equal(mutations.length,1);
  assert.equal(mutations[0].path,rootPath+'/threshold-evaluations/'+evaluationID+'/cancel');
  assert.equal(JSON.parse(mutations[0].body).expected_version,7,'only the explicit cancellation uses the refreshed version');
  assert.equal(storedReason(),true,'saved original cancellation reason survives settlement');
  assert.deepEqual(getCurrent().criteria,{max_additional_reviews:0});assert.equal(getCurrent().freeze_hash,'e'.repeat(64));
  assert.equal(getCurrent().result,null);assert.equal(getCurrent().version,8);
  const drafts=Array.from({length:storage.length},(_,index)=>storage.getItem(storage.key(index))).filter(Boolean).map(raw=>JSON.parse(raw)).filter(value=>value.schema==='threshold-study-draft-v149'&&value.form.evaluation.cancel_reason);
  assert.ok(drafts.length>0,'original human draft remains discoverable');
  for(const draft of drafts){assert.equal(draft.form.evaluation.reviews_raw,'0');assert.equal(draft.form.evaluation.limitations,limitations);assert.deepEqual(draft.form.evaluation.feedback_refs,[{feedback_id:sample.id,expected_version:1}]);}
 },{serverVersion:7});
});

for(const status of [401,403])test('V150 cancellation still requires a fresh authorized identity after source-blocked history loads: '+status,async()=>{
 await fixture('queued',async({container,calls,click,wait,setReason,storedReason,setAuth,getCurrent})=>{
  await setReason('身份变化前写下的取消说明必须保留');
  assert.equal(storedReason(),true,'reason is durably saved before the identity failure');
  const before=calls.length;setAuth(status);await click('取消后续比较');
  await wait(()=>calls.slice(before).some(call=>call.path==='/api/me')&&container.querySelector('[aria-label="取消后续比较理由"]')===null,'permission failure hides the verified old view');
  assert.equal(calls.filter(call=>call.method!=='GET').length,0,'401/403 cannot dispatch cancellation or any fresh write');
  assert.equal(getCurrent().status,'queued','denial cannot be displayed as successful cancellation');
  assert.equal(storedReason(),true,'permission failure cannot delete the saved human cancellation reason');
  assert.ok(!container.textContent.includes('已取消后续比较'));
 });
});
