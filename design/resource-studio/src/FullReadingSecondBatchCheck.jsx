import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {digest,fullKeys,fullInt,fullSha,fullText,fullUUID,same,verifyFullDraft} from './report-full-reading-contract.mjs';

const need=value=>{if(!value)throw Error('invalid');};
const messages={
 role_not_authorized:'当前角色不能准备云模型调用，请联系管理员核对权限。',
 first_stage_not_repairable:'第一批原记录的状态不符合本次核对条件，请联系管理员核对。',
 second_stage_not_pristine:'第二批已有处理记录，是否发送过尚不能确定，请联系管理员核对原记录。',
 second_stage_not_sent_unproven:'缺少第二批未发送的独立证明，暂无法判断是否已经发送，请联系管理员核对原记录。',
 first_raw_unverified:'第一批原回答或费用记录无法核对，请联系管理员核对。',
 original_job_unverified:'原后台任务状态无法核对，请联系管理员核对。',
 original_history_unverified:'原授权或第一批失败记录无法核对，请联系管理员核对。',
 candidate_store_unavailable:'机器候选保存功能暂不可用，请联系管理员。',
 first_candidate_required:'请先在第一批保存机器待核候选，然后重新核对。',
 first_candidate_unverified:'第一批候选与原回答无法核对，请联系管理员。',
 ledger_ambiguous:'存在需要核对的云调用记录，第二批是否发送过尚不能确定，请联系管理员。',
 ledger_capacity:'待核对的云调用记录过多，第二批是否发送过尚不能确定，请联系管理员。',
 resume_job_exists:'存在恢复任务或记录，第二批是否发送过尚不能确定，请联系管理员核对原记录。',
 frozen_scope_changed:'原资料、识别版本、配置或模型发生变化或无法核对，请刷新并重新核对。',
 budget_insufficient:'当日模型额度不足，尚未预留额度，请联系管理员核对。',
 budget_unverified:'预算记录无法核对，尚未预留额度，请联系管理员。'
};

