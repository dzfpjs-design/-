import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {randomUUID} from 'node:crypto';
const require=createRequire(import.meta.url),React=require('react'),{act}=React;
async function component(){const out=await build({entryPoints:['src/DjiOnboardingStudio.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),mod={exports:{}};new Function('require','module','exports',out.outputFiles[0].text)(require,mod,mod.exports);return mod.exports.default}
const Btn=({children,...p})=>React.createElement('button',p,children),account={workspace_id:'workspace',user_id:'operator',role:'admin'},grant='a'.repeat(64);
function setup(html,locks){const dom=new JSDOM(html,{url:'http://localhost/setup/'});Object.assign(globalThis,{window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});Object.defineProperty(dom.window.navigator,'locks',{value:locks});return dom}
function cleanup(dom){dom.window.close();for(const key of ['window','document','localStorage','navigator','crypto'])delete globalThis[key]}
const wait=()=>new Promise(r=>setTimeout(r,0));
function button(root,text){return [...root.querySelectorAll('button')].find(x=>x.textContent===text)}
async function fill(dom,root,label,value){const wrap=[...root.querySelectorAll('label')].find(x=>x.textContent.startsWith(label));assert.ok(wrap,label);const el=wrap.querySelector('input,select,textarea');const proto=el.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:el.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);el.dispatchEvent(new dom.window.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));await wait()})}
async function check(root,label){const input=[...root.querySelectorAll('label')].find(x=>x.textContent.includes(label)).querySelector('input');await act(async()=>{input.click();await wait()})}
test('凭据只留内存，响应丢失后降权也可核对并恢复已保存配置',async()=>{
 const dom=setup('<div id="root"></div>',{request:async(_k,_o,cb)=>cb({})}),Studio=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));let saved,requestBody,posts=0;
 const req=async(path,o={})=>{if(path==='/mqtt/authorizations')return {enabled:true,key_ready:true,authorizations:[{id:grant,name:'测试 MQTT',broker:'mqtts://192.168.0.20:8883',enabled:true}]};if(path==='/dji/onboarding/configurations'&&!o.method)return {experimental_enabled:true,configurations:saved?[saved.configuration]:[]};if(path==='/dji/onboarding/configurations'&&o.method==='POST'){posts++;requestBody=JSON.parse(o.body);saved={request_id:requestBody.request_id,state:'succeeded',configuration:{id:'cfg-id',version:1,dock_sn:requestBody.config.dock_sn,aircraft_sn:requestBody.config.aircraft_sn,organization_name:requestBody.config.organization_name,broker_authorization_id:grant,enabled:false,receiver_state:'waiting'}};throw Error('断线')};if(path.startsWith('/dji/onboarding/operations/'))return saved;throw Error(path)};
 try{
  await act(async()=>{root.render(React.createElement(Studio,{account,Btn,request:req}));await wait()});
  for(const [label,value] of [['已授权 MQTT 服务',grant],['机场序列号','SYNTH_DOCK'],['飞机序列号','SYNTH_AIR'],['组织标识','synthetic_org'],['组织名称','测试组织'],['时间服务器','ntp.example.test'],['机场绑定码','fake-dock-code'],['飞机绑定码','fake-air-code'],['大疆应用 ID','fake-app-id'],['大疆应用 Key','fake-app-key'],['大疆应用许可证','fake-license']])await fill(dom,document,label,value);
  await check(document,'我已核对专用 MQTT');await check(document,'我已核对机场');
  await act(async()=>{button(document,'保存并暂停').click();await wait()});assert.equal(posts,1);assert.equal(requestBody.config.app_key,'fake-app-key');
  const stored=Object.values(localStorage).join(' ');for(const value of ['fake-dock-code','fake-air-code','fake-app-id','fake-app-key','fake-license'])assert.equal(stored.includes(value),false);
  assert.match(document.body.textContent,/上次操作结果待核对/);
  await act(async()=>{root.render(React.createElement(Studio,{account:{...account,role:'viewer'},Btn,request:req}));await wait()});assert.match(document.body.textContent,/上次操作结果待核对/);
  await act(async()=>{button(document,'核对原请求').click();await wait()});assert.equal(posts,1);assert.equal(localStorage.getItem('dji-onboarding:workspace:operator:pending'),null);assert.match(document.body.textContent,/配置已保存并暂停/);assert.match(document.body.textContent,/SYNTH_DOCK/);
 }finally{await act(async()=>root.unmount());cleanup(dom)}
});
test('多标签只允许一个编辑者，并且启用必须先确认',async()=>{
 const held=new Set(),dom=setup('<div id="a"></div><div id="b"></div>',{request:async(k,o,cb)=>{if(held.has(k)&&o.ifAvailable)return cb(null);held.add(k);try{return await cb({})}finally{held.delete(k)}}}),Studio=await component(),a=require('react-dom/client').createRoot(document.getElementById('a')),b=require('react-dom/client').createRoot(document.getElementById('b'));let statusPosts=0;
 const saved={id:'cfg-id',version:1,dock_sn:'SYNTH_DOCK',aircraft_sn:'SYNTH_AIR',organization_name:'测试组织',broker_authorization_id:grant,enabled:false,receiver_state:'waiting'};
 const req=async(path,o={})=>{if(path==='/mqtt/authorizations')return {enabled:true,key_ready:true,authorizations:[{id:grant,name:'测试 MQTT',broker:'mqtts://192.168.0.20:8883',enabled:true}]};if(path==='/dji/onboarding/configurations')return {experimental_enabled:true,configurations:[saved]};if(path.endsWith('/status')){statusPosts++;const x=JSON.parse(o.body);return {request_id:x.request_id,state:'succeeded',configuration:{...saved,version:2,enabled:true}}}throw Error(path)};
 try{
  await act(async()=>{a.render(React.createElement(Studio,{account,Btn,request:req,enableVerified:true}));await wait()});await act(async()=>{b.render(React.createElement(Studio,{account,Btn,request:req,enableVerified:true}));await wait()});assert.equal(document.querySelector('#a fieldset').disabled,false);assert.equal(document.querySelector('#b fieldset').disabled,true);
  await act(async()=>{button(document.querySelector('#a'),'启用接收').click();await wait()});assert.equal(statusPosts,0);assert.match(document.querySelector('#a').textContent,/确认启用/);
  await act(async()=>{button(document.querySelector('#a'),'确认启用').click();await wait()});assert.equal(statusPosts,1);
  await act(async()=>{a.unmount();await wait()});await act(async()=>{button(document.querySelector('#b'),'接手编辑').click();await wait()});assert.equal(document.querySelector('#b fieldset').disabled,false);
 }finally{await act(async()=>b.unmount());cleanup(dom)}
});
test('受控测试关闭时禁止新增和启用，仍可暂停',async()=>{
 const dom=setup('<div id="root"></div>',{request:async(_k,_o,cb)=>cb({})}),Studio=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));
 const rows=[{id:'paused',version:1,dock_sn:'D1',aircraft_sn:'A1',organization_name:'测试',enabled:false},{id:'running',version:1,dock_sn:'D2',aircraft_sn:'A2',organization_name:'测试',enabled:true}];
 const req=async(path)=>path==='/mqtt/authorizations'?{enabled:true,key_ready:true,authorizations:[]}:{experimental_enabled:false,configurations:rows};
 try{await act(async()=>{root.render(React.createElement(Studio,{account,Btn,request:req,enableVerified:true}));await wait()});assert.match(document.body.textContent,/受控测试默认关闭/);assert.equal(document.querySelector('fieldset').disabled,true);assert.equal(button(document,'启用接收').disabled,true);assert.equal(button(document,'暂停接收').disabled,false)}finally{await act(async()=>root.unmount());cleanup(dom)}
});
