import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';
import {externalCapabilityList} from './external-capability-list.mjs';
import ExternalCapabilityRegistration from './ExternalCapabilityRegistration';
import ExternalCapabilityManager from './ExternalCapabilityManager';

// Read-only inventory. Registration and release retain their existing backend
// authorization and verification workflow; this view never mutates either.
export default function ExternalCapabilityList({account, kind = 'all', Btn, request = json, onRegisterGuard}) {
  const identity = `${account?.user_id || ''}:${account?.workspace_id || ''}:${kind}`;
  const selectionKey=`ga-external-selected-v1:${identity}`;
  const readSelection=()=>{try{const value=JSON.parse(localStorage.getItem(selectionKey)||'null');return value&&typeof value.id==='string'?value:null;}catch{return null;}};
  const [selection,setSelection]=useState(()=>({identity,value:readSelection()}));
  const selected=selection.identity===identity?selection.value:null;
  useEffect(()=>{setSelection({identity,value:readSelection()});},[identity]);
  const createGuard=useRef(null),managerGuard=useRef(null),guardCallback=useRef(onRegisterGuard);guardCallback.current=onRegisterGuard;
  useEffect(()=>{const check=()=>!(createGuard.current&&createGuard.current()===false)&&!(managerGuard.current&&managerGuard.current()===false);guardCallback.current?.(check);return()=>guardCallback.current?.(null,check);},[]);
  function guardFor(ref,guard,previous){if(guard)ref.current=guard;else if(!previous||ref.current===previous)ref.current=null;}
  function select(value){
    if(managerGuard.current&&managerGuard.current()===false)return;
    try{if(value)localStorage.setItem(selectionKey,JSON.stringify(value));else localStorage.removeItem(selectionKey);}catch{}
    setSelection({identity,value});
  }
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState({identity:'', status:'loading'});
  useEffect(() => {
    if (!account?.user_id || !account?.workspace_id) {
      setState({identity, status:'error'});
      return;
    }
    const controller = new AbortController();
    let active = true;
    setState({identity, status:'loading'});
    Promise.resolve().then(() => request('/capabilities', {
      cache:'no-store', signal:controller.signal,
      headers:{'X-Workspace-ID':account.workspace_id},
    })).then(data => externalCapabilityList(data, kind)).then(data => {
      if (active) setState({identity, status:'ready', ...data});
    }).catch(() => {
      if (active && !controller.signal.aborted) setState({identity, status:'error'});
    });
    return () => {active = false; controller.abort();};
  }, [identity, attempt, request]);

  const current = state.identity === identity ? state : {status:'loading'};
  const noun = kind === 'tool' ? '外部工具' : kind === 'specialist_model' ? '外部专业模型' : '外部专业模型与工具';
  return <section className="local-model-card" aria-label={`已登记${noun}`}>
    <h2>已登记{noun}</h2>
    <p>查看当前工作区的 HTTP JSON 和 MCP 能力登记。契约发布不代表服务在线、领域效果已验证或已供业务实例使用。</p>
    {['tool','specialist_model'].includes(kind)&&<ExternalCapabilityRegistration key={identity} {...{account,kind,Btn,request}} onRegisterGuard={(guard,previous)=>guardFor(createGuard,guard,previous)} onChanged={()=>setAttempt(n=>n+1)}/>}
    {selected&&(current.status!=='ready'||!current.rows.some(row=>row.key===selected.id))&&<ExternalCapabilityManager key={identity+':'+selected.id} {...{account,kind,Btn,request}} identity={selected} onClose={()=>select(null)} onChanged={()=>setAttempt(n=>n+1)} onRegisterGuard={(guard,previous)=>guardFor(managerGuard,guard,previous)}/>}
    {current.status === 'loading' ? <p role="status">正在读取登记记录…</p>
      : current.status === 'error' ? <p role="alert">登记记录暂时无法读取，请检查权限和连接。 <Btn onClick={()=>setAttempt(x=>x+1)}>重试读取</Btn></p>
      : <>
        {!current.rows.length ? <p>在已读取的记录中没有这类外部能力。</p> : current.rows.map(row => <article className="media-form" key={row.key}>
          <h3>{row.name}</h3><p>{row.description}</p>
          <p>{row.category} · {row.adapter} · 能力版本 {row.releaseVersion} · 契约版本 {row.contractVersion} · {row.status}</p>
          {row.identity&&<Btn onClick={()=>select(row.identity)}>打开登记记录</Btn>}
          {row.identity&&selected?.id===row.identity.id&&<ExternalCapabilityManager key={identity+':'+row.identity.id} {...{account,kind,Btn,request}} identity={row.identity} onClose={()=>select(null)} onChanged={()=>setAttempt(n=>n+1)} onRegisterGuard={(guard,previous)=>guardFor(managerGuard,guard,previous)}/>}
        </article>)}
        {current.truncated && <p>这里只检索最近 100 条能力登记，较早记录可能未显示。</p>}
      </>}
  </section>;
}
