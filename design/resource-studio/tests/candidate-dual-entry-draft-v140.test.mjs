// Real simultaneous ObjectStateStudio + NativeReceiptStudio, with their real
// query children, React DOM and StrictMode. HTTP is synthetic and GET-only.
// Storage failure below is a real setItem exception, not a mocked guard.
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
async function components(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'candidate-dual-entry-v140-'));const entry=join(temporary,'entry.mjs');
  await writeFile(entry,'export {default as ObjectStudio} from '+JSON.stringify(join(project,'src/ObjectStateStudio.jsx'))+';\nexport {default as ReceiptStudio} from '+JSON.stringify(join(project,'src/NativeReceiptStudio.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {candidateQueryScopeV139,forgetCandidateQueryDraftV139} from '+JSON.stringify(join(project,'src/native-candidate-query-v139.mjs'))+';\nexport {default as BundledReact} from "react";\n');
  const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
  const output=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(output,'bundle both real entry components and the shared real query child');const file=join(temporary,'components.mjs');await writeFile(file,output.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
const id=value=>'14020000-0000-4000-8000-'+String(value).padStart(12,'0');
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-dual-entry-operator',role:'operator'},instance=id(3),release=id(4),entity=id(5),otherEntity=id(6);
const prefix='ga-native-candidate-query-v139:'+account.user_id+':'+account.workspace_id+':'+instance+':'+release+':'+entity+':';
const objectText='对象页尚未保存的独立检索稿',receiptText='收件页已保存的另一份检索稿';
function object(identity){return {id:identity,name:identity===entity?'共同核查对象甲':'另一个对象乙',version:1,object_compatibility:{status:'current',target_release_id:release,usable:true,source_label:identity===entity?'共同核查对象甲':'另一个对象乙',source_type:'测试对象',source_release_number:1}};}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const prior=tails.get(name)||Promise.resolve(),next=prior.catch(()=>{}).then(callback);tails.set(name,next);return next.finally(()=>{if(tails.get(name)===next)tails.delete(name);});}};}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources in the synthetic dual-entry DOM');}}

