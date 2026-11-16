import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'vite';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {mount,installEventBrowser} from '../scripts/react-event-harness.mjs';
import {abilityScope,abilityKey,readAbility,writeAbility,abilityHash,checkedAbilityReceipt} from '../src/workflow-ability-contract.mjs';
const account={workspace_id:'workspace-a',user_id:'user-a',username:'负责人甲',role:'viewer'};
const step={id:'calculate',name:'核算资料',purpose:'按已确认输入核算',execution:{adapter:'builtin',capability_name:'本机核算'}};
const task=(patch={})=>({id:'task-a',version:4,status:'workflow_waiting',workflow_runtime:{receipts:[],current_step_id:step.id},permissions:{current_actor_role:'operator',can_run_ability:true,can_retry_ability:false,can_probe_ability:true,can_cancel:true},...patch});
const body={version:4,step_id:step.id,request_id:'12345678-1234-4234-9234-123456789abc',retry:false};
const scope=abilityScope(account,task(),step),pending={body};
const memory=()=>{const cache=new Map();return {get length(){return cache.size;},key:index=>[...cache.keys()][index]??null,getItem:key=>cache.get(key)??null,setItem:(key,value)=>cache.set(key,value),removeItem:key=>cache.delete(key)};};
const finish=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const receipt=async(sent=body,patch={})=>({request_id:sent.request_id,task_id:'task-a',actor_id:account.user_id,saved:true,status:'saved',request_hash:await abilityHash(sent),step_id:step.id,visit:0,result_version:sent.version+1,task_version:sent.version+1,task_status:'workflow_queued',current_step_id:step.id,current_visit:0,...patch});
const unknown=(sent=body,patch={})=>({request_id:sent.request_id,task_id:'task-a',actor_id:account.user_id,saved:false,status:'unknown',request_hash:null,step_id:null,visit:null,result_version:null,task_version:4,task_status:'workflow_waiting',current_step_id:step.id,current_visit:0,...patch});
function put(storage,targetScope=scope,targetBody=body){const old=readAbility(storage,targetScope);return writeAbility(storage,targetScope,{...old,pending:{body:targetBody}},old);}
async function environment(run){
 const previous=Object.fromEntries(['fetch','localStorage','sessionStorage'].map(key=>[key,globalThis[key]])),storage=memory(),components=[];
 globalThis.localStorage=storage;globalThis.sessionStorage=memory();
 const server=await createServer({configFile:false,cacheDir:'.m63-validation/vite-cache',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{const {default:Automatic}=await server.ssrLoadModule('/src/WorkflowAutomaticStep.jsx');await run({server,storage,Automatic,mount:(props={})=>{for(const [key,value] of Object.entries({task:task(),step,account,Btn:'button'}))if(!(key in props))props[key]=value;const value=mount(Automatic,props);components.push(value);return value;}});}
 finally{components.forEach(component=>component.unmount());await server.close();Object.assign(globalThis,previous);}
}
test('ability scope separates actors/tasks/steps/visits while role changes preserve own request',()=>{
 assert.deepEqual(abilityScope({...account,role:'admin'},task(),step),scope);
 for(const changed of [{...scope,user_id:'other'}, {...scope,task_id:'other'}, {...scope,step_id:'other'}, {...scope,visit:1}])assert.notEqual(abilityKey(scope),abilityKey(changed));
 assert.throws(()=>abilityScope({...account,user_id:''},task(),step));
});
test('full frozen wire and CAS/readback protect original identity; hash deliberately excludes version',async()=>{
 const storage=memory(),old=put(storage);
 assert.deepEqual(readAbility(storage,scope).pending,pending);
 assert.throws(()=>writeAbility(storage,scope,{...old,pending:{body:{...body,version:6}}},old),/不能更换/);
 assert.throws(()=>writeAbility(storage,scope,{...old,pending:null},old),/尚未保留/);
 const external={...old,history:[{body:{...body,request_id:'12345678-1234-4234-9234-123456789aaa'},status:'saved',minimum_version:5}]};storage.setItem(abilityKey(scope),JSON.stringify(external));
 assert.throws(()=>writeAbility(storage,scope,{...old,pending:null,history:[{body,status:'saved',minimum_version:5}]},old),/已变化/);
 assert.equal(await abilityHash(body),createHash('sha256').update(JSON.stringify({request_id:body.request_id,retry:false,step_id:step.id})).digest('hex'));
 assert.equal(await abilityHash(body),await abilityHash({...body,version:30}));
 const bad=memory();bad.setItem=()=>{};assert.throws(()=>put(bad),/未完整读回/);
});
test('typed receipts require exact actor/request/hash/step/visit, legacy missing proof stays explicit',async()=>{
 await checkedAbilityReceipt(await receipt(),pending,scope);
 for(const patch of [{actor_id:'other'},{task_id:'other'},{request_id:'other'},{request_hash:'f'.repeat(64)},{step_id:'other'},{visit:1},{result_version:4},{visit:undefined},{step_id:null},{visit:null}])await assert.rejects(()=>receipt(body,patch).then(value=>checkedAbilityReceipt(value,pending,scope)));
 await checkedAbilityReceipt(await receipt(body,{step_id:null,result_version:null,visit:null}),pending,scope);
 await checkedAbilityReceipt(unknown(),pending,scope);
 await assert.rejects(()=>checkedAbilityReceipt(unknown(body,{request_hash:'f'.repeat(64)}),pending,scope));
});
test('unreadable/misattributed local records block sending without deleting them',async()=>environment(async({storage,mount})=>{
 let posts=0,refreshes=0;globalThis.fetch=async()=>{posts++;return finish({});};
 storage.setItem(abilityKey(scope),'{broken');const before=storage.getItem(abilityKey(scope));const component=mount({onRefresh:()=>refreshes++});await component.flush();assert.equal(component.button('确认并执行此步骤').props.disabled,true);await component.click('刷新处理进度和权限');assert.equal(refreshes,1);assert.equal(posts,0);assert.equal(storage.getItem(abilityKey(scope)),before);
}));
test('storage quota failure prevents POST and old actorless request blocks fresh send',async()=>environment(async({storage,mount})=>{
 let posts=0;globalThis.fetch=async()=>{posts++;return finish({});};
 const component=mount();await component.flush();const save=storage.setItem;storage.setItem=()=>{throw Error('quota');};await component.click('确认并执行此步骤');assert.equal(posts,0);component.unmount();storage.setItem=save;
 const oldKey='ga-workflow-ability:workspace-a:task-a:calculate:0';storage.setItem(oldKey,JSON.stringify(body));const legacy=mount();await legacy.flush();assert.equal(legacy.button('确认并执行此步骤').props.disabled,true);assert.equal(storage.getItem(oldKey),JSON.stringify(body));
}));
test('lost POST recovers own saved UUID after remount without duplicate send',async()=>environment(async({storage,mount})=>{
 let sent,posts=0,refreshes=0;globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){posts++;sent=JSON.parse(options.body);throw Error('response lost after commit');}assert.ok(path.endsWith('/ability-requests/'+sent.request_id));return finish(await receipt(sent));};
 let component=mount({onRefresh:()=>refreshes++});await component.flush();await component.click('确认并执行此步骤');assert.equal(posts,1);assert.deepEqual(readAbility(storage,scope).pending.body,sent);component.unmount();
 component=mount({onRefresh:()=>refreshes++});await component.flush();await component.click('核对原能力请求是否已保存');assert.equal(posts,1);assert.equal(refreshes,1);assert.equal(readAbility(storage,scope).pending,null);assert.deepEqual(readAbility(storage,scope).history[0].body,sent);assert.equal(component.button('确认并执行此步骤').props.disabled,true,'stale task cannot start another request');
}));
test('unknown same-version is never success; replay uses full original body despite changed retry state',async()=>environment(async({storage,mount})=>{
 put(storage);let sent,posts=0;globalThis.fetch=async(_,options={})=>{if(options.method==='POST'){posts++;sent=JSON.parse(options.body);throw Error('still unknown');}return finish(unknown());};
 const component=mount();await component.flush();await component.click('核对原能力请求是否已保存');assert.equal(readAbility(storage,scope).pending.body.request_id,body.request_id);assert.throws(()=>component.button('保留旧请求，重新核对当前版本'));await component.click('按原编号继续能力请求');assert.equal(posts,1);assert.deepEqual(sent,body);
}));
test('unknown request needs a second newer-version probe and explicit new confirmation',async()=>environment(async({storage,mount})=>{
 put(storage);let probes=0,posts=0,sent;const currentTask=task();globalThis.fetch=async(_,options={})=>{if(options.method==='POST'){posts++;sent=JSON.parse(options.body);return finish({ability_request:await receipt(sent)});}probes++;return finish(unknown(body,{task_version:7}));};
 const props={task:currentTask,onRefresh:()=>{currentTask.version=7;}};const component=mount(props);await component.flush();await component.click('核对原能力请求是否已保存');assert.equal(posts,0);await component.click('保留旧请求，重新核对当前版本');assert.equal(probes,2);assert.equal(posts,0);assert.deepEqual(readAbility(storage,scope).history[0].body,body);assert.equal(readAbility(storage,scope).history[0].status,'version_superseded');await component.click('确认并执行此步骤');assert.equal(posts,1);assert.equal(sent.version,7);assert.notEqual(sent.request_id,body.request_id);
}));
test('another actor or changed visit never substitutes success or offers a fresh attempt',async()=>environment(async({storage,mount})=>{
 put(storage);globalThis.fetch=async()=>finish(unknown(body,{task_version:9,current_visit:1}));const component=mount();await component.flush();await component.click('核对原能力请求是否已保存');assert.throws(()=>component.button('按原编号继续能力请求'));assert.throws(()=>component.button('保留旧请求，重新核对当前版本'));assert.deepEqual(readAbility(storage,scope).pending,pending);
}));
for(const kind of ['task','actor','step','visit'])test('late success is fenced when current '+kind+' changes',async()=>environment(async({storage,mount})=>{
 let release,sent,refreshes=0;const deferred=new Promise(resolve=>release=resolve);globalThis.fetch=async(_,options)=>{sent=JSON.parse(options.body);return deferred;};const props={task:task(),step,account,onRefresh:()=>refreshes++},component=mount(props);await component.flush();const posting=component.click('确认并执行此步骤');await new Promise(resolve=>setImmediate(resolve));
 if(kind==='task')props.task=task({id:'task-b'});if(kind==='actor')props.account={...account,user_id:'user-b'};if(kind==='step')props.step={...step,id:'other'};if(kind==='visit')props.task=task({workflow_runtime:{receipts:[{}],current_step_id:step.id}});component.render();await component.flush();
 release(finish({ability_request:await receipt(sent)}));await posting;assert.equal(refreshes,0);assert.deepEqual(readAbility(storage,scope).pending.body,sent);const changed=abilityScope(props.account,props.task,props.step);assert.equal(readAbility(storage,changed).pending,null);
}));
test('late response cannot clear a newer same-scope cached request',async()=>environment(async({storage,mount})=>{
 let release,sent;const deferred=new Promise(resolve=>release=resolve);globalThis.fetch=async(_,options)=>{sent=JSON.parse(options.body);return deferred;};const component=mount();await component.flush();const posting=component.click('确认并执行此步骤');await new Promise(resolve=>setImmediate(resolve));const newer={...body,request_id:'12345678-1234-4234-9234-123456789aaa'};storage.setItem(abilityKey(scope),JSON.stringify({...readAbility(storage,scope),pending:{body:newer}}));release(finish({ability_request:await receipt(sent)}));await posting;assert.deepEqual(readAbility(storage,scope).pending.body,newer);
}));
test('permissions use current task grants, 403 preserves pending and refresh may restore access',async()=>environment(async({storage,mount})=>{
 const props={task:task()},component=mount(props);let posts=0,issues=0;props.onPermissionIssue=()=>issues++;globalThis.fetch=async()=>{posts++;return finish({detail:'permission revoked'},403);};await component.flush();assert.equal(component.button('确认并执行此步骤').props.disabled,false,'stale session viewer cannot veto current operator grant');await component.click('确认并执行此步骤');assert.equal(issues,1);assert.equal(component.button('核对原能力请求是否已保存').props.disabled,true);assert.ok(readAbility(storage,scope).pending);
 props.task={...props.task,permissions:{...props.task.permissions,can_probe_ability:false,current_actor_role:'viewer'}};component.render();await component.flush();assert.equal(component.button('核对原能力请求是否已保存').props.disabled,true);props.task={...props.task,permissions:{...props.task.permissions,can_probe_ability:true,current_actor_role:'operator'}};component.render();await component.flush();assert.equal(component.button('核对原能力请求是否已保存').props.disabled,false);assert.equal(posts,1);
}));
test('queued current task is not own-request acknowledgment and cannot replay an unverified duplicate',async()=>environment(async({storage,mount})=>{
 put(storage);globalThis.fetch=async()=>finish(unknown(body,{task_status:'workflow_queued',task_version:8}));const component=mount({task:task({status:'workflow_queued'})});await component.flush();await component.click('核对原能力请求是否已保存');assert.ok(readAbility(storage,scope).pending);assert.throws(()=>component.button('按原编号继续能力请求'));assert.throws(()=>component.button('确认并执行此步骤'));
}));
test('worker progress is truthful for queued, lease loss, missing heartbeat and old goal receipts',async()=>environment(async({server})=>{
 const {default:Progress}=await server.ssrLoadModule('/src/WorkflowWorkerProgress.jsx');
 for(const [state,text] of [['queued','等待后台领取'],['running','处理回执尚未完成'],['waiting_recovery','等待受控恢复'],['waiting_approval','等待业务审批状态允许执行'],['approval_closed','原业务已结束'],['failed','后台处理失败'],['blocked','尚未创建后台任务'],['unverified','尚未通过身份核对']]){
  const html=renderToStaticMarkup(React.createElement(Progress,{worker:{state,job_id:'private-job-id',lease_token:'private-token',database_now:'2026-10-15T02:00:00Z',last_activity_at:'2026-10-15T01:00:00Z',lease_until:'2026-10-15T02:01:00Z'}}));assert.ok(html.includes(text));assert.ok(html.includes('尚未取得可核实的心跳'));assert.ok(html.includes('最近保存活动'));assert.ok(!html.includes('private-'));assert.ok(!html.includes('离开页面后继续处理'));
 }
 const historical=renderToStaticMarkup(React.createElement(Progress,{worker:{state:'succeeded',historical:true}}));assert.ok(historical.includes('历史处理记录'));
 const missing=renderToStaticMarkup(React.createElement(Progress,{}));assert.ok(missing.includes('尚未取得可核对的后台处理状态'));
}));


