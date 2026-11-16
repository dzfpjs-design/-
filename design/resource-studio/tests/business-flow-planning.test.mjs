import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {BusinessFlowPlanningController as Controller,FLOW_PLAN_BASE,FLOW_PLANNING_BASE,FLOW_PLANNING_PROFILES,flowPlanningModels,flowPlanningSHA,flowPlanningStatus} from '../src/business-flow-planning.mjs';
import {createRetainedControllerLifecycle} from '../src/retained-controller-lifecycle.mjs';

const id=n=>'10000000-0000-4000-8000-'+n.toString(16).padStart(12,'0');
const copy=value=>structuredClone(value);
function memory(){const data=new Map();return {data,failWrites:false,failFor:null,getItem(key){return data.get(key)??null;},setItem(key,value){if(this.failWrites||this.failFor?.(key))throw Error('synthetic storage quota');data.set(key,String(value));},removeItem(key){data.delete(key);}};}
function fixture({storage=memory(),actorId=id(2),profile='flow-v184'}={}){
 const expected=profile==='flow-v193'?{provider:'deepseek',model:'synthetic-deepseek',output:'report-business-flow-output-v193',preview:'/preview-flow-nested',start:'/start-flow-nested'}:profile==='flow-v190'?{provider:'deepseek',model:'synthetic-deepseek',output:'report-business-flow-output-v190',preview:'/preview-flow-causal',start:'/start-flow-causal'}:profile==='flow-v187'?{provider:'deepseek',model:'synthetic-deepseek',output:'report-business-flow-output-v187',preview:'/preview-flow-reasoned',start:'/start-flow-reasoned'}:{provider:'qwen',model:'synthetic-qwen',output:'report-business-flow-output-v184',preview:'/preview-flow-plan',start:'/start-flow-plan'};
 const session=memory(),account={user_id:actorId,workspace_id:id(3),role:'operator'},producerId=id(1),model={id:id(5),name:'合成已批准模型',status:'published',generation_profile:profile,execution:{provider:expected.provider,model:expected.model,report_evidence_output:{schema:expected.output}}};
 session.setItem('ga-setup-csrf','synthetic-csrf');session.setItem('ga-setup-csrf-user',actorId);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 const plan={id:id(4),version:2,revision:19,status:'confirmed',producer_id:producerId,plan_hash:'a'.repeat(64),source_summary:{input_count:920},ledger:{scopes:[{scope_id:'fire',name:'疑似火情复核',claim_positions:[2,7],context_positions:[8]}]}};
 const old={schema:'report-scoped-design-view-v181',id:id(6),producer_id:producerId,version:1,plan_id:plan.id,scope_id:'fire',status:'failed',generation_profile:'flow-v183',result:null,note:'旧失败回答及费用应保留',error:'原结构校验失败'};
 const items=new Map([[old.id,copy(old)]]),commands=new Map(),calls=[],mode={plans:[plan],models:[model],revision:19,lose:false,drop:false,wait:null,hideProbes:0,wrongHash:false,available:true};
 const request=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});
  assert.equal(options.headers?.['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-csrf');
  if(path==='/me')return {...account};
  if(path==='/setup/draft')return {revision:mode.revision};
  if(path===FLOW_PLAN_BASE+'/plans?producer_id='+producerId)return {producer_id:producerId,items:copy(mode.plans)};
  if(path===FLOW_PLANNING_BASE+'?producer_id='+producerId)return {producer_id:producerId,items:copy([...items.values()])};
  if(path===FLOW_PLANNING_BASE+'/preview-flow-plan'||path===FLOW_PLANNING_BASE+'/preview-flow-reasoned'||path===FLOW_PLANNING_BASE+'/preview-flow-causal'||path===FLOW_PLANNING_BASE+'/preview-flow-nested'){
   assert.equal(path,FLOW_PLANNING_BASE+expected.preview);assert.equal(options.method,'POST');const body=JSON.parse(options.body);assert.equal(body.generation_profile,profile);
   assert.deepEqual(Object.keys(body),['plan_id','expected_plan_version','plan_hash','scope_id','revision','model_id','note','activity_id','generation_profile']);
   return {schema:'report-scoped-design-preview-v181',producer_id:producerId,plan_id:body.plan_id,plan_version:body.expected_plan_version,plan_hash:body.plan_hash,scope_id:body.scope_id,revision:body.revision,model_id:body.model_id,generation_profile:profile,provider:expected.provider,model:expected.model,note:body.note,call_count:1,requires_separate_authorization:true,partial_scope:true,source_snapshot_hash:'b'.repeat(64),preview_hash:'c'.repeat(64),claim_count:920,map_count:3,materials:[{name:'公开合成报告'}],sent_payload:{input_claims:[{statement:'声明一'},{statement:'声明二'},{statement:'补充上下文'}],spans:[['w:p1/s1','公开合成原文。']]},input_summary:{token_bound:123},budget:{call_limit:1,conservative_cost_usd:0.632722,maximum_cost_usd:3}};
  }
  if(path.startsWith(FLOW_PLANNING_BASE+'/requests/')){
   const nonce=path.split('/requests/')[1].split('?')[0],expected=new URLSearchParams(path.split('?')[1]).get('expected_sha256');let saved=commands.get(nonce);if(mode.hideProbes>0){mode.hideProbes--;saved=null;}if(saved)assert.equal(saved.sha,expected);
   return {schema:'report-scoped-design-request-v181',request_id:nonce,wire_sha256:mode.wrongHash?'d'.repeat(64):expected,found:!!saved,result:saved?copy(saved.result):null};
  }
  if(path===FLOW_PLANNING_BASE+'/start-flow-plan'||path===FLOW_PLANNING_BASE+'/start-flow-reasoned'||path===FLOW_PLANNING_BASE+'/start-flow-causal'||path===FLOW_PLANNING_BASE+'/start-flow-nested'){
   assert.equal(path,FLOW_PLANNING_BASE+expected.start);assert.equal(options.method,'POST');const body=JSON.parse(options.body);assert.equal(body.request_id,body.activity_id);assert.equal(body.generation_profile,profile);assert.equal(body.consent,true);
   if(mode.drop)throw Error('synthetic not delivered');
   let saved=commands.get(body.request_id);if(saved)assert.equal(saved.raw,options.body);else{
    const result={schema:'report-scoped-design-result-v181',id:id(100+commands.size),job_id:id(200+commands.size),version:1,operation:'start',status:'queued'};
    saved={raw:options.body,sha:await flowPlanningSHA(options.body,webcrypto),result};commands.set(body.request_id,saved);
    items.set(result.id,{schema:'report-scoped-design-view-v181',...copy(result),producer_id:producerId,plan_id:body.plan_id,scope_id:body.scope_id,generation_profile:body.generation_profile,result:null,note:body.note,error:null});
   }
   if(mode.wait)await mode.wait;if(mode.lose){mode.lose=false;throw Error('synthetic response lost after save');}return copy(saved.result);
  }
  const prefix=FLOW_PLANNING_BASE+'/';if(path.startsWith(prefix)){const row=items.get(path.slice(prefix.length));assert.ok(row,'known saved activity');return copy(row);}
  assert.fail('unknown API '+path);
 };
 const options={account,producerId,request,storage,session,crypto:webcrypto,locks:{request:async(name,settings,run)=>{assert.equal(settings.mode,'exclusive');return run();}},models:()=>mode.models,prepareDraft:async()=>mode.revision,currentAvailable:()=>mode.available};
 return {storage,session,account,producerId,model,plan,old,items,commands,calls,mode,options,posts:()=>calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/start-flow-plan'||call.path===FLOW_PLANNING_BASE+'/start-flow-reasoned'||call.path===FLOW_PLANNING_BASE+'/start-flow-causal'||call.path===FLOW_PLANNING_BASE+'/start-flow-nested')};
}
async function configured(f){const c=new Controller(f.options);await c.open();c.edit({scope_id:'fire',model_id:f.model.id,note:'保留当前已确认业务和原人工稿'});return c;}

