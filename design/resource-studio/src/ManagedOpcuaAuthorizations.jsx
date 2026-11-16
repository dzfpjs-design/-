import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {useAuthorizationEditor} from './ManagedModbusAuthorizations';
const initial={name:'',host:'',port:'4840',path:'/',node_id:''};
async function encodeFile(file){if(!file||file.size<1||file.size>32768)throw Error('请选择不超过 32 KB 的证书或私钥文件。');const bytes=new Uint8Array(await file.arrayBuffer());let text='';for(const value of bytes)text+=String.fromCharCode(value);return btoa(text)}
export default function ManagedOpcuaAuthorizations({account,Btn,onChanged=()=>{},request=json}){
 const identity=JSON.stringify([account?.workspace_id,account?.user_id,account?.role]),scope=account.workspace_id+':'+account.user_id,key='managed-opcua-auth-pending:'+scope,draftKey='managed-opcua-auth-draft:'+scope;
 const [form,setForm]=useState(initial),[files,setFiles]=useState({}),[fileEpoch,setFileEpoch]=useState(0),[info,setInfo]=useState(null),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState('');
 const alive=useRef(false),current=useRef(identity);current.current=identity;const active=()=>alive.current&&current.current===identity;
 const call=(path,opts={})=>request(path,{...opts,headers:{...(opts.headers||{}),'X-Workspace-ID':account.workspace_id}});
 async function load(){const value=await call('/opcua/authorizations');if(active())setInfo(value)}
 const editor=useAuthorizationEditor(scope,draftKey,()=>{
  const raw=localStorage.getItem(draftKey),value=raw?JSON.parse(raw):initial;
  if(!value||typeof value!=='object'||Object.keys(initial).some(k=>typeof value[k]!=='string'))throw Error('草稿无法核对');
  setForm({...initial,...value});
  const saved=localStorage.getItem(key);
  if(saved){const item=JSON.parse(saved);if(item.scope!==scope||!item.request_id||!['create','status'].includes(item.kind))throw Error('原请求无法核对');setPending(item)}else setPending(null);
 });
 useEffect(()=>{alive.current=true;setInfo(null);setPending(null);setFiles({});setForm(initial);setError('');setNote('');try{const d=localStorage.getItem(draftKey);if(d)setForm({...initial,...JSON.parse(d)});const raw=localStorage.getItem(key);if(raw){const x=JSON.parse(raw);if(x.scope!==scope||!x.request_id||!['create','status'].includes(x.kind))throw Error();setPending(x)}}catch{setError('本机授权草稿或原请求无法读取，请检查浏览器存储。')}
  if(account.role==='admin')load().catch(e=>{if(active())setError(e.message)});return()=>{alive.current=false};
 },[identity]);
 function edit(name,value){if(!editor.held()||busy||pending){setError('请先取得编辑权并核对未完成操作。');return}const next={...form,[name]:value};try{localStorage.setItem(draftKey,JSON.stringify(next));setForm(next);setError('')}catch{setError('授权输入未能保存，请检查浏览器存储。')}}
 async function lock(fn){if(!navigator?.locks?.request)throw Error('浏览器不支持安全的多标签操作。');return navigator.locks.request(key,{mode:'exclusive'},fn)}
 async function perform(intent,body,resolve=false){if(busy)return;if(!editor.held()){setError('请先取得此授权草稿的编辑权。');return}setBusy(true);setError('');setNote('');const original=identity;
  try{await lock(async()=>{
   if(!active()||current.current!==original)throw Error('账号或工作区已变化。');const raw=localStorage.getItem(key);
   if(resolve){if(!raw||JSON.stringify(JSON.parse(raw))!==JSON.stringify(intent))throw Error('本机原请求已变化。')}
   else{if(raw)throw Error('已有待核对授权，请先处理。');localStorage.setItem(key,JSON.stringify(intent));if(localStorage.getItem(key)!==JSON.stringify(intent))throw Error('原请求未能保存，未提交。');setPending(intent)}
   const endpoint=resolve?'/opcua/authorizations/operations/'+encodeURIComponent(intent.request_id)+'/resolve':intent.kind==='create'?'/opcua/authorizations':'/opcua/authorizations/'+encodeURIComponent(intent.grant_id)+'/status';
   const receipt=await call(endpoint,{method:'POST',body:JSON.stringify(resolve?{kind:intent.kind}:body)});
   if(!active()||current.current!==original)throw Error('账号或工作区已变化，原请求保留。');
   if(receipt?.request_id!==intent.request_id||receipt?.kind!==intent.kind||!['succeeded','cancelled'].includes(receipt.state))throw Error('授权回执无法核对，原请求保留。');
   if(localStorage.getItem(key)!==JSON.stringify(intent))throw Error('本机原请求已变化，原请求保留。');
   localStorage.removeItem(key);setPending(null);setNote(receipt.state==='cancelled'?'原请求已安全结束，不会执行。':'授权已保存。');
   if(intent.kind==='create'&&receipt.state==='succeeded'){localStorage.removeItem(draftKey);setForm(initial);setFiles({});setFileEpoch(n=>n+1)}
   if(account.role==='admin'){try{await load()}catch{if(active())setError('授权已保存，但列表暂时无法刷新；请稍后刷新。')}}onChanged();
  })}catch(e){if(active()){let retained=true;try{retained=!!localStorage.getItem(key)}catch{}setError((e.message||'操作未完成')+(retained?'；原请求保留，请核对。':''))}}
  finally{if(active())setBusy(false)}
 }
 async function create(e){e.preventDefault();if(pending||busy||!editor.held())return;
  try{if(!form.name.trim()||!/^\d{1,3}(\.\d{1,3}){3}$/.test(form.host)||!Number.isInteger(Number(form.port))||!form.path.startsWith('/')||!form.node_id.trim())throw Error('请填写名称、IPv4 地址、端口、服务路径和节点。');
   const values=await Promise.all(['server_certificate','client_certificate','client_private_key'].map(k=>encodeFile(files[k])));const request_id=crypto.randomUUID(),intent={scope,kind:'create',request_id};
   const body={request_id,name:form.name.trim(),host:form.host,port:Number(form.port),path:form.path,node_id:form.node_id.trim(),server_certificate:values[0],client_certificate:values[1],client_private_key:values[2]};await perform(intent,body);
  }catch(e){if(active())setError(e.message||'无法读取证书文件。')}
 }
 function status(row){if(pending)return;const request_id=crypto.randomUUID();perform({scope,kind:'status',request_id,grant_id:row.id},{request_id,version:row.version,enabled:!row.enabled})}
 return <section className="media-form" aria-label="OPC UA 设备授权"><h3>授权设备节点与证书</h3><p>指定一个已允许的内网设备和只读节点，上传服务端证书、客户端证书与私钥。证书应用标识从证书中核对，私钥加密保存在服务器。</p><p>证书和私钥不会保存在浏览器中；中断后可核对原请求，若服务器未保存，需要重新选取文件。</p>
  {error&&<p role="alert">{error}</p>}{note&&<p role="status">{note}</p>}{editor.state!=='ready'&&<aside className="info"><p role="status">{editor.state==='busy'?'另一标签页正在编辑该授权，请返回原标签页或关闭后重新获取编辑权。':editor.state==='corrupt'?'本机草稿或原请求无法核对，已停止编辑；请保留本机记录。':editor.state==='unsupported'?'当前浏览器不能保护多标签草稿，请更换支持安全锁的浏览器。':'正在取得草稿编辑权…'}</p>{editor.state==='busy'&&<Btn onClick={editor.retry}>重新获取编辑权</Btn>}</aside>}{pending&&<aside className="info"><p>上次授权操作尚待核对，原请求编号已保留。</p><Btn disabled={busy||editor.state!=='ready'} onClick={()=>perform(pending,null,true)}>核对并结束原请求</Btn></aside>}
  {account.role!=='admin'?<p>当前账号不能更改授权；仍可核对本人未完成的请求。</p>:!info?<p>正在读取授权范围…</p>:<>
   {!info.enabled||!info.networks.length?<p>系统尚未设置允许的设备网络，请联系系统管理员完成一次网络范围配置。</p>:!info.vault_ready?<p role="alert">服务器证书安全存储密钥不可用，请由系统管理员恢复后再配置。</p>:<><p>允许的网络：{info.networks.join('、')}</p><form onSubmit={create}><fieldset disabled={busy||!!pending||editor.state!=='ready'} style={{border:0,margin:0,padding:0}}>
    <label>授权名称<input value={form.name} onChange={e=>edit('name',e.target.value)} maxLength={100}/></label><label>设备 IPv4 地址<input value={form.host} onChange={e=>edit('host',e.target.value)} placeholder="例如 192.168.1.20"/></label><label>端口<input type="number" min="1" max="65535" value={form.port} onChange={e=>edit('port',e.target.value)}/></label><label>服务路径<input value={form.path} onChange={e=>edit('path',e.target.value)} /></label><label>节点标识<input value={form.node_id} onChange={e=>edit('node_id',e.target.value)} placeholder="例如 ns=2;s=temperature"/></label>
    <div key={fileEpoch}><label>服务端证书<input type="file" onChange={e=>setFiles(v=>({...v,server_certificate:e.target.files?.[0]}))}/></label><label>客户端证书<input type="file" onChange={e=>setFiles(v=>({...v,client_certificate:e.target.files?.[0]}))}/></label><label>客户端私钥<input type="file" onChange={e=>setFiles(v=>({...v,client_private_key:e.target.files?.[0]}))}/></label></div><Btn type="submit" primary disabled={busy||!!pending||editor.state!=='ready'}>保存节点授权</Btn></fieldset></form></>}
   <h4>已保存的节点授权</h4>{!info.authorizations.length?<p>尚无通过此页面保存的授权。</p>:<ul>{info.authorizations.map(row=><li key={row.id}><strong>{row.name}</strong> · {row.host}:{row.port} · {row.node_id} · {row.enabled?(row.available?'已启用':row.reason||'暂不可用'):'已停用'} <Btn disabled={busy||!!pending||editor.state!=='ready'||(!row.enabled&&!row.available)} onClick={()=>status(row)}>{row.enabled?'停用授权':'恢复授权'}</Btn></li>)}</ul>}<Btn disabled={busy} onClick={()=>load().catch(e=>setError(e.message))}>刷新授权</Btn>
  </>}
 </section>;
}
