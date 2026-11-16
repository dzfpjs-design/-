// Real controller/local durability/hash/lifecycle decisions with explicit API
// and browser-storage IO fixtures. No server authority, PG, UI, SDK/provider
// send, actual money, permission or restart durability is proved here.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {SameGoalAdviceControllerV310,adviceCanonical,adviceHash,adviceMinorUnits} from '../src/same-goal-advice-v310-controller.mjs';
import {createRetainedControllerLifecycle} from '../src/retained-controller-lifecycle.mjs';

const id=n=>'31000000-0000-4000-8000-'+String(n).padStart(12,'0'),sha=n=>String(n).repeat(64);
const clone=value=>JSON.parse(JSON.stringify(value));
const account={user_id:id(1),workspace_id:id(2),role:'operator'},task={id:id(3),instance_id:id(4),instance_release_id:id(5)};
const base='/workspace/tasks/'+task.id+'/business-advice';
const flags={can_dispatch:false,permissions_verified:false,source_authenticity_verified:false,
 current_outcome_verified:false,goal_completion_verified:false,data_reception_verified:false};
class Memory{
 constructor(){this.data=new Map();this.fail=false;this.badReadback=false;}
 get length(){return this.data.size;}key(n){return [...this.data.keys()][n]??null;}
 getItem(key){if(this.fail)throw Error('合成本机存储暂不可用');return this.badReadback?null:this.data.get(key)??null;}
 setItem(key,value){if(this.fail)throw Error('合成本机存储暂不可用');this.data.set(key,String(value));}
 removeItem(key){if(this.fail)throw Error('合成本机存储暂不可用');this.data.delete(key);}
}
const barrier=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
const readGate=()=>({entered:barrier(),release:barrier()});
async function until(predicate){for(let attempt=0;attempt<200;attempt++){if(predicate())return;await new Promise(resolve=>setImmediate(resolve));}assert.fail('controller did not reach the expected bounded async state');}
const error=(status,message)=>Object.assign(Error(message),{status});
async function fixture({admin=false}={}){
 const storage=new Memory(),session=new Memory(),calls=[],receipts=new Map(),mode={};
 for(const[key,value]of Object.entries({'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id,'ga-setup-csrf':'csrf-fixture'}))session.setItem(key,value);
 const selection={kind:'approved_source_set',source_set_ref:{source_set_id:id(6),source_set_hash:sha(1)}};
 const modelPin={id:id(7),content_hash:sha(2),release_version:'1.0.0'};
 const model={name:'原配置模型',provider:'deepseek',model:'deepseek-v4-pro',release_version:'1.0.0',available:true};
 const context={schema:'same-goal-advice-context-v310',task_id:task.id,task_version:3,title:'合成业务任务',status:'workflow_active',
  goal_hash:sha(3),goal_version:1,goal_purpose:'只提供业务建议',mature_head_hash:sha(4),data_as_of:'2026-11-02T00:00:00+00:00',generation:3,freshness:'current',
  document_sources:[{selection,title:'已批准的原资料摘录',description:'仅本任务固定摘录',input_key:'runtime_document_sources',excerpt_count:1}],
  model_pin:modelPin,model,actor:{role:'operator',is_business_actor:true,is_data_actor:true,is_admin:false},
  quota:{status:'ready',reference:{id:id(8),record_hash:sha(5)},limits:{max_cost_minor_units:100},expires_at:'2026-11-03T00:00:00Z'},
  price:{status:'ready',reference:{id:id(9),record_hash:sha(6)},currency:'CNY',checked_at:'2026-11-02T00:00:00Z',expires_at:'2026-11-03T00:00:00Z'},
  permissions:{can_prepare:true,can_authorize:true,can_preview:true,can_start:true,can_review:true,can_configure_quota:false,can_register_price:false},blocked_reasons:[]};
 const documents=[];
 for(const [index,text]of ['合成数据摘要😀，仅在这次任务中使用。','已批准摘录，不能代表整份资料已核验。'].entries())
  documents.push({role:index===0?'data':'business_document',title:index===0?'数据原文':'业务资料摘录',text,
   evidence_ref:{evidence_id:id(10+index),sha256:await adviceHash(text,webcrypto),size_bytes:new TextEncoder().encode(text).length,media_type:'text/plain'},
   version:1,source_scope:index===0?'evaluation_text':'approved_excerpt',as_of:context.data_as_of,consent:{status:'not_authorized',grants:[]}});
 const actor={...account,role:admin?'admin':'operator'};
 context.actor={...context.actor,role:actor.role,is_admin:admin};
 context.permissions.can_configure_quota=admin;context.permissions.can_register_price=admin;
 const state={me:clone(actor),context,plan:null,preview:null,documents,postCounts:{},sendJobs:0,auths:0,reviewWrites:0,reads:0};
 let tail=Promise.resolve();const locks={request:async(key,options,run)=>{const prior=tail,gate=barrier();tail=gate.promise;await prior;try{return await run();}finally{gate.resolve();}}};
 const plan=()=>clone(state.plan);
 const makePreview=nonce=>({schema:'same-goal-advice-preview-view-v307',plan_id:id(12),request_id:nonce,preview_generation:1,
  latest_for_plan:true,plan_version:state.plan.version,plan_status:state.plan.status,phase:'preview_saved',preview_hash:sha(7),
  messages:[{role:'system',content:'合成固定分析要求'},{role:'user',content:[{type:'text',text:'合成匿名全文，请核对费用后再发'}]}],
  options:{max_tokens:1024,model:model.model},model:{...model,id:modelPin.id},currency:'CNY',maximum_cost_minor_units:3,
  configured_ceiling_minor_units:100,input_token_bound:256,output_token_bound:1024,
  price_checked_at:context.price.checked_at,price_expires_at:context.price.expires_at,evidence_ids:documents.map(d=>d.evidence_ref.evidence_id),
  can_start:false,cloud_send_authorized:false,preview_generated_cloud_calls:0,start_endpoint_available:true,requires_current_checks:true});
 function updateReceipt(row){
  const value={schema:'same-goal-advice-command-receipt-v310',found:true,operation:row.operation,task_id:task.id,plan_id:['quota','price'].includes(row.operation)?null:id(12),
   request_id:row.request_id,actor_id:account.user_id,body_hash:row.body_hash,result_status:mode.needsCheck?'needs_check':'available',result:null};
  if(!mode.needsCheck)value.result=['prepare','cancel'].includes(row.operation)?plan():row.operation==='preview'?clone(state.preview):clone(row.result);
  return {...value,...(mode.badReceipt||{})};
 }
 let controller;
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});
  assert.equal(options.headers?.['X-Workspace-ID'],account.workspace_id);
  if(path==='/me'){if(mode.meForbidden)throw error(403,'synthetic membership revoked');return clone(state.me);}
  if(mode.readBarrier){const gate=mode.readBarrier;mode.readBarrier=null;await gate.promise;}
  if(mode.unauthorized)throw error(401,'synthetic expired login');
  if(!options.method||options.method==='GET'){
   state.reads++;
   if(mode.getErrors?.has(path))throw mode.getErrors.get(path);
   if(path===base+'/context'){if(mode.contextFailure)throw error(409,'当前配置暂不可核对');return clone(state.context);}
   if(path===base+'/preparations'){if(mode.listForbidden)throw error(403,'synthetic source read revoked');
    return {schema:'same-goal-advice-operation-v302/list',task_id:task.id,records:mode.listRecords?clone(mode.listRecords):state.plan?[plan()]:[]};}
   if(path.startsWith(base+'/operations/')){
    const found=receipts.get(path.split('/').at(-1));if(!found)throw error(404,'原申请不存在');return updateReceipt(found);
   }
   if(path===base+'/preparations/'+id(12))return plan();
   if(path===base+'/preparations/'+id(12)+'/documents')return {schema:'same-goal-advice-documents-v310',task_id:task.id,
    plan_id:id(12),plan_version:state.plan.version,data_as_of:context.data_as_of,model_pin:modelPin,model,
    documents:clone(state.documents),restrictions:['只提供已保存文字；不核验整份文档或现场真实性。']};
   if(path===base+'/preparations/'+id(12)+'/preview'){if(!state.preview)throw error(404,'尚未生成预览');return clone(state.preview);}
   throw Error('unsupported actual fixture GET '+path);
  }
  assert.equal(options.method,'POST');const pending=JSON.parse(storage.getItem(controller.key+':pending'));
  assert.equal(storage.getItem(controller.key+':original:'+pending.request_id),JSON.stringify(pending));
  assert.equal(options.body,pending.raw,'the exact durable body precedes every network mutation');
  assert.equal(await adviceHash(options.body,webcrypto),pending.body_hash);
  const body=JSON.parse(options.body),op=pending.operation;state.postCounts[op]=(state.postCounts[op]||0)+1;
  if(mode.rejection)throw error(409,'合成当前版本已变化，未应用本请求');
  if(mode.unknownWithoutCommit)throw Error('合成连接中断，服务器结果未知');
  if(op==='authorize'){
   const doc=state.documents.find(row=>row.evidence_ref.evidence_id===pending.evidence_id);
   if(doc.version!==body.expected_version)throw error(409,'原授权版本已变化');
   doc.version++;doc.consent.status='authorized';state.auths++;
   doc.consent.grants.push({audit_id:id(100+state.auths),audit_hash:sha(8),actor_id:account.user_id,action:'evidence.cloud_authorize',
    model_profile_id:body.model_profile_id,evidence_sha256:doc.evidence_ref.sha256,version_after:doc.version,reason:body.reason});
  }else{
   let original=receipts.get(body.request_id);
   if(original)assert.equal(original.body_hash,pending.body_hash);
   else{
    if(op==='prepare')state.plan={schema:'same-goal-advice-operation-v302',task_id:task.id,plan_id:id(12),version:1,status:'queued',phase:'queued',
     request_id:body.request_id,preparation_snapshot_hash:sha(9),prepare_job_id:id(13),document_preparation:null,reviews:[],result:null,
     send_job:null,human_review_available:false,cancel_available:true,data_as_of:context.data_as_of,source_review_scope:null};
    if(op==='preview'){state.plan.version++;state.preview=makePreview(body.request_id);}
    if(op==='start'){state.sendJobs++;state.plan.version++;state.plan.status='generating';state.plan.phase='send_queued';state.plan.send_job={id:id(14),status:'queued'};state.plan.cancel_available=false;}
    if(op==='cancel'){state.plan.version++;state.plan.status='cancelled';state.plan.phase='cancelled';state.plan.cancel_available=false;}
    if(op==='review'){state.reviewWrites++;state.plan.version++;state.plan.status='reviewed';state.plan.phase='reviewed';state.plan.human_review_available=false;}
    const result=op==='quota'?{schema:'same-goal-advice-quota-authorization-v304',task_id:task.id,request_id:body.request_id,
     quota_policy_ref:{id:id(17),record_hash:sha(2)},limits:{max_cost_minor_units:body.max_cost_minor_units,currency:body.currency},
     configuration_only:true,pricing_verified:false,cloud_send_authorized:false}:
     op==='price'?{schema:'same-goal-advice-official-price-registration-v307/read',basis_ref:{id:id(18),record_hash:sha(1)},
      price:{currency:'CNY'},money_reserved:false,cloud_send_authorized:false}:
     op==='start'?{schema:'same-goal-advice-start-view-v308',plan_id:id(12),request_id:body.request_id,
     plan_version:state.plan.version,send_job_id:id(14),send_job_status:'queued',execution_snapshot_hash:sha(4)}:
     op==='review'?{request_id:body.request_id,decision:body.decision,reason:body.reason,actor_id:account.user_id,result_hash:body.result_hash,
      reviewed_at:'2026-11-02T00:01:00Z',review_ref:{id:id(15),record_hash:sha(5)},can_dispatch:false,goal_completion_verified:false}:null;
    if(op==='review')state.plan.reviews=[result];
    original={operation:op,request_id:body.request_id,body_hash:pending.body_hash,result};receipts.set(body.request_id,original);
   }
  }
  if(mode.loseResponse===op){mode.loseResponse=null;throw Error('合成服务器已保存但响应丢失');}
  return op==='authorize'?{saved:true}:updateReceipt(receipts.get(body.request_id)).result;
 };
 const readActivity={active:0,maximum:0};
 // Synthetic API latency/concurrency only. This is not a database connection,
 // real IAM, paid provider or target-browser concurrency proof.
 const request=async(path,options={})=>{
  if(path==='/me'||options.method&&options.method!=='GET')return api(path,options);
  readActivity.active++;readActivity.maximum=Math.max(readActivity.maximum,readActivity.active);
  try{const gate=mode.getGates?.get(path);if(gate){gate.entered.resolve();await gate.release.promise;}return await api(path,options);}
  finally{readActivity.active--;}
 };
 const create=()=>{controller=new SameGoalAdviceControllerV310({account:actor,task,request,storage,session,locks,crypto:webcrypto});return controller;};
 const c=create();await c.open();
 const prepared=async()=>{c.updateDraft({selected_document_source:selection,prepare_reason:'保留中文理由😀，确认本任务已批准范围'});await c.prepare();
  state.plan.version++;state.plan.status='awaiting_consent';state.plan.phase='awaiting_consent';state.plan.document_preparation={actual_receipt_IO:true};await c.refresh();};
 const consented=async()=>{await prepared();for(const doc of state.documents){const id=doc.evidence_ref.evidence_id;
  c.updateDraft({consent_reasons:{...c.draft.consent_reasons,[id]:'明确核对这份原文后允许发送'}});
  c.updateDraft({consent_checked:{...c.draft.consent_checked,[id]:true}});await c.authorizeEvidence(id);}};
 const previewed=async()=>{await consented();c.updateDraft({preview_reason:'核对完整匿名正文和本次上界'});await c.preparePreview();
  c.updateDraft({preview_checked:true,start_reason:'明确同意按此原文和金额上限开始一次'});};
 const ready=async()=>{await previewed();await c.startAnalysis();state.plan.phase='ready_for_review';state.plan.status='ready_for_review';
  state.plan.send_job.status='succeeded';state.plan.human_review_available=true;state.plan.version++;
  state.plan.result={schema:'same-goal-advice-result-v302',plan_ref:{id:id(12),record_hash:sha(4)},preview_hash:sha(7),
   evaluation_ref:{id:id(16),record_hash:sha(3)},input_hash:sha(2),model_value:{candidate_reviews:[],source_reviews:[],recommendation:{reason:'仅对已提供文字的合成意见'}},
   review_state:'ready_for_review',...flags};await c.refresh();};
 return {c,create,storage,session,state,mode,calls,receipts,selection,prepared,consented,previewed,ready,readActivity};
}

