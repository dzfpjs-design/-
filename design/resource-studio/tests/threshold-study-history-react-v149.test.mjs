// Real component/Journal and runtime client; HTTP is an explicit fixture.
// This proves the history/fresh-options boundary, not SQL or Worker authority.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){if(!bundle)bundle=(async()=>{
 temporary=await mkdtemp(join(tmpdir(),'threshold-study-history-v149-'));const entry=join(temporary,'entry.mjs');
 await writeFile(entry,'export {default as Study} from '+JSON.stringify(join(project,'src/ThresholdStudyV149.jsx'))+';\nexport {ThresholdStudyJournalV149,studyScopeV149,emptyStudyFormV149} from '+JSON.stringify(join(project,'src/threshold-study-contract-v149.mjs'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
 const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
 const output=result.output.find(item=>item.type==='chunk'&&item.isEntry),path=join(temporary,'component.mjs');assert.ok(output);await writeFile(path,output.code);return import(pathToFileURL(path).href);
})();return bundle;}
const id=n=>'14910000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value));
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-history-operator',role:'operator'},instance=id(3),record={id:id(4),version:1,extractor_id:id(5)},candidateID=id(60),evaluationID=id(70),rootPath='/api/local-rules/evolution';
const rationale='来源受阻后仍要独立读回的服务器方案说明',humanReason='原已提交的漏报判断理由仍可读',limitations='原独立评估的适用限制与人工说明仍保留',sourceIssue='当前原处理依据不可用于比较，历史人工说明保留，机器数值暂不展示';
const baseline={id:record.extractor_id,title:'原登记规则',field:'count',field_label:'数量',operator:'gt',threshold:20,unit:'株'},proposed={...baseline,id:id(6),title:'原冻结候选规则',threshold:18};
const training={id:id(10),version:1,root_id:id(11),observation_id:id(12),entity_id:id(13),entity_label:'原训练对象',label:'should_alert',reason:humanReason,classification:'unknown',field_label:'数量',value:null,unit:'株',eligible:false,issue:sourceIssue},sample={...training,id:id(20),root_id:id(21),observation_id:id(22),entity_id:id(23),entity_label:'独立评估对象',reason:'独立原样本的人工作业理由'};
const savedCandidate={schema:'threshold-study-candidate-view-v149',workspace_id:account.workspace_id,id:candidateID,version:2,status:'frozen',actor_id:account.user_id,title:'已保存的历史候选方案',rationale,baseline, candidate:proposed,training:[training],freeze_hash:'c'.repeat(64),current_issue:sourceIssue,can_edit:false,can_freeze:false,eligible_for_trial:false};
const savedEvaluation={schema:'threshold-study-evaluation-view-v149',workspace_id:account.workspace_id,id:evaluationID,version:4,status:'completed',actor_id:account.user_id,title:'已保存的历史评估',candidate_id:candidateID,baseline,candidate:proposed,training:[training],samples:[sample],criteria:{max_additional_reviews:0},purpose:'mechanism_check',limitations,freeze_hash:'e'.repeat(64),job:{id:id(80),status:'succeeded',attempts:1,error:null},progress:{completed:2,total:2,reserved:2},result:null,error:null,current_issue:sourceIssue,can_edit:false,can_freeze:false,can_run:false,can_cancel:false,eligible_for_trial:false};
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources');}}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}

