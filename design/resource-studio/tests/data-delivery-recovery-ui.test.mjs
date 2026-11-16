// The seven original recovery invariants now run through actual React DOM,
// ManualObservationForm, DataDeliveryStudio, controller and runtime-api.
// Only HTTP/system-of-record rows are synthetic; no real ACK is claimed.
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import {build} from 'vite';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {deliveryFixture,id,copy,sha} from './data-delivery-v245-fixture.mjs';
const project=resolve(fileURLToPath(new URL('..',import.meta.url))),require=createRequire(join(project,'package.json')),reactPath=require.resolve('react'),reactURL=pathToFileURL(reactPath).href,H='a'.repeat(64);
let compiled;
async function components(){
 if(!compiled)compiled=(async()=>{
  const folder=await mkdtemp(join(tmpdir(),'delivery-recovery-migration-')),entry=join(folder,'entry.jsx');
  await writeFile(entry,'export {default as Manual} from '+JSON.stringify(join(project,'src/ManualObservationForm.jsx'))+';export {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';');
  const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),logLevel:'silent',define:{'process.env.NODE_ENV':'"development"'},build:{ssr:entry,write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}});
  const chunk=result.output.find(row=>row.type==='chunk'&&row.isEntry),file=join(folder,'actual.mjs');assert.ok(chunk);await writeFile(file,chunk.code);return import(pathToFileURL(file).href);
 })();return compiled;
}
const fields=[{key:'note',label:'原登记说明',type:'string',required:true},{key:'quantity',label:'数量',type:'integer',required:false},{key:'checked',label:'已复核',type:'boolean',required:false}];
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};
async function harness(run){
 assert.equal(process.env.NODE_ENV,'development');const f=deliveryFixture();f.state.revision=7;
 const source=id(20),extractor=id(21),key='ga-reported:'+f.account.workspace_id+':'+f.account.username+':'+f.instanceId;
 const catalog={sources:[{id:source,name:'合成人工来源',timezone:'Asia/Shanghai'}],rules:[{id:extractor,name:'原样登记',version:1,content_hash:H,rules:[],registration_mode:'passthrough',registration_fields:fields}]};
 const original={id:f.observationId,version:7,entity_id:f.entityId,source_id:source,extractor_id:extractor,request_id:id(22),processing_version:'1.0.0',status:'processed',data_received:false,can_verify:false,retry_available:false,block_reason:'等待原结果交接',review_basis:{fields,original_attributes:{note:'已保存原登记',quantity:0,checked:false},extracted_attributes:{note:'已保存原登记',quantity:0,checked:false},verification_input_hash:H,verification:null,evidence_ids:[],source_name:'合成人工来源',extractor_name:'原样登记',observed_at:'2026-10-18T00:00:00Z',integrity_issue:null},data_delivery:{status:'blocked',error:'固定输出依据缺失，请恢复后核对',retry_available:true}};
 const state={postGate:null,casReject:false,wire:[],refreshes:0,postSettled:0},errors=[],gates=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e));
 class NoNetwork extends ResourceLoader{fetch(){throw Error('External resources forbidden in a synthetic React regression');}}
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://delivery-recovery.invalid/',resources:new NoNetwork(),virtualConsole:vc}),response=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
 const fetch=async(raw,options={})=>{
  const u=new URL(String(raw),dom.window.location.href),path=u.pathname.replace(/^\/api/,''),method=options.method||'GET';state.wire.push({path:path+u.search,method,raw:options.body});
  try{
   assert.equal(u.origin,dom.window.location.origin);
   if(path==='/me')return response(await f.api(path,options));
   if(path.endsWith('/reported-observation-options')){assert.equal(method,'GET');return response(catalog);}
   if(path.startsWith(f.base)){
    if(path===f.base+'/retry'){assert.equal(method,'POST');if(state.postGate)await state.postGate.promise;if(state.casReject){f.state.revision=8;return response({detail:'原收件状态已变化，请保留原申请并重新核对'},409);}}
    return response(await f.api(path+u.search,options));
   }
   assert.equal(method,'GET','delivery recovery cannot register or extract observations');
   return response({detail:'本合成场景没有启用此附属能力，明确不可用'},503);
  }catch(e){if(e.name==='AssertionError')errors.push(e);return response({detail:e.message},e.status||503);}
  finally{if(path===f.base+'/retry')state.postSettled++;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,location:dom.window.location,history:dom.window.history,localStorage:f.storage,sessionStorage:f.session,crypto:webcrypto,fetch,IS_REACT_ACT_ENVIRONMENT:true,addEventListener:dom.window.addEventListener.bind(dom.window),removeEventListener:dom.window.removeEventListener.bind(dom.window)},prior=new Map(Object.keys(globals).map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
 for(const[name,value]of Object.entries(globals))Object.defineProperty(globalThis,name,{value,configurable:true,writable:true});Object.defineProperty(dom.window.navigator,'locks',{value:f.locks,configurable:true});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 const modules=await components(),{createRoot}=await import('react-dom/client');modules.setSession(f.csrf,f.account.workspace_id,f.account.user_id,f.account.username);
 const container=document.getElementById('root');let root,props,guard;
 const previousConsoleError=console.error;console.error=(...values)=>{const message=values.map(String).join(' ');if(/overlapping act|without await/.test(message))errors.push(Error(message));else previousConsoleError(...values);};
 const Button=({children,primary,ghost,small,icon,...rest})=>React.createElement('button',{type:'button',...rest},children);
 const register=(fn,previous)=>{if(fn)guard=fn;else if(!previous||guard===previous)guard=null;};
 const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label);
 const until=async(predicate,label='actual React regression state')=>{for(let i=0;i<300;i++){assert.deepEqual(errors,[]);if(predicate())return;await act(async()=>new Promise(resolve=>setTimeout(resolve,2)));}assert.ok(predicate(),label+': '+container.textContent.slice(0,1200));};
 const click=async label=>{await until(()=>button(label)&&!button(label).matches(':disabled'),label);await act(async()=>button(label).click());};
 const change=async(label,value)=>{const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node,label);assert.equal(node.matches(':disabled'),false,label+' is editable');await act(async()=>{const proto=node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});};
 const check=async label=>{const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node);assert.equal(node.matches(':disabled'),false);await act(async()=>node.click());};
 const renderCurrent=()=>root.render(React.createElement(React.StrictMode,null,React.createElement(modules.Manual,props)));
 const render=async()=>act(async()=>renderCurrent());
 // An async DOM click handler does not await a nested harness act(). The
 // callback schedules the real parent update; only the outer driver owns act.
 const mount=async extra=>{props={instanceId:f.instanceId,account:f.account,releaseID:f.release.id,objects:f.objects,enabled:true,observations:[original],Btn:Button,onRegisterGuard:register,onRefresh:()=>{state.refreshes++;props={...props,observations:props.observations.map(row=>({...row,version:f.state.revision,data_received:f.state.delivery==='received'}))};renderCurrent();},...extra};root=createRoot(container,{onUncaughtError:e=>errors.push(e),onCaughtError:e=>errors.push(e),onRecoverableError:e=>errors.push(e)});await render();};
 const unmount=async()=>{if(root){const old=root;root=null;await act(async()=>old.unmount());await Promise.resolve();}};
 const update=async patch=>{props={...props,...patch};await render();};
 const settle=async()=>{for(let i=0;i<8;i++)await act(async()=>new Promise(resolve=>setTimeout(resolve,0)));};
 const fillRegistration=async()=>{await until(()=>container.querySelector('[aria-label="规则登记对象"]')?.options.length>1&&!container.querySelector('[aria-label="规则登记对象"]').matches(':disabled'));await change('规则登记对象',f.entityId);await change('人工数据来源',source);await change('本地处理方式',extractor);await change('登记字段 原登记说明','  当前未提交人工稿  ');await change('登记字段 数量','0');await change('登记字段 已复核','false');};
 const openDelivery=async()=>{await click('查看这条观测的数据收件');await until(()=>container.querySelector('[aria-label="原数据投递重试原因"]')&&!container.querySelector('[aria-label="原数据投递重试原因"]').matches(':disabled'));};
 const confirmDelivery=async()=>{await change('原数据投递重试原因','恢复固定原件后重交原投递');await check('确认只重试原数据投递');};
 const posts=()=>state.wire.filter(row=>row.method==='POST');
 const assertRegistration=raw=>{assert.equal(f.storage.getItem(key),raw);assert.equal(container.querySelector('[aria-label="登记字段 原登记说明"]').value,'  当前未提交人工稿  ');assert.equal(container.querySelector('[aria-label="登记字段 数量"]').value,'0');assert.equal(container.querySelector('[aria-label="登记字段 已复核"]').value,'false');};
 try{await run({f,state,container,key,source,extractor,original,mount,unmount,update,render,settle,until,click,change,check,button,fillRegistration,openDelivery,confirmDelivery,posts,assertRegistration,guard:()=>guard,text:()=>container.textContent,gate:()=>{const g=deferred();gates.push(g);return g;}});assert.deepEqual(errors,[]);}
 finally{try{for(const g of gates)g.resolve();await unmount();assert.deepEqual(errors,[]);}finally{console.error=previousConsoleError;dom.window.close();for(const[name,value]of prior){if(value)Object.defineProperty(globalThis,name,value);else delete globalThis[name];}}}
}