async function checked(value,account,row){
 await verifyFullDraft(row,account);
 const batch=row.preview.batches[1],scope=value?.scope;
 need(row.preview.batches.length===2&&batch?.batch_id==='extract-2');
 need(fullKeys(value,'schema,scope,original_job,original_history_verified,first_candidate,original_authorization,ledger_clear,frozen_scope_current,budget,batch,blockers,eligible_for_future_resume,can_start,can_send,can_apply,can_publish,requires_separate_authorization,first_batch_confirmed,unverified,preview_hash')&&value.schema==='report-full-second-batch-resume-preview-v1');
 need(fullKeys(scope,'workspace_id,actor_id,draft_id,revision,stage,source_hash,original_preview_hash,original_start_receipt_hash,batch_hash,messages_hash,generation_contract_hash')&&scope.workspace_id===account.workspace_id&&scope.actor_id===account.user_id&&scope.draft_id===row.id&&scope.revision===row.revision&&scope.stage==='extract-2'&&scope.source_hash===row.preview.source_hash&&scope.original_preview_hash===row.preview.preview_hash&&scope.original_start_receipt_hash===row.start_receipt.receipt_hash&&scope.batch_hash===batch.batch_hash&&scope.messages_hash===batch.messages_hash&&scope.generation_contract_hash===row.preview.generation_contract_hash);
 need([value.can_start,value.can_send,value.can_apply,value.can_publish,value.first_batch_confirmed].every(x=>x===false)&&value.requires_separate_authorization===true);
 need(typeof value.original_history_verified==='boolean'&&typeof value.ledger_clear==='boolean'&&typeof value.frozen_scope_current==='boolean');
 need(fullKeys(value.original_authorization,'request_id,frozen_batch_count,original_consent_verified,new_send_authorized')&&value.original_authorization.request_id===row.request_id&&value.original_authorization.frozen_batch_count===2&&value.original_authorization.original_consent_verified===true&&value.original_authorization.new_send_authorized===false);
 need(fullKeys(value.batch,'source_count,input_summary,provider,model')&&value.batch.source_count===batch.source_refs.length&&same(value.batch.input_summary,batch.input_summary)&&value.batch.provider===row.preview.provider&&value.batch.model===row.preview.model&&fullText(value.batch.model,200)&&fullText(value.batch.provider,200));
 need(Array.isArray(value.blockers)&&value.blockers.length<=50&&value.blockers.every(b=>fullKeys(b,'code,message')&&fullText(b.code,100)&&fullText(b.message,1000))&&typeof value.eligible_for_future_resume==='boolean'&&value.eligible_for_future_resume===(value.blockers.length===0));
 const codes=value.blockers.map(b=>b.code),has=code=>codes.includes(code);
 need(new Set(codes).size===codes.length&&has('second_stage_not_sent_unproven'));
 need(has('role_not_authorized')===!['admin','operator'].includes(account.role));
 need(has('first_stage_not_repairable')===!(row.status==='failed'&&row.phase==='failed'&&row.error_code==='full_candidate_invalid'&&row.extractions[0]?.status==='failed'&&row.extractions[0]?.error_code==='full_candidate_invalid'));
 need(has('original_history_unverified')===!value.original_history_verified&&has('frozen_scope_changed')===!value.frozen_scope_current&&has('ledger_ambiguous')===!value.ledger_clear);
 if(value.original_job===null)need(has('original_job_unverified'));
 else {const j=value.original_job;need(!has('original_job_unverified')&&fullKeys(j,'id,status,attempts,lease_recoveries,data_hash')&&j.id===row.job_id&&j.status==='failed'&&j.attempts===1&&j.lease_recoveries===0&&fullSha.test(j.data_hash));}
 const candidateBlockers=['candidate_store_unavailable','first_candidate_required','first_candidate_unverified'].filter(has);
 need(value.first_candidate===null?candidateBlockers.length===1:candidateBlockers.length===0);
 need(Array.isArray(value.unverified)&&value.unverified.length<=20&&value.unverified.every(x=>fullText(x,120)));
 if(value.first_candidate!==null){let c=value.first_candidate;need(fullKeys(c,'receipt_hash,algorithm_version,quarantine_count,confirmed,status')&&fullSha.test(c.receipt_hash)&&fullText(c.algorithm_version,120)&&fullInt(c.quarantine_count)&&c.quarantine_count<=1630&&c.confirmed===false&&c.status==='needs_review');}
 if(value.budget!==null){const b=value.budget;need(fullKeys(b,'day_utc,observed_at_utc,used_tokens,token_limit,used_calls,call_limit,required_tokens,available')&&/^\d{4}-\d{2}-\d{2}$/.test(b.day_utc)&&fullText(b.observed_at_utc,60)&&Number.isFinite(Date.parse(b.observed_at_utc))&&b.observed_at_utc.startsWith(b.day_utc)&&['used_tokens','token_limit','used_calls','call_limit','required_tokens'].every(k=>fullInt(b[k]))&&b.required_tokens===batch.input_summary.token_bound&&b.available===(b.used_tokens+b.required_tokens<=b.token_limit&&b.used_calls+1<=b.call_limit)&&!has('budget_unverified')&&has('budget_insufficient')===!b.available);}
 else need(has('budget_unverified')&&!has('budget_insufficient'));
 need(fullSha.test(value.preview_hash));const {preview_hash,...signed}=value;need(await digest(signed)===preview_hash);
 return value;
}

