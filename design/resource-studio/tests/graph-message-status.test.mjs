// Display-only invented tasks. These fixtures grant no source/runtime authority.
import test from 'node:test';
import assert from 'node:assert/strict';
import {GRAPH_MESSAGE_RETRY_REASON,inspectGraphMessageStatus,formatGraphMessageTime,
 graphMessageStatusView} from '../src/graph-message-status.mjs';

const job='10000000-0000-4000-8000-000000000001';
const event='10000000-0000-4000-8000-000000000002';
const taskID='10000000-0000-4000-8000-000000000003';
const copy=value=>JSON.parse(JSON.stringify(value));

export function graphMessageTaskFixture({status='open',retry=false}={}){
 const closed=status==='open'?null:status==='timed_out'?160:130;
 const window={activation_id:'a3',status,opened_at:100,deadline:160,closed_at:closed,event_id:status==='consumed'?event:null};
 const activation={id:'a3',node_id:'wait_results',status:({open:'waiting_event',consumed:'completed',timed_out:'timed_out',cancelled:'cancelled'})[status],
  created_at:90,wait_started_at:100,deadline:160,completed_at:['consumed','timed_out'].includes(status)?closed:null};
 return {id:taskID,business_name:'合成资源核对',graph_runtime:{status:status==='cancelled'?'cancelled':'waiting',
  clock:closed??100,activations:{a3:activation}},graph_wait_poll:{job_id:job},
  graph_node_labels:{a3:{name:'接收巡视处理结果',source_step_id:null}},
  business:{flow:{steps:[]}},graph_message_status:{arrival_basis:'durable_admission',last_checked_at:closed??100,
   error:retry?{job_id:job,reason:'合成来源核对失败',job_version:4,status:'failed',attempts:1}:null,
   can_retry:retry,windows:[window],candidate_only:true,physical_state_verified:false}};
}

function invalid(change,code){
 const task=graphMessageTaskFixture();change(task);
 assert.throws(()=>inspectGraphMessageStatus(task),error=>error.code===code&&/原草稿和任务记录保留/.test(error.message));
}

test('old tasks without the projection stay compatible; present null or undefined is not absence',()=>{
 assert.equal(inspectGraphMessageStatus({}),null);
 assert.equal(graphMessageStatusView({graph_runtime:{status:'waiting'}}),null);
 for(const value of [null,undefined,[],true]){
  assert.throws(()=>inspectGraphMessageStatus({graph_message_status:value}),error=>error.code==='status_shape');
 }
});

test('validated projection is detached and retains exact original fields',()=>{
 const task=graphMessageTaskFixture({retry:true}),before=copy(task),status=inspectGraphMessageStatus(task);
 assert.deepEqual(status,task.graph_message_status);
 assert.deepEqual(Object.keys(status).sort(),['arrival_basis','last_checked_at','error','can_retry','windows','candidate_only','physical_state_verified'].sort());
 status.windows[0].status='cancelled';status.error.reason='changed';
 assert.deepEqual(task,before);
});

for(const [name,change,code] of [
 ['extra projection field',task=>task.graph_message_status.extra=true,'status_shape'],
 ['wrong arrival basis',task=>task.graph_message_status.arrival_basis='device_capture_time','evidence_boundary'],
 ['claiming fact verification',task=>task.graph_message_status.physical_state_verified=true,'evidence_boundary'],
 ['candidate flag as integer',task=>task.graph_message_status.candidate_only=1,'evidence_boundary'],
 ['truthy retry flag',task=>task.graph_message_status.can_retry='true','status_fields'],
 ['invalid checked time',task=>task.graph_message_status.last_checked_at=-1,'status_fields'],
 ['unknown graph state',task=>task.graph_runtime.status='timeout','runtime_shape'],
 ['clock before checked cut',task=>task.graph_runtime.clock=99,'runtime_shape'],
 ['not a window list',task=>task.graph_message_status.windows={},'window_inventory'],
 ['duplicate activation',task=>task.graph_message_status.windows.push(copy(task.graph_message_status.windows[0])),'window_identity'],
 ['unknown window phase',task=>task.graph_message_status.windows[0].status='waiting_event','window_closed'],
 ['missing activation',task=>delete task.graph_runtime.activations.a3,'window_activation'],
 ['unopened wait mismatch',task=>task.graph_runtime.activations.a3.wait_started_at=null,'window_time'],
 ['changed deadline',task=>task.graph_message_status.windows[0].deadline=170,'window_time'],
 ['open with a receipt',task=>task.graph_message_status.windows[0].event_id=event,'window_open'],
 ['open with close time',task=>task.graph_message_status.windows[0].closed_at=100,'window_open'],
 ['extra window field',task=>task.graph_message_status.windows[0].producer_id=job,'window_shape'],
])test('invalid '+name+' throws instead of silently falling back',()=>invalid(change,code));

