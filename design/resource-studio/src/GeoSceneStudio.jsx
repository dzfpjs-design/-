import React,{useEffect,useRef,useState} from 'react';
import {api,json,hashBytes} from './runtime-api';
import GeoRuns from './GeoRuns';
import GeoQualityStudioV351 from './GeoQualityStudioV351';
import GeoUpload from './GeoUpload';
import GeoPairs from './GeoPairs';
import LearnedGeoPairs from './LearnedGeoPairs';
import WMSStudio from './WMSStudio';
import {savedForm,readDraft,draftKey,formPayload} from './geo-form.mjs';
const states={queued:'等待本地读取',running:'正在本地处理',parsed:'空间信息已保存，请确认RGB设置',ready:'RGB预览已保存',failed:'处理失败，原件保留',cancelled:'已取消，原件保留'};
const references={affine_available:'已读取坐标系与仿射变换，地面定位精度未核验',missing_crs:'没有坐标系，暂不能确定地理位置',missing_transform:'没有可靠的像素到地图变换',unsupported_gcps_or_rpc:'当前变换类型尚未支持，不能直接生成地图位置'};
function Preview({scene,Btn}){
 const [url,setURL]=useState(''),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 useEffect(()=>{let alive=true,value;setURL('');setError('');api('/geo/scenes/'+scene.id+'/preview').then(async r=>{const b=await r.arrayBuffer();if(await hashBytes(b)!==scene.preview.sha256)throw Error('影像预览摘要不一致，未显示。');value=URL.createObjectURL(new Blob([b],{type:'image/png'}));if(alive)setURL(value);else URL.revokeObjectURL(value);}).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;if(value)URL.revokeObjectURL(value);};},[scene.id,scene.preview.sha256,retry]);
 return <div>{error&&<p role="alert" className="error">{error}<Btn onClick={()=>setRetry(v=>v+1)}>重新读取预览</Btn></p>}{!url&&!error&&<p role="status">正在核对保存的RGB预览…</p>}{url&&<img className="detection-image" src={url} alt={scene.name+' RGB缩放预览'}/>}<p>RGB预览 {scene.preview.width} × {scene.preview.height} · 最近邻采样 · 有效预览像素 {scene.preview.valid_pixels}/{scene.preview.sampled_pixels}</p><small>这是显示预览，不能把采样比例当成整幅影像的数据质量结论；黑色无效区域不代表真实地物。</small></div>;
}
function RGBForm({scene,workspace,save,busy,Btn,Field}){
 const meta=scene.metadata,key=draftKey(workspace,scene.id);
 const [form,setForm]=useState(()=>{try{return readDraft(localStorage,key,scene);}catch{return savedForm(scene);}}),[draftError,setDraftError]=useState('');
 const {bands,ranges,map,observed}=form;
 const processing=['queued','running'].includes(scene.status);busy=busy||processing;
 function edit(field,update){setForm(old=>({...old,[field]:typeof update==='function'?update(old[field]):update,dirty:true}));}
 useEffect(()=>{if(!form.dirty&&form.version!==scene.version)setForm(savedForm(scene));},[scene.version,form.dirty]);
 useEffect(()=>{try{if(form.dirty)localStorage.setItem(key,JSON.stringify(form));else localStorage.removeItem(key);setDraftError('');}catch{setDraftError('浏览器无法保存未提交的输入；请保持此页打开，确认后提交到服务器。');}},[form,key]);
 async function submit(){const confirmed=await save(formPayload(form,needsRange));if(confirmed)setForm(savedForm(confirmed));}

 const needsRange=bands.some(b=>b&&meta.dtypes[Number(b)-1]!=='uint8');
 return <section><h4>确认RGB显示与影像来源</h4>{form.dirty&&<p role="status">输入尚未提交，暂存在此浏览器。{form.version!==scene.version?'服务器记录已更新，请核对最新设置；当前输入保留，提交会检查版本。':'点击保存后才会写入工作区。'}</p>}{draftError&&<p role="alert" className="error">{draftError}</p>}{form.dirty&&<Btn disabled={busy} onClick={()=>setForm(savedForm(scene))}>放弃当前输入并读取已保存设置</Btn>}<p>波段名称不能代替实际用途。高位深影像需明确显示范围，系统不会自动拉伸或把前三波段当成RGB。</p><div className="local-model-fields">{['红','绿','蓝'].map((name,i)=><Field key={name} label={name+'色通道使用的波段'}><select aria-label={name+'色通道使用的波段'} disabled={busy} value={bands[i]} onChange={e=>edit('bands',v=>v.map((x,j)=>j===i?Number(e.target.value)||'':x))}><option value="">请选择</option>{Array.from({length:meta.bands},(_,n)=><option key={n} value={n+1}>波段 {n+1} · {meta.dtypes[n]}</option>)}</select>{needsRange&&<div><input aria-label={name+'色通道显示最小值'} type="number" disabled={busy} value={ranges[i][0]} onChange={e=>edit('ranges',v=>v.map((x,j)=>j===i?[e.target.value,x[1]]:x))}/><input aria-label={name+'色通道显示最大值'} type="number" disabled={busy} value={ranges[i][1]} onChange={e=>edit('ranges',v=>v.map((x,j)=>j===i?[x[0],e.target.value]:x))}/></div>}</Field>)}</div>
  <label className="media-channel-choice"><input type="checkbox" aria-label="确认影像已有地图参考" checked={map} disabled={busy||meta.georeference_status!=='affine_available'} onChange={e=>edit('map',e.target.checked)}/>我确认这是具有地图参考的影像；坐标精度仍需按来源核验</label>
  <Field label="采集时间（可选，按中国时间填写）" note="未知时留空，上传时间不会作为拍摄时间。清空已填写的时间并保存，可改回未知；系统不会猜测采集时间。"><input aria-label="影像采集时间" type="datetime-local" disabled={busy} value={observed} onChange={e=>edit('observed',e.target.value)}/></Field>
  <Btn primary disabled={busy||bands.some(b=>!b)||new Set(bands).size!==3||needsRange&&ranges.some(r=>r.some(x=>x==='')||!Number.isFinite(Number(r[0]))||!Number.isFinite(Number(r[1]))||Number(r[0])>=Number(r[1]))} onClick={submit}>保存设置并生成RGB预览</Btn>
 </section>;
}
export default function GeoSceneStudio({account,Btn,Field,workspace,onSave,onCommit,externalScene}){
 const [rows,setRows]=useState([]),[catalog,setCatalog]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[focus,setFocus]=useState('');const active=useRef(true);
 async function load(){const [c,r]=await Promise.all([json('/geo/catalog'),json('/geo/scenes')]);if(active.current){setCatalog(c);setRows(r);}}
 useEffect(()=>{active.current=true;load().catch(e=>setError(e.message));const timer=setInterval(()=>load().catch(e=>{if(active.current)setError(e.message);}),3000);return()=>{active.current=false;clearInterval(timer);};},[]);
 useEffect(()=>{if(externalScene?.workspace===account.workspace_id&&externalScene.id){setFocus(externalScene.id);load().catch(e=>{if(active.current)setError(e.message);});}},[externalScene,account.workspace_id]);
 async function act(fn){setBusy(true);setError('');try{const result=await fn();await load();return result;}catch(e){setError(e.message);}finally{setBusy(false);}}
 const allowed=['admin','operator'].includes(account.role);
 return <section id="geo-scene-studio" className="local-model-card"><h2>影像与地图</h2><p>上传GeoTIFF，或通过WMS读取已授权图层和范围；保存来源、地理参考与RGB预览。专业模型在本地处理，业务任务选择已关联影像与固定范围，保存核查说明和候选回执。WMS派生影像是地图渲染图，不是卫星原始波段，不自动更新业务对象。</p><small>本地GeoTIFF单幅上限512MB；WMS地图最长边2048像素。WMTS 和 ArcGIS 服务请使用上方的地图服务入口；VRT外部引用尚未接入。</small>{error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  <WMSStudio key={'wms:'+account.workspace_id+':'+account.username} {...{account,Btn,Field,workspace,onSave,onCommit}} onScene={async id=>{setFocus(id);await load();}}/>
  <GeoUpload account={account} catalog={catalog} disabled={busy} Btn={Btn} onSaved={async row=>{setFocus(row.id);await load();}}/><Btn disabled={busy} onClick={()=>load().catch(e=>setError(e.message))}>刷新影像记录</Btn>{!catalog?.runtime_installed&&<p className="warning-text">本地地理环境尚未就绪，上传入口保持禁用。</p>}
  {!rows.length&&<p>还没有保存的影像。原件与空间信息保存在当前工作区。</p>}{rows.map(row=><article className="local-sample" key={row.id}><h3>{row.name}</h3><p>{states[row.status]} · {Math.round(row.size_bytes/1024)} KB · 本地处理</p>{row.quality_origin_v351&&<p>这是根据用户声明关联的 SCL 质量层生成的派生影像；质量分类及来源关联未经过独立认证。</p>}{row.error&&<p className="error">{row.error}</p>}<div className="actions"><Btn onClick={()=>setFocus(focus===row.id?'':row.id)}>{focus===row.id?'收起影像':'查看影像'}</Btn><a className="btn" href={'/api/geo/scenes/'+row.id+'/original?workspace_id='+encodeURIComponent(account.workspace_id)}>{row.quality_origin_v351?'下载有效区派生GeoTIFF':row.wms_origin?"下载WMS派生GeoTIFF":"下载已核对的GeoTIFF原件"}</a>{['queued','running'].includes(row.status)&&<Btn disabled={busy||!allowed} onClick={()=>act(()=>json('/geo/scenes/'+row.id+'/cancel',{method:'POST',body:JSON.stringify({version:row.version})}))}>取消当前处理</Btn>}{['failed','cancelled'].includes(row.status)&&<Btn disabled={busy||!allowed} onClick={()=>act(()=>json('/geo/scenes/'+row.id+'/retry',{method:'POST',body:JSON.stringify({version:row.version})}))}>保留记录并重试处理</Btn>}</div>
   {focus===row.id&&row.metadata&&<><p>{row.quality_origin_v351?'有效区派生影像':row.wms_origin?'WMS派生影像':'原影像'} {row.metadata.width} × {row.metadata.height} · {row.metadata.bands} 波段 · 坐标系 {row.metadata.crs||'未提供'}</p><p>{references[row.metadata.georeference_status]}</p><p>像素轴尺度（原坐标单位）：{row.metadata.pixel_scale_native.map(v=>v.toPrecision(5)).join(' × ')} {row.metadata.axis_units?.join(' / ')||'单位未知'}；这不是未经核验的真实地面分辨率。</p><p>采集时间：{row.configuration?.observed_at?new Date(row.configuration.observed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（用户填写）':'未知'}</p>{row.preview&&<Preview scene={row} Btn={Btn}/>}{allowed&&<RGBForm key={row.id} scene={row} workspace={account.workspace_id} busy={busy} Btn={Btn} Field={Field} save={value=>act(async()=>{const confirmed=await json('/geo/scenes/'+row.id+'/configuration',{method:'PUT',body:JSON.stringify(value)});setNotice('RGB设置已保存，原件与此前版本保留；预览在本地后台生成。');return confirmed;})}/>}<details><summary>此前设置与处理记录（{row.history.length}）</summary>{row.history.map((h,i)=><p key={i}>第 {h.generation} 次 · {states[h.status]} · {h.error||'记录保留'}</p>)}</details><GeoRuns key={row.id+':runs'} scene={row} account={account} Btn={Btn} Field={Field}/></>}
  </article>)}
  <GeoQualityStudioV351 key={'quality:'+account.workspace_id+':'+account.username} scenes={rows} account={account} Btn={Btn} Field={Field}/>
  <GeoPairs key={account.workspace_id+':'+account.username} scenes={rows} account={account} Btn={Btn} workspace={workspace} onSave={onSave} onCommit={onCommit}/>
  <LearnedGeoPairs key={'learned:'+account.workspace_id+':'+account.username} scenes={rows} account={account} Btn={Btn}/>
 </section>;
}
