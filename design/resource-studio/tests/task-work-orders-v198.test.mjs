// Actual controller and closed server DTOs; invented authenticated IO only.
// Root runs this file. No DB, device, independent acceptance or browser proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createTaskWorkOrdersController,checkedWorkOrderList,checkedWorkOrder,WORK_ORDERS_BASE} from '../src/task-work-orders-v198.mjs';

const id=n=>'19800000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=v=>JSON.parse(JSON.stringify(v));
const scope={actor_id:id(1),workspace_id:id(2),task_id:id(3)};
const flags={execution_completed:false,independent_acceptance:false,world_state_verified:false};
class Memory{constructor(){this.values=new Map();this.fail=false;}getItem(k){if(this.fail)throw Error('synthetic storage unavailable');return this.values.get(k)??null;}setItem(k,v){if(this.fail)throw Error('synthetic storage unavailable');this.values.set(k,String(v));}removeItem(k){if(this.fail)throw Error('synthetic storage unavailable');this.values.delete(k);}}
function order(){return {schema:'workflow-work-order-view-v198',id:id(4),batch_id:id(5),parent_task_id:scope.task_id,title:'核对受损样地',description:'到登记样地核对，保存已有资料和现场说明。',expected_result:'提交核对说明，由任务负责人另行验收。',assignee_id:scope.actor_id,assignee_name:'样地核对员',due_at:'2026-10-25T18:00:00+08:00',simulation_only:true,scope:'human_work_orders',status:'assigned',version:1,note:'',updated_at:'2026-10-24T10:00:00+08:00',history:[{version:1,status:'assigned',operation:'assigned',note:'',actor_id:id(6),actor_name:'任务负责人',at:'2026-10-24T10:00:00+08:00',request_id:null}],...flags,permissions:{progress:true,submit:true,return:false,cancel:false}};}
function fixture({storage=new Memory(),session=new Memory(),state=null}={}){
 session.setItem('ga-setup-csrf-user',scope.actor_id);session.setItem('ga-setup-csrf-workspace',scope.workspace_id);session.setItem('ga-setup-csrf','synthetic-work-order-csrf');
 const calls=[],mode={},saved=new Map(),events=[],server=state||{item:order(),me:{user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'}};
 const api=async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,body:options.body,headers:options.headers});
  assert.equal(options.headers?.['X-Workspace-ID'],scope.workspace_id);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-work-order-csrf');
  if(path==='/me')return copy(server.me);
  const url=new URL('https://synthetic.invalid'+path);
  if(path.startsWith(WORK_ORDERS_BASE+'/requests/')){const nonce=url.pathname.split('/').at(-1),result=saved.get(nonce)||null,sha=url.searchParams.get('expected_sha256');const reply={schema:'workflow-work-order-original-result-v198',request_id:nonce,found:!!result,body_sha256:sha,result:copy(result)};mode.modifyRecovery?.(reply);return reply;}
  if(method==='GET'&&url.pathname===WORK_ORDERS_BASE){assert.equal(url.searchParams.get('parent_task_id'),scope.task_id);assert.ok(['assigned','dispatched'].includes(url.searchParams.get('mode')));return {schema:'workflow-work-order-list-v198',scope:'human_work_orders',mode:url.searchParams.get('mode'),items:[copy(server.item)],...flags};}
  if(method==='GET'&&path===WORK_ORDERS_BASE+'/'+server.item.id){const result=copy(server.item);mode.modifyDetail?.(result);return result;}
  assert.equal(method,'POST');const operation=url.pathname.split('/').at(-1);assert.ok(['progress','submit','return','cancel'].includes(operation));
  const pending=JSON.parse(storage.getItem(c.outboxKey));assert.equal(options.body,pending.raw,'exact original is durable before POST');assert.equal(pending.sha256,await c.hash(options.body));assert.deepEqual(Object.keys(JSON.parse(options.body)).sort(),['expected_version','note','request_id']);
  if(mode.reject||mode.rejectBound){const body=JSON.parse(options.body);if(mode.rejectBound&&server.item.version===1){const item=server.item;item.version=2;item.status='in_progress';item.note='另一窗口先保存的说明';item.updated_at='2026-10-24T10:30:00+08:00';item.history.push({version:2,status:item.status,operation:'progress',note:item.note,actor_id:scope.actor_id,actor_name:'核对员',at:item.updated_at,request_id:id(91)});}const error=Error('synthetic conflict');error.status=409;error.detail={code:'workflow_work_order_version_conflict',not_applied:true,current_version:2,...(mode.rejectBound?{request_id:body.request_id,body_sha256:pending.sha256,item_id:server.item.id,operation}:{})};mode.modifyRejection?.(error.detail);throw error;}
  if(mode.unknown)throw Error('synthetic transport unknown');
  const body=JSON.parse(options.body);assert.equal(body.expected_version,server.item.version);
  const item=server.item;item.version++;item.status=({progress:'in_progress',submit:'completion_reported',return:'returned',cancel:'cancelled'})[operation];item.note=body.note;item.updated_at='2026-10-24T11:00:00+08:00';item.history.push({version:item.version,status:item.status,operation,note:body.note,actor_id:scope.actor_id,actor_name:'样地核对员',at:item.updated_at,request_id:body.request_id});
  if(operation==='submit')item.permissions={progress:false,submit:false,return:false,cancel:false};
  const original=copy(item);delete original.permissions;
  const reply={schema:'workflow-work-order-result-v198',request_id:body.request_id,item:original,...flags};saved.set(body.request_id,copy(reply));events.push('saved');
  if(mode.lostAfterCommit)throw Error('synthetic response lost after commit');return copy(reply);
 };
 const locks={request:async(_name,_options,run)=>run()},onSaved=async value=>{events.push(value);};
 const c=createTaskWorkOrdersController({api,scope,storage,session,crypto:webcrypto,locks,onSaved});c.mount();
 return {c,api,scope,storage,session,server,calls,saved,mode,events,locks,onSaved};
}
async function selected(f){await f.c.open();await f.c.select(f.server.item.id);return f;}
const posts=f=>f.calls.filter(row=>row.method==='POST');

