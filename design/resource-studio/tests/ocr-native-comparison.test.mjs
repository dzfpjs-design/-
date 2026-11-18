import test from 'node:test';
import assert from 'node:assert/strict';
import React, {act} from 'react';
import {JSDOM} from 'jsdom';
import {build} from 'vite';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

let Component, createRoot, tmp;
const hash = 'a'.repeat(64), rawHash = 'b'.repeat(64);
const actor = {user_id:'u1', workspace_id:'w1', role:'operator'};
const materialID = 'material-1';
const detail = {material_id:materialID, material_version:3, run_id:'run-1', generation:1,
  unit:{id:'unit-1',version:2,kind:'pdf_page',page:1}, raw:{id:'raw-1',hash:rawHash}};
const Btn = ({children,...props}) => React.createElement('button',props,children);
const props = (extra={}) => ({account:actor, materialID, materialHash:hash, detail, Btn, ...extra});
const reply = (extra={}) => ({schema:'ocr-native-comparison-v1', status:'different',
  scope:{material_id:materialID,material_version:3,source_sha256:hash,run_id:'run-1',generation:1,
    unit_id:'unit-1',unit_version:2,page:1,raw_revision_id:'raw-1',raw_revision_hash:rawHash},
  native_text:'合成 PDF 文字',ocr_text:'合成机器文字', ...extra});
const flush = async () => {await Promise.resolve(); await new Promise(resolve => setTimeout(resolve,0));};
const deferred = () => {let resolve; const promise = new Promise(r => {resolve=r;}); return {promise,resolve};};

test.before(async () => {
  tmp=await mkdtemp(join(tmpdir(),'ocr-comparison-test-'));
  const entry=fileURLToPath(new URL('../src/OCRNativeComparison.jsx',import.meta.url));
  const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',
    build:{ssr:entry,write:false,minify:false,rollupOptions:{external:['react'],
      output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?new URL('../node_modules/react/index.js',import.meta.url).href:id}}}});
  const modulePath=join(tmp,'component.mjs');await writeFile(modulePath,out.output.find(x=>x.isEntry).code);
  Component=(await import(pathToFileURL(modulePath))).default;
  ({createRoot}=await import('react-dom/client'));
});
test.after(async()=>{await rm(tmp,{recursive:true,force:true});});

async function withDOM(run) {
  const dom=new JSDOM('<div id="app"></div>',{url:'http://localhost/'}), names=['window','document','navigator','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
  const before=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
  for(const name of names.slice(0,5)) Object.defineProperty(globalThis,name,{configurable:true,writable:true,value:dom.window[name]});
  globalThis.IS_REACT_ACT_ENVIRONMENT=true;
  const root=createRoot(document.getElementById('app'));
  const render=async p=>{await act(async()=>{root.render(React.createElement(Component,p));await flush();});};
  const click=async label=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes(label));assert.ok(b);await act(async()=>{b.click();await flush();});};
  try {await run({render,click,root,dom});} finally {
    await act(async()=>root.unmount());dom.window.close();
    for(const name of names){if(before[name])Object.defineProperty(globalThis,name,before[name]);else delete globalThis[name];}
  }
}

test('按需显示原文和原始识别，对照不写入存储也不确认',async()=>withDOM(async({render,click})=>{
  const calls=[];const request=async (path,options)=>{calls.push({path,options});return reply();};
  await render(props({request}));assert.equal(calls.length,0);
  assert.match(document.body.textContent,/不会修改或确认/);
  await click('读取本页文字对照');assert.equal(calls.length,1);
  assert.match(calls[0].path,/native-comparison\?run_id=run-1$/);
  assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.headers['X-Workspace-ID'],'w1');
  assert.match(document.body.textContent,/合成 PDF 文字/);assert.match(document.body.textContent,/合成机器文字/);
  assert.match(document.body.textContent,/存在差异/);assert.equal(localStorage.length,0);assert.equal(sessionStorage.length,0);
}));

test('空白差异匹配并不宣称内容正确，无法比较时不给空结果冒充成功',async()=>withDOM(async({render,click})=>{
  let status='no_difference';const request=async()=>status==='no_difference'?reply({status}):reply({status,native_text:null,ocr_text:null});
  await render(props({request}));await click('读取本页文字对照');
  assert.match(document.body.textContent,/仍需对照原页面核查/);
  status='too_large';await click('重新读取本页文字');
  assert.match(document.body.textContent,/超出对照处理范围/);assert.doesNotMatch(document.body.textContent,/合成 PDF 文字/);
}));

test('读取失败保留明确提示并支持重试',async()=>withDOM(async({render,click})=>{
  let count=0;const request=async()=>{if(++count===1)throw Error('synthetic internal detail');return reply();};
  await render(props({request}));await click('读取本页文字对照');
  assert.match(document.body.textContent,/暂时无法读取/);assert.doesNotMatch(document.body.textContent,/synthetic internal detail/);
  await click('重试读取本页文字');assert.equal(count,2);assert.match(document.body.textContent,/合成机器文字/);
}));

test('错误的材料、页面、版本或过大文本均不展示',async()=>withDOM(async({render,click})=>{
  let result=reply({scope:{...reply().scope,raw_revision_hash:'c'.repeat(64)}});const request=async()=>result;
  await render(props({request}));await click('读取本页文字对照');assert.match(document.body.textContent,/范围或结果发生变化/);assert.doesNotMatch(document.body.textContent,/合成 PDF 文字/);
  result=reply({native_text:'x'.repeat(20001)});await click('重试读取本页文字');
  assert.match(document.body.textContent,/范围或结果发生变化/);assert.doesNotMatch(document.body.textContent,/合成机器文字/);
}));

test('切页或切换账号和角色立即隐藏旧结果，迟到响应不能覆盖',async()=>withDOM(async({render,click})=>{
  const pending=deferred();let call=0;const request=async()=>++call===1?reply():pending.promise;
  await render(props({request}));await click('读取本页文字对照');assert.match(document.body.textContent,/合成 PDF 文字/);
  const changed={...detail,unit:{...detail.unit,id:'unit-2',page:2},raw:{id:'raw-2',hash:rawHash}};
  await render(props({request,detail:changed}));assert.doesNotMatch(document.body.textContent,/合成 PDF 文字/);
  await click('读取本页文字对照');await render(props({request,detail:changed,account:{...actor,role:'viewer'}}));
  await act(async()=>{pending.resolve(reply());await flush();});
  assert.doesNotMatch(document.body.textContent,/合成 PDF 文字|合成机器文字/);
  await render(props({request,detail:changed,account:{...actor,user_id:'u2',workspace_id:'w2',role:'viewer'}}));
  assert.doesNotMatch(document.body.textContent,/合成 PDF 文字|合成机器文字/);
  await render(props({request}));assert.doesNotMatch(document.body.textContent,/合成 PDF 文字|合成机器文字/);
}));
