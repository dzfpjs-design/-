import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act,useEffect} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {graphSourceCommandHash,graphSourceRetirementHash} from '../src/graph-sources-controller-v122.mjs';

// Real mounted Panel, controller, hooks and runtime-api. Only fetch/storage are
// synthetic boundaries. Every fetch path is explicitly allowed; no network IO.
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temp=await mkdtemp(join(tmpdir(),'graph-sources-real-react-'));
  const result=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:join(project,'src/GraphSourcesPanel.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const file=join(temp,'GraphSourcesPanel.mjs');await writeFile(file,chunk.code);return (await import(pathToFileURL(file).href)).default;
 })();return bundle;
}
const ID=n=>'82000000-0000-4000-8000-'+String(n).padStart(12,'0'),H='b'.repeat(64),copy=x=>JSON.parse(JSON.stringify(x));
const scope={actor_id:ID(1),workspace_id:ID(2)};
class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden in source-panel DOM test');}}
async function harness(run){
 const errors=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-sources.invalid/',resources:new NoResources(),virtualConsole});
 const names=['window','document','navigator','HTMLElement','Event','localStorage','sessionStorage','crypto','fetch','IS_REACT_ACT_ENVIRONMENT'],descriptors=Object.fromEntries(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
 for(const [name,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});
 const local=dom.window.localStorage,session=dom.window.sessionStorage,container=dom.window.document.getElementById('root');
 session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-csrf');
 const state={loseRegister:false,loseRevoke:false,noCommit:false,loseRetirement:false,quota:false,returns:0,busy:[],records:new Map(),revocations:new Map(),retirements:new Map(),probe:{setup:0,cleanup:0},cookieActor:scope.actor_id,meError:null,holdMe:false,meWaiters:[],holdCatalog:false,catalogWaiters:[]},calls=[],unexpected=[];
 const source={id:ID(3),name:'合成监测来源',kind:'source',content_hash:H,release_version:'1.0.0'},extractor={id:ID(4),name:'合成温度处理',kind:'tool',content_hash:H,release_version:'1.0.0'};
 const makeContract=body=>({schema:'graph-event-observation-source-contract-v1',adapter_id:body.adapter_id,event_kind:body.adapter_id==='processed-observation-v1'?'observation.data':'perception.anomaly',correlation_kind:'entity_id',source_id:body.source_id,source_hash:body.source_hash,source_release_version:'1.0.0',extractor_id:body.extractor_id,extractor_hash:body.extractor_hash,extractor_release_version:'1.0.0',result_schema:{type:'object',properties:{temperature:{type:'number',title:'温度'}},required:['temperature'],additionalProperties:false},output_schema:{type:'object',properties:{result:{type:'object'}}},output_schema_hash:H,contract_hash:H});
 const catalog=row=>({...copy(row),source_name:source.name,extractor_name:extractor.name,current_availability:{available:row.status==='active',reason_code:row.status==='active'?'available':'registration_stopped',label:row.status==='active'?'发布版本可核对':'已停止'},receipt_verified:false});
 const originalSet=dom.window.Storage.prototype.setItem;dom.window.Storage.prototype.setItem=function(key,value){if(state.quota&&this===local)throw Error('synthetic local quota');return originalSet.call(this,key,value);};
 const response=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
 const catalogResponse=async value=>{if(state.holdCatalog)await new Promise(resolve=>state.catalogWaiters.push(resolve));return response(value);};
 Object.defineProperty(globalThis,'fetch',{configurable:true,writable:true,value:async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,body:options.body});assert.equal(options.credentials,'same-origin');assert.equal(options.headers['X-Workspace-ID'],scope.workspace_id);
  const base='/api/setup/graph-event-producers';
  if(path==='/api/me'){
   const actorAtRequest=state.cookieActor,errorAtRequest=state.meError;
   if(state.holdMe)await new Promise(resolve=>state.meWaiters.push(resolve));
   if(errorAtRequest)return new Response(JSON.stringify({detail:'synthetic SQL/private diagnostic'}),{status:errorAtRequest,headers:{'Content-Type':'application/json'}});
   return response({user_id:actorAtRequest,workspace_id:scope.workspace_id,role:'admin'});
  }
  if(path===base+'/catalog/capabilities?kind=source&limit=20')return catalogResponse({items:[source],next_after:null,can_register:true});
  if(path===base+'/catalog/capabilities?kind=extractor&limit=20')return catalogResponse({items:[extractor],next_after:null,can_register:true});
  if(path===base+'/catalog/registrations?limit=20')return catalogResponse({items:[...state.records.values()].map(catalog),next_after:null,can_register:true});
  if(path===base+'/'+ID(5)+'/delivery-records?limit=20'&&method==='GET')return response({schema:'graph-event-delivery-page-v1',producer_id:ID(5),items:[],next_after:null});
  if(path.startsWith(base+'/catalog/registrations/')){const row=[...state.records.values()].find(item=>item.id===path.split('/').at(-1));assert.ok(row);return response(catalog(row));}
  if(path===base+'/preview'&&method==='POST'){const body=JSON.parse(options.body);return response({contract:makeContract(body),contract_hash:H,command_hash:await graphSourceCommandHash(scope,'register',body,webcrypto),execution_granted:false,meaning:'只登记处理结果来源'});}
  if(path===base&&method==='POST'){
   const body=JSON.parse(options.body),raw=[...Array(local.length)].map((_,i)=>local.getItem(local.key(i))).find(value=>value.includes('graph-source-outbox-v122')&&value.includes(body.request_id));assert.ok(raw,'original outbox exists before POST');assert.equal(JSON.parse(raw).exact_body,options.body);
   const producer={id:ID(5),label:body.label,status:'active',version:1,contract_hash:H,contract:makeContract(body),registration:{request_id:body.request_id,actor_id:scope.actor_id,command_hash:await graphSourceCommandHash(scope,'register',body,webcrypto),command:copy(body)},execution_granted:false,physical_state_verified:false};
   if(!state.noCommit)state.records.set(body.request_id,producer);if(state.loseRegister)throw Error('synthetic lost registration response');return response({producer,created:true});
  }
  if(path.startsWith(base+'/requests/')&&method==='GET'){const producer=state.records.get(path.split('/').at(-1));return response({found:!!producer,producer:producer||null});}
  if(path===base+'/revocations'&&method==='POST'){
   const body=JSON.parse(options.body),row=[...state.records.values()].find(item=>item.id===body.producer_id);assert.ok(row);row.status='revoked';row.version++;
   const receipt={id:ID(6),request_id:body.request_id,actor_id:scope.actor_id,producer_id:body.producer_id,command_hash:await graphSourceCommandHash(scope,'revoke',body,webcrypto),command:copy(body),result:{status:'revoked',version:row.version,contract_hash:H}};
   state.revocations.set(body.request_id,receipt);if(state.loseRevoke)throw Error('synthetic lost stop response');return response({receipt,replayed:false});
  }
  if(path.startsWith(base+'/revocations/requests/')&&method==='GET'){const receipt=state.revocations.get(path.split('/').at(-1));return response({found:!!receipt,receipt:receipt||null});}
  if(path.startsWith(base+'/command-requests/')&&method==='GET'){
   const parts=path.split('/'),kind=parts.at(-2),request_id=parts.at(-1),saved=(kind==='register'?state.records:state.revocations).get(request_id)||null;
   const retirement=[...state.retirements.values()].find(row=>row.kind===kind&&row.original_request_id===request_id)||null;
   return response({schema:'graph-event-source-request-state-v1',kind,request_id,outcome:saved?'saved':retirement?'retired':'not_found',command_hash:saved?(kind==='register'?saved.registration.command_hash:saved.command_hash):retirement?.original_command_hash||null,saved,retirement});
  }
  if(path===base+'/command-retirements'&&method==='POST'){
   const body=JSON.parse(options.body),original_command_hash=await graphSourceCommandHash(scope,body.kind,body.original,webcrypto),command_hash=await graphSourceRetirementHash(scope,body,webcrypto);
   const retirement={id:ID(7),request_id:body.request_id,actor_id:scope.actor_id,kind:body.kind,original_request_id:body.original.request_id,original_command_hash,command_hash,command:copy(body),result:{outcome:'retired',kind:body.kind,original_request_id:body.original.request_id,original_command_hash}};
   state.retirements.set(body.request_id,retirement);if(state.loseRetirement)throw Error('synthetic lost retirement response');return response({retirement,replayed:false});
  }
  if(path.startsWith(base+'/command-retirements/requests/')&&method==='GET'){const retirement=state.retirements.get(path.split('/').at(-1))||null;return response({found:!!retirement,retirement});}
  unexpected.push(path);throw Error('Unapproved synthetic fetch path');
 }});
 let root;
 const settle=async()=>{for(let i=0;i<16;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 const button=label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(node,'actual button '+label);return node;};
 const click=async label=>{const node=button(label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
 async function input(label,value){const wrapper=[...container.querySelectorAll('label')].find(item=>item.textContent.startsWith(label));assert.ok(wrapper,label);const node=wrapper.querySelector('input,select,textarea');assert.ok(node);const prototype=node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;
  await act(async()=>{Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();}
 try{
  const Panel=await component(),{createRoot}=await import('react-dom/client');
  function Probe(){useEffect(()=>{state.probe.setup++;return()=>{state.probe.cleanup++;};},[]);return null;}
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Probe),React.createElement(Panel,{actorID:scope.actor_id,workspaceID:scope.workspace_id,onBusy:value=>state.busy.push(value),onReturnToFlow:()=>state.returns++}))));await settle();};
  const unmount=async()=>{if(root){const previous=root;root=null;await act(async()=>previous.unmount());await settle();}};
  const fill=async()=>{await input('结果名称','温度处理结果');await input('选择已发布来源',source.id);await input('选择已发布处理能力',extractor.id);};
  const freshSession=()=>{session.clear();session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-csrf');};
  const recovery=async(label,action)=>{const row=[...container.querySelectorAll('.graph-sources-recovery-list li')].find(node=>node.querySelector('strong')?.textContent===label&&[...node.querySelectorAll('button')].some(button=>button.textContent===action));assert.ok(row,'actual recovery candidate '+label);const target=[...row.querySelectorAll('button')].find(button=>button.textContent===action);assert.equal(target.disabled,false);await act(async()=>target.click());await settle();};
  await run({dom,container,state,calls,local,session,mount,unmount,settle,button,click,input,fill,freshSession,recovery,text:()=>container.textContent});await unmount();assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const name of names){if(descriptors[name])Object.defineProperty(globalThis,name,descriptors[name]);else delete globalThis[name];}}
}

