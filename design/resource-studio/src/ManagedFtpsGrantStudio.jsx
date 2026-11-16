import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';

const fresh=()=>crypto.randomUUID();
const defaults=()=>({protocol:'ftps',name:'',host:'',port:'21',tls_name:'',root:'/',data_port_min:'',data_port_max:'',chroot:false});
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);

/** Secrets are held only in the live input state and sent over the current session. */
export default function ManagedFtpsGrantStudio(props){
 return <ManagedFtpsGrantPanel key={JSON.stringify([props.account?.workspace_id,props.account?.user_id,props.account?.role])} {...props}/>;
}
function ManagedFtpsGrantPanel({account,Btn,onRegisterGuard,onChanged,request=json}){
 const userScope=account?.workspace_id+':'+account?.user_id,scope=userScope+':'+account?.role,key='managed-ftps-grant:'+userScope+':pending',draftKey='managed-ftps-grant:'+userScope+':draft';
 const [form,setForm]=useState(defaults),[username,setUsername]=useState(''),[password,setPassword]=useState(''),[caPem,setCaPem]=useState(''),[certState,setCertState]=useState('empty'),[certEpoch,setCertEpoch]=useState(0),[data,setData]=useState(null),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[storageError,setStorageError]=useState(false),[editor,setEditor]=useState(false),[editorAttempt,setEditorAttempt]=useState(0);
 const identity=useRef({scope,epoch:0}),alive=useRef(false),running=useRef(false),certSequence=useRef(0),current=useRef({});
 if(identity.current.scope!==scope)identity.current={scope,epoch:identity.current.epoch+1};const epoch=identity.current.epoch;current.current={pending,busy,storageError};
 const active=()=>alive.current&&identity.current.scope===scope&&identity.current.epoch===epoch;
 const req=(path,options={})=>request(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 async function locked(fn){if(!navigator.locks?.request)throw Error('当前浏览器无法保护多标签页请求');return navigator.locks.request(key,{mode:'exclusive'},fn)}
 async function load(){const value=await req('/ftps-authorizations/grants');if(!Array.isArray(value?.grants)||typeof value.enabled!=='boolean'||typeof value.key_ready!=='boolean')throw Error('授权清单无法核对');if(active())setData(value);return value}
 useEffect(()=>{alive.current=true;setData(null);setPending(null);setError('');setNote('');setStorageError(false);setUsername('');setPassword('');setCaPem('');setCertState('empty');setCertEpoch(v=>v+1);certSequence.current++;setBusy(false);running.current=false;
  try{const raw=localStorage.getItem(key);if(raw){const value=JSON.parse(raw);if(value?.scope!==userScope||!['create','revoke'].includes(value.kind)||typeof value.request_id!=='string')throw Error();setPending(value)}const saved=localStorage.getItem(draftKey);setForm(saved?{...defaults(),...JSON.parse(saved)}:defaults())}catch{setStorageError(true);setError('本机授权请求或草稿无法核对，已暂停操作。')}
  if(account?.role==='admin')load().catch(()=>{if(active())setError('无法读取文件网关授权，请检查后台连接。')});return()=>{alive.current=false;certSequence.current++};
 },[scope]);
 useEffect(()=>{const guard=()=>!current.current.busy&&!current.current.pending&&!current.current.storageError;onRegisterGuard?.(guard);return()=>onRegisterGuard?.(null,guard)},[onRegisterGuard]);
 useEffect(()=>{if(account?.role!=='admin')return;let stopped=false,release;setEditor(false);
  if(!navigator.locks?.request){setError('当前浏览器无法保护多标签页草稿，已暂停编辑。');return}
  navigator.locks.request(key+':editor',{mode:'exclusive',ifAvailable:true},async lock=>{
   if(stopped)return;if(!lock){setEditor(false);return}
   try{const saved=localStorage.getItem(draftKey);setForm(saved?{...defaults(),...JSON.parse(saved)}:defaults());setEditor(true);await new Promise(resolve=>{release=resolve})}catch{setStorageError(true);setError('本机草稿无法读取，已暂停操作。')}
  }).catch(()=>{if(!stopped)setError('无法保护草稿，已暂停编辑。')});
  return()=>{stopped=true;release?.()};
 },[key,editorAttempt,account?.role]);
 function changeMany(values){if(!editor)return;const next={...form,...values};try{localStorage.setItem(draftKey,JSON.stringify(next));setForm(next);setError('')}catch{setStorageError(true);setError('非敏感输入未能保存，已暂停提交。')}}
 function change(name,value){changeMany({[name]:value})}
 function submission(){return {request_id:fresh(),name:form.name.trim(),host:form.host.trim(),port:Number(form.port),tls_name:form.tls_name.trim(),root:form.root.trim(),data_port_min:Number(form.data_port_min),data_port_max:Number(form.data_port_max),server_chroot_confirmed:form.chroot,username,password,ca_pem:caPem};}
 async function retain(entry){await locked(()=>{if(!active())throw Error();if(localStorage.getItem(key))throw Error();const value=JSON.stringify(entry);localStorage.setItem(key,value);if(localStorage.getItem(key)!==value)throw Error();});if(active())setPending(entry)}
 async function release(entry){await locked(()=>{if(!active())throw Error();const raw=localStorage.getItem(key);if(!raw||!same(JSON.parse(raw),entry))throw Error();localStorage.removeItem(key);if(localStorage.getItem(key)!==null)throw Error();});if(active())setPending(null)}
 async function accept(entry,result){if(result?.request_id!==entry.request_id||!['succeeded','cancelled'].includes(result.state))throw Error();
  if(result.state==='succeeded'){const grant=result.grant;if(!grant||grant.protocol!==entry.protocol||entry.kind==='revoke'&&(grant.id!==entry.id||grant.enabled!==false)||entry.kind==='create'&&(grant.host!==entry.host||grant.port!==entry.port||grant.tls_name!==entry.tls_name||grant.root!==entry.root))throw Error();}
  await release(entry);if(!active())return;setUsername('');setPassword('');setCaPem('');setCertState('empty');setCertEpoch(v=>v+1);certSequence.current++;setNote(result.state==='cancelled'?'原请求已安全结束，可重新操作。':entry.kind==='create'?'授权请求已保存，当前状态以清单为准。':'授权已撤销，已保存的资料与历史保留。');
  if(account.role==='admin')await load();await onChanged?.();}
 async function start(kind,grant){if(running.current||pending||busy||storageError)return;running.current=true;setBusy(true);setError('');setNote('');let entry,retained=false;
  try{let body,url;
   if(kind==='create'){body=submission();if(certState==='loading'||certState==='error'||!body.name||!body.host||!username||!password||!Number.isInteger(body.port)||body.port<1||body.port>65535||!body.root||!body.tls_name||!body.server_chroot_confirmed)throw Error('请完成必填内容和必要确认。');entry={scope:userScope,request_id:body.request_id,kind,protocol:'ftps',host:body.host,port:body.port,tls_name:body.tls_name,root:body.root};url='/ftps-authorizations/grants'}
   else{body={request_id:fresh(),version:grant.version};entry={scope:userScope,request_id:body.request_id,kind,protocol:grant.protocol,id:grant.id};url='/ftps-authorizations/grants/'+encodeURIComponent(grant.id)+'/revoke'}
   await retain(entry);retained=true;const result=await req(url,{method:'POST',body:JSON.stringify(body)});if(!active())return;await accept(entry,result);
  }catch(e){if(active())setError(retained?'操作结果尚未核对，原请求已保留；请先核对或安全结束。':(e.message==='请完成必填内容和必要确认。'?e.message:'本机无法安全保存原请求，未提交。'))}
  finally{if(active()){running.current=false;setBusy(false)}}}
 async function recover(cancel){if(!pending||running.current||busy)return;running.current=true;setBusy(true);setError('');try{
  const url='/ftps-authorizations/operations/'+encodeURIComponent(pending.request_id)+(cancel?'/cancel':'');
  const result=await req(url,cancel?{method:'POST'}:{});if(active())await accept(pending,result);
 }catch(e){if(active())setError(e.status===404&&!cancel?'尚未找到原请求；可安全结束后重新填写。':'原请求仍待核对，请稍后重试。')}
 finally{if(active()){running.current=false;setBusy(false)}}}
 if(!account?.workspace_id||!account?.user_id)return null;
 if(account.role!=='admin'&&!pending&&!storageError)return null;
 return <section className="local-model-card" aria-label="FTPS 目录授权"><h3>授权 FTPS 目录</h3>
  <p>工作区管理员可在系统允许的内网范围内配置只读目录。账号、密码与私有 CA 加密保存在服务器；本机草稿不保存账号、密码或证书。留空证书则使用系统信任库，始终验证服务器名称。</p>
  {error&&<p role="alert">{error}</p>}{note&&<p role="status">{note}</p>}
  {pending&&<aside className="info"><p>上次授权操作尚待核对。不会重复提交凭据。</p><Btn disabled={busy} onClick={()=>recover(false)}>核对原请求</Btn><Btn disabled={busy} onClick={()=>recover(true)}>安全结束原请求</Btn></aside>}
  {account.role!=='admin'?<p>当前角色不可修改授权；可核对或安全结束本人原请求。</p>:!data?<p>正在读取授权配置…</p>:!data.enabled?<p>图形授权尚未由系统管理员启用。</p>:!data.key_ready?<p role="alert">服务器加密密钥不可用，请由系统管理员恢复原密钥。</p>:!data.networks.length?<p>系统尚未配置允许访问的内网范围。</p>:<>
   <p>允许的内网范围：{data.networks.join('、')}。保存授权不代表服务已连接。</p>
   {!editor&&<p>草稿正在其他页面编辑，关闭该页面后可 <button type="button" onClick={()=>setEditorAttempt(x=>x+1)}>重新取得编辑权</button>。</p>}
   <fieldset disabled={busy||!!pending||storageError||!editor}><legend>新增只读目录授权</legend>
    <label>授权名称<input value={form.name} onChange={e=>change('name',e.target.value)} maxLength={100}/></label>
    <label>服务器 IPv4 地址<input value={form.host} onChange={e=>change('host',e.target.value)}/></label>
    <label>服务端口<input type="number" value={form.port} onChange={e=>change('port',e.target.value)}/></label>
    <label>服务器证书名称<input value={form.tls_name} onChange={e=>change('tls_name',e.target.value)} placeholder="证书中的域名或 IP"/></label>
    <label>授权根目录<input value={form.root} onChange={e=>change('root',e.target.value)}/></label>
    <label>被动数据端口起始<input type="number" value={form.data_port_min} onChange={e=>change('data_port_min',e.target.value)}/></label>
    <label>被动数据端口结束<input type="number" value={form.data_port_max} onChange={e=>change('data_port_max',e.target.value)}/></label>
    <label>私有 CA 证书（可选）<input key={certEpoch} type="file" accept=".pem,.crt,.cer" onChange={async e=>{const file=e.target.files?.[0],sequence=++certSequence.current;setCaPem('');if(!file){setCertState('empty');return}if(file.size>131072){setError('证书文件过大');setCertState('error');return}setCertState('loading');try{const value=await file.text();if(active()&&sequence===certSequence.current){setCaPem(value);setCertState('ready');setError('')}}catch{if(active()&&sequence===certSequence.current){setError('证书文件无法读取');setCertState('error')}}}}/></label>{certState==='loading'&&<p role="status">正在读取证书，请稍候。</p>}
    <label><input type="checkbox" checked={form.chroot} onChange={e=>change('chroot',e.target.checked)}/>已确认服务器将此账号限制在授权目录</label>
    <label>账号<input value={username} autoComplete="off" onChange={e=>setUsername(e.target.value)}/></label><label>密码<input type="password" value={password} autoComplete="new-password" onChange={e=>setPassword(e.target.value)}/></label>
    <Btn primary disabled={certState==='loading'||certState==='error'} onClick={()=>start('create')}>保存目录授权</Btn>
   </fieldset>
   <h4>已配置的目录授权</h4>{!data.grants.length?<p>尚无目录授权。</p>:data.grants.map(g=><article key={g.protocol+':'+g.id}><p>{g.name} · {g.protocol.toUpperCase()} · {g.host}:{g.port} · 证书名称 {g.tls_name} · {g.root} · {g.enabled?'允许读取':'已撤销或超出范围'}</p>{g.enabled&&<Btn disabled={busy||!!pending||storageError} onClick={()=>start('revoke',g)}>撤销此授权</Btn>}</article>)}
  </>}
 </section>;
}
