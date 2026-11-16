import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';

const initial={name:'',host:'',port:'502',unit:'1',start:'0',end:'31',readHolding:true,readInput:false};

/** Keep one editor per account/workspace; never let two stale forms overwrite a draft. */
export function useAuthorizationEditor(scope,draftKey,restore){
 const [state,setState]=useState('loading'),[revision,setRevision]=useState(0);
 const lease=useRef(null),restoreRef=useRef(restore);restoreRef.current=restore;
 useEffect(()=>{
  let cancelled=false,release=null;setState('loading');
  if(!navigator?.locks?.request){setState('unsupported');return()=>{cancelled=true}};
  let task;
  try{task=navigator.locks.request(draftKey+':editor',{mode:'exclusive',ifAvailable:true},async lock=>{
   if(cancelled)return;
   if(!lock){setState('busy');return}
   try{restoreRef.current()}catch{setState('corrupt');return}
   if(cancelled)return;
   await new Promise(resolve=>{release=resolve;lease.current=resolve;setState('ready')});
  })}catch{setState('unsupported');return()=>{cancelled=true}}
  task.catch(()=>{if(!cancelled)setState('unsupported')});
  return()=>{cancelled=true;if(release)release();if(lease.current===release)lease.current=null};
 },[scope,draftKey,revision]);
 return {state,held:()=>!!lease.current,retry:()=>setRevision(v=>v+1)};
}

