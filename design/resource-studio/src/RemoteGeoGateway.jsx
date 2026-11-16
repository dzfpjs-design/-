import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {stableStringify} from './content-signature.js';
import {checkGeoAttempt,verifyGeoReceipt,geoPath,geoBody} from './remote-geo-contract.mjs';
const DefaultField=({label,children})=><label>{label}{children}</label>;
const fresh=()=>crypto.randomUUID();

/** Administrator imports one saved remote TIFF through the GeoScene pipeline. */
export default function RemoteGeoGateway({protocol,account,Btn,Field=DefaultField,onRegisterGuard,onImported,onGo,onConfigure,request=json}){
 const valid=protocol==='ftp'||protocol==='smb',scope=JSON.stringify([protocol,account?.workspace_id,account?.user_id,account?.role]);
 const prefix='remote-geo:'+encodeURIComponent(JSON.stringify([protocol,account?.workspace_id,account?.user_id])),key=prefix+':pending',alive=useRef(false),scopeRef=useRef(scope),identity=useRef({scope,epoch:0}),selectedRef=useRef(''),lock=useRef(false),guardRef=useRef(onRegisterGuard),current=useRef({});
 if(identity.current.scope!==scope)identity.current={scope,epoch:identity.current.epoch+1};const epoch=identity.current.epoch;scopeRef.current=scope;guardRef.current=onRegisterGuard;
 const [connections,setConnections]=useState(null),[connectionId,setConnectionId]=useState(''),[listings,setListings]=useState([]),[imports,setImports]=useState([]),[available,setAvailable]=useState(null),[listingId,setListingId]=useState(''),[fileId,setFileId]=useState('');
 const [pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[storageError,setStorageError]=useState(false),[scene,setScene]=useState(null),[unverified,setUnverified]=useState(0);
 current.current={pending,busy,storageError};const active=original=>alive.current&&scopeRef.current===original&&identity.current.epoch===epoch;
 const req=(path,options={})=>request(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 const p='/'+protocol,g=p+'/geo';
 async function journal(fn){if(!navigator?.locks?.request)throw Error('浏览器无法保护多标签页的原请求。');return navigator.locks.request(prefix+':journal',{mode:'exclusive'},fn);}
 async function retain(a,original){await journal(()=>{if(!active(original))throw Error('账号或工作区已变化');if(localStorage.getItem(key))throw Error('已有待核对请求');const text=JSON.stringify(checkGeoAttempt(a));localStorage.setItem(key,text);if(localStorage.getItem(key)!==text)throw Error('无法保存原请求');});}
 async function release(a,original){await journal(()=>{if(!active(original))throw Error('账号或工作区已变化');const value=localStorage.getItem(key);if(!value||stableStringify(checkGeoAttempt(JSON.parse(value)))!==stableStringify(a))throw Error('本机原请求已变化');localStorage.removeItem(key);if(localStorage.getItem(key)!==null)throw Error('无法完成请求核对');});}
 async function loadBase(original=scope){const value=await req(p+'/connections');if(!Array.isArray(value?.connections))throw Error('连接清单无法核对');if(active(original))setConnections(value.connections);return value.connections;}
 async function loadHistory(id,original=scope){
  const base=g+'/connections/'+encodeURIComponent(id),[l,i]=await Promise.all([req(base+'/listings'),req(base+'/imports')]);
  if(!Array.isArray(l?.listings)||!Array.isArray(i?.imports)||typeof l.source_available!=='boolean'||typeof i.source_available!=='boolean')throw Error('影像历史或授权状态无法核对');
  let bad=0;const checked=(arr,kind)=>arr.flatMap(r=>{try{if(r?.state!=='succeeded'||r?.kind!==kind||r?.submission?.connection_id!==id)throw Error();return [verifyGeoReceipt(r,{kind,submission:r.submission},protocol)];}catch{bad++;return [];}});
  const ls=checked(l.listings,'geo_listing'),im=checked(i.imports,'geo_import'),access=l.source_available&&i.source_available;
  if(active(original)&&selectedRef.current===id){setAvailable(access);setUnverified(bad);setListings(ls);setImports(im);setListingId(old=>ls.some(x=>x.result.listing_id===old)?old:(ls[0]?.result.listing_id||''));setFileId('');}
  return {listings:ls,imports:im,available:access};
 }
 useEffect(()=>{alive.current=true;setConnections(null);setConnectionId('');setListings([]);setImports([]);setPending(null);setBusy(false);setScene(null);setError('');setNotice('');setAvailable(null);setStorageError(false);lock.current=false;
  selectedRef.current='';if(!valid||!account?.workspace_id||!account?.user_id)return()=>{alive.current=false;};
  try{const saved=localStorage.getItem(key);if(saved)setPending(checkGeoAttempt(JSON.parse(saved)));}catch{setStorageError(true);setError('无法读取本机恢复记录，操作已暂停；原记录保留。');}
  if(account.role==='admin')loadBase(scope).catch(e=>{if(active(scope))setError('无法读取目录连接：'+e.message);});return()=>{alive.current=false;};
 },[scope]);
 useEffect(()=>{const guard=()=>!lock.current&&!current.current.pending&&!current.current.busy&&!current.current.storageError;guardRef.current?.(guard);return()=>guardRef.current?.(null,guard);},[]);
 async function choose(id){if(pending||busy)return;selectedRef.current=id;setConnectionId(id);setListings([]);setImports([]);setAvailable(null);setListingId('');setFileId('');setScene(null);setError('');if(!id)return;try{await loadHistory(id);}catch(e){if(active(scope))setError('历史读取失败：'+e.message);}}
 async function complete(receipt,a,original){
  verifyGeoReceipt(receipt,a,protocol);
  if(receipt.state==='pending'){if(active(original))setNotice('原请求仍在处理，可稍后核对。');return;}
  if(receipt.state==='failed'){if(active(original))setError('处理未完成：'+(receipt.error||'请核对连接后重试')+'；原请求保留。');return;}
  if(receipt.state==='cancelled'){await release(a,original);if(active(original)){setPending(null);setNotice('原请求已结束。');}return;}
  const saved=verifyGeoReceipt(await req(g+'/requests/'+encodeURIComponent(a.submission.request_id)),a,protocol);
  if(saved.state!=='succeeded'||stableStringify(saved.result)!==stableStringify(receipt.result))throw Error('服务器回执不一致');
  if(account.role!=='admin'){
   if(a.kind==='geo_import'){
    const savedScene=await req(g+'/requests/'+encodeURIComponent(a.submission.request_id)+'/result');
    if(savedScene?.scene_id!==receipt.result.scene_id||savedScene?.evidence_id!==receipt.result.evidence_id||stableStringify(savedScene.source)!==stableStringify(receipt.result.source))throw Error('影像原件与保存记录不一致');
    if(active(original))setScene(savedScene);
   }
   await release(a,original);if(active(original)){setPending(null);setNotice(a.kind==='geo_import'?'原影像已保存并完成核对。':'原影像目录请求已完成核对。');}return;
  }
  const id=a.submission.connection_id,base=g+'/connections/'+encodeURIComponent(id),snapshotId=a.kind==='geo_listing'?receipt.result.listing_id:a.submission.listing_id;
  const snapshot=await req(base+'/listings/'+encodeURIComponent(snapshotId));
  if(typeof snapshot?.source_available!=='boolean'||snapshot?.listing?.state!=='succeeded'||snapshot.listing.kind!=='geo_listing'||snapshot.listing.submission?.connection_id!==id)throw Error('原影像目录快照无法核对');
  const verified=verifyGeoReceipt(snapshot.listing,{kind:'geo_listing',submission:snapshot.listing.submission},protocol);
  if(a.kind==='geo_listing'&&stableStringify(verified.result)!==stableStringify(receipt.result))throw Error('目录与回执不一致');
  if(a.kind==='geo_import'){
   const source=receipt.result.source,originalFile=verified.result.files.find(x=>x.id===a.submission.file_id);
   if(!originalFile||originalFile.name!==source.name||originalFile.size!==source.size||originalFile.modified!==source.modified)throw Error('影像来源与目录快照不一致');
   const savedScene=await req(g+'/requests/'+encodeURIComponent(a.submission.request_id)+'/result');
   if(savedScene?.scene_id!==receipt.result.scene_id||savedScene?.evidence_id!==receipt.result.evidence_id||stableStringify(savedScene.source)!==stableStringify(source))throw Error('影像原件与保存记录不一致');
   if(active(original))setScene(savedScene);
  }
  selectedRef.current=id;
  // History can be unavailable; the exact original snapshot and original file
  // have already been independently verified before clearing the journal.
  let history;try{history=await loadHistory(id,original);}catch{if(active(original))setAvailable(snapshot.source_available);}
  await release(a,original);if(!active(original))return;setPending(null);setConnectionId(id);setNotice(a.kind==='geo_listing'?'目录已保存；远端文件变化后请重新读取。':'影像原件已保存并进入本地解析队列。');
  if(a.kind==='geo_listing'&&!history){setListings(old=>[receipt,...old]);setListingId(receipt.result.listing_id);}
  if(a.kind==='geo_import'&&onImported){try{await onImported();}catch{if(active(original))setNotice('原件已保存；影像列表暂未刷新，请手动刷新。');}}
 }
 async function execute(a){if(lock.current||busy||pending||storageError)return;lock.current=true;const original=scope;setBusy(true);setError('');let retained=false;
  try{await retain(a,original);retained=true;if(!active(original))return;setPending(a);const receipt=await req(geoPath(protocol,a),{method:'POST',body:JSON.stringify(geoBody(a))});if(active(original))await complete(receipt,a,original);}
  catch(e){if(active(original))setError((retained?'操作结果待核对，原请求保留：':'操作未发送：')+e.message);}
  finally{if(active(original)){lock.current=false;setBusy(false);}}
 }
 async function recover(mode){if(!pending||lock.current||busy)return;lock.current=true;const a=pending,original=scope;setBusy(true);setError('');
  try{let value;if(mode==='cancel')value=await req(g+'/requests/'+encodeURIComponent(a.submission.request_id)+'/cancel',{method:'POST',body:JSON.stringify(a)});else if(mode==='retry')value=await req(geoPath(protocol,a),{method:'POST',body:JSON.stringify(geoBody(a))});else value=await req(g+'/requests/'+encodeURIComponent(a.submission.request_id));if(active(original))await complete(value,a,original);}
  catch(e){if(active(original))setError('原请求仍待核对：'+e.message);}finally{if(active(original)){lock.current=false;setBusy(false);}}
 }
 async function readScene(r){try{const s=await req(g+'/requests/'+encodeURIComponent(r.request_id)+'/result');if(s.scene_id!==r.result.scene_id||stableStringify(s.source)!==stableStringify(r.result.source))throw Error('原件来源不匹配');if(active(scope))setScene(s);}catch(e){if(active(scope))setError('无法核对影像原件：'+e.message);}}
 if(!valid||!account?.workspace_id||!account?.user_id)return null;
 if(account.role!=='admin'&&!pending&&!scene&&!storageError)return null;
 const disabled=busy||!!pending||storageError,selected=(connections||[]).find(x=>x.id===connectionId),listing=listings.find(x=>x.result.listing_id===listingId),file=listing?.result.files.find(x=>x.id===fileId);
 return <section className="local-model-card" aria-label={protocol.toUpperCase()+'遥感影像'}><h3>从 {protocol.toUpperCase()} 导入遥感影像</h3>
  <p>选择已保存的目录连接，读取并导入 GeoTIFF 原件。单幅最大 512 MB，影像进入本地影像解析，不会作为业务文档处理。</p>
  {protocol==='ftp'&&<p>FTP 账号、密码和文件以明文传输，仅限明确批准的内网。</p>}
  {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{busy&&<p role="status">正在处理，请稍候。大影像传输可能需要数分钟；中断后可核对原请求。</p>}
  {pending&&<aside className="info"><p>有一项影像操作尚待核对，不能重复创建请求。</p><Btn disabled={busy} onClick={()=>recover('read')}>核对原请求</Btn>{account.role==='admin'&&<Btn disabled={busy} onClick={()=>recover('retry')}>重试原请求</Btn>}<Btn disabled={busy} onClick={()=>recover('cancel')}>安全结束原请求</Btn></aside>}
  {account.role!=='admin'&&<p>当前没有管理员权限，可核对或安全结束本人之前提交的影像请求。</p>}
  {account.role==='admin'&&<><Field label="选择已保存的目录连接"><select aria-label={protocol.toUpperCase()+'影像目录连接'} value={connectionId} disabled={disabled} onChange={e=>choose(e.target.value)}><option value="">请选择连接</option>{(connections||[]).map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
  {connections?.length===0&&<p>还没有目录连接，请先在{protocol.toUpperCase()}资料网关中保存已授权连接。{onConfigure&&<Btn disabled={disabled} onClick={onConfigure}>前往配置目录连接</Btn>}</p>}
  {selected&&<><p>{available===false?'此来源授权已撤销，已保存的影像仍可核对。':available===null?'正在核对来源授权。':'来源授权有效。'}</p><Btn disabled={disabled} onClick={()=>choose(selected.id)}>刷新记录</Btn><Btn disabled={disabled||available!==true} onClick={()=>execute({kind:'geo_listing',submission:{request_id:fresh(),version:selected.version,connection_id:selected.id}})}>读取影像目录</Btn>
   {!!listings.length&&<><Field label="已保存的影像目录"><select aria-label="影像目录快照" value={listingId} disabled={disabled} onChange={e=>{setListingId(e.target.value);setFileId('');}}>{listings.map((x,i)=><option key={x.result.listing_id} value={x.result.listing_id}>{x.result.files.length} 幅影像 · {i?'历史目录 '+(i+1):'最近一次'}</option>)}</select></Field>
    {!listing?.result.files.length?<p>目录中没有符合条件的 GeoTIFF。</p>:<Field label="选择影像"><select aria-label="要导入的影像" disabled={disabled} value={fileId} onChange={e=>setFileId(e.target.value)}><option value="">请选择影像</option>{listing.result.files.map(x=><option key={x.id} value={x.id}>{x.name} · {(x.size/1048576).toFixed(1)} MB</option>)}</select></Field>}
    <Btn disabled={disabled||!file||available!==true} onClick={()=>execute({kind:'geo_import',submission:{request_id:fresh(),version:selected.version,connection_id:selected.id,listing_id:listingId,file_id:fileId}})}>导入所选影像</Btn></>}
   <h4>影像导入记录</h4>{unverified>0&&<p role="alert">有 {unverified} 条记录无法核对，请联系管理员。</p>}{!imports.length?<p>还没有影像导入记录。</p>:<ul>{imports.map(r=><li key={r.request_id}>{r.result.source.name} <Btn disabled={busy} onClick={()=>readScene(r)}>查看原件和解析状态</Btn><details><summary>查看来源</summary><p>{r.result.source.size} 字节 · 原件摘要 {r.result.source.sha256}</p></details></li>)}</ul>}</>}
  </>}
  {scene&&<aside className="info"><strong>{scene.source.name}</strong><p>{({queued:'等待解析',running:'正在解析',parsed:'影像信息解析完成，待设置波段',ready:'影像已完成设置',failed:'解析未完成，原件保留',cancelled:'解析已取消，原件保留'})[scene.status]||'解析状态待核对'}</p>{scene.error&&<p>{scene.error}</p>}{onGo&&<Btn onClick={()=>onGo(scene.scene_id)}>前往影像配置</Btn>}</aside>}
 </section>;
}
