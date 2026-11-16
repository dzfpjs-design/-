import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import WmtsPositionPicker from './WmtsPositionPicker';
import {mapSourceConfiguration,verifySourcePublication,requireMapBusinesses} from './map-source-configuration.mjs';

const label={wmts:'WMTS 瓦片服务',arcgis:'ArcGIS MapServer',arcgis_image:'ArcGIS ImageServer'};
const empty={form:{name:'',protocol:'wmts',endpoint:'',secret_ref:''},pending:null,selections:{}};
const DOCUMENT_DRAFT_ID=crypto.randomUUID();
function storagePrefix(a){return `map-services-v2:${a.workspace_id}:${a.user_id}:${a.username}:`;}
function storageKey(a){return storagePrefix(a)+DOCUMENT_DRAFT_ID;}
function stable(value){if(Array.isArray(value))return value.map(stable);if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,stable(value[k])]));return value;}
function same(a,b){return JSON.stringify(stable(a))===JSON.stringify(stable(b));}
function safeDraft(x){
 if(!x||!x.form||!['wmts','arcgis','arcgis_image'].includes(x.form.protocol))return empty;
 // Do not put pasted credentials or query tokens in browser storage.
 if(/[@?#]/.test(x.form.endpoint||'')||!(/^(GA_CONNECTOR_SECRET_[A-Z0-9_]+)?$/.test(x.form.secret_ref||'')))return null;
 return x;
}
function fields(c){
 if(c?.protocol==='wmts'){
  const layer=c.catalog?.layers?.[0],set=layer?.matrix_sets?.[0],mat=set?.matrices?.[0];
  return {layer:layer?.id||'',matrix_set:set?.id||'',matrix:mat?.id||'',style:layer?.styles?.[0]||'',row:'0',col:'0'};
 }
 return {layer:c?.catalog?.layers?.[0]?.id||'',bbox:(c?.catalog?.bounds||['','','','']).map(String)};
}
export default function MapServicesStudio({account,Btn,Field,onRegisterGuard,workspace,onSave,onCommit}){
 const key=storageKey(account),anchor=storagePrefix(account)+'tab-current',alive=useRef(true),draftRef=useRef(empty),volatile=useRef(false),working=useRef(false),positionGuard=useRef(null),callback=useRef(onRegisterGuard);callback.current=onRegisterGuard;
 const currentWorkspace=useRef(workspace);currentWorkspace.current=workspace;
 const [draft,setDraft]=useState(null),[records,setRecords]=useState([]),[chosen,setChosen]=useState(''),[selection,setSelection]=useState(null);
 const [refs,setRefs]=useState([]),[history,setHistory]=useState([]),[historyMore,setHistoryMore]=useState(false),[previewId,setPreviewId]=useState(''),[copies,setCopies]=useState([]),[copyChoice,setCopyChoice]=useState(''),[endpointInput,setEndpointInput]=useState('');
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[image,setImage]=useState('');
 const request=(path,opts={})=>json(path,{...opts,headers:{...opts.headers,'X-Workspace-ID':account.workspace_id}});
 function persist(next){
  const safe=safeDraft({...next,updatedAt:Date.now()});
  if(!safe){setError('地址不能包含凭据或查询参数；请移除后再保存，敏感内容不会写入本机草稿。');throw Error('请先移除地址中的凭据或查询参数');}
  try{localStorage.setItem(key,JSON.stringify(safe));sessionStorage.setItem(anchor,key);draftRef.current=safe;setDraft(safe);}catch{setError('当前浏览器无法保存草稿，已停止发送；请恢复浏览器存储后重试。');throw Error('无法保存草稿');}
 }
 function readCopies(){try{let rows=[];for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k?.startsWith(storagePrefix(account))){try{const value=safeDraft(JSON.parse(localStorage.getItem(k)));if(value)rows.push({key:k,value});}catch{}}}rows.sort((a,b)=>(b.value.updatedAt||0)-(a.value.updatedAt||0));setCopies(rows);return rows;}catch{throw Error('浏览器存储暂不可读取');}}
 function choose(id){try{persist({...draftRef.current,selected_id:id});setChosen(id);}catch{}}
 function restoreCopy(){try{const row=copies.find(x=>x.key===copyChoice);if(!row)throw Error('请先选择要恢复的输入');persist({...row.value});setEndpointInput(row.value.form.endpoint||'');volatile.current=false;setChosen(row.value.selected_id||'');setNotice('已恢复本机保存的输入；未自动发送请求。');}catch(e){setError(e.message);}}
 function editForm(k,v){try{persist({...draftRef.current,form:{...draftRef.current.form,[k]:v}});setError('');}catch{}}
 async function load(){const [rows,meta]=await Promise.all([request('/map-services/connections'),request('/wms/catalog')]);if(alive.current){setRecords(rows);setChosen(previous=>previous||draftRef.current?.selected_id||'');setRefs((meta.secret_references||[]).filter(x=>/^GA_CONNECTOR_SECRET_[A-Z0-9_]+$/.test(x)));setLoading(false);}return rows;}
 useEffect(()=>{
  alive.current=true;
  try{const previous=sessionStorage.getItem(anchor);let value=empty;if(previous?.startsWith(storagePrefix(account))){const raw=localStorage.getItem(previous);if(raw){value=safeDraft(JSON.parse(raw));if(!value)throw Error();}}persist(value);setEndpointInput(value.form.endpoint||'');volatile.current=false;}catch{setError('本机草稿不可读取，请检查浏览器存储；已有服务器连接未删除。');}
  load().catch(e=>{if(alive.current){setLoading(false);setError(e.message);}});
  return()=>{alive.current=false;};
 },[key]);
 useEffect(()=>{const guard=()=>!volatile.current&&!working.current&&(!positionGuard.current||positionGuard.current()!==false);callback.current?.(guard);const before=e=>{if(volatile.current){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',before);return()=>{callback.current?.(null,guard);window.removeEventListener('beforeunload',before);};},[]);
 const selected=records.find(x=>x.id===chosen);const pending=draft?.pending;const disabled=busy||!!pending||!draft;
 useEffect(()=>{const saved=draftRef.current?.selections?.[chosen];setSelection(saved&&saved.catalog_request_id===selected?.catalog_request_id?saved.value:selected?.selection?{...selected.selection,row:String(selected.selection.row??''),col:String(selected.selection.col??''),bbox:selected.selection.bbox?.map(String)}:fields(selected));},[chosen,selected?.catalog_request_id,selected?.preview_id]);
 function updateSelection(value){try{persist({...draftRef.current,selections:{...draftRef.current.selections,[chosen]:{catalog_request_id:selected?.catalog_request_id,value}}});setSelection(value);setError('');return true;}catch{return false;}}
 useEffect(()=>{let active=true;if(!chosen){setHistory([]);setPreviewId('');return;}request('/map-services/connections/'+encodeURIComponent(chosen)+'/previews').then(rows=>{if(active){setHistory(rows);setHistoryMore(rows.length===50);setPreviewId(selected?.preview_id||rows[0]?.id||'');}}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[chosen,selected?.preview_id,selected?.catalog_request_id]);
 useEffect(()=>{
  let active=true,objectURL='';setImage('');
  if(previewId)fetch('/api/map-services/previews/'+encodeURIComponent(previewId)+'/image',{credentials:'same-origin',headers:{'X-Workspace-ID':account.workspace_id},cache:'no-store'}).then(r=>{if(!r.ok)throw Error('保存的地图暂时无法读取');return r.blob();}).then(b=>{if(active){objectURL=URL.createObjectURL(b);setImage(objectURL);}}).catch(e=>{if(active)setError(e.message);});
  return()=>{active=false;if(objectURL)URL.revokeObjectURL(objectURL);};
 },[previewId,account.workspace_id]);
 async function send(p){
  working.current=true;setBusy(true);setError('');setNotice('');
  try{
   if(!draftRef.current.pending)persist({...draftRef.current,pending:p});
   let result;
   if(p.kind==='create'){
    try{result=await request('/map-services/connections/requests/'+p.body.request_id);}catch(e){if(e.status!==404)throw e;result=await request('/map-services/connections',{method:'POST',body:JSON.stringify(p.body)});}
    if(result.request_id!==p.body.request_id||result.name!==p.body.name||result.protocol!==p.body.protocol||result.endpoint!==p.body.endpoint.replace(/\/$/,'')||result.secret_ref!==p.body.secret_ref)throw Error('服务器记录与原登记不一致，请管理员核对。');
    setChosen(result.id);
   }else if(p.kind==='catalog'){
    result=await request('/map-services/connections/'+p.id+'/catalog',{method:'POST',body:JSON.stringify(p.body)});
    if(result.catalog_request_id!==p.body.request_id||result.catalog_previous_version!==p.body.version)throw Error('目录回执与原请求不一致，请核对。');
   }else if(p.kind==='publication'){
    if(typeof onSave!=='function'||typeof onCommit!=='function')throw Error('工作区保存尚未准备好');
    requireMapBusinesses(p.businesses,currentWorkspace.current);
    await onSave();if(!alive.current)throw Error('已离开原工作区');
    result=await request('/map-source-publications',{method:'POST',body:JSON.stringify(p.body)});
    verifySourcePublication(result,p);
    if(!result.source_valid){if(!alive.current)return;persist({...draftRef.current,pending:null,businessChoices:{...draftRef.current.businessChoices,[p.body.preview_id]:p.businesses},publicationStates:{...draftRef.current.publicationStates,[p.body.preview_id]:{state:'published_unavailable',businesses:p.businesses}}});setNotice('原地图来源已发布，但授权或原件目前失效；业务选择已保留，恢复后再关联。');return;}
    const next=mapSourceConfiguration(currentWorkspace.current,result,p);
    if(!onCommit(next))throw Error('业务配置暂不能保存，原选择保留');
    await onSave();if(!alive.current)throw Error('已离开原工作区，原选择保留');
   }else{
    try{result=await request('/map-services/previews/requests/'+p.body.request_id);}catch(e){if(e.status!==404)throw e;result=await request('/map-services/connections/'+p.id+'/previews',{method:'POST',body:JSON.stringify(p.body)});}
    if(result.request_id!==p.body.request_id||result.connection_id!==p.id||!same(result.submission,p.body))throw Error('地图回执与原请求不一致，请核对。');
   }
   if(!alive.current)return;
   await load();persist({...draftRef.current,pending:null,...(p.kind==='create'?{selected_id:result.id}:{}),...(p.kind==='publication'?{publicationStates:{...draftRef.current.publicationStates,[p.body.preview_id]:{state:'linked',businesses:p.businesses}}}:{})});
   setNotice(p.kind==='create'?'连接已登记，请读取真实图层目录。':p.kind==='catalog'?'图层目录已保存，请选择图层和只读范围。':p.kind==='publication'?'固定地图来源和业务选择已保存；生成并启用实例后才用于业务。':'地图原图与选择范围已保存在当前工作区。');
  }catch(e){if(alive.current)setError(e.message+' 原请求已保留，请核对连接后按原请求重试；不会自动发起新的请求。');}
  finally{working.current=false;if(alive.current)setBusy(false);}
 }
 async function resolvePending(){
  if(!pending)return;working.current=true;setBusy(true);setError('');
  try{
   if(pending.kind==='publication'){
    const r=await request('/map-source-publications/resolve',{method:'POST',body:JSON.stringify(pending.body)});
    if(r.request_id!==pending.body.request_id||!['created','cancelled'].includes(r.state))throw Error('来源请求回执无法核对');
    if(r.state==='created')verifySourcePublication(r,pending);
    persist({...draftRef.current,pending:null,businessChoices:{...draftRef.current.businessChoices,[pending.body.preview_id]:pending.businesses},publicationStates:{...draftRef.current.publicationStates,[pending.body.preview_id]:{state:r.state==='created'?(r.source_valid?'published_check_link':'published_unavailable'):'cancelled',businesses:pending.businesses}}});
    setNotice(r.state==='created'?(r.source_valid?'地图来源已发布，业务关联需核对或重新保存；原选择已保留。':'地图来源已发布，但授权或原件目前失效；原业务选择保留，恢复后再关联。'):'原请求已安全结束，原业务选择保留。');return;
   }
   const r=await request('/map-services/operations/resolve',{method:'POST',body:JSON.stringify({kind:pending.kind,request_id:pending.body.request_id,connection_id:pending.id||''})});
   if(r.request_id!==pending.body.request_id||!['completed','cancelled'].includes(r.state))throw Error('结束回执无法核对');
   if(r.state==='completed'){if(pending.kind==='create'&&(!r.record||r.record.name!==pending.body.name||r.record.protocol!==pending.body.protocol||r.record.endpoint!==pending.body.endpoint||r.record.secret_ref!==pending.body.secret_ref))throw Error('已完成登记内容与原请求不一致');if(pending.kind==='catalog'&&r.version!==pending.body.version)throw Error('已完成目录请求版本不一致');if(pending.kind==='preview'&&!same(r.submission,pending.body))throw Error('已完成地图读取内容与原请求不一致');}
   const rows=await load();if(pending.kind==='create'&&r.state==='completed')setChosen(rows.find(x=>x.request_id===pending.body.request_id)?.id||'');persist({...draftRef.current,pending:null});setNotice(r.state==='completed'?'原请求已完成并保存，已读取服务器记录。':'原请求已安全结束；可以修改后另行提交。');
  }catch(e){if(alive.current)setError(e.message+' 原请求保留，请稍后再次核对。');}finally{working.current=false;if(alive.current)setBusy(false);}
 }
 function confirmEndpoint(){try{const u=new URL(endpointInput);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash||!(u.hostname.startsWith('[')||/^\d+\.\d+\.\d+\.\d+$/.test(u.hostname)))throw Error('请填写不含凭据和查询参数的固定 IP 地址。');persist({...draftRef.current,form:{...draftRef.current.form,endpoint:endpointInput}});volatile.current=false;setError('');setNotice('服务地址已保存到本机草稿。');}catch(e){setError(e.message);}}
 async function moreHistory(){if(!history.length)return;try{setBusy(true);const rows=await request('/map-services/connections/'+encodeURIComponent(chosen)+'/previews?before='+encodeURIComponent(history[history.length-1].id));setHistory(current=>[...current,...rows.filter(r=>!current.some(x=>x.id===r.id))]);setHistoryMore(rows.length===50);}catch(e){setError(e.message);}finally{setBusy(false);}}
 function create(){
  try{
   if(volatile.current)throw Error('请先确认服务地址或清空未保存的输入');const f=draft.form,u=new URL(f.endpoint);
   if(!f.name.trim()||!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash)throw Error('请填写名称及不含凭据、查询参数的 HTTP(S) 服务地址。');
   if(f.protocol==='arcgis'&&!u.pathname.replace(/\/$/,'').endsWith('/MapServer'))throw Error('ArcGIS 地址需指向具体 MapServer 服务。');
   if(f.protocol==='arcgis_image'&&!u.pathname.replace(/\/$/,'').endsWith('/ImageServer'))throw Error('ArcGIS 地址需指向具体 ImageServer 服务。');
   if(f.secret_ref&&!/^GA_CONNECTOR_SECRET_[A-Z0-9_]+$/.test(f.secret_ref))throw Error('凭据引用格式不正确。');
   send({kind:'create',body:{request_id:crypto.randomUUID(),...f,name:f.name.trim(),endpoint:f.endpoint.replace(/\/$/,'')}});
  }catch(e){setError(e.message);}
 }
 function toggleBusiness(name,checked){try{const id=previewId;const existing=draftRef.current.businessChoices?.[id]||currentWorkspace.current?.resources?.find(r=>r.details?.map_preview_id===id)?.businesses||[];persist({...draftRef.current,businessChoices:{...draftRef.current.businessChoices,[id]:checked?[...new Set([...existing,name])]:existing.filter(x=>x!==name)}});}catch{}}
 function publishSource(){try{const row=history.find(x=>x.id===previewId);if(!selected||!row)throw Error('请先选择已保存的地图');const choices=draftRef.current.businessChoices?.[previewId]||currentWorkspace.current?.resources?.find(r=>r.details?.map_preview_id===previewId)?.businesses||[];requireMapBusinesses(choices,currentWorkspace.current);send({kind:'publication',id:selected.id,name:selected.name,protocol:selected.protocol,selection:row.selection,businesses:[...choices],body:{preview_id:previewId,request_id:crypto.randomUUID()}});}catch(e){setError(e.message);}}
 function preview(){
  try{
   if(!selected||!selection)throw Error('请先选择图层');
   const value=selected.protocol==='wmts'?{layer:selection.layer,matrix_set:selection.matrix_set,matrix:selection.matrix,style:selection.style,row:Number(selection.row),col:Number(selection.col),...(selection.max_row!==undefined?{max_row:Number(selection.max_row),max_col:Number(selection.max_col)}:{})}:{layer:selection.layer,bbox:selection.bbox.map(Number)};
   if(selected.protocol==='wmts'&&(!/^\d+$/.test(selection.row)||!/^\d+$/.test(selection.col)))throw Error('瓦片行列号必须为非负整数');
   if(selected.protocol!=='wmts'&&selection.bbox.some(x=>x.trim()===''||!Number.isFinite(Number(x))))throw Error('请填写有效的地图范围');
   send({kind:'preview',id:selected.id,body:{request_id:crypto.randomUUID(),version:selected.version,selection:value,confirm_read_only:true}});
  }catch(e){setError(e.message);}
 }
 const writer=['admin','operator'].includes(account.role),admin=account.role==='admin';
 const layer=selected?.catalog?.layers?.find(x=>x.id===selection?.layer),matrixSet=layer?.matrix_sets?.find(x=>x.id===selection?.matrix_set);
 return <section className="local-model-section" aria-label="WMTS 和 ArcGIS 地图服务">
  <h3>WMTS 与 ArcGIS 地图服务</h3>
  <p>添加已授权的 WMTS 1.0.0 KVP、ArcGIS MapServer 或 ImageServer，读取目录后选择图层，保存一幅只读地图。目录中的外部链接不会被自动访问。当前不自动发布到业务实例，也不代表卫星原始波段。</p>
  {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  <details><summary>找回本机保存的输入</summary><p>本机按账号和工作区保留各页面的输入。恢复仅复制草稿，不会自动提交。</p><Btn disabled={busy||!!pending} onClick={()=>{try{readCopies();}catch(e){setError(e.message);}}}>读取本机输入</Btn>{copies.length>0&&<><Field label="选择之前保存的输入"><select aria-label="选择之前保存的输入" value={copyChoice} onChange={e=>setCopyChoice(e.target.value)}><option value="">请选择</option>{copies.map((r,i)=><option key={r.key} value={r.key}>{r.value.form.name||'未命名服务'} · {new Date(r.value.updatedAt||0).toLocaleString()} {r.value.pending?'· 待核对':''}</option>)}</select></Field><Btn disabled={busy||!!pending||!copyChoice} onClick={restoreCopy}>恢复所选输入</Btn></>}</details>
  {pending&&<div className="local-sample"><p>上次操作结果尚待核对。原内容保存在本机，刷新不会自动重发。</p><Btn disabled={busy} onClick={()=>send(pending)}>核对并按原请求重试</Btn> <Btn disabled={busy} onClick={resolvePending}>结束待核对请求并修改</Btn><p>系统会先核对原请求；若尚未完成，会阻止迟到请求继续执行。</p></div>}
  {admin&&draft&&<details id="map-services-add"><summary>添加 WMTS 或 ArcGIS 服务</summary><div className="local-model-fields">
   <Field label="服务名称"><input aria-label="地图服务名称" disabled={disabled} value={draft.form.name} onChange={e=>editForm('name',e.target.value)}/></Field>
   <Field label="服务类型"><select aria-label="地图服务类型" disabled={disabled} value={draft.form.protocol} onChange={e=>editForm('protocol',e.target.value)}><option value="wmts">WMTS</option><option value="arcgis">ArcGIS MapServer</option><option value="arcgis_image">ArcGIS ImageServer</option></select></Field>
   <Field label="服务地址"><input aria-label="地图服务地址" disabled={disabled} value={endpointInput} onChange={e=>{setEndpointInput(e.target.value);volatile.current=e.target.value!==draftRef.current.form.endpoint;}}/></Field><Btn disabled={disabled||!endpointInput} onClick={confirmEndpoint}>确认服务地址</Btn>{endpointInput!==draft.form.endpoint&&<><p>当前地址只保存在页面内，请确认或放弃后再离开；不要在地址中输入凭据。</p><Btn disabled={disabled} onClick={()=>{setEndpointInput(draft.form.endpoint);volatile.current=false;setError('');}}>放弃本次地址输入</Btn></>}
   <Field label="服务端凭据引用（可留空）"><select aria-label="地图服务凭据引用" disabled={disabled} value={draft.form.secret_ref} onChange={e=>editForm('secret_ref',e.target.value)}><option value="">不使用凭据</option>{refs.map(x=><option key={x} value={x}>{x}</option>)}</select></Field>
  </div><p>须先由运维授权此确切服务地址及当前工作区；为防止域名重绑定，仅允许固定 IP，域名请经授权的本地受控代理访问。密钥只保存在服务端，不能填入地址或页面。</p><Btn disabled={disabled} onClick={create}>保存服务连接</Btn></details>}
  {loading?<p>正在读取地图连接…</p>:<><Field label="选择地图服务"><select aria-label="选择地图服务" value={chosen} disabled={disabled} onChange={e=>choose(e.target.value)}><option value="">请选择服务</option>{records.map(c=><option value={c.id} key={c.id}>{c.name} · {label[c.protocol]}</option>)}</select></Field>{!records.length&&<p>尚未添加 WMTS 或 ArcGIS 地图服务。</p>}</>}
  {selected&&<article className="local-sample"><h4>{selected.name} · {label[selected.protocol]}</h4><p>{selected.endpoint} · {selected.catalog?`已保存 ${selected.catalog.layers.length} 个图层目录`:'尚未读取目录'}</p>
   {admin&&<Btn disabled={disabled} onClick={()=>send({kind:'catalog',id:selected.id,body:{request_id:crypto.randomUUID(),version:selected.version}})}>读取图层目录</Btn>}
   {selected.catalog&&selection&&<><div className="local-model-fields"><Field label="选择图层"><select aria-label="地图服务图层" disabled={disabled} value={selection.layer} onChange={e=>{const l=selected.catalog.layers.find(x=>x.id===e.target.value),s=l?.matrix_sets?.[0];updateSelection(selected.protocol==='wmts'?{layer:e.target.value,matrix_set:s?.id||'',matrix:s?.matrices?.[0]?.id||'',style:l?.styles?.[0]||'',row:'0',col:'0',max_row:undefined,max_col:undefined}:{...selection,layer:e.target.value});}}>{selected.catalog.layers.map(x=><option value={x.id} key={x.id}>{x.title}</option>)}</select></Field>
   {selected.protocol==='wmts'?<><Field label="瓦片矩阵集"><select aria-label="瓦片矩阵集" disabled={disabled} value={selection.matrix_set} onChange={e=>{const s=layer.matrix_sets.find(x=>x.id===e.target.value);updateSelection({...selection,matrix_set:e.target.value,matrix:s?.matrices?.[0]?.id||'',row:'0',col:'0',max_row:undefined,max_col:undefined});}}>{layer?.matrix_sets?.map(x=><option value={x.id} key={x.id}>{x.id} · {x.crs}</option>)}</select></Field><Field label="瓦片级别"><select aria-label="瓦片级别" disabled={disabled} value={selection.matrix} onChange={e=>updateSelection({...selection,matrix:e.target.value,row:'0',col:'0',max_row:undefined,max_col:undefined})}>{matrixSet?.matrices?.map(x=><option value={x.id} key={x.id}>{x.id} · {x.width}×{x.height}</option>)}</select></Field><Field label="地图样式"><select aria-label="地图样式" disabled={disabled} value={selection.style} onChange={e=>updateSelection({...selection,style:e.target.value})}>{layer?.styles?.map(x=><option value={x} key={x}>{x}</option>)}</select></Field>{['row','col'].map((k,i)=><Field key={k} label={i?'瓦片列号':'瓦片行号'}><input aria-label={i?'瓦片列号':'瓦片行号'} type="number" min="0" disabled={disabled} value={selection[k]} onChange={e=>updateSelection({...selection,[k]:e.target.value,max_row:undefined,max_col:undefined})}/></Field>)}</>:<>{['西边界','南边界','东边界','北边界'].map((name,i)=><Field key={name} label={name+' · '+selected.catalog.crs}><input aria-label={'地图范围 '+name} type="number" step="any" disabled={disabled} value={selection.bbox?.[i]??''} onChange={e=>updateSelection({...selection,bbox:selection.bbox.map((v,j)=>j===i?e.target.value:v)})}/></Field>)}</>}
   </div>{selected.protocol==='wmts'&&selection.max_row!==undefined&&<p>将拼接第 {selection.row}–{selection.max_row} 行、第 {selection.col}–{selection.max_col} 列的瓦片。</p>}{selected.protocol==='wmts'&&<WmtsPositionPicker key={[account.workspace_id,account.user_id,selected.id].join(':')} account={account} connection={selected} selection={selection} disabled={disabled} Btn={Btn} Field={Field} onSelected={tile=>updateSelection({...selection,...tile})} onGuard={(guard,previous)=>{if(guard)positionGuard.current=guard;else if(!previous||positionGuard.current===previous)positionGuard.current=null;}}/>}{writer&&<Btn primary disabled={disabled} onClick={preview}>确认此范围，只读并保存地图</Btn>}</>}
   {history.length>0&&<><p>地图读取历史：图像为用户确认的瓦片拼接或范围渲染；采集时间与领域效果尚未核实。</p><Field label="已保存的地图"><select aria-label="已保存的地图" value={previewId} onChange={e=>setPreviewId(e.target.value)}>{history.map(h=><option key={h.id} value={h.id}>{h.created_at} · 图层 {h.selection.layer}</option>)}</select></Field>{historyMore&&<Btn disabled={busy} onClick={moreHistory}>查看更多历史</Btn>}{image?<img src={image} alt={'已保存的地图 '+selected.name} style={{maxWidth:'100%',maxHeight:'400px'}}/>:<p>正在读取已保存地图…</p>}{draft?.publicationStates?.[previewId]?.state==='published_check_link'&&<p>此地图来源已发布；业务关联需核对或重新保存，原业务选择已保留。</p>}{admin&&workspace&&onSave&&onCommit&&<details><summary>登记此地图并用于业务</summary><p>登记的是上方选中的已保存地图，不授权自动读取其他区域或更新现场对象；生成并启用业务实例后生效。</p>{(workspace.businesses||[]).length?(workspace.businesses||[]).map(item=>typeof item==='string'?item:item.name).filter(Boolean).map(name=>{const choices=draft?.businessChoices?.[previewId]||workspace.resources?.find(r=>r.details?.map_preview_id===previewId)?.businesses||[];return <label className="media-channel-choice" key={name}><input type="checkbox" aria-label={'此地图用于 '+name} disabled={disabled} checked={choices.includes(name)} onChange={e=>toggleBusiness(name,e.target.checked)}/>{name}</label>;}):<p>请先在业务配置中添加业务，再关联地图。</p>}{(draft?.businessChoices?.[previewId]||[]).filter(name=>!(workspace.businesses||[]).some(item=>(typeof item==='string'?item:item.name)===name)).map(name=><p key={name}>原选择的业务“{name}”已不在当前业务清单。 <Btn disabled={disabled} onClick={()=>toggleBusiness(name,false)}>从本次选择移除</Btn></p>)}<Btn primary disabled={disabled||!previewId} onClick={publishSource}>保存地图来源与业务关联</Btn></details>}</>}
  </article>}
 </section>;
}
