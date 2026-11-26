import React,{useCallback,useEffect,useRef,useState} from 'react';

const STATES={
 ready:'已找到完整首答，可以恢复保存',
 needs_settlement:'首答已保存，等待核对用量',
 settled:'首答和用量已保存',
 needs_attention:'首答不完整或原记录需要核对',
};

export default function AdviceResponseRecoveryV2({account,task,requestJSON,onRecovered}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState('');
 const alive=useRef(0);
 const scope=JSON.stringify([account?.workspace_id,account?.user_id,task?.id]);
 const base='/workspace/tasks/'+encodeURIComponent(task?.id||'')+'/business-advice/response-recovery';
 const refresh=useCallback(async()=>{
  const generation=alive.current;setError('');
  try{const result=await requestJSON(base);if(alive.current===generation)setData(result);}
  catch{if(alive.current===generation){setData(null);setError('暂时无法读取结果保存状态，请稍后刷新。');}}
 },[base,requestJSON,scope]);
 useEffect(()=>{alive.current++;setData(null);if(account?.role==='admin')refresh();return()=>{alive.current++;};},[scope,account?.role,refresh]);
 const recover=async item=>{
  if(busy)return;const generation=alive.current;setBusy(item.permit_id);setError('');
  try{
   await requestJSON(base+'/'+encodeURIComponent(item.permit_id)+'/recover',{method:'POST'});
   if(alive.current===generation){await refresh();if(onRecovered)onRecovered();}
  }catch{
   if(alive.current===generation){setData(null);setError('恢复结果尚未确认，请刷新状态。系统不会再次发送原请求。');}
  }finally{if(alive.current===generation)setBusy('');}
 };
 if(account?.role!=='admin'||(!data&&!error)||data?.enabled===false)return null;
 return <section className="media-form" aria-label="业务建议结果恢复">
  <div className="same-advice-heading"><div><h4>业务建议结果恢复</h4>
   <p>核对已经接收的模型首答及用量记录，恢复不会再次发送请求。</p></div>
   <button type="button" disabled={!!busy} onClick={refresh}>刷新状态</button></div>
  {error&&<p role="alert">{error}</p>}
  {data?.items.length===0&&<p>当前业务没有需要核对的原请求。</p>}
  {data?.items.map(item=><article className="media-form" key={item.permit_id}>
   <p>原请求时间：{item.recorded_at?new Date(item.recorded_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'待核对'}（北京时间）</p>
   <p>核对依据：{item.state==='needs_attention'?'原请求记录或首答完整性尚未确认。':
    item.state==='ready'?'已核对原请求和一次性派发记录，找到完整加密首答。':'已核对原请求及已保存的首答。'}</p>
   <p>当前状态：<strong>{STATES[item.state]||'原记录需要核对'}</strong></p>
   {item.state==='needs_attention'&&<p>系统无法确认完整首答，请联系管理员核对原记录；不要重复发送。</p>}
   {(item.state==='ready'||item.state==='needs_settlement')&&<button type="button" disabled={!!busy}
    onClick={()=>recover(item)}>{busy===item.permit_id?'正在恢复…':'恢复结果并核对用量'}</button>}
   {item.state==='settled'&&<p>结果已保存，业务内容仍需按原流程核对。</p>}
  </article>)}
 </section>;
}
