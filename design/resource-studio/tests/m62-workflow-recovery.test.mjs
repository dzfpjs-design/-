import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {mount,installEventBrowser} from '../scripts/react-event-harness.mjs';
import {NativeGoalJournal,nativeGoalScope} from '../src/native-goal-request-v132.mjs';
import {cancellationScope,cancellationKey,readCancellation,writeCancellation,cancellationHash,checkedCancellationReceipt} from '../src/workflow-cancel-contract.mjs';
import {workflowTaskRefreshPolicy,readWorkflowNoteRecovery} from '../src/native-step-execution-contract.mjs';

const account={workspace_id:'workspace-a',user_id:'user-a',username:'负责人甲',role:'operator'};
const scope=cancellationScope(account,'task-a');
const body={version:4,reason:' 保留取消理由 ',retained_note:'尚未提交的完整人工说明',request_id:'12345678-1234-4234-9234-123456789abc'};
const finish=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const memory=()=>{const cache=new Map();return {get length(){return cache.size;},key:index=>[...cache.keys()][index]??null,getItem:key=>cache.get(key)??null,setItem:(key,value)=>cache.set(key,String(value)),removeItem:key=>cache.delete(key)};};
const goalJournal=storage=>new NativeGoalJournal({storage,scope:nativeGoalScope(account,'task-a'),legacyKey:'ga-native-goal:'+account.workspace_id+':'+account.username+':task-a'});
const receipt=async(extra={})=>({request_id:body.request_id,task_id:'task-a',saved:true,status:'saved',request_hash:await cancellationHash(body),version:5,cancellation:{actor:account.user_id,reason:body.reason.trim(),retained_note:body.retained_note},...extra});
async function environment(run){
 const previous=Object.fromEntries(['fetch','localStorage','sessionStorage','window','location','history','setInterval','clearInterval'].map(key=>[key,globalThis[key]]));
 const storage=memory(),timers=[];
 Object.assign(globalThis,{localStorage:storage,sessionStorage:memory(),window:{addEventListener(){},removeEventListener(){}},location:{search:'?task=task-a'},history:{replaceState(){}},setInterval:fn=>{timers.push(fn);return timers.length;},clearInterval(){}});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 const components=[];
 try{await run({server,storage,timers,mount:(Component,props)=>{const value=mount(Component,props);components.push(value);return value;}});}
 finally{components.forEach(component=>component.unmount());await server.close();Object.assign(globalThis,previous);}
}

async function workspaceFixture({server,mount},sessionAccount=account,currentRole='operator'){
 const {default:Workspace}=await server.ssrLoadModule('/src/BusinessWorkspace.jsx');
 const step={id:'review',agent:'人工确认',role:'负责人',name:'核对',purpose:'核对原资料',output:'结论',condition:''};
 const business={id:'business-a',name:'核验',flow:{steps:[step,{...step,id:'followup',name:'后续核对'}]}},release={id:'release-a',package_id:'package-a',snapshot:{object_types:[],businesses:[business]}};
 const makeTask=(id,title)=>({id,version:4,status:'workflow_waiting',title,business_name:'核验',instance_release_number:1,business,roles:{负责人:{username:account.username}},object_snapshots:[],workflow_runtime:{current_step_id:'review',drafts:{},receipts:[]},permissions:{current_actor_role:currentRole,can_save_note:currentRole!=='viewer',can_complete_step:currentRole!=='viewer',can_cancel:currentRole!=='viewer',requires_override:currentRole==='admin'}});
 const context={tasks:{'task-a':makeTask('task-a','任务甲'),'task-b':makeTask('task-b','任务乙')},notes:[],completions:[],complete:null};
 globalThis.fetch=async(path,options={})=>{
  const action=path.match(/^\/api\/workspace\/tasks\/([^/]+)\/(note|complete-step)$/);
  if(action){const [,id,kind]=action,sent=JSON.parse(options.body);if(kind==='complete-step'){context.completions.push({id,body:sent});return context.complete(id,sent);}
   context.notes.push({id,body:sent});const old=context.tasks[id];context.tasks[id]={...old,version:old.version+1,workflow_runtime:{...old.workflow_runtime,drafts:{...old.workflow_runtime.drafts,[sent.step_id]:{...sent}}}};return finish(context.tasks[id]);
  }
  if(path==='/api/instances')return finish([{id:'instance-a',status:'partially_active',active_release_id:release.id,enabled_business_ids:[business.id]}]);
  if(path.endsWith('/releases'))return finish([release]);
  if(path==='/api/workspace/instances/instance-a/tasks')return finish(Object.values(context.tasks));
  const selected=path.match(/^\/api\/workspace\/tasks\/([^/]+)$/);if(selected)return finish(context.tasks[selected[1]]);
  if(path.includes('goal-tool-options'))return finish({detail:'无本机工具'},409);return finish([]);
 };
 context.component=mount(Workspace,{instanceId:'instance-a',account:sessionAccount,Btn:'button',Field:'label'});await context.component.flush();return context;
}

