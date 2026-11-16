// Actual component + ReactDOM/StrictMode + jsdom events. Only authenticated
// transport is synthetic; this does not claim browser or backend acceptance.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto} from 'node:crypto';
import {createRequire} from 'node:module';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const fromProject=createRequire(join(project,'package.json'));
const id=n=>'20300000-0000-4000-8000-'+String(n).padStart(12,'0');
const copy=value=>JSON.parse(JSON.stringify(value));
const identity={user:id(1),workspace:id(2)},dailyID=id(3),firstID=id(4),secondID=id(5);
const scope={...identity,modality:'audio',daily_id:dailyID};
const outboxKey='ga-media-daily-retry-v203:'+JSON.stringify(scope);
const transportName='__mediaDailyDeliveryReactTransportV203';
let temp,bundled;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});

async function component(){
 if(!bundled)bundled=(async()=>{
  temp=await mkdtemp(join(tmpdir(),'media-daily-react-v203-'));
  // Resolve esbuild through this project's Vite dependency, including pnpm's
  // non-hoisted layout. No globally installed package/runtime is assumed.
  const viteRequire=createRequire(fromProject.resolve('vite/package.json'));
  const {build}=await import(pathToFileURL(viteRequire.resolve('esbuild')).href);
  const output=join(temp,'MediaDailyDelivery.mjs');
  await build({absWorkingDir:project,entryPoints:[join(project,'src/MediaDailyDelivery.jsx')],
   outfile:output,write:true,bundle:true,platform:'node',format:'esm',jsx:'automatic',logLevel:'silent',
   plugins:[{name:'explicit-runtime-transport-seam',setup(build){
    build.onResolve({filter:/^\.\/runtime-api$/},()=>({path:'runtime-api',namespace:'media-daily-seam'}));
    build.onLoad({filter:/.*/,namespace:'media-daily-seam'},()=>({contents:
     `export const json=(...args)=>globalThis.${transportName}(...args);\n`+
     'export const hashBytes=async bytes=>Array.from(new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256",bytes))).map(v=>v.toString(16).padStart(2,"0")).join("");',loader:'js'}));
    build.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(fromProject.resolve(args.path)).href,external:true}));
   }}]});
  // A real generated JS module is imported by the test runner, not a replacement
  // component/controller. Only ./runtime-api was substituted above.
  return (await import(pathToFileURL(output).href)).default;
 })();
 return bundled;
}

class NoResources extends ResourceLoader{fetch(){throw Error('External jsdom resources are forbidden');}}

function revision(revision,status){
 return {event_id:revision===1?firstID:secondID,version:2,modality:'audio',daily_id:dailyID,
  revision,day:'2026-10-01',payload_hash:(revision===1?'a':'b').repeat(64),source_hash:'c'.repeat(64),
  status,received_at:status==='received'?'2026-10-25T12:00:00+00:00':null,attempts:1,
  error:status==='blocked_delivery'?'第一版原音频当时缺失，现需恢复接收':null,
  cloud_calls:0,physical_state_verified:false,can_retry:status==='blocked_delivery'};
}

