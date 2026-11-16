import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {randomUUID} from 'node:crypto';

test('只读来源登记响应丢失后刷新、核对原请求，不重复创建',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),old=new Map(['window','document','navigator','localStorage','crypto','IS_REACT_ACT_ENVIRONMENT'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});
 const held=new Set();Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(key,opts,fn)=>{if(held.has(key)&&opts.ifAvailable)return fn(null);held.add(key);try{return await fn({})}finally{held.delete(key)}}}});
 const require=createRequire(import.meta.url),React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;
 const built=await build({entryPoints:['src/DjiCloudAccess.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),loaded={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,loaded,loaded.exports);
 const Studio=loaded.exports.default,Btn=({children,...p})=>React.createElement('button',p,children),account={workspace_id:'w',user_id:'u',role:'admin'};
 let sent,posts=0,published=null;const connection={id:'conn',name:'林区机场',broker:'mqtt://192.168.0.9:1883',device_sn:'DOCK123',topic:'thing/product/DOCK123/osd',version:1,status:'paused'};
 const request=async(path,opts={})=>{
  if(path==='/dji/cloud/options')return {brokers:[{value:'auth-1',label:'林区服务',broker:connection.broker,authorization_id:'auth-1'}]};
  if(path.startsWith('/dji/cloud/connections?page='))return {connections:sent?[connection]:[],page:1,has_more:false};
  if(path==='/dji/cloud/connections'&&opts.method==='POST'){sent=JSON.parse(opts.body);posts++;throw Error('连接中断')}
  if(path==='/dji/cloud/operations/'+sent?.request_id)return {request_id:sent.request_id,state:'succeeded',connection};
  if(path==='/dji/cloud/connections/conn/status'&&opts.method==='POST'){const b=JSON.parse(opts.body);connection.status=b.status;connection.version=b.expected_version+1;return {request_id:b.request_id,state:'succeeded',connection:{...connection}}}
  if(path==='/dji/cloud/connections/conn/capability'){const b=JSON.parse(opts.body);published={id:'cap1',name:'机场状态只读',status:'published',version:1,handler:'dji_cloud_dock_status_v1'};return {request_id:b.request_id,state:'succeeded',connection_id:'conn',capability:published}}
  if(path==='/dji/cloud/connections/conn/status')return {connection:{...connection,capability:published},receiver:null,latest:null};
  throw Error('unexpected '+path);
 };
 let root;const button=text=>[...document.querySelectorAll('button')].find(x=>x.textContent===text),label=text=>[...document.querySelectorAll('label')].find(x=>x.textContent.startsWith(text))?.querySelector('input,select');
 async function fill(text,value){const node=label(text);assert.ok(node,text);await act(async()=>{const proto=node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));await Promise.resolve()})}
 const mount=async(role='admin')=>{root=createRoot(document.getElementById('root'));await act(async()=>{root.render(React.createElement(Studio,{account:{...account,role},Btn,request}));await new Promise(r=>setTimeout(r,0))})};
 try{
  await mount();assert.match(document.body.textContent,/不会完成大疆设备上云握手/);
  await fill('来源名称','林区机场');await fill('已允许的 MQTT 服务','auth-1');await fill('机场设备序列号','DOCK123');
  await act(async()=>{button('保存来源').click();await new Promise(r=>setTimeout(r,0))});
  assert.equal(posts,1);assert.equal(sent.authorization_id,'auth-1');assert.equal(sent.username_ref,undefined);assert.match(localStorage.getItem('dji-cloud:w:u:pending'),/DOCK123/);assert.match(document.body.textContent,/结果尚未核对/);
  await act(async()=>root.unmount());await mount('viewer');assert.match(document.body.textContent,/上次操作结果待核对/);assert.doesNotMatch(document.body.textContent,/添加大疆机场状态来源/);
  await act(async()=>{button('核对原请求').click();await new Promise(r=>setTimeout(r,0))});
  assert.equal(posts,1);assert.equal(localStorage.getItem('dji-cloud:w:u:pending'),null);assert.match(document.body.textContent,/林区机场/);assert.match(document.body.textContent,/保存配置不代表设备已连接/);
  await act(async()=>root.unmount());await mount();await fill('选择来源','conn');
  await act(async()=>{button('启用接收').click();await Promise.resolve()});assert.match(document.body.textContent,/确认上述服务器/);
  await act(async()=>{button('确认启用接收').click();await new Promise(r=>setTimeout(r,0))});
  await act(async()=>{button('配置只读工具').click();await Promise.resolve()});assert.match(document.body.textContent,/不包含起飞、航线、返航/);
  await act(async()=>{button('确认发布只读工具').click();await new Promise(r=>setTimeout(r,0))});assert.match(document.body.textContent,/已发布只读工具：机场状态只读/);
  connection.authorization_available=false;await fill('选择来源','');await fill('选择来源','conn');assert.match(document.body.textContent,/MQTT 服务授权已停用或不可用/);assert.match(document.body.textContent,/当前接入不可用，工具不能读取/);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k]}}
});

test('多页面编辑锁和分页来源均可恢复；普通成员只读',async()=>{
 const dom=new JSDOM('<div id="a"></div><div id="b"></div>',{url:'http://localhost/setup/'}),old=new Map(['window','document','navigator','localStorage','crypto','IS_REACT_ACT_ENVIRONMENT'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));Object.assign(globalThis,{window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});
 const held=new Set();Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(key,opts,fn)=>{if(held.has(key)&&opts.ifAvailable)return fn(null);held.add(key);try{return await fn({})}finally{held.delete(key)}}}});
 const require=createRequire(import.meta.url),React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;const built=await build({entryPoints:['src/DjiCloudAccess.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),loaded={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,loaded,loaded.exports);
 const Studio=loaded.exports.default,Btn=({children,...p})=>React.createElement('button',p,children),account={workspace_id:'w',user_id:'u',role:'admin'};const row=i=>({id:'c'+i,name:'机场'+i,device_sn:'SN'+i,status:'paused',version:1});
 const request=async(path)=>{if(path.endsWith('/options'))return {brokers:[{value:'mqtt://1.1.1.1:1883',label:'服务'}]};if(path.includes('?page=')){const page=Number(path.split('=').at(-1));return {connections:[row(page)],page,has_more:page===1}};throw Error(path)};
 const a=createRoot(document.querySelector('#a')),b=createRoot(document.querySelector('#b'));
 try{
  await act(async()=>{a.render(React.createElement(Studio,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});await act(async()=>{b.render(React.createElement(Studio,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});
  assert.equal(document.querySelector('#a fieldset').disabled,false);assert.equal(document.querySelector('#b fieldset').disabled,true);
  await act(async()=>{[...document.querySelectorAll('#a button')].find(x=>x.textContent==='加载更多来源').click();await new Promise(r=>setTimeout(r,0))});assert.match(document.querySelector('#a').textContent,/机场2/);
  await act(async()=>{a.unmount();await new Promise(r=>setTimeout(r,0))});await act(async()=>{[...document.querySelectorAll('#b button')].find(x=>x.textContent==='重新取得编辑权').click();await new Promise(r=>setTimeout(r,0))});assert.equal(document.querySelector('#b fieldset').disabled,false);
  await act(async()=>{b.render(React.createElement(Studio,{account:{...account,role:'viewer'},Btn,request}));await new Promise(r=>setTimeout(r,0))});assert.doesNotMatch(document.querySelector('#b').textContent,/添加大疆机场状态来源/);assert.match(document.querySelector('#b').textContent,/机场1/);
 }finally{await act(async()=>b.unmount());dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k]}}
});
