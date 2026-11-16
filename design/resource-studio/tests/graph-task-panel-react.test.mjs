import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import React,{act,useEffect} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {graphHash} from '../src/graph-task-controller.mjs';
import {webcrypto} from 'node:crypto';
// Real installed React DOM and StrictMode with synthetic JSON transport only.
// No HTTP, browser, database, original documents, provider calls or models.
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{temp=await mkdtemp(join(tmpdir(),'graph-task-real-react-'));
  const output=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:join(project,'src/GraphTaskPanel.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=output.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const file=join(temp,'GraphTaskPanel.mjs');await writeFile(file,chunk.code);return (await import(pathToFileURL(file).href)).default;})();
 return bundle;
}
const copy=x=>JSON.parse(JSON.stringify(x));
const scope={actor_id:'20000000-0000-4000-8000-000000000001',workspace_id:'20000000-0000-4000-8000-000000000002',task_id:'20000000-0000-4000-8000-000000000003'};
const jobID='20000000-0000-4000-8000-000000000004';
function syntheticTask(){
 const schema={type:'object',properties:{note:{type:'string',title:'现场核对说明'}},required:['note'],additionalProperties:false};
 return {id:scope.task_id,workspace_id:scope.workspace_id,version:2,title:'合成对象状态核对',business_name:'对象核对',instance_release_number:4,
  graph_runtime:{graph_hash:'a'.repeat(64),version:1,cancel_epoch:0,status:'waiting',clock:110,commands:[],activations:{human:{id:'human',node_id:'human-node',version:0,epoch:0,status:'ready',output:null,draft:null},wait:{id:'wait',node_id:'wait-node',version:0,epoch:0,status:'waiting_event',created_at:100,wait_started_at:100,deadline:200,completed_at:null,output:null}}},
  graph_pending_humans:[{activation_id:'human',node_id:'human-node',source_step_id:'human-step',role:'负责人',actor_id:scope.actor_id,version:0,can_edit:true,output_schema:schema,draft:null}],
  graph_node_labels:{wait:{name:'等待监测分析结果',kind:'wait'},human:{name:'核对说明',kind:'human',output_schema:schema}},
  graph_jobs:{},graph_wait_poll:{job_id:jobID},graph_waits:[{id:'wait',node_id:'wait-node',status:'waiting_event',deadline:200}],graph_wait_retries:{},object_snapshots:[],roles:{负责人:{username:'合成负责人'}},
  business:{flow:{steps:[{id:'human-step',name:'核对说明',purpose:'保留实际人工输入',citations:[]}],citations:[]}},permissions:{can_cancel_task:true},
  graph_message_status:{arrival_basis:'durable_admission',last_checked_at:110,error:{job_id:jobID,reason:'synthetic internal diagnostic',job_version:3,status:'failed',attempts:1},can_retry:true,windows:[{activation_id:'wait',status:'open',opened_at:100,deadline:200,closed_at:null,event_id:null}],candidate_only:true,physical_state_verified:false}};
}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources permitted in the synthetic DOM test');}}
async function harness(run){
 const virtualConsole=new VirtualConsole(),errors=[];virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-task.invalid/',resources:new NoResources(),virtualConsole});
 const names=['window','document','navigator','HTMLElement','Event','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'],descriptors=Object.fromEntries(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
 for(const [name,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});
 const storage=dom.window.localStorage,session=dom.window.sessionStorage;Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_name,_opts,fn)=>fn()}});
 session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-csrf');
 const calls=[],state={task:syntheticTask(),reasoningStatus:null,loseRetirementResponse:false,retirementUnknownWithoutCommit:false,loseResponse:false,unknownWithoutCommit:false,onSavedAccept:true,saved:0},container=dom.window.document.getElementById('root');let root,guard,probe={setup:0,cleanup:0};
 const api=async(path,options={})=>{calls.push({path,method:options.method||'GET',body:options.body});
  if(path==='/me')return {...scope,user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(path.endsWith('/graph/reasoning-evaluation-status')&&state.reasoningStatus)return copy(state.reasoningStatus);
  if(path.includes('/graph/requests/')){const id=path.split('/').at(-1),retired=state.task.graph_reasoning_retirements_v337?.[id],record=retired?{actor_id:retired.actor_id,body_hash:retired.body_hash,activation_id:retired.activation_id,job_id:retired.job_id}:state.task.graph_wait_retries[id];return {schema:'workflow-graph-request-recovery-v1',found:!!record,request:copy(record||null),task:copy(state.task)};}
  if(!options.method||options.method==='GET')return copy(state.task);
  if(path.endsWith('/graph/retire-obsolete-reasoning')){
   const pendingKey=Object.keys(storage).find(key=>key.endsWith(':outbox'));assert.ok(pendingKey,'original request saved before POST');
   const pending=JSON.parse(storage.getItem(pendingKey));assert.equal(pending.exact_body,options.body);
   if(state.retirementUnknownWithoutCommit)throw Error('连接中断，服务器尚未保存');
   const body=JSON.parse(options.body),hash=await graphHash({actor_id:scope.actor_id,body},webcrypto);
   state.task.graph_reasoning_retirements_v337??={};state.task.graph_reasoning_retirements_v337[body.request_id]={
    schema:'graph-reasoning-retirement-v337',request_id:body.request_id,actor_id:scope.actor_id,body_hash:hash,
    activation_id:body.activation_id,job_id:jobID,head_hash:body.expected_head_hash,status:'retired'};
   state.task.graph_jobs[body.activation_id].phase='retired_for_new_evaluation';state.task.version++;
   if(state.loseRetirementResponse)throw Error('模拟响应丢失，原操作已保存');
   return {schema:'graph-reasoning-retirement-v337',status:'retired',task:copy(state.task)};
  }
  assert.ok(path.endsWith('/graph/retry-messages-v2'),'only the intended message retry mutation is permitted');
  const pendingKey=Object.keys(storage).find(key=>key.endsWith(':outbox'));assert.ok(pendingKey,'outbox exists before POST');const pending=JSON.parse(storage.getItem(pendingKey));assert.equal(pending.exact_body,options.body);
  const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['job_id','job_version','reason','request_id']);assert.equal(body.job_version,3);assert.equal(body.job_id,jobID);
  if(state.unknownWithoutCommit)throw Error('合成连接中断，服务器尚未保存');
  state.task.graph_wait_retries[body.request_id]={actor_id:scope.actor_id,body_hash:await graphHash({actor_id:scope.actor_id,body},webcrypto),job_id:jobID};state.task.version++;
  state.task.graph_message_status.error.status='queued';state.task.graph_message_status.error.job_version=4;state.task.graph_message_status.can_retry=false;
  if(state.loseResponse)throw Error('合成网络中断，保存结果未知');return copy(state.task);
 };
 const settle=async()=>{for(let i=0;i<12;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 const button=label=>{const found=[...container.querySelectorAll('button')].find(node=>node.textContent===label);assert.ok(found,'actual DOM button '+label);return found;};
 try{
  const {createRoot}=await import('react-dom/client'),Component=await component();
  function Probe(){useEffect(()=>{probe.setup++;return()=>{probe.cleanup++;};},[]);return null;}
  const props=()=>({task:copy(state.task),api,actor:{id:scope.actor_id},workspace:scope.workspace_id,onRegisterGuard:value=>{guard=value;},onSaved:async()=>{state.saved++;return state.onSavedAccept;}});
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Probe),React.createElement(Component,props()))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const unmount=async()=>{if(root){const previous=root;root=null;await act(async()=>previous.unmount());await settle();}};
  const click=async label=>{const control=button(label);assert.equal(control.disabled,false,label+' enabled');await act(async()=>control.click());await settle();};
  await run({dom,container,state,calls,storage,session,mount,unmount,render,click,button,settle,get guard(){return guard;},get probe(){return probe;},text:()=>container.textContent});
  await unmount();assert.deepEqual(errors,[],'no React or jsdom runtime errors');
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const name of names){if(descriptors[name])Object.defineProperty(globalThis,name,descriptors[name]);else delete globalThis[name];}}
}

