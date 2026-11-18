import {digest, fullInt, fullKeys, fullSha, fullUUID} from './report-full-reading-contract.mjs';

const invalid=()=>{throw Error('无法核对汇总范围，请重新读取报告状态。');};
const need=value=>{if(!value)invalid();};

/** Only the server's current read-only contract is accepted. It grants no send authority. */
export async function verifyDelegatedReducePreview(value,account,row,body){
 const v2=value?.schema==='delegated-reduce-preview-v2';
 need(fullKeys(value,'schema,preflight,supported_map_count,maximum_call_count,token_upper_bound,price_limit_available,price_upper_bound,price_note,additional_cost,user_confirmed_business,can_start,can_send,start_blocker'+(v2?',receipt_binding':''))&&['delegated-reduce-preview-v1','delegated-reduce-preview-v2'].includes(value.schema));
 need(value.supported_map_count===14&&value.maximum_call_count===1&&fullInt(value.token_upper_bound,1)&&value.price_limit_available===false&&value.price_upper_bound===null&&typeof value.price_note==='string'&&value.additional_cost===true&&value.user_confirmed_business===false&&((value.can_start===false&&value.can_send===false&&typeof value.start_blocker==='string'&&value.start_blocker.length>0)||(value.can_start===true&&value.can_send===true&&value.start_blocker===null)));
 const p=value.preflight;
 need(fullKeys(p,'schema,draft_id,revision,workspace_id,actor_id,original_job_id,source_hash,map_plan_hash,map_results_hash,map_calls,reduction_preview_hash,reduction_preparation_hash,token_bound,additional_call_count,additional_cost,user_confirmed_business,can_start,can_send,preflight_hash')&&p.schema==='delegated-reduce-readonly-preflight-v1');
 need(account?.role==='admin'&&p.workspace_id===account.workspace_id&&p.actor_id===account.user_id&&p.draft_id===row.id&&p.revision===body.revision&&p.revision===row.revision&&p.original_job_id===row.job_id&&fullUUID.test(p.original_job_id||'')&&p.source_hash===row.preview?.source_hash&&row.status==='awaiting_merge'&&row.request_id?.startsWith('fl_'));
 need(Array.isArray(row.maps)&&row.maps.length===14&&row.maps.every(s=>s.status==='succeeded'&&fullSha.test(s.result_hash||'')&&fullUUID.test(s.reserved_call_id||''))&&p.map_calls===row.maps.length&&p.map_results_hash===await digest(row.maps.map(s=>[s.chunk_id,s.result_hash,s.reserved_call_id])));
 need([p.source_hash,p.map_plan_hash,p.map_results_hash,p.reduction_preview_hash,p.reduction_preparation_hash,p.preflight_hash].every(x=>fullSha.test(x||''))&&p.token_bound===value.token_upper_bound&&p.additional_call_count===1&&p.additional_cost===true&&p.user_confirmed_business===false&&p.can_start===false&&p.can_send===false);
 const {preflight_hash,...unsigned}=p;need(await digest(unsigned)===preflight_hash);
 if(v2){const b=value.receipt_binding;
  need(fullKeys(b,'schema,workspace_id,actor_id,draft_id,revision,request_id,preflight_hash,reduction_preview_hash,map_plan_hash,map_inputs_hash,reduction_preparation_hash,map_grant_manifest_hash,binding_hash')&&b.schema==='delegated-reduce-receipt-binding-v1');
  need(b.workspace_id===account.workspace_id&&b.actor_id===account.user_id&&b.draft_id===row.id&&b.revision===body.revision&&b.request_id===body.request_id&&b.preflight_hash===p.preflight_hash&&b.reduction_preview_hash===p.reduction_preview_hash&&b.map_grant_manifest_hash===p.map_plan_hash&&b.map_plan_hash===row.preview?.map_preparation?.plan?.plan_hash);
  need([b.map_plan_hash,b.map_inputs_hash,b.reduction_preparation_hash,b.binding_hash].every(x=>fullSha.test(x||'')));const {binding_hash,...unsignedBinding}=b;need(await digest(unsignedBinding)===binding_hash);
 }
 return value;
}
