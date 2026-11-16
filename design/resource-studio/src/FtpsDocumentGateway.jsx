import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {stableStringify} from './content-signature.js';
import {checkFtpsAttempt,verifyFtpsReceipt,ftpsBody,ftpsPath} from './ftps-document-contract.mjs';

const uid=()=>crypto.randomUUID();
const cleanName=value=>typeof value==='string'&&value.trim().length>0&&value.length<=100&&!/[\u0000-\u001f\u007f]/.test(value)&&!/(?:sk-[a-z0-9_-]{10,}|bearer\s+\S+|(?:密码|密钥|password|secret|token|api[_-]?key)\s*[:=：])/i.test(value);
const DefaultField=({label,children})=><label>{label}{children}</label>;
function modifiedAt(value){
 const match=typeof value==='string'&&value.match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?:\.\d+)?$/);if(!match)return '远端修改时间未知';
 const parts=match.slice(1).map(Number),utc=Date.UTC(parts[0],parts[1]-1,parts[2],parts[3],parts[4],parts[5]),original=new Date(utc);
 if(!Number.isFinite(utc)||original.getUTCFullYear()!==parts[0]||original.getUTCMonth()!==parts[1]-1||original.getUTCDate()!==parts[2]||original.getUTCHours()!==parts[3]||original.getUTCMinutes()!==parts[4]||original.getUTCSeconds()!==parts[5])return '远端修改时间未知';
 return '远端修改：'+new Date(utc+8*3600*1000).toISOString().slice(0,19).replace('T',' ')+'（北京时间）';
}

