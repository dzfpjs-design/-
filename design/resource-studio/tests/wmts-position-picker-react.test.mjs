import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';

test('position lookup preserves input, applies one verified tile and refuses to treat multiple tiles as one map',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT','localStorage','sessionStorage','crypto'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/WmtsPositionPicker.jsx')).default,{createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));
  const Btn=({children,...props})=>React.createElement('button',props,children),Field=({children,label})=>React.createElement('label',null,label,children);
  const calls=[],selected=[];let mode='point',canSave=true;
  const request=async(path,opts)=>{calls.push({path,opts});return {connection_id:'map',version:2,layer:'forest',matrix_set:'world',matrix:'1',...(mode==='point'?{kind:'point',row:1,col:2}:{kind:'bounds',min_row:0,max_row:1,min_col:0,max_col:1,tile_count:4})};};
  const render=()=>act(async()=>{root.render(React.createElement(Component,{account:{workspace_id:'workspace',user_id:'actor'},connection:{id:'map',version:2},selection:{layer:'forest',matrix_set:'world',matrix:'1'},Btn,Field,request,onSelected:v=>{if(!canSave)return false;selected.push(v);return true;}}));await Promise.resolve();});
  const edit=async(label,value)=>act(async()=>{const element=document.querySelector(`[aria-label="${label}"]`);assert.ok(element,label);Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(element,value);element.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  const click=async()=>act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='计算瓦片位置').click();await Promise.resolve();await Promise.resolve();});
  await render();await edit('经度','117.65');await edit('纬度','24.51');await click();
  assert.equal(calls[0].opts.headers['X-Workspace-ID'],'workspace');assert.deepEqual(JSON.parse(calls[0].opts.body),{version:2,layer:'forest',matrix_set:'world',matrix:'1',longitude:117.65,latitude:24.51});assert.deepEqual(selected,[{row:'1',col:'2'}]);
  canSave=false;await click();assert.equal(selected.length,1);assert.match(document.body.textContent,/瓦片位置未能保存/);assert.doesNotMatch(document.body.textContent,/已定位到第/);canSave=true;
  await act(async()=>{root.unmount();root=createRoot(document.getElementById('root'));});await render();assert.equal(document.querySelector('[aria-label="经度"]').value,'117.65');
  await act(async()=>{const select=document.querySelector('[aria-label="位置方式"]');select.value='bounds';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  for(const [label,value] of [['西侧经度','117'],['南侧纬度','24'],['东侧经度','118'],['北侧纬度','25']])await edit(label,value);
  mode='bounds';await click();assert.equal(selected.length,1);assert.match(document.body.textContent,/涉及 4 张瓦片/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d] of old){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});

test('a late result for an older catalogue version cannot modify the current selection',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT','localStorage','sessionStorage','crypto'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/WmtsPositionPicker.jsx')).default,{createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));
  const Btn=({children,...props})=>React.createElement('button',props,children),Field=({children,label})=>React.createElement('label',null,label,children);
  let finish;const selected=[],request=()=>new Promise(resolve=>{finish=resolve;});
  const render=version=>act(async()=>{root.render(React.createElement(Component,{account:{workspace_id:'workspace',user_id:'actor'},connection:{id:'map',version},selection:{layer:'forest',matrix_set:'world',matrix:'1'},Btn,Field,request,onSelected:v=>selected.push(v)}));await Promise.resolve();});
  await render(1);
  for(const [label,value] of [['经度','117.65'],['纬度','24.51']])await act(async()=>{const element=document.querySelector(`[aria-label="${label}"]`);Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(element,value);element.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  await act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='计算瓦片位置').click();await Promise.resolve();});
  await render(2);
  await act(async()=>{finish({connection_id:'map',version:1,layer:'forest',matrix_set:'world',matrix:'1',kind:'point',row:0,col:0});await Promise.resolve();await Promise.resolve();});
  assert.deepEqual(selected,[]);assert.match(document.body.textContent,/地图或矩阵已变化/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d] of old){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});
