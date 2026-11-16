import React,{useEffect,useState} from 'react';
import {json} from './runtime-api';

export default function VideoReadBoundary({channel,seconds,Btn}){
 const [value,setValue]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[refresh,setRefresh]=useState(0);
 useEffect(()=>{let alive=true;setLoading(true);setValue(null);setError('');json('/media/channels/'+channel.id+'/read-boundary?seconds='+seconds).then(v=>{if(alive)setValue(v)}).catch(e=>{if(alive)setError(e.message)}).finally(()=>{if(alive)setLoading(false)});return()=>{alive=false}},[channel.id,channel.version,seconds,refresh]);
 const rules=value?.modes?.manual?.rules||value?.modes?.automatic?.rules;
 return <div className="media-form" aria-label="业务与设备读取规则">
  <strong>业务与设备读取规则</strong>
  {loading&&<p role="status">正在核对已发布规则和今日读取次数…</p>}
  {error&&<p className="error" role="alert">读取规则尚未核对：{error}。执行时仍由服务器检查。</p>}
  {value&&<>
   {value.usage?<p>今天已读取 <strong>{value.usage.used}</strong> 次{rules?(rules.daily_limit!=null?' / 每天最多 '+rules.daily_limit+' 次':'；已发布规则未设置每日次数限制'):'；当前读取规则未通过核对'}。{rules?.cooldown_minutes!=null?'最少间隔 '+rules.cooldown_minutes+' 分钟。':''}</p>:<p className="error">预算尚未核对：{value.budget_error}。</p>}
   {value.modes.manual.issue&&<p className="warning-text">人工读取：{value.modes.manual.issue}</p>}
   <details><summary>查看业务、设备与计次规则</summary>
    <p>{rules?.scope==='published_instance'?'按已发布业务“'+rules.business_name+'”与设备规则读取。':'未取得当前业务读取规则；独立来源仅用于已确认的本地接入验证。'}{rules?.legacy_manual_channel_consent?'旧实例没有设备快照，仅保留通道明确授权的人工一次读取；不能自动运行。':''}</p>
    <p>失败也计一次；手动、定时与补充共用次数。连续视频按连接启动或重连计次，保存片段不重复计次。{rules?.max_read_seconds!=null?'单次最长 '+Math.round(rules.max_read_seconds/60)+' 分钟。':''}摄像头不要求执行时段；不允许移动、云台或原始媒体云上传。</p>
    {value.modes.automatic.issue&&<p className="warning-text">自动读取：{value.modes.automatic.issue}</p>}
    <p>中国时间 {new Date(value.checked_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} 的核对状态，实际读取前与执行中会再次核对。{value.source_identity_limit}</p>
   </details>
  </>}
  <Btn disabled={loading} onClick={()=>setRefresh(v=>v+1)}>重新核对读取规则</Btn>
 </div>;
}
