import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto,createHash} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {deliveryRetryCommandHash,deliveryRetirementCommandHash} from '../src/graph-source-delivery-controller-v124.mjs';
import {graphSourceCommandHash} from '../src/graph-sources-controller-v122.mjs';

// Only fetch and browser storage are synthetic boundaries. The mounted JSX,
// controller, hooks, runtime-api and optional parent are the real source files.
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),bundles=new Map(),temps=[];
after(async()=>{for(const path of temps)await rm(path,{recursive:true,force:true});});
function component(parent){const entry=parent?'GraphSourcesPanel':'GraphSourceDeliveryPanelV124';if(!bundles.has(entry))bundles.set(entry,(async()=>{
 const temp=await mkdtemp(join(tmpdir(),'graph-delivery-real-react-'));temps.push(temp);const built=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:join(project,'src/'+entry+'.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
 const chunk=built.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const path=join(temp,entry+'.mjs');await writeFile(path,chunk.code);return (await import(pathToFileURL(path).href)).default;
})());return bundles.get(entry);}
const ID=n=>'85000000-0000-4000-8000-'+String(n).padStart(12,'0'),H='c'.repeat(64),copy=x=>JSON.parse(JSON.stringify(x)),scope={actor_id:ID(1),workspace_id:ID(2)};
const sorted=x=>Array.isArray(x)?x.map(sorted):x!==null&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,sorted(x[k])])):x;
const digest=x=>createHash('sha256').update(JSON.stringify(sorted(x))).digest('hex');
class NoResources extends ResourceLoader{fetch(){throw Error('External resource access forbidden');}}
function makeRecord(){return {schema:'graph-event-delivery-record-v1',intent_id:ID(3),producer_id:ID(4),actor_id:scope.actor_id,intent_hash:H,source_label:'实际原来源甲',object:{id:ID(5),name:'合成对象甲'},observation_id:ID(6),observed_at:'2026-10-01T00:00:00+00:00',candidate_only:true,source_time_verified:false,physical_state_verified:false,intent_status:'pending',admission:null,delivery:{job_id:ID(7),status:'failed',version:3,attempts:1},action:{allowed:true,kind:'retry_admission',reason_code:'retry_available',label:'可恢复原结果',remaining_retries:32},result:{schema:{type:'object',properties:{temperature:{type:'number',title:'温度'},note:{type:'string',title:'核对说明'}}},value:{temperature:23.5,note:'公开合成处理结果'}}};}
async function harness(run,{parent=false}={}){
 const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-delivery.invalid/',resources:new NoResources(),virtualConsole:console});
 const names=['window','document','navigator','HTMLElement','Event','localStorage','sessionStorage','crypto','fetch','IS_REACT_ACT_ENVIRONMENT'],descriptors=Object.fromEntries(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
 for(const [name,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,container=dom.window.document.getElementById('root'),calls=[],unexpected=[];
 function freshSession(){session.clear();session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-delivery');}freshSession();
 const state={actor:scope.actor_id,role:'admin',record:makeRecord(),lost:false,noCommit:false,quota:false,outcome:'queued',receipts:new Map(),retirements:new Map(),retired:new Map(),retirementLost:false,retirementNoCommit:false,saveRace:false,enforceVersion:false,holdMe:false,meWaiters:[],holdDetail:false,detailWaiters:[],busy:[],returns:0};
 const originalSet=dom.window.Storage.prototype.setItem;dom.window.Storage.prototype.setItem=function(key,value){if(this===storage&&state.quota)throw Error('synthetic quota');return originalSet.call(this,key,value);};
 const body={schema:'graph-event-producer-command-v1',request_id:ID(20),label:'已登记来源甲',adapter_id:'processed-observation-v1',source_id:ID(21),source_hash:H,extractor_id:ID(22),extractor_hash:H};
 const contract={schema:'graph-event-observation-source-contract-v1',adapter_id:body.adapter_id,event_kind:'observation.data',correlation_kind:'entity_id',source_id:body.source_id,source_hash:H,source_release_version:'1.0.0',extractor_id:body.extractor_id,extractor_hash:H,extractor_release_version:'1.0.0',result_schema:state.record.result.schema,output_schema:{type:'object'},output_schema_hash:H,contract_hash:H};
 const registration={id:ID(4),label:body.label,status:'active',version:1,contract_hash:H,contract,registration:{request_id:body.request_id,actor_id:scope.actor_id,command_hash:await graphSourceCommandHash(scope,'register',body,webcrypto),command:body},execution_granted:false,physical_state_verified:false,source_name:'已发布来源甲',extractor_name:'已发布处理能力',current_availability:{available:true,reason_code:'available',label:'当前可选'},receipt_verified:false};
 const response=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}}),BASE='/api/setup/graph-event-producers';
 Object.defineProperty(globalThis,'fetch',{configurable:true,writable:true,value:async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,body:options.body});assert.equal(options.credentials,'same-origin');assert.equal(options.headers['X-Workspace-ID'],scope.workspace_id);assert.equal(options.headers['X-CSRF-Token'],'synthetic-delivery');
  if(path==='/api/me'){const actor=state.actor;if(state.holdMe)await new Promise(resolve=>state.meWaiters.push(resolve));return response({user_id:actor,workspace_id:scope.workspace_id,role:state.role});}
  if(parent&&[BASE+'/catalog/capabilities?kind=source&limit=20',BASE+'/catalog/capabilities?kind=extractor&limit=20'].includes(path))return response({items:[],next_after:null,can_register:true});
  if(parent&&path===BASE+'/catalog/registrations?limit=20')return response({items:[registration],next_after:null,can_register:true});
  if(parent&&path===BASE+'/catalog/registrations/'+ID(4))return response(registration);
  if(path===BASE+'/'+ID(4)+'/delivery-records?limit=20')return response({schema:'graph-event-delivery-page-v1',producer_id:ID(4),items:[{...copy(state.record),result:null},...(state.otherRecord?[{...copy(state.otherRecord),result:null}]:[])],next_after:null});
  if(path===BASE+'/'+ID(40)+'/delivery-records?limit=20')return response({schema:'graph-event-delivery-page-v1',producer_id:ID(40),items:[],next_after:ID(41)});
  if(path===BASE+'/'+ID(40)+'/delivery-records?limit=20&after='+ID(41))return response({schema:'graph-event-delivery-page-v1',producer_id:ID(40),items:[],next_after:null});
  if(path===BASE+'/intents/'+ID(3)+'/delivery-record'){if(state.holdDetail)await new Promise(resolve=>state.detailWaiters.push(resolve));return response(copy(state.record));}
  if(path===BASE+'/intents/'+ID(33)+'/delivery-record'&&state.otherRecord)return response(copy(state.otherRecord));
  if(path.startsWith(BASE+'/intents/'+ID(3)+'/retry-requests/')){const receipt=state.receipts.get(path.split('/').at(-1))||null;return response({found:receipt!==null,receipt,legacy:null});}
  if(path.startsWith(BASE+'/intents/'+ID(3)+'/retry-command-requests/')){const nonce=path.split('/').at(-1),saved=state.receipts.get(nonce)||null,retirement=state.retired.get(nonce)||null;return response({schema:'graph-event-intent-retry-request-state-v1',intent_id:ID(3),request_id:nonce,outcome:saved?'saved':retirement?'retired':'not_found',command_hash:saved?.command_hash||retirement?.original_command_hash||null,saved,legacy:null,retirement});}
  if(path.startsWith(BASE+'/intents/'+ID(3)+'/retry-retirements/requests/')){const retirement=state.retirements.get(path.split('/').at(-1))||null;return response({found:retirement!==null,retirement});}
  if(path===BASE+'/intents/'+ID(3)+'/retry-retirements'&&method==='POST'){
   const command=JSON.parse(options.body),nonce=command.original.request_id;
   // Transport-only race fixture: the original in-flight command wins the
   // server lock before the newly confirmed ending request reaches it.
   if(state.saveRace)state.receipts.set(nonce,{schema:'graph-event-intent-retry-receipt-v1',id:ID(8),request_id:nonce,actor_id:scope.actor_id,intent_id:ID(3),producer_id:ID(4),job_id:ID(7),intent_hash:H,command_hash:await deliveryRetryCommandHash(scope,ID(3),command.original,webcrypto),command:command.original,result:{outcome:'queued',accepted_job_version:command.original.job_version+1,reconciled:false,admission:null},receipt_hash:H});
   if(state.receipts.has(nonce)||state.retired.has(nonce))return new Response(JSON.stringify({detail:'synthetic original is terminal'}),{status:409,headers:{'Content-Type':'application/json'}});
   const original_command_hash=await deliveryRetryCommandHash(scope,ID(3),command.original,webcrypto),command_hash=await deliveryRetirementCommandHash(scope,ID(3),command,webcrypto),unsigned={schema:'graph-event-intent-retry-retirement-receipt-v1',id:ID(31),request_id:command.request_id,actor_id:scope.actor_id,intent_id:ID(3),producer_id:ID(4),job_id:ID(7),intent_hash:H,original_request_id:nonce,original_command_hash,command_hash,command,result:{outcome:'retired',original_request_id:nonce,original_command_hash}},retirement={...unsigned,receipt_hash:digest(unsigned)};
   assert.ok([...Array(storage.length)].some((_,i)=>{try{const r=JSON.parse(storage.getItem(storage.key(i)));return r.schema==='graph-source-delivery-retirement-outbox-v125'&&r.exact_body===options.body;}catch{return false;}}));
   if(!state.retirementNoCommit){state.retirements.set(command.request_id,retirement);state.retired.set(nonce,retirement);}if(state.retirementLost)throw Error('synthetic lost retirement response');return response({retirement,replayed:false});
  }
  if(path===BASE+'/intents/'+ID(3)+'/retry'&&method==='POST'){
   const command=JSON.parse(options.body),command_hash=await deliveryRetryCommandHash(scope,ID(3),command,webcrypto);
   if(state.retired.has(command.request_id)||state.enforceVersion&&command.job_version!==state.record.delivery.version)return new Response(JSON.stringify({detail:'synthetic old Job version'}),{status:409,headers:{'Content-Type':'application/json'}});
   assert.ok([...Array(storage.length)].some((_,i)=>{try{const p=JSON.parse(storage.getItem(storage.key(i)));return p.schema==='graph-source-delivery-outbox-v124'&&p.exact_body===options.body;}catch{return false;}}));
   const admission={schema:'graph-event-admission-v1',id:ID(9),workspace_id:scope.workspace_id,producer_id:ID(4),intent_id:ID(3),source_key:'synthetic:original',contract_hash:H,source_intent_hash:H,projection:{original_number:1.0},sequence:4,admitted_at:'2026-10-01T00:00:01.125000+00:00',admission_hash:H};
   const receipt={schema:'graph-event-intent-retry-receipt-v1',id:ID(8),request_id:command.request_id,actor_id:scope.actor_id,intent_id:ID(3),producer_id:ID(4),job_id:ID(7),intent_hash:H,command_hash,command,result:{outcome:state.outcome,accepted_job_version:state.outcome==='queued'?command.job_version+1:9,reconciled:state.outcome==='already_admitted',admission:state.outcome==='queued'?null:admission},receipt_hash:H};
   if(!state.noCommit)state.receipts.set(command.request_id,receipt);if(state.lost)throw Error('synthetic lost response with private SQL diagnostics');return response({already_admitted:state.outcome==='already_admitted',delivery:{status:'mutable compatibility only'}});
  }
  unexpected.push(path);throw Error('Unapproved synthetic fetch path');
 }});
 let root,props={actorID:scope.actor_id,workspaceID:scope.workspace_id,producer:{id:ID(4),label:'当前所选来源甲'},onBusy:value=>state.busy.push(value),onReturnToFlow:()=>state.returns++};
 const settle=async()=>{for(let i=0;i<20;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 const button=(label,prefix=false)=>{const found=[...container.querySelectorAll('button')].find(node=>prefix?node.textContent.startsWith(label):node.textContent===label);assert.ok(found,'actual button '+label);return found;};
 const click=async(label,prefix=false)=>{const node=button(label,prefix);assert.equal(node.disabled,false,'enabled actual button '+label);await act(async()=>node.click());await settle();};
 async function reason(text,selector='.graph-delivery-detail textarea'){const node=container.querySelector(selector);assert.ok(node);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(node,text);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();}
 try{
  const Panel=await component(parent),{createRoot}=await import('react-dom/client');
  const paint=()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Panel,props)));
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await act(async()=>paint());await settle();};
  const unmount=async()=>{if(root){const prior=root;root=null;await act(async()=>prior.unmount());await settle();}};
  const changeProps=async patch=>{props={...props,...patch};await act(async()=>paint());await settle();};
  const detail=async()=>{if(parent)await click('已登记来源甲',true);await click('合成对象甲',true);};
  const submit=async()=>{await reason('人工核对后的原恢复说明');await click(state.record.action.kind==='reconcile_receipt'?'核对已送达结果并更新状态':'恢复送达');await click('确认恢复送达');};
  const endingReason=text=>reason(text,'.graph-delivery-end textarea');
  await run({dom,container,state,calls,storage,session,mount,unmount,changeProps,settle,button,click,reason,endingReason,detail,submit,freshSession,text:()=>container.textContent});await unmount();assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const name of names){if(descriptors[name])Object.defineProperty(globalThis,name,descriptors[name]);else delete globalThis[name];}}
}
const posts=h=>h.calls.filter(row=>row.method==='POST');

