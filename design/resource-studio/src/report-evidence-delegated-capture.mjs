import {digest,fullInt,fullKeys,fullSha,fullUUID} from './report-evidence-reading-contract.mjs';

const outcomes=new Set(['not_recorded','partial','transport_interrupted','oversize','unsupported_encoding','http_error','invalid_envelope','multiple_choices','content_missing','invalid_content_json','normal_end_unverified','length','other_termination']);
const finish=new Set([null,'stop','length','content_filter','tool_calls','function_call']);
const nullableInt=x=>x===null||fullInt(x);
const need=x=>{if(!x)throw Error('原响应状态与当前报告范围无法核对；原记录保留。');};

export async function verifyDelegatedCapture(view,account,row,stage){
 const record=stage==='reduce'?row?.reduction:row?.maps?.find(s=>s.chunk_id===stage);
 const authority=stage==='reduce'?row?.reduction?.preview?.reduction_preparation:row?.preview?.map_preparation?.plan?.chunks?.find(c=>c.chunk_id===stage);
 need(row?.request_id?.startsWith('fl_')&&record&&authority&&fullSha.test(authority.messages_hash||'')&&fullUUID.test(row.id||'')&&fullInt(row.revision,1));
 need(fullKeys(view,'schema,scope,capture,ledger,outcome,finish_reason,choice_count,recovery,can_view_body,body_access,can_apply,can_publish,can_resend,view_hash')&&view.schema==='report-delegated-http-capture-view-v1');
 const scope=view.scope;
 need(fullKeys(scope,'workspace_id,actor_id,draft_id,revision,stage,source_hash,preview_hash,messages_hash,call_id')&&scope.workspace_id===account.workspace_id&&scope.actor_id===account.user_id&&row.actor_id===account.user_id&&row.workspace_id===account.workspace_id&&scope.draft_id===row.id&&scope.revision===row.revision&&scope.stage===stage&&scope.source_hash===row.preview.source_hash&&scope.preview_hash===row.preview.preview_hash&&scope.messages_hash===authority.messages_hash&&scope.call_id===record.reserved_call_id);
 need(outcomes.has(view.outcome)&&finish.has(view.finish_reason)&&nullableInt(view.choice_count)&&view.can_view_body===false&&['not_recorded','download_disabled'].includes(view.body_access)&&view.can_apply===false&&view.can_publish===false&&view.can_resend===false&&fullSha.test(view.view_hash||''));
 const {view_hash,...signed}=view;need(await digest(signed)===view_hash);
 const c=view.capture,l=view.ledger;
 if(l!==null)need(fullKeys(l,'status,reserved_tokens,charged_tokens,usage_reported')&&['reserved','succeeded','failed'].includes(l.status)&&fullInt(l.reserved_tokens)&&fullInt(l.charged_tokens)&&typeof l.usage_reported==='boolean'&&scope.call_id!==null);
 else need(scope.call_id===null);
 if(c===null){need(view.outcome==='not_recorded'&&view.body_access==='not_recorded');}
 else{
  need(fullKeys(c,'state,request_started,headers_saved,http_status,content_encoding_safe,body_bytes,body_sha256,response_complete,observed_bytes,omitted_bytes,prefix_only,reason,capture_hash,encoding_supported')&&['missing','partial','quarantined','complete'].includes(c.state)&&typeof c.request_started==='boolean'&&typeof c.headers_saved==='boolean'&&nullableInt(c.http_status)&&[null,true,false].includes(c.content_encoding_safe)&&[null,true,false].includes(c.encoding_supported)&&fullInt(c.body_bytes)&&c.body_bytes<=1024*1024&&(c.body_sha256===null||fullSha.test(c.body_sha256))&&typeof c.response_complete==='boolean'&&nullableInt(c.observed_bytes)&&nullableInt(c.omitted_bytes)&&typeof c.prefix_only==='boolean'&&(c.reason===null||typeof c.reason==='string'&&c.reason.length<=80)&&fullSha.test(c.capture_hash||''));
  need(view.body_access===(c.state==='missing'?'not_recorded':'download_disabled'));
  if(view.outcome==='normal_end_unverified')need(c.response_complete===true&&c.http_status===200&&c.encoding_supported===true&&view.finish_reason==='stop'&&view.choice_count===1);
  if(view.outcome==='length')need(c.response_complete===true&&c.http_status===200&&c.encoding_supported===true&&view.finish_reason==='length'&&view.choice_count===1);
  if(view.outcome==='multiple_choices')need(c.response_complete===true&&view.choice_count>1);
  if(c.headers_saved===false)need(c.encoding_supported===null&&c.http_status===null);
 }
 const r=view.recovery,blockers=new Set(['role_not_authorized','original_job_unsettled','single_paid_stage_required','inspect_target_stage','paid_stage_unsettled','stage_order_unverified','saved_response_unverified']);
 need(fullKeys(r,'status,blocker,target_stage,remaining_count,basis_hash')&&['not_applicable','blocked','other_stage','eligible'].includes(r.status)&&fullInt(r.remaining_count)&&(r.target_stage===null||typeof r.target_stage==='string'&&/^map-[1-9][0-9]*$|^reduce$/.test(r.target_stage)));
 if(r.status==='not_applicable')need(r.blocker===null&&r.target_stage===null&&r.remaining_count===0&&r.basis_hash===null);
 else if(r.status==='blocked')need(blockers.has(r.blocker)&&r.basis_hash===null);
 else if(r.status==='other_stage')need(r.blocker==='inspect_target_stage'&&r.target_stage!==stage&&r.target_stage!==null&&r.basis_hash===null);
 else {
  const unfinished=row.reduction?.preview?[['reduce',row.reduction]]:row.maps.filter(s=>s.status!=='succeeded').map(s=>[s.chunk_id,s]);
  const targets=unfinished.filter(([,s])=>s.reserved_call_id!==null),remaining=unfinished.filter(([name])=>name!==stage).map(([name])=>name);
  need(['admin','operator'].includes(account.role)&&['failed','response_unknown'].includes(row.status)&&r.blocker===null&&r.target_stage===stage&&fullSha.test(r.basis_hash||'')&&targets.length===1&&targets[0][0]===stage&&remaining.length===r.remaining_count&&c!==null&&c.response_complete===true&&c.encoding_supported===true&&view.outcome==='normal_end_unverified');
  need(await digest({workspace_id:account.workspace_id,actor_id:account.user_id,draft_id:row.id,revision:row.revision,stage,call_id:scope.call_id,capture_hash:c.capture_hash,source_hash:row.preview.source_hash,remaining})===r.basis_hash);
 }
 return view;
}