test('the actual task panel keeps queued reasoning beside the independent evaluation warning',async()=>harness(async h=>{
 const task=h.state.task;
 task.graph_runtime.activations.reasoning={id:'reasoning',node_id:'reasoning',version:0,epoch:0,status:'ready',output:null,draft:null};
 task.graph_jobs.reasoning={job_id:jobID,phase:'queued'};
 task.graph_node_labels.reasoning={name:'业务分析',kind:'ability'};
 h.state.reasoningStatus={schema:'reasoning-evaluation-status-v335',status:'superseded',activation_id:'reasoning',
  evaluated_at:'2026-11-03T01:00:00Z',current_evaluated_at:'2026-11-03T02:00:00Z',checked_at:'2026-11-03T02:01:00Z',current_freshness:'current'};
 await h.mount();
 const panel=[...h.container.querySelectorAll('article')].find(row=>row.querySelector('h4')?.textContent==='业务分析');
 assert.ok(panel);assert.match(panel.textContent,/已排队/);assert.match(panel.textContent,/已有更新的评价/);
 assert.match(panel.textContent,/原流程尚未因此继续/);assert.doesNotMatch(panel.textContent,/处理完成/);
 assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));

test('retirement persists its original request before POST and recovers a lost response after remount',async()=>harness(async h=>{
 const task=h.state.task;task.graph_pending_humans=[];task.current_goal_evaluation_v288={head_hash:'c'.repeat(64)};
 task.graph_runtime.activations.reasoning={id:'reasoning',node_id:'reasoning',version:0,epoch:0,status:'ready',output:null,draft:null};
 task.graph_jobs.reasoning={job_id:jobID,phase:'queued',attempt:1};
 task.graph_node_labels.reasoning={name:'业务分析',kind:'ability',actor_id:scope.actor_id};
 h.state.reasoningStatus={schema:'reasoning-evaluation-status-v335',status:'superseded',activation_id:'reasoning',
  evaluated_at:'2026-11-03T01:00:00Z',current_evaluated_at:'2026-11-03T02:00:00Z',checked_at:'2026-11-03T02:01:00Z',current_freshness:'current'};
 await h.mount();await h.click('停止旧步骤，准备后续处理');
 assert.match(h.text(),/不会生成新建议或完成任务/);h.state.loseRetirementResponse=true;
 await h.click('确认停止旧步骤');
 const outbox=Object.keys(h.storage).find(key=>key.endsWith(':outbox'));assert.ok(outbox);
 const saved=h.storage.getItem(outbox);assert.ok(saved);assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
 await h.unmount();await h.mount();assert.equal(h.storage.getItem(outbox),saved);
 await h.click('只核对原请求是否已保存');
 assert.equal(h.storage.getItem(outbox),null);assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
 assert.match(h.text(),/旧资料步骤已停用/);assert.match(h.text(),/任务尚未完成/);
}));

test('retirement retries the original exact request only after checking that it was not saved',async()=>harness(async h=>{
 const task=h.state.task;task.graph_pending_humans=[];task.current_goal_evaluation_v288={head_hash:'c'.repeat(64)};
 task.graph_runtime.activations.reasoning={id:'reasoning',node_id:'reasoning',version:0,epoch:0,status:'ready',output:null,draft:null};
 task.graph_jobs.reasoning={job_id:jobID,phase:'queued',attempt:1};
 task.graph_node_labels.reasoning={name:'业务分析',kind:'ability',actor_id:scope.actor_id};
 h.state.reasoningStatus={schema:'reasoning-evaluation-status-v335',status:'superseded',activation_id:'reasoning',
  evaluated_at:'2026-11-03T01:00:00Z',current_evaluated_at:'2026-11-03T02:00:00Z',checked_at:'2026-11-03T02:01:00Z',current_freshness:'current'};
 await h.mount();h.state.retirementUnknownWithoutCommit=true;
 await h.click('停止旧步骤，准备后续处理');await h.click('确认停止旧步骤');
 const original=h.calls.find(row=>row.method==='POST').body;
 await h.click('只核对原请求是否已保存');assert.ok(h.button('按原内容重试提交'));
 h.state.retirementUnknownWithoutCommit=false;await h.click('按原内容重试提交');
 assert.deepEqual(h.calls.filter(row=>row.method==='POST').map(row=>row.body),[original,original]);
 assert.match(h.text(),/旧资料步骤已停用/);
}));

test('actual GraphTaskPanel StrictMode preserves human input and recovers a lost message retry by original nonce after remount',async()=>harness(async h=>{
 await h.mount();assert.ok(h.probe.setup>=2&&h.probe.cleanup>=1,'StrictMode effects actually replay');
 const input=h.container.querySelector('textarea');assert.ok(input);const setter=Object.getOwnPropertyDescriptor(h.dom.window.HTMLTextAreaElement.prototype,'value').set;
 await act(async()=>{setter.call(input,'刷新后仍需保留的草稿');input.dispatchEvent(new h.dom.window.Event('input',{bubbles:true}));});await h.settle();
 assert.equal(input.value,'刷新后仍需保留的草稿');assert.ok([...Array(h.storage.length)].some((_,index)=>h.storage.getItem(h.storage.key(index)).includes('刷新后仍需保留的草稿')));
 h.state.loseResponse=true;await h.click('恢复原消息检查');await h.click('确认恢复检查');
 const outbox=Object.keys(h.storage).find(key=>key.endsWith(':outbox'));assert.ok(outbox);const saved=h.storage.getItem(outbox);assert.ok(saved);
 await h.unmount();await h.mount();assert.equal(h.container.querySelector('textarea').value,'刷新后仍需保留的草稿');assert.equal(h.storage.getItem(outbox),saved);
 await h.click('只核对原请求是否已保存');assert.equal(h.storage.getItem(outbox),null);assert.equal(h.calls.filter(call=>call.method==='POST').length,1);assert.equal(h.state.saved,1);assert.ok(!h.text().includes('synthetic internal diagnostic'));
 assert.equal(h.state.task.graph_message_status.windows[0].deadline,200);assert.equal(h.state.task.graph_runtime.commands.length,0);
}));

test('same Task version refresh reflects queued/running checks and malformed status preserves cancellation and human editing',async()=>harness(async h=>{
 await h.mount();h.state.task.graph_message_status.error.status='queued';h.state.task.graph_message_status.can_retry=false;await h.render();
 assert.equal(h.container.querySelector('textarea').disabled,false);assert.ok(![...h.container.querySelectorAll('button')].some(node=>node.textContent==='确认恢复检查'));
 assert.ok(h.text().includes('原消息检查正在排队'));h.state.task.graph_message_status.error.status='running';await h.render();assert.ok(h.text().includes('后台正在核对原消息'));
 h.state.task.graph_message_status.arrival_basis='unverified';await h.render();assert.equal(h.container.querySelector('textarea').disabled,false);assert.ok([...h.container.querySelectorAll('[role=alert]')].some(node=>node.textContent.includes('消息等待状态暂无法核对')));
 assert.equal(h.button('取消后续流程').disabled,false);assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));


test('actual DOM retries a negatively-probed unknown message request with its exact original body',async()=>harness(async h=>{
 await h.mount();h.state.unknownWithoutCommit=true;await h.click('恢复原消息检查');await h.click('确认恢复检查');
 const original=h.calls.find(call=>call.method==='POST').body;await h.click('只核对原请求是否已保存');
 assert.ok(h.button('按原内容重试提交'));h.state.unknownWithoutCommit=false;await h.click('按原内容重试提交');
 const posts=h.calls.filter(call=>call.method==='POST');assert.equal(posts.length,2);assert.equal(posts[0].body,original);assert.equal(posts[1].body,original);
 assert.equal(Object.keys(h.state.task.graph_wait_retries).length,1);assert.ok(!Object.keys(h.storage).some(key=>key.endsWith(':outbox')));
}));

test('completed graph read-only results tolerate null wait schema and nested null items while preserving human records and hiding internal identifiers',async()=>harness(async h=>{
 const task=h.state.task,wait=task.graph_runtime.activations.wait,human=task.graph_runtime.activations.human;
 const eventID='20000000-0000-4000-8000-000000000005',entityID='20000000-0000-4000-8000-000000000006',internalHash='b'.repeat(64),nestedHash='c'.repeat(64);
 task.version=3;task.graph_runtime.version=2;task.graph_runtime.status='succeeded';task.graph_runtime.clock=130;task.graph_pending_humans=[];task.graph_waits=[];
 Object.assign(wait,{status:'completed',completed_at:120,output:{schema:'internal-learned-projection-v1',event_id:eventID,contract_hash:internalHash,candidate_pixels:11,unknown_pixels:2,result:{attributes:{'差异候选像素数':11,'未知像素数':2}},records:[{note:'等待消息内嵌套保存的原说明',entity_id:entityID,payload_hash:nestedHash}]}});
 task.graph_node_labels.wait.output_schema=null;
 Object.assign(human,{version:1,status:'completed',output:{note:'已完成的原人工核查结果'},draft:{note:'原人工草稿仍保留'}});
 task.graph_runtime.activations.automatic={id:'automatic',node_id:'automatic-node',version:1,epoch:0,status:'completed',output:[{note:'items为空规则时的原自动结果',rows:[{value:'更深一层的原保存结果',node_id:entityID,body_hash:nestedHash}]}],draft:null};
 task.graph_node_labels.automatic={name:'原自动接收汇总',kind:'ability',output_schema:{type:'array',items:null}};
 task.graph_message_status={...task.graph_message_status,last_checked_at:130,error:null,can_retry:false,windows:[{activation_id:'wait',status:'consumed',opened_at:100,deadline:200,closed_at:120,event_id:eventID}]};
 await h.mount();assert.ok(h.probe.setup>=2&&h.probe.cleanup>=1,'actual StrictMode replay still runs');
 const panel=h.container.querySelector('[aria-label="完整流程任务"]');assert.ok(panel);assert.ok(h.text().includes('完整流程已完成'));
 const preserved=[...panel.querySelectorAll('details')].find(node=>node.querySelector('summary')?.textContent.includes('等待监测分析结果'));assert.ok(preserved,'actual completed wait result remains available in the DOM');
 assert.ok(preserved.textContent.includes('差异候选像素数'));assert.ok(preserved.textContent.includes('11'));assert.ok(preserved.textContent.includes('等待消息内嵌套保存的原说明'));
 assert.ok(h.text().includes('items为空规则时的原自动结果'));assert.ok(h.text().includes('更深一层的原保存结果'));assert.ok(h.text().includes('现场核对说明'));assert.ok(h.text().includes('已完成的原人工核查结果'));assert.ok(h.text().includes('原人工草稿仍保留'));
 for(const hidden of [eventID,entityID,internalHash,nestedHash,'internal-learned-projection-v1'])assert.equal(h.text().includes(hidden),false,'read-only rendering must retain internal ID/hash hiding');
 assert.equal(panel.querySelector('textarea'),null,'completed human records are displayed without reopening an editable result form');assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));

test('actual Task parent presents saved business analysis and original inputs in both result areas across StrictMode remount',async()=>harness(async h=>{
 const id=n=>'19700000-0000-4000-8000-'+String(n).padStart(12,'0'),evidenceA=id(81),evidenceB=id(82),hash='d'.repeat(64);
 const context={schema:'workflow-resense-context-v196',scope:{workspace_id:scope.workspace_id,task_id:scope.task_id,instance_id:id(10),release_id:id(11),package_id:id(12),business_id:'private_business_key',graph_hash:hash,source_binding_hash:hash,goal_snapshot_hash:hash,native_goal_context_hash:hash},query:{query_id:id(13),request_hash:hash,goal_delegation_hash:hash,version_at_read:1,result_hash_at_read:hash},gap:{gap_id:id(14),entity_id:id(15),version_at_read:1,fields:['temperature_c'],observation_request:{private_note:'不得直接展示的完整本地观察上下文'},observation_request_hash:hash},policy:{policy_id:id(16),approved_hash:hash,approved_by:scope.actor_id,version_at_read:1,source_pins:[{source_id:id(17),source_hash:hash}]},producer:{capability_id:id(18),capability_hash:hash,invocation_id:id(19),job_id:id(20),activation_id:'private_producer_activation',node_id:'private_producer_node',cancel_epoch:0,input_hash:hash,request_key_hash:hash,actor_id:scope.actor_id},permissions_verified:false,can_dispatch:false,context_hash:hash};
 const inputs={question:'根据当前证据，先核对范围还是立即扩大处置？',evidence_ids:[evidenceB,evidenceA],candidates:[{candidate_ref:'private_proposal_one',summary:'先核对当前受损范围',context:copy(context)},{candidate_ref:'private_proposal_two',summary:'立即扩大处置范围',context:null}]};
 const output={schema:'business-reasoning-result-v197',analysis:{summary:'现有资料支持先核对范围；扩大处置的依据存在冲突。',candidate_assessments:[{candidate_ref:'private_proposal_one',assessment:'supported',reason:'当前原件支持先确认受损对象。',evidence_refs:[evidenceA],missing_information:[]},{candidate_ref:'private_proposal_two',assessment:'conflicting',reason:'另一原件记载的边界不一致。',evidence_refs:[evidenceB],missing_information:[]}],recommendation:{candidate_ref:'private_proposal_one',reason:'先核实范围，后续动作仍按原批准流程。',evidence_refs:[evidenceA]}},review_state:'conflicting_evidence',recommended_candidate_ref:'private_proposal_one',recommended_context:copy(context),permissions_verified:false,can_dispatch:false};
 const task=h.state.task;task.version=3;task.graph_runtime.version=2;task.graph_runtime.status='succeeded';task.graph_runtime.clock=130;
 task.graph_runtime.activations={reasoned:{id:'reasoned',node_id:'business-analysis-node',version:1,epoch:0,status:'completed',completed_at:120,inputs_snapshot:{arguments:copy(inputs)},output:copy(output),draft:null}};
 task.graph_pending_humans=[];task.graph_waits=[];task.graph_node_labels={reasoned:{name:'核对处置建议',kind:'ability',output_schema:{type:'object'}}};
 task.graph_jobs={reasoned:{job_id:jobID,phase:'succeeded',output_schema:{type:'object'}}};
 task.graph_message_status={...task.graph_message_status,last_checked_at:130,error:null,can_retry:false,windows:[]};
 const saved=copy(task),requests=[],oldFetch=Object.getOwnPropertyDescriptor(globalThis,'fetch');
 Object.defineProperty(globalThis,'fetch',{configurable:true,writable:true,value:async(path,options={})=>{
  requests.push({path,options});assert.equal(path,'/api/evidence/'+evidenceA+'/content');assert.equal(options.method||'GET','GET');assert.equal(options.credentials,'same-origin');assert.equal(options.headers['X-CSRF-Token'],'synthetic-csrf');
  return new Response('<script>never_run()</script>\n当前已保存资料原文',{headers:{'content-type':'text/plain; charset=utf-8'}});
 }});
 try{
  await h.mount();assert.ok(h.probe.setup>=2&&h.probe.cleanup>=1,'actual parent StrictMode replay runs');
  const panels=()=>[...h.container.querySelectorAll('[aria-label="业务分析结果"]')];
  assert.equal(panels().length,2,'both automatic Job output and saved activation output use the real child');
  for(const panel of panels()){
   assert.ok(panel.textContent.includes(inputs.question));assert.ok(panel.textContent.includes('先核对当前受损范围'));
   assert.ok(panel.textContent.includes('立即扩大处置范围'));assert.ok(panel.textContent.includes('引用：资料2'));
   assert.ok(panel.textContent.includes('引用：资料1'));assert.ok(panel.textContent.includes('模型建议不等于业务批准'));
  }
  for(const hidden of [output.schema,'candidate_ref','recommended_context','can_dispatch','private_proposal_one','private_proposal_two','不得直接展示的完整本地观察上下文',evidenceA,evidenceB,id(19),hash])assert.equal(h.text().includes(hidden),false,'parent must not fall back to raw saved values for '+hidden);
  assert.equal(requests.length,0,'saved result rendering and StrictMode do not read evidence automatically');
  await h.click('查看资料2');
  assert.equal(requests.length,1);assert.ok(h.container.querySelector('[aria-label="资料2原文"]')?.textContent.includes('当前已保存资料原文'));
  assert.equal(h.container.querySelectorAll('script,iframe,object,embed').length,0);
  assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
  await h.unmount();await h.mount();
  assert.equal(panels().length,2);assert.ok(h.text().includes(inputs.question));assert.ok(h.text().includes('先核对当前受损范围'));
  assert.deepEqual(h.state.task,saved,'viewing and remount do not alter the saved synthetic system of record');
  assert.equal(requests.length,1,'remount recovers saved output without issuing another evidence read');
  assert.equal(h.calls.filter(call=>call.method==='POST').length,0);assert.ok(h.calls.some(call=>call.method==='GET'));
 }finally{if(oldFetch)Object.defineProperty(globalThis,'fetch',oldFetch);else delete globalThis.fetch;}
}));
