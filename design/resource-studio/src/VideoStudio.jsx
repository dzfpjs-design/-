import React,{useState,useEffect,useRef} from 'react';
import {api,json,hashBytes} from './runtime-api';
const states={queued:'等待处理',running:'正在本地处理',succeeded:'视频结果已保存',failed:'处理失败',cancelled:'已取消'};
const labels={person:'人',cat:'猫',dog:'狗',car:'汽车',sofa:'沙发',remote:'遥控器'};
const reviewStates={pending:'待核查',confirmed:'已确认线索',dismissed:'已排除'};

export function EvidenceView({run,frame,workspaceId}){
 const [video,setVideo]=useState(''),[image,setImage]=useState(''),[error,setError]=useState('');
 useEffect(()=>{let disposed=false,url='';setVideo('');setError('');api('/videos/'+run.id+'/original?workspace_id='+encodeURIComponent(workspaceId)).then(r=>r.blob()).then(async b=>{
  if(await hashBytes(await b.arrayBuffer())!==run.input_sha256)throw Error('视频原件摘要不一致，停止回放。');
  if(!disposed){url=URL.createObjectURL(b);setVideo(url);}
 }).catch(e=>{if(!disposed)setError(e.message);});return()=>{disposed=true;if(url)URL.revokeObjectURL(url);};},[run.id,workspaceId]);
 useEffect(()=>{let disposed=false,url='';setImage('');if(frame)api('/evidence/'+frame.evidence_id+'/content').then(r=>r.blob()).then(async b=>{
  if(await hashBytes(await b.arrayBuffer())!==frame.frame_sha256)throw Error('视频帧摘要不一致，停止显示。');
  if(!disposed){url=URL.createObjectURL(b);setImage(url);}
 }).catch(e=>{if(!disposed)setError(e.message);});return()=>{disposed=true;if(url)URL.revokeObjectURL(url);};},[frame?.id]);
 return <div className="video-evidence">{error&&<p className="error" role="alert">{error}</p>}
  {video&&<div><h4>原始视频</h4><video controls preload="metadata" src={video}/><p>{run.source_context||run.gateway_capture?'已核对网关读取的原件；本次只保存画面，未保存音轨。':'原件已核对；原文件含有的音轨会保留，当前处理链只分析画面。'}</p></div>}
  {frame&&image&&<div><h4>第 {frame.seconds.toFixed(1)} 秒的保存画面</h4><svg className="detection-image" role="img" aria-label="保存画面与会话内轨迹" viewBox={`0 0 ${frame.width} ${frame.height}`}><image href={image} width={frame.width} height={frame.height}/>{frame.tracks.map(t=><g key={t.track_ref}><rect x={t.bbox[0]} y={t.bbox[1]} width={t.bbox[2]-t.bbox[0]} height={t.bbox[3]-t.bbox[1]} stroke="#00ffd0" strokeWidth="2" fill="none"/><text x={t.bbox[0]+3} y={Math.max(15,t.bbox[1]+15)} fill="#fff" stroke="#082450" paintOrder="stroke" strokeWidth="3" fontSize="14">{labels[t.label]||t.label} #{t.track_ref} {Math.round(t.score*100)}%</text></g>)}</svg>
  <p>{frame.tracks.length} 条当前轨迹；编号只在本视频会话内有效。{frame.utc?'拍摄时间由用户提供，尚未校准。':'未提供拍摄时间，保留视频内时间，不推测实际日期。'}</p></div>}
 </div>;
}

function AlertReview({alert,run,Btn,onChange,onFrame,canEdit}){
 const saved=alert.review_draft??alert.review?.note??'';
 const [note,setNote]=useState(saved),[busy,setBusy]=useState(false),[error,setError]=useState(''),[formVersion,setFormVersion]=useState(alert.version);
 const baseline=useRef({version:alert.version,note:saved});
 useEffect(()=>{if(alert.version!==baseline.current.version){if(note===baseline.current.note){setNote(saved);setFormVersion(alert.version);}else setError('这条线索已有新的保存记录，当前输入已保留。请核对新记录后再提交。');baseline.current={version:alert.version,note:saved};}},[alert.version]);
 async function decide(decision){setBusy(true);setError('');try{const r=await json(`/videos/${run.id}/alerts/${alert.id}/review`,{method:'POST',body:JSON.stringify({version:formVersion,decision,note})});const savedNote=r.review_draft??r.review?.note??'';baseline.current={version:r.version,note:savedNote};setFormVersion(r.version);setNote(savedNote);await onChange();}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <article className="video-alert"><h4>{labels[alert.label]||alert.label}持续出现线索 · {reviewStates[alert.status]}</h4><p>视频 {alert.from_seconds.toFixed(1)}—{alert.to_seconds.toFixed(1)} 秒 · 轨迹 #{alert.track_ref}。请对照原视频核查；采样间隔内发生的变化可能未被记录。</p><Btn onClick={()=>onFrame(alert.frame_id)}>查看对应画面</Btn>
 <label>核查说明<textarea aria-label="核查说明" value={note} disabled={!canEdit||busy} onChange={e=>setNote(e.target.value)} placeholder="说明画面中的实际情况以及判断依据"/></label>
 <div className="actions"><Btn disabled={!canEdit||busy||!note.trim()} onClick={()=>decide('draft')}>保存核查说明</Btn><Btn disabled={!canEdit||busy||note.trim().length<5} onClick={()=>decide('confirmed')}>确认这条线索</Btn><Btn disabled={!canEdit||busy||note.trim().length<5} onClick={()=>decide('dismissed')}>排除这条线索</Btn></div>
 {alert.task_id&&<p>业务智能体已收到关联对象的核查任务；请在对应业务工作区核对。未自动确认异常或执行设备动作。</p>}{alert.business_review&&<p>业务已保存判断：{({abnormal:'业务异常',not_abnormal:'不是业务异常',uncertain:'尚不能判断'})[alert.business_review.decision]}。{alert.business_review.note}</p>}
 {alert.review_draft&&<p>核查说明草稿已保存，刷新后可继续处理；尚未提交新的判断。</p>}{note!==(alert.review_draft??alert.review?.note??'')&&<p>说明尚未保存，请保存说明或提交判断。</p>}{alert.review&&<p>已保存核查说明：{alert.review.note}。再次判断会保留此前记录。</p>}{error&&<p className="error" role="alert">{error}<Btn onClick={()=>{setNote(saved);setFormVersion(alert.version);baseline.current={version:alert.version,note:saved};setError('');}}>读取已保存说明</Btn></p>}</article>;
}

export default function VideoStudio({installation,account,Btn,Field,focusRun}){
 const [runs,setRuns]=useState([]),[selected,setSelected]=useState(''),[detail,setDetail]=useState(null),[frameId,setFrameId]=useState('');
 const [hz,setHz]=useState(2),[threshold,setThreshold]=useState(.5),[classId,setClassId]=useState('none'),[seconds,setSeconds]=useState(1);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[loading,setLoading]=useState(true);
 const file=useRef(),selectedRef=useRef('');selectedRef.current=selected;
 const canEdit=['admin','operator'].includes(account.role);
 useEffect(()=>{if(focusRun){selectedRef.current=focusRun.id;setSelected(focusRun.id);setDetail(null);setFrameId('');}},[focusRun]);
 async function load(){const list=await json('/videos');setRuns(list);const id=selectedRef.current||list[0]?.id;if(id){const d=await json('/videos/'+id);if(!selectedRef.current||selectedRef.current===id)setDetail(d);if(!selectedRef.current)setSelected(id);}setLoading(false);}
 useEffect(()=>{let stopped=false;const refresh=()=>load().catch(e=>{if(!stopped){setError(e.message);setLoading(false);}});refresh();const timer=setInterval(refresh,2500);return()=>{stopped=true;clearInterval(timer);};},[]);
 useEffect(()=>{if(selected)json('/videos/'+selected).then(d=>{if(selectedRef.current===selected){setDetail(d);setFrameId('');}}).catch(e=>setError(e.message));},[selected]);
 async function action(fn){setBusy(true);setError('');try{await fn();await load();}catch(e){setError(e.message);}finally{setBusy(false);}}
 async function upload(e){const original=e.target.files?.[0];e.target.value='';if(!original)return;await action(async()=>{
  const form=new FormData();form.append('file',original);form.append('installation_id',installation.id);form.append('configuration',JSON.stringify({sample_hz:hz,threshold,presence_rule:classId==='none'?null:{label_id:Number(classId),seconds}}));
  const r=await json('/videos',{method:'POST',body:form});if(r.input_sha256!==await hashBytes(await original.arrayBuffer()))throw Error('上传摘要不一致，请保留原件。');
  const read=await api('/videos/'+r.id+'/original?workspace_id='+encodeURIComponent(account.workspace_id));if(await hashBytes(await read.arrayBuffer())!==r.input_sha256)throw Error('视频读回核验失败，请保留原件。');
  selectedRef.current=r.id;setSelected(r.id);setNotice('原视频与处理设置已保存并读回核验。只在本机处理，可以刷新或离开后继续查看。');
 });}
 const frame=detail?.frames.find(f=>f.id===frameId)||detail?.frames[0];
 return <section className="local-model-card video-studio"><h2>视频信息提取</h2><p>处理上传的 MP4 片段，或核查上方网关读取的原视频与本地提取结果；按确认的条件报告待核查线索。上方可配置间隔读取或单路持续接收；全天运行容量尚未验证。</p><p>当前上限20MB、60秒；每秒保存1或2张分析画面。跳过的画面没有经过识别，不承诺发现全部事件。</p>
 {error&&<p className="error" role="alert">{error}<Btn onClick={()=>action(load)}>重新读取</Btn></p>}{notice&&<p role="status">{notice}</p>}
 {installation?.status==='ready'?<><h3>新视频的处理设置</h3><p>这里的设置用于下一次上传。已保存视频保留当时的采样和报告条件。</p><div className="local-model-fields"><Field label="每秒分析画面数"><select aria-label="每秒分析画面数" value={hz} onChange={e=>setHz(Number(e.target.value))}><option value="1">1张</option><option value="2">2张</option></select></Field><Field label="建立轨迹的最低分数"><input type="number" aria-label="建立轨迹的最低分数" min=".2" max=".8" step=".05" value={threshold} onChange={e=>setThreshold(Number(e.target.value))}/></Field><Field label="向业务智能体报告的条件"><select aria-label="向业务智能体报告的条件" value={classId} onChange={e=>setClassId(e.target.value)}><option value="none">仅提取信息，暂不报告线索</option><option value="0">人持续出现</option><option value="2">汽车持续出现</option><option value="15">猫持续出现</option><option value="16">狗持续出现</option></select></Field>{classId!=='none'&&<Field label="连续出现至少多少秒"><input type="number" aria-label="连续出现至少多少秒" min=".5" max="30" step=".5" value={seconds} onChange={e=>setSeconds(Number(e.target.value))}/></Field>}</div><input ref={file} type="file" hidden accept="video/mp4" onChange={upload}/><Btn primary disabled={busy||!canEdit||!(threshold>=.2&&threshold<=.8)||!(seconds>=.5&&seconds<=30)} onClick={()=>file.current.click()}>上传视频并在本机处理</Btn></>:<p>先完成本地检测模型安装，再处理视频。</p>}
 <h3>视频与处理记录</h3>{loading?<p>正在读取已保存记录…</p>:!runs.length?<p>还没有视频。上传后，原件、设置、逐帧结果和核查决定会保存到服务器。</p>:<><Field label="选择保存的视频"><select aria-label="选择保存的视频" value={selected} onChange={e=>setSelected(e.target.value)}>{runs.map(r=><option key={r.id} value={r.id}>{r.name} · {states[r.status]}{r.retry_of?' · 重试记录':''}</option>)}</select></Field>{detail&&<><h3>{detail.name}</h3><p>{states[detail.status]} · 已保存 {detail.saved_samples} 张画面 · 本地 {detail.backend.toUpperCase()} · 未调用云模型</p>{detail.error&&<p className="error">{detail.error}</p>}
 {['queued','running'].includes(detail.status)&&<Btn disabled={busy||!canEdit} onClick={()=>action(()=>json('/videos/'+detail.id+'/cancel',{method:'POST',body:JSON.stringify({version:detail.version})}))}>停止处理并保留结果</Btn>}
 {['failed','cancelled'].includes(detail.status)&&<Btn disabled={busy||!canEdit||installation?.status!=='ready'} onClick={()=>action(async()=>{const r=await json('/videos/'+detail.id+'/retry',{method:'POST',body:JSON.stringify({version:detail.version})});selectedRef.current=r.id;setSelected(r.id);})}>保留原记录并重新处理</Btn>}
 <p>数据管理智能体已接收 {detail.delivery.data_received} 张画面的候选状态；业务核查队列已接收 {detail.delivery.business_received} 条待核查线索。{detail.delivery.pending>0&&`还有 ${detail.delivery.pending} 条等待送达。`}</p>
 {detail.result&&<p>共解码 {detail.result.decoded_frames} 帧、分析 {detail.result.sample_count} 帧；视频内时长 {detail.result.duration_seconds.toFixed(1)} 秒。{detail.result.gap_intervals.length>0?'存在时间缺口，缺口处已重新建立轨迹。':'本片段未发现时间戳缺口。'}</p>}
 {!!detail.frames.length&&<Field label="查看已保存的分析画面"><select aria-label="查看已保存的分析画面" value={frame?.id||''} onChange={e=>setFrameId(e.target.value)}>{detail.frames.map(f=><option key={f.id} value={f.id}>第 {f.seconds.toFixed(1)} 秒 · {f.tracks.length} 条轨迹</option>)}</select></Field>}
 {detail.source_context&&<p className={detail.source_context.partial?'warning-text':''}>{detail.source_context.partial?'此片段来自不完整的网关读取，可核查已保存画面，不能据此认定全程无异常。':(detail.source_context.domain_binding?`本片段关联场景：${detail.source_context.domain_binding.entity_label}；视觉轨迹未核验为对象身份。`:'此片段来自网关读取；通道名称和轨迹尚未映射为已确认的业务对象。')}来源拍摄时间尚未校准。</p>}<EvidenceView run={detail} frame={frame} workspaceId={account.workspace_id}/><a href={'/api/videos/'+detail.id+'/original?workspace_id='+encodeURIComponent(account.workspace_id)}>下载原始视频</a>
 <h3>业务线索与核查</h3>{!detail.alerts.length?<p>{detail.configuration.presence_rule?'还没有符合已设条件的线索；这不代表没有业务异常。':'此视频只提取信息，未设置线索报告条件。'}</p>:detail.alerts.map(a=><AlertReview key={a.id} alert={a} run={detail} Btn={Btn} onChange={load} onFrame={setFrameId} canEdit={canEdit}/>)}</>}</>}
 </section>;
}
