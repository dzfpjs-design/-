import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';

const emptyDraft = () => ({name:'', host:'', port:'443', http_header_names:[''], ws_header_names:['']});
const emptySecrets = (d=emptyDraft()) => ({http:d.http_header_names.map(()=>''), ws:d.ws_header_names.map(()=>'')});
const labels = {not_verified:'尚未验证', stopped:'已停止', revoked:'已撤销', pending:'等待连接', connecting:'正在连接', connected:'上次设备查询成功', backoff:'连接中断，等待重试', disconnected:'未连接', authentication_failed:'鉴权未通过，请核对配置', authorization_lost:'授权已失效，连接已停用', authorization_unavailable:'授权暂不可核验，连接已暂停', sdk_unavailable:'受安全限制，底座连接未开放', blocked:'连接被阻止', error:'连接异常'};
const websocketLabels = {unknown:'尚未确认',pending:'正在连接，尚未确认',connected:'已建立',auth_401:'鉴权失败（401），请核对身份凭据',auth_403:'鉴权失败（403），请核对访问权限',disconnected:'已断开'};
const clean = (x,n=253) => typeof x === 'string' && x.length <= n && !/[\u0000-\u001f\u007f]/.test(x);
const validDraft = d => d && Object.keys(d).sort().join(',') === Object.keys(emptyDraft()).sort().join(',') && ['name','host','port'].every(k=>clean(d[k])) && ['http_header_names','ws_header_names'].every(k=>Array.isArray(d[k])&&d[k].length>=1&&d[k].length<=8&&d[k].every(x=>clean(x,64)));
const migrateDraft = d => d && typeof d.http_header_name==='string' && typeof d.ws_header_name==='string' ? {name:d.name,host:d.host,port:d.port,http_header_names:[d.http_header_name],ws_header_names:[d.ws_header_name]} : d;
const hasSecrets = s => [...s.http,...s.ws].some(Boolean);
const isUuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);

export default function DjiCloud2Connections(props) {
  return <Panel key={[props.account?.workspace_id,props.account?.user_id,props.account?.role].join(':')} {...props}/>;
}

