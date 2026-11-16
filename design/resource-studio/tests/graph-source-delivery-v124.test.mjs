import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {GraphSourceDeliveryControllerV124,deliveryRetryCommandHash,deliveryRetirementCommandHash,validDeliveryRetirementReason,inspectDeliveryRecord} from '../src/graph-source-delivery-controller-v124.mjs';
import {GRAPH_SOURCE_DELIVERY_RECOVERY_MAX_RECORD_LENGTH as MAX} from '../src/graph-source-delivery-recovery-v124.mjs';

const ID=n=>'84000000-0000-7000-8000-'+String(n).padStart(12,'0'),H='a'.repeat(64),copy=x=>JSON.parse(JSON.stringify(x));
const scope={actor_id:ID(1),workspace_id:ID(2)},BASE='/setup/graph-event-producers';
const sorted=x=>Array.isArray(x)?x.map(sorted):x!==null&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,sorted(x[k])])):x;
const digest=x=>createHash('sha256').update(JSON.stringify(sorted(x))).digest('hex');
class Memory{
 constructor(entries=[]){this.data=new Map(entries);this.fail=false;}
 get length(){return this.data.size;}key(i){return [...this.data.keys()][i]??null;}getItem(k){return this.data.get(k)??null;}
 setItem(k,v){if(this.fail)throw Error('synthetic quota');this.data.set(k,String(v));}removeItem(k){this.data.delete(k);}
}
const session=()=>new Memory([['ga-setup-csrf-user',scope.actor_id],['ga-setup-csrf-workspace',scope.workspace_id],['ga-setup-csrf','synthetic']]);
const record=(options={})=>({schema:'graph-event-delivery-record-v1',intent_id:ID(3),producer_id:ID(4),actor_id:scope.actor_id,intent_hash:H,source_label:'公开合成温度结果',object:{id:ID(5),name:'合成对象甲'},observation_id:ID(6),observed_at:'2026-10-01T00:00:00+00:00',candidate_only:true,source_time_verified:false,physical_state_verified:false,intent_status:'pending',admission:null,delivery:{job_id:ID(7),status:'failed',version:3,attempts:1},action:{allowed:true,kind:'retry_admission',reason_code:'retry_available',label:'可恢复原结果',remaining_retries:32},result:{schema:{type:'object',properties:{temperature:{type:'number',title:'温度'}},required:['temperature']},value:{temperature:23.5}},...options});
async function fixture(){
 const storage=new Memory(),state={record:record(),actor:scope.actor_id,role:'operator',lost:false,noCommit:false,outcome:'queued',receipts:new Map(),legacy:new Map(),retirements:new Map(),retired:new Map(),retirementLost:false,retirementNoCommit:false,enforceVersion:false,detailFailure:false,holdMe:false,meWaiters:[]},calls=[];
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});assert.equal(options.headers['X-Workspace-ID'],scope.workspace_id);assert.equal(options.headers['X-CSRF-Token'],'synthetic');
  if(path==='/me'){const actor=state.actor;if(state.holdMe)await new Promise(resolve=>state.meWaiters.push(()=>resolve()));return {user_id:actor,workspace_id:scope.workspace_id,role:state.role};}
  if(path===BASE+'/'+ID(4)+'/delivery-records?limit=20')return {schema:'graph-event-delivery-page-v1',producer_id:ID(4),items:[{...copy(state.record),result:null}],next_after:null};
  if(path===BASE+'/'+ID(40)+'/delivery-records?limit=20')return {schema:'graph-event-delivery-page-v1',producer_id:ID(40),items:[],next_after:ID(41)};
  if(path===BASE+'/'+ID(40)+'/delivery-records?limit=20&after='+ID(41))return {schema:'graph-event-delivery-page-v1',producer_id:ID(40),items:[],next_after:null};
  if(path===BASE+'/intents/'+ID(3)+'/delivery-record'){if(state.detailFailure)throw Object.assign(Error('private SQL table detail'),{status:409});return copy(state.record);}
  if(path===BASE+'/intents/'+ID(33)+'/delivery-record'&&state.otherRecord)return copy(state.otherRecord);
  if(path.startsWith(BASE+'/intents/'+ID(3)+'/retry-requests/')){const nonce=path.split('/').at(-1);return {found:state.receipts.has(nonce)||state.legacy.has(nonce),receipt:copy(state.receipts.get(nonce)||null),legacy:copy(state.legacy.get(nonce)||null)};}
  if(path.startsWith(BASE+'/intents/'+ID(3)+'/retry-command-requests/')){const nonce=path.split('/').at(-1),saved=state.receipts.get(nonce)||null,legacy=state.legacy.get(nonce)||null,retirement=state.retired.get(nonce)||null;return {schema:'graph-event-intent-retry-request-state-v1',intent_id:ID(3),request_id:nonce,outcome:saved||legacy?'saved':retirement?'retired':'not_found',command_hash:saved?.command_hash||legacy?.retry.command_hash||retirement?.original_command_hash||null,saved:copy(saved),legacy:copy(legacy),retirement:copy(retirement)};}
  if(path.startsWith(BASE+'/intents/'+ID(3)+'/retry-retirements/requests/')){const value=state.retirements.get(path.split('/').at(-1))||null;return {found:value!==null,retirement:copy(value)};}
  if(path===BASE+'/intents/'+ID(3)+'/retry-retirements'&&options.method==='POST'){
   const command=JSON.parse(options.body),old=command.original.request_id;if(state.receipts.has(old)||state.legacy.has(old)||state.retired.has(old))throw Object.assign(Error('synthetic original already terminal'),{status:409});
   const unsigned={schema:'graph-event-intent-retry-retirement-receipt-v1',id:ID(30),request_id:command.request_id,actor_id:scope.actor_id,intent_id:ID(3),producer_id:ID(4),job_id:ID(7),intent_hash:H,original_request_id:old,original_command_hash:await deliveryRetryCommandHash(scope,ID(3),command.original,webcrypto),command_hash:await deliveryRetirementCommandHash(scope,ID(3),command,webcrypto),command:copy(command),result:{outcome:'retired',original_request_id:old,original_command_hash:await deliveryRetryCommandHash(scope,ID(3),command.original,webcrypto)}};
   const retirement={...unsigned,receipt_hash:digest(unsigned)};if(!state.retirementNoCommit){state.retirements.set(command.request_id,retirement);state.retired.set(old,retirement);}if(state.retirementLost)throw Error('synthetic lost retirement response');return {retirement,replayed:false};
  }
  if(path===BASE+'/intents/'+ID(3)+'/retry'&&options.method==='POST'){
   const body=JSON.parse(options.body),command_hash=await deliveryRetryCommandHash(scope,ID(3),body,webcrypto);
   if(state.retired.has(body.request_id)||state.enforceVersion&&body.job_version!==state.record.delivery.version)throw Object.assign(Error('synthetic old request cannot be sent'),{status:409});
   assert.ok([...storage.data.values()].some(raw=>{try{const p=JSON.parse(raw);return p.schema==='graph-source-delivery-outbox-v124'&&p.exact_body===options.body&&p.command_hash===command_hash;}catch{return false;}}),'immutable exact body exists before sending');
   const admission={schema:'graph-event-admission-v1',id:ID(10),workspace_id:scope.workspace_id,producer_id:ID(4),intent_id:ID(3),source_key:'synthetic:original',contract_hash:H,source_intent_hash:H,projection:{original_float:1.0},sequence:5,admitted_at:'2026-10-01T00:00:02.125000+00:00',admission_hash:H};
   const receipt={schema:'graph-event-intent-retry-receipt-v1',id:ID(9),request_id:body.request_id,actor_id:scope.actor_id,intent_id:ID(3),producer_id:ID(4),job_id:ID(7),intent_hash:H,command_hash,command:copy(body),result:{outcome:state.outcome,accepted_job_version:state.outcome==='queued'?body.job_version+1:18,reconciled:state.outcome==='already_admitted',admission:state.outcome==='queued'?null:admission},receipt_hash:H};
   if(!state.noCommit)state.receipts.set(body.request_id,receipt);if(state.lost)throw Error('synthetic lost POST response');return {already_admitted:state.outcome==='already_admitted',delivery:{status:'untrusted compatibility projection'}};
  }
  throw Error('Unapproved synthetic API path '+path);
 };
 const make=(currentSession=session(),onChange=()=>{})=>new GraphSourceDeliveryControllerV124({api,scope,storage,session:currentSession,crypto:webcrypto,onChange});
 const c=make();await c.open(ID(4));await c.selectIntent(ID(3));c.editReason('人工核对后恢复送达');return {c,make,state,storage,calls,api};
}
const posts=h=>h.calls.filter(row=>row.method==='POST');
const originalCandidate=c=>c.recovery.items.find(row=>JSON.parse(row.raw).schema==='graph-source-delivery-outbox-v124');
async function lose(h,outcome='queued'){h.state.outcome=outcome;h.state.lost=true;await assert.rejects(h.c.submitRetry());assert.ok(h.c.pending);return copy(h.c.pending);}

