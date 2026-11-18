import {digest,fullInt,fullKeys,fullSha,fullUUID,same} from './report-evidence-reading-contract.mjs';

const BASIS='schema,workspace_id,actor_id,original_draft_id,original_revision,original_job_id,original_start_receipt_hash,original_source_hash,original_projection_hash,original_first_raw_hash,original_first_candidate_receipt_hash,original_messages_hashes,new_messages_hashes,new_preview_hash,operator_asserted_history_hash,history_digest_verification,history_assessment,historical_second_send,source_relationship,new_analysis,not_original_batch_recovery,authorization_basis,user_reviewed_history,user_confirmed_business,duplicate_cost_risk';
const ASSESSMENT='schema,workspace_id,actor_id,original_draft_id,original_revision,original_start_receipt_hash,original_source_hash,second_messages_hash,original_job_id,original_job_data_hash,original_job_failed_single_attempt_no_active_lease,original_authorization_and_failure_audit_verified,first_response_ledger_and_candidate_verified,second_stage_pending_and_workspace_ledger_clear,independent_transport_or_provider_proof,conclusion,assumptions,assessment_hash';
const ASSUMPTIONS=['runtime_code_not_independently_attested','database_and_audit_integrity_assumed','no_unobserved_external_gateway_bypass_assumed'];
const need=value=>{if(!value)throw Error('独立分析的来源与原记录无法核对，已保留原输入并暂停操作。');};

/** Validate the original server envelope before exposing a linked draft. */
export async function verifyDelegatedEvidenceOrigin(reply,account,row){
 need(fullKeys(reply,'schema,origin,can_apply,can_publish')&&reply.schema==='report-evidence-delegated-origin-v1'&&reply.can_apply===false&&reply.can_publish===false);
 const origin=reply.origin,link=origin?.payload,b=link?.basis;
 need(fullKeys(origin,'payload,origin_hash')&&fullSha.test(origin.origin_hash||'')&&await digest(link)===origin.origin_hash);
 need(fullKeys(link,'schema,basis,new_draft_id,new_job_id,request_id,command_hash')&&link.schema==='report-evidence-delegated-origin-v1'&&fullSha.test(link.command_hash||'')&&fullUUID.test(link.new_job_id||'')&&link.new_draft_id===row?.id&&link.request_id===row?.request_id&&/^fl_[0-9a-f-]{36}$/.test(link.request_id||''));
 need(fullKeys(b,BASIS)&&b.schema==='report-evidence-delegated-basis-v1'&&b.actor_id===account?.user_id&&b.workspace_id===account?.workspace_id&&row.actor_id===account.user_id&&row.workspace_id===account.workspace_id&&fullUUID.test(b.original_draft_id||'')&&fullUUID.test(b.original_job_id||'')&&fullInt(b.original_revision,1));
 for(const key of ['original_start_receipt_hash','original_source_hash','original_projection_hash','original_first_raw_hash','original_first_candidate_receipt_hash','new_preview_hash'])need(fullSha.test(b[key]||''));
 need(b.operator_asserted_history_hash===null||fullSha.test(b.operator_asserted_history_hash||''));
 need(b.original_source_hash===row.preview?.source_hash&&b.original_projection_hash===row.preview?.projection_hash&&b.new_preview_hash===row.preview?.preview_hash);
 const chunks=row.preview?.map_preparation?.plan?.chunks;
 need(Array.isArray(chunks)&&chunks.length>0&&Array.isArray(b.new_messages_hashes)&&same(b.new_messages_hashes,chunks.map(c=>c.messages_hash))&&b.new_messages_hashes.every(x=>fullSha.test(x||''))&&new Set(b.new_messages_hashes).size===b.new_messages_hashes.length);
 need(Array.isArray(b.original_messages_hashes)&&b.original_messages_hashes.length===2&&b.original_messages_hashes.every(x=>fullSha.test(x||''))&&!b.original_messages_hashes.some(x=>b.new_messages_hashes.includes(x)));
 const a=b.history_assessment;
 need(fullKeys(a,ASSESSMENT)&&a.schema==='report-evidence-history-assessment-v1'&&fullSha.test(a.assessment_hash||''));
 const {assessment_hash,...assessment}=a;need(await digest(assessment)===assessment_hash);
 for(const key of ['workspace_id','actor_id','original_draft_id','original_revision','original_start_receipt_hash','original_source_hash','original_job_id'])need(a[key]===b[key]);
 need(a.second_messages_hash===b.original_messages_hashes[1]&&fullSha.test(a.original_job_data_hash||'')&&a.original_job_failed_single_attempt_no_active_lease===true&&a.original_authorization_and_failure_audit_verified===true&&a.first_response_ledger_and_candidate_verified===true&&a.second_stage_pending_and_workspace_ledger_clear===true&&a.independent_transport_or_provider_proof===false&&a.conclusion==='conditional_engineering_inference_not_provider_proof'&&same(a.assumptions,ASSUMPTIONS));
 need(b.history_digest_verification===(b.operator_asserted_history_hash===null?'not_provided':'operator_asserted_not_independently_verified')&&b.historical_second_send==='not_independently_proven'&&b.source_relationship==='same_report_word_and_ocr_views_overlap'&&b.new_analysis===true&&b.not_original_batch_recovery===true&&b.authorization_basis==='authenticated_actor_submitted_request'&&b.user_reviewed_history===false&&b.user_confirmed_business===false&&b.duplicate_cost_risk===true);
 return {draft_id:row.id,request_id:row.request_id,preview_hash:row.preview.preview_hash,source_hash:row.preview.source_hash,origin_hash:origin.origin_hash};
}

export function delegatedEvidenceOriginMatches(origin,row){
 return !!origin&&origin.draft_id===row?.id&&origin.request_id===row?.request_id&&origin.preview_hash===row?.preview?.preview_hash&&origin.source_hash===row?.preview?.source_hash&&fullSha.test(origin.origin_hash||'');
}
