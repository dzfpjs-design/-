import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';

export default function MapWorkspaceGrants({account,Btn,onRegisterGuard}) {
 const key=`map-workspace-grant:${account.workspace_id}:${account.user_id}`;
 const [rows,setRows]=useState(null),[endpoint,setEndpoint]=useState(''),[pending,setPending]=useState(null),[choice,setChoice]=useState(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[storageError,setStorageError]=useState(false);
 const current=useRef({pending:null,choice:null,busy:false}),cb=useRef(onRegisterGuard),alive=useRef(true),scope=useRef(key);cb.current=onRegisterGuard;scope.current=key;current.current={pending,choice,busy};
 const req=(path,options={})=>json(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 const active=original=>alive.current&&scope.current===original;
 function safeAddress(value){try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password&&!u.search&&!u.hash;}catch{return false;}}
 async function reload(){const original=key;try{const r=await req('/map-workspace-grants');if(!Array.isArray(r.grants))throw Error('授权清单格式无效');if(active(original))setRows(r.grants);}catch(e){if(active(original))setError('无法读取工作区授权：'+e.message);}}
 function pendingEntries(){const found=[];for(let i=0;i<localStorage.length;i++){const name=localStorage.key(i);if(name?.startsWith(key+':pending:')){const value=JSON.parse(localStorage.getItem(name));if(!value||typeof value.request_id!=='string'||name!==key+':pending:'+value.request_id||typeof value.endpoint!=='string'||!safeAddress(value.endpoint)||typeof value.secret_ref!=='string'||typeof value.allowed!=='boolean'||!Number.isInteger(value.version))throw Error('恢复记录损坏');found.push(value);}}return found;}
 useEffect(()=>{alive.current=true;setRows(null);setChoice(null);setEndpoint('');setError('');setNotice('');setBusy(false);setStorageError(false);try{sessionStorage.removeItem(key);const found=pendingEntries();if(found.length)setPending(found[0]);else setPending(null);}catch{setStorageError(true);}reload();return()=>{alive.current=false;};},[key]);
 useEffect(()=>{const guard=()=>!current.current.pending&&!current.current.choice&&!current.current.busy;cb.current?.(guard);return()=>cb.current?.(null,guard);},[]);
 function retain(body){try{if(!safeAddress(body.endpoint))throw Error();const k=key+':pending:'+body.request_id;localStorage.setItem(k,JSON.stringify(body));if(localStorage.getItem(k)!==JSON.stringify(body))throw Error();return true;}catch{setStorageError(true);setError('无法保留原请求，请检查地址和浏览器存储。');return false;}}
 function release(body){try{localStorage.removeItem(key+':pending:'+body.request_id);const found=pendingEntries();setPending(found[0]||null);return true;}catch{setStorageError(true);setError('原请求处理完毕，但本机恢复记录无法更新。');return false;}}
 function sameSubmission(value,body){const fields=['request_id','endpoint','secret_ref','allowed','version'];return value&&typeof value==='object'&&Object.keys(value).length===fields.length&&fields.every(k=>value[k]===body[k]);}
 function sameResult(value,body){return value&&value.request_id===body.request_id&&value.endpoint===body.endpoint&&value.secret_ref===body.secret_ref&&value.allowed===body.allowed&&value.version===body.version+1;}
 function edit(value){setEndpoint(value);}
 async function send(body){const original=key;setBusy(true);setError('');try{const result=await req('/map-workspace-grants',{method:'POST',body:JSON.stringify(body)});if(!sameResult(result,body))throw Error('原操作结果与请求不一致');if(!active(original)||!release(body))return;setChoice(null);setEndpoint('');setNotice(body.allowed?'工作区读取授权已保存。':'工作区读取授权已撤销；已保存资料不会删除。');await reload();}catch(e){if(active(original))setError('原操作仍待核对。请检查登录与当前工作区后重试，或安全结束原请求：'+e.message);}finally{if(active(original))setBusy(false);}}
 async function cancelPending(body){const original=key;setBusy(true);setError('');try{const result=await req('/map-workspace-grants/requests/'+encodeURIComponent(body.request_id)+'/cancel',{method:'POST'});if(result.request_id!==body.request_id||!['applied','cancelled'].includes(result.state)||result.state==='applied'&&(!sameSubmission(result.submission,body)||!sameResult(result.result,body)))throw Error('原操作回执无法核对');if(!active(original)||!release(body))return;setChoice(null);setEndpoint(body.endpoint);setNotice(result.state==='applied'?'原操作已完成，结果已核对。':'原操作已安全结束，可重新修改。');await reload();}catch(e){if(active(original))setError('原操作尚未安全结束，请保留请求并重试：'+e.message);}finally{if(active(original))setBusy(false);}}
 function confirm(){const value={...choice,request_id:crypto.randomUUID()};if(retain(value)){setPending(value);send(value);}}
 function prepareNew(){try{const u=new URL(endpoint.trim());if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash)throw Error();const value=u.href.replace(/\/$/,'');const existing=rows.find(x=>x.endpoint===value&&!x.secret_ref);if(existing)throw Error('已有此服务授权，请在下方修改。');setChoice({endpoint:value,secret_ref:'',allowed:true,version:0});setError('');}catch(e){setError(e.message||'请填写不含凭据和查询参数的服务地址。');}}
 if(account.role!=='admin')return null;
 return <section className="local-model-card" aria-label="地图服务工作区授权"><h3>地图服务工作区授权</h3>
  <p>先输入运维已批准当前工作区使用的确切 IP 服务地址，将它授权给本工作区。这里不展示其他工作区的服务或凭据；未获运行环境和本工作区预先批准的地址不能接入。带凭据的服务仍需运维为本工作区预先授权。</p>
  <p>运行环境的地址授权调整后，配置后台和资料处理器须加载一致设置并分别重启；此处暂不能检查两者的设置是否一致。</p>
  <p>撤销后不能再凭此授权发起新的服务读取，正在读取的操作可能被中止。撤销可能需要等待正在进行的读取释放授权锁，等待时间受服务连接超时影响。已保存资料会保留，其访问由相应业务权限控制；部分已登记的地图来源可能受影响。</p>
  {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {storageError&&<p role="alert">恢复记录不可用，授权操作已停用。</p>}
  {pending&&<aside className="info"><p>上次授权操作结果尚未确认；服务地址和原请求已保留。</p><Btn disabled={busy||storageError} onClick={()=>send(pending)}>核对并重试原操作</Btn><Btn disabled={busy||storageError} onClick={()=>cancelPending(pending)}>安全结束原请求</Btn></aside>}
  {!pending&&choice&&<aside className="info"><p>确认{choice.allowed?'授权':'撤销'}：{choice.endpoint}{choice.secret_ref?' · 已授权的凭据引用':' · 无凭据'}？</p><Btn disabled={busy||storageError} onClick={confirm}>确认{choice.allowed?'授权':'撤销'}</Btn><Btn disabled={busy} onClick={()=>setChoice(null)}>返回</Btn></aside>}
  {!pending&&!choice&&<><label>已批准的地图服务地址 <input aria-label="待授权地图服务地址" value={endpoint} onChange={e=>edit(e.target.value)} disabled={busy||storageError}/></label><Btn disabled={busy||storageError||!rows||!endpoint.trim()} onClick={prepareNew}>核对并授权此工作区</Btn></>}
  {!rows?<p>正在读取当前工作区授权…</p>:!rows.length?<p>当前工作区还没有地图服务授权。</p>:<ul>{rows.map(row=><li key={row.endpoint+'|'+row.secret_ref}><strong>{row.endpoint}</strong> · {row.secret_ref?'已授权的凭据引用':'无凭据'} · {row.allowed?'已授权':'已撤销'}{row.origin==='legacy'?'（原运行环境授权）':''} <Btn disabled={busy||storageError||!!pending||!!choice} onClick={()=>setChoice({endpoint:row.endpoint,secret_ref:row.secret_ref,allowed:!row.allowed,version:row.version})}>{row.allowed?'撤销授权':'重新授权'}</Btn></li>)}</ul>}
 </section>;
}