test('uses the established typed canonical spelling and durable original before one formal command',async()=>{
 const t=await fixture();assert.equal(adviceCanonical({'😀':1,'\uffff':2}),'{"\uffff":2,"😀":1}');await t.prepared();
 assert.equal(t.state.postCounts.prepare,1);assert.equal(t.c.pending,null);assert.equal(t.c.plan.phase,'awaiting_consent');
 assert.ok(t.calls.some(row=>row.path.startsWith(base+'/operations/')));assert.equal(t.c.guard(),true);
 const cached=[...t.storage.data.values()].join('\n');for(const doc of t.state.documents)assert.equal(cached.includes(doc.text),false,'source bytes are not draft cache');
});

test('lost prepare response recovers original nonce after disposal/remount using GET only',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'原人稿跨刷新保留'});t.mode.loseResponse='prepare';
 await assert.rejects(t.c.prepare());const nonce=t.c.pending.request_id;t.c.dispose();const replacement=t.create();await replacement.open();
 assert.equal(replacement.pending,null);assert.equal(replacement.draft.prepare_reason,'原人稿跨刷新保留');assert.equal(t.state.postCounts.prepare,1);
 assert.ok(t.calls.some(row=>row.path===base+'/operations/'+nonce));
});

test('two real Evidence audit matches preserve human reason and never invent a nonce receipt',async()=>{
 const t=await fixture();await t.consented();assert.equal(t.state.auths,2);assert.equal(t.c.pending,null);
 const auth=t.c.history.filter(row=>row.operation==='authorize');assert.equal(auth.length,2);
 assert.ok(auth.every(row=>row.proof.kind==='evidence_authorize_audit'&&row.proof.audit_id));
});

