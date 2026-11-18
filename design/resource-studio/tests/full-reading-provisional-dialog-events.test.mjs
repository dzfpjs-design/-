import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {build} from 'vite';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {digest} from '../src/report-full-reading-contract.mjs';
import {fixture,id,account,memory,tabFor,reseal,failedSavedDraft,localRecoveryReply,candidate} from './report-full-reading-fixture.mjs';

let Panel,createRoot,tmp;
const flush=async()=>{await Promise.resolve();await new Promise(r=>setTimeout(r,0));};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const missing=()=>Object.assign(Error('synthetic not found'),{status:404});
const rawHash='c'.repeat(64),algorithm='report-failed-candidate-quarantine-v1';
const clone=x=>structuredClone(x);
const cite={source_ref:'o:p1',quote:'登记员登记后交核对员核对。'};

test.before(async()=>{
 tmp=await mkdtemp(join(tmpdir(),'full-provisional-parent-'));const prior=globalThis.sessionStorage;globalThis.sessionStorage=tabFor();
 try{const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',build:{ssr:resolve('src/ReportFullReadingDialog.jsx'),write:false,minify:false,
  rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}});
  const file=join(tmp,'dialog.mjs');await writeFile(file,out.output.find(x=>x.isEntry).code);Panel=(await import(pathToFileURL(file))).ReportFullReadingPanel;
  process.env.NODE_ENV='development';({createRoot}=await import('react-dom/client'));
 }finally{if(prior===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=prior;}
});
test.after(async()=>{await rm(tmp,{recursive:true,force:true});});

async function environment(run){
 const f=await fixture(),initial=await failedSavedDraft(f);
 initial.error_code='full_candidate_invalid';initial.extractions[0].error_code='full_candidate_invalid';await reseal(initial,'view_hash');
 const selected=candidate();selected.objects=[{name:'合成测试对象',fields:[{label:'合成字段',kind:'数值',required:false,unit:null,citations:[cite],inference_reason:''}],citations:[cite],inference_reason:''}];
 const quarantine=[{kind:'unit',path:'/objects/0/fields/0/unit',reason:'unit_evidence_not_established',original_value:'合成待核单位',citations:[cite]}];
 const makePreview=async(row)=>reseal({schema:'report-full-failed-candidate-preview-v1',scope:{workspace_id:account.workspace_id,actor_id:account.user_id,
  draft_id:row.id,revision:row.revision,stage:'extract-1',preview_hash:row.preview.preview_hash,raw_hash:rawHash},algorithm_version:algorithm,
  candidate:clone(selected),quarantine:clone(quarantine),status:'needs_review',confirmed:false,can_apply:false,can_publish:false},'view_hash');
 const control={row:initial,record:null,posts:[],mode:'normal',invalid:0,pending:null},calls=[];
 const list=await reseal({schema:'report-full-reading-list-v1',actor_id:account.user_id,workspace_id:account.workspace_id,items:[]},'list_hash');
 const commit=async body=>{
  const scope={workspace_id:account.workspace_id,actor_id:account.user_id,draft_id:control.row.id,stage:'extract-1'};
  const receipt=await reseal({schema:'report-full-provisional-receipt-v1',...scope,request_id:body.request_id,draft_revision:body.revision,
   source_hash:body.source_hash,raw_hash:body.raw_hash,preview_hash:control.row.preview.preview_hash,view_hash:body.view_hash,algorithm_version:body.algorithm_version,
   candidate_hash:await digest(selected),quarantine_hash:await digest(quarantine),command_hash:await digest({kind:'report_full_provisional_save',...scope,body})},'receipt_hash');
  return {schema:'report-full-provisional-v1',id:id(71),candidate:clone(selected),quarantine:clone(quarantine),receipt,status:'needs_review',confirmed:false,can_apply:false,can_publish:false};
 };
 const request=async(path,options={})=>{
  calls.push({path,method:options.method||'GET'});
  if(path==='/me')return account;
  if(path==='/setup/understanding/models')return f.models;
  if(path==='/setup/report-full-readings')return list;
  if(path==='/setup/report-drafts/active')return {blocked:false};
  if(path==='/setup/report-full-readings/'+control.row.id)return control.row;
  if(path.includes('/failed-candidate?')){
   if(control.mode==='denied401'||control.mode==='denied403')throw Object.assign(Error('synthetic denied'),{status:control.mode==='denied401'?401:403});
   if(control.mode==='deferred'){control.pending=deferred();return control.pending.promise;}
   return makePreview(control.row);
  }
  if(path.endsWith('/stages/extract-1/provisional') && options.method==='POST'){
   const body=JSON.parse(options.body);control.posts.push(body);control.record=control.record||await commit(body);return control.record;
  }
  if(path.includes('/provisional-receipts/')){if(!control.record)throw missing();return control.record;}
  if(path.endsWith('/stages/extract-1/provisional'))return {schema:'report-full-provisional-list-v1',workspace_id:account.workspace_id,actor_id:account.user_id,
   draft_id:control.row.id,stage:'extract-1',items:control.record?[{id:control.record.id,receipt:clone(control.record.receipt),status:'needs_review',confirmed:false}]:[]};
  throw Error('unexpected synthetic request');
 };
 const dom=new JSDOM('<div id="root"></div>',{url:'https://test.invalid/setup/',pretendToBeVisual:true});
 const names=['window','document','navigator','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'],old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 const tails=new Map();Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request(name,_opts,fn){const prev=tails.get(name)||Promise.resolve(),next=prev.then(fn);tails.set(name,next.catch(()=>{}));return next;}}});
 for(const name of names.slice(0,5))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value:dom.window[name]});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const Btn=({children,primary,...props})=>React.createElement('button',props,children),Dialog=({children})=>React.createElement('div',null,children);
 let root=createRoot(document.getElementById('root'));const storage=memory();
 const props={account,material:{id:id(3),name:'synthetic.docx',version:1,hash:f.original_ref.hash},Dialog,Btn,
  prepareDraft:async()=>1,onClose(){},onSessionInvalid(){control.invalid++;},onRegisterCloseGuard(){},initialDraftID:initial.id,
  request,storage,tabStorage:tabFor(),locks:dom.window.navigator.locks};
 const render=async(value=props)=>{await act(async()=>{root.render(React.createElement(Panel,value));});for(let i=0;i<8;i++)await act(async()=>{await flush();});};
 const click=async label=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes(label));assert.ok(b,'missing '+label);assert.equal(b.disabled,false);await act(async()=>{b.click();await flush();});};
 const waitFor=async f=>{for(let i=0;i<100;i++){if(f())return;await act(async()=>{await flush();});}assert.ok(f(),'等待合成组件状态超时');};
 const remount=async(value=props)=>{await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render(value);};
 try{await render();await run({props,render,click,waitFor,remount,control,calls,makePreview,storage});}
 finally{await act(async()=>root.unmount());dom.window.close();for(const name of names){if(old[name])Object.defineProperty(globalThis,name,old[name]);else delete globalThis[name];}}
}