test('actual StrictMode panel shows business labels, explicit preview and lookup-only recovery of a lost registration after remount',async()=>harness(async h=>{
 await h.mount();assert.ok(h.state.probe.setup>=2&&h.state.probe.cleanup>=1);await h.fill();assert.equal(h.container.querySelector('[aria-label="登记前核对"]'),null);
 await h.click('核对登记内容');assert.ok(h.text().includes('温度'));assert.ok(!h.text().includes(H));assert.ok(!h.text().includes(ID(3)));assert.ok(!h.text().includes('JSON'));
 h.state.loseRegister=true;await h.click('登记并保存');const posts=()=>h.calls.filter(call=>call.path==='/api/setup/graph-event-producers'&&call.method==='POST');assert.equal(posts().length,1);const original=posts()[0].body;
 await h.input('结果名称','另一个待登记的人稿');await h.unmount();await h.mount();assert.equal(posts().length,1);assert.ok(h.text().includes('原登记结果待核对'));
 await h.click('只核对原请求是否已保存');assert.equal(posts().length,1);assert.equal(posts()[0].body,original);assert.ok(h.text().includes('登记已保存'));assert.equal(h.container.querySelector('input[maxlength="200"]').value,'另一个待登记的人稿');
 assert.ok(h.container.querySelector('[aria-label="已登记的处理结果"]').textContent.includes('温度处理结果'));await h.click('返回业务与流程选择来源');assert.equal(h.state.returns,1);
}));
test('actual panel invalidates preview on edits and blocks leaving while a quota failure retains unsaved input',async()=>harness(async h=>{
 await h.mount();await h.fill();await h.click('核对登记内容');assert.ok(h.container.querySelector('[aria-label="登记前核对"]'));
 h.state.quota=true;await h.input('结果名称','存储失败也保留当前输入');assert.equal(h.container.querySelector('[aria-label="登记前核对"]'),null);assert.equal(h.button('返回业务与流程选择来源').disabled,true);assert.equal(h.state.busy.at(-1),true);
 const leave=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(leave);assert.equal(leave.defaultPrevented,true);assert.equal(h.calls.filter(call=>call.path==='/api/setup/graph-event-producers'&&call.method==='POST').length,0);
 h.state.quota=false;await h.click('重试保存本页输入');assert.equal(h.button('返回业务与流程选择来源').disabled,false);assert.equal(h.container.querySelector('input[maxlength="200"]').value,'存储失败也保留当前输入');
}));
test('actual panel requires inline stop confirmation and recovers its own immutable receipt without a second mutation',async()=>harness(async h=>{
 await h.mount();await h.fill();await h.click('核对登记内容');await h.click('登记并保存');assert.ok(h.container.querySelector('[aria-label="登记详情"]'));
 await h.input('停止此结果来源的原因','停止后保留已保存记录');await h.click('停止此结果来源');assert.ok(h.text().includes('正在等待该来源的任务可能暂停'));assert.equal(h.calls.filter(call=>call.path.endsWith('/revocations')&&call.method==='POST').length,0);
 h.state.loseRevoke=true;await h.click('确认停止');assert.ok(h.text().includes('原停止结果待核对'));await h.unmount();await h.mount();await h.click('只核对原请求是否已保存');
 assert.equal(h.calls.filter(call=>call.path.endsWith('/revocations')&&call.method==='POST').length,1);assert.ok(h.text().includes('已停止此结果来源'));assert.ok(![...h.container.querySelectorAll('button')].some(button=>button.textContent==='停止此结果来源'));assert.ok(h.text().includes('物理设备不会关闭'));
}));
test('actual panel never flashes restored private input before cookie identity verification and conceals it again after focus fails',async()=>harness(async h=>{
 await h.mount();await h.fill();await h.input('结果名称','仅原账号可见的人稿');const saved=[...Array(h.local.length)].map((_,i)=>[h.local.key(i),h.local.getItem(h.local.key(i))]);await h.unmount();
 h.state.cookieActor=ID(91);h.state.holdMe=true;await h.mount();assert.ok(h.text().includes('正在核对登录'));assert.ok(!h.text().includes('仅原账号可见的人稿'));assert.equal(h.container.querySelector('input,textarea'),null);
 h.state.holdMe=false;await act(async()=>{for(const resume of h.state.meWaiters.splice(0))resume();});await h.settle();assert.ok(h.text().includes('当前登录身份尚无法核对'));assert.equal(h.container.querySelector('input,textarea'),null);assert.ok(!h.text().includes('仅原账号可见的人稿'));
 h.state.meError=403;await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();assert.ok(!h.text().includes('SQL'));assert.equal(h.container.querySelector('input'),null);
 h.state.meError=null;h.state.cookieActor=scope.actor_id;await h.click('重新核对登录');assert.equal(h.container.querySelector('input[maxlength="200"]').value,'仅原账号可见的人稿');
 h.state.cookieActor=ID(92);await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();assert.equal(h.container.querySelector('input'),null);assert.ok(!h.text().includes('仅原账号可见的人稿'));
 assert.deepEqual([...Array(h.local.length)].map((_,i)=>[h.local.key(i),h.local.getItem(h.local.key(i))]),saved);assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));
test('actual panel gives a recoverable generic storage problem instead of permanent checking and permits identity-only retry without showing damaged drafts',async()=>harness(async h=>{
 await h.mount();await h.fill();await h.input('结果名称','损坏记录旁仍保留的原稿');await h.unmount();
 const key=[...Array(h.session.length)].map((_,i)=>h.session.key(i)).find(value=>value.endsWith(':form-head'));assert.ok(key);h.session.setItem(key,'synthetic damaged head');
 await h.mount();assert.ok(h.text().includes('本机恢复记录暂无法读取'));assert.ok(!h.text().includes('正在核对登录'));assert.equal(h.button('重新核对登录').disabled,false);assert.equal(h.container.querySelector('input'),null);
 const reads=h.calls.filter(call=>call.path==='/api/me').length;await h.click('重新核对登录');assert.ok(h.calls.filter(call=>call.path==='/api/me').length>reads);assert.equal(h.session.getItem(key),'synthetic damaged head');assert.equal(h.container.querySelector('input'),null);assert.ok(!h.text().includes('损坏记录旁仍保留的原稿'));
}));
test('busy focus and visibility conceal immediately and a late old-actor identity response cannot reveal drafts before the queued new check',async()=>harness(async h=>{
 await h.mount();await h.fill();await h.input('结果名称','忙碌期间也不得暴露的原账号稿');
 const saved=[...Array(h.local.length)].map((_,i)=>[h.local.key(i),h.local.getItem(h.local.key(i))]);
 // First hold ordinary reads after /me verified A: the busy view is visible.
 h.state.holdCatalog=true;await h.click('刷新已发布能力与登记记录');assert.ok(h.state.catalogWaiters.length>0);assert.equal(h.container.querySelector('input[maxlength="200"]').value,'忙碌期间也不得暴露的原账号稿');
 h.state.cookieActor=ID(93);await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));
 Object.defineProperty(h.dom.window.document,'visibilityState',{configurable:true,value:'visible'});await act(async()=>h.dom.window.document.dispatchEvent(new h.dom.window.Event('visibilitychange')));
 assert.equal(h.container.querySelector('input,textarea'),null);assert.ok(!h.text().includes('忙碌期间也不得暴露的原账号稿'));
 h.state.holdMe=true;h.state.holdCatalog=false;await act(async()=>{for(const resume of h.state.catalogWaiters.splice(0))resume();});await h.settle();assert.ok(h.state.meWaiters.length>0);assert.equal(h.container.querySelector('input'),null);
 h.state.holdMe=false;await act(async()=>{for(const resume of h.state.meWaiters.splice(0))resume();});await h.settle();assert.ok(h.text().includes('当前登录身份尚无法核对'));assert.equal(h.container.querySelector('input'),null);
 // Then hold an actual old-A /me response and invalidate its check epoch.
 h.state.cookieActor=scope.actor_id;await h.click('重新核对登录');h.state.holdMe=true;await h.click('刷新已发布能力与登记记录');assert.ok(h.state.meWaiters.length>0);const old=h.state.meWaiters.splice(0);
 h.state.cookieActor=ID(94);await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));assert.equal(h.container.querySelector('input'),null);
 await act(async()=>{for(const resume of old)resume();});await h.settle();assert.ok(h.state.meWaiters.length>0,'queued cold identity request actually started');
 assert.equal(h.container.querySelector('input,textarea'),null);assert.ok(!h.text().includes('忙碌期间也不得暴露的原账号稿'));
 h.state.holdMe=false;await act(async()=>{for(const resume of h.state.meWaiters.splice(0))resume();});await h.settle();assert.ok(h.text().includes('当前登录身份尚无法核对'));assert.equal(h.container.querySelector('input,textarea'),null);
 assert.deepEqual([...Array(h.local.length)].map((_,i)=>[h.local.key(i),h.local.getItem(h.local.key(i))]),saved);assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));