for(const fault of ['actor','model','sha','reason','version'])test('authorized label without exact original '+fault+' audit cannot release pending',async()=>{
 const t=await fixture();await t.prepared();const evidenceId=t.state.documents[0].evidence_ref.evidence_id;
 t.c.updateDraft({consent_reasons:{[evidenceId]:'原授权理由'}});t.c.updateDraft({consent_checked:{[evidenceId]:true}});t.mode.loseResponse='authorize';
 await assert.rejects(t.c.authorizeEvidence(evidenceId));const grant=t.state.documents[0].consent.grants[0];
 const keys={actor:'actor_id',model:'model_profile_id',sha:'evidence_sha256',reason:'reason',version:'version_after'};
 grant[keys[fault]]=fault==='version'?99:fault==='reason'?'另一理由':fault==='sha'?sha(0):id(98);
 await t.c.recover();assert.ok(t.c.pending);assert.equal(t.state.postCounts.authorize,1);assert.equal(t.c.documents.documents[0].consent.status,'authorized');
});

test('lost Start response is resolved through actual command receipt without creating a second send Job',async()=>{
 const t=await fixture();await t.previewed();t.mode.loseResponse='start';await assert.rejects(t.c.startAnalysis());const nonce=t.c.pending.request_id;
 t.c.dispose();const replacement=t.create();await replacement.open();assert.equal(replacement.pending,null);assert.equal(t.state.sendJobs,1);
 assert.equal(t.state.postCounts.start,1);assert.ok(t.calls.some(row=>row.path===base+'/operations/'+nonce));
});

