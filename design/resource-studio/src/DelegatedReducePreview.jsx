import React,{useEffect,useRef,useState} from 'react';
import {verifyDelegatedReducePreview} from './report-evidence-delegated-reduce.mjs';

/** Read-only: the server's disabled authorization contract never grants a send. */
export default function DelegatedReducePreview({account,row,session,Btn='button',onSessionInvalid}){
 const scope=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,row?.id,row?.revision,row?.view_hash]);
 const live=useRef({scope,generation:0,serial:0,controller:null});
 if(live.current.scope!==scope){live.current.scope=scope;live.current.generation++;live.current.serial++;}
 const generation=live.current.generation,[view,setView]=useState(null),current=view?.scope===scope&&view?.generation===generation?view:null;
 useEffect(()=>()=>{live.current.serial++;live.current.controller?.abort();live.current.controller=null;},[scope]);
 const expected=account?.role==='admin'&&row?.actor_id===account.user_id&&row?.workspace_id===account.workspace_id&&row?.request_id?.startsWith('fl_')&&row?.status==='awaiting_merge';
 async function read(){
  if(!expected||current?.phase==='loading')return;
  const serial=++live.current.serial,ownGeneration=live.current.generation,controller=new AbortController();live.current.controller?.abort();live.current.controller=controller;
  const active=()=>live.current.scope===scope&&live.current.generation===ownGeneration&&live.current.serial===serial&&!controller.signal.aborted;
  setView({scope,generation:ownGeneration,phase:'loading'});
  try{
   await session.identity();if(!active())return;
   const origin=await session.delegatedCheck(row);if(!active())return;
   if(!origin||session.row?.id!==row.id||session.row?.view_hash!==row.view_hash)throw Error('当前报告已变化，请重新读取。');
   const body={revision:row.revision,request_id:'fer_'+crypto.randomUUID()};
   const value=await session.request('/setup/report-evidence-readings/'+encodeURIComponent(row.id)+'/delegated-reduce/preview',{method:'POST',signal:controller.signal,cache:'no-store',body:JSON.stringify(body)});
   await verifyDelegatedReducePreview(value,account,row,body);await session.identity();
   if(active()&&session.row?.view_hash===row.view_hash)setView({scope,generation:ownGeneration,phase:'ready',value});
  }catch(error){
   if(!active())return;
   if([401,403].includes(error?.status)||error?.code==='SESSION_SCOPE_CHANGED'||/页面会话已变化/.test(error?.message||'')){session.invalidIdentity=true;session.emit();onSessionInvalid?.();}
   setView({scope,generation:ownGeneration,phase:'error',message:[401,403].includes(error?.status)?'当前账号无法核对汇总范围，私人内容已隐藏。':error?.status===409?'资料组、依据或账本暂不能完整核对，请刷新报告后再试。':'暂时无法核对汇总范围；原资料和已保存结果均保留，可重试。'});
  }finally{if(live.current.controller===controller)live.current.controller=null;}
 }
 if(account?.role!=='admin')return <section aria-label="汇总业务草案"><h4>汇总业务草案</h4><p>请由本报告所属工作区的管理员核对汇总范围；已保存的资料组仍可查看。</p></section>;
 if(!expected)return null;
 if(row.maps.length!==14||row.maps.some(stage=>stage.status!=='succeeded'))return <section aria-label="汇总业务草案"><h4>汇总业务草案</h4><p>当前汇总仅支持 14 个资料组全部完成并可核对的报告；已保存的结果仍可查看。</p></section>;
 return <section aria-label="汇总业务草案"><h4>汇总业务草案</h4><p>汇总依据已保存的分段结果及其原文引用生成候选业务草案；不会自动确认或应用业务。</p>
  <Btn type="button" disabled={current?.phase==='loading'} onClick={read}>{current?.phase==='loading'?'正在核对汇总范围…':current?.phase==='ready'?'重新核对汇总范围':'核对汇总范围与费用'}</Btn>
  {current?.phase==='error'&&<p role="alert">{current.message}</p>}
  {current?.phase==='ready'&&<div role="status"><p>服务器已核对 {row.maps.length} 个已完成资料组。本次额外汇总最多调用模型 1 次，保守预留 {current.value.token_upper_bound.toLocaleString()} token。</p><p>token 不是金额上限；尚无可核对的模型单价，无法计算或保证实际费用上限。</p><p>本次核对不会创建汇总任务或调用模型。原始资料与各组结果已保存在服务器；本页预览未保存，刷新后可重新核对。</p>{current.value.can_start?<p>服务器核对时具备汇总条件；界面启动功能尚不可用，尚未创建汇总任务或调用模型。已保存的结果仍可查看。</p>:<p>汇总授权与安全派发尚未开放，当前不能开始汇总。已保存的结果仍可查看。</p>}</div>}
 </section>;
}
