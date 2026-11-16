// Public synthetic DTO/SSR checks only. The old fixture supplies public source
// text; all new-family envelopes below are constructed protocol specimens,
// never actual provider, authenticated HTTP, PG, or semantic-quality evidence.
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
import specs from '../src/report-claim-array-specs-v168.mjs';
import {copy,digest,evidenceCatalog,evidenceCommandHash,evidenceReviewValue,verifyEvidencePreview,verifyEvidenceDraft} from '../src/report-evidence-reading-contract.mjs';
import {CLAIM_ARRAY_DRAFT,OBJECT_REVIEW_FAMILY,claimArrayPreview,legacyEvidenceReviewAllowed,claimArrayPolicy,claimArrayRequest,claimArrayDispatch,checkClaimArrayGeneration,checkClaimArrayPreparation,checkClaimArrayCandidate,verifyClaimArrayMapProjection} from '../src/claim-array-reading-v168.mjs';
import {capturedV7,memory,tabFor,missing,reseal,acceptedEntry} from './report-evidence-v7-fixture.mjs';

const hash=raw=>createHash('sha256').update(raw).digest('hex');
const priorTab=globalThis.sessionStorage;globalThis.sessionStorage??=tabFor();
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
let temporary,Editor,Preview;
test.before(async()=>{
 const project=fileURLToPath(new URL('..',import.meta.url));temporary=await mkdtemp(join(tmpdir(),'claim-array-v168-'));
 const output=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),logLevel:'silent',build:{ssr:join(project,'src/ReportEvidenceReadingDialog.jsx'),write:false,minify:false,rollupOptions:{external:['react'],output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}}),path=join(temporary,'dialog.mjs');
 await writeFile(path,output.output.find(item=>item.isEntry).code);const module=await import(pathToFileURL(path).href);Editor=module.EvidenceCandidateEditor;Preview=module.EvidenceCallPreview;
});
test.after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});if(priorTab===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=priorTab;});

async function scene(){
 const f=await capturedV7(),p=copy(f.preview),row=copy(f.start.draft),account=f.account;
 p.preparation_cap={execution:{provider:'qwen',model:'qwen3.8-max-0902',max_tokens:8192,report_evidence_output:{schema:'report-evidence-output-v168',max_tokens:32768,map_transport:'qwen-public-claim-native-v168',reduce_transport:'qwen-public-claim-native-v168',endpoint:'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'}},data_policy:{redact_fields:[]}};p.provider='qwen';p.model='qwen3.8-max-0902';
 const catalog=await evidenceCatalog(p),m=copy(p.map_preparation);m.schema='report-claim-array-map-preparation-v168';m.generation_contract=copy(specs.map);m.plan.schema='report-evidence-read-plan-v2';
 let total=0,tokens=0;
 for(const c of m.plan.chunks){
  const raw={schema:'report-evidence-read-payload-v1',source_hash:catalog.descriptor.source_hash,catalog_hash:catalog.descriptor.catalog_hash,spans:c.span_refs.map(ref=>[ref,catalog.index[ref].text])},payload={schema:'report-claim-array-input-v167',generation:m.generation_contract.schema,task_id:'report-claim-array-map-task-v168',source_hash:raw.source_hash,catalog_hash:raw.catalog_hash,spans:c.span_refs.map(ref=>({span_ref:ref,text:catalog.index[ref].text})),context:copy(m.context),user_instruction:m.user_instruction},r=claimArrayRequest(m.generation_contract,payload);
  Object.assign(c,{payload_hash:await digest(raw),messages_hash:await digest(r.messages),request_hash:await digest(r.request),input_summary:r.input_summary});await reseal(c,'chunk_hash');total+=r.input_summary.prepared_request_bytes;tokens+=r.input_summary.token_bound;
 }
 await reseal(m.plan,'plan_hash');m.input_summary={map_call_count:m.plan.chunks.length,map_token_bound:tokens,prepared_request_bytes:total,maximum_total_calls:m.plan.chunks.length+1,full_report_confirmed:false};await reseal(m,'preparation_hash');p.map_preparation=m;p.input_summary=copy(m.input_summary);await reseal(p,'preview_hash');
 Object.assign(row,{schema:CLAIM_ARRAY_DRAFT,object_review_family:OBJECT_REVIEW_FAMILY,preview:p,review_value:null,review_history:[]});
 row.start_receipt.preview_hash=p.preview_hash;row.start_receipt.command_hash=await evidenceCommandHash(account,'extract',null,{...p.body,preview_hash:p.preview_hash,consent:true});await reseal(row.start_receipt,'receipt_hash');await reseal(row,'view_hash');
 return {f,p,row,account,catalog};
}

