import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';

const fresh=()=>crypto.randomUUID();
const defaults=()=>({name:'',host:'',port:'8883'});

/** Broker credentials never enter browser storage; an uncertain request is fenced by ID. */
export default function ManagedMqttAuthorizations(props){
 return <Panel key={[props.account?.workspace_id,props.account?.user_id,props.account?.role].join(':')} {...props}/>;
}
function Panel({account,Btn,onChanged,onRegisterGuard,request=json}){
 const scope=account?.workspace_id+':'+account?.user_id,key='managed-mqtt:'+scope+':pending',draftKey='managed-mqtt:'+scope+':draft';
 const [data,setData]=useState(null),[form,setForm]=useState(defaults),[username,setUsername]=useState(''),[password,setPassword]=useState(''),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[editor,setEditor]=useState(false),[attempt,setAttempt]=useState(0),[error,setError]=useState(''),[notice,setNotice]=useState(''),[badStorage,setBadStorage]=useState(false);
 const live=useRef(false),current=useRef({}),guardRef=useRef(onRegisterGuard);guardRef.current=onRegisterGuard;current.current={busy,pending,badStorage};
 const req=(path,options={})=>request(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 async function load(){const result=await req('/mqtt/authorizations');if(!Array.isArray(result?.authorizations)||typeof result.enabled!=='boolean'||typeof result.key_ready!=='boolean')throw Error('授权清单无法核对');if(live.current)setData(result);return result}
 useEffect(()=>{live.current=true;setError('');setNotice('');setUsername('');setPassword('');setPending(null);setBadStorage(false);
  try{const draft=JSON.parse(localStorage.getItem(draftKey)||'null');if(draft){if(typeof draft.name!=='string'||typeof draft.host!=='string'||typeof draft.port!=='string')throw Error();setForm({...defaults(),...draft})}const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved){if(saved.scope!==scope||!['create','status'].includes(saved.kind)||typeof saved.request_id!=='string')throw Error();setPending(saved)}}catch{setBadStorage(true);setError('本机原请求或草稿无法核对，操作已暂停。')}
  if(account.role==='admin')load().catch(()=>{if(live.current)setError('无法读取 MQTT 授权，请检查后台连接。')});return()=>{live.current=false};
 },[scope]);
 useEffect(()=>{let stopped=false,release;setEditor(false);if(account.role!=='admin')return;
  if(!navigator.locks?.request){setError('当前浏览器不能保护多标签页草稿，请使用支持安全锁的浏览器。');return}
  navigator.locks.request(key+':editor',{mode:'exclusive',ifAvailable:true},async lock=>{if(stopped||!lock)return;try{const saved=JSON.parse(localStorage.getItem(draftKey)||'null');setForm(saved?{...defaults(),...saved}:defaults());setEditor(true);await new Promise(resolve=>{release=resolve})}catch{setBadStorage(true)}}).catch(()=>{if(!stopped)setError('无法保护多标签页编辑。')});
  return()=>{stopped=true;release?.()};
 },[scope,attempt]);
 useEffect(()=>{const guard=()=>!current.current.busy&&!current.current.pending&&!current.current.badStorage;guardRef.current?.(guard);return()=>guardRef.current?.(null,guard)},[]);
 function edit(patch){const next={...form,...patch};setForm(next);try{localStorage.setItem(draftKey,JSON.stringify(next))}catch{setBadStorage(true);setError('草稿无法保留，已暂停操作。')}}
 async function retain(value){if(!navigator.locks?.request)throw Error('当前浏览器不能保护原请求');await navigator.locks.request(key,{mode:'exclusive'},async()=>{if(localStorage.getItem(key))throw Error('已有原请求，请先核对');localStorage.setItem(key,JSON.stringify(value));if(localStorage.getItem(key)!==JSON.stringify(value))throw Error('无法保留原请求')});setPending(value)}
 async function clear(value){await navigator.locks.request(key,{mode:'exclusive'},async()=>{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved?.request_id!==value.request_id||saved?.scope!==scope)throw Error('原请求已变化，请先核对');localStorage.removeItem(key)});if(live.current)setPending(null)}
 async function finish(value,result){if(result?.request_id!==value.request_id||!['succeeded','cancelled'].includes(result.state))throw Error('服务器结果尚未核对');await clear(value);setUsername('');setPassword('');if(account.role==='admin')await load();onChanged?.();setNotice(result.state==='cancelled'?'原请求已停止。':'授权已保存。')}
 async function create(event){event.preventDefault();if(busy||pending||!editor||badStorage)return;setBusy(true);setError('');try{const rid=fresh(),value={scope,kind:'create',request_id:rid};await retain(value);const result=await req('/mqtt/authorizations',{method:'POST',body:JSON.stringify({request_id:rid,name:form.name,host:form.host,port:Number(form.port),username,password})});await finish(value,result)}catch(e){if(live.current)setError('授权结果待核对：'+e.message)}finally{if(live.current)setBusy(false)}}
 async function changeStatus(grant){if(busy||pending||!editor||badStorage)return;setBusy(true);setError('');try{const rid=fresh(),value={scope,kind:'status',request_id:rid};await retain(value);const result=await req('/mqtt/authorizations/'+encodeURIComponent(grant.id)+'/status',{method:'POST',body:JSON.stringify({request_id:rid,version:grant.version,enabled:!grant.enabled})});await finish(value,result)}catch(e){if(live.current)setError('授权结果待核对：'+e.message)}finally{if(live.current)setBusy(false)}}
 async function recover(cancel=false){if(!pending||busy)return;const value=pending;setBusy(true);setError('');try{const result=await req('/mqtt/authorizations/operations/'+encodeURIComponent(value.request_id)+(cancel?'/cancel':''),cancel?{method:'POST'}:{});await finish(value,result)}catch(e){if(live.current)setError(cancel?'原请求未能停止，请稍后核对。':'尚未找到原结果，可稍后核对或停止原请求。')}finally{if(live.current)setBusy(false)}}
 if(account?.role!=='admin'&&!pending)return null;
 return <section className="local-model-card" aria-label="MQTT 服务授权"><h3>授权 MQTT 服务</h3><p>仅支持系统允许网段内的固定私网 IP 和 MQTT over TLS。服务器证书必须由系统信任，且证书包含该 IP；账号和密码加密保存，不保存在浏览器中。</p>
 {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{account.role==='admin'&&!editor&&<p>此授权正在其他标签页编辑。关闭其他标签页后可接手。 <Btn onClick={()=>setAttempt(n=>n+1)}>接手编辑</Btn></p>}
 {pending&&<p role="alert">上次授权请求结果尚未核对。 <Btn disabled={busy} onClick={()=>recover(false)}>核对原请求</Btn> <Btn disabled={busy} onClick={()=>recover(true)}>停止原请求</Btn></p>}
 {account.role==='admin'&&(!data?<p>正在读取服务授权…</p>:!data.enabled||!data.networks?.length?<p>系统尚未配置允许的设备网络，请联系系统管理员。</p>:!data.key_ready?<p role="alert">服务器加密密钥不可用，请恢复原密钥。</p>:<><p>允许网络：{data.networks.join('、')}</p><form onSubmit={create}><fieldset disabled={busy||!!pending||!editor||badStorage}>
 <label>授权名称 <input value={form.name} onChange={e=>edit({name:e.target.value})} required maxLength={100}/></label>
 <label>服务 IP <input value={form.host} onChange={e=>edit({host:e.target.value})} placeholder="192.168.0.10" required/></label>
 <label>端口 <input value={form.port} onChange={e=>edit({port:e.target.value})} inputMode="numeric" required/></label>
 <label>用户名 <input value={username} onChange={e=>setUsername(e.target.value)} autoComplete="off" required/></label>
 <label>密码 <input type="password" value={password} onChange={e=>setPassword(e.target.value)} autoComplete="new-password" required/></label>
 <Btn type="submit">保存服务授权</Btn></fieldset></form>
 <h4>已保存的 MQTT 服务授权</h4>{!data.authorizations.length?<p>尚未登记 MQTT 服务。</p>:data.authorizations.map(g=><article key={g.id}><p>{g.name} · {g.broker} · {g.enabled?'已启用':'已停用'}</p><Btn disabled={busy||!!pending||!editor||badStorage} onClick={()=>changeStatus(g)}>{g.enabled?'停用授权':'恢复授权'}</Btn></article>)}</>)}
 </section>;
}
