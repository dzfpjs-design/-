import React,{useEffect,useRef,useState} from 'react';

// Read-only presentation of the last committed snapshot. Never participates
// in the controls or checks that authorize a workflow action.
export default function GoalBudgetDisplayCandidate({taskId,scopeKey,request,Btn,onDenied}) {
 const [result,setResult]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[hidden,setHidden]=useState(false),[refresh,setRefresh]=useState(0);
 const requestRef=useRef(request),deniedRef=useRef(onDenied);requestRef.current=request;deniedRef.current=onDenied;
 useEffect(()=>{
  const abort=new AbortController();let active=true;
  setResult(null);setError('');setHidden(false);setLoading(true);
  (async()=>{
   try {
    const value=await requestRef.current('/workspace/tasks/'+encodeURIComponent(taskId)+'/goal/budget-display',{cache:'no-store',signal:abort.signal});
    if(!active)return;
    if(!value||value.task_id!==taskId||value.basis!=='saved_snapshot'||
       !Number.isSafeInteger(value.calls)||value.calls<0||!Number.isSafeInteger(value.steps)||value.steps<0||
       typeof value.observed_at!=='string'||!Number.isFinite(Date.parse(value.observed_at)))throw Error('计数结果无法核对');
    setResult(value);
   } catch(e) {
    if(!active||e?.name==='AbortError')return;
    setResult(null);
    if(e?.status===404){setHidden(true);return;}
    if([401,403].includes(e?.status)){deniedRef.current?.(e);setError('账号或工作区权限已变化，计数已隐藏；原草稿保留。');return;}
    setError('暂时无法核实目标计数，已隐藏上次结果；请稍后重试。');
   } finally {if(active)setLoading(false);}
  })();
  return()=>{active=false;abort.abort();};
 },[scopeKey,taskId,refresh]);
 if(hidden)return null;
 return <section className="media-form" aria-label="目标调用与步骤计数">
  <div className="section-title"><h4>目标调用与步骤计数</h4><Btn disabled={loading} onClick={()=>setRefresh(x=>x+1)}>刷新计数</Btn></div>
  {loading?<p role="status">正在核对已保存的目标记录…</p>:error?<p role="alert">{error}</p>:result&&<>
   <p>任务工具调用计数：{result.calls} 次 · 任务步骤计数：{result.steps} 步</p>
   <p>计数包括已执行、已预留和结果待核的工作，与模型费用无关。</p>
   <p>读取于 {new Date(result.observed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}。仅展示已保存的记录，未保存的编辑不计入；处理时仍会重新核对权限和最新状态。</p>
  </>}
 </section>;
}
