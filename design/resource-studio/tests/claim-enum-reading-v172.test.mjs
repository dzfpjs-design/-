// Public synthetic protocol and actual component seams. These constructed
// envelopes are not provider, authenticated HTTP, PostgreSQL or quality proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createHash} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {build} from 'vite';
import specs from '../src/report-claim-enum-specs-v172.mjs';
import {copy,digest,evidenceCatalog,evidenceCommandHash,evidenceReviewValue,verifyEvidencePreview,verifyEvidenceDraft,verifyEvidenceReductionPreview,verifyEvidenceReply} from '../src/report-evidence-reading-contract.mjs';
import {CLAIM_ENUM_DRAFT,claimEnumPreview,claimEnumStageSpec,claimArrayPolicy,claimArrayRequest,claimArrayDispatch,claimArrayInputs,checkClaimArrayGeneration,checkClaimArrayPreparation,verifyClaimArrayMapProjection,verifyClaimArrayReduced} from '../src/claim-enum-reading-v172.mjs';
import {claimArrayPreview,legacyEvidenceReviewAllowed,claimArrayStageSpec} from '../src/claim-reading-family.mjs';
import {verifyNativeCapture,readNativeCapture} from '../src/report-evidence-native-capture.mjs';
import {capturedV7,memory,tabFor,missing,reseal,acceptedEntry} from './report-evidence-v7-fixture.mjs';

const DELIVERY='qwen-public-claim-enum-native-v172',GENERATION='report-claim-enum-map-generation-v172';
const hash=raw=>createHash('sha256').update(raw).digest('hex');
const priorTab=globalThis.sessionStorage;globalThis.sessionStorage??=tabFor();
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
let temporary,Editor,Preview,ReducePreview,ReportEvidenceReadingSession;
test.before(async()=>{
 ({ReportEvidenceReadingSession}=await import('../src/report-evidence-reading-session.mjs'));
 const project=fileURLToPath(new URL('..',import.meta.url));temporary=await mkdtemp(join(tmpdir(),'claim-enum-v172-'));
 const output=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),logLevel:'silent',build:{ssr:join(project,'src/ReportEvidenceReadingDialog.jsx'),write:false,minify:false,rollupOptions:{external:['react'],output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
 const path=join(temporary,'dialog.mjs');await writeFile(path,output.output.find(item=>item.isEntry).code);const module=await import(pathToFileURL(path).href);Editor=module.EvidenceCandidateEditor;Preview=module.EvidenceCallPreview;ReducePreview=module.EvidenceReductionPreview;
});
test.after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});if(priorTab===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=priorTab;});

async function sealPlan(m){await reseal(m.plan,'plan_hash');await reseal(m,'preparation_hash');}
async function scene(){
 const f=await capturedV7(),p=copy(f.preview),row=copy(f.start.draft),account=f.account;
 p.preparation_cap={execution:{provider:'qwen',model:'qwen3.8-max-0902',max_tokens:8192,report_evidence_output:{schema:'report-evidence-output-v172',max_tokens:32768,map_transport:DELIVERY,reduce_transport:DELIVERY,endpoint:'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'}},data_policy:{redact_fields:[]}};p.provider='qwen';p.model='qwen3.8-max-0902';
 const catalog=await evidenceCatalog(p),m=copy(p.map_preparation);m.schema='report-claim-enum-map-preparation-v172';m.generation_contract=copy(specs.map);m.plan.schema='report-evidence-read-plan-v2';await sealPlan(m);
 let total=0,tokens=0;
 for(const c of m.plan.chunks){
  const raw={schema:'report-evidence-read-payload-v1',source_hash:catalog.descriptor.source_hash,catalog_hash:catalog.descriptor.catalog_hash,spans:c.span_refs.map(ref=>[ref,catalog.index[ref].text])};
  const payload={schema:'report-claim-array-input-v167',generation:GENERATION,task_id:'report-claim-enum-map-task-v172',source_hash:raw.source_hash,catalog_hash:raw.catalog_hash,spans:c.span_refs.map(ref=>({span_ref:ref,text:catalog.index[ref].text})),context:copy(m.context),user_instruction:m.user_instruction};
  const spec=await claimEnumStageSpec(m,c.chunk_id),actual=claimArrayRequest(spec,payload);
  Object.assign(c,{payload_hash:await digest(raw),messages_hash:await digest(actual.messages),request_hash:await digest(actual.request),input_summary:actual.input_summary});await reseal(c,'chunk_hash');await sealPlan(m);total+=actual.input_summary.prepared_request_bytes;tokens+=actual.input_summary.token_bound;
 }
 m.input_summary={map_call_count:m.plan.chunks.length,map_token_bound:tokens,prepared_request_bytes:total,maximum_total_calls:m.plan.chunks.length+1,full_report_confirmed:false};await sealPlan(m);p.map_preparation=m;p.input_summary=copy(m.input_summary);await reseal(p,'preview_hash');
 Object.assign(row,{schema:CLAIM_ENUM_DRAFT,object_review_family:'report-object-semantic-review-v166',preview:p,review_value:null,review_history:[]});row.start_receipt.preview_hash=p.preview_hash;row.start_receipt.command_hash=await evidenceCommandHash(account,'extract',null,{...p.body,preview_hash:p.preview_hash,consent:true});await reseal(row.start_receipt,'receipt_hash');await reseal(row,'view_hash');
 return {f,p,row,account,catalog};
}

