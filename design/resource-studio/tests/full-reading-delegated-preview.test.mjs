import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {build} from 'vite';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {linkedFixture,account,reseal} from './report-delegated-preview-fixture.mjs';

let Component,createRoot,tmp;
const flush=async()=>{await Promise.resolve();await new Promise(r=>setTimeout(r,0));};
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
test.before(async()=>{({createRoot}=await import('react-dom/client'));tmp=await mkdtemp(join(tmpdir(),'delegated-preview-'));const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:resolve('src/FullReadingDelegatedPreview.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}});const file=join(tmp,'component.mjs');await writeFile(file,out.output.find(x=>x.isEntry).code);Component=(await import(pathToFileURL(file))).default;});
test.after(async()=>rm(tmp,{recursive:true,force:true}));

async function env(run){
 const fixture=await linkedFixture(),{row}=fixture,dom=new JSDOM('<div id="root"></div>',{url:'https://test.invalid/setup/',pretendToBeVisual:true});
 const names=['window','document','navigator','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const n of names.slice(0,5))Object.defineProperty(globalThis,n,{configurable:true,writable:true,value:dom.window[n]});
 globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const ctrl={calls:[],mode:'normal',pending:null,mutation:null};
 const request=async(path,options)=>{
  const command=JSON.parse(options.body);ctrl.calls.push({path,options,command});
  if(ctrl.mode==='deferred'){ctrl.pending=deferred();return ctrl.pending.promise;}
  if(['401','403','409'].includes(ctrl.mode))throw Object.assign(Error('private error'),{status:Number(ctrl.mode)});
  const value=await fixture.reply(command);if(ctrl.mutation)await ctrl.mutation(value);return value;
 };
 const root=createRoot(document.getElementById('root')),props={account,row,request};
 const render=async p=>{await act(async()=>root.render(React.createElement(Component,p||props)));await act(flush);};
 const click=async()=>{const b=document.querySelector('section[aria-label="独立分段分析预览"] button');assert.ok(b&&!b.disabled);await act(async()=>{b.click();await flush();});};
 const waitFor=async fn=>{for(let i=0;i<150&&!fn();i++)await act(flush);assert.ok(fn(),'waiting for visible state');};
 try{await render();await run({fixture,row,ctrl,props,render,click,waitFor});}
 finally{await act(async()=>root.unmount());dom.window.close();for(const n of names){if(old[n])Object.defineProperty(globalThis,n,old[n]);else delete globalThis[n];}}
}

test('真实范围只读预览，无手输内部摘要、授权、模型调用或持久化',async()=>env(async({row,ctrl,click,waitFor})=>{
 await click();await waitFor(()=>document.body.textContent.includes('工程推断'));
 const text=document.body.textContent;
 assert.match(text,/独立的新分析/);assert.match(text,/不是恢复原来的第二批/);assert.match(text,/缺少供应商或独立网络证明/);
 assert.match(text,/两种视图来自同一份报告/);assert.match(text,/内容有重叠/);assert.match(text,/可能造成额外费用/);
 assert.match(text,/尚未人工确认|不等于人工确认/);assert.match(text,/暂时不能开始/);assert.match(text,/本次核对操作不会创建分析任务或调用模型/);
 assert.equal(ctrl.calls.length,1);const c=ctrl.calls[0];assert.equal(c.path,'/setup/report-evidence-delegated/preview');
 assert.equal(c.options.method,'POST');assert.equal(c.options.cache,'no-store');assert.equal(c.options.headers['X-Workspace-ID'],account.workspace_id);
 assert.equal(c.command.original_draft_id,row.id);assert.equal(c.command.original_revision,row.revision);
 assert.match(c.command.reading.request_id,/^fl_[0-9a-f-]{36}$/);assert.deepEqual(c.command.reading.selection,row.preview.body.selection);
 assert.equal(c.command.reading.model_id,row.preview.body.model_id);assert.equal('operator_asserted_history_hash' in c.command,false);
 assert.equal('consent' in c.command,false);assert.equal(localStorage.length,0);assert.equal(sessionStorage.length,0);
 assert.equal(document.querySelectorAll('button').length,1);assert.equal(document.querySelectorAll('input').length,0);
}));
for(const status of ['401','403','409'])test('权限或版本错误 '+status+' 隐藏之前预览',async()=>env(async({ctrl,click,waitFor})=>{
 await click();await waitFor(()=>document.body.textContent.includes('工程推断'));ctrl.mode=status;await click();await waitFor(()=>document.querySelector('[role="alert"]'));
 assert.doesNotMatch(document.body.textContent,/工程推断|内容有重叠/);assert.doesNotMatch(document.body.textContent,/private error/);
}));
test('只读账号不发起需要配置权限的预览',async()=>env(async({props,ctrl,render})=>{
 await render({...props,account:{...account,role:'viewer'}});
 assert.match(document.body.textContent,/只能查看已保存记录/);
 assert.equal(document.querySelector('section[aria-label="独立分段分析预览"] button').disabled,true);
 assert.equal(ctrl.calls.length,0);
}));
for(const type of ['can-start','actor','source','new-messages','history','provider','hash'])test('重算摘要后仍拒绝来源或能力篡改：'+type,async()=>env(async({ctrl,click,waitFor})=>{
 ctrl.mutation=async value=>{
  if(type==='can-start')value.can_start=true;
  if(type==='actor')value.basis.actor_id='not-the-actor';
  if(type==='source')value.basis.original_source_hash='b'.repeat(64);
  if(type==='new-messages')value.basis.new_messages_hashes=[...value.basis.original_messages_hashes];
  if(type==='history'){value.basis.history_assessment.independent_transport_or_provider_proof=true;await reseal(value.basis.history_assessment,'assessment_hash');}
  if(type==='provider')value.reading_preview.model='different';
  if(type==='hash'){value.linked_preview_hash='f'.repeat(64);return;}
  await reseal(value,'linked_preview_hash');
 };
 await click();await waitFor(()=>document.querySelector('[role="alert"]'));assert.doesNotMatch(document.body.textContent,/工程推断|内容有重叠/);
}));
for(const change of ['role','version'])test('切换'+change+'后迟到回复不能显示',async()=>env(async({ctrl,props,render,click,waitFor,fixture})=>{
 ctrl.mode='deferred';await click();await waitFor(()=>ctrl.pending);
 const next=change==='role'?{...props,account:{...account,role:'viewer'}}:{...props,row:{...props.row,revision:props.row.revision+1}};
 await render(next);const oldReply=await fixture.reply(ctrl.calls[0].command);await act(async()=>{ctrl.pending.resolve(oldReply);await flush();});
 assert.doesNotMatch(document.body.textContent,/工程推断|内容有重叠/);
}));
