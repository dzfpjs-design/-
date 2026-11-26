import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('new preparation and existing history share one user-facing area',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
 const account={workspace_id:id(1),user_id:id(2),role:'admin'},task={id:id(3),instance_id:id(4),instance_release_id:id(5)};
 dom.window.sessionStorage.setItem('ga-setup-csrf-user',account.user_id);
 dom.window.sessionStorage.setItem('ga-setup-csrf-workspace',account.workspace_id);
 dom.window.sessionStorage.setItem('ga-setup-csrf','synthetic');
 const selection={kind:'approved_source_set',source_set_ref:{source_set_id:id(6),source_set_hash:'a'.repeat(64)}};
 const permissions=Object.fromEntries(['can_prepare','can_authorize','can_preview','can_start','can_review','can_configure_quota','can_register_price'].map(key=>[key,false]));
 const request=async path=>{
  if(path.endsWith('/business-advice-v2/availability'))return {enabled:true};
  if(path.endsWith('/business-advice-v2/context'))return {enabled:true,can_prepare:true,sources:[{title:'批准资料',selection}],goal_hash:'a'.repeat(64),head_hash:'b'.repeat(64),expires_at:'2026-11-26T00:00:00Z',max_tokens:1024,model:'DeepSeek'};
  if(path.endsWith('/business-advice-v2/preparations'))return {items:[]};
  if(path.endsWith('/business-advice/context'))return {schema:'same-goal-advice-context-v310',task_id:task.id,task_version:1,title:'合成任务',permissions,document_sources:[],blocked_reasons:[],goal_hash:'a'.repeat(64),mature_head_hash:'b'.repeat(64)};
  if(path.endsWith('/business-advice/preparations'))return {schema:'same-goal-advice-operation-v302/list',task_id:task.id,records:[]};
  throw Error('unexpected path');
 };
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/SameGoalAdviceV310.jsx');
  root=createRoot(document.getElementById('root'));
  const locks={request:async(_name,_options,callback)=>callback({name:'synthetic'})};
  await act(async()=>root.render(React.createElement(Panel,{account,task,requestJSON:request,storage:dom.window.localStorage,tabStorage:dom.window.sessionStorage,locks})));
  await act(async()=>new Promise(resolve=>setTimeout(resolve,20)));
  assert.equal([...document.querySelectorAll('h4')].filter(item=>item.textContent==='业务建议').length,1);
  assert.equal([...document.querySelectorAll('summary')].filter(item=>item.textContent==='历史建议').length,1);
  assert.ok([...document.querySelectorAll('button')].some(item=>item.textContent==='使用批准资料'));
  assert.doesNotMatch(document.body.textContent,/准备新建议|单次费用上限|测试|实验|V2/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('an unreadable history is not presented as an empty history',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {SameGoalAdviceContentV310:Content}=await server.ssrLoadModule('/src/SameGoalAdviceV310.jsx');
  root=createRoot(document.getElementById('root'));
  const view={denied:false,loading:false,localSaved:true,reads:{history:'failed'},context:null,list:null,plan:null};
  await act(async()=>root.render(React.createElement(Content,{view,account:{role:'admin'},historyOnly:true,onAction:()=>{},onDraft:()=>{}})));
  assert.match(document.body.textContent,/历史建议暂未读回/);
  assert.doesNotMatch(document.body.textContent,/暂无历史建议/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