test('open remains open after both displayed deadline and last checked time',()=>{
 const task=graphMessageTaskFixture();task.graph_runtime.clock=200;task.graph_runtime.activations.a3.status='waiting_guard';
 task.graph_message_status.last_checked_at=200;
 const view=graphMessageStatusView(task);
 assert.equal(view.windows[0].status,'open');assert.equal(view.windows[0].title,'等待已登记来源的处理结果');
 assert.match(view.windows[0].detail,/不会.*改判为超时/);
 assert.equal(view.windows[0].closedAt,null);assert.equal(view.status.can_retry,false);
});

test('consumed, server timeout and cancellation remain distinct saved outcomes',()=>{
 const consumed=graphMessageStatusView(graphMessageTaskFixture({status:'consumed'}));
 const timed=graphMessageStatusView(graphMessageTaskFixture({status:'timed_out'}));
 const cancelled=graphMessageStatusView(graphMessageTaskFixture({status:'cancelled'}));
 assert.equal(consumed.windows[0].title,'处理结果已到达');assert.match(consumed.windows[0].detail,/仍需.*核实/);
 assert.equal(timed.windows[0].title,'后台已确认等待超时');
 assert.equal(cancelled.windows[0].title,'该项等待已取消');assert.equal(cancelled.terminal,true);
 assert.equal(consumed.status.physical_state_verified,false);
});

test('a claimed consumed receipt, premature timeout and mismatched closure are rejected',()=>{
 const consumed=graphMessageTaskFixture({status:'consumed'});consumed.graph_message_status.windows[0].event_id=null;
 assert.throws(()=>inspectGraphMessageStatus(consumed),error=>error.code==='window_consumed');
 const timed=graphMessageTaskFixture({status:'timed_out'});timed.graph_message_status.windows[0].closed_at=159;
 timed.graph_runtime.activations.a3.completed_at=159;
 assert.throws(()=>inspectGraphMessageStatus(timed),error=>error.code==='window_timeout');
 const cancelled=graphMessageTaskFixture({status:'cancelled'});cancelled.graph_runtime.activations.a3.status='ready';
 assert.throws(()=>inspectGraphMessageStatus(cancelled),error=>error.code==='window_cancelled');
});

test('server true needs the actual original job version, recoverable status and nonterminal task',()=>{
 const task=graphMessageTaskFixture({retry:true});assert.equal(inspectGraphMessageStatus(task).can_retry,true);
 for(const change of [task=>task.graph_message_status.error=null,
  task=>task.graph_message_status.error.job_version=0,
  task=>task.graph_message_status.error.status='queued',
  task=>task.graph_runtime.status='stopped',
  task=>task.graph_wait_poll.job_id=event]){
  const altered=copy(task);change(altered);assert.throws(()=>inspectGraphMessageStatus(altered));
 }
});

test('server false is never promoted by job failure or by a task permission flag',()=>{
 const task=graphMessageTaskFixture({retry:true});task.graph_message_status.can_retry=false;
 task.permissions={can_cancel_task:true,can_edit:true};
 assert.equal(graphMessageStatusView(task).status.can_retry,false);
});

test('unresolved error may omit job enrichment but cannot authorize a retry',()=>{
 const task=graphMessageTaskFixture();task.graph_message_status.error={job_id:job,reason:''};
 assert.equal(inspectGraphMessageStatus(task).error.reason,'');
 assert.equal(graphMessageStatusView(task).problem.kind,'blocked');
 task.graph_message_status.error.job_version=4;
 assert.throws(()=>inspectGraphMessageStatus(task),error=>error.code==='error_job_fields');
});