test('cancel journal freezes original body and verifies exact actor hash and saved tuple',async()=>{
 const storage=memory(),state=readCancellation(storage,scope);
 writeCancellation(storage,scope,{...state,reason:body.reason,pending:{body}});
 assert.deepEqual(readCancellation(storage,scope).pending.body,body);
 assert.throws(()=>writeCancellation(storage,scope,{...readCancellation(storage,scope),pending:{body:{...body,reason:'换体'}}}),/不能更换/);
 assert.throws(()=>writeCancellation(storage,scope,{...readCancellation(storage,scope),pending:null}),/尚未保留/);
 await checkedCancellationReceipt(await receipt(),{body},scope);
 await assert.rejects(()=>checkedCancellationReceipt(null,{body},scope));
 const saved=await receipt();
 for(const patch of [{request_hash:'0'.repeat(64)},{cancellation:{...saved.cancellation,actor:'other-user'}},{cancellation:{...saved.cancellation,retained_note:'另一份说明'}},{request_id:'other-id'}])await assert.rejects(()=>checkedCancellationReceipt({...saved,...patch},{body},scope));
 assert.notEqual(cancellationKey(scope),cancellationKey({...scope,user_id:'other-user'}));
});

test('cancel draft survives reload before submission and storage failure sends nothing',async()=>environment(async({server,storage,mount})=>{
 const {default:Cancellation}=await server.ssrLoadModule('/src/WorkflowCancellation.jsx');
 let posts=0;globalThis.fetch=async()=>{posts++;throw Error('must not send');};
 const props={task:{id:'task-a',version:4,status:'workflow_waiting'},account,Btn:'button',open:true};
 let component=mount(Cancellation,props);await component.flush();await component.input('业务任务取消原因','刷新后仍保留的原因');
 component.unmount();component=mount(Cancellation,props);await component.flush();assert.equal(component.byAria('业务任务取消原因').props.value,'刷新后仍保留的原因');
 storage.setItem=()=>{throw Error('quota');};await component.click('确认取消此任务');assert.equal(posts,0);
}));

test('lost cancellation response recovers own exact UUID without duplicate POST after reload',async()=>environment(async({server,storage,mount})=>{
 const {default:Cancellation}=await server.ssrLoadModule('/src/WorkflowCancellation.jsx');
 let sent,posts=0,saved=0,preserved=0;
 globalThis.fetch=async(path,options={})=>{
  if(options.method==='POST'){posts++;sent=JSON.parse(options.body);throw Error('response lost after commit');}
  assert.ok(path.endsWith('/cancel-requests/'+sent.request_id));return finish({request_id:sent.request_id,task_id:'task-a',saved:true,status:'saved',request_hash:await cancellationHash(sent),version:5,cancellation:{actor:account.user_id,reason:sent.reason.trim(),retained_note:sent.retained_note}});
 };
 const props={task:{id:'task-a',version:4,status:'workflow_waiting'},account,Btn:'button',open:true,retainedNote:body.retained_note,onBeforeSend:()=>preserved++,onSaved:()=>saved++};
 let component=mount(Cancellation,props);await component.flush();await component.input('业务任务取消原因',body.reason);await component.click('确认取消此任务');
 assert.equal(posts,1);assert.equal(preserved,1);assert.equal(saved,0);assert.equal(readCancellation(storage,scope).pending.body.reason,body.reason);
 component.unmount();component=mount(Cancellation,props);await component.flush();assert.equal(component.byAria('业务任务取消原因').props.disabled,true);
 await component.click('核对原取消请求是否已保存');assert.equal(posts,1);assert.equal(saved,1);assert.equal(readCancellation(storage,scope).pending,null);assert.deepEqual(readCancellation(storage,scope).history[0].body,sent);
}));

