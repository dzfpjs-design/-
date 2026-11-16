import React,{useEffect,useRef,useState} from 'react';
import InputCorrection from './InputCorrection';
async function json(...args){return (await import('./runtime-api')).json(...args);}
const stages={failed:'处理失败',received:'已保存，等待处理',queued:'等待处理',running:'正在处理',cancelled:'处理已取消'};
const time=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'时间未登记';
const text=value=>value===null?'未填写':typeof value==='object'?JSON.stringify(value):String(value);
export default function StateInputIssue({issue,instanceId,entityId,stateHash,formKey,canWrite,Btn,onReload}){
 const [detail,setDetail]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[reason,setReason]=useState(''),[pending,setPending]=useState(null),[cacheError,setCacheError]=useState('');
 const life=useRef(0),request=useRef(null);
 const key=formKey+':input-retry:'+issue.observation_id;
 useEffect(()=>{life.current++;setDetail(null);setError('');setBusy(false);request.current=null;setPending(null);setCacheError('');try{
  const stored=JSON.parse(localStorage.getItem(key)||'null');if(stored){const original=stored.request_id?stored:stored.pending;const draft=stored.reason;if(typeof draft!=='string'||(original&&(!original.request_id||!Number.isInteger(original.expected_version)||typeof original.reason!=='string')))throw Error('原重试请求无法恢复，请保留本地记录');request.current=original||null;setPending(original||null);setReason(original?.reason||draft);}
 }catch(e){setCacheError(e.message);}return()=>{life.current++;};},[key]);
 useEffect(()=>{life.current++;setDetail(null);setBusy(false);},[stateHash]);
 async function load(){const epoch=life.current;setBusy(true);setError('');try{
  const result=await json(`/workspace/instances/${instanceId}/objects/${entityId}/state-inputs/${issue.observation_id}?expected_state_hash=${stateHash}`);
  if(epoch===life.current)setDetail(result);
 }catch(e){if(epoch===life.current){setDetail(null);setError(e.message+' 请重新核对已保存状态。');}}
 finally{if(epoch===life.current)setBusy(false);}}
 function changeReason(value){try{localStorage.setItem(key,JSON.stringify({reason:value,pending:request.current}));setReason(value);}catch(e){setCacheError('重试理由未保存，已阻止提交：'+e.message);}}
 async function retry(){if(!canWrite||cacheError)return;const epoch=life.current;setBusy(true);setError('');try{
  if(!request.current){if(!detail?.retry_available||!reason.trim())throw Error('先核对原输入并填写重试理由');
   const original={request_id:crypto.randomUUID(),expected_version:detail.observation_version,reason:reason.trim()};
   try{localStorage.setItem(key,JSON.stringify({reason:original.reason,pending:original}));}catch(e){setCacheError('原请求未保存，未发送：'+e.message);throw e;}request.current=original;setPending(original);
  }
  await json(`/observations/${issue.observation_id}/retry`,{method:'POST',body:JSON.stringify(request.current)});
  if(epoch!==life.current)return;
  try{localStorage.removeItem(key);}catch(e){throw Error('服务器已接收；本地等待记录尚未清除，请恢复原请求核对');}
  request.current=null;setPending(null);setDetail(null);setError('原输入重试已保存。后台结果尚未完成，请重新核对状态；原值仍有问题时会再次失败。');
  onReload();
 }catch(e){if(epoch===life.current)setError(e.message+' 原请求保留，恢复时沿用同一编号。');}
 finally{if(epoch===life.current)setBusy(false);}}
 return <article className="state-input-issue">
 <h5>{issue.source_name} · {stages[issue.stage]||'处理状态待核'}</h5>
 <p>{issue.correction_observation_id?'原失败已关联独立成功更正，原输入与失败历史仍保留。':issue.message}</p><p>登记时间：{time(issue.reported_at)}（北京时间，来源待核）。已尝试{issue.attempts}次。</p>
 {!issue.input_integrity_verified&&<p role="alert">原输入未通过完整性核对，不能作为可信状态依据。</p>}
 <Btn disabled={busy} onClick={load}>{busy?'正在核对…':'核对未完成输入与回执'}</Btn>
 {cacheError&&<p role="alert">{cacheError} 已阻止新重试。</p>}{error&&<p role="status">{error}</p>}
 {detail&&<>
 <h5>原登记内容</h5>{detail.original_attributes?<dl>{Object.entries(detail.original_attributes).map(([k,v])=><React.Fragment key={k}><dt>{k}</dt><dd>{text(v)}</dd></React.Fragment>)}</dl>:<p>原输入未通过核对，已收起原值。</p>}
 <h5>处理尝试</h5>{detail.failure_history.length?<ol>{detail.failure_history.map((receipt,i)=><li key={i}>第{receipt.attempt}次：{receipt.kind} · {time(receipt.at)}。{receipt.delivered?'失败通知已送达数据管理智能体':'失败通知等待送达'}。{receipt.has_extraction_receipt?'已保存提取回执':'未保存提取回执'}。</li>)}</ol>:<p>尚无失败回执，原输入仍在后台处理。</p>}
 {!detail.retry_receipts_verified&&<p role="alert">原重试回执未通过核对，已禁止重试。</p>}
 <InputCorrection {...{detail,instanceId,entityId,formKey,canWrite,Btn,onReload}}/>
 <p>原值保持不变；发现空值或错误资料时，请补充更正登记。重试不会修正原值，也不代表处理成功。</p>
 {canWrite&&detail.retry_available&&!pending&&<label>原输入重试理由<input value={reason} maxLength={2000} onChange={e=>changeReason(e.target.value)} /></label>}
 {canWrite&&(pending||detail.retry_available)&&<Btn disabled={busy||!!cacheError||(!pending&&!reason.trim())} onClick={retry}>{pending?'恢复原输入重试请求':'保存原输入重试'}</Btn>}
 {!detail.retry_available&&!pending&&<p>{detail.correction_result?'旧输入保留在失败历史，不再重试；新值来自独立更正记录。':detail.correction?.saved?'已有独立更正记录，请先核对其处理和送达结果。':`当前不满足本地原输入重试条件，或已达到${detail.retry_limit}次上限。原输入和回执仍保留。`}</p>}
 </>}
 {pending&&!detail&&canWrite&&<><p>原请求等待核实，理由与编号已保留，不能替换条件。</p><Btn disabled={busy||!!cacheError} onClick={retry}>恢复原输入重试请求</Btn></>}
 </article>;
}
