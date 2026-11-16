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
import {FLOW_PLAN_BASE,FLOW_PLANNING_BASE,flowPlanningSHA} from '../src/business-flow-planning.mjs';

// Actual JSX/controller/ReactDOM, synthetic owner HTTP and real JSDOM storage.
// These tests prove the component seam, not model quality, API or PG durability.
const id=n=>'95000000-0000-4000-8000-'+String(n).padStart(12,'0');
const copy=value=>structuredClone(value),project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let temporary,compiled;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'business-flow-planning-react-'));
  const previous=process.env.NODE_ENV;let result;
  try{
   process.env.NODE_ENV='development';
   result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',
    define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/BusinessFlowPlanning.jsx'),write:false,minify:false,
     rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});
  }finally{if(previous===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=previous;}
  const entry=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(entry);
  const target=join(temporary,'Planning.mjs');await writeFile(target,entry.code);
  return (await import(pathToFileURL(target).href)).default;
 })();
 return compiled;
}

async function browser(){
 assert.equal(process.env.NODE_ENV,'development','React and ReactDOM must use one stable development runtime');
 const network=[],errors=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 class NoResources extends ResourceLoader{fetch(url){network.push(String(url));throw Error('external resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',
  {url:'https://synthetic-flow-planning.invalid/',resources:new NoResources(),virtualConsole});
 const boundAdd=dom.window.addEventListener.bind(dom.window),boundRemove=dom.window.removeEventListener.bind(dom.window);
 const eventListeners=new Map();
 const browserEvents={
  addEventListener(type,listener,...options){const set=eventListeners.get(type)||new Set();set.add(listener);eventListeners.set(type,set);return boundAdd(type,listener,...options);},
  removeEventListener(type,listener,...options){eventListeners.get(type)?.delete(listener);return boundRemove(type,listener,...options);},
  dispatchEvent:dom.window.dispatchEvent.bind(dom.window)};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,
  Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,localStorage:dom.window.localStorage,
  sessionStorage:dom.window.sessionStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,
  ...browserEvents,
  fetch:url=>{network.push(String(url));throw Error('real HTTP forbidden');}};
 const originals=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const locks={request:async(_key,options,run)=>{assert.equal(options.mode,'exclusive');return run();}};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks});
 const backing=dom.window.localStorage,session=dom.window.sessionStorage;
 const storage={failWrites:false,getItem:key=>backing.getItem(key),removeItem:key=>backing.removeItem(key),
  setItem(key,value){if(this.failWrites)throw Error('synthetic local storage quota');backing.setItem(key,value);}};
 const snapshot=()=>Object.fromEntries(Array.from({length:backing.length},(_,i)=>{const key=backing.key(i);return[key,backing.getItem(key)];}));
 const container=dom.window.document.getElementById('root');let root,guard=null,Component;
 const register=(next,previous)=>{if(next)guard=next;else if(!previous||guard===previous)guard=null;};
 const {createRoot}=await import('react-dom/client');
 const checkErrors=()=>assert.deepEqual(errors.map(error=>error?.stack||String(error)),[],'actual React/DOM errors must not be hidden by a missing control');
 const until=async(predicate,label)=>{for(let step=0;step<70;step++){checkErrors();if(predicate())return;await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});}checkErrors();assert.ok(predicate(),label);};
 const button=label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(node,'actual button '+label);return node;};
 const click=async label=>{const node=button(label);assert.equal(node.disabled,false,label+' enabled');await act(async()=>node.click());};
 const select=async(label,value)=>{const node=container.querySelector('select[aria-label="'+label+'"]');assert.ok(node,label);assert.equal(node.disabled,false);await act(async()=>{node.value=value;node.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});};
 const type=async(value)=>{const node=container.querySelector('textarea[aria-label="业务流程生成要求"]');assert.ok(node);assert.equal(node.readOnly,false);const set=Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set;await act(async()=>{set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});};
 const Button=({primary,children,...props})=>React.createElement('button',{type:'button',...props},children);
 async function mount(props){Component=Component||await component();root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Component,{...props,Btn:Button,onRegisterGuard:register}))));}
 async function unmount(){if(root){await act(async()=>root.unmount());root=null;await new Promise(resolve=>queueMicrotask(resolve));}}
 async function close(){try{await unmount();checkErrors();assert.deepEqual(network,[]);}finally{dom.window.close();for(const[key,descriptor]of originals){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}}
 return {dom,container,storage,session,locks,snapshot,mount,unmount,close,until,button,click,select,type,
  hasListener:type=>(eventListeners.get(type)?.size||0)>0,guard:()=>guard};
}

