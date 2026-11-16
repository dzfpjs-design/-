import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import CameraSecurityForm from './CameraSecurityForm';

const documentID=crypto.randomUUID();
const labels={matches:'网关中已找到一致配置；仍需读取画面验证。',missing:'网关中未找到原通道，可明确选择恢复。',conflict:'网关中已有不同配置，系统不会覆盖。'};

/** A confirmed discovery remains untrusted until explicit setup and validation. */
export default function OnvifCameraEnrollment({account,scan,device,Btn,onRegisterGuard}){
 const prefix=`ga-onvif-enrollment:${account.workspace_id}:${account.user_id}:${scan.id}:${device.candidate_id}:`;
 const key=prefix+documentID,anchor=prefix+'current';
 const initial={gateway:'',credential:'',pending:null,last:null};
 const [draft,setDraft]=useState(null),[gateways,setGateways]=useState([]),[refs,setRefs]=useState([]),[version,setVersion]=useState(null),[first,setFirst]=useState(false),[second,setSecond]=useState(false);
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[error,setError]=useState(''),[checkState,setCheckState]=useState(''),[copies,setCopies]=useState([]),[copyChoice,setCopyChoice]=useState('');
 const alive=useRef(true),running=useRef(false),draftRef=useRef(initial),guardCallback=useRef(onRegisterGuard);guardCallback.current=onRegisterGuard;
 const [securityEditing,setSecurityEditing]=useState(false);const securityEditingRef=useRef(false);securityEditingRef.current=securityEditing;
 const request=(url,options={})=>json(url,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 function save(value){
  try{localStorage.setItem(key,JSON.stringify(value));sessionStorage.setItem(anchor,key);draftRef.current=value;setDraft(value);return true;}
  catch{setError('无法保存本机接入请求，未启动新操作。请检查浏览器存储。');return false;}
 }
 useEffect(()=>{
  alive.current=true;
  try{const previous=sessionStorage.getItem(anchor),raw=previous?.startsWith(prefix)?localStorage.getItem(previous):null;
   const value=raw?JSON.parse(raw):initial;
   if(!value||typeof value!=='object'||typeof value.gateway!=='string'||typeof value.credential!=='string')throw Error();
   save(value);
  }catch{setError('无法恢复本机接入输入，原记录保留。');}
  return()=>{alive.current=false;};
 },[key]);
 useEffect(()=>{const guard=()=>!running.current&&!securityEditingRef.current;guardCallback.current?.(guard);const before=e=>{if(running.current||securityEditingRef.current){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',before);return()=>{guardCallback.current?.(null,guard);window.removeEventListener('beforeunload',before);};},[]);
 async function run(fn){if(running.current)return;running.current=true;setBusy(true);setError('');setNotice('');
  try{await fn();}catch(e){if(alive.current)setError(e.message||'操作暂时无法完成，请核对原请求。');}
  finally{running.current=false;if(alive.current)setBusy(false);}}
 function verify(result,body){if(!result||result.request_id!==body.request_id||result.scan_id!==scan.id||result.candidate_id!==device.candidate_id||result.gateway_id!==body.gateway_id||!['pending','configured','cancelled'].includes(result.state))throw Error('接入回执与原请求不一致，请联系管理员核对。');return result;}
 function readCopies(){try{const rows=[];for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k?.startsWith(prefix)){try{const value=JSON.parse(localStorage.getItem(k));if(value&&typeof value.gateway==='string'&&typeof value.credential==='string'&&(value.pending||value.last))rows.push({key:k,value});}catch{}}}setCopies(rows);}catch{setError('无法读取本机保留的接入记录。');}}
 function restoreCopy(){const row=copies.find(x=>x.key===copyChoice);if(!row){setError('请先选择原接入记录。');return;}if(save(row.value)){setCheckState('');setNotice('已恢复本机记录；不会自动发送摄像头或网关请求。');}}
 async function readOptions(id){const q=new URLSearchParams({scan_id:scan.id,candidate_id:device.candidate_id,gateway_id:id});const value=await request('/media/onvif-enrollments/options?'+q);if(!Array.isArray(value.credential_references)||!Number.isInteger(value.gateway_version))throw Error('摄像头接入授权无法核对。');if(alive.current){setRefs(value.credential_references);setVersion(value.gateway_version);}}
 function open(){return run(async()=>{const rows=await request('/media/gateways');if(!Array.isArray(rows))throw Error('无法读取视频网关列表。');if(alive.current)setGateways(rows);const current=draftRef.current;if(current.gateway&&!current.pending&&!current.last&&rows.some(row=>row.id===current.gateway))await readOptions(current.gateway);});}
 function choose(id){if(!draft||draft.pending)return;save({...draftRef.current,gateway:id,credential:''});setRefs([]);setVersion(null);setCheckState('');if(!id)return;
  run(async()=>{await readOptions(id);});
 }
 function revokeCredential(){return run(async()=>{const ref=draftRef.current.credential;if(!ref?.startsWith('GA_ONVIF_CAMERA_SECRET_UI_'))throw Error('此凭据由运维管理，请联系运维停用。');const result=await request('/media/camera-security/references/'+encodeURIComponent(ref)+'/revoke',{method:'POST',body:JSON.stringify({})});if(result.reference!==ref||result.revoked!==true)throw Error('停用结果无法核对，请重试。');await readOptions(draftRef.current.gateway);save({...draftRef.current,credential:''});setNotice('平台凭据已停用。网关可能仍保存密码或继续拉流；请同时停用网关通道，无法确认的在途写入需另行核对。');});}
 function send(body){return run(async()=>{
  if(!save({...draftRef.current,pending:body}))return;
  const result=verify(await request('/media/onvif-enrollments',{method:'POST',body:JSON.stringify(body)}),body);
  if(!alive.current)return;
  if(result.state==='configured'){setCheckState('');if(save({...draftRef.current,pending:null,last:body}))setNotice('已写入网关的按需取流配置；仍需读取通道并验证画面。网关重启后请再次核对。');}
  else if(result.state==='cancelled'){if(save({...draftRef.current,pending:null,last:null}))setNotice('原请求已安全结束，可以重新填写。');}
  else setError('接入仍待核对，请使用原请求继续。');
 });}
 function begin(){if(!draft||!draft.gateway||!draft.credential||!Number.isInteger(version)||!first||!second){setError('请选择网关和服务端凭据引用，并确认两个操作范围。');return;}
  send({request_id:crypto.randomUUID(),scan_id:scan.id,candidate_id:device.candidate_id,gateway_id:draft.gateway,gateway_version:version,credential_ref:draft.credential,confirm_onvif_read:true,confirm_gateway_write_and_credential_storage:true});}
 function check(){return run(async()=>{const body=draftRef.current.pending||draftRef.current.last;if(!body)throw Error('没有可核对的原请求。');
  const result=verify(await request('/media/onvif-enrollments/requests/'+encodeURIComponent(body.request_id)),body);
  if(result.state==='cancelled'){if(save({...draftRef.current,pending:null,last:null}))setNotice('原请求已安全结束。');return;}
  if(!['matches','missing','conflict'].includes(result.gateway_state))throw Error('网关状态暂时无法核对。');
  if(result.state==='configured'&&!save({...draftRef.current,pending:null,last:body}))return;
  setCheckState(result.gateway_state);setNotice(labels[result.gateway_state]);
 });}
 function cancel(){return run(async()=>{const body=draftRef.current.pending;if(!body)return;
  const result=verify(await request('/media/onvif-enrollments/requests/'+encodeURIComponent(body.request_id)+'/cancel',{method:'POST',body:JSON.stringify(body)}),body);
  if(result.state==='cancelled'){if(save({...draftRef.current,pending:null,last:null}))setNotice('原请求已安全结束，迟到请求不会执行。');}
  else if(result.state==='configured'){if(save({...draftRef.current,pending:null,last:body}))setNotice('原请求已先写入网关；没有删除任何网关配置，请核对通道。');}
  else throw Error('原请求仍待核对。');
 });}
 if(!draft)return <p>正在恢复摄像头接入记录…</p>;
 return <details className="local-sample"><summary onClick={e=>{if(!e.currentTarget.parentElement.open)open();}}>接入已确认摄像头</summary>
  <p>仅支持经明确授权的 HTTPS ONVIF Media 1、HTTP Digest 和 H.264 RTSP。选择网关后可配置设备授权和凭据；网关仅配置按需拉流，不授予业务读取或设备控制权限。</p>
  <p>摄像头账号和密码会写入所选 MediaMTX 的运行配置；请确保网关配置和日志只对授权人员可见。网关重启后路径可能消失，可手动核对并恢复。</p>
  {notice&&<p role="status">{notice}</p>}{error&&<p role="alert" className="error">{error}</p>}
  <details><summary>找回本机原接入请求</summary><p>可找回其他页面或关闭页面前保留的请求；恢复不会自动访问设备。</p><Btn disabled={busy} onClick={readCopies}>读取本机接入记录</Btn>{copies.length>0&&<><select aria-label="选择本机摄像头接入记录" value={copyChoice} onChange={e=>setCopyChoice(e.target.value)}><option value="">请选择</option>{copies.map((row,i)=><option key={row.key} value={row.key}>记录 {i+1} · {row.value.pending?'待核对':'已保存'}</option>)}</select><Btn disabled={busy||!!draft.pending||!copyChoice} onClick={restoreCopy}>恢复所选请求</Btn></>}</details>
  {!draft.pending&&!draft.last&&<>
   <label>选择视频网关 <select aria-label="选择摄像头接入的视频网关" disabled={busy||securityEditing} value={draft.gateway} onChange={e=>choose(e.target.value)}><option value="">请选择</option>{gateways.map(g=><option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
   {draft.gateway&&<CameraSecurityForm key={draft.gateway} {...{account,scan,device,Btn}} gateway={draft.gateway} onDirty={setSecurityEditing} onConfigured={async ref=>{await readOptions(draft.gateway);if(!save({...draftRef.current,credential:ref}))throw Error('凭据已保存，当前页面尚未保存选择，请核对原请求。');}}/>}
   <label>摄像头凭据引用 <select aria-label="摄像头凭据引用" disabled={busy||!draft.gateway} value={draft.credential} onChange={e=>save({...draftRef.current,credential:e.target.value})}><option value="">请选择</option>{refs.map(r=><option key={r} value={r}>{r}</option>)}</select></label>
   {draft.credential?.startsWith('GA_ONVIF_CAMERA_SECRET_UI_')&&<><p>停用平台凭据不会停用网关拉流或删除网关中的密码；如需停止访问，还需停用网关通道。</p><Btn disabled={busy||securityEditing} onClick={revokeCredential}>停用平台凭据</Btn></>}
   {draft.gateway&&!refs.length&&!busy&&<p>尚无匹配凭据，请先配置摄像头授权和凭据。</p>}
   <label><input type="checkbox" checked={first} onChange={e=>setFirst(e.target.checked)} disabled={busy}/> 我已核实设备身份，允许只读获取媒体配置与取流地址。</label>
   <label><input type="checkbox" checked={second} onChange={e=>setSecond(e.target.checked)} disabled={busy}/> 我允许向所选网关写入该摄像头的按需取流配置，网关会持有摄像头账号密码。</label>
   <Btn disabled={busy||!draft.gateway||!draft.credential||!first||!second} onClick={begin}>配置摄像头到视频网关</Btn>
  </>}
  {(draft.pending||draft.last)&&<><p>{draft.pending?'原接入请求尚待核对，输入已保留。':'已保留原接入请求；当前画面和网关状态需要另行核对。'}</p><Btn disabled={busy} onClick={check}>核对原接入和网关</Btn>{draft.pending&&<><Btn disabled={busy} onClick={()=>send(draft.pending)}>按原请求重试</Btn><Btn disabled={busy} onClick={cancel}>结束待核对请求</Btn></>}{checkState==='missing'&&!draft.pending&&<Btn disabled={busy} onClick={()=>send(draft.last)}>恢复原网关通道</Btn>}</>}
 </details>;
}
