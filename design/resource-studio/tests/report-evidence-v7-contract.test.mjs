import test from 'node:test';import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {stableStringify} from '../src/content-signature.js';
import {copy,digest,evidenceCatalog,evidenceMapPayload,evidenceReviewValue,verifyEvidencePreview,verifyEvidenceDraft,verifyEvidenceReply,verifyEvidenceReductionPreview} from '../src/report-evidence-reading-contract.mjs';
import {evidenceNative,evidenceV5,evidenceV6,evidenceSlots,checkEvidenceV5Generation,checkEvidenceV5Preparation,evidenceV5Request,evidenceV5ResponseReady} from '../src/report-evidence-v5-contract.mjs';
import {verifyEvidenceSavedFacts} from '../src/report-evidence-saved-facts.mjs';
import specs from '../src/report-evidence-v7-specs.mjs';
import {capturedV7,acceptedEntry,reseal} from './report-evidence-v7-fixture.mjs';

test('GEN7 original API serializers retain native body/content, typed human history and separate paid receipts',async()=>{
 const f=await capturedV7(),before=copy(f);assert.equal(evidenceNative(f.preview),true);assert.equal(evidenceSlots(f.preview),true);assert.equal(evidenceV5(f.preview)||evidenceV6(f.preview),false);
 await verifyEvidencePreview(f.preview,f.account);for(const row of [f.start.draft,f.mapped,f['reduce-start'].draft,f.reduced,f['human-save'].draft])await verifyEvidenceDraft(row,f.account);
 await verifyEvidenceReductionPreview(f['reduce-preview'],f.account,f.mapped);
 for(const kind of ['extract','reduce','review'])await verifyEvidenceReply(kind==='extract'?f.start:kind==='reduce'?f['reduce-start']:f['human-save'],f.account,acceptedEntry(f,kind));
 assert.equal(f['reduce-preview'].reduction_preparation.generation_contract.schema,'report-evidence-reduction-generation-v5');assert.deepEqual(evidenceReviewValue(f['human-save'].draft),f['human-save'].draft.review_value);assert.equal(f['human-save'].draft.review_value.schema,'report-evidence-manual-review-v5');assert.deepEqual(f,before);
});

test('native complete POST includes the full exact schema once and preserves full source coverage independently of object order',async()=>{
 const f=await capturedV7(),catalog=await evidenceCatalog(f.preview),spec=f.preview.map_preparation.generation_contract;
 assert.deepEqual(spec.dispatch_options.response_format.json_schema.schema,spec.output_schema);assert.equal(spec.dispatch_options.tools,undefined);assert.equal(spec.dispatch_options.tool_choice,undefined);
 for(const c of f.preview.map_preparation.plan.chunks){const actual=evidenceV5Request(spec,evidenceMapPayload(f.preview,catalog,c));assert.equal(actual.messages[0].content,specs.stage_prefix['map-v7']);assert.equal(actual.messages[0].content.includes(stableStringify(spec.output_schema)),false);assert.equal(await digest(actual.request),c.request_hash);assert.equal(await digest(actual.messages),c.messages_hash);assert.equal(new TextEncoder().encode(stableStringify(actual.request)).length,c.input_summary.prepared_request_bytes);assert.deepEqual(actual.input_summary,c.input_summary);}
 const reordered=copy(catalog);reordered.index=Object.fromEntries(Object.entries(reordered.index).reverse());await checkEvidenceV5Preparation(f.preview,reordered,f.preview.preparation_cap);
 const changed=copy(f.preview);changed.input_summary.prepared_messages_bytes=changed.input_summary.prepared_request_bytes;await reseal(changed,'preview_hash');await assert.rejects(verifyEvidencePreview(changed,f.account));
 const missing=copy(f.preview);missing.map_preparation.plan.chunks.at(-1).span_refs.pop();await reseal(missing,'preview_hash');await assert.rejects(verifyEvidencePreview(missing,f.account));
});

test('native adoption requires actual stop/content/one choice/zero tools and complete safe capture, never relabels tool replies',async()=>{
 const f=await capturedV7();for(const [stage,reduce] of [[f.mapped.maps[0],false],[f.reduced.reduction,true]]){assert.equal(evidenceV5ResponseReady(stage,f.preview,reduce),true);assert.equal(stage.receipt.finish_reason,'stop');assert.equal(stage.receipt.tool_call_count,0);assert.equal(stage.receipt.response_body_sha256,stage.journal.wire_sha256);assert.notEqual(stage.receipt.arguments_sha256,stage.receipt.response_body_sha256);
  for(const mutate of [r=>{r.finish_reason='tool_calls';},r=>{r.tool_call_count=1;},r=>{r.tool_name='submit_report_evidence_map';},r=>{r.response_choice_count=2;},r=>{r.response_complete=false;},r=>{r.http_content_encoding='gzip';},r=>{r.http_content_encoding='\u0000';},r=>{r.capture_hash=null;},r=>{r.delivery='deepseek-strict-report-tool-v3';}]){const bad=copy(stage);mutate(bad.receipt);assert.equal(evidenceV5ResponseReady(bad,f.preview,reduce),false);}}
 const bad=copy(f.reduced);bad.reduction.projection_metadata.arguments_sha256='f'.repeat(64);await reseal(bad,'view_hash');await assert.rejects(verifyEvidenceDraft(bad,f.account));
});

test('old GEN5 and GEN6 serializers remain accepted verbatim while mixed or unknown native policy is rejected',async()=>{
 const f=await capturedV7();for(const version of [5,6]){const root=new URL('./fixtures/report-evidence-v'+version+'/',import.meta.url),preview=JSON.parse(await readFile(new URL('preview.json',root),'utf8')),row=JSON.parse(await readFile(new URL('reduced.json',root),'utf8')),account={user_id:preview.actor_id,workspace_id:preview.workspace_id,role:'admin'},before=copy(row);await verifyEvidencePreview(preview,account);await verifyEvidenceDraft(row,account);assert.equal(evidenceNative(preview),false);assert.deepEqual(row,before);await assert.rejects(checkEvidenceV5Generation(f.preview.map_preparation.generation_contract,preview.preparation_cap));}
 for(const mutate of [s=>{s.schema='report-evidence-map-generation-v8';},s=>{s.gateway_prefix_hash='f'.repeat(64);},s=>{s.dispatch_options.enable_thinking=true;},s=>{s.schema_proof.provider_schema.properties={};}]){const bad=copy(f.preview.map_preparation.generation_contract);mutate(bad);await assert.rejects(checkEvidenceV5Generation(bad,f.preview.preparation_cap));}
});

test('native saved facts retain original content proof and stay unreviewed with no adoption authority',async()=>{
 const f=await capturedV7(),before=copy(f['saved-facts']);await verifyEvidenceSavedFacts(f['saved-facts'],f.account,f.mapped,f['saved-facts'].stage);assert.equal(f['saved-facts'].generation_schema,'report-evidence-map-generation-v7');assert.equal(f['saved-facts'].tool_metadata.tool_call_count,0);assert.equal(f['saved-facts'].tool_metadata.finish_reason,'stop');for(const key of ['eligible_for_reduce','can_apply','can_publish'])assert.equal(f['saved-facts'][key],false);assert.deepEqual(f['saved-facts'],before);
 const bad=copy(before);bad.tool_metadata.finish_reason='tool_calls';await reseal(bad,'view_hash');await assert.rejects(verifyEvidenceSavedFacts(bad,f.account,f.mapped,bad.stage));
});
