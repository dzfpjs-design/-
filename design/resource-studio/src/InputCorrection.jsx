import React,{useEffect,useRef,useState} from 'react';
async function json(...args){return (await import('./runtime-api')).json(...args);}
export default function InputCorrection({detail,instanceId,entityId,formKey,canWrite,Btn,onReload}){
 const key=formKey+':correction:'+detail.observation_id;
 const [values,setValues]=useState({}),[reason,setReason]=useState(''),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[cacheError,setCacheError]=useState('');
 const request=useRef(null),life=useRef(0);
 useEffect(()=>{life.current++;setError('');setCacheError('');setBusy(false);request.current=null;setPending(null);try{
  const draft=JSON.parse(localStorage.getItem(key)||'null');if(draft){if(typeof draft.reason!=='string'||!draft.values||typeof draft.values!=='object'||(draft.pending&&(!draft.pending.request_id||!Number.isInteger(draft.pending.expected_version))))throw Error('原更正草稿无法恢复，请保留本地记录');setValues(draft.values);setReason(draft.reason);request.current=draft.pending||null;setPending(draft.pending||null);}
  else{setValues(Object.fromEntries((detail.correction?.fields||[]).map(f=>[f,detail.original_attributes?.[f]==null?'':String(detail.original_attributes[f])])));setReason('');}
 }catch(e){setCacheError(e.message);}return()=>{life.current++;};},[key]);
 function save(nextValues,nextReason){try{localStorage.setItem(key,JSON.stringify({values:nextValues,reason:nextReason,pending:request.current}));setValues(nextValues);setReason(nextReason);}catch(e){setCacheError('更正草稿未保存，已阻止提交：'+e.message);}}
 async function submit(){if(!canWrite||cacheError)return;const epoch=life.current;setBusy(true);setError('');try{
  if(!request.current){if(!detail.correction?.available||!reason.trim())throw Error('先填写更正值与理由');
   const parsed={};for(const field of detail.correction.fields){const raw=String(values[field]??'').trim();if(!raw||!Number.isFinite(Number(raw)))throw Error('请为“'+field+'”填写有效数值');parsed[field]=Number(raw);}
   const original={request_id:crypto.randomUUID(),expected_version:detail.observation_version,reason:reason.trim(),values:parsed};
   try{localStorage.setItem(key,JSON.stringify({values,reason,pending:original}));}catch(e){setCacheError('原更正请求未保存，未发送：'+e.message);throw e;}request.current=original;setPending(original);
  }
  await json(`/workspace/instances/${instanceId}/objects/${entityId}/state-inputs/${detail.observation_id}/correct`,{method:'POST',body:JSON.stringify(request.current)});
  if(epoch!==life.current)return;
  localStorage.removeItem(key);request.current=null;setPending(null);setError('更正已另存。旧输入保持不变；后台成功处理且数据管理收到后才采用新结果。');onReload();
 }catch(e){if(epoch===life.current)setError(e.message+' 待核请求沿同一编号恢复，不覆盖旧登记。');}
 finally{if(epoch===life.current)setBusy(false);}}
 const corrected=detail.correction_result,saved=detail.correction?.saved;
 return <section className="input-correction">
 <h5>另存更正登记</h5><p>原输入、原登记时间和失败回执保留。更正只改变本次填写的登记值，不代表新的现场观测或异常已排除。</p>
 {corrected&&<p role="status">独立更正已成功处理并送达数据管理智能体；旧失败留在历史中。请在对象状态核对新值。</p>}
 {saved&&!corrected&&<p role="status">已保存独立更正，{saved.review_rejected?'更正被人工拒绝，原缺口保留，可从原失败另存更正':saved.status==='failed'?'处理失败，原缺口仍保留':'处理或回执核对尚未完成，原缺口不能关闭'}。</p>}
 {cacheError&&<p role="alert">{cacheError}</p>}{error&&<p role="status">{error}</p>}
 {canWrite&&(detail.correction?.available||pending)&&<>
 <fieldset disabled={busy||!!pending||!!cacheError} style={{border:0,padding:0}}>
 {(detail.correction?.fields?.length?detail.correction.fields:Object.keys(values)).map(f=><label key={f}>更正值：{f}<input aria-label={'更正值 '+f} type="number" step="any" value={values[f]??''} onChange={e=>save({...values,[f]:e.target.value},reason)}/></label>)}
 <label>另存更正理由<input value={reason} maxLength={2000} onChange={e=>save(values,e.target.value)}/></label>
 </fieldset>
 {pending&&<p>原更正请求已保留，结果等待核实；不能替换原值、理由或编号。</p>}
 <Btn disabled={busy||!!cacheError||(!pending&&!reason.trim())} onClick={submit}>{pending?'恢复原更正请求':'另存更正并处理'}</Btn>
 </>}
 {!detail.correction?.available&&!pending&&!corrected&&!saved&&<p>{detail.correction?.message||'此输入暂不支持在这里另存更正，请核查原入口。'}</p>}
 </section>;
}
