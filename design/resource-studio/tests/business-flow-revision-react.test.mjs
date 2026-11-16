import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {FLOW_REVISION_BASE,flowRevisionSHA} from '../src/business-flow-revision.mjs';

// Real bundled JSX/controller/ReactDOM. HTTP and documents are public synthetic
// fixtures; these tests do not claim actual API, provider or PG acceptance.
const id=n=>'96000000-0000-4000-8000-'+String(n).padStart(12,'0');
const copy=value=>structuredClone(value),project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let temporary,compiled;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'business-flow-revision-react-'));
  const previous=process.env.NODE_ENV;let result;
  try{
   process.env.NODE_ENV='development';
   result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',
    define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/BusinessFlowRevision.jsx'),write:false,minify:false,
     rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});
  }finally{if(previous===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=previous;}
  const entry=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(entry);
  const target=join(temporary,'Revision.mjs');await writeFile(target,entry.code);
  return (await import(pathToFileURL(target).href)).default;
 })();
 return compiled;
}

async function browser(){
 assert.equal(process.env.NODE_ENV,'development','React and ReactDOM must use the same development runtime');
 const network=[],errors=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 class NoResources extends ResourceLoader{fetch(url){network.push(String(url));throw Error('external resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',
  {url:'https://synthetic-flow-revision.invalid/',resources:new NoResources(),virtualConsole});
 const boundAdd=dom.window.addEventListener.bind(dom.window),boundRemove=dom.window.removeEventListener.bind(dom.window),listeners=new Map();
 const browserEvents={
  addEventListener(type,listener,...options){const set=listeners.get(type)||new Set();set.add(listener);listeners.set(type,set);return boundAdd(type,listener,...options);},
  removeEventListener(type,listener,...options){listeners.get(type)?.delete(listener);return boundRemove(type,listener,...options);},
  dispatchEvent:dom.window.dispatchEvent.bind(dom.window)};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,
  Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,localStorage:dom.window.localStorage,
  sessionStorage:dom.window.sessionStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,...browserEvents,
  fetch:url=>{network.push(String(url));throw Error('real HTTP forbidden');}};
 const originals=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const locks={request:async(_key,options,run)=>{assert.equal(options.mode,'exclusive');return run();}};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks});
 const backing=dom.window.localStorage,session=dom.window.sessionStorage;
 const storage={getItem:key=>backing.getItem(key),setItem:(key,value)=>backing.setItem(key,value),removeItem:key=>backing.removeItem(key)};
 const snapshot=()=>Object.fromEntries(Array.from({length:backing.length},(_,i)=>{const key=backing.key(i);return[key,backing.getItem(key)];}));
 const container=dom.window.document.getElementById('root');let root,guard=null,Component;
 const register=(next,previous)=>{if(next)guard=next;else if(!previous||guard===previous)guard=null;};
 const {createRoot}=await import('react-dom/client');
 const checkErrors=()=>assert.deepEqual(errors.map(error=>error?.stack||String(error)),[],'React/DOM failures must not be hidden');
 const until=async(predicate,label)=>{for(let step=0;step<80;step++){checkErrors();if(predicate())return;await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});}checkErrors();assert.ok(predicate(),label);};
 const button=label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(node,'actual button '+label);return node;};
 const click=async label=>{const node=button(label);assert.equal(node.disabled,false,label+' enabled');await act(async()=>node.click());};
 const select=async(label,value)=>{const node=container.querySelector('select[aria-label="'+label+'"]');assert.ok(node,label);assert.equal(node.disabled,false);await act(async()=>{node.value=value;node.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});};
 const type=async value=>{const node=container.querySelector('textarea[aria-label="原文复核修订要求"]');assert.ok(node);assert.equal(node.readOnly,false);const setter=Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set;await act(async()=>{setter.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});};
 const Button=({primary,children,...props})=>React.createElement('button',{type:'button',...props},children);
 async function mount(props){Component=Component||await component();root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Component,{...props,Btn:Button,onGuardChange:register}))));}
 async function unmount(){if(root){await act(async()=>root.unmount());root=null;await new Promise(resolve=>queueMicrotask(resolve));}}
 async function close(){try{await unmount();checkErrors();assert.deepEqual(network,[]);}finally{dom.window.close();for(const[key,descriptor]of originals){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}}
 return {dom,container,storage,session,locks,snapshot,mount,unmount,close,until,button,click,select,type,guard:()=>guard,hasListener:type=>(listeners.get(type)?.size||0)>0};
}

