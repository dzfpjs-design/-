// Real ReactDOM components, original controller, browser storage and runtime
// transport. Only HTTP responses are synthetic. This is NOT browser, API, PG,
// worker, authorization or business acceptance evidence.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash,webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function components(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'layered-approval-react-v175-'));
  const entry=join(temporary,'entry.mjs');
  await writeFile(entry,
   'export {default as Settings} from '+JSON.stringify(join(project,'src/ApprovalSettingsV175.jsx'))+';\n'+
   'export {default as Task} from '+JSON.stringify(join(project,'src/TaskApprovalPanelV175.jsx'))+';\n'+
   'export * from '+JSON.stringify(join(project,'src/layered-approval-v175.mjs'))+';\n'+
   'export {json,setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\n'+
   'export {default as BundledReact} from "react";\n');
  const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
  const output=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(output);
  const path=join(temporary,'actual-components.mjs');await writeFile(path,output.code);return import(pathToFileURL(path).href);
 })();
 return bundle;
}
const id=n=>'17520000-0000-4000-8000-'+String(n).padStart(12,'0');
const copy=value=>JSON.parse(JSON.stringify(value)),sha=value=>createHash('sha256').update(value).digest('hex');
const account={user_id:id(1),workspace_id:id(2),username:'实际组件测试成员',role:'admin'},taskID=id(3),instance=id(4),unitID=id(5),owner=id(6),csrf='synthetic-approval-csrf';
const stage={name:'一级责任审核',role_key:'reviewer',scope_id:unitID};
const policy={instance_id:instance,business_id:'business_approval_fixture',name:'真实DTO形状的合成规则',scope_id:unitID,stages:[stage],executor:{...stage,name:'执行负责人',role_key:'executor'},acceptance:{...stage,name:'独立验收人',role_key:'acceptance'},enabled:true};
const initialSettings=()=>({schema:'approval-settings-view-v175',units:[{id:unitID,version:1,name:'当前实际组织',parent_id:null,enabled:true}],grants:[],policies:[],members:[{id:account.user_id,username:account.username,system_role:'admin'},{id:owner,username:'原任务发起人',system_role:'operator'}],can_manage:true});
function detail({status='draft',actorOwns=true}={}){
 return {schema:'layered-approval-task-view-v175',task_id:taskID,managed_id:id(7),version:1,task_version:1,status,owner_id:actorOwns?account.user_id:owner,scope_id:unitID,policy:copy(policy),value:{title:'服务器保存的任务',note:'原处理说明',graph_inputs:{quantity:1,optional:null},goal_spec:null},draft_history:[],current_stage:0,submission_hash:status==='in_review'?'a'.repeat(64):null,submissions:[],decisions:[],decision_history:[],execution:null,acceptance:null,acceptance_history:[],supplements:[],acceptance_basis:null,acceptance_issue:null,can_manage:actorOwns,current_user_id:account.user_id,system_role:'admin',permissions:{revise:status==='draft'&&actorOwns,submit:status==='draft'&&actorOwns,decide:status==='in_review'&&!actorOwns,withdraw:false,start:false,resubmit_acceptance:false,accept:false},blocked_reasons:{}};
}
function parentTask(value){return {id:taskID,version:value.task_version,title:value.value.title,business_name:'已配置业务',instance_release_number:1,business:{name:'已配置业务',flow:{citations:[]}},object_snapshots:[],workflow_runtime:{receipts:[]},layered_approval_v175:{status:value.status}};}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden in component test');}}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(name)||Promise.resolve()).then(()=>callback({name})),tail=next.then(()=>{},()=>{});tails.set(name,tail);return next.finally(()=>{if(tails.get(name)===tail)tails.delete(name);});}};}

