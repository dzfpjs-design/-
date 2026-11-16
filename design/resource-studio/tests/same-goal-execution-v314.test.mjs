// Actual controller and default Card with explicit offline DTO/storage IO.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';
import {SameGoalExecutionControllerV314} from '../src/same-goal-execution-v314-controller.mjs';
import {continuationHash as hash} from '../src/same-goal-continuation-v312-controller.mjs';

const id=n=>'31400000-0000-4000-8000-'+String(n).padStart(12,'0');
const sha='a'.repeat(64),schema='same-goal-continuation-execution-';
const account={user_id:id(1),workspace_id:id(2),role:'operator'};
const task={id:id(3),instance_release_id:id(4)};
const advicePlan={plan_id:id(5)},continuation={continuation_id:id(6)};
const ids={task_id:task.id,advice_plan_id:advicePlan.plan_id,continuation_id:continuation.continuation_id};
const base='/workspace/tasks/'+task.id+'/business-advice/preparations/'+advicePlan.plan_id+'/continuations/'+continuation.continuation_id+'/executions';
const grantBase=base.replace(/executions$/,'tool-grants'),grantSchema='same-goal-continuation-grant-';
const flags={can_dispatch:false,execution_approved:false,goal_completion_verified:false,cloud_send_authorized:false};
const permissions={can_decide:false,can_start:false,can_cancel:false,can_review:false,can_recover:false};
const draft={purpose:'核对本次结果',work_description:'先用原数据汇总，之后人工判断。',expected_result:'得到可复核统计'};
const field={key:'temperature',label:'气温',unit:'℃'};
const grant={...ids,grant_id:id(7),grant_ref:{id:id(7),record_hash:sha},version:1,phase:'active',expired:false,eligible:true,field,operation:'mean'};
const tool={id:id(21),content_hash:sha,release_version:'1.0.0',name:'已核观测数值汇总',fields:[field],binding:'observation_records'};
const policy={ref:{policy_id:id(22),version_id:id(23),revision:2,record_hash:sha},stages:[{label:'业务核对',scope_name:'原办理单位'}]};
const grantView=(row=grant)=>({...row,...flags,schema:grantSchema+'view-v313',max_calls:1,policy_ref:policy.ref,
 expires_at:'2026-11-02T14:00:00Z',permissions:{can_revoke:row.phase==='active'}});
const proposal={...ids,schema:schema+'preview-v314',continuation_ref:{id:ids.continuation_id,version:2,revision_hash:sha,source_hash:sha},
 grant_ref:{...grant.grant_ref,version:1},preview_hash:sha,draft,rationale:'只读核对原数据',plan_origin:'human_revision',supersedes_plan_id:null,field,
 operation:'mean',record_count:2,missing_count:1,data_as_of:'2026-11-02T12:00:00Z',simulation_only:false,test_only:true};
const view=(extra={})=>({...ids,...flags,schema:schema+'view-v314',plan_id:id(8),version:1,phase:'in_review',stage:0,submission_hash:sha,
 proposal,operations:[],job:null,output:null,output_hash:null,data_received:false,permissions,...extra});