async function response(s,old,authorized,spec,wire,stageName,reduce=false){
 const raw=JSON.stringify(wire),body=JSON.stringify({choices:[{finish_reason:'stop',message:{role:'assistant',content:raw}}],usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}});
 const receipt={...copy(old.receipt),delivery:DELIVERY,provider:'qwen',model:'qwen3.8-max-0902',evidence_profile:claimArrayPolicy(s.p.preparation_cap,reduce),response_complete:true,http_content_encoding:null,capture_hash:hash('public synthetic enum capture '+stageName),http_status:200,finish_reason:'stop',response_choice_count:1,tool_call_count:0,tool_call_id:null,tool_name:null,tool_type:null,tool_calls_hash:null,arguments_sha256:hash(raw),arguments_bytes:Buffer.byteLength(raw),arguments_encoding:'utf-8',response_body_sha256:hash(body),response_body_bytes:Buffer.byteLength(body),response_body_encoding:'utf-8-surrogateescape-v1',envelope_valid:true,envelope_errors:[],usage_available:true,usage_present:true,usage_unavailable:false,usage_unavailable_reason:null,original_usage_shape:'object',usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}};
 const version=reduce?'2':'1',binding={schema:'report-evidence-journal-binding-v'+version,actor_id:s.row.actor_id,workspace_id:s.row.workspace_id,draft_id:s.row.id,stage:stageName,reserved_call_id:old.reserved_call_id,source_hash:s.p.source_hash,catalog_hash:s.catalog.descriptor.catalog_hash,generation_hash:await digest({published_model_hash:s.p.published_model_hash,contract:spec}),dispatch_hash:await digest(claimArrayDispatch(s.p,authorized)),messages_hash:authorized.messages_hash};
 const journal={...copy(old.journal),schema:'report-evidence-journal-descriptor-v'+version,binding,binding_hash:await digest(binding),wire_bytes:Buffer.byteLength(body),wire_sha256:hash(body),receipt_status:'saved',receipt_bytes:Buffer.byteLength(JSON.stringify(receipt)),receipt_sha256:hash(JSON.stringify(receipt)),consumable:true,quarantine_reasons:[]};await reseal(journal,'descriptor_hash');
 const metadata=Object.fromEntries(specs.metadata_keys.map(key=>[key,receipt[key]]));
 const common={delivery:DELIVERY,binding,binding_hash:journal.binding_hash,journal_descriptor_hash:journal.descriptor_hash,journal_wire_sha256:journal.wire_sha256,http_body_sha256:receipt.response_body_sha256,http_body_bytes:receipt.response_body_bytes,http_body_encoding:receipt.response_body_encoding,arguments_sha256:receipt.arguments_sha256,arguments_bytes:receipt.arguments_bytes,tool_metadata:metadata,tool_metadata_hash:await digest(metadata),evidence_profile:receipt.evidence_profile,evidence_profile_hash:await digest(receipt.evidence_profile),map_preparation_hash:s.p.map_preparation.preparation_hash,map_plan_hash:s.p.map_preparation.plan.plan_hash};
 return {raw,receipt,journal,common};
}

