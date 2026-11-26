import React,{useCallback,useEffect,useRef,useState} from 'react';
import './advice-user-compact.css';

const FAILURE={access_or_source:'请核对权限或资料',database:'服务暂不可用',internal:'整理遇到问题'};
const WORK={waiting:'等待整理',processing:'正在整理',needs_attention:'需要核对',complete:'整理完成'};
const STATUS={not_authorized:'尚未授权',authorized:'授权有效',revoked:'已撤销',expired:'授权已到期',role_unavailable:'已暂停：原资料岗位权限不可用'};

// The request identifier is saved before network I/O so a lost reply never
// causes a new grant/revoke to be invented on refresh or retry.
function durableRequest(storage,key,reason){
 const old=storage.getItem(key);
 if(old){const value=JSON.parse(old);if(!value?.request_id||value.reason!==reason)throw Error('待核对的原申请不一致');return value;}
 if(!globalThis.crypto?.randomUUID)throw Error('当前浏览器不能安全生成申请编号');
 const value={request_id:globalThis.crypto.randomUUID(),reason};storage.setItem(key,JSON.stringify(value));
 if(storage.getItem(key)!==JSON.stringify(value))throw Error('申请编号未能保存');
 return value;
}

export default function OfflineDocumentDelegationV2({account,task,requestJSON,storage=globalThis.sessionStorage}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(''),[agreed,setAgreed]=useState({});
 const alive=useRef(0);
 const scope=JSON.stringify([account?.workspace_id,account?.user_id,task?.id]);
 const base='/workspace/tasks/'+encodeURIComponent(task?.id||'')+'/business-advice/offline-delegations';
 const refresh=useCallback(async()=>{
  const generation=alive.current;setError('');
  try{const result=await requestJSON(base);if(alive.current===generation){
   for(const item of result.items||[]){for(const [action,field] of [['grant','grant_request_id'],['revoke','revoke_request_id']]){
    const key='ga-offline-document-v2:'+scope+':'+item.job_id+':'+action;
    try{const pending=JSON.parse(storage.getItem(key)||'null');if(pending?.request_id&&pending.request_id===item[field])storage.removeItem(key);}catch{}
   }}
   setData(result);}}
  catch{if(alive.current===generation){setData(null);setError('暂时无法读取授权状态，请刷新重试。');}}
 },[base,requestJSON,scope,storage]);
 useEffect(()=>{alive.current++;setData(null);setAgreed({});refresh();return()=>{alive.current++;};},[scope,refresh]);
 const execute=async(item,action)=>{
  if(busy)return;const generation=alive.current,reason=action==='revoke'?'资料岗位撤销授权':'';
  const key='ga-offline-document-v2:'+scope+':'+item.job_id+':'+action;
  setBusy(item.job_id);setError('');
  try{
   const draft=durableRequest(storage,key,reason);
   const suffix=action==='grant'?'/'+encodeURIComponent(item.job_id)+'/grant':
    '/'+encodeURIComponent(item.job_id)+'/grants/'+encodeURIComponent(item.grant_id)+'/revoke';
   const body=action==='grant'?{request_id:draft.request_id}:draft;
   await requestJSON(base+suffix,{method:'POST',body:JSON.stringify(body)});
   storage.removeItem(key);if(alive.current===generation)await refresh();
  }catch{if(alive.current===generation){setData(null);setError('操作结果尚未确认，请刷新查看。');}}
  finally{if(alive.current===generation)setBusy('');}
 };
 if(!data&&!error)return null;
 if(data?.enabled===false)return null;
 if(!error&&data?.items.length===0)return null;
 return <section className="media-form advice-user-panel" aria-label="资料整理授权">
  <div className="advice-user-heading"><h4>资料整理授权</h4>
   <button className="btn" type="button" disabled={!!busy} onClick={refresh}>刷新</button></div>
  {error&&<p role="alert">{error}</p>}
  {data?.items.map(item=><article className="advice-user-item" key={item.job_id}>
   <h5>{item.task_title} · {item.source_label}</h5><p>当前状态：<strong>{STATUS[item.state]||'待核对'}</strong>{item.prepared?' · 资料整理已完成':''}</p>
   {item.expires_at&&<p>授权有效至：{new Date(item.expires_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）</p>}
   {item.state==='role_unavailable'&&<p>权限恢复后，未过期的授权可能重新生效。</p>}
   {item.state==='authorized'&&<p>整理进度：{WORK[item.work_state]||'等待核对'}</p>}
   {item.state==='authorized'&&item.work_state==='needs_attention'&&<p>{FAILURE[item.failure_kind]||'整理需要核对'}，请联系管理员。</p>}
   {!item.prepared&&!item.background_available&&(item.can_authorize||item.state==='authorized')&&<p role="note">暂不能自动整理。</p>}
   {data?.permit_committed&&item.can_revoke&&<p role="alert">已提交的分析请求可能无法撤回。</p>}
   {item.can_authorize&&<><label className="advice-user-choice"><input type="checkbox" checked={!!agreed[item.job_id]} onChange={e=>setAgreed({...agreed,[item.job_id]:e.target.checked})}/>同意离线整理已批准资料</label>
    <button className="btn primary" type="button" disabled={!!busy||!agreed[item.job_id]} onClick={()=>execute(item,'grant')}>确认授权</button></>}
   {item.can_revoke&&<button className="btn" type="button" disabled={!!busy} onClick={()=>execute(item,'revoke')}>撤销授权</button>}
  </article>)}
 </section>;
}
