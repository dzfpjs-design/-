import React,{useEffect,useRef,useState} from 'react';
import FormalContinuationWorkflowV345 from './FormalContinuationWorkflowV345.jsx';

function asTime(value){
 try {const date=new Date(value);if(!Number.isFinite(date.getTime()))return '时间待核对';return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'short'}).format(date);}
 catch{return '时间待核对';}
}
function valid(view){
 if(!view||view.schema!=='formal-continuation-context-v344'||typeof view.available!=='boolean'||view.can_start!==false||view.task_completed!==false||view.cloud_calls!==0||view.device_actions!==0)return false;
 if(!view.available)return typeof view.reason==='string';
 return Number.isInteger(view.advice_generation)&&Number.isInteger(view.current_generation)&&view.review&&typeof view.review.role==='string'&&typeof view.review.available==='boolean'&&Array.isArray(view.steps)&&Array.isArray(view.results)&&
  view.steps.every(s=>s&&typeof s.name==='string'&&typeof s.role==='string'&&typeof s.output==='string'&&typeof s.available==='boolean')&&
  view.results.every(r=>r&&Number.isInteger(r.data_generation)&&Number.isInteger(r.record_count)&&typeof r.current_data==='boolean'&&typeof r.simulation_only==='boolean'&&typeof r.test_only==='boolean');
}
export default function FormalContinuationContextV344({account,task,advicePlan,requestJSON,storage,tabStorage,locks,onRegisterGuard,Btn="button"}){
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[value,setValue]=useState(null),[error,setError]=useState('');
 const epoch=useRef(0),formalGuard=useRef(null);useEffect(()=>()=>{epoch.current++;},[]);
 function registerGuard(fn,previous){if(fn||!previous||formalGuard.current===previous)formalGuard.current=fn;onRegisterGuard?.(fn,previous);}
 const identity=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,task?.id,advicePlan?.plan_id]);
 const current=useRef(identity);current.current=identity;
 useEffect(()=>{epoch.current++;setOpen(false);setBusy(false);setValue(null);setError('');},[identity]);
 const displayed=value?.scope===identity?value.data:null;
 async function read(){
  if(busy)return;const n=++epoch.current,scope=identity;setBusy(true);setValue(null);setError('');
  const same=me=>me?.user_id===account?.user_id&&me?.workspace_id===account?.workspace_id&&me?.role===account?.role;
  try{
   if(!same(await requestJSON('/me')))throw Error('账号或工作区已变化，请重新打开任务。');
   const response=await requestJSON('/workspace/tasks/'+encodeURIComponent(task.id)+'/business-advice/preparations/'+encodeURIComponent(advicePlan.plan_id)+'/formal-continuation/context');
   if(!same(await requestJSON('/me')))throw Error('账号或工作区已变化，请重新打开任务。');
   if(!valid(response))throw Error('原流程依据未能完整核对，请稍后重试。');
   if(epoch.current===n&&current.current===scope)setValue({scope,data:response});
  }catch(e){if(epoch.current===n&&current.current===scope)setError(e.message||'读取失败，请重试。');}
  finally{if(epoch.current===n&&current.current===scope)setBusy(false);}
 }
 return <section className="media-form" aria-label="原业务流程后续步骤">
  <h5>原业务流程后续步骤</h5>
  <p>核对原建议、最新资料与尚未办理的步骤；此处不会启动流程或操作设备。</p>
  <Btn type="button" disabled={busy} aria-expanded={open} onClick={()=>{if(open){if(formalGuard.current&&!formalGuard.current()){setError('办理输入尚未完整保存，请先重试保存。');return;}epoch.current++;setBusy(false);setOpen(false);}else{setOpen(true);read();}}}>{open?'收起':'查看后续步骤'}</Btn>
  {open&&<div aria-live="polite">{busy&&<p>正在核对原建议和办理记录，请稍候…</p>}
   {error&&<><p role="alert">{error}</p><Btn type="button" disabled={busy} onClick={read}>重试核对</Btn></>}
   {displayed&&!displayed.available&&<p>{displayed.reason}</p>}
   {displayed?.available&&<>
    <p>原建议依据第 {displayed.advice_generation} 版资料（{asTime(displayed.advice_data_as_of)}）；目前资料为第 {displayed.current_generation} 版。
     {displayed.different_generation&&' 新资料没有自动进入旧建议，请分别核对。'}</p>
    {displayed.results.length?<ul>{displayed.results.map(r=><li key={r.execution_id}>
     已复核的办理：{r.field?.label||'数值'}，{r.record_count} 条记录，依据第 {r.data_generation} 版资料。
     {r.current_data?' 与当前资料版本一致。':' 资料版本已变化，需重新核对。'}
     {(r.simulation_only||r.test_only)&&' 软件模拟技术结果，不能作为现场事实或真实业务签批。'}
    </li>)}</ul>:<p>尚无已采纳的关联办理结果。</p>}
    <p>后续还需由 {displayed.review.role}{displayed.review.person&&`（账号：${displayed.review.person}）`} 核对建议{!displayed.review.available&&'（责任人当前不可用，请核对）'}，并完成以下业务步骤：</p>
    <ol>{displayed.steps.map((s,i)=><li key={i}>{s.name} · {s.role}{s.person&&`（账号：${s.person}）`} · 产出：{s.output}{!s.available&&'（责任人当前不可用，请核对）'}</li>)}</ol>
    <p>{displayed.reason}</p>
   </>}
   <FormalContinuationWorkflowV345 account={account} task={task} advicePlan={advicePlan} requestJSON={requestJSON} storage={storage} tabStorage={tabStorage} locks={locks} Btn={Btn} onRegisterGuard={registerGuard}/>
  </div>}
 </section>;
}
