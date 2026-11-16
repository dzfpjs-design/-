import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';

// Actual JSX, installed React DOM and actual controller; synthetic transport
// only. This test does not claim browser, API or persisted approval evidence.
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let temporary,bundled;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundled)bundled=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'graph-approval-caption-'));
  // React and ReactDOM above resolve in the same environment before Vite can
  // mutate NODE_ENV. Keep the JSX bundle consistent and restore that process
  // setting before importing the compiled component or running another test.
  const previousNodeEnv=process.env.NODE_ENV;let output;
  try{
   process.env.NODE_ENV='development';
   output=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',
    define:{'process.env.NODE_ENV':JSON.stringify('development')},
    build:{ssr:join(project,'src/GraphTaskPanel.jsx'),write:false,minify:false,
     rollupOptions:{output:{format:'es',inlineDynamicImports:true,
      paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  }finally{
   if(previousNodeEnv===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=previousNodeEnv;
  }
  const chunk=output.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);
  const path=join(temporary,'panel.mjs');await writeFile(path,chunk.code);
  return (await import(pathToFileURL(path).href)).default;
 })();
 return bundled;
}
const scope={actor_id:'90000000-0000-4000-8000-000000000001',
 workspace_id:'90000000-0000-4000-8000-000000000002',task_id:'90000000-0000-4000-8000-000000000003'};
function task(status,version){return {id:scope.task_id,workspace_id:scope.workspace_id,version,
 title:'已保存的完整流程',business_name:'合成核验',instance_release_number:1,status:'workflow_completed',
 layered_approval_v175:{schema:'layered-approval-task-view-v175',task_id:scope.task_id,status},
 graph_runtime:{graph_hash:'a'.repeat(64),version:1,cancel_epoch:0,status:'succeeded',clock:100,
  commands:[],activations:{}},graph_pending_humans:[],graph_jobs:{},graph_waits:[],
 graph_node_labels:{},object_snapshots:[],roles:{},business:{flow:{steps:[],citations:[]}},
 permissions:{can_cancel_task:false}};}
class NoResources extends ResourceLoader{fetch(){throw Error('no external resources in caption test');}}

test('actual graph panel reports saved independent acceptance and retains pre-acceptance wording otherwise',async()=>{
 const errors=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',
  {url:'https://approval-caption.invalid/',resources:new NoResources(),virtualConsole});
 const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,
  HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,
  sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true};
 const originals=Object.fromEntries(Object.keys(values).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value] of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_key,_options,fn)=>fn()}});
 dom.window.sessionStorage.setItem('ga-setup-csrf-user',scope.actor_id);
 dom.window.sessionStorage.setItem('ga-setup-csrf-workspace',scope.workspace_id);
 dom.window.sessionStorage.setItem('ga-setup-csrf','synthetic-csrf');
 const container=dom.window.document.getElementById('root'),calls=[];let root,current=task('executing',3);
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET'});
  assert.equal(options.method||'GET','GET','caption reads cannot send a mutation');
  return path==='/me'?{user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'}:structuredClone(current);
 };
 try{
  const Component=await component();
  root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error)});
  async function render(){
   await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Component,
    {task:structuredClone(current),api,actor:{user_id:scope.actor_id,role:'operator'},workspace:scope.workspace_id}))));
   for(let step=0;step<12;step++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});
   assert.deepEqual(errors.map(error=>error?.stack||String(error)),[],
    'actual component must render without a caught React or DOM error before caption assertions');
  }
  await render();
  assert.match(container.textContent,/流程完成后还须由独立岗位验收/);
  current=task('accepted',4);await render();
  assert.match(container.textContent,/独立验收已保存/);
  assert.match(container.textContent,/原流程结果与验收依据保留在上方记录中/);
  assert.doesNotMatch(container.textContent,/流程完成后还须由独立岗位验收/);
  assert.equal(container.querySelectorAll('textarea,input,select').length,0);
  assert.equal([...container.querySelectorAll('button')].some(button=>/完成此待办|确认.*验收/.test(button.textContent)),false);
  current=task('acceptance_returned',5);await render();
  assert.match(container.textContent,/流程完成后还须由独立岗位验收/);
  assert.doesNotMatch(container.textContent,/独立验收已保存/);
  current=task('accepted',6);
  current.graph_runtime={...current.graph_runtime,version:2,status:'blocked',error:'required_receipts_missing'};
  current.graph_node_labels={original_human_check:{name:'原人工核查'}};
  current.layered_approval_v175.acceptance={basis:{graph_supplement:{
   schema:'workflow-graph-supplement-evidence-v1',reason:'已保存同目标的补充办理与独立数据接收回执。',
   original_graph_status:'blocked',original_graph_error:'required_receipts_missing',
   skipped_node_ids:['packet','reasoning','review_decision','original_human_check'],evidence_generation:3,basis_hash:'b'.repeat(64),
  }}};
  await render();
  const supplement=container.querySelector('[aria-label="已验收的补充办理依据"]');
  assert.ok(supplement);
  assert.match(supplement.textContent,/补充办理已独立验收/);
  assert.match(supplement.textContent,/原流程受阻/);
  assert.match(supplement.textContent,/原流程缺少完成标准所需的回执/);
  assert.match(supplement.textContent,/原人工核查/);
  assert.match(supplement.textContent,/资料包准备.*业务推理.*业务建议核对/);
  assert.match(supplement.textContent,/第 3 代/);
  assert.equal(container.querySelector('[data-graph-processing-state]').dataset.graphProcessingState,'blocked');
  assert.doesNotMatch(container.textContent,/完整流程已完成|original_human_check/);
  assert.ok(calls.some(call=>call.path==='/workspace/tasks/'+scope.task_id));
  assert.ok(calls.every(call=>call.method==='GET'));
  assert.deepEqual(errors,[]);
 }finally{
  if(root)await act(async()=>root.unmount());
  dom.window.close();
  for(const [key,descriptor] of Object.entries(originals)){
   if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
  }
 }
});