/** Import a selected, already authorised FTPS file into setup materials. */
export default function FtpsDocumentGateway({account,Btn,Field=DefaultField,onRegisterGuard,onImported,onGo,authorizationRevision,request=json}){
 const scope=JSON.stringify([account?.workspace_id,account?.user_id,account?.role]);
 const prefix='ftps-document:'+encodeURIComponent(account?.workspace_id||'')+':'+encodeURIComponent(account?.user_id||'');
 const [grants,setGrants]=useState(null),[connections,setConnections]=useState(null),[connectionId,setConnectionId]=useState('');
 const [name,setName]=useState(''),[grantId,setGrantId]=useState(''),[listings,setListings]=useState([]),[imports,setImports]=useState([]),[unverifiedHistory,setUnverifiedHistory]=useState(0),[sourceAvailable,setSourceAvailable]=useState(null),[listingId,setListingId]=useState(''),[fileId,setFileId]=useState('');
 const [pending,setPending]=useState(null),[verified,setVerified]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[storageError,setStorageError]=useState(false),[material,setMaterial]=useState(null);
 const current=useRef({}),scopeRef=useRef(scope),identity=useRef({scope,epoch:0}),alive=useRef(false),operation=useRef(false),selectedRef=useRef(''),historySequence=useRef(0),guardCallback=useRef(onRegisterGuard);
 if(identity.current.scope!==scope)identity.current={scope,epoch:identity.current.epoch+1};const epoch=identity.current.epoch;scopeRef.current=scope;guardCallback.current=onRegisterGuard;
 const [draftError,setDraftError]=useState(false),[connectionsCursor,setConnectionsCursor]=useState(null),[listingCursor,setListingCursor]=useState(null),[importCursor,setImportCursor]=useState(null),[loadingMore,setLoadingMore]=useState(false);
 current.current={pending,busy,storageError,draftError};
 const active=original=>alive.current&&scopeRef.current===original&&identity.current.epoch===epoch;
 const requestScoped=(path,options={})=>request(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 const journalKey=attempt=>prefix+':pending:'+attempt.submission.request_id;
 function readPending(){
  const found=[];
  for(let i=0;i<localStorage.length;i++){
   const key=localStorage.key(i);if(!key?.startsWith(prefix+':pending:'))continue;
   const value=checkFtpsAttempt(JSON.parse(localStorage.getItem(key)));if(key!==journalKey(value))throw Error('原请求与账号范围不一致');found.push(value);
  }
  return found;
 }
 async function withLock(fn){if(!navigator?.locks?.request)throw Error('浏览器无法保护多标签页中的原请求，请使用支持安全锁的浏览器。');return navigator.locks.request(prefix+':journal',{mode:'exclusive'},fn);}
 async function retain(attempt,original){await withLock(()=>{if(!active(original))throw Error('账号或工作区已变化');if(readPending().length)throw Error('已有待核对请求，请先完成原操作。');const key=journalKey(attempt),value=JSON.stringify(checkFtpsAttempt(attempt));localStorage.setItem(key,value);if(localStorage.getItem(key)!==value)throw Error('本机无法保留原请求。');});}
 async function release(attempt,original){return withLock(()=>{if(!active(original))throw Error('账号或工作区已变化');const all=readPending(),key=journalKey(attempt),value=localStorage.getItem(key);if(!value||stableStringify(checkFtpsAttempt(JSON.parse(value)))!==stableStringify(attempt))throw Error('本机原请求已变化，请先核对。');localStorage.removeItem(key);if(localStorage.getItem(key)!==null)throw Error('本机原请求无法安全清理。');return all.filter(x=>x.submission.request_id!==attempt.submission.request_id);});}
 async function loadBase(original=scope){
  const [g,c]=await Promise.all([requestScoped('/ftps/grants'),requestScoped('/ftps/connections')]);
  if(!Array.isArray(g?.grants)||!Array.isArray(c?.connections))throw Error('连接清单格式无法核对。');
  if(active(original)){setGrants(g.grants);setConnections(old=>{const selected=old?.find(x=>x.id===selectedRef.current);return selected&&!c.connections.some(x=>x.id===selected.id)?[...c.connections,selected]:c.connections});setConnectionsCursor(c.next_cursor||null);}
  return {grants:g.grants,connections:c.connections};
 }
 async function loadHistory(id,original=scope){
  const sequence=++historySequence.current;
  const [l,i]=await Promise.all([requestScoped('/ftps/connections/'+encodeURIComponent(id)+'/listings'),requestScoped('/ftps/connections/'+encodeURIComponent(id)+'/imports')]);
  if(!Array.isArray(l?.listings)||!Array.isArray(i?.imports)||typeof l.source_available!=='boolean'||typeof i.source_available!=='boolean')throw Error('目录、导入记录或授权状态无法核对。');
  let unverified=0;const checked=(rows,kind)=>rows.flatMap(x=>{try{if(x?.state!=='succeeded'||x?.kind!==kind||x?.submission?.connection_id!==id)throw Error();return [verifyFtpsReceipt(x,{kind,submission:x.submission})];}catch{unverified++;return [];}});
  const ls=checked(l.listings,'listing'),im=checked(i.imports,'import');
  const available=l.source_available&&i.source_available;
  if(active(original)&&selectedRef.current===id&&sequence===historySequence.current){setSourceAvailable(available);setUnverifiedHistory(unverified);setListings(ls);setImports(im);setListingCursor(l.next_cursor||null);setImportCursor(i.next_cursor||null);setListingId(old=>ls.some(x=>x.result.listing_id===old)?old:(ls[0]?.result.listing_id||''));setFileId('');}
  return {listings:ls,imports:im,sourceAvailable:available};
 }
 useEffect(()=>{
  alive.current=true;operation.current=false;selectedRef.current='';setGrants(null);setConnections(null);setConnectionId('');setConnectionsCursor(null);setListingCursor(null);setImportCursor(null);setName('');setGrantId('');setListings([]);setImports([]);setUnverifiedHistory(0);setSourceAvailable(null);setListingId('');setFileId('');setPending(null);setVerified(null);setMaterial(null);setError('');setNotice('');setBusy(false);setStorageError(false);setDraftError(false);
  if(!account?.workspace_id||!account?.user_id||account.role!=='admin')return()=>{alive.current=false;};
  try{const found=readPending();if(found.length)setPending(found[0]);const draft=JSON.parse(localStorage.getItem(prefix+':draft')||'null');if(draft){if(typeof draft.name!=='string'||typeof draft.grantId!=='string'||draft.name&&!cleanName(draft.name))throw Error('草稿无法核对');setName(draft.name);setGrantId(draft.grantId);}}catch{setStorageError(true);setError('本机原请求或输入无法读取，操作已暂停；原记录保留。');}
  loadBase(scope).catch(e=>{if(active(scope))setError('无法读取已授权目录：'+e.message);});
  return()=>{alive.current=false;};
 },[scope,account?.role]);
 const seenAuthorizationRevision=useRef(authorizationRevision);
 useEffect(()=>{
  if(authorizationRevision===undefined||seenAuthorizationRevision.current===authorizationRevision||account?.role!=='admin')return;
  historySequence.current++;setSourceAvailable(null);
  if(busy||pending)return;
  seenAuthorizationRevision.current=authorizationRevision;
  const selected=selectedRef.current;
  Promise.all([loadBase(scope),...(selected?[loadHistory(selected,scope)]:[])]).catch(()=>{if(active(scope))setError('无法刷新授权目录或状态，请检查后台连接。');});
 },[authorizationRevision,busy,pending,scope]);
 useEffect(()=>{const guard=()=>!operation.current&&!current.current.pending&&!current.current.busy&&!current.current.storageError&&!current.current.draftError;guardCallback.current?.(guard);return()=>guardCallback.current?.(null,guard);},[]);
 function editDraft(nextName,nextGrant){setName(nextName);setGrantId(nextGrant);try{if(nextName&&!cleanName(nextName))throw Error('连接名称不能为空白或包含疑似凭据');localStorage.setItem(prefix+':draft',JSON.stringify({name:nextName,grantId:nextGrant}));setDraftError(false);setError('');}catch(e){setDraftError(true);setError('输入尚未保存：'+e.message+'；请修改后重试。');}}
 async function openConnection(id){if(pending||busy)return;selectedRef.current=id;setConnectionId(id);setListings([]);setImports([]);setListingCursor(null);setImportCursor(null);setUnverifiedHistory(0);setSourceAvailable(null);setListingId('');setFileId('');setMaterial(null);if(!id)return;setError('');try{await loadHistory(id);}catch(e){if(active(scope)&&selectedRef.current===id)setError('无法读取已保存的目录或导入记录：'+e.message);}}
 async function loadMoreConnections(){
  if(!connectionsCursor||loadingMore)return;const cursor=connectionsCursor,original=scope;setLoadingMore(true);
  try{const result=await requestScoped('/ftps/connections?cursor='+encodeURIComponent(cursor));if(!Array.isArray(result?.connections))throw Error();if(active(original)){setConnections(old=>[...(old||[]),...result.connections.filter(x=>!(old||[]).some(v=>v.id===x.id))]);setConnectionsCursor(result.next_cursor||null);}}
  catch{if(active(original))setError('无法加载更多连接，请重试。')}finally{if(active(original))setLoadingMore(false)}
 }
 async function loadMoreHistory(kind){
  const cursor=kind==='listing'?listingCursor:importCursor,id=selectedRef.current,sequence=historySequence.current,original=scope;
  if(!cursor||!id||loadingMore)return;setLoadingMore(true);
  try{const key=kind==='listing'?'listings':'imports',result=await requestScoped('/ftps/connections/'+encodeURIComponent(id)+'/'+key+'?cursor='+encodeURIComponent(cursor));if(!Array.isArray(result?.[key])||typeof result.source_available!=='boolean')throw Error();
   let invalid=0;const values=result[key].flatMap(x=>{try{if(x?.state!=='succeeded'||x?.kind!==kind||x?.submission?.connection_id!==id)throw Error();return [verifyFtpsReceipt(x,{kind,submission:x.submission})]}catch{invalid++;return []}});
   if(active(original)&&selectedRef.current===id&&sequence===historySequence.current){setSourceAvailable(result.source_available);setUnverifiedHistory(old=>old+invalid);if(kind==='listing'){setListings(old=>[...old,...values.filter(x=>!old.some(v=>v.request_id===x.request_id))]);setListingCursor(result.next_cursor||null)}else{setImports(old=>[...old,...values.filter(x=>!old.some(v=>v.request_id===x.request_id))]);setImportCursor(result.next_cursor||null)}}
  }catch{if(active(original)&&selectedRef.current===id)setError('无法加载更多历史记录，请重试。')}finally{if(active(original))setLoadingMore(false)}
 }
 async function checkedCompletion(receipt,attempt,original,releaseWithoutHistory=false){
  verifyFtpsReceipt(receipt,attempt);
  if(receipt.state==='pending'){if(active(original))setNotice('服务器仍在处理原请求，可稍后核对；不会自动创建新请求。');return;}
  if(receipt.state==='failed'){if(active(original))setError('原请求未成功'+(typeof receipt.error==='string'?'：'+receipt.error:'。')+' 请核对后选择“安全结束原请求”；原始资料与服务器记录保留。');return;}
  if(receipt.state==='cancelled'){if(!active(original))return;const rest=await release(attempt,original);if(active(original)){setVerified(null);setPending(rest[0]||null);setNotice(rest.length?'原请求已安全结束；还有其他请求需要核对。':'原请求已安全结束，可以重新选择。');}return;}
  const savedReceipt=verifyFtpsReceipt(await requestScoped('/ftps/requests/'+encodeURIComponent(attempt.submission.request_id)),attempt);
  if(savedReceipt.state!=='succeeded'||stableStringify(savedReceipt.result)!==stableStringify(receipt.result))throw Error('服务器尚未读回相同的操作结果；请继续核对原请求。');
  let sourceStillAvailable=null,historyUnavailable=false;
  async function safeHistory(id){try{return await loadHistory(id,original);}catch{historyUnavailable=true;if(active(original)&&selectedRef.current===id)setSourceAvailable(null);return null;}}
  if(attempt.kind==='connect'){
   const base=await requestScoped('/ftps/connections/'+encodeURIComponent(receipt.result.id));if(typeof base?.source_available!=='boolean')throw Error('服务器连接授权状态无法核对。');const found=base.connection;
   if(found?.id!==receipt.result.id||found?.name!==receipt.result.name||found?.grant_id!==receipt.result.grant_id||found?.status!=='active'||found?.version!==receipt.result.version)throw Error('服务器连接与原回执不一致。');
   if(!found)throw Error('服务器尚未读回这条连接；请继续核对原请求。');
   selectedRef.current=receipt.result.id;if(active(original)){setConnections(old=>[found,...(old||[]).filter(x=>x.id!==found.id)]);setConnectionId(receipt.result.id);}const history=await safeHistory(receipt.result.id);sourceStillAvailable=history?.sourceAvailable??base.source_available;
  }else if(attempt.kind==='listing'){
   selectedRef.current=attempt.submission.connection_id;if(active(original))setConnectionId(attempt.submission.connection_id);
   const snapshot=await requestScoped('/ftps/connections/'+encodeURIComponent(attempt.submission.connection_id)+'/listings/'+encodeURIComponent(receipt.result.listing_id));
   if(typeof snapshot?.source_available!=='boolean'||snapshot?.listing?.state!=='succeeded'||snapshot.listing.kind!=='listing'||snapshot.listing.submission?.connection_id!==attempt.submission.connection_id)throw Error('原目录快照无法核对；请继续核对原请求。');
   const checked=verifyFtpsReceipt(snapshot.listing,attempt);if(stableStringify(checked.result)!==stableStringify(receipt.result))throw Error('服务器目录快照与原回执不一致。');
   const history=await safeHistory(attempt.submission.connection_id);sourceStillAvailable=history?.sourceAvailable??snapshot.source_available;
   if(active(original)){if(!history?.listings.some(x=>x.request_id===attempt.submission.request_id))setListings(old=>[receipt,...old.filter(x=>x.request_id!==receipt.request_id)]);setListingId(receipt.result.listing_id);setFileId('');}
  }else{
   selectedRef.current=attempt.submission.connection_id;if(active(original))setConnectionId(attempt.submission.connection_id);
   const snapshot=await requestScoped('/ftps/connections/'+encodeURIComponent(attempt.submission.connection_id)+'/listings/'+encodeURIComponent(attempt.submission.listing_id));
   if(typeof snapshot?.source_available!=='boolean'||snapshot?.listing?.state!=='succeeded'||snapshot.listing.kind!=='listing'||snapshot.listing.submission?.connection_id!==attempt.submission.connection_id)throw Error('原目录快照无法核对；请继续核对原请求。');
   const checked=verifyFtpsReceipt(snapshot.listing,{kind:'listing',submission:snapshot.listing.submission});const originalFile=checked.result.files.find(x=>x.id===attempt.submission.file_id),source=receipt.result.source;
   if(checked.result.listing_id!==attempt.submission.listing_id||!originalFile||originalFile.name!==source.name||originalFile.size!==source.size||originalFile.modified!==source.modified)throw Error('导入来源与原目录快照不一致；请继续核对原请求。');
   const saved=await requestScoped('/setup/materials/'+encodeURIComponent(receipt.result.material_id));
   if(saved?.id!==receipt.result.material_id||saved?.hash!==receipt.result.source.sha256||saved?.size!==receipt.result.source.size)throw Error('服务器读回的资料与来源摘要或大小不一致；请继续核对原请求。');
   const history=await safeHistory(attempt.submission.connection_id);
   sourceStillAvailable=history?.sourceAvailable??snapshot.source_available;if(active(original)&&!history?.imports.some(x=>x.request_id===attempt.submission.request_id))setImports(old=>[receipt,...old.filter(x=>x.request_id!==receipt.request_id)]);
   if(active(original))setMaterial(saved);
  }
  if(!active(original))return;
  if(historyUnavailable&&!releaseWithoutHistory){setVerified({receipt,attempt});setError('');setNotice('操作结果已读回核对，但记录清单暂未更新；原请求仍保留。可重试读取，或核对后完成。');return;}
  const rest=await release(attempt,original);
  if(!active(original))return;
  setVerified(null);setPending(rest[0]||null);setError('');setNotice((attempt.kind==='connect'?'连接已保存；输入保留为草稿。':attempt.kind==='listing'?'目录已读取并保存；远端文件可能随后变化。':'原件已保存，解析状态可在下方查看。')+(sourceStillAvailable===false?' 此来源当前不可用，已保存资料保留。':'')+(historyUnavailable?' 记录清单暂未更新，请重新读取。':'')+(rest.length?' 还有其他请求需要核对。':''));
  if(attempt.kind==='import'&&onImported){try{await onImported();}catch{if(active(original))setNotice('原件已保存；资料清单暂未刷新，请前往资料页重新读取。');}}
 }
 async function run(attempt){
  if(operation.current||busy||storageError||draftError||pending)return;operation.current=true;const original=scope;let retained=false;setBusy(true);setError('');setNotice('');
  try{await retain(attempt,original);retained=true;if(!active(original))return;setPending(attempt);const receipt=await requestScoped(ftpsPath(attempt),{method:'POST',body:JSON.stringify(ftpsBody(attempt))});if(active(original))await checkedCompletion(receipt,attempt,original);}
  catch(e){if(active(original)){setError(retained?'操作结果尚未确认：'+e.message+' 原请求保留，请先核对。':'操作未发送：'+e.message);try{const list=readPending();setPending(list[0]||null);}catch{setStorageError(true);}}}
  finally{if(active(original)){operation.current=false;setBusy(false);}}
 }
 async function recover(retry=false){
  if(!pending||operation.current||busy)return;operation.current=true;const attempt=pending,original=scope;setBusy(true);setError('');
  try{const receipt=await requestScoped(retry?ftpsPath(attempt):'/ftps/requests/'+encodeURIComponent(attempt.submission.request_id),retry?{method:'POST',body:JSON.stringify(ftpsBody(attempt))}:{});if(active(original))await checkedCompletion(receipt,attempt,original);}
  catch(e){if(active(original))setError('原请求仍待核对：'+e.message+' 可重试原请求或安全结束。');}
  finally{if(active(original)){operation.current=false;setBusy(false);}}
 }
 async function cancel(){
  if(!pending||operation.current||busy)return;operation.current=true;const attempt=pending,original=scope;setBusy(true);setError('');
  try{const receipt=await requestScoped('/ftps/requests/'+encodeURIComponent(attempt.submission.request_id)+'/cancel',{method:'POST',body:JSON.stringify(attempt)});if(active(original))await checkedCompletion(receipt,attempt,original);}
  catch(e){if(active(original))setError('原请求尚未安全结束：'+e.message+' 原记录保留。');}
  finally{if(active(original)){operation.current=false;setBusy(false);}}
 }
 async function finishVerified(){
  if(!verified||!pending||operation.current||busy||stableStringify(verified.attempt)!==stableStringify(pending))return;operation.current=true;const original=scope;setBusy(true);setError('');
  try{await checkedCompletion(verified.receipt,verified.attempt,original,true);}catch(e){if(active(original))setError('原请求仍待核对：'+e.message+' 原记录保留。');}
  finally{if(active(original)){operation.current=false;setBusy(false);}}
 }
 async function readMaterial(id){try{const result=await requestScoped('/setup/materials/'+encodeURIComponent(id));if(active(scope)){if(result?.id!==id)throw Error('资料记录不匹配');setMaterial(result);}}catch(e){if(active(scope))setError('暂时无法核对资料解析状态：'+e.message);}}
 if(account?.role!=='admin')return null;
 const disabled=busy||!!pending||storageError||draftError,selected=(connections||[]).find(x=>x.id===connectionId),listing=listings.find(x=>x.result.listing_id===listingId),file=listing?.result.files.find(x=>x.id===fileId);
 return <section className="local-model-card" aria-label="远程文档目录"><h3>从已授权 FTPS 目录导入资料</h3>
  <p>选择管理员事先批准的目录，读取文件清单，再选择一份文件复制到初始化资料。不会删除远端文件，也不会自动覆盖业务配置。</p><p>当前只读取已授权的单层目录；自动同步尚未接通。管理员可在上方配置授权，须确认远端服务器已隔离授权目录。</p><p>图形授权的撤销即时生效；进行中的读取完成后撤销生效，已保存的原件保留。运行环境授权的修改需要重启后台。</p>
  {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{storageError&&<p role="alert">本机恢复记录不可用；当前操作已暂停。</p>}
  {pending&&<aside className="info"><p>上次{({connect:'连接保存',listing:'目录读取',import:'文件导入'})[pending.kind]}结果尚需处理；原请求保留，不能开始新操作。</p><Btn disabled={busy} onClick={()=>recover(false)}>核对原请求</Btn><Btn disabled={busy} onClick={()=>recover(true)}>重试原请求</Btn><Btn disabled={busy} onClick={cancel}>安全结束原请求</Btn>{verified&&<Btn disabled={busy} onClick={finishVerified}>已核对并完成</Btn>}</aside>}
  <details id="ftps-document-add"><summary>添加已授权目录连接</summary><Field label="连接名称"><input aria-label="文档目录连接名称" value={name} maxLength={100} disabled={busy||!!pending||storageError} onChange={e=>editDraft(e.target.value,grantId)}/></Field><Field label="选择已批准的目录"><select aria-label="已批准的文档目录" value={grantId} disabled={busy||!!pending||storageError} onChange={e=>editDraft(name,e.target.value)}><option value="">请选择目录</option>{(grants||[]).map(g=><option key={g.id} value={g.id}>{g.name} · {g.host}:{g.port} · {g.root}</option>)}</select></Field>{grants?.length===0&&<p>尚无已批准目录，请联系管理员配置。</p>}<Btn disabled={disabled||!cleanName(name)||!grantId} onClick={()=>run({kind:'connect',submission:{request_id:uid(),name:name.trim(),grant_id:grantId}})}>保存连接</Btn></details>
  <Field label="选择已保存的目录连接"><select aria-label="文档目录连接" value={connectionId} disabled={disabled} onChange={e=>openConnection(e.target.value)}><option value="">请选择连接</option>{(connections||[]).map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
  {connectionsCursor&&<Btn disabled={disabled||loadingMore} onClick={loadMoreConnections}>加载更多目录连接</Btn>}
  {selected&&<div><h4>{selected.name}</h4>{sourceAvailable===false&&<p role="status">此来源的读取授权已撤销或不可用；已保存的目录和资料仍可核对。</p>}{sourceAvailable===null&&<p role="status">来源授权状态尚未核对，请重新读取记录。</p>}<Btn disabled={disabled} onClick={()=>openConnection(selected.id)}>重新读取目录和导入记录</Btn><Btn disabled={disabled||selected.status!=='active'||sourceAvailable!==true} onClick={()=>run({kind:'listing',submission:{request_id:uid(),version:selected.version,connection_id:selected.id}})}>读取目录文件</Btn>
   {listings.length>0&&<><Field label="已保存的目录清单"><select aria-label="文档目录清单" value={listingId} disabled={disabled} onChange={e=>{setListingId(e.target.value);setFileId('');}}>{listings.map((x,index)=><option key={x.result.listing_id} value={x.result.listing_id}>{x.result.files.length} 个可导入文件 · {index===0?'最近一次':'历史清单 '+(index+1)}</option>)}</select></Field><p>仅显示格式、大小和文件信息符合条件的普通文件，未显示的文件可在源端核对。清单是读取时的快照，文件若已变化，需重新读取目录。</p>
    {!listing?.result.files.length?<p>目录中没有可导入的文件。</p>:<Field label="选择要导入的文件"><select aria-label="要导入的文档" value={fileId} disabled={disabled} onChange={e=>setFileId(e.target.value)}><option value="">请选择文件</option>{listing.result.files.map(x=><option key={x.id} value={x.id}>{x.name} · {x.size} 字节 · {modifiedAt(x.modified)}</option>)}</select></Field>}
    <Btn disabled={disabled||!file||sourceAvailable!==true} onClick={()=>run({kind:'import',submission:{request_id:uid(),version:selected.version,connection_id:selected.id,listing_id:listingId,file_id:fileId}})}>确认导入所选文件</Btn></>}
   {listingCursor&&<Btn disabled={disabled||loadingMore} onClick={()=>loadMoreHistory('listing')}>加载更多目录清单</Btn>}<h4>已保存的导入记录</h4>{unverifiedHistory>0&&<p role="alert">有 {unverifiedHistory} 条历史记录的来源依据尚不能核对；原记录保留，请联系管理员核查。</p>}{!imports.length?<p>还没有可核对的导入记录。</p>:<ul>{imports.map(x=><li key={x.request_id}>{x.result.name} · 原件已保存 <Btn disabled={busy} onClick={()=>readMaterial(x.result.material_id)}>查看解析状态</Btn><details><summary>查看来源依据</summary><p>远端文件：{x.result.source.name} · {x.result.source.size} 字节 · {modifiedAt(x.result.source.modified)}</p><p>原件摘要：{x.result.source.sha256}</p></details></li>)}</ul>}
  </div>}
  {importCursor&&<Btn disabled={disabled||loadingMore} onClick={()=>loadMoreHistory('import')}>加载更多导入记录</Btn>}{material&&<aside className="info"><strong>{material.name}</strong>{material.archived&&<p>该原件已在归档中，需在资料页恢复后才能用于理解。</p>}<p>{({queued:'等待解析',parsing:'正在解析',parsed:'正文解析完成',failed:'解析未完成，原件保留'})[material.status]||'解析状态待核对'}</p>{onGo&&<Btn onClick={()=>onGo(0)}>前往领域与资料查看原件</Btn>}</aside>}
 </section>;
}
