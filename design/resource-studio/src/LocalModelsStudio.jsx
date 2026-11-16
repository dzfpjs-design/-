import {audioPackID,vadPackID,packRuntimeReady,installBody} from './model-package-policy.mjs';
import React, {useEffect, useRef, useState} from 'react';
import {api, json, hashBytes} from './runtime-api';
import GeoSceneStudio from './GeoSceneStudio';
import MaskOriginal from './MaskOriginal';
import VideoStudio from './VideoStudio';
import MediaGatewayStudio from './MediaGatewayStudio';
import AudioStudio from './AudioStudio';
import ASRComparison from './ASRComparison';
import AudioDailyStudio from './AudioDailyStudio';
import ImageryDailyStudio from './ImageryDailyStudio';
import SpeechActivityStudio from './SpeechActivityStudio';
import PixelPairStudioV249 from './PixelPairStudioV249';
import SoundInputPanel from './SoundInputPanel.jsx';

const soundPackID='yamnet-mediapipe-v1';

const states={queued:'等待处理',installing:'正在安装',ready:'本地推理可用',running:'正在本地识别',succeeded:'识别结果已保存',failed:'处理失败',cancelled:'已取消'};
const stages={ready:'本机检查已通过',requested:'准备安装',runtime_check:'正在准备并核对本地隔离环境',runtime_ready:'隔离环境已准备，继续核对模型',license_check:'核对许可',downloading:'下载固定版本',hash_check:'核对文件摘要',loading:'加载本地模型',model_check:'正在本机执行语音推理检查',smoke_check:'本机推理检查通过'};
const labels={cat:'猫',dog:'狗',person:'人',car:'汽车',truck:'卡车',bird:'鸟',sofa:'沙发',remote:'遥控器'};

function DetectionResult({run,Btn,workspaceId}){
 const [url,setURL]=useState(''),[error,setError]=useState('');
 useEffect(()=>{let disposed=false,objectURL='';api('/evidence/'+run.evidence_id+'/content').then(r=>r.blob()).then(async blob=>{
  if(await hashBytes(await blob.arrayBuffer())!==run.input_sha256)throw Error('原件摘要不一致，已停止显示。');
  if(!disposed){objectURL=URL.createObjectURL(blob);setURL(objectURL);}
 }).catch(e=>{if(!disposed)setError(e.message);});return()=>{disposed=true;if(objectURL)URL.revokeObjectURL(objectURL);};},[run.evidence_id]);
 const result=run.result;
 return <div className="local-model-result">{error&&<p className="error" role="alert">{error}</p>}
  {url&&<svg className="detection-image" role="img" aria-label={'原图与'+result.detections.length+'个候选目标位置'} viewBox={`0 0 ${result.width} ${result.height}`}>
   <image href={url} width={result.width} height={result.height}/>
   {result.detections.map((d,i)=><g key={i}><rect x={d.bbox[0]} y={d.bbox[1]} width={d.bbox[2]-d.bbox[0]} height={d.bbox[3]-d.bbox[1]} fill="none" stroke="#00ffd0" strokeWidth="2"/><text x={d.bbox[0]+3} y={Math.max(15,d.bbox[1]+15)} fill="#fff" stroke="#082450" paintOrder="stroke" strokeWidth="3" fontSize="14">{labels[d.label]||d.label} {Math.round(d.score*100)}%</text></g>)}
  </svg>}
  <p>本地{result.backend==='cpu'?'CPU':'MPS'}推理 · {result.inference_ms} 毫秒（不含排队与模型加载）· {result.detections.length} 个候选框 · 未调用云模型</p>
  {!result.detections.length?<p>当前门槛下没有识别到通用类别目标。这不代表画面安全或没有业务异常。</p>:<ul>{result.detections.map((d,i)=><li key={i}>{labels[d.label]||d.label} · 置信度 {Math.round(d.score*100)}% · 原图位置 {d.bbox.map(v=>Math.round(v)).join('，')}</li>)}</ul>}
  {result.exif_orientation!==1&&<p className="warning-text">位置按文件解码后的原始像素记录，未按EXIF旋转。现场接入需先统一画面方向。</p>}
  {url&&<a className="btn" href={'/api/specialists/runs/'+run.id+'/original?workspace_id='+encodeURIComponent(workspaceId)}>下载原图</a>}
 </div>;
}