test('explicit original resend keeps exact nonce/body after a confirmed missing command',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'只续原申请'});t.mode.unknownWithoutCommit=true;
 await assert.rejects(t.c.prepare());const raw=t.c.pending.raw;await t.c.recover();assert.equal(t.c.notFound,true);
 t.mode.unknownWithoutCommit=false;await t.c.resendOriginal();const posts=t.calls.filter(row=>row.method==='POST');
 assert.equal(posts.length,2);assert.ok(posts.every(row=>row.body===raw));assert.equal(t.receipts.size,1);
});

test('human review uses real ready result hash and persists original decision/reason before request',async()=>{
 const t=await fixture();await t.ready();t.c.updateDraft({review_decision:'needs_revision',review_reason:'这份原人稿不能丢失，需补充特定摘录'});
 t.mode.loseResponse='review';await assert.rejects(t.c.review());const row=t.c.pending;
 assert.equal(row.body.result_hash,await adviceHash(adviceCanonical(t.state.plan.result),webcrypto));
 t.c.dispose();const replacement=t.create();await replacement.open();assert.equal(replacement.pending,null);assert.equal(t.state.reviewWrites,1);
 assert.equal(replacement.draft.review_reason,'这份原人稿不能丢失，需补充特定摘录');assert.equal(replacement.plan.reviews[0].decision,'needs_revision');
});

test('saved command with damaged result stays durably needs_check and cannot advance',async()=>{
 const t=await fixture();t.mode.needsCheck=true;await t.prepared();assert.equal(t.c.pending,null);assert.equal(t.c.needsCheck,true);
 t.c.dispose();const replacement=t.create();await replacement.open();assert.equal(replacement.needsCheck,true);
 await assert.rejects(replacement.preparePreview());assert.equal(t.state.postCounts.preview,undefined);
 t.mode.needsCheck=false;await replacement.recover();assert.equal(replacement.needsCheck,false);
});

for(const key of ['actor_id','task_id','body_hash','operation'])test('wrong receipt '+key+' retains original pending and never accepts a replacement',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'核对原回执'});
 t.mode.badReceipt={[key]:key==='operation'?'start':key==='body_hash'?sha(0):id(99)};
 await assert.rejects(t.c.prepare());assert.ok(t.c.pending);assert.equal(t.state.postCounts.prepare,1);
});

test('storage failure protects human labor and prevents any mutation until reliable retry',async()=>{
 const t=await fixture();t.storage.fail=true;assert.throws(()=>t.c.updateDraft({prepare_reason:'尚在当前页面的人稿'}));
 assert.equal(t.c.localSaved,false);assert.equal(t.c.guard(),false);await assert.rejects(t.c.prepare());assert.equal(t.state.postCounts.prepare,undefined);
 t.storage.fail=false;assert.equal(t.c.retryLocalSave(),true);assert.equal(t.c.draft.prepare_reason,'尚在当前页面的人稿');
});

test('identity switch during an awaited read hides source/answer and fences late response without POST',async()=>{
 const t=await fixture();await t.prepared();const gate=barrier();t.mode.readBarrier=gate;
 const request=t.c.refresh();t.session.setItem('ga-setup-csrf-user',id(90));gate.resolve();await assert.rejects(request);
 const view=t.c.view();assert.equal(view.denied,true);assert.equal(view.documents,null);assert.equal(view.plan,null);assert.equal(view.draft,null);
 assert.equal(t.state.postCounts.prepare,1);
});

test('401 preserves exact original request across a new authenticated controller without second POST',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'失效会话仍保留原人稿'});
 t.mode.loseResponse='prepare';await assert.rejects(t.c.prepare());const raw=t.c.pending.raw;t.mode.unauthorized=true;
 await assert.rejects(t.c.recover());assert.equal(t.c.view().denied,true);assert.equal(JSON.parse(t.storage.getItem(t.c.key+':pending')).raw,raw);
 t.c.dispose();t.mode.unauthorized=false;t.session.setItem('ga-setup-csrf','new-session-csrf');const replacement=t.create();await replacement.open();
 assert.equal(replacement.pending,null);assert.equal(t.state.postCounts.prepare,1);
});

test('blocked context does not hide independently readable saved history',async()=>{
 const t=await fixture();await t.prepared();t.mode.contextFailure=true;await t.c.refresh();
 assert.equal(t.c.context,null);assert.equal(t.c.plan.plan_id,id(12));assert.equal(t.c.documents.documents.length,2);
 await assert.rejects(t.c.preparePreview());assert.equal(t.state.postCounts.preview,undefined);
});

