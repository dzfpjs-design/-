// Invented transport only. Root executes these tests; no provider or authority stub.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createGraphTaskController,graphHash,humanValues,humanOutput,inspectHumanSchema} from '../src/graph-task-controller.mjs';
import {freshNumber,numericValue,numericToken,typedRequestJSON,cloneTyped,plainTyped} from '../src/typed-numbers-v132.mjs';
import {graphWireParseCanonical,graphWireCanonical,graphWireDigest} from '../src/full-semantic-graph-wire.mjs';
import {taskInputForm,createTaskInputDraft,editTaskInput,buildTaskGraphInputs,persistTaskCreation,taskCreationJSON,readTaskCreation,readTaskForm,saveTaskForm,completeTaskCreation,lookupTaskCreation,retireTaskCreation,migrateLegacyTaskCreation} from '../src/graph-task-inputs.mjs';

const scope={actor_id:'13200000-0000-4000-8000-000000000001',workspace_id:'13200000-0000-4000-8000-000000000002',task_id:'13200000-0000-4000-8000-000000000003'};
const schema={type:'object',properties:{reading:{type:'number',title:'核查数值'}},required:['reading'],additionalProperties:false};
class Storage{
 constructor(){this.rows=new Map();this.fail=false;}
 get length(){return this.rows.size;} key(i){return [...this.rows.keys()][i]??null;}
 getItem(k){return this.rows.get(k)??null;} removeItem(k){this.rows.delete(k);}
 setItem(k,v){if(this.fail)throw Error('合成本机配额不足');this.rows.set(k,String(v));}
}
const task=()=>({id:scope.task_id,workspace_id:scope.workspace_id,version:1,title:'原数值核查',business_name:'合成业务',instance_release_number:1,
 graph_runtime:{graph_hash:'a'.repeat(64),version:1,cancel_epoch:0,status:'running',commands:[],activations:{human:{id:'human',node_id:'a',status:'ready',version:0,epoch:0,draft:null,output:null}}},
 graph_pending_humans:[{activation_id:'human',node_id:'a',actor_id:scope.actor_id,version:0,can_edit:true,output_schema:schema,draft:null}],graph_jobs:{},graph_waits:[],business:{flow:{steps:[]}},permissions:{can_cancel_task:true},object_snapshots:[]});
const fingerprint=command=>({activation_id:command.activation_id,output:command.output,actor_id:scope.actor_id,expected_version:command.version,cancel_epoch:command.cancel_epoch});
async function fixture(){
 const storage=new Storage(),session=new Storage(),calls=[],mode={},state={task:task(),receipt:null},locks={request:async(_key,_options,fn)=>fn()};
 session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-csrf');
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body,headers:options.headers});
  assert.equal(options.headers?.['X-Workspace-ID'],scope.workspace_id);
  if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(path.includes('/graph/requests/'))return {schema:'workflow-graph-request-recovery-v1',found:!!state.receipt,request:cloneTyped(state.receipt),task:cloneTyped(state.task)};
  if(path.endsWith('/graph/command-hash')){
   const request=graphWireParseCanonical(options.body);assert.equal(request.schema,'workflow-graph-command-hash-command-v1');
   assert.equal(storage.getItem(controller.outboxKey),null,'read-only preparation precedes the durable mutation request');
   if(mode.beforePreparation)await mode.beforePreparation();
   const value={schema:'workflow-graph-command-hash-v1',actor_id:scope.actor_id,workspace_id:scope.workspace_id,task_id:scope.task_id,graph_hash:'a'.repeat(64),operation:'complete',command:request.command,body_hash:await graphWireDigest(fingerprint(request.command),{crypto:webcrypto})};
   if(mode.corrupt)mode.corrupt(value);
   const canonical=graphWireCanonical(value);return {schema:'report-full-semantic-graph-canonical-wire-v1',canonical,value:JSON.parse(canonical)};
  }
  if(options.method==='POST'){
   assert.ok(path.endsWith('/graph/complete'),'no other mutation is permitted');
   const pending=JSON.parse(storage.getItem(controller.outboxKey));assert.equal(pending.schema,'graph-task-outbox-v3');assert.equal(pending.exact_body,options.body);
   if(mode.notSent)throw Error('合成未送达，结果未知');
   const command=graphWireParseCanonical(options.body),activation=state.task.graph_runtime.activations.human;
   state.receipt={request_id:command.request_id,op:'complete',actor_id:scope.actor_id,body_hash:await graphWireDigest(fingerprint(command),{crypto:webcrypto})};
   state.task.graph_runtime.commands.push(state.receipt);state.task.graph_runtime.version++;state.task.version++;
   activation.status='completed';activation.version++;activation.output=cloneTyped(command.output);state.task.graph_pending_humans=[];
   if(mode.lost)throw Error('合成保存后丢失响应');return cloneTyped(state.task);
  }
  assert.equal(path,'/workspace/tasks/'+scope.task_id);return cloneTyped(state.task);
 };
 const options={api,scope,storage,session,locks,crypto:webcrypto,typedNumbers:true};
 const controller=createGraphTaskController(options);await controller.open();return {controller,options,storage,session,mode,state,calls};
}
const mutations=f=>f.calls.filter(c=>c.method==='POST'&&c.path.endsWith('/graph/complete'));

