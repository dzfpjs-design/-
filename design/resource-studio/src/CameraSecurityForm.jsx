import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';

// Password, account and certificate are deliberately memory-only. Only an
// opaque request ID and the non-sensitive selected target survive refresh.
export default function CameraSecurityForm({account,scan,device,gateway,Btn,onConfigured,onDirty}){
 const marker=`camera-security:${account.workspace_id}:${account.user_id}:${scan.id}:${device.candidate_id}:${gateway}`;
 const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[certificate,setCertificate]=useState('');
 const [identity,setIdentity]=useState(false),[trust,setTrust]=useState(false),[pending,setPending]=useState('');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[auditPending,setAuditPending]=useState([]);
 const alive=useRef(true);
 function pendingList(){const prefix=marker+':';return Object.keys(localStorage).filter(k=>k.startsWith(prefix)).map(k=>k.slice(prefix.length)).sort();}
 useEffect(()=>{try{setPending(pendingList()[0]||'');}catch{setError('无法保存恢复标识，暂不能提交安全配置。');}return()=>{alive.current=false;onDirty?.(false);};},[marker]);
 useEffect(()=>{onDirty?.(busy||!!(username||password||certificate));},[busy,username,password,certificate]);
 const request=(url,options={})=>json(url,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 async function readAudits(){const q=new URLSearchParams({scan_id:scan.id,candidate_id:device.candidate_id,gateway_id:gateway});const result=await request('/media/camera-security/pending-audits?'+q);if(!Array.isArray(result.records)||result.records.some(x=>x.gateway_id!==gateway||x.candidate_id!==device.candidate_id||!/^GA_ONVIF_CAMERA_SECRET_UI_[A-F0-9_]+$/.test(x.reference||'')||typeof x.revoked!=='boolean'))throw Error('待恢复授权列表无法核对');if(alive.current)setAuditPending(result.records);}
 useEffect(()=>{readAudits().catch(()=>{if(alive.current)setError('无法读取待恢复的授权审计，请稍后重试。');});},[marker]);
 async function recoverAudit(row){setBusy(true);setError('');try{const result=await request('/media/camera-security/references/'+encodeURIComponent(row.reference)+'/recover-audit',{method:'POST',body:JSON.stringify({})});if(result.reference!==row.reference||result.audit_recovered!==true||typeof result.revoked!=='boolean')throw Error();if(!result.revoked)await onConfigured(result.reference);await readAudits();if(alive.current)setNotice(result.revoked?'已补齐原授权审计；凭据仍处于停用状态。':'已补齐原授权审计，可继续配置视频网关。');}catch{if(alive.current)setError('授权审计尚未恢复，请检查连接后重试。');}finally{if(alive.current)setBusy(false);}}
 function verified(result,id){if(!result||result.request_id!==id||!['saved','cancelled'].includes(result.state)||result.gateway_id!==gateway||result.candidate_id!==device.candidate_id||!/^GA_ONVIF_CAMERA_SECRET_UI_[A-F0-9_]+$/.test(result.reference||''))throw Error('服务器回执与原设备不一致，请联系管理员核对。');return result;}
 async function finish(result,id){verified(result,id);if(result.state==='saved')await onConfigured(result.reference);localStorage.removeItem(marker+':'+id);setPending(pendingList()[0]||'');if(result.state==='cancelled'){setNotice('原授权请求已安全结束；输入保留，可修改后重新保存。');return;}setUsername('');setPassword('');setCertificate('');setNotice('设备授权、凭据和证书已安全保存，可继续配置视频网关。');}
 async function submit(){
  if(!username||!password||!certificate||!identity||!trust){setError('请填写账号、密码和可信证书，并核对设备身份。');return;}
  if(pending){setError('请先核对之前的授权请求。');return;}
  if(location.protocol!=='https:'&&!['127.0.0.1','localhost','[::1]'].includes(location.hostname)){setError('请通过 HTTPS 配置摄像头凭据。');return;}
  const id=crypto.randomUUID();try{if(pendingList().length)throw Error();localStorage.setItem(marker+':'+id,'pending');}catch{setError('已有待核对请求或无法保存恢复标识，未提交凭据。');return;}
  setPending(id);setBusy(true);setError('');
  try{const result=await request('/media/camera-security',{method:'POST',body:JSON.stringify({request_id:id,scan_id:scan.id,candidate_id:device.candidate_id,gateway_id:gateway,username,password,certificate,confirm_identity:true,confirm_trust:true})});if(alive.current)await finish(result,id);}
  catch{if(alive.current)setError('保存结果尚未确认，输入仍在本页；请先核对原请求。');}
  finally{if(alive.current)setBusy(false);}
 }
 async function check(){setBusy(true);setError('');try{const result=await request('/media/camera-security/requests/'+encodeURIComponent(pending));if(alive.current)await finish(result,pending);}catch{if(alive.current)setError('尚未确认授权是否保存，请检查连接后再次核对。');}finally{if(alive.current)setBusy(false);}}
 async function cancel(){setBusy(true);setError('');try{const result=await request('/media/camera-security/requests/'+encodeURIComponent(pending)+'/cancel',{method:'POST',body:JSON.stringify({gateway_id:gateway,candidate_id:device.candidate_id})});if(alive.current)await finish(result,pending);}catch{if(alive.current)setError('原请求尚未安全结束，请检查连接后重新核对。');}finally{if(alive.current)setBusy(false);}}
 return <details><summary>配置摄像头授权和凭据</summary>
  <p>先核实设备身份与证书。账号、密码和证书只在本页临时输入，后台加密保存；接入后密码也会写入所选 MediaMTX 运行配置。</p>
  {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {auditPending.length>0&&<div><p>以下设备授权已保存，但审计尚未完成。管理员可接手恢复；不会显示账号或密码。</p>{auditPending.map(row=><p key={row.reference}>待恢复授权 {row.reference.slice(-8)} {row.revoked?'（已停用）':''} <Btn disabled={busy} onClick={()=>recoverAudit(row)}>补齐授权审计</Btn></p>)}</div>}
  {pending&&<p>上次保存结果待核对；不会自动重发。<Btn disabled={busy} onClick={check}>核对原授权请求</Btn> <Btn disabled={busy} onClick={cancel}>结束原请求并修改</Btn></p>}
  <label>摄像头账号 <input aria-label="摄像头账号" autoComplete="off" value={username} disabled={busy||!!pending} onChange={e=>setUsername(e.target.value)}/></label>
  <label>摄像头密码 <input aria-label="摄像头密码" type="password" autoComplete="new-password" value={password} disabled={busy||!!pending} onChange={e=>setPassword(e.target.value)}/></label>
  <label>可信证书（PEM） <textarea aria-label="摄像头可信证书" value={certificate} disabled={busy||!!pending} onChange={e=>setCertificate(e.target.value)}/></label>
  <label><input type="checkbox" checked={identity} disabled={busy||!!pending} onChange={e=>setIdentity(e.target.checked)}/> 我已核实设备身份及地址</label>
  <label><input type="checkbox" checked={trust} disabled={busy||!!pending} onChange={e=>setTrust(e.target.checked)}/> 我确认该证书来自可信设备或可信机构</label>
  <Btn disabled={busy||!!pending} onClick={submit}>保存设备授权</Btn>
 </details>;
}