test('known rejected original is retained before explicit editing while unknown response cannot be discarded',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'保持被拒原文'});t.mode.rejection=true;
 await assert.rejects(t.c.prepare());await t.c.releaseRejected();assert.equal(t.c.pending,null);assert.equal(t.state.postCounts.prepare,1);
 assert.ok([...t.storage.data.keys()].some(key=>key.includes(':not-applied:')));
 t.mode.rejection=false;t.mode.unknownWithoutCommit=true;await assert.rejects(t.c.prepare());await assert.rejects(t.c.releaseRejected());assert.ok(t.c.pending);
});

test('retained lifecycle supports StrictMode replay but never a disposed old scope',async()=>{
 const t=await fixture(),queued=[],lifecycle=createRetainedControllerLifecycle({defer:fn=>queued.push(fn)});
 const first=lifecycle.setup(t.c,t.c.key);lifecycle.cleanup(first);const second=lifecycle.setup(t.c,t.c.key);
 while(queued.length)queued.shift()();assert.ok(second);assert.equal(t.c.active,true);
 lifecycle.cleanup(second);while(queued.length)queued.shift()();assert.equal(t.c.active,false);assert.equal(lifecycle.setup(t.c,t.c.key),null);
 await assert.rejects(t.c.refresh());
});

for(const endpoint of ['me','list'])test('403 from protected '+endpoint+' hides previously loaded text and answer immediately',async()=>{
 const t=await fixture();await t.ready();assert.equal(t.c.documents.documents.length,2);assert.ok(t.c.plan.result);
 if(endpoint==='me')t.mode.meForbidden=true;else t.mode.listForbidden=true;
 await assert.rejects(t.c.refresh());const value=t.c.view();
 assert.equal(value.denied,true);assert.equal(value.documents,null);assert.equal(value.plan,null);assert.equal(value.preview,null);assert.equal(value.draft,null);
 assert.ok([...t.storage.data.values()].some(raw=>raw.includes('明确同意按此原文和金额上限开始一次')));
});

test('lost shared pointer retains an independently indexed unknown original across cold remount without another POST',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'即使共享指针丢失也保留这份原申请'});
 t.mode.loseResponse='prepare';await assert.rejects(t.c.prepare());const nonce=t.c.pending.request_id;
 t.storage.removeItem(t.c.key+':pending');t.c.storageChanged();assert.equal(t.c.guard(),true,'independent original is a durable cold index');
 t.c.dispose();const replacement=t.create();await replacement.open();
 assert.equal(replacement.pending,null);assert.equal(t.state.postCounts.prepare,1);assert.ok(t.calls.some(row=>row.path===base+'/operations/'+nonce));
});

test('missing shared pointer with no server receipt restores the SAME original and blocks a new nonce',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'原申请尚未明确完成'});
 t.mode.unknownWithoutCommit=true;await assert.rejects(t.c.prepare());const original=clone(t.c.pending);
 t.storage.removeItem(t.c.key+':pending');t.c.dispose();const replacement=t.create();await replacement.open();
 assert.equal(replacement.pending.request_id,original.request_id);assert.equal(replacement.pending.raw,original.raw);
 await assert.rejects(replacement.prepare());assert.equal(t.state.postCounts.prepare,1);
 t.mode.unknownWithoutCommit=false;await replacement.resendOriginal();
 assert.equal(t.state.postCounts.prepare,2);assert.equal(t.receipts.size,1);assert.equal(replacement.pending,null);
 assert.ok(t.calls.filter(row=>row.method==='POST').every(row=>row.body===original.raw));
});

test('guard blocks navigation when the independent unknown original itself is missing',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'不能丢掉唯一未核正文'});
 t.mode.unknownWithoutCommit=true;await assert.rejects(t.c.prepare());t.storage.removeItem(t.c.key+':original:'+t.c.pending.request_id);
 assert.equal(t.c.guard(),false);assert.equal(t.c.localSaved,false);
});

test('amount parsing uses exact integer cents and never rounds an extra fractional digit',()=>{
 for(const [raw,expected]of [['0.01',1],['1',100],['1.2',120],['1.23',123],[' 10.05 ',1005],['10000000.00',1000000000]])
  assert.equal(adviceMinorUnits(raw),expected);
 for(const raw of ['0','0.00','0.001','1.005','1.234','1e2','1,000.00','-1.00','+1.00','01.00','¥1.00','10000000.01','Infinity','NaN'])
  assert.throws(()=>adviceMinorUnits(raw),raw+' cannot be rounded or coerced');
});

test('admin quota command freezes only actual inherited currency and exact amount, not model tokens or grants',async()=>{
 const t=await fixture({admin:true});t.state.context.quota={status:'unavailable',reference:null,limits:null,expires_at:null};
 t.state.context.price.currency='USD';t.c.updateDraft({quota_cost:'1.23',quota_reason:'明确一次费用上限，不授权资料外发'});
 await t.c.configureQuota();assert.equal(t.state.postCounts.quota,1);assert.equal(t.c.pending,null);assert.equal(t.c.planId,'');
 const post=t.calls.find(row=>row.method==='POST'&&row.path===base+'/quota-options'),body=JSON.parse(post.body);
 assert.equal(body.max_cost_minor_units,123);assert.equal(body.currency,'USD');assert.equal(body.validity_seconds,7200);
 assert.deepEqual(Object.keys(body).sort(),['currency','expected_goal_hash','expected_mature_head_hash','max_cost_minor_units','reason','request_id','validity_seconds']);
 assert.equal(t.state.sendJobs,0);assert.equal(t.state.auths,0);assert.equal(t.c.history.at(-1).plan_id,null);
});

