import React,{useEffect,useId,useRef,useState} from 'react';
import {GRAPH_MESSAGE_RETRY_REASON,graphMessageStatusView} from './graph-message-status.mjs';

export default function GraphMessageStatus({task,busy=false,blockedReason='',canSubmit=true,onRetry,onRefresh}){
 const heading=useId(),alive=useRef(true),inFlight=useRef(false);
 const [confirmation,setConfirmation]=useState(null),[action,setAction]=useState(''),[problem,setProblem]=useState('');
 let view=null,invalid=false;
 try{view=graphMessageStatusView(task);}catch{invalid=true;}
 const target=view?.status.error;
 const targetKey=target?JSON.stringify([task.id,target.job_id,target.job_version]):'';
 const allowed=!!view?.status.can_retry&&!busy&&!action&&canSubmit===true&&!blockedReason&&typeof onRetry==='function';
 const confirming=confirmation!==null&&confirmation===targetKey&&allowed;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{setConfirmation(null);setProblem('');},[task?.id,targetKey]);
 async function perform(kind,callback){
  if(busy||inFlight.current||typeof callback!=='function')return;
  inFlight.current=true;setAction(kind);setProblem('');
  try{await callback();}
  catch{if(alive.current)setProblem(kind==='retry'
   ?'恢复请求尚未核对，请使用原请求核对入口查看是否已保存；不要另建请求。'
   :'等待进度暂未更新，当前记录和草稿保留。');}
  finally{inFlight.current=false;if(alive.current)setAction('');}
 }
 const refresh=<button type="button" disabled={busy||!!action||typeof onRefresh!=='function'}
  onClick={()=>perform('refresh',onRefresh)}>更新等待进度</button>;
 if(invalid)return <section className="media-form" aria-labelledby={heading}>
  <h3 id={heading}>消息等待状态需核对</h3>
  <p role="alert">消息等待状态暂无法核对，已保存草稿、原请求核对和任务取消仍可使用。</p>
  {problem&&<p role="alert">{problem}</p>}{refresh}
 </section>;
 if(view===null)return null;
 return <section className="media-form" aria-labelledby={heading}>
  <h3 id={heading}>消息等待与检查</h3>
  <p>最近一次消息核对：{view.checkedAt}</p>
  <p>这里只展示已接纳处理结果的等待状态。消息到达不证明现场情况，仍需按业务要求核实。</p>
  {view.problem&&<aside role={view.problem.kind==='blocked'?'alert':'status'}>
   <h4>{view.problem.title}</h4><p>{view.problem.detail}</p>
  </aside>}
  {view.windows.length===0&&<p>当前没有已建立的消息等待窗口，请结合其他待办和条件查看任务进度。</p>}
  {view.windows.map(window=><article className="media-form" key={window.key}>
   <h4>{window.label}</h4><p>{window.title}</p><p>{window.detail}</p>
   <dl><dt>开始等待</dt><dd>{window.openedAt}</dd><dt>本次等待期限</dt><dd>{window.deadline}</dd>
    {window.closedAt!==null&&<><dt>后台结束核对</dt><dd>{window.closedAt}</dd></>}
   </dl>
  </article>)}
  {!!blockedReason&&<p>当前执行条件暂不可用于恢复检查；原等待记录、草稿和原请求核对入口保留。</p>}
  {view.status.error&&!view.status.can_retry&&view.problem?.kind==='blocked'
   &&<p>当前不能恢复此检查，请由任务负责人或管理员核对。</p>}
  <div className="actions">{refresh}{view.status.can_retry&&!confirming&&<button type="button" disabled={!allowed}
   onClick={()=>{if(allowed){setProblem('');setConfirmation(targetKey);}}}>恢复原消息检查</button>}</div>
  {confirming&&<aside className="info" role="group" aria-label="确认恢复原消息检查">
   <p>将按原窗口、原期限和原处理回执恢复检查，不会重新执行模型或重新采集。排队恢复不代表结果已到达。</p>
   <div className="actions"><button type="button" disabled={!allowed} onClick={()=>{
    if(!allowed||confirmation!==targetKey)return;setConfirmation(null);
    perform('retry',()=>onRetry(GRAPH_MESSAGE_RETRY_REASON));
   }}>确认恢复检查</button><button type="button" disabled={busy||!!action} onClick={()=>setConfirmation(null)}>暂不恢复</button></div>
  </aside>}
  {action==='retry'&&<p role="status">正在核对并提交原消息检查恢复请求。</p>}
  {problem&&<p role="alert">{problem}</p>}
 </section>;
}
