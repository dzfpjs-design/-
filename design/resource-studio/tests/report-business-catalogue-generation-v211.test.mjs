// Synthetic requests/storage only. Root runs; no provider, PG or browser claims.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto,randomUUID,createHash} from 'node:crypto';
import {CatalogueGenerationClientV211,catalogueGenerationModelsV211,catalogueGenerationViewV211,catalogueGenerationPreviewV211,catalogueGenerationPartialV212,CATALOGUE_GENERATION_BASE_V211 as BASE} from '../src/report-business-catalogue-generation-v211.mjs';
import {catalogueSourceAvailable} from '../src/report-business-catalogue-generation-v211.mjs';

test('a successful claim-enumeration report opens catalogue preparation',()=>{
 const producer={status:'review',phase:'review',reduction:{status:'succeeded'},
  preview:{map_preparation:{generation_contract:{schema:'report-claim-enum-map-generation-v172'}}}};
 assert.equal(catalogueSourceAvailable(producer),true);
 assert.equal(catalogueSourceAvailable({...producer,reduction:{status:'running'}}),false);
 assert.equal(catalogueSourceAvailable({...producer,preview:{}}),false);
});

const id=n=>'21121100-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value));
const flags={semantic_quality_verified:false,can_apply:false,can_publish:false};
class Storage{
 constructor(){this.rows=new Map();this.failPending=false;this.failForm=false;this.failRemove=false;this.formCurrentWrites=0;this.failFormCurrentAt=null;}
 get length(){return this.rows.size;}key(n){return [...this.rows.keys()][n]??null;}getItem(key){return this.rows.get(key)??null;}
 setItem(key,value){if(key.endsWith(':form-current')){this.formCurrentWrites++;if(this.formCurrentWrites===this.failFormCurrentAt)throw Error('synthetic shared form pointer quota');}if(this.failPending&&key.endsWith(':pending')||this.failForm&&key.includes(':form'))throw Error('synthetic storage quota');this.rows.set(key,String(value));}
 removeItem(key){if(!this.failRemove)this.rows.delete(key);}
}
function locking(){const tails=new Map();return {request(key,_options,run){const next=(tails.get(key)||Promise.resolve()).then(run),tail=next.catch(()=>{});tails.set(key,tail);return next.finally(()=>{if(tails.get(key)===tail)tails.delete(key);});}};}
function projection(){return {schema:'report-business-catalogue-projection-v211',source_snapshot_hash:'a'.repeat(64),projection_hash:'c'.repeat(64),input_count:2,machine_only:true,human_confirmed:false,can_confirm:false,dispatch_allowed:false,...flags,
 wire:{schema:'report-business-catalogue-output-v211',input_count:2,businesses:[{key:'review',name:'公开合成记录核对'}],allocation_groups:[{input_positions:[0,1]}],questions:[],merge_suggestions:[]},assignments:[{input_position:0,machine_only:true},{input_position:1,machine_only:true}]};}