test('a real fresh-session panel explicitly restores an old draft without losing current input, then cold-recovers the original registration using GET only',async()=>harness(async h=>{
 await h.mount();await h.fill();await h.click('核对登记内容');h.state.loseRegister=true;await h.click('登记并保存');await h.input('结果名称','原窗口保留的另一份人稿');
 const headKey=[...Array(h.session.length)].map((_,i)=>h.session.key(i)).find(key=>key.endsWith(':form-head')),owner=JSON.parse(h.session.getItem(headKey)).owner;
 const old=[...Array(h.local.length)].map((_,i)=>[h.local.key(i),h.local.getItem(h.local.key(i))]).filter(([key])=>key.includes(owner));
 await h.unmount();h.freshSession();await h.mount();assert.equal(h.container.querySelector('input[maxlength="200"]').value,'');assert.ok(!h.container.querySelector('[aria-label="原请求核对"]'));
 await h.input('结果名称','新窗口尚需保留的当前稿');h.state.quota=true;await h.recovery('原窗口保留的另一份人稿','保留当前输入并恢复此稿');assert.equal(h.container.querySelector('input[maxlength="200"]').value,'新窗口尚需保留的当前稿');assert.equal(h.button('返回业务与流程选择来源').disabled,true);
 h.state.quota=false;await h.click('重试保存本页输入');await h.recovery('原窗口保留的另一份人稿','保留当前输入并恢复此稿');assert.equal(h.container.querySelector('input[maxlength="200"]').value,'原窗口保留的另一份人稿');
 for(const [key,value] of old)assert.equal(h.local.getItem(key),value);assert.ok([...Array(h.local.length)].some((_,i)=>h.local.getItem(h.local.key(i)).includes('新窗口尚需保留的当前稿')));
 await h.recovery('温度处理结果','恢复并核对原请求');assert.ok(h.text().includes('登记已保存'));assert.equal(h.calls.filter(call=>call.path==='/api/setup/graph-event-producers'&&call.method==='POST').length,1);assert.equal(h.container.querySelector('input[maxlength="200"]').value,'原窗口保留的另一份人稿');
 assert.ok(h.calls.some(call=>call.path.includes('/command-requests/register/')));assert.ok(!h.text().includes(H));
}));
test('a real fresh-session panel finds a lost retirement reply by its own nonce and never resends the original registration',async()=>harness(async h=>{
 await h.mount();await h.fill();await h.click('核对登记内容');h.state.noCommit=true;h.state.loseRegister=true;await h.click('登记并保存');const original=h.calls.find(call=>call.path==='/api/setup/graph-event-producers'&&call.method==='POST').body;
 await h.unmount();h.freshSession();await h.mount();await h.recovery('温度处理结果','恢复并核对原请求');await h.input('停止这次未完成操作的原因','保留原稿，停止这次未完成操作');
 await h.click('停止这次未完成的操作');assert.equal(h.calls.filter(call=>call.path.endsWith('/command-retirements')&&call.method==='POST').length,0);h.state.loseRetirement=true;await h.click('确认停止这次操作');assert.ok(h.text().includes('停止未完成操作的结果待核对'));
 const stop=h.calls.find(call=>call.path.endsWith('/command-retirements')&&call.method==='POST').body;assert.deepEqual(JSON.parse(stop).original,JSON.parse(original));assert.notEqual(JSON.parse(stop).request_id,JSON.parse(original).request_id);
 await h.unmount();h.freshSession();await h.mount();await h.recovery('取消未结清请求的原请求','核对这次停止操作是否已保存');assert.ok(h.text().includes('这次未完成的登记操作已停止'));assert.equal(h.container.querySelector('[aria-label="原请求核对"]'),null);
 assert.equal(h.calls.filter(call=>call.path==='/api/setup/graph-event-producers'&&call.method==='POST').length,1);assert.equal(h.calls.filter(call=>call.path.endsWith('/command-retirements')&&call.method==='POST').length,1);assert.ok(h.calls.some(call=>call.path.includes('/command-retirements/requests/')));
}));
