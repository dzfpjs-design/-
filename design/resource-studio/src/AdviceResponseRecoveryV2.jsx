import React,{useCallback,useEffect,useRef,useState} from 'react';
import './advice-user-compact.css';

const STATES={
 ready:'建议待恢复',
 needs_settlement:'建议待完成',
 settled:'建议已保存 · 用量记录待核',
 settled_usage_unknown:'建议已保存 · 实际用量待核',
 failed:'建议未生成 · 用量记录待核',
 failed_usage_unknown:'建议未生成 · 实际用量待核',
 needs_attention:'需要核对',
};

export default function AdviceResponseRecoveryV2({account,task,requestJSON,onRecovered}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState('');
 const alive=useRef(0);
 const scope=JSON.stringify([account?.workspace_id,account?.user_id,task?.id]);
 const base='/workspace/tasks/'+encodeURIComponent(task?.id||'')+'/business-advice/response-recovery';
 const refresh=useCallback(async()=>{
  const generation=alive.current;setError('');
  try{const result=await requestJSON(base);if(alive.current===generation)setData(result);}
  catch{if(alive.current===generation){setData(null);setError('暂时无法读取建议状态，请刷新重试。');}}
 },[base,requestJSON,scope]);
 useEffect(()=>{alive.current++;setData(null);if(account?.role==='admin')refresh();return()=>{alive.current++;};},[scope,account?.role,refresh]);
 const recover=async item=>{
  if(busy)return;const generation=alive.current;setBusy(item.permit_id);setError('');
  try{
   await requestJSON(base+'/'+encodeURIComponent(item.permit_id)+'/recover',{method:'POST'});
   if(alive.current===generation){await refresh();if(onRecovered)onRecovered();}
  }catch{
   if(alive.current===generation){setData(null);setError('恢复结果尚未确认，请刷新查看，勿重复提交。');}
  }finally{if(alive.current===generation)setBusy('');}
 };
 const loadMore=async()=>{
  if(busy||!data?.next_cursor)return;const generation=alive.current,cursor=data.next_cursor;setBusy('page');setError('');
  try{const page=await requestJSON(base+'?cursor='+encodeURIComponent(cursor));
   if(alive.current===generation)setData(old=>old?.enabled&&old.next_cursor===cursor?({...page,items:[...old.items,...page.items]}):old);
  }catch{if(alive.current===generation)setError('加载更多记录失败，请重试或刷新。');}
  finally{if(alive.current===generation)setBusy('');}
 };
 if(account?.role!=='admin'||(!data&&!error)||data?.enabled===false)return null;
 if(!error&&data?.items.length===0&&!data?.next_cursor)return null;
 return <section className="media-form advice-user-panel" aria-label="建议状态">
  <div className="advice-user-heading"><h4>建议状态</h4>
   <button className="btn" type="button" disabled={!!busy} onClick={refresh}>刷新</button></div>
  {error&&<p role="alert">{error}</p>}
  {data?.items.map(item=><article className="advice-user-item" key={item.permit_id}>
   <p>{item.recorded_at?new Date(item.recorded_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'时间待核对'} · <strong>{STATES[item.state]||'需要核对'}</strong></p>
   {item.state==='needs_attention'&&<p>请联系管理员核对，勿重复提交。</p>}
   {(item.state==='ready'||item.state==='needs_settlement')&&<button className="btn primary" type="button" disabled={!!busy}
    onClick={()=>recover(item)}>{busy===item.permit_id?'正在恢复…':'恢复建议'}</button>}
  </article>)}
  {data?.next_cursor&&<button className="btn" type="button" disabled={!!busy} onClick={loadMore}>{busy==='page'?'正在加载…':'查看更多'}</button>}
 </section>;
}
