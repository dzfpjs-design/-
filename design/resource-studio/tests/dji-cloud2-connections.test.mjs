import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {randomUUID} from 'node:crypto';
const require=createRequire(import.meta.url),React=require('react'),{act}=React;
async function component(){const out=await build({entryPoints:['src/DjiCloud2Connections.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),mod={exports:{}};new Function('require','module','exports',out.outputFiles[0].text)(require,mod,mod.exports);return mod.exports.default;}
const Btn=({children,...p})=>React.createElement('button',p,children),account={workspace_id:'workspace',user_id:'operator',role:'admin'},wait=()=>new Promise(r=>setTimeout(r,0));
function setup(){const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'});Object.assign(globalThis,{window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(_k,_o,cb)=>cb({})}});return dom;}
function cleanup(dom){dom.window.close();for(const k of ['window','document','localStorage','navigator','crypto'])delete globalThis[k];}
function button(text){const b=[...document.querySelectorAll('button')].find(x=>x.textContent===text);assert.ok(b,text);return b;}
async function fill(dom,label,value){const el=[...document.querySelectorAll('label')].find(x=>x.textContent.startsWith(label))?.querySelector('input');assert.ok(el,label);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));await wait();});}
test('保存丢包后可刷新并核对，凭据不进入浏览器存储',async()=>{
 const dom=setup(),Studio=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));let saved,posts=0;
 const req=async(path,o={})=>{assert.equal(o.headers['X-Workspace-ID'],'workspace');if(path==='/dji/cloud2/connections'&&!o.method)return {connections:saved?[saved.connection]:[]};if(path==='/dji/cloud2/connections'&&o.method==='POST'){posts++;const body=JSON.parse(o.body);assert.deepEqual(body.http_headers,[{name:'X-Http',value:'synthetic-http-secret'},{name:'user_id',value:'synthetic-second-secret'}]);assert.deepEqual(body.ws_headers,[{name:'X-Ws',value:'synthetic-ws-secret'}]);saved={request_id:body.request_id,state:'succeeded',connection:{id:'cfg',version:1,name:body.name,host:body.host,port:body.port,status:'not_verified',enabled:true}};throw Error('lost response');}if(path.startsWith('/dji/cloud2/operations/'))return saved;throw Error(path);};
 try{await act(async()=>{root.render(React.createElement(Studio,{account,Btn,request:req}));await wait();});
  for(const [k,v] of [['连接名称','测试底座'],['底座地址','example.test'],['HTTP 鉴权请求头名称','X-Http'],['HTTP 鉴权请求头值','synthetic-http-secret'],['消息连接 鉴权请求头名称','X-Ws'],['消息连接 鉴权请求头值','synthetic-ws-secret']])await fill(dom,k,v);
  await act(async()=>{button('添加 HTTP鉴权头').click();await wait();});await fill(dom,'HTTP 鉴权请求头名称 2','user_id');await fill(dom,'HTTP 鉴权请求头值 2','synthetic-second-secret');
  await act(async()=>{button('保存连接').click();await wait();});assert.equal(posts,1);assert.match(document.body.textContent,/上次操作结果待核对/);
  assert.doesNotMatch(Object.values(localStorage).join(' '),/synthetic-(http|ws|second)-secret/);
  await act(async()=>{root.render(React.createElement(Studio,{account:{...account,role:'viewer'},Btn,request:req}));await wait();});assert.match(document.body.textContent,/上次操作结果待核对/);
  await act(async()=>{button('核对原请求').click();await wait();});assert.equal(posts,1);assert.equal(localStorage.getItem('dji-cloud2:workspace:operator:pending'),null);assert.match(document.body.textContent,/连接配置已保存/);assert.match(document.body.textContent,/尚未验证/);
 }finally{await act(async()=>root.unmount());cleanup(dom);}
});
test('撤权需要确认，回执丢失后不会重复撤权',async()=>{
 const dom=setup(),Studio=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));let revoked=false,op,posts=0;
 const row=()=>({id:'cfg',version:revoked?2:1,name:'连接一',host:'example.test',port:443,status:revoked?'revoked':'not_verified',enabled:!revoked});
 const req=async(path,o={})=>{if(path==='/dji/cloud2/connections')return {connections:[row()]};if(path.endsWith('/revoke')){posts++;const body=JSON.parse(o.body);revoked=true;op={request_id:body.request_id,state:'succeeded',connection:row()};throw Error('lost response');}if(path.startsWith('/dji/cloud2/operations/'))return op;throw Error(path);};
 try{await act(async()=>{root.render(React.createElement(Studio,{account,Btn,request:req}));await wait();});await act(async()=>{button('撤销授权').click();await wait();});assert.equal(posts,0);
  await act(async()=>{button('确认撤销').click();await wait();});assert.equal(posts,1);assert.match(document.body.textContent,/上次操作结果待核对/);
  await act(async()=>{button('核对原请求').click();await wait();});assert.equal(posts,1);assert.match(document.body.textContent,/授权已撤销/);assert.match(document.body.textContent,/已撤销该连接的本地授权/);
 }finally{await act(async()=>root.unmount());cleanup(dom);}
});
test('地址未获授权时显示原因并能安全结束原请求，输入的鉴权值不落盘',async()=>{
 const dom=setup(),Studio=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));let requestId,guard;
 const req=async(path,o={})=>{if(path==='/dji/cloud2/connections'&&!o.method)return {connections:[]};if(path==='/dji/cloud2/connections'){requestId=JSON.parse(o.body).request_id;throw Error('地址或端口不在系统管理员允许的范围内');}if(path.endsWith('/cancel'))return {request_id:requestId,state:'cancelled'};throw Error(path);};
 try{await act(async()=>{root.render(React.createElement(Studio,{account,Btn,request:req,onRegisterGuard:g=>{if(g)guard=g;}}));await wait();});
  for(const [k,v] of [['连接名称','测试底座'],['底座地址','192.0.2.10'],['HTTP 鉴权请求头名称','X-Http'],['HTTP 鉴权请求头值','synthetic-http-secret'],['消息连接 鉴权请求头名称','X-Ws'],['消息连接 鉴权请求头值','synthetic-ws-secret']])await fill(dom,k,v);
  assert.equal(guard(),false);
  await act(async()=>{button('保存连接').click();await wait();});assert.match(document.body.textContent,/地址或端口不在系统管理员允许的范围内/);assert.equal(guard(),false);
  await act(async()=>{button('安全结束原请求').click();await wait();});assert.match(document.body.textContent,/原请求已安全结束/);assert.equal(guard(),true);
  assert.doesNotMatch(Object.values(localStorage).join(' '),/synthetic-(http|ws)-secret/);assert.equal(document.querySelector('input[type=password]').value,'');
 }finally{await act(async()=>root.unmount());cleanup(dom);}
});
test('允许增删多条鉴权头，已失效授权不会显示为已连接',async()=>{
 const dom=setup(),Studio=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));
 const req=async()=>({connections:[{id:'cfg',version:1,name:'底座',host:'192.0.2.10',port:443,status:'authorization_lost',enabled:true,http_header_names:['user_id','X-User-Token'],ws_header_names:['user_id']}]});
 try{await act(async()=>{root.render(React.createElement(Studio,{account,Btn,request:req}));await wait();});assert.match(document.body.textContent,/授权已失效，连接已停用/);assert.match(document.body.textContent,/user_id、X-User-Token/);assert.doesNotMatch(document.body.textContent,/上次连接成功/);
  await fill(dom,'HTTP 鉴权请求头名称','user_id');await fill(dom,'HTTP 鉴权请求头值','synthetic-first-secret');await act(async()=>{button('添加 HTTP鉴权头').click();await wait();});await fill(dom,'HTTP 鉴权请求头名称 2','X-User-Token');await fill(dom,'HTTP 鉴权请求头值 2','synthetic-second-secret');
  await act(async()=>{button('删除 HTTP鉴权头 1').click();await wait();});assert.equal(document.querySelector('input[type=password]').value,'synthetic-second-secret');assert.doesNotMatch(Object.values(localStorage).join(' '),/synthetic-(first|second)-secret/);
 }finally{await act(async()=>root.unmount());cleanup(dom);}
});
test('设备查询成功不代表消息通道已连接',async()=>{
 const dom=setup(),Studio=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));
 try{await act(async()=>{root.render(React.createElement(Studio,{account,Btn,request:async()=>({connections:[{id:'cfg',version:1,name:'底座',host:'192.0.2.10',port:443,status:'connected',enabled:true}]})}));await wait();});assert.match(document.body.textContent,/上次设备查询成功；消息连接未验证/);assert.match(document.body.textContent,/后台尚不能连接底座，设备查询和断线恢复尚不能使用/);}
 finally{await act(async()=>root.unmount());cleanup(dom);}
});
