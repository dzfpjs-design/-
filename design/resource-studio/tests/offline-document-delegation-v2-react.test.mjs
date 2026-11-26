import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';

test('authorization, lost reply, refresh, revocation and role loss remain understandable',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT','crypto'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
 Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/OfflineDocumentDelegationV2.jsx');
  root=createRoot(document.getElementById('root'));
  let state='not_authorized',lastGrant=null,lastRevoke=null,role='admin',lost=true,failBefore=false,grantCalls=[],empty=false,holdNext=false,resolveOld;
  const request=async(path,options={})=>{
   if(role==='viewer')throw Error('permission denied');
   if(options.method==='POST'){
    const body=JSON.parse(options.body);
    if(path.endsWith('/grant')){grantCalls.push(body.request_id);if(failBefore){failBefore=false;throw Error('not yet saved');}state='authorized';lastGrant=body.request_id;if(lost){lost=false;throw Error('response lost');}}
    else{state='revoked';lastRevoke=body.request_id;}
    return {state};
   }
   if(holdNext){holdNext=false;return await new Promise(resolve=>{resolveOld=resolve;});}
   return {enabled:true,permit_committed:true,items:empty?[]:[{job_id:'job-1',grant_id:state==='authorized'?'grant-1':null,
    task_title:'林区资料',source_label:'已批准的业务资料包',state,grant_request_id:lastGrant,revoke_request_id:lastRevoke,
    expires_at:null,prepared:false,background_available:false,can_authorize:state!=='authorized',can_revoke:state==='authorized'}]};
  };
  const render=()=>React.createElement(Panel,{account:{workspace_id:'workspace',user_id:'user'},task:{id:'task'},requestJSON:request,storage:dom.window.sessionStorage});
  await act(async()=>root.render(render()));
  assert.match(document.body.textContent,/暂不能自动整理/);assert.doesNotMatch(document.body.textContent,/派发许可|账本|测试/);
  await act(async()=>document.querySelector('input[type=checkbox]').click());
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='确认授权').click());
  assert.match(document.body.textContent,/结果尚未确认/);assert.doesNotMatch(document.body.textContent,/林区资料/);
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/授权有效/);assert.match(document.body.textContent,/已提交的分析请求可能无法撤回/);assert.equal(dom.window.sessionStorage.length,0);
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='撤销授权').click());
  assert.match(document.body.textContent,/已撤销/);
  await act(async()=>{root.unmount();root=createRoot(document.getElementById('root'));root.render(render());});
  assert.match(document.body.textContent,/已撤销/);
  failBefore=true;await act(async()=>document.querySelector('input[type=checkbox]').click());
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='确认授权').click());
  assert.match(document.body.textContent,/结果尚未确认/);
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  if(!document.querySelector('input[type=checkbox]').checked)await act(async()=>document.querySelector('input[type=checkbox]').click());
  await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='确认授权').click());
  assert.equal(grantCalls.length,3);assert.notEqual(grantCalls[0],grantCalls[1]);assert.equal(grantCalls[1],grantCalls[2]);
  role='viewer';await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.match(document.body.textContent,/无法读取授权状态/);assert.doesNotMatch(document.body.textContent,/林区资料/);
  role='admin';empty=true;await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click());
  assert.equal(document.body.textContent,'','no empty authorization panel interrupts the user');
  holdNext=true;await act(async()=>dom.window.dispatchEvent(new dom.window.Event('focus')));
  assert.ok(resolveOld);empty=false;await act(async()=>dom.window.dispatchEvent(new dom.window.Event('focus')));
  await act(async()=>resolveOld({enabled:true,permit_committed:false,items:[]}));
  assert.match(document.body.textContent,/授权有效/,'newly available work is discovered without an empty panel');
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('disabled, unavailable, inconsistent and permission failures remain distinguishable and recoverable',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;
 try{
  const {createRoot}=await import('react-dom/client'),{default:Panel}=await server.ssrLoadModule('/src/OfflineDocumentDelegationV2.jsx');root=createRoot(document.getElementById('root'));
  let mode='disabled',reads=0;
  const request=async()=>{reads++;if(mode==='missing')throw Object.assign(Error('not found'),{status:404});if(mode==='offline')throw Error('network');if(mode==='denied')throw Object.assign(Error('denied'),{status:403});if(mode==='inconsistent')return {enabled:false,items:[{job_id:'j'}]};if(mode==='malformed')return {enabled:true};if(mode==='enabled')return {enabled:true,permit_committed:false,items:[{job_id:'j',task_title:'资料',source_label:'已批准资料',state:'not_authorized',can_authorize:true,can_revoke:false}]};return {enabled:false,items:[]};};
  const render=()=>React.createElement(Panel,{account:{workspace_id:'w',user_id:'u'},task:{id:'t'},requestJSON:request,storage:dom.window.sessionStorage});
  const mount=async()=>act(async()=>{root.render(render());});const refresh=async()=>act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click();});
  await mount();assert.equal(document.body.textContent,'');
  for(const state of ['missing','offline','denied','inconsistent','malformed']){mode=state;await act(async()=>{root.unmount();root=createRoot(document.getElementById('root'));root.render(render());});assert.match(document.body.textContent,/无法读取授权状态/);assert.doesNotMatch(document.body.textContent,/同意离线整理/);}
  mode='enabled';await refresh();assert.match(document.body.textContent,/同意离线整理/);assert.ok(reads>=7);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
