import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import React,{act,useEffect} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {copy,digest} from '../src/report-evidence-reading-contract.mjs';

// Real React DOM reconciliation and development StrictMode; no hook dispatcher,
// controller replacements, server, original material, OCR image or external IO.
// jsdom storage/DOM and the serial test lock do not certify a real browser.
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),bundles=new Map();
let bundleDirectory;
after(async()=>{if(bundleDirectory)await rm(bundleDirectory,{recursive:true,force:true});});
async function component(kind){
 if(!bundles.has(kind))bundles.set(kind,(async()=>{
  bundleDirectory??=await mkdtemp(join(tmpdir(),'source-region-real-react-'));
  const name=kind==='source'?'FullSemanticSourceReview':'FullSemanticRegionMapping';
  const output=await build({root:project,configFile:false,envDir:bundleDirectory,cacheDir:join(bundleDirectory,'vite-cache'),mode:'test',logLevel:'silent',build:{ssr:join(project,'src',name+'.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const entry=output.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(entry,'actual JSX entry was built');
  const path=join(bundleDirectory,name+'.mjs');await writeFile(path,entry.code);
  return (await import(pathToFileURL(path).href)).default;
 })());
 return bundles.get(kind);
}
const tick=()=>new Promise(done=>setImmediate(done));
function barrier(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
async function sealed(value,key){value[key]=await digest(value);return value;}
function scopedPrefix(kind,scope){return (kind==='source'?'ga-full-semantic-source-review-v1:':'ga-full-semantic-regions-v1:')+scope.workspace_id+':'+scope.actor_id+':'+scope.handoff_id+':';}
async function fixture(kind,storage,{tag='甲',scope={actor_id:crypto.randomUUID(),workspace_id:crypto.randomUUID(),handoff_id:crypto.randomUUID()}}={}){
 const source=kind==='source',H='a'.repeat(64),value='合成领域内容'+tag,owner={path:'/domain',kind:'domain',label:'合成领域'+tag,value_hash:await digest(value),ref_hashes:[],evidence_status:'context',pending_reasons:['context_not_source_evidence']};
 const manifest=await sealed({schema:'report-full-semantic-source-manifest-v1',handoff_id:scope.handoff_id,design_hash:H,result_hash:H,source_hash:H,catalog_hash:H,privacy:{schema:'evidence-privacy-v1',projection_hash:H},owners:[owner],refs:[]},'manifest_hash');
 const base='/setup/'+(source?'full-semantic-source-reviews/':'full-semantic-region-mappings/')+scope.handoff_id;
 const state={schema:source?'report-full-semantic-source-review-state-v1':'report-full-semantic-region-state-v1',handoff_id:scope.handoff_id,active:true,configuration:{revision:1,hash:H},manifest,...(source?{reviews:[]}:{sessions:[],proofs:[]}),can_confirm:false,can_publish:false};
 const evidence={schema:'report-full-semantic-source-review-evidence-v1',manifest_hash:manifest.manifest_hash,owner:{...copy(owner),value},references:[],formal_source_status:'pending_final_view_projection',can_confirm:false,can_publish:false};
 const original=crypto.randomUUID(),copyID=crypto.randomUUID(),selection=await sealed({schema:'report-full-semantic-region-source-selection-v1',anchor:{source_ref:{id:original,version:1,hash:H},source_evidence_id:crypto.randomUUID(),source_name:'合成原件'+tag+'.docx',copy_material_id:copyID,copy_sha256:H,copy_evidence_id:crypto.randomUUID(),provenance_hash:H,run_id:crypto.randomUUID(),generation:1},copy_ref:{id:copyID,version:2,hash:H},pages:[{page:1,unit_id:crypto.randomUUID(),revision_id:crypto.randomUUID(),revision_hash:H,revision_kind:'confirmed',image_sha256:H,unit_version:1}]},'selection_hash');
 const options=await sealed({schema:'report-full-semantic-region-source-options-v1',handoff_id:scope.handoff_id,active:true,manifest_hash:manifest.manifest_hash,configuration:copy(state.configuration),copies:[{copy_ref:copy(selection.copy_ref),name:'合成副本'+tag+'.pdf',selection:copy(selection),confirmed_pages:1,whole_document_confirmed:true,reason:null,page_count:1}],can_confirm:false,can_publish:false},'options_hash');
 const prefix=scopedPrefix(kind,scope),outboxKey=prefix+'outbox',tabKey=prefix+'tab',calls=[],holds=[],postSnapshots=[];let inflight=0;
 function hold(path){const gate={path,entered:0,release:barrier()};holds.push(gate);return {get entered(){return gate.entered;},release:()=>gate.release.resolve()};}
 async function api(path,request={}){
  inflight++;try{
   const call={path,method:request.method||'GET',body:request.body,headers:copy(request.headers||{})};calls.push(call);
   assert.equal(call.headers['X-Workspace-ID'],scope.workspace_id,'real scoped transport keeps workspace header');
   const gate=holds.find(item=>item.path===path);if(gate){gate.entered++;await gate.release.promise;}
   if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
   if(path===base)return copy(state);
   if(path===base+'/source-options')return copy(options);
   if(path.startsWith(base+'/evidence?'))return copy(evidence);
   if(path.startsWith(base+'/requests/'))return {schema:source?'report-full-semantic-source-review-recovery-v1':'report-full-semantic-region-recovery-v1',found:false};
   if(call.method==='POST'&&path===base+(source?'/entries':'/sessions')){
    // Observe the actual controller's POST boundary, never manufacture its
    // pending record, hash, consent, request ID or successful receipt.
    const raw=storage.getItem(outboxKey);assert.ok(raw,'durable outbox exists before transport POST');
    const pending=JSON.parse(raw);assert.deepEqual(pending.scope,scope);assert.deepEqual(pending.body,JSON.parse(call.body));assert.equal(pending.attempted,true);
    assert.ok(calls.slice(0,-1).some(item=>item.method==='GET'&&item.path===base+'/requests/'+pending.body.request_id),'actual immutable nonce is queried before POST');
    postSnapshots.push(raw);throw Error('合成传输中断，保存结果未知');
   }
   throw Error('Unexpected synthetic StrictMode API path: '+path);
  }finally{inflight--;}
 }
 return {kind,source,tag,scope,base,owner,selection,api,calls,postSnapshots,prefix,outboxKey,tabKey,hold,releaseAll:()=>holds.forEach(item=>item.release.resolve()),get inflight(){return inflight;},posts:()=>calls.filter(call=>call.method==='POST'),requests:()=>calls.filter(call=>call.path.startsWith(base+'/requests/')),working:tab=>JSON.parse(storage.getItem(tab.getItem(tabKey)))};
}

async function environment(kind,run){
 assert.notEqual(process.env.NODE_ENV,'production','StrictMode regression must use the development React build');
 const network=[],errors=[],unhandled=[],saved=new Map(),fixtures=[];
 class NoResources extends ResourceLoader{fetch(url){network.push(String(url));return Promise.reject(Error('External jsdom resources are forbidden'));}}
 const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://strictmode.invalid/',resources:new NoResources(),virtualConsole});
 const nativeStorage=dom.window.localStorage,tab=dom.window.sessionStorage,storageMode={fail:false},lockTails=new Map();
 const storage={get length(){return nativeStorage.length;},key:index=>nativeStorage.key(index),getItem:key=>nativeStorage.getItem(key),removeItem:key=>nativeStorage.removeItem(key),setItem(key,value){if(storageMode.fail&&key.includes(':working:'))throw Error('synthetic browser quota');nativeStorage.setItem(key,String(value));}};
 const locks={async request(name,options,fn){assert.equal(options.mode,'exclusive');const previous=lockTails.get(name)||Promise.resolve(),release=barrier();lockTails.set(name,release.promise);await previous;try{return await fn();}finally{release.resolve();if(lockTails.get(name)===release.promise)lockTails.delete(name);}}};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks});
 const deny=(...args)=>{network.push(String(args[0]));throw Error('External transport is forbidden in the real React lifecycle test');};
 dom.window.fetch=deny;dom.window.XMLHttpRequest=class{constructor(){deny('XMLHttpRequest');}};dom.window.WebSocket=class{constructor(){deny('WebSocket');}};
 const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Element:dom.window.Element,Node:dom.window.Node,Text:dom.window.Text,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,InputEvent:dom.window.InputEvent,MutationObserver:dom.window.MutationObserver,getComputedStyle:dom.window.getComputedStyle.bind(dom.window),localStorage:storage,sessionStorage:tab,addEventListener:dom.window.addEventListener.bind(dom.window),removeEventListener:dom.window.removeEventListener.bind(dom.window),dispatchEvent:dom.window.dispatchEvent.bind(dom.window),fetch:deny,XMLHttpRequest:dom.window.XMLHttpRequest,WebSocket:dom.window.WebSocket,IS_REACT_ACT_ENVIRONMENT:true};
 for(const [key,value] of Object.entries(values)){saved.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});}
 const previousConsoleError=console.error;console.error=(...args)=>errors.push(Error(args.map(value=>String(value)).join(' ').slice(0,600)));
 const rejection=error=>unhandled.push(error);process.on('unhandledRejection',rejection);
 let root=null,current=null,guard=null,closes=0,probe={setup:0,cleanup:0};
 async function settle(){await act(async()=>{await tick();});}
 async function until(check,label){const deadline=Date.now()+5000;while(Date.now()<deadline){await settle();if(check())return;}assert.ok(check(),label);}
 const container=dom.window.document.getElementById('root');
 const text=()=>container.textContent||'';
 function button(label){const found=[...container.querySelectorAll('button')].find(node=>node.textContent===label);assert.ok(found,'actual DOM button: '+label);return found;}
 const register=value=>{guard=value;},onClose=()=>{closes++;};
 function Probe(){useEffect(()=>{probe.setup++;return()=>{probe.cleanup++;};},[]);return null;}
 function activate(f){tab.setItem('ga-setup-csrf','synthetic-strict-'+f.scope.actor_id+':'+f.scope.workspace_id);tab.setItem('ga-setup-csrf-user',f.scope.actor_id);tab.setItem('ga-setup-csrf-workspace',f.scope.workspace_id);}
 try{
  // react-dom/client must first observe a real jsdom document. It stays the
  // actual installed renderer and shares the same React module as the JSX.
  const {createRoot}=await import('react-dom/client'),Component=await component(kind);
  async function mount(f,{fresh=false}={}){
   activate(f);current=f;if(!fixtures.includes(f))fixtures.push(f);
   if(!root||fresh){assert.equal(root,null,'unmount the old root before creating another');probe={setup:0,cleanup:0};root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});}
   await act(async()=>{root.render(React.createElement(React.StrictMode,null,React.createElement(Probe),React.createElement(Component,{api:f.api,handoffId:f.scope.handoff_id,actorId:f.scope.actor_id,workspaceId:f.scope.workspace_id,onRegisterGuard:register,onClose})));});
  }
  async function unmount(){if(root){const old=root;root=null;await act(async()=>{old.unmount();});await settle();}}
  async function ready(){await until(()=>{const refresh=[...container.querySelectorAll('button')].find(node=>node.textContent==='重新读取保存状态');return !!refresh&&!refresh.disabled&&(current.source?text().includes(current.owner.label):!!container.querySelector('input[type="radio"]'));},'real component finished its original loading chain');}
  async function click(label){const node=button(label);assert.equal(node.disabled,false,'actual DOM button is enabled: '+label);await act(async()=>{node.click();});}
  async function preserve(){let result;await act(async()=>{result=guard?.();});return result;}
  async function input(note='必须恢复的合成人工作业'){
   if(current.source){
    let textarea=container.querySelector('textarea[aria-label="私人核对说明"]');
    if(!textarea){const ownerButton=[...container.querySelectorAll('button')].find(node=>node.textContent.startsWith(current.owner.label+' · '));assert.ok(ownerButton);await act(async()=>{ownerButton.click();});await until(()=>!!container.querySelector('textarea[aria-label="私人核对说明"]')&&!button('重新读取保存状态').disabled,'real evidence panel opened');textarea=container.querySelector('textarea[aria-label="私人核对说明"]');}
    const setter=Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set;
    await act(async()=>{setter.call(textarea,note);textarea.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
    assert.equal(textarea.value,note);assert.equal(current.working(tab).forms['/domain'].value.note,note,'React onChange persisted the actual DOM input');
   }else{
    const radio=container.querySelector('input[type="radio"]');assert.ok(radio);assert.equal(radio.disabled,false);
    await act(async()=>{radio.click();});assert.equal(radio.checked,true);assert.deepEqual(current.working(tab).selection,current.selection,'React onChange persisted the actual copy selection');
   }
  }
  async function unknown(){await click(current.source?'保存本项私人核对':'保存本次副本选择，开始定位');await until(()=>current.posts().length===1&&text().includes('保存结果未知')&&!button('重新读取保存状态').disabled,'unknown POST result is visible and busy is released');const raw=storage.getItem(current.outboxKey);assert.ok(raw);assert.equal(current.postSnapshots.length,1);assert.equal(raw,current.postSnapshots[0]);return raw;}
  const h={dom,storage,tab,storageMode,container,mount,unmount,ready,click,input,unknown,preserve,until,settle,button,text,fixture:options=>fixture(kind,storage,options),get guard(){return guard;},get closes(){return closes;},get probe(){return probe;}};
  await run(h);
 }finally{
  try{
   try{if(root){const old=root;root=null;await act(async()=>{old.unmount();});}}
   finally{fixtures.forEach(f=>f.releaseAll());}
   await until(()=>fixtures.every(f=>f.inflight===0),'all held synthetic transport requests were released');await settle();
  }finally{
   process.off('unhandledRejection',rejection);console.error=previousConsoleError;
   for(const [key,descriptor] of saved){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
   dom.window.close();
  }
  assert.deepEqual(network,[],'no fetch, XHR, WebSocket or external DOM resource was requested');
  assert.deepEqual(unhandled,[],'no unhandled async rejection');
  assert.deepEqual(errors.map(error=>error?.message||String(error)),[],'no React act/render warning or jsdom error');
 }
}

const options={concurrency:false,timeout:30000};
for(const kind of ['source','region']){
 test(kind+': actual StrictMode replays effects and the single initial load becomes usable',options,()=>environment(kind,async h=>{
  const f=await h.fixture(),gate=f.hold(f.base);await h.mount(f);await h.until(()=>gate.entered===1,'initial real transport reached its barrier');
  assert.deepEqual(h.probe,{setup:2,cleanup:1},'the actual development React renderer performed setup-cleanup-setup');
  assert.equal(h.button('重新读取保存状态').disabled,true);gate.release();await h.ready();
  assert.equal(f.calls.filter(call=>call.path===f.base).length,1);assert.equal(f.calls.filter(call=>call.path==='/me').length,f.source?1:2);
  if(!f.source)assert.equal(f.calls.filter(call=>call.path===f.base+'/source-options').length,1);
  await h.input();assert.equal(typeof h.guard,'function');assert.equal(await h.preserve(),true);assert.equal(f.posts().length,0);
  assert.equal(h.button(f.source?'保存本项私人核对':'保存本次副本选择，开始定位').disabled,false);
 }));

 test(kind+': actual DOM save writes an immutable unknown nonce and remount recovers without POST',options,()=>environment(kind,async h=>{
  const f=await h.fixture();await h.mount(f);await h.ready();await h.input('原保存的人工作业');const raw=await h.unknown(),pending=JSON.parse(raw);
  if(f.source){await h.input('等待期间后来追加的劳动');assert.equal(h.storage.getItem(f.outboxKey),raw);assert.equal(pending.body.value.note,'原保存的人工作业');}
  assert.equal(await h.preserve(),true);const working=f.working(h.tab),requestReads=f.requests().length;
  await h.unmount();assert.equal(h.guard,null);assert.deepEqual(h.probe,{setup:2,cleanup:2});assert.equal(h.storage.getItem(f.outboxKey),raw);
  await h.mount(f,{fresh:true});await h.ready();assert.deepEqual(h.probe,{setup:2,cleanup:1});
  if(f.source){assert.equal(h.container.querySelector('textarea[aria-label="私人核对说明"]').value,'等待期间后来追加的劳动');assert.equal(f.requests().length,requestReads);await h.click('查询原保存结果');}
  else assert.equal(h.container.querySelector('input[type="radio"]').checked,true);
  await h.until(()=>f.requests().length===requestReads+1&&!h.button('查询原保存结果').disabled,'original nonce was queried without automatic resend');
  assert.equal(f.requests().at(-1).path,f.base+'/requests/'+pending.body.request_id);assert.equal(f.posts().length,1);assert.equal(h.storage.getItem(f.outboxKey),raw);
  const restored=f.working(h.tab);assert.notEqual(restored.owner,working.owner);assert.deepEqual(f.source?restored.forms:restored.selection,f.source?working.forms:working.selection);
 }));

 for(const change of ['actor','workspace','handoff'])test(kind+': same mounted root isolates a late '+change+' response and preserves original input/nonce',options,()=>environment(kind,async h=>{
  const first=await h.fixture({tag:'旧作用域'});await h.mount(first);await h.ready();await h.input('旧账号不能泄露的合成私人劳动');const raw=await h.unknown(),key=h.tab.getItem(first.tabKey),working=h.storage.getItem(key),gate=first.hold(first.base);
  await h.click('重新读取保存状态');await h.until(()=>gate.entered===1,'old scope refresh is awaiting actual transport');const before=first.calls.length;
  const scope={...first.scope,[change+'_id']:crypto.randomUUID()},next=await h.fixture({tag:'新作用域',scope});
  await h.mount(next);await h.ready();gate.release();await h.settle();await h.settle();
  assert.equal(first.calls.length,before,'retired response did not launch another old-scope request');
  assert.ok(!h.text().includes('旧作用域'));assert.ok(!h.text().includes('旧账号不能泄露的合成私人劳动'));assert.ok(!h.text().includes('保存结果未知'));
  assert.ok(h.text().includes(next.source?next.owner.label:'合成副本新作用域.pdf'));
  assert.equal(h.storage.getItem(first.outboxKey),raw);assert.equal(h.storage.getItem(key),working);assert.equal(h.storage.getItem(next.outboxKey),null);
  assert.equal(next.requests().length,0);assert.equal(next.posts().length,0);assert.equal(await h.preserve(),true);assert.deepEqual(next.working(h.tab).scope,next.scope);
  assert.deepEqual(h.probe,{setup:2,cleanup:1},'scope changed props on the same actual root, without a key or fake remount');
 }));

 test(kind+': actual unmount fences a late response while retaining the original draft and nonce',options,()=>environment(kind,async h=>{
  const f=await h.fixture();await h.mount(f);await h.ready();await h.input('真卸载后仍需恢复的合成人工作业');const raw=await h.unknown(),key=h.tab.getItem(f.tabKey),working=h.storage.getItem(key),gate=f.hold(f.base);
  await h.click('重新读取保存状态');await h.until(()=>gate.entered===1,'transport is suspended across actual root.unmount');const before=f.calls.length;
  await h.unmount();assert.equal(h.guard,null);assert.deepEqual(h.probe,{setup:2,cleanup:2});gate.release();await h.settle();await h.settle();
  assert.equal(f.calls.length,before);assert.equal(h.storage.getItem(key),working);assert.equal(h.storage.getItem(f.outboxKey),raw);assert.equal(h.container.childNodes.length,0);
  await h.mount(f,{fresh:true});await h.ready();assert.equal(h.storage.getItem(f.outboxKey),raw);assert.equal(f.posts().length,1);
  if(f.source)assert.equal(h.container.querySelector('textarea[aria-label="私人核对说明"]').value,'真卸载后仍需恢复的合成人工作业');else assert.equal(h.container.querySelector('input[type="radio"]').checked,true);
 }));

 test(kind+': actual DOM storage failure blocks leave and beforeunload until retry durably saves the input',options,()=>environment(kind,async h=>{
  const f=await h.fixture();await h.mount(f);await h.ready();h.storageMode.fail=true;
  if(f.source){const ownerButton=[...h.container.querySelectorAll('button')].find(node=>node.textContent.startsWith(f.owner.label+' · '));await act(async()=>{ownerButton.click();});await h.until(()=>!!h.container.querySelector('textarea[aria-label="私人核对说明"]')&&!h.button('重新读取保存状态').disabled,'input panel survived the first failed stash write');const textarea=h.container.querySelector('textarea[aria-label="私人核对说明"]'),setter=Object.getOwnPropertyDescriptor(h.dom.window.HTMLTextAreaElement.prototype,'value').set;await act(async()=>{setter.call(textarea,'配额失败不能丢失的合成人工输入');textarea.dispatchEvent(new h.dom.window.Event('input',{bubbles:true}));});}
  else{const radio=h.container.querySelector('input[type="radio"]');await act(async()=>{radio.click();});assert.equal(radio.checked,true);}
  assert.ok(h.text().includes('尚未可靠保存'));assert.equal(await h.preserve(),false);await h.click(f.source?'保留输入并返回':'保留进度并返回');assert.equal(h.closes,0);
  const warning=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(warning);assert.equal(warning.defaultPrevented,true);
  assert.equal(f.posts().length,0);assert.equal(h.storage.getItem(f.outboxKey),null);
  h.storageMode.fail=false;await h.click('重试本机保存');assert.equal(await h.preserve(),true);
  const saved=f.working(h.tab);if(f.source)assert.equal(saved.forms['/domain'].value.note,'配额失败不能丢失的合成人工输入');else assert.deepEqual(saved.selection,f.selection);
  const safe=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(safe);assert.equal(safe.defaultPrevented,false);
  await h.click(f.source?'保留输入并返回':'保留进度并返回');assert.equal(h.closes,1);await h.unmount();await h.mount(f,{fresh:true});await h.ready();
  if(f.source)assert.equal(h.container.querySelector('textarea[aria-label="私人核对说明"]').value,'配额失败不能丢失的合成人工输入');else assert.equal(h.container.querySelector('input[type="radio"]').checked,true);
 }));
}