function fixture(){
 const account={user_id:id(1),workspace_id:id(2),role:'operator'},producer={id:randomUUID(),revision:3,status:'failed'},model={id:id(4),name:'已登记目录模型',generation_profile:'catalogue-v211',provider:'qwen',model:'qwen3.8-max-0902'},storage=new Storage(),locks=locking();
 const state={account,producer,model,storage,locks,me:{...account},revision:3,models:[model],calls:[],rows:new Map(),receipts:new Map(),starts:0,recoveries:0,failLookups:false,failLookupsAfterCommit:false,loseAfterCommit:false,unknownBeforeCommit:false,afterList:null,writeAvailable:true,mutatePreview:null,captureFailureFor:null,partialView:null,listFailure:null};
 // The other registered family's result remains deliberately opaque. Its
 // public list metadata is real-shape, not an incomplete placeholder row.
 state.oldRow={schema:'report-saved-map-design-view-v176',id:id(90),version:2,revision:3,
  producer_id:producer.id,model_id:id(91),job_id:id(92),reserved_call_id:null,status:'failed',
  generation_profile:'indexed-v179',activity_id:id(93),source_snapshot_hash:'a'.repeat(64),preview_hash:'b'.repeat(64),
  note:'旧族记录只保留，不按目录族解释',error:'保留的旧族失败记录',result:{privateOld:'untouched'},manual_value:null,
  projection:null,call_count_limit:1,budget:{schema:'report-saved-map-design-budget-v176',call_limit:1,
   token_bound:45000,maximum_cost_usd:3,pricing_basis_hash:'e'.repeat(64),grant_hash:'f'.repeat(64)},...flags};
 const claims=Array.from({length:2},(_,i)=>({input_position:i,claim:{kind:'业务',statement:'完整公开合成要求 '+i,span_refs:['w:p'+(i+1)+'/s1'],status:'explicit',explanation:''}}));
 const quote=body=>{const hashes=Object.fromEntries(['source_hash','catalog_hash','map_preparation_hash','map_plan_hash','map_inputs_hash','input_order_hash','input_items_hash','input_claims_hash'].map(key=>[key,'d'.repeat(64)]));const value={schema:'report-saved-map-design-preview-v176',...copy(body),...flags,model:model.model,provider:'qwen',source_snapshot_hash:'a'.repeat(64),preview_hash:'b'.repeat(64),materials:[{id:id(3),name:'公开合成报告',version:1}],map_count:1,claim_count:2,
  sent_payload:{schema:'report-business-catalogue-payload-v211',source_snapshot_hash:'a'.repeat(64),...hashes,context:{说明:'仅公开合成资料，不证明实际领域质量'},user_instruction:'完整保留原要求。',catalogue_note:body.note,indexed_inputs:{schema:'report-indexed-model-inputs-v178',source_snapshot_hash:'a'.repeat(64),input_count:2,input_claims:copy(claims)},spans:[['w:p1/s1','合成原文甲'],['w:p2/s1','合成原文乙']]},
  input_summary:{prepared_request_bytes:10000,token_bound:45000,max_input_bytes:900000},budget:{schema:'report-saved-map-design-budget-v176',call_limit:1,token_bound:45000,maximum_cost_usd:3,pricing_basis_hash:'e'.repeat(64),grant_hash:'f'.repeat(64)},call_count:1,requires_separate_authorization:true};state.mutatePreview?.(value);return value;};
 const request=async(path,options={})=>{
  state.calls.push({path,method:options.method||'GET',raw:options.body});
  if(path==='/me')return copy(state.me);
  if(path===BASE+'?producer_id='+producer.id){if(state.listFailure==='nonJSON')throw new SyntaxError('synthetic list response is not JSON');if(state.listFailure==='500'){const error=Error('synthetic list HTTP 500');error.status=500;throw error;}state.afterList?.();return {schema:'report-saved-map-design-list-v178',producer_id:producer.id,items:[copy(state.oldRow),...copy([...state.rows.values()])]};}
  if(path===BASE+'/preview-indexed'){const body=JSON.parse(options.body);assert.equal(body.generation_profile,'catalogue-v211');return quote(body);}
  if(path.startsWith(BASE+'/requests/')){if(state.failLookups)throw Error('synthetic original receipt unavailable');const nonce=path.split('/').at(-1).split('?')[0],result=state.receipts.get(nonce);return {schema:'report-saved-map-design-request-v176',found:!!result,request_id:nonce,wire_sha256:new URL('https://fixture.invalid'+path).searchParams.get('expected_sha256'),result:copy(result||null)};}
  if(path.endsWith('/catalogue-partial')){assert.ok(state.partialView);return copy(state.partialView);}
  if(path.endsWith('/capture-summary')){const consumerId=path.split('/').at(-2);if(state.captureFailureFor===consumerId)throw Error('synthetic selected capture unavailable');return {schema:'report-saved-map-design-capture-summary-v178',available:false,status:'missing',finish_reason:null,http_status:null,response_complete:false,response_bytes:null,arguments_bytes:null,usage:null,problem:null,consumer_id:consumerId};}
  if(options.method==='POST'){
   const body=JSON.parse(options.body),operation=path.endsWith('/start-indexed')?'start':'recover-saved';
   const pointerKey=[...storage.rows.keys()].find(key=>key.endsWith(':pending')),pointer=JSON.parse(storage.getItem(pointerKey));
   const originalKey=pointerKey.slice(0,-':pending'.length)+':original:'+pointer.request_id,original=JSON.parse(storage.getItem(originalKey));
   assert.equal(original.raw,options.body);assert.equal(original.sha256,createHash('sha256').update(options.body).digest('hex'));
   if(state.unknownBeforeCommit)throw Error('synthetic unknown transport before commit');
   let row;
   if(operation==='start'){state.starts++;assert.equal(body.request_id,body.activity_id,'the exact preview activity UUID is the original start nonce');row={schema:'report-saved-map-design-view-v176',id:randomUUID(),version:1,revision:body.revision,status:'queued',producer_id:producer.id,model_id:body.model_id,job_id:randomUUID(),reserved_call_id:null,source_snapshot_hash:body.source_snapshot_hash,preview_hash:body.preview_hash,note:body.note,error:null,result:null,manual_value:null,projection:null,catalogue:null,call_count_limit:1,generation_profile:'catalogue-v211',activity_id:body.activity_id,...flags};state.rows.set(row.id,row);}
   else{state.recoveries++;row=state.rows.get(path.split('/').at(-2));assert.equal(row.version,body.expected_version);row.version++;row.status='needs_clarification';row.catalogue=projection();row.projection=copy(row.catalogue);row.reserved_call_id=id(7);}
   const result={schema:'report-saved-map-design-result-v176',id:row.id,version:row.version,operation,status:row.status,job_id:row.job_id};state.receipts.set(body.request_id,result);if(state.failLookupsAfterCommit)state.failLookups=true;if(state.loseAfterCommit)throw Error('synthetic committed response lost');return copy(result);
  }
  const row=state.rows.get(path.split('/').at(-1));assert.ok(row,'fixture only serves a fixed saved consumer');return copy(row);
 };
 const client=()=>new CatalogueGenerationClientV211({account,producer,models:()=>state.models,request,storage,locks,crypto:webcrypto,prepareDraft:async()=>state.revision,currentAvailable:()=>state.writeAvailable});
 state.client=client;state.quote=quote;return state;
}
async function prepared(f){const c=f.client();await c.open();c.saveForm({model_id:f.model.id,note:'完整业务目录要求😀\n不自动确认。'});assert.equal(await c.prepare(),true,c.error);return c;}