async function mappedScene(){
 const s=await scene(),row=s.row,m=s.p.map_preparation;
 for(const [i,c]of m.plan.chunks.entries()){
  const old=copy(s.f.mapped.maps[i]),stage=row.maps[i],ref=c.span_refs[0],wire={schema:'report-evidence-map-v3',claims:[{kind:'职责',statement:'合成核查人核对合成记录。',span_refs:[ref],status:'explicit',explanation:''}]},raw=JSON.stringify(wire),body=JSON.stringify({choices:[{finish_reason:'stop',message:{role:'assistant',content:raw}}],usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}});
  const r={...old.receipt,delivery:'qwen-public-claim-native-v168',provider:'qwen',model:'qwen3.8-max-0902',evidence_profile:claimArrayPolicy(s.p.preparation_cap),response_complete:true,http_content_encoding:null,capture_hash:hash('public synthetic capture '+i),http_status:200,finish_reason:'stop',response_choice_count:1,tool_call_count:0,tool_call_id:null,tool_name:null,tool_type:null,tool_calls_hash:null,arguments_sha256:hash(raw),arguments_bytes:Buffer.byteLength(raw),arguments_encoding:'utf-8',response_body_sha256:hash(body),response_body_bytes:Buffer.byteLength(body),response_body_encoding:'utf-8-surrogateescape-v1',envelope_valid:true,envelope_errors:[],usage_available:true,usage_present:true,usage_unavailable:false,usage_unavailable_reason:null,original_usage_shape:'object',usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}};
  const b={schema:'report-evidence-journal-binding-v1',actor_id:row.actor_id,workspace_id:row.workspace_id,draft_id:row.id,stage:c.chunk_id,reserved_call_id:old.reserved_call_id,source_hash:s.p.source_hash,catalog_hash:s.catalog.descriptor.catalog_hash,generation_hash:await digest({published_model_hash:s.p.published_model_hash,contract:m.generation_contract}),dispatch_hash:await digest(claimArrayDispatch(s.p,c)),messages_hash:c.messages_hash},d={...old.journal,binding:b,binding_hash:await digest(b),wire_bytes:Buffer.byteLength(body),wire_sha256:hash(body),receipt_status:'saved',receipt_bytes:Buffer.byteLength(JSON.stringify(r)),receipt_sha256:hash(JSON.stringify(r)),consumable:true,quarantine_reasons:[]};await reseal(d,'descriptor_hash');
  const metadata=Object.fromEntries(specs.metadata_keys.map(key=>[key,r[key]])),p={schema:'report-claim-array-map-projection-v168',delivery:r.delivery,binding:b,binding_hash:await digest(b),journal_descriptor_hash:d.descriptor_hash,journal_wire_sha256:d.wire_sha256,http_body_sha256:r.response_body_sha256,http_body_bytes:r.response_body_bytes,http_body_encoding:r.response_body_encoding,arguments_sha256:r.arguments_sha256,arguments_bytes:r.arguments_bytes,tool_metadata:metadata,tool_metadata_hash:await digest(metadata),evidence_profile:r.evidence_profile,evidence_profile_hash:await digest(r.evidence_profile),map_preparation_hash:m.preparation_hash,map_plan_hash:m.plan.plan_hash,allowed_span_refs_hash:await digest(c.span_refs),wire,wire_value_hash:await digest(wire),claims:copy(wire.claims)};await reseal(p,'projection_hash');
  Object.assign(stage,{...old,status:'succeeded',receipt:r,journal:d,raw_response_saved:true,result:p,result_hash:await digest(p),result_count:1});
 }
 row.status='awaiting_merge';row.phase='awaiting_reduce';await reseal(row,'view_hash');return s;
}

function candidate(catalog){
 const citation={span_ref:Object.keys(catalog.index)[0]},evidence={status:'explicit',citations:[citation],explanation:''};
 const result={schema:'report-object-semantic-candidate-wire-v166',source_hash:catalog.descriptor.source_hash,catalog_hash:catalog.descriptor.catalog_hash,domain:'公开合成核查领域',global_claims:[],objects:[{name:'核查记录',fields:[{label:'说明',kind:'文本',required:false,unit:null,citations:[citation],inference_reason:''}],citations:[citation],inference_reason:'',business_identity:{status:'not_established',field_labels:[],citations:[],explanation:'原文未在本合成候选中明确业务标识，继续保留待核。'}}],relationships:[],businesses:[{operation:'upsert',original_name:'',name:'核查业务',trigger:'收到合成记录',output:'核查意见',citations:[citation],inference_reason:'',steps:[{name:'核对',purpose:'核对记录',agent:'人工确认',role:'核查人',output:'意见',next_index:1,branch_condition:'',else_index:null,citations:[citation],inference_reason:''},{name:'保存意见',purpose:'保存核对后的意见',agent:'人工确认',role:'核查人',output:'已保存意见',next_index:null,branch_condition:'',else_index:null,citations:[citation],inference_reason:''}],participants:[{agent:'登记人',responsibility:'登记',evidence},{agent:'核查人',responsibility:'核查',evidence}],interactions:[{from_participant_index:0,to_participant_index:1,purpose:'交接记录',condition:'记录已保存',step_indices:[0],evidence}],conditions:[{kind:'precondition',statement:'已保存记录',step_indices:[0],evidence}],coordination:[{kind:'sequence',step_indices:[0,1],start_rule:'开始核查',wait_rule:'等待人工',join_rule:'意见已保存',evidence}]}],questions:['业务标识尚未明确；技术身份不代表业务编号。']};
 return result;
}

