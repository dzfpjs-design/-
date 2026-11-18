import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {JSDOM} from 'jsdom';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'vite';
import {id,account as fixtureAccount} from './report-evidence-v3-fixture.mjs';
import {digest} from '../src/report-full-reading-contract.mjs';
const account={...fixtureAccount,role:'admin'},sha=x=>x.repeat(64);
let Component,verify,tmp;
test.before(async()=>{tmp=await mkdtemp(join(tmpdir(),'diagnostic-ui-'));const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:resolve('src/DelegatedRecoveryDiagnostic.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}});const f=join(tmp,'component.mjs');await writeFile(f,out.output.find(x=>x.isEntry).code);const mod=await import(pathToFileURL(f));Component=mod.default;verify=mod.verifyRecoveryDiagnostic;});
test.after(()=>rm(tmp,{recursive:true,force:true}));
async function fixture(){const row={id:id(41),actor_id:account.user_id,workspace_id:account.workspace_id,request_id:'fl_'+id(42),revision:3,status:'running',view_hash:sha('a')};const value={schema:'report-delegated-recovery-diagnostic-v1',actor_id:account.user_id,workspace_id:account.workspace_id,draft_id:row.id,revision:row.revision,draft_status:row.status,job_status:'running',lease:'expired',allowance_time:'expired',response:'not_recorded',recovery:'not_evaluated',can_resend:false,can_resume:false,can_apply:false};value.diagnostic_hash=await digest(value);return {row,value};}

test('rejects stale, tampered, and wrong-scope diagnostic facts',async()=>{const {row,value}=await fixture();await verify(value,account,row);await assert.rejects(verify({...value,can_resend:true},account,row));await assert.rejects(verify({...value,revision:2},account,row));await assert.rejects(verify(value,{...account,user_id:id(99)},row));});

test('expired lease and absent response do not imply stopped or unsent; account switch hides facts',async()=>{
 const {row,value}=await fixture(),dom=new JSDOM('<div id="app"></div>',{url:'https://ui-test.invalid/'}),names=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'],old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});
 const calls=[],session={invalidIdentity:false,async identity(){},emit(){},async request(path,options){calls.push({path,options});return value;}},root=createRoot(document.getElementById('app'));
 try{await act(async()=>root.render(React.createElement(Component,{account,row,session})));await act(async()=>document.querySelector('button').click());for(let i=0;i<50&&!document.body.textContent.includes('任务处理期限已过');i++)await act(async()=>new Promise(r=>setTimeout(r,5)));assert.match(document.body.textContent,/实际发送状态仍需核对/);assert.match(document.body.textContent,/不能证明请求未发出/);assert.match(document.body.textContent,/本次分析的调用额度有效期已过/);assert.equal(calls.length,1);assert.equal(calls[0].options.method,undefined);await act(async()=>root.render(React.createElement(Component,{account:{...account,user_id:id(99)},row,session})));assert.doesNotMatch(document.body.textContent,/任务处理期限已过/);}finally{await act(async()=>root.unmount());dom.window.close();for(const [k,v] of Object.entries(old)){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('permission rejection invalidates the parent session',async()=>{
 const {row}=await fixture(),dom=new JSDOM('<div id="app"></div>',{url:'https://ui-test.invalid/'}),names=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'],old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});
 let hidden=0;const session={invalidIdentity:false,async identity(){},emit(){},async request(){throw Object.assign(new Error('denied'),{status:403});}},root=createRoot(document.getElementById('app'));
 try{await act(async()=>root.render(React.createElement(Component,{account,row,session,onSessionInvalid:()=>hidden++})));await act(async()=>document.querySelector('button').click());assert.equal(session.invalidIdentity,true);assert.equal(hidden,1);assert.match(document.body.textContent,/权限已变化/);}finally{await act(async()=>root.unmount());dom.window.close();for(const [k,v] of Object.entries(old)){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('eligible recovery names the existing page actions and never executes them',async()=>{
 const {row,value}=await fixture();row.status='failed';row.maps=[{chunk_id:'map-1',status:'succeeded',reserved_call_id:id(88)},{chunk_id:'map-2',status:'failed',reserved_call_id:id(89)}];value.draft_status='failed';value.job_status='failed';value.lease='not_running';value.response='complete_unverified';value.recovery='eligible';const {diagnostic_hash,...unsigned}=value;value.diagnostic_hash=await digest(unsigned);
 const dom=new JSDOM('<div id="app"></div>',{url:'https://ui-test.invalid/'}),names=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'],old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});
 const calls=[],session={async identity(){},emit(){},async request(path,options){calls.push({path,options});return value;}},root=createRoot(document.getElementById('app'));
 try{await act(async()=>root.render(React.createElement(Component,{account,row,session})));await act(async()=>document.querySelector('button').click());for(let i=0;i<50&&!document.body.textContent.includes('逐组进度与已保存回答');i++)await act(async()=>new Promise(r=>setTimeout(r,5)));assert.match(document.body.textContent,/逐组进度与已保存回答/);assert.match(document.body.textContent,/第 2 组/);assert.match(document.body.textContent,/查看原响应状态/);assert.doesNotMatch(document.body.textContent,/刷新回答保存情况/);assert.match(document.body.textContent,/免费尝试恢复已保存回答/);assert.match(document.body.textContent,/不会调用模型/);assert.equal(document.querySelectorAll('button').length,1);assert.equal(calls.length,1);}finally{await act(async()=>root.unmount());dom.window.close();for(const [k,v] of Object.entries(old)){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('eligible reduce directs the user to the existing reduce response control',async()=>{
 const {row,value}=await fixture();row.status='response_unknown';row.reduction={preview:{},reserved_call_id:id(90)};value.draft_status=row.status;value.job_status='failed';value.lease='not_running';value.response='complete_unverified';value.recovery='eligible';const {diagnostic_hash,...unsigned}=value;value.diagnostic_hash=await digest(unsigned);
 const dom=new JSDOM('<div id="app"></div>',{url:'https://ui-test.invalid/'}),names=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'],old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});
 const session={async identity(){},emit(){},async request(){return value;}},root=createRoot(document.getElementById('app'));
 try{await act(async()=>root.render(React.createElement(Component,{account,row,session})));await act(async()=>document.querySelector('button').click());for(let i=0;i<50&&!document.body.textContent.includes('汇总原响应保存情况');i++)await act(async()=>new Promise(r=>setTimeout(r,5)));assert.match(document.body.textContent,/汇总原响应保存情况/);assert.match(document.body.textContent,/查看原响应状态/);assert.match(document.body.textContent,/免费尝试恢复已保存回答/);assert.doesNotMatch(document.body.textContent,/第 \d+ 组/);assert.equal(document.querySelectorAll('button').length,1);}finally{await act(async()=>root.unmount());dom.window.close();for(const [k,v] of Object.entries(old)){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('eligible diagnostic requires exactly one identifiable saved response',async()=>{
 const {row,value}=await fixture();row.status='failed';value.draft_status='failed';value.job_status='failed';value.lease='not_running';value.recovery='eligible';value.response='complete_unverified';row.maps=[{chunk_id:'map-1',status:'failed',reserved_call_id:id(89)},{chunk_id:'map-2',status:'failed',reserved_call_id:id(90)}];const {diagnostic_hash,...unsigned}=value;value.diagnostic_hash=await digest(unsigned);await assert.rejects(verify(value,account,row));
});
