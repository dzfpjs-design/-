// Actual component, original Journal and runtime transport; HTTP is synthetic.
// The hash pause exposes a same-scope props race, not a SQL/Worker proof.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto,createHash} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){if(!bundle)bundle=(async()=>{
 temporary=await mkdtemp(join(tmpdir(),'threshold-study-fresh-v150-'));const entry=join(temporary,'entry.mjs');
 await writeFile(entry,'export {default as Study} from '+JSON.stringify(join(project,'src/ThresholdStudyV149.jsx'))+';\nexport {ThresholdStudyJournalV149,studyScopeV149} from '+JSON.stringify(join(project,'src/threshold-study-contract-v149.mjs'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
 const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
 const output=result.output.find(item=>item.type==='chunk'&&item.isEntry),path=join(temporary,'component.mjs');assert.ok(output);await writeFile(path,output.code);return import(pathToFileURL(path).href);
})();return bundle;}
const id=n=>'15000000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value)),sha=raw=>createHash('sha256').update(raw).digest('hex');
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-fresh-gate-operator',role:'operator'},instance=id(3),record={id:id(4),version:1,extractor_id:id(5)},rootPath='/api/local-rules/evolution';
const baseline={id:record.extractor_id,title:'原规则二十',field:'count',field_label:'数量',operator:'gt',threshold:20,unit:'株'},candidate={...baseline,id:id(6),title:'已发布候选十八',threshold:18};
const training={id:id(10),version:1,root_id:id(11),observation_id:id(12),entity_id:id(13),entity_label:'合成训练对象',label:'should_alert',reason:'本次选择的已提交漏报理由',classification:'FN',field_label:'数量',value:19,unit:'株',eligible:true,issue:null};
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(name)||Promise.resolve()).catch(()=>{}).then(callback);tails.set(name,next);return next.finally(()=>{if(tails.get(name)===next)tails.delete(name);});}};}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources');}}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}