test('new family verifies actual request shape and complete source without entering old draft codecs',async()=>{
 const s=await scene(),before=copy(s.row);await verifyEvidencePreview(s.p,s.account);await verifyEvidenceDraft(s.row,s.account);assert.equal(claimArrayPreview(s.p),true);assert.equal(evidenceReviewValue(s.row),null);assert.equal(legacyEvidenceReviewAllowed(s.row),false);assert.deepEqual(s.row,before);
 for(const c of s.p.map_preparation.plan.chunks){assert.notEqual(c.messages_hash,c.request_hash);assert.ok(c.input_summary.prepared_request_bytes<=100000);}assert.equal(s.p.input_summary.maximum_total_calls,s.row.maps.length+1);
});

test('new Map projection remains keyless and bound to its own actual synthetic call/source/plan',async()=>{
 const s=await mappedScene();await verifyEvidenceDraft(s.row,s.account);for(const stage of s.row.maps){await verifyClaimArrayMapProjection(stage.result,s.row,stage,s.catalog);assert.equal(Object.hasOwn(stage.result.claims[0],'key'),false);}
});

for(const [label,mutate]of [
 ['old row schema',r=>{r.schema='report-evidence-reading-draft-v3';}],
 ['missing family',r=>{delete r.object_review_family;}],
 ['unknown family',r=>{r.object_review_family='other';}],
 ['legacy human value',r=>{r.review_value={result:r.model_result,note:''};}],
 ['extra key',r=>{r.can_execute=true;}],
])test('mixed family is rejected after public self rehash: '+label,async()=>{const s=await scene();mutate(s.row);await reseal(s.row,'view_hash');await assert.rejects(verifyEvidenceDraft(s.row,s.account));});

for(const [label,mutate]of [
 ['another call',p=>{p.binding.reserved_call_id='ffffffff-ffff-4fff-8fff-ffffffffffff';}],
 ['another delivery',p=>{p.delivery='qwen-tokenplan-native-json-schema-v1';}],
 ['foreign reference',p=>{p.claims[0].span_refs=['w:p9999/s1'];p.wire.claims=copy(p.claims);}],
 ['invented model key',p=>{p.claims[0].key='invented';p.wire.claims=copy(p.claims);}],
])test('new map refuses mixed metadata even with public projection hash: '+label,async()=>{const s=await mappedScene(),stage=s.row.maps[0];mutate(stage.result);await reseal(stage.result,'projection_hash');stage.result_hash=await digest(stage.result);await assert.rejects(verifyClaimArrayMapProjection(stage.result,s.row,stage,s.catalog));});

test('exact generation task/schema/profile are independent of a public self-hash',async()=>{
 const s=await scene();for(const change of [v=>{v.task+=' authorize execution';v.task_hash='f'.repeat(64);},v=>{v.output_schema={type:'object'};},v=>{v.dispatch_options.max_tokens=8192;},v=>{v.endpoint='https://example.invalid';}]){const bad=copy(s.p.map_preparation.generation_contract);change(bad);await assert.rejects(checkClaimArrayGeneration(bad,s.p.preparation_cap));}
 const bad=copy(s.p);bad.map_preparation.plan.chunks[0].span_refs.pop();await reseal(bad.map_preparation.plan.chunks[0],'chunk_hash');await reseal(bad.map_preparation.plan,'plan_hash');await reseal(bad.map_preparation,'preparation_hash');await assert.rejects(checkClaimArrayPreparation(bad,s.catalog,s.p.preparation_cap));
});

test('all optional native objects remain a valid new candidate without a legacy required-text identity',async()=>{
 const s=await scene(),value=candidate(s.catalog),before=copy(value);assert.equal(checkClaimArrayCandidate(value,s.catalog),value);assert.deepEqual(value,before);assert.equal(value.objects[0].fields[0].required,false);
 const bad=copy(value);bad.objects[0].business_identity={status:'referenced',field_labels:['missing'],citations:[{span_ref:Object.keys(s.catalog.index)[0]}],explanation:'unverified'};assert.throws(()=>checkClaimArrayCandidate(bad,s.catalog));
});