function fixture(h,{profile='flow-v184'}={}){
 const account={user_id:id(1),workspace_id:id(2),role:'operator'},producer=id(3);
 for(const[key,value]of Object.entries({'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id,'ga-setup-csrf':'synthetic-csrf'}))h.session.setItem(key,value);
 const qwen={id:id(4),name:'已批准合成Qwen',status:'published',generation_profile:'flow-v184',
  execution:{provider:'qwen',model:'synthetic-qwen',report_evidence_output:{schema:'report-business-flow-output-v184'}}};
 const deepseek={id:id(14),name:'已批准合成DeepSeek',status:'published',generation_profile:'flow-v187',
  execution:{provider:'deepseek',model:'synthetic-deepseek',report_evidence_output:{schema:'report-business-flow-output-v187'}}};
 const causal={id:id(19),name:'核对步骤所需资料来源的合成模型',status:'published',generation_profile:'flow-v190',
  execution:{provider:'deepseek',model:'synthetic-deepseek',report_evidence_output:{schema:'report-business-flow-output-v190'}}};
 const nested={id:id(193),name:'保留条件并行循环层级的合成模型',status:'published',generation_profile:'flow-v193',execution:{provider:'deepseek',model:'synthetic-deepseek',report_evidence_output:{schema:'report-business-flow-output-v193'}}};
 const model=profile==='flow-v193'?nested:profile==='flow-v190'?causal:profile==='flow-v187'?deepseek:qwen,models=profile==='flow-v193'?[qwen,deepseek,causal,nested]:profile==='flow-v190'?[qwen,deepseek,causal]:profile==='flow-v187'?[qwen,deepseek]:[qwen],
  expected=profile==='flow-v193'?{preview:'/preview-flow-nested',start:'/start-flow-nested',provider:'deepseek',model:'synthetic-deepseek'}:profile==='flow-v190'?{preview:'/preview-flow-causal',start:'/start-flow-causal',provider:'deepseek',model:'synthetic-deepseek'}:profile==='flow-v187'?{preview:'/preview-flow-reasoned',start:'/start-flow-reasoned',provider:'deepseek',model:'synthetic-deepseek'}:{preview:'/preview-flow-plan',start:'/start-flow-plan',provider:'qwen',model:'synthetic-qwen'};
 const plan={id:id(5),version:3,revision:9,status:'confirmed',producer_id:producer,plan_hash:'a'.repeat(64),
  source_summary:{input_count:4},ledger:{scopes:[{scope_id:'weather',name:'气象异常核验',claim_positions:[0,2],context_positions:[]}]}};
 const old={schema:'report-scoped-design-view-v181',id:id(6),producer_id:producer,version:1,plan_id:plan.id,scope_id:'weather',
  generation_profile:'flow-v183',status:'failed',result:null,manual_value:null,note:'旧失败回答及费用继续保留',error:'原校验失败'};
 const db={identity:copy(account),items:new Map([[old.id,copy(old)]]),commands:new Map(),lose:true,startGate:null,prepare:0,previewProvider:null};
 const calls=[],generated=[],reviewedParents=[],prefix=`business-flow-planning:v184:${account.workspace_id}:${account.user_id}:${producer}:`;
 const starts=()=>calls.filter(row=>row.path===FLOW_PLANNING_BASE+'/start-flow-plan'||row.path===FLOW_PLANNING_BASE+'/start-flow-reasoned'||row.path===FLOW_PLANNING_BASE+'/start-flow-causal'||row.path===FLOW_PLANNING_BASE+'/start-flow-nested');
 const request=async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,raw:options.body});
  assert.equal(options.headers?.['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-csrf');
  if(path==='/me')return copy(db.identity);
  if(path==='/setup/draft')return {revision:9};
  if(path===FLOW_PLAN_BASE+'/plans?producer_id='+producer)return {producer_id:producer,items:[copy(plan),{...copy(plan),id:id(7),revision:8}]};
  if(path===FLOW_PLANNING_BASE+'?producer_id='+producer)return {producer_id:producer,items:copy([...db.items.values()])};
  if(path===FLOW_PLANNING_BASE+'/preview-flow-plan'||path===FLOW_PLANNING_BASE+'/preview-flow-reasoned'||path===FLOW_PLANNING_BASE+'/preview-flow-causal'||path===FLOW_PLANNING_BASE+'/preview-flow-nested'){
   assert.equal(path,FLOW_PLANNING_BASE+expected.preview);assert.equal(method,'POST');const body=JSON.parse(options.body);
   assert.deepEqual(Object.keys(body),['plan_id','expected_plan_version','plan_hash','scope_id','revision','model_id','note','activity_id','generation_profile']);
   assert.equal(body.plan_id,plan.id);assert.equal(body.expected_plan_version,plan.version);assert.equal(body.generation_profile,profile);
   return {schema:'report-scoped-design-preview-v181',producer_id:producer,plan_id:body.plan_id,plan_version:body.expected_plan_version,
    plan_hash:body.plan_hash,scope_id:body.scope_id,revision:9,model_id:body.model_id,generation_profile:profile,provider:db.previewProvider??expected.provider,model:expected.model,
    note:body.note,call_count:1,requires_separate_authorization:true,partial_scope:true,source_snapshot_hash:'b'.repeat(64),preview_hash:'c'.repeat(64),
    claim_count:4,map_count:2,materials:[{name:'公开合成报告'}],sent_payload:{input_claims:[{statement:'本次业务声明一'},{statement:'本次业务声明二'}],
     spans:[['w:p1/s1','公开合成来源原文：应核对气象异常并保存处理记录。']]},input_summary:{token_bound:1000},
    budget:{schema:'report-scoped-design-budget-v181',call_limit:1,token_bound:1000,conservative_cost_usd:0.125,maximum_cost_usd:3,
     pricing_basis_hash:'d'.repeat(64),grant_hash:'e'.repeat(64)}};
  }
  if(path.startsWith(FLOW_PLANNING_BASE+'/requests/')){
   assert.equal(method,'GET');const [route,query]=path.split('?'),nonce=route.split('/').at(-1),sha=new URLSearchParams(query).get('expected_sha256'),saved=db.commands.get(nonce);
   assert.match(sha,/^[0-9a-f]{64}$/);if(saved)assert.equal(sha,saved.sha);
   return {schema:'report-scoped-design-request-v181',request_id:nonce,wire_sha256:sha,found:!!saved,result:saved?copy(saved.result):null};
  }
  if(path===FLOW_PLANNING_BASE+'/start-flow-plan'||path===FLOW_PLANNING_BASE+'/start-flow-reasoned'||path===FLOW_PLANNING_BASE+'/start-flow-causal'||path===FLOW_PLANNING_BASE+'/start-flow-nested'){
   assert.equal(path,FLOW_PLANNING_BASE+expected.start);assert.equal(method,'POST');const body=JSON.parse(options.body);assert.equal(body.request_id,body.activity_id);assert.equal(body.consent,true);assert.equal(body.generation_profile,profile);
   const original=JSON.parse(h.storage.getItem(prefix+'original:'+body.request_id));
   assert.equal(original.raw,options.body);assert.equal(original.sha256,await flowPlanningSHA(options.body,webcrypto));
   assert.deepEqual(JSON.parse(h.storage.getItem(h.session.getItem(prefix+'tab-pointer'))).pending,original,'exact outbox is durable before the POST');
   assert.equal(db.commands.has(body.request_id),false,'component cannot issue a second start');
   const result={schema:'report-scoped-design-result-v181',id:id(20),job_id:id(21),version:1,operation:'start',status:'queued'};
   db.commands.set(body.request_id,{raw:options.body,sha:original.sha256,result});
   db.items.set(result.id,{schema:'report-scoped-design-view-v181',...copy(result),producer_id:producer,plan_id:plan.id,scope_id:'weather',
    generation_profile:body.generation_profile,result:null,manual_value:null,note:body.note,error:null});
   if(db.startGate)await db.startGate;
   if(db.lose){db.lose=false;throw Error('synthetic POST response lost after durable save');}
   return copy(result);
  }
  const row=db.items.get(path.slice((FLOW_PLANNING_BASE+'/').length));
  if(path.startsWith(FLOW_PLANNING_BASE+'/')&&row){assert.equal(method,'GET');return copy(row);}
  assert.fail('unexpected planning route '+path);
 };
 const onGenerated=async row=>{assert.equal(h.guard()?.(),true,'viewing saved flow must retain the parent leave guard');generated.push(copy(row));};
 const props={account,row:{id:producer},models,request,storage:h.storage,tabStorage:h.session,locks:h.locks,
  prepareDraft:async()=>{db.prepare++;return 9;},onGenerated,onReviewPlan:async parentId=>{reviewedParents.push(parentId);}};
 function saveReview(){
  const attribution={citations:['w:p1/s1'],inferred:true,reason:'公开合成流程建议，实际业务质量仍待人工核对。'};
  const result={schema:'report-business-flow-wire-v182',binding_hash:'f'.repeat(64),business:{name:'气象异常核验',questions:[],
   flows:[{flow_id:'flow_01',name:'人工核验',trigger:{kind:'manual',description:'人工明确发起核验',attribution:copy(attribution)},
    start_node:'f01_n01',end_nodes:['f01_n01'],nodes:[{node_id:'f01_n01',name:'保存核验记录',actor:'human',role:'业务核验人员',
     responsibility:'核对原资料并保存处理记录',inputs:[],outputs:['核验记录'],attribution:copy(attribution)}],edges:[],
    deliverables:[{name:'核验记录',description:'核对原资料并保存处理记录',produced_by:['f01_n01'],attribution:copy(attribution)}]}]},
   evidence:[{span_ref:'w:p1/s1',quote:'公开合成来源原文：应核对气象异常并保存处理记录。'}]};
  db.items.set(id(20),{...db.items.get(id(20)),version:2,status:'review',result,
   ...(profile==='flow-v187'?{projection:{schema:'report-business-flow-projection-v184',raw_plan:{business:{name:'气象异常核验',flows:[{name:'公开合成流程计划',phases:[]}]}}}}:{}),
   ...(profile==='flow-v190'?{projection:{schema:'report-business-flow-projection-v190',wire:copy(result),raw_plan:{business:{name:'气象异常核验',flows:[{name:'公开合成流程计划',phases:[]}]}},raw_causal_plan:{schema:'synthetic-causal-plan-v190',plan:{business:{name:'气象异常核验',flows:[]}},step_sources:[]}}}:{}),
   manual_value:{schema:'report-business-flow-review-v182',result:copy(result),note:'',acknowledge_partial_scope:false,acknowledge_inferences:false}});
  if(profile==='flow-v193'){
   const source=result.business.flows[0],node=source.nodes[0],planValue={schema:'report-business-flow-nested-plan-v192',binding_hash:result.binding_hash,business:{name:result.business.name,questions:[],flows:[{name:source.name,trigger:copy(source.trigger),phase_order:copy(attribution),phases:[],completion:{name:node.name,actor:node.actor,role:node.role,responsibility:node.responsibility,inputs:[],outputs:copy(node.outputs),attribution:copy(node.attribution)}}]}};
   db.items.set(id(20),{...db.items.get(id(20)),result:copy(planValue),projection:{schema:'report-business-flow-projection-v193',wire:copy(planValue),raw_plan:copy(planValue)},manual_value:{schema:'report-business-flow-review-v193',result:copy(planValue),note:'',acknowledge_partial_scope:false,acknowledge_inferences:false}});
  }
 }
 async function configured(){
  await h.mount(props);await h.until(()=>!!h.container.querySelector('select[aria-label="业务流程生成模型"]')&&!h.container.querySelector('select[aria-label="业务流程生成模型"]').disabled,'confirmed current plans and model choices loaded');
  assert.ok(calls.every(row=>row.method==='GET'),'StrictMode initial mount is read-only');
  await h.select('流程生成的已确认范围',plan.id);await h.select('本次生成的业务','weather');await h.select('业务流程生成模型',model.id);
  await h.type('保留用户已确认业务范围和稍后继续的核验要求');
  assert.equal(h.guard()?.(),true,'typing is retained before requesting any human review');
 }
 return {account,producer,model,qwen,deepseek,causal,nested,plan,old,db,calls,generated,reviewedParents,prefix,starts,props,configured,saveReview};
}

test('real StrictMode planning previews actual scope/cost, starts once, recovers its nonce and resumes saved flow without another POST',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h);let release;
 try{
  h.storage.setItem('independent-human-draft','已有人工劳动保持不变');
  await f.configured();await h.click('预览本次范围与报价');
  await h.until(()=>h.container.textContent.includes('核对这一次生成'),'actual preview shown');
  assert.match(h.container.textContent,/本业务已选 2 条／原全部 4 条/);
  assert.match(h.container.textContent,/保守估算 US\$0\.125000/);assert.match(h.container.textContent,/费用上限 US\$3\.00/);
  assert.ok(h.container.textContent.includes('本次业务声明一')&&h.container.textContent.includes('本次业务声明二'));
  assert.ok(h.container.textContent.includes('公开合成来源原文'));assert.equal(f.starts().length,0);
  f.db.startGate=new Promise(resolve=>{release=resolve;});
  await h.click('同意当前范围和费用，生成这项业务流程');
  await h.until(()=>f.starts().length===1,'one actual component start reached the synthetic server');
  assert.equal(h.guard()?.(),false,'in-flight generation cannot abandon pending input');
  const submit=h.button('同意当前范围和费用，生成这项业务流程');assert.equal(submit.disabled,true);
  await act(async()=>submit.click());assert.equal(f.starts().length,1);
  release();await h.until(()=>h.container.textContent.includes('原提交结果待核对')&&!h.button('只核对原请求').disabled,'lost response retains the original pending command');
  await h.click('只核对原请求');
  await h.until(()=>h.container.textContent.includes('活动已保存，后台正在处理'),'queued original activity restored by GET');
  assert.equal(f.starts().length,1);assert.equal(f.db.commands.size,1);assert.equal(h.guard()?.(),true);
  const body=JSON.parse(f.starts()[0].raw),probes=f.calls.filter(row=>row.path.includes('/requests/'+body.request_id+'?'));
  assert.ok(probes.length>=2&&probes.every(row=>row.method==='GET'));
  f.saveReview();
  await h.click('刷新已确认范围和已存活动');await h.until(()=>h.container.textContent.includes('流程已保存，待人工核对'),'saved review activity refreshed');
  await h.click('查看已保存流程');await h.until(()=>f.generated.length===1,'onGenerated opens the actual saved activity');
  assert.equal(f.generated[0].id,id(20));assert.equal(f.generated[0].status,'review');
  assert.deepEqual(f.db.items.get(f.old.id),f.old);assert.equal(h.storage.getItem('independent-human-draft'),'已有人工劳动保持不变');
  const note=h.container.querySelector('textarea').value;await h.unmount();await h.mount(f.props);
  await h.until(()=>h.container.textContent.includes('流程已保存，待人工核对')&&h.container.querySelector('textarea')?.value===note,'same storage/session resumes the saved activity and user requirements');
  assert.equal(f.starts().length,1);assert.equal(f.db.commands.size,1);assert.equal(h.guard()?.(),true);
  assert.equal(f.generated.length,1,'remount does not reopen or regenerate a flow automatically');
 }finally{release?.();await h.close();}
});

