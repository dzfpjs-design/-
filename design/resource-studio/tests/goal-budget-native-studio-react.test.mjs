// Mounted real React components and runtime-api, synthetic transport only.
// No browser, model, backend, fixture file, component or hook replacement.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act,useLayoutEffect} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {defaultNativeGoal} from '../src/native-goal-contract.mjs';
import {typedRequestJSON} from '../src/typed-numbers-v132.mjs';
import {queryMetadata} from './native-query-fixtures.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{temp=await mkdtemp(join(tmpdir(),'native-goal-typed-react-'));const entry=join(temp,'entry.mjs');
  await writeFile(entry,'export {default as Studio} from '+JSON.stringify(join(project,'src/NativeGoalStudio.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\n');
  const result=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const file=join(temp,'studio.mjs');await writeFile(file,chunk.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
const account={user_id:'13210000-0000-4000-8000-000000000001',workspace_id:'13210000-0000-4000-8000-000000000002',username:'synthetic-operator',role:'operator'};
const task={id:'13210000-0000-4000-8000-000000000003',title:'原数值目标任务',version:3};
const legacyKey=id=>'ga-native-goal:'+account.workspace_id+':'+account.username+':'+id;
const oldForm=()=>({...defaultNativeGoal('仅当前账号可见的原人工目标稿'),fields:['reading'],targetField:'reading',targetValue:'-0.0',reason:'明确核对原数值'});
const goal=id=>({...queryMetadata,task_id:id,version:3,task_status:'workflow_waiting',can_manage:true,can_stop:false,goal_confirmed:true,goal:{purpose:'合成目标',criteria:[],required_fields:['reading'],observation_mode:'existing_only',evidence:{source:'archive',minimum_verification:'recorded'}},fields:{reading:'核查数值'},field_kinds:{reading:'number'},entity_ids:['original-object'],field_units:{reading:null},goal_tools:[],relation_types:[],check:null,check_worker:null,acceptance:null,cancelled:null,scope_error:null});
const copy=value=>structuredClone(value);
class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden');}}
const locks=()=>{const tails=new Map();return {request(name,options,fn){assert.equal(options.mode,'exclusive');const previous=tails.get(name)||Promise.resolve(),next=previous.then(fn);tails.set(name,next.catch(()=>{}));return next;}};};
async function harness(run){
 const errors=[],violations=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://native-goal-typed.invalid/',pretendToBeVisual:true,resources:new NoResources(),virtualConsole});
 const backing=dom.window.localStorage,session=dom.window.sessionStorage,state={me:copy(account),task:copy(task),goal:goal(task.id),quota:false,meGate:null,lost:false,visibility:'visible',exportRaw:null},calls=[],blobs=[],commits=[];
 const storage={get length(){return backing.length;},key:i=>backing.key(i),getItem:key=>backing.getItem(key),removeItem:key=>backing.removeItem(key),setItem(key,value){if(state.quota&&key.includes(':working:'))throw new dom.window.DOMException('Synthetic quota full','QuotaExceededError');backing.setItem(key,value);}};
 const original=JSON.stringify({form:oldForm(),pending:null});storage.setItem(legacyKey(task.id),original);
 session.setItem('ga-setup-csrf','synthetic-goal-csrf');session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 Object.defineProperty(dom.window,'localStorage',{configurable:true,value:storage});Object.defineProperty(dom.window.document,'visibilityState',{configurable:true,get:()=>state.visibility});Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 // Match Python JSON's actual signed-zero response bytes. JSON.stringify
 // would erase -0 before the real runtime-api Response.json() can decode it.
 const respond=(value,status=200)=>{const raw=typedRequestJSON(value);if(value===state.goal)state.lastGoalResponse=raw;return new Response(raw,{status,headers:{'Content-Type':'application/json'}});};
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET',path=url.pathname+url.search;calls.push({path,method,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin);const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),'synthetic-goal-csrf');
   if(path==='/api/me'){assert.equal(method,'GET');const identity=copy(state.me),gate=state.meGate;if(gate)await gate.promise;return respond(identity);}
   const base='/api/workspace/tasks/'+state.task.id;
   if(path===base+'/goal/export'){assert.equal(method,'GET');assert.ok(state.exportRaw);return new Response(state.exportRaw,{headers:{'Content-Type':'application/json'}});}
   if(path===base+'/goal'&&method==='GET')return respond(state.goal);
   if(path===base+'/goal/budget-display'&&method==='GET')return respond({task_id:state.task.id,calls:1,steps:3,observed_at:'2026-11-23T12:00:00Z',basis:'saved_snapshot'});
   if(path===base+'/goal'&&method==='POST'){
    const outbox=Array.from({length:storage.length},(_,i)=>storage.key(i)).find(key=>key.startsWith('ga-native-goal-v132:')&&key.endsWith(':outbox'));
    assert.ok(outbox,'original typed request is durable before POST');assert.equal(JSON.parse(storage.getItem(outbox)).exact_body,options.body);
    if(state.lost)throw new TypeError('Synthetic request response lost');throw Error('This test never authorizes a successful formal goal mutation');
   }
   if(path===base+'/goal/requests/recover'&&method==='POST')return respond({version:state.goal.version,saved:false,abandoned:false});
   const ancillary=new Set([base+'/query-proposals',base+'/supporting-queries?limit=20',base+'/intelligent-plan',base+'/goal/acquisitions',base+'/experience']);
   if(ancillary.has(path)&&method==='GET')return respond({detail:'本合成传输不提供此独立资料清单'},503);
   if(method==='GET')return respond({detail:'合成测试未提供独立资料'},503);throw Error('Unexpected synthetic route '+method+' '+path);
  }catch(error){if(!(state.lost&&method==='POST'&&path.endsWith('/goal')&&error instanceof TypeError))violations.push(error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},descriptors=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 const container=dom.window.document.getElementById('root'),gates=[];let root,guard;
 const createURL=Object.getOwnPropertyDescriptor(URL,'createObjectURL'),revokeURL=Object.getOwnPropertyDescriptor(URL,'revokeObjectURL'),anchorClick=dom.window.HTMLAnchorElement.prototype.click;
 Object.defineProperty(URL,'createObjectURL',{configurable:true,value:blob=>{blobs.push(blob);return 'blob:synthetic-goal';}});Object.defineProperty(URL,'revokeObjectURL',{configurable:true,value:()=>{}});dom.window.HTMLAnchorElement.prototype.click=function(){};
 const settle=async()=>{for(let i=0;i<16;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 const gate=()=>{let release;const value={promise:new Promise(resolve=>{release=resolve;}),release:()=>release()};gates.push(value);return value;};
 try{
  const {createRoot}=await import('react-dom/client'),{Studio,setSession}=await component();setSession('synthetic-goal-csrf',account.workspace_id,account.user_id,account.username);
  function Btn({children,primary,ghost,icon,...props}){return React.createElement('button',{type:'button',...props},children);}
  function Probe(){useLayoutEffect(()=>{commits.push({task_id:state.task.id,text:container.textContent,values:[...container.querySelectorAll('input,textarea')].map(node=>node.value)});},[state.task.id]);return null;}
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Studio,{task:copy(state.task),account,Btn,locked:false,onChanged:async()=>{},onRegisterGuard:value=>{guard=value;}}),React.createElement(Probe))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const unmount=async()=>{if(root){const current=root;root=null;await act(async()=>current.unmount());await settle();}};
  const click=async label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(node,'actual button '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const edit=async(label,value)=>{const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node,label);const prototype=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype,setter=Object.getOwnPropertyDescriptor(prototype,'value').set;await act(async()=>{setter.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();assert.equal(container.querySelector('[aria-label="'+label+'"]').value,value);};
  const snapshots=()=>Array.from({length:storage.length},(_,i)=>storage.key(i)).sort().map(key=>[key,storage.getItem(key)]),contains=text=>container.textContent.includes(text)||[...container.querySelectorAll('input,textarea')].some(node=>node.value.includes(text));
  await run({dom,container,state,storage,calls,blobs,commits,original,mount,unmount,render,settle,gate,click,edit,snapshots,contains,get guard(){return guard;}});await unmount();assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{gates.forEach(item=>item.release());if(root)await act(async()=>root.unmount());if(createURL)Object.defineProperty(URL,'createObjectURL',createURL);else delete URL.createObjectURL;if(revokeURL)Object.defineProperty(URL,'revokeObjectURL',revokeURL);else delete URL.revokeObjectURL;dom.window.HTMLAnchorElement.prototype.click=anchorClick;dom.window.close();for(const [key,descriptor]of Object.entries(descriptors)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
}


test('confirmed goal hides budget and keeps its journal when account identity changes',()=>harness(async h=>{
 await h.mount();await h.settle();
 assert.ok(h.container.querySelector('[aria-label="目标调用与步骤计数"]'));
 assert.ok(h.container.textContent.includes('任务工具调用计数：1 次'));
 const before=h.snapshots();h.state.me={...account,user_id:'13210000-0000-4000-8000-000000000099'};
 await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();
 assert.equal(h.container.querySelector('[aria-label="目标调用与步骤计数"]'),null);
 assert.deepEqual(h.snapshots(),before);
}));