test('blocked and needs-review delivery expose the reason and explicit recovery; waiting and legacy records remain waiting',{concurrency:false,timeout:30000},async()=>harness(async h=>{
 const review={...copy(h.original),id:id(40),data_delivery:{status:'needs_review',error:'原结果版本需要核对',retry_available:true}},waiting={...copy(h.original),id:id(41),status:'received',data_delivery:{status:'waiting',error:null,retry_available:false}},legacy={...copy(h.original),id:id(42)};delete legacy.data_delivery;
 await h.mount({observations:[h.original,review,waiting,legacy]});await h.until(()=>h.container.querySelectorAll('.data-delivery-entry button').length===4);
 for(const wording of ['数据管理：接收受阻','固定输出依据缺失，请恢复后核对','数据管理：接收依据需核查','原结果版本需要核对','数据管理：等待送达'])assert.ok(h.text().includes(wording),wording);
 assert.equal(h.button('重新核对并接收原结果'),undefined);assert.equal(h.button('重试保存的本地处理'),undefined);
 await h.openDelivery();assert.match(h.text(),/原投递被拒收/);assert.match(h.text(),/原数据接收受阻/);assert.equal(h.button('保存原因并重试原数据投递').matches(':disabled'),true);assert.equal(h.posts().length,0);
}));
test('recovery POST targets the exact original observation and CAS context, then rereads without registering or extracting again',{concurrency:false,timeout:30000},async()=>harness(async h=>{
 const before=copy(h.original);await h.mount();await h.fillRegistration();const registration=h.f.storage.getItem(h.key);await h.openDelivery();await h.confirmDelivery();const expected=h.f.context().context_hash;
 await h.click('保存原因并重试原数据投递');await h.until(()=>h.text().includes('原申请已保存')&&!h.container.querySelector('[aria-label="原收件申请仍待核对"]'));
 assert.equal(h.posts().length,1);const post=h.posts()[0];assert.equal(post.path,h.f.base+'/retry');const body=JSON.parse(post.raw);assert.deepEqual(Object.keys(body).sort(),['schema','request_id','expected_context_hash','reason','confirm_retry'].sort());assert.equal(body.expected_context_hash,expected);assert.equal(body.reason,'恢复固定原件后重交原投递');assert.equal(body.confirm_retry,true);
 const saved=JSON.parse(h.f.storage.getItem(h.f.prefix+'original:'+body.request_id));assert.equal(saved.raw,post.raw);assert.equal(saved.sha256,sha(post.raw));assert.equal(saved.scope.observation_id,h.original.id);assert.match(h.text(),/尚未收件，等待原投递/);assert.equal(h.state.refreshes,0);
 h.f.state.delivery='received';h.f.state.revision=8;await h.click('核对当前实际收件');await h.until(()=>h.text().includes('独立接收记录与原投递已核对'));await h.click('收件已核对，刷新上方记录');assert.equal(h.state.refreshes,1);assert.equal(h.posts().length,1);h.assertRegistration(registration);assert.deepEqual(h.original,before);
}));
test('CAS rejection or lost response leaves the original record, typed draft and retry target intact, with no automatic retry on remount',{concurrency:false,timeout:30000},async()=>{
 for(const failure of ['conflict','network'])await harness(async h=>{
  const before=copy(h.original);await h.mount();await h.fillRegistration();const registration=h.f.storage.getItem(h.key);await h.openDelivery();await h.confirmDelivery();h.state.casReject=failure==='conflict';h.f.state.mode=failure==='network'?'unknown':'commit';
  await h.click('保存原因并重试原数据投递');await h.until(()=>h.container.querySelector('[aria-label="原收件申请仍待核对"]')&&!h.button('只核对原收件申请').disabled);const pending=h.f.storage.getItem(h.f.prefix+'pending');assert.ok(pending);assert.equal(h.posts().length,1);assert.equal(h.state.refreshes,0);assert.match(h.text(),failure==='conflict'?/原收件状态已变化/:/原申请回复未知/);h.assertRegistration(registration);assert.deepEqual(h.original,before);
  await h.unmount();await h.mount();await h.until(()=>h.container.querySelector('[aria-label="原收件申请仍待核对"]')&&!h.button('只核对原收件申请').disabled);assert.equal(h.posts().length,1);assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),pending);await h.click('只核对原收件申请');await h.until(()=>h.text().includes('送达结果仍未知'));assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),pending);h.assertRegistration(registration);assert.equal(h.state.refreshes,0);
 });
});
test('an unrelated frozen registration request is never cleared or resent by delivery recovery',{concurrency:false,timeout:30000},async()=>harness(async h=>{
 const pending={release_id:h.f.release.id,source_id:h.source,source_record_id:id(45),entity_id:h.f.entityId,extractor_id:h.extractor,attributes:{note:'独立未决请求',quantity:0,checked:false},observed_at:'2026-10-18T00:00:00Z',business_day:'2026-10-18',startup_mode:'manual'},cached={object:h.f.entityId,source:h.source,rule:h.extractor,values:{note:'独立未决请求',quantity:'0',checked:false},when:'2026-10-18T08:00',pending,saved:null,attempts:[{status:'saved',record_id:id(46)}],scope:{workspace_id:h.f.account.workspace_id,user_id:h.f.account.user_id,username:h.f.account.username,instance_id:h.f.instanceId}};
 h.f.storage.setItem(h.key,JSON.stringify(cached));const raw=h.f.storage.getItem(h.key);await h.mount();await h.until(()=>h.container.querySelector('[aria-label="登记字段 原登记说明"]'));assert.equal(h.container.querySelector('[aria-label="登记字段 原登记说明"]').matches(':disabled'),true);
 await h.openDelivery();await h.confirmDelivery();await h.click('保存原因并重试原数据投递');await h.until(()=>h.text().includes('原申请已保存')&&!h.container.querySelector('[aria-label="原收件申请仍待核对"]'));assert.equal(h.posts().length,1);assert.equal(h.posts()[0].path,h.f.base+'/retry');assert.equal(h.f.storage.getItem(h.key),raw);assert.deepEqual(JSON.parse(raw).pending,pending);assert.equal(h.button('按原编号重试登记').disabled,false);
}));
test('fresh identity and write permission are rechecked before recovery; viewer or another actor cannot POST',{concurrency:false,timeout:30000},async()=>{
 for(const patch of [{role:'viewer'},{user_id:id(55)}])await harness(async h=>{
  await h.mount();await h.fillRegistration();const raw=h.f.storage.getItem(h.key);await h.openDelivery();await h.confirmDelivery();h.f.state.identity={...h.f.account,...patch};await h.click('保存原因并重试原数据投递');await h.until(()=>h.container.querySelector('[aria-label="数据收件范围已隐藏"]'));assert.equal(h.posts().length,0);h.assertRegistration(raw);assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),null);assert.equal(h.guard()(),false);
 });
});
test('a late retry response after instance switch does not refresh the new instance or replace either registration draft',{concurrency:false,timeout:30000},async()=>harness(async h=>{
 await h.mount();await h.fillRegistration();const oldDraft=h.f.storage.getItem(h.key);await h.openDelivery();await h.confirmDelivery();const g=h.gate();h.state.postGate=g;await h.click('保存原因并重试原数据投递');await h.until(()=>h.posts().length===1);const oldPending=h.f.storage.getItem(h.f.prefix+'pending');assert.ok(oldPending);
 const nextInstance=id(60);await h.update({instanceId:nextInstance,observations:[],objects:[]});await h.until(()=>h.container.querySelector('[aria-label="规则登记对象"]').value==='');const newKey='ga-reported:'+h.f.account.workspace_id+':'+h.f.account.username+':'+nextInstance,newDraft=h.f.storage.getItem(newKey);g.resolve();await h.until(()=>h.state.postSettled===1);await h.settle();
 assert.equal(h.state.refreshes,0);assert.equal(h.posts().length,1);assert.equal(h.f.storage.getItem(h.key),oldDraft);assert.equal(h.f.storage.getItem(newKey),newDraft);assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),oldPending);assert.equal(h.container.querySelector('[aria-label="观测数据收件与恢复"]'),null);assert.equal(h.container.querySelector('[aria-label="规则登记对象"]').value,'');
}));
test('switching scope during delayed identity preflight cancels recovery before POST',{concurrency:false,timeout:30000},async()=>harness(async h=>{
 await h.mount();await h.fillRegistration();const oldDraft=h.f.storage.getItem(h.key);await h.openDelivery();await h.confirmDelivery();const g=h.gate(),beforeAuth=h.state.wire.filter(row=>row.path==='/me').length;h.f.state.identityGate=g.promise;await h.click('保存原因并重试原数据投递');await h.until(()=>h.state.wire.filter(row=>row.path==='/me').length>beforeAuth);
 const nextInstance=id(61);await h.update({instanceId:nextInstance,observations:[],objects:[]});h.f.state.identityGate=null;g.resolve();await h.settle();await h.until(()=>h.container.querySelector('[aria-label="规则登记对象"]').value==='');assert.equal(h.posts().length,0);assert.equal(h.state.refreshes,0);assert.equal(h.f.storage.getItem(h.key),oldDraft);assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),null);assert.equal(h.container.querySelector('[aria-label="观测数据收件与恢复"]'),null);
}));