test('simultaneous object and receipt drafts stay independent; saving one cannot release the other unsaved draft',async()=>{
 const errors=[],violations=[],calls=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://candidate-dual-entry.invalid/',pretendToBeVisual:true,resources:new NoResources(),virtualConsole:console});
 const backing=dom.window.localStorage,session=dom.window.sessionStorage,token='synthetic-dual-entry-token',base='/api/workspace/instances/'+instance,state={quota:false,quotaWrites:0,showObject:true,showReceipt:true};
 const storage={get length(){return backing.length;},key:index=>backing.key(index),getItem:key=>backing.getItem(key),removeItem:key=>backing.removeItem(key),setItem(key,value){if(state.quota&&key.startsWith(prefix+'draft:')){state.quotaWrites++;throw new dom.window.DOMException('Synthetic transient storage quota','QuotaExceededError');}backing.setItem(key,value);}};
 Object.defineProperty(dom.window,'localStorage',{configurable:true,value:storage});Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});session.setItem('ga-setup-csrf',token);session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname+url.search});
  try{
   assert.equal(url.origin,dom.window.location.origin);assert.equal(method,'GET','working draft isolation cannot issue a query, model, sync or gateway command');const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),token);const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
   if(url.pathname==='/api/me'){assert.equal(url.search,'');return response(account);}
   if(url.pathname===base+'/native-receipts'){
    if(url.search){assert.deepEqual([...url.searchParams.keys()],['entity_ids']);assert.ok([entity,otherEntity].includes(url.searchParams.get('entity_ids')));}return response({available:0,not_ready:0,truncated:false,preview_hash:'a'.repeat(64),candidates:[],records:[],daily:[],history_count:0,has_more:false});
   }
   if(url.pathname===base+'/native-candidate-queries'){assert.equal(url.search,'');return response({schema:'native-candidate-query-list-v139',queries:[]});}
   throw Error('Unexpected dual-entry HTTP '+method+' '+url.pathname+url.search);
  }catch(error){violations.push(error.stack||error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};const prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 let root,actual;const container=dom.window.document.getElementById('root');
 try{
  const renderer=await import('react-dom/client');actual=await components();assert.equal(actual.BundledReact,React);assert.equal(renderer.version,React.version);actual.setSession(token,account.workspace_id,account.user_id,account.username);
  const settle=async(turns=12)=>{for(let index=0;index<turns;index++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
  const render=async()=>{const common={account,instanceId:instance,releaseID:release,objects:[object(entity),object(otherEntity)],Btn:Button,onOpenTask:()=>{throw Error('This working-draft case must not navigate');}};await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement('div',{'data-entry':'object'},state.showObject?React.createElement(actual.ObjectStudio,{...common,canWrite:true,formKey:'ga-dual-entry-object:'+account.user_id+':'+instance}):null),React.createElement('div',{'data-entry':'receipt'},state.showReceipt?React.createElement(actual.ReceiptStudio,common):null))));await settle();};
  const section=entry=>container.querySelector('[data-entry="'+entry+'"]'),control=(entry,label)=>section(entry)?.querySelector('[aria-label="'+label+'"]'),button=(entry,label)=>[...section(entry).querySelectorAll('button')].find(node=>node.textContent===label);
  const wait=async(predicate,label)=>{for(let index=0;index<100;index++){if(predicate())return;await settle(1);}assert.ok(predicate(),label+'; '+JSON.stringify({object:section('object').textContent.slice(0,800),receipt:section('receipt').textContent.slice(0,800),calls:calls.slice(-12),errors:errors.slice(0,1).map(error=>String(error.stack||error)),violations:violations.slice(0,1)}));};
  const ready=entry=>wait(()=>!!control(entry,'候选搜索文字')&&!button(entry,'读取保存的查询')?.disabled,'real '+entry+' query form ready');
  const change=async(entry,label,value)=>{const node=control(entry,label);assert.ok(node,'actual '+entry+' '+label);assert.equal(node.disabled,false);const kind=node.tagName==='SELECT'?'HTMLSelectElement':'HTMLInputElement';await act(async()=>{Object.getOwnPropertyDescriptor(dom.window[kind].prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  const click=async(entry,label)=>{const node=button(entry,label);assert.ok(node,'actual '+entry+' button '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const beforeUnload=()=>{const event=new dom.window.Event('beforeunload',{cancelable:true});dom.window.dispatchEvent(event);return event.defaultPrevented;};
  root=renderer.createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();
  await change('object','状态核查对象',entity);await ready('object');await change('receipt','多模归集对象范围',entity);await ready('receipt');
  assert.equal(control('object','状态核查对象').value,entity);assert.equal(control('receipt','多模归集对象范围').value,entity);
  state.quota=true;await change('object','候选搜索文字',objectText);assert.ok(state.quotaWrites>0);assert.equal(control('object','候选搜索文字').value,objectText);assert.equal(beforeUnload(),true);
  // A later successful write in the other entry must not forget the failed
  // write from the first entry, even though their server query scope is equal.
  state.quota=false;await change('receipt','候选搜索文字',receiptText);assert.equal(control('receipt','候选搜索文字').value,receiptText);assert.equal(control('object','候选搜索文字').value,objectText);
  await change('object','状态核查对象',otherEntity);assert.equal(control('object','状态核查对象').value,entity,'receipt save must not release the object-page unsaved-draft guard');assert.ok([...section('object').querySelectorAll('[role="alert"]')].some(node=>/保存|输入|草稿|人稿/.test(node.textContent)));
  await change('receipt','多模归集对象范围',otherEntity);assert.equal(control('receipt','多模归集对象范围').value,entity,'parent entry must see the other channel unsaved draft');assert.equal(beforeUnload(),true);
  state.showObject=false;await render();assert.equal(beforeUnload(),true,'unmounting the unsaved entry must not remove its global human-work warning');assert.equal(control('receipt','候选搜索文字').value,receiptText);
  // Storage remains faulty for A's remount; this cannot be called a save.
  state.quota=true;state.showObject=true;await render();await ready('object');assert.equal(control('object','状态核查对象').value,entity);assert.equal(control('object','候选搜索文字').value,objectText,'object channel must recover its own unsaved memory, not the receipt channel text');assert.equal(control('receipt','候选搜索文字').value,receiptText);assert.equal(beforeUnload(),true);
  state.quota=false;await click('object','重试保存检索输入');assert.equal(beforeUnload(),false,'only saving A itself may clear A protection');assert.equal(control('object','候选搜索文字').value,objectText);assert.equal(control('receipt','候选搜索文字').value,receiptText);
  // Both independently persisted pointers survive a cold component remount.
  state.showReceipt=false;await render();state.showReceipt=true;await render();await change('receipt','多模归集对象范围',entity);await ready('receipt');assert.equal(control('receipt','候选搜索文字').value,receiptText,'legacy receipt channel recovers B independently');assert.equal(control('object','候选搜索文字').value,objectText);
  await change('object','状态核查对象',otherEntity);await ready('object');assert.equal(control('object','状态核查对象').value,otherEntity);await change('receipt','多模归集对象范围',otherEntity);await ready('receipt');assert.equal(control('receipt','多模归集对象范围').value,otherEntity);assert.equal(beforeUnload(),false);
  assert.equal(calls.some(row=>row.method!=='GET'),false);assert.deepEqual(violations,[]);assert.deepEqual(errors,[]);
 }finally{
  if(root)await act(async()=>root.unmount());
  if(actual)for(const identity of [entity,otherEntity])for(const channel of ['','object-state'])actual.forgetCandidateQueryDraftV139(actual.candidateQueryScopeV139(account,instance,release,identity),channel);
  dom.window.close();for(const[key,descriptor]of Object.entries(prior)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
 }
});
