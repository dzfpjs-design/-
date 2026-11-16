// Actual installed React DOM/StrictMode with synthetic transport only.  No
// browser, network, database, provider, models or execution authority is used.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {cloneTyped} from '../src/typed-numbers-v132.mjs';
import {graphWireParseCanonical,graphWireCanonical,graphWireDigest} from '../src/full-semantic-graph-wire.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temp=await mkdtemp(join(tmpdir(),'graph-goal-gate-react-'));
  const output=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:join(project,'src/GraphTaskPanel.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=output.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const file=join(temp,'GraphTaskPanel.mjs');await writeFile(file,chunk.code);return (await import(pathToFileURL(file).href)).default;
 })();return bundle;
}
const scope={actor_id:'13300000-0000-4000-8000-000000000001',workspace_id:'13300000-0000-4000-8000-000000000002',task_id:'13300000-0000-4000-8000-000000000003'};
const schema={type:'object',properties:{note:{type:'string',title:'核查说明',maxLength:1000}},required:['note'],additionalProperties:false};
const gate=(status='active',reason=null,human=true,ability=true)=>({schema:'workflow-graph-goal-gate-v133',status,reason,can_submit_new_result:human,can_retry_ability:ability});
function task(){return {id:scope.task_id,workspace_id:scope.workspace_id,version:1,title:'原目标核查任务',business_name:'合成原目标流程',instance_release_number:1,
 goal_spec:{purpose:'合成目标，仅用于显示门测试'},graph_goal_gate_v133:gate(),
 graph_runtime:{graph_hash:'a'.repeat(64),version:1,cancel_epoch:0,status:'waiting',commands:[],activations:{human:{id:'human',node_id:'h',version:0,epoch:0,status:'ready',draft:null,output:null},ability:{id:'ability',node_id:'b',version:1,epoch:0,status:'completed',draft:null,output:{summary:'此前已保存的本地处理结果'}}}},
 graph_pending_humans:[{activation_id:'human',node_id:'h',actor_id:scope.actor_id,role:'负责人',version:0,can_edit:true,output_schema:schema,draft:null}],
 graph_jobs:{ability:{phase:'succeeded'}},graph_waits:[],graph_node_labels:{human:{name:'人工核查',kind:'human',output_schema:schema},ability:{name:'本地只读处理',kind:'ability',output_schema:{type:'object',properties:{summary:{type:'string',title:'处理说明'}}}}},
 business:{flow:{steps:[]}},roles:{负责人:{username:'合成负责人'}},permissions:{can_cancel_task:true},object_snapshots:[]};}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources permitted');}}
