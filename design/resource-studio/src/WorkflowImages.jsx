import React,{useEffect,useRef,useState} from 'react';
import MaskOriginal from './MaskOriginal';
import {api,json,hashBytes} from './runtime-api';
const labels={cat:'猫',dog:'狗',person:'人',car:'汽车',truck:'卡车',bird:'鸟',sofa:'沙发',remote:'遥控器'};

export function TaskOriginalImage({image,path,detection,Btn}){
 const [url,setURL]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(true),[revision,setRevision]=useState(0);
 useEffect(()=>{let alive=true,objectURL;setLoading(true);setError('');setURL('');api(path).then(async response=>{
  const bytes=await response.arrayBuffer();if(await hashBytes(bytes)!==image.sha256)throw Error('原图摘要不一致，未显示为核查依据。');
  objectURL=URL.createObjectURL(new Blob([bytes],{type:image.media_type}));if(alive){setURL(objectURL);setLoading(false);}else URL.revokeObjectURL(objectURL);
 }).catch(e=>{if(alive){setError(e.message);setLoading(false);}});return()=>{alive=false;if(objectURL)URL.revokeObjectURL(objectURL);};},[path,image.sha256,revision]);
 const matched=detection?.image_sha256===image.sha256&&detection.width===image.width&&detection.height===image.height;
 return <article className="workflow-original"><strong>{image.name}</strong><small>{image.width} × {image.height} · 已保存原件 · 拍摄时间未提供</small>{loading&&<p role="status">正在核对原图…</p>}{error&&<div role="alert"><p className="error">{error}</p><Btn onClick={()=>setRevision(v=>v+1)}>重试读取原图</Btn></div>}
 {url&&<>{matched?<svg className="detection-image" role="img" aria-label={'任务原图与'+detection.detections.length+'个候选目标位置'} viewBox={`0 0 ${image.width} ${image.height}`}><image href={url} width={image.width} height={image.height}/>{detection.detections.map((d,i)=><g key={i}><rect x={d.bbox[0]} y={d.bbox[1]} width={d.bbox[2]-d.bbox[0]} height={d.bbox[3]-d.bbox[1]} fill="none" stroke="#00ffd0" strokeWidth="2"/><text x={d.bbox[0]+3} y={Math.max(15,d.bbox[1]+15)} fill="white" stroke="#082450" strokeWidth="3" paintOrder="stroke" fontSize="14">{labels[d.label]||d.label} {Math.round(d.score*100)}%</text></g>)}</svg>:<img className="detection-image" src={url} alt={'任务原图：'+image.name}/>}<a href={url} download={image.name}>下载已核对的原图</a></>}
 {matched&&<><p>本机 {detection.backend.toUpperCase()} 提取 {detection.detections.length} 个候选目标 · 门槛 {detection.threshold} · 未调用云模型。</p>{detection.detections.length?<ul>{detection.detections.map((d,i)=><li key={i}>{labels[d.label]||d.label} · 模型分数 {Math.round(d.score*100)}% · 原图位置 {d.bbox.map(Math.round).join('，')}</li>)}</ul>:<p>当前门槛下未识别到目标，不能据此判定没有业务异常。</p>}<p>候选类别与框只供核查，不证明对象身份或领域异常。</p></>}
 </article>;
}

export function TaskImagePicker({instanceId,selected,onChange,required,disabled,Btn,onBusy}){
 const [images,setImages]=useState([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');const input=useRef();
 async function load(){setImages(await json('/workspace/instances/'+instanceId+'/input-images'));}
 useEffect(()=>{let alive=true;json('/workspace/instances/'+instanceId+'/input-images').then(rows=>{if(alive)setImages(rows);}).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;};},[instanceId]);
 async function upload(file){if(!file)return;setBusy(true);onBusy?.(true);setError('');setNotice('');try{
  const body=new FormData();body.append('file',file);const row=await json('/workspace/instances/'+instanceId+'/input-images',{method:'POST',body});
  if(row.sha256!==await hashBytes(await file.arrayBuffer()))throw Error('服务器原图摘要不一致；请保留本地图片，核对后重试。');
  const original=await api('/workspace/instances/'+instanceId+'/input-images/'+row.id+'/content');if(await hashBytes(await original.arrayBuffer())!==row.sha256)throw Error('原图读回未通过，请刷新资料列表核对；服务器保存记录仍保留。');
  await load();onChange(required?[row.id]:[...selected,row.id].slice(-3));setNotice('原图已保存并读回核对。未提交任务时不会启动模型处理。');
 }catch(e){setError(e.message+' 已成功上传的原件可刷新列表后重新选择。');}finally{setBusy(false);onBusy?.(false);if(input.current)input.current.value='';}}
 return <section className="workflow-images"><h3>任务图片{required?'（此流程需要一张）':'（可选）'}</h3><p>只选择本实例的原图，配置文档不会作为检测图片。取消选择会保留原件；上传不调用云模型。</p><input ref={input} type="file" accept="image/png,image/jpeg" hidden onChange={e=>upload(e.target.files[0])}/><div className="actions"><Btn disabled={busy||disabled} onClick={()=>input.current.click()}>上传任务图片</Btn><Btn disabled={busy} onClick={()=>load().catch(e=>setError(e.message))}>刷新图片列表</Btn></div>{busy&&<p role="status">正在保存并读回核对原图…</p>}{notice&&<p role="status">{notice}</p>}{error&&<p className="error" role="alert">{error}</p>}{!images.length&&!busy&&<p>还没有保存的任务图片。</p>}
 {images.map(image=><div key={image.id} className="workflow-image-choice"><label><input type="checkbox" aria-label={'任务图片 '+image.name} disabled={busy||disabled||!selected.includes(image.id)&&selected.length>=3} checked={selected.includes(image.id)} onChange={e=>onChange(e.target.checked?(required?[image.id]:[...selected,image.id]):selected.filter(id=>id!==image.id))}/>{image.name} · {image.width} × {image.height}</label>{selected.includes(image.id)&&<TaskOriginalImage image={image} path={'/workspace/instances/'+instanceId+'/input-images/'+image.id+'/content'} Btn={Btn}/>}</div>)}
 {selected.some(id=>!images.some(image=>image.id===id))&&<p className="error">选择的图片暂不在当前列表，请刷新或重新选择；原任务提交内容仍保留。</p>}
 </section>;
}

export function WorkflowTaskImages({task,receipt,Btn}){
 if(!task.image_snapshots?.length)return null;
 return <section className="workflow-images"><h3>本任务固定的原图与候选结果</h3>{task.image_snapshots.map(image=>{const models=[...(task.workflow_runtime.receipts||[])].reverse();const segmentation=models.find(r=>r.candidate_only&&r.output?.segmentation?.image_sha256===image.sha256);if(segmentation)return <MaskOriginal key={image.id} image={image} result={segmentation.output.segmentation} path={'/workspace/tasks/'+task.id+'/images/'+image.id+'/content'} maskPath={id=>'/workspace/tasks/'+task.id+'/masks/'+id+'/content'} Btn={Btn}/>;const model=models.find(r=>r.candidate_only&&r.output?.detection?.image_sha256===image.sha256);return <TaskOriginalImage key={image.id} image={image} detection={model?.output.detection} path={'/workspace/tasks/'+task.id+'/images/'+image.id+'/content'} Btn={Btn}/>;})}</section>;
}
