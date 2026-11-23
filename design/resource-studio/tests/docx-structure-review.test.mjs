import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

const require = createRequire(import.meta.url), React = require('react'), {act} = React;
const wait = () => new Promise(resolve => setTimeout(resolve, 0));
async function component() {
  const result = await build({entryPoints:['src/DocxStructureReview.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'});
  const mod = {exports:{}};
  new Function('require','module','exports',result.outputFiles[0].text)(require,mod,mod.exports);
  return mod.exports.default;
}
function setup() {
  const dom = new JSDOM('<div id="root"></div>', {url:'http://localhost/setup/'});
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true});
  Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
  return dom;
}
function teardown(dom) { dom.window.close(); for (const key of ['window','document','navigator','IS_REACT_ACT_ENVIRONMENT']) delete globalThis[key]; }
const account = {user_id:'reader',workspace_id:'workspace',role:'viewer'};
const candidate = (kind,text,n=1) => ({kind,text,review_required:true,location:{scheme:'docx-audit-v1',part:'word/document.xml',body_index:n,child_path:[1,2]}});
const response = (candidates=[]) => ({schema:'docx-structure-audit-v1',status:'review_required',counts:{top_level_directories:1,textboxes_all_branches:1,alternate_content:1},issues:[{code:'directory',message:'目录内容需要核对。'}],candidates});
async function click(element) { await act(async()=>{element.click();await wait();}); }

test('特殊连字符不被静默隐藏并提醒参照原件',async()=>{
  const dom=setup(),Component=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    await act(async()=>root.render(React.createElement(Component,{account,materialID:'doc-1',requestJSON:async()=>response([candidate('textbox','\u201120\u00ad50')])})));
    await click(document.querySelector('summary'));
    const text=document.body.textContent;
    assert.match(text,/‑20［可选连字符］50/);
    assert.match(text,/可能是负号或词内连字符/);
    assert.match(text,/不代表排版时一定显示/);
  } finally { await act(async()=>root.unmount());teardown(dom); }
});

test('仅展开时只读加载，区分候选与原文事实，并显示可定位来源',async()=>{
  const dom=setup(),Component=await component(),root=require('react-dom/client').createRoot(document.getElementById('root')),calls=[];
  const requestJSON=async(path,opts)=>{calls.push({path,opts});return response([candidate('directory_entry','示例目录'),candidate('textbox','示例文本框',3)]);};
  try {
    await act(async()=>root.render(React.createElement(Component,{account,materialID:'doc-1',requestJSON})));
    assert.equal(calls.length,0);
    await click(document.querySelector('summary'));
    assert.equal(calls.length,1);assert.equal(calls[0].path,'/setup/materials/doc-1/docx-structure?include_candidates=true');
    assert.ok(!calls[0].opts.method||calls[0].opts.method==='GET');
    const text=document.body.textContent;
    assert.match(text,/示例目录/);assert.match(text,/示例文本框/);assert.match(text,/待核/);
    assert.match(text,/第 3 项/);assert.match(text,/非页码/);assert.match(text,/目录内容需要核对/);
    assert.doesNotMatch(text,/已确认|已采纳/);
  } finally { await act(async()=>root.unmount());teardown(dom); }
});

test('失败明确提示并可只读重试，不显示服务错误原文',async()=>{
  const dom=setup(),Component=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));let attempts=0;
  const requestJSON=async()=>{if(++attempts===1)throw Error('internal details');return response([]);};
  try {
    await act(async()=>root.render(React.createElement(Component,{account,materialID:'doc-2',requestJSON})));
    await click(document.querySelector('summary'));assert.match(document.body.textContent,/未能读取/);assert.doesNotMatch(document.body.textContent,/internal details/);
    await click([...document.querySelectorAll('button')].find(x=>x.textContent==='重试读取'));
    assert.equal(attempts,2);assert.match(document.body.textContent,/没有检出候选/);
  } finally { await act(async()=>root.unmount());teardown(dom); }
});

test('切换账号或材料后不显示旧请求的迟到原文',async()=>{
  const dom=setup(),Component=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));let finish;
  const requestJSON=()=>new Promise(resolve=>{finish=resolve;});
  try {
    await act(async()=>root.render(React.createElement(Component,{account,materialID:'doc-3',requestJSON})));
    await click(document.querySelector('summary'));
    await act(async()=>root.render(React.createElement(Component,{account:{...account,workspace_id:'other'},materialID:'doc-4',requestJSON})));
    await act(async()=>{finish(response([candidate('textbox','旧工作区私有内容')]));await wait();});
    assert.doesNotMatch(document.body.textContent,/旧工作区私有内容/);
  } finally { await act(async()=>root.unmount());teardown(dom); }
});

test('权限角色变化后隐藏原内容并要求重新读取',async()=>{
  const dom=setup(),Component=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    await act(async()=>root.render(React.createElement(Component,{account:{...account,role:'admin'},materialID:'doc-4',requestJSON:async()=>response([candidate('textbox','先前权限下的候选')])})));
    await click(document.querySelector('summary'));assert.match(document.body.textContent,/先前权限下的候选/);
    await act(async()=>root.render(React.createElement(Component,{account,materialID:'doc-4',requestJSON:async()=>response([])})));
    assert.doesNotMatch(document.body.textContent,/先前权限下的候选/);
    assert.equal(document.querySelector('details').open,false);
  } finally { await act(async()=>root.unmount());teardown(dom); }
});

test('候选分页不遗漏且限制初次渲染量',async()=>{
  const dom=setup(),Component=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));
  const rows=Array.from({length:21},(_,i)=>candidate('textbox','候选内容 '+(i+1),i+1));
  try {
    await act(async()=>root.render(React.createElement(Component,{account,materialID:'doc-5',requestJSON:async()=>response(rows)})));
    await click(document.querySelector('summary'));assert.match(document.body.textContent,/候选内容 20/);assert.doesNotMatch(document.body.textContent,/候选内容 21/);
    await click([...document.querySelectorAll('button')].find(x=>x.textContent==='显示更多候选'));
    assert.match(document.body.textContent,/候选内容 21/);
  } finally { await act(async()=>root.unmount());teardown(dom); }
});

test('StrictMode 挂载后仍能查看，收起时丢弃迟到内容',async()=>{
  const dom=setup(),Component=await component(),root=require('react-dom/client').createRoot(document.getElementById('root'));let finish;
  const requestJSON=()=>new Promise(resolve=>{finish=resolve;});
  try {
    await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Component,{account,materialID:'doc-6',requestJSON}))));
    await click(document.querySelector('summary'));
    assert.match(document.body.textContent,/正在读取/);
    await act(async()=>{finish(response([candidate('textbox','可供核对的候选')]));await wait();});
    assert.match(document.body.textContent,/可供核对的候选/);
    await click(document.querySelector('summary'));
    await click(document.querySelector('summary'));
    await click(document.querySelector('summary'));
    await act(async()=>{finish(response([candidate('textbox','不再显示的原文')]));await wait();});
    assert.doesNotMatch(document.body.textContent,/不再显示的原文/);
  } finally { await act(async()=>root.unmount());teardown(dom); }
});
