// Root runs Node. This suite uses only invented inline tasks and memory stores.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createGraphTaskController,graphCanonical,graphHash,humanValues,humanOutput,inspectHumanSchema,humanNullToggle,readWorkspaceBuckets} from '../src/graph-task-controller.mjs';

const copy=value=>JSON.parse(JSON.stringify(value));
const scope={actor_id:'10000000-0000-4000-8000-000000000001',workspace_id:'10000000-0000-4000-8000-000000000002',task_id:'10000000-0000-4000-8000-000000000003'};
const schema={type:'object',properties:{approved:{type:'boolean',title:'是否核对完成'},note:{type:'string',title:'核对说明',minLength:1},amount:{type:'number',title:'实际数量',minimum:0},choice:{type:['string','integer','null'],enum:['甲',1,null]}},required:['approved','note'],additionalProperties:false};
// The real form supports one non-null primitive type per field. Mixed types are
// explicitly tested as unsupported below; the standard fixture stays typed.
schema.properties.choice={type:['string','null'],enum:['甲','乙',null],title:'处理方向'};
class Memory{
 constructor(){this.map=new Map();this.fail=false;this.badReadback=false;}
 get length(){return this.map.size;}
 key(index){return [...this.map.keys()][index]??null;}
 getItem(key){if(this.fail)throw Error('合成存储故障');return this.badReadback&&this.map.has(key)?null:this.map.get(key)??null;}
 setItem(key,value){if(this.fail)throw Error('合成存储故障');this.map.set(key,String(value));}
 removeItem(key){if(this.fail)throw Error('合成存储故障');this.map.delete(key);}
}
function task(){
 const humans=['a1','a2'].map((activation_id,index)=>({activation_id,node_id:'human'+index,source_step_id:'source'+index,role:'核对职责'+index,actor_id:scope.actor_id,version:0,can_edit:true,output_schema:copy(schema),draft:null}));
 return {id:scope.task_id,workspace_id:scope.workspace_id,version:1,title:'合成并行业务',business_name:'原业务',instance_release_number:3,
  graph_runtime:{graph_hash:'a'.repeat(64),version:1,cancel_epoch:0,status:'running',commands:[],activations:Object.fromEntries(humans.map(h=>[h.activation_id,{id:h.activation_id,node_id:h.node_id,version:0,epoch:0,status:'ready',draft:null,output:null}]))},
  graph_pending_humans:humans,graph_jobs:{},graph_waits:[],permissions:{can_cancel_task:true},business:{flow:{steps:[]}},object_snapshots:[]};
}
const messageJob='10000000-0000-4000-8000-000000000010';
function messageTask(){
 const value=task();value.graph_runtime.clock=100;
 const activation={id:'a3',node_id:'source_wait',status:'waiting_event',version:0,epoch:0,created_at:100,
  wait_started_at:100,deadline:160,completed_at:null,correlation_hash:'c'.repeat(64),output:null,draft:null};
 value.graph_runtime.activations.a3=activation;value.graph_waits=[copy(activation)];
 value.graph_node_labels={a3:{name:'等待已登记来源的处理结果',kind:'wait',source_step_id:null}};
 value.graph_wait_poll={job_id:messageJob,authority_hash:'d'.repeat(64),available_at:105};
 value.graph_message_status={arrival_basis:'durable_admission',last_checked_at:100,
  error:{job_id:messageJob,reason:'合成数据库切点检查失败；不能作为界面正文',job_version:4,status:'failed',attempts:1},
  can_retry:true,windows:[{activation_id:'a3',status:'open',opened_at:100,deadline:160,closed_at:null,event_id:null}],
  candidate_only:true,physical_state_verified:false};
 value.graph_wait_retries={};return value;
}
async function fixture(options={}){
 const storage=options.storage||new Memory(),session=new Memory(),mode={},calls=[],state={task:options.message?messageTask():task(),me:{user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'}},locks={request:async(_key,_options,fn)=>fn()};
 session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-csrf');
 const api=async(path,settings={})=>{
  calls.push({path,method:settings.method||'GET',body:settings.body,headers:settings.headers});
  if(path==='/me')return copy(state.me);
  if(path.includes('/graph/requests/')){
   const nonce=path.split('/').at(-1),found=state.task.graph_wait_retries?.[nonce]||state.task.graph_runtime.commands.find(command=>command.request_id===nonce);
   const response={schema:'workflow-graph-request-recovery-v1',found:!!found,request:found?copy(found):null,task:copy(state.task)};
   mode.probed=true;if(mode.mutateProbe)await mode.mutateProbe(response);return response;
  }
  if(!settings.method||settings.method==='GET'){
   if(mode.beforeRead)await mode.beforeRead();
   if(mode.probed&&mode.oldCurrent){const old=copy(state.task);old.version=0;old.graph_runtime.version=0;return old;}
   if(mode.probed&&mode.currentReadFails)throw Error('合成读回失败');
   const result=copy(state.task);if(mode.probed&&mode.mutateCurrent)await mode.mutateCurrent(result);return result;
  }
  assert.ok(path.includes('/graph/'),'graph controller must never call a legacy completion API');
  const pending=JSON.parse(storage.getItem(controller.outboxKey));
  assert.equal(pending.exact_body,settings.body,'durable exact body must precede any mutation');
  const body=JSON.parse(settings.body),operation=path.split('/').at(-1);
  if(mode.reject){const error=Error('合成服务器拒绝');error.status=409;throw error;}
  if(mode.unknownWithoutCommit)throw Error('合成连接中断，服务器结果未知');
  if(operation==='retry-messages-v2'){
   assert.equal(pending.schema,'graph-task-outbox-v2');assert.equal(pending.operation,'message_retry');
   assert.deepEqual(Object.keys(body).sort(),['job_id','job_version','reason','request_id']);
   const signature=await graphHash({actor_id:scope.actor_id,body},webcrypto),prior=state.task.graph_wait_retries[body.request_id];
   if(prior){assert.equal(prior.body_hash,signature);return copy(state.task);}
   const message=state.task.graph_message_status;
   if(mode.beforeMessageWrite)await mode.beforeMessageWrite();
   if(message.can_retry!==true||message.error.job_version!==body.job_version||message.error.job_id!==body.job_id||body.job_id!==pending.expected_job_id){
    const error=Error('合成原检查已有变化');error.status=409;throw error;
   }
   state.task.graph_wait_retries[body.request_id]={actor_id:scope.actor_id,body_hash:signature,job_id:message.error.job_id};
   state.task.version++;message.can_retry=false;message.error.status='queued';message.error.job_version++;
   if(mode.afterWrite)await mode.afterWrite();
   if(mode.loseResponse)throw Error('合成已保存但响应丢失');return copy(state.task);
  }
  const hashBody={actor_id:scope.actor_id,expected_version:body.version,cancel_epoch:body.cancel_epoch,
   ...(operation==='cancel'?{reason:body.reason}:{activation_id:body.activation_id,[operation==='draft'?'draft':'output']:body[operation==='draft'?'draft':'output']})};
  const command={request_id:body.request_id,op:operation,actor_id:scope.actor_id,body_hash:await graphHash(hashBody,webcrypto)};
  state.task.graph_runtime.commands.push(command);state.task.version++;state.task.graph_runtime.version++;
  if(operation==='cancel'){state.task.graph_runtime.status='cancelled';state.task.graph_runtime.cancel_epoch++;state.task.graph_pending_humans=[];}
  else{
   const activation=state.task.graph_runtime.activations[body.activation_id],human=state.task.graph_pending_humans.find(item=>item.activation_id===body.activation_id);
   activation.version++;human.version++;
   if(operation==='draft'){activation.draft=copy(body.draft);human.draft=copy(body.draft);}
   else{activation.status='completed';activation.output=copy(body.output);state.task.graph_pending_humans=state.task.graph_pending_humans.filter(item=>item.activation_id!==body.activation_id);}
  }
  if(mode.afterWrite)await mode.afterWrite();
  if(mode.loseResponse)throw Error('合成已保存但响应丢失');return copy(state.task);
 };
 const controller=createGraphTaskController({api,scope,storage,session,locks:options.locks===undefined?locks:options.locks,crypto:webcrypto,onSaved:options.onSaved});
 controller.ingest(copy(state.task));
 return {controller,storage,session,mode,calls,state,api,locks};
}
const mutations=fixture=>fixture.calls.filter(call=>['POST','PUT'].includes(call.method));
function fill(controller,identity='a1'){
 controller.edit(identity,'approved',{mode:'value',raw:'false'});
 controller.edit(identity,'note',{mode:'value',raw:'原文证据已核对🙂'});
}

test('required boolean is unanswered initially, optional empty numbers are omitted, enum keeps JSON types',()=>{
 const values=humanValues(schema,null);assert.equal(values.approved.mode,'unset');assert.throws(()=>humanOutput(schema,values),/是否核对完成/);
 values.approved={mode:'value',raw:'false'};values.note={mode:'value',raw:'明确核对'};
 assert.deepEqual(humanOutput(schema,values),{approved:false,note:'明确核对'});
 values.amount={mode:'value',raw:''};assert.throws(()=>humanOutput(schema,values),/数值/);
 values.amount={mode:'value',raw:'0'};values.choice={mode:'value',raw:'2'};
 assert.deepEqual(humanOutput(schema,values),{approved:false,note:'明确核对',amount:0,choice:null});
 const numericEnum={type:'object',properties:{n:{type:'integer',enum:[1,2]}},required:['n'],additionalProperties:false};
 assert.deepEqual(humanOutput(numericEnum,{n:{mode:'value',raw:'0'}}),{n:1});
 assert.throws(()=>humanOutput(numericEnum,{n:{mode:'value',raw:'1x'}}));
 assert.equal(humanValues(schema,{choice:null}).choice.raw,'2');
});

test('structured, mixed, reference and unimplemented constraints visibly block result submission',async()=>{
 for(const spec of [{type:'object',properties:{}},{type:'array',items:{type:'string'}},{type:['string','integer']},{type:'string',oneOf:[]},{$ref:'#/properties/name'}]){
  const value={type:'object',properties:{value:spec},required:['value'],additionalProperties:false};assert.equal(inspectHumanSchema(value).supported,false);assert.throws(()=>humanOutput(value,{}));
 }
 const f=await fixture();f.state.task.graph_pending_humans[0].output_schema.properties.nested={type:'object',properties:{}};assert.throws(()=>f.controller.ingest(f.state.task));
 await assert.rejects(f.controller.submit('complete','a1'));assert.equal(mutations(f).length,0);
});

test('numeric ranges, safe integers, dates and Unicode lengths are validated without defaults',()=>{
 const value={type:'object',properties:{n:{type:'integer',minimum:1,maximum:5},d:{type:'string',format:'date'},s:{type:'string',minLength:1,maxLength:1}},required:['n','d','s'],additionalProperties:false};
 const fields={n:{mode:'value',raw:'2'},d:{mode:'value',raw:'2026-10-19'},s:{mode:'value',raw:'🙂'}};
 assert.deepEqual(humanOutput(value,fields),{n:2,d:'2026-10-19',s:'🙂'});
 for(const number of ['','0','1.5','9007199254740993'])assert.throws(()=>humanOutput(value,{...fields,n:{mode:'value',raw:number}}));
 assert.throws(()=>humanOutput(value,{...fields,d:{mode:'value',raw:'2026-02-30'}}));
});

test('required nullable text and number can change null back to actual input without a default',()=>{
 for(const [type,raw,actual] of [['string','实际现场说明','实际现场说明'],['number','2.5',2.5]]){
  const spec={type:'object',properties:{value:{type:[type,'null']}},required:['value'],additionalProperties:false};
  let values={value:{mode:'value',raw}};assert.deepEqual(humanOutput(spec,values),{value:actual});
  values.value=humanNullToggle(values.value);assert.deepEqual(humanOutput(spec,values),{value:null});
  values=humanValues(spec,{schema:'graph-human-form-draft-v1',values});values.value=humanNullToggle(values.value);
  assert.equal(values.value.mode,'unset');assert.throws(()=>humanOutput(spec,values));
  values.value={mode:'value',raw};assert.deepEqual(humanOutput(spec,values),{value:actual});
 }
});

test('history tasks and direct detail resolve while current objects wait or fail source-current validation',async()=>{
 let rejectObjects,historyReady,currentResult=null,historyResult=null;
 const observed=new Promise(resolve=>{historyReady=resolve;}),objects=new Promise((_resolve,reject)=>{rejectObjects=reject;});
 const api=async path=>{
  if(path==='/instances')return [{id:'instance'}];if(path.endsWith('/releases'))return [{id:'release'}];if(path.endsWith('/objects'))return objects;
  if(path.endsWith('/tasks'))return [{id:scope.task_id,title:'原任务已保存'}];if(path==='/workspace/tasks/'+scope.task_id)return task();return [];
 };
 const loading=readWorkspaceBuckets(api,'instance',{onHistory:value=>{historyResult=value;historyReady();},onCurrent:value=>{currentResult=value;}});
 await observed;assert.equal(historyResult[0].status,'fulfilled');assert.equal(historyResult[0].value[0].id,scope.task_id);assert.equal(currentResult,null);
 assert.equal((await api('/workspace/tasks/'+scope.task_id)).id,scope.task_id);
 const stale=Error('正式来源已变化，不能执行');stale.status=409;rejectObjects(stale);await loading;
 assert.equal(currentResult[2].status,'rejected');assert.equal(currentResult[2].reason.status,409);assert.equal(historyResult[0].status,'fulfilled');
});

test('canonical request bytes follow Python codepoint order and scalar exponent spelling',()=>{
 assert.equal(graphCanonical({'🙂':0.000001,'\ue000':0.0001,a:0.00001}),'{"a":1e-05,"":0.0001,"🙂":1e-06}');
 assert.equal(graphCanonical({x:1e-7,y:-0,z:1.25}),'{"x":1e-07,"y":0,"z":1.25}');
 assert.throws(()=>graphCanonical({x:Infinity}));assert.throws(()=>graphCanonical({x:9007199254740992}));
});

test('two human activations keep independent drafts; draft then completion use current activation CAS',async()=>{
 const f=await fixture(),c=f.controller;fill(c);c.edit('a2','note',{mode:'value',raw:'另一并行草稿'});
 await c.submit('draft','a1');assert.equal(c.pending,null);assert.equal(c.forms.a1.dirty,false);assert.equal(c.forms.a2.dirty,true);
 assert.equal(f.state.task.graph_pending_humans.find(h=>h.activation_id==='a1').version,1);
 await c.submit('complete','a1');assert.equal(f.state.task.graph_runtime.activations.a1.output.approved,false);
 assert.equal(f.state.task.graph_runtime.activations.a2.status,'ready');assert.equal(mutations(f)[1].method,'POST');
 assert.equal(JSON.parse(mutations(f)[1].body).version,1);assert.equal(mutations(f).length,2);
 assert.ok(f.calls.some(call=>call.path.includes('/graph/requests/')));
 assert.ok(f.calls.every(call=>call.headers['X-Workspace-ID']===scope.workspace_id));
});

test('loss of an accepted response recovers only the original nonce after reload',async()=>{
 const f=await fixture(),c=f.controller;fill(c);f.mode.loseResponse=true;await assert.rejects(c.submit('complete','a1'));
 const pending=copy(c.pending);assert.ok(f.storage.getItem(c.outboxKey));
 const next=createGraphTaskController({api:f.api,scope,storage:f.storage,session:f.session,locks:f.locks,crypto:webcrypto});next.ingest(f.state.task);
 f.mode.loseResponse=false;const result=await next.recover();assert.equal(result.found,true);assert.equal(next.pending,null);assert.equal(mutations(f).length,1);
 assert.ok(f.calls.some(call=>call.path.endsWith('/graph/requests/'+pending.body.request_id)));
});

test('unknown and found=false retain original body through refresh and forbid another submit',async()=>{
 const f=await fixture(),c=f.controller;fill(c);f.mode.unknownWithoutCommit=true;await assert.rejects(c.submit('complete','a1'));
 const original=f.storage.getItem(c.outboxKey);assert.equal((await c.recover()).found,false);assert.equal(f.storage.getItem(c.outboxKey),original);
 await assert.rejects(c.submit('draft','a2'));await assert.rejects(c.archiveRejected());assert.equal(mutations(f).length,1);
});

test('rejected command is archived only after a fresh negative probe; editable labor survives',async()=>{
 const f=await fixture(),c=f.controller;fill(c);f.mode.reject=true;await assert.rejects(c.submit('complete','a1'));
 const nonce=c.pending.body.request_id;assert.equal(c.pending.rejected.status,409);await c.archiveRejected();
 assert.equal(c.pending,null);assert.equal(c.forms.a1.dirty,true);assert.ok(f.storage.getItem(c.prefix+'history:request:'+nonce));
 f.mode.reject=false;await c.submit('complete','a1');assert.notEqual(JSON.parse(mutations(f)[1].body).request_id,nonce);
});

for(const field of ['actor_id','op','body_hash','request_id'])test('a found receipt with wrong '+field+' never acknowledges the original command',async()=>{
 const f=await fixture(),c=f.controller;fill(c);f.mode.mutateProbe=response=>{response.request[field]='forged';};
 await assert.rejects(c.submit('complete','a1'));assert.ok(c.pending);assert.ok(f.storage.getItem(c.outboxKey));assert.equal(mutations(f).length,1);
 f.mode.mutateProbe=null;await c.recover();assert.equal(c.pending,null);assert.equal(mutations(f).length,1);
});

test('valid receipt requires current task GET and page acceptance before clearing',async()=>{
 let accept=false;const f=await fixture({onSaved:async()=>accept}),c=f.controller;fill(c);f.mode.currentReadFails=true;
 await assert.rejects(c.submit('draft','a1'));assert.ok(c.pending);f.mode.currentReadFails=false;
 await assert.rejects(c.recover());assert.ok(c.pending);accept=true;await c.recover();assert.equal(c.pending,null);assert.equal(mutations(f).length,1);
});

test('older current GET cannot settle a valid receipt',async()=>{
 const f=await fixture(),c=f.controller;fill(c);f.mode.oldCurrent=true;await assert.rejects(c.submit('complete','a1'));assert.ok(c.pending);
 f.mode.oldCurrent=false;await c.recover();assert.equal(c.pending,null);
});

test('typing after a draft send keeps the newer local generation dirty',async()=>{
 const f=await fixture(),c=f.controller;fill(c);f.mode.afterWrite=()=>c.edit('a1','note',{mode:'value',raw:'提交期间新增的草稿'});
 await c.submit('draft','a1');assert.equal(c.forms.a1.values.note.raw,'提交期间新增的草稿');assert.equal(c.forms.a1.dirty,true);
 assert.equal(f.state.task.graph_pending_humans[0].draft.values.note.raw,'原文证据已核对🙂');assert.equal(c.pending,null);
});

test('a newer server draft needs explicit comparison and preserves the local original',async()=>{
 const f=await fixture(),c=f.controller;fill(c);
 f.state.task.version++;f.state.task.graph_runtime.version++;f.state.task.graph_pending_humans[0].version++;f.state.task.graph_runtime.activations.a1.version++;
 f.state.task.graph_pending_humans[0].draft={schema:'graph-human-form-draft-v1',values:humanValues(schema,{approved:true,note:'另一页面服务器草稿'})};
 c.ingest(f.state.task);assert.equal(c.forms.a1.remoteConflict,true);assert.equal(c.forms.a1.values.note.raw,'原文证据已核对🙂');
 await assert.rejects(c.submit('draft','a1'));assert.equal(mutations(f).length,0);
 c.acknowledgeRemote('a1');await c.submit('draft','a1');assert.equal(c.forms.a1.remoteConflict,false);
});

test('another iteration at the same node never inherits the previous activation draft',async()=>{
 const f=await fixture(),c=f.controller;fill(c);const old=copy(f.state.task.graph_pending_humans[0]);old.activation_id='a3';
 f.state.task.graph_pending_humans=[old];f.state.task.version++;f.state.task.graph_runtime.activations.a3={...copy(f.state.task.graph_runtime.activations.a1),id:'a3'};
 c.ingest(f.state.task);assert.equal(c.forms.a3.values.note.mode,'unset');assert.equal(c.forms.a1.values.note.raw,'原文证据已核对🙂');
});

test('windows retain each distinct human draft and cannot replace a pending command',async()=>{
 const f=await fixture(),first=f.controller;fill(first);const second=createGraphTaskController({api:f.api,scope,storage:f.storage,session:f.session,locks:f.locks,crypto:webcrypto});second.ingest(f.state.task);
 second.edit('a1','note',{mode:'value',raw:'另一窗口原草稿'});first.edit('a1','note',{mode:'value',raw:'第一窗口新的草稿'});
 assert.ok(first.localRecoveries('a1').some(form=>form.values.note.raw==='另一窗口原草稿'));
 f.mode.unknownWithoutCommit=true;await assert.rejects(first.submit('complete','a1'));await assert.rejects(second.submit('draft','a1'));assert.equal(mutations(f).length,1);
});

test('scope and session changes hide foreign outboxes and prevent writes',async()=>{
 const f=await fixture(),c=f.controller;fill(c);f.mode.unknownWithoutCommit=true;await assert.rejects(c.submit('complete','a1'));
 const otherScope={...scope,actor_id:'10000000-0000-4000-8000-000000000004'},other=createGraphTaskController({api:f.api,scope:otherScope,storage:f.storage,session:undefined,locks:f.locks,crypto:webcrypto});
 assert.equal(other.pending,null);assert.notEqual(other.outboxKey,c.outboxKey);
 f.session.setItem('ga-setup-csrf-user',otherScope.actor_id);await assert.rejects(c.recover());assert.equal(mutations(f).length,1);assert.ok(f.storage.getItem(c.outboxKey));
});

test('missing WebLocks and failed local readback issue no mutations; unsaved work blocks leave',async()=>{
 const f=await fixture({locks:null}),c=f.controller;fill(c);await assert.rejects(c.submit('complete','a1'),/多窗口/);assert.equal(mutations(f).length,0);
 const g=await fixture();fill(g.controller);g.storage.fail=true;g.controller.edit('a1','note',{mode:'value',raw:'存储失败时仍保留本页'});
 assert.equal(g.controller.guard(),false);await assert.rejects(g.controller.submit('complete','a1'));assert.equal(mutations(g).length,0);
 g.storage.fail=false;assert.equal(g.controller.guard(),true);assert.equal(g.controller.forms.a1.values.note.raw,'存储失败时仍保留本页');
 g.storage.badReadback=true;await assert.rejects(g.controller.submit('complete','a1'));assert.equal(mutations(g).length,0);
});

test('cancel uses graph state version, preserves original output drafts and recovers its own receipt',async()=>{
 const f=await fixture(),c=f.controller;fill(c);c.editCancelReason('现场条件已变化，停止后续流程');await c.submit('cancel',null,c.cancelReason);
 const sent=JSON.parse(mutations(f)[0].body);assert.equal(sent.version,1);assert.equal(sent.cancel_epoch,0);assert.equal(c.detail.graph_runtime.status,'cancelled');
 assert.equal(c.forms.a1.dirty,true);assert.equal(c.forms.a1.values.note.raw,'原文证据已核对🙂');assert.equal(c.pending,null);
});

test('task detail from a different task and manual completion of a capability are rejected',async()=>{
 const f=await fixture(),c=f.controller;const foreign=task();foreign.id='foreign';assert.throws(()=>c.ingest(foreign));
 await assert.rejects(c.submit('complete','ability_activation'));assert.equal(mutations(f).length,0);
});

test('first panel admission reads task under verified current identity instead of rendering an old prop',async()=>{
 const f=await fixture(),fresh=createGraphTaskController({api:f.api,scope,storage:f.storage,session:f.session,locks:f.locks,crypto:webcrypto});
 assert.equal(fresh.detail,null);await fresh.open();assert.equal(fresh.detail.id,scope.task_id);
 const denied=createGraphTaskController({api:f.api,scope,storage:f.storage,session:f.session,locks:f.locks,crypto:webcrypto});
 f.state.me.user_id='10000000-0000-4000-8000-000000000099';await assert.rejects(denied.open());assert.equal(denied.detail,null);assert.equal(denied.denied,true);
});

test('old text or incompatible server draft is shown for explicit comparison and kept before replacement',async()=>{
 const f=await fixture(),c=f.controller;f.state.task.graph_pending_humans[0].draft='服务器原文字草稿🙂';
 c.ingest(f.state.task);assert.equal(c.forms.a1.remoteConflict,true);fill(c);
 await assert.rejects(c.submit('draft','a1'));assert.equal(mutations(f).length,0);
 c.acknowledgeRemote('a1');assert.deepEqual(c.forms.a1.server_drafts,['服务器原文字草稿🙂']);
 await c.submit('draft','a1');assert.deepEqual(c.forms.a1.server_drafts,['服务器原文字草稿🙂']);assert.equal(c.pending,null);
});

test('A confirmed draft receipt cannot acknowledge B newer server draft or adopt its CAS',async()=>{
 const f=await fixture(),c=f.controller;fill(c);const b=humanValues(schema,{approved:true,note:'B窗口较新草稿'});
 f.mode.afterWrite=()=>{
  const human=f.state.task.graph_pending_humans.find(item=>item.activation_id==='a1'),activation=f.state.task.graph_runtime.activations.a1;
  f.state.task.version++;f.state.task.graph_runtime.version++;human.version++;activation.version++;
  human.draft={schema:'graph-human-form-draft-v1',values:b};activation.draft=copy(human.draft);
 };
 const result=await c.submit('draft','a1');assert.equal(result.found,true);assert.equal(c.pending,null);
 assert.equal(c.forms.a1.values.note.raw,'原文证据已核对🙂');assert.equal(c.forms.a1.dirty,true);assert.equal(c.forms.a1.remoteConflict,true);assert.equal(c.forms.a1.base_version,0);
 assert.equal(c.detail.graph_pending_humans[0].draft.values.note.raw,'B窗口较新草稿');
 await assert.rejects(c.submit('draft','a1'));assert.equal(mutations(f).length,1);
 c.acknowledgeRemote('a1');assert.equal(c.forms.a1.base_version,2);assert.ok(c.forms.a1.server_drafts.some(value=>value.values.note.raw==='B窗口较新草稿'));
});

test('A draft receipt after later completion preserves A local work without marking it current',async()=>{
 const f=await fixture(),c=f.controller;fill(c);f.mode.afterWrite=()=>{
  f.state.task.version++;f.state.task.graph_runtime.version++;
  const activation=f.state.task.graph_runtime.activations.a1;activation.version++;activation.status='completed';activation.output={approved:true,note:'B已完成'};activation.draft=null;
  f.state.task.graph_pending_humans=f.state.task.graph_pending_humans.filter(item=>item.activation_id!=='a1');
 };
 await c.submit('draft','a1');assert.equal(c.pending,null);assert.equal(c.forms.a1.dirty,true);assert.equal(c.forms.a1.remoteConflict,true);assert.equal(c.forms.a1.base_version,0);
 assert.equal(c.forms.a1.values.note.raw,'原文证据已核对🙂');assert.ok(f.storage.getItem(c.prefix+'draft:a1'));
});

test('message retry has a separate v2 body, three-field receipt and durable Task record without a human command',async()=>{
 const f=await fixture({message:true}),c=f.controller;fill(c);
 const result=await c.retryMessages('恢复原检查，保留原等待窗口🙂');
 assert.equal(result.found,true);assert.equal(c.pending,null);assert.equal(c.pendingNotFound,false);
 const sent=JSON.parse(mutations(f)[0].body),receipt=f.state.task.graph_wait_retries[sent.request_id];
 assert.deepEqual(Object.keys(receipt).sort(),['actor_id','body_hash','job_id']);
 assert.deepEqual(receipt,{actor_id:scope.actor_id,job_id:messageJob,body_hash:await graphHash({actor_id:scope.actor_id,body:sent},webcrypto)});
 assert.equal(sent.job_id,messageJob);assert.equal(sent.job_version,4);assert.equal(f.state.task.graph_runtime.commands.length,0);
 assert.equal(f.state.task.graph_runtime.version,1);assert.equal(c.forms.a1.dirty,true);
 const history=JSON.parse(f.storage.getItem(c.prefix+'history:request:'+sent.request_id));
 assert.equal(history.pending.schema,'graph-task-outbox-v2');assert.equal(history.pending.expected_job_id,messageJob);
 assert.equal(mutations(f)[0].path,'/workspace/tasks/'+scope.task_id+'/graph/retry-messages-v2');
});

test('lost message retry response restores its exact receipt after reload even when the current poll Job changed',async()=>{
 const f=await fixture({message:true}),c=f.controller;f.mode.loseResponse=true;
 await assert.rejects(c.retryMessages('只恢复原消息检查'));
 const original=f.storage.getItem(c.outboxKey),pending=JSON.parse(original),nextJob='10000000-0000-4000-8000-000000000011';
 f.state.task.graph_wait_poll.job_id=nextJob;f.state.task.graph_message_status.error.job_id=nextJob;
 f.state.task.graph_message_status.error.job_version=8;f.state.task.graph_message_status.error.status='failed';f.state.task.graph_message_status.can_retry=true;
 const next=createGraphTaskController({api:f.api,scope,storage:f.storage,session:f.session,locks:f.locks,crypto:webcrypto});
 f.mode.loseResponse=false;assert.equal((await next.recover()).found,true);assert.equal(next.pending,null);
 assert.equal(mutations(f).length,1);assert.equal(f.state.task.graph_wait_retries[pending.body.request_id].job_id,messageJob);
 assert.equal(f.state.task.graph_wait_poll.job_id,nextJob);
});

test('unknown message retry survives readonly refresh and only explicit retry resends the original bytes after a negative probe',async()=>{
 const f=await fixture({message:true}),c=f.controller;fill(c);f.mode.unknownWithoutCommit=true;
 await assert.rejects(c.retryMessages('保留原输入与期限，不重新调用模型'));
 const original=f.storage.getItem(c.outboxKey),pending=JSON.parse(original);
 assert.equal(c.pendingNotFound,false);await c.refresh();assert.equal(f.storage.getItem(c.outboxKey),original);
 assert.equal(c.forms.a1.values.note.raw,'原文证据已核对🙂');assert.equal(c.forms.a1.dirty,true);
 assert.equal((await c.recover()).found,false);assert.equal(c.pendingNotFound,true);assert.equal(mutations(f).length,1);
 await assert.rejects(c.retryMessages('不能另建'));await assert.rejects(c.submit('draft','a1'));
 f.mode.unknownWithoutCommit=false;const before=f.calls.length;
 assert.equal((await c.resendMessageRetry()).found,true);
 assert.ok(f.calls.slice(before).findIndex(call=>call.path.endsWith('/graph/requests/'+pending.body.request_id))
  <f.calls.slice(before).findIndex(call=>call.method==='POST'));
 assert.equal(mutations(f).length,2);assert.equal(mutations(f)[1].body,mutations(f)[0].body);
 assert.equal(Object.keys(f.state.task.graph_wait_retries).length,1);assert.equal(c.pendingNotFound,false);
 assert.ok(mutations(f).every(call=>call.path.endsWith('/graph/retry-messages-v2')));
});

test('explicit message retry rechecks a now-accepted nonce instead of sending another POST',async()=>{
 const f=await fixture({message:true}),c=f.controller;f.mode.loseResponse=true;
 await assert.rejects(c.retryMessages('核对丢失的原回执'));f.mode.loseResponse=false;
 assert.equal((await c.resendMessageRetry()).found,true);assert.equal(mutations(f).length,1);assert.equal(c.pending,null);
});

for(const change of ['actor','hash','job','wrong-family','current-missing','current-hash'])test('message retry rejects '+change+' without discarding the original nonce',async()=>{
 const f=await fixture({message:true}),c=f.controller;
 if(change.startsWith('current-'))f.mode.mutateCurrent=value=>{
  const entry=Object.values(value.graph_wait_retries)[0];
  if(change==='current-missing')value.graph_wait_retries={};else entry.body_hash='b'.repeat(64);
 };
 else f.mode.mutateProbe=response=>{
  if(change==='actor')response.request.actor_id='10000000-0000-4000-8000-000000000099';
  else if(change==='hash')response.request.body_hash='b'.repeat(64);
  else if(change==='job')response.request.job_id='10000000-0000-4000-8000-000000000099';
  else response.request.activation_id='a3';
 };
 await assert.rejects(c.retryMessages('核对原检查回执'));assert.ok(c.pending);assert.ok(f.storage.getItem(c.outboxKey));
 f.mode.mutateProbe=null;f.mode.mutateCurrent=null;await c.recover();assert.equal(c.pending,null);assert.equal(mutations(f).length,1);
});

test('message retry checks the frozen local body hash and expected Job independently',async()=>{
 for(const change of ['body','expected-job']){
  const f=await fixture({message:true}),c=f.controller;f.mode.loseResponse=true;
  await assert.rejects(c.retryMessages('固定原检查和原因'));
  const saved=copy(c.pending);
  if(change==='body'){saved.body.reason='篡改原因';saved.exact_body=JSON.stringify(saved.body);}
  else saved.expected_job_id='10000000-0000-4000-8000-000000000099';
  f.storage.setItem(c.outboxKey,JSON.stringify(saved));await assert.rejects(c.recover());
  assert.ok(f.storage.getItem(c.outboxKey));assert.equal(mutations(f).length,1);
 }
});

test('new message retry freezes the selected Job and version before the fresh server read',async()=>{
 for(const change of ['job','version']){
  const f=await fixture({message:true}),c=f.controller;fill(c);
  f.mode.beforeRead=()=>{
   if(change==='version')f.state.task.graph_message_status.error.job_version++;
   else{f.state.task.graph_message_status.error.job_id='10000000-0000-4000-8000-000000000011';f.state.task.graph_wait_poll.job_id=f.state.task.graph_message_status.error.job_id;}
  };
  await assert.rejects(c.retryMessages('只恢复显示的原检查'),/变化/);
  assert.equal(mutations(f).length,0);assert.equal(c.currentPending(),null);assert.equal(c.forms.a1.dirty,true);
 }
});

test('the signed message target prevents a different same-version Job from being restored after the last GET',async()=>{
 const f=await fixture({message:true}),c=f.controller,nextJob='10000000-0000-4000-8000-000000000011';
 f.mode.beforeMessageWrite=()=>{
  f.state.task.graph_wait_poll.job_id=nextJob;f.state.task.graph_message_status.error.job_id=nextJob;
  // Both actual Jobs have version 4: a version alone is not a target pin.
  assert.equal(f.state.task.graph_message_status.error.job_version,4);
 };
 await assert.rejects(c.retryMessages('只恢复刚才核对的原检查'),/变化/);
 const sent=JSON.parse(mutations(f)[0].body);
 assert.equal(sent.job_id,messageJob);assert.equal(sent.job_version,4);
 assert.equal(c.pending.expected_job_id,sent.job_id);assert.equal(c.pending.rejected.status,409);
 assert.equal(f.state.task.graph_message_status.error.status,'failed');
 assert.equal(f.state.task.graph_wait_poll.job_id,nextJob);assert.deepEqual(f.state.task.graph_wait_retries,{});
 assert.equal(mutations(f).length,1);
});

test('an unpublished message outbox without its required signed Job target is preserved and never silently migrated',async()=>{
 const f=await fixture({message:true}),c=f.controller;f.mode.unknownWithoutCommit=true;
 await assert.rejects(c.retryMessages('保留原检查'));
 const old=copy(c.pending);delete old.body.job_id;old.exact_body=JSON.stringify(old.body);
 old.body_hash=await graphHash({actor_id:scope.actor_id,body:old.body},webcrypto);
 const encoded=JSON.stringify(old);f.storage.setItem(c.outboxKey,encoded);
 const restored=createGraphTaskController({api:f.api,scope,storage:f.storage,session:f.session,locks:f.locks,crypto:webcrypto});
 assert.equal(restored.denied,true);assert.equal(f.storage.getItem(c.outboxKey),encoded);
 await assert.rejects(restored.resendMessageRetry());assert.equal(mutations(f).length,1);
});

test('a missing message nonce cannot be resent to a changed Job or after retry authority was withdrawn',async()=>{
 for(const change of ['job','version','permission']){
  const f=await fixture({message:true}),c=f.controller;f.mode.unknownWithoutCommit=true;
  await assert.rejects(c.retryMessages('固定原检查'));
  const original=f.storage.getItem(c.outboxKey);
  if(change==='job'){f.state.task.graph_message_status.error.job_id='10000000-0000-4000-8000-000000000011';f.state.task.graph_wait_poll.job_id=f.state.task.graph_message_status.error.job_id;}
  else if(change==='version')f.state.task.graph_message_status.error.job_version++;
  else f.state.task.graph_message_status.can_retry=false;
  f.mode.unknownWithoutCommit=false;await assert.rejects(c.resendMessageRetry());
  assert.equal(f.storage.getItem(c.outboxKey),original);assert.equal(mutations(f).length,1);assert.equal(c.pendingNotFound,true);
 }
});

test('malformed message status blocks only new message retries and leaves human draft, cancel and old outboxes usable',async()=>{
 const f=await fixture({message:true}),c=f.controller;f.state.task.graph_message_status=null;c.ingest(f.state.task);fill(c);
 await assert.rejects(c.retryMessages('不能凭畸形状态恢复'));assert.equal(mutations(f).length,0);
 await c.submit('draft','a1');const history=JSON.parse(f.storage.getItem(c.prefix+'history:request:'+JSON.parse(mutations(f)[0].body).request_id));
 assert.equal(history.pending.schema,'graph-task-outbox-v1');assert.equal(history.pending.operation,'draft');
 f.mode.loseResponse=true;await assert.rejects(c.submit('cancel',null,'来源失效仍可取消'));
 const pending=copy(c.pending);assert.equal(pending.schema,'graph-task-outbox-v1');f.mode.loseResponse=false;
 await c.recover();assert.equal(c.pending,null);assert.equal(c.detail.graph_runtime.status,'cancelled');
});

test('accepted message recovery needs current Task record, page acceptance and local history before clearing',async()=>{
 let accepted=false;const f=await fixture({message:true,onSaved:async()=>accepted}),c=f.controller;
 f.mode.currentReadFails=true;await assert.rejects(c.retryMessages('保存原恢复回执'));assert.ok(c.pending);
 f.mode.currentReadFails=false;await assert.rejects(c.recover());assert.ok(c.pending);
 accepted=true;let breakStorage=true;c.onSaved=async()=>{if(breakStorage)f.storage.fail=true;return true;};
 await assert.rejects(c.recover());f.storage.fail=false;assert.ok(f.storage.getItem(c.outboxKey));
 breakStorage=false;await c.recover();assert.equal(c.pending,null);assert.equal(mutations(f).length,1);
});

test('rejected message recovery can only be archived after a fresh negative query, never silently rewritten',async()=>{
 const f=await fixture({message:true}),c=f.controller;fill(c);f.mode.reject=true;
 await assert.rejects(c.retryMessages('恢复被拒的原检查'));const pending=copy(c.pending);
 assert.equal(pending.rejected.status,409);await assert.rejects(c.resendMessageRetry());
 await c.archiveRejected();assert.equal(c.pending,null);assert.equal(c.forms.a1.dirty,true);
 assert.ok(f.storage.getItem(c.prefix+'history:request:'+pending.body.request_id));assert.equal(mutations(f).length,1);
});

test('message retry storage or lock failure issues no POST and blocks unsafe leave',async()=>{
 const locked=await fixture({message:true,locks:null});await assert.rejects(locked.controller.retryMessages('缺少多窗口保护'));
 assert.equal(mutations(locked).length,0);
 const f=await fixture({message:true});fill(f.controller);f.storage.fail=true;
 await assert.rejects(f.controller.retryMessages('存储故障时保留草稿'));assert.equal(mutations(f).length,0);assert.equal(f.controller.guard(),false);
 f.storage.fail=false;assert.equal(f.controller.guard(),true);f.storage.badReadback=true;
 await assert.rejects(f.controller.retryMessages('回读失败不提交'));assert.equal(mutations(f).length,0);
});

test('readonly refresh preserves pending bytes and dirty forms while excluding concurrent mutations and late disposed reads',async()=>{
 const f=await fixture({message:true}),c=f.controller;fill(c);f.mode.unknownWithoutCommit=true;
 await assert.rejects(c.retryMessages('保留原提交'));const original=f.storage.getItem(c.outboxKey);
 let release,entered;const waiting=new Promise(resolve=>{entered=resolve;});
 f.mode.beforeRead=()=>new Promise(resolve=>{release=resolve;entered();});
 const refresh=c.refresh();await waiting;
 await assert.rejects(c.refresh());await assert.rejects(c.submit('draft','a1'));await assert.rejects(c.retryMessages('不能并发'));
 release();await refresh;assert.equal(f.storage.getItem(c.outboxKey),original);assert.equal(c.forms.a1.dirty,true);
 assert.equal(c.forms.a1.values.note.raw,'原文证据已核对🙂');assert.equal(mutations(f).length,1);
 let finish,ready;const blocked=new Promise(resolve=>{ready=resolve;});f.mode.beforeRead=()=>new Promise(resolve=>{finish=resolve;ready();});
 const old=c.refresh();await blocked;c.dispose();finish();await assert.rejects(old);assert.equal(f.storage.getItem(c.outboxKey),original);
});

test('message retry shares the actor-workspace-task lock and cannot replace another window pending request',async()=>{
 let tail=Promise.resolve();const locks={request(_key,_options,fn){const run=tail.then(fn);tail=run.catch(()=>{});return run;}};
 const f=await fixture({message:true,locks}),first=f.controller;
 const second=createGraphTaskController({api:f.api,scope,storage:f.storage,session:f.session,locks,crypto:webcrypto});second.ingest(f.state.task);
 f.mode.unknownWithoutCommit=true;const results=await Promise.allSettled([first.retryMessages('第一窗口原检查'),second.retryMessages('第二窗口不能替代')]);
 assert.ok(results.every(result=>result.status==='rejected'));assert.equal(mutations(f).length,1);
 assert.equal(JSON.parse(f.storage.getItem(first.outboxKey)).body.reason,'第一窗口原检查');
 const other=createGraphTaskController({api:f.api,scope:{...scope,task_id:'10000000-0000-4000-8000-000000000099'},storage:f.storage,session:f.session,locks,crypto:webcrypto});
 assert.equal(other.pending,null);assert.notEqual(other.outboxKey,first.outboxKey);
 f.session.setItem('ga-setup-csrf-workspace','10000000-0000-4000-8000-000000000099');await assert.rejects(first.recover());
 assert.ok(f.storage.getItem(first.outboxKey));assert.equal(mutations(f).length,1);
});