async function harness(run,{kind='task',initial=detail()}={}){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://approval-component.invalid/',pretendToBeVisual:true,resources:new NoResources()});
 const storage=dom.window.localStorage,calls=[],violations=[],renderErrors=[],callbacks=[];
 const state={detail:copy(initial),settings:initialSettings(),commands:new Map(),postMode:'normal',recoveryVisible:true};
 let root,actual;
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const scope=()=>({actor_id:account.user_id,workspace_id:account.workspace_id,subject:kind==='task'?'task:'+taskID:'settings:'+instance});
 const pendingKey=()=>actual.approvalScopeKey(scope())+':pending';
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname,query:url.search,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin);assert.equal(options.credentials,'same-origin');
   const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);
   if(url.pathname==='/api/me')return response(copy(account));
   if(method==='GET'&&url.pathname==='/api/workspace/approval-settings-v175')return response(copy(state.settings));
   if(method==='GET'&&url.pathname==='/api/workspace/tasks/'+taskID+'/approval-v175')return response(copy(state.detail));
   if(method==='GET'&&url.pathname==='/api/workspace/tasks/'+taskID+'/signal-approval-source')return response({schema:'signal-approval-source-view-v247',task_id:taskID,found:false,source:null,integrity_issue:null});
   if(method==='GET'&&url.pathname==='/api/workspace/tasks/'+taskID)return response(parentTask(state.detail));
   if(method==='GET'&&url.pathname.startsWith('/api/workspace/approval-settings-v175/requests/')){
    const requestID=url.pathname.split('/').at(-1),pending=JSON.parse(storage.getItem(pendingKey()));
    assert.equal(pending.request_id,requestID);assert.equal(url.searchParams.get('expected_sha256'),sha(pending.exact_body));
    const command=state.commands.get(requestID);return command&&state.recoveryVisible?response(copy(command)):response({detail:'原请求暂无回执'},404);
   }
   if(method==='POST'){
    assert.equal(headers.get('X-CSRF-Token'),csrf);
    const pending=JSON.parse(storage.getItem(pendingKey()));assert.ok(pending,'real component staged its original before HTTP');assert.equal(pending.exact_body,options.body);assert.equal(pending.body_sha256,sha(options.body));assert.equal(pending.path,url.pathname.slice(4));
    const body=JSON.parse(options.body);assert.equal(body.request_id,pending.request_id);assert.equal(state.commands.has(body.request_id),false,'no successful command replay in this test');
    let result,operation,subjectID;
    if(url.pathname==='/api/workspace/approval-settings-v175/units'){
     assert.equal(kind,'settings');operation='unit';subjectID=body.unit_id;assert.equal(body.expected_version,0);state.settings.units.push({id:body.unit_id,version:1,name:body.name,parent_id:body.parent_id,enabled:body.enabled});
     result={schema:'approval-setting-receipt-v175',operation,id:body.unit_id,version:1,request_id:body.request_id};
    }else{
     assert.equal(kind,'task');assert.equal(body.expected_version,state.detail.version);
     const leaf=url.pathname.split('/').at(-1);operation='task_'+leaf.replaceAll('-','_');subjectID=taskID;
     if(leaf==='revise'){state.detail.value=copy(body.value);state.detail.status='draft';}
     else if(leaf==='submit'){state.detail.status='in_review';state.detail.submission_hash='c'.repeat(64);state.detail.permissions={...state.detail.permissions,revise:false,submit:false};}
     else if(leaf==='decide'){assert.equal(body.submission_hash,state.detail.submission_hash);state.detail.decisions.push({actor_id:account.user_id,decision:body.decision,reason:body.reason,stage:0,at:'2026-10-23T00:00:00Z'});state.detail.decision_history=copy(state.detail.decisions);state.detail.status=body.decision==='approve'?'approved':'returned';state.detail.permissions.decide=false;}
     else throw Error('Unexpected synthetic task operation '+leaf);
     state.detail.version++;state.detail.task_version++;
     result={schema:'layered-approval-task-receipt-v175',task_id:taskID,managed_id:state.detail.managed_id,request_id:body.request_id,operation:leaf.replaceAll('-','_'),version:state.detail.version,task_version:state.detail.task_version,status:state.detail.status,current_stage:state.detail.current_stage,submission_hash:state.detail.submission_hash};
    }
    const original={schema:'layered-approval-original-result-v175',request_id:body.request_id,operation,subject_id:subjectID,body_sha256:sha(options.body),result};state.commands.set(body.request_id,original);
    if(state.postMode==='lost'){state.postMode='normal';throw Error('synthetic response lost after commit');}
    return response(result);
   }
   throw Error('Unexpected HTTP '+method+' '+url.pathname);
  }catch(error){if(!error.message.startsWith('synthetic'))violations.push(error.message);throw error;}
 };
 const names=['window','document','navigator','localStorage','sessionStorage','crypto','fetch','HTMLElement','Node','Event','MouseEvent','IS_REACT_ACT_ENVIRONMENT'];
 const prior=Object.fromEntries(names.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 try{
  const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,localStorage:storage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto,fetch,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,IS_REACT_ACT_ENVIRONMENT:true};
  for(const[key,value]of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
  // ReactDOM initializes event support with this real DOM present.
  const {createRoot}=await import('react-dom/client');actual=await components();assert.equal(actual.BundledReact,React);actual.setSession(csrf,account.workspace_id,account.user_id,account.username);
  const container=document.getElementById('root');
  const props=()=>kind==='settings'?{api:actual.json,account:copy(account),instanceId:instance,businesses:[{id:policy.business_id,name:'已发布业务'}]}:{api:actual.json,account:copy(account),task:parentTask(state.detail),onSaved:async task=>callbacks.push(copy(task))};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:e=>renderErrors.push(e),onRecoverableError:e=>renderErrors.push(e)});await act(async()=>root.render(React.createElement(kind==='settings'?actual.Settings:actual.Task,props())));};
  const remount=async()=>{await act(async()=>root.unmount());await mount();};
  const settle=async()=>{for(let i=0;i<6;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});};
  const wait=async(predicate,label)=>{for(let i=0;i<150;i++){if(predicate())return;await settle();}assert.fail('timeout: '+label+'; UI: '+container.textContent.slice(-1200));};
  const button=text=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===text);
  const field=text=>[...container.querySelectorAll('label')].find(node=>node.textContent.trim().startsWith(text))?.querySelector('input,textarea,select');
  const click=async text=>{const node=button(text);assert.ok(node,'actual button '+text);assert.equal(node.disabled,false,'enabled '+text);assert.ok(!node.closest('fieldset')?.disabled);await act(async()=>node.click());await settle();};
  const input=async(text,value)=>{const node=field(text);assert.ok(node,'actual field '+text);assert.equal(node.disabled,false);assert.ok(!node.closest('fieldset')?.disabled);const proto=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLInputElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(proto,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  await mount();await wait(()=>kind==='settings'?!!button('保存组织')&&!button('保存组织').disabled:!!field('本次操作的说明或依据')&&!field('本次操作的说明或依据').disabled,'initial real DTO loaded');
  await run({state,calls,storage,container,actual,pendingKey,scope,callbacks,button,field,click,input,wait,settle,remount});
  assert.deepEqual(violations,[]);assert.deepEqual(renderErrors,[]);
 }finally{
  try{if(root)await act(async()=>root.unmount());}finally{dom.window.close();for(const[key,descriptor]of Object.entries(prior)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
 }
}

test('actual settings component persists typed organization name across remount and saves via original HTTP body',async()=>{
 await harness(async({state,calls,storage,actual,scope,field,input,click,wait,remount})=>{
  await input('组织名称','新增实际操作单位');
  const key=actual.approvalScopeKey(scope());assert.equal(actual.readApprovalDraft(storage,key).unit.name,'新增实际操作单位');assert.equal(calls.filter(x=>x.method==='POST').length,0);
  await remount();await wait(()=>field('组织名称')?.value==='新增实际操作单位','human setting survived remount');await click('保存组织');await wait(()=>state.settings.units.some(x=>x.name==='新增实际操作单位'),'normal HTTP setting save');assert.equal(calls.filter(x=>x.method==='POST').length,1);assert.ok(actual.readApprovalDraft(storage,key).unit.unit_id);assert.equal(field('组织名称').value,'新增实际操作单位');
 },{kind:'settings'});
});

test('actual task component restores human revision, saves full value and submits only after explicit confirmation',async()=>{
 await harness(async({state,calls,storage,actual,scope,field,input,click,wait,remount,button})=>{
  await input('任务名称','修订后的人类任务名称');await input('任务说明','原资料已核对，仍待正式审批');await input('本次操作的说明或依据','按现场资料修订，提交责任人员核对');
  assert.equal(actual.readApprovalDraft(storage,actual.approvalScopeKey(scope())).value.note,'原资料已核对，仍待正式审批');assert.equal(calls.filter(x=>x.method==='POST').length,0);
  await remount();await wait(()=>field('任务名称')?.value==='修订后的人类任务名称','human task draft survived remount');await click('保存修订稿到服务器');await wait(()=>state.detail.value.title==='修订后的人类任务名称','revision saved');
  assert.deepEqual(state.detail.value.graph_inputs,{quantity:1,optional:null});assert.equal(state.detail.value.goal_spec,null);await wait(()=>button('提交审批')?.disabled===false,'submit available after saved revision');await click('提交审批');assert.equal(calls.filter(x=>x.method==='POST').length,1,'opening confirmation does not submit');await click('确认办理');await wait(()=>state.detail.status==='in_review','normal submit saved');assert.equal(calls.filter(x=>x.method==='POST').length,2);assert.equal(state.detail.execution,null,'submission does not execute');
 });
});

test('actual decision confirmation freezes reviewed version and refuses a refreshed submission until re-reviewed',async()=>{
 await harness(async({state,calls,input,click,button,container,wait})=>{
  await input('本次操作的说明或依据','已经逐项核对本级所需依据');await click('本级通过');assert.equal(button('确认办理').disabled,false);
  state.detail.version++;state.detail.submission_hash='b'.repeat(64);state.detail.value.note='外部已保存的新提交内容';await click('更新审批状态');await wait(()=>container.textContent.includes('任务提交、审批状态或验收依据已更新'),'stale confirmation blocked');assert.equal(button('确认办理').disabled,true);assert.equal(calls.filter(x=>x.method==='POST').length,0);
  await click('继续核对');await click('本级通过');await click('确认办理');await wait(()=>state.detail.status==='approved','reviewed refreshed submission saved');const write=calls.find(x=>x.method==='POST');assert.equal(JSON.parse(write.body).submission_hash,'b'.repeat(64));assert.equal(JSON.parse(write.body).expected_version,2);
 },{initial:detail({status:'in_review',actorOwns:false})});
});

test('actual lost-reply component retains original nonce through remount and resolves by GET without duplicate POST',async()=>{
 await harness(async({state,calls,storage,pendingKey,field,input,click,wait,remount})=>{
  await input('任务名称','丢回应时仍保留的修订');await input('本次操作的说明或依据','核对原资料并保留原请求');state.postMode='lost';state.recoveryVisible=false;await click('保存修订稿到服务器');await wait(()=>storage.getItem(pendingKey())!==null&&calls.some(x=>x.path.includes('/requests/')),'unknown original remains');const original=storage.getItem(pendingKey()),before=calls.filter(x=>x.method==='POST').length;
  await remount();await wait(()=>field('任务名称')?.value==='丢回应时仍保留的修订','human input survives unknown remount');assert.equal(storage.getItem(pendingKey()),original);state.recoveryVisible=true;await click('核对原请求结果');await wait(()=>storage.getItem(pendingKey())===null,'GET recovered saved receipt');assert.equal(calls.filter(x=>x.method==='POST').length,before);assert.equal(field('任务名称').value,'丢回应时仍保留的修订');assert.equal(state.commands.size,1);
 });
});

test('actual numeric partial input survives refresh and cannot silently submit its previous valid value',async()=>{
 await harness(async({calls,storage,actual,scope,field,input,click,wait,remount,container})=>{
  await input('本次操作的说明或依据','核对数值输入');await input('quantity','1e');assert.equal(actual.readApprovalDraft(storage,actual.approvalScopeKey(scope())).field_inputs['graph_inputs/quantity'],'1e');await remount();await wait(()=>field('quantity')?.value==='1e','partial numeric human text survived refresh');await click('提交审批');await click('确认办理');assert.equal(calls.filter(x=>x.method==='POST').length,0);assert.match(container.textContent,/未完成的数值输入|尚未完成的数值输入/);
 });
});
