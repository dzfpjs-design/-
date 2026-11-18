import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {api,json,hashBytes} from './runtime-api';
import {sameSetupSession,setupSessionIdentity} from './setup-session-contract.mjs';
import {voiceEvidenceFor,readVoiceEvidence} from './voice-evidence-contract.mjs';

export default function AutomationVoiceEvidence({workspace,business,names,account,onSessionInvalid,Btn,Dialog,_pollIntervalMs=5000}){
 const {refs,ambiguous}=voiceEvidenceFor(workspace,business,names);
 const scope=JSON.stringify([account?.workspace_id,account?.user_id,account?.username,account?.role]);
 const audioRef=useRef(null),urlRef=useRef(''),generation=useRef(0),sessionCheck=useRef(null),monitor=useRef(null);
 const [verification,setVerification]=useState('ready');
 const [selected,setSelected]=useState(null),[url,setUrl]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false),[attempt,setAttempt]=useState(0),[hidden,setHidden]=useState(false);
 monitor.current={selected,scope,hidden,verification,loading};
 useEffect(()=>{setSelected(null);setError('');},[business,scope,JSON.stringify(workspace.businessAliases),JSON.stringify(workspace.voiceInputs),JSON.stringify(names)]);
 function stopAudio(){try{audioRef.current?.pause()}catch{}if(urlRef.current){URL.revokeObjectURL(urlRef.current);urlRef.current='';}}
 useEffect(()=>{let active=true;
  const invalidate=()=>{if(active){generation.current++;stopAudio();setUrl('');setHidden(true);onSessionInvalid?.(account);}};
  const checkStorage=()=>{try{return sessionStorage.getItem('ga-setup-csrf-user')===account.user_id&&sessionStorage.getItem('ga-setup-csrf-workspace')===account.workspace_id&&sessionStorage.getItem('ga-setup-csrf-username')===account.username}catch{return false}};
  const check=async()=>{const epoch=++generation.current;sessionCheck.current?.abort();const controller=new AbortController();sessionCheck.current=controller;stopAudio();setUrl('');setVerification('checking');
   if(!checkStorage()){invalidate();sessionCheck.current=null;return;}
   const timeout=setTimeout(()=>controller.abort(),5000);try{const current=setupSessionIdentity(await json('/me',{signal:controller.signal,headers:{'X-Workspace-ID':account.workspace_id},cache:'no-store'}));if(!active||epoch!==generation.current)return;if(!sameSetupSession(account,current)||!checkStorage()){invalidate();return;}setVerification('ready');}
   catch(e){if(!active||epoch!==generation.current)return;if(e.status===401||e.status===403||e.code==='SESSION_INVALID'){invalidate();return;}setVerification('error');}finally{clearTimeout(timeout);if(sessionCheck.current===controller)sessionCheck.current=null;}
  };
  const poll=async()=>{const state=monitor.current;if(!active||sessionCheck.current||!state?.selected||state.selected.scope!==scope||state.hidden||state.loading||state.verification!=='ready')return;
   const epoch=generation.current,controller=new AbortController();sessionCheck.current=controller;const timeout=setTimeout(()=>controller.abort(),5000);
   try{if(!checkStorage()){invalidate();return;}const current=setupSessionIdentity(await json('/me',{signal:controller.signal,headers:{'X-Workspace-ID':account.workspace_id},cache:'no-store'}));if(!active||epoch!==generation.current)return;if(!sameSetupSession(account,current)||!checkStorage())invalidate();}
   catch(e){if(!active||epoch!==generation.current)return;if(e.status===401||e.status===403||e.code==='SESSION_INVALID'){invalidate();return;}generation.current++;stopAudio();setUrl('');setVerification('error');}
   finally{clearTimeout(timeout);if(sessionCheck.current===controller)sessionCheck.current=null;}
  };const timer=setInterval(poll,_pollIntervalMs);
  const suspend=()=>{generation.current++;sessionCheck.current?.abort();stopAudio();setUrl('');setVerification('checking');};
  const focus=()=>{check();},visibility=()=>{if(document.visibilityState==='visible')check();else suspend();},storage=()=>{if(!checkStorage()){generation.current++;stopAudio();invalidate();}};
  window.addEventListener('focus',focus);window.addEventListener('blur',suspend);window.addEventListener('storage',storage);document.addEventListener('visibilitychange',visibility);if(!checkStorage())invalidate();
  return()=>{active=false;clearInterval(timer);generation.current++;sessionCheck.current?.abort();stopAudio();window.removeEventListener('focus',focus);window.removeEventListener('blur',suspend);window.removeEventListener('storage',storage);document.removeEventListener('visibilitychange',visibility)};
 },[scope,_pollIntervalMs]);
 useLayoutEffect(()=>{if(!selected||selected.scope!==scope||hidden||verification!=='ready')return;let active=true,objectURL='',epoch=generation.current;const controller=new AbortController(),options={signal:controller.signal,headers:{'X-Workspace-ID':account.workspace_id},cache:'no-store'};setLoading(true);setError('');setUrl('');
  const verify=async()=>{if(!active||epoch!==generation.current||controller.signal.aborted)throw Error('读取已停止');const current=setupSessionIdentity(await json('/me',options));if(!sameSetupSession(account,current)){const e=Error('账号或工作区已变化');e.code='SESSION_SCOPE_CHANGED';throw e;}};
  (async()=>{await verify();const blob=await readVoiceEvidence(selected.ref,{readReceipt:r=>json('/setup/voice/requests/'+encodeURIComponent(r.request_id)+'?audio_run_id='+encodeURIComponent(r.audio_run_id),options),readAudio:async r=>{if(!active||epoch!==generation.current||controller.signal.aborted)throw Error('读取已停止');return (await api('/audio/'+encodeURIComponent(r.audio_run_id)+'/original',options)).blob()},hashBytes});await verify();if(active&&epoch===generation.current){objectURL=URL.createObjectURL(blob);urlRef.current=objectURL;setUrl(objectURL)}})().catch(e=>{if(!active||epoch!==generation.current)return;if(e.status===401||e.status===403||e.code==='SESSION_SCOPE_CHANGED'||e.code==='SESSION_INVALID'){setHidden(true);onSessionInvalid?.(account);return;}setError((e.message||'读取失败')+'；请检查连接或稍后重试。')}).finally(()=>{if(active)setLoading(false)});
  return()=>{active=false;controller.abort();if(objectURL&&urlRef.current===objectURL){URL.revokeObjectURL(objectURL);urlRef.current=''}};
 },[selected,attempt,scope,hidden,verification]);
 if(hidden)return <p role="alert">当前账号需要重新核对，语音依据已隐藏，原记录保留。</p>;
 if(verification!=='ready')return <div role="status">{verification==='checking'?'正在核对当前账号，语音依据暂时隐藏。':<>暂时无法核对当前账号，语音依据已隐藏。<Btn onClick={()=>{window.dispatchEvent(new Event('focus'))}}>重试核对</Btn></>}</div>;
 return <div className="automation-voice-evidence"><details><summary>查看本业务的语音依据</summary>{ambiguous?<p role="alert">业务名称关联存在歧义，无法安全确定语音依据；请检查业务名称和配置历史。</p>:refs.length?<><p>这里列出当前配置中可明确关联的自动化语音记录；原始业务名称与内容保持不变。</p>{refs.map((ref,i)=><div key={ref.request_id+':'+i}><span>原业务：{ref.business} · 保存时间：{ref.saved_at||'未记录'}</span> <Btn onClick={()=>setSelected({ref,scope})}>查看语音依据</Btn></div>)}</>:<p>当前配置中未找到可明确关联的语音依据；其他历史版本可能仍有记录，请查看配置历史。</p>}</details>
 {selected&&selected.scope===scope&&<Dialog title="自动化语音依据" onClose={()=>setSelected(null)}><div className="modal-body">{loading&&<p role="status">正在核对收据和读取原音频…</p>}{error&&<><p role="alert">{error}</p><Btn onClick={()=>setAttempt(x=>x+1)}>重新读取</Btn></>}{url&&<><p>原业务：{selected.ref.business}</p><p>保存时间：{selected.ref.saved_at||'未记录'}；保存时配置版本未记录，请查看配置历史。</p><h3>当时核对并保存的文字</h3><p style={{whiteSpace:'pre-wrap'}}>{selected.ref.text}</p><audio ref={audioRef} aria-label="原语音依据" controls src={url}/><p>此处只供查看，不会修改或重新应用原收据。</p></>}<Btn onClick={()=>setSelected(null)}>关闭</Btn></div></Dialog>}
 </div>;
}