test('another actor cancellation never acknowledges this users unknown request',async()=>environment(async({server,storage,mount})=>{
 const {default:Cancellation}=await server.ssrLoadModule('/src/WorkflowCancellation.jsx');
 writeCancellation(storage,scope,{...readCancellation(storage,scope),reason:body.reason,pending:{body}});
 let saved=0,posts=0;
 globalThis.fetch=async(_,options={})=>{if(options.method==='POST')posts++;return finish({request_id:body.request_id,task_id:'task-a',saved:false,status:'unknown',request_hash:null,version:6,cancellation:null});};
 const component=mount(Cancellation,{task:{id:'task-a',version:6,status:'workflow_cancelled',permissions:{can_cancel:false}},account,Btn:'button',open:false,onSaved:()=>saved++});
 await component.flush();await component.click('核对原取消请求是否已保存');assert.equal(saved,0);assert.equal(posts,0);assert.deepEqual(readCancellation(storage,scope).pending.body,body);assert.equal(component.button('按原编号继续取消请求').props.disabled,true);
}));

test('unknown cancellation keeps its frozen body until explicit version review and a new confirmation',async()=>environment(async({server,storage,mount})=>{
 const {default:Cancellation}=await server.ssrLoadModule('/src/WorkflowCancellation.jsx');
 writeCancellation(storage,scope,{...readCancellation(storage,scope),reason:body.reason,pending:{body}});
 const task={id:'task-a',version:4,status:'workflow_waiting'};let probes=0,posts=0,sent;
 globalThis.fetch=async(_,options={})=>{
  if(options.method==='POST'){posts++;sent=JSON.parse(options.body);return finish({cancel_request:{request_id:sent.request_id,task_id:task.id,saved:true,status:'saved',request_hash:await cancellationHash(sent),version:9,cancellation:{actor:account.user_id,reason:sent.reason.trim(),retained_note:sent.retained_note}}});}
  probes++;return finish({request_id:body.request_id,task_id:task.id,saved:false,status:'unknown',request_hash:null,version:6,task_status:'workflow_waiting',cancellation:null});
 };
 const component=mount(Cancellation,{task,account,Btn:'button',open:true,retainedNote:body.retained_note,onSaved:()=>{task.version=6;}});await component.flush();
 await component.click('核对原取消请求是否已保存');assert.equal(posts,0);assert.deepEqual(readCancellation(storage,scope).pending.body,body);
 await component.click('保留旧请求，重新核对当前版本');assert.equal(probes,2);assert.equal(posts,0);assert.equal(readCancellation(storage,scope).pending,null);assert.deepEqual(readCancellation(storage,scope).history[0].body,body);
 await component.click('确认取消此任务');assert.equal(posts,1);assert.equal(sent.version,6);assert.notEqual(sent.request_id,body.request_id);assert.equal(sent.reason,body.reason);assert.equal(readCancellation(storage,scope).history.length,2);assert.equal(readCancellation(storage,scope).pending,null);
}));

test('late cancellation response cannot populate another task or clear its original journal',async()=>environment(async({server,storage,mount})=>{
 const {default:Cancellation}=await server.ssrLoadModule('/src/WorkflowCancellation.jsx');
 let resolvePost,sent;
 const response=new Promise(resolve=>resolvePost=resolve);
 globalThis.fetch=async(_,options={})=>{sent=JSON.parse(options.body);return response;};
 const task={id:'task-a',version:4,status:'workflow_waiting'},props={task,account,Btn:'button',open:true,retainedNote:body.retained_note};
 const component=mount(Cancellation,props);await component.flush();await component.input('业务任务取消原因','A原理由');
 const posting=component.click('确认取消此任务');await new Promise(resolve=>setImmediate(resolve));
 task.id='task-b';component.render();await component.flush();assert.equal(component.byAria('业务任务取消原因').props.value,'');
 resolvePost(finish({cancel_request:{request_id:sent.request_id,task_id:'task-a',saved:true,status:'saved',request_hash:await cancellationHash(sent),version:5,cancellation:{actor:account.user_id,reason:sent.reason,retained_note:sent.retained_note}}}));
 await posting;assert.equal(component.byAria('业务任务取消原因').props.value,'');assert.ok(readCancellation(storage,scope).pending);assert.equal(readCancellation(storage,cancellationScope(account,'task-b')).pending,null);
}));