function fixture(h){
 const account={user_id:id(1),workspace_id:id(2),role:'operator'},producer=id(3);
 for(const[key,value]of Object.entries({'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id,'ga-setup-csrf':'synthetic-csrf'}))h.session.setItem(key,value);
 const model={id:id(4),name:'已批准合成复核Qwen',status:'published',generation_profile:'flow-v185',execution:{provider:'qwen',model:'synthetic-qwen',report_evidence_output:{schema:'report-business-flow-output-v185'}}};
 const wrongModel={...copy(model),id:id(40),name:'旧协议模型不能使用',generation_profile:'flow-v184',execution:{...model.execution,report_evidence_output:{schema:'report-business-flow-output-v184'}}};
 const plan={id:id(5),version:3,revision:9,status:'confirmed',producer_id:producer,plan_hash:'a'.repeat(64),ledger:{scopes:[{scope_id:'review',name:'合成异常复核',claim_positions:[0,2],context_positions:[]},{scope_id:'other',name:'另一合成业务',claim_positions:[1],context_positions:[]}]}};
 function parent(parentId,scope,name){return {schema:'report-scoped-design-view-v181',id:parentId,version:1,producer_id:producer,request_id:id(45),model_id:id(6),plan_id:plan.id,plan_version:plan.version,plan_hash:plan.plan_hash,scope_id:scope,source_snapshot_hash:'b'.repeat(64),status:'needs_clarification',generation_profile:'flow-v184',projection:{schema:'report-business-flow-projection-v184',raw_plan:{schema:'synthetic-plan',business:{name,flows:[{name:'公开合成不可变父AI计划 '+name,phases:[{kind:'steps',steps:[{name:'核对原文并等待人工结论',role:'business_agent'}]}]}],questions:[]}}},manual_value:{result:{business:{name:'另处未保存人工稿不得作为模型输入'}}}};}
 const first=parent(id(7),'review','合成异常复核'),second=parent(id(8),'other','另一合成业务'),parents=new Map([[first.id,first],[second.id,second]]);
 const calls=[],shown=[],exits=[],db={identity:copy(account),items:new Map(),commands:new Map(),lose:true,startGate:null,prepare:0};
 const prefix=parentId=>`business-flow-revision:v185:${account.workspace_id}:${account.user_id}:${producer}:${parentId}:`;
 const starts=()=>calls.filter(row=>row.path===FLOW_REVISION_BASE+'/start-flow-revision');
 const parentSource=p=>({schema:'synthetic-parent-source',binding:{attempt_id:p.id,plan_id:plan.id,plan_hash:plan.plan_hash,scope_id:p.scope_id,source_snapshot_hash:p.source_snapshot_hash,raw_plan_hash:'d'.repeat(64)},raw_plan:copy(p.projection.raw_plan),parent_hash:'e'.repeat(64)});
 function saveRevision(attemptId=id(20),parentId=first.id){const p=parents.get(parentId);db.items.set(attemptId,{schema:'report-scoped-design-view-v181',id:attemptId,version:2,producer_id:producer,parent_attempt_id:p.id,plan_id:plan.id,scope_id:p.scope_id,generation_profile:'flow-v185',status:'needs_clarification',note:'公开合成已保存复核',error:null,projection:{schema:'report-business-flow-projection-v185',parent_source:parentSource(p),raw_review:{findings:[{question:'公开合成复核发现：两项响应需要并行',reason:'原文明确要求同时处理，仍需人工核对',severity:'待核'}],revised_plan:{schema:'synthetic-plan',business:{name:p.projection.raw_plan.business.name,flows:[{name:'公开合成修订候选',phases:[{kind:'parallel',mode:'all',branches:[]}]}],questions:[]}}},wire:{schema:'report-business-flow-wire-v182',business:{name:'公开合成修订候选',flows:[],questions:[]},evidence:[]}}});}
 const request=async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,raw:options.body});assert.equal(options.headers?.['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-csrf');
  if(path==='/me')return copy(db.identity);
  if(path==='/setup/draft')return {revision:9};
  if(path==='/setup/report-scoped-designs/v181/plans?producer_id='+producer)return {producer_id:producer,items:[copy(plan)]};
  if(path===FLOW_REVISION_BASE+'?producer_id='+producer)return {producer_id:producer,items:[...copy([...parents.values()]),...copy([...db.items.values()])]};
  const parentId=path.slice((FLOW_REVISION_BASE+'/').length);if(path.startsWith(FLOW_REVISION_BASE+'/')&&parents.has(parentId)){assert.equal(method,'GET');return copy(parents.get(parentId));}
  if(path===FLOW_REVISION_BASE+'/preview-flow-revision'){
   assert.equal(method,'POST');const body=JSON.parse(options.body),p=parents.get(body.parent_attempt_id);assert.ok(p);assert.equal(body.generation_profile,'flow-v185');assert.equal(body.model_id,model.id);assert.equal(body.plan_id,plan.id);assert.equal(body.scope_id,p.scope_id);
   assert.deepEqual(Object.keys(body),['plan_id','expected_plan_version','plan_hash','scope_id','revision','model_id','note','activity_id','generation_profile','parent_attempt_id']);
   return {schema:'report-scoped-design-preview-v181',producer_id:producer,plan_id:body.plan_id,plan_version:body.expected_plan_version,plan_hash:body.plan_hash,scope_id:body.scope_id,revision:9,model_id:body.model_id,generation_profile:'flow-v185',provider:'qwen',model:'synthetic-qwen',note:body.note,parent_attempt_id:p.id,parent_source_hash:'f'.repeat(64),base_plan_hash:'d'.repeat(64),platform_policy_hash:'1'.repeat(64),source_snapshot_hash:'b'.repeat(64),preview_hash:'c'.repeat(64),call_count:1,requires_separate_authorization:true,partial_scope:true,claim_count:4,sent_payload:{parent_source:parentSource(p),platform_policy:{business_agent:{role:'公开合成平台政策：业务驱动智能体统一决策'},perception_agent:{role:'公开合成平台政策：多模感知负责观察'},data_agent:{role:'公开合成平台政策：数据管理负责来源与持久化'}},input_claims:[{statement:'本次原文复核声明一',status:'explicit',kind:'业务'},{statement:'本次原文复核声明二',status:'inferred',kind:'协同',explanation:'关联关系仍待核对'}],spans:[['w:p1/s1','公开合成复核来源原文：两项响应应同时开展。']]},input_summary:{token_bound:1000},budget:{call_limit:1,conservative_cost_usd:0.625520,maximum_cost_usd:3}};
  }
  if(path.startsWith(FLOW_REVISION_BASE+'/requests/')){assert.equal(method,'GET');const[route,query]=path.split('?'),nonce=route.split('/').at(-1),sha=new URLSearchParams(query).get('expected_sha256'),saved=db.commands.get(nonce);assert.match(sha,/^[0-9a-f]{64}$/);if(saved)assert.equal(saved.sha,sha);return {schema:'report-scoped-design-request-v181',request_id:nonce,wire_sha256:sha,found:!!saved,result:saved?copy(saved.result):null};}
  if(path===FLOW_REVISION_BASE+'/start-flow-revision'){
   assert.equal(method,'POST');const body=JSON.parse(options.body),p=parents.get(body.parent_attempt_id),key=prefix(p.id),original=JSON.parse(h.storage.getItem(key+'original:'+body.request_id));assert.equal(body.request_id,body.activity_id);assert.notEqual(body.request_id,p.request_id);assert.equal(body.consent,true);assert.equal(Object.keys(body).length,14);assert.equal(original.raw,options.body);assert.equal(original.sha256,await flowRevisionSHA(options.body,webcrypto));assert.deepEqual(JSON.parse(h.storage.getItem(h.session.getItem(key+'tab-pointer'))).pending,original,'raw command is durable before the POST');assert.equal(db.commands.has(body.request_id),false,'the component must not repeat a paid start');
   const result={schema:'report-scoped-design-result-v181',id:id(30),job_id:id(31),version:1,operation:'start',status:'queued'};db.commands.set(body.request_id,{raw:options.body,sha:original.sha256,result});db.items.set(result.id,{schema:'report-scoped-design-view-v181',...copy(result),producer_id:producer,parent_attempt_id:p.id,plan_id:plan.id,scope_id:p.scope_id,generation_profile:'flow-v185',projection:null,note:body.note,error:null});if(db.startGate)await db.startGate;if(db.lose){db.lose=false;throw Error('synthetic response lost after durable save');}return copy(result);
  }
  const row=db.items.get(path.slice((FLOW_REVISION_BASE+'/').length));if(path.startsWith(FLOW_REVISION_BASE+'/')&&row){assert.equal(method,'GET');return copy(row);}assert.fail('unexpected revision route '+path);
 };
 const props={account,producerId:producer,parentAttemptId:first.id,models:[wrongModel,model],request,storage:h.storage,tabStorage:h.session,locks:h.locks,prepareDraft:async()=>{db.prepare++;return 9;},onShowResult:async row=>{assert.equal(h.guard()?.(),true);shown.push(copy(row));},onExit:()=>{exits.push(true);}};
 async function configured(mountProps=props){await h.mount(mountProps);await h.until(()=>!!h.container.querySelector('select[aria-label="原文复核修订模型"]')&&!h.container.querySelector('select[aria-label="原文复核修订模型"]').disabled,'normal parent and current confirmed range loaded');assert.ok(calls.every(row=>row.method==='GET'),'StrictMode initialization is GET-only');assert.ok(!h.container.textContent.includes(wrongModel.name));await h.select('原文复核修订模型',model.id);await h.type('请对照来源复核时序、并行和三智能体职责');assert.equal(h.guard()?.(),true);}
 return {account,producer,model,plan,first,second,parents,db,calls,shown,exits,props,prefix,starts,saveRevision,configured};
}

test('real StrictMode 185 previews policy/source/parent and starts once, retaining its nonce without adopting or switching Review',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h);let release;
 try{
  h.storage.setItem('independent-review-current-draft','原184人工修订和当前选择保持不变');const parentBefore=copy(f.first);
  await f.configured();assert.ok(f.calls.some(row=>row.path===FLOW_REVISION_BASE+'/'+f.first.id));assert.ok(h.container.textContent.includes('公开合成不可变父AI计划'));
  await h.click('预览原文、平台政策与独立费用');await h.until(()=>h.container.textContent.includes('核对这一次独立复核'),'full independent quote shown');
  assert.match(h.container.textContent,/本业务已选 2 条／原全部 4 条/);assert.match(h.container.textContent,/保守估算 US\$0\.625520/);assert.match(h.container.textContent,/费用上限 US\$3\.00/);
  for(const text of ['本次原文复核声明一','本次原文复核声明二','公开合成复核来源原文','公开合成平台政策：业务驱动智能体统一决策'])assert.ok(h.container.textContent.includes(text));assert.equal(f.starts().length,0);assert.equal(f.shown.length,0);
  f.db.startGate=new Promise(resolve=>{release=resolve;});await h.click('同意本次范围和独立费用，复核并另存新候选');await h.until(()=>f.starts().length===1,'one component start reaches the synthetic server');assert.equal(h.guard()?.(),false);const submit=h.button('同意本次范围和独立费用，复核并另存新候选');assert.equal(submit.disabled,true);await act(async()=>submit.click());assert.equal(f.starts().length,1);
  release();await h.until(()=>h.container.textContent.includes('原复核提交待核对')&&!h.button('只核对原请求').disabled,'lost response leaves protected original command');assert.equal(h.container.querySelector('textarea').readOnly,true);await h.click('只核对原请求');await h.until(()=>h.container.textContent.includes('后台处理这一次已存活动'),'original queued activity recovered');assert.equal(f.starts().length,1);assert.equal(f.db.commands.size,1);assert.equal(f.shown.length,0);
  const body=JSON.parse(f.starts()[0].raw),probes=f.calls.filter(row=>row.path.includes('/requests/'+body.request_id+'?'));assert.ok(probes.length>=2&&probes.every(row=>row.method==='GET'));assert.equal(body.parent_attempt_id,f.first.id);
  f.saveRevision(id(30));await h.click('刷新已保存复核');await h.until(()=>h.container.textContent.includes('公开合成复核发现：两项响应需要并行'),'findings and revised candidate read through normal GET');assert.ok(h.container.textContent.includes('公开合成修订候选'));assert.equal(f.shown.length,0,'poll/refresh does not select or adopt a new Review draft');
  await h.click('打开这份新候选');await h.until(()=>f.shown.length===1,'explicit show callback only');assert.equal(f.shown[0].id,id(30));assert.equal(f.shown[0].projection.schema,'report-business-flow-projection-v185');assert.equal(h.storage.getItem('independent-review-current-draft'),'原184人工修订和当前选择保持不变');assert.deepEqual(f.first,parentBefore);assert.equal(f.starts().length,1);assert.ok(!f.calls.some(row=>/adopt|publish|\/save|author-review/.test(row.path)));
 }finally{release?.();await h.close();}
});

