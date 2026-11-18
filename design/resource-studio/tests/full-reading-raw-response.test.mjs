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
const actor = {user_id:'actor-1', workspace_id:'workspace-1', role:'operator'};
const draft = {id:'draft-1',revision:2,preview:{preview_hash:'a'.repeat(64)},
  merge:{preview:{preview_hash:'b'.repeat(64)}}};
const Btn = ({children,...props}) => React.createElement('button',props,children);
const props = (extra={}) => ({account:actor,row:draft,stage:'extract-1',Btn,...extra});
const reply = (extra={}) => ({schema:'report-full-raw-view-v1',status:'available',
  scope:{workspace_id:actor.workspace_id,actor_id:actor.user_id,draft_id:draft.id,revision:2,
    stage:'extract-1',preview_hash:'a'.repeat(64),raw_hash:'c'.repeat(64)},
  content:'合成原答 <img src=x onerror=alert(1)>',content_encoding:'plain',finish_reason:'length',
  response_body_status:'complete',can_apply:false,can_publish:false,...extra});
const flush = async () => {await Promise.resolve(); await new Promise(resolve => setTimeout(resolve,0));};
const deferred = () => {let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};

test.before(async()=>{
  tmp=await mkdtemp(join(tmpdir(),'full-reading-raw-test-'));
  const entry=fileURLToPath(new URL('../src/FullReadingRawResponse.jsx',import.meta.url));
  const out=await build({configFile:false,envDir:tmp,cacheDir:join(tmp,'cache'),logLevel:'silent',
    build:{ssr:entry,write:false,minify:false,rollupOptions:{external:['react'],
      output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?new URL('../node_modules/react/index.js',import.meta.url).href:id}}}});
  const modulePath=join(tmp,'component.mjs');await writeFile(modulePath,out.output.find(x=>x.isEntry).code);
  Component=(await import(pathToFileURL(modulePath))).default;
  process.env.NODE_ENV='development';
  ({createRoot}=await import('react-dom/client'));
});
test.after(async()=>{await rm(tmp,{recursive:true,force:true});});

async function withDOM(run){
  const dom=new JSDOM('<div id="app"></div>',{url:'http://localhost/'}),names=['window','document','navigator','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
  const before=Object.fromEntries(names.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
  for(const name of names.slice(0,5))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value:dom.window[name]});
  globalThis.IS_REACT_ACT_ENVIRONMENT=true;const root=createRoot(document.getElementById('app'));
  const render=async p=>{await act(async()=>{root.render(React.createElement(Component,p));await flush();});};
  const click=async label=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes(label));assert.ok(b);await act(async()=>{b.click();await flush();});};
  try{await run({render,click});}finally{await act(async()=>root.unmount());dom.window.close();for(const name of names){if(before[name])Object.defineProperty(globalThis,name,before[name]);else delete globalThis[name];}}
}

test('按需只读并区分截断，原答只作文本显示且不持久化',async()=>withDOM(async({render,click})=>{
  const calls=[];const request=async(path,options)=>{calls.push({path,options});return reply();};
  await render(props({request}));assert.equal(calls.length,0);
  await click('查看抽取1原回答');assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/setup/report-full-readings/draft-1/stages/extract-1/raw?revision=2');
  assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.headers['X-Workspace-ID'],'workspace-1');
  assert.match(document.body.textContent,/可能被截断/);assert.match(document.body.textContent,/合成原答/);
  assert.equal(document.querySelector('img'),null);assert.equal(localStorage.length,0);assert.equal(sessionStorage.length,0);
}));

test('不存在、不可展示及缺少结束证明分别说明且不猜测费用',async()=>withDOM(async({render,click})=>{
  let result=reply({status:'not_saved',scope:{...reply().scope,raw_hash:null},content:null,content_encoding:null,finish_reason:null,response_body_status:null});
  await render(props({request:async()=>result}));await click('查看抽取1原回答');
  assert.match(document.body.textContent,/不能说明请求未发送或未计费/);
  result=reply({status:'unavailable',content:null,content_encoding:null,finish_reason:null,response_body_status:'oversize'});
  await click('重新读取抽取1原回答');assert.match(document.body.textContent,/完整原答未能保存/);
  result=reply({status:'unavailable',content:null,content_encoding:null,finish_reason:'stop',response_body_status:'complete'});
  await click('重新读取抽取1原回答');assert.match(document.body.textContent,/暂无法安全展示，原记录保留/);
  assert.doesNotMatch(document.body.textContent,/完整原答未能保存|模型报告正常结束/);
  result=reply({finish_reason:null});await click('重新读取抽取1原回答');
  assert.match(document.body.textContent,/缺少可核对的结束原因/);assert.doesNotMatch(document.body.textContent,/原答未能保存/);
}));

test('来源、版本与响应格式不一致不能显示内容，读取失败可重试',async()=>withDOM(async({render,click})=>{
  let count=0;const request=async()=>{if(++count===1)throw Error('synthetic private internal');return reply({scope:{...reply().scope,revision:3}});};
  await render(props({request}));await click('查看抽取1原回答');
  assert.match(document.body.textContent,/暂时无法读取/);assert.doesNotMatch(document.body.textContent,/synthetic private internal/);
  await click('重试读取抽取1原回答');assert.match(document.body.textContent,/查看范围已变化/);
  assert.doesNotMatch(document.body.textContent,/合成原答/);
}));

test('阶段、版本、账号与权限变化同步隐藏，迟到结果不显示',async()=>withDOM(async({render,click})=>{
  const pending=deferred();let call=0;const request=async()=>++call===1?reply():pending.promise;
  await render(props({request}));await click('查看抽取1原回答');assert.match(document.body.textContent,/合成原答/);
  await render(props({request,row:{...draft,revision:3}}));assert.doesNotMatch(document.body.textContent,/合成原答/);
  await click('查看抽取1原回答');await render(props({request,row:{...draft,revision:3},stage:'merge'}));
  await act(async()=>{pending.resolve(reply());await flush();});assert.doesNotMatch(document.body.textContent,/合成原答/);
  await render(props({request,account:{...actor,role:'viewer'}}));assert.doesNotMatch(document.body.textContent,/合成原答/);
  await render(props({request,account:{...actor,user_id:'actor-2',workspace_id:'workspace-2'}}));assert.doesNotMatch(document.body.textContent,/合成原答/);
  await render(props({request}));assert.doesNotMatch(document.body.textContent,/合成原答/);
}));

test('合并阶段使用独立预览绑定及安全结束说明',async()=>withDOM(async({render,click})=>{
  const result=reply({scope:{...reply().scope,stage:'merge',preview_hash:'b'.repeat(64)},finish_reason:'stop'});
  await render(props({stage:'merge',request:async()=>result}));await click('查看合并原回答');
  assert.match(document.body.textContent,/不能证明内容准确或没有遗漏/);
}));

test('对编码原答给予明确说明，不解除任何确认或发布约束',async()=>withDOM(async({render,click})=>{
  let result=reply({content:'"\\u0000"',content_encoding:'json-ascii-string-v1',finish_reason:'stop'});
  await render(props({request:async()=>result}));await click('查看抽取1原回答');
  assert.match(document.body.textContent,/以转义字符串形式保留/);
  result=reply({can_apply:true});await click('重新读取抽取1原回答');
  assert.match(document.body.textContent,/查看范围已变化/);assert.doesNotMatch(document.body.textContent,/合成原答/);
}));
