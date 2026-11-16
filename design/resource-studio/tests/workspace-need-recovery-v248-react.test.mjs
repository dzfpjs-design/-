// Actual DataNeed/BusinessWorkspace, production runtime-json and React DOM.
// Only HTTP and persistence failures are synthetic; Root proves real API/DB/
// restart and browser behavior independently.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import {build} from 'vite';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {needRecoveryFixture,id} from './workspace-need-recovery-v248-fixture.mjs';

const project=resolve(fileURLToPath(new URL('..',import.meta.url))),require=createRequire(join(project,'package.json')),reactPath=require.resolve('react'),reactURL=pathToFileURL(reactPath).href;
let folder,compiled;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
async function components(){
 if(!compiled)compiled=(async()=>{
  folder=await mkdtemp(join(tmpdir(),'need-request-recovery-v248-'));const entry=join(folder,'entry.jsx');
  await writeFile(entry,'export {default as Panel} from '+JSON.stringify(join(project,'src/DataNeedStudio.jsx'))+';export {default as Workspace} from '+JSON.stringify(join(project,'src/BusinessWorkspace.jsx'))+';export {json,setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';');
  const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),logLevel:'silent',define:{'process.env.NODE_ENV':'"development"'},build:{ssr:entry,write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}});
  const chunk=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);const file=join(folder,'actual.mjs');await writeFile(file,chunk.code);return import(pathToFileURL(file).href);
 })();return compiled;
}
async function harness(run,{workspace=false}={}){
 assert.equal(process.env.NODE_ENV,'development');const f=needRecoveryFixture(),errors=[],vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error));
 class NoNetwork extends ResourceLoader{fetch(){throw Error('External resources forbidden in synthetic recovery UI tests');}}
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://need-recovery-react.invalid/?view=workspace&instance='+f.instanceId,resources:new NoNetwork(),virtualConsole:vc});
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),path=url.pathname.replace(/^\/api/,'');
  try{
   assert.equal(url.origin,dom.window.location.origin);
   let value;
   if(path==='/me'||path.startsWith(f.base)||path==='/workspace/instances/'+f.instanceId+'/data-need-requests')value=await f.api(path+url.search,options);
   else{
    assert.equal(options.method||'GET','GET','Recovery cannot dispatch other Tasks, models or collection');
    if(path==='/instances')value=[f.instance];
    else if(path==='/instances/'+f.instanceId+'/releases')value=[f.release];
    else if(path==='/instances/'+f.instanceId+'/objects')value=[f.object];
    else if(['/workspace/instances/'+f.instanceId+'/tasks','/workspace/instances/'+f.instanceId+'/observations','/workspace/instances/'+f.instanceId+'/daily'].includes(path))value=[];
    else throw Object.assign(Error('本合成场景没有启用此附属能力：'+path),{status:503});
   }
   return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
  }catch(error){if(error.name==='AssertionError')errors.push(error);return new Response(JSON.stringify({detail:error.message}),{status:error.status||503,headers:{'Content-Type':'application/json'}});}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,StorageEvent:dom.window.StorageEvent,localStorage:f.storage,sessionStorage:f.session,crypto:webcrypto,fetch,location:dom.window.location,history:dom.window.history,IS_REACT_ACT_ENVIRONMENT:true,addEventListener:dom.window.addEventListener.bind(dom.window),removeEventListener:dom.window.removeEventListener.bind(dom.window)},prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});Object.defineProperty(dom.window.navigator,'locks',{value:f.locks,configurable:true});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 const actual=await components(),{createRoot}=await import('react-dom/client');actual.setSession(f.csrf,f.account.workspace_id,f.account.user_id,f.account.username);
 const container=document.getElementById('root');let root,props,guard;
 const oldConsoleError=console.error;console.error=(...values)=>{const message=values.map(String).join(' ');if(/overlapping act|without await/.test(message))errors.push(Error(message));else oldConsoleError(...values);};
 const Btn=({children,primary,ghost,small,icon,...rest})=>React.createElement('button',{type:'button',...rest},children),Field=({label,children})=>React.createElement('label',null,label,children);
 const register=(fn,previous)=>{if(fn)guard=fn;else if(!previous||guard===previous)guard=null;};
 const render=async()=>act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(workspace?actual.Workspace:actual.Panel,props))));
 const mount=async()=>{const common={account:f.account,instanceId:f.instanceId,Btn,onRegisterGuard:register};props=workspace?{...common,Field,onBack:()=>{}}:{...common,api:actual.json,storage:f.storage,tabStorage:f.session,locks:f.locks};root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
 const unmount=async()=>{if(root){const old=root;root=null;await act(async()=>old.unmount());await Promise.resolve();}};
 const until=async(predicate,label='actual V248 recovery state')=>{for(let turn=0;turn<350;turn++){assert.deepEqual(errors,[]);if(predicate())return;await act(async()=>new Promise(resolve=>setTimeout(resolve,2)));}assert.ok(predicate(),label+': '+container.textContent.slice(0,1400));};
 const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label);
 const openDetails=async label=>{const node=[...container.querySelectorAll('summary')].find(item=>item.textContent.trim()===label);assert.ok(node,label);if(!node.parentElement.open)await act(async()=>node.click());};
 const click=async label=>{await until(()=>button(label)&&!button(label).matches(':disabled'),label);const parents=[];for(let parent=button(label).parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS'&&!parent.open)parents.unshift(parent);for(const parent of parents)await act(async()=>parent.querySelector(':scope > summary').click());await act(async()=>button(label).click());};
 const change=async(label,value)=>{const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node,label);assert.equal(node.matches(':disabled'),false);await act(async()=>{const proto=node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});};
 const ready=async()=>{if(workspace){await until(()=>container.textContent.includes(f.instance.name));await openDetails('业务资料需求与缺口处理');}await until(()=>container.querySelector('[aria-label="资料需求业务"]')&&!container.querySelector('[aria-label="资料需求业务"]').matches(':disabled'));await openDetails('新增资料需求');};
 const fill=async()=>{await change('资料需求业务',f.businessId);await change('资料需求对象',f.entityId);const checkbox=container.querySelector('[aria-label="资料字段：'+f.label+'"]');assert.ok(checkbox);await act(async()=>checkbox.click());await change('资料用途',f.question);};
 try{await run({f,dom,container,mount,unmount,until,button,click,change,ready,fill,openDetails,guard:()=>guard,text:()=>container.textContent,update:async patch=>{props={...props,...patch};await render();}});assert.deepEqual(errors,[]);}
 finally{try{await unmount();assert.deepEqual(errors,[]);}finally{console.error=oldConsoleError;dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}}
}