test('real StrictMode delivery panel shows nearby source, object, Beijing time and evidence with explicit inline confirmation',async()=>harness(async h=>{
 await h.mount();await h.detail();assert.ok(h.text().includes('实际原来源甲'));assert.ok(h.text().includes('合成对象甲'));assert.ok(h.text().includes('北京时间'));assert.ok(h.text().includes('08:00:00'));assert.ok(h.text().includes('温度'));assert.ok(h.text().includes('23.5'));assert.ok(h.text().includes('不能直接作为现场事实'));
 assert.ok(!h.text().includes(H));assert.ok(!h.text().includes(ID(3)));assert.ok(!h.text().includes('JSON'));assert.equal(h.container.querySelector('a'),null);await h.reason('先保存说明再确认');await h.click('恢复送达');assert.equal(posts(h).length,0);assert.ok(h.container.querySelector('[aria-label="确认恢复送达"]'));await h.click('继续核对');assert.equal(posts(h).length,0);
}));
test('real lost response followed by a wholly fresh session uses explicit recovery GET only and keeps the original source detail',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.lost=true;await h.submit();assert.equal(posts(h).length,1);const original=posts(h)[0].body;assert.ok(h.text().includes('上次恢复结果待核对'));assert.ok(!h.text().includes('SQL'));
 await h.unmount();h.freshSession(); // No component mount or automatic retry occurs during a fresh login session.
 await h.mount();assert.equal(posts(h).length,1);assert.equal(h.container.querySelector('[aria-label="实际处理结果详情"]'),null);await h.click('选择并核对上次恢复结果');assert.equal(posts(h).length,1);assert.equal(posts(h)[0].body,original);assert.equal(h.container.querySelector('[aria-label="上次送达恢复结果待核对"]'),null);assert.ok(h.text().includes('上次恢复操作已核对'));assert.ok(h.container.querySelector('[aria-label="实际处理结果详情"]').textContent.includes('实际原来源甲'));
}));
test('current delivered result with an absent retry nonce stays unresolved until explicit same-body resend',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.noCommit=true;h.state.lost=true;await h.submit();const original=posts(h)[0].body;
 h.state.record.intent_status='admitted';h.state.record.admission={id:ID(9),sequence:4,admitted_at:'2026-10-01T00:00:01Z'};h.state.record.action={allowed:true,kind:'reconcile_receipt',reason_code:'receipt_available',label:'核对已送达结果',remaining_retries:0};await h.click('核对上次恢复结果');assert.ok(h.text().includes('不能根据当前状态认定它已保存'));assert.ok(h.text().includes('是否用于某个任务、任务是否完成'));assert.equal(posts(h).length,1);
 h.state.noCommit=false;h.state.lost=false;h.state.outcome='already_admitted';await h.click('按原内容重试恢复送达');assert.equal(posts(h).length,2);assert.equal(posts(h)[1].body,original);assert.equal(h.container.querySelector('[aria-label="上次送达恢复结果待核对"]'),null);assert.ok(h.text().includes('上次恢复操作已核对'));
}));
test('changing producer changes only the list and preserves an unresolved original retry and its actual source',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.lost=true;await h.submit();await h.changeProps({producer:{id:ID(40),label:'另一所选来源乙'}});assert.ok(h.text().includes('另一所选来源乙的送达记录'));assert.ok(h.container.querySelector('[aria-label="实际处理结果详情"]').textContent.includes('实际原来源甲'));assert.ok(h.text().includes('上次恢复结果待核对'));await h.click('读取更多送达记录');await h.click('核对上次恢复结果');assert.equal(posts(h).length,1);assert.ok(h.text().includes('上次恢复操作已核对'));assert.ok(!h.container.querySelector('[aria-label="实际处理结果详情"]').textContent.includes('另一所选来源乙'));
}));
test('real quota failure keeps typed reason, blocks leave and recovers without a request or fabricated saved state',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.quota=true;await h.reason('存储失败后仍保留的人工作业');assert.equal(h.container.querySelector('textarea').value,'存储失败后仍保留的人工作业');assert.equal(h.state.busy.at(-1),true);assert.equal(h.button('恢复送达').disabled,true);const event=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);assert.equal(posts(h).length,0);
 h.state.quota=false;await h.click('重试保存恢复说明');assert.equal(h.state.busy.at(-1),false);assert.equal(h.container.querySelector('textarea').value,'存储失败后仍保留的人工作业');assert.equal(posts(h).length,0);
}));
test('real busy identity recheck conceals immediately and late old login cannot reveal a private reason',async()=>harness(async h=>{
 await h.mount();await h.detail();await h.reason('原账号私有恢复说明');h.state.holdMe=true;await h.click('更新本条结果详情');assert.ok(h.state.meWaiters.length);const old=h.state.meWaiters.splice(0);h.state.actor=ID(90);await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));assert.equal(h.container.querySelector('textarea'),null);assert.ok(!h.text().includes('原账号私有恢复说明'));
 await act(async()=>{old.forEach(fn=>fn());});await h.settle();assert.equal(h.container.querySelector('textarea'),null);assert.ok(h.state.meWaiters.length);h.state.holdMe=false;await act(async()=>h.state.meWaiters.splice(0).forEach(fn=>fn()));await h.settle();assert.equal(h.container.querySelector('textarea'),null);assert.ok(!h.text().includes('原账号私有恢复说明'));assert.ok([...Array(h.storage.length)].some((_,i)=>h.storage.getItem(h.storage.key(i)).includes('原账号私有恢复说明')));assert.equal(posts(h).length,0);
}));
test('real parent keeps an unsaved child reason mounted through parent identity masking and aggregates the leave guard',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.quota=true;await h.reason('父页核对登录期间必须保留的说明');assert.equal(h.button('返回业务与流程选择来源').disabled,true);h.state.holdMe=true;await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));assert.equal(h.container.querySelector('textarea'),null);assert.ok(!h.text().includes('父页核对登录期间必须保留的说明'));assert.equal(h.state.busy.at(-1),true);
 h.state.holdMe=false;await act(async()=>h.state.meWaiters.splice(0).forEach(fn=>fn()));await h.settle();assert.equal(h.container.querySelector('.graph-source-delivery-v124 textarea').value,'父页核对登录期间必须保留的说明');assert.equal(h.button('返回业务与流程选择来源').disabled,true);const event=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
 h.state.quota=false;await h.click('重试保存恢复说明');assert.equal(h.button('返回业务与流程选择来源').disabled,false);await h.click('返回业务与流程选择来源');assert.equal(h.state.returns,1);assert.equal(posts(h).length,0);
},{parent:true}));
test('damaged parent source draft stays untouched while identity-only verification unlocks independent original delivery GET recovery',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.lost=true;await h.submit();const original=posts(h)[0].body;assert.equal(posts(h).length,1);await h.unmount();
 const head=[...Array(h.session.length)].map((_,index)=>h.session.key(index)).find(key=>key.startsWith('ga-graph-sources-v122:')&&key.endsWith(':form-head'));assert.ok(head);const damaged='synthetic damaged parent source head';h.session.setItem(head,damaged);
 await h.mount();assert.ok(h.text().includes('本机恢复记录暂无法读取'));assert.ok(h.text().includes('送达记录暂时隐藏'));assert.ok(!h.text().includes('正在核对登录'));assert.equal(h.container.querySelector('[aria-label="上次送达恢复结果待核对"]'),null);assert.equal(posts(h).length,1);
 const prior=h.calls.filter(row=>row.path==='/api/me').length;await h.click('重新核对登录');assert.ok(h.calls.filter(row=>row.path==='/api/me').length>prior);assert.ok(h.text().includes('本机恢复记录暂无法读取'));assert.ok(h.container.querySelector('[aria-label="上次送达恢复结果待核对"]'));
 await h.click('核对上次恢复结果');assert.equal(posts(h).length,1);assert.equal(posts(h)[0].body,original);assert.equal(h.container.querySelector('[aria-label="上次送达恢复结果待核对"]'),null);assert.ok(h.text().includes('上次恢复操作已核对'));assert.equal(h.session.getItem(head),damaged);assert.equal(h.state.busy.at(-1),true);
 assert.ok(h.calls.some(row=>row.method==='GET'&&row.path.endsWith('/retry-command-requests/'+JSON.parse(original).request_id)));const leave=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(leave);assert.equal(leave.defaultPrevented,true);
},{parent:true}));
test('V125 real inline ending preserves original results and recovers a lost ending response from an empty session by GET only',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.noCommit=true;h.state.lost=true;await h.submit();const original=posts(h)[0].body;await h.click('核对上次恢复结果');await h.endingReason('  原任务版本已变化，结束旧恢复申请  ');assert.ok(h.text().includes('不撤销原处理结果或已送达消息，也不停止来源'));await h.click('结束这次未完成的恢复操作');assert.equal(posts(h).length,1);assert.ok(h.container.querySelector('[aria-label="确认结束未完成的恢复操作"]'));
 h.state.retirementLost=true;await h.click('确认结束这次恢复操作');const ending=posts(h).find(row=>row.path.endsWith('/retry-retirements'));assert.ok(ending);assert.equal(JSON.parse(ending.body).reason,'  原任务版本已变化，结束旧恢复申请  ');assert.deepEqual(JSON.parse(ending.body).original,JSON.parse(original));assert.ok(h.container.querySelector('[aria-label="结束恢复操作的原结果待核对"]'));assert.equal([...h.container.querySelectorAll('button')].some(node=>node.textContent==='按原内容重试恢复送达'),false);
 await h.unmount();h.freshSession();await h.mount();await h.click('读取本机结束操作记录');assert.equal(posts(h).length,2);await h.click('选择并核对结束结果');assert.equal(posts(h).length,2);assert.equal(h.container.querySelector('[aria-label="结束恢复操作的原结果待核对"]'),null);assert.equal(h.container.querySelector('[aria-label="上次送达恢复结果待核对"]'),null);assert.ok(h.container.querySelector('[aria-label="已核对的结束事实"]'));assert.ok(!h.container.querySelector('[aria-label="已核对的上次恢复结果"]'));assert.ok(h.calls.some(row=>row.path.endsWith('/retry-retirements/requests/'+JSON.parse(ending.body).request_id)));assert.ok(h.calls.some(row=>row.path.endsWith('/retry-command-requests/'+JSON.parse(original).request_id)));
}));
test('V125 real quota guard retains separate ending reason and original reason until storage is repaired',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.noCommit=true;h.state.lost=true;await h.submit();await h.click('核对上次恢复结果');const original=h.container.querySelector('.graph-delivery-detail textarea').value;h.state.quota=true;await h.endingReason('存储失败也保留的结束理由');assert.equal(h.container.querySelector('.graph-delivery-end textarea').value,'存储失败也保留的结束理由');assert.equal(h.container.querySelector('.graph-delivery-detail textarea').value,original);assert.equal(h.button('结束这次未完成的恢复操作').disabled,true);assert.equal(posts(h).length,1);const event=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
 h.state.quota=false;await h.click('重试保存恢复说明');assert.equal(h.container.querySelector('.graph-delivery-end textarea').value,'存储失败也保留的结束理由');await h.click('结束这次未完成的恢复操作');await h.click('继续保留原恢复操作');assert.equal(posts(h).length,1);assert.equal(h.state.busy.at(-1),false);
}));
test('V125 a saved original winning the real UI race shows saved recovery and never claims that ending succeeded',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.noCommit=true;h.state.lost=true;await h.submit();await h.click('核对上次恢复结果');await h.endingReason('版本变化后准备结束');await h.click('结束这次未完成的恢复操作');h.state.saveRace=true;await h.click('确认结束这次恢复操作');
 assert.ok(h.text().includes('上次恢复操作已核对'));assert.ok(h.text().includes('结束没有生效'));assert.equal(h.container.querySelector('.graph-delivery-end'),null);assert.equal(h.container.querySelector('[aria-label="结束恢复操作的原结果待核对"]'),null);assert.equal(h.container.querySelector('[aria-label="已核对的结束事实"]'),null);assert.equal(posts(h).filter(row=>row.path.endsWith('/retry-retirements')).length,1);assert.equal(posts(h).filter(row=>row.path.endsWith('/retry')).length,1);
}));
test('V125 real new lost-response recovery never displays the previously ended operation as its success',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.noCommit=true;h.state.lost=true;await h.submit();const old=JSON.parse(posts(h)[0].body);await h.click('核对上次恢复结果');await h.endingReason('结束旧版本的未完成申请');await h.click('结束这次未完成的恢复操作');await h.click('确认结束这次恢复操作');assert.ok(h.container.querySelector('[aria-label="已核对的结束事实"]'));
 const immutable=[...Array(h.storage.length)].map((_,index)=>[h.storage.key(index),h.storage.getItem(h.storage.key(index))]).filter(([key])=>key.includes(':outbox:')||key.includes(':completed:'));await h.submit();const originals=posts(h).filter(row=>row.path.endsWith('/retry'));assert.equal(originals.length,2);assert.notEqual(JSON.parse(originals[1].body).request_id,old.request_id);assert.ok(h.container.querySelector('[aria-label="上次送达恢复结果待核对"]'));assert.equal(h.container.querySelector('[aria-label="已核对的结束事实"]'),null);assert.equal(h.container.querySelector('[aria-label="已核对的上次恢复结果"]'),null);for(const [key,raw] of immutable)assert.equal(h.storage.getItem(key),raw);
}));
test('V125 opening another actual result clears old ending evidence and keeps its immutable history',async()=>harness(async h=>{
 await h.mount();await h.detail();h.state.noCommit=true;h.state.lost=true;await h.submit();await h.click('核对上次恢复结果');await h.endingReason('结束后核对另一条结果');await h.click('结束这次未完成的恢复操作');await h.click('确认结束这次恢复操作');const evidence=h.container.querySelector('[aria-label="已核对的结束事实"]');assert.ok(evidence);assert.ok(evidence.textContent.includes('人工核对后的原恢复说明'));
 const immutable=[...Array(h.storage.length)].map((_,index)=>[h.storage.key(index),h.storage.getItem(h.storage.key(index))]).filter(([key])=>key.includes(':outbox:')||key.includes(':completed:'));h.state.otherRecord={...makeRecord(),intent_id:ID(33),object:{id:ID(35),name:'另一条合成结果乙'}};await h.click('更新送达列表');await h.click('另一条合成结果乙',true);assert.ok(h.container.querySelector('[aria-label="实际处理结果详情"]').textContent.includes('另一条合成结果乙'));assert.equal(h.container.querySelector('[aria-label="已核对的结束事实"]'),null);assert.equal(h.container.querySelector('[aria-label="已核对的上次恢复结果"]'),null);for(const [key,raw] of immutable)assert.equal(h.storage.getItem(key),raw);
}));