test('real planning refuses start when the exact outbox cannot be stored, retaining the work and leave guard',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h);
 try{
  await f.configured();await h.click('预览本次范围与报价');await h.until(()=>h.container.textContent.includes('核对这一次生成'),'preview complete');
  const note=h.container.querySelector('textarea').value;h.storage.failWrites=true;
  await h.click('同意当前范围和费用，生成这项业务流程');
  await h.until(()=>h.container.textContent.includes('当前输入尚未可靠保存'),'write failure is visible');
  assert.equal(f.starts().length,0);assert.equal(f.db.commands.size,0);
  let safe;await act(async()=>{safe=h.guard()?.();});assert.equal(safe,false);
  assert.equal(h.container.querySelector('textarea').value,note);
  h.storage.failWrites=false;await h.click('重试保护本机输入和原请求');
  await h.until(()=>h.guard()?.()===true,'original raw command can be durably retained after storage recovers');
  assert.equal(h.guard()({write:true}),false,'a persisted pending original still prevents unrelated new writes');
  assert.equal(f.starts().length,0,'protecting local work never sends a paid request');
 }finally{h.storage.failWrites=false;await h.close();}
});

test('real planning conceals source text after a session identity change and preserves the prior account journal',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h);
 try{
  await f.configured();await h.click('预览本次范围与报价');await h.until(()=>h.container.textContent.includes('公开合成来源原文'),'original source text shown beside the quote');
  assert.equal(h.hasListener('focus'),true,'the real component registered its focus check on the browser event target');
  const before=h.snapshot();h.session.setItem('ga-setup-csrf-user',id(99));f.db.identity={...f.account,user_id:id(99)};
  await act(async()=>globalThis.dispatchEvent(new h.dom.window.Event('focus')));
  await h.until(()=>!h.container.textContent.includes('公开合成来源原文')&&h.container.textContent.includes('账号或会话已变化'),'foreign identity cannot retain displayed original source');
  assert.equal(h.container.querySelectorAll('textarea,select').length,0);
  assert.equal(f.starts().length,0);assert.equal(f.db.commands.size,0);assert.deepEqual(h.snapshot(),before);
 }finally{await h.close();}
});