for(const raw of ['-0.0','1e21'])test('typed human '+raw+' retains original text through lost response and cold lookup',async()=>{
 const f=await fixture();f.controller.edit('human','reading',{mode:'value',raw});f.mode.lost=true;
 await assert.rejects(()=>f.controller.submit('complete','human'),/丢失响应/);
 const original=f.storage.getItem(f.controller.outboxKey),pending=JSON.parse(original);
 assert.match(pending.exact_body,new RegExp('"reading":'+raw.replace(/[.+]/g,'\\$&')));
 assert.equal(f.controller.forms.human.values.reading.raw,raw);assert.equal(mutations(f).length,1);
 f.controller.dispose();const cold=createGraphTaskController(f.options);await cold.open();assert.equal(f.storage.getItem(cold.outboxKey),original);
 const before=f.calls.length;assert.equal((await cold.recover()).found,true);assert.equal(cold.pending,null);assert.equal(mutations(f).length,1);
 assert.ok(f.calls.slice(before).every(c=>c.method==='GET'),'cold recovery never prepares or resends');
 const output=cold.detail.graph_runtime.activations.human.output.reading;
 assert.ok(raw==='-0.0'?Object.is(output,-0):output===1e21);
});

test('negative lookup enables only explicit original-body resend, without another preparation',async()=>{
 const f=await fixture();f.controller.edit('human','reading',{mode:'value',raw:'-0.0'});f.mode.notSent=true;
 await assert.rejects(()=>f.controller.submit('complete','human'));const exact=f.controller.pending.exact_body;
 assert.equal((await f.controller.recover()).found,false);assert.equal(mutations(f).length,1);f.mode.notSent=false;
 await f.controller.resendHumanResult();assert.deepEqual(mutations(f).map(c=>c.body),[exact,exact]);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/command-hash')).length,1);assert.equal(f.controller.pending,null);
});

test('new typed controller cold-recovers an unchanged legacy v1 nonce without preparing or rewriting it',async()=>{
 const f=await fixture(),body={request_id:'13200000-0000-4000-8000-000000000040',activation_id:'human',version:0,cancel_epoch:0,output:{reading:2}};
 const body_hash=await graphHash(fingerprint(body),webcrypto),pending={schema:'graph-task-outbox-v1',scope,operation:'complete',body,exact_body:JSON.stringify(body),body_hash,form_generation:0,rejected:null},raw=JSON.stringify(pending);
 f.storage.setItem(f.controller.outboxKey,raw);f.state.receipt={request_id:body.request_id,op:'complete',actor_id:scope.actor_id,body_hash};f.state.task.graph_runtime.commands.push(f.state.receipt);f.state.task.version++;f.state.task.graph_runtime.version++;
 Object.assign(f.state.task.graph_runtime.activations.human,{status:'completed',version:1,output:{reading:2}});f.state.task.graph_pending_humans=[];
 const cold=createGraphTaskController(f.options);await cold.open();assert.equal(f.storage.getItem(cold.outboxKey),raw);const before=f.calls.length;assert.equal((await cold.recover()).found,true);assert.ok(f.calls.slice(before).every(call=>call.method==='GET'));assert.equal(mutations(f).length,0);
});