async function mappedScene(){
 const s=await scene();
 for(const [i,c]of s.p.map_preparation.plan.chunks.entries()){
  const old=copy(s.f.mapped.maps[i]),wire={schema:'report-evidence-map-v3',claims:[{kind:'职责',statement:'公开合成核查人核对本组记录。',span_refs:[c.span_refs[0]],status:'explicit',explanation:''}]},spec=await claimEnumStageSpec(s.p.map_preparation,c.chunk_id),r=await response(s,old,c,spec,wire,c.chunk_id);
  const projection={schema:'report-claim-enum-map-projection-v172',...r.common,response_complete:true,http_content_encoding:null,stage_spec_hash:await digest(spec),output_schema_hash:await digest(spec.output_schema),schema_proof_hash:await digest(spec.schema_proof),published_model_hash:s.p.published_model_hash,allowed_span_refs_hash:await digest(c.span_refs),wire,wire_value_hash:await digest(wire),claims:copy(wire.claims)};await reseal(projection,'projection_hash');
  Object.assign(s.row.maps[i],old,{status:'succeeded',receipt:r.receipt,journal:r.journal,raw_response_saved:true,result:projection,result_hash:await digest(projection),result_count:wire.claims.length});
 }
 s.row.status='awaiting_merge';s.row.phase='awaiting_reduce';s.row.revision=s.f.mapped.revision;await reseal(s.row,'view_hash');return s;
}

function candidate(s){
 const refs=s.row.maps.map(stage=>stage.result.claims[0].span_refs[0]),citation={span_ref:refs[0]},evidence={status:'explicit',citations:[citation],explanation:''};
 return {schema:'report-object-semantic-candidate-wire-v166',source_hash:s.catalog.descriptor.source_hash,catalog_hash:s.catalog.descriptor.catalog_hash,domain:'公开合成核查领域',global_claims:s.row.maps.flatMap(stage=>copy(stage.result.claims)),objects:[{name:'核查记录',fields:[{label:'高度',kind:'数值',required:false,unit:'米',citations:[citation],inference_reason:''}],citations:[citation],inference_reason:'',business_identity:{status:'not_established',field_labels:[],citations:[],explanation:'合成材料未明确业务标识，继续保留待核。'}}],relationships:[],businesses:[{operation:'upsert',original_name:'',name:'核查业务',trigger:'收到合成记录',output:'核查意见',citations:[citation],inference_reason:'',steps:[{name:'核对',purpose:'核对记录',agent:'人工确认',role:'核查人',output:'意见',next_index:1,branch_condition:'',else_index:null,citations:[citation],inference_reason:''},{name:'保存意见',purpose:'保存核对后的意见',agent:'人工确认',role:'核查人',output:'已保存意见',next_index:null,branch_condition:'',else_index:null,citations:[citation],inference_reason:''}],participants:[{agent:'登记人',responsibility:'登记',evidence},{agent:'核查人',responsibility:'核查',evidence}],interactions:[{from_participant_index:0,to_participant_index:1,purpose:'交接记录',condition:'记录已保存',step_indices:[0],evidence}],conditions:[{kind:'precondition',statement:'已保存记录',step_indices:[0],evidence}],coordination:[{kind:'sequence',step_indices:[0,1],start_rule:'开始核查',wait_rule:'等待人工',join_rule:'意见已保存',evidence}]}],questions:['业务标识尚未明确；技术身份不代表业务编号。']};
}