test('actual lists are Task scoped and permissions, not a role guess, govern commands',async()=>{
 const f=await selected(fixture());assert.equal(posts(f).length,0);assert.equal(f.c.detail.title,'核对受损样地');
 f.c.edit('未提交的核对说明');await f.c.setMode('dispatched');await f.c.select(f.server.item.id);assert.equal(f.c.form.note,'未提交的核对说明');assert.ok(f.c.safeToLeave());
 f.server.me.role='admin';f.server.item.permissions={progress:false,submit:false,return:false,cancel:false};await f.c.refresh();assert.equal(f.c.canWriteNote,false);
 await assert.rejects(()=>f.c.submit('progress'));assert.equal(posts(f).length,0);
 const bad=copy(f.server.item);bad.world_state_verified=true;assert.throws(()=>checkedWorkOrder(bad,scope));
 const list={schema:'workflow-work-order-list-v198',scope:'human_work_orders',mode:'assigned',items:[order(),order()],...flags};assert.throws(()=>checkedWorkOrderList(list,scope,'assigned'));
});

test('one actual POST is preceded by durable exact bytes and completed only after original GET plus detail history',async()=>{
 const f=await selected(fixture());f.c.edit('保留空格与换行：\n  已核对，但现场效果尚待独立验收。');const input=f.c.form.note;
 const reply=await f.c.submit('submit');assert.equal(reply.found,true);assert.equal(posts(f).length,1);assert.equal(f.c.pending,null);assert.equal(f.c.form.note,input);assert.equal(f.c.detail.status,'completion_reported');assert.ok(f.c.notice.includes('尚不表示独立验收'));
 const p=posts(f)[0],body=JSON.parse(p.body);assert.equal(body.note,input);assert.equal(body.expected_version,1);assert.ok(f.storage.getItem(f.c.prefix+'original:'+body.request_id));assert.ok(f.storage.getItem(f.c.prefix+'completed:'+body.request_id));assert.equal(f.storage.getItem(f.c.outboxKey),null);
 const original=f.calls.find(row=>row.path.includes('/requests/'));assert.ok(original);assert.equal(original.method,'GET');assert.equal(f.events.at(-1).kind,'work_order');assert.equal(f.events.at(-1).request_id,body.request_id);
});

