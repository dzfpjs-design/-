// ORIGINAL empty-PG synthetic serializers supplied by Root. This is protocol
// parity/negative-copy coverage, not new auth, provider quality or browser proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {stableStringify} from '../src/content-signature.js';
import {copy,digest,evidenceCatalog,evidenceMapPayload,evidenceReviewValue,verifyEvidencePreview,
 verifyEvidenceDraft,verifyEvidenceReply,verifyEvidenceReductionPreview} from '../src/report-evidence-reading-contract.mjs';
import {evidenceV5,evidenceV6,evidenceSlots,checkEvidenceV5Generation,evidenceV5Request,
 checkEvidenceV5Preparation,evidenceV5ResponseReady,evidenceV5ManualAvailability} from '../src/report-evidence-v5-contract.mjs';
import {verifyEvidenceSavedFacts} from '../src/report-evidence-saved-facts.mjs';

const names=['human-save','mapped','preview','reduce-preview','reduce-start','reduced','saved-facts','start'];
async function captured(version=6){
 const directory=new URL('./fixtures/report-evidence-v'+version+'/',import.meta.url),manifest=JSON.parse(await readFile(new URL('manifest.json',directory),'utf8'));
 assert.equal(manifest.schema,'report-evidence-public-synthetic-serializer-fixture-v1');
 assert.equal(manifest.provider,'MockTransport only; no actual model or private report');
 assert.deepEqual(manifest.files.map(pin=>pin.file),names.map(name=>name+'.json'));
 const f={};for(const pin of manifest.files){assert.deepEqual(Object.keys(pin).sort(),['bytes','file','sha256']);const raw=await readFile(new URL(pin.file,directory));assert.equal(raw.length,pin.bytes);assert.equal(createHash('sha256').update(raw).digest('hex'),pin.sha256);f[pin.file.slice(0,-5)]=JSON.parse(raw);}
 f.account={user_id:f.preview.actor_id,workspace_id:f.preview.workspace_id,role:'admin'};return f;
}
async function seal(value,key='view_hash'){const body={...value};delete body[key];value[key]=await digest(body);return value;}
function entry(f,kind){
 const p=kind==='reduce'?f['reduce-preview']:f.preview,reply=kind==='extract'?f.start:kind==='reduce'?f['reduce-start']:f['human-save'];
 const body=kind==='review'?{revision:reply.receipt.accepted_revision-1,request_id:reply.receipt.request_id,value:copy(reply.draft.review_value)}:{...copy(p.body),preview_hash:p.preview_hash,consent:true};
 return {schema:'report-evidence-reading-command-v3',scope:{actor_id:f.account.user_id,workspace_id:f.account.workspace_id},kind,draft_id:kind==='extract'?null:reply.draft.id,body,command_hash:reply.receipt.command_hash,binding:{material_id:f.preview.body.selection.original_ref.id,source_hash:f.preview.source_hash,preview_hash:p.preview_hash,map_inputs_hash:kind==='reduce'?p.reduction_preparation.map_inputs_hash:null,map_plan_hash:f.preview.map_preparation.plan.plan_hash,remaining_chunks:[]},receipt:null,rejection:null,resolution:null};
}

test('GEN6 original serializers validate the complete compiler3/profile4/POST chain and unchanged typed human value',async()=>{
 const f=await captured(),before=copy(f);
 assert.equal(evidenceV5(f.preview),false);assert.equal(evidenceV6(f.preview),true);assert.equal(evidenceSlots(f.preview),true);
 assert.equal(f.preview.preparation_cap.execution.report_evidence_output.schema,'report-evidence-output-v4');
 assert.match(JSON.stringify(f.preview.map_preparation.generation_contract),/report-evidence-strict-ref-arm-proof-v3/);
 await verifyEvidencePreview(f.preview,f.account);
 for(const row of [f.start.draft,f.mapped,f['reduce-start'].draft,f.reduced,f['human-save'].draft])await verifyEvidenceDraft(row,f.account);
 await verifyEvidenceReductionPreview(f['reduce-preview'],f.account,f.mapped);
 for(const kind of ['extract','reduce','review'])await verifyEvidenceReply(kind==='extract'?f.start:kind==='reduce'?f['reduce-start']:f['human-save'],f.account,entry(f,kind));
 assert.equal(f['reduce-preview'].reduction_preparation.generation_contract.schema,'report-evidence-reduction-generation-v4');
 const human=evidenceReviewValue(f['human-save'].draft);assert.deepEqual(human,f['human-save'].draft.review_value);assert.equal(human.schema,'report-evidence-manual-review-v5');
 assert.deepEqual(f,before);
});

