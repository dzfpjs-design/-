// Real React DOM/StrictMode and real runtime-api; only HTTP responses are
// synthetic. No component/hook, backend authority, worker or model is replaced.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {defaultNativeGoal} from '../src/native-goal-contract.mjs';
import {abilityScope,abilityKey} from '../src/workflow-ability-contract.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function components(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'workflow-current-unavailable-'));const entry=join(temporary,'entry.mjs');
  await writeFile(entry,'export {default as Automatic} from '+JSON.stringify(join(project,'src/WorkflowAutomaticStep.jsx'))+';\nexport {default as Goal} from '+JSON.stringify(join(project,'src/NativeGoalStudio.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\n');
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const output=join(temporary,'components.mjs');await writeFile(output,chunk.code);return import(pathToFileURL(output).href);
 })();return bundle;
}
const account={user_id:'14300000-0000-4000-8000-000000000001',workspace_id:'14300000-0000-4000-8000-000000000002',username:'synthetic-operator',role:'operator'},taskID='14300000-0000-4000-8000-000000000003',nonce='14300000-0000-4000-8000-000000000004';
const reason='当前启用版本暂不可读，请先刷新核对；原稿和原请求保留。';
const step={id:'local_count',name:'原本地核算步骤',purpose:'仅核对原任务冻结输入',execution:{adapter:'builtin',capability_name:'本机原计数'}};
const automaticTask=()=>({id:taskID,version:3,status:'workflow_waiting',workflow_runtime:{current_step_id:step.id,receipts:[]},permissions:{can_run_ability:true,can_retry_ability:false,can_probe_ability:true,can_cancel:true}});
const goalView=()=>({task_id:taskID,version:3,task_status:'workflow_waiting',can_manage:true,can_stop:true,goal_confirmed:true,goal:{purpose:'原业务目标与已保存条件',required_fields:['reading'],observation_mode:'existing_only',criteria:[]},fields:{reading:'原核查数量'},field_kinds:{reading:'integer'},field_units:{reading:null},entity_ids:['original-object'],goal_tools:[],query_tools:[],relation_types:[],check:null,check_worker:null,acceptance:null,cancelled:null,scope_error:null});
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources permitted');}}
function Button({children,primary,ghost,icon,...props}){return React.createElement('button',{type:'button',...props},children);}
function deferred(){let release;return {promise:new Promise(resolve=>{release=resolve;}),release:()=>release()};}