test('goal stop remains available with dirty or pending human work and preserves it first',async()=>environment(async({server,storage,mount})=>{
 const browser=installEventBrowser({account,storage});let component;try{
 const {default:Goal}=await server.ssrLoadModule('/src/NativeGoalStudio.jsx');
 let preserved=0,posts=0,changed=0;
 let goal={task_id:'task-a',version:4,can_manage:true,can_stop:true,goal_confirmed:true,goal:{purpose:'原目标',required_fields:[],criteria:[],observation_mode:'existing_only'},fields:{},query_tools:[],goal_tools:[],scope_error:'原业务已暂停',acceptance:null,cancelled:null,check:null};
 globalThis.fetch=async(path,options={})=>{
  assert.equal(options.headers['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers['X-CSRF-Token'],'synthetic-event-csrf');if(path==='/api/me')return finish(account);
  if(options.method==='POST'){assert.ok(path.endsWith('/goal/stop'));assert.equal(preserved,1);posts++;const sent=JSON.parse(options.body);goal={...goal,version:5,cancelled:{reason:sent.reason}};}
  return finish(goal);
 };
 component=mount(Goal,{task:{id:'task-a',title:'原任务'},account,Btn:'button',locked:true,stopLocked:false,onBeforeStop:()=>preserved++,onChanged:()=>changed++});
 await component.flush();assert.equal(component.button('停止目标协调').props.disabled,false);assert.equal(component.button('按原要求核对目标').props.disabled,true);
 await component.input('业务目标操作理由','停止后续协调，保留未提交人稿');await component.click('停止目标协调');assert.equal(posts,1);assert.equal(changed,1);assert.equal(goalJournal(storage).pending(),null);component.unmount();
 }finally{component?.unmount();browser.restore();}
}));

test('saved goal stop with lost response can be probed after reload while human notes remain dirty',async()=>environment(async({server,storage,mount})=>{
 const browser=installEventBrowser({account,storage});let component;try{
 const {default:Goal}=await server.ssrLoadModule('/src/NativeGoalStudio.jsx');
 let goal={task_id:'task-a',version:4,can_manage:true,can_stop:true,goal_confirmed:true,goal:{purpose:'原目标',required_fields:[],criteria:[],observation_mode:'existing_only'},fields:{},query_tools:[],goal_tools:[],acceptance:null,cancelled:null,check:null};
 let original,stops=0,probes=0,changed=0;
 globalThis.fetch=async(path,options={})=>{
  assert.equal(options.headers['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers['X-CSRF-Token'],'synthetic-event-csrf');if(path==='/api/me')return finish(account);
  if(path.endsWith('/goal/stop')){stops++;original=JSON.parse(options.body);goal={...goal,version:6,can_stop:false,cancelled:{reason:original.reason}};throw Error('lost after stop saved');}
  if(path.endsWith('/requests/recover')){const sent=JSON.parse(options.body);assert.equal(sent.operation,'stop');assert.equal(sent.action,'probe');assert.deepEqual(sent.original_request,original);probes++;return finish({saved:true,abandoned:false,version:6});}
  return finish(goal);
 };
 const props={task:{id:'task-a',title:'原任务'},account,Btn:'button',locked:true,stopLocked:false,onBeforeStop:()=>{},onChanged:()=>changed++};
 component=mount(Goal,props);await component.flush();await component.input('业务目标操作理由','停止后继续保留原说明');await component.click('停止目标协调');assert.equal(stops,1);const journal=goalJournal(storage),raw=storage.getItem(journal.outboxKey);assert.deepEqual(journal.pending().body,original);assert.equal(journal.pending().action,'stop');component.unmount();
 component=mount(Goal,props);await component.flush();assert.equal(storage.getItem(journal.outboxKey),raw);assert.equal(component.button('核对原请求是否已保存').props.disabled,false);await component.click('核对原请求是否已保存');assert.equal(stops,1);assert.equal(probes,1);assert.equal(changed,1);assert.equal(journal.pending(),null);const working=browser.sessionStorage.getItem(journal.pointerKey);assert.equal(JSON.parse(storage.getItem(working)).form.reason,'停止后继续保留原说明');component.unmount();
 }finally{component?.unmount();browser.restore();}
}));

test('waiting-task refresh policy keeps draft and request while terminal cancellation archives them',()=>{
 const current={task:{id:'task-a',version:4,status:'workflow_waiting',workflow_runtime:{current_step_id:'review'}},dirty:true,note:'本页新输入',pendingAction:{request_id:'original-completion'}};
 const incoming={...current.task,version:5,permissions:{can_complete_step:false},native_execution_blocked:true};
 assert.deepEqual(workflowTaskRefreshPolicy(current,incoming),{apply:true,archive:false,replaceDraft:false});
 assert.deepEqual(workflowTaskRefreshPolicy(current,{...incoming,status:'workflow_cancelled'}),{apply:true,archive:true,replaceDraft:true});
});

test('selected waiting task refreshes permission changes without replacing dirty note; 403 freezes writes',async()=>environment(async({server,timers,mount})=>{
 const {default:Workspace}=await server.ssrLoadModule('/src/BusinessWorkspace.jsx');
 const step={id:'review',agent:'人工确认',role:'负责人',name:'核对',purpose:'核对原资料',output:'结论',condition:''};
 const business={id:'business-a',name:'核验',flow:{steps:[step]}};
 const release={id:'release-a',package_id:'package-a',snapshot:{object_types:[],businesses:[business]}};
 let denied=false,reads=0;
 let task={id:'task-a',version:4,status:'workflow_waiting',title:'原任务',business_name:'核验',instance_release_number:1,business,roles:{负责人:{username:'负责人甲'}},object_snapshots:[],workflow_runtime:{current_step_id:'review',drafts:{},receipts:[]},permissions:{current_actor_role:'operator',can_save_note:true,can_complete_step:true,can_cancel:true,reason:null}};
 globalThis.fetch=async(path)=>{
  if(path==='/api/instances')return finish([{id:'instance-a',status:'partially_active',active_release_id:release.id,enabled_business_ids:[business.id]}]);
  if(path.endsWith('/releases'))return finish([release]);
  if(path==='/api/workspace/tasks/task-a'){reads++;return denied?finish({detail:'当前工作区执行权限已撤回'},403):finish(task);}
  if(path.includes('goal-tool-options'))return finish({detail:'无本机工具'},409);
  return finish([]);
 };
 const component=mount(Workspace,{instanceId:'instance-a',account,Btn:'button',Field:'label'});await component.flush();await component.flush();
 await component.input('业务步骤处理结果','更新状态也不能丢失的本页说明');
 task={...task,version:5,native_execution_blocked:true,permissions:{...task.permissions,can_complete_step:false,reason:'业务已暂停'}};
 await timers[0]();await component.flush();assert.ok(reads>=2);assert.equal(component.byAria('业务步骤处理结果').props.value,'更新状态也不能丢失的本页说明');assert.equal(component.button('保存结果并继续流程').props.disabled,true);
 task={...task,version:6,workflow_runtime:{...task.workflow_runtime,drafts:{review:{note:'另一处理人已保存的新说明',branch:false}}}};
 await timers[0]();await component.flush();assert.equal(component.byAria('业务步骤处理结果').props.value,'更新状态也不能丢失的本页说明');assert.equal(component.button('保存处理说明').props.disabled,true);
 await component.click('已核对服务器新稿，保留本页说明');assert.equal(component.button('保存处理说明').props.disabled,false);
 denied=true;await timers[0]();await component.flush();assert.equal(component.button('保存处理说明').props.disabled,true);assert.equal(component.byAria('业务步骤处理结果').props.value,'更新状态也不能丢失的本页说明');
}));

for(const action of ['保存处理说明','保存结果并继续流程'])test('late '+action+' receipt cannot acknowledge a newer server draft or bypass its review',async()=>environment(async({server,storage,timers,mount})=>{
 const {default:Workspace}=await server.ssrLoadModule('/src/BusinessWorkspace.jsx');
 const step={id:'review',agent:'人工确认',role:'负责人',name:'核对',purpose:'核对原资料',output:'结论',condition:''};
 const business={id:'business-a',name:'核验',flow:{steps:[step]}},release={id:'release-a',package_id:'package-a',snapshot:{object_types:[],businesses:[business]}};
 let task={id:'task-a',version:4,status:'workflow_waiting',title:'原任务',business_name:'核验',instance_release_number:1,business,roles:{负责人:{username:account.username}},object_snapshots:[],workflow_runtime:{current_step_id:'review',drafts:{},receipts:[]},permissions:{current_actor_role:'operator',can_save_note:true,can_complete_step:true,can_cancel:true}};
 let resolveSave,putBody,completions=0;const saving=new Promise(resolve=>resolveSave=resolve);
 globalThis.fetch=async(path,options={})=>{
  if(path.endsWith('/note')){putBody=JSON.parse(options.body);return saving;}
  if(path.endsWith('/complete-step')){completions++;throw Error('must not complete over remote human draft');}
  if(path==='/api/instances')return finish([{id:'instance-a',status:'partially_active',active_release_id:release.id,enabled_business_ids:[business.id]}]);
  if(path.endsWith('/releases'))return finish([release]);
  if(path==='/api/workspace/tasks/task-a')return finish(task);
  if(path.includes('goal-tool-options'))return finish({detail:'无本机工具'},409);return finish([]);
 };
 const component=mount(Workspace,{instanceId:'instance-a',account,Btn:'button',Field:'label'});await component.flush();await component.input('业务步骤处理结果','本页A完整人稿');
 const pending=component.click(action);await new Promise(resolve=>setImmediate(resolve));assert.equal(putBody.note,'本页A完整人稿');
 const savedA={...task,version:5,workflow_runtime:{...task.workflow_runtime,drafts:{review:{note:putBody.note,branch:null}}}};
 task={...task,version:6,workflow_runtime:{...task.workflow_runtime,drafts:{review:{note:'远端B较新人稿',branch:false}}}};
 await timers[0]();await component.flush();resolveSave(finish(savedA));await pending;
 await timers[0]();await component.flush();assert.equal(completions,0);assert.equal(component.byAria('业务步骤处理结果').props.value,'本页A完整人稿');assert.equal(component.button('保存处理说明').props.disabled,true);
 const cached=JSON.parse(storage.getItem('ga-workflow-note:'+account.workspace_id+':'+account.username+':task-a'));assert.equal(cached.note,'本页A完整人稿');assert.equal(cached.pending,true);assert.ok(component.button('已核对服务器新稿，保留本页说明'));
}));

for(const pendingCompletion of [false,true])test('permission loss archives '+(pendingCompletion?'the original completion':'dirty notes')+' before task switch and restores only this actors local work',async()=>environment(async({server,storage,mount})=>{
 const {default:Workspace}=await server.ssrLoadModule('/src/BusinessWorkspace.jsx');
 const step={id:'review',agent:'人工确认',role:'负责人',name:'核对',purpose:'核对原资料',output:'结论',condition:''};
 const business={id:'business-a',name:'核验',flow:{steps:[step]}},release={id:'release-a',package_id:'package-a',snapshot:{object_types:[],businesses:[business]}};
 const originalAction={version:4,step_id:'review',note:'本页原稿需继续恢复',branch:false,override_reason:'原代处理原因',perception_decision:'uncertain',request_id:'aabbccdd-1234-4234-9234-123456789abc'};
 const noteKey='ga-workflow-note:'+account.workspace_id+':'+account.username+':task-a',archiveKey='ga-workflow-note-recovery:'+JSON.stringify([account.workspace_id,account.user_id,account.username,'task-a']);
 const makeTask=(id,title)=>({id,version:4,status:'workflow_waiting',title,business_name:'核验',instance_release_number:1,business,roles:{负责人:{username:account.username}},object_snapshots:[],workflow_runtime:{current_step_id:'review',drafts:{review:{note:'服务器原说明'}},receipts:[]},permissions:{current_actor_role:'operator',can_save_note:!pendingCompletion,can_complete_step:!pendingCompletion,can_cancel:true}});
 const taskA=makeTask('task-a','任务甲'),taskB=makeTask('task-b','任务乙');let puts=0,readsB=0;
 if(pendingCompletion)storage.setItem(noteKey,JSON.stringify({step_id:'review',note:originalAction.note,pending:true,action:originalAction}));
 writeCancellation(storage,scope,{...readCancellation(storage,scope),reason:body.reason,pending:{body}});const originalCancel=storage.getItem(cancellationKey(scope));
 globalThis.fetch=async(path,options={})=>{
  if(path.endsWith('/note')){puts++;taskA.permissions={...taskA.permissions,can_save_note:false,can_complete_step:false};return finish({detail:'当前处理权限已撤回'},403);}
  if(options.method&&options.method!=='GET')throw Error('navigation must not submit original completion or cancellation');
  if(path==='/api/instances')return finish([{id:'instance-a',status:'partially_active',active_release_id:release.id,enabled_business_ids:[business.id]}]);
  if(path.endsWith('/releases'))return finish([release]);
  if(path==='/api/workspace/tasks/task-a')return finish(taskA);
  if(path==='/api/workspace/tasks/task-b'){readsB++;return finish(taskB);}
  if(path==='/api/workspace/instances/instance-a/tasks')return finish([taskA,taskB]);
  if(path.includes('goal-tool-options'))return finish({detail:'无本机工具'},409);return finish([]);
 };
 const props={instanceId:'instance-a',account,Btn:'button',Field:'label'};let component=mount(Workspace,props);await component.flush();
 if(!pendingCompletion)await component.input('业务步骤处理结果',originalAction.note);
 await component.click('打开任务：任务乙');assert.equal(readsB,0);assert.equal(component.byAria('业务步骤处理结果').props.value,originalAction.note);assert.ok(component.button('保留本机草稿并切换'));
 const setItem=storage.setItem;storage.setItem=(key,value)=>{if(key===archiveKey)throw Error('archive quota');setItem(key,value);};await component.click('保留本机草稿并切换');assert.equal(readsB,0);assert.equal(component.byAria('业务步骤处理结果').props.value,originalAction.note);storage.setItem=setItem;
 await component.click('保留本机草稿并切换');assert.equal(readsB,1);assert.equal(component.byAria('业务步骤处理结果').props.value,'服务器原说明');assert.ok(JSON.stringify(component.render()).includes('尚未认定服务器保存'));
 const archived=readWorkflowNoteRecovery(storage,archiveKey,{...scope});assert.equal(archived.drafts[0].draft.note,originalAction.note);if(pendingCompletion)assert.deepEqual(archived.drafts[0].draft.action,originalAction);assert.throws(()=>readWorkflowNoteRecovery(storage,archiveKey,{...scope,user_id:'another-actor'}));
 await component.click('打开任务：任务甲');assert.equal(component.byAria('业务步骤处理结果').props.value,originalAction.note);assert.equal(component.button('保存处理说明').props.disabled,true);assert.ok(JSON.stringify(component.render()).includes('尚未核实服务器已保存'));if(pendingCompletion)assert.ok(component.button('核对并重试已提交结果'));
 component.unmount();component=mount(Workspace,props);await component.flush();assert.equal(component.byAria('业务步骤处理结果').props.value,originalAction.note);if(pendingCompletion)assert.deepEqual(JSON.parse(storage.getItem(noteKey)).action,originalAction);
 // An active-cache loss does not silently replace the server note with an old
 // archive: the same actor explicitly restores the preserved original draft.
 component.unmount();storage.removeItem(noteKey);component=mount(Workspace,props);await component.flush();assert.equal(component.byAria('业务步骤处理结果').props.value,'服务器原说明');
 await component.click('恢复本机说明到当前步骤（仍未保存）');assert.equal(component.byAria('业务步骤处理结果').props.value,originalAction.note);assert.ok(JSON.stringify(component.render()).includes('尚未认定服务器保存'));const restored=JSON.parse(storage.getItem(noteKey));assert.equal(restored.pending,true);if(pendingCompletion)assert.deepEqual(restored.action,originalAction);
 assert.equal(puts,pendingCompletion?0:1);assert.equal(storage.getItem(cancellationKey(scope)),originalCancel);
}));

for(const outcome of ['success','revisable_failure'])test('late completion '+outcome+' cannot change another tasks newer draft or original pending request',async()=>environment(async env=>{
 const {storage}=env,flow=await workspaceFixture(env),component=flow.component;let releaseA,bodyA;
 flow.complete=async(id,sent)=>{if(id==='task-b')throw Error('B response unknown');bodyA=sent;return new Promise(resolve=>releaseA=resolve);};
 await component.input('业务步骤处理结果','A原完成说明');const finishingA=component.click('保存结果并继续流程');await component.flush();assert.ok(bodyA);assert.equal(component.button('打开任务：任务乙').props.disabled,true);
 // Even an independently invoked navigation callback must retain scope fences;
 // the ordinary button above is also disabled while the request is in flight.
 await component.click('打开任务：任务乙');assert.equal(component.button('保留本机草稿并切换').props.disabled,true);await component.click('保留本机草稿并切换');
 await component.input('业务步骤处理结果','B新的说明和待核请求');await component.click('保存结果并继续流程');const keyB='ga-workflow-note:'+account.workspace_id+':'+account.username+':task-b',savedB=storage.getItem(keyB),pendingB=JSON.parse(savedB).action;assert.notEqual(pendingB.request_id,bodyA.request_id);
 const taskA=flow.tasks['task-a'];releaseA(outcome==='success'?finish({...taskA,version:taskA.version+1,status:'workflow_completed'}):finish({detail:'原A提交校验未通过'},422));await finishingA;
 assert.equal(component.byAria('业务步骤处理结果').props.value,'B新的说明和待核请求');assert.equal(storage.getItem(keyB),savedB);assert.ok(component.button('核对并重试已提交结果'));assert.equal(component.button('打开任务：任务乙').props.disabled,false);
 const originalA=JSON.parse(storage.getItem('ga-workflow-note:'+account.workspace_id+':'+account.username+':task-a'));assert.deepEqual(originalA.action,bodyA);assert.equal(JSON.stringify(component.render()).includes('原A提交校验未通过'),false);
}));

test('late completion cannot clear a newer same-task step draft',async()=>environment(async env=>{
 const flow=await workspaceFixture(env),component=flow.component;let release,sent;
 flow.complete=async(_,body)=>{sent=body;return new Promise(resolve=>release=resolve);};
 await component.input('业务步骤处理结果','旧步骤完整说明');const finishing=component.click('保存结果并继续流程');await component.flush();const original=flow.tasks['task-a'];
 flow.tasks['task-a']={...original,version:original.version+1,workflow_runtime:{...original.workflow_runtime,current_step_id:'followup',drafts:{followup:{note:'新步骤服务器稿'}}}};
 await env.timers[0]();await component.flush();await component.input('业务步骤处理结果','新步骤本机人稿');const key='ga-workflow-note:'+account.workspace_id+':'+account.username+':task-a',before=env.storage.getItem(key);
 release(finish({...original,version:original.version+1,status:'workflow_completed'}));await finishing;assert.equal(component.byAria('业务步骤处理结果').props.value,'新步骤本机人稿');assert.equal(env.storage.getItem(key),before);
 const archiveKey='ga-workflow-note-recovery:'+JSON.stringify([account.workspace_id,account.user_id,account.username,'task-a']);assert.deepEqual(readWorkflowNoteRecovery(env.storage,archiveKey,scope).drafts[0].draft.action,sent);
}));

for(const reread of [false,true])test('completion cache CAS preserves another original request'+(reread?' after same-step readback':''),async()=>environment(async env=>{
 const flow=await workspaceFixture(env),component=flow.component;let release,sent;
 flow.complete=async(_,body)=>{sent=body;return new Promise(resolve=>release=resolve);};await component.input('业务步骤处理结果','A先前说明');const finishing=component.click('保存结果并继续流程');await component.flush();
 const key='ga-workflow-note:'+account.workspace_id+':'+account.username+':task-a',replacement={...sent,note:'另一页面新的原请求',request_id:'00bbccdd-1234-4234-9234-123456789abc'},encoded=JSON.stringify({step_id:sent.step_id,note:replacement.note,pending:true,action:replacement});env.storage.setItem(key,encoded);
 if(reread){await component.click('打开任务：任务甲');await component.click('保留本机草稿并切换');assert.equal(component.byAria('业务步骤处理结果').props.value,replacement.note);}
 release(finish({...flow.tasks['task-a'],version:flow.tasks['task-a'].version+1,status:'workflow_completed'}));await finishing;assert.equal(env.storage.getItem(key),encoded);assert.equal(component.byAria('业务步骤处理结果').props.value,reread?replacement.note:'A先前说明');assert.ok(component.button('核对并重试已提交结果'));
 if(!reread){const calls=flow.completions.length;await component.click('核对并重试已提交结果');assert.equal(flow.completions.length,calls);assert.equal(env.storage.getItem(key),encoded);}
}));

for(const currentRole of ['operator','admin'])test('task actions use current '+currentRole+' permissions after a viewer session is promoted',async()=>environment(async env=>{
 const flow=await workspaceFixture(env,{...account,role:'viewer'},currentRole),component=flow.component;
 await component.input('业务步骤处理结果','使用服务器当前角色保存');assert.equal(!!component.button('保存处理说明').props.disabled,false);assert.equal(!!component.button('保存结果并继续流程').props.disabled,false);
 if(currentRole==='admin')await component.input('管理员代处理原因','当前管理员明确代处理');await component.click('保存处理说明');assert.equal(flow.notes.length,1);if(currentRole==='admin')assert.equal(flow.notes[0].body.override_reason,'当前管理员明确代处理');
}));