function Panel({account, Btn, onRegisterGuard, request=json}) {
  const scope = `${account.workspace_id}:${account.user_id}`;
  const draftKey = `dji-cloud2:${scope}:draft`, pendingKey = `dji-cloud2:${scope}:pending`;
  const [draft,setDraft] = useState(emptyDraft), [secrets,setSecrets] = useState(emptySecrets);
  const [connections,setConnections] = useState(null), [pending,setPending] = useState(null);
  const [editing,setEditing] = useState(false), [busy,setBusy] = useState(false), [attempt,setAttempt] = useState(0);
  const [badStorage,setBadStorage] = useState(false), [error,setError] = useState(''), [notice,setNotice] = useState('');
  const [confirmRevoke,setConfirmRevoke] = useState(null);
  const alive=useRef(false), working=useRef(false), status=useRef({}), guardRef=useRef(onRegisterGuard);
  guardRef.current=onRegisterGuard;
  status.current={busy,pending,badStorage,secrets};
  const req=(path, options={})=>request(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});

  function pendingRead() {
    const raw=localStorage.getItem(pendingKey);
    if (!raw) return null;
    const p=JSON.parse(raw);
    if (!p || p.scope!==scope || !isUuid(p.request_id) || !['create','revoke'].includes(p.kind) || !p.expected || typeof p.expected!=='object') throw Error('原请求无效');
    if (p.kind==='create' && !clean(p.expected.name,128)) throw Error('原配置无效');
    if (p.kind==='revoke' && (!clean(p.expected.id,128)||!Number.isSafeInteger(p.expected.version))) throw Error('原撤权请求无效');
    return p;
  }
  async function load() {
    const reply=await req('/dji/cloud2/connections');
    if (!Array.isArray(reply?.connections)) throw Error('invalid list');
    if (alive.current) setConnections(reply.connections);
  }
  useEffect(()=>{
    alive.current=true; setError(''); setNotice(''); setSecrets(emptySecrets()); setPending(null); setBadStorage(false); setConfirmRevoke(null);
    try {const d=migrateDraft(JSON.parse(localStorage.getItem(draftKey)||'null')); if (d && !validDraft(d)) throw Error(); setDraft(d||emptyDraft()); setSecrets(emptySecrets(d||emptyDraft())); setPending(pendingRead());}
    catch {setBadStorage(true); setError('本机草稿或原请求无法核对，请检查浏览器存储；原记录已保留。');}
    load().catch(()=>{if(alive.current)setError('无法读取连接配置，请检查登录、工作区及后台连接。');});
    return ()=>{alive.current=false;};
  },[scope]);
  useEffect(()=>{
    let stopped=false,release; setEditing(false);
    if (account.role!=='admin') return;
    if (!navigator.locks?.request) {setError('当前浏览器无法保护多标签编辑，请使用支持安全锁的浏览器。'); return;}
    navigator.locks.request(`${pendingKey}:editor`,{mode:'exclusive',ifAvailable:true},async lock=>{
      if(stopped||!lock) return;
      try {const d=migrateDraft(JSON.parse(localStorage.getItem(draftKey)||'null')); if(d&&!validDraft(d))throw Error(); setDraft(d||emptyDraft()); setPending(pendingRead()); setSecrets(emptySecrets(d||emptyDraft())); setEditing(true); await new Promise(resolve=>{release=resolve;});}
      catch {setBadStorage(true); setError('本机草稿或原请求无法核对；原记录已保留。');}
    }).catch(()=>{if(!stopped)setError('无法保护多标签编辑。');});
    return ()=>{stopped=true;release?.();};
  },[scope,attempt]);
  useEffect(()=>{
    const guard=()=>!working.current&&!status.current.busy&&!status.current.pending&&!status.current.badStorage&&!hasSecrets(status.current.secrets);
    guardRef.current?.(guard);return()=>guardRef.current?.(null,guard);
  },[]);
  useEffect(()=>{
    const beforeUnload=e=>{if(working.current||status.current.pending||hasSecrets(status.current.secrets)){e.preventDefault();e.returnValue='';}};
    window.addEventListener('beforeunload',beforeUnload);return()=>window.removeEventListener('beforeunload',beforeUnload);
  },[]);
  function edit(patch) {
    const next={...draft,...patch};setDraft(next);
    try {if(!validDraft(next))throw Error();localStorage.setItem(draftKey,JSON.stringify(next));setError('');}
    catch {setBadStorage(true);setError('草稿无法安全保留，请检查输入和浏览器存储。');}
  }
  function updateHeader(kind, index, field, value) {
    if(field==='name') {const key=`${kind}_header_names`, names=[...draft[key]];names[index]=value;edit({[key]:names});}
    else {const values=draft[`${kind}_header_names`].map((_,i)=>i===index?value:(secrets[kind][i]||''));setSecrets({...secrets,[kind]:values});}
  }
  function addHeader(kind) {const key=`${kind}_header_names`;if(draft[key].length>=8)return;edit({[key]:[...draft[key],'']});setSecrets({...secrets,[kind]:[...secrets[kind],'']});}
  function removeHeader(kind,index) {const key=`${kind}_header_names`;if(draft[key].length<=1)return;edit({[key]:draft[key].filter((_,i)=>i!==index)});setSecrets({...secrets,[kind]:secrets[kind].filter((_,i)=>i!==index)});}
  function locked(fn) {if(!navigator.locks?.request)throw Error('无法保护原请求');return navigator.locks.request(`${pendingKey}:journal`,{mode:'exclusive'},fn);}
  async function retain(value) {await locked(()=>{if(!alive.current||pendingRead())throw Error('已有原请求');localStorage.setItem(pendingKey,JSON.stringify(value));if(localStorage.getItem(pendingKey)!==JSON.stringify(value))throw Error('无法保存');setPending(value);});}
  async function clear(value) {await locked(()=>{if(!alive.current||JSON.stringify(pendingRead())!==JSON.stringify(value))throw Error('原请求已变化');localStorage.removeItem(pendingKey);if(localStorage.getItem(pendingKey)!==null)throw Error('无法清除');setPending(null);});}
  async function finish(value, reply) {
    if(reply?.request_id!==value.request_id||!['succeeded','cancelled'].includes(reply.state))throw Error('回执无法核对');
    if(reply.state==='succeeded') {
      const c=reply.connection, e=value.expected;
      if(!c||typeof c.id!=='string'||!Number.isSafeInteger(c.version))throw Error('配置回执不完整');
      if(value.kind==='create'&&c.name!==e.name)throw Error('配置回执不一致');
      if(value.kind==='revoke'&&(c.id!==e.id||c.version!==e.version+1||c.enabled!==false))throw Error('撤权回执不一致');
    }
    await clear(value);if(!alive.current)return;
    setSecrets(emptySecrets(draft));setConfirmRevoke(null);
    if(reply.state==='succeeded'&&value.kind==='create'){setDraft(emptyDraft());try{localStorage.removeItem(draftKey);}catch{setBadStorage(true);setError('配置已保存，但本机草稿无法清除。');}}
    const createNotice=reply.connection?.enabled===false?'原连接已撤销；如需使用，请新增连接。':reply.connection?.status==='authorization_lost'?'原授权已失效，请撤销后重新新增连接。':reply.connection?.status==='authorization_unavailable'?'配置已保留，但授权暂不可核验；请先恢复原有安全配置。':'连接配置已保存，官方底座联调结果仍需核验。';
    setNotice(reply.state==='cancelled'?'原请求已安全结束。':value.kind==='create'?createNotice:'已撤销该连接的本地授权。');
    try{await load();}catch{if(alive.current)setError('操作已保存，但清单刷新失败，请重试读取。');}
  }
  async function create(e) {
    e.preventDefault();if(working.current||busy||pending||!editing||badStorage)return;
    if(!draft.name.trim()||!draft.host.trim()||!draft.port.trim()){setError('请填写连接名称、地址和端口。');return;}
    const headers={};for(const kind of ['http','ws']){headers[kind]=draft[`${kind}_header_names`].map((name,i)=>({name:name.trim(),value:secrets[kind][i]||''}));if(headers[kind].some(x=>!x.name||!x.value)){setError('请填写每项鉴权头的名称和值，或删除不需要的项。');return;}if(new Set(headers[kind].map(x=>x.name.toLowerCase())).size!==headers[kind].length){setError('同一组鉴权头名称不能重复。');return;}}
    const port=Number(draft.port);if(!Number.isInteger(port)||port<1||port>65535){setError('端口应为 1 至 65535 的数字。');return;}
    const name=draft.name.trim();const value={scope,kind:'create',request_id:crypto.randomUUID(),expected:{name}};let retained=false;
    working.current=true;setBusy(true);setError('');setNotice('');
    try{await retain(value);retained=true;const reply=await req('/dji/cloud2/connections',{method:'POST',body:JSON.stringify({name,host:draft.host,port,http_headers:headers.http,ws_headers:headers.ws,request_id:value.request_id,confirm:true})});await finish(value,reply);}
    catch(e){if(alive.current){const known=['地址或端口不在系统管理员允许的范围内','鉴权请求头名称或内容无效','连接名称或端口无效'].includes(e?.message)?`${e.message}。`:'';setError(retained?`${known}请核对或安全结束原请求后再操作，避免重复提交。`:'原请求未发送：无法保存恢复记录，请检查浏览器存储。');}}
    finally{setSecrets(emptySecrets(draft));working.current=false;if(alive.current)setBusy(false);}
  }
  async function revoke(row) {
    if(working.current||busy||pending||!editing||badStorage)return;
    const value={scope,kind:'revoke',request_id:crypto.randomUUID(),expected:{id:row.id,version:row.version}};let retained=false;
    working.current=true;setBusy(true);setError('');setNotice('');
    try{await retain(value);retained=true;const reply=await req(`/dji/cloud2/connections/${encodeURIComponent(row.id)}/revoke`,{method:'POST',body:JSON.stringify({request_id:value.request_id,version:row.version,confirm:true})});await finish(value,reply);}
    catch{if(alive.current)setError(retained?'撤权结果尚未核对，请先核对或安全结束原请求。':'撤权请求未发送：无法保存恢复记录，请检查浏览器存储。');}
    finally{working.current=false;if(alive.current)setBusy(false);}
  }
  async function recover(cancel=false) {
    if(!pending||working.current||busy)return;const value=pending;working.current=true;setBusy(true);setError('');
    try{const path=`/dji/cloud2/operations/${encodeURIComponent(value.request_id)}${cancel?'/cancel':''}`;await finish(value,await req(path,cancel?{method:'POST'}:{}));}
    catch{if(alive.current)setError('原请求尚未核对，记录仍保留；请稍后重试。');}
    finally{working.current=false;if(alive.current)setBusy(false);}
  }
  const disabled=busy||!!pending||!editing||badStorage;
  return <section className="local-model-card" aria-label="大疆上云 API 2 连接">
    <h2>大疆上云 API 2 连接</h2>
    <p>为当前工作区配置大疆底座连接和鉴权。当前受安全限制，仅开放保存和撤销配置；后台尚不能连接底座，设备查询和断线恢复尚不能使用。许可证审核通过后仍需完成安全验证和官方模拟器或设备联调，保存配置不代表设备已连接或可以飞行。</p>
    <p>目前只接受系统管理员预先允许的底座 IPv4 地址和端口，底座必须使用系统信任且匹配该 IP 的 TLS 证书；不支持忽略证书校验或使用自签名证书。HTTP 与消息连接的鉴权请求头由大疆底座的鉴权配置决定。鉴权值仅在本页内存中使用，保存后由后台加密保管；刷新或保存失败后需要重新填写。</p>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{busy&&<p role="status">正在处理，请稍候。</p>}
    {pending&&<div><h3>上次操作结果待核对</h3><p>请先核对原请求，避免重复提交；确认原请求未保存时可安全结束后重新操作。</p><Btn disabled={busy} onClick={()=>recover(false)}>核对原请求</Btn> <Btn disabled={busy} onClick={()=>recover(true)}>安全结束原请求</Btn></div>}
    {account.role==='admin'&&!editing&&!badStorage&&<p>此账号的另一个页面正在编辑。关闭其他页面后可接手。 <Btn onClick={()=>setAttempt(x=>x+1)}>接手编辑</Btn></p>}
    {account.role==='admin'&&<details><summary>新增底座连接</summary><form onSubmit={create}><fieldset disabled={disabled}>
      <label>连接名称 <input value={draft.name} onChange={e=>edit({name:e.target.value})} maxLength={100} required/></label>
      <label>底座地址 <input value={draft.host} onChange={e=>edit({host:e.target.value})} maxLength={253} placeholder="例如 192.0.2.10" required/></label>
      <label>端口 <input type="number" min="1" max="65535" value={draft.port} onChange={e=>edit({port:e.target.value})} required/></label>
      {['http','ws'].map(kind=><div key={kind}><h4>{kind==='http'?'HTTP':'消息连接'}鉴权请求头</h4><p>按底座的鉴权配置添加；每组 1 至 8 项。</p>{draft[`${kind}_header_names`].map((header,index)=><div key={index}>
        <label>{kind==='http'?'HTTP':'消息连接'} 鉴权请求头名称 {index+1} <input value={header} onChange={e=>updateHeader(kind,index,'name',e.target.value)} autoComplete="off" maxLength={64} required/></label>
        <label>{kind==='http'?'HTTP':'消息连接'} 鉴权请求头值 {index+1} <input type="password" value={secrets[kind][index]||''} onChange={e=>updateHeader(kind,index,'value',e.target.value)} autoComplete="new-password" required/></label>
        <Btn type="button" disabled={draft[`${kind}_header_names`].length<=1} onClick={()=>removeHeader(kind,index)}>删除{kind==='http'?' HTTP':'消息连接'}鉴权头 {index+1}</Btn></div>)}<Btn type="button" disabled={draft[`${kind}_header_names`].length>=8} onClick={()=>addHeader(kind)}>添加{kind==='http'?' HTTP':'消息连接'}鉴权头</Btn></div>)}
      <Btn type="submit">保存连接</Btn> <Btn type="button" onClick={()=>setSecrets(emptySecrets(draft))}>清除鉴权值</Btn>
    </fieldset></form></details>}
    <h3>当前工作区的连接</h3>{connections===null?<p>正在读取…</p>:!connections.length?<p>尚未保存大疆上云 API 2 连接。</p>:connections.map(row=><article className="media-form" key={row.id}>
      <h4>{row.name}</h4><p>底座：{row.host}:{row.port}；{row.enabled===false?'授权已撤销':(labels[row.status]||'状态待核验')}。{row.last_checked_at&&<> 最近检查：{row.last_checked_at}。</>}</p>{row.enabled!==false&&<p>消息连接：{websocketLabels[row.websocket_status]||websocketLabels.unknown}。{row.websocket_last_failure&&!['auth_401','auth_403'].includes(row.websocket_status)&&<>最近一次鉴权失败（{row.websocket_last_failure==='auth_401'?'401':'403'}）。</>}</p>}{row.status==='authorization_lost'&&<p>授权人身份或权限已变化。请先撤销旧连接，再由管理员重新新增授权。</p>}{row.status==='authorization_unavailable'&&<p>请联系管理员检查密钥、权限保护和允许地址。原配置仍保留，恢复后可刷新核验。</p>}{(row.http_header_names||row.ws_header_names)&&<p>鉴权头名称：HTTP {(row.http_header_names||[]).join('、')||'无'}；消息连接 {(row.ws_header_names||[]).join('、')||'无'}。鉴权值不会显示。</p>}{row.notice&&<p>{row.notice}</p>}
      {account.role==='admin'&&row.enabled!==false&&(confirmRevoke===row.id?<div><p>撤销后本工作区将停止使用此连接；如需再次使用，请新增连接。若大疆底座另有独立授权，也需要在那里撤销。</p><Btn disabled={disabled} onClick={()=>revoke(row)}>确认撤销</Btn> <Btn disabled={busy} onClick={()=>setConfirmRevoke(null)}>返回</Btn></div>:<Btn disabled={disabled} onClick={()=>setConfirmRevoke(row.id)}>撤销授权</Btn>)}
    </article>)}<Btn disabled={busy} onClick={()=>load().catch(()=>setError('无法刷新连接清单，请稍后重试。'))}>刷新连接状态</Btn>
  </section>;
}