test('admin can register real selected model price before quota, with no model send or Evidence authorization',async()=>{
 const t=await fixture({admin:true});t.state.context.quota.status='unavailable';t.state.context.price.status='unavailable';
 t.c.updateDraft({price_reason:'先核对指定模型的官网价格原件'});await t.c.registerPrice();
 const body=JSON.parse(t.calls.find(row=>row.path===base+'/price-options'&&row.method==='POST').body);
 assert.deepEqual(body.model_pin,t.state.context.model_pin);assert.equal(body.validity_seconds,3600);
 assert.equal(t.c.pending,null);assert.equal(t.c.history.at(-1).plan_id,null);assert.equal(t.state.sendJobs,0);assert.equal(t.state.auths,0);
});

for(const operation of ['quota','price'])test('lost admin '+operation+' response resumes through exact null-Plan receipt without registering twice',async()=>{
 const t=await fixture({admin:true});t.c.updateDraft({quota_cost:'0.01',quota_reason:'原额度理由',price_reason:'原官网价格理由'});t.mode.loseResponse=operation;
 await assert.rejects(operation==='quota'?t.c.configureQuota():t.c.registerPrice());const nonce=t.c.pending.request_id;
 t.c.dispose();const replacement=t.create();await replacement.open();
 assert.equal(replacement.pending,null);assert.equal(t.state.postCounts[operation],1);assert.ok(t.calls.some(row=>row.path===base+'/operations/'+nonce));
 assert.equal(replacement.draft.quota_reason,'原额度理由');assert.equal(replacement.draft.price_reason,'原官网价格理由');
});

test('ordinary business account cannot configure budget or price even if a UI hint is incorrectly true',async()=>{
 const t=await fixture();t.state.context.permissions.can_configure_quota=true;t.state.context.permissions.can_register_price=true;
 await t.c.refresh();t.c.updateDraft({quota_cost:'1.00',quota_reason:'用户本机理由',price_reason:'用户价格理由'});
 await assert.rejects(t.c.configureQuota());await assert.rejects(t.c.registerPrice());assert.equal(t.state.postCounts.quota,undefined);assert.equal(t.state.postCounts.price,undefined);
});

test('unavailable currency or excessive decimal precision cannot create quota request or fall back to CNY',async()=>{
 const t=await fixture({admin:true});t.c.updateDraft({quota_cost:'1.005',quota_reason:'不能隐式四舍五入'});
 await assert.rejects(t.c.configureQuota());assert.equal(t.state.postCounts.quota,undefined);
 t.c.updateDraft({quota_cost:'1.00'});t.state.context.price.currency=null;await assert.rejects(t.c.configureQuota());
 assert.equal(t.state.postCounts.quota,undefined);assert.equal(t.c.pending,null);
});

test('known rejected active quota retains its nonce and reason instead of pretending to renew an existing policy',async()=>{
 const t=await fixture({admin:true});t.c.updateDraft({quota_cost:'2.00',quota_reason:'原有效政策不能被此页面续期'});t.mode.rejection=true;
 await assert.rejects(t.c.configureQuota());assert.equal(t.c.pending.operation,'quota');assert.equal(t.state.postCounts.quota,1);
 assert.equal(t.c.pending.body.max_cost_minor_units,200);assert.equal(t.c.draft.quota_reason,'原有效政策不能被此页面续期');
});

test('known previous draft schema gains empty admin fields without dropping human reasons or selecting an operation',async()=>{
 const t=await fixture();t.c.updateDraft({prepare_reason:'上一版已保存的人稿'});
 for(const [key,raw]of [...t.storage.data.entries()])if(key.includes(':draft:')||key.endsWith(':draft-latest')){
  const row=JSON.parse(raw);if(row.schema==='same-goal-advice-local-draft-v310'){
   delete row.draft.quota_cost;delete row.draft.quota_reason;delete row.draft.price_reason;t.storage.setItem(key,JSON.stringify(row));
  }
 }
 t.c.dispose();const replacement=t.create();await replacement.open();
 assert.equal(replacement.draft.prepare_reason,'上一版已保存的人稿');assert.equal(replacement.draft.quota_cost,'');assert.equal(replacement.draft.price_reason,'');
 assert.equal(t.calls.filter(row=>row.method==='POST').length,0);
});

test('request hash matches the Root-executed Python Unicode newline emoji and backslash fixture exactly',async()=>{
 const body={request_id:'5d39cedd-cf1d-4b04-97be-e64676251770',expected_goal_hash:'a'.repeat(64),expected_mature_head_hash:'b'.repeat(64),
  document_source:null,reason:'核对“批准摘录”\n🌲\\路径'};
 const authoritative='4a8407672a3b99e168396781a83e19b938cbf371018aafe1312cfce04fd5aa5e';
 assert.equal(await adviceHash(adviceCanonical(body),webcrypto),authoritative);
 for(const reason of [body.reason.replace('🌲','🌳'),body.reason.replace('\n','\\n')])
  assert.notEqual(await adviceHash(adviceCanonical({...body,reason}),webcrypto),authoritative);
});

for(const available of [false,undefined])test('cancel requires explicit actual server availability, including old DTO '+available,async()=>{
 const t=await fixture();await t.prepared();if(available===undefined)delete t.state.plan.cancel_available;else t.state.plan.cancel_available=false;
 t.c.updateDraft({cancel_reason:'原取消理由保留'});await assert.rejects(t.c.cancel());assert.equal(t.state.postCounts.cancel,undefined);
 assert.equal(t.c.draft.cancel_reason,'原取消理由保留');assert.equal(t.c.plan.phase,'awaiting_consent');
});