test('v3 storage rejects a nonfinite mirror rather than collapsing it to null',async()=>{
 const f=await fixture();f.controller.edit('human','reading',{mode:'value',raw:'-0.0'});f.mode.notSent=true;await assert.rejects(()=>f.controller.submit('complete','human'));
 const original=f.storage.getItem(f.controller.outboxKey),record=JSON.parse(original);record.body.output.reading=null;
 const raw=JSON.stringify(record).replace('"reading":null','"reading":1e999');f.storage.setItem(f.controller.outboxKey,raw);
 const cold=createGraphTaskController(f.options);assert.equal(cold.denied,true);assert.equal(f.storage.getItem(cold.outboxKey),raw);assert.equal(mutations(f).length,1);
});

test('preparation cannot freeze an edited draft or an unbound actor, graph, body, nonce or digest',async t=>{
 for(const field of ['actor_id','workspace_id','task_id','graph_hash','body_hash','command'])await t.test(field,async()=>{
  const f=await fixture();f.controller.edit('human','reading',{mode:'value',raw:'1e21'});
  f.mode.corrupt=value=>{value[field]=field==='command'?{...value.command,request_id:'13200000-0000-4000-8000-000000000099'}:field.endsWith('hash')?'f'.repeat(64):'different';};
  await assert.rejects(()=>f.controller.submit('complete','human'));assert.equal(f.controller.pending,null);assert.equal(mutations(f).length,0);assert.equal(f.controller.forms.human.values.reading.raw,'1e21');
 });
 const f=await fixture();f.controller.edit('human','reading',{mode:'value',raw:'1e21'});f.mode.beforePreparation=()=>f.controller.edit('human','reading',{mode:'value',raw:'1.'});
 await assert.rejects(()=>f.controller.submit('complete','human'),/输入或原待办已变化/);assert.equal(f.controller.forms.human.values.reading.raw,'1.');assert.equal(mutations(f).length,0);assert.equal(f.controller.pending,null);
});

test('incomplete/unsafe input and durable-storage failure preserve the human draft and never mutate',async()=>{
 const f=await fixture();for(const raw of ['-','1e','9007199254740992','NaN']){
  f.controller.edit('human','reading',{mode:'value',raw});await assert.rejects(()=>f.controller.submit('complete','human'));assert.equal(f.controller.forms.human.values.reading.raw,raw);
 }
 f.controller.edit('human','reading',{mode:'value',raw:'-0.0'});f.storage.fail=true;await assert.rejects(()=>f.controller.submit('complete','human'));
 assert.equal(f.controller.forms.human.values.reading.raw,'-0.0');assert.equal(mutations(f).length,0);assert.equal(f.controller.guard(),false);
 f.storage.fail=false;assert.equal(f.controller.guard(),true);
});

test('numeric enum needs its actual float token; a bare unsafe integer is not relabelled a float',()=>{
 const unsafe={type:'object',properties:{reading:{type:'number',enum:[1e21]}},required:['reading'],additionalProperties:false};
 assert.equal(inspectHumanSchema(unsafe,{typedNumbers:true}).supported,false);
 const original=graphWireParseCanonical('{"additionalProperties":false,"properties":{"reading":{"enum":[1e21,-0.0],"type":"number"}},"required":["reading"],"type":"object"}');
 assert.equal(inspectHumanSchema(original,{typedNumbers:true}).supported,true);
 const values=humanValues(original,null,{typedNumbers:true});values.reading={mode:'value',raw:'1'};
 assert.equal(numericToken(humanOutput(original,values,{typedNumbers:true}).reading),'-0.0');
});

