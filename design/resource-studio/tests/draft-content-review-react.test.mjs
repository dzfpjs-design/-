import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('content approval is separate from publication and expires on a local edit',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const names=['window','document','navigator','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(names.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.sessionStorage=dom.window.sessionStorage;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');
  const {default:Panel,draftContentSignature}=await server.ssrLoadModule('/src/DraftContentReview.jsx');
  const account={user_id:'u',workspace_id:'w',role:'admin'};dom.window.sessionStorage.setItem('ga-setup-csrf','one');
  let w={instanceProfile:{domain:'林业',goal:''},materials:[],businesses:['巡护'],flows:{'巡护':{candidateKey:'patrol',trigger:'按计划',output:'记录',steps:[{id:'1',name:'检查',agent:'业务智能体',output:'记录'}]}}};
  let calls=0,adopted=0;
  const request=async(_path,options)=>{calls++;const body=JSON.parse(options.body);assert.equal(body.action,'confirm');assert.equal(body.revision,7);
   const next=structuredClone(w);next.flows['巡护'].candidateContentReview={business:'巡护',signature:draftContentSignature(w,w.flows['巡护'],'巡护'),confirmedAt:'2026-01-01',actor_id:'u'};
   return {draft:{revision:8,configuration:next}};};
  const Btn=({children,...props})=>React.createElement('button',props,children);
  const render=()=>React.createElement(Panel,{account,business:'巡护',flow:w.flows['巡护'],workspace:w,liveWorkspace:()=>w,
   canManage:true,prepareDraft:async()=>7,onAdopt:async(draft)=>{adopted++;w=draft.configuration;},Btn,request});
  root=createRoot(document.getElementById('root'));await act(async()=>root.render(render()));
  assert.match(document.body.textContent,/草案内容待确认/);assert.match(document.body.textContent,/正式生成或启用仍需核对资料依据/);
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='确认草案内容').click());
  assert.equal(calls,1);assert.equal(adopted,1);await act(async()=>root.render(render()));assert.match(document.body.textContent,/草案内容已确认/);
  w={...w,flows:{...w.flows,'巡护':{...w.flows['巡护'],output:'新的产出'}}};await act(async()=>root.render(render()));
  assert.match(document.body.textContent,/草案内容待确认/);assert.equal(w.flows['巡护'].confirmedAt,undefined);
 } finally {if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});

test('business provenance is part of the local content signature',async()=>{
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 try{
  const {draftContentSignature}=await server.ssrLoadModule('/src/DraftContentReview.jsx');
  const flow={candidateKey:'original',trigger:'定时',output:'记录',steps:[]},workspace={instanceProfile:{},materials:[],flows:{巡护:flow}};
  const original=draftContentSignature(workspace,flow,'巡护');
  assert.notEqual(original,draftContentSignature(workspace,{...flow,candidateKey:'other'},'巡护'));
  assert.notEqual(original,draftContentSignature(workspace,{...flow,candidateBasisHash:'changed'},'巡护'));
 } finally {await server.close();}
});

test('late approval is never adopted into another session',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const names=['window','document','navigator','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(names.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.sessionStorage=dom.window.sessionStorage;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;
 try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/DraftContentReview.jsx');
  const flow={candidateKey:'x',trigger:'定时',output:'记录',steps:[{id:'1',name:'检查',agent:'业务智能体',output:'记录'}]},w={instanceProfile:{},materials:[],flows:{巡护:flow}};
  let account={user_id:'u',workspace_id:'w',role:'admin'},resolve,entered,adopted=0;
  const waiting=new Promise(r=>resolve=r),started=new Promise(r=>entered=r),Btn=({children,...props})=>React.createElement('button',props,children);
  const render=()=>React.createElement(Panel,{account,business:'巡护',flow,workspace:w,liveWorkspace:()=>w,canManage:true,
   prepareDraft:async()=>1,onAdopt:async()=>{adopted++;},Btn,request:async()=>{entered();return waiting;}});
  dom.window.sessionStorage.setItem('ga-setup-csrf','first');root=createRoot(document.getElementById('root'));await act(async()=>root.render(render()));
  await act(async()=>document.querySelector('button').click());await started;
  account={user_id:'other',workspace_id:'other',role:'admin'};dom.window.sessionStorage.setItem('ga-setup-csrf','second');await act(async()=>root.render(render()));
  await act(async()=>{resolve({draft:{revision:2,configuration:w}});await new Promise(r=>setTimeout(r,10));});assert.equal(adopted,0);
 } finally {if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});

