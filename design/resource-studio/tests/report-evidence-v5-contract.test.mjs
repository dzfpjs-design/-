// Root-reviewed ORIGINAL serializers from an empty owned PG + MockTransport.
// These tests prove client/backend protocol parity, not provider quality,
// authentication, original-report semantics, browser UX or persistence anew.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {
 copy,digest,evidenceCatalog,evidenceReviewValue,verifyEvidencePreview,
 verifyEvidenceDraft,verifyEvidenceReply,verifyEvidenceReductionPreview,
 evidenceSavedRecoveryAvailability,
} from '../src/report-evidence-reading-contract.mjs';
import {
 evidenceV5,verifyEvidenceV5MapProjection,evidenceV5ResponseReady,
} from '../src/report-evidence-v5-contract.mjs';

const DIRECTORY=new URL('./fixtures/report-evidence-v5/',import.meta.url);
const FILES=['human-save','mapped','preview','reduce-preview','reduce-start','reduced','saved-facts','start'];
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const hash='f'.repeat(64);

async function captured(){
 const manifest=JSON.parse(await readFile(new URL('manifest.json',DIRECTORY),'utf8'));
 assert.equal(manifest.schema,'report-evidence-public-synthetic-serializer-fixture-v1');
 assert.equal(manifest.provider,'MockTransport only; no actual model or private report');
 assert.equal(manifest.test,'test_v5_real_map_reduce_manual_cas_nonce_facts_and_independent_readback');
 assert.deepEqual(manifest.files.map(item=>item.file),FILES.map(name=>name+'.json'));
 const values={};
 for(const pin of manifest.files){
  assert.deepEqual(Object.keys(pin).sort(),['bytes','file','sha256']);
  const bytes=await readFile(new URL(pin.file,DIRECTORY));
  assert.equal(bytes.byteLength,pin.bytes,pin.file);assert.equal(sha(bytes),pin.sha256,pin.file);
  values[pin.file.slice(0,-5)]=JSON.parse(bytes);
 }
 const account={user_id:values.preview.actor_id,workspace_id:values.preview.workspace_id,role:'admin'};
 return {...values,account};
}

async function reseal(value,key){const { [key]:ignored,...body }=value;value[key]=await digest(body);return value;}

function entryFor(f,kind){
 const preview=f.preview,reduce=f['reduce-preview'];
 const reply=kind==='extract'?f.start:kind==='reduce'?f['reduce-start']:f['human-save'];
 const body=kind==='extract'?{...copy(preview.body),preview_hash:preview.preview_hash,consent:true}:
  kind==='reduce'?{...copy(reduce.body),preview_hash:reduce.preview_hash,consent:true}:
  {revision:f.reduced.revision,request_id:reply.receipt.request_id,value:copy(reply.draft.review_value)};
 return {schema:'report-evidence-reading-command-v3',scope:{actor_id:f.account.user_id,workspace_id:f.account.workspace_id},
  kind,draft_id:kind==='extract'?null:reply.draft.id,body,command_hash:reply.receipt.command_hash,
  binding:{material_id:preview.body.selection.original_ref.id,source_hash:preview.source_hash,
   preview_hash:kind==='reduce'?reduce.preview_hash:preview.preview_hash,
   map_inputs_hash:kind==='reduce'?reduce.reduction_preparation.map_inputs_hash:null,
   map_plan_hash:preview.map_preparation.plan.plan_hash,remaining_chunks:[]},
  receipt:null,rejection:null,resolution:null};
}

async function changedMap(f,mutate){
 const row=copy(f.mapped),stage=row.maps[0];mutate(stage.result,stage);
 await reseal(stage.result,'projection_hash');stage.result_hash=await digest(stage.result);
 await reseal(row,'view_hash');return {row,stage};
}

test('all eight original real serializers cross the V5 client boundary without changing a field',async()=>{
 const f=await captured(),before=copy(f);
 assert.equal(evidenceV5(f.preview),true);await verifyEvidencePreview(f.preview,f.account);
 for(const row of [f.start.draft,f.mapped,f['reduce-start'].draft,f.reduced,f['human-save'].draft]){
  await verifyEvidenceDraft(row,f.account);assert.equal(row.schema,'report-evidence-reading-draft-v3');
  assert.equal(row.can_apply,false);assert.equal(row.can_publish,false);
 }
 await verifyEvidenceReductionPreview(f['reduce-preview'],f.account,f.mapped);
 assert.equal(f.mapped.status,'awaiting_merge');assert.equal(f.mapped.phase,'awaiting_reduce');
 assert.equal(f.mapped.reduction.preview,null);assert.equal(f.mapped.model_result,null);
 assert.equal(f.reduced.status,'review');assert.equal(f.reduced.review_value,null);
 assert.deepEqual(f,before);
});

