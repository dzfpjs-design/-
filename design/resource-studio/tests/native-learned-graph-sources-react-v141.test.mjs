// Actual React DOM + StrictMode + runtime-api + native journal/controller.
// HTTP transport data are synthetic. This does not assert browser acceptance,
// a real GeoPair run, backend authority, or durability across a server restart.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act,useLayoutEffect} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {account,scope,source,trace,clone,locks,server,deferred,id,wire} from './fixtures/native-learned-graph-sources-v141.mjs';
import {learnedSourcePrefixV141} from '../src/native-learned-graph-sources-v141.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function components(){
 if(!bundle)bundle=(async()=>{
  temp=await mkdtemp(join(tmpdir(),'native-learned-graph-sources-real-react-'));const entry=join(temp,'entry.mjs');
  await writeFile(entry,'export {default as Panel} from '+JSON.stringify(join(project,'src/LearnedGraphSourcesPanelV141.jsx'))+';\nexport {default as Receipt} from '+JSON.stringify(join(project,'src/NativeReceiptStudio.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
  const reactEntries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(reactEntries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(reactEntries,key)?pathToFileURL(join(project,'node_modules/react',reactEntries[key])).href:key}}}});
  const output=result.output.find(value=>value.type==='chunk'&&value.isEntry);assert.ok(output,'bundle actual panel and its actual dependencies');const file=join(temp,'panel.mjs');await writeFile(file,output.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources are forbidden in the synthetic DOM');}}
async function harness(run){
 const errors=[],violations=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://native-learned-graph-source.invalid/',pretendToBeVisual:true,resources:new NoResources(),virtualConsole:console});
 const backend=await server(),calls=[],commits=[],busy=[],gates=[],backing=dom.window.localStorage,session=dom.window.sessionStorage;
 const receiptRecord={id:id(71),entity_id:id(72),received_day:'2026-10-21',data_received:false,integrity_error:null,record:{modality:'imagery',name:'原双影像差异候选',attributes:{'差异候选像素数':2,'未知像素数':0},review_state:'unreviewed',task_id:id(73)},graph_message_support:{schema:'native-learned-geo-graph-receipt-support-v141',status:'new_inputs_only',can_replay_as_new_message:false,message:'归集、接收、接纳和等待匹配分别核对。'}};
 const state={account:clone(account),visibility:'visible',quota:false,meGate:null,receipts:false,receiptRecord,receiptRetryGate:null,receiptLost:false,receiptPosts:0,receiptStage:{schema:'native-learned-geo-graph-receipt-stages-v141',observation_id:receiptRecord.id,observation_version:1,source_id:source.id,entity_id:receiptRecord.entity_id,task_id:receiptRecord.record.task_id,collected:true,data_received:false,data_status:'blocked',data_message:'原接收受阻，结果仍保留。',deliveries:[],waiting_match:'not_checked',waiting_message:'请到相应任务核对等待窗口。',candidate_only:true,source_time_verified:false,physical_state_verified:false}};
 const storage={get length(){return backing.length;},key:index=>backing.key(index),getItem:key=>backing.getItem(key),removeItem:key=>backing.removeItem(key),setItem(key,value){if(state.quota&&key.startsWith(learnedSourcePrefixV141(scope))&&key.includes(':form:'))throw new dom.window.DOMException('Synthetic quota full','QuotaExceededError');backing.setItem(key,value);}};
 const token='synthetic-learned-v141';session.setItem('ga-setup-csrf',token);session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 Object.defineProperty(dom.window,'localStorage',{configurable:true,value:storage});Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});Object.defineProperty(dom.window.document,'visibilityState',{configurable:true,get:()=>state.visibility});
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),path=url.pathname+url.search,method=options.method||'GET';calls.push({path,method,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin);const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),token);
   if(path==='/api/me'){
    assert.equal(method,'GET');const snapshot=clone(backend.state.who),gate=state.meGate;if(gate)await gate.promise;
    return new Response(JSON.stringify(snapshot),{headers:{'Content-Type':'application/json'}});
   }
   const receiptBase='/api/workspace/instances/'+scope.instance_id+'/native-receipts',stageBase='/api'+backend.base+'/observations/'+receiptRecord.id;
   if(path===receiptBase){assert.equal(method,'GET');return new Response(JSON.stringify({available:0,not_ready:0,truncated:false,candidates:[],records:[state.receiptRecord],daily:[],history_count:0,has_more:false}),{headers:{'Content-Type':'application/json'}});}
   if(path===stageBase+'/stages'){assert.equal(method,'GET');return new Response(JSON.stringify(wire(state.receiptStage)),{headers:{'Content-Type':'application/json'}});}
   if(path===stageBase+'/retry-data-delivery'){
    assert.equal(method,'POST');assert.deepEqual(JSON.parse(options.body),{expected_version:1,reason:'恢复原件和权限后，按原结果重试数据接收'});state.receiptPosts++;
    state.receiptStage={...state.receiptStage,observation_version:2,data_received:true,data_status:'received',data_message:'原结果的数据接收回执已核实。'};
    if(state.receiptRetryGate)await state.receiptRetryGate.promise;
    if(state.receiptLost){state.receiptLost=false;throw Error('synthetic committed data receipt response unknown');}
    return new Response(JSON.stringify(wire(state.receiptStage)),{headers:{'Content-Type':'application/json'}});
   }
   if(path.startsWith('/api'+backend.base)){
    const result=await backend.api(path.slice(4),{...options,headers:{'X-Workspace-ID':headers.get('X-Workspace-ID'),'X-CSRF-Token':headers.get('X-CSRF-Token')}});
    return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json'}});
   }
   // The common delivery component remains real and visible. No synthetic
   // admission is minted for a registration-only case. Its empty page is the
   // actual closed page shape, rather than a fake successful delivery.
   const delivery='/api/setup/graph-event-producers/';
   if(path.startsWith(delivery)&&path.includes('/delivery-records?limit=20')){
    assert.equal(method,'GET');const producer=path.slice(delivery.length).split('/')[0];return new Response(JSON.stringify({schema:'graph-event-delivery-page-v1',producer_id:producer,items:[],next_after:null}),{headers:{'Content-Type':'application/json'}});
   }
   throw Error('Unexpected synthetic HTTP route '+method+' '+path);
  }catch(error){
   if(error.message.startsWith('synthetic '))throw error;
   if(error.status)return new Response(JSON.stringify({detail:error.message}),{status:error.status,headers:{'Content-Type':'application/json'}});
   violations.push(error.message);throw error;
  }
 };
 const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};
 const previous=Object.fromEntries(Object.keys(values).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value]of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 let root;const container=dom.window.document.getElementById('root');
 try{
  const renderer=await import('react-dom/client'),actual=await components(),{createRoot}=renderer;
  assert.equal(actual.BundledReact,React,'actual component bundle and test use the same installed React object');
  assert.equal(renderer.version,React.version,'actual React DOM and React releases match; never patch an internal dispatcher');
  actual.setSession(token,account.workspace_id,account.user_id,account.username);
  const Btn=({primary,children,...props})=>React.createElement('button',{type:'button',...props},children);
  function Witness(){useLayoutEffect(()=>{commits.push({actor:state.account.user_id,values:[...container.querySelectorAll('input,textarea')].map(node=>node.value),text:container.textContent});});return state.receipts?React.createElement(actual.Receipt,{account:state.account,instanceId:scope.instance_id,releaseID:id(74),objects:[],Btn,onBusy:value=>busy.push(value)}):React.createElement(actual.Panel,{account:state.account,instanceId:scope.instance_id,instanceName:'实际来源界面合成实例',onBusy:value=>busy.push(value)});}
  const settle=async(turns=18)=>{for(let i=0;i<turns;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
  const wait=async(predicate,label)=>{
   for(let i=0;i<100;i++){if(predicate())return;await settle(1);}
   const diagnostic={text:container.textContent.slice(0,1200),labels:[...container.querySelectorAll('label')].slice(0,12).map(node=>node.firstChild?.textContent),calls:calls.slice(-20).map(({path,method})=>({path,method})),errors:errors.slice(0,1).map(error=>String(error.stack||error.message).split('\n').slice(0,6).join('\n')),violations:violations.slice(0,3)};
   assert.ok(predicate(),label+'; synthetic DOM diagnostic '+JSON.stringify(diagnostic));
  };
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Witness))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const unmount=async()=>{if(root){const prior=root;root=null;await act(async()=>prior.unmount());await settle();}};
  const labelControl=label=>[...container.querySelectorAll('label')].find(node=>node.firstChild?.textContent===label)?.querySelector('input,select,textarea');
  const setValue=async(label,value)=>{
   const control=labelControl(label);assert.ok(control,'actual control '+label);assert.equal(control.disabled,false);const kind=control.tagName==='SELECT'?'HTMLSelectElement':control.tagName==='TEXTAREA'?'HTMLTextAreaElement':'HTMLInputElement',setter=Object.getOwnPropertyDescriptor(dom.window[kind].prototype,'value').set;
   await act(async()=>{setter.call(control,value);control.dispatchEvent(new dom.window.Event(control.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();assert.equal(labelControl(label)?.value,value);
  };
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent===label);
  const click=async label=>{const control=button(label);assert.ok(control,'actual button '+label);assert.equal(control.disabled,false,'button enabled '+label);await act(async()=>control.click());await settle();};
  const ready=()=>wait(()=>!!labelControl('结果来源名称')&&!button('刷新实际来源和登记记录')?.disabled,'actual native source form becomes ready');
  const gate=()=>{const value=deferred();gates.push(value);return value;};
  const snapshot=()=>Array.from({length:storage.length},(_,index)=>storage.key(index)).sort().map(key=>[key,storage.getItem(key)]);
  const contains=content=>container.textContent.includes(content)||[...container.querySelectorAll('input,textarea')].some(node=>node.value.includes(content));
  const fill=async(name='实际选择的变化候选来源')=>{await ready();await setValue('结果来源名称',name);await setValue('已有变化结果来源',source.id);await setValue('已登记的本地变化分析能力',id(9));};
  await run({dom,backend,state,calls,storage,session,container,busy,commits,mount,unmount,render,settle,wait,ready,gate,labelControl,button,setValue,click,snapshot,contains,fill,setSession:actual.setSession});
  await unmount();assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{
  gates.forEach(value=>value.resolve());if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,descriptor]of Object.entries(previous)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
 }
}

test('actual empty native source page explains prerequisite; every read stays GET and creates nothing',()=>harness(async h=>{
 h.backend.state.optionsSources=[];await h.mount();await h.ready();assert.ok(h.container.textContent.includes('请先在已启用业务中完成真实变化分析、关联对象并接收结果'));
 assert.equal(h.backend.saved.size,0);assert.equal(h.calls.filter(value=>value.method==='POST').length,0);assert.equal(h.labelControl('已有变化结果来源').options.length,1);assert.ok(!h.container.querySelector('textarea[placeholder*="JSON"]'));
}));

test('actual explicit source and model preview then confirmation saves and cold-reads original receipt',()=>harness(async h=>{
 const firstCapability=h.backend.state.optionsCapabilities[0];h.backend.state.optionsCapabilities.push({...clone(firstCapability),id:id(91),content_hash:'f'.repeat(64),probability_threshold:0.75,alignment:'reproject_after_to_before'});
 await h.mount();await h.fill('人员可理解的变化候选候选名称');await h.click('预览并核对登记范围');await h.wait(()=>!!h.button('确认登记并保存'),'preview is visible');
 const capabilityOptions=[...h.labelControl('已登记的本地变化分析能力').options].slice(1).map(option=>option.textContent);assert.deepEqual(capabilityOptions,['本地学习型变化检测 · 阈值 0.5 · 原网格一致 · 第2代','本地学习型变化检测 · 阈值 0.75 · B重投影到A · 第2代']);assert.ok(capabilityOptions.every(label=>!label.includes(id(9))&&!label.includes(id(91))),'same-name capabilities are distinguishable without internal IDs');
 assert.ok(h.container.textContent.includes('差异候选像素数'));assert.ok(h.container.textContent.includes('采集时刻'));assert.equal(h.backend.saved.size,0);
 const preview=h.calls.find(value=>value.method==='POST'&&value.path.endsWith('/preview'));assert.ok(preview);const original=JSON.parse(preview.body);assert.equal(original.source_id,source.id);assert.equal(original.capability_id,id(9));assert.equal(Object.hasOwn(original,'installation_id'),false);assert.equal(Object.hasOwn(original,'extractor_id'),false);
 let sawDurable=false;h.backend.state.beforePost=async(path,body,raw)=>{if(path===h.backend.base){const head=JSON.parse(h.storage.getItem(learnedSourcePrefixV141(scope)+'active')),stored=JSON.parse(h.storage.getItem(head.key));assert.equal(stored.exact_body,raw);assert.equal(stored.body.request_id,body.request_id);sawDurable=true;}};
 await h.click('确认登记并保存');await h.wait(()=>h.container.textContent.includes('服务器已核实原操作保存'),'actual saved receipt is shown');assert.equal(sawDurable,true);assert.equal(h.backend.saved.size,1);
 const posts=h.calls.filter(value=>value.method==='POST'&&value.path==='/api'+h.backend.base);assert.equal(posts.length,1);assert.equal(posts[0].body,preview.body);assert.ok(h.calls.some(value=>value.method==='GET'&&value.path.endsWith('/command-requests/register/'+original.request_id)));
 await h.click('刷新实际来源和登记记录');await h.wait(()=>[...h.container.querySelectorAll('.graph-sources-record-list button')].some(node=>node.textContent.includes('人员可理解的变化候选候选名称')),'registered name is accessible');
 const record=[...h.container.querySelectorAll('.graph-sources-record-list button')].find(node=>node.textContent.includes('人员可理解的变化候选候选名称'));assert.ok(record);await act(async()=>record.click());await h.settle();await h.wait(()=>h.container.textContent.includes('登记本身没有接纳或等待匹配回执'),'detail distinguishes stages');
 assert.ok(h.container.textContent.includes('学习型双影像'));assert.equal(h.backend.saved.size,1);
}));

test('actual unknown request remount only looks up original nonce, then explicitly confirms safe ending',()=>harness(async h=>{
 await h.mount();await h.fill();await h.click('预览并核对登记范围');await h.wait(()=>!!h.button('确认登记并保存'),'preview ready');h.backend.state.lost='before';await h.click('确认登记并保存');await h.wait(()=>h.container.textContent.includes('原登记结果尚待核对'),'unknown remains visible');
 const originalPost=h.calls.find(value=>value.method==='POST'&&value.path==='/api'+h.backend.base),original=JSON.parse(originalPost.body);assert.ok(originalPost);const pointer=JSON.parse(h.storage.getItem(learnedSourcePrefixV141(scope)+'active')),raw=h.storage.getItem(pointer.key);
 await h.unmount();const count=h.calls.filter(value=>value.method==='POST').length;await h.mount();await h.ready();assert.equal(h.calls.filter(value=>value.method==='POST').length,count);assert.equal(h.storage.getItem(pointer.key),raw);
 await h.click('只查原请求结果');await h.wait(()=>!!h.button('结束这次未确认操作'),'negative read permits an explicit choice');assert.ok(h.calls.some(value=>value.method==='GET'&&value.path.endsWith('/command-requests/register/'+original.request_id)));assert.equal(h.calls.filter(value=>value.method==='POST').length,count);
 await h.setValue('结束这次未确认操作的原因','保留原稿，明确结束这一次登记');await h.click('结束这次未确认操作');assert.ok(h.button('确认结束这次操作'));assert.equal(h.calls.filter(value=>value.method==='POST').length,count,'first click only opens human confirmation');
 h.backend.state.lost='after';await h.click('确认结束这次操作');await h.wait(()=>h.container.textContent.includes('结束操作的结果仍待核对'),'lost ending response remains unknown');assert.equal(h.backend.retired.size,1);assert.equal(h.backend.saved.size,0);
 const ending=h.calls.find(value=>value.method==='POST'&&value.path.endsWith('/command-retirements')),body=JSON.parse(ending.body);assert.notEqual(body.request_id,original.request_id);assert.deepEqual(body.original,original);
 await h.unmount();const endingCount=h.calls.filter(value=>value.method==='POST').length;await h.mount();await h.ready();await h.click('只查结束操作结果');await h.wait(()=>h.container.textContent.includes('服务器已核实结束这一次未确认操作'),'cold actual ending receipt is shown');assert.equal(h.calls.filter(value=>value.method==='POST').length,endingCount);assert.equal(h.storage.getItem(pointer.key),raw,'original wire is retained as history');
}));

test('actual private draft stays concealed before fresh identity; late original account cannot repaint new scope',()=>harness(async h=>{
 const privateText='只属于原账号的变化候选来源稿';await h.mount();await h.ready();await h.setValue('结果来源名称',privateText);await h.unmount();
 const gate=h.gate();h.state.meGate=gate;await h.mount();assert.equal(h.contains(privateText),false);assert.equal(h.labelControl('结果来源名称'),undefined);const original=h.snapshot();gate.resolve();h.state.meGate=null;await h.ready();assert.equal(h.labelControl('结果来源名称').value,privateText);
 const late=h.gate();h.state.meGate=late;await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();assert.equal(h.contains(privateText),false);
 const other={...account,user_id:id(199),username:'合成另一管理员'};h.backend.state.who=other;h.state.account=other;h.state.meGate=null;h.setSession('synthetic-learned-v141',other.workspace_id,other.user_id,other.username);await h.render();await h.ready();
 const first=h.commits.find(value=>value.actor===other.user_id);assert.ok(first);assert.ok(!first.values.includes(privateText)&&!first.text.includes(privateText),'scope rerender first committed layout is concealed');
 await h.setValue('结果来源名称','新账号自己的来源稿');const next=h.snapshot();late.resolve();await h.settle();assert.equal(h.labelControl('结果来源名称').value,'新账号自己的来源稿');assert.equal(h.contains(privateText),false);assert.deepEqual(h.snapshot(),next,'late old response does not write either journal');assert.ok(original.some(([key,raw])=>key.startsWith(learnedSourcePrefixV141(scope))&&raw.includes(privateText)));assert.equal(h.calls.filter(value=>value.method==='POST').length,0);
}));

test('actual quota failure keeps human input, parent guard activity and beforeunload until explicit saving',()=>harness(async h=>{
 await h.mount();await h.ready();h.state.quota=true;await h.setValue('结果来源名称','存储失败也不能丢的人稿');assert.equal(h.busy.at(-1),true);const event=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);assert.ok(h.container.textContent.includes('尚未完整保存'));assert.equal(h.button('预览并核对登记范围').disabled,true);assert.equal(h.calls.filter(value=>value.method==='POST').length,0);
 h.state.quota=false;await h.click('重试保存本页输入');assert.equal(h.busy.at(-1),false);await h.unmount();await h.mount();await h.ready();assert.equal(h.labelControl('结果来源名称').value,'存储失败也不能丢的人稿');assert.equal(h.calls.filter(value=>value.method==='POST').length,0);
}));

test('actual learned receipt retries only its blocked original result, then lost reply requires GET instead of a second POST',()=>harness(async h=>{
 h.state.receipts=true;await h.mount();await h.wait(()=>!!h.button('核对变化候选接收与接纳'),'explicit learned support exposes stages before data reception exists');
 assert.equal(Object.hasOwn(h.state.receiptRecord,'data_reception'),false);assert.equal(h.calls.filter(value=>value.method==='POST').length,0);
 await h.click('核对变化候选接收与接纳');assert.ok(h.button('按原结果重试数据接收'));assert.ok(h.container.textContent.includes('恢复原件和权限后，重新核对同一结果；不重跑模型'));
 const barrier=h.gate();h.state.receiptRetryGate=barrier;h.state.receiptLost=true;const retry=h.button('按原结果重试数据接收');
 await act(async()=>{retry.click();retry.click();});await h.settle();assert.equal(h.state.receiptPosts,1,'two real DOM clicks cannot issue two mutation requests');assert.equal(h.busy.at(-1),true);
 const unload=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(unload);assert.equal(unload.defaultPrevented,true);
 barrier.resolve();await h.wait(()=>!!h.button('只核对这份结果的数据接收')&&!h.button('只核对这份结果的数据接收').disabled,'lost response exposes only original-result reconciliation');
 assert.equal(h.container.querySelector('[aria-label="变化候选送达阶段"]'),null,'unconfirmed old blocked stage is hidden');assert.equal(h.button('按原结果重试数据接收'),undefined);assert.equal(h.busy.at(-1),true,'unknown result still protects the parent');
 const before=h.calls.filter(value=>value.method==='POST').length;await h.click('只核对这份结果的数据接收');await h.wait(()=>!!h.container.querySelector('[aria-label="变化候选送达阶段"]'),'actual GET restores verified stages');assert.equal(h.calls.filter(value=>value.method==='POST').length,before);assert.equal(h.state.receiptPosts,1);assert.equal(h.busy.at(-1),false);
 assert.ok(h.container.textContent.includes('数据管理：已接收'));assert.ok(h.container.textContent.includes('接收回执已核实'));assert.equal(h.button('按原结果重试数据接收'),undefined);assert.equal(h.container.querySelector('[aria-label="语音结果送达阶段"]'),null);
 const settled=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(settled);assert.equal(settled.defaultPrevented,false);
}));

test('actual pending and received learned stages stay read-only and never offer another delivery mutation',()=>harness(async h=>{
 h.state.receipts=true;h.state.receiptStage={...h.state.receiptStage,data_status:'pending',data_message:'原事件正在等待接收。'};await h.mount();await h.wait(()=>!!h.button('核对变化候选接收与接纳'),'original learned record visible');await h.click('核对变化候选接收与接纳');assert.equal(h.button('按原结果重试数据接收'),undefined);
 h.state.receiptStage={...h.state.receiptStage,data_status:'received',data_received:true,data_message:'原结果已有实际接收回执。'};await h.click('核对变化候选接收与接纳');assert.equal(h.button('按原结果重试数据接收'),undefined);assert.equal(h.calls.filter(value=>value.method==='POST').length,0);assert.ok(h.container.textContent.includes('尚无可核对的接纳记录'));
}));