test('fresh options409 cannot hide known server history or readonly pagination and cannot enable a study write',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://threshold-study-history.invalid/',pretendToBeVisual:true,resources:new NoResources()}),calls=[],errors=[],violations=[];let root;
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(name,options,callback)=>{assert.equal(options.mode,'exclusive');return callback();}}});
 const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const fetch=async(raw,options={})=>{const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname,search:url.search});try{
  assert.equal(method,'GET','history recovery never sends a study command');
  if(url.pathname==='/api/me')return response(account);assert.equal(new Headers(options.headers).get('X-Workspace-ID'),account.workspace_id);
  if(url.pathname===rootPath+'/threshold-study-options')return response({detail:'原基线登记当前不可用于新研究'},409);
  if(url.pathname===rootPath+'/threshold-candidates/'+candidateID)return response(copy(savedCandidate));
  if(url.pathname===rootPath+'/threshold-evaluations/'+evaluationID)return response(copy(savedEvaluation));
  if(url.pathname===rootPath+'/threshold-candidates'){assert.equal(url.searchParams.get('baseline_capability_id'),record.extractor_id);const after=url.searchParams.get('after');if(after)assert.equal(after,candidateID);return response({schema:'threshold-study-candidate-list-v149',workspace_id:account.workspace_id,candidates:[after?{...copy(savedCandidate),id:id(61),title:'下一页的历史候选方案'}:copy(savedCandidate)],next_after:after?null:candidateID});}
  if(url.pathname===rootPath+'/threshold-evaluations'){assert.equal(url.searchParams.get('candidate_id'),candidateID);return response({schema:'threshold-study-evaluation-list-v149',workspace_id:account.workspace_id,evaluations:[copy(savedEvaluation)],next_after:null});}
  throw Error('Unexpected history route '+url.pathname);
 }catch(error){violations.push(error.stack);throw error;}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 try{
  const actual=await component(),renderer=await import('react-dom/client');assert.equal(actual.BundledReact,React);actual.setSession('synthetic-study-history-csrf',account.workspace_id,account.user_id,account.username);
  const journal=new actual.ThresholdStudyJournalV149(actual.studyScopeV149(account,instance,record));journal.open();const form=actual.emptyStudyFormV149();form.candidate={id:candidateID,version:2,capability_id:proposed.id,title:savedCandidate.title,rationale,feedback_refs:[{feedback_id:training.id,expected_version:1}]};form.evaluation={...form.evaluation,id:evaluationID,version:4,title:savedEvaluation.title,feedback_refs:[{feedback_id:sample.id,expected_version:1}],limitations,confirm_compatible_objects:true};journal.save(form);journal.dispose();
  const container=dom.window.document.getElementById('root'),settle=async()=>{for(let i=0;i<8;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});},button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent===label),click=async label=>{const node=button(label);assert.ok(node,'actual '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();},wait=async(predicate,label)=>{for(let i=0;i<80;i++){if(predicate())return;await settle();}assert.ok(predicate(),label+' '+container.textContent.slice(0,700));};
  const mount=async()=>{root=renderer.createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error)});await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Study,{record,instanceId:instance,account,Btn:Button}))));await settle();};
  await mount();assert.equal(calls.length,0);await click('比较阈值候选');await wait(()=>calls.some(call=>call.path===rootPath+'/threshold-evaluations/'+evaluationID)&&container.textContent.includes(limitations),'known IDs still cold-read after options409');
  assert.ok(container.textContent.includes(rationale));assert.ok(container.textContent.includes(humanReason));assert.ok(container.textContent.includes(sample.reason));assert.ok(container.textContent.includes(sourceIssue));assert.ok(!container.textContent.includes('机制比较通过'));assert.equal(container.querySelectorAll('table').length,0,'no hidden result or raw fallback');
  for(const label of ['保存候选方案','冻结候选方案','保存评估草稿','冻结评估样本','开始真实比较'])assert.ok(!button(label)||button(label).disabled,'fresh action stays blocked: '+label);
  await click('继续读取候选方案');await wait(()=>container.textContent.includes('下一页的历史候选方案'),'history pagination stays available');assert.ok(calls.some(call=>call.path===rootPath+'/threshold-candidates'&&new URLSearchParams(call.search).get('after')===candidateID));
  await act(async()=>root.unmount());root=null;const before=calls.length;await mount();await click('比较阈值候选');await wait(()=>container.textContent.includes(limitations),'same independent draft pointers recover on remount');assert.ok(calls.slice(before).every(call=>call.method==='GET'));assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});