test('actual cancel receipt, not a cancelled label, acknowledges saved original reason',async()=>{
 const t=await fixture();await t.prepared();t.c.updateDraft({cancel_reason:'明确取消并保留原材料'});t.mode.loseResponse='cancel';
 await assert.rejects(t.c.cancel());const nonce=t.c.pending.request_id;await t.c.recover();
 assert.equal(t.c.pending,null);assert.equal(t.state.postCounts.cancel,1);assert.equal(t.c.plan.phase,'cancelled');
 assert.ok(t.calls.some(row=>row.path===base+'/operations/'+nonce));
});

test('authenticated retained answer emits before slow context and history while all fresh writes remain blocked',async()=>{
 const t=await fixture();await t.ready();const expected=clone(t.c.plan.result),context=readGate(),history=readGate(),views=[];
 t.c.onChange=value=>views.push(value);t.mode.getGates=new Map([[base+'/context',context],[base+'/preparations',history]]);
 const start=t.calls.length,refresh=t.c.refresh();
 try{
  await Promise.all([context.entered.promise,history.entered.promise]);
  await until(()=>t.c.view().reads.plan==='ready'&&t.c.view().reads.documents==='ready'&&t.c.view().reads.preview==='ready');
  const value=t.c.view();assert.deepEqual(value.plan.result,expected);assert.equal(value.context,null);assert.equal(value.list,null);
  assert.deepEqual(value.reads,{context:'loading',history:'loading',plan:'ready',documents:'ready',preview:'ready'});
  assert.equal(value.busy,true);assert.equal(value.loading,true);assert.equal(t.c.guard(),false);
  assert.ok(views.some(view=>view.plan?.result&&view.reads.context==='loading'));
  const posts=t.calls.filter(row=>row.method==='POST').length;await assert.rejects(t.c.review());await assert.rejects(t.c.prepare());
  assert.equal(t.calls.filter(row=>row.method==='POST').length,posts);
 }finally{context.release.resolve();history.release.resolve();await refresh;}
 assert.equal(t.calls.slice(start).filter(row=>row.path===base+'/preparations/'+id(12)).length,1);
 assert.equal(t.c.view().loading,false);assert.equal(t.c.view().busy,false);
});

test('unknown selected Plan resolves only from the real unique active history without waiting for context',async()=>{
 const t=await fixture();await t.ready();t.c.planId='';assert.equal(t.c.persist(),true);t.c.dispose();
 const context=readGate();t.mode.getGates=new Map([[base+'/context',context]]);const replacement=t.create(),start=t.calls.length,opening=replacement.open();
 try{await context.entered.promise;await until(()=>replacement.view().reads.plan==='ready');
  assert.equal(replacement.planId,id(12));assert.ok(replacement.plan.result);assert.equal(replacement.context,null);
  assert.equal(replacement.view().reads.history,'ready');assert.equal(replacement.view().loading,true);
 }finally{context.release.resolve();await opening;}
 assert.equal(t.calls.slice(start).filter(row=>row.path===base+'/preparations/'+id(12)).length,1);
});

test('unknown Plan with multiple returned active records is not guessed from array order',async()=>{
 const t=await fixture();await t.ready();t.c.planId='';assert.equal(t.c.persist(),true);
 const second=clone(t.state.plan);Object.assign(second,{plan_id:id(90),request_id:id(91),prepare_job_id:id(92),preparation_snapshot_hash:sha(0)});
 second.send_job={...second.send_job,id:id(93)};second.result.plan_ref={id:second.plan_id,record_hash:sha(1)};
 t.mode.listRecords=[clone(t.state.plan),second];const start=t.calls.length;
 await t.c.refresh();assert.equal(t.c.planId,'');assert.equal(t.c.view().reads.history,'ready');assert.equal(t.c.view().reads.plan,'idle');
 assert.equal(t.c.plan,null);assert.equal(t.calls.slice(start).some(row=>row.path.startsWith(base+'/preparations/')),false);
});

test('read scheduling bounds the real controller synthetic API concurrency to three',async()=>{
 const t=await fixture();await t.ready();const context=readGate(),history=readGate(),plan=readGate(),documents=readGate();
 t.readActivity.maximum=0;t.mode.getGates=new Map([[base+'/context',context],[base+'/preparations',history],
  [base+'/preparations/'+id(12),plan],[base+'/preparations/'+id(12)+'/documents',documents]]);
 const refresh=t.c.refresh();
 try{
  await Promise.all([context.entered.promise,history.entered.promise,plan.entered.promise]);assert.equal(t.readActivity.active,3);
  assert.equal(t.c.view().reads.documents,'idle');plan.release.resolve();await documents.entered.promise;
  assert.equal(t.c.view().reads.plan,'ready');assert.equal(t.readActivity.active,3);
  documents.release.resolve();await until(()=>t.c.view().reads.preview==='ready');assert.ok(t.c.plan.result);
 }finally{context.release.resolve();history.release.resolve();plan.release.resolve();documents.release.resolve();await refresh;}
 assert.equal(t.readActivity.maximum,3);assert.equal(t.readActivity.active,0);
});

test('failed history does not erase independently checked answer and is never presented as an empty list',async()=>{
 const t=await fixture();await t.ready();t.mode.getErrors=new Map([[base+'/preparations',error(409,'合成历史回执需要核查')]]);
 await assert.rejects(t.c.refresh());const value=t.c.view();assert.equal(value.reads.history,'failed');assert.equal(value.list,null);
 assert.equal(value.reads.plan,'ready');assert.ok(value.plan.result);assert.equal(value.reads.documents,'ready');assert.equal(value.loading,false);
});

test('failed current context clears prior permissions but leaves checked historical answer readable',async()=>{
 const t=await fixture();await t.ready();assert.equal(t.c.context.permissions.can_prepare,true);t.mode.contextFailure=true;
 await t.c.refresh();assert.equal(t.c.view().reads.context,'failed');assert.equal(t.c.context,null);assert.ok(t.c.plan.result);
 const posts=t.calls.filter(row=>row.method==='POST').length;await assert.rejects(t.c.prepare());
 assert.equal(t.calls.filter(row=>row.method==='POST').length,posts);
});

