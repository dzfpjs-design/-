import React,{useEffect,useRef,useState} from 'react';
import {api,json,hashBytes} from './runtime-api';
import {resolveAssociation} from './geo-recovery.mjs';
import {emptyPair,readPair,pairBody,pairDraftKey,pairReviewKey} from './geo-pair-form.mjs';
const states={queued:'等待本地比较',running:'正在本地比较',succeeded:'比较证据已保存',failed:'比较失败，原件保留',cancelled:'已取消，原件保留'};
const labels={before:'A影像（参考网格）',after:'B影像（已对齐到A）',valid:'共同有效区域：白色可比较、黑色未知',difference:'RGB像素差异：亮度表示数值差，不是变化概率',probability:'模型变化概率：越亮越高（0～1）；未知区请对照有效区域',candidate:'超过阈值的候选：透明区需结合有效区域判断'};

export function ComparisonEvidence({row,account,Btn,basePath='/geo/pairs/'+row.id,compact=false}){
 const [images,setImages]=useState({}),[error,setError]=useState(''),[attempt,setAttempt]=useState(0),[expanded,setExpanded]=useState(false);
 useEffect(()=>{let alive=true;const urls=[];setImages({});setError('');(async()=>{
  const loaded={};for(const kind of ['before','after','valid',row.request_kind==='learned_binary_change'?'probability':'difference','candidate']){const ev=row.result.artifacts[kind];
   const res=await api(basePath+'/artifacts/'+kind),raw=await res.arrayBuffer();if(await hashBytes(raw)!==ev.sha256)throw Error('比较证据摘要不一致，未显示。');
   if(!alive)return;loaded[kind]=URL.createObjectURL(new Blob([raw],{type:'image/png'}));urls.push(loaded[kind]);
  }if(alive)setImages(loaded);
 })().catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;urls.forEach(u=>URL.revokeObjectURL(u));};},[account.workspace_id,row.id,row.result_hash,basePath,attempt]);
 return <div>{error&&<p className="error" role="alert">{error}<Btn onClick={()=>setAttempt(v=>v+1)}>重新核对证据</Btn></p>}{!error&&!Object.keys(images).length&&<p role="status">正在读取并核对两幅原件与五份比较证据…</p>}{Object.keys(images).length>0&&<><div className={'pair-evidence'+(compact&&!expanded?' pair-evidence-compact':'')}>{Object.entries(images).map(([kind,url])=><figure key={kind}><figcaption>{labels[kind]}</figcaption><img src={url} alt={labels[kind]}/></figure>)}</div>{compact&&<Btn onClick={()=>setExpanded(v=>!v)}>{expanded?'收起为紧凑对照':'放大对照证据'}</Btn>}</>}</div>;
}

