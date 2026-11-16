import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let folder,compiled;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  folder=await mkdtemp(join(tmpdir(),'graph-evaluation-v335-'));
  const react=pathToFileURL(join(project,'node_modules/react/index.js')).href;
  const bundle=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),mode:'test',logLevel:'silent',
   define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/GraphReasoningEvaluationStatusV335.jsx'),
    write:false,minify:false,rollupOptions:{external:name=>name==='react'||name===react,output:{format:'es',inlineDynamicImports:true,paths:()=>react}}}});
  const file=join(folder,'component.mjs');await writeFile(file,bundle.output.find(row=>row.type==='chunk'&&row.isEntry).code);
  return (await import(pathToFileURL(file).href)).default;
 })();return compiled;
}

async function harness(fn){
 const dom=new JSDOM('<div id="root"></div>',{url:'https://local.invalid/'}),prior=new Map();
 for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,
  HTMLElement:dom.window.HTMLElement,IS_REACT_ACT_ENVIRONMENT:true})){
  prior.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
 }
 const pending=[],denied=[],api=(path,options)=>new Promise((resolve,reject)=>pending.push({path,options,resolve,reject}));
 let root;try{
  const Card=await component(),{createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));
  let props={api,taskId:'task-one',activationId:'reasoning-one',scopeKey:'user:workspace:task-one',onDenied:()=>denied.push(true)};
  const render=async patch=>{props={...props,...patch};await act(async()=>root.render(React.createElement(Card,props)));};
  const settle=async(index,value,fail=false)=>act(async()=>{(fail?pending[index].reject:pending[index].resolve)(value);await Promise.resolve();});
  await render({});await fn({dom,pending,denied,render,settle,text:()=>document.body.textContent,root});
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}
const dto=(status,extra={})=>({schema:'reasoning-evaluation-status-v335',status,activation_id:'reasoning-one',...extra});

test('shows verified source status while preserving task state and makes only a read request',{concurrency:false},async()=>harness(async h=>{
 assert.match(h.text(),/正在核对/);assert.equal(h.pending.length,1);
 assert.equal(h.pending[0].path,'/workspace/tasks/task-one/graph/reasoning-evaluation-status');
 assert.equal(h.pending[0].options.method,undefined);
 await h.settle(0,dto('superseded',{checked_at:'2026-11-03T02:01:00Z',evaluated_at:'2026-11-03T01:00:00Z',current_evaluated_at:'2026-11-03T02:00:00Z',current_freshness:'stale'}));
 assert.match(h.text(),/已有更新的评价/);assert.match(h.text(),/原流程尚未因此继续/);
 assert.match(h.text(),/更新评价也已过期/);assert.match(h.text(),/北京时间/);
 assert.match(h.text(),/核对时间/);
 assert.doesNotMatch(h.text(),/处理完成|已获.*授权/);
}));

test('task and identity changes discard a late previous response',{concurrency:false},async()=>harness(async h=>{
 await h.render({taskId:'task-two',activationId:'reasoning-two',scopeKey:'other:workspace:task-two'});
 assert.equal(h.pending[0].options.signal.aborted,true);assert.equal(h.pending.length,2);
 await h.settle(1,{...dto('current'),activation_id:'reasoning-two'});
 assert.match(h.text(),/仍在有效期内/);
 await h.settle(0,dto('superseded'));assert.doesNotMatch(h.text(),/已有更新的评价/);
}));

test('unknown, denied and older servers remain truthful without executing anything',{concurrency:false},async()=>harness(async h=>{
 await h.settle(0,dto('expired'));assert.match(h.text(),/评价已过期/);
 await h.render({scopeKey:'user:workspace:second'});await h.settle(1,{status:403},true);
 assert.match(h.text(),/当前账号无法查看/);assert.doesNotMatch(h.text(),/评价已过期/);assert.equal(h.denied.length,1);
 await h.render({scopeKey:'user:workspace:third'});await h.settle(2,{status:404},true);
 assert.equal(h.text(),'');assert.equal(h.pending.length,3);
}));

test('malformed data and mismatched activation cannot appear as current',{concurrency:false},async()=>harness(async h=>{
 await h.settle(0,dto('current',{activation_id:'another'}));assert.match(h.text(),/暂时无法核对/);
 assert.ok([...h.dom.window.document.querySelectorAll('button')].some(row=>row.textContent==='重新核对评价资料'));
}));
