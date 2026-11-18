import React,{useEffect,useRef,useState} from 'react';
import {verifyDelegatedCapture} from './report-evidence-delegated-capture.mjs';

const explanations={
 not_recorded:'尚未找到保存的原响应；这不能证明请求没有发送或没有产生费用。',
 partial:'只保存了部分响应；不能将它当作完整回答。',
 transport_interrupted:'接收响应时发生中断；已保存的内容保留，不能自动重发。',
 oversize:'响应超过保存上限或仅保存了片段；不能当作完整回答。',
 unsupported_encoding:'响应编码暂不受支持；原始字节保留，不会自动解码或采用。',
 http_error:'供应商返回了非成功状态；原始响应保留供核对。',
 invalid_envelope:'供应商响应格式无法解析；原始字节保留。',
 multiple_choices:'供应商返回多个候选，不能自动采用其中一个。',
 content_missing:'响应缺少可核对的回答内容；原始字节保留。',
 invalid_content_json:'回答内容不是有效的 JSON；原始字节保留。',
 normal_end_unverified:'供应商报告正常结束，回答格式可读取；内容和业务含义仍需核对，不代表已经采用。',
 length:'供应商报告输出因长度结束，回答可能截断，不能自动采用。',
 other_termination:'供应商未以普通完成状态结束，不能当作完整回答。'
};

/** Read only. Capture status never grants recovery, adoption or retransmission. */
export default function DelegatedEvidenceCapture({account,row,stage,session,Btn='button',onSessionInvalid,onCapture}){
 const scope=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,row?.id,row?.revision,row?.view_hash,stage]);
 const live=useRef({scope,generation:0,serial:0,controller:null});
 if(live.current.scope!==scope){live.current.scope=scope;live.current.generation++;live.current.serial++;}
 const generation=live.current.generation,[view,setView]=useState(null),current=view?.scope===scope&&view?.generation===generation?view:null;
 useEffect(()=>()=>{live.current.serial++;live.current.controller?.abort();live.current.controller=null;},[scope]);
 const path='/setup/report-evidence-readings/'+encodeURIComponent(row.id)+'/delegated-captures/'+encodeURIComponent(stage);
 function start(){const serial=++live.current.serial,ownGeneration=live.current.generation,controller=new AbortController();live.current.controller?.abort();live.current.controller=controller;const active=()=>live.current.scope===scope&&live.current.generation===ownGeneration&&live.current.serial===serial&&!controller.signal.aborted;return {controller,active,ownGeneration};}
 function fail(task,error){if(!task.active())return;if([401,403].includes(error?.status)||error?.code==='SESSION_SCOPE_CHANGED'||/页面会话已变化/.test(error?.message||'')){session.invalidIdentity=true;session.emit();onSessionInvalid?.();}setView({scope,generation:task.ownGeneration,phase:'error',message:[401,403].includes(error?.status)?'当前账号无法查看原响应，私人内容已隐藏。':error?.status===409?'原记录已变化，请刷新后重新核对。':'暂时无法核对原响应，请重试。'});}
 async function read(){if(current?.phase==='loading')return;const task=start();setView({scope,generation:task.ownGeneration,phase:'loading'});try{await session.identity();if(!task.active())return;const value=await session.request(path+'?revision='+encodeURIComponent(row.revision),{signal:task.controller.signal,cache:'no-store'});await verifyDelegatedCapture(value,account,row,stage);await session.identity();if(task.active()&&onCapture)await onCapture(value);if(task.active())setView({scope,generation:task.ownGeneration,phase:'ready',value});}catch(error){fail(task,error);}finally{if(live.current.controller===task.controller)live.current.controller=null;}}

 const value=current?.value,summary=value?.capture;
 return <section aria-label="原响应保存情况"><Btn type="button" disabled={current?.phase==='loading'} onClick={read}>{current?.phase==='loading'?'正在核对原响应…':value?'重新核对原响应状态':'查看原响应状态'}</Btn>
  {current?.phase==='error'&&<p role="alert">{current.message}</p>}
  {value&&<div role="status"><p>{explanations[value.outcome]}</p>{summary?.headers_saved&&summary.encoding_supported===false&&<p>保存的响应编码不受支持，内容不会自动解码。</p>}{value.ledger&&<p>调用记录已保存；{value.ledger.usage_reported?'已收到用量信息。':'尚无可核对的实际用量。'}</p>}
   {value.recovery.status==='eligible'&&<p>当前状态允许免费尝试恢复已保存回答；执行时服务器会重新核对，不会调用模型。</p>}
   {value.body_access==='download_disabled'&&<p>{summary?.body_bytes>0?'原始响应字节已保存在服务器，暂不可下载。':'响应状态已记录，暂不可下载。'}</p>}
   <p>查看状态不会发送模型请求；只有服务器重新核对通过才能恢复，不能凭状态确认或发布。</p></div>}
 </section>;
}
