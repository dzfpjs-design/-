import test from 'node:test';
import assert from 'node:assert/strict';
import React, {act} from 'react';
import {JSDOM} from 'jsdom';
import {build} from 'vite';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {fixture,id,account,memory,tabFor,reseal,failedSavedDraft} from './report-full-reading-fixture.mjs';

let Panel,createRoot,tmp;
const flush=async()=>{await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};

test.before(async()=>{
  tmp=await mkdtemp(join(tmpdir(),'full-raw-dialog-'));
  const prior=globalThis.sessionStorage;globalThis.sessionStorage=tabFor();
  try{
    const built=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',
      build:{ssr:resolve('src/ReportFullReadingDialog.jsx'),write:false,minify:false,
        rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:x=>x==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:x}}}});
    const file=join(tmp,'dialog.mjs');await writeFile(file,built.output.find(x=>x.isEntry).code);
    Panel=(await import(pathToFileURL(file))).ReportFullReadingPanel;
    process.env.NODE_ENV='development';({createRoot}=await import('react-dom/client'));
  }finally{if(prior===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=prior;}
});
test.after(async()=>{await rm(tmp,{recursive:true,force:true});});

async function environment(run){
  const f=await fixture(),row=await failedSavedDraft(f),cache=memory(),tab=tabFor(),calls=[];
  const control={mode:'normal',invalid:0,pending:null};
  const dom=new JSDOM('<div id="root"></div>',{url:'https://test.invalid/setup/',pretendToBeVisual:true});
  const names=['window','document','navigator','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
  const old=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
  for(const name of names.slice(0,5))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value:dom.window[name]});
  globalThis.IS_REACT_ACT_ENVIRONMENT=true;
  const list=await reseal({schema:'report-full-reading-list-v1',actor_id:account.user_id,workspace_id:account.workspace_id,items:[]},'list_hash');
  const raw={schema:'report-full-raw-view-v1',scope:{workspace_id:account.workspace_id,actor_id:account.user_id,
    draft_id:row.id,revision:row.revision,stage:'extract-1',preview_hash:row.preview.preview_hash,raw_hash:'c'.repeat(64)},
    status:'available',content:'合成私有原答',content_encoding:'plain',finish_reason:'length',response_body_status:'complete',
    can_apply:false,can_publish:false};
  const request=async(path,options={})=>{
    calls.push({path,method:options.method||'GET'});
    if(path==='/me')return control.mode==='role_changed'?{...account,role:'viewer'}:account;
    if(path==='/setup/understanding/models')return f.models;
    if(path==='/setup/report-full-readings')return list;
    if(path==='/setup/report-drafts/active')return {blocked:false};
    if(path==='/setup/report-full-readings/'+row.id)return row;
    if(path.includes('/stages/extract-1/raw?')){
      if(control.mode==='expired')throw Object.assign(Error('synthetic expired'),{status:401});
      if(control.mode==='delayed'){control.pending=deferred();return control.pending.promise;}
      return raw;
    }
    throw Error('unexpected synthetic request');
  };
  const Btn=({children,primary,...props})=>React.createElement('button',props,children);
  const Dialog=({children})=>React.createElement('div',null,children);
  const props={account,material:{id:id(3),name:'synthetic.docx',version:1,hash:f.original_ref.hash},
    Dialog,Btn,prepareDraft:async()=>1,onClose(){},onSessionInvalid(){control.invalid++;},
    onRegisterCloseGuard(){},initialDraftID:row.id,request,storage:cache,tabStorage:tab,
    locks:{request:async(_key,_options,operation)=>operation()}};
  const root=createRoot(document.getElementById('root'));
  async function render(value=props){await act(async()=>{root.render(React.createElement(Panel,value));});for(let i=0;i<8;i++)await act(async()=>{await flush();});}
  async function click(label){const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes(label));assert.ok(b,'button '+label);await act(async()=>{b.click();await flush();});}
  try{await render();await run({props,root,render,click,control,calls,raw});}
  finally{await act(async()=>root.unmount());dom.window.close();for(const name of names){if(old[name])Object.defineProperty(globalThis,name,old[name]);else delete globalThis[name];}}
}

test('真实对话框中原答已显示后会话失效会隐藏全部私人内容',async()=>environment(async({click,control,calls})=>{
  await click('查看抽取1原回答');assert.match(document.body.textContent,/合成私有原答/);
  control.mode='expired';await click('重新读取抽取1原回答');
  assert.match(document.body.textContent,/暂不显示私人报告内容/);
  assert.doesNotMatch(document.body.textContent,/合成私有原答/);assert.equal(control.invalid,1);
  assert.equal(calls.filter(c=>c.method!=='GET').length,0);
}));

test('真实对话框中切换角色立即隐藏旧原答并丢弃迟到响应',async()=>environment(async({props,render,click,control,calls,raw})=>{
  await click('查看抽取1原回答');assert.match(document.body.textContent,/合成私有原答/);
  control.mode='delayed';await click('重新读取抽取1原回答');assert.ok(control.pending);
  await render({...props,account:{...account,role:'viewer'}});
  assert.match(document.body.textContent,/暂不显示私人报告内容/);assert.doesNotMatch(document.body.textContent,/合成私有原答/);
  await act(async()=>{control.pending.resolve(raw);await flush();});
  assert.doesNotMatch(document.body.textContent,/合成私有原答/);
  assert.equal(calls.filter(c=>c.method!=='GET').length,0);
}));

test('服务器返回角色已变化时隐藏旧原答并且不再发送读取请求',async()=>environment(async({click,control,calls})=>{
  await click('查看抽取1原回答');assert.match(document.body.textContent,/合成私有原答/);
  const before=calls.filter(c=>c.path.includes('/stages/extract-1/raw?')).length;
  control.mode='role_changed';await click('重新读取抽取1原回答');
  assert.match(document.body.textContent,/暂不显示私人报告内容/);
  assert.doesNotMatch(document.body.textContent,/合成私有原答/);
  assert.equal(control.invalid,1);
  assert.equal(calls.filter(c=>c.path.includes('/stages/extract-1/raw?')).length,before);
}));
