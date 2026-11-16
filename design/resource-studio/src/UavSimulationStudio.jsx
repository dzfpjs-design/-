import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api.js';
import {acquireDraft} from './uav-simulation-draft.mjs';

const base='/uav-simulation/missions';
const labels={blocked:'不可起飞',awaiting_approval:'待确认',dispatch_unknown:'派发结果待核对',flying:'模拟飞行中',
  low_battery:'电量不足',connection_lost:'已失联',needs_recovery:'需处理异常',return_unknown:'返航结果待核对',
  returning:'模拟返航中',returned:'已模拟返航',completed:'模拟任务完成'};

export default function UavSimulationStudio({account,request=json,_pollIntervalMs=10000}){
  // A change of account or workspace tears down the old editing lease and UI.
  return <Studio key={`${account?.workspace_id}:${account?.user_id}`} account={account} request={request} pollIntervalMs={_pollIntervalMs}/>;
}
function Studio({account,request,pollIntervalMs}){
  const [rows,setRows]=useState([]),[selected,setSelected]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[draft,setDraft]=useState(null),[lockEpoch,setLockEpoch]=useState(0);
  const [pollError,setPollError]=useState('');
  const lease=useRef(null),alive=useRef(true),inFlight=useRef(false),currentDraft=useRef(null);
  const cache=useRef(new Map()),selectedRef=useRef(null),listTicket=useRef(0),appliedTicket=useRef(0);
  const key=`uav-simulation:${account?.workspace_id}:${account?.user_id}:draft-v1`,admin=account?.role==='admin';
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
  useEffect(()=>{let active=true,owned=null;
    acquireDraft({storage:localStorage,locks:navigator.locks,key}).then(value=>{
      if(!active){value.release();return}owned=value;lease.current=value;currentDraft.current=value.value;setDraft(value.value);
    }).catch(e=>{if(active)setError(e.message)});
    return()=>{active=false;if(owned)owned.release();lease.current=null;currentDraft.current=null};
  },[key,lockEpoch]);
  useEffect(()=>{let active=true,timer,deadline,controller;
    async function poll(){
      controller=new AbortController();deadline=setTimeout(()=>controller.abort(),10000);
      try{await reload(controller.signal);if(active&&alive.current)setPollError('')}
      catch{if(active&&alive.current)setPollError('模拟任务状态更新失败，列表可能不完整；稍后将自动重试。')}
      finally{clearTimeout(deadline);if(active)timer=setTimeout(poll,pollIntervalMs)}
    }
    poll();return()=>{active=false;clearTimeout(timer);clearTimeout(deadline);controller?.abort()};
  },[request,pollIntervalMs]);
  function persist(value){if(!lease.current)throw Error('未取得草稿编辑权。');lease.current.save(value);currentDraft.current=value;setDraft(value)}
  function edit(name,value){try{persist({...currentDraft.current,fields:{...currentDraft.current.fields,[name]:value}});setError('')}catch(e){setError(e.message)}}
  function remember(row){
    if(!row||typeof row.id!=='string'||(row.version!==undefined&&(!Number.isSafeInteger(row.version)||row.version<0)))throw Error('任务状态版本无法核对，请刷新后重试。');
    const previous=cache.current.get(row.id),version=row.version??0;
    if(previous&&(previous.version??0)>=version)return previous;
    cache.current.set(row.id,row);return row;
  }
  function choose(row){const current=remember(row);selectedRef.current=current;setSelected(current);setRows(old=>[current,...old.filter(x=>x.id!==current.id)].slice(0,100))}
  async function reload(signal){
    const options=signal?{signal}:undefined;
    const ticket=++listTicket.current,value=await request(base,options);
    if(!Array.isArray(value.missions))throw Error('任务清单无法核对。');
    if(!alive.current)return;
    // Keep every row's highest known version even when requests complete out of order.
    const next=value.missions.map(remember);
    if(ticket>=appliedTicket.current){appliedTicket.current=ticket;setRows(next)}
    const current=selectedRef.current;
    if(current){
      const inList=next.find(row=>row.id===current.id);
      const latest=inList||remember(await request(`${base}/${current.id}`,options));
      if(alive.current&&selectedRef.current?.id===current.id){selectedRef.current=latest;setSelected(latest)}
    }
  }
  async function action(fn){if(inFlight.current)return;inFlight.current=true;setBusy(true);setError('');try{await fn()}catch(e){if(alive.current)setError(e.message||'操作未完成，请核对原任务。')}finally{inFlight.current=false;if(alive.current)setBusy(false)}}
  async function prepare(){return action(async()=>{
    let record=currentDraft.current;if(!record||!lease.current)throw Error('未取得草稿编辑权。');
    let body=record.pending;
    if(!body){const f=record.fields,values=[f.startLon,f.startLat,f.endLon,f.endLat];
      if(!f.event.trim()||f.event.trim().length>128||values.some(v=>!v.trim()||!Number.isFinite(Number(v)))||Math.abs(Number(f.startLon))>180||Math.abs(Number(f.endLon))>180||Math.abs(Number(f.startLat))>90||Math.abs(Number(f.endLat))>90)throw Error('请填写事件和有效经纬度（经度±180、纬度±90）。');
      body={request_id:crypto.randomUUID(),event_id:f.event.trim(),start:[Number(f.startLon),Number(f.startLat)],destination:[Number(f.endLon),Number(f.endLat)],speed_m_s:10,observation_seconds:60};
      record={...record,pending:body};persist(record);
    }
    let result;
    try{result=await request(base,{method:'POST',body:JSON.stringify(body)})}
    catch(e){if(alive.current&&(e.status===422||e.status===403))persist({...record,pending:null});throw e}
    if(!alive.current)return;
    if(result.id!==body.request_id)throw Error('返回的任务与原请求不一致，请检查任务列表。');
    persist({...record,pending:null});choose(result);await reload();
  })}
  async function open(row){return action(async()=>{const result=await request(`${base}/${row.id}`);if(alive.current)choose(result)})}
  async function command(endpoint,body){return action(async()=>{
    if(!selected)throw Error('请先选择任务。');
    const id=selected.id;
    try{const result=await request(`${base}/${id}/${endpoint}`,{method:'POST',body:JSON.stringify(body)});if(alive.current)choose(result)}
    catch(e){if(!alive.current)return;try{const result=await request(`${base}/${id}`);if(alive.current)choose(result)}catch{}throw Error('操作结果尚未确认，请核对原任务状态；不要重复派发。')}
    if(alive.current)await reload();
  })}
  const fields=draft?.fields||{},pending=draft?.pending,editable=!!draft&&!busy&&admin&&!pending;
  return <section className="uav-simulation" aria-label="无人机模拟任务" style={{padding:20,maxWidth:1000,margin:'auto',lineHeight:1.6}}>
    <h2>无人机模拟任务</h2>
    <p role="note" style={{background:'#fff4da',padding:12,borderRadius:8}}>仅连接隔离模拟器，不控制真实设备，不产生现场照片或视频。直线路径未核验空域、地形和障碍物，不能用于实际飞行。</p>
    {error&&<p role="alert" style={{color:'#b42318'}}>{error}</p>}
    {pollError&&<p role="status" style={{color:'#9a6700'}}>{pollError}</p>}
    {!draft&&<button className="btn" onClick={()=>{setError('');setLockEpoch(x=>x+1)}}>重新获取草稿编辑权</button>}
    <h3>规划模拟核查</h3><p>输入模拟机库与目标经纬度，系统评估模拟续航、风速和设备状态。输入自动保存在本机，任务提交后保存在服务器。</p>
    {pending&&<p role="status">上次规划请求尚待核对：{pending.event_id}。请继续恢复原请求，再创建新方案。</p>}
    <div style={{display:'flex',gap:12,flexWrap:'wrap'}}>
      {[['event','事件名称'],['startLon','机库经度'],['startLat','机库纬度'],['endLon','目标经度'],['endLat','目标纬度']].map(([name,label])=><label key={name}>{label} <input aria-label={label} disabled={!editable} type={name==='event'?'text':'number'} value={fields[name]||''} onChange={e=>edit(name,e.target.value)}/></label>)}
    </div>
    <button className="btn primary" disabled={busy||!admin||!draft} onClick={prepare}>{pending?'恢复上次规划请求':'生成模拟方案'}</button>{!admin&&<p>仅工作区管理员可规划和确认模拟任务。</p>}
    <h3>任务记录</h3><button className="btn" disabled={busy} onClick={()=>action(reload)}>刷新记录</button>
    {rows.length===0?<p>{pollError?'暂时无法读取任务记录。':'暂无任务记录。'}</p>:<ul>{rows.map(row=><li key={row.id}><button className="btn" onClick={()=>open(row)}>{row.event_id} · {labels[row.status]||row.status}</button></li>)}</ul>}
    {selected&&<article style={{border:'1px solid #ccd8e4',borderRadius:8,padding:16}}>
      <h3>{selected.event_id} · {labels[selected.status]||selected.status}</h3>
      <p>模拟单程 {selected.plan.distance_m} 米，往返及观察 {selected.plan.duration_seconds} 秒；评估时电量 {selected.plan.battery_percent}%，风速 {selected.plan.wind_m_s} 米/秒。</p>
      <p>{selected.plan.limitations}</p>{selected.reasons?.map(x=><p role="alert" key={x}>{x}</p>)}
      {selected.alerts?.map(x=><p role="alert" key={x}>{x}</p>)}
      {selected.status==='awaiting_approval'&&<button className="btn primary" disabled={!admin||busy} onClick={()=>command('start',{plan_hash:selected.plan_hash,confirm:true})}>确认方案并派发模拟任务</button>}
      {['flying','low_battery'].includes(selected.status)&&<button className="btn" disabled={!admin||busy} onClick={()=>command('return',{confirm:true})}>确认模拟返航</button>}
      {selected.request_id&&<button className="btn" disabled={busy} onClick={()=>command('refresh',{})}>核对模拟状态</button>}
      {['dispatch_unknown','return_unknown','connection_lost'].includes(selected.status)&&<p>设备或回执状态尚不明确，请核对原任务。系统不会自动重发。</p>}
    </article>}
  </section>;
}
