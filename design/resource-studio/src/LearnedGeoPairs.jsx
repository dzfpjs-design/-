import React,{useEffect,useRef,useState} from 'react';
import {api,json,hashBytes} from './runtime-api';
import {resolveAssociation} from './geo-recovery.mjs';
import {ComparisonEvidence,Review} from './GeoPairs';
import {learnedPairKey,readLearnedPair,emptyLearnedPair,learnedPairBody} from './learned-pair-form.mjs';

const states={queued:'等待本地分析',running:'正在识别变化候选',succeeded:'结果与来源已保存',failed:'分析失败，原件保留',cancelled:'已取消，原件保留'};
export default function LearnedGeoPairs({scenes,account,Btn}){
 const key=learnedPairKey(account.workspace_id,account.username),active=useRef(true),context=useRef(key);
 const [catalog,setCatalog]=useState(null),[rows,setRows]=useState([]),[form,setForm]=useState(emptyLearnedPair),[draftReady,setDraftReady]=useState(false),[draftError,setDraftError]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[focus,setFocus]=useState('');
 const allowed=['admin','operator'].includes(account.role),model=catalog?.installations[0],ready=model?.status==='ready'&&model.recipe_hash===catalog?.model_recipe_hash&&model.pipeline_hash===catalog?.pipeline_hash;
 const sources=scenes.filter(s=>s.status==='ready'&&s.configuration?.map_image_confirmed),locked=busy||!!form.pending||!draftReady||!allowed;
 async function load(){const [c,r]=await Promise.all([json('/geo/learned-pairs/catalog'),json('/geo/learned-pairs')]);if(active.current&&context.current===key){setCatalog(c);setRows(r);}}
 useEffect(()=>{active.current=true;context.current=key;try{setForm(readLearnedPair(localStorage,key));setDraftReady(true);}catch(e){setDraftError(e.message);setDraftReady(false);}load().catch(e=>setError(e.message));const timer=setInterval(()=>load().catch(e=>{if(active.current)setError(e.message);}),3000);return()=>{active.current=false;clearInterval(timer);};},[key]);
 function persist(value){localStorage.setItem(key,JSON.stringify(value));}
 function edit(update){if(locked)return;const next={...form,...update};try{persist(next);setDraftError('');setForm(next);}catch{setDraftError('草稿暂存失败，请保持此页打开并恢复浏览器存储；尚未提交。');}}
 async function action(fn){setBusy(true);setError('');setNotice('');try{await fn();await load();}catch(e){if(active.current)setError(e.message);}finally{if(active.current)setBusy(false);}}
 async function install(){await action(async()=>{await json('/specialists/installations',{method:'POST',body:JSON.stringify({pack_id:catalog.pack_id,source:'prepared_local',backend:'cpu',...(model?{version:model.version}:{})})});setNotice('本地安装请求已保存，后台正在核对文件并检查模型加载。');});}
 async function submit(){setBusy(true);setError('');setNotice('');try{
  const body=form.pending||learnedPairBody(form,scenes,model,catalog,()=>crypto.randomUUID());
  const pending={...form,pending:body};persist(pending);setForm(pending);
  const expectedHash=await hashBytes(new TextEncoder().encode(JSON.stringify(body,Object.keys(body).sort())));
  const checked=(path,options)=>{if(!active.current||context.current!==key)throw Error('所属工作区已变化，原请求保留。');return json(path,options);};
  const saved=await resolveAssociation(body,id=>checked('/geo/pairs/requests/'+encodeURIComponent(id)+'?expected_hash='+expectedHash),value=>checked('/geo/learned-pairs',{method:'POST',body:JSON.stringify(value)}));
  if(!active.current||context.current!==key)return;setRows(old=>[saved,...old.filter(r=>r.id!==saved.id)]);setFocus(saved.id);
  const next={...form,pending:null};persist(next);setForm(next);setNotice('请求已保存，后台使用固定本地模型与两幅原件分析。');await load();
 }catch(e){if(active.current&&context.current===key){if(e.definitelyRejected){const next={...form,pending:null};try{persist(next);setForm(next);}catch{setDraftError('原请求被拒绝，草稿未能更新；请保持此页打开。');}}setError(e.message+' 原输入或待核对请求保留。');}}finally{if(active.current&&context.current===key)setBusy(false);}}
 async function download(row,kind){await action(async()=>{const base='/geo/pairs/'+row.id,res=await api(kind==='export'?base+'/export':base+'/artifacts/'+kind),raw=await res.arrayBuffer();if(kind!=='export'&&await hashBytes(raw)!==row.result.artifacts[kind].sha256)throw Error('下载证据摘要不一致。');if(!active.current)return;const url=URL.createObjectURL(new Blob([raw],{type:res.headers.get('content-type')}));const a=document.createElement('a');a.href=url;a.download=kind==='export'?'变化分析记录.json':kind==='probability_raster'?'变化概率.tif':'变化候选掩膜.tif';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});}
 const row=rows.find(r=>r.id===focus)||rows[0];
 return <section className="local-model-card"><header><h3>识别影像变化候选</h3><p>使用本地专业模型分析两幅影像，保存概率、候选范围及核对说明。</p></header>
  <p className="warning-text">当前模型以建筑变化数据训练，尚未验证您的业务领域。请结合原件核对候选；业务变化原因和对象状态需另行确认。</p>
  {!catalog&&<p role="status">正在读取本地模型与分析记录…</p>}
  {catalog&&<div><strong>本地模型：</strong>{ready?'已通过本地工程加载检查，可生成候选':model?({queued:'等待安装',installing:'正在核对文件并加载',failed:'安装失败',cancelled:'安装已取消'})[model.status]||'版本需要重新核对':'尚未安装'}
   {model?.smoke?.production_license_verified===false&&<p className="warning-text">生产使用许可待核验。本机工程检查不代表生产使用许可通过。</p>}
   {model?.error&&<p className="error" role="alert">{model.error}</p>}
   {account.role==='admin'&&(!model||['failed','cancelled'].includes(model.status))&&<Btn disabled={busy||!catalog.prepared_available||!catalog.runtime_available} onClick={install}>{model?'按原准备包重试安装':'安装并检查本地模型'}</Btn>}
   {model&&['queued','installing'].includes(model.status)&&account.role==='admin'&&<Btn disabled={busy} onClick={()=>action(()=>json('/specialists/installations/'+model.id+'/cancel',{method:'POST',body:JSON.stringify({version:model.version})}))}>取消安装</Btn>}
   {catalog.prepared_available&&!ready&&<p>已发现本地准备文件；文件摘要、转换回执及模型加载将在安装时核验，文件存在不代表核验通过。</p>}
   {!catalog.prepared_available&&<p>尚未发现本地准备文件。新安装或重试前，请联系管理员准备固定包；不会下载或转为云调用。</p>}
   {!catalog.runtime_available&&<p>本地推理环境尚未就绪，请联系管理员完成安装；当前不会改用云端处理。</p>}
   {!ready&&account.role!=='admin'&&<p>请由管理员安装并检查本地模型。已保存的结果仍可查看。</p>}
  </div>}
  <div className="learned-pair-fields"><label>A参考影像<select aria-label="变化分析A影像" value={form.before} disabled={locked} onChange={e=>{const a=sources.find(s=>s.id===e.target.value);edit({before:e.target.value,roi:a?(a.configuration.window||[0,0,a.metadata.width,a.metadata.height]).map(String):['','','','']});}}><option value="">选择已确认影像</option>{sources.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
   <label>B后期影像<select aria-label="变化分析B影像" value={form.after} disabled={locked} onChange={e=>edit({after:e.target.value})}><option value="">选择已确认影像</option>{sources.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
  </div>
  <div className="learned-pair-fields">{['左侧像素','上侧像素','右侧像素','下侧像素'].map((label,i)=><label key={label}>{label}<input aria-label={'变化分析'+label} inputMode="numeric" value={form.roi[i]} disabled={locked} onChange={e=>edit({roi:form.roi.map((v,j)=>i===j?e.target.value:v)})}/></label>)}</div>
  <div className="learned-pair-fields"><label>影像对齐<select aria-label="变化分析对齐方式" value={form.alignment} disabled={locked} onChange={e=>edit({alignment:e.target.value})}><option value="same_grid">两幅原始网格一致</option><option value="reproject_after_to_before">允许将B重投影到A</option></select></label><label>候选概率阈值（0～1）<input aria-label="变化候选概率阈值" inputMode="decimal" value={form.threshold} disabled={locked} onChange={e=>edit({threshold:e.target.value})}/></label></div>
  <label><input type="checkbox" checked={form.confirmed} disabled={locked} onChange={e=>edit({confirmed:e.target.checked})}/>我已核对两幅影像的RGB含义与显示范围，可以进行比较</label>
  {form.pending&&<p role="status">原请求尚待核对，来源与范围保持锁定；可核对保存结果或按原请求重试。</p>}
  <div className="actions"><Btn primary disabled={busy||!allowed||!draftReady||!!draftError||!form.pending&&!ready} onClick={submit}>{form.pending?'核对原请求或重试':'保存并开始本地分析'}</Btn><Btn disabled={busy} onClick={()=>action(load)}>刷新记录</Btn></div>
  {draftError&&<p className="error" role="alert">{draftError}</p>}{error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {!rows.length&&catalog&&<p>尚无变化分析记录。请先选择两幅影像并确认比较范围。</p>}
  {rows.length>0&&<><label>查看已保存分析<select aria-label="已保存变化分析" value={row.id} onChange={e=>setFocus(e.target.value)}>{rows.map(r=><option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})} · {r.sources.map(s=>s.name).join(' / ')} · 阈值 {r.request.probability_threshold} · {states[r.status]}</option>)}</select></label>
   <article><h4>{states[row.status]}</h4><p>原件：{row.sources.map(s=>s.name).join(' / ')} · 本地模型安装代次 {row.model_trace.generation} · 概率阈值 {row.request.probability_threshold}</p>
    <p>采集时间：{row.sources.map(s=>s.configuration.observed_at||'未知').join(' / ')}；上传时间不替代采集时间。</p>
    {row.error&&<p className="error" role="alert">{row.error}</p>}
    {!row.workflow_task_id&&allowed&&['queued','running','failed','cancelled'].includes(row.status)&&<Btn disabled={busy} onClick={()=>action(()=>json('/geo/pairs/'+row.id+'/'+(['failed','cancelled'].includes(row.status)?'retry':'cancel'),{method:'POST',body:JSON.stringify({version:row.version})}))}>{['failed','cancelled'].includes(row.status)?'按固定来源与模型重试':'取消分析'}</Btn>}
    {row.workflow_task_id&&<p>这是业务任务的本地分析，请在业务步骤中核查、取消或重试。<a href={'/setup/?view=workspace&instance='+encodeURIComponent(row.workflow_instance_id)+'&task='+encodeURIComponent(row.workflow_task_id)}>进入业务核查</a></p>}
    {row.status==='succeeded'&&<><p>候选 {row.result.candidate_pixels} 像素 · 共同有效 {row.result.common_valid_pixels} 像素 · 未知 {row.result.unknown_pixels} 像素{row.result.geometry?' · 候选面积 '+row.result.geometry.area_m2.toFixed(2)+' m²':''}</p>
     <ComparisonEvidence row={row} account={account} Btn={Btn} compact/>
     <div className="actions">{row.sources.map((s,i)=><a key={s.scene_id} href={'/api/geo/scenes/'+s.scene_id+'/original?workspace_id='+encodeURIComponent(account.workspace_id)}>下载{i?'B':'A'}原件</a>)}<Btn disabled={busy} onClick={()=>download(row,'probability_raster')}>下载概率GeoTIFF</Btn><Btn disabled={busy} onClick={()=>download(row,'change_mask')}>下载候选掩膜GeoTIFF</Btn><Btn disabled={busy} onClick={()=>download(row,'export')}>导出处理记录</Btn></div>
     {account.role==='admin'&&<Btn disabled={busy||!ready||row.model_trace.installation_id!==model.id||row.model_trace.generation!==model.generation} onClick={()=>action(async()=>{await json('/geo/pairs/'+row.id+'/register-business',{method:'POST',body:JSON.stringify({version:row.version})});setNotice('本地模型能力已登记。请到“业务与流程”为步骤选择该能力，重新确认流程后生成实例版本。');})}>登记为业务处理能力</Btn>}
     {!row.workflow_task_id&&<Review key={row.id} row={row} account={account} Btn={Btn} onSaved={saved=>setRows(old=>old.map(r=>r.id===saved.id?saved:r))}/>}
    </>}
   </article></>}
 </section>;
}
