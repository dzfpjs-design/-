import {digest, fullInt, fullKeys, fullSha, fullUUID} from './report-full-reading-contract.mjs';

const invalid=()=>{throw Error('无法核对汇总范围，请重新读取报告状态。');};
const need=value=>{if(!value)invalid();};

/** Only the server's current read-only contract is accepted. It grants no send authority. */
export async function verifyDelegatedReducePreview(value,account,row,body){
 need(fullKeys(value,'schema,preflight,supported_map_count,maximum_call_count,token_upper_bound,price_limit_available,price_upper_bound,price_note,additional_cost,user_confirmed_business,can_start,can_send,start_blocker')&&value.schema==='delegated-reduce-preview-v1');
 need(value.supported_map_count===14&&value.maximum_call_count===1&&fullInt(value.token_upper_bound,1)&&value.price_limit_available===false&&value.price_upper_bound===null&&typeof value.price_note==='string'&&value.additional_cost===true&&value.user_confirmed_business===false&&((value.can_start===false&&value.can_send===false&&typeof value.start_blocker==='string'&&value.start_blocker.length>0)||(value.can_start===true&&value.can_send===true&&value.start_blocker===null)));
 const p=value.preflight;
 need(fullKeys(p,'schema,draft_id,revision,workspace_id,actor_id,original_job_id,source_hash,map_plan_hash,map_results_hash,map_calls,reduction_preview_hash,reduction_preparation_hash,token_bound,additional_call_count,additional_cost,user_confirmed_business,can_start,can_send,preflight_hash')&&p.schema==='delegated-reduce-readonly-preflight-v1');
 need(account?.role==='admin'&&p.workspace_id===account.workspace_id&&p.actor_id===account.user_id&&p.draft_id===row.id&&p.revision===body.revision&&p.revision===row.revision&&p.original_job_id===row.job_id&&fullUUID.test(p.original_job_id||'')&&p.source_hash===row.preview?.source_hash&&row.status==='awaiting_merge'&&row.request_id?.startsWith('fl_'));
 need(Array.isArray(row.maps)&&row.maps.length===14&&row.maps.every(s=>s.status==='succeeded'&&fullSha.test(s.result_hash||'')&&fullUUID.test(s.reserved_call_id||''))&&p.map_calls===row.maps.length&&p.map_results_hash===await digest(row.maps.map(s=>[s.chunk_id,s.result_hash,s.reserved_call_id])));
 need([p.source_hash,p.map_plan_hash,p.map_results_hash,p.reduction_preview_hash,p.reduction_preparation_hash,p.preflight_hash].every(x=>fullSha.test(x||''))&&p.token_bound===value.token_upper_bound&&p.additional_call_count===1&&p.additional_cost===true&&p.user_confirmed_business===false&&p.can_start===false&&p.can_send===false);
 const {preflight_hash,...unsigned}=p;need(await digest(unsigned)===preflight_hash);
 return value;
}