test('实际父对话框：失败批次预览并保存，推进版本后新会话仍发现原保存稿',async()=>environment(async({click,waitFor,remount,control,props,storage,calls})=>{
 await click('生成机器待核候选');await waitFor(()=>document.body.textContent.includes('合成待核单位'));
 assert.match(document.body.textContent,/尚未人工确认|未人工确认/);await click('保存此机器候选');
 await waitFor(()=>document.body.textContent.includes('机器候选已在服务器保存'));
 assert.equal(control.posts.length,1);const original=clone(control.record.receipt);
 const progressed=await localRecoveryReply(control.row,{revision:control.row.revision,request_id:id(72)});control.row=progressed.draft;
 localStorage.clear();await remount({...props,storage:memory(),tabStorage:tabFor()});
 assert.match(document.body.textContent,/抽取已保存，等待另行核对并授权合并/);
 await click('查找已保存机器候选');await waitFor(()=>document.body.textContent.includes('原草案第 '+original.draft_revision+' 版'));
 await click('查看已保存候选');await waitFor(()=>document.body.textContent.includes('合成测试对象'));
 assert.match(document.body.textContent,/尚未人工确认/);assert.equal(control.posts.length,1);
 assert.deepEqual(control.record.receipt,original);assert.equal(calls.filter(x=>x.method==='POST').length,1);
}));

for(const status of [401,403])test('实际父对话框：候选读取 '+status+' 后隐藏私人内容',async()=>environment(async({click,waitFor,control,calls})=>{
 await click('生成机器待核候选');await waitFor(()=>document.body.textContent.includes('合成待核单位'));
 control.mode='denied'+status;await click('重新读取机器待核候选');await waitFor(()=>document.body.textContent.includes('暂不显示私人报告内容'));
 assert.doesNotMatch(document.body.textContent,/合成测试对象|合成待核单位/);assert.equal(control.invalid,1);
 assert.equal(calls.filter(x=>x.method==='POST').length,0);
}));

test('实际父对话框：角色切换后迟到候选响应被丢弃',async()=>environment(async({props,render,click,control,calls,makePreview})=>{
 control.mode='deferred';await click('生成机器待核候选');assert.ok(control.pending);
 const stale=await makePreview(control.row);
 await render({...props,account:{...account,role:'viewer'}});
 assert.match(document.body.textContent,/暂不显示私人报告内容/);
 await act(async()=>{control.pending.resolve(stale);await flush();});
 assert.doesNotMatch(document.body.textContent,/合成测试对象|合成待核单位/);
 assert.equal(calls.filter(x=>x.method==='POST').length,0);
}));