test('preview is catalogue-only, includes every source item, and never starts before explicit one-call consent',async()=>{
 const f=fixture(),c=await prepared(f),activity=c.preview.body.activity_id;
 assert.equal(f.starts,0);assert.equal(c.preview.view.sent_payload.indexed_inputs.input_count,2);assert.equal(c.preview.view.sent_payload.spans.length,2);assert.equal(Object.hasOwn(c.preview.view.sent_payload,'catalog'),false);
 assert.equal(await c.start(),false);assert.equal(f.starts,0);c.acknowledge(true);assert.equal(await c.start(),true,c.error);
 const posted=f.calls.find(call=>call.path.endsWith('/start-indexed')),body=JSON.parse(posted.raw);
 assert.equal(body.request_id,activity);assert.equal(body.activity_id,activity);assert.equal(body.consent,true);assert.equal(f.starts,1);assert.equal(await c.pending(),null);assert.equal(c.guard(),true);assert.equal(c.selected.status,'queued');
});

test('new discovery filters old families without decoding or mutating them and read-only polling never resends',async()=>{
 const f=fixture(),originalOld=copy(f.oldRow),c=await prepared(f);c.acknowledge(true);await c.start();const starts=f.starts;
 await c.act(()=>c.refresh());await c.act(()=>c.refresh());assert.equal(f.starts,starts);assert.equal(c.items.length,1);assert.equal(c.items[0].generation_profile,'catalogue-v211');
 assert.deepEqual(catalogueGenerationModelsV211([{...f.model,generation_profile:'indexed-v179'},f.model,{...f.model,provider:'deepseek'}]),[f.model]);
 assert.equal(await c.prepare(),false,'an existing in-flight catalogue must be read before any new activity');assert.equal(f.starts,starts);
 assert.deepEqual(f.oldRow,originalOld);assert.deepEqual(f.oldRow.result,{privateOld:'untouched'});
});