test('real shared dropdown selects 187 DeepSeek, preserves its single reasoned nonce across remount and opens the saved flow without 184-only review',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h,{profile:'flow-v187'});
 try{
  h.storage.setItem('independent-human-draft','原184人稿和当前Review选择不变');await f.configured();
  const modelSelect=h.container.querySelector('select[aria-label="业务流程生成模型"]');assert.equal(modelSelect.value,f.deepseek.id);assert.ok([...modelSelect.options].some(option=>option.value===f.qwen.id&&option.textContent.includes('Qwen')));assert.ok([...modelSelect.options].some(option=>option.value===f.deepseek.id&&option.textContent.includes('DeepSeek')));
  await h.click('预览本次范围与报价');await h.until(()=>h.container.textContent.includes('核对这一次生成'),'187 quote appears on the existing planning page');
  assert.match(h.container.textContent,/本业务已选 2 条／原全部 4 条/);assert.match(h.container.textContent,/保守估算 US\$0\.125000/);assert.match(h.container.textContent,/费用上限 US\$3\.00/);for(const phrase of ['气象异常核验','本次业务声明一','本次业务声明二','公开合成来源原文'])assert.ok(h.container.textContent.includes(phrase));
  const preview=f.calls.find(call=>call.path===FLOW_PLANNING_BASE+'/preview-flow-reasoned');assert.ok(preview);assert.equal(JSON.parse(preview.raw).generation_profile,'flow-v187');assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/preview-flow-plan').length,0);assert.equal(f.starts().length,0);
  await h.click('同意当前范围和费用，生成这项业务流程');await h.until(()=>h.container.textContent.includes('原提交结果待核对')&&!h.button('只核对原请求').disabled,'187 lost response keeps the original command');assert.equal(f.starts().length,1);assert.equal(f.starts()[0].path,FLOW_PLANNING_BASE+'/start-flow-reasoned');assert.ok(h.container.textContent.includes('原提交使用 DeepSeek'));
  const raw=f.starts()[0].raw,body=JSON.parse(raw),original=h.storage.getItem(f.prefix+'original:'+body.request_id),note=h.container.querySelector('textarea').value;assert.equal(body.request_id,body.activity_id);assert.equal(body.generation_profile,'flow-v187');assert.equal(JSON.parse(original).sha256,await flowPlanningSHA(raw,webcrypto));
  await h.unmount();await h.mount(f.props);await h.until(()=>h.container.textContent.includes('原提交结果待核对')&&h.container.querySelector('textarea')?.value===note&&!h.button('只核对原请求').disabled,'same legacy namespace restores 187 pending without POST');assert.equal(f.starts().length,1);assert.equal(JSON.parse(h.storage.getItem(h.session.getItem(f.prefix+'tab-pointer'))).pending.raw,raw);
  await h.click('只核对原请求');await h.until(()=>h.container.textContent.includes('活动已保存，后台正在处理'),'original 187 activity restored through GET');assert.equal(f.starts().length,1);assert.equal(f.db.commands.size,1);assert.equal(h.storage.getItem(f.prefix+'original:'+body.request_id),original);assert.equal(f.generated.length,0);
  f.saveReview();await h.click('刷新已确认范围和已存活动');await h.until(()=>h.container.textContent.includes('流程已保存，待人工核对'),'187 candidate with the shared compiled format is available');
  assert.ok(![...h.container.querySelectorAll('button')].some(button=>button.textContent==='对照原文复核并修订'),'187 must not be admitted to the independent 184-parent review protocol even with projection184');assert.equal(f.reviewedParents.length,0);assert.equal(f.generated.length,0);
  await h.click('查看已保存流程');await h.until(()=>f.generated.length===1,'explicit saved-flow action reaches the ordinary human review callback');assert.equal(f.generated[0].generation_profile,'flow-v187');assert.equal(f.generated[0].id,id(20));assert.equal(f.reviewedParents.length,0);assert.equal(f.starts().length,1);assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/start-flow-plan').length,0);assert.ok(!f.calls.some(call=>call.path.includes('flow-revision')));assert.equal(h.storage.getItem('independent-human-draft'),'原184人稿和当前Review选择不变');assert.deepEqual(f.db.items.get(f.old.id),f.old);
 }finally{await h.close();}
});

