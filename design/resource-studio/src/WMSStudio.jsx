import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {wmsDraftKey,readWMSDraft,normalizeWMSEndpoint,wmsWindow,wmsDimensions,sameWMSRead,sameWMSConnection,savedWMSScope,wmsRecordFieldsPresent,wmsBusinessNames,sameWMSScope,wmsSourceConfiguration} from './wms-form.mjs';

const labels={queued:'等待后台读取',running:'正在读取并保存',catalog_ready:'图层目录已保存，请确认只读范围',active:'已启用确认的只读范围',paused:'连接已暂停',succeeded:'原地图响应和派生影像已保存',failed:'处理失败，原请求与已有响应保留',cancelled:'已取消，原记录保留'};
const coordinateLabels=['西边界 / 最小X','南边界 / 最小Y','东边界 / 最大X','北边界 / 最大Y'];

function Bounds({value,onChange,disabled,Field,prefix}){
 return <div className="local-model-fields">{coordinateLabels.map((label,i)=><Field key={label} label={label}><input aria-label={prefix+' '+label} type="number" step="any" disabled={disabled} value={value[i]??''} onChange={e=>onChange(value.map((v,j)=>i===j?e.target.value:v))}/></Field>)}</div>;
}

export default function WMSStudio({account,Btn,Field,onScene,workspace,onSave,onCommit}){
 const key=wmsDraftKey(account.workspace_id,account.username),active=useRef(true),current=useRef(null),abort=useRef(null);
 const [draft,setDraft]=useState(null),[connections,setConnections]=useState([]),[reads,setReads]=useState([]),[catalog,setCatalog]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[storageError,setStorageError]=useState('');
 const currentWorkspace=useRef(workspace);currentWorkspace.current=workspace;
 const request=(path,options={})=>json(path,{...options,headers:{...options.headers,'X-Workspace-ID':account.workspace_id}});
 function assertActive(){if(!active.current)throw Error('已离开原工作区，原请求保留。');}
 function persist(value){assertActive();try{localStorage.setItem(key,JSON.stringify(value));current.current=value;setDraft(value);setStorageError('');}catch{setStorageError('无法保存浏览器草稿，已停止提交；原输入和服务器记录保留。');throw Error('浏览器草稿暂不可保存，请先恢复存储。');}}
 function edit(part,value){try{persist({...current.current,[part]:value});}catch(e){setError(e.message);}}
 async function load(){const [c,r,s]=await Promise.all([request('/wms/connections'),request('/wms/reads'),request('/wms/catalog')]);if(active.current){setConnections(c);setReads(r);setCatalog(s);setLoading(false);}return c;}
 useEffect(()=>{active.current=true;try{const value=readWMSDraft(localStorage,key);current.current=value;setDraft(value);}catch(e){setStorageError(e.message);}load().catch(e=>{if(active.current){setError(e.message);setLoading(false);}});const timer=setInterval(()=>load().catch(e=>{if(active.current)setError(e.message);}),3000);return()=>{active.current=false;clearInterval(timer);abort.current?.abort();};},[key]);
 const admin=account.role==='admin',writer=['admin','operator'].includes(account.role),locked=busy||!!draft?.pending||!!storageError;

 async function pendingRequest(pending){
  let result;
  if(pending.kind==='publication'){
   const values=await request('/wms/connections');assertActive();const c=values.find(c=>c.id===pending.connection_id);
   if(!sameWMSScope(c,pending))throw Error('原地图目录或登记范围已变化，请核对原选择。');
   try{return await request('/wms/connections/'+encodeURIComponent(pending.connection_id)+'/source-registrations/'+encodeURIComponent(pending.body.scope_id),{signal:abort.current.signal});}
   catch(e){if(![404,409].includes(e.status))throw e;}
   assertActive();return request('/wms/connections/'+encodeURIComponent(pending.connection_id)+'/register-source',{method:'POST',body:JSON.stringify(pending.body),signal:abort.current.signal});
  }
  if(pending.kind==='approval'){
   const values=await request('/wms/connections');assertActive();const c=values.find(c=>c.id===pending.connection_id);
   if(c&&savedWMSScope(c,pending))return c;
   return request('/wms/connections/'+encodeURIComponent(pending.connection_id)+'/approve',{method:'POST',body:JSON.stringify(pending.body),signal:abort.current.signal});
  }
  const lookup=pending.kind==='connection'?'/wms/connections/requests/':'/wms/reads/requests/';
  try{result=await request(lookup+encodeURIComponent(pending.body.request_id),{signal:abort.current.signal});}
  catch(e){if(e.status!==404)throw e;assertActive();const path=pending.kind==='connection'?'/wms/connections':'/wms/connections/'+encodeURIComponent(pending.connection_id)+'/reads';result=await request(path,{method:'POST',body:JSON.stringify(pending.body),signal:abort.current.signal});}
  assertActive();if(!(pending.kind==='connection'?sameWMSConnection(result,pending):sameWMSRead(result,pending)))throw Error('服务器记录与原请求不一致，原请求保持锁定，请核对来源。');return result;
 }
 async function submit(pending){
  if(!current.current)return;setBusy(true);setError('');setNotice('');abort.current=new AbortController();
  try{assertActive();persist({...current.current,pending});if(pending.kind==='publication'){await onSave();assertActive();}
   const result=await pendingRequest(pending);assertActive();
   if(pending.kind==='publication'){
    const value=wmsSourceConfiguration(currentWorkspace.current,result,pending);
    if(!onCommit(value))throw Error('来源登记已保存，但原工作区配置暂不能提交；请保留原选择并重试。');
    await onSave();assertActive();
   }
   persist({...current.current,pending:null});
   setNotice(pending.kind==='publication'?'地图来源与业务关联已保存；任务仍需明确选择固定影像，不自动读取或修改对象状态。':pending.kind==='connection'?'服务登记已保存，后台正在读取真实图层目录。':pending.kind==='approval'?'只读图层与允许范围已保存，可以在下方读取地图。':'地图请求已保存，后台处理结果将显示在读取记录中。');await load();return result;
  }catch(e){if(active.current){if([403,415,422].includes(e.status)||['approval','publication'].includes(pending.kind)&&e.status===409){try{persist({...current.current,pending:null});}catch{}}setError(e.message+(current.current?.pending?' 原请求已保留且暂时锁定，请核对或按原请求重试。':' 当前输入保留，请核对后再提交。'));}}
  finally{abort.current=null;if(active.current)setBusy(false);}
 }
 async function action(path,row){setBusy(true);setError('');try{await request(path,{method:'POST',body:JSON.stringify({version:row.version})});assertActive();await load();setNotice('服务器记录已更新。');}catch(e){if(active.current)setError(e.message+' 原记录保留，请刷新后核对。');}finally{if(active.current)setBusy(false);}}
 function approve(connection,form){try{if(!form.confirm)throw Error('请明确确认此图层、样式和范围只用于读取地图。');const layer=connection.catalog.layers.find(l=>l.name===form.layer),crs=form.crs||Object.keys(layer?.bounds||{})[0];return submit({kind:'approval',connection_id:connection.id,catalog_sha256:connection.catalog_sha256,body:{version:connection.version,layer:form.layer,crs,bbox:wmsWindow(form.bbox,crs),style:form.style||'',confirm_read_only:true}});}catch(e){setError(e.message);}}
 function read(connection,form){try{const scope=connection.scopes[form.scope_id];if(!scope)throw Error('请选择已确认的读取范围。');const [width,height]=wmsDimensions(form.width,form.height);return submit({kind:'read',connection_id:connection.id,body:{request_id:crypto.randomUUID(),scope_id:scope.id,bbox:wmsWindow(form.bbox,scope.crs),width,height}});}catch(e){setError(e.message);}}
 function registerSource(connection,scope){
  try{const selection=current.current.scopeBusinesses?.[connection.id+':'+scope.id]||currentWorkspace.current.resources?.find(r=>r.details?.wms_connection_id===connection.id&&r.details?.scope_id===scope.id)?.businesses||[];
   return submit({kind:'publication',connection_id:connection.id,catalog_sha256:connection.catalog_sha256,scope:{...scope,bbox:[...scope.bbox]},businesses:[...selection],body:{version:connection.version,scope_id:scope.id}});
  }catch(e){setError(e.message);}
 }
 function register(){try{const service=draft.service;return submit({kind:'connection',body:{...service,name:service.name.trim(),endpoint:normalizeWMSEndpoint(service.endpoint),request_id:crypto.randomUUID()}});}catch(e){setError(e.message);}}

 return <section className="wms-studio"><h3>接入WMS地图服务</h3><p>连接已有地图服务，选择图层与只读范围，再保存地图响应和本地派生GeoTIFF。地图颜色来自服务样式，不是卫星原始波段；透明区域保留为无数据，未提供的采集时间保持未知。</p>
  {error&&<p role="alert" className="error">{error}<Btn disabled={busy} onClick={()=>load().then(()=>setError('')).catch(e=>setError(e.message))}>重新读取服务器记录</Btn></p>}{storageError&&<p role="alert" className="error">{storageError}</p>}{notice&&<p role="status">{notice}</p>}{loading&&<p role="status">正在读取地图服务与保存记录…</p>}{catalog&&!catalog.runtime_installed&&<p className="warning-text">本地地理转换环境尚未就绪，可以查看目录；地图读取入口保持禁用，不转云。</p>}
  {draft?.pending&&<div className="info"><p>有一项保存结果尚待核对，输入已保留。确认前不另建请求。</p><Btn disabled={busy||!!storageError} onClick={()=>submit(current.current.pending)}>核对并按原请求重试</Btn>{busy&&<Btn onClick={()=>abort.current?.abort()}>停止等待，保留原请求</Btn>}{!busy&&draft.pending.kind==='publication'&&<Btn onClick={()=>{try{persist({...current.current,pending:null});setError('');setNotice('本次关联核对已结束；已登记来源和配置原记录保留，请重新核对选择。');}catch(e){setError(e.message);}}}>结束关联核对，保留来源记录</Btn>}</div>}
  {admin&&draft&&<details><summary id="wms-gateway-add">添加地图服务</summary><div className="local-model-fields"><Field label="地图服务名称"><input aria-label="地图服务名称" disabled={locked} value={draft.service.name} onChange={e=>edit('service',{...draft.service,name:e.target.value})}/></Field><Field label="WMS服务地址"><input aria-label="WMS服务地址" type="url" disabled={locked} placeholder="https://地图服务地址/geoserver/wms" value={draft.service.endpoint} onChange={e=>edit('service',{...draft.service,endpoint:e.target.value})}/></Field><Field label="WMS协议版本"><select aria-label="WMS协议版本" disabled={locked} value={draft.service.version} onChange={e=>edit('service',{...draft.service,version:e.target.value})}><option value="1.3.0">1.3.0</option><option value="1.1.1">1.1.1</option></select></Field></div><small>可粘贴GetCapabilities地址，标准目录参数会去掉，按选择的协议版本读取。服务确切地址及工作区须由运维授权；不会跟随目录链接或跳转。</small><details><summary>服务需要认证时</summary><Field label="已授权的服务器凭据引用"><select aria-label="已授权的服务器凭据引用" disabled={locked} value={draft.service.secret_ref} onChange={e=>edit('service',{...draft.service,secret_ref:e.target.value})}><option value="">不使用服务凭据</option>{(catalog?.secret_references||[]).map(ref=><option key={ref}>{ref}</option>)}</select></Field><small>只可选择当前工作区已授权的引用；实际密钥由服务器管理，不在此填写密码或令牌。</small></details><Btn primary disabled={locked||!draft.service.name.trim()||!draft.service.endpoint} onClick={register}>保存服务并读取图层</Btn></details>}
  {!loading&&!connections.length&&<p>还没有登记地图服务。可只接入需要的服务，不必先配置其它网关。</p>}
  {connections.map(connection=>{const form=draft?.approval[connection.id]||{},readForm=draft?.read[connection.id]||{},scope=connection.scopes[readForm.scope_id],layers=connection.catalog?.layers||[];
   const setApproval=value=>edit('approval',{...draft.approval,[connection.id]:value}),setRead=value=>edit('read',{...draft.read,[connection.id]:value});
   return <article className="local-sample" key={connection.id}><h4>{connection.name}</h4><p>{labels[connection.status]} · WMS {connection.configuration.version}</p><p>{connection.configuration.endpoint}</p>{connection.error&&<p className="error">{connection.error}</p>}
    {admin&&['queued','running'].includes(connection.status)&&<Btn disabled={locked} onClick={()=>action('/wms/connections/'+connection.id+'/cancel',connection)}>取消目录读取</Btn>}
    {admin&&['failed','cancelled'].includes(connection.status)&&<Btn disabled={locked} onClick={()=>action('/wms/connections/'+connection.id+'/retry',connection)}>按原服务重试目录读取</Btn>}
    {admin&&connection.status==='active'&&<Btn disabled={locked} onClick={()=>action('/wms/connections/'+connection.id+'/pause',connection)}>暂停此服务的新读取</Btn>}
    {admin&&connection.status==='paused'&&<Btn disabled={locked} onClick={()=>action('/wms/connections/'+connection.id+'/resume',connection)}>恢复已确认的读取范围</Btn>}
    {admin&&draft&&['catalog_ready','active'].includes(connection.status)&&<details><summary>确认允许读取的图层与范围</summary><Field label="地图图层"><select aria-label={'地图图层 '+connection.name} disabled={locked} value={form.layer||''} onChange={e=>{const layer=layers.find(l=>l.name===e.target.value),crs=Object.keys(layer?.bounds||{})[0]||'';setApproval({layer:e.target.value,crs,bbox:layer?.bounds[crs]||['','','',''],style:'',confirm:false});}}><option value="">请选择服务声明的图层</option>{layers.map(l=><option key={l.name} value={l.name} disabled={!Object.keys(l.bounds).length}>{l.title}{!Object.keys(l.bounds).length?'（尚无可用坐标范围）':''}</option>)}</select></Field>{form.layer&&<><Field label="图层坐标系"><select aria-label="图层坐标系" disabled={locked} value={form.crs} onChange={e=>{const layer=layers.find(l=>l.name===form.layer);setApproval({...form,crs:e.target.value,bbox:layer.bounds[e.target.value],confirm:false});}}>{Object.keys(layers.find(l=>l.name===form.layer)?.bounds||{}).map(c=><option key={c}>{c}</option>)}</select></Field><Field label="地图样式"><select aria-label="地图样式" disabled={locked} value={form.style||''} onChange={e=>setApproval({...form,style:e.target.value,confirm:false})}><option value="">服务默认样式</option>{layers.find(l=>l.name===form.layer)?.styles.map(s=><option key={s}>{s}</option>)}</select></Field><Bounds {...{Field}} prefix={'允许范围 '+connection.name} value={form.bbox||['','','','']} disabled={locked} onChange={bbox=>setApproval({...form,bbox,confirm:false})}/><p>经纬度填写西、南、东、北；投影坐标填写X/Y。后台处理协议轴顺序，界面不要求手动交换坐标。</p><label className="media-channel-choice"><input type="checkbox" disabled={locked} checked={!!form.confirm} onChange={e=>setApproval({...form,confirm:e.target.checked})}/>确认仅允许读取此图层、样式与范围，不授予设备控制权限</label><Btn primary disabled={locked||!form.confirm} onClick={()=>approve(connection,form)}>保存只读图层与范围</Btn></>}</details>}
    {Object.values(connection.scopes).map(s=><div className="wms-source-registration" key={s.id}><p>已确认：{s.title} · {s.crs} · 范围 {s.bbox.join('，')} · {s.style||'服务默认样式'}</p>
    {admin&&workspace&&onSave&&onCommit&&draft&&<details><summary>用于哪些业务</summary><p>仅查询此范围已保存的地图；先读取地图并确认RGB后可登记。新的网络读取和业务对象状态不由此自动授权。</p>{wmsBusinessNames(currentWorkspace.current).length?wmsBusinessNames(currentWorkspace.current).map(name=>{const id=connection.id+':'+s.id,selected=draft.scopeBusinesses?.[id]||currentWorkspace.current.resources?.find(r=>r.details?.wms_connection_id===connection.id&&r.details?.scope_id===s.id)?.businesses||[];return <label className="media-channel-choice" key={name}><input type="checkbox" aria-label={'地图来源用于 '+name} disabled={locked} checked={selected.includes(name)} onChange={e=>edit('scopeBusinesses',{...current.current.scopeBusinesses,[id]:e.target.checked?[...selected,name]:selected.filter(b=>b!==name)})}/>{name}</label>;}):<p>尚未确定业务清单，可以先登记来源，后续再选择关联业务。</p>}<Btn primary disabled={locked||connection.status!=='active'} onClick={()=>registerSource(connection,s)}>{connection.publications?.[s.id]?'保存业务来源关联':'登记为业务地图来源'}</Btn></details>}
    {connection.publications?.[s.id]&&<p>此范围的只读来源已登记；调用前核对当前权限与版本，领域效果仍需核验。</p>}
   </div>)}
    {writer&&draft&&connection.status==='active'&&<details><summary>读取并保存一幅地图</summary><Field label="已确认的图层范围"><select aria-label={'读取图层范围 '+connection.name} disabled={locked} value={readForm.scope_id||''} onChange={e=>{const s=connection.scopes[e.target.value];setRead({scope_id:e.target.value,bbox:s?.bbox||['','','',''],width:'512',height:'512'});}}><option value="">请选择已确认范围</option>{Object.values(connection.scopes).map(s=><option key={s.id} value={s.id}>{s.title} · {s.crs} · {s.style||'默认样式'}</option>)}</select></Field>{scope&&<><Bounds {...{Field}} prefix={'本次读取 '+connection.name} value={readForm.bbox} disabled={locked} onChange={bbox=>setRead({...readForm,bbox})}/><div className="local-model-fields"><Field label="地图宽度（像素）"><input aria-label="地图宽度（像素）" type="number" min="32" max="2048" step="1" disabled={locked} value={readForm.width} onChange={e=>setRead({...readForm,width:e.target.value})}/></Field><Field label="地图高度（像素）"><input aria-label="地图高度（像素）" type="number" min="32" max="2048" step="1" disabled={locked} value={readForm.height} onChange={e=>setRead({...readForm,height:e.target.value})}/></Field></div><small>可缩小范围；不能超出已确认范围。分辨率是本次渲染请求，不等于卫星实际分辨率。当前不选择时间维度，不把读取时间当采集时间。</small><Btn primary disabled={locked||!catalog?.runtime_installed} onClick={()=>read(connection,readForm)}>读取本次地图并保存</Btn></>}</details>}
   </article>;
  })}
  <details open={reads.length>0||undefined}><summary>地图读取记录（{reads.length}）</summary>{reads.map((row,index)=>!wmsRecordFieldsPresent(row)?<article className="local-sample" key={row?.id||"damaged-"+index}><h4>已保存的地图读取记录需要核对</h4><p className="error">保存结果的结构不完整，不能将它显示为已完成。原记录未删除；请由运维核对原响应与保存证据。</p><Btn disabled={busy} onClick={()=>refresh()}>重新读取地图记录</Btn></article>:<article className="local-sample" key={row.id}><h4>{row.connection_snapshot.scope.title}</h4><p>{labels[row.status]} · 范围 {row.request.bbox.join('，')} · {row.request.width} × {row.request.height} 像素</p>{row.error&&<p className="error">{row.error}</p>}{row.response&&<><p>原PNG响应已保存；采集时间未知。</p><a className="btn" href={'/api/wms/reads/'+row.id+'/response?workspace_id='+encodeURIComponent(account.workspace_id)}>下载原始地图PNG</a></>}{writer&&['queued','running'].includes(row.status)&&<Btn disabled={locked} onClick={()=>action('/wms/reads/'+row.id+'/cancel',row)}>取消这次读取</Btn>}{writer&&['failed','cancelled'].includes(row.status)&&<Btn disabled={locked} onClick={()=>action('/wms/reads/'+row.id+'/retry',row)}>按原请求重试，已有PNG不重读</Btn>}{row.status==='succeeded'&&<><p>本地派生RGB影像已保存，透明区域 {row.result.transparent_pixels} 像素。请继续核对RGB与地图参考后再用于业务；不是原始卫星波段。</p><a className="btn" href={'/api/geo/scenes/'+row.scene_id+'/original?workspace_id='+encodeURIComponent(account.workspace_id)}>下载派生GeoTIFF</a><Btn disabled={busy} onClick={()=>onScene(row.scene_id)}>查看派生影像与RGB设置</Btn><details><summary>固定服务与请求依据</summary><p>{row.connection_snapshot.configuration.endpoint} · WMS {row.connection_snapshot.configuration.version} · {row.connection_snapshot.scope.layer} · {row.connection_snapshot.scope.crs} · {row.connection_snapshot.scope.style||'默认样式'}</p></details></>}</article>)}</details>
 </section>;
}
