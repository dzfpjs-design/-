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
import {digest} from '../src/report-full-reading-contract.mjs';

let Panel,createRoot,tmp;
const flush=async()=>{await Promise.resolve();await new Promise(r=>setTimeout(r,0));};
test.before(async()=>{({createRoot}=await import('react-dom/client'));tmp=await mkdtemp(join(tmpdir(),'delegated-parent-'));const prior=globalThis.sessionStorage;globalThis.sessionStorage=tabFor();try{const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:resolve('src/ReportFullReadingDialog.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}});const file=join(tmp,'panel.mjs');await writeFile(file,out.output.find(x=>x.isEntry).code);Panel=(await import(pathToFileURL(file))).ReportFullReadingPanel;}finally{if(prior===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=prior;}});
test.after(async()=>rm(tmp,{recursive:true,force:true}));

async function env(run,{admin=false}={}){
 const activeAccount=admin?{...account,role:'admin'}:account;
 const fixture=await linkedFixture(),{f,row}=fixture;
 const list=await reseal({schema:'report-full-reading-list-v1',actor_id:account.user_id,workspace_id:account.workspace_id,items:[]},'list_hash');
 const ctrl={calls:[],mode:'normal',invalid:0,closed:0,plans:[]};
 const request=async(path,options={})=>{ctrl.calls.push({path,method:options.method||'GET'});if(path==='/me')return ctrl.changedIdentity?{...activeAccount,role:'viewer'}:activeAccount;
  if(path==='/setup/understanding/models')return f.models;if(path==='/setup/report-full-readings')return list;
  if(path==='/setup/report-drafts/active')return {blocked:false};if(path==='/setup/report-full-readings/'+row.id)return row;
  if(ctrl.blockAux&&path.includes('/stages/'))throw Object.assign(Error('denied'),{status:403});
  if(path.startsWith('/setup/materials/'+id(3)+'/docx-visible-text-v2?')){if(ctrl.blockComparison)throw Object.assign(Error('denied'),{status:403});const ref=row.preview.source_snapshot.original.ref;const reading=row.preview.source_snapshot.original.reading;return reseal({schema:'docx-visible-text-comparison-v2',actor_id:activeAccount.user_id,workspace_id:activeAccount.workspace_id,material:{...ref},legacy_reading_hash:await digest(reading),visible_reading_hash:'c'.repeat(64),visible_text_policy:'preserve-ooxml-run-hyphens-v2',paragraph_count:1,changes:[{paragraph_index:1,body_index:1,legacy_id:'p1',visible_id:'v2:p1',before:'20℃',after:'‑20℃',nonbreaking_hyphens:1,optional_hyphens:0}],legacy_preserved:true,saved_new_view:false,can_apply:false,can_send:false},'comparison_hash');}
  if(path==='/setup/report-evidence-delegated/preview'){if(ctrl.mode!=='normal')throw Object.assign(Error('denied'),{status:Number(ctrl.mode)});return fixture.reply(JSON.parse(options.body));}
  if(path==='/setup/report-evidence-delegated/budget-plan/find')return {schema:'report-evidence-budget-plan-list-v1',workspace_id:account.workspace_id,actor_id:account.user_id,original_draft_id:row.id,items:ctrl.plans,truncated:false,next_before_preview_id:null};
  if(path==='/setup/report-evidence-delegated/budget-plan/preview'){const command=JSON.parse(options.body),selection=row.preview.body.selection,coverage=row.preview.source_snapshot.coverage,pick=(v,keys)=>Object.fromEntries(keys.map(k=>[k,v[k]]));const item={schema:'report-evidence-budget-plan-v1',preview_id:id(90),workspace_id:account.workspace_id,actor_id:account.user_id,original_draft_id:row.id,original_revision:command.original_revision,request_id:command.reading.request_id,command_hash:await digest({kind:'report_evidence_budget_preview_v2',workspace_id:account.workspace_id,actor_id:account.user_id,body:command}),source_summary_verified:true,source_hash:row.preview.source_hash,selection_hash:await digest(selection),selection:pick(selection,['source_mode','original_ref','copy_ref','provenance_hash','run_id','generation','run_version']),coverage:pick(coverage,['expected_pages','prepared_pages','selected_pages','word_blocks','ocr_regions','word_characters','ocr_characters','versions_count']),model_id:row.preview.body.model_id,model_hash:row.preview.model_hash,max_calls:14,max_tokens:150000,not_before:'2026-11-18T00:00:00Z',expires_at:'2026-11-19T00:00:00Z',status:'saved',historical_second_send:'not_independently_proven',source_relationship:'same_report_word_and_ocr_views_overlap',not_original_batch_recovery:true,duplicate_cost_risk:true,price_limit_available:false,user_confirmed_business:false,allowance_issued:null,budget_reserved:null,model_called:null,execution_history:'unknown',preview_action_calls_model:false,usage_marker:'not_recorded',can_start:false,can_send:false,start_blocker:'安全派发尚未开放'};ctrl.plans=[await reseal(item,'summary_hash')];return ctrl.plans[0];}
  if(path==='/setup/report-evidence-delegated/activation/status')throw Object.assign(Error('denied'),{status:403});
  throw Error('unexpected synthetic request');};
 const dom=new JSDOM('<div id="root"></div>',{url:'https://test.invalid/setup/',pretendToBeVisual:true}),names=['window','document','navigator','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const n of names.slice(0,5))Object.defineProperty(globalThis,n,{configurable:true,writable:true,value:dom.window[n]});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const Btn=({children,primary,...props})=>React.createElement('button',props,children),Dialog=({children})=>React.createElement('div',null,children),root=createRoot(document.getElementById('root'));
 const props={account:activeAccount,material:{id:id(3),name:'合成报告',version:1,hash:f.original_ref.hash},Dialog,Btn,prepareDraft:async()=>1,onClose(){ctrl.closed++;},onSessionInvalid(){ctrl.invalid++;},onRegisterCloseGuard(){},initialDraftID:row.id,request,storage:memory(),tabStorage:tabFor()};
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

test('真实父报告对话框在激活状态查询 403 后遮罩所有旧资料',async()=>env(async({ctrl,waitFor})=>{const clickText=async value=>{const b=[...document.querySelectorAll('button')].find(node=>node.textContent===value);assert.ok(b,value);await act(async()=>{b.click();await flush();});};await clickText('查找已保存预览');await waitFor(()=>document.body.textContent.includes('没有找到已保存预览'));await clickText('保存当前范围的额度预览');await waitFor(()=>document.body.textContent.includes('计划最多 14 次'));await clickText('核对定向启用状态');await waitFor(()=>document.body.textContent.includes('暂不显示私人报告内容'));assert.doesNotMatch(document.body.textContent,/计划最多|Word 正文|OCR|合成报告/);assert.equal(ctrl.invalid,1);},{admin:true}));

for(const label of ['查看抽取1原回答','生成机器待核候选','核对继续读取的条件'])test('辅助入口 '+label+' 权限失败时父报告隐私遮罩',async()=>env(async({ctrl,waitFor})=>{ctrl.blockAux=true;const button=[...document.querySelectorAll('button')].find(node=>node.textContent===label);assert.ok(button,label);await act(async()=>{button.click();await flush();});await waitFor(()=>document.body.textContent.includes('暂不显示私人报告内容'));assert.doesNotMatch(document.body.textContent,/合成报告|私人完整读取候选/);assert.equal(ctrl.invalid,1);}));

test('全文读取父对话框可核对旧版字符差异且不启动模型',async()=>env(async({ctrl,waitFor})=>{
 const button=[...document.querySelectorAll('button')].find(node=>node.textContent==='查看原文字符对照');assert.ok(button);await act(async()=>{button.click();await flush();});await waitFor(()=>document.body.textContent.includes('原件保留特殊字符后的文字'));assert.match(document.body.textContent,/‑20℃/);assert.match(document.body.textContent,/尚未形成可发送的新存档/);assert.equal(ctrl.calls.filter(c=>c.path.includes('/docx-visible-text-v2?')).length,1);assert.equal(ctrl.calls.filter(c=>c.method==='POST').length,0);
}));

for(const scenario of ['403','身份变化'])test('字符对照期间'+scenario+'后明确指引返回，并不再请求或显示原内容',async()=>env(async({ctrl,waitFor})=>{
 const button=[...document.querySelectorAll('button')].find(node=>node.textContent==='查看原文字符对照');assert.ok(button);await act(async()=>{button.click();await flush();});await waitFor(()=>document.body.textContent.includes('原件保留特殊字符后的文字'));
 if(scenario==='403')ctrl.blockComparison=true;else ctrl.changedIdentity=true;
 await act(async()=>{button.click();await flush();});
 await waitFor(()=>document.body.textContent.includes('账号或工作区权限已变化'));assert.match(document.body.textContent,/重新登录/);assert.doesNotMatch(document.body.textContent,/‑20℃|Word 正文|原件保留特殊字符后的文字|正在核对账号与工作区/);assert.equal(ctrl.invalid,1);
 const count=ctrl.calls.length;const exit=[...document.querySelectorAll('button')].find(node=>node.textContent==='返回资料');assert.ok(exit);await act(async()=>{exit.click();await flush();});assert.equal(ctrl.closed,1);assert.equal(ctrl.calls.length,count);
}));
