// Actual default parent + child Cards and actual controllers. Public DTO,
// browser storage and request IO are explicit offline fixtures: no backend
// custody, authority, SDK, HTTP, money or deployment is claimed by these tests.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {adviceCanonical,adviceHash,ADVICE_PREFIX} from '../src/same-goal-advice-v310-controller.mjs';
import {CONTINUATION_PREFIX} from '../src/same-goal-continuation-v312-controller.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let folder,compiled;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  folder=await mkdtemp(join(tmpdir(),'same-goal-continuation-parent-v312-'));const reactPath=join(project,'node_modules/react/index.js'),reactURL=pathToFileURL(reactPath).href,previous=process.env.NODE_ENV;let output;
  try{output=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},
   build:{ssr:join(project,'src/SameGoalAdviceV310.jsx'),write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}});}
  finally{if(previous===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=previous;}
  const chunk=output.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);const file=join(folder,'Parent.mjs');await writeFile(file,chunk.code);return (await import(pathToFileURL(file).href)).default;
 })();return compiled;
}
const id=n=>'31210000-0000-4000-8000-'+String(n).padStart(12,'0'),sha='a'.repeat(64),copy=v=>structuredClone(v);
const account={user_id:id(1),workspace_id:id(2),role:'operator'},makeTask=n=>({id:id(n),instance_id:id(n+100),instance_release_id:id(n+200)});
const flags={can_dispatch:false,execution_approved:false,goal_completion_verified:false,cloud_send_authorized:false};
const oldFlags={can_dispatch:false,permissions_verified:false,source_authenticity_verified:false,current_outcome_verified:false,goal_completion_verified:false,data_reception_verified:false};
const gate=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
class Memory{
 constructor(){this.data=new Map();this.failPrefix='';}get length(){return this.data.size;}key(n){return [...this.data.keys()][n]??null;}getItem(key){return this.data.get(key)??null;}
 setItem(key,value){if(this.failPrefix&&key.startsWith(this.failPrefix))throw Error('synthetic local durability failure');this.data.set(key,String(value));}removeItem(key){this.data.delete(key);}
}
function seedParent(storage,session,task,planId,actor=account){
 const owner=id(99),scope={actor_id:actor.user_id,workspace_id:actor.workspace_id,task_id:task.id,instance_id:task.instance_id,release_id:task.instance_release_id},key=ADVICE_PREFIX+adviceCanonical(scope);
 session.setItem('ga-same-goal-advice-v310-tab',owner);
 const row={schema:'same-goal-advice-local-draft-v310',scope,owner,plan_id:planId,draft:{selected_document_source:null,prepare_reason:'',consent_reasons:{},consent_checked:{},preview_reason:'',preview_checked:false,start_reason:'',review_decision:'',review_reason:'',cancel_reason:'',quota_cost:'',quota_reason:'',price_reason:''}};
 storage.setItem(key+':draft:'+owner,JSON.stringify(row));storage.setItem(key+':draft-latest',JSON.stringify(row));
}
async function fixture(){
 const storage=new Memory(),session=new Memory(),calls=[],mode={},receipts=new Map(),extraPlans=new Map(),initialTask=makeTask(10),tasks=new Map(),modelPin={id:id(800),content_hash:sha,release_version:'1.0.0'};
 for(const[key,value]of Object.entries({'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id,'ga-setup-csrf':'parent-fixture-csrf'}))session.setItem(key,value);
 const model={id:modelPin.id,name:'本任务已发布模型',provider:'deepseek',model:'explicit-offline-fixture',release_version:'1.0.0',available:true};
 async function register(task){
  const planId=id(Number(task.id.slice(-12))+500),base='/workspace/tasks/'+task.id+'/business-advice',child=base+'/preparations/'+planId+'/continuations';
  const dataText='本任务数据原件的离线DTO读取边界。',documentText='已批准业务摘录的离线DTO读取边界。';
  const documents=[];for(const[index,text]of [dataText,documentText].entries())documents.push({role:index?'business_document':'data',title:index?'批准的业务资料':'本次数据与接收记录',text,
   evidence_ref:{evidence_id:id(850+index),sha256:await adviceHash(text,webcrypto),size_bytes:new TextEncoder().encode(text).length,media_type:'text/plain'},version:1,source_scope:index?'approved_excerpt':'evaluation_text',as_of:'2026-11-02T10:00:00Z',consent:{status:'not_authorized',grants:[]}});
  const value={schema:'same-goal-advice-operation-v302',task_id:task.id,plan_id:planId,version:5,request_id:id(900),preparation_snapshot_hash:sha,phase:'reviewed',status:'reviewed',human_review_available:false,data_as_of:'2026-11-02T10:00:00Z',cancel_available:false,
   // No preparation DTO fields are consumed by this parent test; the non-null
   // marker deliberately leaves document reads to their separate API fixtures.
   document_preparation:{},reviews:[{decision:'accepted',reason:'人工采用有限文字中的核对建议',reviewed_at:'2026-11-02T10:30:00Z'}],
   result:{schema:'same-goal-advice-result-v302',plan_ref:{id:planId,record_hash:sha},review_state:'ready_for_review',model_value:{candidate_reviews:[],source_reviews:[],conflicts:[],recommendation:{candidate_ref:null,reason:'原建议仍供人工判断',supporting_fact_refs:[]}},...oldFlags}};
  const context={schema:'same-goal-advice-context-v310',task_id:task.id,task_version:4,title:'原业务任务',goal_purpose:'原目标',goal_hash:sha,mature_head_hash:sha,model,document_sources:[],blocked_reasons:[],
   permissions:{can_prepare:false,can_authorize:false,can_preview:false,can_start:false,can_review:false,can_configure_quota:false,can_register_price:false}};
  const continuation={schema:'same-goal-continuation-context-v312',task_id:task.id,advice_plan_id:planId,task_version:4,source_hash:sha,source:{origin_kind:'independent_advice',bridge_state:'not_bound',review:{decision:'accepted',reason:'原人工决定_'+task.id.slice(-2),reviewed_at:'2026-11-02T10:30:00Z'},data_as_of:'2026-11-02T10:00:00Z',limitations:['原资料仅为批准摘录_'+task.id.slice(-2)],recommendation:{candidate_ref:null,reason:'原建议：仅供规划草案_'+task.id.slice(-2),supporting_fact_refs:[]}},candidates:[],allowed_routes:['operational_action'],tools:[],assignees:[],permissions:{can_create:true,can_preview:true},blocked_reasons:[],...flags};
  const result={task,planId,base,child,plan:value,context,continuation,documents:{schema:'same-goal-advice-documents-v310',task_id:task.id,plan_id:planId,plan_version:5,model_pin:modelPin,model,documents,restrictions:[]}};tasks.set(task.id,result);seedParent(storage,session,task,planId);return result;
 }
 const initial=await register(initialTask);
 const request=async(path,options={})=>{
  calls.push({path,method:options.method||'GET'});assert.ok(!options.method||options.method==='GET','mount and navigation must not POST');if(path==='/me')return copy(account);
  const entry=[...tasks.values()].find(row=>path.startsWith(row.base));assert.ok(entry,'fixed task API path');
  if(path===entry.base+'/context'){if(mode.parentGate)await mode.parentGate.promise;return copy(entry.context);}
  if(path===entry.base+'/preparations')return {schema:'same-goal-advice-operation-v302/list',task_id:entry.task.id,records:[copy(entry.plan),...[...extraPlans.values()].filter(plan=>plan.task_id===entry.task.id).map(copy)]};
  if(path.startsWith(entry.base+'/operations/')){if(mode.receiptGate)await mode.receiptGate.promise;const receipt=receipts.get(path.split('/').at(-1));assert.ok(receipt);return copy(receipt);}
  if(path===entry.base+'/preparations/'+entry.planId)return copy(entry.plan);
  if(extraPlans.has(path.split('/').at(-1)))return copy(extraPlans.get(path.split('/').at(-1)));
  if(path===entry.base+'/preparations/'+entry.planId+'/documents')return copy(entry.documents);
  if(path===entry.base+'/preparations/'+entry.planId+'/preview')throw Object.assign(Error('explicit absent preview IO'),{status:404});
  if(path===entry.child+'/context'){if(mode.childGate)await mode.childGate.promise;return copy(entry.continuation);}
  if(path===entry.child)return {schema:'same-goal-continuation-view-v312/list',task_id:entry.task.id,advice_plan_id:entry.planId,records:copy(entry.savedDrafts||[])};
  for(const row of entry.savedDrafts||[]){
   const target=entry.child+'/'+row.continuation_id;
   if(path===target)return copy(row);
   const execution=target+'/executions';
   if(path===execution||path===execution+'/context'){
    const scope={task_id:entry.task.id,advice_plan_id:entry.planId,continuation_id:row.continuation_id};
    if(path===execution)return {schema:'same-goal-continuation-execution-view-v314/list',...scope,records:[]};
    const grantId=id(82);
    return {schema:'same-goal-continuation-execution-context-v314',...scope,...flags,task_version:4,continuation_version:row.version,
     revision_hash:row.revision.record_hash,source_hash:sha,draft:row.revision.draft,blocked_reasons:[],returned_candidates:[],
     permissions:{can_preview:true,can_submit:true},grants:[{...scope,grant_id:grantId,grant_ref:{id:grantId,record_hash:sha},
      eligible:true,expired:false,phase:'active',version:1,field:{label:'气温',key:'temperature',unit:'℃'},operation:'mean'}]};
   }
  }
  assert.fail('unexpected fixed API '+path);
 };
 const stageParentPrepare=async()=>{
  const scope={actor_id:account.user_id,workspace_id:account.workspace_id,task_id:initialTask.id,instance_id:initialTask.instance_id,release_id:initialTask.instance_release_id},key=ADVICE_PREFIX+adviceCanonical(scope),requestId=id(970),nextId=id(971),body={request_id:requestId,expected_goal_hash:sha,expected_mature_head_hash:sha,document_source:{kind:'approved_source_set',source_set_ref:{source_set_id:id(972),source_set_hash:sha}},reason:'另一个窗口已明确提交的原整理申请'};
  const row={schema:'same-goal-advice-local-command-v310',scope,operation:'prepare',plan_id:null,evidence_id:null,request_id:requestId,path:initial.base+'/preparations',body,raw:adviceCanonical(body),body_hash:await adviceHash(adviceCanonical(body),webcrypto),expected:{}};
  const next={...copy(initial.plan),plan_id:nextId,version:1,request_id:requestId,phase:'queued',status:'queued',document_preparation:null,result:null,reviews:[]};extraPlans.set(nextId,next);
  receipts.set(requestId,{schema:'same-goal-advice-command-receipt-v310',found:true,operation:'prepare',task_id:initialTask.id,plan_id:nextId,request_id:requestId,actor_id:account.user_id,body_hash:row.body_hash,result_status:'available',result:copy(next)});
  const raw=JSON.stringify(row);storage.setItem(key+':original:'+requestId,raw);storage.setItem(key+':pending',raw);return {key,row,raw,next};
 };
 return {actor:account,storage,session,calls,mode,register,initial,request,stageParentPrepare,locks:{request:async(key,options,run)=>run()}};
}
// Opt-in Root-owned replay only. Paths are supplied by the runner; this test
// never discovers private files or silently replaces a missing captured reply.
async function capturedFixture(){
 const parentFile=process.env.GA_OFFLINE_ADVICE_CAPTURE,childFile=process.env.GA_OFFLINE_CONTINUATION_CAPTURE;
 assert.ok(parentFile&&childFile,'both explicit capture paths are required');const p=JSON.parse(await readFile(parentFile,'utf8')),q=JSON.parse(await readFile(childFile,'utf8'));
 assert.equal(p.schema,'actual-business-advice-progressive-capture-v311');assert.equal(q.source_kind,'actual_authenticated_http_response');assert.equal(p.task.id,q.task_id);assert.equal(p.plan_id,q.advice_id);assert.equal(q.responses.me.value.user_id,p.account.user_id);assert.equal(q.responses.me.value.workspace_id,p.account.workspace_id);assert.equal(q.responses.me.value.role,p.account.role);
 const actor=copy(p.account),task=copy(p.task),storage=new Memory(),session=new Memory(),calls=[],mode={},entries=new Map(),strip=path=>path.startsWith('/api/')?path.slice(4):path;
 for(const[path,row]of Object.entries(p.responses))entries.set(strip(path),{status:row.status??200,value:row.value});
 for(const row of Object.values(q.responses))if(row?.path)entries.set(strip(row.path),{status:row.status??200,value:row.value});
 for(const[key,value]of Object.entries({'ga-setup-csrf-user':actor.user_id,'ga-setup-csrf-workspace':actor.workspace_id,'ga-setup-csrf':'explicit-offline-capture-replay'}))session.setItem(key,value);seedParent(storage,session,task,p.plan_id,actor);
 const base='/workspace/tasks/'+task.id+'/business-advice',child=base+'/preparations/'+p.plan_id+'/continuations';
 const request=async(path,options={})=>{calls.push({path,method:options.method||'GET'});assert.ok(!options.method||options.method==='GET','actual DTO replay is GET-only');const row=entries.get(path);assert.ok(row,'capture must contain the exact requested API');if(row.status>=400)throw Object.assign(Error('captured read was unavailable'),{status:row.status});assert.ok(row.value!==undefined,'capture body must exist');return copy(row.value);};
 return {actor,storage,session,calls,mode,initial:{task,planId:p.plan_id,base,child},request,locks:{request:async(key,options,run)=>run()},capture:q};
}
async function capturedExecutionFixture(){
 const file=process.env.GA_OFFLINE_EXECUTION_CAPTURE;
 assert.ok(file,'an explicit execution capture path is required');
 const capture=JSON.parse(await readFile(file,'utf8'));
 assert.equal(capture.schema,'actual-same-goal-execution-capture-v314');
 assert.equal(capture.source_kind,'actual_authenticated_http_response');
 const {account:actor,task,advice_id:planId,continuation_id:continuationId,execution_id:executionId}=capture;
 const storage=new Memory(),session=new Memory(),calls=[],entries=new Map();
 for(const [raw,row] of Object.entries(capture.responses)){
  const path=raw.startsWith('/api/')?raw.slice(4):raw;
  assert.ok(!entries.has(path),'capture contains a duplicate normalized path');
  assert.ok(Number.isSafeInteger(row.status),'capture must record the original HTTP status');
  entries.set(path,row);
 }
 const base='/workspace/tasks/'+task.id+'/business-advice';
 const preparation=base+'/preparations/'+planId;
 const child=preparation+'/continuations';
 const continuation=child+'/'+continuationId;
 const executions=continuation+'/executions';
 const record=executions+'/'+executionId;
 for(const path of ['/me',base+'/context',base+'/preparations',preparation,child+'/context',child,continuation,
  executions+'/context',executions,record]){
  assert.equal(entries.get(path)?.status,200,'a required authenticated capture is missing');
 }
 for(const path of [preparation+'/documents',preparation+'/preview']){
  assert.ok(entries.has(path),'original document and preview statuses must be explicitly captured');
 }
 const me=entries.get('/me').value;
 for(const key of ['user_id','workspace_id','role'])assert.equal(me[key],actor[key]);
 const draft=entries.get(continuation).value,actual=entries.get(record).value;
 const scope={task_id:task.id,advice_plan_id:planId,continuation_id:continuationId};
 for(const row of [draft,entries.get(executions+'/context').value,entries.get(executions).value,actual]){
  for(const [key,value] of Object.entries(scope))assert.equal(row[key],value);
 }
 assert.equal(actual.plan_id,executionId);
 assert.equal(actual.phase,'adopted');
 assert.equal(actual.job?.status,'succeeded');
 assert.equal(actual.data_received,true);
 assert.ok(actual.output&&Number.isFinite(actual.output.value),'capture must carry the adopted numeric result');
 assert.ok(actual.policy_stage_count>0&&actual.stage===actual.policy_stage_count,'capture must contain completed real approval stages');
 const draftIndex=entries.get(child).value.records.findIndex(row=>row.continuation_id===continuationId);
 const executionIndex=entries.get(executions).value.records.findIndex(row=>row.plan_id===executionId);
 assert.ok(draftIndex>=0&&executionIndex>=0,'captured records must be selectable in their own lists');
 const csrf='explicit-offline-execution-replay';
 for(const [key,value] of Object.entries({'ga-setup-csrf-user':actor.user_id,'ga-setup-csrf-workspace':actor.workspace_id,'ga-setup-csrf':csrf}))session.setItem(key,value);
 seedParent(storage,session,task,planId,actor);
 const request=async(path,options={})=>{
  const method=options.method||'GET';
  calls.push({path,method});
  assert.equal(method,'GET','captured execution replay is read-only');
  assert.equal(options.headers['X-Workspace-ID'],actor.workspace_id);
  assert.equal(options.headers['X-CSRF-Token'],csrf);
  const row=entries.get(path);
  assert.ok(row,'capture must contain the exact requested API');
  if(row.status>=400)throw Object.assign(Error('captured read was unavailable'),{status:row.status});
  assert.ok(row.value!==undefined,'captured response body must exist');
  return copy(row.value);
 };
 return {actor,storage,session,calls,initial:{task,planId,base,child},request,
  locks:{request:async(key,options,run)=>run()},executionCapture:{actual,draftIndex,executionIndex,executions,record,documents:preparation+'/documents'}};
}
async function harness(run,{actual=false,execution=false}={}){
 const errors=[],network=[],vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error));class NoResources extends ResourceLoader{fetch(url){network.push(url);throw Error('network forbidden');}}
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://continuation-parent.invalid/',resources:new NoResources(),virtualConsole:vc});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,
  addEventListener:dom.window.addEventListener.bind(dom.window),removeEventListener:dom.window.removeEventListener.bind(dom.window),dispatchEvent:dom.window.dispatchEvent.bind(dom.window)};
 const prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
 let root;try{
  const Parent=await component(),{createRoot}=await import('react-dom/client');
  const f=execution?await capturedExecutionFixture():actual?await capturedFixture():await fixture();
  const container=document.getElementById('root');let task=f.initial.task,currentGuard=null;
  root=createRoot(container,{onUncaughtError:e=>errors.push(e),onCaughtError:e=>errors.push(e),onRecoverableError:e=>errors.push(e)});
  const onRegisterGuard=(guard,previous)=>{if(guard)currentGuard=guard;else if(!previous||previous===currentGuard)currentGuard=null;};
  const render=()=>act(async()=>root.render(React.createElement(Parent,{account:f.actor,task,requestJSON:f.request,storage:f.storage,tabStorage:f.session,locks:f.locks,onRegisterGuard})));
  const until=async predicate=>{for(let n=0;n<200;n++){if(predicate())return;await act(async()=>{await new Promise(done=>setImmediate(done));});}assert.fail('bounded parent/child state did not arrive');};
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent===label),field=()=>container.querySelector('textarea[aria-label="办理说明（可先只写这一项）"]');
  const click=async node=>{assert.ok(node);await act(async()=>node.click());},edit=async text=>{assert.ok(field());await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(field(),text);field().dispatchEvent(new dom.window.Event('input',{bubbles:true}));});};
  await render();await until(()=>button('拟定后续办理方案')&&!button('拟定后续办理方案').disabled);assert.deepEqual(errors,[]);
  await run({...f,container,dom,render,until,button,field,click,edit,text:()=>container.textContent,guard:()=>currentGuard?.(),switchTask:async next=>{task=next;await render();}});
  assert.deepEqual(errors,[]);assert.deepEqual(network,[]);assert.ok(f.calls.every(row=>row.method==='GET'));
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}