for(const field of ['schema','producer_id','status'])test('incomplete other-family public '+field+' cannot grant a new paid activity',async()=>{
 const f=fixture();delete f.oldRow[field];const c=f.client();await c.open();assert.equal(c.view().activitiesLoaded,false);assert.equal(c.guard({write:true}),false);
 assert.equal(await c.prepare(),false);assert.equal(f.starts,0);assert.equal(f.calls.some(call=>call.path.endsWith('/preview-indexed')),false);assert.deepEqual(f.oldRow.result,{privateOld:'untouched'});
});

test('unknown other-family status is never treated as a completed or empty inventory',async()=>{
 const f=fixture();f.oldRow.status='cancelled';const c=f.client();await c.open();assert.equal(c.view().activitiesLoaded,false);assert.equal(c.guard({write:true}),false);
 assert.equal(await c.prepare(),false);assert.equal(f.starts,0);assert.equal(f.calls.some(call=>call.path.endsWith('/preview-indexed')),false);assert.deepEqual(f.oldRow.result,{privateOld:'untouched'});
});

test('lost committed response cold recovery reads the exact original nonce and never sends another start',async()=>{
 const f=fixture(),c=await prepared(f);f.loseAfterCommit=true;f.failLookupsAfterCommit=true;c.acknowledge(true);assert.equal(await c.start(),false);assert.equal(f.starts,1);
 const old=copy(c.pendingRecord),stored=f.storage.getItem(c.key+':original:'+old.request_id);c.dispose();f.failLookups=false;f.writeAvailable=false;
 const cold=f.client();await cold.open();assert.equal(cold.pendingRecord.raw,old.raw);assert.equal(await cold.recoverOriginal(),true,cold.error);assert.equal(f.starts,1);assert.equal(await cold.pending(),null);assert.equal(f.storage.getItem(cold.key+':original:'+old.request_id),stored);assert.ok(cold.selected);
});

test('quota failure preserves orphan original and protects the same bytes before a deliberate retry',async()=>{
 const f=fixture(),c=await prepared(f);f.storage.failPending=true;c.acknowledge(true);assert.equal(await c.start(),false);assert.equal(f.starts,0);const original=copy(c.pendingRecord);assert.ok(original);assert.equal(c.guard(),false);c.dispose();
 const cold=f.client();await cold.open();assert.equal(cold.pendingRecord.request_id,original.request_id);assert.equal(cold.pendingDurable,false);assert.equal(cold.localSaved,false);f.storage.failPending=false;
 assert.equal(await cold.protectOriginal(),true,cold.error);assert.equal(await cold.retryOriginal(),true,cold.error);assert.equal(f.starts,1);assert.equal(f.calls.find(call=>call.path.endsWith('/start-indexed')).raw,original.raw);
});

test('unknown attempted original cannot be archived or replaced and failed activities never become editable fake results',async()=>{
 const f=fixture(),c=await prepared(f);f.unknownBeforeCommit=true;c.acknowledge(true);assert.equal(await c.start(),false);const old=copy(c.pendingRecord);
 assert.equal(await c.retireOriginal(),false);assert.equal(c.pendingRecord.request_id,old.request_id);assert.equal(await c.prepare(),false);assert.equal(f.starts,0);
 const malformed={schema:'report-saved-map-design-view-v176',id:id(6),version:1,revision:3,status:'failed',producer_id:f.producer.id,model_id:f.model.id,job_id:id(8),reserved_call_id:null,source_snapshot_hash:'a'.repeat(64),preview_hash:'b'.repeat(64),note:'',error:null,result:{schema:'report-object-semantic-candidate-wire-v166'},manual_value:null,catalogue:null,call_count_limit:1,generation_profile:'catalogue-v211',activity_id:id(9),...flags};
 assert.throws(()=>catalogueGenerationViewV211(malformed,c.scope));assert.throws(()=>catalogueGenerationViewV211({...malformed,result:null,status:'review'},c.scope));
});