test('new start and Reduce receipts use original exact commands while human save retains receipt-v1',async()=>{
 const f=await captured(),before=copy(f);
 for(const [kind,reply] of [['extract',f.start],['reduce',f['reduce-start']],['review',f['human-save']]])
  assert.deepEqual(await verifyEvidenceReply(reply,f.account,entryFor(f,kind)),reply.draft);
 assert.equal(f['reduce-start'].receipt.schema,'report-evidence-reduce-receipt-v3');
 assert.equal(f['reduce-start'].receipt.map_inputs_hash,f['reduce-preview'].reduction_preparation.map_inputs_hash);
 assert.equal(f['human-save'].receipt.schema,'report-evidence-review-receipt-v1');
 assert.equal(f['human-save'].current_matches,true);assert.deepEqual(f,before);
 for(const kind of ['extract','reduce','review']){
  const entry=entryFor(f,kind),reply=kind==='extract'?f.start:kind==='reduce'?f['reduce-start']:f['human-save'];
  entry.schema='report-evidence-reading-command-v2';await assert.rejects(verifyEvidenceReply(reply,f.account,entry));
 }
});

test('original sparse slot position and every five-field value survive independent model and display views',async()=>{
 const f=await captured(),catalog=await evidenceCatalog(f.preview),before=copy(f);
 for(const stage of f.mapped.maps){
  await verifyEvidenceV5MapProjection(stage.result,f.mapped,stage,catalog);
  assert.deepEqual(stage.result.slot_positions,[2]);assert.equal(stage.result.claims.length,1);
  assert.equal(stage.result.claims[0].statement,' 合成待核声明＋+；不代表模型质量。 ');
  assert.equal(stage.result.codec,'map-slots-to-v3-v1');
  assert.notEqual(stage.result.http_body_sha256,stage.result.arguments_sha256);
  assert.notEqual(stage.result.arguments_sha256,stage.result.wire_value_hash);
 }
 const facts=f['saved-facts'];assert.equal(facts.schema,'report-evidence-saved-facts-v2');
 assert.deepEqual(facts.slot_origin.slot_positions,[2]);assert.equal(facts.facts[0].position,2);
 assert.equal(facts.facts[0].slot_name,'slot_003');
 const {key,...fields}=f.mapped.maps[0].result.claims[0];assert.deepEqual(facts.facts[0].fields,fields);
 assert.equal(facts.original_protocol_valid,true);assert.equal(facts.meaning_review_status,'unreviewed');
 assert.equal(facts.eligible_for_reduce,false);assert.equal(facts.can_apply,false);assert.equal(facts.can_publish,false);
 assert.deepEqual(f,before);
});

test('re-sealing public projection hashes cannot relabel HTTP, journal, arguments or codec identity',async()=>{
 const f=await captured(),catalog=await evidenceCatalog(f.preview);
 for(const field of ['http_body_sha256','journal_wire_sha256','arguments_sha256','wire_value_hash','view_hash','codec_hash','tool_metadata_hash']){
  const {row,stage}=await changedMap(f,result=>{result[field]=hash;});
  await assert.rejects(verifyEvidenceV5MapProjection(stage.result,row,stage,catalog),field);
 }
 const {row,stage}=await changedMap(f,result=>{result.arguments_sha256=result.http_body_sha256;});
 await assert.rejects(verifyEvidenceV5MapProjection(stage.result,row,stage,catalog));
});

test('slot holes, stable slot-path IDs and exact closed claim fields cannot silently move or grow',async()=>{
 const f=await captured(),catalog=await evidenceCatalog(f.preview);
 const mutations=[result=>{result.slot_positions=[0];},result=>{result.slot_positions=[2,2];},
  result=>{result.claims[0].key='mc_'+hash.slice(0,32);},result=>{result.claims[0].quote='not a slot field';},
  result=>{result.codec='map-slots-to-v3-v99';},result=>{result.wire_schema='report-evidence-map-v3';}];
 for(const mutate of mutations){const {row,stage}=await changedMap(f,mutate);
  await assert.rejects(verifyEvidenceV5MapProjection(stage.result,row,stage,catalog));}
});

test('Reduce preparation consumes every original Map proof exactly once and freezes a separate full POST',async()=>{
 const f=await captured(),r=f['reduce-preview'].reduction_preparation,before=copy(f);
 await verifyEvidenceReductionPreview(f['reduce-preview'],f.account,f.mapped);
 assert.equal(r.call_count,1);assert.equal(r.requires_separate_authorization,true);
 assert.equal(r.generation_contract.evidence_stage,'reduce-v3');
 assert.equal(r.generation_contract.dispatch_options.max_tokens,131072);
 assert.notEqual(r.request_hash,r.messages_hash);assert.equal(Object.hasOwn(r.input_summary,'prepared_messages_bytes'),false);
 assert.equal(r.map_proofs.length,f.mapped.maps.length);assert.deepEqual(f,before);
 for(const mutate of [p=>{p.map_inputs_hash=hash;},p=>{p.map_proofs.pop();},
  p=>{p.map_proofs.reverse();},p=>{p.input_order.push(p.input_order[0]);},
  p=>{p.input_summary.prepared_messages_bytes=p.input_summary.prepared_request_bytes;delete p.input_summary.prepared_request_bytes;}]){
  const p=copy(f['reduce-preview']);mutate(p.reduction_preparation);
  await reseal(p.reduction_preparation,'preparation_hash');await reseal(p,'preview_hash');
  await assert.rejects(verifyEvidenceReductionPreview(p,f.account,f.mapped));
 }
});

