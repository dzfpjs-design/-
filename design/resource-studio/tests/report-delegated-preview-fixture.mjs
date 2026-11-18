// Synthetic boundary records only: no report, account, cloud call or receipt is real.
import {fixture as fullFixture, incompleteSavedDraft, signedStage, reseal, account, id} from './report-full-reading-fixture.mjs';
import {fixture as evidenceFixture} from './report-evidence-v3-fixture.mjs';
import {copy, digest} from '../src/report-full-reading-contract.mjs';
export {account, id, reseal};

export async function linkedFixture() {
  const f=await fullFixture(), row=await incompleteSavedDraft(f), template=await evidenceFixture();
  const old=row.preview;
  old.provider=template.preview.provider; old.model=template.preview.model;
  old.model_hash=template.preview.published_model_hash; old.configuration_hash=template.preview.configuration_hash;
  old.generation_contract.dispatch_options.model=old.model;
  old.generation_contract_hash=await digest(old.generation_contract);
  for(const b of old.batches){Object.assign(b,await signedStage(b.payload,old.generation_contract));await reseal(b,'batch_hash');}
  old.input_summary={batch_count:old.batches.length,extract_token_bound:old.batches.reduce((n,b)=>n+b.input_summary.token_bound,0),
    total_source_refs:old.batches.reduce((n,b)=>n+b.source_refs.length,0),
    prepared_messages_bytes:old.batches.reduce((n,b)=>n+b.input_summary.prepared_messages_bytes,0),
    full_report_confirmed:false,maximum_total_calls:old.batches.length+1};
  await reseal(old,'preview_hash');
  row.start_receipt.preview_hash=old.preview_hash;
  row.start_receipt.command_hash=await digest({kind:'report_full_reading',actor_id:account.user_id,
    workspace_id:account.workspace_id,body:{...copy(old.body),preview_hash:old.preview_hash,consent:true}});
  await reseal(row.start_receipt,'receipt_hash');
  row.error_code='full_candidate_invalid';row.extractions[0].error_code='full_candidate_invalid';
  await reseal(row,'view_hash');
  async function reply(command){
    const newer=(await evidenceFixture(command.reading)).preview;
    const oldHashes=old.batches.map(b=>b.messages_hash),newHashes=newer.map_preparation.plan.chunks.map(c=>c.messages_hash);
    const history=await reseal({schema:'report-evidence-history-assessment-v1',workspace_id:account.workspace_id,actor_id:account.user_id,
      original_draft_id:row.id,original_revision:row.revision,original_start_receipt_hash:row.start_receipt.receipt_hash,
      original_source_hash:old.source_hash,second_messages_hash:oldHashes[1],original_job_id:row.job_id,original_job_data_hash:'a'.repeat(64),
      original_job_failed_single_attempt_no_active_lease:true,original_authorization_and_failure_audit_verified:true,
      first_response_ledger_and_candidate_verified:true,second_stage_pending_and_workspace_ledger_clear:true,
      independent_transport_or_provider_proof:false,conclusion:'conditional_engineering_inference_not_provider_proof',
      assumptions:['runtime_code_not_independently_attested','database_and_audit_integrity_assumed','no_unobserved_external_gateway_bypass_assumed']},'assessment_hash');
    const basis={schema:'report-evidence-delegated-basis-v1',workspace_id:account.workspace_id,actor_id:account.user_id,
      original_draft_id:row.id,original_revision:row.revision,original_job_id:row.job_id,
      original_start_receipt_hash:row.start_receipt.receipt_hash,original_source_hash:old.source_hash,
      original_projection_hash:old.projection_hash,original_first_raw_hash:'b'.repeat(64),
      original_first_candidate_receipt_hash:'c'.repeat(64),original_messages_hashes:oldHashes,new_messages_hashes:newHashes,
      new_preview_hash:newer.preview_hash,operator_asserted_history_hash:null,history_digest_verification:'not_provided',
      history_assessment:history,historical_second_send:'not_independently_proven',
      source_relationship:'same_report_word_and_ocr_views_overlap',new_analysis:true,not_original_batch_recovery:true,
      authorization_basis:'authenticated_actor_submitted_request',user_reviewed_history:false,user_confirmed_business:false,duplicate_cost_risk:true};
    return reseal({schema:'report-evidence-delegated-preview-v1',basis,reading_preview:newer,can_start:false,
      start_blocker:'合成测试：定向额度尚未接通。',can_apply:false,can_publish:false},'linked_preview_hash');
  }
  return {f,row,reply};
}