async function harness(run){
 const errors=[],virtualConsole=new VirtualConsole();
 virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{
  url:'https://synthetic-media-delivery.invalid/',resources:new NoResources(),virtualConsole});
 const state={me:{user_id:identity.user,workspace_id:identity.workspace,role:'admin'},
  first:revision(1,'blocked_delivery'),second:revision(2,'received'),calls:[],posts:[],records:new Map(),
  failMeAfterDurable:0};
 const session=dom.window.sessionStorage,storage=dom.window.localStorage;
 session.setItem('ga-setup-csrf-user',identity.user);
 session.setItem('ga-setup-csrf-workspace',identity.workspace);
 session.setItem('ga-setup-csrf','synthetic-csrf-never-a-real-credential');
 let tail=Promise.resolve();
 const locks={request:(_key,options,action)=>{assert.equal(options.mode,'exclusive');
  const result=tail.then(action);tail=result.catch(()=>{});return result;}};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks});
 const transport=async(path,options={})=>{
  const method=options.method||'GET';state.calls.push({path,method,raw:options.body});
  if(path==='/me'){
   if(state.failMeAfterDurable>0&&storage.getItem(outboxKey)!==null){
    state.failMeAfterDurable--;const error=Error('合成身份核对断网，尚未发送POST');error.status=503;throw error;
   }
   return copy(state.me);
  }
  assert.equal(session.getItem('ga-setup-csrf-user'),identity.user,'transport is reached only by the original account');
  assert.equal(session.getItem('ga-setup-csrf-workspace'),identity.workspace,'transport is reached only in the original workspace');
  if(path==='/media-daily/audio/'+dailyID){
   assert.equal(method,'GET');return {schema:'media-daily-delivery-status-v203',daily_id:dailyID,
    modality:'audio',daily_version:2,current:copy(state.second),revisions:[copy(state.second),copy(state.first)],
    not_authoritative:true,physical_state_verified:false};
  }
  const recovery=path.match(new RegExp('^/media-daily/events/'+firstID+'/requests/([a-f0-9-]+)$'));
  if(recovery){
   assert.equal(method,'GET');const saved=state.records.get(recovery[1]);
   return {schema:'media-daily-retry-request-v203',found:!!saved,
    body:saved?copy(saved.body):null,result:saved?copy(state.first):null};
  }
  assert.equal(path,'/media-daily/events/'+firstID+'/retry');assert.equal(method,'POST');
  assert.equal(typeof options.body,'string');const pending=JSON.parse(storage.getItem(outboxKey));
  assert.equal(pending.raw,options.body,'complete exact original body is durable before the HTTP seam');
  assert.equal(pending.event_id,firstID,'historical R1 is the target, never received R2');
  const sha=Array.from(new Uint8Array(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(options.body)))).map(v=>v.toString(16).padStart(2,'0')).join('');
  assert.equal(pending.sha256,sha,'real WebCrypto validates the stored original bytes');
  const original=JSON.parse(storage.getItem(outboxKey+':original:'+pending.body.request_id));
  assert.equal(original.raw,pending.raw,'the retained original exists independently of the pending pointer');
  const body=JSON.parse(options.body);assert.equal(body.expected_version,2);
  assert.equal(body.payload_hash,'a'.repeat(64));
  const previous=state.records.get(body.request_id);
  if(previous)assert.deepEqual(previous.body,body,'same nonce cannot change the original command');
  else{
   state.records.set(body.request_id,{body:copy(body),raw:options.body});
   state.first={...state.first,status:'pending_delivery',version:3,error:null,can_retry:false};
  }
  state.posts.push({path,raw:options.body,body:copy(body)});
  return copy(state.first);
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,
  HTMLElement:dom.window.HTMLElement,HTMLInputElement:dom.window.HTMLInputElement,
  Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,
  localStorage:storage,sessionStorage:session,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,
  fetch:()=>{throw Error('Real HTTP is forbidden');},[transportName]:transport};
 const prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const container=dom.window.document.getElementById('root');let root;
 const settle=async()=>{for(let index=0;index<12;index++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 const until=async(predicate,label)=>{for(let attempt=0;attempt<100;attempt++){
  if(predicate())return;await act(async()=>{await new Promise(resolve=>setTimeout(resolve,5));});
 }assert.ok(predicate(),label+'; '+container.textContent.slice(0,1500));};
 const button=label=>{const found=[...container.querySelectorAll('button')].find(node=>node.textContent===label);
  assert.ok(found,'actual component button '+label);return found;};
 const click=async label=>{const node=button(label),details=node.closest('details');
  if(details&&!details.open){await act(async()=>details.querySelector('summary').click());
   assert.equal(details.open,true,'actual history disclosure is opened before selecting its revision');}
  assert.equal(node.disabled,false,label+' enabled');
  await act(async()=>node.click());await settle();};
 const input=()=>{const node=container.querySelector('input[maxlength="1000"]');assert.ok(node,'actual reason input is visible');return node;};
 const edit=async value=>{const node=input();assert.equal(node.disabled,false);
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(node,value);
   node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();};
 try{
  const {createRoot}=await import('react-dom/client'),Component=await component();
  const Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),
   onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
   await act(async()=>root.render(React.createElement(React.StrictMode,null,
    React.createElement(Component,{modality:'audio',dailyID,account:{role:'admin'},Btn}))));
   await until(()=>container.textContent.includes('数据管理已接收'),'received R2 loaded');await settle();};
  const unmount=async()=>{if(root){const old=root;root=null;await act(async()=>old.unmount());await settle();}};
  await mount();await run({state,dom,storage,session,container,settle,until,click,button,input,edit,mount,unmount,
   text:()=>container.textContent});
  await unmount();assert.deepEqual(errors,[],'real component renders without React/DOM errors');
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();
  for(const[key,descriptor]of prior){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
 }
}