export function Review({row,account,Btn,onSaved}){
 const key=pairReviewKey(account.workspace_id,account.username,row.id),active=useRef(true);
 const [initial]=useState(()=>{const saved={note:row.review?.note||'',decision:row.review?.decision||'uncertain',version:row.version,dirty:false};try{const raw=localStorage.getItem(key),value=raw?JSON.parse(raw):saved;if(typeof value.note!=='string'||!['keep','exclude','uncertain'].includes(value.decision)||!Number.isInteger(value.version)||value.version<1)throw Error('invalid');return {form:value,error:''};}catch{return {form:saved,error:'核查草稿无法读取，原记录保留；未覆盖旧输入，请先恢复浏览器存储。'};}});const [form,setForm]=useState(initial.form);
 const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[storageError,setStorageError]=useState(initial.error);
 useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
 useEffect(()=>{if(!form.dirty&&row.version!==form.version)setForm({note:row.review?.note||'',decision:row.review?.decision||'uncertain',version:row.version,dirty:false});},[row.version,form.dirty]);
 useEffect(()=>{if(!form.dirty)return;try{localStorage.setItem(key,JSON.stringify(form));setStorageError('');}catch{setStorageError('核查草稿暂存失败，请保持此页打开；恢复浏览器存储后再提交。');}},[key,form]);
 const allowed=['admin','operator'].includes(account.role);
 async function save(){setBusy(true);setError('');setNotice('');try{const saved=await json('/geo/pairs/'+row.id+'/review',{method:'PUT',body:JSON.stringify({version:form.version,note:form.note,decision:form.decision})});if(!active.current)return;onSaved(saved);setForm({note:saved.review.note,decision:saved.review.decision,version:saved.version,dirty:false});try{localStorage.removeItem(key);}catch{setStorageError('说明已保存到服务器，浏览器旧草稿未能清除；以服务器记录为准。');}setNotice('核查说明已保存到服务器，可在刷新与重启后恢复。');}catch(e){if(active.current)setError(e.message+' 当前说明保留。');}finally{if(active.current)setBusy(false);}}
 return <div><h4>人工核查</h4>{row.review&&<p>已保存说明：{row.review.note} · {({keep:'保留候选',exclude:'排除候选',uncertain:'待进一步核实'})[row.review.decision]}</p>}
  {allowed&&!initial.error&&<><label>对候选结果的判断<select value={form.decision} disabled={busy} onChange={e=>setForm({...form,decision:e.target.value,dirty:true})}><option value="uncertain">待进一步核实</option><option value="keep">保留候选</option><option value="exclude">排除候选</option></select></label><label>核查说明<textarea rows={3} maxLength={4000} value={form.note} disabled={busy} onChange={e=>setForm({...form,note:e.target.value,dirty:true})}/></label><div className="actions"><Btn disabled={busy||!!storageError||!form.note.trim()||form.version!==row.version} onClick={save}>{busy?'正在保存核查说明…':'保存核查说明'}</Btn>{form.version!==row.version&&<Btn onClick={()=>setForm({...form,version:row.version,dirty:true})}>核对服务器说明后保留当前文字再提交</Btn>}</div></>}
  {storageError&&<p className="error" role="alert">{storageError}</p>}{error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{row.review_history.length>0&&<details><summary>此前核查说明（{row.review_history.length}）</summary>{row.review_history.map((r,i)=><p key={i}>{r.note} · {r.decision}</p>)}</details>}
 </div>;
}

export default function GeoPairs({scenes,account,Btn,workspace,onSave,onCommit}){
 const currentWorkspace=useRef(workspace);currentWorkspace.current=workspace;
 const key=pairDraftKey(account.workspace_id,account.username),context=useRef(key),active=useRef(true),pending=useRef(null);
 const [form,setForm]=useState(emptyPair),[draftReady,setDraftReady]=useState(false),[draftError,setDraftError]=useState(''),[rows,setRows]=useState([]),[catalog,setCatalog]=useState(null),[loaded,setLoaded]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[focus,setFocus]=useState('');
 const allowed=['admin','operator'].includes(account.role),sources=scenes.filter(s=>s.status==='ready'&&s.configuration?.map_image_confirmed);
 async function load(){const [c,r]=await Promise.all([json('/geo/pairs/catalog'),json('/geo/pairs')]);if(active.current&&context.current===key){setCatalog(c);setRows(r);setLoaded(true);}}
 useEffect(()=>{active.current=true;context.current=key;try{const f=readPair(localStorage,key);pending.current=f.pending;setForm(f);setDraftReady(true);}catch(e){setDraftError(e.message);setDraftReady(false);}load().catch(e=>setError(e.message));const timer=setInterval(()=>load().catch(e=>{if(active.current)setError(e.message);}),3000);return()=>{active.current=false;clearInterval(timer);};},[key]);
 function persist(value){localStorage.setItem(key,JSON.stringify(value));}
 function edit(update){if(busy||pending.current)return;const next={...form,...update};try{persist(next);setDraftError('');}catch{setDraftError('比较草稿无法暂存，尚未发送；请保持此页打开并恢复浏览器存储。');}setForm(next);}
 async function submit(){setBusy(true);setError('');setNotice('');try{
  const body=pending.current||pairBody(form,scenes,()=>crypto.randomUUID());const next={...form,pending:body};persist(next);pending.current=body;setForm(next);
  const expectedHash=await hashBytes(new TextEncoder().encode(JSON.stringify(body,Object.keys(body).sort())));
  const call=(path,options)=>{if(!active.current||context.current!==key)throw Error('所属工作区已变化，原比较请求保留。');return json(path,options);};
  const saved=await resolveAssociation(body,id=>call('/geo/pairs/requests/'+encodeURIComponent(id)+'?expected_hash='+expectedHash),value=>call('/geo/pairs',{method:'POST',body:JSON.stringify(value)}));if(!active.current||context.current!==key)return;
  setRows(old=>[saved,...old.filter(r=>r.id!==saved.id)]);setFocus(saved.id);setNotice('比较请求已保存，原件与RGB设置已冻结。后台只执行本地像素差异基线。');
  persist({...form,pending:null});pending.current=null;setForm({...form,pending:null});
  try{await load();}catch(e){setError('请求已保存，清单暂未刷新：'+e.message);}
 }catch(e){if(active.current&&context.current===key){if(e.definitelyRejected){try{persist({...form,pending:null});pending.current=null;setForm({...form,pending:null});}catch{}}setError(e.message+(pending.current?' 原请求尚待核对，来源与范围保持锁定。':' 当前输入保留。'));}}finally{if(active.current&&context.current===key)setBusy(false);}}
 async function act(row,action){setBusy(true);setError('');try{const r=await json('/geo/pairs/'+row.id+'/'+action,{method:'POST',body:JSON.stringify({version:row.version})});if(active.current)setRows(old=>old.map(v=>v.id===r.id?r:v));}catch(e){if(active.current)setError(e.message);}finally{if(active.current)setBusy(false);}}
 async function register(row){setBusy(true);setError('');setNotice('');try{await onSave();const saved=await json('/geo/pairs/'+row.id+'/register-business',{method:'POST',body:JSON.stringify({version:row.version})});if(!active.current||context.current!==key)return;
  const latest=currentWorkspace.current,connectionID='local-geo-pair-node:'+saved.runtime_hash.slice(0,16),id='local-pair-tool:'+saved.capability_id;
  const connection={id:connectionID,category:'tools',name:'本地影像对照服务',protocol:'本地受管工具',endpoint:'',auth:'无需认证',status:'local_ready'};
  const resource={id,category:'tools',name:'本地双影像像素差异（阈值'+saved.threshold+'）',connection:connectionID,protocol:'本地受管工具',status:'candidate',caps:['双影像像素候选','共同有效与未知区域'],businesses:[],checks:{installed:true,domain_evaluation:false},details:{capability_id:saved.capability_id,runtime_hash:saved.runtime_hash,threshold:saved.threshold,alignment:saved.alignment,deployment:'local',learned_model:false}};
  if(!latest||!onCommit({...latest,connections:[...latest.connections.filter(c=>c.id!==connectionID),connection],resources:[...latest.resources.filter(r=>r.id!==id),resource]}))throw Error('能力已在服务器登记，但资源关联未保存；请按此记录重试，不另建能力。');await onSave();if(active.current)setNotice('本地像素对照能力和资源关联已保存。可在业务节点选择；阈值和对齐方式固定，新任务需明确选择A/B及核对范围，不会认定业务异常。');
 }catch(e){if(active.current)setError(e.message+' 原比较与能力记录保留。');}finally{if(active.current)setBusy(false);}}
 const locked=busy||!!form.pending||!draftReady||!allowed;
 return <section className="local-model-card"><h3>双影像对照</h3><p>核对同一范围的两幅地图影像，在本机保存对齐结果、有效区域与RGB数值差异，供人工核查。像素差异可能来自光照、季节或传感器，不代表已确认的业务变化。</p>
  <p className="warning-text">这里比较两幅影像的颜色差异，不判断变化原因。需要使用专业模型时，请在下方“识别影像变化候选”中明确选择；所有影像分析默认本地运行，不自动转送云端。</p>
  {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{draftError&&<p className="error" role="alert">{draftError}</p>}
  <details open={!!form.pending}><summary>选择影像与比较范围</summary>{sources.length<2&&<p>请先上传两份不同的GeoTIFF，并分别保存RGB设置、确认地图参考。已保存来源会显示在下方。</p>}
   <fieldset disabled={locked}><label>A影像（比较范围以此为准）<select value={form.before} onChange={e=>{const s=sources.find(s=>s.id===e.target.value);edit({before:e.target.value,roi:s?['0','0',String(s.metadata.width),String(s.metadata.height)]:['','','',''],confirmed:false});}}><option value="">选择已保存影像</option>{sources.map(s=><option key={s.id} value={s.id}>{s.name} · {s.metadata.width}×{s.metadata.height}</option>)}</select></label>
   <label>B影像<select value={form.after} onChange={e=>edit({after:e.target.value,confirmed:false})}><option value="">选择不同影像</option>{sources.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
   {[form.before,form.after].map((id,i)=>{const s=scenes.find(s=>s.id===id);return s?.configuration&&<p key={i}>{i?'B':'A'}：{s.name} · RGB {s.configuration.rgb_bands.join('/')} · 范围 {s.configuration.ranges?JSON.stringify(s.configuration.ranges):'0–255'} · 采集时间 {s.configuration.observed_at||'未知（不生成时序结论）'}</p>;})}
   <label>对齐方式<select value={form.alignment} onChange={e=>edit({alignment:e.target.value})}><option value="same_grid">要求两幅影像原始网格一致</option><option value="reproject_after_to_before">将B重投影到A网格（最近邻）</option></select></label><small>重投影使用原件声明的坐标；不能证明实测配准精度或消除定位误差。</small>
   <div className="geo-roi-inputs">{['左像素','上像素','右像素（不含）','下像素（不含）'].map((label,i)=><label key={i}>{label}<input inputMode="numeric" value={form.roi[i]} onChange={e=>edit({roi:form.roi.map((v,j)=>j===i?e.target.value:v)})}/></label>)}</div>
   <label>RGB数值差异阈值（1–255）<input inputMode="numeric" value={form.threshold} onChange={e=>edit({threshold:e.target.value})}/></label><small>比较三通道的最大绝对数值差，阈值不表示模型概率；最多比较1600万像素，不自动缩小影像。</small>
   <label><input type="checkbox" checked={form.confirmed} onChange={e=>edit({confirmed:e.target.checked})}/>我已核对两幅RGB含义与数值范围，可以进行像素对照</label></fieldset>
   <div className="actions"><Btn disabled={busy||!allowed||!draftReady||!!draftError||!catalog?.runtime_installed||!loaded||(!form.pending&&sources.length<2)} onClick={submit}>{busy?'正在保存或核对原请求…':form.pending?'核对原请求并重试':'保存并开始本地像素比较'}</Btn></div>{form.pending&&<p>原请求内容已暂存；结果不确定时不能更换影像或范围。刷新后仍可核对同一请求。</p>}
  </details>
  <Btn disabled={busy} onClick={()=>load().then(()=>setError('')).catch(e=>setError(e.message))}>刷新比较记录</Btn>{!loaded&&<p role="status">正在读取保存的比较记录…</p>}{loaded&&!rows.length&&<p>尚无双影像比较记录。</p>}
  {rows.map(row=><article className="local-sample" key={row.id}><h4>{row.sources[0].name} ↔ {row.sources[1].name}</h4><p>{states[row.status]} · 本地像素差异基线 · {row.chronology==='unknown_dates_A_B_comparison'?'采集日期未知，仅作A/B对照':'已按填写的采集时间排序'}</p>{row.error&&<p role="alert" className="error">{row.error}</p>}
   <div className="actions"><Btn onClick={()=>setFocus(focus===row.id?'':row.id)}>{focus===row.id?'收起对照':'查看对照与核查'}</Btn>{account.role==='admin'&&row.status==='succeeded'&&<Btn disabled={busy||!workspace||!onSave||!onCommit} onClick={()=>register(row)}>登记本地像素对照能力</Btn>}{allowed&&!row.workflow_task_id&&['queued','running'].includes(row.status)&&<Btn disabled={busy} onClick={()=>act(row,'cancel')}>取消当前比较</Btn>}{allowed&&!row.workflow_task_id&&['failed','cancelled'].includes(row.status)&&<Btn disabled={busy} onClick={()=>act(row,'retry')}>保留原请求与来源重试</Btn>}</div>
   {focus===row.id&&<><p>冻结范围：{row.request.roi.join(', ')} · 阈值 {row.request.threshold} · {row.request.alignment==='same_grid'?'原始网格一致':'B重投影到A网格'}；后续来源设置不会覆盖本次比较。</p>{row.status==='succeeded'&&<><p>共同有效 {row.result.common_valid_pixels} / {row.result.total_pixels} 像素 · 未知 {row.result.unknown_pixels} · 差异候选 {row.result.candidate_pixels} 像素</p>{row.result.geometry_issue&&<p className="warning-text">候选地图边界未生成：{row.result.geometry_issue}；像素证据保留。</p>}<ComparisonEvidence row={row} account={account} Btn={Btn}/><Review row={row} account={account} Btn={Btn} onSaved={saved=>setRows(old=>old.map(v=>v.id===saved.id?saved:v))}/><a className="btn" href={'/api/geo/pairs/'+row.id+'/export?workspace_id='+encodeURIComponent(account.workspace_id)}>导出已核对的对照与核查记录</a><small>实测配准、云影屏蔽和领域变化质量未核验；不会自动更新业务对象或发布经验。</small></>}</>}
  </article>)}
 </section>;
}