async function harness(run){
 const errors=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://graph-goal-gate.invalid/',resources:new NoResources(),virtualConsole});
 const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};
 const descriptors=Object.fromEntries(Object.keys(values).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const [key,value]of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_key,_options,fn)=>fn()}});
 const storage=dom.window.localStorage,session=dom.window.sessionStorage;session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-csrf');
 const container=dom.window.document.getElementById('root'),state={task:task(),receipts:{},lose:false,notSent:false},calls=[];let root,guard;
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],scope.workspace_id);
  if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(path.includes('/graph/requests/')){const receipt=state.receipts[path.split('/').at(-1)]||null;return {schema:'workflow-graph-request-recovery-v1',found:!!receipt,request:cloneTyped(receipt),task:cloneTyped(state.task)};}
  if(path.endsWith('/graph/command-hash')){
   const {command}=graphWireParseCanonical(options.body),body_hash=await graphWireDigest({activation_id:command.activation_id,output:command.output,actor_id:scope.actor_id,expected_version:command.version,cancel_epoch:command.cancel_epoch},{crypto:webcrypto});
   const value={schema:'workflow-graph-command-hash-v1',operation:'complete',actor_id:scope.actor_id,workspace_id:scope.workspace_id,task_id:scope.task_id,graph_hash:'a'.repeat(64),command,body_hash},canonical=graphWireCanonical(value);
   return {schema:'report-full-semantic-graph-canonical-wire-v1',canonical,value:JSON.parse(canonical)};
  }
  if(options.method==='POST'||options.method==='PUT'){
   const operation=path.split('/').at(-1);assert.ok(['complete','draft'].includes(operation));assert.equal(options.method,operation==='draft'?'PUT':'POST');
   const key=Object.keys(storage).find(key=>key.endsWith(':outbox'));assert.ok(key,'original outbox exists before mutation');const pending=JSON.parse(storage.getItem(key));assert.equal(pending.exact_body,options.body);
   if(state.notSent)throw Error('合成连接未确认原请求');
   const command=operation==='draft'?JSON.parse(options.body):graphWireParseCanonical(options.body),receipt={request_id:command.request_id,op:operation,actor_id:scope.actor_id,body_hash:pending.body_hash};state.receipts[command.request_id]=receipt;state.task.graph_runtime.commands.push(receipt);
   const activation=state.task.graph_runtime.activations.human;activation.version++;
   if(operation==='draft'){
    activation.draft=cloneTyped(command.draft);state.task.graph_pending_humans[0].version=activation.version;state.task.graph_pending_humans[0].draft=cloneTyped(command.draft);
   }else{activation.status='completed';activation.output=cloneTyped(command.output);state.task.graph_pending_humans=[];}
   state.task.version++;state.task.graph_runtime.version++;
   if(state.lose)throw Error('合成保存成功后丢失响应');return cloneTyped(state.task);
  }
  assert.equal(path,'/workspace/tasks/'+scope.task_id);return cloneTyped(state.task);
 };
 const settle=async()=>{for(let i=0;i<12;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 const button=label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(node,'actual button '+label);return node;};
 try{
  const {createRoot}=await import('react-dom/client'),Panel=await component();
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Panel,{task:cloneTyped(state.task),api,actor:{id:scope.actor_id},workspace:scope.workspace_id,onRegisterGuard:value=>{guard=value;},onSaved:async()=>true}))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const unmount=async()=>{if(root){const current=root;root=null;await act(async()=>current.unmount());await settle();}};
  const click=async label=>{const node=button(label);assert.equal(node.disabled,false,label+' enabled');await act(async()=>node.click());await settle();};
  const edit=async text=>{const node=container.querySelector('textarea');assert.ok(node);assert.equal(node.disabled,false);const setter=Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set;await act(async()=>{setter.call(node,text);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();assert.equal(container.querySelector('textarea').value,text);};
  await run({dom,container,state,calls,storage,mount,unmount,render,click,button,edit,settle,get guard(){return guard;}});await unmount();assert.deepEqual(errors,[]);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,descriptor]of Object.entries(descriptors)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
}

test('expired Goal blocks a new result but actual draft save, prior output and cancellation remain available',()=>harness(async h=>{
 h.state.task.graph_goal_gate_v133=gate('expired','deadline_expired',false,false);
 h.state.task.graph_jobs.ability={phase:'late_result_preserved',reason:'原目标已停止、到期或额度受限；原调用结果已保留，未推进流程'};await h.mount();
 assert.ok(h.container.textContent.includes('原目标已到截止时间'));assert.ok(h.container.textContent.includes('此前已保存的本地处理结果'));assert.ok(h.container.textContent.includes('执行条件变化后返回的原结果已保留'));assert.ok(!h.container.textContent.includes('取消后返回的原结果'));
 assert.equal(h.button('保存结果并完成此待办').disabled,true);assert.equal(h.button('取消后续流程').disabled,false);
 await h.edit('已到期仍需保留的人工核查稿');await h.click('保存草稿，稍后继续');
 assert.equal(h.calls.filter(call=>call.path.endsWith('/graph/draft')&&call.method==='PUT').length,1);assert.equal(h.calls.filter(call=>call.path.endsWith('/graph/complete')).length,0);
 assert.equal(h.state.task.graph_runtime.activations.human.draft.values.note.raw,'已到期仍需保留的人工核查稿');
 await h.unmount();await h.mount();assert.equal(h.container.querySelector('textarea').value,'已到期仍需保留的人工核查稿');assert.ok(h.guard());
}));