test('compact Reduce metadata rebuilds candidate-v6 and cannot use Map journal schema or mixed identity',async()=>{
 const f=await captured(),before=copy(f),metadata=f.reduced.reduction.projection_metadata;
 assert.equal(metadata.schema,'report-evidence-reduction-tool-projection-v3');
 assert.equal(f.reduced.model_result.schema,'report-evidence-candidate-projection-v6');
 assert.equal(f.reduced.reduction.journal.schema,'report-evidence-journal-descriptor-v2');
 assert.equal(Object.hasOwn(metadata,'result'),false);assert.equal(Object.hasOwn(metadata,'reconciliation'),false);
 assert.equal(metadata.map_inputs_hash,f['reduce-preview'].reduction_preparation.map_inputs_hash);
 await verifyEvidenceDraft(f.reduced,f.account);assert.deepEqual(f,before);
 for(const mutate of [m=>{m.map_inputs_hash=hash;},m=>{m.arguments_sha256=m.http_body_sha256;},
  m=>{m.binding.schema='report-evidence-journal-binding-v1';},
  m=>{m.reduction_projection_metadata.projection_hash=hash;},m=>{m.result=copy(f.reduced.model_result);}]){
  const row=copy(f.reduced);mutate(row.reduction.projection_metadata);
  await reseal(row.reduction.projection_metadata,'projection_hash');await reseal(row,'view_hash');
  await assert.rejects(verifyEvidenceDraft(row,f.account));
 }
});

test('completion gates reject length, multiple tools and wrong profile without creating recovery authority',async()=>{
 const f=await captured(),good=f.mapped.maps[0];
 assert.equal(evidenceV5ResponseReady(good,f.preview),true);
 for(const mutate of [s=>{s.receipt.finish_reason='length';},s=>{s.receipt.response_choice_count=2;},
  s=>{s.receipt.tool_call_count=2;},s=>{s.receipt.tool_name='submit_report_evidence_map';},
  s=>{s.receipt.evidence_profile.stage='map-v4';},s=>{s.receipt.envelope_valid=false;}]){
  const stage=copy(good);mutate(stage);stage.status='failed';
  assert.equal(evidenceV5ResponseReady(stage,f.preview),false);
  const failed={...copy(f.mapped),status:'failed',maps:[stage]};
  assert.equal(evidenceSavedRecoveryAvailability(failed).allowed,false);
 }
 assert.equal(f['saved-facts'].eligible_for_reduce,false);
});

test('candidate-v6 becomes an independent complete manual-v5 value without rewriting the model or captured save',async()=>{
 const f=await captured(),before=copy(f),human=evidenceReviewValue(f.reduced),saved=f['human-save'].draft.review_value;
 assert.equal(human.schema,'report-evidence-manual-review-v5');
 assert.equal(human.result.schema,'report-evidence-candidate-wire-v5');assert.equal(human.note,'');
 assert.deepEqual(human.result,saved.result);assert.notEqual(saved.note,'');
 for(const field of ['participants','interactions','conditions','coordination'])assert.ok(human.result.businesses[0][field].length,field);
 assert.deepEqual(human.result.global_claims,f.reduced.model_result.global_claims.map(({key,...claim})=>claim));
 assert.deepEqual(evidenceReviewValue(f['human-save'].draft),saved);
 human.note='后来独立人稿';human.result.businesses[0].steps[0].purpose='后来职责';assert.deepEqual(f,before);
});

test('unknown versions and cross-actor or cross-workspace records fail closed even after re-signing views',async()=>{
 const f=await captured();
 for(const account of [{...f.account,user_id:'10000000-0000-4000-8000-000000000099'},
  {...f.account,workspace_id:'10000000-0000-4000-8000-000000000098'}]){
  await assert.rejects(verifyEvidencePreview(f.preview,account));await assert.rejects(verifyEvidenceDraft(f.reduced,account));
 }
 for(const mutate of [r=>{r.schema='report-evidence-reading-draft-v2';},
  r=>{r.schema='report-evidence-reading-draft-v99';},r=>{r.model_result.schema='report-evidence-candidate-projection-v5';},
  r=>{r.preview.map_preparation.generation_contract.schema='report-evidence-map-generation-v99';}]){
  const row=copy(f.reduced);mutate(row);await reseal(row,'view_hash');await assert.rejects(verifyEvidenceDraft(row,f.account));
 }
 const hiddenHuman=copy(f['human-save'].draft);assert.ok(hiddenHuman.review_history.length);
 hiddenHuman.review_value=null;await reseal(hiddenHuman,'view_hash');
 await assert.rejects(verifyEvidenceDraft(hiddenHuman,f.account),'A saved human history must never fall back to its model value');
});