test('actual parent has zero continuation reads before explicit open and opening only reads the fixed child APIs',{concurrency:false},async()=>harness(async h=>{
 assert.equal(h.calls.filter(row=>row.path.includes('/continuations')).length,0);assert.equal(h.field(),null);await h.click(h.button('拟定后续办理方案'));await h.until(()=>h.field()&&!h.field().disabled);assert.equal(h.calls.filter(row=>row.path===h.initial.child+'/context').length,1);assert.equal(h.calls.filter(row=>row.path===h.initial.child).length,1);
}));
test('collapse and reopen retain the actual child DOM/controller, local text and owner without another GET',{concurrency:false},async()=>harness(async h=>{
 await h.click(h.button('拟定后续办理方案'));await h.until(()=>h.field()&&!h.field().disabled);await h.edit('这段人工办理说明需要保留 😀');const node=h.field(),before=h.calls.filter(row=>row.path.includes('/continuations')).length;
 await h.click(h.button('收起办理草案'));assert.equal(h.field(),node);assert.ok(node.closest('[hidden]'));await h.click(h.button('拟定后续办理方案'));assert.equal(h.field(),node);assert.equal(h.field().value,'这段人工办理说明需要保留 😀');assert.equal(h.calls.filter(row=>row.path.includes('/continuations')).length,before);assert.equal(h.guard(),true);
}));
test('parent refresh keeps the already mounted child and unsent text while the parent Plan temporarily clears',{concurrency:false},async()=>harness(async h=>{
 await h.click(h.button('拟定后续办理方案'));await h.until(()=>h.field()&&!h.field().disabled);await h.edit('父页面刷新时不要覆盖的办理草稿');const node=h.field(),before=h.calls.filter(row=>row.path.includes('/continuations')).length;
 h.mode.parentGate=gate();await h.click(h.button('刷新原记录'));assert.equal(h.field(),node);assert.equal(h.field().value,'父页面刷新时不要覆盖的办理草稿');await act(async()=>h.mode.parentGate.resolve());await h.until(()=>!h.button('刷新原记录').disabled);assert.equal(h.field(),node);assert.equal(h.calls.filter(row=>row.path.includes('/continuations')).length,before);
}));
test('broken child persistence reaches the synchronous parent leave/collapse guard and never discards the work',{concurrency:false},async()=>harness(async h=>{
 await h.click(h.button('拟定后续办理方案'));await h.until(()=>h.field()&&!h.field().disabled);h.storage.failPrefix=CONTINUATION_PREFIX;await h.edit('存储暂不可用但仍需保留的人工输入');assert.equal(h.guard(),false);await h.click(h.button('收起办理草案'));assert.ok(h.button('收起办理草案'));assert.equal(h.field().value,'存储暂不可用但仍需保留的人工输入');const event=new h.dom.window.Event('beforeunload',{cancelable:true});globalThis.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
 assert.equal(h.guard(),false);assert.equal(h.field().value,'存储暂不可用但仍需保留的人工输入');h.storage.failPrefix='';await h.click(h.button('重新保存本机原稿'));assert.equal(h.guard(),true);
}));
test('new Task scope immediately hides old child and late old reads cannot restore it; old draft remains scoped locally',{concurrency:false},async()=>harness(async h=>{
 await h.click(h.button('拟定后续办理方案'));await h.until(()=>h.field()&&!h.field().disabled);await h.edit('旧任务专属的人工草案');h.mode.childGate=gate();await h.click(h.button('刷新原草案与办理条件'));const next=await h.register(makeTask(20));await h.switchTask(next.task);assert.equal(h.field(),null);assert.doesNotMatch(h.text(),/旧任务专属的人工草案|原建议：仅供规划草案_10/);
 await act(async()=>h.mode.childGate.resolve());await h.until(()=>h.button('拟定后续办理方案')&&!h.button('拟定后续办理方案').disabled);assert.equal(h.field(),null);assert.equal(h.calls.filter(row=>row.path.startsWith(next.child)).length,0);assert.ok([...h.storage.data.values()].some(value=>value.includes('旧任务专属的人工草案')));assert.doesNotMatch(h.text(),/旧任务专属的人工草案/);
}));
test('delayed different-Plan Prepare receipt cannot unmount a child whose new human draft stopped persisting',{concurrency:false},async()=>harness(async h=>{
 await h.click(h.button('拟定后续办理方案'));await h.until(()=>h.field()&&!h.field().disabled);await h.edit('点击恢复时已经保存的本机办理稿');const node=h.field(),original=await h.stageParentPrepare();
 await act(async()=>globalThis.dispatchEvent(new h.dom.window.StorageEvent('storage',{key:original.key+':pending',newValue:original.raw})));await h.until(()=>h.button('核对原申请结果'));
 h.mode.receiptGate=gate();await h.click(h.button('核对原申请结果'));await h.until(()=>h.calls.some(row=>row.path===h.initial.base+'/operations/'+original.row.request_id));
 h.storage.failPrefix=CONTINUATION_PREFIX;await h.edit('回执等待期间新写且尚未持久的人工说明');assert.equal(h.guard(),false);await act(async()=>h.mode.receiptGate.resolve());await h.until(()=>!h.button('刷新原记录').disabled);
 assert.equal(h.field(),node);assert.equal(h.field().value,'回执等待期间新写且尚未持久的人工说明');assert.equal(h.storage.getItem(original.key+':pending'),original.raw);assert.equal(h.storage.getItem(original.key+':received:'+original.row.request_id),null);assert.ok(h.button('收起办理草案'));
 h.storage.failPrefix='';await h.click(h.button('重新保存本机原稿'));assert.equal(h.guard(),true);await h.click(h.button('核对原申请结果'));await h.until(()=>h.field()===null);assert.equal(h.storage.getItem(original.key+':pending'),null);assert.ok([...h.storage.data.values()].some(value=>value.includes('回执等待期间新写且尚未持久的人工说明')));assert.ok(h.calls.every(row=>row.method==='GET'));
}));
test('saved continuation mounts execution lazily, keeps the editor across parent reads, and blocks switching while its draft is unsaved',{concurrency:false},async()=>harness(async h=>{
 const draft={route:'business_conclusion',candidate_ref:null,selected_tools:[],purpose:'核对气温',work_description:'用本地工具汇总',expected_result:'原结果可复核',assignee_id:null,due_at:null};
 h.initial.continuation.source.decision_intent='business_conclusion';
 h.initial.continuation.allowed_routes=['business_conclusion'];
 h.initial.savedDrafts=[id(80),id(81)].map(continuation_id=>({
  schema:'same-goal-continuation-view-v312',task_id:h.initial.task.id,advice_plan_id:h.initial.planId,continuation_id,
  version:1,phase:'draft',source_hash:sha,source_ref:{advice_plan_id:h.initial.planId,source_hash:sha},
  revision:{version:1,record_hash:sha,draft,actor_id:account.user_id,saved_at:'2026-11-02T10:00:00Z',reason:'原保存说明'},
  history:[],permissions:{can_revise:true,can_withdraw:true},...flags}));
 await h.click(h.button('拟定后续办理方案'));
 await h.until(()=>h.button('打开原草案')&&!h.button('打开原草案').disabled);
 await h.click(h.button('打开原草案'));
 await h.until(()=>h.button('打开审批与执行'));
 assert.equal(h.calls.some(row=>row.path.includes('/executions')),false);
 await h.click(h.button('打开审批与执行'));
 const reason=()=>h.container.querySelector('[aria-label="人工办理理由"]');
 await h.until(()=>reason()&&!reason().disabled);
 const original=reason();
 await h.click(h.button('刷新原草案与办理条件'));
 await h.until(()=>!h.button('刷新原草案与办理条件').disabled);
 assert.equal(reason(),original);
 h.storage.failPrefix='ga-same-goal-execution-v314:';
 await act(async()=>{
  Object.getOwnPropertyDescriptor(h.dom.window.HTMLTextAreaElement.prototype,'value').set.call(original,'保留执行理由');
  original.dispatchEvent(new h.dom.window.Event('input',{bubbles:true}));
 });
 assert.equal(h.guard(),false);
 await h.click(h.button('打开原草案'));
 assert.equal(reason(),original);
 assert.equal(reason().value,'保留执行理由');
 assert.equal(h.calls.some(row=>row.path.endsWith('/'+id(81))),false);
 h.storage.failPrefix='';
 await h.click(h.button('重新保存本机输入'));
 assert.equal(h.guard(),true);
 await h.click(h.button('打开原草案'));
 await h.until(()=>reason()===null);
 assert.ok([...h.storage.data.values()].some(raw=>raw.includes('保留执行理由')));
 assert.equal(h.calls.some(row=>row.path.includes('/'+id(81)+'/executions')),false);
}));
test('explicit authenticated DTO replay retains lazy parent/child and real historical draft bodies without writes',{concurrency:false,skip:!(process.env.GA_OFFLINE_ADVICE_CAPTURE&&process.env.GA_OFFLINE_CONTINUATION_CAPTURE)},async()=>harness(async h=>{
 assert.equal(h.calls.filter(row=>row.path.includes('/continuations')).length,0);await h.click(h.button('拟定后续办理方案'));await h.until(()=>h.button('打开原草案')&&!h.button('打开原草案').disabled);await h.click(h.button('打开原草案'));await h.until(()=>h.container.querySelector('[aria-label="已保存的办理草案"]'));
 const actual=h.capture.responses.current.value,first=actual.history[0];assert.ok(first?.draft,'actual sealed history body is required');assert.ok(h.text().includes(first.reason));if(first.draft.work_description)assert.ok(h.text().includes(first.draft.work_description));assert.equal(h.guard(),true);const saved=h.container.querySelector('[aria-label="已保存的办理草案"]'),before=h.calls.filter(row=>row.path.includes('/continuations')).length;
 await h.click(h.button('收起办理草案'));await h.click(h.button('拟定后续办理方案'));assert.equal(h.container.querySelector('[aria-label="已保存的办理草案"]'),saved);assert.equal(h.calls.filter(row=>row.path.includes('/continuations')).length,before);assert.equal(actual.phase,'withdrawn');assert.ok(h.calls.every(row=>row.method==='GET'));
},{actual:true}));
test('authenticated adopted execution replay uses the actual saved draft, approval stages and data receipt',{concurrency:false,skip:!process.env.GA_OFFLINE_EXECUTION_CAPTURE},async()=>harness(async h=>{
 const {actual,draftIndex,executionIndex,executions,record,documents}=h.executionCapture;
 const buttons=label=>[...h.container.querySelectorAll('button')].filter(node=>node.textContent===label);
 const executionReads=()=>h.calls.filter(row=>row.path===executions||row.path.startsWith(executions+'/')).length;
 const result=()=>h.container.querySelector('[aria-label="原执行结果与数据回执"]');
 const progress=()=>h.container.querySelector('[aria-label="办理进度与处理"]');
 assert.equal(executionReads(),0);
 assert.ok(h.calls.some(row=>row.path===documents),'the original parent document endpoint was read');
 await h.click(h.button('拟定后续办理方案'));
 await h.until(()=>buttons('打开原草案')[draftIndex]&&!buttons('打开原草案')[draftIndex].disabled);
 await h.click(buttons('打开原草案')[draftIndex]);
 await h.until(()=>h.button('打开审批与执行'));
 assert.equal(executionReads(),0);
 await h.click(h.button('打开审批与执行'));
 await h.until(()=>buttons('打开办理记录')[executionIndex]&&!buttons('打开办理记录')[executionIndex].disabled);
 await h.click(buttons('打开办理记录')[executionIndex]);
 await h.until(()=>result());
 assert.ok(h.calls.some(row=>row.path===record),'the selected execution was fetched by its original endpoint');
 assert.match(progress().textContent,/已采用办理结果/);
 assert.ok(progress().textContent.includes('已通过 '+actual.stage+' 级审批，共 '+actual.policy_stage_count+' 级'));
 const terms=[...result().querySelectorAll('dt')].map(node=>[node.textContent,node.nextElementSibling?.textContent]);
 const displayed=new Map(terms);
 const unit=actual.output.operation!=='count'&&actual.proposal.field.unit?' '+actual.proposal.field.unit:'';
 assert.equal(terms[0][1],String(actual.output.value)+unit);
 assert.equal(displayed.get('有效记录'),actual.output.sample_count+' 条');
 assert.equal(displayed.get('缺测记录'),actual.output.missing_count+' 条');
 assert.equal(displayed.get('数据管理接收'),'独立接收回执已核对');
 assert.equal(h.button('开始本次只读汇总'),undefined);
 assert.equal(h.button('保存结果复核决定'),undefined);
 const mounted=result(),before=executionReads();
 await h.click(h.button('收起审批与执行'));
 assert.ok(mounted.closest('[hidden]'));
 await h.click(h.button('打开审批与执行'));
 assert.equal(result(),mounted);
 await h.click(h.button('刷新原草案与办理条件'));
 await h.until(()=>!h.button('刷新原草案与办理条件').disabled);
 assert.equal(result(),mounted);
 await h.click(h.button('刷新原记录'));
 await h.until(()=>!h.button('刷新原记录').disabled);
 assert.equal(result(),mounted);
 assert.equal(executionReads(),before);
 assert.equal(h.guard(),true);
 assert.ok(h.calls.every(row=>row.method==='GET'));
},{execution:true}));