test('a same-scope fresh block arriving during original-wire hash prevents POST, preserves the human draft and still allows original GET recovery',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://threshold-study-fresh.invalid/',pretendToBeVisual:true,resources:new NoResources()}),calls=[],errors=[],violations=[],pause=deferred();let root,blockedReason='',hashEntered=false,pausedRaw=null,gateUsed=false,saved=null;
 const crypto={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:async(algorithm,bytes)=>{const raw=new TextDecoder().decode(bytes);if(!gateUsed&&raw.includes('"schema":"threshold-candidate-draft-v149"')){gateUsed=true;hashEntered=true;pausedRaw=raw;await pause.promise;}return webcrypto.subtle.digest(algorithm,bytes);}}};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const fetch=async(raw,options={})=>{const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname,search:url.search,raw:options.body});try{
  if(url.pathname==='/api/me')return response(account);assert.equal(new Headers(options.headers).get('X-Workspace-ID'),account.workspace_id);
  if(method==='GET'&&url.pathname===rootPath+'/threshold-study-options')return response({schema:'threshold-study-options-v149',workspace_id:account.workspace_id,baseline,candidates:[candidate],feedbacks:{training:[training],evaluation:[]}});
  if(method==='GET'&&url.pathname===rootPath+'/threshold-candidates')return response({schema:'threshold-study-candidate-list-v149',workspace_id:account.workspace_id,candidates:saved?[copy(saved)]:[],next_after:null});
  if(method==='GET'&&url.pathname.startsWith(rootPath+'/threshold-study-requests/')){assert.equal(url.searchParams.get('original_method'),'POST');assert.equal(url.searchParams.get('original_path'),rootPath+'/threshold-candidates');assert.equal(url.searchParams.get('expected_sha256'),sha(pausedRaw));return response({schema:'threshold-study-request-state-v149',found:!!saved,saved:saved?{command_id:id(90),target_kind:'candidate',target_id:saved.id,version:1,result_hash:'a'.repeat(64)}:null,rejected:null});}
  if(method==='POST'&&url.pathname===rootPath+'/threshold-candidates'){
   const body=JSON.parse(options.body);assert.equal(options.body,pausedRaw);assert.equal(new Headers(options.headers).get('X-CSRF-Token'),'synthetic-study-fresh-csrf');saved={schema:'threshold-study-candidate-view-v149',workspace_id:account.workspace_id,id:id(60),version:1,status:'draft',actor_id:account.user_id,title:body.title,rationale:body.rationale,baseline,candidate,training:[training],freeze_hash:null,current_issue:null,can_edit:true,can_freeze:true,eligible_for_trial:false};return response(saved,201);
  }
  if(method==='GET'&&url.pathname===rootPath+'/threshold-candidates/'+id(60))return response(saved);
  throw Error('Unexpected fresh-gate route '+method+' '+url.pathname);
 }catch(error){violations.push(error.stack);throw error;}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,fetch,crypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 try{
  const actual=await component(),renderer=await import('react-dom/client');assert.equal(actual.BundledReact,React);actual.setSession('synthetic-study-fresh-csrf',account.workspace_id,account.user_id,account.username);
  const container=dom.window.document.getElementById('root'),settle=async()=>{for(let i=0;i<8;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});},button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent===label),input=label=>container.querySelector('[aria-label="'+label+'"]'),click=async label=>{const node=button(label);assert.ok(node,'actual '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();},wait=async(predicate,label)=>{for(let i=0;i<80;i++){if(predicate())return;await settle();}assert.ok(predicate(),label+' '+container.textContent.slice(0,700));};
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Study,{record,instanceId:instance,account,Btn:Button,blockedReason}))));await settle();};
  const edit=async(label,value)=>{const node=input(label);assert.ok(node);assert.equal(node.disabled,false);const prototype=node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:node.tagName==='INPUT'?dom.window.HTMLInputElement.prototype:dom.window.HTMLTextAreaElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  root=renderer.createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error)});await render();assert.equal(calls.length,0);await click('比较阈值候选');await edit('候选规则',candidate.id);await edit('候选方案名称','等待核对时不得发送的方案');const reason='这份人工作业说明在暂停和原请求核对后仍须保留';await edit('调整说明',reason);
  const label=[...container.querySelectorAll('label')].find(node=>node.textContent.includes(training.reason)&&node.querySelector('input[type="checkbox"]'));assert.ok(label);await act(async()=>label.querySelector('input').click());await settle();await click('保存候选方案');await wait(()=>hashEntered,'actual original-wire hashing is paused');assert.equal(calls.filter(call=>call.method!=='GET').length,0);
  blockedReason='原依据正在重新核对，当前禁止新的比较写入';await render();assert.ok(container.textContent.includes(blockedReason));assert.equal(input('调整说明').value,reason);pause.resolve();await wait(()=>calls.some(call=>call.method!=='GET')||!!button('核对原研究请求')&&!button('核对原研究请求').disabled,'the paused operation reaches a terminal or retained-original boundary');
  assert.equal(calls.filter(call=>call.method!=='GET').length,0,'latest same-scope fresh block is checked at the real write boundary');assert.equal(input('调整说明').value,reason);assert.equal(input('调整说明').disabled,true);
  const journal=new actual.ThresholdStudyJournalV149(actual.studyScopeV149(account,instance,record)),pending=await journal.pending();assert.ok(pending);assert.equal(pending.exact_body,pausedRaw);assert.equal(pending.wire_sha256,sha(pausedRaw));assert.equal(JSON.parse(pausedRaw).rationale,reason);
  const before=calls.filter(call=>call.path.includes('/threshold-study-requests/')).length;await click('核对原研究请求');await wait(()=>calls.filter(call=>call.path.includes('/threshold-study-requests/')).length>before,'read-only original GET is allowed while fresh writes are blocked');assert.equal(calls.filter(call=>call.method!=='GET').length,0);assert.equal((await journal.pending()).exact_body,pausedRaw);assert.equal(input('调整说明').value,reason);assert.equal(button('按原编号重试研究请求').disabled,true);assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{pause.resolve();if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});