async function harness(kind,run){
 const errors=[],violations=[],calls=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://workflow-current-unavailable.invalid/',resources:new NoResources(),virtualConsole:console});
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,state={blocked:reason,goal:goalView(),goalReadGate:null},base='/api/workspace/tasks/'+taskID,legacyKey='ga-native-goal:'+account.workspace_id+':'+account.username+':'+taskID;
 const form={...defaultNativeGoal('保留原目标人工稿'),fields:['reading'],reason:'保留原目标操作说明'};storage.setItem(legacyKey,JSON.stringify({form,pending:null}));
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_name,_options,callback)=>callback()}});
 session.setItem('ga-setup-csrf','synthetic-v143-csrf');session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET',path=url.pathname+url.search;calls.push({path,method,body:options.body});
  const respond=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
  try{
   assert.equal(url.origin,dom.window.location.origin);const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),'synthetic-v143-csrf');
   if(path==='/api/me'){assert.equal(method,'GET');return respond(account);}
   if(path===base+'/ability-requests/'+nonce){assert.equal(method,'GET');return respond({request_id:nonce,task_id:taskID,actor_id:account.user_id,saved:false,status:'unknown',request_hash:null,step_id:null,visit:null,result_version:null,task_version:3,task_status:'workflow_waiting',current_step_id:step.id,current_visit:0});}
   if(path===base+'/goal'&&method==='GET'){const gate=state.goalReadGate;if(gate)await gate.promise;return respond(state.goal);}
   if(path===base+'/goal/requests/recover'){
    assert.equal(method,'POST');const body=JSON.parse(options.body);assert.equal(body.operation,'check');assert.equal(body.action,'probe');assert.deepEqual(body.original_request,{request_id:nonce,expected_version:3,reason:'原核对请求完整说明'});return respond({version:3,saved:true,abandoned:false});
   }
   if(path===base+'/goal/stop'){
    assert.equal(method,'POST');const body=JSON.parse(options.body);assert.equal(body.reason,'目录失读时仍保留的停止说明');assert.equal(body.expected_version,3);assert.ok(body.request_id);state.goal={...state.goal,version:4,can_stop:false,cancelled:{reason:body.reason,at:'2026-10-21T06:00:00Z'}};return respond(state.goal);
   }
   const ancillary=new Set([base+'/query-proposals',base+'/supporting-queries?limit=20',base+'/intelligent-plan',base+'/goal/acquisitions',base+'/experience']);
   if(ancillary.has(path)&&method==='GET')return respond({detail:'本合成传输不提供此独立历史目录'},503);
   throw Error('Unexpected synthetic request '+method+' '+path);
  }catch(error){violations.push(error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 const container=dom.window.document.getElementById('root');let root,refreshes=0,cancellations=0,guard;
 try{
  const {createRoot}=await import('react-dom/client'),actual=await components();actual.setSession('synthetic-v143-csrf',account.workspace_id,account.user_id,account.username);
  const settle=async()=>{for(let turn=0;turn<16;turn++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
  const render=async()=>{const props=kind==='automatic'?{task:automaticTask(),step,account,Btn:Button,executionBlockedReason:state.blocked,onRefresh:async()=>{refreshes++;},onCancel:()=>{cancellations++;}}:{task:{id:taskID,title:'原人工任务',version:3},account,Btn:Button,locked:false,executionBlockedReason:state.blocked,onChanged:async()=>{},onRegisterGuard:value=>{guard=value;}};await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(kind==='automatic'?actual.Automatic:actual.Goal,props))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const unmount=async()=>{if(root){const previous=root;root=null;await act(async()=>previous.unmount());await settle();}};
  const button=label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(node,'actual button '+label);return node;};
  const click=async label=>{const node=button(label);assert.equal(node.disabled,false,label+' remains available');await act(async()=>node.click());await settle();};
  const edit=async(label,value)=>{const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node,label);assert.equal(node.disabled,false);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();assert.equal(container.querySelector('[aria-label="'+label+'"]').value,value);};
  await run({state,storage,session,container,calls,form,legacyKey,mount,unmount,render,button,click,edit,settle,get refreshes(){return refreshes;},get cancellations(){return cancellations;},get guard(){return guard;}});
  await unmount();assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{state.goalReadGate?.release();if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,descriptor]of Object.entries(prior)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
}

test('automatic current-read failure blocks fresh and negative-lookup execution while preserving original GET and cancellation',()=>harness('automatic',async h=>{
 await h.mount();assert.equal(h.button('确认并执行此步骤').disabled,true,'a stale task permission is not current execution eligibility');assert.ok(h.container.textContent.includes(reason));h.button('确认并执行此步骤').click();await h.settle();assert.equal(h.calls.length,0);
 await h.unmount();const scope=abilityScope(account,automaticTask(),step),key=abilityKey(scope),original=JSON.stringify({schema:1,scope,pending:{body:{version:3,step_id:step.id,request_id:nonce,retry:false}},history:[]});h.storage.setItem(key,original);
 await h.mount();await h.click('核对原能力请求是否已保存');assert.equal(h.calls.filter(call=>call.method==='GET'&&call.path.endsWith('/ability-requests/'+nonce)).length,1);assert.equal(h.button('按原编号继续能力请求').disabled,true,'a negative lookup cannot start a previously unsent invocation while current eligibility is unknown');
 await h.click('刷新处理进度和权限');await h.click('取消任务');assert.equal(h.refreshes,1);assert.equal(h.cancellations,1);assert.equal(h.storage.getItem(key),original);assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
}));

test('Goal current-read failure blocks fresh and late check dispatch but preserves local draft, original probe and stop',()=>harness('goal',async h=>{
 await h.mount();assert.equal(h.button('按原要求核对目标').disabled,true);assert.ok(h.container.textContent.includes(reason));assert.equal(h.button('停止目标协调').disabled,false);
 await h.edit('业务目标操作理由','目录失读时仍保留的停止说明');await h.guard();const raw=[...Array(h.storage.length)].map((_,index)=>h.storage.getItem(h.storage.key(index))).find(value=>value.includes('目录失读时仍保留的停止说明'));assert.ok(raw,'local human draft is still durable');
 h.state.blocked='';await h.render();const gate=deferred();h.state.goalReadGate=gate;await h.click('按原要求核对目标');h.state.blocked=reason;await h.render();gate.release();h.state.goalReadGate=null;await h.settle();assert.equal(h.calls.filter(call=>call.path.endsWith('/goal/check')).length,0,'a parent current failure during fresh load must stop the later dispatch');
 await h.unmount();h.storage.setItem(h.legacyKey,JSON.stringify({form:{...h.form,reason:'目录失读时仍保留的停止说明'},pending:{action:'check',body:{request_id:nonce,expected_version:3,reason:'原核对请求完整说明'}}}));
 await h.mount();assert.equal(h.button('恢复原目标请求').disabled,true,'the unknown original is retained but cannot first dispatch a check');await h.click('核对原请求是否已保存');assert.ok([...Array(h.storage.length)].some((_,index)=>h.storage.getItem(h.storage.key(index)).includes(nonce)),'original nonce and body remain in immutable local history');
 assert.equal(h.calls.filter(call=>call.path.endsWith('/goal/requests/recover')).length,1);await h.click('停止目标协调');assert.equal(h.calls.filter(call=>call.path.endsWith('/goal/stop')).length,1);assert.ok(h.container.textContent.includes('目标已停止：目录失读时仍保留的停止说明'));assert.equal(h.calls.filter(call=>call.path.endsWith('/goal/check')).length,0);assert.ok(h.calls.some(call=>call.method==='GET'&&call.path.endsWith('/goal')));
}));