test('actual history control selects blocked R1 beneath received R2; human reason survives real unmount/remount',async()=>harness(async h=>{
 assert.ok(h.text().includes('第2版已接收'));
 assert.ok(h.text().includes('第1版：数据接收受阻'));
 assert.equal(h.container.querySelector('input'),null,'received current head does not expose a wrong retry form');
 await h.click('重试接收第1版');assert.ok(h.text().includes('重试对象：第1版整理结果。'));
 await h.edit('第一版原音频已恢复，保留后续第二版记录。');
 assert.equal(h.storage.getItem(outboxKey+':reason'),'第一版原音频已恢复，保留后续第二版记录。');
 assert.equal(h.state.posts.length,0);
 await h.unmount();await h.mount();await h.click('重试接收第1版');
 assert.equal(h.input().value,'第一版原音频已恢复，保留后续第二版记录。');
 assert.equal(h.state.posts.length,0,'restoring human work never submits it');
}));

test('durable original followed by /me network failure recovers found:false then sends exact same nonce/raw once through real buttons',async()=>harness(async h=>{
 await h.click('重试接收第1版');await h.edit('恢复第一版原件后，仅重试这一份每日整理的接收。');
 h.state.failMeAfterDurable=1;
 await h.click('重试接收这一版结果');
 await h.until(()=>h.storage.getItem(outboxKey)!==null,'pending original is durably saved');
 const originalRaw=h.storage.getItem(outboxKey),original=JSON.parse(originalRaw);
 assert.equal(h.state.posts.length,0,'/me failure occurred before any POST');
 assert.equal(original.event_id,firstID);assert.equal(original.body.expected_version,2);
 assert.equal(original.body.payload_hash,'a'.repeat(64));
 assert.equal(JSON.parse(h.storage.getItem(outboxKey+':original:'+original.body.request_id)).raw,original.raw);
 await h.unmount();await h.mount();assert.equal(h.storage.getItem(outboxKey),originalRaw);
 assert.ok(h.text().includes('这次重试已在本机保存'));
 await h.click('核对这次重试的结果');assert.equal(h.state.posts.length,0);
 assert.equal(h.storage.getItem(outboxKey),originalRaw,'GET found:false keeps exact original pointer');
 await h.click('继续提交这次重试');
 await h.until(()=>h.storage.getItem(outboxKey)===null,'original request recovered and pointer safely cleared');
 assert.equal(h.state.posts.length,1);assert.equal(h.state.posts[0].raw,original.raw);
 assert.equal(h.state.posts[0].body.request_id,original.body.request_id);
 assert.equal(h.state.posts[0].path,'/media-daily/events/'+firstID+'/retry');
 assert.equal(h.state.first.status,'pending_delivery','accepted retry is not fabricated as ACK');
 assert.equal(h.state.second.status,'received','received R2 was not retried or substituted');
 const saved=JSON.parse(h.storage.getItem(outboxKey+':received:'+original.body.request_id));
 assert.equal(saved.original.raw,original.raw);assert.equal(saved.result.event_id,firstID);
 assert.equal(saved.result.payload_hash,original.body.payload_hash);
 await h.unmount();await h.mount();assert.equal(h.state.posts.length,1);
 assert.ok(h.text().includes('第1版：等待数据管理接收'));
}));

for(const changed of ['user','workspace'])test('actual '+changed+' switch conceals source and human input, preserves original pending, and refuses later writes',async()=>harness(async h=>{
 await h.click('重试接收第1版');await h.edit('此说明只能由原账号和工作区继续核对。');
 h.state.failMeAfterDurable=1;await h.click('重试接收这一版结果');
 await h.until(()=>h.storage.getItem(outboxKey)!==null,'original pending exists');
 const original=h.storage.getItem(outboxKey),reason=h.storage.getItem(outboxKey+':reason');
 const staleButton=h.button('核对这次重试的结果');assert.equal(h.state.posts.length,0);
 h.session.setItem(changed==='user'?'ga-setup-csrf-user':'ga-setup-csrf-workspace',id(90));
 h.state.me={...h.state.me,[changed==='user'?'user_id':'workspace_id']:id(90)};
 await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();
 assert.ok(h.text().includes('原接收请求已隐藏并保留'));
 assert.equal(h.container.querySelector('input'),null);assert.equal(h.container.querySelector('button'),null);
 assert.equal(h.text().includes(reason),false);assert.equal(h.text().includes('第一版原音频'),false);
 assert.equal(h.text().includes('第2版'),false);
 const calls=h.state.calls.length;
 await act(async()=>{staleButton.click();h.dom.window.dispatchEvent(new h.dom.window.Event('focus'));});await h.settle();
 assert.equal(h.state.posts.length,0);assert.equal(h.state.calls.length,calls,'concealed surface cannot perform another transport write/read');
 assert.equal(h.storage.getItem(outboxKey),original);
 assert.equal(h.storage.getItem(outboxKey+':reason'),reason);
 assert.ok(h.storage.getItem(outboxKey+':original:'+JSON.parse(original).body.request_id));
}));