test('an explicit sequence still requires at least two referenced steps',async()=>{
 const s=await scene(),value=candidate(s.catalog),business=value.businesses[0];business.steps.pop();business.steps[0].next_index=null;business.coordination[0].step_indices=[0];
 assert.throws(()=>checkClaimArrayCandidate(value,s.catalog));
});

test('actual read-only editor renders index-based businesses and identity explanation, not old form/required gate',async()=>{
 const s=await scene(),value=candidate(s.catalog),changes=[];
 const markup=renderToStaticMarkup(React.createElement(Editor,{value:{result:value,note:''},row:s.row,catalog:s.catalog,account:s.account,Btn:Button,writable:true,onEdit:(...args)=>changes.push(args)}));
 assert.match(markup,/业务标识：尚未明确/);assert.match(markup,/交接记录/);assert.match(markup,/技术记录身份仅用于保存/);assert.doesNotMatch(markup,/尚缺明确的必填文本识别字段|补充业务|保存完整人工稿/);assert.match(markup,/disabled/);assert.deepEqual(changes,[]);
});

test('actual request preview renders new-family full POST size and separate Reduce consent',async()=>{
 const s=await scene(),markup=renderToStaticMarkup(React.createElement(Preview,{preview:s.p,Btn:Button,busy:false,writable:true,pending:[],onStart(){assert.fail('SSR must not dispatch');}}));
 assert.match(markup,/发送内容大小（正文和格式要求）/);assert.ok(markup.includes(s.p.input_summary.prepared_request_bytes.toLocaleString()));assert.match(markup,/单独预览并明确授权/);assert.doesNotMatch(markup,/report-claim-array|json_schema|undefined/);
});

test('new family cannot create a legacy private save; existing immutable pending remains untouched',async()=>{
 const s=await scene(),calls=[],{ReportEvidenceReadingSession}=await import('../src/report-evidence-reading-session.mjs'),session=new ReportEvidenceReadingSession({account:s.account,materialID:s.p.body.selection.original_ref.id,storage:memory(),tabStorage:tabFor(s.account),locks:{request:async(_key,_options,callback)=>callback()},request:async(path,options)=>{calls.push({path,options});throw missing();}});
 try{session.row=copy(s.row);session.state.draft_id=s.row.id;session.state.value={result:candidate(s.catalog),note:'preserved synthetic human text'};const before=copy(session.state);await assert.rejects(session.save(),/独立对象修订/);assert.deepEqual(session.state,before);assert.equal(calls.length,0);}finally{session.dispose();}
});

test('old immutable review still has GET-only recovery, but new family cannot retry PUT or settle old CAS',async()=>{
 const s=await scene(),calls=[],{ReportEvidenceReadingSession}=await import('../src/report-evidence-reading-session.mjs'),session=new ReportEvidenceReadingSession({account:s.account,materialID:s.p.body.selection.original_ref.id,storage:memory(),tabStorage:tabFor(s.account),locks:{request:async(_key,_options,callback)=>callback()},request:async(path,options={})=>{calls.push({path,...options});if(path==='/me')return s.account;throw missing();}});
 try{
  session.row=copy(s.row);session.state.draft_id=s.row.id;session.state.pending=acceptedEntry(s.f,'review');assert.equal(session.persist(),true);const before=copy(session.state),raw=session.storage.getItem(session.key);
  await assert.rejects(session.save({probeOnly:true}),error=>error.status===404);assert.ok(calls.some(call=>call.path.includes('/review-receipts/')));assert.equal(calls.some(call=>call.method==='PUT'||call.method==='POST'),false);
  await assert.rejects(session.save({retry:true}),/独立对象修订稿/);assert.equal(calls.some(call=>call.method==='PUT'||call.method==='POST'),false);assert.deepEqual(session.state,before);assert.equal(session.storage.getItem(session.key),raw);
  session.conflict=copy(s.row);assert.throws(()=>session.resolveConflict(true),/独立对象修订/);assert.deepEqual(session.state,before);assert.equal(session.storage.getItem(session.key),raw);
 }finally{session.dispose();}
});

test('legacy V7 remains strict and readable without new-family marker or conversion',async()=>{
 const f=await capturedV7(),before=copy(f.reduced);await verifyEvidenceDraft(f.reduced,f.account);assert.equal(legacyEvidenceReviewAllowed(f.reduced),true);assert.equal(evidenceReviewValue(f.reduced).schema,'report-evidence-manual-review-v5');assert.deepEqual(f.reduced,before);
 const bad=copy(f.reduced);bad.object_review_family=OBJECT_REVIEW_FAMILY;await reseal(bad,'view_hash');await assert.rejects(verifyEvidenceDraft(bad,f.account));
});
