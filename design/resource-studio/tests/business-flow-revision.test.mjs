import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {BusinessFlowRevisionController as Controller,FLOW_REVISION_BASE,flowRevisionModels,flowRevisionSHA,checkedFlowRevisionParent} from '../src/business-flow-revision.mjs';
import {createRetainedControllerLifecycle} from '../src/retained-controller-lifecycle.mjs';

const id=n=>'10000000-0000-4000-8000-'+n.toString(16).padStart(12,'0');
const copy=value=>structuredClone(value);
function memory(){const data=new Map();return {data,failWrites:false,failFor:null,getItem(key){return data.get(key)??null;},setItem(key,value){if(this.failWrites||this.failFor?.(key))throw Error('synthetic storage quota');data.set(key,String(value));},removeItem(key){data.delete(key);}};}
function fixture({storage=memory(),actorId=id(2),parentId=id(7)}={}){
 const session=memory(),account={user_id:actorId,workspace_id:id(3),role:'operator'},producerId=id(1),model={id:id(5),name:'公开合成复核模型',status:'published',generation_profile:'flow-v185',execution:{provider:'qwen',model:'synthetic-qwen',report_evidence_output:{schema:'report-business-flow-output-v185'}}};
 session.setItem('ga-setup-csrf','synthetic-csrf');session.setItem('ga-setup-csrf-user',actorId);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 const plan={id:id(4),version:2,revision:19,status:'confirmed',producer_id:producerId,plan_hash:'a'.repeat(64),ledger:{scopes:[{scope_id:'fire',name:'合成业务复核',claim_positions:[1],context_positions:[2]}]}};
 const raw={schema:'synthetic-raw-plan',business:{name:'合成业务复核',flows:[{name:'原始流程',phases:[{kind:'steps',steps:[{name:'核对来源',role:'business_agent'}]}]}],questions:[]}};
 const parent={schema:'report-scoped-design-view-v181',id:parentId,version:1,producer_id:producerId,request_id:id(30),model_id:id(6),plan_id:plan.id,plan_version:2,plan_hash:plan.plan_hash,scope_id:'fire',source_snapshot_hash:'b'.repeat(64),status:'needs_clarification',generation_profile:'flow-v184',projection:{schema:'report-business-flow-projection-v184',raw_plan:raw},manual_value:{result:{business:{name:'尚未保存的人工修订，不能发给模型'}}}};
 const parentSource={schema:'synthetic-parent-source',binding:{attempt_id:parent.id,plan_id:plan.id,plan_hash:plan.plan_hash,scope_id:'fire',source_snapshot_hash:parent.source_snapshot_hash,raw_plan_hash:'d'.repeat(64)},raw_plan:copy(raw),parent_hash:'e'.repeat(64)};
 const other={schema:'report-scoped-design-view-v181',id:id(50),version:1,producer_id:producerId,parent_attempt_id:id(99),status:'failed',generation_profile:'flow-v185',projection:null,note:'另一原流程的复核必须保留'};
 const rows=new Map([[other.id,copy(other)]]),commands=new Map(),calls=[],mode={drop:false,lose:false,hideProbes:0,wait:null,available:true,wrongHash:false,revision:19};
 const request=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-csrf');
  if(path==='/me')return {...account};
  if(path==='/setup/draft')return {revision:mode.revision};
  if(path==='/setup/report-scoped-designs/v181/plans?producer_id='+producerId)return {producer_id:producerId,items:[copy(plan)]};
  if(path===FLOW_REVISION_BASE+'/'+parent.id)return copy(parent);
  if(path===FLOW_REVISION_BASE+'?producer_id='+producerId)return {producer_id:producerId,items:[copy(parent),...copy([...rows.values()])]};
  if(path===FLOW_REVISION_BASE+'/preview-flow-revision'){
   assert.equal(options.method,'POST');const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body),['plan_id','expected_plan_version','plan_hash','scope_id','revision','model_id','note','activity_id','generation_profile','parent_attempt_id']);assert.equal(body.parent_attempt_id,parent.id);
   return {schema:'report-scoped-design-preview-v181',producer_id:producerId,plan_id:body.plan_id,plan_version:body.expected_plan_version,plan_hash:body.plan_hash,scope_id:body.scope_id,revision:body.revision,model_id:body.model_id,note:body.note,generation_profile:'flow-v185',provider:'qwen',parent_attempt_id:parent.id,parent_source_hash:'f'.repeat(64),base_plan_hash:'d'.repeat(64),platform_policy_hash:'1'.repeat(64),source_snapshot_hash:'b'.repeat(64),preview_hash:'c'.repeat(64),call_count:1,requires_separate_authorization:true,partial_scope:true,claim_count:920,sent_payload:{input_claims:[{statement:'公开合成业务声明'},{statement:'公开合成上下文'}],spans:[['w:p1/s1','公开合成原文。']],parent_source:copy(parentSource),platform_policy:{business_agent:{role:'统一业务决策与协调'},perception_agent:{role:'感知及补充观测'},data_agent:{role:'来源、质量与持久化'}}},input_summary:{token_bound:1000},budget:{call_limit:1,conservative_cost_usd:0.625520,maximum_cost_usd:3}};
  }
  if(path.startsWith(FLOW_REVISION_BASE+'/requests/')){const nonce=path.split('/requests/')[1].split('?')[0],sha=new URLSearchParams(path.split('?')[1]).get('expected_sha256');let saved=commands.get(nonce);if(mode.hideProbes>0){mode.hideProbes--;saved=null;}if(saved)assert.equal(saved.sha,sha);return {schema:'report-scoped-design-request-v181',request_id:nonce,wire_sha256:mode.wrongHash?'0'.repeat(64):sha,found:!!saved,result:saved?copy(saved.result):null};}
  if(path===FLOW_REVISION_BASE+'/start-flow-revision'){
   assert.equal(options.method,'POST');const body=JSON.parse(options.body);assert.equal(body.parent_attempt_id,parent.id);assert.equal(body.request_id,body.activity_id);assert.notEqual(body.request_id,parent.request_id);assert.equal(body.generation_profile,'flow-v185');assert.equal(body.consent,true);if(mode.drop)throw Error('synthetic not delivered');let saved=commands.get(body.request_id);
   if(saved)assert.equal(saved.raw,options.body);else{const result={schema:'report-scoped-design-result-v181',id:id(100+commands.size),job_id:id(200+commands.size),version:1,status:'queued',operation:'start'};saved={raw:options.body,sha:await flowRevisionSHA(options.body,webcrypto),result};commands.set(body.request_id,saved);rows.set(result.id,{schema:'report-scoped-design-view-v181',...copy(result),producer_id:producerId,parent_attempt_id:parent.id,plan_id:plan.id,scope_id:'fire',generation_profile:'flow-v185',projection:null,note:body.note,error:null});}
   if(mode.wait)await mode.wait;if(mode.lose){mode.lose=false;throw Error('synthetic response lost after save');}return copy(saved.result);
  }
  if(path.startsWith(FLOW_REVISION_BASE+'/')){const row=rows.get(path.slice(FLOW_REVISION_BASE.length+1));assert.ok(row);return copy(row);}assert.fail('unknown API '+path);
 };
 const options={account,producerId,parentAttemptId:parent.id,request,storage,session,crypto:webcrypto,locks:{request:async(name,settings,run)=>{assert.equal(settings.mode,'exclusive');return run();}},models:()=>[model],prepareDraft:async()=>mode.revision,currentAvailable:()=>mode.available};
 return {storage,session,account,producerId,model,plan,parent,parentSource,other,rows,commands,calls,mode,options,posts:()=>calls.filter(row=>row.path===FLOW_REVISION_BASE+'/start-flow-revision')};
}
async function configured(f){const c=new Controller(f.options);await c.open();c.edit({model_id:f.model.id,note:'对照原文核对时序、并行和三智能体职责'});return c;}