test('old retry command hash remains exact for Unicode and whitespace without a new schema',async()=>{
 const body={request_id:ID(100),job_version:3,reason:'  合成恢复😀  '};const canonical='{"actor_id":"'+scope.actor_id+'","command":{"job_version":3,"reason":"  合成恢复😀  ","request_id":"'+ID(100)+'"},"intent_id":"'+ID(3)+'","scope":"graph-event-retry-v1","workspace_id":"'+scope.workspace_id+'"}';
 assert.equal(await deliveryRetryCommandHash(scope,ID(3),body,webcrypto),createHash('sha256').update(canonical).digest('hex'));
 assert.notEqual(await deliveryRetryCommandHash(scope,ID(3),body,webcrypto),await deliveryRetryCommandHash(scope,ID(3),{...body,reason:body.reason.trim()},webcrypto));
 await assert.rejects(deliveryRetryCommandHash(scope,ID(3),{...body,job_version:1.5},webcrypto));await assert.rejects(deliveryRetryCommandHash(scope,ID(3),{...body,reason:'\ud800'},webcrypto));
});
test('actual closed record supports exhausted retry count when only reconciling an already delivered result',()=>{
 const r=record({intent_status:'admitted',admission:{id:ID(10),sequence:5,admitted_at:'2026-10-01T00:00:02Z'},action:{allowed:true,kind:'reconcile_receipt',reason_code:'receipt_available',label:'核对已送达结果',remaining_retries:0}});
 assert.equal(inspectDeliveryRecord(r,scope,{detail:true}),r);assert.throws(()=>inspectDeliveryRecord({...r,physical_state_verified:true},scope,{detail:true}));assert.throws(()=>inspectDeliveryRecord(r,scope));
});
for(const outcome of ['queued','already_admitted'])test(outcome+' POST committed with a lost response is recovered from a fresh session by original nonce GET only',async()=>{
 const h=await fixture(),p=await lose(h,outcome),cold=h.make();h.c.dispose();assert.equal(cold.pending,null);await cold.open();const row=originalCandidate(cold);assert.ok(row);await cold.restoreRecovery(row);
 assert.equal(posts(h).length,1);assert.equal(posts(h)[0].body,p.exact_body);assert.equal(cold.pending,null);assert.equal(cold.lastReceipt.command_hash,p.command_hash);assert.equal(cold.lastReceipt.result.outcome,outcome);assert.equal(cold.detail.source_label,'公开合成温度结果');assert.equal(cold.lastLegacy,null);
 const saved=JSON.parse(h.storage.getItem(cold.prefix+'completed:'+p.body.request_id));assert.deepEqual(Object.keys(saved).sort(),['schema','scope','window_id','intent_id','producer_id','body','exact_body','command_hash'].sort());assert.ok(!Object.hasOwn(saved,'receipt'));
});
test('already delivered current state does not prove the original retry nonce was saved; explicit resend keeps the original body',async()=>{
 const h=await fixture();h.state.noCommit=true;const p=await lose(h);h.state.record=record({intent_status:'admitted',admission:{id:ID(10),sequence:5,admitted_at:'2026-10-01T00:00:02Z'},delivery:{job_id:ID(7),status:'failed',version:17,attempts:5},action:{allowed:true,kind:'reconcile_receipt',reason_code:'receipt_available',label:'核对已送达结果',remaining_retries:0}});
 await h.c.recover();assert.equal(h.c.pendingNotFound,true);assert.equal(h.c.lastReceipt,null);assert.ok(h.c.notice.includes('不能根据当前状态'));assert.equal(posts(h).length,1);
 h.state.lost=false;h.state.noCommit=false;h.state.outcome='already_admitted';await h.c.resend();assert.equal(posts(h).length,2);assert.equal(posts(h)[1].body,p.exact_body);assert.equal(h.c.pending,null);assert.equal(h.c.lastReceipt.result.accepted_job_version,18);
});
test('legacy Job history proves its original retry but does not invent a new receipt or timestamp',async()=>{
 const h=await fixture();h.state.noCommit=true;const p=await lose(h);h.state.legacy.set(p.body.request_id,{origin:'job_history_v1',job_id:ID(7),intent_id:ID(3),producer_id:ID(4),retry:{request_id:p.body.request_id,actor_id:scope.actor_id,command_hash:p.command_hash,command:copy(p.body),accepted_job_version:p.body.job_version+1}});
 await h.c.recover();assert.equal(h.c.pending,null);assert.equal(h.c.lastReceipt,null);assert.equal(h.c.lastOutcome,'legacy_saved');assert.ok(!Object.hasOwn(h.c.lastLegacy,'saved_at'));assert.equal(posts(h).length,1);
});
test('receipt scope, nonce, original command and linked identity corruption cannot settle pending',async t=>{
 for(const mutate of [r=>{r.actor_id=ID(101);},r=>{r.intent_id=ID(101);},r=>{r.producer_id=ID(101);},r=>{r.request_id=ID(101);},r=>{r.command.reason='替换说明';},r=>{r.command_hash='b'.repeat(64);},r=>{r.result.accepted_job_version++;},r=>{r.unexpected=true;}])await t.test(String(mutate),async()=>{const h=await fixture(),p=await lose(h);mutate(h.state.receipts.get(p.body.request_id));await assert.rejects(h.c.recover());assert.equal(h.c.pending.exact_body,p.exact_body);assert.equal(h.c.lastReceipt,null);assert.equal(h.storage.getItem(h.c.prefix+'completed:'+p.body.request_id),null);});
});
test('historical receipt settlement survives broken current detail and never substitutes the current selected producer',async()=>{
 const h=await fixture(),p=await lose(h);await h.c.selectProducer(ID(40));assert.equal(h.c.detail.producer_id,ID(4));assert.equal(h.c.list.next_after,ID(41));await h.c.loadMore();assert.equal(h.c.list.next_after,null);
 h.state.detailFailure=true;await h.c.recover();assert.equal(h.c.pending,null);assert.equal(h.c.lastReceipt.command_hash,p.command_hash);assert.equal(h.c.detail.producer_id,ID(4));assert.ok(h.c.notice.includes('详情暂未更新'));assert.ok(!h.c.notice.includes('SQL'));assert.equal(posts(h).length,1);
});
test('a wrong-producer detail response is rejected before changing the already opened original detail',async()=>{
 const h=await fixture();await lose(h);h.state.record=record({producer_id:ID(40),source_label:'错误来源'});await h.c.recover();assert.equal(h.c.pending,null);assert.equal(h.c.detail.producer_id,ID(4));assert.equal(h.c.detail.source_label,'公开合成温度结果');
});
test('a changed original Job version before a fresh command causes a readable refusal and no new outbox or POST',async()=>{
 const h=await fixture();h.state.record.delivery.version++;await assert.rejects(h.c.submitRetry());assert.equal(posts(h).length,0);assert.equal(h.c.pending,null);assert.equal([...h.storage.data.keys()].some(k=>k.includes(':outbox:')),false);assert.equal(h.c.reason,'人工核对后恢复送达');
});
test('failed immutable outbox storage prevents POST and retains the durable reason and in-memory draft',async()=>{
 const h=await fixture();h.storage.fail=true;await assert.rejects(h.c.submitRetry());assert.equal(posts(h).length,0);assert.equal(h.c.pending,null);assert.equal(h.c.localSaved,false);assert.equal(h.c.reason,'人工核对后恢复送达');h.storage.fail=false;assert.equal(h.c.saveLocal(),true);await h.c.submitRetry();assert.equal(posts(h).length,1);
});
test('one thousand actual reason edits use bounded owner slots; quota repair preserves the last text',async()=>{
 const h=await fixture();for(let i=1;i<=1000;i++)h.c.editReason('恢'.repeat(i));const keys=[...h.storage.data.keys()].filter(k=>k.startsWith(h.c.draftPrefix));assert.equal(keys.length,3);assert.ok([...h.storage.data.values()].reduce((n,s)=>n+s.length,0)<20000);
 h.storage.fail=true;h.c.editReason('配额失败后的最后说明');assert.equal(h.c.localSaved,false);assert.equal(h.c.reason,'配额失败后的最后说明');assert.equal(posts(h).length,0);h.storage.fail=false;assert.equal(h.c.saveLocal(),true);assert.equal(h.c.localSaved,true);
});
test('a cloned session preserves its own full reason snapshot after the original tab cycles both mutable slots',async()=>{
 const h=await fixture();h.c.editReason('复制时保留的说明');const clonedSession=new Memory([...h.c.session.data]);for(let i=0;i<8;i++)h.c.editReason('原窗口后续说明'+i);
 const b=h.make(clonedSession);await b.open(ID(4));await b.selectIntent(ID(3));assert.equal(b.reason,'复制时保留的说明');b.editReason('复制窗口独立说明');assert.equal(h.c.reason,'原窗口后续说明7');assert.notEqual(b.draftOwner,h.c.draftOwner);
});
test('cold unsent reason recovery explicitly preserves the currently typed reason before restoring an old snapshot',async()=>{
 const h=await fixture();h.c.editReason('早先保留的说明');h.c.dispose();const cold=h.make();await cold.open(ID(4));await cold.selectIntent(ID(3));cold.editReason('新会话当前说明');await cold.loadReasonDrafts();const row=cold.reasonRecoveries.items.find(r=>r.reason==='早先保留的说明');assert.ok(row);await cold.restoreReason(row);assert.equal(cold.reason,'早先保留的说明');assert.ok([...h.storage.data.values()].some(raw=>raw.includes('新会话当前说明')));assert.equal(posts(h).length,0);
});
test('changed local recovery bytes and wrong original command hash refuse recovery without POST or overwrite',async()=>{
 const h=await fixture(),p=await lose(h),cold=h.make();await cold.open();const row=originalCandidate(cold);h.storage.setItem(row.key,row.raw+' ');await assert.rejects(cold.restoreRecovery(row));assert.equal(cold.pending,null);assert.equal(posts(h).length,1);
 h.storage.setItem(row.key,JSON.stringify({...p,command_hash:'b'.repeat(64)}));await cold.discoverRecovery();await assert.rejects(cold.restoreRecovery(originalCandidate(cold)));assert.equal(cold.pending,null);assert.equal(posts(h).length,1);
});
test('busy focus immediately conceals and a late old identity cannot verify before a fresh queued check',async()=>{
 const h=await fixture();h.state.holdMe=true;const work=h.c.selectProducer(ID(40));await Promise.resolve();assert.equal(h.state.meWaiters.length,1);const old=h.state.meWaiters.splice(0);h.state.actor=ID(101);await h.c.recheckIdentity();assert.equal(h.c.identityStatus,'checking');old.forEach(fn=>fn());await assert.rejects(work);for(let i=0;i<4;i++)await Promise.resolve();assert.notEqual(h.c.identityStatus,'verified');assert.equal(h.state.meWaiters.length,1);h.state.holdMe=false;h.state.meWaiters.splice(0).forEach(fn=>fn());for(let i=0;i<10;i++)await Promise.resolve();assert.equal(h.c.identityStatus,'concealed');assert.equal(h.c.reason,'人工核对后恢复送达');assert.equal(posts(h).length,0);
});
test('writer maximum matches recovery model and rejects overlarge journals without deleting earlier human work',async()=>{
 const h=await fixture(),before=[...h.storage.data];h.c.reasons={};for(let i=100;i<120;i++)h.c.reasons[ID(i)]={producer_id:ID(4),reason:'字'.repeat(20000)};assert.equal(h.c.saveLocal(),false);assert.equal(h.c.localSaved,false);for(const [key,raw] of before)assert.equal(h.storage.getItem(key),raw);assert.equal(posts(h).length,0);assert.ok(MAX===256*1024);
});
test('workspace or account session changes conceal original content and fence all operations without deleting the outbox',async()=>{
 const h=await fixture(),p=await lose(h),raw=h.storage.getItem(h.c.prefix+'outbox:'+p.body.request_id);h.c.session.setItem('ga-setup-csrf-workspace',ID(110));await assert.rejects(h.c.recover());assert.equal(h.c.identityStatus,'concealed');assert.equal(h.storage.getItem(h.c.prefix+'outbox:'+p.body.request_id),raw);assert.equal(posts(h).length,1);
 const wrongSession=session();wrongSession.setItem('ga-setup-csrf-user',ID(111));const other=h.make(wrongSession);assert.equal(other.corrupt,true);assert.notEqual(other.identityStatus,'verified');assert.deepEqual(other.recovery.items,[]);
});
const retirementPosts=h=>h.calls.filter(row=>row.method==='POST'&&row.path.endsWith('/retry-retirements'));
const retirementCandidate=c=>c.retirementRecovery.items.find(row=>JSON.parse(row.raw).schema==='graph-source-delivery-retirement-outbox-v125');
async function missing(h){h.state.noCommit=true;const p=await lose(h);await h.c.recover();assert.equal(h.c.pendingNotFound,true);h.c.setRetirementReason('  原版本已变化，结束这次未完成申请  ');return p;}
test('V125 retirement hash preserves both reasons and matches the unchanged original retry envelope',async()=>{
 const original={request_id:ID(90),job_version:3,reason:' \t '},body={schema:'graph-event-intent-retry-retirement-v1',request_id:ID(91),original,reason:'  结束申请😀  '};
 assert.equal(await deliveryRetirementCommandHash(scope,ID(3),body,webcrypto),digest({schema:'graph-event-intent-retry-retirement-command-hash-v1',workspace_id:scope.workspace_id,actor_id:scope.actor_id,intent_id:ID(3),command:body}));
 assert.equal(body.reason,'  结束申请😀  ');assert.equal(body.original.reason,' \t ');assert.equal(validDeliveryRetirementReason('\u0085\u001c'),false);assert.equal(validDeliveryRetirementReason('\ufeff'),true);assert.equal(validDeliveryRetirementReason('😀'.repeat(1000)),true);assert.equal(validDeliveryRetirementReason('😀'.repeat(1001)),false);await assert.rejects(deliveryRetirementCommandHash(scope,ID(3),{...body,request_id:original.request_id},webcrypto));
});
test('V125 original failed CAS can be ended without changing the Job, message, source or original request bytes',async()=>{
 const h=await fixture(),p=await missing(h),raw=h.storage.getItem(h.c.prefix+'outbox:'+p.body.request_id);h.state.record.delivery.version=12;h.state.enforceVersion=true;h.state.lost=false;await assert.rejects(h.c.resend());assert.equal(h.c.pending.exact_body,p.exact_body);await h.c.recover();await h.c.retireOriginal();
 assert.equal(h.c.pending,null);assert.equal(h.c.retirement,null);assert.equal(h.c.lastOutcome,'retired');assert.equal(h.c.lastReceipt,null);assert.equal(h.c.lastRetirement.original_command_hash,p.command_hash);assert.equal(h.storage.getItem(h.c.prefix+'outbox:'+p.body.request_id),raw);assert.equal(h.state.record.delivery.version,12);assert.equal(h.state.record.delivery.status,'failed');assert.equal(h.state.record.intent_status,'pending');assert.equal(retirementPosts(h).length,1);assert.equal(JSON.parse(retirementPosts(h)[0].body).reason,'  原版本已变化，结束这次未完成申请  ');
 h.state.noCommit=false;await h.c.submitRetry();assert.equal(h.c.lastReceipt.command.job_version,12);assert.notEqual(h.c.lastReceipt.request_id,p.body.request_id);
});
test('V125 lost retirement response is cold-recovered using two original GETs without a new POST or current Job health',async()=>{
 const h=await fixture(),p=await missing(h);h.state.retirementLost=true;await assert.rejects(h.c.retireOriginal());const r=copy(h.c.retirement);assert.ok(r);assert.equal(h.c.pendingNotFound,true);await assert.rejects(h.c.resend());const cold=h.make();h.c.dispose();await cold.open();await cold.discoverRetirements();h.state.detailFailure=true;await cold.restoreRetirement(retirementCandidate(cold));
 assert.equal(cold.pending,null);assert.equal(cold.retirement,null);assert.equal(cold.lastOutcome,'retired');assert.equal(cold.lastReceipt,null);assert.equal(cold.lastRetirement.request_id,r.body.request_id);assert.equal(retirementPosts(h).length,1);assert.equal(h.calls.filter(row=>row.method==='POST'&&row.path.endsWith('/retry')).length,1);
 assert.ok(h.calls.some(row=>row.path.endsWith('/retry-retirements/requests/'+r.body.request_id)));assert.ok(h.calls.some(row=>row.path.endsWith('/retry-command-requests/'+p.body.request_id)));const marker=JSON.parse(h.storage.getItem(cold.retirementPrefix+'completed:'+r.body.request_id));assert.equal(Object.keys(marker).length,9);assert.equal(marker.original_window_id,p.window_id);assert.equal(marker.exact_body,r.exact_body);assert.ok(!Object.hasOwn(marker,'receipt'));
});
test('V125 original retry winning the retirement race only reports the saved retry and never records this retirement as successful',async()=>{
 const h=await fixture(),p=await missing(h);h.state.noCommit=false;h.state.lost=false;await h.api(BASE+'/intents/'+ID(3)+'/retry',{method:'POST',body:p.exact_body,headers:{'X-Workspace-ID':scope.workspace_id,'X-CSRF-Token':'synthetic'}});await h.c.retireOriginal();
 assert.equal(h.c.pending,null);assert.equal(h.c.retirement,null);assert.equal(h.c.lastReceipt.request_id,p.body.request_id);assert.equal(h.c.lastRetirement,null);assert.ok(h.c.notice.includes('结束没有生效'));assert.equal([...h.storage.data.keys()].some(key=>key.startsWith(h.c.retirementPrefix+'completed:')),false);
});
test('V125 negative retirement lookup permits only explicit same-body resend after both commands remain not found',async()=>{
 const h=await fixture();await missing(h);h.state.retirementNoCommit=true;h.state.retirementLost=true;await assert.rejects(h.c.retireOriginal());const r=copy(h.c.retirement);await assert.rejects(h.c.resendRetirement());await h.c.recoverRetirement();assert.equal(h.c.retirementNotFound,true);assert.equal(h.c.lastRetirement,null);assert.equal(retirementPosts(h).length,1);
 h.state.retirementNoCommit=false;h.state.retirementLost=false;await h.c.resendRetirement();assert.equal(retirementPosts(h).length,2);assert.equal(retirementPosts(h)[1].body,r.exact_body);assert.equal(h.c.retirement,null);assert.equal(h.c.lastOutcome,'retired');
});
test('V125 quota failure retains ending reason and original pending; saving the other reason cannot clear its guard',async()=>{
 const h=await fixture(),p=await missing(h);h.storage.fail=true;h.c.setRetirementReason('存储失败仍保留的结束理由');assert.equal(h.c.localSaved,false);assert.equal(h.c.retirementReason,'存储失败仍保留的结束理由');await assert.rejects(h.c.retireOriginal());assert.equal(retirementPosts(h).length,0);h.storage.fail=false;assert.equal(h.c.saveLocal(),true);assert.equal(h.c.localSaved,false);assert.equal(h.c.saveRetirementReason(),true);assert.equal(h.c.localSaved,true);assert.equal(h.c.pending.exact_body,p.exact_body);await h.c.retireOriginal();assert.equal(retirementPosts(h).length,1);
});
test('V125 intent UUID equal to original retry nonce cannot overwrite original restore reason with ending reason',async()=>{
 const h=await fixture(),ids=[ID(3),ID(93)];h.c.crypto={subtle:webcrypto.subtle,randomUUID:()=>ids.shift()};h.state.noCommit=true;await lose(h);assert.equal(h.c.pending.body.request_id,h.c.pending.intent_id);await h.c.recover();const original=h.c.reason;h.c.setRetirementReason('独立结束理由');assert.equal(h.c.reason,original);assert.equal(h.c.pending.body.reason,original);assert.equal(h.c.retirementReason,'独立结束理由');await h.c.retireOriginal();assert.equal(h.c.reasons[ID(3)].reason,original);assert.equal(h.c.retirementReasons[ID(3)][ID(3)],'独立结束理由');
});
test('V125 cold session explicitly restores ending draft by original intent and nonce, preserving current draft and bounded cloned snapshots',async()=>{
 const h=await fixture(),p=await missing(h);h.c.setRetirementReason('早先结束理由');const cloned=new Memory([...h.c.session.data]);for(let i=0;i<1000;i++)h.c.setRetirementReason('原窗口后续理由'+i);assert.equal([...h.storage.data.keys()].filter(k=>k.startsWith(h.c.retirementDraftPrefix)).length,3);
 const b=h.make(cloned);await b.open();await b.recover();assert.equal(b.retirementReason,'早先结束理由');b.setRetirementReason('复制窗口独立理由');assert.equal(h.c.retirementReason,'原窗口后续理由999');const cold=h.make();await cold.open();await cold.restoreRecovery(originalCandidate(cold));cold.setRetirementReason('冷会话当前结束理由');await cold.loadRetirementReasonDrafts();const row=cold.retirementReasonRecoveries.items.find(r=>r.reason==='早先结束理由');assert.ok(row);await cold.restoreRetirementReason(row);assert.equal(cold.retirementReason,'早先结束理由');assert.ok([...h.storage.data.values()].some(raw=>raw.includes('冷会话当前结束理由')));assert.equal(cold.pending.body.request_id,p.body.request_id);assert.equal(retirementPosts(h).length,0);
});
test('V125 retirement receipt tampering cannot clear either pending or write completion hints',async t=>{
 for(const [name,mutate] of [['actor',r=>{r.actor_id=ID(98);}],['producer',r=>{r.producer_id=ID(98);}],['original',r=>{r.original_request_id=ID(98);}],['body',r=>{r.command.reason+='改写';}],['hash',r=>{r.receipt_hash=H;}],['extra',r=>{r.extra=true;}]])await t.test(name,async()=>{const h=await fixture();await missing(h);h.state.retirementLost=true;await assert.rejects(h.c.retireOriginal());const r=copy(h.c.retirement),actual=h.state.retirements.get(r.body.request_id);mutate(actual);await assert.rejects(h.c.recoverRetirement());assert.ok(h.c.pending);assert.equal(h.c.retirement.exact_body,r.exact_body);assert.equal(h.c.lastRetirement,null);assert.equal(h.storage.getItem(h.c.retirementPrefix+'completed:'+r.body.request_id),null);});
});
test('V125 retirement winner in another window clears the stale attempt without inventing its own completion',async()=>{
 const h=await fixture();await missing(h);const b=h.make(new Memory([...h.c.session.data]));await b.open();await b.recover();b.setRetirementReason('另一窗口的结束尝试');await h.c.retireOriginal();const actual=h.c.lastRetirement;await b.retireOriginal();assert.equal(b.lastRetirement.request_id,actual.request_id);assert.equal(b.lastOutcome,'retired');const attempts=retirementPosts(h);assert.equal(attempts.length,2);const second=JSON.parse(attempts[1].body);assert.notEqual(second.request_id,actual.request_id);assert.equal(h.storage.getItem(b.retirementPrefix+'completed:'+second.request_id),null);assert.equal(b.pending,null);assert.equal(b.retirement,null);
});
test('V125 mixed original and ending session links cannot clear either pointer even when the original is saved or retired',async t=>{
 for(const outcome of ['saved','retired'])await t.test(outcome,async()=>{
  const h=await fixture(),p=await missing(h),pendingKey=h.c.prefix+'outbox:'+p.body.request_id;
  if(outcome==='saved'){h.state.noCommit=false;h.state.lost=false;await h.api(BASE+'/intents/'+ID(3)+'/retry',{method:'POST',body:p.exact_body,headers:{'X-Workspace-ID':scope.workspace_id,'X-CSRF-Token':'synthetic'}});}else await h.c.retireOriginal();
  const other={request_id:ID(801),job_version:3,reason:'另一条保留的原恢复说明'},body={schema:'graph-event-intent-retry-retirement-v1',request_id:ID(802),original:other,reason:'另一条独立结束理由'},r={schema:'graph-source-delivery-retirement-outbox-v125',scope:copy(scope),window_id:ID(803),intent_id:ID(3),producer_id:ID(4),original_window_id:ID(804),body,exact_body:JSON.stringify(body),command_hash:await deliveryRetirementCommandHash(scope,ID(3),body,webcrypto)},retireKey=h.c.retirementPrefix+'outbox:'+body.request_id;
  h.storage.setItem(retireKey,JSON.stringify(r));h.c.session.setItem(h.c.prefix+'pending',pendingKey);h.c.session.setItem(h.c.prefix+'origin-window',p.window_id);h.c.session.setItem(h.c.retirementPrefix+'pending',retireKey);h.c.session.setItem(h.c.retirementPrefix+'origin-window',r.window_id);
  const cold=h.make(h.c.session),before=[...h.c.session.data],postCount=posts(h).length;await cold.open();await assert.rejects(cold.recover());assert.equal(cold.pending.exact_body,p.exact_body);assert.equal(cold.retirement.exact_body,r.exact_body);assert.deepEqual([...h.c.session.data],before);assert.equal(cold.lastReceipt,null);assert.equal(cold.lastRetirement,null);assert.equal(posts(h).length,postCount);
 });
});
test('V125 a new unknown original clears old volatile success while preserving immutable history and same-original confirmation',async()=>{
 const h=await fixture(),old=await missing(h);await h.c.retireOriginal();assert.equal(h.c.lastRetirement.original_request_id,old.body.request_id);const preserved=[...h.storage.data].filter(([key])=>key.startsWith(h.c.prefix+'outbox:')||key.startsWith(h.c.prefix+'completed:')||key.startsWith(h.c.retirementPrefix+'outbox:')||key.startsWith(h.c.retirementPrefix+'completed:'));
 await assert.rejects(h.c.submitRetry());const next=copy(h.c.pending);assert.notEqual(next.body.request_id,old.body.request_id);assert.equal(h.c.lastRetirement,null);assert.equal(h.c.lastReceipt,null);assert.equal(h.c.lastLegacy,null);assert.equal(h.c.lastOutcome,null);assert.equal(h.c.lastOriginal,null);assert.equal(h.c.notice,'');for(const [key,raw] of preserved)assert.equal(h.storage.getItem(key),raw);
 await h.c.recover();h.state.noCommit=false;h.state.lost=false;await h.c.resend();assert.equal(h.c.lastReceipt.request_id,next.body.request_id);assert.equal(h.c.lastOriginal.exact_body,next.exact_body);
 await h.c.discoverRecovery();const row=h.c.recovery.items.find(item=>JSON.parse(item.raw).schema==='graph-source-delivery-outbox-v124'&&JSON.parse(item.raw).body.request_id===next.body.request_id),seen=[];h.c.onChange=current=>{if(current.pending?.body.request_id===next.body.request_id)seen.push(current.lastReceipt?.request_id||null);};await h.c.restoreRecovery(row);assert.ok(seen.length);assert.ok(seen.every(nonce=>nonce===next.body.request_id));assert.equal(h.c.lastReceipt.request_id,next.body.request_id);
 h.state.otherRecord=record({intent_id:ID(33),object:{id:ID(35),name:'另一条实际结果'}});await h.c.selectIntent(ID(33));assert.equal(h.c.lastReceipt,null);assert.equal(h.c.lastRetirement,null);assert.equal(h.c.lastOutcome,null);assert.equal(h.c.lastOriginal,null);for(const [key,raw] of preserved)assert.equal(h.storage.getItem(key),raw);
});
