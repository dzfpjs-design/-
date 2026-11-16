import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';

// Actual bundled component and ReactDOM; public synthetic source DTOs only.
// This does not establish backend binding, model quality or browser acceptance.
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),copy=value=>structuredClone(value);
let directory,compiled;
after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  directory=await mkdtemp(join(tmpdir(),'business-flow-causal-evidence-react-'));
  const previous=process.env.NODE_ENV;let result;
  try{
   process.env.NODE_ENV='development';
   result=await build({root:project,configFile:false,envDir:directory,cacheDir:join(directory,'cache'),mode:'test',logLevel:'silent',
    define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/BusinessFlowCausalEvidence.jsx'),write:false,minify:false,
     rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});
  }finally{if(previous===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=previous;}
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const target=join(directory,'CausalEvidence.mjs');await writeFile(target,chunk.code);return (await import(pathToFileURL(target).href)).default;
 })();
 return compiled;
}

async function browser(){
 assert.equal(process.env.NODE_ENV,'development','use the same development React runtime');
 const errors=[],network=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 class NoResources extends ResourceLoader{fetch(url){network.push(String(url));throw Error('external resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-causal-evidence.invalid/',resources:new NoResources(),virtualConsole});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,IS_REACT_ACT_ENVIRONMENT:true,fetch:url=>{network.push(String(url));throw Error('network forbidden');}};
 const originals=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const {createRoot}=await import('react-dom/client'),container=dom.window.document.getElementById('root'),root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)}),Component=await component();
 const assertClean=()=>assert.deepEqual(errors.map(error=>error?.stack||String(error)),[]);
 async function render(props){await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Component,props))));assertClean();}
 async function close(){try{await act(async()=>root.unmount());assertClean();assert.deepEqual(network,[]);}finally{dom.window.close();for(const[key,descriptor]of originals){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}}
 return {dom,container,render,close,text:()=>container.textContent||''};
}
const nodeId='hidden-node-pointer-190',A='w:p1/s1',B='o:p2/s1';
function sourceView(){return {schema:'business-flow-causal-source-view-v190',current_draft_matches_original:true,original_candidate_only:true,runtime_entry_binding_verified:false,
 internal_hash:'do-not-display-causal-hash',rows:[
  {flow_index:0,flow_name:'异常核验',node_id:nodeId,step_name:'核对异常',input_name:'观测汇总资料',sources:[{kind:'step',step_name:'汇总观测',output_name:'观测汇总',actor:'data_agent',role:'资料管理员'},{kind:'entry',label:'流程触发或配置需提供的资料（待绑定）'}],citations:[A,B]},
  {flow_index:0,flow_name:'异常核验',node_id:'other-hidden-node',step_name:'核对异常',input_name:'同名输入不可猜配',sources:[{kind:'step',step_name:'错误同名步骤不得显示',output_name:'错误输出不得显示',actor:'business_agent',role:'核验人'}],citations:[]},
  {flow_index:1,flow_name:'另一个流程',node_id:nodeId,step_name:'其他流程同号步骤',input_name:'其他流程输入不得显示',sources:[],citations:[]},
 ]};}

test('real source card distinguishes alternative step/entry sources and sends only the exact citation on click',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),view=sourceView(),before=copy(view),citations=[];
 try{
  await h.render({view,flowIndex:0,nodeId,onCitation:ref=>citations.push(ref)});for(const phrase of ['资料来源核对','观测汇总资料','汇总观测','观测汇总','数据管理智能体','资料管理员','备选来源（或）','并非全部已经具备','流程入口资料：待绑定','尚未证明该资料真实存在','不代表资料已经具备'])assert.ok(h.text().includes(phrase),phrase);
  for(const hidden of [nodeId,'other-hidden-node','do-not-display-causal-hash',A,B,'错误同名步骤不得显示','其他流程输入不得显示','node_id','flow_index'])assert.equal(h.text().includes(hidden),false,'never display internal/other-node data: '+hidden);
  const buttons=[...h.container.querySelectorAll('button')];assert.equal(buttons.length,2);assert.equal(buttons[0].textContent,'查看原文依据 1');assert.equal(buttons[0].disabled,false);await act(async()=>buttons[0].click());await act(async()=>buttons[1].click());assert.deepEqual(citations,[A,B]);assert.deepEqual(view,before);assert.equal(h.container.querySelectorAll('input,textarea,select').length,0,'source projection is read-only');
 }finally{await h.close();}
});

test('real changed-draft warnings retain original bindings and refuse to guess a newly edited node by its name',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),view=sourceView(),before=copy(view);
 try{
  await h.render({view,flowIndex:0,nodeId,currentChanged:true});assert.ok(h.container.querySelector('[role="alert"]')?.textContent.includes('本稿已修改'));assert.ok(h.text().includes('仅对应原 AI 候选'));assert.ok(h.text().includes('不能当作当前稿的来源核验结果'));assert.ok(h.text().includes('原候选所需资料：观测汇总资料'));assert.ok(h.text().includes('汇总观测'));assert.deepEqual(view,before);
  const changed=copy(view);changed.current_draft_matches_original=false;await h.render({view:changed,flowIndex:0,nodeId,currentChanged:false});assert.ok(h.container.querySelector('[role="alert"]')?.textContent.includes('本稿已修改'),'server mismatch also warns independently of local dirty flag');assert.ok(h.text().includes('原候选所需资料：观测汇总资料'));
  await h.render({view:changed,flowIndex:0,nodeId:'new-edited-node-pointer',currentChanged:true});assert.ok(h.text().includes('本稿已修改'));assert.ok(h.text().includes('没有对应的原候选记录'));assert.ok(h.text().includes('不会按相同名称猜配'));assert.equal(h.text().includes('汇总观测'),false);assert.equal(h.text().includes('错误同名步骤不得显示'),false);assert.equal(h.container.querySelectorAll('button').length,0);assert.deepEqual(view,before);
 }finally{await h.close();}
});

test('real card handles absent/invalid/empty views and exact node scope without exposing JSON, pointers or hashes',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),view=sourceView();
 try{
  await h.render({view:null,flowIndex:0,nodeId});assert.equal(h.text(),'');assert.equal(h.container.querySelector('section'),null);
  await h.render({view:{...copy(view),schema:'wrong-source-view'},flowIndex:0,nodeId});assert.ok(h.text().includes('暂不能完整核对'));assert.equal(h.text().includes('观测汇总资料'),false);
  await h.render({view:{...copy(view),runtime_entry_binding_verified:true},flowIndex:0,nodeId});assert.ok(h.text().includes('暂不能完整核对'),'a contradictory verification flag cannot become a success indicator');
  await h.render({view:{...copy(view),rows:[]},flowIndex:0,nodeId});assert.ok(h.text().includes('没有对应的原候选记录'));assert.equal(h.container.querySelectorAll('button').length,0);
  await h.render({view,flowIndex:2,nodeId});assert.ok(h.text().includes('不会按相同名称猜配'));assert.equal(h.text().includes('观测汇总资料'),false);assert.equal(h.text().includes('其他流程输入不得显示'),false);
  await h.render({view,flowIndex:0,nodeId:'missing-node-pointer'});assert.ok(h.text().includes('没有对应的原候选记录'));for(const hidden of [nodeId,'missing-node-pointer','do-not-display-causal-hash','node_id','runtime_entry_binding_verified',A,B])assert.equal(h.text().includes(hidden),false);assert.equal(h.container.querySelectorAll('button,input,textarea,select').length,0);
 }finally{await h.close();}
});