test('real 185 identity denial immediately conceals parent and findings while retaining pending original storage',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h);
 try{
  f.saveRevision();await f.configured();await h.click('查看已保存复核结果');await h.until(()=>h.container.textContent.includes('公开合成复核发现：两项响应需要并行'),'saved findings are visible before denial');await h.click('预览原文、平台政策与独立费用');await h.until(()=>h.container.textContent.includes('公开合成复核来源原文'),'source beside quote');await h.click('同意本次范围和独立费用，复核并另存新候选');await h.until(()=>h.container.textContent.includes('原复核提交待核对')&&!h.button('只核对原请求').disabled,'pending survives synthetic lost reply');
  assert.ok(h.container.textContent.includes('公开合成不可变父AI计划'));assert.ok(h.container.textContent.includes('公开合成复核发现：两项响应需要并行'));assert.equal(h.hasListener('focus'),true);const before=h.snapshot(),starts=f.starts().length;f.db.identity={...f.account,user_id:id(99)};
  await act(async()=>globalThis.dispatchEvent(new h.dom.window.Event('focus')));await h.until(()=>h.container.textContent.includes('账号或会话已变化')&&!h.container.textContent.includes('公开合成不可变父AI计划')&&!h.container.textContent.includes('公开合成复核发现：两项响应需要并行')&&!h.container.textContent.includes('公开合成复核来源原文'),'real server identity denial redraws the current mount into concealment');
  assert.equal(h.container.querySelectorAll('textarea,select').length,0);assert.equal(f.starts().length,starts);assert.equal(f.db.commands.size,1);assert.deepEqual(h.snapshot(),before,'denial does not delete or rewrite original outbox');assert.equal(f.shown.length,0);
 }finally{await h.close();}
});