test('real 187 wrong-provider preview is rejected before any start while retaining the selected model and typed requirements',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h,{profile:'flow-v187'});
 try{
  await f.configured();const note=h.container.querySelector('textarea').value,before=h.snapshot();f.db.previewProvider='qwen';
  await h.click('预览本次范围与报价');await h.until(()=>h.container.textContent.includes('模型协议无法核对'),'response provider mismatch is visible');
  assert.equal(h.container.querySelector('select[aria-label="业务流程生成模型"]').value,f.deepseek.id);assert.equal(h.container.querySelector('textarea').value,note);assert.equal(h.container.querySelector('textarea').readOnly,false);assert.ok(!h.container.textContent.includes('核对这一次生成'));assert.ok(![...h.container.querySelectorAll('button')].some(button=>button.textContent==='同意当前范围和费用，生成这项业务流程'));
  assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/preview-flow-reasoned').length,1);assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/preview-flow-plan').length,0);assert.equal(f.starts().length,0);assert.equal(f.db.commands.size,0);assert.equal(f.generated.length,0);assert.equal(f.reviewedParents.length,0);assert.equal(h.guard()?.(),true);assert.deepEqual(h.snapshot(),before,'failed quote leaves the original form journal unchanged');
 }finally{await h.close();}
});
test('real shared selector offers 190 source checking and opens its separate saved candidate after one causal submission',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h,{profile:'flow-v190'});
 try{
  h.storage.setItem('independent-human-draft','原184与187人工劳动继续保留');await f.configured();const select=h.container.querySelector('select[aria-label="业务流程生成模型"]');assert.equal(select.value,f.causal.id);assert.ok([...select.options].some(option=>option.value===f.qwen.id));assert.ok([...select.options].some(option=>option.value===f.deepseek.id));assert.ok(h.container.textContent.includes('核对步骤所需资料来源'));assert.ok(!h.container.textContent.includes('flow-v190'));
  await h.click('预览本次范围与报价');await h.until(()=>h.container.textContent.includes('核对这一次生成'),'190 source quote shown on the same page');const preview=f.calls.find(call=>call.path===FLOW_PLANNING_BASE+'/preview-flow-causal');assert.ok(preview);assert.equal(JSON.parse(preview.raw).generation_profile,'flow-v190');assert.equal(Object.keys(JSON.parse(preview.raw)).length,9);assert.match(h.container.textContent,/本业务已选 2 条／原全部 4 条/);assert.ok(h.container.textContent.includes('公开合成来源原文'));assert.match(h.container.textContent,/费用上限 US\$3\.00/);
  await h.click('同意当前范围和费用，生成这项业务流程');await h.until(()=>h.container.textContent.includes('原提交结果待核对')&&!h.button('只核对原请求').disabled,'190 original command protected after lost reply');const raw=f.starts()[0].raw,body=JSON.parse(raw);assert.equal(f.starts().length,1);assert.equal(f.starts()[0].path,FLOW_PLANNING_BASE+'/start-flow-causal');assert.equal(body.generation_profile,'flow-v190');assert.equal(body.request_id,body.activity_id);const original=h.storage.getItem(f.prefix+'original:'+body.request_id);
  await h.click('只核对原请求');await h.until(()=>h.container.textContent.includes('活动已保存，后台正在处理'),'190 activity restored without repeating start');assert.equal(f.starts().length,1);assert.equal(f.db.commands.size,1);assert.equal(h.storage.getItem(f.prefix+'original:'+body.request_id),original);f.saveReview();await h.click('刷新已确认范围和已存活动');await h.until(()=>h.container.textContent.includes('流程已保存，待人工核对'),'190 candidate is readable');assert.equal(f.generated.length,0);assert.ok(![...h.container.querySelectorAll('button')].some(button=>button.textContent==='对照原文复核并修订'));
  await h.click('查看已保存流程');await h.until(()=>f.generated.length===1,'explicitly opening the separate 190 candidate');assert.equal(f.generated[0].generation_profile,'flow-v190');assert.equal(f.generated[0].projection.wire.schema,'report-business-flow-wire-v182');assert.ok(f.generated[0].projection.raw_plan.business.flows);assert.ok(f.generated[0].projection.raw_causal_plan);assert.equal(f.reviewedParents.length,0);assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/start-flow-plan'||call.path===FLOW_PLANNING_BASE+'/start-flow-reasoned').length,0);assert.equal(h.storage.getItem('independent-human-draft'),'原184与187人工劳动继续保留');assert.deepEqual(f.db.items.get(f.old.id),f.old);
 }finally{await h.close();}
});