/** Only a verified, revision-bound server preflight makes the attempt available. */
export function delegatedEvidenceRecoveryAvailability(row,captures={}){
 const unavailable={allowed:false,reason:'请先查看原响应状态；系统须核对已保存响应，不能自动重发。'};
 if(!row?.request_id?.startsWith('fl_')||!['failed','response_unknown'].includes(row.status))return unavailable;
 const stages=row.reduction?.preview?[['reduce',row.reduction]]:row.maps.filter(s=>s.status!=='succeeded').map(s=>[s.chunk_id,s]);
 const targets=stages.filter(([,s])=>s.reserved_call_id!==null);
 if(targets.length!==1)return unavailable;
 const [stage,record]=targets[0],view=captures[stage],r=view?.recovery,s=view?.scope;
 if(view?.schema!=='report-delegated-http-capture-view-v1'||r?.status!=='eligible'||r.target_stage!==stage||r.blocker!==null||!fullSha.test(r.basis_hash||'')||s?.draft_id!==row.id||s?.revision!==row.revision||s?.stage!==stage||s?.call_id!==record.reserved_call_id||s?.source_hash!==row.preview.source_hash||s?.preview_hash!==row.preview.preview_hash||r.remaining_count!==stages.length-1)return unavailable;
 return {allowed:true,reason:'当前检查允许免费尝试从已保存原响应恢复；服务器会在操作时重新核对，条件变化时不会采用或重发。'};
}
