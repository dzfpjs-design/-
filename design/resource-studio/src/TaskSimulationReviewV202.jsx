import React,{useEffect,useRef,useState} from 'react';
import {createTaskSimulationReviewController,simulationReviewScopeKey,SIMULATION_REVIEW_RECEIPT} from './task-simulation-review-v202.mjs';

function SavedReview({value,scope}){
 if(!value||value.schema!==SIMULATION_REVIEW_RECEIPT||value.task_id!==scope.task_id||value.activation_id!==scope.activation_id||value.state!=='saved'||value.engineering_only!==true||value.gaps_resolved!==false||value.human_review_verified!==false||!Array.isArray(value.missing_information)||!value.missing_information.every(item=>typeof item==='string'))return null;
 return <aside className="info" aria-label="已保存的模拟联动复核"><strong>资料缺项仍未解决</strong><p>仅模拟设备联动的工程复核已保存。仍须核对原设备回执与任务进度；不证明现场事实，不作为人工业务审查或生产执行授权。</p>{value.missing_information.length>0&&<ul>{value.missing_information.map((item,index)=><li key={index} style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{item}</li>)}</ul>}</aside>;
}
export default function TaskSimulationReviewV202(props){
 const scope={actor_id:typeof props.actor==='string'?props.actor:props.actor?.user_id||props.actor?.id,workspace_id:typeof props.workspace==='string'?props.workspace:props.workspace?.workspace_id||props.workspace?.id,task_id:props.task?.id,activation_id:props.activationID};let key;
 try{key=simulationReviewScopeKey(scope);}catch{return <p role="alert">当前账号或模拟步骤暂不能核对，未提交工程复核。</p>;}
 return <Review key={key} {...props} scope={scope}/>;
}
function Review({scope,task,api,available=false,savedReview,onRegisterGuard,onSaved,blockedReason=''}){
 const holder=useRef(),latest=useRef();latest.current={onSaved};if(!holder.current)holder.current=createTaskSimulationReviewController({api,scope});const c=holder.current,[,draw]=useState(0),[expanded,setExpanded]=useState(false);
 const run=async action=>{const token=c.generation;try{return await action();}catch(error){if(c.active&&token===c.generation&&!c.denied){c.error=error.message;c.notify();}return null;}};
 useEffect(()=>{let mounted=true;c.onChange=()=>{if(mounted&&c.active)draw(n=>n+1);};c.onSaved=value=>mounted?latest.current.onSaved?.(value):false;c.mount();if(c.pending)setExpanded(true);c.notify();
  const focus=()=>{if(c.opened||c.pending)run(()=>c.identity());},unload=event=>{if(!c.safeToLeave()){event.preventDefault();event.returnValue='';}},storage=event=>{if(event.key?.startsWith(c.prefix))run(async()=>{c.check();c.reloadPending();c.notify();});};
  window.addEventListener('focus',focus);window.addEventListener('beforeunload',unload);window.addEventListener('storage',storage);return()=>{mounted=false;c.dispose();window.removeEventListener('focus',focus);window.removeEventListener('beforeunload',unload);window.removeEventListener('storage',storage);};
 },[c]);
 useEffect(()=>{const guard=()=>c.safeToLeave();onRegisterGuard?.(guard);return()=>onRegisterGuard?.(null,guard);},[c,onRegisterGuard]);
 useEffect(()=>{if(c.active&&!c.denied&&!c.pending&&c.basis&&task?.version>c.basis.task_version){c.form.acknowledged=false;c.persist();c.notify();}},[c,task?.version,c.basis?.task_version]);
 if(c.denied)return <p role="alert">账号、工作区或任务权限已变化，原分析与输入已隐藏；原请求仍保留。</p>;
 const newerTask=!!c.basis&&task?.version>c.basis.task_version,visible=expanded,locked=c.busy||!!c.pending||!!blockedReason||c.protected||newerTask,showEntry=available&&!visible&&!c.pending;
 if(!available&&!visible&&!savedReview&&!c.receipt&&!c.error)return null;
 return <section aria-label="模拟设备联动复核" className="media-form">
  <SavedReview value={savedReview} scope={scope}/>
  {!visible&&c.pending&&<aside className="info"><strong>原模拟联动复核请求待核对</strong><p>原编号与完整内容已保留，尚未另建请求或重发。</p><button type="button" disabled={c.busy} onClick={()=>{setExpanded(true);run(()=>c.open());}}>继续核对原复核请求</button></aside>}
  {c.receipt&&!savedReview&&<aside className="info" role="status"><strong>资料缺项仍未解决</strong><p>工程复核已保存，原设备步骤已重新排队；后续仍须核对设备回执、归档与独立验收。</p></aside>}
  {showEntry&&<><p>可先查看本步骤是否满足模拟设备联动条件；查看不会派设备或修改原分析。</p><button type="button" disabled={c.busy||!!blockedReason} onClick={()=>{setExpanded(true);run(()=>c.open());}}>查看模拟联动条件</button></>}
  {visible&&<>
   <div className="actions"><h5>仅核对已批准的模拟设备动作</h5><button type="button" disabled={c.busy} onClick={()=>{if(c.safeToLeave())setExpanded(false);}}>收起复核</button></div>
   <p>原模型建议和资料缺项保持原样。此处仅允许在原批准范围内验证模拟设备动作，不补充业务证据，也不扩大设备权限。</p>
   {blockedReason&&<p role="alert">{blockedReason}</p>}
   {c.busy&&<p role="status">正在核对原分析与模拟联动复核记录…</p>}
   {!c.localSaved&&<div role="alert">本机理由尚未安全保存，暂不能提交。<button type="button" disabled={c.busy} onClick={()=>run(()=>c.retryLocal())}>重试保存理由</button></div>}
   {c.pending&&<aside className="info"><h5>原复核请求待核对</h5><p>原编号、理由与完整内容已保留。只核对原请求，不另建编号或自动重发；刷新后仍可继续核对。</p>{c.pending.rejected&&<p role="alert">服务器未接受本次复核：{c.pending.rejected.message}</p>}<p style={{whiteSpace:'pre-wrap'}}>原理由：{c.pending.body.reason}</p><button type="button" disabled={c.busy} onClick={()=>run(()=>c.recover())}>只核对原复核请求</button>{c.pendingNotFound&&c.pending.rejected&&<button type="button" disabled={c.busy} onClick={()=>run(()=>c.archiveRejected())}>保留被拒请求，重新核对条件</button>}</aside>}
   {!c.basis&&!c.pending&&!c.busy&&<button type="button" onClick={()=>run(()=>c.open())}>重新读取模拟条件</button>}
   {c.basis&&<>
    <aside className="info" role="note"><strong>资料缺项仍未解决</strong><p>已保存的模型分析：{c.basis.analysis_summary||'暂无可核对的原分析摘要。'}</p><p>原建议依据：{c.basis.candidate_reason||'暂无可核对的建议依据。'}</p>{c.basis.missing_information.length>0?<><h5>尚缺少的资料</h5><ul>{c.basis.missing_information.map((item,index)=><li key={index} style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{item}</li>)}</ul></>:<p>仍按服务器保存的原资料状态核对；工程复核不将任何资料缺项改为已解决。</p>}</aside>
    {!!c.basis.targets.length&&<div><h5>本次仅涉及以下模拟设备</h5><ul>{c.basis.targets.map((item,index)=><li key={index}>{item.display_name} · 仅模拟（SIM）</li>)}</ul></div>}
    {c.basis.reason&&<p role={c.basis.can_confirm?'note':'status'}>{c.basis.reason}</p>}
    {c.basis.state==='not_applicable'&&<p>当前步骤不适用此工程复核，未提交或重新排队。原分析与任务记录保留。</p>}
    {newerTask&&<p role="alert">任务已更新，原理由仍保留。请先刷新复核依据，再核对当前资料缺项与模拟设备；旧勾选已取消。</p>}
    {c.stale&&<aside role="alert"><p>原任务或复核依据已变化，旧理由保留；请对照当前分析与缺项后重新勾选。</p><button type="button" disabled={locked} onClick={()=>run(()=>c.useLatest())}>保留理由，采用当前复核依据</button></aside>}
    {c.basis.state==='reviewable'&&<>
     <label>本次模拟验证理由<textarea aria-label="模拟设备联动验证理由" rows={3} maxLength={1000} disabled={locked||!c.basis.can_confirm} value={c.form.reason} onChange={event=>run(()=>c.edit({reason:event.target.value}))}/></label><p>最多1000字。理由在本机保留，确认后保存到服务器原任务。</p>
     <label><input type="checkbox" checked={c.form.acknowledged} disabled={locked||c.stale||!c.basis.can_confirm} onChange={event=>run(()=>c.edit({acknowledged:event.target.checked}))}/>保留资料缺项，仅验证已批准的模拟设备动作</label>
     <div className="actions"><button type="button" disabled={!c.canConfirm||!!blockedReason||newerTask} onClick={()=>run(()=>c.confirm())}>仅验证模拟设备联动</button><button type="button" disabled={c.busy||!!c.pending} onClick={()=>run(()=>c.refresh())}>刷新复核依据</button></div>
    </>}
   </>}
  </>}
  {c.error&&<p role="alert">{c.error}</p>}{c.notice&&<p role="status">{c.notice}</p>}
 </section>;
}
