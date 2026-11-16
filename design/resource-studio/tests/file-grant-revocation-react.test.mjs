import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';
for(const protocol of ['ftp','smb']) test(`${protocol} revocation invalidates selected connection and ignores late history`,async()=>{
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
  const module=protocol==='ftp'?'FtpDocumentGateway':'SmbDocumentGateway';const Component=(await server.ssrLoadModule(`/src/${module}.jsx`)).default,{createRoot}=await import('react-dom/client');
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