test('documents failure and lawful absent preview have different explicit round states',async()=>{
 const t=await fixture();await t.prepared();t.mode.getErrors=new Map([[base+'/preparations/'+id(12)+'/documents',error(409,'原依据待核查')]]);
 await t.c.refresh();const value=t.c.view();assert.equal(value.reads.documents,'failed');assert.equal(value.documents,null);
 assert.equal(value.reads.preview,'ready');assert.equal(value.preview,null);assert.equal(value.reads.plan,'ready');
});

test('Plan read failure leaves downstream idle without falling back to a retained answer',async()=>{
 const t=await fixture();await t.ready();t.mode.getErrors=new Map([[base+'/preparations/'+id(12),error(409,'原回答需要核查')]]);
 await assert.rejects(t.c.refresh());const value=t.c.view();assert.equal(value.reads.plan,'failed');assert.equal(value.plan,null);
 assert.equal(value.reads.documents,'idle');assert.equal(value.documents,null);assert.equal(value.reads.preview,'idle');
});

for(const state of ['available','needs_check','not_found'])test('cold original '+state+' is checked and durably settled before slow context without automatic POST',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'慢配置也不能挡住原申请核对'});
 if(state==='not_found')t.mode.unknownWithoutCommit=true;else t.mode.loseResponse='prepare';
 await assert.rejects(t.c.prepare());const original=clone(t.c.pending);t.c.dispose();
 if(state==='needs_check')t.mode.needsCheck=true;
 const context=readGate();t.mode.getGates=new Map([[base+'/context',context]]);const replacement=t.create(),start=t.calls.length,opening=replacement.open();
 try{
  await context.entered.promise;assert.ok(t.calls.slice(start).some(row=>row.path===base+'/operations/'+original.request_id));
  assert.equal(t.state.postCounts.prepare,1);assert.equal(replacement.view().busy,true);assert.equal(replacement.view().loading,true);
  if(state==='not_found'){
   assert.equal(replacement.notFound,true);assert.equal(replacement.pending.raw,original.raw);
   assert.equal(t.storage.getItem(replacement.key+':received:'+original.request_id),null);
  }else{
   assert.equal(replacement.pending,null);assert.equal(t.storage.getItem(replacement.key+':pending'),null);
   const receipt=JSON.parse(t.storage.getItem(replacement.key+':received:'+original.request_id));
   assert.equal(receipt.body_hash,original.body_hash);assert.equal(receipt.result_status,state);assert.equal(replacement.needsCheck,state==='needs_check');
   await until(()=>replacement.view().reads.plan==='ready');
  }
 }finally{context.release.resolve();await opening;}
 assert.equal(t.calls.slice(start).some(row=>row.method==='POST'),false);
 assert.equal(t.storage.getItem(replacement.key+':original:'+original.request_id),JSON.stringify(original));
 if(state!=='not_found')assert.equal(t.calls.slice(start).filter(row=>row.path===base+'/preparations/'+id(12)).length,1);
});

test('wrong original receipt hash cannot clear pending while slow context is read',async()=>{
 const t=await fixture();t.c.updateDraft({selected_document_source:t.selection,prepare_reason:'只能承认完整原正文的回执'});t.mode.loseResponse='prepare';
 await assert.rejects(t.c.prepare());const original=clone(t.c.pending);t.c.dispose();t.mode.badReceipt={body_hash:sha(0)};
 const context=readGate();t.mode.getGates=new Map([[base+'/context',context]]);const replacement=t.create(),opening=replacement.open();
 try{await context.entered.promise;assert.equal(replacement.pending.raw,original.raw);
  assert.equal(t.storage.getItem(replacement.key+':received:'+original.request_id),null);
 }finally{context.release.resolve();await opening;}
 assert.equal(t.state.postCounts.prepare,1);assert.equal(replacement.pending.request_id,original.request_id);
});

test('403 on a delayed protected branch immediately hides early answer and rejects later context completion',async()=>{
 const t=await fixture();await t.ready();const context=readGate(),history=readGate(),views=[];
 t.c.onChange=value=>views.push(value);t.mode.getGates=new Map([[base+'/context',context],[base+'/preparations',history]]);
 const refresh=t.c.refresh();
 try{
  await Promise.all([context.entered.promise,history.entered.promise]);await until(()=>t.c.view().reads.plan==='ready');assert.ok(t.c.plan.result);
  t.mode.listForbidden=true;history.release.resolve();await until(()=>t.c.view().denied);
  assert.equal(t.c.view().plan,null);assert.equal(t.c.view().documents,null);assert.equal(t.c.view().context,null);
 }finally{history.release.resolve();context.release.resolve();await assert.rejects(refresh);}
 const deniedAt=views.findIndex(value=>value.denied);assert.ok(deniedAt>=0);
 assert.ok(views.slice(deniedAt).every(value=>value.plan===null&&value.documents===null&&value.preview===null&&value.context===null));
});

test('changed real session role during a late Plan response hides independently read branches',async()=>{
 const t=await fixture();await t.ready();const plan=readGate();t.mode.getGates=new Map([[base+'/preparations/'+id(12),plan]]);
 const refresh=t.c.refresh();await plan.entered.promise;t.state.me.role='viewer';plan.release.resolve();await assert.rejects(refresh);
 assert.equal(t.c.view().denied,true);assert.equal(t.c.view().plan,null);assert.equal(t.c.view().documents,null);assert.equal(t.c.view().context,null);
 assert.equal(t.state.sendJobs,1);
});
