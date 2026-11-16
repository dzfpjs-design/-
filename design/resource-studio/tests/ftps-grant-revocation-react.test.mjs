import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';
for(const protocol of ['ftps']) test(`${protocol} revocation invalidates selected connection and ignores late history`,async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});
 Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:dom.window.navigator.userAgent,locks:{request:async(_k,_o,fn)=>fn()}},configurable:true});
 let available=true,delay=false,late=[];
 const request=async(path)=>{
  if(path===`/${protocol}/grants`)return {grants:[]};
  if(path===`/${protocol}/connections`)return {connections:[{id:'one',name:'目录',status:'active',version:1}]};
  if(path.endsWith('/listings')||path.endsWith('/imports')){const kind=path.endsWith('/listings')?'listings':'imports',value={[kind]:[],source_available:available};if(delay)return new Promise(resolve=>late.push(()=>resolve(value)));return value;}
  throw Error('unexpected request');
 };
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const module='FtpsDocumentGateway';const Component=(await server.ssrLoadModule(`/src/${module}.jsx`)).default,{createRoot}=await import('react-dom/client');
  const Btn=({children,...props})=>React.createElement('button',props,children),props={account:{workspace_id:'ws',user_id:'admin',role:'admin'},Btn,request,authorizationRevision:0};root=createRoot(document.getElementById('root'));
  await act(async()=>{root.render(React.createElement(Component,props));await Promise.resolve();await Promise.resolve()});
  await act(async()=>{const select=document.querySelector('[aria-label="文档目录连接"]');select.value='one';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await Promise.resolve();await Promise.resolve()});
  const button=text=>[...document.querySelectorAll('button')].find(x=>x.textContent===text);assert.equal(button('读取目录文件').disabled,false);
  delay=true;await act(async()=>{button('重新读取目录和导入记录').click();await Promise.resolve()});assert.equal(late.length,2);
  delay=false;available=false;await act(async()=>{root.render(React.createElement(Component,{...props,authorizationRevision:1}));await Promise.resolve();await Promise.resolve()});
  assert.equal(button('读取目录文件').disabled,true);assert.match(document.body.textContent,/读取授权已撤销或不可用/);
  await act(async()=>{late.forEach(fn=>fn());await Promise.resolve();await Promise.resolve()});
  assert.equal(button('读取目录文件').disabled,true);assert.match(document.body.textContent,/读取授权已撤销或不可用/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k]}}
});

test('CA selection cannot submit while reading or silently retain a failed selection',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});
 Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:dom.window.navigator.userAgent,locks:{request:async(_k,_o,fn)=>fn({})}},configurable:true});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/ManagedFtpsGrantStudio.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,...props})=>React.createElement('button',props,children);root=createRoot(document.getElementById('root'));
  await act(async()=>{root.render(React.createElement(Component,{account:{workspace_id:'ws',user_id:'admin',role:'admin'},Btn,request:async()=>({enabled:true,key_ready:true,networks:['192.168.1.0/24'],grants:[]})}));await Promise.resolve();await Promise.resolve()});
  const field=document.querySelector('input[type=file]'),save=[...document.querySelectorAll('button')].find(x=>x.textContent==='保存目录授权');let finish;
  Object.defineProperty(field,'files',{configurable:true,value:[{size:20,text:()=>new Promise(resolve=>{finish=resolve})}]});await act(async()=>{field.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await Promise.resolve()});assert.equal(save.disabled,true);assert.match(document.body.textContent,/正在读取证书/);
  await act(async()=>{finish('synthetic certificate');await Promise.resolve()});assert.equal(save.disabled,false);assert.ok(!JSON.stringify(localStorage).includes('synthetic certificate'));
  Object.defineProperty(field,'files',{configurable:true,value:[{size:131073,text:async()=>''}]});await act(async()=>{field.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await Promise.resolve()});assert.equal(save.disabled,true);assert.match(document.body.textContent,/证书文件过大/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k]}}
});

test('FTPS exposes older connections and source-scoped pages on demand',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:dom.window.navigator.userAgent,locks:{request:async(_k,_o,fn)=>fn()}},configurable:true});
 const seen=[];const request=async path=>{seen.push(path);if(path==='/ftps/grants')return {grants:[]};if(path==='/ftps/connections')return {connections:[{id:'one',name:'当前目录',status:'active',version:1}],next_cursor:'connection-cursor'};if(path==='/ftps/connections?cursor=connection-cursor')return {connections:[{id:'two',name:'旧目录',status:'active',version:1}],next_cursor:null};if(path==='/ftps/connections/one/listings')return {listings:[],source_available:true,next_cursor:'listing-cursor'};if(path==='/ftps/connections/one/imports')return {imports:[],source_available:true,next_cursor:'import-cursor'};if(path.includes('?cursor='))return path.includes('/listings?')?{listings:[],source_available:true,next_cursor:null}:{imports:[],source_available:true,next_cursor:null};throw Error(path)};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{const Component=(await server.ssrLoadModule('/src/FtpsDocumentGateway.jsx')).default,{createRoot}=await import('react-dom/client'),Btn=({children,...props})=>React.createElement('button',props,children);root=createRoot(document.getElementById('root'));
  await act(async()=>{root.render(React.createElement(Component,{account:{workspace_id:'ws',user_id:'admin',role:'admin'},Btn,request}));await Promise.resolve();await Promise.resolve()});const button=text=>[...document.querySelectorAll('button')].find(x=>x.textContent===text);
  await act(async()=>{button('加载更多目录连接').click();await Promise.resolve()});assert.match(document.body.textContent,/旧目录/);assert.ok(!button('加载更多目录连接'));
  await act(async()=>{const select=document.querySelector('[aria-label="文档目录连接"]');select.value='one';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await Promise.resolve();await Promise.resolve()});
  await act(async()=>{button('加载更多目录清单').click();await Promise.resolve()});await act(async()=>{button('加载更多导入记录').click();await Promise.resolve()});assert.ok(seen.includes('/ftps/connections/one/listings?cursor=listing-cursor'));assert.ok(seen.includes('/ftps/connections/one/imports?cursor=import-cursor'));
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k]}}
});