const fail=status=>Object.assign(Error('原接口拒绝'),{status});
const copy=value=>structuredClone(value);
class Memory{
 constructor(){this.data=new Map();this.broken=false;}
 get length(){return this.data.size;}
 key(index){return [...this.data.keys()][index]??null;}
 getItem(key){return this.data.get(key)??null;}
 setItem(key,value){if(this.broken)throw Error('本机存储失败');this.data.set(key,String(value));}
 removeItem(key){this.data.delete(key);}
}
const gate=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
function fixture(role='operator'){
 const actor={...account,role};
 const storage=new Memory(),session=new Memory(),calls=[],receipts=new Map(),mode={};
 for(const [key,value] of Object.entries({'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id,'ga-setup-csrf':'fixture-token'}))session.setItem(key,value);
 const state={plan:null,history:[],returned:[],grants:[copy(grant)],me:copy(actor)};
 let tail=Promise.resolve();
 const locks={request:async(key,options,run)=>{const prior=tail,next=gate();tail=next.promise;await prior;try{return await run();}finally{next.resolve();}}};
 const request=async(path,options={})=>{
  const method=options.method||'GET';
  calls.push({path,method,body:options.body});
  assert.equal(options.headers['X-Workspace-ID'],account.workspace_id);
  assert.equal(options.headers['X-CSRF-Token'],'fixture-token');
  if(path==='/me')return copy(state.me);
  if(path===base+'/context'){
   if(mode.contextGate)await mode.contextGate.promise;
   if(mode.contextStatus)throw fail(mode.contextStatus);
   return {...ids,...flags,schema:schema+'context-v314',task_version:3,continuation_version:2,revision_hash:sha,source_hash:sha,draft,
    grants:copy(state.grants),returned_candidates:copy(state.returned),permissions:{can_preview:true,can_submit:true},blocked_reasons:[]};
  }
  if(path===grantBase+'/context')return {...ids,...flags,schema:grantSchema+'context-v313',task_version:3,continuation_version:2,
   revision_hash:sha,source_hash:sha,tools:[copy(tool)],operations:['mean','count'],policy:copy(policy),permissions:{can_issue:state.me.role==='admin'},blocked_reasons:[]};
  if(method==='GET'&&path===grantBase)return {...ids,schema:grantSchema+'view-v313/list',records:state.grants.map(grantView)};
  const records=[...state.history,...(state.plan?[state.plan]:[])];
  if(method==='GET'&&path===base)return {...ids,schema:schema+'view-v314/list',records:copy(records)};
  if(method==='GET'&&records.some(row=>path===base+'/'+row.plan_id))return copy(records.find(row=>path===base+'/'+row.plan_id));
  if(path.startsWith(base+'/operations/')||path.startsWith(grantBase+'/operations/')){
   const saved=receipts.get(path.split('/').at(-1));
   if(!saved)throw fail(404);
   return {...copy(saved),...mode.badReceipt};
  }
  assert.equal(method,'POST');
  const body=JSON.parse(options.body);
  if(path===base+'/preview'){
   assert.ok([...storage.data.values()].some(raw=>raw.includes(body.request_id)));
   return {...copy(proposal),rationale:body.rationale,grant_ref:body.grant_ref,supersedes_plan_id:body.supersedes_plan_id};
  }
  const operation=path===base?'submit':path.split('/').at(-1);
  const before=[...storage.data].find(([key])=>key.endsWith(':original:'+body.request_id));
  assert.ok(before,'full original must precede transport');
  assert.equal(JSON.parse(before[1]).body_hash,await hash(options.body,webcrypto));
  if(mode.failWrite)throw fail(mode.failWrite);
  if(path===grantBase||path.startsWith(grantBase+'/')){
   const issue=path===grantBase;
   assert.equal(state.me.role,'admin');
   const target=issue?id(24):path.split('/').at(-2);
   const result=issue?grantView({...grant,grant_id:target,grant_ref:{id:target,record_hash:sha},operation:body.operation}):
    grantView({...state.grants.find(row=>row.grant_id===target),version:body.expected_grant_version+1,phase:'revoked',eligible:false});
   state.grants=[...state.grants.filter(row=>row.grant_id!==target),result];
   receipts.set(body.request_id,{...ids,schema:grantSchema+'receipt-v313',found:true,grant_id:target,operation:issue?'issue':'revoke',
    request_id:body.request_id,actor_id:actor.user_id,body_hash:await hash(options.body,webcrypto),result_status:'available',result});
   if(mode.lost)throw Error('连接断开');
   return copy(result);
  }
  if(operation==='submit'){
   if(body.supersedes_plan_id&&state.plan)state.history.push(copy(state.plan));
   state.plan=view({plan_id:body.supersedes_plan_id?id(31):id(8),proposal:{...proposal,grant_ref:body.grant_ref,
    supersedes_plan_id:body.supersedes_plan_id,rationale:body.rationale}});
  }else state.plan={...state.plan,version:state.plan.version+1,phase:operation==='cancel'?'cancel_requested':operation==='review'?'adopted':state.plan.phase};
  const response={...ids,schema:schema+'receipt-v314',found:true,plan_id:state.plan.plan_id,operation,request_id:body.request_id,actor_id:account.user_id,
   body_hash:await hash(options.body,webcrypto),result_status:mode.needsCheck?'needs_check':'available',result:mode.needsCheck?null:copy(state.plan)};
  receipts.set(body.request_id,response);
  if(mode.lost)throw Error('连接断开');
  return copy(state.plan);
 };
 const make=()=>new SameGoalExecutionControllerV314({account:actor,task,advicePlan,continuation,request,storage,session,locks,crypto:webcrypto});
 return {storage,session,calls,receipts,mode,state,request,locks,make};
}
async function preview(controller){
 await controller.open();
 controller.updateDraft({grantId:id(7),rationale:'只读核对原数据'});
 await controller.preparePreview();
 assert.equal(controller.preview?.rationale,'只读核对原数据');
}

test('preview remains local to the read-only endpoint, submit validates the original receipt before clearing pending',async()=>{
 const f=fixture(),controller=f.make();
 await preview(controller);
 assert.equal(f.receipts.size,0);
 await controller.submit();
 assert.equal(controller.plan.phase,'in_review');
 assert.equal(controller.pending,null);
 assert.equal(controller.plan.job,null);
 assert.equal(f.calls.filter(row=>row.method==='POST').length,2);
});

test('lost submit response reloads the same nonce using GET and never automatically posts again',async()=>{
 const f=fixture(),controller=f.make();
 await preview(controller);f.mode.lost=true;
 await controller.submit();
 const nonce=controller.pending.request_id;
 assert.equal(controller.guard(),true);
 await assert.rejects(()=>controller.submit());
 controller.dispose();
 const reopened=f.make();
 await reopened.open();
 assert.equal(reopened.pending,null);
 assert.equal(reopened.plan.phase,'in_review');
 assert.ok(f.calls.some(row=>row.path===base+'/operations/'+nonce&&row.method==='GET'));
 assert.equal(f.calls.filter(row=>row.path===base&&row.method==='POST').length,1);
});

test('unknown absent response can resend only the original body after a receipt read',async()=>{
 const f=fixture(),controller=f.make();
 await preview(controller);f.mode.failWrite=503;
 await controller.submit();
 const original=controller.pending;
 f.mode.failWrite=null;
 await controller.resendOriginal();
 const writes=f.calls.filter(row=>row.path===base&&row.method==='POST');
 assert.equal(writes.length,2);
 assert.equal(writes[0].body,writes[1].body);
 assert.equal(JSON.parse(writes[1].body).request_id,original.request_id);
 assert.equal(controller.pending,null);
});

test('known rejection keeps the original and resumes human editing after absence is checked',async()=>{
 const f=fixture(),controller=f.make();
 await preview(controller);f.mode.failWrite=409;
 await controller.submit();
 const nonce=controller.pending.request_id;
 assert.equal(controller.pendingRejected.status,409);
 await controller.releaseRejected();
 assert.equal(controller.pending,null);
 assert.ok(f.storage.getItem(controller.key+':original:'+nonce));
 controller.updateDraft({rationale:'保留原件后修订人工理由'});
 assert.equal(controller.draft.rationale,'保留原件后修订人工理由');
 assert.equal(controller.preview,null);
});

test('wrong receipt actor or missing persisted original cannot clear the write obligation',async()=>{
 const f=fixture(),controller=f.make();
 await preview(controller);f.mode.badReceipt={actor_id:id(99)};
 await controller.submit();
 const original=controller.pending;
 assert.ok(original);
 f.storage.removeItem(controller.key+':original:'+original.request_id);
 assert.equal(controller.guard(),false);
 await assert.rejects(()=>controller.submit());
 assert.equal(f.calls.filter(row=>row.path===base&&row.method==='POST').length,1);
});

test('stored needs-check obligation blocks resend and fresh actions across reload',async()=>{
 const f=fixture(),controller=f.make();
 await preview(controller);f.mode.needsCheck=true;
 await controller.submit();
 assert.equal(controller.needsCheck,true);
 const reopened=f.make();await reopened.open();
 assert.equal(reopened.needsCheck,true);
 await reopened.resendOriginal();
 assert.equal(f.calls.filter(row=>row.path===base&&row.method==='POST').length,1);
});

test('a broken local draft blocks transport and the synchronous leave guard',async()=>{
 const f=fixture(),controller=f.make();
 await preview(controller);
 f.storage.broken=true;
 controller.updateDraft({rationale:'需要保留的人工作业'});
 assert.equal(controller.guard(),false);
 await assert.rejects(()=>controller.preparePreview());
 assert.equal(f.calls.filter(row=>row.method==='POST').length,1);
 f.storage.broken=false;controller.retryLocalSave();
 assert.equal(controller.guard(),true);
 assert.equal(controller.draft.rationale,'需要保留的人工作业');
});

test('explicit role permissions and an independent data receipt gate review and cancel',async()=>{
 const f=fixture(),controller=f.make();
 const output={field:'temperature',operation:'mean',value:21,sample_count:1,missing_count:1};
 f.state.plan=view({phase:'queued',job:{id:id(9),status:'running'},output,output_hash:sha,permissions:{...permissions,can_review:true,can_cancel:true}});
 await controller.open();await controller.select(id(8));
 controller.updateDraft({review_choice:'adopt',review_reason:'核对后采用'});
 await assert.rejects(()=>controller.command('review'));
 await assert.rejects(()=>controller.command('start'));
 controller.updateDraft({cancel_reason:'人工申请停止办理'});
 await controller.command('cancel');
 assert.equal(controller.plan.phase,'cancel_requested');
 f.state.plan={...f.state.plan,job:{id:id(9),status:'succeeded'},data_received:true};
 await controller.refresh();await controller.command('review');
 assert.equal(controller.plan.phase,'adopted');
 assert.equal(JSON.parse(f.calls.find(row=>row.path.endsWith('/review')&&row.method==='POST').body).output_hash,sha);
});

test('revoked grant and protected read denial never open a fresh write',async()=>{
 const f=fixture(),controller=f.make();
 f.state.grants=[{...grant,phase:'revoked',eligible:false}];
 await controller.open();controller.updateDraft({grantId:id(7),rationale:'保留人工说明'});
 await assert.rejects(()=>controller.preparePreview());
 f.mode.contextStatus=403;
 await controller.refresh();
 assert.equal(controller.view().denied,true);
 assert.equal(controller.view().context,null);
 assert.equal(controller.view().draft,null);
 assert.equal(f.calls.filter(row=>row.method==='POST').length,0);
});

test('changed CSRF during a delayed read fences the reply and hides old scope',async()=>{
 const f=fixture(),controller=f.make();
 f.mode.contextGate=gate();
 const opening=controller.open();
 while(!f.calls.some(row=>row.path===base+'/context'))await new Promise(done=>setImmediate(done));
 f.session.setItem('ga-setup-csrf','new-token');
 f.mode.contextGate.resolve();
 await opening;
 assert.equal(controller.view().denied,true);
 assert.equal(controller.view().context,null);
});

async function fillGrant(controller){
 await controller.openGrants();
 controller.updateGrantDraft({toolId:tool.id});
 controller.updateGrantDraft({field:field.key,operation:'mean',reason:'仅汇总本次已核观测'});
}
test('admin grant controls load lazily, persist exact issue body, and refresh actual grant and execution reads',async()=>{
 const f=fixture('admin'),controller=f.make();f.state.grants=[];
 await controller.open();
 assert.equal(f.calls.some(row=>row.path.startsWith(grantBase)),false);
 await fillGrant(controller);
 assert.equal(controller.draft.grantSetup.field,'temperature');
 await controller.issueGrant();
 assert.equal(controller.pending,null);
 assert.equal(controller.plan,null);
 assert.equal(controller.selectedId,null);
 const post=f.calls.find(row=>row.path===grantBase&&row.method==='POST'),body=JSON.parse(post.body);
 assert.deepEqual(body,{request_id:body.request_id,expected_task_version:3,expected_continuation_version:2,revision_hash:sha,source_hash:sha,
  capability_pin:{id:tool.id,content_hash:sha,release_version:'1.0.0'},field:'temperature',operation:'mean',policy_ref:policy.ref,validity_seconds:7200,reason:'仅汇总本次已核观测'});
 assert.ok(controller.grantList.records.some(row=>row.grant_id===id(24)));
 assert.ok(controller.context.grants.some(row=>row.grant_id===id(24)&&row.eligible));
 assert.equal(controller.draft.grantId,null);
 assert.equal(f.calls.filter(row=>row.path===grantBase&&row.method==='GET').length,2);
});

test('unknown grant receipt cannot cross into the execution receipt family and reload reads the grant original only',async()=>{
 const f=fixture('admin'),controller=f.make();
 f.state.plan=view();
 await controller.open();await controller.select(id(8));
 await fillGrant(controller);f.mode.lost=true;
 await controller.issueGrant();
 const pending=controller.pending;
 assert.equal(pending.family,'grant');
 assert.equal(pending.target_id,null);
 f.mode.badReceipt={schema:schema+'receipt-v314',plan_id:id(8)};
 await controller.recover();
 assert.equal(controller.pending.request_id,pending.request_id);
 assert.equal(controller.plan.plan_id,id(8));
 f.mode.badReceipt=null;
 controller.dispose();
 const reopened=f.make();await reopened.open();
 assert.equal(reopened.pending,null);
 assert.equal(reopened.plan.plan_id,id(8));
 assert.equal(reopened.grantOpen,false);
 assert.equal(f.calls.filter(row=>row.path===grantBase&&row.method==='POST').length,1);
 assert.ok(f.calls.some(row=>row.path===grantBase+'/operations/'+pending.request_id));
 assert.equal(f.calls.some(row=>row.path===base+'/operations/'+pending.request_id),false);
});

test('revoke uses the current listed grant version and leaves the original selection unavailable',async()=>{
 const f=fixture('admin'),controller=f.make();await controller.open();await controller.openGrants();
 controller.updateDraft({grantId:id(7),rationale:'原执行理由保留'});
 controller.updateGrantDraft({revoke:{id:id(7),reason:'主动撤回本次只读授权'}});
 await controller.revokeGrant(id(7));
 const post=f.calls.find(row=>row.path===grantBase+'/'+id(7)+'/revoke');
 const body=JSON.parse(post.body);
 assert.equal(body.expected_grant_version,1);
 assert.equal(body.reason,'主动撤回本次只读授权');
 assert.equal(controller.grantList.records.find(row=>row.grant_id===id(7)).phase,'revoked');
 assert.equal(controller.draft.grantId,id(7));
 assert.equal(controller.draft.rationale,'原执行理由保留');
 await assert.rejects(()=>controller.preparePreview());
});

test('operator cannot open or issue a grant even while reading actual available execution grants',async()=>{
 const f=fixture(),controller=f.make();await controller.open();
 await assert.rejects(()=>controller.openGrants());
 await assert.rejects(()=>controller.issueGrant());
 assert.equal(f.calls.some(row=>row.path.startsWith(grantBase)),false);
 assert.equal(controller.context.grants.length,1);
});

test('grant retry after absence uses only its saved canonical body and requires the matching continuation receipt',async()=>{
 const f=fixture('admin'),controller=f.make();await controller.open();await fillGrant(controller);
 f.mode.failWrite=503;await controller.issueGrant();
 const original=controller.pending;
 assert.ok(original);
 f.mode.failWrite=null;f.mode.badReceipt={continuation_id:id(30)};
 await controller.resendOriginal();
 const writes=f.calls.filter(row=>row.path===grantBase&&row.method==='POST');
 assert.equal(writes.length,2);
 assert.equal(writes[0].body,writes[1].body);
 assert.equal(controller.pending.request_id,original.request_id);
 f.mode.badReceipt=null;await controller.recover();
 assert.equal(controller.pending,null);
 assert.equal(f.calls.filter(row=>row.path===grantBase&&row.method==='POST').length,2);
});

test('correction requires an explicit returned execution and cannot reuse its reserved grant',async()=>{
 const f=fixture(),controller=f.make();
 const original=view({phase:'result_returned',job:{id:id(9),status:'succeeded'},
  operations:[{operation:'start',request_id:id(10)}],output:{field:'temperature',operation:'mean',value:21,sample_count:1,missing_count:1},
  output_hash:sha,data_received:true});
 f.state.plan=copy(original);
 f.state.returned=[{plan_id:original.plan_id,phase:'result_returned',label:'结果退回'}];
 f.state.grants.push({...grant,grant_id:id(25),grant_ref:{id:id(25),record_hash:sha}});
 await controller.open();
 await controller.select(original.plan_id);
 controller.updateDraft({grantId:grant.grant_id,rationale:'补充重新核对原证据'});
 assert.equal(controller.draft.supersedesPlanId,null);
 assert.throws(()=>controller.updateDraft({supersedesPlanId:id(99)}));
 controller.updateDraft({supersedesPlanId:original.plan_id});
 await assert.rejects(()=>controller.preparePreview(),/已预留执行/);
 controller.updateDraft({grantId:id(25)});
 await controller.preparePreview();
 assert.equal(controller.preview.supersedes_plan_id,original.plan_id);
 await controller.submit();
 const body=JSON.parse(f.calls.find(row=>row.path===base&&row.method==='POST').body);
 assert.equal(body.supersedes_plan_id,original.plan_id);
 assert.equal(body.grant_ref.id,id(25));
 assert.equal(controller.plan.plan_id,id(31));
 assert.deepEqual(f.state.history,[original]);
});

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let folder;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
test('default Card opens lazily and renders real-controller cancellation, results, and per-role gates without dispatching',{concurrency:false},async()=>{
 const f=fixture();
 f.state.plan=view({phase:'queued',job:{id:id(9),status:'succeeded'},output:{field:'temperature',operation:'mean',value:21,sample_count:1,missing_count:1},
  output_hash:sha,data_received:true,permissions:{...permissions,can_review:true}});
 class NoResources extends ResourceLoader{fetch(){assert.fail('offline Card may not fetch resources');}}
 const dom=new JSDOM('<div id="root"></div>',{url:'https://execution-v314.invalid/',resources:new NoResources()});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,sessionStorage:dom.window.sessionStorage,
  localStorage:dom.window.localStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};
 const previous=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value] of Object.entries(globals))Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
 let root;
 try{
  folder=await mkdtemp(join(tmpdir(),'same-goal-execution-v314-'));
  const reactPath=join(project,'node_modules/react/index.js'),reactURL=pathToFileURL(reactPath).href,priorMode=process.env.NODE_ENV;
  let result;
  try{
   result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),mode:'test',logLevel:'silent',
    define:{'process.env.NODE_ENV':JSON.stringify('development')},
    build:{ssr:join(project,'src/SameGoalExecutionV314.jsx'),write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),
     output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}});
  }finally{if(priorMode===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=priorMode;}
  const file=join(folder,'Card.mjs'),entry=result.output.find(row=>row.type==='chunk'&&row.isEntry);
  await writeFile(file,entry.code);
  const Card=(await import(pathToFileURL(file).href)).default,{createRoot}=await import('react-dom/client');
  const element=document.getElementById('root'),errors=[];
  root=createRoot(element,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error)});
  const button=label=>[...element.querySelectorAll('button')].find(node=>node.textContent===label);
  const until=async predicate=>{for(let n=0;n<100;n++){if(predicate())return;await act(async()=>{await new Promise(done=>setImmediate(done));});}assert.fail('Card state did not arrive');};
  const render=actor=>act(async()=>root.render(React.createElement(Card,{account:actor,task,advicePlan,continuation,requestJSON:f.request,storage:f.storage,tabStorage:f.session,locks:f.locks})));
  await render(account);
  assert.deepEqual(f.calls,[]);
  await act(async()=>button('打开审批与执行').click());
  await until(()=>button('打开办理记录')&&!button('打开办理记录').disabled);
  await act(async()=>button('打开办理记录').click());
  await until(()=>button('保存结果复核决定'));
  assert.match(element.textContent,/独立接收回执已核对/);
  assert.match(element.textContent,/21 ℃/);
  assert.equal(button('开始本次只读汇总'),undefined);
  assert.equal(button('保存本级审批决定'),undefined);
  assert.equal(button('添加本次工具范围'),undefined);
  assert.equal(button('保存结果复核决定').disabled,true);
  const current=element.querySelector('[aria-label="办理进度与处理"]');
  await act(async()=>button('收起审批与执行').click());
  assert.ok(current.closest('[hidden]'));
  const count=f.calls.length;
  await act(async()=>button('打开审批与执行').click());
  assert.equal(element.querySelector('[aria-label="办理进度与处理"]'),current);
  assert.equal(f.calls.length,count);
  f.state.me={...account,role:'admin'};
  await render(f.state.me);
  assert.equal(element.querySelector('[aria-label="办理进度与处理"]'),null);
  await act(async()=>button('打开审批与执行').click());
  await until(()=>button('添加本次工具范围')&&!button('添加本次工具范围').disabled);
  assert.equal(f.calls.some(row=>row.path.startsWith(grantBase)),false);
  await act(async()=>button('添加本次工具范围').click());
  await until(()=>button('登记本次工具范围'));
  assert.match(element.textContent,/当前审批政策第 2 版/);
  assert.match(element.textContent,/已核观测数值汇总/);
  assert.equal(button('登记本次工具范围').disabled,true);
  assert.ok(f.calls.every(row=>row.method==='GET'));
  assert.deepEqual(errors,[]);
 }finally{
  if(root)await act(async()=>root.unmount());
  dom.window.close();
  for(const [key,value] of previous)if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];
 }
});