const inputScope={actor_id:scope.actor_id,workspace_id:scope.workspace_id,instance_id:'instance',release_id:'release',business_id:'business',configuration_hash:'b'.repeat(64)};
const metadata=()=>({schema:'workflow-graph-task-inputs-v1',instance_id:'instance',release_id:'release',business_id:'business',configuration_hash:inputScope.configuration_hash,mode:'graph',hash:'c'.repeat(64),graph_hash:'d'.repeat(64),binding_hash:'e'.repeat(64),execution_granted:false,input_schema:schema,user_input_schema:schema,entity_fields:[],system_fields:[]});
for(const raw of ['-0.0','1e21'])test('Task input '+raw+' is raw until v2 freeze, then lookup and retirement embed exact original bytes',async()=>{
 const storage=new Storage(),meta=metadata();let draft=createTaskInputDraft(meta,inputScope,{typedNumbers:true});
 draft=editTaskInput(draft,meta,inputScope,'reading',{mode:'value',raw},{typedNumbers:true});
 const payload={release_id:'release',business_id:'business',entity_ids:[],request_id:'original',graph_inputs:buildTaskGraphInputs(meta,inputScope,draft,[],{typedNumbers:true})};
 assert.equal(numericToken(payload.graph_inputs.reading),raw);const exact=persistTaskCreation(storage,'form',{title:'原稿',graphInputDraft:draft},payload,{typedNumbers:true});
 assert.equal(JSON.parse(storage.getItem('form:task-create-pending')).schema,'workflow-task-creation-outbox-v2');
 assert.equal(readTaskForm(storage,'form').graphInputDraft.values.reading.raw,raw);
 const cold=readTaskCreation(storage,'form');assert.equal(taskCreationJSON(cold.payload),exact);assert.equal(numericToken(cold.payload.graph_inputs.reading),raw);
 assert.throws(()=>saveTaskForm(storage,'form',{creationAction:null,title:'旧窗口'}),/另一窗口/);
 const calls=[],retirement={schema:'workflow-task-request-retirement-v1',actor_id:inputScope.actor_id,instance_id:'instance',request_id:'original',original:plainTyped(cold.payload),creation_hash:'a'.repeat(64),command_hash:'b'.repeat(64),reason:'保留原稿后结束此次请求'};
 const api=async(path,options)=>{calls.push({path,body:options.body});return path.endsWith('/retire')?retirement:{schema:'workflow-task-request-recovery-v1',found:false,request:null,retirement};};
 await lookupTaskCreation(api,inputScope,cold.payload);assert.equal(calls[0].body,exact);
 await retireTaskCreation(api,inputScope,cold.payload,retirement.reason);assert.ok(calls[1].body.includes('"original":'+exact));assert.equal(calls[2].body,exact);
 completeTaskCreation(storage,'form',{title:'新稿',creationAction:null},cold.payload);assert.equal(readTaskCreation(storage,'form'),null);assert.ok(storage.getItem('form:task-create-outbox:original'));
});

