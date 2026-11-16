import test from 'node:test';import assert from 'node:assert/strict';import React,{act} from 'react';import {JSDOM} from 'jsdom';import {createServer} from 'vite';import {webcrypto} from 'node:crypto';

test('remote raster import recovers the original request and verifies its source',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:'test',locks:{request:async(_k,_o,fn)=>fn()}},configurable:true});
 const cid='11111111-1111-4111-8111-111111111111',lid='22222222-2222-4222-8222-222222222222',sid='33333333-3333-4333-8333-333333333333',eid='44444444-4444-4444-8444-444444444444',fid='a'.repeat(64),sha='b'.repeat(64),records=new Map(),listings=[],imports=[];let failImport=true,imported=0;
 const request=async(path,opts={})=>{
  if(path==='/ftp/connections')return {connections:[{id:cid,name:'影像目录',version:1,status:'active'}]};
  if(path.startsWith('/ftp/geo/requests/') && !path.endsWith('/result'))return records.get(path.split('/').at(-1));
  if(path.endsWith('/result')){const r=records.get(path.split('/').at(-2));return {scene_id:sid,evidence_id:eid,status:'parsed',source:r.result.source};}
  if(path.endsWith('/listings/'+lid))return {listing:listings[0],source_available:true};
  if(path.endsWith('/listings')&&!opts.method)return {listings,source_available:true};if(path.endsWith('/imports')&&!opts.method)return {imports,source_available:true};
  const b=JSON.parse(opts.body),isListing=path.endsWith('/listings'),kind=isListing?'geo_listing':'geo_import',submission={...b,connection_id:cid},result=isListing?{listing_id:lid,files:[{id:fid,name:'scene.tif',size:1048576,modified:'20261115120000'}]}:{scene_id:sid,evidence_id:eid,status:'queued',source:{protocol:'ftp',connection_id:cid,listing_id:lid,file_id:fid,name:'scene.tif',size:1048576,modified:'20261115120000',sha256:sha}};
  const r={request_id:b.request_id,state:'succeeded',kind,submission,result};records.set(b.request_id,r);(isListing?listings:imports).unshift(r);if(!isListing&&failImport){failImport=false;throw Error('连接中断');}return r;
 };
 const vite=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const Component=(await vite.ssrLoadModule('/src/RemoteGeoGateway.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,...p})=>React.createElement('button',p,children);root=createRoot(document.getElementById('root'));
  await act(async()=>{root.render(React.createElement(Component,{protocol:'ftp',account:{workspace_id:'ws',user_id:'u',role:'admin'},Btn,request,onImported:()=>imported++}));await Promise.resolve();await Promise.resolve();});
  const select=async(label,value)=>{const e=document.querySelector('select[aria-label="'+label+'"]');assert.ok(e);await act(async()=>{e.value=value;e.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await Promise.resolve();await Promise.resolve();});};
  const click=async(text)=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent===text);assert.ok(b,text);assert.equal(b.disabled,false,text);await act(async()=>{b.click();for(let i=0;i<8;i++)await Promise.resolve();});};
  await select('FTP影像目录连接',cid);await click('读取影像目录');await select('要导入的影像',fid);await click('导入所选影像');assert.match(document.body.textContent,/操作结果待核对/);assert.equal(localStorage.length,1);
  await click('核对原请求');assert.equal(localStorage.length,0);assert.equal(imported,1);assert.match(document.body.textContent,/影像信息解析完成/);
 }finally{if(root)await act(async()=>root.unmount());await vite.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('downgraded owner sees and cancels own pending request without admin calls',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:'test',locks:{request:async(_k,_o,fn)=>fn()}},configurable:true});
 const submission={request_id:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',connection_id:'11111111-1111-4111-8111-111111111111',version:1},attempt={kind:'geo_listing',submission},key='remote-geo:'+encodeURIComponent(JSON.stringify(['ftp','ws','u']))+':pending';localStorage.setItem(key,JSON.stringify(attempt));const calls=[];
 const request=async(path,opts={})=>{calls.push(path);if(path==='/ftp/geo/requests/'+submission.request_id+'/cancel')return {request_id:submission.request_id,state:'cancelled',kind:attempt.kind,submission,result:null};throw Error('不能调用管理员入口：'+path);};
 const vite=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{const Component=(await vite.ssrLoadModule('/src/RemoteGeoGateway.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,...p})=>React.createElement('button',p,children);root=createRoot(document.getElementById('root'));
  await act(async()=>{root.render(React.createElement(Component,{protocol:'ftp',account:{workspace_id:'ws',user_id:'u',role:'viewer'},Btn,request}));await Promise.resolve();});
  assert.match(document.body.textContent,/当前没有管理员权限/);assert.ok(![...document.querySelectorAll('button')].some(x=>x.textContent==='重试原请求'));
  await act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='安全结束原请求').click();for(let i=0;i<5;i++)await Promise.resolve();});
  assert.equal(localStorage.getItem(key),null);assert.deepEqual(calls,['/ftp/geo/requests/'+submission.request_id+'/cancel']);
 }finally{if(root)await act(async()=>root.unmount());await vite.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('empty imagery gateway offers navigation to configure a connection',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:'test',locks:{request:async(_k,_o,fn)=>fn()}},configurable:true});
 const vite=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root,navigations=0;
 try{const Component=(await vite.ssrLoadModule('/src/RemoteGeoGateway.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,...p})=>React.createElement('button',p,children);root=createRoot(document.getElementById('root'));
  await act(async()=>{root.render(React.createElement(Component,{protocol:'smb',account:{workspace_id:'ws',user_id:'u',role:'admin'},Btn,request:async()=>({connections:[]}),onConfigure:()=>navigations++}));await Promise.resolve();await Promise.resolve();});
  await act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='前往配置目录连接').click();});assert.equal(navigations,1);
 }finally{if(root)await act(async()=>root.unmount());await vite.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
