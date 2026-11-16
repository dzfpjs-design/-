import React,{useEffect,useState} from 'react';

const labels={
 current:'上次核对时，原步骤引用的评价仍在有效期内；这不代表步骤已经执行或已获模型发送授权。',
 superseded:'上次核对时，任务已有更新的评价，原步骤仍使用之前的资料；原流程尚未因此继续。',
 expired:'上次核对时，原步骤引用的评价已过期，需要重新核对资料；原流程状态保持不变。',
 unknown:'评价资料状态暂时无法核对；原流程状态保持不变。',
};

function readableTime(value){
 if(typeof value!=='string'||!value)return null;
 const date=new Date(value);if(!Number.isFinite(date.getTime()))return null;
 return date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
}

export default function GraphReasoningEvaluationStatusV335({api,taskId,activationId,scopeKey,onDenied,canRetire=false,onRetire}){
 const key=scopeKey+':'+activationId;
 const [result,setResult]=useState({key:null,status:'loading'});
 const [retry,setRetry]=useState(0);
 const [confirming,setConfirming]=useState(false);
 useEffect(()=>{
  let active=true;const controller=new AbortController();
  setResult({key,status:'loading'});
  const timer=setTimeout(()=>{if(active){active=false;controller.abort();setResult({key,status:'unknown'});}},90000);
  Promise.resolve().then(()=>api('/workspace/tasks/'+encodeURIComponent(taskId)+'/graph/reasoning-evaluation-status',{signal:controller.signal})).then(value=>{
   if(!active)return;
   if(value?.schema!=='reasoning-evaluation-status-v335'||!['current','superseded','expired','unknown','not_applicable'].includes(value.status)
      ||(!['unknown','not_applicable'].includes(value.status)&&value.activation_id!==activationId)){
    setResult({key,status:'unknown'});return;
   }
   setResult({key,status:value.status,checkedAt:readableTime(value.checked_at),evaluatedAt:readableTime(value.evaluated_at),
    currentEvaluatedAt:readableTime(value.current_evaluated_at),currentFreshness:value.current_freshness});
  }).catch(error=>{
   if(!active)return;
   if(error?.status===404){setResult({key,status:'not_applicable'});return;}
   if(error?.status===403||error?.status===401){setResult({key,status:'denied'});onDenied?.();return;}
   setResult({key,status:'unknown'});
  }).finally(()=>clearTimeout(timer));
  return()=>{active=false;clearTimeout(timer);controller.abort();};
 },[api,taskId,activationId,key,retry]);
 const visible=result.key===key?result:{status:'loading'};
 if(visible.status==='not_applicable')return null;
 if(visible.status==='loading')return <p role="status">正在核对本步骤使用的评价资料…</p>;
 if(visible.status==='denied')return <p role="alert">当前账号无法查看本步骤的评价资料状态。</p>;
 const text=labels[visible.status]||labels.unknown;
 return <aside className="info" aria-label="本步骤的评价资料状态">
  <p role={visible.status==='current'?'status':'alert'}>{text}</p>
  {visible.checkedAt&&<p>核对时间：{visible.checkedAt}（北京时间）</p>}
  {visible.evaluatedAt&&<p>原评价时间：{visible.evaluatedAt}（北京时间）</p>}
  {visible.status==='superseded'&&visible.currentEvaluatedAt&&<p>更新评价时间：{visible.currentEvaluatedAt}（北京时间）{visible.currentFreshness==='stale'?'；更新评价也已过期，需重新核对。':''}</p>}
  <button type="button" onClick={()=>setRetry(value=>value+1)}>重新核对评价资料</button>
  {visible.status==='superseded'&&canRetire&&!confirming&&<button type="button" onClick={()=>setConfirming(true)}>停止旧步骤，准备后续处理</button>}
  {visible.status==='superseded'&&canRetire&&confirming&&<div role="group" aria-label="确认停止旧步骤">
   <p>只停止尚未执行的旧资料步骤，保留原流程和所有记录。此操作不会生成新建议或完成任务；之后请在业务建议中核对最新资料。</p>
   <button type="button" onClick={()=>{setConfirming(false);onRetire?.();}}>确认停止旧步骤</button>
   <button type="button" onClick={()=>setConfirming(false)}>返回</button>
  </div>}
 </aside>;
}