test('the supported old GEN5 original serializers remain unchanged; mixed and unknown versions are rejected',async()=>{
 const old=await captured(5),f=await captured(),before=copy(old);
 await verifyEvidencePreview(old.preview,old.account);await verifyEvidenceDraft(old.reduced,old.account);assert.equal(evidenceV5(old.preview),true);assert.equal(evidenceV6(old.preview),false);assert.deepEqual(old,before);
 const freshSpec=f.preview.map_preparation.generation_contract,oldSpec=old.preview.map_preparation.generation_contract;
 await assert.rejects(checkEvidenceV5Generation(freshSpec,old.preview.preparation_cap));
 await assert.rejects(checkEvidenceV5Generation(oldSpec,f.preview.preparation_cap));
 for(const mutate of [s=>{s.schema='report-evidence-map-generation-v7';},s=>{s.gateway_prefix_hash='f'.repeat(64);},s=>{s.task+=' changed';},s=>{s.dispatch_options.tool_choice.function.name='other_tool';}]){
  const changed=copy(freshSpec);mutate(changed);await assert.rejects(checkEvidenceV5Generation(changed,f.preview.preparation_cap));
 }
});

test('all source spans retain their full canonical POST hash, size and order independently of index object order',async()=>{
 const f=await captured(),catalog=await evidenceCatalog(f.preview),spec=f.preview.map_preparation.generation_contract;
 for(const chunk of f.preview.map_preparation.plan.chunks){const actual=evidenceV5Request(spec,evidenceMapPayload(f.preview,catalog,chunk));assert.equal(await digest(actual.request),chunk.request_hash);assert.equal(await digest(actual.messages),chunk.messages_hash);assert.equal(new TextEncoder().encode(stableStringify(actual.request)).length,chunk.input_summary.prepared_request_bytes);assert.deepEqual(actual.input_summary,chunk.input_summary);}
 const reordered=copy(catalog);reordered.index=Object.fromEntries(Object.entries(reordered.index).reverse());await checkEvidenceV5Preparation(f.preview,reordered,f.preview.preparation_cap);
 const changed=copy(f.preview);changed.input_summary.prepared_messages_bytes=changed.input_summary.prepared_request_bytes;await seal(changed,'preview_hash');await assert.rejects(verifyEvidencePreview(changed,f.account));
 const truncated=copy(f.preview);truncated.map_preparation.plan.chunks.at(-1).span_refs.pop();await seal(truncated,'preview_hash');await assert.rejects(verifyEvidencePreview(truncated,f.account));
});

test('new success proof never adopts mismatched delivery, old stage policy, body or arguments hashes',async()=>{
 const f=await captured(),row=f.mapped,stage=row.maps[0];assert.equal(evidenceV5ResponseReady(stage,row.preview),true);
 for(const mutate of [s=>{s.receipt.delivery='deepseek-strict-report-tool-v2';},s=>{s.receipt.evidence_profile.stage='map-v5';},s=>{s.receipt.response_body_sha256='f'.repeat(64);},s=>{s.receipt.response_choice_count=2;}]){const changed=copy(stage);mutate(changed);assert.equal(evidenceV5ResponseReady(changed,row.preview),false);}
 const changed=copy(f.reduced);changed.reduction.projection_metadata.arguments_sha256='f'.repeat(64);await seal(changed);await assert.rejects(verifyEvidenceDraft(changed,f.account));
});

test('GEN6 saved facts stay read-only and scope/protocol bound; oversized human conversion remains blocked without truncation',async()=>{
 const f=await captured(),facts=f['saved-facts'],before=copy(facts);await verifyEvidenceSavedFacts(facts,f.account,f.mapped,facts.stage);
 assert.equal(facts.generation_schema,'report-evidence-map-generation-v6');assert.equal(facts.tool_metadata.delivery,'deepseek-strict-report-tool-v3');
 for(const key of ['eligible_for_reduce','can_apply','can_publish'])assert.equal(facts[key],false);assert.deepEqual(facts,before);
 for(const mutate of [v=>{v.generation_schema='report-evidence-map-generation-v5';},v=>{v.tool_metadata.delivery='deepseek-strict-report-tool-v2';},v=>{v.draft_revision++;}]){const changed=copy(facts);mutate(changed);await seal(changed);await assert.rejects(verifyEvidenceSavedFacts(changed,f.account,f.mapped,facts.stage));}
 // Capacity-only synthetic copy, never presented as a new valid server proof.
 const row=copy(f.reduced),claim=row.model_result.global_claims[0];assert.ok(claim);
 row.model_result.global_claims=Array.from({length:160},(_,i)=>({...copy(claim),key:'gc_'+i.toString(16).padStart(32,'0'),statement:'字'.repeat(2000),explanation:'核'.repeat(1000)}));
 const original=JSON.stringify(row.model_result);assert.equal(evidenceV5ManualAvailability(row).allowed,false);assert.equal(evidenceReviewValue(row),null);assert.equal(JSON.stringify(row.model_result),original);
});