test('a committed but lost POST response survives remount and is recovered only by its original nonce',async()=>{
 const f=await selected(fixture());f.c.edit('人工办理说明必须完整保留');f.mode.lostAfterCommit=true;await assert.rejects(()=>f.c.submit('progress'));
 const original=copy(f.c.pending),stored=f.storage.getItem(f.c.outboxKey);assert.equal(posts(f).length,1);f.c.dispose();
 const restored=createTaskWorkOrdersController({api:f.api,scope,storage:f.storage,session:f.session,crypto:webcrypto,locks:f.locks,onSaved:f.onSaved});restored.mount();await restored.open();
 assert.equal(posts(f).length,1);assert.equal(restored.pending,null);assert.equal(restored.form.note,original.body.note);assert.ok(f.storage.getItem(restored.prefix+'original:'+original.body.request_id));assert.ok(stored.includes(original.sha256));
});

for(const failure of ['unknown','reject'])test(failure+' keeps original nonce after found:false and never retries POST or enables editing',async()=>{
 const f=await selected(fixture());f.c.edit('这份尚未核对的原说明不能丢失');f.mode[failure]=true;await assert.rejects(()=>f.c.submit('progress'));
 const raw=f.c.pending.raw,nonce=f.c.pending.body.request_id,sha=f.c.pending.sha256;await f.c.recover();await f.c.recover();assert.equal(posts(f).length,1);assert.equal(f.c.pending.raw,raw);assert.equal(f.c.pending.body.request_id,nonce);assert.equal(f.c.pending.sha256,sha);assert.equal(f.c.canWriteNote,false);assert.ok(f.c.safeToLeave());
 assert.throws(()=>f.c.edit('不得覆盖原待核对命令'));await assert.rejects(()=>f.c.submit('submit'));assert.equal(posts(f).length,1);assert.ok(f.c.notice.includes('不能证明未提交'));
 await assert.rejects(()=>f.c.useRejectedLatest());assert.equal(f.c.pending.raw,raw);assert.equal(posts(f).length,1);
});

test('fully bound authoritative CAS rejection requires explicit original GET and fresh read before retaining and editing a new version',async()=>{
 const f=await selected(fixture());f.c.edit('被拒旧版说明必须全文留下');f.mode.rejectBound=true;await assert.rejects(()=>f.c.submit('progress'));const original=copy(f.c.pending),nonce=original.body.request_id;assert.equal(f.c.pending.rejection.not_applied,true);assert.equal(posts(f).length,1);
 await f.c.recover();assert.ok(f.c.pending,'found:false alone must not clear even an authoritative rejection');assert.equal(f.c.state.rejections.length,0);
 await f.c.useRejectedLatest();assert.equal(f.c.pending,null);assert.equal(posts(f).length,1,'explicit edit recovery does not submit');assert.equal(f.c.form.version,2);assert.equal(f.c.form.note,original.body.note);assert.equal(f.c.state.rejections[0].request_id,nonce);assert.equal(JSON.parse(f.storage.getItem(f.c.prefix+'rejected:'+nonce)).original.raw,original.raw);assert.ok(f.c.notice.includes('尚未重新提交'));
 f.mode.rejectBound=false;await f.c.submit('progress');assert.equal(posts(f).length,2);const next=JSON.parse(posts(f)[1].body);assert.notEqual(next.request_id,nonce);assert.equal(next.expected_version,2);assert.equal(next.note,original.body.note);
});