test('real unmount/remount restores the same parent pending raw without borrowing another parent nonce',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h);
 try{
  h.storage.setItem('independent-human-draft','不能被重新挂载或复核替换');await f.configured();await h.click('预览原文、平台政策与独立费用');await h.until(()=>h.container.textContent.includes('核对这一次独立复核'),'quote ready');await h.click('同意本次范围和独立费用，复核并另存新候选');await h.until(()=>h.container.textContent.includes('原复核提交待核对')&&!h.button('只核对原请求').disabled,'original command pending');
  const raw=f.starts()[0].raw,body=JSON.parse(raw),key=f.prefix(f.first.id),original=h.storage.getItem(key+'original:'+body.request_id),note=h.container.querySelector('textarea').value,probeCount=f.calls.filter(row=>row.path.includes('/requests/')).length;assert.equal(f.starts().length,1);await h.unmount();
  await h.mount({...f.props,parentAttemptId:f.second.id});await h.until(()=>h.container.textContent.includes('公开合成不可变父AI计划 另一合成业务')&&!!h.container.querySelector('textarea'),'other parent uses independent restored form');assert.equal(h.container.querySelector('textarea').value,'');assert.ok(!h.container.textContent.includes('原复核提交待核对'));assert.equal(f.calls.filter(row=>row.path.includes('/requests/')).length,probeCount,'opening a different parent cannot probe the old nonce');assert.equal(h.storage.getItem(key+'original:'+body.request_id),original);assert.equal(f.starts().length,1);await h.unmount();
  await h.mount(f.props);await h.until(()=>h.container.textContent.includes('原复核提交待核对')&&h.container.querySelector('textarea')?.value===note&&!h.button('只核对原请求').disabled,'same parent pending and form restored');const pointer=h.session.getItem(key+'tab-pointer'),pending=JSON.parse(h.storage.getItem(pointer)).pending;assert.equal(pending.raw,raw);assert.equal(pending.request_id,body.request_id);assert.equal(pending.sha256,await flowRevisionSHA(raw,webcrypto));assert.equal(f.starts().length,1);
  await h.click('只核对原请求');await h.until(()=>h.container.textContent.includes('后台处理这一次已存活动'),'GET restores original single activity after remount');assert.equal(f.starts().length,1);assert.equal(f.db.commands.size,1);assert.equal(f.shown.length,0);assert.equal(h.storage.getItem('independent-human-draft'),'不能被重新挂载或复核替换');assert.equal(h.storage.getItem(key+'original:'+body.request_id),original);
 }finally{await h.close();}
});
