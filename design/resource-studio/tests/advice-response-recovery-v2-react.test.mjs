import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('recover, lost reply, refresh and role changes stay understandable',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdviceResponseRecoveryV2.jsx');
  root=createRoot(document.getElementById('root'));let state='ready',role='admin',lost=true,posts=0,enabled=true;
  const request=async(_path,options={})=>{
   if(role!=='admin')throw Error('permission lost');
   if(options.method==='POST'){posts++;state='settled';if(lost){lost=false;throw Error('lost reply');}return {state:'settled'};}
   return {enabled,items:enabled?[{permit_id:'permit-1',state,recorded_at:'2026-11-25T12:00:00Z'}]:[]};
  };
  const render=()=>React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role},task:{id:'t'},requestJSON:request});
  await act(async()=>root.render(render()));
  assert.match(document.body.textContent,/已核对原请求和一次性派发记录/);
  assert.match(document.body.textContent,/不会再次发送/);
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='恢复结果并核对用量').click());
  assert.match(document.body.textContent,/恢复结果尚未确认/);assert.equal(posts,1);
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新状态').click());
  assert.match(document.body.textContent,/首答和用量已保存/);assert.equal(posts,1);
  await act(async()=>{root.unmount();root=createRoot(document.getElementById('root'));root.render(render());});
  assert.match(document.body.textContent,/首答和用量已保存/);
  state='needs_attention';await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新状态').click());
  assert.match(document.body.textContent,/不要重复发送/);assert.equal(document.querySelectorAll('button').length,1);
  role='viewer';await act(async()=>root.render(render()));assert.equal(document.body.textContent,'');
  role='admin';enabled=false;await act(async()=>root.render(render()));assert.equal(document.body.textContent,'');
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