async function reduceScene(){
 const s=await mappedScene(),m=s.p.map_preparation,inputs=await claimArrayInputs(s.row,s.catalog),preview=copy(s.f['reduce-preview']);
 const hashes={map_preparation_hash:m.preparation_hash,map_plan_hash:m.plan.plan_hash,map_inputs_hash:await digest(inputs.proofs),input_order_hash:await digest(inputs.order),input_items_hash:await digest(inputs.items),input_claims_hash:await digest(inputs.claims)};
 const policy={single_batch:true,...claimArrayPolicy(s.p.preparation_cap,true)},options={...copy(specs.reduce.dispatch_options),max_tokens:policy.max_output_tokens};
 const generation={...copy(specs.reduce),model_hash:await digest(s.p.preparation_cap),map_generation_hash:await digest(m.generation_contract),map_preparation_hash:m.preparation_hash,map_plan_hash:m.plan.plan_hash,map_inputs_hash:hashes.map_inputs_hash,dispatch_options:options,options_hash:await digest(options),input_policy:policy,input_policy_hash:await digest(policy)};
 const payload={schema:'report-claim-enum-reduction-payload-v172',source_hash:s.catalog.descriptor.source_hash,catalog_hash:s.catalog.descriptor.catalog_hash,...hashes,context:copy(m.context),user_instruction:m.user_instruction,input_claims:inputs.claims,spans:inputs.refs.map(ref=>[ref,s.catalog.index[ref].text])},actual=claimArrayRequest(generation,payload);
 const preparation={schema:'report-claim-enum-reduction-preparation-v172',...Object.fromEntries(Object.entries(hashes).filter(([key])=>key!=='input_order_hash')),map_proofs:inputs.proofs,input_order:inputs.order,generation_contract:generation,generation_contract_hash:await digest(generation),payload,payload_hash:await digest(payload),messages_hash:await digest(actual.messages),request_hash:await digest(actual.request),input_summary:actual.input_summary,output_capacity:{schema:'report-evidence-output-capacity-v172',max_output_tokens:policy.max_output_tokens,max_wire_value_bytes:1024*1024,max_response_body_bytes:1024*1024,completion_required:'stop',tool_name:null,input_claim_count:inputs.order.length,required_reconciliation_entries:inputs.order.length,token_estimate:null,fit_guaranteed:false,overflow_behavior:'preserve_response_and_fail'},call_count:1,requires_separate_authorization:true,can_apply:false,can_publish:false};await reseal(preparation,'preparation_hash');
 preview.published_model_hash=s.p.published_model_hash;preview.reduction_preparation=preparation;await reseal(preview,'preview_hash');return {...s,reduce:preview,inputs};
}

async function reducedScene(){
 const s=await reduceScene(),result=candidate(s),reconciliation=s.inputs.order.map((_,i)=>({input_position:i,disposition:'kept',output_kind:'global_claims',output_index:i,reason:'合成声明原样保留，含义仍待核。'})),wire={result,reconciliation},r=await response(s,s.f.reduced.reduction,s.reduce.reduction_preparation,s.reduce.reduction_preparation.generation_contract,wire,'reduce',true);
 const inner={schema:'report-evidence-reduction-projection-v172',wire_bytes_sha256:hash(r.raw),wire_bytes:Buffer.byteLength(r.raw),wire_value_hash:await digest(wire),source_hash:s.catalog.descriptor.source_hash,catalog_hash:s.catalog.descriptor.catalog_hash,allowed_span_refs_hash:await digest(s.inputs.refs),context_hash:await digest(s.p.map_preparation.context),input_order:s.inputs.order,input_items_hash:await digest(s.inputs.items),result_hash:await digest(result),reconciliation_hash:await digest(reconciliation),provider_delivery_verified:false,semantic_quality_verified:false,platform_identity_confirmed:false,can_apply:false,can_publish:false};const fullInner={...inner,...wire};await reseal(fullInner,'projection_hash');inner.projection_hash=fullInner.projection_hash;
 const outer={schema:'report-claim-enum-reduce-wrapper-v172',...r.common,reduction_preparation_hash:s.reduce.reduction_preparation.preparation_hash,map_inputs_hash:s.reduce.reduction_preparation.map_inputs_hash,arguments_projection_hash:inner.projection_hash,reduction_projection_metadata:inner,result,reconciliation};await reseal(outer,'projection_hash');
 const receipt=copy(s.f['reduce-start'].receipt);receipt.preview_hash=s.reduce.preview_hash;receipt.map_inputs_hash=s.reduce.reduction_preparation.map_inputs_hash;receipt.map_plan_hash=s.p.map_preparation.plan.plan_hash;receipt.command_hash=await evidenceCommandHash(s.account,'reduce',s.row.id,{...s.reduce.body,preview_hash:s.reduce.preview_hash,consent:true});await reseal(receipt,'receipt_hash');
 const {result:discardResult,reconciliation:discardReconciliation,...projection_metadata}=outer;
 Object.assign(s.row.reduction,copy(s.f.reduced.reduction),{status:'succeeded',receipt:r.receipt,journal:r.journal,raw_response_saved:true,preview:s.reduce,start_receipt:receipt,reconciliation,projection_metadata,result_hash:await digest(outer),result_count:['global_claims','objects','relationships','businesses','questions'].reduce((n,key)=>n+result[key].length,0)});
 Object.assign(s.row,{status:'review',phase:'review',revision:s.f.reduced.revision,model_result:result,model_result_hash:await digest(result)});await reseal(s.row,'view_hash');return s;
}

