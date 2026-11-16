// Actual installed React DOM/StrictMode, synthetic transport, no browser/network.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act,useState} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {cloneTyped} from '../src/typed-numbers-v132.mjs';
import {graphWireParseCanonical,graphWireCanonical,graphWireDigest} from '../src/full-semantic-graph-wire.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function components(){
 if(!bundle)bundle=(async()=>{temp=await mkdtemp(join(tmpdir(),'typed-graph-react-'));
  const entry=join(temp,'entry.mjs');await writeFile(entry,'export {default as Panel} from '+JSON.stringify(join(project,'src/GraphTaskPanel.jsx'))+';\nexport {default as Inputs} from '+JSON.stringify(join(project,'src/GraphTaskInputs.jsx'))+';\n');
  const output=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=output.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const file=join(temp,'typed-components.mjs');await writeFile(file,chunk.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
const scope={actor_id:'13200000-0000-4000-8000-000000000001',workspace_id:'13200000-0000-4000-8000-000000000002',task_id:'13200000-0000-4000-8000-000000000003'};
const schema={type:'object',properties:{reading:{type:'number',title:'核查数值'}},required:['reading'],additionalProperties:false};
const task=()=>({id:scope.task_id,workspace_id:scope.workspace_id,version:1,title:'原数值核查任务',business_name:'合成数值流程',instance_release_number:1,
 graph_runtime:{graph_hash:'a'.repeat(64),version:1,cancel_epoch:0,status:'running',commands:[],activations:{human:{id:'human',node_id:'a',status:'ready',version:0,epoch:0,draft:null,output:null}}},
 graph_pending_humans:[{activation_id:'human',node_id:'a',actor_id:scope.actor_id,version:0,can_edit:true,output_schema:schema,draft:null}],graph_jobs:{},graph_waits:[],graph_node_labels:{a:{name:'核对原数值',output_schema:schema}},business:{flow:{steps:[]}},permissions:{can_cancel_task:true},object_snapshots:[]});
class NoResources extends ResourceLoader{fetch(){throw Error('External resource access forbidden');}}
async function harness(run,{inputs=false}={}){
 const errors=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://typed-graph.invalid/',resources:new NoResources(),virtualConsole});
 const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};
 const descriptors=Object.fromEntries(Object.keys(values).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const [key,value]of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_key,_options,fn)=>fn()}});
 const storage=dom.window.localStorage,session=dom.window.sessionStorage;session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-csrf');
 const container=dom.window.document.getElementById('root'),state={task:task(),receipt:null,lost:false,notSent:false,quota:false},calls=[];let root,guard,inputDraft=null;
 const prototype=dom.window.Storage.prototype,originalSet=prototype.setItem;prototype.setItem=function(key,value){if(state.quota&&this===storage)throw Error('合成本机存储配额不足');return originalSet.call(this,key,value);};
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],scope.workspace_id);
  if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(path.includes('/graph/requests/'))return {schema:'workflow-graph-request-recovery-v1',found:!!state.receipt,request:cloneTyped(state.receipt),task:cloneTyped(state.task)};
  if(path.endsWith('/graph/command-hash')){
   const request=graphWireParseCanonical(options.body),command=request.command;
   const body_hash=await graphWireDigest({activation_id:command.activation_id,output:command.output,actor_id:scope.actor_id,expected_version:command.version,cancel_epoch:command.cancel_epoch},{crypto:webcrypto});
   const value={schema:'workflow-graph-command-hash-v1',operation:'complete',actor_id:scope.actor_id,workspace_id:scope.workspace_id,task_id:scope.task_id,graph_hash:'a'.repeat(64),command,body_hash},canonical=graphWireCanonical(value);
   return {schema:'report-full-semantic-graph-canonical-wire-v1',canonical,value:JSON.parse(canonical)};
  }
  if(options.method==='POST'){
   assert.ok(path.endsWith('/graph/complete'));const outbox=Object.keys(storage).find(key=>key.endsWith(':outbox'));assert.ok(outbox);const pending=JSON.parse(storage.getItem(outbox));assert.equal(pending.exact_body,options.body);
   if(state.notSent)throw Error('合成发送结果未知');const command=graphWireParseCanonical(options.body);
   state.receipt={request_id:command.request_id,op:'complete',actor_id:scope.actor_id,body_hash:pending.body_hash};state.task.graph_runtime.commands.push(state.receipt);
   Object.assign(state.task.graph_runtime.activations.human,{status:'completed',version:1,output:cloneTyped(command.output)});state.task.graph_pending_humans=[];state.task.version++;state.task.graph_runtime.version++;
   if(state.lost)throw Error('合成保存后响应丢失');return cloneTyped(state.task);
  }
  assert.equal(path,'/workspace/tasks/'+scope.task_id);return cloneTyped(state.task);
 };
 const settle=async()=>{for(let i=0;i<12;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 try{
  const {createRoot}=await import('react-dom/client'),{Panel,Inputs}=await components();
  function InputForm(){const [draft,setDraft]=useState(null);return React.createElement(Inputs,{metadata:{schema:'workflow-graph-task-inputs-v1',mode:'graph',instance_id:'instance',release_id:'release',business_id:'business',configuration_hash:'b'.repeat(64),hash:'c'.repeat(64),graph_hash:'d'.repeat(64),binding_hash:'e'.repeat(64),execution_granted:false,input_schema:schema,user_input_schema:schema,entity_fields:[],system_fields:[]},scope:{actor_id:scope.actor_id,workspace_id:scope.workspace_id,instance_id:'instance',release_id:'release',business_id:'business',configuration_hash:'b'.repeat(64)},draft,onChange:value=>{inputDraft=value;setDraft(value);}});}
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await act(async()=>root.render(React.createElement(React.StrictMode,null,inputs?React.createElement(InputForm):React.createElement(Panel,{task:cloneTyped(state.task),api,actor:{id:scope.actor_id},workspace:scope.workspace_id,onRegisterGuard:value=>{guard=value;},onSaved:async()=>true}))));await settle();};
  const unmount=async()=>{if(root){const current=root;root=null;await act(async()=>current.unmount());await settle();}};
  const click=async label=>{const node=[...container.querySelectorAll('button')].find(value=>value.textContent===label);assert.ok(node,'actual button '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const edit=async raw=>{const node=container.querySelector('input');assert.ok(node);assert.equal(node.type,'text');assert.equal(node.inputMode,'decimal');const setter=Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set;await act(async()=>{setter.call(node,raw);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();assert.equal(container.querySelector('input').value,raw);};
  await run({dom,container,storage,state,calls,mount,unmount,click,edit,settle,get guard(){return guard;},get inputDraft(){return inputDraft;}});await unmount();assert.deepEqual(errors,[]);
 }finally{prototype.setItem=originalSet;if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,descriptor]of Object.entries(descriptors)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
}

test('actual Task input form retains incomplete sign and trailing decimal text without normalization',()=>harness(async h=>{
 await h.mount();for(const raw of ['-','1.','-0.0','1e21']){await h.edit(raw);assert.equal(h.inputDraft.values.reading.raw,raw);}
 assert.equal(h.calls.length,0,'input form itself performs no API request');
},{inputs:true}));

for(const raw of ['-0.0','1e21'])test('actual human '+raw+' survives unknown completion, remount and original-only lookup',()=>harness(async h=>{
 await h.mount();await h.edit('-');await h.click('保存结果并完成此待办');assert.equal(h.calls.filter(c=>c.method==='POST').length,0);
 await h.edit(raw);h.state.lost=true;await h.click('保存结果并完成此待办');const key=Object.keys(h.storage).find(k=>k.endsWith(':outbox')),original=h.storage.getItem(key);assert.ok(original);
 assert.equal(JSON.parse(original).schema,'graph-task-outbox-v3');await h.unmount();await h.mount();assert.equal(h.storage.getItem(key),original);
 const before=h.calls.length;await h.click('只核对原请求是否已保存');assert.equal(h.storage.getItem(key),null);assert.ok(h.calls.slice(before).every(c=>c.method==='GET'));
 assert.equal(h.calls.filter(c=>c.path.endsWith('/graph/complete')).length,1);assert.ok(h.container.textContent.includes(raw==='-0.0'?'-0.0':'1e+21'));
}));

test('actual human negative probe exposes explicit same-byte resend and never re-prepares it',()=>harness(async h=>{
 await h.mount();await h.edit('-0.0');h.state.notSent=true;await h.click('保存结果并完成此待办');await h.click('只核对原请求是否已保存');
 const original=h.calls.find(c=>c.path.endsWith('/graph/complete')).body;h.state.notSent=false;await h.click('按原内容重试提交');
 assert.deepEqual(h.calls.filter(c=>c.path.endsWith('/graph/complete')).map(c=>c.body),[original,original]);assert.equal(h.calls.filter(c=>c.path.endsWith('/command-hash')).length,1);
}));

test('actual typed human quota failure leaves raw input and beforeunload guard until storage recovers',()=>harness(async h=>{
 await h.mount();h.state.quota=true;await h.edit('1.');await act(async()=>assert.equal(h.guard(),false));
 const event=new h.dom.window.Event('beforeunload',{cancelable:true});await act(async()=>h.dom.window.dispatchEvent(event));assert.equal(event.defaultPrevented,true);
 await h.click('保存结果并完成此待办');assert.equal(h.calls.filter(c=>c.method==='POST').length,0);assert.equal(h.container.querySelector('input').value,'1.');
 h.state.quota=false;await act(async()=>assert.equal(h.guard(),true));await h.settle();assert.equal(h.container.querySelector('input').value,'1.');await h.unmount();await h.mount();assert.equal(h.container.querySelector('input').value,'1.');
}));