test('an edit while the approval is in flight is never overwritten',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const names=['window','document','navigator','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(names.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.sessionStorage=dom.window.sessionStorage;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;
 try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/DraftContentReview.jsx');
  let flow={candidateKey:'x',trigger:'定时',output:'记录',steps:[{id:'1',name:'检查',agent:'业务智能体',output:'记录'}]};
  let w={instanceProfile:{},materials:[],flows:{巡护:flow}},resolve,entered,adopted=0;
  const waiting=new Promise(r=>resolve=r),started=new Promise(r=>entered=r),Btn=({children,...props})=>React.createElement('button',props,children);
  const account={user_id:'u',workspace_id:'w',role:'admin'};
  const render=()=>React.createElement(Panel,{account,business:'巡护',flow,workspace:w,liveWorkspace:()=>w,canManage:true,
   prepareDraft:async()=>1,onAdopt:async()=>{adopted++;},Btn,request:async()=>{entered();return waiting;}});
  dom.window.sessionStorage.setItem('ga-setup-csrf','first');root=createRoot(document.getElementById('root'));await act(async()=>root.render(render()));
  await act(async()=>document.querySelector('button').click());await started;
  flow={...flow,output:'新输入'};w={...w,flows:{巡护:flow}};await act(async()=>root.render(render()));
  await act(async()=>{resolve({draft:{revision:2,configuration:w}});await new Promise(r=>setTimeout(r,10));});
  assert.equal(adopted,0);assert.equal(w.flows.巡护.output,'新输入');assert.match(document.body.textContent,/确认结果暂未核对/);
 } finally {if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});

test('a material version change immediately conceals an old content approval',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const names=['window','document','navigator','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(names.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.sessionStorage=dom.window.sessionStorage;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;
 try{
  const {createRoot}=await import('react-dom/client');const {default:Panel,draftContentSignature}=await server.ssrLoadModule('/src/DraftContentReview.jsx');
  const saved={id:'material',name:'报告',version:1,hash:'hash',archived:false};
  const flow={candidateKey:'x',trigger:'定时',output:'记录',steps:[{id:'1',name:'检查',agent:'业务智能体',output:'记录'}],candidateUnresolvedSources:[{material_id:'material',source_text:'原文',quote:'原文',reason:'not_found'}]};
  const w={instanceProfile:{},materials:[saved],flows:{巡护:flow}};
  flow.candidateContentReview={business:'巡护',signature:draftContentSignature(w,flow,'巡护'),confirmedAt:'now',actor_id:'u'};
  const account={user_id:'u',workspace_id:'w',role:'admin'},Btn=({children,...props})=>React.createElement('button',props,children);
  let canonical=[saved];const render=()=>React.createElement(Panel,{account,business:'巡护',flow,workspace:w,liveWorkspace:()=>w,
   canonicalMaterials:canonical,materialStatusRead:'ready',canManage:true,prepareDraft:async()=>1,onAdopt:async()=>{},Btn});
  dom.window.sessionStorage.setItem('ga-setup-csrf','first');root=createRoot(document.getElementById('root'));await act(async()=>root.render(render()));
  assert.match(document.body.textContent,/草案内容已确认/);
  canonical=[{...saved,version:2}];await act(async()=>root.render(render()));
  assert.doesNotMatch(document.body.textContent,/草案内容已确认/);assert.match(document.body.textContent,/关联资料已变化/);assert.equal(document.querySelector('button').disabled,true);
 } finally {if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});