function catalogueSource(f){return {plan_id:f.plan.id,plan_version:f.plan.version,plan_hash:f.plan.plan_hash,producer_id:f.producerId,revision:f.plan.revision,scope_id:'fire',business_name:'疑似火情复核'};}
test('confirmed catalogue handoff selects the exact saved scope using only reads and preserves human input',async()=>{
 const f=fixture(),c=await configured(f);
 c.edit({scope_id:''});
 const from=f.calls.length;
 await c.selectCatalogueSource(catalogueSource(f),()=>true);
 assert.equal(c.state.form.scope_id,'fire');
 assert.equal(c.state.form.note,'保留当前已确认业务和原人工稿');
 assert.equal(c.state.form.model_id,f.model.id);
 assert.equal(JSON.parse(f.storage.getItem(c.key)).form.scope_id,'fire');
 assert.equal(c.quote,null);
 assert.ok(f.calls.slice(from).length>0&&f.calls.slice(from).every(call=>call.method==='GET'));
 c.dispose();
});
test('catalogue handoff rejects a drifted plan, mismatched business, or changed editor after refresh',async()=>{
 const f=fixture(),c=await configured(f),source=catalogueSource(f);
 c.edit({scope_id:''});
 const saved=f.storage.getItem(c.key);
 await assert.rejects(c.selectCatalogueSource({...source,plan_version:source.plan_version+1},()=>true),/完全一致/);
 await assert.rejects(c.selectCatalogueSource({...source,business_name:'其他业务'},()=>true),/业务条目/);
 let checks=0;
 await assert.rejects(c.selectCatalogueSource(source,()=>++checks===1),/期间目录或输入已变化/);
 assert.equal(f.storage.getItem(c.key),saved);
 assert.equal(c.state.form.scope_id,'');
 assert.ok(f.calls.every(call=>call.method==='GET'));
 c.dispose();
});

