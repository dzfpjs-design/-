// Actual parent, both actual V198 children and their controllers under ReactDOM.
// Only authenticated HTTP transport is synthetic. This is not browser, PG,
// source authority, actual work execution or independent acceptance evidence.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act,useEffect} from 'react';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {scope,fixture as archiveFixture,copy,digest} from './task-archives-v198-fixture.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const id=n=>'19800000-0000-4000-8000-'+String(n).padStart(12,'0');
const taskPath='/workspace/tasks/'+scope.task_id,workBase='/workspace/work-orders-v198';
const workFlags={execution_completed:false,independent_acceptance:false,world_state_verified:false};
let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'graph-v198-records-parent-'));
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/GraphTaskPanel.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});
  const entry=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(entry);
  const file=join(temporary,'GraphTaskPanel.mjs');await writeFile(file,entry.code);
  return (await import(pathToFileURL(file).href)).default;
 })();return bundle;
}
function task(){
 const output={type:'object',properties:{note:{type:'string',title:'现场核对说明',maxLength:4000}},required:['note'],additionalProperties:false};
 return {id:scope.task_id,workspace_id:scope.workspace_id,version:2,title:'原任务与三份独立人稿',business_name:'对象状态核对',instance_release_number:4,
  workflow_review_mode:'engineering_simulation',workflow_simulation_only:true,
  graph_runtime:{graph_hash:'a'.repeat(64),version:1,cancel_epoch:0,status:'waiting',clock:110,commands:[],ready_human_ids:['human'],activations:{human:{id:'human',node_id:'human-node',version:0,epoch:0,status:'ready',output:null,draft:null}}},
  graph_pending_humans:[{activation_id:'human',node_id:'human-node',source_step_id:'human-step',role:'负责人',actor_id:scope.actor_id,version:0,can_edit:true,output_schema:output,draft:null,required_inputs:[],inputs_available:true,activation_status:'ready',can_complete:true}],
  graph_node_labels:{human:{name:'核对说明',kind:'human',output_schema:output}},graph_jobs:{},graph_waits:[],object_snapshots:[],roles:{负责人:{username:'当前任务负责人'}},
  business:{flow:{steps:[{id:'human-step',name:'核对说明',purpose:'保留原Task人工输入',citations:[]}],citations:[]}},permissions:{can_cancel_task:true}};
}
function workOrder(){
 return {schema:'workflow-work-order-view-v198',id:id(51),batch_id:id(52),parent_task_id:scope.task_id,title:'核对受损样地',description:'记录原任务限定样地的情况。',expected_result:'办理说明与仍待补充事项。',assignee_id:scope.actor_id,assignee_name:'样地核对员',due_at:'2026-10-25T18:00:00+08:00',simulation_only:true,scope:'human_work_orders',status:'assigned',version:1,note:'',updated_at:'2026-10-24T10:00:00+08:00',
  history:[{version:1,status:'assigned',operation:'assigned',note:'',actor_id:scope.actor_id,actor_name:'任务负责人',at:'2026-10-24T10:00:00+08:00',request_id:null}],...workFlags,permissions:{progress:true,submit:true,return:false,cancel:false}};
}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources are forbidden in the synthetic parent DOM');}}
async function harness(run){
 const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-task-parent.invalid/',resources:new NoResources(),virtualConsole:console});
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,archive=archiveFixture(storage);
 const state={task:task(),item:workOrder(),failPrefix:null,callbacks:[],callbackAttempts:[],guardEvents:[],probe:{setup:0,cleanup:0}},calls=[],commands=new Map();
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_name,_options,fn)=>fn()}});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,Event:dom.window.Event,MutationObserver:dom.window.MutationObserver,localStorage:storage,sessionStorage:session,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,fetch:()=>{throw Error('Real HTTP is forbidden');}};
 const previous=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-csrf');
 const originalSet=dom.window.Storage.prototype.setItem;
 dom.window.Storage.prototype.setItem=function(key,value){if(this===storage&&state.failPrefix&&String(key).startsWith(state.failPrefix))throw Error('synthetic selected family storage failure');return originalSet.call(this,key,value);};
 const refreshTask=()=>{state.task.version++;state.task.title='原任务已刷新至第 '+state.task.version+' 版';};
 const api=async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,body:options.body});
  const url=new URL(path,'https://synthetic-task-parent.invalid');
  if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(path===taskPath){
   assert.equal(method,'GET');const value=copy(state.task);
   if(state.deferNextTaskRead){
    state.deferNextTaskRead=false;state.deferredTask=copy(value);
    return new Promise(resolve=>{state.resolveDeferredTask=()=>resolve(value);});
   }
   return value;
  }
  if(url.pathname.startsWith('/task-archives')){
   const value=await archive.api(path,options);if(method==='POST')refreshTask();return value;
  }
  if(url.pathname===workBase){
   assert.equal(method,'GET');assert.equal(url.searchParams.get('parent_task_id'),scope.task_id);
   assert.ok(['assigned','dispatched'].includes(url.searchParams.get('mode')));
   return {schema:'workflow-work-order-list-v198',scope:'human_work_orders',mode:url.searchParams.get('mode'),items:[copy(state.item)],...workFlags};
  }
  if(url.pathname.startsWith(workBase+'/requests/')){
   assert.equal(method,'GET');const nonce=url.pathname.split('/').at(-1),stored=commands.get(nonce);
   assert.ok(stored,'original work order request was persisted by the synthetic server');assert.equal(url.searchParams.get('expected_sha256'),stored.sha);
   return {schema:'workflow-work-order-original-result-v198',request_id:nonce,found:true,body_sha256:stored.sha,result:copy(stored.result)};
  }
  if(url.pathname===workBase+'/'+state.item.id){assert.equal(method,'GET');return copy(state.item);}
  assert.equal(method,'POST');assert.equal(path,workBase+'/'+state.item.id+'/submit');
  const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['expected_version','note','request_id']);assert.equal(body.expected_version,state.item.version);
  const key=Object.keys(storage).find(key=>key.startsWith('ga-task-work-orders-v198:')&&key.endsWith(':outbox'));
  assert.ok(key,'actual child journals before POST');assert.equal(JSON.parse(storage.getItem(key)).raw,options.body);
  const item=state.item;item.version++;item.status='completion_reported';item.note=body.note;item.updated_at='2026-10-24T11:00:00+08:00';item.permissions={progress:false,submit:false,return:false,cancel:false};
  item.history.push({version:item.version,status:item.status,operation:'submit',note:item.note,actor_id:scope.actor_id,actor_name:'样地核对员',at:item.updated_at,request_id:body.request_id});
  const saved=copy(item);delete saved.permissions;
  const result={schema:'workflow-work-order-result-v198',request_id:body.request_id,item:saved,...workFlags};
  commands.set(body.request_id,{sha:await digest(options.body),result:copy(result)});refreshTask();return result;
 };
 const container=document.getElementById('root');let root,registered;
 const register=(guard,old)=>{state.guardEvents.push({guard,old});if(guard)registered=guard;else if(!old||old===registered)registered=null;};
 const onSaved=async value=>{
  // These are assertions at the real parent boundary, not a replacement for
  // either child controller. Child work_order/archive receipts are not Tasks.
  state.callbackAttempts.push(copy(value));
  assert.equal(value.id,scope.task_id);assert.equal(value.workspace_id,scope.workspace_id);
  assert.ok(value.graph_runtime&&Array.isArray(value.graph_pending_humans));
  assert.equal(Object.hasOwn(value,'kind'),false);assert.equal(Object.hasOwn(value,'archive_id'),false);
  assert.equal(value.version,state.task.version);state.callbacks.push(copy(value));return true;
 };
 const until=async(predicate,label)=>{for(let n=0;n<100;n++){if(predicate())return;await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});}assert.deepEqual(errors,[]);assert.ok(predicate(),label);};
 const settle=async()=>{for(let n=0;n<8;n++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 const section=name=>{const node=container.querySelector('[aria-label="'+name+'"]');assert.ok(node,'real child '+name);return node;};
 const button=(label,within=container)=>{const node=[...within.querySelectorAll('button')].find(node=>node.textContent===label||node.textContent.includes(label));assert.ok(node,'actual button '+label);return node;};
 const click=async(label,within)=>{const node=button(label,within);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
 const edit=async(node,value)=>{assert.ok(node,'actual editable form');assert.equal(node.disabled,false);assert.notEqual(node.readOnly,true);const proto=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(proto,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();};
 const checkGuard=async expected=>{let result;await act(async()=>{assert.equal(typeof registered,'function');result=registered();});await settle();assert.equal(result,expected,'parent combines all three real guards');};
 const graphInput=()=>[...container.querySelectorAll('textarea')].find(node=>!node.closest('[aria-label="人工工作单"]')&&!node.closest('[aria-label="任务档案"]'));
 try{
  const {createRoot}=await import('react-dom/client'),Panel=await component();
  function Probe(){useEffect(()=>{state.probe.setup++;return()=>{state.probe.cleanup++;};},[]);return null;}
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Probe),React.createElement(Panel,{task:copy(state.task),api,actor:{user_id:scope.actor_id},workspace:{workspace_id:scope.workspace_id},onSaved,onRegisterGuard:register}))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:e=>errors.push(e),onCaughtError:e=>errors.push(e),onRecoverableError:e=>errors.push(e)});await render();await until(()=>container.textContent.includes('核对受损样地')&&!!container.querySelector('[aria-label="候选说明"]')&&!!graphInput(),'all real forms loaded');};
  const unmount=async()=>{if(root){const old=root;root=null;await act(async()=>old.unmount());await settle();}};
  await mount();await run({dom,container,state,calls,storage,archive,commands,render,mount,unmount,until,settle,section,button,click,edit,checkGuard,graphInput});
  await unmount();assert.equal(registered,null,'parent guard is unregistered after actual cleanup');assert.deepEqual(errors,[],'no ReactDOM/jsdom failures');
 }finally{if(root)await act(async()=>root.unmount());dom.window.Storage.prototype.setItem=originalSet;dom.window.close();for(const [key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
}

test('nested Task mounts both real V198 records and preserves all three guards across StrictMode cleanup and remount',async()=>harness(async h=>{
 assert.ok(h.state.probe.setup>=2&&h.state.probe.cleanup>=1,'StrictMode really replays effects');
 for(const name of ['人工工作单','任务档案'])assert.ok(h.section(name));
 assert.ok(h.calls.some(row=>row.method==='GET'&&row.path.startsWith(workBase+'?')&&row.path.includes('parent_task_id='+scope.task_id)));
 assert.ok(h.calls.some(row=>row.method==='GET'&&row.path.startsWith('/task-archives?')&&row.path.includes('task_id='+scope.task_id)));
 assert.ok(h.graphInput());assert.equal(h.calls.filter(row=>row.method==='POST').length,0);
 await h.click('核对受损样地',h.section('人工工作单'));await h.until(()=>!!h.container.querySelector('[aria-label="工作单办理说明"]'),'work order opens beside graph and archive');
 await h.checkGuard(true);
 const values=[
  ['ga-graph-task:',()=>h.graphInput(),'原Task人稿\n不会被子记录覆盖'],
  ['ga-task-work-orders-v198:',()=>h.container.querySelector('[aria-label="工作单办理说明"]'),'工作单未保护的完整说明'],
  ['ga-task-archives-v198:',()=>h.container.querySelector('[aria-label="候选说明"]'),'档案未保护的完整说明']];
 for(const [prefix,input,value]of values){
  h.state.failPrefix=prefix;await h.edit(input(),value);await h.checkGuard(false);
  assert.equal(input().value,value,'failed persistence retains actual typed text');
  h.state.failPrefix=null;await h.checkGuard(true);
 }
 await h.unmount();await h.mount();await h.click('核对受损样地',h.section('人工工作单'));
 await h.until(()=>!!h.container.querySelector('[aria-label="工作单办理说明"]'),'work order restored');
 for(const [,input,value]of values)assert.equal(input().value,value,'all real controllers restore separate drafts');
 h.state.failPrefix='ga-task-work-orders-v198:';await h.edit(values[1][1](),'新挂载工作单的原稿仍受保护');await h.checkGuard(false);
 h.state.failPrefix=null;await h.checkGuard(true);
 h.state.failPrefix='ga-task-archives-v198:';await h.edit(values[2][1](),'新挂载档案的原稿仍受保护');await h.checkGuard(false);
 h.state.failPrefix=null;await h.checkGuard(true);
 assert.equal(h.calls.filter(row=>row.method==='POST').length,0,'guards and restore never submit');
}));

test('both real child save callbacks re-read Task and pass only a full fresh Task DTO to the outer parent',async()=>harness(async h=>{
 const human='父流程仍未提交的人稿\n两次子记录保存不得覆盖';await h.edit(h.graphInput(),human);
 await h.click('核对受损样地',h.section('人工工作单'));await h.until(()=>!!h.container.querySelector('[aria-label="工作单办理说明"]'),'work detail ready');
 await h.edit(h.container.querySelector('[aria-label="工作单办理说明"]'),'只保存真实办理说明，不表示已验收');
 const before=h.calls.length;await h.click('提交办理结果',h.section('人工工作单'));
 await h.until(()=>h.state.callbacks.length===1,'work order save reaches actual outer Task callback');
 const workPost=h.calls.findIndex((row,index)=>index>=before&&row.method==='POST'&&row.path===workBase+'/'+id(51)+'/submit');
 assert.ok(workPost>=before);assert.ok(h.calls.slice(workPost+1).some(row=>row.method==='GET'&&row.path===taskPath),'work order receipt is followed by fresh Task GET');
 assert.equal(h.state.callbacks[0].version,3);assert.ok(h.container.textContent.includes('原任务已刷新至第 3 版'));assert.equal(h.graphInput().value,human);
 await h.edit(h.container.querySelector('[aria-label="候选说明"]'),'档案修订与工作单说明分别保存\n原来源不改');
 const archiveBefore=h.calls.length;await h.click('保存修订为新版本',h.section('任务档案'));
 await h.until(()=>h.state.callbacks.length===2,'archive receipt reaches same full Task callback');
 const archivePost=h.calls.findIndex((row,index)=>index>=archiveBefore&&row.method==='POST'&&row.path.startsWith('/task-archives/')&&row.path.endsWith('/revisions'));
 assert.ok(archivePost>=archiveBefore);assert.ok(h.calls.slice(archivePost+1).some(row=>row.method==='GET'&&row.path===taskPath),'archive receipt is followed by fresh Task GET');
 assert.deepEqual(h.state.callbacks.map(row=>row.version),[3,4]);assert.ok(h.container.textContent.includes('原任务已刷新至第 4 版'));
 assert.equal(h.graphInput().value,human);assert.equal(h.container.querySelector('[aria-label="工作单办理说明"]').value,'只保存真实办理说明，不表示已验收');
 assert.equal(h.archive.versions.length,2);assert.equal(h.commands.size,1);assert.equal(h.calls.filter(row=>row.method==='POST').length,2);
 assert.ok(h.calls.some(row=>row.path.startsWith(workBase+'/requests/')));assert.ok(h.calls.some(row=>row.path.startsWith('/task-archives/requests/original?')));
 await h.checkGuard(true);
}));

test('a late child-save Task GET cannot overwrite a newer same-scope Task or forward its stale DTO',async()=>harness(async h=>{
 const human='在较新Task中仍须保留的原人稿';await h.edit(h.graphInput(),human);
 await h.click('核对受损样地',h.section('人工工作单'));await h.until(()=>!!h.container.querySelector('[aria-label="工作单办理说明"]'),'work detail ready');
 const note='工作单原结果已保存，父Task读取暂时延迟';await h.edit(h.container.querySelector('[aria-label="工作单办理说明"]'),note);
 h.state.deferNextTaskRead=true;await h.click('提交办理结果',h.section('人工工作单'));
 await h.until(()=>typeof h.state.resolveDeferredTask==='function','actual parent fresh GET is deferred');
 assert.equal(h.state.deferredTask.version,3);assert.equal(h.state.callbackAttempts.length,0);
 h.state.task.version=4;h.state.task.title='较新Task第 4 版，旧回应不得覆盖';await h.render();
 await h.until(()=>h.container.textContent.includes('较新Task第 4 版'),'actual parent ingests newer Task props');
 await act(async()=>{h.state.resolveDeferredTask();await new Promise(resolve=>setImmediate(resolve));});await h.settle();
 assert.equal(h.state.callbackAttempts.length,0,'rejected stale Task must never reach the outer onSaved, even if that callback would reject it');
 assert.ok(h.container.textContent.includes('较新Task第 4 版'));assert.equal(h.container.textContent.includes('原任务已刷新至第 3 版'),false);
 assert.equal(h.graphInput().value,human);assert.equal(h.container.querySelector('[aria-label="工作单办理说明"]').value,note);
 assert.equal(h.commands.size,1);assert.equal(h.calls.filter(row=>row.method==='POST').length,1,'late GET cannot cause another command');
 await h.checkGuard(true);
}));