test('real shared selector retains 193 hierarchy and restores the original nested submission before explicitly opening its saved plan',{concurrency:false,timeout:30000},async()=>{
 const h=await browser(),f=fixture(h,{profile:'flow-v193'});
 try{
  h.storage.setItem('independent-human-draft','旧流程和当前未保存人工核对继续保留');await f.configured();
  const select=h.container.querySelector('select[aria-label="业务流程生成模型"]');assert.equal(select.value,f.nested.id);for(const model of [f.qwen,f.deepseek,f.causal])assert.ok([...select.options].some(option=>option.value===model.id));
  assert.ok(h.container.textContent.includes('保留条件、并行与循环的层级'));assert.ok(h.container.textContent.includes('实际能力绑定、任务与执行授权尚未接入'));assert.ok(!h.container.textContent.includes('flow-v193'));
  await h.click('预览本次范围与报价');await h.until(()=>h.container.textContent.includes('核对这一次生成'),'193 complete source range and quote shown');
  const preview=f.calls.find(call=>call.path===FLOW_PLANNING_BASE+'/preview-flow-nested');assert.ok(preview);assert.equal(JSON.parse(preview.raw).generation_profile,'flow-v193');assert.equal(Object.keys(JSON.parse(preview.raw)).length,9);assert.match(h.container.textContent,/本业务已选 2 条／原全部 4 条/);assert.ok(h.container.textContent.includes('公开合成来源原文'));assert.match(h.container.textContent,/保守估算 US\$0\.125000/);assert.match(h.container.textContent,/费用上限 US\$3\.00/);assert.equal(f.starts().length,0);
  await h.click('同意当前范围和费用，生成这项业务流程');await h.until(()=>h.container.textContent.includes('原提交结果待核对')&&!h.button('只核对原请求').disabled,'193 lost start reply retains its original command');
  assert.equal(f.starts().length,1);assert.equal(f.starts()[0].path,FLOW_PLANNING_BASE+'/start-flow-nested');const raw=f.starts()[0].raw,body=JSON.parse(raw),original=h.storage.getItem(f.prefix+'original:'+body.request_id);assert.equal(body.generation_profile,'flow-v193');assert.equal(body.request_id,body.activity_id);assert.equal(JSON.parse(original).sha256,await flowPlanningSHA(raw,webcrypto));
  await h.unmount();await h.mount(f.props);await h.until(()=>h.container.textContent.includes('原提交结果待核对')&&!h.button('只核对原请求').disabled,'193 pending raw restores in the shared original outbox');assert.equal(f.starts().length,1);assert.equal(JSON.parse(h.storage.getItem(h.session.getItem(f.prefix+'tab-pointer'))).pending.raw,raw);
  await h.click('只核对原请求');await h.until(()=>h.container.textContent.includes('活动已保存，后台正在处理'),'193 original nonce recovers through GET');assert.equal(f.starts().length,1);assert.equal(f.db.commands.size,1);assert.equal(h.storage.getItem(f.prefix+'original:'+body.request_id),original);assert.equal(f.generated.length,0);
  f.saveReview();await h.click('刷新已确认范围和已存活动');await h.until(()=>h.container.textContent.includes('流程已保存，待人工核对'),'recursive candidate becomes available without adoption');assert.equal(f.generated.length,0);assert.ok(![...h.container.querySelectorAll('button')].some(button=>button.textContent==='对照原文复核并修订'));
  await h.click('查看已保存流程');await h.until(()=>f.generated.length===1,'user explicitly opens the retained recursive plan');assert.equal(f.generated[0].generation_profile,'flow-v193');assert.equal(f.generated[0].result.schema,'report-business-flow-nested-plan-v192');assert.equal(f.generated[0].projection.wire.schema,'report-business-flow-nested-plan-v192');assert.equal(f.generated[0].manual_value.result.business.flows[0].completion.name,'保存核验记录');assert.equal(f.reviewedParents.length,0);assert.equal(f.starts().length,1);assert.ok(!f.calls.some(call=>/flow-revision|start-flow-plan|start-flow-reasoned|start-flow-causal/.test(call.path)));assert.equal(h.storage.getItem('independent-human-draft'),'旧流程和当前未保存人工核对继续保留');assert.deepEqual(f.db.items.get(f.old.id),f.old);
 }finally{await h.close();}
});