test('preview uses current confirmed business and exact nine-key 184 body without starting a paid activity',async()=>{
 const f=fixture(),c=await configured(f),quote=await c.preview();assert.equal(quote.view.sent_payload.input_claims.length,3);assert.equal(quote.view.claim_count,920);assert.equal(quote.body.plan_id,f.plan.id);assert.equal(quote.body.generation_profile,'flow-v184');assert.equal(f.posts().length,0);assert.deepEqual(f.items.get(f.old.id),f.old);c.dispose();
});
test('one deliberate start durably saves exact bytes and receipt; prior failed records and human work remain',async()=>{
 const f=fixture();f.storage.setItem('unrelated-human-draft','必须保留');const c=await configured(f);await c.preview();await c.start();assert.equal(f.posts().length,1);assert.equal(f.commands.size,1);assert.equal(c.pending,null);assert.equal(c.state.history.length,1);
 const body=JSON.parse(f.posts()[0].body),record=JSON.parse(f.storage.getItem(c.prefix+'original:'+body.request_id));assert.equal(record.raw,f.posts()[0].body);assert.equal(record.sha256,await flowPlanningSHA(record.raw,webcrypto));assert.deepEqual(Object.keys(body),['plan_id','expected_plan_version','plan_hash','scope_id','revision','model_id','note','activity_id','generation_profile','request_id','consent','source_snapshot_hash','preview_hash']);assert.equal(f.storage.getItem('unrelated-human-draft'),'必须保留');assert.deepEqual(f.items.get(f.old.id),f.old);c.dispose();
});
test('local storage failure retains typing and blocks preview/start before network submission',async()=>{
 const f=fixture(),c=await configured(f);f.storage.failWrites=true;c.edit({note:'本机保存失败也不能丢的要求'});assert.equal(c.localSaved,false);assert.equal(c.safeToLeave(),false);await assert.rejects(c.preview());await assert.rejects(c.start());assert.equal(f.posts().length,0);assert.equal(c.state.form.note,'本机保存失败也不能丢的要求');f.storage.failWrites=false;assert.equal(c.persist(),true);await c.preview();assert.equal(f.posts().length,0);c.dispose();
});
test('outbox failure preserves the same nonce and can resume only the exact protected original submission',async()=>{
 const f=fixture(),c=await configured(f);const quote=await c.preview();f.storage.failFor=key=>key.includes('original:');await assert.rejects(c.start());assert.equal(f.posts().length,0);assert.equal(c.pending.request_id,quote.body.activity_id);const raw=c.pending.raw;f.storage.failFor=null;assert.equal(c.persist(),true);await c.recover(true);assert.equal(f.posts().length,1);assert.equal(f.posts()[0].body,raw);assert.equal(f.commands.size,1);c.dispose();
});
test('lost response survives refresh; GET-only recovery preserves later typing and never repeats POST',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();f.mode.lose=true;await assert.rejects(c.start());const p=copy(c.pending);c.edit({note:'原提交之后继续输入的要求'});const oldKey=c.key,oldRaw=f.storage.getItem(oldKey);c.dispose();
 const next=new Controller(f.options);await next.open();assert.notEqual(next.key,oldKey);assert.deepEqual(next.pending,p);assert.equal(next.state.form.note,'原提交之后继续输入的要求');assert.equal(f.posts().length,1);await next.recover();assert.equal(next.pending,null);assert.equal(next.state.form.note,'原提交之后继续输入的要求');assert.equal(f.posts().length,1);assert.equal(f.storage.getItem(oldKey),oldRaw);next.dispose();
});
test('not found cannot trigger automatic resubmission; explicit retry retains nonce and exact raw',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();f.mode.drop=true;await assert.rejects(c.start());const p=copy(c.pending);await c.recover();assert.equal(c.notFound,true);assert.equal(f.posts().length,1);assert.deepEqual(c.pending,p);f.mode.drop=false;await c.recover(true);assert.equal(f.posts().length,2);assert.equal(f.posts()[1].body,p.raw);assert.equal(f.commands.size,1);c.dispose();
});
test('explicit duplicate submit replays one stored activity; successful GET prevents a further POST',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();f.mode.lose=true;await assert.rejects(c.start());const original=c.pending.raw;f.mode.hideProbes=1;await c.recover(true);assert.equal(f.posts().length,2);assert.equal(f.posts()[1].body,original);assert.equal(f.commands.size,1);assert.equal(f.items.size,2);await assert.rejects(c.recover(true));assert.equal(f.posts().length,2);c.dispose();
});
test('busy double click cannot stage another nonce or logical activity',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();let release;f.mode.wait=new Promise(resolve=>{release=resolve;});const first=c.start();await assert.rejects(c.start(),/正在核对/);for(let i=0;i<30&&!f.posts().length;i++)await new Promise(resolve=>setImmediate(resolve));assert.equal(f.posts().length,1);release();await first;assert.equal(f.commands.size,1);c.dispose();
});
test('account switch conceals private state and fences original in-flight completion',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();let release;f.mode.wait=new Promise(resolve=>{release=resolve;});const first=c.start();for(let i=0;i<30&&!f.posts().length;i++)await new Promise(resolve=>setImmediate(resolve));f.session.setItem('ga-setup-csrf-user',id(99));assert.equal(c.visible,false);assert.throws(()=>c.edit({note:'另一账号不能覆盖'}));release();await assert.rejects(first);assert.ok(c.pending);c.dispose();
 const other=fixture({storage:f.storage,actorId:id(99)}),second=new Controller(other.options);await second.open();assert.equal(second.pending,null);assert.equal(second.state.form.note,'');assert.notEqual(second.prefix,c.prefix);second.dispose();
});
test('missing or obsolete confirmed scope cannot be treated as automatic whole-report classification',async()=>{
 for(const variant of ['missing','old','draft']){const f=fixture();if(variant==='missing')f.mode.plans=[];if(variant==='old')f.plan.revision=18;if(variant==='draft')f.plan.status='draft';const c=new Controller(f.options);await c.open();c.edit({plan_id:f.plan.id,scope_id:'fire',model_id:f.model.id});assert.equal(c.plan,null);await assert.rejects(c.preview(),/尚未确认业务范围/);assert.equal(f.posts().length,0);c.dispose();}
});
test('only explicit published Qwen 184 model metadata is eligible',()=>{
 const f=fixture(),valid=f.model;assert.deepEqual(flowPlanningModels([valid]),[valid]);for(const edit of [{status:'draft'},{generation_profile:'flow-v183'},{execution:{...valid.execution,provider:'deepseek'}},{execution:{...valid.execution,report_evidence_output:{schema:'report-business-flow-output-v183'}}},{execution:undefined}])assert.equal(flowPlanningModels([{...valid,...edit}]).length,0);
});
test('unknown provider outcome is read-only history and blocks another generation activity',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();await c.start();const row=[...f.items.values()].find(item=>item.generation_profile==='flow-v184');row.status='response_unknown';await c.operation(()=>c.refresh());await c.selectAttempt(row.id);await assert.rejects(c.preview(),/原调用结果待核对/);assert.equal(f.posts().length,1);assert.equal(c.selected.status,'response_unknown');c.dispose();
});
test('parent unsaved-work boundary blocks paid actions while saved records remain readable',async()=>{
 const f=fixture(),c=await configured(f);f.mode.available=false;await assert.rejects(c.preview());assert.equal(f.posts().length,0);await c.selectAttempt(f.old.id);assert.equal(c.selected.status,'failed');assert.equal(c.state.form.note,'保留当前已确认业务和原人工稿');c.dispose();
});
test('a mismatched original receipt cannot clear pending or claim a saved result',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();f.mode.lose=true;await assert.rejects(c.start());const raw=c.pending.raw;f.mode.wrongHash=true;await assert.rejects(c.recover());assert.equal(c.pending.raw,raw);assert.equal(c.state.history.length,0);assert.equal(f.posts().length,1);c.dispose();
});
test('StrictMode retained lifetime repeats no POST and real unmount invalidates later work',async()=>{
 const f=fixture(),c=new Controller(f.options),queue=[],life=createRetainedControllerLifecycle({defer:fn=>queue.push(fn)}),one=life.setup(c,'same');const opening=c.open();life.cleanup(one);const two=life.setup(c,'same');queue.splice(0).forEach(fn=>fn());await opening;assert.equal(await c.open(),true);assert.equal(f.posts().length,0);assert.equal(c.active,true);life.cleanup(two);queue.splice(0).forEach(fn=>fn());assert.equal(c.active,false);await assert.rejects(c.preview());assert.equal(f.posts().length,0);
});
test('conservative estimate must be a finite nonnegative number within the separate cost maximum',async()=>{
 const f=fixture(),c=await configured(f),quote=await c.preview();assert.equal(quote.view.budget.conservative_cost_usd,0.632722);assert.equal(quote.view.budget.maximum_cost_usd,3);
 for(const invalid of [undefined,null,'0.1',NaN,Infinity,-0.01,3.01]){const view=copy(quote.view);view.budget.conservative_cost_usd=invalid;assert.throws(()=>c.checkPreview(view,quote.body),/报价/);}
 const free=copy(quote.view);free.budget.conservative_cost_usd=0;assert.equal(c.checkPreview(free,quote.body),free);assert.equal(f.posts().length,0);c.dispose();
});
test('reviewed activity remains readable as saved human review without implying business adoption',async()=>{
 const f=fixture(),c=await configured(f),row=f.items.get(f.old.id);row.status='reviewed';row.generation_profile='flow-v184';await c.operation(()=>c.refresh());await c.selectAttempt(row.id);assert.equal(c.selected.status,'reviewed');assert.equal(flowPlanningStatus('reviewed'),'已保存人工核对');assert.equal(f.posts().length,0);c.dispose();
});
test('frozen 184 and 187 metadata route DeepSeek preview and the exact new start without changing source scope',async()=>{
 assert.equal(Object.isFrozen(FLOW_PLANNING_PROFILES),true);assert.equal(Object.isFrozen(FLOW_PLANNING_PROFILES['flow-v184']),true);assert.equal(Object.isFrozen(FLOW_PLANNING_PROFILES['flow-v187']),true);
 assert.deepEqual(FLOW_PLANNING_PROFILES['flow-v184'],{provider:'qwen',output:'report-business-flow-output-v184',preview:'/preview-flow-plan',start:'/start-flow-plan'});assert.deepEqual(FLOW_PLANNING_PROFILES['flow-v187'],{provider:'deepseek',output:'report-business-flow-output-v187',preview:'/preview-flow-reasoned',start:'/start-flow-reasoned'});assert.throws(()=>{FLOW_PLANNING_PROFILES['flow-v187'].provider='qwen';},TypeError);
 const f=fixture({profile:'flow-v187'}),c=await configured(f),quote=await c.preview();assert.equal(quote.body.generation_profile,'flow-v187');assert.equal(quote.view.provider,'deepseek');assert.equal(quote.view.model,'synthetic-deepseek');assert.equal(quote.view.sent_payload.input_claims.length,3);assert.equal(quote.view.claim_count,920);assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/preview-flow-reasoned').length,1);await c.start();assert.equal(f.posts().length,1);assert.equal(f.posts()[0].path,FLOW_PLANNING_BASE+'/start-flow-reasoned');assert.equal(JSON.parse(f.posts()[0].body).generation_profile,'flow-v187');assert.equal(c.state.history.length,1);assert.deepEqual(f.items.get(f.old.id),f.old);c.dispose();
});
test('saved 184 pending survives missing or replaced model list and retries only original 184 endpoint and bytes',async()=>{
 const f=fixture(),c=await configured(f);await c.preview();f.mode.drop=true;await assert.rejects(c.start());const p=copy(c.pending),original=f.storage.getItem(c.prefix+'original:'+p.request_id);c.dispose();f.mode.models=[];
 const next=new Controller(f.options);await next.open();assert.deepEqual(next.pending,p);assert.equal(flowPlanningModels(f.mode.models).length,0);await next.recover();assert.equal(f.posts().length,1);f.mode.models=[{id:id(77),status:'published',generation_profile:'flow-v187',execution:{provider:'deepseek',model:'synthetic-deepseek',report_evidence_output:{schema:'report-business-flow-output-v187'}}}];f.mode.drop=false;await next.recover(true);
 assert.equal(f.posts().length,2);assert.equal(f.posts()[1].path,FLOW_PLANNING_BASE+'/start-flow-plan');assert.equal(f.posts()[1].body,p.raw);assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/start-flow-reasoned').length,0);assert.equal(f.storage.getItem(next.prefix+'original:'+p.request_id),original);assert.equal(f.commands.size,1);next.dispose();
});
test('choosing a different provider invalidates the old quote and cannot silently reuse its activity',async()=>{
 const f=fixture(),c=await configured(f),quote=await c.preview(),deep={id:id(77),status:'published',generation_profile:'flow-v187',execution:{provider:'deepseek',model:'synthetic-deepseek',report_evidence_output:{schema:'report-business-flow-output-v187'}}};f.mode.models.push(deep);c.edit({model_id:deep.id});assert.equal(c.quote,null);assert.equal(quote.body.generation_profile,'flow-v184');await assert.rejects(c.start(),/重新核对本次范围与费用/);assert.equal(c.pending,null);assert.equal(f.posts().length,0);c.dispose();
});
test('187 quote rejects wrong provider, model or profile and inconsistent published public metadata',async()=>{
 const f=fixture({profile:'flow-v187'}),c=await configured(f),q=await c.preview();for(const change of [{provider:'qwen'},{model:'synthetic-qwen'},{generation_profile:'flow-v184'}])assert.throws(()=>c.checkPreview({...copy(q.view),...change},q.body),/模型协议/);
 assert.equal(flowPlanningModels([{...f.model,provider:'qwen'}]).length,0);assert.equal(flowPlanningModels([{...f.model,model:'different-model'}]).length,0);assert.equal(flowPlanningModels([{...f.model,execution:{...f.model.execution,report_evidence_output:{schema:'report-business-flow-output-v184'}}}]).length,0);assert.equal(flowPlanningModels([{...f.model,generation_profile:'flow-v185'}]).length,0);assert.equal(f.posts().length,0);c.dispose();
});
test('another tab shared 187 original blocks an already quoted 184 start and clears the unrelated quote',async()=>{
 const storage=memory(),qwen=fixture({storage}),q=await configured(qwen);await q.preview();assert.equal(q.quote.body.generation_profile,'flow-v184');
 const deep=fixture({storage,profile:'flow-v187'}),d=await configured(deep);await d.preview();deep.mode.drop=true;await assert.rejects(d.start());const p=copy(d.pending),original=storage.getItem(d.prefix+'original:'+p.request_id);
 await assert.rejects(q.start(),/另一个窗口已有原提交/);assert.deepEqual(q.pending,p);assert.equal(q.pending.body.generation_profile,'flow-v187');assert.equal(q.quote,null);assert.equal(qwen.posts().length,0);assert.equal(storage.getItem(q.prefix+'original:'+p.request_id),original);assert.equal(q.prefix,d.prefix,'existing actor/workspace/report shared guard is unchanged');q.dispose();d.dispose();
});
test('190 uses frozen causal metadata and restores its exact original endpoint even when only an older model is listed',async()=>{
 assert.equal(Object.isFrozen(FLOW_PLANNING_PROFILES['flow-v190']),true);assert.deepEqual(FLOW_PLANNING_PROFILES['flow-v190'],{provider:'deepseek',output:'report-business-flow-output-v190',preview:'/preview-flow-causal',start:'/start-flow-causal'});
 const f=fixture({profile:'flow-v190'}),c=await configured(f),quote=await c.preview();assert.equal(quote.body.generation_profile,'flow-v190');assert.equal(Object.keys(quote.body).length,9);assert.equal(quote.view.provider,'deepseek');assert.equal(quote.view.sent_payload.input_claims.length,3);assert.equal(quote.view.claim_count,920);assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/preview-flow-causal').length,1);
 f.mode.drop=true;await assert.rejects(c.start());const p=copy(c.pending),original=f.storage.getItem(c.prefix+'original:'+p.request_id);assert.equal(f.posts()[0].path,FLOW_PLANNING_BASE+'/start-flow-causal');c.dispose();f.mode.models=[{id:id(77),status:'published',generation_profile:'flow-v187',execution:{provider:'deepseek',model:'synthetic-deepseek',report_evidence_output:{schema:'report-business-flow-output-v187'}}}];
 const next=new Controller(f.options);await next.open();assert.deepEqual(next.pending,p);f.mode.drop=false;await next.recover(true);assert.equal(f.posts().length,2);assert.equal(f.posts()[1].path,FLOW_PLANNING_BASE+'/start-flow-causal');assert.equal(f.posts()[1].body,p.raw);assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/start-flow-plan'||call.path===FLOW_PLANNING_BASE+'/start-flow-reasoned').length,0);assert.equal(f.commands.size,1);assert.equal(f.storage.getItem(next.prefix+'original:'+p.request_id),original);assert.deepEqual(f.items.get(f.old.id),f.old);next.dispose();
});
test('193 uses the same durable outbox and exact nested endpoint without rewriting older family bytes',async()=>{
 assert.equal(Object.isFrozen(FLOW_PLANNING_PROFILES['flow-v193']),true);assert.deepEqual(FLOW_PLANNING_PROFILES['flow-v193'],{provider:'deepseek',output:'report-business-flow-output-v193',preview:'/preview-flow-nested',start:'/start-flow-nested'});
 const f=fixture({profile:'flow-v193'}),c=await configured(f);await c.preview();assert.equal(c.quote.body.generation_profile,'flow-v193');assert.equal(Object.keys(c.quote.body).length,9);
 f.mode.lose=true;await assert.rejects(c.start());const p=copy(c.pending),raw=f.storage.getItem(c.prefix+'original:'+p.request_id);c.dispose();f.mode.models=[];
 const restored=new Controller(f.options);await restored.open();assert.deepEqual(restored.pending,p);await restored.recover();assert.equal(restored.pending,null);assert.equal(f.posts().length,1);assert.equal(f.posts()[0].path,FLOW_PLANNING_BASE+'/start-flow-nested');assert.equal(f.posts()[0].body,p.raw);assert.equal(f.storage.getItem(restored.prefix+'original:'+p.request_id),raw);assert.deepEqual(f.items.get(f.old.id),f.old);assert.equal(f.calls.filter(call=>call.path===FLOW_PLANNING_BASE+'/start-flow-plan'||call.path===FLOW_PLANNING_BASE+'/start-flow-causal').length,0);restored.dispose();
});
