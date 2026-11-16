import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {randomUUID} from 'node:crypto';
const require=createRequire(import.meta.url),React=require('react'),{act}=React;
let createRoot;
async function component(){const result=await build({entryPoints:['src/ManagedMqttAuthorizations.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),mod={exports:{}};new Function('require','module','exports',result.outputFiles[0].text)(require,mod,mod.exports);return mod.exports.default}
function setup(html,locks){const dom=new JSDOM(html,{url:'http://localhost'});Object.assign(globalThis,{window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});Object.defineProperty(dom.window.navigator,'locks',{value:locks});return dom}
function cleanup(dom){dom.window.close();for(const k of ['window','document','localStorage','navigator','crypto'])delete globalThis[k]}
const Btn=({children,...p})=>React.createElement('button',p,children),account={workspace_id:'w',user_id:'u',role:'admin'};
test('pending survives downgrade and credentials stay out of storage',async()=>{
 const dom=setup('<div id="root"></div>',{request:async(_k,_o,cb)=>cb({})});createRoot=require('react-dom/client').createRoot;const Studio=await component(),root=createRoot(document.getElementById('root'));let sent;
 const request=async(path,o={})=>{if(path==='/mqtt/authorizations'&&!o.method)return {enabled:true,key_ready:true,networks:['192.168.0.0/24'],authorizations:[]};if(path==='/mqtt/authorizations'){sent=JSON.parse(o.body);throw Error('断线')}if(path.endsWith('/cancel'))return {request_id:path.split('/').at(-2),state:'cancelled'};throw Error(path)};
 async function fill(label,value){const node=[...document.querySelectorAll('label')].find(x=>x.textContent.startsWith(label)).querySelector('input');await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})}
 try{
  await act(async()=>{root.render(React.createElement(Studio,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});await fill('授权名称','测试');await fill('服务 IP','192.168.0.10');await fill('用户名','synthetic-user');await fill('密码','synthetic-password');
  await act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='保存服务授权').click();await new Promise(r=>setTimeout(r,0))});assert.equal(sent.password,'synthetic-password');assert.doesNotMatch(Object.values(localStorage).join(' '),/synthetic-user|synthetic-password/);
  await act(async()=>{root.render(React.createElement(Studio,{account:{...account,role:'viewer'},Btn,request}));await new Promise(r=>setTimeout(r,0))});assert.match(document.body.textContent,/上次授权请求结果尚未核对/);
  await act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='停止原请求').click();await new Promise(r=>setTimeout(r,0))});assert.equal(localStorage.getItem('managed-mqtt:w:u:pending'),null);
 }finally{await act(async()=>root.unmount());cleanup(dom)}
});
test('only one browser tab edits a saved MQTT draft',async()=>{
 const held=new Set(),dom=setup('<div id="a"></div><div id="b"></div>',{request:async(k,o,cb)=>{if(held.has(k)&&o.ifAvailable)return cb(null);held.add(k);try{return await cb({})}finally{held.delete(k)}}});createRoot=require('react-dom/client').createRoot;const Studio=await component(),a=createRoot(document.getElementById('a')),b=createRoot(document.getElementById('b')),request=async()=>({enabled:true,key_ready:true,networks:['192.168.0.0/24'],authorizations:[]});
 try{
  await act(async()=>{a.render(React.createElement(Studio,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});await act(async()=>{b.render(React.createElement(Studio,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});assert.equal(document.querySelector('#a fieldset').disabled,false);assert.equal(document.querySelector('#b fieldset').disabled,true);
  const input=[...document.querySelectorAll('#a label')].find(x=>x.textContent.startsWith('授权名称')).querySelector('input');await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(input,'林区服务');input.dispatchEvent(new dom.window.Event('input',{bubbles:true}))});
  await act(async()=>{a.unmount();await new Promise(r=>setTimeout(r,0))});await act(async()=>{[...document.querySelectorAll('#b button')].find(x=>x.textContent==='接手编辑').click();await new Promise(r=>setTimeout(r,0))});assert.equal(document.querySelector('#b fieldset').disabled,false);assert.equal([...document.querySelectorAll('#b label')].find(x=>x.textContent.startsWith('授权名称')).querySelector('input').value,'林区服务');
 }finally{await act(async()=>b.unmount());cleanup(dom)}
});