test('same-call saved-answer recovery hands only a real catalogue view forward without a new start',async()=>{
 const f=fixture(),c=await prepared(f);c.acknowledge(true);await c.start();const row=f.rows.get(c.selected.id);row.status='response_unknown';await c.act(()=>c.refresh());
 assert.equal(await c.recoverSaved(),true,c.error);assert.equal(f.starts,1);assert.equal(f.recoveries,1);const value=await c.consumerForEditing();assert.equal(value.manual_value,null);assert.equal(value.result,null);assert.equal(value.catalogue.schema,'report-business-catalogue-projection-v211');
});

test('scope changes after a response latch denial and hide private form, while saved local labour survives',async()=>{
 const f=fixture(),c=await prepared(f),raw=f.storage.getItem(c.key+':form-current');f.afterList=()=>{f.me={...f.me,user_id:id(99)};};
 assert.equal(await c.act(()=>c.refresh()),false);assert.equal(c.denied,true);assert.equal(c.selected,null);assert.deepEqual(c.items,[]);assert.equal(c.form.note,'');assert.equal(f.storage.getItem(c.key+':form-current'),raw);assert.equal(c.guard(),false);
});

test('source and quote weakening cannot enable cloud dispatch or disclosure of the server-only catalogue',async()=>{
 for(const fault of ['catalog','last_claim','wrong_count','budget','scope','missing_span','kind_object']){
  const f=fixture();f.mutatePreview=value=>{if(fault==='catalog')value.sent_payload.catalog={privateServerOnly:true};else if(fault==='last_claim')value.sent_payload.indexed_inputs.input_claims.pop();else if(fault==='wrong_count')value.claim_count=3;else if(fault==='budget')value.budget.maximum_cost_usd=4;else if(fault==='missing_span')value.sent_payload.spans.pop();else if(fault==='kind_object')value.sent_payload.indexed_inputs.input_claims[0].claim.kind={unexpected:'object'};else value.generation_profile='indexed-v179';};
  const c=f.client();await c.open();c.saveForm({model_id:f.model.id,note:'保留原输入'});assert.equal(await c.prepare(),false,fault);assert.equal(f.starts,0);assert.equal(c.preview,null);assert.equal(c.form.note,'保留原输入');
 }
});

test('original pointer tampering and another producer scope cannot manufacture an empty pending inventory',async()=>{
 const f=fixture(),c=await prepared(f);f.unknownBeforeCommit=true;c.acknowledge(true);await c.start();const pending=copy(c.pendingRecord),key=c.key+':pending';
 f.storage.setItem(key,JSON.stringify({request_id:pending.request_id,sha256:'f'.repeat(64)}));await assert.rejects(c.pending());assert.equal(f.starts,0);
 const other=f.client();other.scope={...other.scope,producer_id:randomUUID()};other.key='separate-public-synthetic-scope';f.storage.setItem(other.key+':original:'+pending.request_id,JSON.stringify(pending));await assert.rejects(other.pending());
});

test('capture read failures never attach another activity summary after select or refresh',async()=>{
 const f=fixture(),c=await prepared(f);c.acknowledge(true);await c.start();const first=c.selected.id;assert.equal(c.capture.consumer_id,first);
 const second={...copy(c.selected),id:randomUUID(),activity_id:randomUUID(),job_id:randomUUID(),note:'另一真实活动的合成投影'};f.rows.set(second.id,second);f.captureFailureFor=second.id;
 assert.equal(await c.select(second.id),false);assert.equal(c.selected.id,second.id);assert.equal(c.capture,null);assert.notEqual(first,c.selected.id);
 f.captureFailureFor=null;assert.equal(await c.act(()=>c.refresh()),true);assert.equal(c.capture.consumer_id,second.id);f.captureFailureFor=second.id;second.version++;
 assert.equal(await c.act(()=>c.refresh()),false);assert.equal(c.selected.version,second.version);assert.equal(c.capture,null);
});