test('V248 actual lost POST restores the original query outside the recent list while paused and remains readable after another cold entry',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill();h.f.state.mode='lost';await h.click('保存资料需求并核对');await h.until(()=>h.button('只核对原资料需求申请')&&!h.button('只核对原资料需求申请').disabled);
 const raw=h.f.storage.getItem(h.f.prefix+'pending'),p=JSON.parse(raw);assert.equal(h.f.storage.getItem(h.f.prefix+'received:'+p.body.request_id),null);assert.equal(h.f.state.posts.length,1);await h.unmount();h.f.recovery.paused=true;h.f.recovery.hideRecent=true;
 await h.mount();await h.until(()=>h.container.querySelector('[aria-label="原需求找回依据"]')&&!h.container.querySelector('[aria-label="原资料需求申请"]'));
 const detail=h.container.querySelector('[aria-label="已保存的资料需求与原依据"]');assert.ok(detail);assert.match(detail.textContent,/以下只展示历史/);assert.ok(detail.textContent.includes(h.f.question));assert.match(h.text(),/完整保存回执/);assert.equal(h.f.recovery.postAttempts.length,1);assert.equal(h.f.storage.getItem(h.f.prefix+'original:'+p.body.request_id),raw);
 const reads=h.f.recovery.lookups;await h.unmount();await h.mount();await h.until(()=>h.container.querySelector('[aria-label="已保存的资料需求与原依据"]'));assert.equal(h.f.recovery.lookups,reads);assert.equal(h.f.recovery.postAttempts.length,1);assert.ok(h.text().includes(h.f.question));
}));
test('V248 actual entry found false and repeated readonly checks keep the original pending without creating or sending another request',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const p=await h.f.seedOriginal({saved:false}),raw=JSON.stringify(p);h.f.recovery.hideRecent=true;await h.mount();await h.until(()=>h.button('只核对原资料需求申请')&&!h.button('只核对原资料需求申请').disabled);assert.match(h.text(),/结果仍未知/);assert.equal(h.f.recovery.postAttempts.length,0);assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),raw);await h.click('只核对原资料需求申请');await h.until(()=>h.f.recovery.lookups>=2&&!h.button('只核对原资料需求申请').disabled);assert.equal(h.f.recovery.postAttempts.length,0);assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),raw);assert.equal(h.guard()(),true);assert.ok(!h.container.querySelector('[aria-label="原需求找回依据"]'));
}));
test('V248 actual known-query detail has a readonly retry after temporary failure even when paused and outside the recent list',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.f.seedOriginal();h.f.recovery.paused=true;h.f.recovery.hideRecent=true;await h.mount();await h.until(()=>h.container.querySelector('[aria-label="原需求找回依据"]'));await h.unmount();
 h.f.recovery.detailOverride=()=>{throw Object.assign(Error('原详情暂未读回'),{status:503});};await h.mount();await h.until(()=>h.button('重新读取原需求详情')&&!h.button('重新读取原需求详情').disabled);assert.ok(!h.container.querySelector('[aria-label="已保存的资料需求与原依据"]'));
 h.f.recovery.detailOverride=null;const lookups=h.f.recovery.lookups;await h.click('重新读取原需求详情');await h.until(()=>h.container.querySelector('[aria-label="已保存的资料需求与原依据"]'));assert.equal(h.f.recovery.lookups,lookups);assert.equal(h.f.recovery.postAttempts.length,0);
}));
test('V248 actual original request can be found later by a readonly button without replaying a previously unknown POST',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const p=await h.f.seedOriginal({saved:false}),raw=JSON.stringify(p);await h.mount();await h.until(()=>h.button('只核对原资料需求申请')&&!h.button('只核对原资料需求申请').disabled);h.f.state.created.set(p.body.request_id,{body:p.body,id:id(92)});await h.click('只核对原资料需求申请');await h.until(()=>!h.container.querySelector('[aria-label="原资料需求申请"]')&&h.container.querySelector('[aria-label="原需求找回依据"]'));assert.equal(h.f.recovery.postAttempts.length,0);assert.equal(h.f.storage.getItem(h.f.prefix+'original:'+p.body.request_id),raw);assert.ok(h.text().includes(p.body.purpose));
}));
test('V248 actual recovery rejects a mismatched actor proof and retains all original work until a valid readonly result is available',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const p=await h.f.seedOriginal(),raw=JSON.stringify(p);h.f.recovery.hideRecent=true;h.f.recovery.lookupOverride=reply=>({...reply,request:{...reply.request,actor_id:id(99)}});await h.mount();await h.until(()=>h.button('只核对原资料需求申请')&&!h.button('只核对原资料需求申请').disabled);assert.ok(!h.container.querySelector('[aria-label="原需求找回依据"]'));assert.ok(!h.container.querySelector('[aria-label="已保存的资料需求与原依据"]'));assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),raw);h.f.recovery.lookupOverride=null;await h.click('只核对原资料需求申请');await h.until(()=>h.container.querySelector('[aria-label="原需求找回依据"]'));assert.equal(h.f.recovery.postAttempts.length,0);assert.equal(h.f.storage.getItem(h.f.prefix+'original:'+p.body.request_id),raw);
}));
test('V248 actual account change during delayed lookup hides the late original result and never deletes the prior account request',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const p=await h.f.seedOriginal({question:'只属于原账号的待恢复问题'}),raw=JSON.stringify(p);h.f.recovery.hideRecent=true;let release;h.f.recovery.lookupGate=new Promise(resolve=>release=resolve);await h.mount();await h.until(()=>h.f.recovery.lookups===1);h.f.session.setItem('ga-setup-csrf-user',id(99));h.f.state.identity={...h.f.account,user_id:id(99)};await h.update({account:h.f.state.identity});h.f.recovery.lookupGate=null;await act(async()=>release());await h.until(()=>!h.text().includes(p.body.purpose));assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),raw);assert.equal(h.f.storage.getItem(h.f.prefix+'received:'+p.body.request_id),null);assert.equal(h.f.recovery.postAttempts.length,0);
}));
test('V248 actual local receipt failure keeps the readonly retry and saved original recoverable without any POST',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const p=await h.f.seedOriginal(),raw=JSON.stringify(p);h.f.recovery.hideRecent=true;h.f.storage.failSet=key=>key===h.f.prefix+'lookup:'+p.body.request_id;await h.mount();await h.until(()=>h.button('只核对原资料需求申请')&&!h.button('只核对原资料需求申请').disabled);assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),raw);assert.equal(h.guard()(),true);h.f.storage.failSet=null;await h.click('只核对原资料需求申请');await h.until(()=>h.container.querySelector('[aria-label="原需求找回依据"]')&&!h.container.querySelector('[aria-label="原资料需求申请"]'));assert.equal(h.f.recovery.postAttempts.length,0);assert.equal(h.f.storage.getItem(h.f.prefix+'original:'+p.body.request_id),raw);
}));
test('V248 actual original-query recovery leaves a separate current human draft intact and still editable',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const p=await h.f.seedOriginal({formQuestion:'另一份尚未提交的人稿'}),human=h.f.storage.getItem(h.f.prefix+'form');h.f.recovery.hideRecent=true;await h.mount();await h.ready();assert.equal(h.container.querySelector('[aria-label="资料用途"]').value,'另一份尚未提交的人稿');assert.equal(h.f.storage.getItem(h.f.prefix+'form'),human);assert.ok(h.container.querySelector('[aria-label="已保存的资料需求与原依据"]').textContent.includes(p.body.purpose));await h.change('资料用途','恢复原需求后继续修改这份人稿');assert.equal(h.guard()(),true);assert.equal(h.f.recovery.postAttempts.length,0);
}));
test('V248 actual legacy archive recovery names the weaker historical record and preserves inapplicable original controls without inventing a typed receipt',{concurrency:false,timeout:30000},()=>harness(async h=>{
 h.f.state.lookupEvidence='legacy_job_context';const p=await h.f.seedOriginal({bodyOverrides:{source:'archive',confirmation:'unverified_extraction',max_age_hours:48}}),raw=JSON.stringify(p);await h.mount();await h.until(()=>h.container.querySelector('[aria-label="原需求找回依据"]'));assert.match(h.text(),/旧版没有独立保存完整提交回执/);assert.ok(!h.text().includes('已核对这次提交的完整保存回执'));assert.equal(h.f.storage.getItem(h.f.prefix+'original:'+p.body.request_id),raw);assert.ok(!h.container.querySelector('[aria-label="资料有效小时数"]'));assert.equal(h.f.recovery.postAttempts.length,0);assert.ok(!h.text().includes('body_hash'));
}));
test('V248 actual parent Workspace keeps failed human edits protected after recovery and reopens their reliably saved text',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.f.seedOriginal({formQuestion:'父工作区当前人稿'});h.f.recovery.hideRecent=true;await h.mount();await h.ready();assert.equal(h.container.querySelector('[aria-label="资料用途"]').value,'父工作区当前人稿');h.f.storage.failSet=key=>key===h.f.prefix+'form';await h.change('资料用途','找回后仍要保护的未保存修改');let allowed;await act(async()=>{allowed=await h.guard()();});assert.equal(allowed,false);h.f.storage.failSet=null;await h.click('重试保存当前人稿');await act(async()=>{allowed=await h.guard()();});assert.equal(allowed,true);await h.unmount();await h.mount();await h.ready();assert.equal(h.container.querySelector('[aria-label="资料用途"]').value,'找回后仍要保护的未保存修改');assert.equal(h.f.recovery.postAttempts.length,0);
},{workspace:true}));
test('V248 actual viewer can recover their original history while new create and verification controls remain disabled',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.f.seedOriginal();h.f.account.role='viewer';h.f.state.identity.role='viewer';h.f.recovery.paused=true;await h.mount();await h.until(()=>h.container.querySelector('[aria-label="原需求找回依据"]'));assert.match(h.text(),/完整保存回执/);assert.ok(!h.container.querySelector('[aria-label="原资料需求申请"]'));const verify=h.button('重新核对现有资料');assert.ok(verify);assert.equal(verify.disabled,true);assert.equal(h.f.recovery.postAttempts.length,0);
}));
