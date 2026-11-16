// Actual controller, hashes and local journal with explicit API/storage IO
// fixtures. These tests do not prove backend permissions, PG or deployment.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {SameGoalContinuationControllerV312,continuationCanonical as canonical,continuationHash as hash,continuationDueAt,continuationDueText,continuationHasContent} from '../src/same-goal-continuation-v312-controller.mjs';
import {createRetainedControllerLifecycle} from '../src/retained-controller-lifecycle.mjs';

const id=n=>'31200000-0000-4000-8000-'+String(n).padStart(12,'0'),sha=n=>String(n).repeat(64),copy=v=>structuredClone(v);
const account={user_id:id(1),workspace_id:id(2),role:'operator'},task={id:id(3),instance_release_id:id(4)},advicePlan={plan_id:id(5)};
const FLAGS={can_dispatch:false,execution_approved:false,goal_completion_verified:false,cloud_send_authorized:false};
const fail=(status,message='明确的合成API拒绝')=>Object.assign(Error(message),{status});
const empty=()=>({route:null,candidate_ref:null,selected_tools:[],purpose:'',work_description:'',expected_result:'',assignee_id:null,due_at:null});
class Memory{constructor(){this.data=new Map();this.fail=false;}get length(){return this.data.size;}key(n){return [...this.data.keys()][n]??null;}getItem(key){if(this.fail)throw Error('本机存储暂不可用');return this.data.get(key)??null;}setItem(key,value){if(this.fail)throw Error('本机存储暂不可用');this.data.set(key,String(value));}removeItem(key){this.data.delete(key);}}
const gate=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
async function until(predicate){for(let n=0;n<200;n++){if(predicate())return;await new Promise(done=>setImmediate(done));}assert.fail('bounded controller state did not arrive');}
async function fixture({storage=new Memory(),session=new Memory(),mode={},initialPlan=null}={}){
 for(const[key,value]of Object.entries({'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id,'ga-setup-csrf':'fixture-csrf'}))session.setItem(key,value);
 const base='/workspace/tasks/'+task.id+'/business-advice/preparations/'+advicePlan.plan_id+'/continuations',calls=[],receipts=new Map();
 const context={schema:'same-goal-continuation-context-v312',task_id:task.id,advice_plan_id:advicePlan.plan_id,task_version:7,source_hash:sha(1),
  source:{origin_kind:'independent_advice',bridge_state:'not_bound',original_graph_hash:sha(2),review:{decision:'accepted',reason:'仅采用所提供摘录的建议',actor_id:account.user_id,reviewed_at:'2026-11-02T12:00:00Z',review_ref:{id:id(7),record_hash:sha(3)}},
   recommendation:{candidate_ref:'candidate_a',reason:'建议先人工核查，未授予执行权限',supporting_fact_refs:[]},data_as_of:'2026-11-02T11:00:00Z',limitations:['仅软件测试记录，未核验现场']},
  candidates:[{candidate_ref:'candidate_a',proposal:'先人工核查',model_review:{suitability:'uncertain',reasoning:'现场资料仍需核实'}}],allowed_routes:['operational_action'],
  tools:[{id:id(8),content_hash:sha(4),release_version:'1.0.0',name:'已发布核查工具',kind:'service',planning_available:true,blocked_reason:null}],assignees:[{id:account.user_id,name:'当前办理人员',role:'operator'},{id:id(9),name:'另一办理人员',role:'operator'}],
  permissions:{can_create:true,can_preview:true},blocked_reasons:[],...FLAGS};
 const state={me:copy(account),context,plans:new Map(),writeCount:0,modelCalls:0};if(initialPlan)state.plans.set(initialPlan.continuation_id,copy(initialPlan));
 let tail=Promise.resolve();const locks={request:async(key,options,run)=>{const previous=tail,next=gate();tail=next.promise;await previous;try{return await run();}finally{next.resolve();}}};
 const makeView=(body,previous=null,withdraw=false)=>{
  const version=(previous?.version||0)+1,revisionVersion=withdraw?previous.revision.version:version,continuation_id=previous?.continuation_id||id(20+state.plans.size);
  const revision=withdraw?previous.revision:{version:revisionVersion,record_hash:sha(5),draft:copy(body.draft),actor_id:account.user_id,saved_at:'2026-11-02T12:10:00Z',reason:body.reason};
  return {schema:'same-goal-continuation-view-v312',task_id:task.id,advice_plan_id:advicePlan.plan_id,continuation_id,version,phase:withdraw?'withdrawn':'draft',source_hash:context.source_hash,source_ref:{advice_plan_id:advicePlan.plan_id,source_hash:context.source_hash},revision,
   history:withdraw?previous.history:[...(previous?.history||[]),{version:revisionVersion,record_hash:revision.record_hash,previous_hash:previous?.revision.record_hash||null,actor_id:account.user_id,saved_at:revision.saved_at,reason:body.reason,draft:copy(revision.draft)}],permissions:{can_revise:!withdraw,can_withdraw:!withdraw},...FLAGS};
 };
 const request=async(path,options={})=>{
  const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;calls.push({path,method,body,raw:options.body,headers:options.headers});
  if(path==='/me'){if(mode.identityError)throw fail(mode.identityError);return copy(state.me);}
  if(method==='GET'){
   if(path===base+'/context'){if(mode.contextGate)await mode.contextGate.promise;if(mode.contextError)throw fail(mode.contextError);return copy(context);}
   if(path===base){if(mode.listGate)await mode.listGate.promise;return {schema:'same-goal-continuation-view-v312/list',task_id:task.id,advice_plan_id:advicePlan.plan_id,records:[...state.plans.values()].map(copy)};}
   if(path.startsWith(base+'/operations/')){if(mode.receiptGate)await mode.receiptGate.promise;if(mode.receiptError)throw fail(mode.receiptError);const row=receipts.get(path.split('/').at(-1));if(!row)throw fail(404);return {...copy(row),...(mode.badReceipt||{})};}
   const row=state.plans.get(path.split('/').at(-1));if(mode.planError)throw fail(mode.planError);if(!row)throw fail(404);return copy(row);
  }
  if(path===base+'/preview'){
   if(mode.previewError)throw fail(mode.previewError);
   const preview={schema:'same-goal-continuation-preview-v312',task_id:task.id,advice_plan_id:advicePlan.plan_id,task_version:body.expected_task_version,source_hash:body.source_hash,
    normalized_draft:copy(body.draft),missing_fields:body.draft.purpose?[]:[{field:'purpose',message:'办理目的尚未填写，可后续完善'}],source_refs:copy(context.source),...FLAGS};
   preview.preview_hash=await hash(canonical(preview),webcrypto);return {...preview,...(mode.badPreview||{})};
  }
  if(mode.writeError)throw fail(mode.writeError);const old=receipts.get(body.request_id);if(old){assert.equal(old.body_hash,await hash(canonical(body),webcrypto));return copy(old.result);}
  const operation=path===base?'create':path.endsWith('/revisions')?'revise':'withdraw',previous=operation==='create'?null:state.plans.get(path.split('/').at(-2));
  const view=makeView(body,previous,operation==='withdraw');state.plans.set(view.continuation_id,copy(view));state.writeCount++;
  const receipt={schema:'same-goal-continuation-receipt-v312',found:true,operation,task_id:task.id,advice_plan_id:advicePlan.plan_id,continuation_id:view.continuation_id,request_id:body.request_id,actor_id:account.user_id,body_hash:await hash(canonical(body),webcrypto),result:mode.needsCheck?null:copy(view),result_status:mode.needsCheck?'needs_check':'available'};
  receipts.set(body.request_id,receipt);if(mode.lostAfterSave)throw Error('网络中断，响应未知');return copy(view);
 };
 const make=(overrides={})=>new SameGoalContinuationControllerV312({account,task,advicePlan,request,storage,session,locks,crypto:webcrypto,...overrides});
 const controller=make();await controller.open();return {controller,make,storage,session,locks,mode,state,calls,receipts,base,makeView};
}
async function input(f){f.controller.updateDraft({work_description:'先核对已批准资料，暂不执行设备。',reason:'先保存供完善的草案'});await f.controller.preparePreview();assert.ok(f.controller.view().preview);}

test('China-time due is explicit, optional and rejects rollover or unqualified dates',()=>{
 assert.equal(continuationDueAt(''),null);assert.equal(continuationDueAt('2026-11-03T09:30'),'2026-11-03T09:30:00+08:00');assert.equal(continuationDueText('2026-11-03T01:30:00Z'),'2026-11-03T09:30');
 for(const value of ['2026-02-30T09:00','2026-11-03T25:00','2026-11-03','09:00'])assert.throws(()=>continuationDueAt(value));
});
test('open only GETs; no recommendation, route, tool or assignee is auto-selected',async()=>{const f=await fixture();assert.deepEqual(f.controller.view().draft.fields,empty());assert.ok(f.calls.every(row=>row.method==='GET'));assert.equal(f.state.modelCalls,0);assert.equal(f.controller.guard(),true);});
test('one human paragraph previews and saves incomplete draft with exact original receipt',async()=>{
 const f=await fixture();await input(f);assert.equal(f.controller.view().preview.missing_fields.length,1);await f.controller.save();const v=f.controller.view();assert.equal(v.plan.phase,'draft');assert.equal(v.pending,null);assert.equal(v.editId,v.plan.continuation_id);assert.equal(f.state.writeCount,1);assert.equal(f.state.modelCalls,0);assert.ok(FLAGS.can_dispatch===false);
 const original=JSON.parse([...f.storage.data].find(([key])=>key.includes(':original:'))[1]);assert.equal(original.body_hash,await hash(canonical(original.body),webcrypto));assert.equal(original.body.draft.work_description,'先核对已批准资料，暂不执行设备。');
});
test('empty content remains blocked while an intentional optional selection counts as human input',async()=>{
 const f=await fixture();f.controller.updateDraft({reason:'保存说明'});await assert.rejects(()=>f.controller.preparePreview());assert.equal(f.calls.filter(row=>row.method==='POST').length,0);assert.equal(continuationHasContent(empty()),false);
 f.controller.updateDraft({route:'operational_action'});await f.controller.preparePreview();assert.equal(f.controller.view().preview.normalized_draft.route,'operational_action');
});
test('editing any field invalidates RAM preview and keeps the complete local human draft',async()=>{const f=await fixture();await input(f);f.controller.updateDraft({expected_result:'有出处的核对结论'});assert.equal(f.controller.view().preview,null);await assert.rejects(()=>f.controller.save());assert.equal(f.controller.guard(),true);});
test('local storage failure prevents even preview POST and preserves entered work in memory',async()=>{const f=await fixture();f.storage.fail=true;f.controller.updateDraft({work_description:'需要保留的人工说明',reason:'保存说明'});await assert.rejects(()=>f.controller.preparePreview());assert.equal(f.calls.filter(row=>row.method==='POST').length,0);assert.equal(f.controller.draft.fields.work_description,'需要保留的人工说明');assert.equal(f.controller.guard(),false);});
test('preview wrong hash or source never becomes a reusable save grant',async()=>{const f=await fixture();f.mode.badPreview={preview_hash:sha(9)};f.controller.updateDraft({purpose:'核对',reason:'保存说明'});await f.controller.preparePreview();assert.equal(f.controller.view().preview,null);assert.equal(f.state.writeCount,0);});
test('lost write response recovers exact nonce with GET, never a second automatic POST',async()=>{
 const f=await fixture();await input(f);f.mode.lostAfterSave=true;await f.controller.save();assert.ok(f.controller.pending);const before=f.calls.filter(row=>row.method==='POST').length;await f.controller.recover();assert.equal(f.controller.pending,null);assert.equal(f.state.writeCount,1);assert.equal(f.calls.filter(row=>row.method==='POST').length,before);
});
test('reload recovers independently inventoried original even without a pending pointer',async()=>{
 const f=await fixture();await input(f);f.mode.lostAfterSave=true;await f.controller.save();const first=f.controller.pending.request_id;f.storage.removeItem(f.controller.key+':pending');f.controller.dispose();const reopened=f.make();await reopened.open();assert.equal(reopened.pending,null);assert.equal(reopened.view().plan.continuation_id,id(20));assert.equal(f.state.writeCount,1);assert.ok(f.calls.some(row=>row.path.endsWith('/operations/'+first)));
});
test('unknown not-found original can only be explicitly resent with the identical full body',async()=>{
 const f=await fixture();await input(f);f.mode.writeError=503;await f.controller.save();const original=copy(f.controller.pending);f.mode.writeError=null;await f.controller.resendOriginal();const attempts=f.calls.filter(row=>row.path===f.base&&row.method==='POST');assert.equal(attempts.length,2);assert.equal(attempts[0].raw,attempts[1].raw);assert.equal(attempts[1].body.request_id,original.request_id);assert.equal(f.state.writeCount,1);
});
test('known rejected original is retained and editing requires a fresh operation404',async()=>{
 const f=await fixture();await input(f);f.mode.writeError=409;await f.controller.save();assert.equal(f.controller.view().pendingRejected.status,409);await f.controller.releaseRejected();assert.equal(f.controller.pending,null);assert.equal(f.controller.preview,null);assert.equal(f.controller.draft.fields.work_description,'先核对已批准资料，暂不执行设备。');assert.ok([...f.storage.data.keys()].some(key=>key.includes(':original:')));
});
test('unknown network failure has no release-as-rejected path',async()=>{const f=await fixture();await input(f);f.mode.writeError=503;await f.controller.save();await assert.rejects(()=>f.controller.releaseRejected());assert.ok(f.controller.pending);});
test('accepted command with damaged result remains needs-check across reopen and blocks replacement',async()=>{
 const f=await fixture();await input(f);f.mode.needsCheck=true;await f.controller.save();assert.equal(f.controller.view().needsCheck,true);assert.ok(f.controller.pending);await assert.rejects(()=>f.controller.save());const next=f.make();await next.open();assert.equal(next.view().needsCheck,true);assert.equal(f.state.writeCount,1);
});
test('wrong actor/body receipt cannot clear the protected original',async()=>{const f=await fixture();await input(f);f.mode.badReceipt={actor_id:id(99)};await f.controller.save();assert.ok(f.controller.pending);assert.equal(f.controller.view().plan,null);assert.equal(f.state.writeCount,1);});
test('history selection is read-only and never silently replaces the current human draft',async()=>{
 const f=await fixture();await input(f);await f.controller.save();const saved=f.controller.plan;f.controller.updateDraft({work_description:'尚未保存的另一段人工输入'});await f.controller.selectContinuation(saved.continuation_id);assert.equal(f.controller.draft.fields.work_description,'尚未保存的另一段人工输入');assert.equal(f.state.writeCount,1);
});
test('continue editing makes a protected new local owner and saves a revision, not a second draft',async()=>{
 const f=await fixture();await input(f);await f.controller.save();const oldOwner=f.controller.owner;f.controller.continueEditing();assert.notEqual(f.controller.owner,oldOwner);assert.ok(f.storage.getItem(f.controller.key+':draft:'+oldOwner));f.controller.updateDraft({expected_result:'新增核对结果',reason:'补充预期结果'});await f.controller.preparePreview();await f.controller.save();assert.equal(f.controller.plan.version,2);assert.equal(f.state.plans.size,1);assert.ok(f.calls.some(row=>row.path.endsWith('/revisions')&&row.body.expected_continuation_version===1));
});
test('assigned operator can preview and revise with can-create false but cannot reassign',async()=>{
 const f=await fixture();await input(f);await f.controller.save();f.state.context.permissions.can_create=false;f.state.context.permissions.can_preview=true;await f.controller.refresh();f.controller.continueEditing();assert.throws(()=>f.controller.updateDraft({assignee_id:id(9)}));f.controller.updateDraft({purpose:'受指派人员补充目的',reason:'完善自己的办理内容'});await f.controller.preparePreview();await f.controller.save();assert.equal(f.controller.plan.version,2);
});
test('withdraw retains saved contents and history without changing the original review',async()=>{
 const f=await fixture();await input(f);await f.controller.save();const original=copy(f.controller.plan.revision),review=copy(f.state.context.source.review);f.controller.updateDraft({withdraw_reason:'暂缓，保留原稿供复查'});await f.controller.withdraw();assert.equal(f.controller.plan.phase,'withdrawn');assert.deepEqual(f.controller.plan.revision,original);assert.deepEqual(f.state.context.source.review,review);assert.equal(f.state.modelCalls,0);
});
test('rejected review cannot create; preview permission is not creation permission',async()=>{const f=await fixture();f.state.context.source.review.decision='rejected';f.state.context.permissions.can_create=false;await f.controller.refresh();f.controller.updateDraft({work_description:'人工说明',reason:'说明'});await f.controller.preparePreview();await assert.rejects(()=>f.controller.save());assert.equal(f.state.writeCount,0);});
test('new controller gets its own writer while duplicated tab drafts remain independently recoverable',async()=>{
 const f=await fixture();f.controller.updateDraft({work_description:'第一个窗口的人工作业',reason:'说明一'});const firstOwner=f.controller.owner,next=f.make();await next.open();assert.notEqual(next.owner,firstOwner);next.updateDraft({work_description:'第二个窗口另写的作业'});assert.equal(JSON.parse(f.storage.getItem(f.controller.key+':draft:'+firstOwner)).draft.fields.work_description,'第一个窗口的人工作业');assert.ok(next.view().localDrafts.length>0);
});
test('local persisted data excludes original recommendation, context and source evidence',async()=>{const f=await fixture();await input(f);await f.controller.save();for(const value of f.storage.data.values()){assert.ok(!value.includes('建议先人工核查，未授予执行权限'));assert.ok(!value.includes('仅软件测试记录，未核验现场'));assert.ok(!value.includes('same-goal-continuation-context-v312'));}});
test('another window sees an unresolved original and cannot issue an independent replacement',async()=>{const f=await fixture();await input(f);f.mode.writeError=503;await f.controller.save();const next=f.make();await next.open();assert.ok(next.pending);assert.throws(()=>next.updateDraft({purpose:'替代'}));assert.equal(f.state.writeCount,0);});
test('401 and protected GET403 immediately hide source and draft; stale late branch cannot revive them',async()=>{
 for(const status of [401,403]){const f=await fixture();f.mode.contextGate=gate();f.mode.contextError=status;const pending=f.controller.refresh();await until(()=>f.calls.filter(row=>row.path.endsWith('/context')).length>=2);f.mode.contextGate.resolve();await pending;assert.equal(f.controller.view().denied,true);assert.equal(f.controller.view().context,null);assert.equal(f.controller.view().draft,null);assert.equal(f.controller.view().list,null);}
});
test('identity/CSRF and disposal fence every late reply without posting',async()=>{
 const f=await fixture();f.mode.contextGate=gate();const refresh=f.controller.refresh();await until(()=>f.controller.reads.context==='loading');f.session.setItem('ga-setup-csrf-user',id(77));assert.equal(f.controller.view().denied,true);f.mode.contextGate.resolve();await refresh;assert.equal(f.controller.view().context,null);assert.ok(f.calls.every(row=>row.method==='GET'));
});
test('slow context does not erase separately read historical draft and all writes remain blocked',async()=>{
 const f=await fixture();await input(f);await f.controller.save();f.mode.contextGate=gate();const refresh=f.controller.refresh();await until(()=>f.controller.view().plan!==null);assert.equal(f.controller.view().loading,true);assert.equal(f.controller.view().context,null);assert.equal(f.controller.view().plan.revision.draft.work_description,'先核对已批准资料，暂不执行设备。');await assert.rejects(()=>f.controller.withdraw());f.mode.contextGate.resolve();await refresh;
});
test('StrictMode lifetime suspends immediately and cannot revive disposed controller',async()=>{const f=await fixture(),deferred=[],lifetime=createRetainedControllerLifecycle({defer:fn=>deferred.push(fn)});const first=lifetime.setup(f.controller,f.controller.key);lifetime.cleanup(first);assert.throws(()=>f.controller.updateDraft({purpose:'过期回调'}));const second=lifetime.setup(f.controller,f.controller.key);assert.ok(second);lifetime.cleanup(second);deferred.forEach(fn=>fn());assert.equal(f.controller.active,false);assert.equal(lifetime.setup(f.controller,f.controller.key),null);});
test('tampered original path or lost original custody blocks resending rather than creating a new request',async()=>{
 const f=await fixture();await input(f);f.mode.writeError=503;await f.controller.save();const originalKey=f.controller.key+':original:'+f.controller.pending.request_id,row=JSON.parse(f.storage.getItem(originalKey));row.path='/unrelated-command';f.storage.setItem(originalKey,canonical(row));assert.equal(f.controller.guard(),false);f.controller.storageChanged();assert.equal(f.controller.view().localSaved,false);assert.equal(f.state.writeCount,0);
});
test('pending pointer with missing original fails closed after reload and cannot issue a fresh command',async()=>{
 const f=await fixture();await input(f);f.mode.writeError=503;await f.controller.save();const nonce=f.controller.pending.request_id;f.storage.removeItem(f.controller.key+':original:'+nonce);const reopened=f.make();await reopened.open();assert.match(reopened.error,/原件缺失/);assert.equal(reopened.guard(),false);assert.equal(f.state.writeCount,0);
});
test('catalogue matches the server bounded1000 entries and refuses overflow instead of silently truncating',async()=>{
 const f=await fixture(),tool=copy(f.state.context.tools[0]),actor=copy(f.state.context.assignees[0]);f.state.context.tools=Array.from({length:1000},(_,n)=>({...tool,id:id(1000+n),name:'真实目录条目 '+n}));f.state.context.assignees=Array.from({length:1000},(_,n)=>({...actor,id:id(3000+n),name:'真实岗位条目 '+n}));await f.controller.refresh();assert.equal(f.controller.view().context.tools.length,1000);assert.equal(f.controller.view().context.assignees.length,1000);
 f.state.context.tools.push({...tool,id:id(9000)});await f.controller.refresh();assert.equal(f.controller.view().context,null);assert.equal(f.controller.view().reads.context,'failed');
});
test('pre-journal busy blocks synchronous leave while pure slow GET can leave after draft readback',async()=>{
 const f=await fixture();await input(f);const held=gate(),entered=gate(),original=f.controller.locks;f.controller.locks={request:async(...args)=>{entered.resolve();await held.promise;return original.request(...args);}};
 const saving=f.controller.save();await entered.promise;assert.equal(f.controller.pending,null);assert.equal(f.controller.guard(),false);f.controller.dispose();held.resolve();await saving;assert.equal(f.state.writeCount,0);
 const second=await fixture();second.mode.contextGate=gate();const reading=second.controller.refresh();await until(()=>second.controller.loading);assert.equal(second.controller.guard(),true);second.controller.dispose();second.mode.contextGate.resolve();await reading;
});
test('dispose in the resolved-identity microtask gap prevents the subsequent POST',async()=>{
 const f=await fixture(),c=f.controller;const original=c._identity.bind(c);let closeOnReturn=false;
 c._identity=async epoch=>{const me=await original(epoch);if(closeOnReturn)queueMicrotask(()=>c.dispose());return me;};
 closeOnReturn=true;await assert.rejects(()=>c._request(f.base+'/preview',{method:'POST',body:canonical({request_id:id(99)})}));assert.equal(f.calls.filter(row=>row.method==='POST').length,0);
});
test('shared request budget rechecks the original lifecycle before a queued POST reaches transport',async()=>{
 const f=await fixture(),hold=gate(),calls=[];
 const request=async(path)=>{calls.push(path);await hold.promise;return {};};
 const first=f.make({request}),second=f.make({request}),third=f.make({request}),queued=f.make({request});
 const paths=['/first','/second','/third'];
 const active=[first.requestAPI(paths[0]),second.requestAPI(paths[1]),third.requestAPI(paths[2])];
 const abandoned=queued.requestAPI('/must-not-send',{method:'POST'});
 try{assert.deepEqual(calls,paths);}
 finally{
  queued.dispose();
  const refused=assert.rejects(abandoned);
  hold.resolve();
  await Promise.all([...active,refused]);
 }
 assert.deepEqual(calls,paths);
});

test('a late saved draft receipt cannot switch away from newly unsaved execution work',async()=>{
 const f=await fixture(),c=f.controller;
 await input(f);await c.save();
 const first=c.selectedId;
 let childSaved=true;
 c.beforeContinuationChange=id=>id!==first||childSaved;
 c.newDraft();
 await input(f);
 f.mode.receiptGate=gate();
 const saving=c.save();
 await until(()=>f.calls.filter(row=>row.path.startsWith(f.base+'/operations/')).length===2);
 childSaved=false;
 f.mode.receiptGate.resolve();
 await saving;
 assert.equal(c.selectedId,first);
 assert.ok(c.pending);
 assert.equal(c.guard(),false);
 assert.throws(()=>c.newDraft());
 assert.throws(()=>c.continueEditing());
 const original=c.pending.request_id;
 await c.recover();
 assert.equal(c.pending.request_id,original);
 childSaved=true;
 await c.recover();
 assert.equal(c.pending,null);
 assert.notEqual(c.selectedId,first);
 assert.equal(f.state.writeCount,2);
});
test('editing only work preserves the original due ISO offset, seconds and fraction byte-for-byte',async()=>{
 const f=await fixture(),due='2026-11-03T18:00:30.123+09:00';f.controller.updateDraft({due_at:due,work_description:'原办理内容',reason:'保存原始期限'});await f.controller.preparePreview();await f.controller.save();f.controller.continueEditing();assert.equal(f.controller.draft.due_text,'2026-11-03T17:00');f.controller.updateDraft({work_description:'只修改工作说明',reason:'期限保持原文'});await f.controller.preparePreview();assert.equal(f.controller.preview.normalized_draft.due_at,due);await f.controller.save();assert.equal(f.controller.plan.revision.draft.due_at,due);
});
test('an explicit due edit, including edit-and-return to the displayed minute, uses new China-time input',async()=>{
 const f=await fixture();f.controller.updateDraft({due_at:'2026-11-03T18:00:30.123+09:00',work_description:'原办理内容',reason:'保存期限'});await f.controller.preparePreview();await f.controller.save();f.controller.continueEditing();f.controller.updateDraft({due_text:'2026-11-03T17:01'});f.controller.updateDraft({due_text:'2026-11-03T17:00',reason:'明确调整为整分'});await f.controller.preparePreview();assert.equal(f.controller.preview.normalized_draft.due_at,'2026-11-03T17:00:00+08:00');
});
test('legacy local draft migration retains untouched original ISO and independently typed due input',async()=>{
 const f=await fixture(),c=f.controller,original='2026-11-03T18:00:30.123+09:00';c.updateDraft({due_at:original,work_description:'需保留的旧稿',reason:'迁移说明'});const key=c.key+':draft:'+c.owner,row=JSON.parse(f.storage.getItem(key));delete row.draft.due_edited;f.storage.setItem(key,canonical(row));const next=f.make();await next.open();assert.equal(next.draft.fields.due_at,original);assert.equal(next.draft.due_edited,false);assert.equal(next._fields().due_at,original);
 const secondKey=next.key+':draft:'+next.owner,second=JSON.parse(f.storage.getItem(secondKey));delete second.draft.due_edited;second.draft.due_text='2026-11-03T17:15';f.storage.setItem(secondKey,canonical(second));const final=f.make();await final.open();assert.equal(final.draft.fields.due_at,original);assert.equal(final._fields().due_at,'2026-11-03T17:15:00+08:00');
});
test('restore historical draft first preserves local work, then revises from current version without rewriting old text or due',async()=>{
 const f=await fixture(),originalDue='2026-11-03T18:00:30.123+09:00';f.controller.updateDraft({purpose:'  原办理目的 😀  ',work_description:'保留CRLF\r\n与空格  ',expected_result:'有出处的结果',due_at:originalDue,reason:'第一版人工保存说明'});await f.controller.preparePreview();await f.controller.save();
 f.controller.continueEditing();f.controller.updateDraft({work_description:'第二版内容',reason:'第二版人工说明'});await f.controller.preparePreview();await f.controller.save();const before=copy(f.controller.plan),oldOwner=f.controller.owner;
 f.controller.updateDraft({work_description:'尚未提交的本机内容'});f.controller.restoreRevision(1);assert.equal(JSON.parse(f.storage.getItem(f.controller.key+':draft:'+oldOwner)).draft.fields.work_description,'尚未提交的本机内容');assert.deepEqual(f.controller.draft.fields,before.history[0].draft);assert.equal(f.controller.draft.reason,'第一版人工保存说明');assert.equal(f.controller.draft.due_edited,false);assert.equal(f.controller.plan.version,2);
 f.controller.updateDraft({reason:'恢复原文字段，按当前版本新增修订'});await f.controller.preparePreview();await f.controller.save();assert.equal(f.controller.plan.version,3);assert.equal(f.controller.plan.revision.draft.due_at,originalDue);assert.equal(f.controller.plan.revision.draft.work_description,'保留CRLF\r\n与空格  ');assert.deepEqual(f.controller.plan.history.slice(0,2),before.history);assert.equal(f.calls.filter(row=>row.path.endsWith('/revisions')).at(-1).body.expected_continuation_version,2);
});
test('withdrawn historical draft can only be copied as a new draft with current create permission',async()=>{
 const f=await fixture();await input(f);await f.controller.save();f.controller.updateDraft({withdraw_reason:'保留原稿，暂缓此方案'});await f.controller.withdraw();const withdrawn=copy(f.controller.plan),identity=withdrawn.continuation_id;
 f.state.context.permissions.can_create=false;await f.controller.refresh();assert.throws(()=>f.controller.restoreRevision(1));f.state.context.permissions.can_create=true;await f.controller.refresh();f.controller.restoreRevision(1);assert.equal(f.controller.editId,null);assert.equal(f.controller.plan.phase,'withdrawn');f.controller.updateDraft({reason:'另存一份草案，原撤回记录不改'});await f.controller.preparePreview();await f.controller.save();assert.notEqual(f.controller.plan.continuation_id,identity);assert.equal(f.controller.plan.phase,'draft');assert.deepEqual(f.state.plans.get(identity),withdrawn);
});
test('assigned operator cannot restore another old assignee and can restore its own version without reassignment',async()=>{
 const f=await fixture();f.controller.updateDraft({work_description:'最初交由另一人',assignee_id:id(9),reason:'原分工'});await f.controller.preparePreview();await f.controller.save();f.controller.continueEditing();f.controller.updateDraft({assignee_id:account.user_id,reason:'业务负责人明确调整分工'});await f.controller.preparePreview();await f.controller.save();f.state.context.permissions.can_create=false;await f.controller.refresh();const before=copy(f.controller.draft),owner=f.controller.owner;
 assert.throws(()=>f.controller.restoreRevision(1),/其他办理人/);assert.deepEqual(f.controller.draft,before);assert.equal(f.controller.owner,owner);f.controller.restoreRevision(2);assert.equal(f.controller.draft.fields.assignee_id,account.user_id);assert.equal(f.controller.editId,f.controller.plan.continuation_id);
});
test('complete durable pending can leave and recover, but damaged receipt custody or persistence cannot',async()=>{
 const f=await fixture();await input(f);f.mode.writeError=503;await f.controller.save();assert.ok(f.controller.pending);assert.equal(f.controller.busy,false);assert.equal(f.controller.guard(),true);assert.throws(()=>f.controller.updateDraft({purpose:'替代未知申请'}));
 f.storage.fail=true;assert.equal(f.controller.guard(),false);f.storage.fail=false;
 const second=await fixture();await input(second);second.mode.needsCheck=true;await second.controller.save();const nonce=second.controller.pending.request_id,key=second.controller.key+':settled:'+nonce,row=JSON.parse(second.storage.getItem(key));row.receipt=null;second.storage.setItem(key,canonical(row));assert.equal(second.controller.guard(),false);assert.equal(second.state.writeCount,1);
});
test('one work paragraph needs no extra audit prose; exact original create and revise bodies record explicit system actions',async()=>{
 const f=await fixture();f.controller.updateDraft({work_description:'先把已批准资料核对清楚，再补办理细节。'});await f.controller.preparePreview();assert.ok(f.controller.preview);await f.controller.save();assert.equal(f.controller.plan.revision.reason,'系统记录：保存办理草案，尚未审批或执行');assert.equal(f.controller.draft.reason,'');
 f.controller.continueEditing();f.controller.updateDraft({work_description:'补充一项核对细节，不调用设备。'});await f.controller.preparePreview();await f.controller.save();assert.equal(f.controller.plan.revision.reason,'系统记录：保存草案新版本，原来源保留');
 const originals=[...f.storage.data].filter(([key])=>key.includes(':original:')).map(([,value])=>JSON.parse(value));assert.equal(originals.find(row=>row.operation==='create').body.reason,'系统记录：保存办理草案，尚未审批或执行');assert.equal(originals.find(row=>row.operation==='revise').body.reason,'系统记录：保存草案新版本，原来源保留');assert.equal(f.state.modelCalls,0);assert.equal(f.controller.plan.can_dispatch,false);
});
test('nonempty human save reason keeps whitespace and line breaks verbatim instead of replacing it with the system action',async()=>{
 const f=await fixture(),reason='  人工说明\r\n保留空格与表情 😀  ';f.controller.updateDraft({work_description:'需要保存的办理说明',reason});await f.controller.preparePreview();await f.controller.save();assert.equal(f.controller.plan.revision.reason,reason);const body=f.calls.find(row=>row.path===f.base&&row.method==='POST').body;assert.equal(body.reason,reason);assert.equal(f.controller.draft.reason,reason);
});
