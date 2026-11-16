import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {wmsBusinessNames} from './wms-form.mjs';

// These are published workspace capabilities; adding a resource only binds an
// existing, verified capability to the current configuration draft.
const allowedKinds={video:['snapshot','embedded_live'],maps:['satellite']};
const typeText={snapshot:'按时更新的静态画面',embedded_live:'第三方公开直播',satellite:'日合成遥感影像'};
const statusText={draft:'已登记，尚未发布',validated:'已核对，尚未发布',published:'来源已发布',revoked:'已撤销'};

export default function PublicObservationStudio({category,account,workspace,onCommit,onSave,onBusy,Btn}){
 const [templates,setTemplates]=useState([]),[registrations,setRegistrations]=useState([]);
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const alive=useRef(true),current=useRef({account,workspace});current.current={account,workspace};
 const admin=account?.role==='admin';
 const session=()=>`${current.current.account?.workspace_id||''}:${current.current.account?.user_id||''}:${current.current.account?.role||''}`;
 async function load(){
  const identity=session();
  const [sources,registered]=await Promise.all([json('/public-observations/templates'),json('/public-observations/registrations')]);
  if(!Array.isArray(sources)||!Array.isArray(registered))throw Error('来源目录格式不完整，未改变配置。');
  if(!alive.current||identity!==session())throw Error('工作区或账号已变化，未更新当前页面。');
  setTemplates(sources);setRegistrations(registered);setLoading(false);
 }
 useEffect(()=>{alive.current=true;load().catch(e=>{if(alive.current){setError(e.message);setLoading(false);}});return()=>{alive.current=false;onBusy?.(false);};},[]);
 function sourceResource(t,row,existing){return {...(existing||{}),id:existing?.id||'public:'+row.source_id,
  category:t.kind==='satellite'?'maps':'video',protocol:'PUBLIC_OBSERVATION',name:existing?.name||t.name,
  status:existing?.status||'candidate',caps:existing?.caps||[t.kind==='satellite'?'影像读取':'观测资料读取'],
  businesses:existing?.businesses||[],checks:{...(existing?.checks||{}),registered:true},
  details:{...(existing?.details||{}),public_source_id:row.source_id,manifest_hash:row.manifest_hash,source_kind:t.kind,read_only:true}};}
 async function act(id,operation){
  if(busy)return;const identity=session();setBusy(id);onBusy?.(true);setError('');setNotice('');
  const check=()=>{if(!alive.current||identity!==session())throw Error('账号或工作区已变化，原登记保留，请重新核对。');};
  try{await operation(check);check();await load();}catch(e){if(alive.current)setError(e?.message||'操作未完成，请重试。');}
  finally{if(alive.current)setBusy('');onBusy?.(false);}
 }
 function match(t){return registrations.filter(r=>r.provider_id===t.provider_id);}
 function bound(row){return (workspace?.resources||[]).find(r=>r.protocol==='PUBLIC_OBSERVATION'&&r.details?.public_source_id===row?.source_id);}
 async function add(t){return act(t.provider_id,async check=>{
  if(!admin)throw Error('请由工作区管理员登记并发布公开来源。');
  await onSave();check();
  let rows=match(t);if(rows.length>1)throw Error('发现重复来源登记，请先核对服务器记录。');
  let row=rows[0];
  if(!row){row=await json('/public-observations/registrations',{method:'POST',body:JSON.stringify({provider_id:t.provider_id,confirm_read_only:true})});check();}
  if(row.status==='revoked')throw Error('此来源已撤销，不能加入配置。');
  if(row.status!=='published'){row=await json('/public-observations/'+encodeURIComponent(row.source_id)+'/publish',{method:'POST',body:JSON.stringify({version:row.version,confirm_read_only:true})});check();}
  if(row.status!=='published'||row.provider_id!==t.provider_id||!row.source_id||!/^[a-f0-9]{64}$/.test(row.manifest_hash||''))throw Error('来源发布结果不完整，原登记保留，请重试读取。');
  const accepted=onCommit(draft=>{
   check();const matches=(draft.resources||[]).filter(r=>r.protocol==='PUBLIC_OBSERVATION'&&r.details?.public_source_id===row.source_id);
   if(matches.length>1)throw Error('草稿中有重复来源关联，请先核对，未修改。');
   return {...draft,resources:[...(draft.resources||[]).filter(r=>r!==matches[0]),sourceResource(t,row,matches[0])]};
  });
  if(accepted!==true)throw Error('来源已发布，但配置关联尚未保存；来源记录保留，可重试。');
  await onSave();check();setNotice('来源已登记并加入当前配置。生成并启用实例后，业务工作台才能读取；来源是否在线需在使用时核验。');
 });}
 async function unbind(row){return act(row.source_id,async check=>{
  await onSave();check();const accepted=onCommit(draft=>{check();return {...draft,resources:(draft.resources||[]).filter(r=>!(r.protocol==='PUBLIC_OBSERVATION'&&r.details?.public_source_id===row.source_id))};});
  if(accepted!==true)throw Error('关联未移除，请处理配置保存问题后重试。');
  await onSave();check();setNotice('已从当前草稿移除关联，已登记来源和已启用实例不受影响。');
 });}
 async function setBusiness(row,name,checked){return act(row.source_id,async check=>{
  await onSave();check();const accepted=onCommit(draft=>{check();
   if(!wmsBusinessNames(draft).includes(name))throw Error('业务清单已变化，请重新核对。');
   const matches=(draft.resources||[]).filter(r=>r.protocol==='PUBLIC_OBSERVATION'&&r.details?.public_source_id===row.source_id);
   if(matches.length!==1)throw Error('草稿关联已变化，请重新读取。');
   return {...draft,resources:draft.resources.map(r=>r===matches[0]?{...r,businesses:checked?[...new Set([...(r.businesses||[]),name])]:(r.businesses||[]).filter(b=>b!==name)}:r)};
  });
  if(accepted!==true)throw Error('业务关联未保存，请先处理配置保存问题。');
  await onSave();check();setNotice('业务关联已保存。新的配置需重新生成并启用实例后生效。');
 });}
 const shown=templates.filter(t=>allowedKinds[category]?.includes(t.kind));
 return <section className="local-model-card public-observation-studio" aria-label="公开观测资料">
  <h2>公开观测资料</h2>
  <p>选择需要的公开资料并加入业务配置；这些资料只读，不会作为当前林区事件的现场证据。生成并启用实例后，业务工作台按实例配置读取。</p>
  {loading&&<p role="status">正在读取当前工作区的来源目录…</p>}
  {error&&<p className="error" role="alert">{error} <Btn disabled={!!busy} onClick={()=>{setLoading(true);setError('');load().catch(e=>{setError(e.message);setLoading(false);});}}>重试读取</Btn></p>}
  {notice&&<p role="status">{notice}</p>}
  {!loading&&!error&&!shown.length&&<p>当前没有可配置的此类公开来源。</p>}
  {shown.map(t=>{const matches=match(t),row=matches.length===1?matches[0]:null,resource=bound(row),names=wmsBusinessNames(workspace);
   return <article className="local-sample" key={t.provider_id}>
    <h3>{t.name}</h3><p>{typeText[t.kind]||'公开资料'} · {t.provider} · {t.location}</p>
    <p>{t.limitations}</p><p><a href={t.source_page} target="_blank" rel="noopener noreferrer">查看来源说明</a></p>
    {matches.length>1?<p className="error">来源登记存在重复，请管理员核对后再操作。</p>:<p>{row?(statusText[row.status]||'登记状态待核对'):'尚未登记'} · {resource?'已加入当前配置':'未加入当前配置'}</p>}
    {!admin&&<p>仅管理员可以登记来源或调整关联。</p>}
    {admin&&matches.length<=1&&<div className="actions">{!resource?<Btn primary disabled={!!busy||row?.status==='revoked'} onClick={()=>add(t)}>{row?.status==='published'?'加入当前配置':'登记并加入当前配置'}</Btn>:<Btn disabled={!!busy} onClick={()=>unbind(row)}>从当前配置移除</Btn>}</div>}
    {resource&&<div><h4>关联业务</h4>{names.length?names.map(name=><label className="media-channel-choice" key={name}><input type="checkbox" aria-label={t.name+'用于'+name} disabled={!admin||!!busy} checked={(resource.businesses||[]).includes(name)} onChange={e=>setBusiness(row,name,e.target.checked)}/>{name}</label>):<p>尚未确定业务清单，可在确定后选择。</p>}</div>}
   </article>;
  })}
 </section>;
}
