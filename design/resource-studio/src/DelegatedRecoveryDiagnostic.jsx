import React,{useEffect,useRef,useState} from 'react';
import {digest,fullInt,fullKeys,fullSha} from './report-evidence-reading-contract.mjs';

const need=x=>{if(!x)throw Error('任务状态与当前报告无法核对，请刷新后重试。');};
function recoveryPlace(row){
 if(row?.reduction?.preview){return row.reduction.reserved_call_id?{kind:'reduce'}:null;}
 if(!Array.isArray(row?.maps))return null;const matches=row.maps.map((stage,index)=>({stage,index})).filter(x=>x.stage.status!=='succeeded'&&x.stage.reserved_call_id);
 return matches.length===1?{kind:'map',index:matches[0].index+1}:null;
}
export async function verifyRecoveryDiagnostic(value,account,row){
 need(fullKeys(value,'schema,actor_id,workspace_id,draft_id,revision,draft_status,job_status,lease,allowance_time,response,recovery,can_resend,can_resume,can_apply,diagnostic_hash'));
 need(value.schema==='report-delegated-recovery-diagnostic-v1'&&value.actor_id===account.user_id&&value.workspace_id===account.workspace_id&&row.actor_id===account.user_id&&row.workspace_id===account.workspace_id&&value.draft_id===row.id&&value.revision===row.revision&&fullInt(value.revision,1)&&value.draft_status===row.status);
 need(['queued','running','failed','succeeded','cancelled'].includes(value.job_status)&&['not_running','unverified','expired','within_time_window'].includes(value.lease)&&['unverified','not_yet_valid','expired','within_time_window'].includes(value.allowance_time)&&['not_determined','not_recorded','complete_unverified','incomplete_or_unusable','all_completed'].includes(value.response)&&['not_evaluated','not_applicable','blocked','other_stage','eligible'].includes(value.recovery));
 if(value.recovery==='eligible')need(['failed','response_unknown'].includes(row.status)&&recoveryPlace(row)!==null);
 need(value.can_resend===false&&value.can_resume===false&&value.can_apply===false&&fullSha.test(value.diagnostic_hash||''));const {diagnostic_hash,...facts}=value;need(await digest(facts)===diagnostic_hash);
 return value;
}

/** A read-only check; never resumes, sends, or changes the user's report. */
export default function DelegatedRecoveryDiagnostic({account,row,session,Btn='button',onSessionInvalid}){
 const scope=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,row?.id,row?.revision,row?.view_hash]);
 const live=useRef({scope,generation:0,serial:0,controller:null});
 if(live.current.scope!==scope){live.current.scope=scope;live.current.generation++;live.current.serial++;}
 const generation=live.current.generation,[state,setState]=useState(null),current=state?.scope===scope&&state?.generation===generation?state:null;
 useEffect(()=>()=>{live.current.serial++;live.current.controller?.abort();live.current.controller=null;},[scope]);
 async function read(){if(current?.phase==='loading')return;const serial=++live.current.serial,ownGeneration=live.current.generation,controller=new AbortController();live.current.controller?.abort();live.current.controller=controller;const active=()=>live.current.scope===scope&&live.current.generation===ownGeneration&&live.current.serial===serial&&!controller.signal.aborted;setState({scope,generation:ownGeneration,phase:'loading'});
  try{await session.identity();if(!active())return;const value=await session.request('/setup/report-evidence-readings/'+encodeURIComponent(row.id)+'/delegated-recovery-diagnostic?revision='+encodeURIComponent(row.revision),{signal:controller.signal,cache:'no-store'});await verifyRecoveryDiagnostic(value,account,row);await session.identity();if(active())setState({scope,generation:ownGeneration,phase:'ready',value});}
  catch(error){if(!active())return;if([401,403].includes(error?.status)||error?.code==='SESSION_SCOPE_CHANGED'||/页面会话已变化/.test(error?.message||'')){session.invalidIdentity=true;session.emit();onSessionInvalid?.();}setState({scope,generation:ownGeneration,phase:'error',message:[401,403].includes(error?.status)?'账号权限已变化，报告内容已隐藏。':error?.status===409?'任务记录已变化或暂时无法核对，请刷新后重试。':'暂时无法核对任务状态，请重试。'});}
  finally{if(live.current.controller===controller)live.current.controller=null;}}
 const v=current?.value,place=v?.recovery==='eligible'?recoveryPlace(row):null;
 return <section aria-label="报告任务状态核对"><Btn type="button" disabled={current?.phase==='loading'} onClick={read}>{current?.phase==='loading'?'正在核对任务状态…':v?'重新核对任务状态':'核对任务与恢复状态'}</Btn>
 {current?.phase==='error'&&<p role="alert">{current.message}</p>}
 {v&&<div role="status">{v.lease==='within_time_window'&&<p>系统记录的任务处理期限尚未到期；这不能证明任务仍在运行。</p>}{v.lease==='expired'&&<p>系统记录的任务处理期限已过，任务是否仍在运行及实际发送状态仍需核对，请勿重复启动。</p>}{v.lease==='unverified'&&<p>系统记录的任务处理期限暂时无法核对，请勿重复启动。</p>}{v.lease==='not_running'&&<p>任务记录当前不处于运行状态；这不能单独证明请求是否发出。</p>}
 {v.response==='complete_unverified'&&<p>已保存完整响应，内容仍需核对。</p>}{v.response==='not_recorded'&&<p>未找到已保存的响应；这不能证明请求未发出。</p>}{v.response==='incomplete_or_unusable'&&<p>已保存响应不完整或暂不能采用。</p>}{v.response==='not_determined'&&<p>当前无法仅凭任务状态判断是否已发送或收到响应。</p>}{v.response==='all_completed'&&<p>已完成的资料组仍需核对，不代表业务内容已确认。</p>}
 {v.recovery==='eligible'?<p>当前可以免费尝试恢复已保存回答。{place.kind==='reduce'?<>请在页面下方“汇总原响应保存情况”中点击“查看原响应状态”</>:<>请在页面下方“逐组进度与已保存回答”的第 {place.index} 组点击“查看原响应状态”</>}；核对通过后再点击下方“免费尝试恢复已保存回答”。服务器会再次核对，不会调用模型。</p>:<p>当前检查未确认可以免费恢复；原记录保留，不能自动继续或重发。</p>}
 {v.allowance_time==='expired'&&<p>本次分析的调用额度有效期已过；已保存回答的免费恢复仍需单独核对。</p>}{v.allowance_time==='not_yet_valid'&&<p>本次分析的调用额度尚未进入有效期。</p>}{v.allowance_time==='unverified'&&<p>本次分析的调用额度有效期暂时无法核对。</p>}{v.allowance_time==='within_time_window'&&<p>本次分析的调用额度仍在记录的有效期内；其他执行条件仍须核对。</p>}
 <p>本次仅查看状态，不会发送模型请求或修改任务。</p></div>}
 </section>;
}