async function captureView(s,index=0){
 const stage=s.row.maps[index],chunk=s.p.map_preparation.plan.chunks[index],spec=await claimEnumStageSpec(s.p.map_preparation,chunk.chunk_id),value={schema:'report-evidence-native-capture-view-v1',actor_id:s.account.user_id,workspace_id:s.account.workspace_id,draft_id:s.row.id,draft_revision:s.row.revision,generation_schema:GENERATION,stage:chunk.chunk_id,stage_status:stage.status,reserved_call_id:stage.reserved_call_id,delivery:DELIVERY,request_hash:chunk.request_hash,messages_hash:chunk.messages_hash,source_hash:s.p.source_hash,catalog_hash:s.catalog.descriptor.catalog_hash,profile_hash:await digest(claimArrayPolicy(s.p.preparation_cap)),schema_proof_hash:await digest(spec.schema_proof),token_bound:chunk.input_summary.token_bound,capture_state:'complete',response_complete:true,http_status:200,http_content_encoding:null,content_encoding_safe:true,headers_hash:hash('public synthetic headers'),body_bytes:stage.receipt.response_body_bytes,body_sha256:stage.receipt.response_body_sha256,capture_hash:stage.receipt.capture_hash,recovery_eligible:false,blocker:'stage_already_succeeded',can_apply:false,can_publish:false};await reseal(value,'view_hash');return value;
}

test('per-chunk stage schema and proof equal frozen actual Python specimens; top policy is not a static dispatch',async()=>{
 const before=copy(specs.sample_preparation);assert.equal(Object.hasOwn(specs.map,'output_schema'),false);assert.equal(Object.hasOwn(specs.map,'schema_proof'),false);
 for(const [id,expected]of Object.entries(specs.sample_stage_specs))assert.deepEqual(await claimEnumStageSpec(specs.sample_preparation,id),expected);
 assert.deepEqual(specs.sample_preparation,before);assert.notDeepEqual(specs.sample_stage_specs['map-1'].schema_proof,specs.sample_stage_specs['map-2'].schema_proof);
});

test('new profile previews full selected text and new draft while preserving independent human-review boundary',async()=>{
 const s=await scene(),before=copy(s.row);await verifyEvidencePreview(s.p,s.account);await verifyEvidenceDraft(s.row,s.account);assert.equal(claimEnumPreview(s.p),true);assert.equal(claimArrayPreview(s.p),true);assert.equal(legacyEvidenceReviewAllowed(s.row),false);assert.equal(evidenceReviewValue(s.row),null);assert.deepEqual(s.row,before);
 assert.deepEqual(s.p.source_snapshot,s.f.preview.source_snapshot);assert.equal(s.p.input_summary.maximum_total_calls,s.row.maps.length+1);for(const c of s.p.map_preparation.plan.chunks)assert.notEqual(c.messages_hash,c.request_hash);
});

test('new Maps remain keyless and journal hashes bind distinct actual per-group schema, not the top policy',async()=>{
 const s=await mappedScene();await verifyEvidenceDraft(s.row,s.account);const hashes=[];
 for(const stage of s.row.maps){const spec=await claimArrayStageSpec(s.p,stage.chunk_id);await verifyClaimArrayMapProjection(stage.result,s.row,stage,s.catalog);assert.equal(Object.hasOwn(stage.result.claims[0],'key'),false);assert.deepEqual(spec.allowed_span_refs,s.p.map_preparation.plan.chunks.find(c=>c.chunk_id===stage.chunk_id).span_refs);hashes.push(stage.journal.binding.generation_hash);}
 assert.equal(new Set(hashes).size,s.row.maps.length);
});

