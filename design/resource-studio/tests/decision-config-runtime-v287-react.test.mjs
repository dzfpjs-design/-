// Real ReactDOM/StrictMode; synthetic read-only transports, no native browser or API.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act,useState} from 'react';
import {build} from 'vite';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {decisionReviewContextFixture} from './decision-review-context-v284-fixture.mjs';
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),temporary=[],compiled=new Map();
const id=n=>'28700000-0000-4000-8000-'+String(n).padStart(12,'0');
after(async()=>{for(const path of temporary)await rm(path,{recursive:true,force:true});});
async function component(name){
 if(!compiled.has(name))compiled.set(name,(async()=>{
  const dir=await mkdtemp(join(tmpdir(),'decision-config-react-'));temporary.push(dir);
  const result=await build({root:project,configFile:false,envDir:dir,cacheDir:join(dir,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/'+name+'.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:n=>n==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:n}}}});
  const path=join(dir,name+'.mjs');await writeFile(path,result.output.find(x=>x.type==='chunk'&&x.isEntry).code);return(await import(pathToFileURL(path).href)).default;
 })());return compiled.get(name);
}
async function harness(){
 assert.equal(process.env.NODE_ENV,'development');
 const errors=[],external=[];const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e));
 class NoResources extends ResourceLoader{fetch(url){external.push(url);throw Error('Offline test cannot fetch resources');}}
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://synthetic-config.invalid/',resources:new NoResources(),virtualConsole:vc});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,MutationObserver:dom.window.MutationObserver,sessionStorage:dom.window.sessionStorage,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true,addEventListener:dom.window.addEventListener.bind(dom.window),removeEventListener:dom.window.removeEventListener.bind(dom.window),fetch:()=>{throw Error('No ambient fetch');}};
 const prior=new Map(Object.keys(globals).map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const[k,value]of Object.entries(globals))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value});
 const {createRoot}=await import('react-dom/client'),root=createRoot(document.querySelector('#root'));
 return {dom,root,errors,external,async render(node){await act(async()=>{root.render(React.createElement(React.StrictMode,null,node));await new Promise(r=>setTimeout(r,0));});},async close(){await act(async()=>root.unmount());dom.window.close();for(const[k,descriptor]of prior){if(descriptor)Object.defineProperty(globalThis,k,descriptor);else delete globalThis[k];}assert.deepEqual(errors,[]);assert.deepEqual(external,[]);}};
}
test('decision review shows original evidence, retries only GET and hides results on identity change',async()=>{
 const Component=await component('BusinessDecisionReviewV284'),h=await harness(),value=decisionReviewContextFixture(),calls=[];
 sessionStorage.setItem('ga-setup-csrf-user',id(1));sessionStorage.setItem('ga-setup-csrf-workspace',id(2));
 let fail=true;const requestJSON=async(path,options)=>{calls.push({path,options});if(fail){fail=false;throw Object.assign(Error('temporary'),{status:503});}return structuredClone(value);};
 try{
  await h.render(React.createElement(Component,{value:value.result,taskId:value.task_id,activationId:value.activation_id,readerId:id(1),workspaceId:id(2),requestJSON}));
  assert.match(document.body.textContent,/读取失败/);const retry=[...document.querySelectorAll('button')].find(x=>x.textContent==='重新读取原建议与依据');assert.ok(retry);
  await act(async()=>{retry.click();await new Promise(r=>setTimeout(r,0));});
  assert.match(document.body.textContent,/逐项方案核对/);assert.match(document.body.textContent,/温度/);assert.match(document.body.textContent,/模拟资料流程/);
  assert.ok(calls.length>=2);assert.ok(calls.every(c=>c.options.method==='GET'&&c.path.includes('decision-review-context-v284?activation_id=')));
  assert.doesNotMatch(document.body.textContent,/28400000|invocation_id|input_hash|candidate_2/);
  sessionStorage.setItem('ga-setup-csrf-user',id(3));await act(async()=>window.dispatchEvent(new window.Event('focus')));
  assert.match(document.body.textContent,/账号或工作区已变化/);assert.doesNotMatch(document.body.textContent,/逐项方案核对/);
 }finally{await h.close();}
});
test('policy selector never chooses for the user; explicit empty and original selection survive GET retry',async()=>{
 const Component=await component('MonitorPolicyPinInputV287'),h=await harness(),calls=[],changes=[],records=[];
 const scope={actor_id:id(1),workspace_id:id(2),instance_id:id(3),release_id:id(4),business_id:'weather',configuration_hash:'a'.repeat(64)};
 const selectedObjects=[id(5)],field={key:'policy_pin',label:'自动补充观测策略',required:true,nullable:true};
 const response={schema:'monitor-policy-options-v287',instance_id:scope.instance_id,release_id:scope.release_id,entity_ids:selectedObjects,options:[{policy_id:id(6),approved_hash:'b'.repeat(64),title:'温湿度观测',fields:[{key:'temperature_c',label:'温度'}],valid_until:'2099-11-01T12:00:00Z',scope_label:'本次已选对象'}],can_dispatch:false};
 const requestJSON=async(path,options)=>{calls.push({path,options});return structuredClone(response);};
 function Wrapper(){const[value,setValue]=useState({mode:'unset',raw:''});return React.createElement(Component,{field,value,scope,selectedObjects,requestRole:'operator',requestJSON,onChange:v=>{changes.push(v);setValue(v);},onOptions:r=>records.push(r)});}
 try{
  await h.render(React.createElement(Wrapper));let select=document.querySelector('select');assert.equal(select.value,'');assert.equal(changes.length,0);assert.equal(select.options.length,3);
  await act(async()=>{select.value='none';select.dispatchEvent(new window.Event('change',{bubbles:true}));});
  assert.equal(document.querySelector('select').value,'none');assert.deepEqual(changes.at(-1),{mode:'null',raw:''});assert.match(document.body.textContent,/已明确选择不自动补充观测/);
  await act(async()=>{select=document.querySelector('select');select.value='option:0';select.dispatchEvent(new window.Event('change',{bubbles:true}));});
  assert.match(changes.at(-1).raw,/approved_hash/);assert.equal(document.querySelector('select').value,'option:0');const count=changes.length;
  await act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='重新读取可用策略').click();await new Promise(r=>setTimeout(r,0));});
  assert.equal(changes.length,count);assert.equal(document.querySelector('select').value,'option:0');assert.ok(records.some(r=>r.value?.options?.length===1));
  assert.ok(calls.every(c=>c.options.method==='GET'));assert.doesNotMatch(document.body.textContent,/28700000|approved_hash|bbbbbbbb/);
 }finally{await h.close();}
});
