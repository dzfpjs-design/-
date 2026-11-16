import React,{useEffect,useState} from 'react';
import {json} from './runtime-api';

const time=value=>{if(!value)return '未知';const date=new Date(value);return Number.isFinite(date.getTime())?date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'未知'};
const sourceKey=x=>x.gateway_id+':'+x.device_sn;
const rainfall={0:'无雨',1:'小雨',2:'中雨',3:'大雨'};

/** A read-only view of records received by the existing authorised MQTT gateway. */
export default function DjiDockStatus({account,Btn,request=json}){
 const scope=account?.workspace_id+':'+account?.user_id;
 return <Panel key={scope} account={account} Btn={Btn} request={request}/>;
}
function Panel({account,Btn,request}){
 const [sources,setSources]=useState(null),[selected,setSelected]=useState(''),[reading,setReading]=useState(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 const req=(path,opts={})=>request(path,{...opts,headers:{...(opts.headers||{}),'X-Workspace-ID':account.workspace_id}});
 useEffect(()=>{let active=true;const ctrl=new AbortController(),deadline=setTimeout(()=>ctrl.abort(),10000);setSources(null);setError('');
  req('/dji/dock-status/sources',{signal:ctrl.signal}).then(data=>{if(!Array.isArray(data.sources))throw Error('接入清单格式无法核对');if(active)setSources(data.sources);}).catch(e=>{if(active)setError(e.message||'接入清单暂时不可用')}).finally(()=>clearTimeout(deadline));
  return()=>{active=false;clearTimeout(deadline);ctrl.abort()};
 },[account.workspace_id,account.user_id,attempt]);
 const source=sources?.find(x=>sourceKey(x)===selected);
 useEffect(()=>{let active=true,timer,deadline,ctrl;setReading(null);if(!source)return()=>{};
  async function poll(){ctrl=new AbortController();deadline=setTimeout(()=>ctrl.abort(),10000);try{const row=await req('/dji/dock-status/sources/'+encodeURIComponent(source.gateway_id)+'/'+encodeURIComponent(source.device_sn),{signal:ctrl.signal});
   if(row?.source?.gateway_id!==source.gateway_id||row?.source?.device_sn!==source.device_sn)throw Error('接入范围已变化，请重新选择');
   if(active){setReading(row);setError('');}}
   catch(e){if(active)setError('状态更新失败：'+(e.message||'请稍后重试'));}
   finally{clearTimeout(deadline);if(active)timer=setTimeout(poll,15000);}}
  poll();return()=>{active=false;clearTimeout(timer);clearTimeout(deadline);ctrl?.abort()};
 },[source?.gateway_id,source?.device_sn,attempt,account.workspace_id,account.user_id]);
 return <section className="local-model-card" aria-label="大疆机场只读状态"><h2>大疆机场只读状态</h2>
  <p>从现有 MQTT 接入中读取已授权的机场 OSD 状态报文。请先在传感网关按设备配置具体主题；这里不会发送飞行或控制指令。</p>
  <p>报文来自配置的主题，并不证明设备型号、当前在线或满足起飞条件；来源和现场状态仍需核验。</p>
  {error&&<p role="alert">{error} <Btn onClick={()=>setAttempt(x=>x+1)}>重新读取</Btn></p>}
  {sources===null?<p role="status">正在读取已授权的状态来源…</p>:!sources.length?<p>尚无已配置的机场 OSD 主题。请在本页 MQTT 接入中配置确切的设备主题。</p>:<label>选择已授权状态来源<select aria-label="大疆机场状态来源" value={selected} onChange={e=>{setSelected(e.target.value);setError('')}}><option value="">请选择</option>{sources.map(x=><option key={sourceKey(x)} value={sourceKey(x)}>{x.name} · {x.gateway_name} · {x.device_sn}</option>)}</select></label>}
  {source&&!reading&&!error&&<p role="status">正在读取已保存的状态…</p>}
  {source&&reading&&<article><h3>{source.name}</h3><p>MQTT 接入：{source.gateway_status==='active'?'已启用':'已暂停'}；接收器状态：{reading.receiver?.status||'未确认'}；接收器心跳：{time(reading.receiver?.heartbeat_at)}</p>
   {!reading.latest?<p>还没有收到此主题的状态报文。</p>:!reading.latest.parsed?<p role="alert">{reading.latest.reason} 接收时间：{time(reading.latest.received_at)}</p>:<>
    <p>设备报告时间：{time(reading.latest.reported.reported_at)}；系统接收时间：{time(reading.latest.received_at)}{reading.latest.retained?'；这是 MQTT 保留消息':''}。</p>
    <p>机场状态码：{reading.latest.reported.mode_code??'未知'}；飞行器在舱：{reading.latest.reported.aircraft_in_dock===1?'设备报告在舱':reading.latest.reported.aircraft_in_dock===0?'设备报告不在舱':'未知'}；电量：{reading.latest.reported.battery_percent===null?'未知':reading.latest.reported.battery_percent+'%'}</p>
    <p>风速：{reading.latest.reported.wind_m_s===null?'未知':reading.latest.reported.wind_m_s+' 米/秒'}；环境温度：{reading.latest.reported.temperature_c===null?'未知':reading.latest.reported.temperature_c+' ℃'}；降雨：{rainfall[reading.latest.reported.rainfall_code]??'未知'}</p>
    <p>{reading.latest.reported.position?`设备报告坐标：${reading.latest.reported.position.latitude}，${reading.latest.reported.position.longitude}；仍需现场核验。`:reading.latest.reported.position_note}</p>
    <p>{reading.latest.state_note}</p><p>原始报文已保存在对应 MQTT 接入的接收记录中，可在那里核对原件。</p>
   </>}
  </article>}
 </section>;
}
