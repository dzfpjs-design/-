import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import OnvifCameraEnrollment from './OnvifCameraEnrollment';

// Pending scans are client-scoped; sending again uses the same request id.
// Device candidates and explicit confirmations live in the workspace backend.
export default function OnvifDiscovery({account,Btn,onEnrollmentGuard}){
 const admin=account?.role==='admin',key=`ga-onvif-scan:${account?.workspace_id||''}:${account?.user_id||''}`;
 const [scans,setScans]=useState([]),[pending,setPending]=useState(''),[authorized,setAuthorized]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const alive=useRef(true),busyRef=useRef(false);
 const request=(path,options={})=>json(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 async function load(){
  const rows=await request('/media/onvif/scans');
  if(!Array.isArray(rows)||rows.some(row=>row.workspace_id!==account.workspace_id))throw Error('发现记录与当前工作区不一致，请重试。');
  if(!alive.current)return;
  let original='';try{original=localStorage.getItem(key)||'';}catch{throw Error('本机存储不可用，无法安全恢复本次发现。');}
  let exact=null;
  if(original){
   try{exact=await request('/media/onvif/scans/requests/'+encodeURIComponent(original));}
   catch(e){if(e.status!==404)throw e;}
   if(!alive.current)return;
   if(exact){
    if(exact.workspace_id!==account.workspace_id||exact.actor_id!==account.user_id||exact.request_id!==original||!Array.isArray(exact.devices))throw Error('原扫描回执与当前账号不一致，请保留原请求并联系管理员。');
    try{localStorage.removeItem(key);}catch{throw Error('原扫描已保存，但无法清理本机待核对记录，请重试。');}
    original='';
   }
  }
  if(!alive.current)return;
  setScans(exact&&!rows.some(row=>row.id===exact.id)?[exact,...rows]:rows);setPending(original);
 }
 useEffect(()=>{alive.current=true;if(admin)load().catch(e=>{if(alive.current)setError(e.message)});return()=>{alive.current=false;};},[key,admin]);
 async function run(fn){if(busyRef.current)return;busyRef.current=true;setBusy(true);setError('');setNotice('');try{await fn();}catch(e){if(alive.current)setError(e.message);}finally{busyRef.current=false;if(alive.current)setBusy(false);}}
 function prepare(){return run(async()=>{if(!authorized)throw Error('请确认有权发现该局域网中的设备。');let id;try{id=localStorage.getItem(key);if(!id){id=crypto.randomUUID();localStorage.setItem(key,id);}if(localStorage.getItem(key)!==id)throw Error();}catch{throw Error('无法保存扫描编号，未启动发现。请检查浏览器本机存储。');}setPending(id);await submit(id);});}
 async function submit(id){try{await request('/media/onvif/scans',{method:'POST',body:JSON.stringify({request_id:id,local_network_authorized:true})});await load();setNotice('本次发现结果已保存；设备身份与可用性尚未验证。');}catch(e){throw Error('发现结果尚未确认。请先核对保存记录，再按原请求重试；不会自动扫描。');}}
 function check(){return run(async()=>{await load();setNotice('已重新读取保存的发现记录。');});}
 function retry(){return run(async()=>{if(!pending)throw Error('没有待核对的扫描请求。');await submit(pending);});}
 function confirm(scan,device){return run(async()=>{try{await request('/media/onvif/scans/'+encodeURIComponent(scan.id)+'/confirm',{method:'POST',body:JSON.stringify({candidate_ids:[device.candidate_id],candidate_confirmation:true})});await load();setNotice('已记录待接入设备；接下来仍需配置网关取流并验证画面。');}catch(e){await load();throw Error('设备记录结果未能确认，请查看列表后重试。');}});}
 if(!admin)return <section className="local-sample"><h3>发现局域网摄像头</h3><p>请联系管理员进行局域网设备发现与确认。</p></section>;
 return <section id="onvif-discovery" className="local-sample" aria-label="发现局域网摄像头" tabIndex={-1}><h3>发现局域网摄像头</h3><p>在运行节点的默认网卡发送 ONVIF 发现请求（兼容两种常见设备类型），只发现同一局域网可响应的设备；跨网段、禁用组播及不支持 ONVIF 的设备可能找不到。响应未经认证，结果可能包括录像机等其他 ONVIF 设备，仅作待核对线索，不读取画面、不控制设备。</p>
 <label><input type="checkbox" checked={authorized} onChange={e=>setAuthorized(e.target.checked)} disabled={busy||!!pending}/> 我确认有权发现运行节点所在局域网的设备</label>
 <div className="actions"><Btn disabled={busy||!authorized||!!pending} onClick={prepare}>开始发现摄像头</Btn><Btn disabled={busy} onClick={check}>刷新发现记录</Btn>{pending&&<Btn disabled={busy} onClick={retry}>按原请求重试发现</Btn>}</div>
 {pending&&<p>上次发现结果待核对；先刷新记录，尚未找到时可按原请求重试。</p>}{error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 {!scans.length&&!busy&&<p>暂无已保存的发现记录。</p>}
 {scans.map(scan=><article className="local-sample" key={scan.id}><h4>发现记录 · {new Date(scan.checked_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</h4>{scan.packets_limited&&<p>回应数量达到上限，仅显示部分结果。</p>}{!scan.devices?.length?<p>没有发现符合条件的摄像头。请检查网卡、组播与设备发现设置。</p>:scan.devices.map(device=><div key={device.candidate_id}><p><strong>{device.name}</strong> · {device.source_ip} · ONVIF 响应未认证<br/>设备声明地址：{device.service_address}</p>{scan.confirmed_ids?.includes(device.candidate_id)?<><p>已记录候选；设备身份与画面仍需核验。</p><OnvifCameraEnrollment key={[account.workspace_id,account.user_id,scan.id,device.candidate_id].join(':')} {...{account,scan,device,Btn}} onRegisterGuard={(guard,previous)=>onEnrollmentGuard?.('onvif:'+scan.id+':'+device.candidate_id,guard,previous)}/></>:<Btn disabled={busy} onClick={()=>confirm(scan,device)}>记录为待接入设备</Btn>}</div>)}</article>)}
 <p>设备发现与视频读取授权分开。接入后仍需读取网关通道、确认读取授权并验证画面；当前仅支持受信任 HTTPS、HTTP Digest、ONVIF Media 1 与 H.264。</p></section>;
}