for(const field of ['request_id','body_sha256','item_id','operation'])test('a mismatched CAS '+field+' is not authority to drop original pending',async()=>{
 const f=await selected(fixture());f.c.edit('不接受其他原请求的拒绝证明');f.mode.rejectBound=true;f.mode.modifyRejection=d=>{d[field]=field==='body_sha256'?'f'.repeat(64):field==='operation'?'submit':id(99);};await assert.rejects(()=>f.c.submit('progress'));assert.equal(f.c.pending.rejection,undefined);await assert.rejects(()=>f.c.useRejectedLatest());assert.ok(f.c.pending);assert.equal(posts(f).length,1);
});

test('local write failure protects memory, forbids POST, recovers guard after write service returns',async()=>{
 const f=await selected(fixture());f.storage.fail=true;f.c.edit('只有本页暂存的人工输入');assert.equal(f.c.localSaved,false);assert.equal(f.c.safeToLeave(),false);assert.equal(f.c.form.note,'只有本页暂存的人工输入');await assert.rejects(()=>f.c.submit('progress'));assert.equal(posts(f).length,0);
 f.storage.fail=false;assert.equal(f.c.retryLocal(),true);assert.ok(f.c.safeToLeave());f.c.busy=true;assert.equal(f.c.safeToLeave(),false);f.c.busy=false;assert.ok(f.c.safeToLeave());
 await f.c.submit('progress');assert.equal(posts(f).length,1);
});

test('unsubmitted work survives current version changes and requires explicit preservation before adopting its CAS',async()=>{
 const f=await selected(fixture());f.c.edit('我尚未提交的独立说明');const server=f.server.item;server.version=2;server.note='另一窗口已经记录';server.status='in_progress';server.updated_at='2026-10-24T10:30:00+08:00';server.history.push({version:2,status:server.status,operation:'progress',note:server.note,actor_id:scope.actor_id,actor_name:'核对员',at:server.updated_at,request_id:id(90)});
 await f.c.refresh();assert.equal(f.c.form.note,'我尚未提交的独立说明');assert.equal(f.c.form.conflict,true);await assert.rejects(()=>f.c.submit('progress'));assert.equal(posts(f).length,0);
 f.c.keepInputOnLatest();assert.equal(f.c.form.version,2);assert.equal(f.c.form.note,'我尚未提交的独立说明');assert.ok([...f.storage.values.keys()].some(key=>key.startsWith(f.c.prefix+'retained:')));await f.c.submit('progress');assert.equal(JSON.parse(posts(f)[0].body).expected_version,2);
});

test('tampered recovery digest or history cannot clear the original command',async()=>{
 const f=await selected(fixture());f.c.edit('完整原件');f.mode.lostAfterCommit=true;await assert.rejects(()=>f.c.submit('progress'));const raw=f.c.pending.raw;
 f.mode.modifyRecovery=value=>{value.body_sha256='f'.repeat(64);};await assert.rejects(()=>f.c.recover());assert.equal(f.c.pending.raw,raw);
 delete f.mode.modifyRecovery;f.mode.modifyDetail=item=>{item.history.at(-1).note='changed history';item.note='changed history';};await assert.rejects(()=>f.c.recover());assert.equal(f.c.pending.raw,raw);assert.equal(posts(f).length,1);
});

test('identity changes conceal work without overwriting original scope storage or posting',async()=>{
 const f=await selected(fixture());f.c.edit('原账号私人输入');const before=copy([...f.storage.values]);f.session.setItem('ga-setup-csrf-user',id(99));await assert.rejects(()=>f.c.identity());assert.equal(f.c.denied,true);assert.equal(f.c.readable,false);assert.deepEqual([...f.storage.values],before);assert.equal(posts(f).length,0);
});

test('oversize notes remain complete local work and are rejected before a command is created',async()=>{
 const f=await selected(fixture()),whole='字'.repeat(12001);f.c.edit(whole);assert.equal(f.c.form.note,whole);assert.ok(f.c.safeToLeave());await assert.rejects(()=>f.c.submit('progress'),/12000/);assert.equal(posts(f).length,0);assert.equal(f.c.pending,null);assert.ok(f.c.exportRecord().includes(whole));
});