test('failed restore at backup or target pointer keeps both human drafts and never declares the current input protected',async()=>{
 for(const offset of [1,2]){
  const f=fixture(),old=f.client();await old.open();old.saveForm({model_id:f.model.id,note:'要恢复的完整旧要求😀'});old.dispose();
  const c=f.client();await c.open();c.saveForm({model_id:f.model.id,note:'恢复前后来工作，必须保留。'});c.loadForms();const target=c.retainedForms.find(row=>row.value.note==='要恢复的完整旧要求😀');assert.ok(target);
  f.storage.failFormCurrentAt=f.storage.formCurrentWrites+offset;assert.equal(await c.restoreForm(target),false);assert.equal(c.localSaved,false);assert.equal(c.guard(),false);const expected=offset===1?'恢复前后来工作，必须保留。':'要恢复的完整旧要求😀';assert.equal(c.form.note,expected);c.dispose();
  const cold=f.client();await cold.open();assert.equal(cold.form.note,expected);assert.equal(cold.localSaved,false);cold.saveForm({...cold.form});assert.equal(cold.localSaved,true);cold.saveForm({...cold.form,note:'之后明确保存的最新要求。'});cold.dispose();const latest=f.client();await latest.open();assert.equal(latest.form.note,'之后明确保存的最新要求。');assert.equal(latest.localSaved,true);
  const retained=[...f.storage.rows.values()].join('\n');assert.ok(retained.includes('恢复前后来工作'));assert.ok(retained.includes('要恢复的完整旧要求😀'));assert.equal(f.starts,0);
 }
});

test('form and outbox protection cannot make each other falsely healthy',async()=>{
 const f=fixture(),c=await prepared(f),other=f.client();await other.open();f.storage.failPending=true;c.acknowledge(true);assert.equal(await c.start(),false);const original=copy(c.pendingRecord);
 f.storage.failForm=true;other.saveForm({model_id:f.model.id,note:'另一窗口尚未保护的后来人工要求。'});assert.equal(other.localSaved,false);await other.pending();f.storage.failPending=false;
 assert.equal(await other.protectOriginal(),true);assert.equal(other.pendingDurable,true);assert.equal(other.localSaved,false,'protecting the original outbox does not protect unsaved human input');assert.equal(await other.retryOriginal(),false);assert.equal(f.starts,0);f.storage.failForm=false;other.persistForm();assert.equal(other.localSaved,true);assert.equal(other.guard(),false,'the original pending command remains an independent boundary');assert.equal(other.pendingRecord.raw,original.raw);assert.equal(f.starts,0);
});

test('viewer can open the exact complete catalogue read-only while new cloud writes stay blocked',async()=>{
 const f=fixture(),c=await prepared(f);c.acknowledge(true);await c.start();const row=f.rows.get(c.selected.id);row.status='needs_clarification';row.catalogue=projection();c.dispose();f.account.role='viewer';f.me.role='viewer';f.writeAvailable=false;f.producer.revision=79;
 const viewer=f.client();await viewer.open();const result=await viewer.consumerForEditing();assert.equal(result.revision,3);assert.equal(viewer.scope.revision,79);assert.equal(result.catalogue.projection_hash,row.catalogue.projection_hash);assert.equal(await viewer.prepare(),false);assert.equal(f.starts,1);assert.throws(()=>catalogueGenerationViewV211({...result,revision:undefined},viewer.scope));
});