test('Task v2 exact body is immutable, corrupt mirrors and quota failure do not become saved drafts',()=>{
 const storage=new Storage(),payload={request_id:'original',graph_inputs:{reading:freshNumber('-0.0')}};
 persistTaskCreation(storage,'form',{},payload,{typedNumbers:true});const original=storage.getItem('form:task-create-pending');
 assert.throws(()=>persistTaskCreation(storage,'form',{}, {request_id:'original',graph_inputs:{reading:freshNumber('0')}},{typedNumbers:true}),/待核对/);
 const bad=JSON.parse(original);bad.payload.graph_inputs.reading=null;const raw=JSON.stringify(bad);storage.setItem('form:task-create-pending',raw);storage.setItem('form:task-create-outbox:original',raw);assert.throws(()=>readTaskCreation(storage,'form'),/镜像/);
 storage.setItem('form:task-create-pending',original);storage.setItem('form:task-create-outbox:original',original);
 const restored=readTaskCreation(storage,'form').payload;restored.graph_inputs.reading=freshNumber('0');assert.throws(()=>taskCreationJSON(restored),/正文已变化/);
 const empty=new Storage();empty.fail=true;assert.throws(()=>persistTaskCreation(empty,'form',{},payload,{typedNumbers:true}),/配额/);assert.equal(empty.length,0);
});

test('legacy Task outbox remains v1 with its exact original encoder and helper opt-in remains explicit',()=>{
 const storage=new Storage(),payload={request_id:'old',graph_inputs:{reading:2},title:'旧原请求'};
 assert.equal(persistTaskCreation(storage,'form',{},payload),JSON.stringify(payload));assert.equal(readTaskCreation(storage,'form').schema,'workflow-task-creation-outbox-v1');assert.equal(taskCreationJSON(readTaskCreation(storage,'form').payload),JSON.stringify(payload));
 const original=storage.getItem('form:task-create-pending');assert.equal(persistTaskCreation(storage,'form',{},readTaskCreation(storage,'form').payload,{typedNumbers:true}),JSON.stringify(payload));assert.equal(storage.getItem('form:task-create-pending'),original);
 const meta=metadata();assert.equal(taskInputForm(meta,inputScope).supported,true);
 const draft=createTaskInputDraft(meta,inputScope);draft.values.reading={mode:'value',raw:'1e21'};assert.throws(()=>buildTaskGraphInputs(meta,inputScope,draft,[]));
 assert.equal(numericValue(buildTaskGraphInputs(meta,inputScope,draft,[],{typedNumbers:true}).reading),1e21);
});

test('form-only legacy recovery backs up exact human form bytes and creates only the original v1 request',async()=>{
 const storage=new Storage(),payload={request_id:'old-only-form',release_id:'old-release',title:'原请求标题',graph_inputs:{reading:0}},form={title:'本页仍需保留的人稿',creationAction:payload,goalForm:{fields:[],enabled:false}},raw=JSON.stringify(form,null,2);
 storage.setItem('form',raw);assert.deepEqual(readTaskForm(storage,'form'),form);const pending=migrateLegacyTaskCreation(storage,'form',payload);
 assert.equal(pending.schema,'workflow-task-creation-outbox-v1');assert.deepEqual(pending.payload,payload);assert.equal(taskCreationJSON(pending.payload),JSON.stringify(payload));
 const backups=[...storage.rows].filter(([key])=>key.startsWith('form:task-create-legacy-form:old-only-form:'));assert.equal(backups.length,1);assert.equal(backups[0][1],raw);
 const calls=[];await lookupTaskCreation(async(path,options)=>{calls.push({path,...options});return {schema:'workflow-task-request-recovery-v1',found:false,request:null};},inputScope,pending.payload);
 assert.equal(calls.length,1);assert.ok(calls[0].path.endsWith('/lookup'));assert.equal(calls[0].body,JSON.stringify(payload));
 const original=storage.getItem('form:task-create-pending');persistTaskCreation(storage,'form',form,pending.payload,{typedNumbers:true});assert.equal(storage.getItem('form:task-create-pending'),original);
});

