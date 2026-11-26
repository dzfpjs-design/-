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
  root=createRoot(document.getElementById('root'));let state='ready',role='admin',lost=true,posts=0,enabled=true,multi=false,empty=false,holdNext=false,resolveOld;
  const request=async(path,options={})=>{
   if(role!=='admin')throw Error('permission lost');
   if(options.method==='POST'){posts++;state='settled';if(lost){lost=false;throw Error('lost reply');}return {state:'settled'};}
   if(holdNext){holdNext=false;return await new Promise(resolve=>{resolveOld=resolve;});}
   if(path.includes('?cursor='))return {enabled:true,items:[{permit_id:'permit-2',state:'needs_attention',recorded_at:'2026-11-24T12:00:00Z'}],next_cursor:null};
   return {enabled,items:enabled&&!empty?[{permit_id:'permit-1',state,recorded_at:'2026-11-25T12:00:00Z'}]:[],next_cursor:multi?'next-page':null};
  };
  const render=()=>React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role},task:{id:'t'},requestJSON:request});
  await act(async()=>root.render(render()));
  assert.match(document.body.textContent,/建议待恢复/);
  assert.doesNotMatch(document.body.textContent,/首答|派发|账本/);
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='恢复建议').click());
  assert.match(document.body.textContent,/恢复结果尚未确认/);assert.equal(posts,1);
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/建议已保存 · 用量记录待核/);assert.equal(posts,1);
  await act(async()=>{root.unmount();root=createRoot(document.getElementById('root'));root.render(render());});
  assert.match(document.body.textContent,/建议已保存 · 用量记录待核/);
  state='settled_usage_unknown';await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/建议已保存 · 实际用量待核/);
  assert.match(document.body.textContent,/实际用量待核/);
  assert.doesNotMatch(document.body.textContent,/首答和用量已保存/);
  state='failed';await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/建议未生成 · 用量记录待核/);
  state='failed_usage_unknown';await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/建议未生成 · 实际用量待核/);
  state='needs_attention';await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/勿重复提交/);assert.equal(document.querySelectorAll('button').length,1);
  multi=true;await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='查看更多').click());
  assert.equal(document.querySelectorAll('article').length,2);
  empty=true;multi=false;await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.equal(document.body.textContent,'','no empty recovery panel interrupts the user');
  holdNext=true;await act(async()=>dom.window.dispatchEvent(new dom.window.Event('focus')));
  assert.ok(resolveOld);empty=false;await act(async()=>dom.window.dispatchEvent(new dom.window.Event('focus')));
  await act(async()=>resolveOld({enabled:true,items:[],next_cursor:null}));
  assert.match(document.body.textContent,/需要核对/,'a newly pending item appears when returning to the page');
  role='viewer';await act(async()=>root.render(render()));assert.equal(document.body.textContent,'');
  role='admin';enabled=false;await act(async()=>root.render(render()));assert.equal(document.body.textContent,'');
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