for(const [name,change] of [
 ['unknown status',error=>error.status='response_unknown'],
 ['negative attempts',error=>error.attempts=-1],
 ['fractional version',error=>error.job_version=1.5],
 ['boolean attempts',error=>error.attempts=true],
 ['unknown field',error=>error.stack='raw stack'],
 ['malformed job identity',error=>error.job_id='job-opaque'],
])test('malformed error '+name+' cannot enable retry',()=>{
 const task=graphMessageTaskFixture({retry:true});change(task.graph_message_status.error);
 assert.throws(()=>inspectGraphMessageStatus(task));
});

for(const phase of ['queued','running'])test('old reason during '+phase+' is shown as an existing recovery request, never another failure',()=>{
 const task=graphMessageTaskFixture({retry:true});task.graph_message_status.can_retry=false;
 task.graph_message_status.error.status=phase;task.graph_message_status.error.job_version=5;
 task.graph_message_status.error.reason='SELECT secret_payload FROM private_table WHERE job_id='+job;
 const view=graphMessageStatusView(task);
 assert.equal(view.problem.title,'上次检查问题已申请恢复');assert.equal(view.problem.kind,phase);
 assert.match(view.problem.detail,/尚不代表检查成功/);
 assert.doesNotMatch(JSON.stringify({problem:view.problem,windows:view.windows}),/SELECT|private_table|secret_payload|10000000-0000/);
});

test('a finished old check and an ended task do not offer a new waiting window',()=>{
 const task=graphMessageTaskFixture({retry:true});task.graph_message_status.can_retry=false;
 task.graph_message_status.error.status='succeeded';assert.equal(graphMessageStatusView(task).problem.kind,'ended');
 task.graph_message_status.error.status='failed';task.graph_runtime.status='cancelled';
 task.graph_runtime.activations.a3.status='cancelled';task.graph_message_status.windows[0].status='cancelled';
 task.graph_message_status.windows[0].closed_at=100;
 const view=graphMessageStatusView(task);assert.equal(view.problem.kind,'history');assert.equal(view.terminal,true);
 assert.match(view.problem.detail,/不会重新开启等待/);
});

test('business labels are used and opaque fallback names never become the main label',()=>{
 const task=graphMessageTaskFixture();assert.equal(graphMessageStatusView(task).windows[0].label,'接收巡视处理结果');
 task.graph_node_labels.a3={source_step_id:'source_step',name:'wait_results'};
 task.business.flow.steps=[{id:'source_step',name:'等待复核回执'}];
 assert.equal(graphMessageStatusView(task).windows[0].label,'等待复核回执');
 task.business.flow.steps=[];task.graph_node_labels.a3.name=job;
 assert.equal(graphMessageStatusView(task).windows[0].label,'等待处理结果 1');
});

test('times use explicit Beijing timezone and display raw seconds as seconds',()=>{
 assert.equal(formatGraphMessageTime(0),'1970年01月01日 08:00:00（北京时间）');
 assert.equal(formatGraphMessageTime(100),'1970年01月01日 08:01:40（北京时间）');
 for(const value of [null,'100',true,-1,NaN,Infinity,8640000000001])assert.throws(()=>formatGraphMessageTime(value));
});

test('invalid numeric and string JSON values are never normalized to a successful status',()=>{
 for(const value of [NaN,Infinity])invalid(task=>task.graph_message_status.last_checked_at=value,'projection_number');
 for(const value of ['\0','\ud800']){
  const task=graphMessageTaskFixture({retry:true});task.graph_message_status.error.reason=value;
  assert.throws(()=>inspectGraphMessageStatus(task),error=>error.code==='projection_text');
 }
});

test('fixed retry reason preserves the old window and never asks for human diagnostic input',()=>{
 assert.equal(GRAPH_MESSAGE_RETRY_REASON,'恢复原消息检查；保留原等待窗口、期限、输入与处理回执。');
 const task=graphMessageTaskFixture();task.graph_message_status.windows=[];task.graph_runtime.activations={};
 const view=graphMessageStatusView(task);assert.deepEqual(view.windows,[]);assert.equal(view.status.can_retry,false);
});