export default function FullReadingSecondBatchCheck({account,row,Btn='button',request=json,onSessionInvalid}){
 const expected=!!(fullUUID.test(account?.user_id||'')&&fullUUID.test(account?.workspace_id||'')&&fullText(account?.role,60)&&account.role&&row?.actor_id===account.user_id&&row?.workspace_id===account.workspace_id&&fullUUID.test(row?.id||'')&&fullInt(row.revision,1)&&fullSha.test(row.view_hash||'')&&row.preview?.batches?.length===2);
 const scope=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,row?.id,row?.revision,row?.view_hash,row?.preview?.preview_hash]);
 const live=useRef({scope,generation:0,serial:0,controller:null});
 if(live.current.scope!==scope){live.current.scope=scope;live.current.generation++;live.current.serial++;}
 const generation=live.current.generation,[view,setView]=useState(null),current=view?.scope===scope&&view?.generation===generation?view:null;
 useEffect(()=>()=>{live.current.serial++;live.current.controller?.abort();live.current.controller=null;},[scope]);
 async function load(){
  if(!expected||current?.phase==='loading')return;
  const serial=++live.current.serial,ownGeneration=live.current.generation,controller=new AbortController();live.current.controller?.abort();live.current.controller=controller;
  const active=()=>live.current.scope===scope&&live.current.generation===ownGeneration&&live.current.serial===serial&&!controller.signal.aborted;
  setView({scope,generation:ownGeneration,phase:'loading'});
  try{await verifyFullDraft(row,account);if(!active())return;const path='/setup/report-full-readings/'+encodeURIComponent(row.id)+'/stages/extract-2/resume-preview?revision='+encodeURIComponent(row.revision);
   const result=await request(path,{method:'POST',signal:controller.signal,cache:'no-store',headers:{'X-Workspace-ID':account.workspace_id}});
   const value=await checked(result,account,row);if(active())setView({scope,generation:ownGeneration,phase:'ready',value});
  }catch(error){if(active()){if(error?.status===401||error?.status===403)onSessionInvalid?.();setView({scope,generation:ownGeneration,phase:'error',message:error?.status===401||error?.status===403?'账号权限已变化，核对内容已隐藏。':error?.status===409?'原记录已变化，请刷新后重新核对。':error?.message==='invalid'?'核对结果与原记录不一致，请刷新后重试。':'暂时无法核对，请重试或联系管理员。'});}}
  finally{if(live.current.controller===controller)live.current.controller=null;}
 }
 if(!expected)return null;
 const v=current?.value,b=v?.budget;
 return <section aria-label="第二批读取条件核对"><h4>第 2 批读取条件核对</h4>
  <p>本次只核对，不发送第 2 批；当前无法在此开始读取。是否曾经发送，须以原记录核对结果为准。</p>
  <Btn type="button" disabled={current?.phase==='loading'} onClick={load}>{current?.phase==='loading'?'正在核对…':v?'重新核对读取条件':'核对继续读取的条件'}</Btn>
  {current?.phase==='error'&&<p role="alert">{current.message}</p>}
  {v&&<div role="status"><p>{v.first_candidate?'第一批机器候选已保存，有 '+v.first_candidate.quarantine_count+' 项需重点核对，尚未人工确认。':'未能核对到第一批已保存的机器候选。'}</p>
   <p>第 2 批模型：{v.batch.model}；来源 {v.batch.source_count} 处；保守额度上限 {v.batch.input_summary.token_bound.toLocaleString()} token。具体发送文字请查看原读取范围。</p>
   {b?<p>工作区额度（UTC {b.day_utc}，核对时点）：已用 {b.used_tokens.toLocaleString()} / {b.token_limit.toLocaleString()} token，调用 {b.used_calls} / {b.call_limit} 次；本批需要 {b.required_tokens.toLocaleString()} token。此信息可能变化，未预留额度。</p>:<p>当前预算记录无法核对，未预留额度。</p>}
   <p>费用金额尚无法估算；本次核对不产生模型调用。后续合并可能另行产生费用。</p>
   {v.blockers.length?<><p>以下条件尚需处理：</p><ul>{v.blockers.map((b,i)=><li key={i}>{messages[b.code]||'存在暂无法识别的核对问题，请联系管理员。'}</li>)}</ul></>:<p>现有条件已核对；继续读取功能尚不可用。</p>}
   <p>当前无法开始读取；将来如可继续，仍须另行核对范围与费用。机器候选不是已确认业务，不能应用或发布。</p></div>}
 </section>;
}