/** Administrator approves a bounded read-only device; no device command is sent. */
export default function ManagedModbusAuthorizations({account,Btn,onChanged=()=>{},request=json}){
 const identity=JSON.stringify([account?.workspace_id,account?.user_id,account?.role]);
 const scope=account?.workspace_id+':'+account?.user_id,key='managed-modbus-auth-pending:'+scope,draftKey='managed-modbus-auth-draft:'+scope;
 const [form,setForm]=useState(initial),[info,setInfo]=useState(null),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState('');
 const alive=useRef(false),current=useRef(identity),active=()=>alive.current&&current.current===identity;current.current=identity;
 const call=(path,opts={})=>request(path,{...opts,headers:{...(opts.headers||{}),'X-Workspace-ID':account.workspace_id}});
 async function load(){const value=await call('/modbus/authorizations');if(active())setInfo(value)}
 const editor=useAuthorizationEditor(scope,draftKey,()=>{
  const raw=localStorage.getItem(draftKey),value=raw?JSON.parse(raw):initial;
  if(!value||typeof value!=='object'||Object.keys(initial).some(k=>typeof value[k]!==typeof initial[k]))throw Error('草稿无法核对');
  setForm({...initial,...value});
  const saved=localStorage.getItem(key);
  if(saved){const item=JSON.parse(saved);if(item.scope!==scope||!item.submission?.request_id||!['create','status'].includes(item.kind))throw Error('原请求无法核对');setPending(item)}else setPending(null);
 });
 useEffect(()=>{alive.current=true;setInfo(null);setError('');setNote('');setPending(null);setForm(initial);
  try{const f=localStorage.getItem(draftKey);if(f)setForm({...initial,...JSON.parse(f)});const raw=localStorage.getItem(key);if(raw){const item=JSON.parse(raw);if(item.scope!==scope||!item.submission?.request_id||!['create','status'].includes(item.kind))throw Error();setPending(item)}}catch{setError('本机授权草稿或原请求无法读取，请检查浏览器存储。')}
  if(account.role==='admin')load().catch(e=>{if(active())setError(e.message)});
  return()=>{alive.current=false};
 },[identity]);
 function edit(name,value){if(!editor.held()||busy||pending){setError('请先取得编辑权并核对未完成操作。');return}const next={...form,[name]:value};try{localStorage.setItem(draftKey,JSON.stringify(next));setForm(next);setError('')}catch{setError('授权输入未能保存，请检查浏览器存储。')}}
 async function lock(fn){if(!navigator?.locks?.request)throw Error('浏览器不支持安全的多标签操作。');return navigator.locks.request(key,{mode:'exclusive'},fn)}
 async function execute(intent,resolve=false){if(busy)return;if(!editor.held()){setError('请先取得此授权草稿的编辑权。');return}setBusy(true);setError('');setNote('');const original=identity;
  try{await lock(async()=>{
   if(!active()||current.current!==original)throw Error('账号或工作区已变化。');
   const raw=localStorage.getItem(key);
   if(resolve){if(!raw||JSON.stringify(JSON.parse(raw))!==JSON.stringify(intent))throw Error('本机原请求已变化。')}
   else {if(raw)throw Error('已有待核对授权，请先处理。');localStorage.setItem(key,JSON.stringify(intent));if(localStorage.getItem(key)!==JSON.stringify(intent))throw Error('原请求未能保存，未提交。');setPending(intent)}
   const endpoint=resolve?'/modbus/authorizations/operations/'+encodeURIComponent(intent.submission.request_id)+'/resolve':intent.kind==='create'?'/modbus/authorizations':'/modbus/authorizations/'+encodeURIComponent(intent.grant_id)+'/status';
   const body=resolve?{kind:intent.kind,submission:intent.submission,grant_id:intent.grant_id}:intent.submission;
   const receipt=await call(endpoint,{method:'POST',body:JSON.stringify(body)});
   if(!active()||current.current!==original)throw Error('账号或工作区已变化，原请求保留。');
   if(receipt?.request_id!==intent.submission.request_id||receipt?.kind!==intent.kind||!['succeeded','cancelled'].includes(receipt.state))throw Error('授权回执无法核对，原请求保留。');
   if(localStorage.getItem(key)!==JSON.stringify(intent))throw Error('本机原请求已变化，原请求保留。');
   localStorage.removeItem(key);setPending(null);setNote(receipt.state==='cancelled'?'原请求已安全结束，不会执行。':'授权已保存。');
   if(intent.kind==='create'&&receipt.state==='succeeded'){localStorage.removeItem(draftKey);setForm(initial)}
   if(account.role==='admin'){try{await load()}catch{if(active())setError('授权已保存，但列表暂时无法刷新；请稍后点击刷新授权。')}}onChanged();
  })}catch(e){if(active()){let retained=true;try{retained=!!localStorage.getItem(key)}catch{}setError((e.message||'操作未完成')+(retained?'；原请求保留，请核对。':''))}}
  finally{if(active())setBusy(false)}
 }
 function create(e){e.preventDefault();if(pending)return;
  const values=['port','unit','start','end'].map(k=>Number(form[k]));
  if(!form.name.trim()||!/^\d{1,3}(\.\d{1,3}){3}$/.test(form.host)||values.some(n=>!Number.isInteger(n))||(!form.readHolding&&!form.readInput)){setError('请填写名称、IPv4 地址、有效数字，并至少选择一种读取类型。');return}
  const submission={request_id:crypto.randomUUID(),name:form.name.trim(),host:form.host,port:values[0],unit:values[1],start:values[2],end:values[3],functions:[...(form.readHolding?[3]:[]),...(form.readInput?[4]:[])]};
  execute({scope,kind:'create',grant_id:null,submission});
 }
 function status(row){if(pending)return;execute({scope,kind:'status',grant_id:row.id,submission:{request_id:crypto.randomUUID(),version:row.version,enabled:!row.enabled}})}
 return <section className="media-form" aria-label="Modbus 设备授权"><h3>授权设备读取范围</h3><p>填写设备地址和允许读取的寄存器范围；系统只读取，不向设备写入。地址必须在本机预先允许的内网范围内。</p>
  {error&&<p role="alert">{error}</p>}{note&&<p role="status">{note}</p>}
  {editor.state!=='ready'&&<aside className="info"><p role="status">{editor.state==='busy'?'另一标签页正在编辑该授权，请返回原标签页或关闭后重新获取编辑权。':editor.state==='corrupt'?'本机草稿或原请求无法核对，已停止编辑；请保留本机记录。':editor.state==='unsupported'?'当前浏览器不能保护多标签草稿，请更换支持安全锁的浏览器。':'正在取得草稿编辑权…'}</p>{editor.state==='busy'&&<Btn onClick={editor.retry}>重新获取编辑权</Btn>}</aside>}
  {pending&&<aside className="info"><p>上次授权操作尚待核对，原请求已保留。</p><Btn disabled={busy||editor.state!=='ready'} onClick={()=>execute(pending,true)}>核对并结束原请求</Btn></aside>}
  {account.role!=='admin'?<p>当前账号不能更改授权；仍可核对本人未完成的请求。</p>:!info?<p>正在读取授权范围…</p>:<>
   {!info.enabled||!info.networks.length?<p>系统尚未设置允许的设备网络，请联系系统管理员完成一次网络范围配置。</p>:<><p>允许的网络：{info.networks.join('、')}</p><form onSubmit={create}><fieldset disabled={busy||!!pending||editor.state!=='ready'} style={{border:0,margin:0,padding:0}}>
    <label>授权名称<input value={form.name} onChange={e=>edit('name',e.target.value)} maxLength={100}/></label>
    <label>设备 IPv4 地址<input value={form.host} onChange={e=>edit('host',e.target.value)} placeholder="例如 192.168.1.20"/></label>
    <label>端口<input type="number" min="1" max="65535" value={form.port} onChange={e=>edit('port',e.target.value)}/></label>
    <label>设备站号<input type="number" min="1" max="247" value={form.unit} onChange={e=>edit('unit',e.target.value)}/></label>
    <label>允许读取的起始寄存器<input type="number" min="0" max="65535" value={form.start} onChange={e=>edit('start',e.target.value)}/></label>
    <label>允许读取的结束寄存器<input type="number" min="0" max="65535" value={form.end} onChange={e=>edit('end',e.target.value)}/></label>
    <fieldset><legend>允许读取的类型</legend><label><input type="checkbox" checked={form.readHolding} onChange={e=>edit('readHolding',e.target.checked)}/>保持寄存器</label><label><input type="checkbox" checked={form.readInput} onChange={e=>edit('readInput',e.target.checked)}/>输入寄存器</label></fieldset>
    <Btn type="submit" primary disabled={busy||!!pending||editor.state!=='ready'}>保存读取授权</Btn></fieldset></form></>}
   <h4>已保存的设备授权</h4>{!info.authorizations.length?<p>尚无通过此页面保存的授权。</p>:<ul>{info.authorizations.map(row=><li key={row.id}><strong>{row.name}</strong> · {row.host}:{row.port} · 站号 {row.unit} · 寄存器 {row.start}–{row.end} · {row.enabled?(row.available?'已启用':'超出当前网络范围'):'已停用'} <Btn disabled={busy||!!pending||editor.state!=='ready'||(!row.enabled&&!row.available)} onClick={()=>status(row)}>{row.enabled?'停用授权':'恢复授权'}</Btn></li>)}</ul>}
   <Btn disabled={busy} onClick={()=>load().catch(e=>setError(e.message))}>刷新授权</Btn>
  </>}
 </section>;
}