test('same Task renders stopped, accepted, step and call limits without disabling drafts or treating all budgets alike',()=>harness(async h=>{
 await h.mount();await h.edit('各类目标限制期间仍保留的输入');
 for(const [status,reason,title,human]of [['stopped','goal_stopped','原目标已停止',false],['accepted','goal_accepted','原目标已保存验收结果',false],['budget_blocked','max_steps','步骤额度已用尽',false],['budget_blocked','max_tool_calls','能力调用额度已用尽',true],['active',null,'每次新的结果提交',true]]){
  h.state.task.graph_goal_gate_v133=gate(status,reason,human,status==='active');await h.render();assert.ok(h.container.textContent.includes(title));
  assert.equal(h.button('保存结果并完成此待办').disabled,!human);assert.equal(h.container.querySelector('textarea').value,'各类目标限制期间仍保留的输入');assert.equal(h.container.querySelector('textarea').disabled,false);assert.equal(h.button('保存草稿，稍后继续').disabled,false);
 }
 delete h.state.task.graph_goal_gate_v133;await h.render();assert.ok(h.container.textContent.includes('目标执行限制暂无法核对'));assert.equal(h.button('保存结果并完成此待办').disabled,true);
 h.state.task.graph_goal_gate_v133={...gate(),reason:'SELECT private diagnostic'};await h.render();assert.ok(!h.container.textContent.includes('SELECT private diagnostic'));assert.equal(h.button('保存结果并完成此待办').disabled,true);
 delete h.state.task.goal_spec;delete h.state.task.graph_goal_gate_v133;await h.render();assert.equal(h.button('保存结果并完成此待办').disabled,false);assert.ok(!h.container.textContent.includes('目标执行限制暂无法核对'));assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
}));

test('a saved original result is recovered by GET after the Goal stops, without another result submission',()=>harness(async h=>{
 await h.mount();await h.edit('原目标停止前已提交的结果');h.state.lose=true;await h.click('保存结果并完成此待办');
 const key=Object.keys(h.storage).find(key=>key.endsWith(':outbox')),original=h.storage.getItem(key);assert.ok(original);
 h.state.task.graph_goal_gate_v133=gate('stopped','goal_stopped',false,false);await h.unmount();await h.mount();assert.equal(h.storage.getItem(key),original);
 const count=h.calls.length;assert.equal(h.button('只核对原请求是否已保存').disabled,false);await h.click('只核对原请求是否已保存');
 assert.equal(h.storage.getItem(key),null);assert.ok(h.calls.slice(count).every(call=>call.method==='GET'));assert.equal(h.calls.filter(call=>call.path.endsWith('/graph/complete')).length,1);
 assert.ok(h.container.textContent.includes('原目标停止前已提交的结果'));assert.ok(h.container.textContent.includes('原目标已停止'));
}));

test('negative original lookup retains exact pending bytes while expiry disables effectful resend and keeps GET available',()=>harness(async h=>{
 await h.mount();await h.edit('尚未保存的原结果不能用重试解除到期');h.state.notSent=true;await h.click('保存结果并完成此待办');
 const key=Object.keys(h.storage).find(key=>key.endsWith(':outbox')),original=h.storage.getItem(key);assert.ok(original);
 h.state.task.graph_goal_gate_v133=gate('expired','deadline_expired',false,false);await h.render();await h.click('只核对原请求是否已保存');
 assert.equal(h.button('按原内容重试提交').disabled,true);assert.equal(h.button('只核对原请求是否已保存').disabled,false);assert.equal(h.storage.getItem(key),original);
 assert.equal(h.container.querySelector('textarea').value,'尚未保存的原结果不能用重试解除到期');assert.equal(h.calls.filter(call=>call.path.endsWith('/graph/complete')).length,1);
 await h.unmount();await h.mount();assert.equal(h.storage.getItem(key),original);assert.equal(h.button('只核对原请求是否已保存').disabled,false);
}));
