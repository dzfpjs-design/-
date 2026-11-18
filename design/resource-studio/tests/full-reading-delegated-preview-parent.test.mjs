import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {build} from 'vite';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {linkedFixture,account,id,reseal} from './report-delegated-preview-fixture.mjs';
import {memory,tabFor} from './report-full-reading-fixture.mjs';

let Panel,createRoot,tmp;
const flush=async()=>{await Promise.resolve();await new Promise(r=>setTimeout(r,0));};
test.before(async()=>{({createRoot}=await import('react-dom/client'));tmp=await mkdtemp(join(tmpdir(),'delegated-parent-'));const prior=globalThis.sessionStorage;globalThis.sessionStorage=tabFor();try{const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:resolve('src/ReportFullReadingDialog.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}});const file=join(tmp,'panel.mjs');await writeFile(file,out.output.find(x=>x.isEntry).code);Panel=(await import(pathToFileURL(file))).ReportFullReadingPanel;}finally{if(prior===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=prior;}});
test.after(async()=>rm(tmp,{recursive:true,force:true}));

async function env(run){
 const fixture=await linkedFixture(),{f,row}=fixture;
 const list=await reseal({schema:'report-full-reading-list-v1',actor_id:account.user_id,workspace_id:account.workspace_id,items:[]},'list_hash');
 const ctrl={calls:[],mode:'normal',invalid:0};
 const request=async(path,options={})=>{ctrl.calls.push({path,method:options.method||'GET'});if(path==='/me')return account;
  if(path==='/setup/understanding/models')return f.models;if(path==='/setup/report-full-readings')return list;
  if(path==='/setup/report-drafts/active')return {blocked:false};if(path==='/setup/report-full-readings/'+row.id)return row;
  if(path==='/setup/report-evidence-delegated/preview'){if(ctrl.mode!=='normal')throw Object.assign(Error('denied'),{status:Number(ctrl.mode)});return fixture.reply(JSON.parse(options.body));}
  throw Error('unexpected synthetic request');};
 const dom=new JSDOM('<div id="root"></div>',{url:'https://test.invalid/setup/',pretendToBeVisual:true}),names=['window','document','navigator','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const n of names.slice(0,5))Object.defineProperty(globalThis,n,{configurable:true,writable:true,value:dom.window[n]});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const Btn=({children,primary,...props})=>React.createElement('button',props,children),Dialog=({children})=>React.createElement('div',null,children),root=createRoot(document.getElementById('root'));
 const props={account,material:{id:id(3),name:'合成报告',version:1,hash:f.original_ref.hash},Dialog,Btn,prepareDraft:async()=>1,onClose(){},onSessionInvalid(){ctrl.invalid++;},onRegisterCloseGuard(){},initialDraftID:row.id,request,storage:memory(),tabStorage:tabFor()};
 const click=async()=>{const b=document.querySelector('section[aria-label="独立分段分析预览"] button');assert.ok(b&&!b.disabled);await act(async()=>{b.click();await flush();});};
 const waitFor=async fn=>{for(let i=0;i<150&&!fn();i++)await act(flush);assert.ok(fn(),'waiting for visible state');};
 try{await act(async()=>{root.render(React.createElement(Panel,props));await flush();});await waitFor(()=>document.querySelector('section[aria-label="独立分段分析预览"] button'));await run({ctrl,click,waitFor});}
 finally{await act(async()=>root.unmount());dom.window.close();for(const n of names){if(old[n])Object.defineProperty(globalThis,n,old[n]);else delete globalThis[n];}}
}

test('父对话框通过身份复核请求只读范围，不启动模型',async()=>env(async({ctrl,click,waitFor})=>{
 await click();await waitFor(()=>document.body.textContent.includes('工程推断'));assert.match(document.body.textContent,/暂时不能开始/);
 const posted=ctrl.calls.filter(c=>c.method==='POST');assert.equal(posted.length,1);assert.equal(posted[0].path,'/setup/report-evidence-delegated/preview');
 assert.ok(ctrl.calls.filter(c=>c.path==='/me').length>=1);
}));
for(const status of ['401','403'])test('父对话框 '+status+' 隐藏已显示的私人报告并仅通知一次',async()=>env(async({ctrl,click,waitFor})=>{
 await click();await waitFor(()=>document.body.textContent.includes('工程推断'));ctrl.mode=status;await click();
 await waitFor(()=>document.body.textContent.includes('暂不显示私人报告内容'));
 assert.doesNotMatch(document.body.textContent,/工程推断|合成报告/);assert.equal(ctrl.invalid,1);
}));