for(const [label,change]of [
 ['task',v=>{v.task+=' grant execution';}],['base constraints',v=>{v.base_schema.$defs.MapClaimWireV3.properties.statement.maxLength++;}],['static schema smuggling',v=>{v.output_schema=copy(v.base_schema);}],['old delivery',v=>{v.delivery='qwen-public-claim-native-v168';}],['wrong policy',v=>{v.schema_policy='all-catalog-references';}],['coerced option',v=>{v.base_options.n='1';}],
])test('top policy rejects public self-rehashed mutation: '+label,async()=>{const s=await scene(),spec=copy(s.p.map_preparation.generation_contract);change(spec);await assert.rejects(checkClaimArrayGeneration(spec,s.p.preparation_cap));});

test('full preparation rejects invented refs, omitted source and duplicate refs after all public seals are rebuilt',async()=>{
 for(const mutation of [m=>m.plan.chunks[0].span_refs.push('w:p9999/s1'),m=>m.plan.chunks.pop(),m=>m.plan.chunks[0].span_refs.push(m.plan.chunks[0].span_refs[0])]){const s=await scene(),p=copy(s.p);mutation(p.map_preparation);for(const c of p.map_preparation.plan.chunks)await reseal(c,'chunk_hash');await sealPlan(p.map_preparation);await assert.rejects(checkClaimArrayPreparation(p,s.catalog,p.preparation_cap));}
});

for(const [label,mutate]of [
 ['nonexistent full-catalog child',p=>{p.claims[0].span_refs=['w:p1672/s2'];p.wire.claims=copy(p.claims);}],
 ['valid neighbor-group ref',(p,s)=>{p.claims[0].span_refs=[s.p.map_preparation.plan.chunks[1].span_refs[0]];p.wire.claims=copy(p.claims);}],
 ['old projection',p=>{p.schema='report-claim-array-map-projection-v168';}],['old transport',p=>{p.delivery='qwen-public-claim-native-v168';}],['wrong dynamic stage',p=>{p.stage_spec_hash='f'.repeat(64);}],['wrong actual schema',p=>{p.output_schema_hash='f'.repeat(64);}],['wrong proof',p=>{p.schema_proof_hash='f'.repeat(64);}],['wrong model registration',p=>{p.published_model_hash='f'.repeat(64);}],['invented technical key',p=>{p.claims[0].key='invented';p.wire.claims=copy(p.claims);}],
])test('server-shaped Map still rejects incoherent projection after public rehash: '+label,async()=>{
 const s=await mappedScene(),stage=s.row.maps[0];if(label==='valid neighbor-group ref')assert.ok(s.catalog.index[s.p.map_preparation.plan.chunks[1].span_refs[0]]);if(label==='nonexistent full-catalog child')assert.equal(s.catalog.index['w:p1672/s2'],undefined);
 mutate(stage.result,s);stage.result.wire_value_hash=await digest(stage.result.wire);await reseal(stage.result,'projection_hash');stage.result_hash=await digest(stage.result);await assert.rejects(verifyClaimArrayMapProjection(stage.result,s.row,stage,s.catalog));
});

test('Reduce uses complete verified Map proofs with four new bindings and independently authorized request',async()=>{
 const s=await reduceScene();await verifyEvidenceReductionPreview(s.reduce,s.account,s.row);assert.equal(s.reduce.reduction_preparation.requires_separate_authorization,true);assert.equal(s.row.reduction.preview,null);
 for(const proof of s.reduce.reduction_preparation.map_proofs)for(const key of ['stage_spec_hash','output_schema_hash','schema_proof_hash','published_model_hash'])assert.match(proof[key],/^[a-f0-9]{64}$/);
 const request=claimArrayRequest(s.reduce.reduction_preparation.generation_contract,s.reduce.reduction_preparation.payload);assert.equal(await digest(request.request),s.reduce.reduction_preparation.request_hash);assert.equal(s.reduce.reduction_preparation.call_count,1);
});

