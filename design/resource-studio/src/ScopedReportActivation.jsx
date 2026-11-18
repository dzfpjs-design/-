import React,{useEffect,useRef,useState} from 'react';
import {withDraftCacheLock} from './draft-cache-contract.mjs';
import {verifyScopedActivationStatus} from './report-scoped-activation.mjs';

const base='/setup/report-evidence-delegated/activation';
export default function ScopedReportActivation({account,plan,request,Btn='button',onChanged,onSessionInvalid}){
 const scope=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,plan?.preview_id,plan?.original_revision,plan?.command_hash]);
 const live=useRef({scope,serial:0,controller:null});if(live.current.scope!==scope){live.current.scope=scope;live.current.serial++;}
 const [view,setView]=useState(null),current=view?.scope===scope?view:null;
 useEffect(()=>()=>{live.current.serial++;live.current.controller?.abort();live.current.controller=null;},[scope]);
 async function run(action){
  if(current?.busy||!plan?.verified||account?.role!=='admin')return;
  const serial=++live.current.serial,controller=new AbortController();live.current.controller?.abort();live.current.controller=controller;
  const active=()=>live.current.scope===scope&&live.current.serial===serial&&!controller.signal.aborted;
  const post=(path,body)=>request(path,{method:'POST',signal:controller.signal,cache:'no-store',headers:{'X-Workspace-ID':account.workspace_id},body:JSON.stringify(body)});
  async function status(){const value=await post(base+'/status',{preview_id:plan.preview_id});await verifyScopedActivationStatus(value,account,plan);return value;}
  setView({scope,busy:true});
  try{
   let value,notice=null;
   if(action==='read')value=await status();
   else {
    await withDraftCacheLock('ga-report-scope:'+account.workspace_id+':'+account.user_id+':'+plan.preview_id,async()=>{
     if(!active())return;
     const before=await status();if(!active())return;
     if(action==='activate'&&(before.status!=='not_found'||before.plan_status!=='saved')||action==='revoke'&&before.status!=='active')throw Error('原启用状态已变化，请重新核对。');
     try{await post(action==='activate'?base:base+'/'+encodeURIComponent(before.activation_id)+'/revoke',action==='activate'?{preview_id:plan.preview_id,consent:true}:{});}catch(error){if([401,403].includes(error?.status))throw error;/* The write may have committed; consult the durable status. */}
     if(active()){value=await status();if(action==='activate'&&value.status!=='active')notice='服务器尚未确认启用，请核对当前状态后再操作。';if(action==='revoke'&&value.status!=='revoked')notice='服务器尚未确认撤销，请核对当前状态后再操作。';}
    });
    if(active()&&value)await onChanged?.();
   }
   if(active()&&value)setView({scope,busy:false,value,notice});
  }catch(error){if(active()){const invalid=[401,403].includes(error?.status)||error?.code==='SESSION_SCOPE_CHANGED'||/页面会话已变化/.test(error?.message||'');if(invalid)onSessionInvalid?.();setView({scope,busy:false,error:invalid?'当前账号无法核对定向启用状态，私人内容已隐藏。':'启用状态暂时无法确认；不会自动重试，请重新核对服务器记录。'});}}
  finally{if(live.current.controller===controller)live.current.controller=null;}
 }
 const status=current?.value;
 return <section aria-label="定向启用"><p>定向启用仅适用于这一份报告和所示计划，有效期内可撤销。启用本身不会调用模型；执行分析仍需单独启动并可能产生费用。撤销后已经进入发送流程的请求仍可能继续。</p>
  <Btn type="button" disabled={current?.busy} onClick={()=>run('read')}>{current?.busy?'正在核对启用状态…':status?'重新核对启用状态':'核对定向启用状态'}</Btn>
  {current?.error&&<p role="alert">{current.error}</p>}{current?.notice&&<p role="status">{current.notice}</p>}
  {status?.status==='not_found'&&<><p>服务器未找到可核对的启用记录；这不能证明历史没有调用或费用。</p>{status.plan_status==='saved'&&<Btn type="button" disabled={current.busy} onClick={()=>run('activate')}>确认启用这一计划</Btn>}</>}
  {status?.status==='active'&&<><p>服务器确认此计划当前已启用，有效期至 {new Date(status.expires_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）。</p><Btn type="button" disabled={current.busy} onClick={()=>run('revoke')}>撤销这一计划的启用</Btn></>}
  {status?.status==='revoked'&&<p>服务器确认此计划的启用已撤销；已进入发送流程的请求仍可能继续。</p>}
  {status?.status==='expired'&&<p>此计划的启用已过期；如需继续，请重新核对计划与额度。</p>}
 </section>;
}
