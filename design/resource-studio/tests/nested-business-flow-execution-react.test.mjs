// Actual Vite JSX / ReactDOM / StrictMode, with synthetic server boundaries.
// Does not prove an actual browser, registry, qualification, PG or adoption.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {executionFixture,completeValue,id} from './nested-business-flow-execution-fixture.mjs';
import {open as openReview} from './nested-author-review-fixture.mjs';
import {nestedExecutionState,nestedExecutionValue,nestedExecutionContext,editNestedExecution,saveNestedExecutionDraft} from '../src/nested-business-flow-execution-editor.mjs';
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let directory,compiled;
after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});});
async function component(){if(!compiled)compiled=(async()=>{directory=await mkdtemp(join(tmpdir(),'nested-execution-react-'));const built=await build({root:project,configFile:false,envDir:directory,cacheDir:join(directory,'cache'),logLevel:'silent',mode:'test',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/NestedBusinessFlowExecutionEditor.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});const entry=built.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(entry);const file=join(directory,'component.mjs');await writeFile(file,entry.code);return(await import(pathToFileURL(file).href)).default;})();return compiled;}
async function harness(run,options={}){
 assert.equal(process.env.NODE_ENV,'development');const errors=[],network=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));class NoResources extends ResourceLoader{fetch(url){network.push(String(url));throw Error('External resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-nested-execution.invalid/',resources:new NoResources(),virtualConsole:console}),globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Element:dom.window.Element,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,IS_REACT_ACT_ENVIRONMENT:true,fetch:url=>{network.push(String(url));throw Error('Real HTTP forbidden');}},previous=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 let root,c;const f=await executionFixture(options),container=dom.window.document.getElementById('root');try{const Component=await component(),{createRoot}=await import('react-dom/client'),paint=()=>root?.render(React.createElement(React.StrictMode,null,React.createElement(Component,{controller:c,qualification:f.q,...f.integrations})));
  async function until(check,label){for(let i=0;i<120;i++){assert.deepEqual(errors,[]);if(check())return;await act(async()=>{await new Promise(done=>setImmediate(done));});}assert.ok(check(),label);}
  async function mount(next=f.c){c=next;root=createRoot(container,{onCaughtError:error=>errors.push(error),onUncaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});c.onChange=paint;await act(async()=>paint());await until(()=>!!container.querySelector('[aria-label="选择递归执行流程"],[aria-label="来源暂不可用时保留的执行输入"]')&&!c.busy,'recursive context or independently preserved server draft loaded');}
  async function unmount(){if(root){c.onChange=()=>{};const old=root;root=null;await act(async()=>old.unmount());}}
  const button=label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(node,'actual control '+label);return node;};
  async function select(label,value){const node=[...container.querySelectorAll('select')].find(item=>item.getAttribute('aria-label')===label);assert.ok(node,'actual selector '+label);assert.equal(node.disabled,false);await act(async()=>{node.value=value;assert.equal(node.value,value);node.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});}
  async function click(label){const node=button(label);assert.equal(node.disabled,false,label+' enabled');await act(async()=>node.click());await until(()=>!c.busy,'operation settled');}
  async function type(label,value){const node=[...container.querySelectorAll('input,textarea')].find(item=>item.getAttribute('aria-label')===label);assert.ok(node,'actual field '+label);assert.equal(node.disabled,false);await act(async()=>{const prototype=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});assert.equal(node.value,value);}
  await run({...f,dom,container,mount,unmount,until,button,click,type,select,text:()=>container.textContent||'',get controller(){return c;}});await unmount();assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
 }finally{if(root)await act(async()=>root.unmount());c?.dispose();dom.window.close();for(const[key,value]of previous){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}
test('actual recursive UI switches three flows and saves incomplete user work through remount and original-nonce recovery',{timeout:40000,concurrency:false},()=>harness(async h=>{
 await h.mount();assert.equal(h.posts().length,0);assert.equal(h.container.querySelectorAll('[aria-label="选择递归执行流程"] button').length,3);assert.ok(h.text().includes('仅工程模拟（SIM），非人工业务审查'));assert.ok(h.text().includes('仍保留 3 项待确认问题'));assert.ok(h.text().includes('空分支：直接结束本分支'));assert.equal(h.button('检查本流程的采用范围与执行安排').disabled,true);
 await h.click('公开递归流程 2 · 尚未采用');await h.type('本流程采用后的业务名称','保留完整名称与未完成配置');h.mode.lost=true;await h.click('保存递归执行草稿');assert.ok(nestedExecutionState(h.controller,h.q).pending);const pending=structuredClone(nestedExecutionState(h.controller,h.q).pending),old=h.controller,oldJournal=h.storage.getItem(old.key);await h.unmount();old.dispose();const next=await openReview(h);await h.mount(next);assert.equal(nestedExecutionState(next,h.q).pending.raw,pending.raw);assert.equal(h.storage.getItem(old.key),oldJournal);await h.click('只查询原递归执行请求');assert.equal(nestedExecutionState(next,h.q).pending,null);assert.equal(h.posts().length,1);assert.equal(nestedExecutionValue(next,h.q).selected_flow_index,1);assert.equal(nestedExecutionValue(next,h.q).flows[1].business_name,'保留完整名称与未完成配置');assert.ok(h.text().includes('非人工业务审查'));assert.ok(h.text().includes('服务器保存历史（1 版）'));
}));
test('actual recursive port cards preserve duplicate multiline ordinals and adjacent evidence without mounting the full catalog',{timeout:40000,concurrency:false},()=>harness(async h=>{
 await h.mount();const action=h.container.querySelector('[data-nested-execution-action="/business/flows/0/phases/0/decision"]'),ports=[...action.querySelectorAll('[data-nested-execution-output]')];assert.equal(ports.length,2);assert.deepEqual(ports.map(port=>Number(port.dataset.nestedExecutionOutput)),[0,1]);assert.ok(ports.every(port=>port.querySelector('strong').textContent.includes('重复\n原结果')));await h.type('确认处置建议人工结果 2显示名称','仅修改第二项\n保留项内换行');const node=nestedExecutionValue(h.controller,h.q).flows[0].buffers.nodes['/business/flows/0/phases/0/decision'];assert.equal(node.outputs[1].title,'仅修改第二项\n保留项内换行');assert.equal(node.outputs[0].title,'重复\n原结果');assert.equal(h.text().includes('完整原文候选143'),false);const details=action.querySelector('details');await act(async()=>{details.open=true;details.dispatchEvent(new h.dom.window.Event('toggle'));});await h.until(()=>details.textContent.includes('公开原文A'),'adjacent selected reference is readable');assert.equal(h.text().includes('server_supplied_'),false);assert.equal(h.posts().length,0);
},{mutatePlan:plan=>{const action=plan.business.flows[0].phases[0].decision;action.outputs=['重复\n原结果','重复\n原结果'];action.inputs=['重复\n原资料','重复\n原资料'];}}));
test('actual identity fence conceals recursive names, buffers and source evidence without deleting durable user work',{timeout:40000,concurrency:false},()=>harness(async h=>{
 await h.mount();await h.type('本流程采用后的业务名称','原账号业务配置必须隐藏');const snapshot=h.storage.snapshot();h.session.setItem('ga-setup-csrf-user',id(999));await act(async()=>{await h.controller.recheckIdentity().catch(()=>{});});await h.until(()=>!h.container.querySelector('input,textarea,select'),'identity is concealed');assert.equal(h.text().includes('公开递归流程'),false);assert.equal(h.text().includes('原账号业务配置必须隐藏'),false);assert.deepEqual(h.storage.snapshot(),snapshot);assert.equal(h.posts().length,0);
}));
test('actual execution preview and two explicit acknowledgments adopt once, keeping engineering markers and other flow buffers',{timeout:40000,concurrency:false},()=>harness(async h=>{
 await h.open();const value=completeValue(nestedExecutionContext(h.c,h.q),nestedExecutionValue(h.c,h.q),h.scope.actorId);editNestedExecution(h.c,h.q,value);await saveNestedExecutionDraft(h.c,h.q);await h.mount();assert.equal(h.button('检查本流程的采用范围与执行安排').disabled,false);await h.click('检查本流程的采用范围与执行安排');const panel=h.container.querySelector('[aria-label="递归流程采用预览"]');assert.ok(panel);assert.ok(panel.textContent.includes('公开递归流程 2、公开递归流程 3'));assert.equal(h.button('将此递归流程加入配置').disabled,true);for(const box of panel.querySelectorAll('input[type="checkbox"]'))await act(async()=>box.click());await h.click('将此递归流程加入配置');assert.equal(h.posts().filter(call=>call.path.endsWith('/nested-adopt')).length,1);assert.equal(nestedExecutionValue(h.controller,h.q).flows.length,3);assert.ok(h.text().includes('仅工程模拟（SIM）'));assert.equal(h.calls.some(call=>/\/tasks|\/instances|\/generate|\/start/.test(call.path)),false);
}));
test('actual extractor selector preserves unfinished work and invalidates adoption on a new tool version',{timeout:40000,concurrency:false},()=>harness(async h=>{
 const tool={capability_id:id(999),capability_hash:'a'.repeat(64),release_version:'1.0.0',name:'本地观测规则'};
 const originalHTTP=h.integrations.typedHTTP;
 h.integrations.typedHTTP=async(path,options)=>{
  const raw=await originalHTTP(path,options);
  return path.includes('/nested-context?')?JSON.stringify({...JSON.parse(raw),observation_extractor_options:[tool]}):raw;
 };
 await h.open();
 const value=completeValue(nestedExecutionContext(h.c,h.q),nestedExecutionValue(h.c,h.q),h.scope.actorId);
 editNestedExecution(h.c,h.q,value);
 await saveNestedExecutionDraft(h.c,h.q);
 await h.mount();
 const previewLabel='检查本流程的采用范围与执行安排';
 await h.click(previewLabel);
 const panel=h.container.querySelector('[aria-label="递归流程采用预览"]');
 assert.ok(panel);
 for(const box of panel.querySelectorAll('input[type="checkbox"]'))await act(async()=>box.click());
 await h.select('添加本地观测提取工具',tool.capability_id);
 assert.equal(h.container.querySelector('[aria-label="递归流程采用预览"]'),null);
 assert.equal(h.button(previewLabel).disabled,true);
 assert.equal(nestedExecutionState(h.controller,h.q).consent,false);
 assert.equal(nestedExecutionState(h.controller,h.q).share_source,false);
 await h.click('保存递归执行草稿');
 const saved=h.posts().filter(call=>call.path.endsWith('/nested-drafts/save')).at(-1);
 const original={capability_id:tool.capability_id,capability_hash:tool.capability_hash,actor_id:''};
 assert.deepEqual(JSON.parse(saved.raw).value.flows[0].observation_extractors,[original]);
 const old=h.controller;
 await h.unmount();
 old.dispose();
 tool.capability_hash='b'.repeat(64);
 tool.release_version='2.0.0';
 const next=await openReview(h);
 await h.mount(next);
 assert.ok(h.text().includes('原提取工具或版本已变化'));
 assert.deepEqual(nestedExecutionValue(next,h.q).flows[0].observation_extractors,[original]);
 assert.equal(h.button(previewLabel).disabled,true);
 await h.click('移除提取工具 1');
 await h.select('添加本地观测提取工具',tool.capability_id);
 await h.select('提取负责人 1',h.scope.actorId);
 await h.click('保存递归执行草稿');
 const corrected=h.posts().filter(call=>call.path.endsWith('/nested-drafts/save')).at(-1);
 assert.deepEqual(JSON.parse(corrected.raw).value.flows[0].observation_extractors,[
  {...original,capability_hash:tool.capability_hash,actor_id:h.scope.actorId},
 ]);
 assert.ok(h.text().includes('请先在本流程配置一个已发布的补采服务步骤'));
 assert.equal(h.button(previewLabel).disabled,true);
 assert.equal(h.posts().filter(call=>call.path.endsWith('/nested-preview')).length,1);
 assert.equal(h.posts().filter(call=>call.path.endsWith('/nested-adopt')).length,0);
}));

test('actual cold mount reads saved user input before unavailable fresh context and never creates a default replacement',{timeout:40000,concurrency:false},()=>harness(async h=>{
 await h.open();await saveNestedExecutionDraft(h.c,h.q);h.c.dispose();h.mode.contextUnavailable=true;const next=await openReview(h),before=h.posts().length;await h.mount(next);assert.ok(h.container.querySelector('[aria-label="来源暂不可用时保留的执行输入"]'));assert.ok(h.text().includes('公开递归流程 1'));assert.ok(h.text().includes('不代表最新来源或执行资格'));assert.equal(h.container.querySelector('[aria-label="递归流程逐项执行配置"]'),null);assert.equal(h.posts().length,before);assert.equal(nestedExecutionState(next,h.q).form.version,1);assert.equal(nestedExecutionState(next,h.q).form.dirty,false);assert.equal(h.button('导出递归执行草稿备份').disabled,false);
}));
test('actual cold mount with source422 exposes the original save query and recovers a committed unknown nonce using only GET',{timeout:40000,concurrency:false},()=>harness(async h=>{
 await h.open();h.mode.lost=true;await assert.rejects(saveNestedExecutionDraft(h.c,h.q),/response lost/);const original=structuredClone(nestedExecutionState(h.c,h.q).pending);h.c.dispose();h.mode.contextUnavailable=true;const next=await openReview(h);await h.mount(next);assert.ok(h.container.querySelector('[aria-label="递归执行原请求恢复"]'));assert.equal(nestedExecutionState(next,h.q).pending.raw,original.raw);await h.click('只查询原递归执行请求');assert.equal(nestedExecutionState(next,h.q).pending,null);assert.equal(nestedExecutionState(next,h.q).form.version,1);assert.equal(h.posts().length,1);assert.ok(h.container.querySelector('[aria-label="来源暂不可用时保留的执行输入"]'));
}));
for(const number of ['1.00','1e3'])test('actual numeric buffer '+number+' survives server float normalization and can be explicitly adopted',{timeout:40000,concurrency:false},()=>harness(async h=>{
 await h.open();const ctx=nestedExecutionContext(h.c,h.q),value=completeValue(ctx,nestedExecutionValue(h.c,h.q),h.scope.actorId),action=ctx.action_ports.find(action=>action.actor==='business_agent');value.flows[0].buffers.nodes[action.path].inputs.amount={mode:'literal',value:{mode:'value',raw:''}};editNestedExecution(h.c,h.q,value);await saveNestedExecutionDraft(h.c,h.q);await h.mount();await h.type(action.name+'参数 数量实际值',number);await h.click('保存递归执行草稿');await h.click('检查本流程的采用范围与执行安排');const preview=nestedExecutionState(h.controller,h.q).preview;assert.ok(preview);assert.ok(preview.command_raw.includes(number));assert.ok(preview.raw.includes(number==='1.00'?'1.0':'1000.0'));const panel=h.container.querySelector('[aria-label="递归流程采用预览"]');for(const checkbox of panel.querySelectorAll('input[type="checkbox"]'))await act(async()=>checkbox.click());await h.click('将此递归流程加入配置');assert.equal(h.posts().filter(call=>call.path.endsWith('/nested-adopt')).length,1);assert.equal(nestedExecutionValue(h.controller,h.q).flows[0].buffers.nodes[action.path].inputs.amount.value.raw,number);
}));
test('actual demoted owner still sees saved work and original GET recovery while all editing and POST retry remain unavailable',{timeout:40000,concurrency:false},()=>harness(async h=>{
 await h.open();h.mode.lost=true;await assert.rejects(saveNestedExecutionDraft(h.c,h.q));h.c.dispose();h.mode.readOnly=true;const next=await openReview(h),start=h.calls.length;await h.mount(next);assert.ok(h.container.querySelector('[aria-label="来源暂不可用时保留的执行输入"]'));assert.equal(h.container.querySelector('input,textarea,select'),null);assert.equal(h.button('只查询原递归执行请求').disabled,false);await h.click('只查询原递归执行请求');assert.equal(nestedExecutionState(next,h.q).pending,null);assert.ok(h.calls.slice(start).every(call=>call.method==='GET'));assert.equal(h.posts().length,1);assert.ok(h.text().includes('当前账号为只读'));
}));