for(const [label,mutate]of [
 ['no separate consent',r=>{r.requires_separate_authorization=false;}],['old preparation',r=>{r.schema='report-claim-array-reduction-preparation-v168';}],['old proof inventory',r=>{delete r.map_proofs[0].stage_spec_hash;}],['foreign proof model',r=>{r.map_proofs[0].published_model_hash='f'.repeat(64);}],['unverified output promise',r=>{r.output_capacity.fit_guaranteed=true;}],
])test('Reduce preview rejects altered independent preparation: '+label,async()=>{const s=await reduceScene();mutate(s.reduce.reduction_preparation);await reseal(s.reduce.reduction_preparation,'preparation_hash');await reseal(s.reduce,'preview_hash');await assert.rejects(verifyEvidenceReductionPreview(s.reduce,s.account,s.row));});

test('complete reduced draft keeps V166 objects, full workflow and reconciliation without approving identities or semantic quality',async()=>{
 const s=await reducedScene(),before=copy(s.row);await verifyEvidenceDraft(s.row,s.account);await verifyClaimArrayReduced(s.row,s.catalog);assert.deepEqual(s.row,before);assert.equal(s.row.model_result.schema,'report-object-semantic-candidate-wire-v166');assert.equal(s.row.model_result.objects[0].fields[0].required,false);assert.equal(evidenceReviewValue(s.row),null);
 const metadata=s.row.reduction.projection_metadata.reduction_projection_metadata;for(const key of ['provider_delivery_verified','semantic_quality_verified','platform_identity_confirmed','can_apply','can_publish'])assert.equal(metadata[key],false);
 assert.equal(s.row.model_result.businesses[0].participants.length,2);assert.equal(s.row.model_result.businesses[0].coordination[0].wait_rule,'等待人工');
});

test('capture verification uses exact current group proof; other-group or old-family proof is rejected, reads remain GET-only',async()=>{
 const s=await mappedScene(),view=await captureView(s),calls=[];assert.equal(await verifyNativeCapture(view,s.account,s.row,view.stage),view);
 await readNativeCapture({account:s.account,row:s.row,stage:view.stage,request:async(path,options)=>{calls.push({path,...options});return copy(view);}});assert.deepEqual(calls,[{path:'/setup/report-evidence-readings/'+s.row.id+'/captures/'+view.stage,method:'GET'}]);
 const bad=copy(view);bad.schema_proof_hash=await digest((await claimEnumStageSpec(s.p.map_preparation,s.row.maps[1].chunk_id)).schema_proof);await reseal(bad,'view_hash');await assert.rejects(verifyNativeCapture(bad,s.account,s.row,view.stage));
 bad.schema_proof_hash=await digest(s.p.map_preparation.generation_contract);await reseal(bad,'view_hash');await assert.rejects(verifyNativeCapture(bad,s.account,s.row,view.stage));
});

test('new family never retries a preserved legacy human-save PUT or rewrites its local request',async()=>{
 const s=await scene(),calls=[],storage=memory(),session=new ReportEvidenceReadingSession({account:s.account,materialID:s.p.body.selection.original_ref.id,storage,tabStorage:tabFor(s.account),locks:{request:async(_key,_options,callback)=>callback()},request:async(path,options={})=>{calls.push({path,...options});if(path==='/me')return s.account;throw missing();}});
 try{session.row=copy(s.row);session.state.draft_id=s.row.id;session.state.pending=acceptedEntry(s.f,'review');session.state.value={result:{retained:'uncommitted private input'},note:'不得丢弃'};assert.equal(session.persist(),true);const before=copy(session.state),raw=storage.getItem(session.key);
  await assert.rejects(session.save({probeOnly:true}),error=>error.status===404);await assert.rejects(session.save({retry:true}),/独立对象修订/);assert.ok(calls.some(call=>call.path.includes('/review-receipts/')));assert.equal(calls.some(call=>call.method==='PUT'||call.method==='POST'),false);assert.deepEqual(session.state,before);assert.equal(storage.getItem(session.key),raw);
 }finally{session.dispose();}
});

