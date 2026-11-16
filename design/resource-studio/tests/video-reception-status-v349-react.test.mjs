// Real task component, synthetic transport. This is not a database test.
import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';import {fileURLToPath,pathToFileURL} from 'node:url';
import React,{act} from 'react';import {JSDOM} from 'jsdom';import {build} from 'vite';
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const task=(id,run)=>({id,perception_event:{run_id:run,frame_id:'frame',video_alert_id:'alert',from_seconds:1,to_seconds:3,label:'cat',track_ref:'t1'}});
const response=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
const state=value=>({schema:'video-candidate-data-reception-v349',state:value,data_reception_verified:value==='received',observation_id:value==='received'?'saved':null});
test('real task evidence handles all reception states and stale replies',async()=>{
 const temp=await mkdtemp(join(tmpdir(),'video-reception-ui-'));const dom=new JSDOM('<main id="root"></main>',{url:'http://localhost/'});let root;
 const keys=['window','document','navigator','HTMLElement','sessionStorage','localStorage','fetch','IS_REACT_ACT_ENVIRONMENT'];const old=Object.fromEntries(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 const calls=[];let mode='received',releaseOld;const oldReply=new Promise(done=>{releaseOld=done;});
 const fetch=async path=>{calls.push(String(path));if(String(path).includes('/videos/'))return new Response(JSON.stringify({detail:'测试中没有读取视频原件'}),{status:409,headers:{'Content-Type':'application/json'}});
  if(String(path).includes('/workspace/tasks/old/'))return oldReply;if(mode==='offline')throw Error('offline');if(mode==='bad')return response({...state('received'),data_reception_verified:false});return response(state(mode));};
 try{
  for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,sessionStorage:dom.window.sessionStorage,localStorage:dom.window.localStorage,fetch,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});
  const entry=join(temp,'entry.jsx');await writeFile(entry,'export {default as Evidence} from '+JSON.stringify(join(project,'src/PerceptionTaskEvidence.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';');
  const built=await build({root:project,configFile:false,mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const file=join(temp,'bundle.mjs');await writeFile(file,built.output.find(x=>x.isEntry).code);const actual=await import(pathToFileURL(file).href);actual.setSession('csrf','workspace','user','reviewer');
  const {createRoot}=await import('react-dom/client');const container=document.getElementById('root');root=createRoot(container);const Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
  const settle=async()=>{for(let i=0;i<5;i++)await act(async()=>new Promise(done=>setTimeout(done,0)));};
  const render=async value=>{await act(async()=>root.render(React.createElement(actual.Evidence,{task:value,account:{workspace_id:'workspace'},Btn})));await settle();};
  const refresh=async()=>{const button=[...container.querySelectorAll('button')].find(x=>x.textContent.includes('刷新接收状态'));assert.ok(button);await act(async()=>button.click());await settle();};
  await render(task('one','run-one'));assert.match(container.textContent,/已收到并核对保存依据/);
  mode='pending';await refresh();assert.match(container.textContent,/正在等待接收/);assert.doesNotMatch(container.textContent,/已收到并核对/);
  mode='blocked';await refresh();assert.match(container.textContent,/接收受阻/);
  mode='integrity_problem';await refresh();assert.match(container.textContent,/接收依据未通过核对/);
  mode='not_available';await refresh();assert.match(container.textContent,/尚无可核验的独立接收记录/);
  mode='offline';await refresh();assert.match(container.textContent,/暂时无法核对/);assert.doesNotMatch(container.textContent,/已收到并核对/);
  mode='bad';await refresh();assert.match(container.textContent,/暂时无法核对/);assert.doesNotMatch(container.textContent,/已收到并核对/);
  await render(task('old','run-old'));assert.match(container.textContent,/正在核对数据管理接收状态/);
  mode='pending';await render(task('new','run-new'));releaseOld(response(state('received')));await settle();assert.match(container.textContent,/正在等待接收/);assert.doesNotMatch(container.textContent,/已收到并核对/);
  assert.ok(calls.some(x=>x.endsWith('/workspace/tasks/new/video-data-reception')));
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const[k,v]of Object.entries(old)){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}await rm(temp,{recursive:true,force:true});}
});
