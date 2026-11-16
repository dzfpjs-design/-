// Actual component, Journal and runtime client; HTTP is an explicit synthetic fixture.
// This is a cold-entry UI contract test, not browser, SQL or Worker evidence.
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

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){if(!bundle)bundle=(async()=>{
 temporary=await mkdtemp(join(tmpdir(),'threshold-study-cold-cancel-v150-'));const entry=join(temporary,'entry.mjs');
 await writeFile(entry,'export {default as Study} from '+JSON.stringify(join(project,'src/ThresholdStudyV149.jsx'))+';\nexport {ThresholdStudyJournalV149,studyScopeV149,emptyStudyFormV149} from '+JSON.stringify(join(project,'src/threshold-study-contract-v149.mjs'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
 const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
 const output=result.output.find(item=>item.type==='chunk'&&item.isEntry),path=join(temporary,'component.mjs');assert.ok(output,'actual component bundle exists');await writeFile(path,output.code);return import(pathToFileURL(path).href);
})();return bundle;}
const id=n=>'15030000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value));
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-cold-history-operator',role:'operator'},instance=id(3),record={id:id(4),version:1,extractor_id:id(5)},candidateID=id(60),evaluationID=id(70);
const rootPath='/api/local-rules/evolution',csrf='synthetic-cold-history-csrf',blockedReason='原来源已停用，当前不能开始新的研究执行';
const issue='原处理依据当前不可用，历史人工理由保留，机器数值和比较结果不展示',rationale='原冻结方案的调整说明',limitations='原评估的适用范围限制',cancelReason='重新打开原记录后，明确取消这一次尚未完成的比较';
const baseline={id:record.extractor_id,title:'原登记规则',field:'count',field_label:'数量',operator:'gt',threshold:20,unit:'株'},proposed={...baseline,id:id(6),title:'原冻结候选规则',threshold:18};
const training={id:id(10),version:1,root_id:id(11),observation_id:id(12),entity_id:id(13),entity_label:'原训练对象',label:'should_alert',reason:'原已提交的人工作业理由',classification:'unknown',field_label:'数量',value:null,unit:'株',eligible:false,issue};
const sample={...training,id:id(20),root_id:id(21),observation_id:id(22),entity_id:id(23),entity_label:'独立评估对象',reason:'原独立样本的核查理由'};
const candidate={schema:'threshold-study-candidate-view-v149',workspace_id:account.workspace_id,id:candidateID,version:2,status:'frozen',actor_id:account.user_id,title:'已有的原候选方案',rationale,baseline,candidate:proposed,training:[training],freeze_hash:'c'.repeat(64),current_issue:issue,can_edit:false,can_freeze:false,eligible_for_trial:false};
const evaluation={schema:'threshold-study-evaluation-view-v149',workspace_id:account.workspace_id,id:evaluationID,version:4,status:'queued',actor_id:account.user_id,title:'已有的原评估',candidate_id:candidateID,baseline,candidate:proposed,training:[training],samples:[sample],criteria:{max_additional_reviews:0},purpose:'mechanism_check',limitations,freeze_hash:'e'.repeat(64),job:{id:id(80),status:'queued',attempts:0,error:null},progress:{completed:0,total:2,reserved:0},result:null,error:null,current_issue:issue,can_edit:false,can_freeze:false,can_run:false,can_cancel:true,eligible_for_trial:false};
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources');}}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const result=(tails.get(name)||Promise.resolve()).then(()=>callback({name,mode:'exclusive'})),tail=result.then(()=>{},()=>{});tails.set(name,tail);return result.finally(()=>{if(tails.get(name)===tail)tails.delete(name);});}};}

test('V150 cold blocked scope can explicitly view saved comparison history and cancel only its original evaluation',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://threshold-study-cold-cancel.invalid/',pretendToBeVisual:true,resources:new NoResources()}),calls=[],errors=[],violations=[];
 let root,activeKey,current=copy(evaluation),saved=null,nonce=null;
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const original=()=>{const raw=dom.window.localStorage.getItem(activeKey);assert.ok(raw,'actual Journal saved the original request before transport');return JSON.parse(raw);};
 const fetch=async(raw,options={})=>{const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname,search:url.search,body:options.body});try{
  assert.equal(url.origin,dom.window.location.origin);assert.equal(options.credentials,'same-origin');const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);
  if(method==='GET'){
   if(url.pathname==='/api/me')return response(account);
   if(url.pathname===rootPath+'/threshold-study-options'){assert.equal(url.searchParams.get('baseline_capability_id'),record.extractor_id);return response({detail:'原基线当前不可用于新研究'},409);}
   if(url.pathname===rootPath+'/threshold-candidates/'+candidateID)return response(copy(candidate));
   if(url.pathname===rootPath+'/threshold-evaluations/'+evaluationID)return response(copy(current));
   if(url.pathname===rootPath+'/threshold-candidates'){assert.equal(url.searchParams.get('baseline_capability_id'),record.extractor_id);return response({schema:'threshold-study-candidate-list-v149',workspace_id:account.workspace_id,candidates:[copy(candidate)],next_after:null});}
   if(url.pathname===rootPath+'/threshold-evaluations'){assert.equal(url.searchParams.get('candidate_id'),candidateID);return response({schema:'threshold-study-evaluation-list-v149',workspace_id:account.workspace_id,evaluations:[copy(current)],next_after:null});}
   if(url.pathname.startsWith(rootPath+'/threshold-study-requests/')){const sent=original();assert.equal(url.pathname,rootPath+'/threshold-study-requests/'+sent.request_id);assert.equal(url.searchParams.get('expected_sha256'),sent.wire_sha256);assert.equal(url.searchParams.get('original_method'),'POST');assert.equal(url.searchParams.get('original_path'),rootPath+'/threshold-evaluations/'+evaluationID+'/cancel');assert.equal(sent.path,url.searchParams.get('original_path'));return response({schema:'threshold-study-request-state-v149',found:saved!==null,saved:copy(saved),rejected:null});}
  }
  if(method==='POST'&&url.pathname===rootPath+'/threshold-evaluations/'+evaluationID+'/cancel'){
   assert.equal(headers.get('X-CSRF-Token'),csrf);const sent=original(),body=JSON.parse(options.body);assert.equal(options.body,sent.exact_body,'only the durable original wire is transmitted');assert.equal(sent.path,url.pathname);assert.equal(sent.method,'POST');assert.equal(sent.scope.actor_id,account.user_id);assert.equal(sent.scope.workspace_id,account.workspace_id);assert.equal(sent.scope.instance_id,instance);assert.equal(sent.wire_sha256,createHash('sha256').update(options.body).digest('hex'));
   assert.deepEqual(Object.keys(body).sort(),['schema','request_id','expected_version','reason'].sort());assert.equal(body.schema,'threshold-study-cancel-v149');assert.equal(body.request_id,sent.request_id);assert.equal(body.expected_version,4);assert.equal(body.reason,cancelReason);assert.equal(saved,null,'one explicit original cancellation');nonce=body.request_id;
   current={...current,version:5,status:'cancelled',error:cancelReason,can_cancel:false};saved={command_id:id(90),target_kind:'evaluation',target_id:evaluationID,version:5,result_hash:createHash('sha256').update(JSON.stringify(current)).digest('hex')};return response(copy(current));
  }
  throw Error('Unexpected cold recovery HTTP '+method+' '+url.pathname);
 }catch(cause){violations.push(cause.stack);throw cause;}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 try{
  const actual=await component();assert.equal(actual.BundledReact,React);actual.setSession(csrf,account.workspace_id,account.user_id,account.username);
  const journal=new actual.ThresholdStudyJournalV149(actual.studyScopeV149(account,instance,record));journal.open();activeKey=journal.active;
  const form=actual.emptyStudyFormV149();form.candidate={id:candidateID,version:2,capability_id:proposed.id,title:candidate.title,rationale,feedback_refs:[{feedback_id:training.id,expected_version:1}]};form.evaluation={...form.evaluation,id:evaluationID,version:4,title:evaluation.title,feedback_refs:[{feedback_id:sample.id,expected_version:1}],limitations,confirm_compatible_objects:true};journal.save(form);journal.dispose();
  const container=dom.window.document.getElementById('root'),settle=async()=>{for(let i=0;i<8;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});},button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label);
  const wait=async(predicate,label)=>{for(let i=0;i<80;i++){if(predicate())return;await settle();}assert.ok(predicate(),label+'; '+container.textContent.slice(0,650));};
  const click=async label=>{const node=button(label);assert.ok(node,'actual '+label+' entry exists');assert.equal(node.disabled,false,label+' remains available');await act(async()=>node.click());await settle();};
  const freshDisabled=()=>{for(const label of ['比较阈值候选','保存候选方案','冻结候选方案','保存评估草稿','冻结评估样本','开始真实比较'])assert.ok(!button(label)||button(label).disabled,'fresh operation remains unavailable: '+label);};
  root=createRoot(container,{onUncaughtError:cause=>errors.push(cause.message),onCaughtError:cause=>errors.push(cause.message)});
  await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Study,{record,instanceId:instance,account,Btn:Button,blockedReason}))));await settle();
  assert.equal(calls.length,0,'closed blocked scope never auto-fetches or auto-submits');freshDisabled();
  await click('查看已保存的比较记录');
  await wait(()=>container.querySelector('[aria-label="取消后续比较理由"]')&&button('刷新候选与判断')?.disabled===false,'explicit cold entry reads the saved original evaluation');
  assert.ok(calls.length>0);assert.ok(calls.every(call=>call.method==='GET'),'entering history is GET-only');assert.ok(calls.some(call=>call.path===rootPath+'/threshold-study-options'));
  assert.ok(container.textContent.includes(rationale));assert.ok(container.textContent.includes(training.reason));assert.ok(container.textContent.includes(sample.reason));assert.ok(container.textContent.includes(issue));assert.equal(container.querySelectorAll('table').length,0,'null machine result has no history fallback');freshDisabled();
  const input=container.querySelector('[aria-label="取消后续比较理由"]');assert.ok(input);assert.equal(input.disabled,false,'the cold history entry permits a new human cancellation reason');
  await act(async()=>{input.value=cancelReason;input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();
  const draftContainsReason=()=>Array.from({length:dom.window.localStorage.length},(_,index)=>dom.window.localStorage.getItem(dom.window.localStorage.key(index))).filter(Boolean).some(raw=>{try{return JSON.parse(raw).form?.evaluation?.cancel_reason===cancelReason;}catch{return false;}});
  assert.equal(draftContainsReason(),true,'actual Journal readback protects the cancellation reason before POST');assert.ok(calls.every(call=>call.method==='GET'),'typing a reason never submits');
  await click('取消后续比较');await wait(()=>container.textContent.includes('已取消后续比较')&&dom.window.localStorage.getItem(activeKey)===null,'the independently read original cancellation settles');
  const mutations=calls.filter(call=>call.method!=='GET');assert.equal(mutations.length,1,'no create, save, freeze or run command is sent');assert.equal(mutations[0].method,'POST');assert.equal(mutations[0].path,rootPath+'/threshold-evaluations/'+evaluationID+'/cancel');
  assert.equal(calls.filter(call=>call.path===rootPath+'/threshold-study-requests/'+nonce).length,2,'same original nonce is read before and after its explicit POST');assert.ok(Array.from({length:dom.window.localStorage.length},(_,index)=>dom.window.localStorage.key(index)).some(key=>key.endsWith(':resolution:'+nonce)),'resolved original request remains archived');assert.equal(draftContainsReason(),true);assert.equal(current.result,null);assert.equal(current.samples[0].value,null);assert.equal(current.eligible_for_trial,false);freshDisabled();assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});
