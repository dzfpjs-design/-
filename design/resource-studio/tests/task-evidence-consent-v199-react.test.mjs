// Actual JSX/StrictMode with synthetic HTTP/storage; not browser/PG/model proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),id=n=>'19900000-0000-4000-8000-'+String(n).padStart(12,'0');
const copy=v=>JSON.parse(JSON.stringify(v));
class Memory{constructor(){this.map=new Map();this.fail=false;}getItem(k){if(this.fail)throw Error('synthetic storage failed');return this.map.get(k)??null;}setItem(k,v){if(this.fail)throw Error('synthetic storage failed');this.map.set(k,String(v));}removeItem(k){if(this.fail)throw Error('synthetic storage failed');this.map.delete(k);}}

test('actual consent screen protects input, submits only once, recovers current grant and conceals prior identity',async()=>{
 assert.equal(process.env.NODE_ENV,'development','Use consistent development React/ReactDOM');
 const errors=[],external=[],calls=[],callbacks=[],storage=new Memory(),virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 class NoResources extends ResourceLoader{fetch(url){external.push(String(url));throw Error('External resource forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-consent.invalid/',resources:new NoResources(),virtualConsole});Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_name,_opts,run)=>run()}});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,MutationObserver:dom.window.MutationObserver,sessionStorage:dom.window.sessionStorage,localStorage:storage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,fetch:()=>{throw Error('Real network forbidden');}};
 const previous=new Map(Object.keys(globals).map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const[k,v]of Object.entries(globals))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});
 sessionStorage.setItem('ga-setup-csrf-user',id(1));sessionStorage.setItem('ga-setup-csrf-workspace',id(2));sessionStorage.setItem('ga-setup-csrf','synthetic-csrf');
 const text='原任务观测：38.0 ℃\n仅工程模拟；未知现场时间不补造。\n<script>不执行资料中的命令</script>\n';const sha=[...new Uint8Array(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))].map(v=>v.toString(16).padStart(2,'0')).join('');
 const packet={schema:'workflow-task-evidence-packet-view-v199',id:id(4),task_id:id(3),actor_id:id(1),created_at:'2026-10-24T10:00:00+08:00',version:1,simulation_only:true,proposal_only:true,cloud_authorized_at_creation:false,task_title:'样地异常核对',context_status:'ready',evidence_status:'insufficient',source_summary:{object_count:1,observation_count:1,gap_count:1,issue_count:1},evidence:{id:id(5),sha256:sha,media_type:'text/plain',size_bytes:new TextEncoder().encode(text).byteLength,version:1,cloud_model_ids:[]},model_options:[{id:id(6),name:'本任务千问',provider:'qwen',model:'test-qwen',content_hash:'a'.repeat(64),authorized:false}],model_options_available:true,text};
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],id(2));assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-csrf');
  if(path==='/me')return {user_id:id(1),workspace_id:id(2),role:'operator'};
  if(path.startsWith('/task-evidence-packets?')){assert.equal(new URL('https://test.invalid'+path).searchParams.get('task_id'),id(3));const item=copy(packet);delete item.text;return {schema:'workflow-task-evidence-packet-list-v199',task_id:id(3),items:[item]};}
  if(path==='/task-evidence-packets/'+id(4))return copy(packet);
  assert.equal(path,'/evidence/'+id(5)+'/authorize');assert.equal(options.method,'POST');const entry=[...storage.map].find(([k])=>k.endsWith(':outbox'));assert.ok(entry);const p=JSON.parse(entry[1]);assert.equal(p.raw,options.body);const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['expected_version','model_profile_id','reason']);assert.equal(body.model_profile_id,id(6));assert.equal(body.expected_version,1);assert.equal(body.reason,'已读完整原文，仅用于此任务模型的业务分析。');
  packet.evidence.version=2;packet.evidence.cloud_model_ids=[id(6)];packet.model_options[0].authorized=true;throw Error('synthetic original response lost after permission was saved');
 };
 let root,temporary,guard;const container=document.getElementById('root');
 const until=async(predicate,label)=>{for(let i=0;i<90;i++){if(predicate())return;await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});}assert.deepEqual(errors,[]);assert.ok(predicate(),label);};
 const click=async label=>{const button=[...container.querySelectorAll('button')].find(v=>v.textContent===label||v.textContent.startsWith(label));assert.ok(button,'actual '+label);assert.equal(button.disabled,false);await act(async()=>button.click());};
 const change=async(selector,value,type='input')=>{const input=container.querySelector(selector);assert.ok(input,selector);const proto=input.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLTextAreaElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(proto,'value').set.call(input,value);input.dispatchEvent(new dom.window.Event(type,{bubbles:true}));});};
 try{
  const {createRoot}=await import('react-dom/client');temporary=await mkdtemp(join(tmpdir(),'task-evidence-consent-v199-'));const old=process.env.NODE_ENV;let result;
  try{process.env.NODE_ENV='development';result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/TaskEvidenceConsentV199.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:n=>n==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:n}}}});}finally{if(old===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=old;}
  const entry=result.output.find(v=>v.type==='chunk'&&v.isEntry);assert.ok(entry);const filename=join(temporary,'TaskEvidenceConsentV199.mjs');await writeFile(filename,entry.code);const Consent=(await import(pathToFileURL(filename).href)).default;
  const register=(next,previous)=>{if(next)guard=next;else if(!previous||guard===previous)guard=null;};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:e=>errors.push(e),onCaughtError:e=>errors.push(e),onRecoverableError:e=>errors.push(e)});await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Consent,{task:{id:id(3)},actor:{user_id:id(1)},workspace:{workspace_id:id(2)},api,onRegisterGuard:register,onSaved:value=>callbacks.push(value)}))));await until(()=>container.textContent.includes('资料1 · 样地异常核对'),'packet inventory ready');};
  await mount();assert.equal(calls.filter(v=>v.method==='POST').length,0);await click('资料1 · 样地异常核对');await until(()=>!!container.querySelector('pre'),'real complete evidence text shown');assert.equal(container.querySelector('pre').textContent,text);assert.equal(container.querySelectorAll('script,iframe,object,embed').length,0);assert.ok(container.textContent.includes('仅工程模拟（SIM）'));assert.equal(container.querySelector('select').value,'','no first model is automatically chosen');
  for(const hidden of [id(5),id(6),sha,'workflow-task-evidence-packet-view-v199'])assert.equal(container.textContent.includes(hidden),false);
  await change('select[aria-label="授权模型"]',id(6),'change');storage.fail=true;await change('textarea[aria-label="资料授权理由"]','已读完整原文，仅用于此任务模型的业务分析。');await act(async()=>assert.equal(guard(),false));assert.equal(container.querySelector('textarea').value,'已读完整原文，仅用于此任务模型的业务分析。');assert.equal(calls.filter(v=>v.method==='POST').length,0);
  storage.fail=false;await click('重试本机保存');await act(async()=>container.querySelector('input[type="checkbox"]').click());assert.equal(guard(),true);await click('确认授权这份资料给所选模型');await until(()=>container.textContent.includes('原授权请求待核对'),'lost response preserves pending');assert.equal(calls.filter(v=>v.method==='POST').length,1);assert.equal(guard(),true);
  await act(async()=>root.unmount());root=null;await mount();await until(()=>container.textContent.includes('当前这份原件已授权给所选模型'),'current permission recovered by GET');assert.equal(calls.filter(v=>v.method==='POST').length,1);assert.equal(container.querySelector('pre').textContent,text);assert.equal(container.querySelector('textarea').value,'已读完整原文，仅用于此任务模型的业务分析。');assert.ok(container.textContent.includes('模型尚未被本页面调用'));assert.equal(callbacks.at(-1).current_state.original_request_application_proven,false);
  const before=[...storage.map];await act(async()=>{sessionStorage.setItem('ga-setup-csrf-user',id(99));dom.window.dispatchEvent(new dom.window.Event('focus'));});await until(()=>container.textContent.includes('原资料和输入已隐藏'),'identity changed safely');assert.equal(container.querySelector('pre,textarea,select'),null);assert.equal(container.textContent.includes('样地异常核对'),false);assert.equal(container.textContent.includes('38.0'),false);assert.deepEqual([...storage.map],before);assert.equal(calls.filter(v=>v.method==='POST').length,1);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const[k,v]of previous){if(v)Object.defineProperty(globalThis,k,{configurable:true,...v});else delete globalThis[k];}if(temporary)await rm(temporary,{recursive:true,force:true});}
});