function failedPartial(row){const value=projection(),raw=copy(value.wire);raw.allocation_groups=[{input_positions:[0,0]}];const p={...value,schema:'report-business-catalogue-partial-projection-v212',raw_wire:raw,draft_seed:{schema:'report-business-catalogue-edit-document-v211',businesses:[],allocation_groups:[],questions:[]},raw_arguments_sha256:'d'.repeat(64),indexed_inputs_hash:'e'.repeat(64),input_claims_hash:'f'.repeat(64),catalog_hash:'b'.repeat(64),draft_seed_hash:'c'.repeat(64),inventory_complete:true,model_answer_valid:false,original_call_success:false,stats:{input_count:2},issues:[],assignments:Array.from({length:2},(_,i)=>({input_position:i,input_id:'position_'+String(i).padStart(4,'0'),input_id_origin:'platform_position',machine_only:true,assignment_status:i===0?'conflicting':'unassigned',proposed_memberships:[],claim:{statement:'合成原要求'+i},source_spans:[]}))};delete p.wire;return {...copy(row),schema:'report-business-catalogue-partial-view-v212',origin_mode:'failed_catalogue_partial_v212',call_id:row.reserved_call_id,call_status:'failed',catalogue_partial:p,model_answer_valid:false,allocation_complete:false,dispatch_allowed:false,human_confirmed:false};}

test('failed partial is an explicit read-only endpoint with all original positions, never a complete success or fresh model call',async()=>{
 const f=fixture(),c=await prepared(f);c.acknowledge(true);await c.start();const row=f.rows.get(c.selected.id);row.status='failed';row.reserved_call_id=id(71);f.partialView=failedPartial(row);c.dispose();f.account.role='viewer';f.me.role='viewer';f.writeAvailable=false;const viewer=f.client();await viewer.open();assert.equal(f.calls.some(call=>call.path.endsWith('/catalogue-partial')),false);
 const partial=await viewer.partialForEditing();assert.equal(partial.catalogue,null);assert.equal(partial.result,null);assert.equal(partial.manual_value,null);assert.equal(partial.status,'failed');assert.equal(partial.call_status,'failed');assert.equal(partial.catalogue_partial.assignments.length,2);assert.equal(Object.hasOwn(partial.catalogue_partial,'wire'),false);assert.equal(f.starts,1);await assert.rejects(viewer.consumerForEditing());
 for(const fault of ['old_wire_alias','missing_tail','wrong_revision','success']){const bad=copy(partial);if(fault==='old_wire_alias')bad.catalogue_partial.wire=copy(bad.catalogue_partial.raw_wire);else if(fault==='missing_tail')bad.catalogue_partial.assignments.pop();else if(fault==='wrong_revision')bad.revision=79;else bad.call_status='succeeded';assert.throws(()=>catalogueGenerationPartialV212(bad,row,viewer.scope),fault);}
});

test('inventory initial non-JSON or HTTP 500 is unknown rather than a successfully empty list',async()=>{
 for(const failure of ['nonJSON','500']){const f=fixture();f.listFailure=failure;const c=f.client();assert.equal(c.view().activitiesLoaded,false);await c.open();assert.equal(c.view().loading,false);assert.equal(c.view().activitiesLoaded,false);assert.deepEqual(c.view().items,[]);assert.ok(c.view().error);assert.equal(f.starts,0);assert.equal(f.calls.some(call=>call.method==='POST'),false);}
});

test('inventory becomes genuinely loaded-empty only after a successful complete list response',async()=>{
 const f=fixture();f.listFailure='500';const c=f.client();await c.open();assert.equal(c.view().activitiesLoaded,false);f.listFailure=null;assert.equal(await c.act(()=>c.refresh()),true,c.error);assert.equal(c.view().activitiesLoaded,true);assert.deepEqual(c.view().items,[]);assert.equal(c.view().error,'');assert.equal(f.starts,0);assert.equal(f.calls.some(call=>call.method==='POST'),false);
});

test('inventory refresh failure retains the last validated list instead of clearing it or announcing empty',async()=>{
 const f=fixture(),c=await prepared(f);c.acknowledge(true);assert.equal(await c.start(),true,c.error);const before=copy(c.view().items);assert.equal(c.view().activitiesLoaded,true);assert.equal(before.length,1);f.listFailure='500';assert.equal(await c.act(()=>c.refresh()),false);assert.equal(c.view().activitiesLoaded,true);assert.deepEqual(c.view().items,before);assert.equal(c.view().selected.id,before[0].id);assert.ok(c.view().error.includes('500'));assert.equal(f.starts,1);
});
