import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import React,{act} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {createGraphTaskController,graphHash} from '../src/graph-task-controller.mjs';

// Actual controller and GraphTaskPanel; only transport and browser storage are
// synthetic. No real HTTP, database, provider, original report or device input.
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const scope={actor_id:'87000000-0000-4000-8000-000000000001',workspace_id:'87000000-0000-4000-8000-000000000002',task_id:'87000000-0000-4000-8000-000000000003'};
const copy=value=>JSON.parse(JSON.stringify(value));
class Memory{
 constructor(){this.rows=new Map();}
 get length(){return this.rows.size;}
 key(index){return [...this.rows.keys()][index]??null;}
 getItem(key){return this.rows.get(key)??null;}
 setItem(key,value){this.rows.set(key,String(value));}
 removeItem(key){this.rows.delete(key);}
}
function task(){
 const outputSchema={type:'object',additionalProperties:false,properties:{note:{type:'string',title:'本步骤核对说明',minLength:1}},required:['note']};
 const savedSchema={type:'object',properties:{approved:{type:'boolean',title:'原审核结论'},count:{type:'integer',title:'原登记数量'},note:{type:['string','null'],title:'原补充说明'}},required:['approved','count','note'],additionalProperties:false};
 return {id:scope.task_id,workspace_id:scope.workspace_id,version:2,title:'合成的输入依赖核对',business_name:'输入依赖核对',instance_release_number:1,
  graph_runtime:{graph_hash:'a'.repeat(64),version:2,cancel_epoch:0,status:'waiting',commands:[{request_id:'87000000-0000-4000-8000-000000000004',op:'complete',actor_id:scope.actor_id,body_hash:'b'.repeat(64)}],activations:{
   human:{id:'human',node_id:'human-node',version:0,epoch:0,status:'ready',output:null,draft:null},
   original:{id:'original',node_id:'original-node',version:1,epoch:0,status:'completed',output:{approved:false,count:0,note:null},draft:null}}},
  graph_pending_humans:[{activation_id:'human',node_id:'human-node',source_step_id:'human-step',role:'核对人',actor_id:scope.actor_id,version:0,can_edit:true,output_schema:outputSchema,draft:null,inputs_available:false,required_inputs:[
   {label:'上游实际审核结论',available:true,value:false,source:'output',producer_activation_id:'original',schema:{type:'boolean'}},
   {label:'上游实际登记数量',available:true,value:0,source:'output',producer_activation_id:'original',schema:{type:'integer'}},
   {label:'上游已明确空值',available:true,value:null,source:'output',producer_activation_id:'original',schema:{type:['string','null']}},
   {label:'等待补充观测记录',available:false,value:'不得显示的未就绪占位值',source:'output',producer_activation_id:'waiting',schema:{type:'string'}}]}],
  graph_node_labels:{human:{name:'人工核对',kind:'human',output_schema:outputSchema},original:{name:'原来已完成的审核',kind:'human',output_schema:savedSchema}},
  graph_jobs:{},graph_waits:[],object_snapshots:[],roles:{核对人:{username:'合成核对人'}},permissions:{can_cancel_task:true},business:{flow:{steps:[{id:'human-step',name:'人工核对',purpose:'可以保留待补充的核对草稿',citations:[]}],citations:[]}}};
}
function environment(storage=new Memory(),session=new Memory()){
 for(const [key,value] of Object.entries({'ga-setup-csrf-user':scope.actor_id,'ga-setup-csrf-workspace':scope.workspace_id,'ga-setup-csrf':'synthetic-csrf'}))session.setItem(key,value);
 const calls=[],state={task:task(),loseDraftResponse:false},locks={request:async(_key,_options,fn)=>fn()};
 const api=async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,body:options.body});
  if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(path.includes('/graph/requests/')){
   assert.equal(method,'GET');const nonce=path.split('/').at(-1),receipt=state.task.graph_runtime.commands.find(row=>row.request_id===nonce);
   return {schema:'workflow-graph-request-recovery-v1',found:!!receipt,request:copy(receipt||null),task:copy(state.task)};
  }
  if(path==='/workspace/tasks/'+scope.task_id&&method==='GET')return copy(state.task);
  // Any complete/hash preparation would fail this fixture, not manufacture a
  // successful result and conceal a gate regression.
  assert.equal(path,'/workspace/tasks/'+scope.task_id+'/graph/draft');assert.equal(method,'PUT');
  const keys=Array.from({length:storage.length},(_,i)=>storage.key(i)),outboxKey=keys.find(key=>key?.endsWith(':outbox'));
  assert.ok(outboxKey,'original request is durable before PUT');
  const outbox=JSON.parse(storage.getItem(outboxKey));assert.equal(outbox.exact_body,options.body);
  const body=JSON.parse(options.body),human=state.task.graph_pending_humans[0],activation=state.task.graph_runtime.activations.human;
  assert.equal(body.version,human.version);assert.equal(body.activation_id,'human');assert.equal(body.cancel_epoch,0);
  assert.equal(state.task.graph_runtime.commands.some(row=>row.request_id===body.request_id),false,'no second write under the same original nonce');
  const hash=await graphHash({actor_id:scope.actor_id,expected_version:body.version,cancel_epoch:body.cancel_epoch,activation_id:body.activation_id,draft:body.draft},webcrypto);
  state.task.graph_runtime.commands.push({request_id:body.request_id,op:'draft',actor_id:scope.actor_id,body_hash:hash});
  human.version++;activation.version++;state.task.version++;state.task.graph_runtime.version++;
  human.draft=copy(body.draft);activation.draft=copy(body.draft);
  if(state.loseDraftResponse)throw Error('合成：已保存草稿但响应中断');
  return copy(state.task);
 };
 const controller=()=>createGraphTaskController({api,scope,storage,session,locks,crypto:webcrypto,typedNumbers:true});
 return {storage,session,calls,state,api,locks,controller};
}
const mutations=f=>f.calls.filter(call=>call.method!=='GET');