test('typed form encoding survives ordinary form saves and missing original text cannot become legacy',()=>{
 const storage=new Storage(),payload={request_id:'typed-original',graph_inputs:{reading:freshNumber('-0.0')}};
 persistTaskCreation(storage,'form',{title:'原数值稿'},payload,{typedNumbers:true});const record=readTaskCreation(storage,'form'),form=readTaskForm(storage,'form');
 assert.deepEqual(form.creationEncoding,{schema:'workflow-task-creation-form-encoding-v2',request_id:payload.request_id});
 saveTaskForm(storage,'form',{title:'同一人稿继续编辑',creationAction:record.payload});assert.deepEqual(JSON.parse(storage.getItem('form')).creationEncoding,form.creationEncoding);
 storage.removeItem('form:task-create-pending');storage.removeItem('form:task-create-outbox:'+payload.request_id);const raw=storage.getItem('form');
 assert.throws(()=>readTaskForm(storage,'form'),/独立正文/);assert.throws(()=>migrateLegacyTaskCreation(storage,'form',JSON.parse(raw).creationAction),/编码标记/);assert.equal(storage.getItem('form'),raw);assert.equal(storage.getItem('form:task-create-pending'),null);
});

test('typed immutable history and original typed objects each independently prevent a legacy downgrade',()=>{
 for(const keep of ['history','original-object']){
  const storage=new Storage(),payload={request_id:'typed-original',graph_inputs:{reading:freshNumber('1e21')}};
  persistTaskCreation(storage,'form',{},payload,{typedNumbers:true});const mirror=JSON.parse(storage.getItem('form')).creationAction;storage.removeItem('form:task-create-pending');
  storage.setItem('form',JSON.stringify({creationAction:mirror}));if(keep==='original-object')storage.removeItem('form:task-create-outbox:'+payload.request_id);
  const raw=storage.getItem('form');assert.throws(()=>migrateLegacyTaskCreation(storage,'form',keep==='history'?mirror:payload));assert.equal(storage.getItem('form'),raw);assert.equal(storage.getItem('form:task-create-pending'),null);
  if(keep==='history')assert.throws(()=>readTaskForm(storage,'form'),/恢复指针/);
 }
});

test('legacy migration rejects completed, changed, reordered or contradictory originals without writing',()=>{
 for(const failure of ['completed','changed','reordered','encoding','history']){
  const storage=new Storage(),payload={request_id:'old',title:'原内容'},form={creationAction:payload};storage.setItem('form',JSON.stringify(form));
  let proposed=payload;
  if(failure==='completed')storage.setItem('form:task-create-completed:old','{}');
  if(failure==='changed')proposed={...payload,title:'另一正文'};
  if(failure==='reordered')proposed={title:payload.title,request_id:payload.request_id};
  if(failure==='encoding')storage.setItem('form',JSON.stringify({...form,creationEncoding:{schema:'workflow-task-creation-form-encoding-v2',request_id:'old'}}));
  if(failure==='history')storage.setItem('form:task-create-outbox:old','{broken');
  const before=[...storage.rows];assert.throws(()=>migrateLegacyTaskCreation(storage,'form',proposed));assert.deepEqual([...storage.rows],before);
 }
});

test('legacy backup/write/readback failures preserve the old form and never return a request fit to send',()=>{
 for(const phase of ['backup-write','backup-readback','pending-write']){
  const storage=new Storage(),payload={request_id:'old',title:'旧原请求'},raw=JSON.stringify({title:'不能丢的人稿',creationAction:payload},null,2);storage.setItem('form',raw);
  const wrapped={getItem:key=>phase==='backup-readback'&&key.includes(':task-create-legacy-form:')?null:storage.getItem(key),setItem(key,value){if(phase==='backup-write'&&key.includes(':task-create-legacy-form:')||phase==='pending-write'&&key.endsWith(':task-create-pending'))throw Error('合成存储故障');storage.setItem(key,value);},removeItem:key=>storage.removeItem(key)};
  assert.throws(()=>migrateLegacyTaskCreation(wrapped,'form',payload));assert.equal(storage.getItem('form'),raw);assert.equal(storage.getItem('form:task-create-pending'),null);
  if(phase==='pending-write')assert.ok([...storage.rows].some(([key,value])=>key.includes(':task-create-legacy-form:')&&value===raw));
 }
});