test('new-family free recovery receipt is verified as native and cannot be settled by legacy receipt shape',async()=>{
 const s=await mappedScene(),row=s.row,body={revision:row.revision,request_id:'ffffffff-ffff-4fff-8fff-ffffffffffff'},entry={schema:'report-evidence-reading-command-v3',kind:'recovery',draft_id:row.id,body,command_hash:await evidenceCommandHash(s.account,'recovery',row.id,body),binding:{material_id:s.p.body.selection.original_ref.id,source_hash:s.p.source_hash,preview_hash:s.p.preview_hash,map_inputs_hash:null,map_plan_hash:s.p.map_preparation.plan.plan_hash,remaining_chunks:[]}};
 const receipt={schema:'report-evidence-recovery-receipt-v2',kind:'report_evidence_recovery',actor_id:s.account.user_id,workspace_id:s.account.workspace_id,request_id:body.request_id,draft_id:row.id,accepted_revision:body.revision+1,command_hash:entry.command_hash,recovered_stages:row.maps.map(stage=>({stage:stage.chunk_id,call_id:stage.reserved_call_id,capture_hash:stage.receipt.capture_hash,journal_descriptor_hash:stage.journal.descriptor_hash,result_hash:stage.result_hash})),remaining_chunks:[],completion_scope:'all_maps',new_cloud_calls:0};await reseal(receipt,'receipt_hash');row.revision++;row.recovery_history.push(receipt);await reseal(row,'view_hash');
 assert.equal(await verifyEvidenceReply({draft:row,receipt},s.account,entry),row);
 const bad=copy(receipt);bad.new_cloud_calls=1;await reseal(bad,'receipt_hash');await assert.rejects(verifyEvidenceReply({draft:row,receipt:bad},s.account,entry));
});

test('actual preview components show scope restriction, truthful limits and disabled separate Reduce consent when blocked',async()=>{
 const s=await reduceScene(),markup=renderToStaticMarkup(React.createElement(Preview,{preview:s.p,Btn:Button,busy:false,writable:true,pending:[],onStart(){assert.fail('SSR cannot dispatch');}}));assert.match(markup,/每组回答的引用仅可选本组已提供的原文片段/);assert.match(markup,/不代表报告理解质量已通过/);assert.match(markup,/单独预览并明确授权/);assert.doesNotMatch(markup,/undefined|json_schema|report-claim-enum/);
 const blocked=copy(s.reduce);blocked.can_start=false;blocked.start_blocker='预算尚未满足';const reduced=renderToStaticMarkup(React.createElement(ReducePreview,{preview:blocked,readingPreview:s.p,Btn:Button,busy:false,writable:true,pending:[],onStart(){assert.fail('SSR cannot dispatch');}}));assert.match(reduced,/单独确认汇总业务草案/);assert.match(reduced,new RegExp('使用'+s.inputs.claims.length+'条已保存原声明'));assert.match(reduced,/disabled/);assert.match(reduced,/预算尚未满足/);assert.match(reduced,/尚不能保证容纳全部内容/);
});

test('actual candidate component reads complete new object workflow without entering editable legacy human form',async()=>{
 const s=await reducedScene(),markup=renderToStaticMarkup(React.createElement(Editor,{value:{result:s.row.model_result,note:''},row:s.row,catalog:s.catalog,account:s.account,Btn:Button,writable:true,onEdit(){assert.fail('machine candidate is read only');}}));assert.match(markup,/业务标识：尚未明确/);assert.match(markup,/交接记录/);assert.match(markup,/等待人工/);assert.match(markup,/技术记录身份仅用于保存/);assert.doesNotMatch(markup,/尚缺明确的必填文本识别字段|保存完整人工稿/);assert.match(markup,/disabled/);
});

test('unknown draft family and old generation cannot be retagged into this family; original V7 remains strict',async()=>{
 const s=await scene();for(const mutate of [row=>{row.schema='report-claim-array-reading-draft-v168';},row=>{row.schema='unknown-new-family';},row=>{row.object_review_family='other';},row=>{row.review_value={result:null,note:''};}]){const bad=copy(s.row);mutate(bad);await reseal(bad,'view_hash');await assert.rejects(verifyEvidenceDraft(bad,s.account));}
 const before=copy(s.f.reduced);await verifyEvidenceDraft(s.f.reduced,s.account);assert.equal(legacyEvidenceReviewAllowed(s.f.reduced),true);assert.deepEqual(s.f.reduced,before);
});