test('explicit failed-step retry is separate from an unknown original send',async()=>environment(async({mount})=>{
 let sent;globalThis.fetch=async(_,options)=>{sent=JSON.parse(options.body);return finish({ability_request:await receipt(sent)});};
 const current=task({status:'workflow_failed',permissions:{can_run_ability:false,can_retry_ability:true,can_probe_ability:true,can_cancel:true}}),component=mount({task:current});await component.flush();await component.click('核对后发起新的能力尝试');assert.equal(sent.retry,true);assert.equal(sent.version,4);
}));

test('NativeGoalStudio presents queued check beside old saved evidence without claiming a running worker',async()=>environment(async({server,storage})=>{
 const browser=installEventBrowser({account,storage});try{
 const {default:Goal}=await server.ssrLoadModule('/src/NativeGoalStudio.jsx');
 const goal={task_id:'task-a',version:4,can_manage:false,can_stop:false,goal_confirmed:true,goal:{purpose:'原目标',required_fields:[],criteria:[],observation_mode:'existing_only'},fields:{},query_tools:[],goal_tools:[],acceptance:null,cancelled:null,check_worker:{state:'queued',last_activity_at:'2026-10-15T01:00:00Z',database_now:'2026-10-15T02:00:00Z'},check:{checked_at:'2026-10-14T01:00:00Z',evaluation:{checks:[],flow_finished:false,data_received:false},gaps:[],evidence:{evidence:[],observation_evidence:[]}}};
 let posts=0;globalThis.fetch=async(path,options={})=>{assert.equal(options.headers['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers['X-CSRF-Token'],'synthetic-event-csrf');if(options.method==='POST')posts++;return finish(path==='/api/me'?account:goal);};
 const component=mount(Goal,{task:{id:'task-a',title:'原任务'},account,Btn:'button',locked:false,onChanged:()=>{}});
 try{await component.flush();const html=renderToStaticMarkup(component.render());assert.ok(html.includes('目标核查处理进度'));assert.ok(html.includes('等待后台领取'));assert.ok(html.includes('以下为上次已保存的核查依据'));assert.ok(html.includes('上次目标核对'));assert.ok(!html.includes('后台正在核对原资料'));assert.ok(html.includes('尚未取得可核实的心跳'));assert.equal(posts,0);}finally{component.unmount();}
 }finally{browser.restore();}
}));

const humanTask=()=>task({version:8,status:'workflow_waiting',business:{flow:{steps:[{...step,agent:'数据管理'},{id:'review',name:'人工核对',agent:'人工确认'}]}},workflow_runtime:{current_step_id:'review',receipts:[{step_id:step.id,output:{result:24}}]}});

test('lost automatic POST remains reachable from the subsequent human step and probes its own exact receipt',async()=>environment(async({server,storage,mount:mountAutomatic})=>{
 const {default:Recovery}=await server.ssrLoadModule('/src/WorkflowAbilityRecovery.jsx');let sent,posts=0;
 globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){posts++;sent=JSON.parse(options.body);throw Error('lost after durable scheduling');}assert.ok(path.endsWith('/ability-requests/'+sent.request_id));return finish(await receipt(sent,{task_version:8,current_step_id:'review',current_visit:1,task_status:'workflow_waiting'}));};
 const automatic=mountAutomatic();await automatic.flush();await automatic.click('确认并执行此步骤');automatic.unmount();
 const component=mount(Recovery,{task:humanTask(),account,Btn:'button'});try{await component.flush();assert.equal(component.button('核对旧请求 1').props.disabled,false);await component.click('核对旧请求 1');assert.equal(posts,1);assert.equal(readAbility(storage,scope).pending,null);assert.deepEqual(readAbility(storage,scope).history[0].body,sent);const html=renderToStaticMarkup(component.render());assert.ok(html.includes('原请求保存已核实'));assert.ok(!html.includes(sent.request_id));}finally{component.unmount();}
}));

test('advanced-step unknown request is archived locally only after second version-fenced proof, never resent',async()=>environment(async({server,storage})=>{
 const {default:Recovery}=await server.ssrLoadModule('/src/WorkflowAbilityRecovery.jsx');put(storage);let probes=0,posts=0;globalThis.fetch=async(_,options={})=>{if(options.method==='POST')posts++;probes++;return finish(unknown(body,{task_version:8,current_step_id:'review',current_visit:1}));};
 const props={task:humanTask(),account,Btn:'button'};let component=mount(Recovery,props);try{await component.flush();await component.click('核对旧请求 1');assert.ok(readAbility(storage,scope).pending);await component.click('保留旧请求及版本核对依据 1');assert.equal(probes,2);assert.equal(posts,0);const journal=readAbility(storage,scope);assert.equal(journal.pending,null);assert.deepEqual(journal.history[0].body,body);assert.equal(journal.history[0].proof.saved,false);assert.equal(journal.history[0].proof.current_step_id,'review');component.unmount();component=mount(Recovery,props);await component.flush();const html=renderToStaticMarkup(component.render());assert.ok(html.includes('送达状态仍未证实'));assert.ok(!html.includes('保存已核实'));}finally{component.unmount();}
}));

test('late old-step probe cannot clear another actor or task journal',async()=>environment(async({server,storage})=>{
 const {default:Recovery}=await server.ssrLoadModule('/src/WorkflowAbilityRecovery.jsx');put(storage);const otherScope={...scope,user_id:'user-b'};put(storage,otherScope);
 let release;globalThis.fetch=async()=>new Promise(resolve=>release=resolve);const props={task:humanTask(),account,Btn:'button'},component=mount(Recovery,props);
 try{await component.flush();const probing=component.click('核对旧请求 1');await new Promise(resolve=>setImmediate(resolve));props.account={...account,user_id:'user-b'};component.render();await component.flush();release(finish(await receipt(body,{task_version:8,current_step_id:'review',current_visit:1})));await probing;assert.ok(readAbility(storage,scope).pending);assert.ok(readAbility(storage,otherScope).pending);props.task={...humanTask(),id:'task-b'};component.render();await component.flush();assert.throws(()=>component.button('核对旧请求 1'));assert.ok(readAbility(storage,scope).pending);}finally{component.unmount();}
}));


test('current permission contract chooses retry independently of coarse task status',async()=>environment(async({mount})=>{
 for(const [status,permissions,label,retry] of [
  ['workflow_waiting',{can_run_ability:false,can_retry_ability:true,can_probe_ability:true},'核对后发起新的能力尝试',true],
  ['workflow_blocked',{can_run_ability:true,can_retry_ability:false,can_probe_ability:true},'确认并执行此步骤',false]]){
  let sent;globalThis.fetch=async(_,options)=>{sent=JSON.parse(options.body);return finish({ability_request:await receipt(sent,{task_id:'task-'+status})});};
  const component=mount({task:task({id:'task-'+status,status,permissions})});await component.flush();assert.equal(component.button(label).props.disabled,false);await component.click(label);assert.equal(sent.retry,retry);component.unmount();
 }
}));