test('missing required input blocks completion before hash/outbox/POST while a draft saves and recovers its original receipt after reload',async()=>{
 const f=environment(),c=f.controller();await c.open();
 c.edit('human','note',{mode:'value',raw:'先记录现场情况，等待上游结果'});
 const originalResult=copy(f.state.task.graph_runtime.activations.original),originalReceipt=copy(f.state.task.graph_runtime.commands[0]);
 await assert.rejects(()=>c.submit('complete','human'),/所需输入尚未产生/);
 assert.equal(mutations(f).length,0);assert.equal(c.pending,null);assert.equal(f.storage.getItem(c.outboxKey),null);
 assert.equal(c.forms.human.values.note.raw,'先记录现场情况，等待上游结果');assert.equal(c.forms.human.dirty,true);
 f.state.loseDraftResponse=true;await assert.rejects(()=>c.submit('draft','human'),/已保存草稿但响应中断/);
 const raw=f.storage.getItem(c.outboxKey),pending=JSON.parse(raw);assert.ok(raw);assert.equal(pending.operation,'draft');assert.equal(mutations(f).length,1);
 assert.equal(f.state.task.graph_pending_humans[0].inputs_available,false);assert.equal(f.state.task.graph_runtime.activations.human.status,'ready');
 c.dispose();const reopened=f.controller();await reopened.open();assert.equal(f.storage.getItem(reopened.outboxKey),raw);
 const recovered=await reopened.recover();assert.equal(recovered.found,true);assert.equal(mutations(f).length,1,'GET recovery must not resend the saved draft');
 assert.equal(f.storage.getItem(reopened.outboxKey),null);assert.equal(reopened.forms.human.dirty,false);assert.equal(reopened.forms.human.remoteConflict,false);
 assert.equal(reopened.forms.human.values.note.raw,'先记录现场情况，等待上游结果');
 const history=JSON.parse(f.storage.getItem(reopened.prefix+'history:request:'+pending.body.request_id));
 assert.equal(history.receipt.request_id,pending.body.request_id);assert.equal(history.receipt.body_hash,pending.body_hash);assert.equal(history.pending.exact_body,pending.exact_body);
 assert.deepEqual(f.state.task.graph_runtime.activations.original,originalResult);assert.deepEqual(f.state.task.graph_runtime.commands[0],originalReceipt);
 reopened.dispose();
});

test('completion rechecks current required inputs and inconsistent aggregate metadata is rejected',async()=>{
 const f=environment(),human=f.state.task.graph_pending_humans[0];human.required_inputs.at(-1).available=true;human.inputs_available=true;
 const c=f.controller();await c.open();c.edit('human','note',{mode:'value',raw:'页面打开后上游条件发生变化，保留输入'});
 // The rendered detail was available; the authoritative next read is not.
 human.required_inputs.at(-1).available=false;human.inputs_available=false;f.state.task.version++;
 await assert.rejects(()=>c.submit('complete','human'),/所需输入尚未产生/);
 assert.equal(c.detail.graph_pending_humans[0].inputs_available,false);assert.equal(mutations(f).length,0);assert.equal(c.currentPending(),null);
 assert.equal(c.forms.human.values.note.raw,'页面打开后上游条件发生变化，保留输入');
 for(const mutate of [row=>{row.inputs_available=true;},row=>{row.required_inputs[0].available='true';},row=>{delete row.required_inputs[0].value;},row=>{delete row.inputs_available;}]){
  const bad=copy(f.state.task);mutate(bad.graph_pending_humans[0]);const isolated=f.controller();assert.throws(()=>isolated.ingest(bad));isolated.dispose();
 }
 c.dispose();
});