export default function LocalModelsStudio({account,workspace,onCommit,onSave,onGo,Btn,Field,resourceCategory='all',onShowModels,onPixelPairGuard,onSoundInputGuard,onAudioSourceGuard,externalScene}){
 const showModels=['all','models'].includes(resourceCategory),showVideo=['all','video'].includes(resourceCategory),showMaps=['all','maps'].includes(resourceCategory);
 const [catalog,setCatalog]=useState(null),[installations,setInstallations]=useState([]),[runs,setRuns]=useState([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[busy,setBusy]=useState(false),[source,setSource]=useState('official'),[backend,setBackend]=useState('cpu'),[threshold,setThreshold]=useState(.5),[notice,setNotice]=useState('');
 const fileInput=useRef(),active=useRef(true),videoSection=useRef();
 const currentWorkspace=useRef(workspace);currentWorkspace.current=workspace;const packPanel=useRef();
 const packKey='ga-specialist-package:'+account.workspace_id+':'+account.username;
 const [focusRun,setFocusRun]=useState(null),[packID,setPackID]=useState(()=>{try{return ['rtdetr-v2-r18','sam2-1-tiny',audioPackID,vadPackID,soundPackID].includes(localStorage.getItem(packKey))?localStorage.getItem(packKey):'rtdetr-v2-r18'}catch{return 'rtdetr-v2-r18'}});
 async function load(initial=false){try{
  const [c,i,r]=await Promise.all([json('/specialists/catalog'),json('/specialists/installations'),json('/specialists/runs')]);
  if(!active.current)return;setCatalog(c);setInstallations(i);setRuns(r);
  if(initial){const old=i.find(r=>r.pack_id===packID);if(old){setSource(old.source);setBackend(old.backend);}}
  setLoading(false);
 }catch(e){if(active.current){setError(e.message);setLoading(false);}}}
 useEffect(()=>{active.current=true;load(true);const timer=setInterval(()=>load(),2000);return()=>{active.current=false;clearInterval(timer);};},[]);
 async function action(fn){setBusy(true);setError('');try{await fn();await load();}catch(e){setError(e.message);}finally{setBusy(false);}}
 const pack=catalog?.packs.find(p=>p.pack_id===packID),detector=installations.find(i=>i.pack_id==='rtdetr-v2-r18'),current=installations.find(i=>i.pack_id===pack?.pack_id),installing=['queued','installing'].includes(current?.status),admin=account.role==='admin';
 const isVAD=packID===vadPackID,isSound=packID===soundPackID,isAudio=[audioPackID,vadPackID].includes(packID),runtimeReady=isSound||packRuntimeReady(packID,catalog);
 function selectPack(id){try{localStorage.setItem(packKey,id)}catch{setError('显示选择未能暂存，本地安装记录仍保存在服务器。')}const row=installations.find(i=>i.pack_id===id);setPackID(id);setSource(row?.source||'official');setBackend(row?.backend||'cpu');setNotice('');}
  function viewPackage(id){selectPack(id);if(showModels)packPanel.current?.scrollIntoView({block:'start'});else onShowModels?.();}
 async function install(){await action(async()=>{await onSave();await json('/specialists/installations',{method:'POST',body:JSON.stringify(installBody(pack.pack_id,isSound?'official':source,isSound?'cpu':backend,current?.version))});setNotice('安装请求已保存，可以离开页面；返回后继续查看进度。');});}
 async function registerSpatial(){await action(async()=>{await onSave();const row=await json('/geo/installations/'+current.id+'/register-spatial',{method:'POST',body:JSON.stringify({version:current.version})});
   const latest=currentWorkspace.current,id='local-spatial-model:'+current.id,connectionID='local-model-gateway:'+current.id;
   const connection={id:connectionID,category:'models',name:'本地专业模型节点',protocol:'本地受管模型',endpoint:'',auth:'无需认证',status:'local_ready',backend:current.backend,installation_id:current.id};
   const resource={id,category:'models',name:'GeoTIFF本地空间区域分割',connection:connectionID,protocol:'本地受管模型',status:'candidate',caps:['空间区域候选','原坐标边界与测地面积'],businesses:[],checks:{installed:true,domain_evaluation:false},details:{capability_id:row.capability_id,installation_id:current.id,runtime_hash:row.runtime_hash,deployment:'local'}};
   if(!onCommit({...latest,connections:[...latest.connections.filter(c=>c.id!==connectionID),connection],resources:[...latest.resources.filter(r=>r.id!==id),resource]}))throw Error('空间能力已登记，但资源关联未保存；请保留原记录并重试保存。');await onSave();setNotice('本地空间候选能力与资源关联已保存，可在业务流程节点选择；领域语义与地面精度仍待核验。');});}
 async function register(){await action(async()=>{await onSave();const row=await json('/specialists/installations/'+current.id+'/register',{method:'POST',body:JSON.stringify({version:current.version})});
  const latest=currentWorkspace.current,id=isSound?'local-sound-model:'+row.capability_id:isVAD?'local-vad-model:'+row.capability_id:isAudio?'local-audio-model:'+row.capability_id:'local-model:'+row.id,connectionID=isSound?'local-sound-node:'+row.id:isVAD?'local-vad-node:'+row.recipe_hash.slice(0,16):isAudio?'local-audio-node:'+row.recipe_hash.slice(0,16):'local-model-gateway:'+row.id;
  const connection={id:connectionID,category:'models',name:'本地专业模型节点',protocol:'本地受管模型',endpoint:'',auth:'无需认证',status:'local_ready',backend:row.backend,installation_id:row.id};
  const resource={id,category:'models',name:pack.title,connection:connectionID,protocol:'本地受管模型',status:'candidate',caps:[isSound?'环境声音窗口候选':isVAD?'语音区间候选':isAudio?'本地音频转写':packID==='sam2-1-tiny'?'实例掩膜候选':'通用目标检测'],businesses:[],checks:{installed:true,domain_evaluation:false},details:{capability_id:row.capability_id,installation_id:row.id,recipe_hash:row.recipe_hash,deployment:'local'}};
  if(!onCommit({...latest,connections:[...latest.connections.filter(c=>c.id!==connectionID),connection],resources:[...latest.resources.filter(r=>r.id!==id),resource]}))throw Error('能力已登记，但资源关联未保存；原记录保留，请重试保存关联。');await onSave();
  setNotice('专业包、执行能力与资源关联已保存。领域效果待评估，业务自动化需在对应流程和范围中明确确认。');});}
 async function upload(event){const file=event.target.files?.[0];event.target.value='';if(!file)return;
  await action(async()=>{await onSave();const form=new FormData();form.append('file',file);const row=await json(`/specialists/installations/${current.id}/samples?threshold=${threshold}`,{method:'POST',body:form});
   if(row.input_sha256!==await hashBytes(await file.arrayBuffer()))throw Error('上传摘要不匹配，请保留原件。');
   const original=await api('/evidence/'+row.evidence_id+'/content');if(await hashBytes(await original.arrayBuffer())!==row.input_sha256)throw Error('原件读回检查失败，保存的任务保留。');
   setNotice('原图与识别请求已保存，仅在本机处理。可以离开或刷新查看结果。');});}
 if(loading)return <main><h1>接入资源与能力</h1><p>正在读取本地模型与已保存的任务…</p></main>;
  return <section className="local-models-studio"><h2>{showModels?'通用与专业模型':showVideo?'摄像头与音视频':'影像与地图'}</h2>
  {showModels&&<p className="info">专业小模型默认在本地运行；已启用的通用推理模型使用公有云。只有明确核对发送范围后才可调用云模型。本地失败不会自动外发原始数据。安装和样例推理通过后，还需评估具体领域效果。</p>}
  {error&&<div className="error" role="alert">{error}<Btn onClick={()=>{setError('');load();}}>重新读取记录</Btn></div>}
  {notice&&<p role="status">{notice}</p>}
  {showModels&&<PixelPairStudioV249 {...{account,Btn,onSave,onGo}} onRegisterGuard={onPixelPairGuard}/>}
  {showModels&&<><Field label="本地专业模型包"><select aria-label="本地专业模型包" value={packID} disabled={busy} onChange={e=>selectPack(e.target.value)}>{catalog?.packs.map(p=><option key={p.pack_id} value={p.pack_id}>{p.title}</option>)}</select></Field>
  {pack&&<section ref={packPanel} className="local-model-card"><h2>{pack.title}</h2><p>{pack.limitations}</p><p>运行位置：本地 · 许可：{isVAD?'MIT；生产许可链待核查':isAudio?'模型卡标Apache-2.0；生产许可链待核查':pack.license} · 领域评测：待完成</p>
   {isSound?<details><summary>查看此固定模型的许可依据</summary><p>此模型文件内嵌 Metadata：{pack.license_evidence?.author} · 版本{pack.license_evidence?.version}。</p><p>{pack.license_evidence?.license}</p><p>此声明对应当前固定模型文件，不代表全部训练素材的权利或领域效果已经核验。</p></details>:<a href={pack.license_evidence} target="_blank" rel="noreferrer">查看模型来源与许可</a>}
   {!runtimeReady&&<p className="error">本地隔离依赖环境尚未安装，请由运维按模型包依赖清单配置。安装按钮保持禁用。</p>}
   {isSound&&<p>环境声音使用独立本地 CPU 环境，安装任务会自动准备并检查依赖；不要求安装 ASR 或 VAD。{catalog?.sound_runtime_installed?'当前隔离环境可读，实际可用仍以安装检查为准。':'当前尚未准备，仍可正常提交本地安装。'}模型安装与原音保存相互独立，分数不是校准的风险概率。</p>}
   <div className="local-model-fields"><Field label="模型下载来源" note="只下载公开固定文件，原音频和图片不会发送到下载站点。来源必须明确选择，不自动切换。"><select value={isSound?'official':source} disabled={busy||installing||current?.status==='ready'||isSound} onChange={e=>setSource(e.target.value)}><option value="official">{isSound?'Google MediaPipe 官方固定版本':isVAD?'PyPI 官方固定发行包':isAudio?'ModelScope 官方固定版本':'Hugging Face 官方'}</option>{!isAudio&&!isSound&&<option value="mirror">国内镜像（核对固定摘要）</option>}</select></Field>
    <Field label="本机推理后端" note="按此包已核验的后端运行，失败时不自动切换后端或转云。"><select value={isSound?'cpu':backend} disabled={busy||installing||current?.status==='ready'||isSound} onChange={e=>setBackend(e.target.value)}><option value="cpu">CPU</option>{!isAudio&&!isSound&&<option value="mps">Apple MPS</option>}</select></Field></div>
   {current&&<div className="local-model-progress" role="status"><strong>{states[current.status]}</strong><p>{isSound&&current.stage==='model_check'?'正在本机执行环境声音窗口推理检查':stages[current.stage]||current.stage}</p>{current.progress?.total>0&&<><progress value={current.progress.downloaded} max={current.progress.total}/><p>{current.progress.file} · {Math.round(current.progress.downloaded/current.progress.total*100)}%</p></>}{current.error&&<p className="error">{current.error}</p>}{current.smoke&&<p>本机 {current.smoke.backend.toUpperCase()} 已完成加载与推理检查。此检查不证明领域准确率。</p>}</div>}
   <div className="actions">{!current||['failed','cancelled'].includes(current.status)?<Btn primary disabled={busy||!admin||!runtimeReady} onClick={install}>{current?'重试本地安装':'安装到本地节点'}</Btn>:installing?<Btn disabled={busy||!admin} onClick={()=>action(()=>json('/specialists/installations/'+current.id+'/cancel',{method:'POST',body:JSON.stringify({version:current.version})}))}>取消安装</Btn>:null}
    {current?.status==='ready'&&<Btn primary disabled={busy||!admin} onClick={register}>{current.capability_id?'保存资源关联':'登记通用候选能力'}</Btn>}{current?.status==='ready'&&packID==='sam2-1-tiny'&&<Btn disabled={busy||!admin} onClick={registerSpatial}>{current.geo_capability_id?'保存空间能力关联':'登记业务影像分割能力'}</Btn>}</div>
   {current?.attempts?.length>0&&<details><summary>此前安装与恢复记录</summary>{current.attempts.map((a,i)=><p key={i}>{a.source==='mirror'?'国内镜像':'官方来源'} · {a.backend.toUpperCase()} · {states[a.status]} · {a.error||'记录保留'}</p>)}</details>}
  </section>}
  {current?.status==='ready'&&!isAudio&&!isSound&&<section className="local-model-card"><h2>用自己的图片核对模型效果</h2><p>先保存原图，再由本机模型识别并保存结果。分数是模型的估计，不是业务结论的可靠度。</p><Field label="显示候选目标的最低分数"><input type="number" min="0.01" max="0.99" step="0.05" value={threshold} onChange={e=>setThreshold(Number(e.target.value))}/></Field><input ref={fileInput} type="file" accept="image/png,image/jpeg" hidden onChange={upload}/><Btn primary disabled={busy||!['admin','operator'].includes(account.role)||!(threshold>=.01&&threshold<=.99)} onClick={()=>fileInput.current.click()}>上传图片并在本机识别</Btn></section>}
  {!isAudio&&!isSound&&<section className="local-model-card"><h2>图片与识别记录</h2>{!runs.filter(r=>(r.pack_id||'rtdetr-v2-r18')===packID).length?<p>还没有样例。完成本地安装后上传图片，原图、版本和结果会保存在当前工作区。</p>:runs.filter(r=>(r.pack_id||'rtdetr-v2-r18')===packID).map(run=><article className="local-sample" key={run.id}><h3>{run.name}</h3><p>{states[run.status]} · 门槛 {run.threshold} · 本地 {run.backend.toUpperCase()}</p>{run.error&&<p className="error">{run.error}</p>}
   {['queued','running'].includes(run.status)&&<Btn disabled={busy} onClick={()=>action(()=>json('/specialists/runs/'+run.id+'/cancel',{method:'POST',body:JSON.stringify({version:run.version})}))}>取消识别</Btn>}
   {['failed','cancelled'].includes(run.status)&&<Btn disabled={busy||current?.status!=='ready'} onClick={()=>action(()=>json('/specialists/runs/'+run.id+'/retry',{method:'POST',body:JSON.stringify({version:run.version})}))}>保留原记录并重试</Btn>}
   {run.status==='succeeded'&&(run.result.schema==='local-segmentation-v1'?<MaskOriginal image={{name:run.name,sha256:run.input_sha256,media_type:run.media_type||'image/png',width:run.result.width,height:run.result.height}} path={'/evidence/'+run.evidence_id+'/content'} result={run.result} maskPath={id=>'/specialists/runs/'+run.id+'/masks/'+id} Btn={Btn}/>:<DetectionResult {...{run,Btn}} workspaceId={account.workspace_id}/>)}</article>)}</section>}
  {isAudio&&<ASRComparison key={account.workspace_id} {...{account,Btn}}/>}</>}
  {showModels&&isSound&&<SoundInputPanel {...{account,Btn,Field}} onRegisterGuard={onSoundInputGuard}/>}
  {showVideo&&<><MediaGatewayStudio {...{installation:detector,account,workspace,onCommit,onSave,onAudioSourceGuard,Btn,Field}} onOpenSoundModels={()=>viewPackage(soundPackID)} onViewVideo={id=>{setFocusRun({id});videoSection.current?.scrollIntoView({behavior:'smooth',block:'start'});}}/>
  <SoundInputPanel {...{account,Btn,Field}} onRegisterGuard={onSoundInputGuard}/>
  <AudioStudio account={account} workspace={workspace} onSave={onSave} onCommit={onCommit} installation={installations.find(i=>i.pack_id===audioPackID)} onSelectPackage={()=>viewPackage(audioPackID)} Btn={Btn} Field={Field}/>
  <SpeechActivityStudio {...{workspace,onCommit,onSave}} asrInstallation={installations.find(i=>i.pack_id===audioPackID)} onSelectASR={()=>viewPackage(audioPackID)} account={account} installation={installations.find(i=>i.pack_id===vadPackID)} onSelectPackage={()=>viewPackage(vadPackID)} Btn={Btn} Field={Field}/>
  <AudioDailyStudio {...{account,Btn}}/>
  <div ref={videoSection}><VideoStudio installation={detector} account={account} Btn={Btn} Field={Field} focusRun={focusRun}/></div></>}
  {showMaps&&<><GeoSceneStudio account={account} Btn={Btn} Field={Field} workspace={workspace} onSave={onSave} onCommit={onCommit} externalScene={externalScene}/>
  <ImageryDailyStudio {...{account,Btn}}/><PixelPairStudioV249 {...{account,Btn,onSave,onGo}} onRegisterGuard={onPixelPairGuard}/></>}
 </section>;
}
