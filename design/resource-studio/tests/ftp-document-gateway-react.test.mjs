import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';

test('administrator can recover an uncertain connection and import a chosen saved file',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','sessionStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true});
 Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:dom.window.navigator.userAgent,locks:{request:async(_key,_opts,fn)=>fn()}},configurable:true});
 const requests=[],records=new Map(),connections=[],listings=[],imports=[];let failConnect=true,failHistory=false,imported=0;
 const request=async(path,opts={})=>{
  requests.push({path,method:opts.method||'GET',body:opts.body?JSON.parse(opts.body):null,scope:opts.headers?.['X-Workspace-ID']});
  if(path==='/ftp/grants')return {grants:[{id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',name:'批准目录',host:'192.168.1.10',port:21,root:'/资料'}]};
  if(path==='/ftp/connections'&&!opts.method)return {connections};
  if(path==='/ftp/connections/11111111-1111-4111-8111-111111111111'&&!opts.method)return {connection:connections[0],source_available:true};
  if(path.startsWith('/ftp/requests/')&&!path.endsWith('/cancel')){const r=records.get(path.split('/').at(-1));if(!r)throw Error('尚未查到');return r;}
  if(path.startsWith('/ftp/connections/11111111-1111-4111-8111-111111111111/listings/'))return {listing:listings.find(x=>x.result.listing_id===path.split('/').at(-1)),source_available:true};
  if(path.endsWith('/listings')&&!opts.method){if(failHistory)throw Error('历史暂不可读');return {listings,source_available:true};}
  if(path.endsWith('/imports')&&!opts.method){if(failHistory)throw Error('历史暂不可读');return {imports,source_available:true};}
  if(path==='/setup/materials/33333333-3333-4333-8333-333333333333')return {id:'33333333-3333-4333-8333-333333333333',name:'报告.docx',status:'parsed',archived:true,hash:'a'.repeat(64),size:1024};
  const body=JSON.parse(opts.body),kind=path==='/ftp/connections'?'connect':path.endsWith('/listings')?'listing':'import';
  const submission=kind==='connect'?body:{...body,connection_id:'11111111-1111-4111-8111-111111111111'};
  let result;
  if(kind==='connect'){result={id:'11111111-1111-4111-8111-111111111111',name:body.name,grant_id:body.grant_id,version:1,status:'active'};connections.splice(0,connections.length,result);}
  else if(kind==='listing')result={listing_id:listings.length?'44444444-4444-4444-8444-444444444444':'22222222-2222-4222-8222-222222222222',files:[{id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',name:listings.length?'新增报告.docx':'报告.docx',size:1024,modified:'20261115123000'}]};
  else result={material_id:'33333333-3333-4333-8333-333333333333',name:'报告.docx',status:'queued',source:{connection_id:'11111111-1111-4111-8111-111111111111',listing_id:'22222222-2222-4222-8222-222222222222',file_id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',name:'报告.docx',size:1024,modified:'20261115123000',sha256:'a'.repeat(64)}};
  const receipt={request_id:body.request_id,state:'succeeded',kind,submission,result};records.set(body.request_id,receipt);
  if(kind==='listing')listings.unshift(receipt);if(kind==='import')imports.unshift(receipt);
  if(kind==='connect'&&failConnect){failConnect=false;throw Error('连接中断');}return receipt;
 };
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/FtpDocumentGateway.jsx')).default,{createRoot}=await import('react-dom/client');
  const Btn=({children,...props})=>React.createElement('button',props,children),props={account:{workspace_id:'workspace',user_id:'admin',role:'admin'},Btn,request,onImported:()=>imported++};
  const mount=async()=>{root=createRoot(document.getElementById('root'));await act(async()=>{root.render(React.createElement(Component,props));await Promise.resolve();await Promise.resolve();});};
  const button=text=>[...document.querySelectorAll('button')].find(x=>x.textContent===text);
  const input=async(label,value)=>act(async()=>{const e=document.querySelector(`[aria-label="${label}"]`);const proto=e.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(e,value);e.dispatchEvent(new dom.window.Event('change',{bubbles:true}));e.dispatchEvent(new dom.window.Event('input',{bubbles:true}));await Promise.resolve();});
  await mount();await input('文档目录连接名称','x'.repeat(101));assert.equal(button('保存连接').disabled,true);assert.equal(requests.filter(x=>x.method==='POST').length,0);assert.equal(localStorage.length,0);
  await input('文档目录连接名称','token=do-not-save');assert.equal(button('保存连接').disabled,true);assert.equal(localStorage.length,0);
  await input('文档目录连接名称','日常资料');await input('已批准的文档目录','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
  await act(async()=>{button('保存连接').click();await Promise.resolve();await Promise.resolve();});assert.match(document.body.textContent,/原请求保留/);assert.equal(records.size,1);assert.ok([...Array(localStorage.length)].some((_,i)=>localStorage.key(i).includes(':pending:')));
  const firstRequest=requests.find(x=>x.path==='/ftp/connections'&&x.method==='POST').body.request_id;
  await act(async()=>root.unmount());await mount();assert.ok(button('核对原请求'));await act(async()=>{button('核对原请求').click();await Promise.resolve();await Promise.resolve();await Promise.resolve();});
  assert.match(document.body.textContent,/连接已保存/);assert.equal(records.size,1);assert.equal(requests.filter(x=>x.path==='/ftp/connections'&&x.method==='POST').length,1);assert.ok(requests.some(x=>x.path==='/ftp/requests/'+firstRequest));
  await act(async()=>{button('读取目录文件').click();await Promise.resolve();await Promise.resolve();await Promise.resolve();});assert.match(document.body.textContent,/远端修改：2026-11-15 20:30:00（北京时间）/);await input('要导入的文档','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');
  failHistory=true;await act(async()=>{button('确认导入所选文件').click();await Promise.resolve();await Promise.resolve();await Promise.resolve();});assert.match(document.body.textContent,/正文解析完成/);assert.match(document.body.textContent,/已在归档中/);assert.match(document.body.textContent,/原请求仍保留/);assert.equal(imported,0);assert.equal(imports.length,1);assert.ok(button('已核对并完成'));await act(async()=>{button('已核对并完成').click();await Promise.resolve();await Promise.resolve();await Promise.resolve();});assert.match(document.body.textContent,/记录清单暂未更新/);assert.equal(imported,1);assert.equal(button('读取目录文件').disabled,true);failHistory=false;
  await act(async()=>root.unmount());await mount();await input('文档目录连接','11111111-1111-4111-8111-111111111111');assert.match(document.body.textContent,/报告.docx/);assert.ok(button('查看解析状态'));
  await input('要导入的文档','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');await act(async()=>{button('读取目录文件').click();await Promise.resolve();await Promise.resolve();await Promise.resolve();});assert.equal(document.querySelector('[aria-label="文档目录清单"]').value,'44444444-4444-4444-8444-444444444444');assert.equal(document.querySelector('[aria-label="要导入的文档"]').value,'');assert.match(document.querySelector('[aria-label="要导入的文档"]').textContent,/新增报告.docx/);
  assert.ok(requests.every(x=>x.scope==='workspace'));assert.ok(!JSON.stringify(requests).includes('password'));
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('a saved receipt remains recoverable after source authorization is revoked',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','sessionStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true});
 Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:dom.window.navigator.userAgent,locks:{request:async(_key,_opts,fn)=>fn()}},configurable:true});
 const request_id='53f37e1b-07d2-4ea2-9d9c-91d2fc71c634',submission={request_id,version:1,connection_id:'11111111-1111-4111-8111-111111111111'},attempt={kind:'listing',submission};
 const receipt={request_id,state:'succeeded',kind:'listing',submission,result:{listing_id:'22222222-2222-4222-8222-222222222222',files:[{id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',name:'报告.docx',size:42,modified:'20261115123000'}]}};
 localStorage.setItem('ftp-document:workspace:admin:pending:'+request_id,JSON.stringify(attempt));
 const request=async(path,opts={})=>{
  if(path==='/ftp/grants')return {grants:[]};if(path==='/ftp/connections')return {connections:[{id:'11111111-1111-4111-8111-111111111111',name:'旧目录',grant_id:'g',version:1,status:'active'}]};
  if(path==='/ftp/requests/'+request_id)return receipt;if(path==='/ftp/connections/11111111-1111-4111-8111-111111111111/listings/22222222-2222-4222-8222-222222222222')return {listing:receipt,source_available:false};if(path.endsWith('/listings'))return {listings:[receipt],source_available:false};if(path.endsWith('/imports'))return {imports:[],source_available:false};throw Error('意外请求 '+path);
 };
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/FtpDocumentGateway.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,...props})=>React.createElement('button',props,children);root=createRoot(document.getElementById('root'));
  await act(async()=>{root.render(React.createElement(Component,{account:{workspace_id:'workspace',user_id:'admin',role:'admin'},Btn,request}));await Promise.resolve();await Promise.resolve();});
  const button=text=>[...document.querySelectorAll('button')].find(x=>x.textContent===text);assert.ok(button('核对原请求'));
  await act(async()=>{button('核对原请求').click();await Promise.resolve();await Promise.resolve();await Promise.resolve();});
  assert.equal(localStorage.getItem('ftp-document:workspace:admin:pending:'+request_id),null);assert.match(document.body.textContent,/来源当前不可用/);assert.match(document.body.textContent,/读取授权已撤销或不可用/);assert.equal(button('读取目录文件').disabled,true);assert.equal(button('确认导入所选文件').disabled,true);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('late recovery from the previous workspace cannot clear its pending request',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','sessionStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:dom.window.navigator.userAgent,locks:{request:async(_key,_opts,fn)=>fn()}},configurable:true});
 const request_id='53f37e1b-07d2-4ea2-9d9c-91d2fc71c634',submission={request_id,name:'原工作区目录',grant_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},attempt={kind:'connect',submission},key='ftp-document:old:admin:pending:'+request_id;localStorage.setItem(key,JSON.stringify(attempt));let resolveLate;
 const receipt={request_id,state:'succeeded',kind:'connect',submission,result:{id:'c1',name:'原工作区目录',grant_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',version:1,status:'active'}};
 const request=async(path,opts={})=>{if(path==='/ftp/grants')return {grants:[]};if(path==='/ftp/connections')return {connections:[]};if(path==='/ftp/requests/'+request_id&&opts.headers?.['X-Workspace-ID']==='old')return new Promise(resolve=>{resolveLate=()=>resolve(receipt);});throw Error('意外请求');};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/FtpDocumentGateway.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,...props})=>React.createElement('button',props,children);root=createRoot(document.getElementById('root'));
  const props={account:{workspace_id:'old',user_id:'admin',role:'admin'},Btn,request};await act(async()=>{root.render(React.createElement(Component,props));await Promise.resolve();await Promise.resolve();});
  await act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='核对原请求').click();await Promise.resolve();});assert.equal(typeof resolveLate,'function');
  await act(async()=>{root.render(React.createElement(Component,{...props,account:{workspace_id:'new',user_id:'admin',role:'admin'}}));await Promise.resolve();});assert.doesNotMatch(document.body.textContent,/原工作区目录/);
  await act(async()=>{root.render(React.createElement(Component,props));await Promise.resolve();});
  await act(async()=>{resolveLate();await Promise.resolve();await Promise.resolve();});assert.equal(localStorage.getItem(key),JSON.stringify(attempt));assert.ok([...document.querySelectorAll('button')].some(x=>x.textContent==='核对原请求'));
  await act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='核对原请求').click();await Promise.resolve();});
  await act(async()=>{root.render(React.createElement(Component,{...props,account:{...props.account,role:'viewer'}}));await Promise.resolve();});
  await act(async()=>{root.render(React.createElement(Component,props));await Promise.resolve();});
  await act(async()=>{resolveLate();await Promise.resolve();await Promise.resolve();});assert.equal(localStorage.getItem(key),JSON.stringify(attempt));assert.ok([...document.querySelectorAll('button')].some(x=>x.textContent==='核对原请求'));
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