test('185 preview binds normal terminal parent and sends immutable AI plan rather than human draft',async()=>{
 const f=fixture(),c=await configured(f),quote=await c.preview();assert.equal(quote.body.parent_attempt_id,f.parent.id);assert.equal(quote.body.generation_profile,'flow-v185');assert.deepEqual(quote.view.sent_payload.parent_source.raw_plan,f.parent.projection.raw_plan);assert.notDeepEqual(quote.view.sent_payload.parent_source.raw_plan,f.parent.manual_value);assert.equal(quote.view.sent_payload.input_claims.length,2);assert.equal(quote.view.claim_count,920);assert.notEqual(quote.view.parent_source_hash,quote.view.sent_payload.parent_source.parent_hash);assert.equal(f.posts().length,0);c.dispose();
});
test('parent plan and business scope cannot be entered or changed by editing the revision form',async()=>{
 const f=fixture(),c=await configured(f);for(const patch of [{plan_id:id(99)},{scope_id:'other'},{parent_attempt_id:id(99)}])assert.throws(()=>c.edit(patch),/不能手填/);assert.equal(c.state.form.plan_id,f.parent.plan_id);assert.equal(c.state.form.scope_id,f.parent.scope_id);c.dispose();
});
test('new deliberate activity saves exact 14-key body, independent nonce and original receipt',async()=>{
 const f=fixture(),c=await configured(f),parentBefore=copy(f.parent),otherBefore=copy(f.other);f.storage.setItem('business-flow-planning:v184:existing','旧184恢复稿');await c.preview();await c.start();assert.equal(f.posts().length,1);assert.equal(f.commands.size,1);assert.equal(c.pending,null);const body=JSON.parse(f.posts()[0].body);assert.equal(Object.keys(body).length,14);assert.equal(body.parent_attempt_id,f.parent.id);assert.notEqual(body.request_id,f.parent.request_id);const p=JSON.parse(f.storage.getItem(c.prefix+'original:'+body.request_id));assert.equal(p.raw,f.posts()[0].body);assert.equal(p.sha256,await flowRevisionSHA(p.raw,webcrypto));assert.deepEqual(f.parent,parentBefore);assert.deepEqual(f.rows.get(f.other.id),otherBefore);assert.equal(f.storage.getItem('business-flow-planning:v184:existing'),'旧184恢复稿');c.dispose();
});
test('storage failure blocks paid submission and protects original in-memory requirements',async()=>{
 const f=fixture(),c=await configured(f);f.storage.failWrites=true;c.edit({note:'存储故障也必须保留的复核输入'});assert.equal(c.localSaved,false);assert.equal(c.safeToLeave(),false);await assert.rejects(c.preview());await assert.rejects(c.start());assert.equal(f.posts().length,0);assert.equal(c.state.form.note,'存储故障也必须保留的复核输入');f.storage.failWrites=false;assert.equal(c.persist(),true);c.dispose();
});
test('failed outbox protection cannot send; later protection and explicit retry use original nonce',async()=>{
 const f=fixture(),c=await configured(f),quote=await c.preview();f.storage.failFor=key=>key.includes('original:');await assert.rejects(c.start());assert.equal(f.posts().length,0);assert.equal(c.pending.request_id,quote.body.activity_id);const raw=c.pending.raw;f.storage.failFor=null;assert.equal(c.persist(),true);await c.recover(true);assert.equal(f.posts()[0].body,raw);assert.equal(f.commands.size,1);c.dispose();
});
test('lost response and refresh restore independent parent-scoped pending; GET never repeats paid POST',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();f.mode.lose=true;await assert.rejects(c.start());const p=copy(c.pending),oldKey=c.key,oldRaw=f.storage.getItem(oldKey);c.dispose();const next=new Controller(f.options);await next.open();assert.deepEqual(next.pending,p);assert.notEqual(next.key,oldKey);assert.equal(f.posts().length,1);await next.recover();assert.equal(next.pending,null);assert.equal(f.posts().length,1);assert.equal(f.storage.getItem(oldKey),oldRaw);next.dispose();
});
test('unknown receipt allows only explicit identical retry and never invents another activity',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();f.mode.lose=true;await assert.rejects(c.start());const raw=c.pending.raw;f.mode.hideProbes=1;await c.recover();assert.equal(c.notFound,true);assert.equal(f.posts().length,1);f.mode.hideProbes=1;await c.recover(true);assert.equal(f.posts().length,2);assert.equal(f.posts()[1].body,raw);assert.equal(f.commands.size,1);c.dispose();
});
test('parent, producer and profile must all agree before preview',async()=>{
 for(const change of [{status:'running'},{producer_id:id(99)},{generation_profile:'flow-v183'},{projection:{schema:'report-business-flow-projection-v183',raw_plan:{business:{flows:[]}}}}]){const f=fixture();Object.assign(f.parent,change);const c=new Controller(f.options);await assert.rejects(c.open(),/已保存、已结束/);assert.equal(f.posts().length,0);c.dispose();}
});
test('all new preview pins and original raw plan are checked without equating parent hash with source hash',async()=>{
 const f=fixture(),c=await configured(f),q=await c.preview();for(const field of ['parent_source_hash','base_plan_hash','platform_policy_hash']){const bad=copy(q.view);bad[field]='not-a-hash';assert.throws(()=>c.checkPreview(bad,q.body));}
 const wrongParent=copy(q.view);wrongParent.parent_attempt_id=id(99);assert.throws(()=>c.checkPreview(wrongParent,q.body));const wrongPlan=copy(q.view);wrongPlan.sent_payload.parent_source.raw_plan.business.name='替代原AI稿';assert.throws(()=>c.checkPreview(wrongPlan,q.body));const wrongBase=copy(q.view);wrongBase.base_plan_hash='0'.repeat(64);assert.throws(()=>c.checkPreview(wrongBase,q.body));assert.equal(f.posts().length,0);c.dispose();
});
test('account and parent lifetimes cannot share private drafts or pending original bytes',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();f.mode.drop=true;await assert.rejects(c.start());const original=c.pending.raw;f.session.setItem('ga-setup-csrf-user',id(99));assert.equal(c.visible,false);await assert.rejects(c.recover());c.dispose();
 const otherAccount=fixture({storage:f.storage,actorId:id(99)}),a=new Controller(otherAccount.options);await a.open();assert.equal(a.pending,null);assert.equal(a.state.form.note,'');a.dispose();const otherParent=fixture({storage:f.storage,parentId:id(88)}),b=new Controller(otherParent.options);await b.open();assert.equal(b.pending,null);assert.equal(b.state.form.note,'');assert.notEqual(b.prefix,c.prefix);assert.ok([...f.storage.data.values()].some(raw=>raw.includes(JSON.parse(original).request_id)));b.dispose();
});
test('only published explicit 185 models qualify and parent work guard blocks paid preview',async()=>{
 const f=fixture(),c=await configured(f);assert.equal(flowRevisionModels([f.model]).length,1);for(const patch of [{status:'draft'},{generation_profile:'flow-v184'},{execution:{...f.model.execution,report_evidence_output:{schema:'report-business-flow-output-v184'}}}])assert.equal(flowRevisionModels([{...f.model,...patch}]).length,0);f.mode.available=false;await assert.rejects(c.preview());assert.equal(f.posts().length,0);c.dispose();
});
test('response_unknown remains the original activity and blocks another revision',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();await c.start();const row=[...f.rows.values()].find(item=>item.parent_attempt_id===f.parent.id);row.status='response_unknown';await c.operation(()=>c.refresh());await assert.rejects(c.preview(),/结果待核对/);assert.equal(f.posts().length,1);c.dispose();
});
test('a result for another parent is rejected and cannot replace selected review',async()=>{
 const f=fixture(),c=await configured(f);await assert.rejects(c.selectAttempt(f.other.id),/原流程不能对应/);assert.equal(c.state.selected,null);assert.equal(f.posts().length,0);assert.equal(c.items.length,0);c.dispose();
});
test('StrictMode initialization has zero POSTs and double start cannot create another nonce',async()=>{
 const f=fixture(),c=new Controller(f.options),queue=[],life=createRetainedControllerLifecycle({defer:fn=>queue.push(fn)}),one=life.setup(c,'same-parent');const opening=c.open();life.cleanup(one);const two=life.setup(c,'same-parent');queue.splice(0).forEach(fn=>fn());await opening;assert.equal(f.posts().length,0);c.edit({model_id:f.model.id});await c.preview();let release;f.mode.wait=new Promise(resolve=>{release=resolve;});const first=c.start();await assert.rejects(c.start(),/正在核对/);for(let i=0;i<30&&!f.posts().length;i++)await new Promise(resolve=>setImmediate(resolve));assert.equal(f.posts().length,1);release();await first;assert.equal(f.commands.size,1);life.cleanup(two);queue.splice(0).forEach(fn=>fn());assert.equal(c.active,false);await assert.rejects(c.preview());
});
test('original receipt SHA mismatch keeps the pending bytes and cannot claim a completed submit',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();f.mode.lose=true;await assert.rejects(c.start());const p=copy(c.pending);f.mode.wrongHash=true;await assert.rejects(c.recover());assert.deepEqual(c.pending,p);assert.equal(c.state.history.length,0);assert.equal(f.posts().length,1);c.dispose();
});
