// Actual NativeReceiptStudio and its actual V139 query child with React DOM
// and StrictMode. Synthetic GET transport/storage fault only; no query POST,
// model, browser acceptance or backend durability is asserted here.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'candidate-scope-draft-v140-'));const entry=join(temporary,'entry.mjs');
  // Only existing V139 modules: old-source red must exercise the actual lost
  // draft mechanism, not fail collection on a future helper/export.
  await writeFile(entry,'export {default as Studio} from '+JSON.stringify(join(project,'src/NativeReceiptStudio.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {candidateQueryScopeV139,forgetCandidateQueryDraftV139} from '+JSON.stringify(join(project,'src/native-candidate-query-v139.mjs'))+';\nexport {default as BundledReact} from "react";\n');
  const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
  const output=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(output,'bundle the actual receipt page and actual query child');const file=join(temporary,'component.mjs');await writeFile(file,output.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
const id=value=>'14010000-0000-4000-8000-'+String(value).padStart(12,'0');
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-private-draft-operator',role:'operator'},instance=id(3),release=id(4),entity=id(5),otherEntity=id(6),otherRelease=id(7);
const prefix='ga-native-candidate-query-v139:'+account.user_id+':'+account.workspace_id+':'+instance+':'+release+':'+entity+':';
const secret='尚未保存的原对象核查检索文字';
const copy=value=>JSON.parse(JSON.stringify(value));
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const prior=tails.get(name)||Promise.resolve(),next=prior.catch(()=>{}).then(callback);tails.set(name,next);return next.finally(()=>{if(tails.get(name)===next)tails.delete(name);});}};}
function object(identity,targetRelease){return {id:identity,name:identity===entity?'原对象甲':'另一个对象乙',version:1,object_compatibility:{status:'current',target_release_id:targetRelease,usable:true,source_label:identity===entity?'原对象甲':'另一个对象乙',source_type:'测试对象',source_release_number:1}};}
const emptyReceipts=()=>({available:0,not_ready:0,truncated:false,preview_hash:'a'.repeat(64),candidates:[],records:[],daily:[],history_count:0,has_more:false});
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden in this synthetic DOM');}}
async function harness(run){
 const errors=[],violations=[],calls=[],commits=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://candidate-draft-scope.invalid/',pretendToBeVisual:true,resources:new NoResources(),virtualConsole:console});
 const backing=dom.window.localStorage,session=dom.window.sessionStorage,token='synthetic-candidate-draft-token',base='/api/workspace/instances/'+instance;
 const state={account:copy(account),who:copy(account),releaseID:release,quota:false,quotaWrites:0};
 const storage={get length(){return backing.length;},key:index=>backing.key(index),getItem:key=>backing.getItem(key),removeItem:key=>backing.removeItem(key),setItem(key,value){if(state.quota&&key.startsWith(prefix+'draft:')){state.quotaWrites++;throw new dom.window.DOMException('Synthetic candidate draft quota','QuotaExceededError');}backing.setItem(key,value);}};
 Object.defineProperty(dom.window,'localStorage',{configurable:true,value:storage});Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 session.setItem('ga-setup-csrf',token);session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname+url.search,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin);assert.equal(method,'GET','draft recovery never submits, syncs, runs a model or calls a gateway');const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),state.account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),token);const respond=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
   if(url.pathname==='/api/me'){assert.equal(url.search,'');return respond(copy(state.who));}
   if(url.pathname===base+'/native-receipts'){
    if(url.search){assert.deepEqual([...url.searchParams.keys()],['entity_ids']);assert.ok([entity,otherEntity].includes(url.searchParams.get('entity_ids')));}return respond(emptyReceipts());
   }
   if(url.pathname===base+'/native-candidate-queries'){assert.equal(url.search,'');return respond({schema:'native-candidate-query-list-v139',queries:[]});}
   throw Error('Unexpected draft recovery HTTP route '+method+' '+url.pathname+url.search);
  }catch(error){violations.push(error.stack||error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};const prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 let root,actual;const container=dom.window.document.getElementById('root');
 try{
  const renderer=await import('react-dom/client');actual=await component();assert.equal(actual.BundledReact,React);assert.equal(renderer.version,React.version);actual.setSession(token,account.workspace_id,account.user_id,account.username);
  const settle=async(turns=12)=>{for(let index=0;index<turns;index++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
  const record=()=>commits.push({actor:state.account.user_id,release:state.releaseID,candidateText:container.querySelector('.native-candidate-query-v139')?.textContent||'',values:[...container.querySelectorAll('input,textarea')].map(node=>node.value)});
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(React.Profiler,{id:'actual-receipt-query-draft',onRender:record},React.createElement(actual.Studio,{account:state.account,instanceId:instance,releaseID:state.releaseID,objects:[object(entity,state.releaseID),object(otherEntity,state.releaseID)],Btn:Button,onOpenTask:()=>{throw Error('This draft recovery case must not navigate or create a task');}})))));await settle();};
  const mount=async()=>{root=renderer.createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const control=label=>container.querySelector('[aria-label="'+label+'"]'),button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent===label);
  const wait=async(predicate,label)=>{for(let index=0;index<100;index++){if(predicate())return;await settle(1);}assert.ok(predicate(),label+'; '+JSON.stringify({text:container.textContent.slice(0,1400),calls:calls.slice(-10),errors:errors.slice(0,1).map(error=>String(error.stack||error)),violations:violations.slice(0,1)}));};
  const change=async(label,value)=>{const node=control(label);assert.ok(node,'actual control '+label);assert.equal(node.disabled,false);const name=node.tagName==='SELECT'?'HTMLSelectElement':'HTMLInputElement';await act(async()=>{Object.getOwnPropertyDescriptor(dom.window[name].prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  const click=async label=>{const node=button(label);assert.ok(node,'actual button '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const ready=()=>wait(()=>!!control('候选搜索文字')&&!button('读取保存的查询')?.disabled,'actual query child is ready for the selected receipt object');
  const beforeUnload=()=>{const event=new dom.window.Event('beforeunload',{cancelable:true});dom.window.dispatchEvent(event);return event.defaultPrevented;};
  const beginUnsaved=async()=>{await mount();await change('多模归集对象范围',entity);await ready();state.quota=true;await change('候选搜索文字',secret);assert.ok(state.quotaWrites>0,'actual candidate draft Storage.setItem fault occurred');assert.equal(control('候选搜索文字').value,secret);assert.equal(beforeUnload(),true,'unpersisted human input initially guards browser close');assert.equal(button('开始查询').disabled,true);};
  const saveOriginal=async()=>{state.quota=false;await click('重试保存检索输入');assert.equal(control('候选搜索文字').value,secret);assert.equal(beforeUnload(),false,'successful explicit save clears the unsaved-only warning');assert.ok([...Array(storage.length)].map((_,index)=>storage.getItem(storage.key(index))).some(raw=>raw.includes(secret)),'raw original input was actually persisted');};
  await run({dom,state,calls,commits,container,storage,session,mount,render,settle,control,button,wait,change,click,ready,beforeUnload,beginUnsaved,saveOriginal,setSession:actual.setSession});
  assert.equal(calls.some(row=>row.method!=='GET'),false);assert.deepEqual(violations,[]);assert.deepEqual(errors,[]);
 }finally{
  if(root)await act(async()=>root.unmount());
  // Synthetic in-memory inputs belong to this test. Clean through existing
  // V139 exports so a genuine red cannot contaminate the next DOM fixture.
  if(actual)for(const actor of [account,{...account,user_id:id(8)}])for(const version of [release,otherRelease])for(const identity of [entity,otherEntity])actual.forgetCandidateQueryDraftV139(actual.candidateQueryScopeV139(actor,instance,version,identity));
  dom.window.close();for(const[key,descriptor]of Object.entries(prior)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
 }
}

test('actual receipt object selection cannot leave an unpersisted candidate draft; explicit save then permits switching',()=>harness(async h=>{
 await h.beginUnsaved();await h.change('多模归集对象范围',otherEntity);assert.equal(h.control('多模归集对象范围').value,entity,'failed candidate draft must keep the original object selected');assert.equal(h.control('候选搜索文字').value,secret,'attempted object change cannot discard the original human text');assert.ok([...h.container.querySelectorAll('[role="alert"]')].some(node=>/保存|原输入|草稿|人稿/.test(node.textContent)),'blocked scope change explains the unsaved input');assert.equal(h.beforeUnload(),true);
 await h.saveOriginal();await h.change('多模归集对象范围',otherEntity);await h.ready();assert.equal(h.control('多模归集对象范围').value,otherEntity);assert.equal(h.control('候选搜索文字').value,'');assert.equal(h.beforeUnload(),false);
}));

for(const change of ['release','actor'])test('external '+change+' change conceals the old candidate draft but preserves its close warning until saving the original scope',()=>harness(async h=>{
 await h.beginUnsaved();const boundary=h.commits.length;
 if(change==='release')h.state.releaseID=otherRelease;
 else{h.state.account={...account,user_id:id(8),username:'synthetic-new-account'};h.state.who=copy(h.state.account);h.setSession('synthetic-candidate-draft-token',h.state.account.workspace_id,h.state.account.user_id,h.state.account.username);}
 await h.render();await h.ready();const first=h.commits.slice(boundary).find(row=>change==='release'?row.release===otherRelease:row.actor===id(8));assert.ok(first);assert.ok(!first.values.includes(secret)&&!first.candidateText.includes(secret),'first external-scope commit hides old private input');assert.equal(h.control('候选搜索文字').value,'');assert.equal(h.beforeUnload(),true,'hidden original-scope unsaved text still prevents silent page close');
 h.state.account=copy(account);h.state.who=copy(account);h.state.releaseID=release;h.setSession('synthetic-candidate-draft-token',account.workspace_id,account.user_id,account.username);await h.render();await h.ready();assert.equal(h.control('多模归集对象范围').value,entity);assert.equal(h.control('候选搜索文字').value,secret,'returning to original scope recovers original human input');assert.equal(h.beforeUnload(),true);
 await h.saveOriginal();await h.change('多模归集对象范围',otherEntity);await h.ready();assert.equal(h.control('多模归集对象范围').value,otherEntity);assert.equal(h.beforeUnload(),false);
}));
