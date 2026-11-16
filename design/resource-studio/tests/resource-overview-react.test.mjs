import test from 'node:test';
import assert from 'node:assert/strict';
import React, {act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('cards load independently, retry only their own failure, and discard old workspace responses',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/?view=resources'});
 const previous={window:globalThis.window,document:globalThis.document,navigator:Object.getOwnPropertyDescriptor(globalThis,'navigator'),act:globalThis.IS_REACT_ACT_ENVIRONMENT,transport:globalThis.__overviewTestJSON};
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 let sensorFails=true, resolveOld, resolveOldRole, roleCalls=0;const calls=[];
 globalThis.__overviewTestJSON=(path,opts)=>{
  calls.push({path, workspace:opts.headers['X-Workspace-ID']});
  if(path==='/specialists/installations'&&opts.headers['X-Workspace-ID']==='old')return new Promise(resolve=>{resolveOld=resolve;});
  if(path==='/specialists/installations'&&opts.headers['X-Workspace-ID']==='rolecheck'){
   roleCalls++;
   if(roleCalls===1)return new Promise(resolve=>{resolveOldRole=resolve;});
   return Promise.resolve([{status:'ready'},{status:'failed'}]);
  }
  if(path==='/mqtt/gateways'&&sensorFails)return Promise.reject(Error('private server detail that must not render'));
  if(path==='/workspace/device-gateways')return Promise.resolve({connections:[],has_more:false});
  if(path==='/specialists/installations'&&opts.headers['X-Workspace-ID']==='new')return Promise.resolve([{status:'ready'}]);
  return Promise.resolve([]);
 };
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'overview-test-transport',enforce:'pre',resolveId(id){if(/(?:^|\/)runtime-api(?:\.js)?$/.test(id))return '\0overview-api';},load(id){if(id==='\0overview-api')return 'export const json=(...args)=>globalThis.__overviewTestJSON(...args)';}}]});
 let root;
 try{
  const Overview=(await server.ssrLoadModule('/src/ResourceOverview.jsx')).default;
  const {createRoot}=await import('react-dom/client');root=createRoot(document.querySelector('#root'));let chosen='';
  const props={account:{workspace_id:'old',user_id:'actor'},onChoose:id=>{chosen=id;}};
  await act(async()=>{root.render(React.createElement(Overview,props));await Promise.resolve();});
  assert.ok(calls.length>0, 'expected overview to use test transport');
  assert.match(document.body.textContent,/记录暂时无法读取/);assert.doesNotMatch(document.body.textContent,/private server detail/);
  const before=calls.filter(c=>c.path==='/media/gateways').length;sensorFails=false;
  const sensor=[...document.querySelectorAll('article')].find(x=>x.textContent.includes('传感与时序数据'));
  await act(async()=>{[...sensor.querySelectorAll('button')].find(x=>x.textContent==='重试').click();await Promise.resolve();await Promise.resolve();});
  assert.equal(calls.filter(c=>c.path==='/media/gateways').length,before,'retry must not repeat other cards');
  assert.match(document.body.textContent,/0 个 MQTT 来源/);
  const video=[...document.querySelectorAll('article')].find(x=>x.textContent.includes('摄像头与音视频'));
  await act(async()=>video.querySelector('.resource-overview-action').click());assert.equal(chosen,'video');
  await act(async()=>{root.render(React.createElement(Overview,{...props,account:{workspace_id:'new',user_id:'actor'}}));await Promise.resolve();await Promise.resolve();});
  await act(async()=>{resolveOld([{status:'ready'},{status:'ready'}]);await Promise.resolve();await Promise.resolve();});
  assert.match(document.body.textContent,/1 条安装记录 · 1 条本机检查通过/);
  assert.doesNotMatch(document.body.textContent,/2 条安装记录/);
  await act(async()=>{root.render(React.createElement(Overview,{...props,account:{workspace_id:'rolecheck',user_id:'actor',role:'admin'}}));await Promise.resolve();});
  await act(async()=>{root.render(React.createElement(Overview,{...props,account:{workspace_id:'rolecheck',user_id:'actor',role:'viewer'}}));await Promise.resolve();await Promise.resolve();});
  await act(async()=>{resolveOldRole([{status:'ready'},{status:'ready'},{status:'ready'}]);await Promise.resolve();await Promise.resolve();});
  assert.equal(roleCalls,2,'role change must revalidate current workspace records');
  assert.match(document.body.textContent,/2 条安装记录 · 1 条本机检查通过/);
  assert.doesNotMatch(document.body.textContent,/3 条安装记录/);
 }finally{
  if(root)await act(async()=>root.unmount());await server.close();dom.window.close();
  for(const [key,value] of Object.entries({window:previous.window,document:previous.document,IS_REACT_ACT_ENVIRONMENT:previous.act,__overviewTestJSON:previous.transport})){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}
  if(previous.navigator)Object.defineProperty(globalThis,'navigator',previous.navigator);else delete globalThis.navigator;
 }
});
