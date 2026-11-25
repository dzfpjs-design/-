import React,{useCallback,useEffect,useRef,useState} from 'react';

const FAILURE={access_or_source:'岗位权限或资料状态需要核对',database:'后台数据库暂不可用',internal:'后台处理遇到异常'};
const WORK={waiting:'等待整理',processing:'正在整理',needs_attention:'需要核对，系统将保留原资料',complete:'整理完成'};
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
  catch{if(alive.current===generation){setData(null);setError('暂时无法读取资料授权状态，请稍后刷新。');}}
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
  }catch{if(alive.current===generation){setData(null);setError('操作结果尚未确认，请刷新状态；再次操作会沿用原申请，不会新建重复申请。');}}
  finally{if(alive.current===generation)setBusy('');}
 };
 if(!data&&!error)return null;
 if(data?.enabled===false)return null;
 return <section className="media-form" aria-label="资料整理授权">
  <div className="same-advice-heading"><div><h4>资料整理授权</h4><p>授权指定岗位在离线后整理本任务已批准的资料。授权不会发送资料给云模型。</p></div>
   <button type="button" disabled={!!busy} onClick={refresh}>刷新状态</button></div>
  {error&&<p role="alert">{error}</p>}
  {data?.permit_committed&&<p role="alert">本任务已有一次派发许可。撤销此处授权只影响资料整理，不能保证撤回已经进入派发的请求。</p>}
  {data?.enabled&&data.items.length===0&&<p>当前没有可在这里授权的资料整理任务。</p>}
  {data?.items.map(item=><article className="media-form" key={item.job_id}>
   <h5>{item.task_title} · {item.source_label}</h5><p>当前状态：<strong>{STATUS[item.state]||'待核对'}</strong>{item.prepared?' · 资料整理已完成':''}</p>
   {item.expires_at&&<p>授权有效至：{new Date(item.expires_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）</p>}
   {item.state==='role_unavailable'&&<p>恢复岗位权限可能使未过期授权重新生效；如不希望继续，请由管理员撤销。</p>}
   {item.state==='authorized'&&<p>整理进度：{WORK[item.work_state]||'等待核对'}</p>}
   {item.state==='authorized'&&item.work_state==='needs_attention'&&<p>{FAILURE[item.failure_kind]||'后台处理需要核对'}。系统会沿原任务稍后重试；若持续失败，请联系管理员。</p>}
   {item.background_available?<p>后台整理服务运行中，可关闭页面。</p>:<p>授权可保存，后台自动处理尚未启用或暂未连接。</p>}
   {item.can_authorize&&<><label><input type="checkbox" checked={!!agreed[item.job_id]} onChange={e=>setAgreed({...agreed,[item.job_id]:e.target.checked})}/>我已核对授权范围：仅整理这份已批准资料。</label>{' '}
    <button type="button" disabled={!!busy||!agreed[item.job_id]} onClick={()=>execute(item,'grant')}>授权离线整理</button></>}
   {item.can_revoke&&<button type="button" disabled={!!busy} onClick={()=>execute(item,'revoke')}>撤销授权</button>}
  </article>)}
 </section>;
}