let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temp=await mkdtemp(join(tmpdir(),'graph-required-inputs-'));
  const output=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:join(project,'src/GraphTaskPanel.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=output.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);const target=join(temp,'GraphTaskPanel.mjs');await writeFile(target,chunk.code);return (await import(pathToFileURL(target).href)).default;
 })();
 return bundle;
}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resource access in the synthetic task input test');}}
test('actual StrictMode panel shows required and persisted values without treating false/zero/null as missing, and permits only draft saving',async()=>{
 const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-input-gate.invalid/',resources:new NoResources(),virtualConsole:console});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};
 const previous=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value] of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const f=environment(dom.window.localStorage,dom.window.sessionStorage);Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:f.locks});
 let root;const container=dom.window.document.getElementById('root');
 const until=async(predicate,label)=>{for(let i=0;i<40;i++){if(predicate())return;await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});}assert.ok(predicate(),label);};
 const button=label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(node,'real DOM button '+label);return node;};
 try{
  const Component=await component(),{createRoot}=await import('react-dom/client');
  root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
  await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Component,{task:copy(f.state.task),api:f.api,actor:{id:scope.actor_id},workspace:scope.workspace_id,onSaved:async()=>true}))));
  await until(()=>!!container.querySelector('[aria-label="本步骤所需输入"]'),'actual task and required inputs loaded');
  const required=container.querySelector('[aria-label="本步骤所需输入"]');
  const row=label=>[...required.children].find(item=>item.querySelector('strong')?.textContent===label);
  assert.equal(row('上游实际审核结论').querySelector('dd').textContent,'否');assert.equal(row('上游实际登记数量').querySelector('dd').textContent,'0');assert.equal(row('上游已明确空值').querySelector('dd').textContent,'明确为空');
  assert.match(row('等待补充观测记录').textContent,/尚未产生.*可以先保存草稿/);assert.equal(required.textContent.includes('不得显示的未就绪占位值'),false);
  const saved=[...container.querySelectorAll('details')].find(item=>item.querySelector('summary')?.textContent==='原来已完成的审核 · 处理完成');assert.ok(saved,'persisted completed result is still displayed beside the pending step');
  assert.deepEqual([...saved.querySelectorAll('dt')].map(item=>item.textContent),['原审核结论','原登记数量','原补充说明']);
  assert.deepEqual([...saved.querySelectorAll('dd')].map(item=>item.textContent),['否','0','明确为空']);
  for(const internal of [scope.actor_id,scope.task_id,'b'.repeat(64)])assert.equal(container.textContent.includes(internal),false);
  const complete=button('保存结果并完成此待办');assert.equal(complete.disabled,true);
  const textarea=container.querySelector('textarea');assert.ok(textarea);assert.equal(textarea.disabled,false);
  const setValue=Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set;
  await act(async()=>{setValue.call(textarea,'缺资料时也要保存的现场核对草稿');textarea.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  await until(()=>!button('保存草稿，稍后继续').disabled,'draft is editable while upstream input is unavailable');
  await act(async()=>complete.click());assert.equal(mutations(f).length,0,'disabled completion cannot issue even a hash-preparation request');
  await act(async()=>button('保存草稿，稍后继续').click());
  await until(()=>container.textContent.includes('原请求和当前任务已读回核对，处理结果已保存。'),'draft original receipt is read back');
  assert.equal(mutations(f).length,1);assert.equal(mutations(f)[0].method,'PUT');assert.ok(mutations(f)[0].path.endsWith('/graph/draft'));
  assert.equal(f.state.task.graph_runtime.activations.human.status,'ready');assert.equal(f.state.task.graph_pending_humans[0].inputs_available,false);
  assert.equal(f.state.task.graph_pending_humans[0].draft.values.note.raw,'缺资料时也要保存的现场核对草稿');
  assert.equal(button('保存结果并完成此待办').disabled,true);assert.equal(container.querySelector('textarea').value,'缺资料时也要保存的现场核对草稿');
  assert.ok(container.textContent.includes('原来已完成的审核'));assert.ok(container.textContent.includes('原登记数量'));
  await act(async()=>root.unmount());root=null;assert.deepEqual(errors,[],'no JSX/React runtime error in the actual panel');
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();for(const key of Object.keys(globals)){if(previous[key])Object.defineProperty(globalThis,key,previous[key]);else delete globalThis[key];}
 }
});
