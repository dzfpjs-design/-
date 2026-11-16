import SegmentedAudioStudio from './SegmentedAudioStudio';
import React,{useEffect,useRef,useState} from 'react';
import {api,json,hashBytes} from './runtime-api';
import {resolveAssociation} from './geo-recovery.mjs';
import {audioUploadReady} from './model-package-policy.mjs';
import {vadUploadKey,vadReviewKey,vadRequestValue,confirmVAD,reviewValue,sameReview} from './speech-activity-form.mjs';
const labels={queued:'等待本机检测',running:'正在本机检测语音区间',succeeded:'候选区间已保存，待听音核对',failed:'本机处理失败',cancelled:'处理已取消，原件保留'};
function ActivityRecord({row,account,Btn,Field,onSaved,asrInstallation,onSelectASR,workspace,onCommit,onSave}){
 const player=useRef(),stopAt=useRef(null),key=vadReviewKey(account.workspace_id,account.username,row.id);
 const[url,setURL]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[value,setValue]=useState(()=>{
  if(!row.result)return null;
  try{return reviewValue(row,JSON.parse(localStorage.getItem(key)||'null'))}catch{return null}
 });
 const canWrite=['admin','operator'].includes(account.role),[showSegments,setShowSegments]=useState(false);
 useEffect(()=>{let disposed=false,objectURL='';api('/audio-activity/'+row.id+'/original').then(r=>r.blob()).then(async b=>{if(await hashBytes(await b.arrayBuffer())!==row.input_sha256)throw Error('原音频摘要不一致，已停止播放。');if(!disposed){objectURL=URL.createObjectURL(b);setURL(objectURL)}}).catch(e=>{if(!disposed)setError(e.message)});return()=>{disposed=true;if(objectURL)URL.revokeObjectURL(objectURL)}},[row.id,row.input_sha256]);
 useEffect(()=>{if(row.result&&value===null){try{setValue(reviewValue(row,JSON.parse(localStorage.getItem(key)||'null')))}catch(e){setError(e.message)}}},[row.result]);
 const change=next=>{setValue(next);try{localStorage.setItem(key,JSON.stringify(next))}catch{setError('本机草稿未能暂存，已输入内容保留。请先保存草稿到服务器。')}};
 const save=async confirmed=>{setBusy(true);setError('');try{
  const body={version:row.version,...value,...(confirmed?{confirmed:true}:{})};const saved=await json('/audio-activity/'+row.id+(confirmed?'/review':'/review-draft'),{method:'PUT',body:JSON.stringify(body)});
  if(!sameReview(value,confirmed?saved.review:saved.review_draft))throw Error('保存回执与当前核查内容不一致，请保留草稿。');
  localStorage.removeItem(key);onSaved(saved);
 }catch(e){setError(e.message+' 输入草稿保留；可重新读取服务器记录核对保存结果。')}finally{setBusy(false)}};
 const play=async interval=>{try{stopAt.current=interval.end_seconds;player.current.currentTime=interval.start_seconds;await player.current.play()}catch{setError('原音频暂不能播放，请用原件下载核对后重试。')}};
 return <article className="local-sample speech-activity-record"><h3>{row.name}</h3><p>{row.review?'人工核查已保存':labels[row.status]} · 本地 CPU · {Math.ceil(row.size_bytes/1024)} KB</p>{row.error&&<p className="error" role="alert">{row.error}</p>}{error&&<p className="error" role="alert">{error}</p>}
 {url&&<><audio ref={player} controls src={url} aria-label={'语音区间原音频 '+row.name} preload="metadata" onTimeUpdate={()=>{if(stopAt.current!==null&&player.current.currentTime>=stopAt.current){player.current.pause();stopAt.current=null}}} onSeeked={()=>{if(stopAt.current!==null&&(player.current.currentTime<0||player.current.currentTime>stopAt.current)){stopAt.current=null}}}/><a href={url} download={row.name} onClick={()=>{stopAt.current=null}}>下载完整原音频</a></>}
 {row.result&&<><p>原音频 {row.result.audio.duration_seconds.toFixed(2)} 秒 · {row.result.audio.channels} 通道 · 分析步长 32 毫秒 · 检测门槛 {row.configuration.threshold}</p><p>以下时间是语音候选区间，不是词级时间戳，也没有识别说话人。录制时间未知；原音频中的环境声音完整保留。</p>
 {!row.result.activity.intervals.length&&<p>本次未检测到满足门槛和长度要求的语音。请听完整原件；这不表示没有环境声音或业务异常。</p>}
 {row.result.activity.intervals.map((interval,i)=><div className="speech-interval" key={i}><strong>区间 {i+1}：{interval.start_seconds.toFixed(3)}—{interval.end_seconds.toFixed(3)} 秒</strong><Btn disabled={!url||busy} onClick={()=>play(interval)}>播放区间 {i+1}</Btn>{value&&<label>听音判断<select aria-label={'区间 '+(i+1)+' 听音判断'} value={value.decisions[i]} disabled={busy||!canWrite} onChange={e=>change({...value,decisions:value.decisions.map((d,j)=>j===i?e.target.value:d)})}><option value="uncertain">尚不能确认</option><option value="speech">有语音</option><option value="not_speech">不是语音</option></select></label>}</div>)}
 {value&&<><label>核查说明<textarea aria-label={'语音区间核查说明 '+row.name} rows={3} maxLength={5000} disabled={busy||!canWrite} value={value.note} onChange={e=>change({...value,note:e.target.value})}/></label><p role="status">{sameReview(value,row.review_draft)?'草稿已保存到服务器，刷新和重启后可恢复。':sameReview(value,row.review)?'人工核查已保存到服务器。':'当前核查尚未保存到服务器；请保存草稿或确认核查。'}</p><div className="actions"><Btn disabled={busy||!canWrite||sameReview(value,row.review_draft)||sameReview(value,row.review)} onClick={()=>save(false)}>保存核查草稿</Btn><Btn primary disabled={busy||!canWrite||sameReview(value,row.review)} onClick={()=>save(true)}>确认并保存核查</Btn></div></>}
 <Btn onClick={()=>setShowSegments(!showSegments)}>{showSegments?'收起分段文字':'按语音区间生成文字'}</Btn>
 {showSegments&&<SegmentedAudioStudio sourceRow={row} {...{account,asrInstallation,onSelectASR,Btn,Field,workspace,onCommit,onSave}} onPlay={play} audioReady={!!url}/> }
 <small>语音区间核查与分段文字分别保存，人工结果不自动更新对象状态或发布经验；未执行环境声音分类或告警判断。</small></>}
 </article>;
}
export default function SpeechActivityStudio({account,installation,onSelectPackage,Btn,Field,asrInstallation,onSelectASR,workspace,onCommit,onSave}){
 const key=vadUploadKey(account.workspace_id,account.username),fileRef=useRef();const[catalog,setCatalog]=useState(null),[rows,setRows]=useState([]),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const[pending,setPending]=useState(()=>{try{return JSON.parse(localStorage.getItem(key)||'null')}catch{return {invalid:true}}});const[configuration,setConfiguration]=useState(()=>pending?.configuration||{channel:'mono',threshold:.5});
 const allowed=['admin','operator'].includes(account.role);const load=async()=>{const[c,r]=await Promise.all([json('/audio-activity/catalog'),json('/audio-activity')]);setCatalog(c);setRows(r)};
 useEffect(()=>{load().catch(e=>setError(e.message))},[account.workspace_id,account.username,installation?.status==='ready'?installation.version:null]);
 const working=rows.some(r=>['queued','running'].includes(r.status));useEffect(()=>{if(!working)return;const timer=setInterval(()=>json('/audio-activity').then(setRows).catch(e=>setError(e.message)),2500);return()=>clearInterval(timer)},[working]);
 const saved=row=>setRows(prev=>[row,...prev.filter(r=>r.id!==row.id)]);
 const confirm=(row,intent)=>{confirmVAD(row,intent);localStorage.removeItem(key);setPending(null);saved(row);setNotice('原音频和语音区间请求已保存，后台仅在本机处理，可以离开或刷新查看结果。')};
 const upload=async file=>{if(!file)return;setBusy(true);setError('');try{
  if(file.size===0||file.size>20*1024*1024)throw Error('请选择非空且不超过20MB的音频。');
  const sha=await hashBytes(await file.arrayBuffer()),requestHash=await hashBytes(new TextEncoder().encode(vadRequestValue(sha,configuration)));let intent=pending;
  if(intent&&(intent.invalid||intent.input_sha256!==sha||intent.request_hash!==requestHash||intent.size!==file.size))throw Error('请选择原音频，并保持原通道和检测设置；原请求仍待核对。');
  if(!intent){intent={request_id:crypto.randomUUID(),input_sha256:sha,request_hash:requestHash,size:file.size,configuration:{...configuration}};localStorage.setItem(key,JSON.stringify(intent));setPending(intent)}
  const query=()=>json('/audio-activity/requests/'+intent.request_id+'?expected_hash='+intent.request_hash);
  const send=()=>{const form=new FormData();form.append('file',file);form.append('request_id',intent.request_id);form.append('configuration',JSON.stringify(intent.configuration));return json('/audio-activity',{method:'POST',body:form})};
  confirm(await resolveAssociation(intent,query,send),intent);
 }catch(e){setError(e.message+' 原请求保留，连接恢复后可核对保存结果或选择原文件重试。')}finally{setBusy(false);if(fileRef.current)fileRef.current.value=''}};
 const recover=async()=>{setBusy(true);setError('');try{confirm(await json('/audio-activity/requests/'+pending.request_id+'?expected_hash='+pending.request_hash),pending)}catch(e){setError(e.status===404?'尚未查到保存请求，请选择原音频重试。':e.message)}finally{setBusy(false)}};
 const act=async operation=>{setBusy(true);setError('');try{saved(await operation())}catch(e){setError(e.message)}finally{setBusy(false)}};
 const download=async row=>{setError('');try{const data=await json('/audio-activity/'+row.id+'/export');if(data.record.id!==row.id||data.record.input_sha256!==row.input_sha256)throw Error('导出与原记录不一致');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=row.name+'-语音区间核查.json';a.click();URL.revokeObjectURL(url)}catch(e){setError(e.message)}};
 return <section className="local-model-studio"><h2>语音区间检测与听音核查</h2><p>Silero VAD 默认在本机 CPU 运行。找出可能有语音的位置，保留完整原件；转写文字请使用上方的语音转写入口。</p><small>WAV、MP3、FLAC、OGG、M4A，单条音轨，不超过20MB和60秒。模型分数未经领域校准；不用于自动告警或排除环境声音。</small>{error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
 {catalog&&!catalog.workspace_prepared&&<p>请先安装并验证本地语音区间模型包。<Btn onClick={onSelectPackage}>选择本地语音区间模型包</Btn></p>}{catalog&&!catalog.node_prepared&&<p className="error">{catalog.error}。不会自动转云。</p>}
 <Field label="分析哪个音频通道"><select aria-label="语音区间分析通道" value={configuration.channel} disabled={busy||!!pending} onChange={e=>setConfiguration({...configuration,channel:e.target.value})}><option value="mono">单通道音频（多通道时停止并提示）</option><option value="mix">我确认合并所有通道</option>{Array.from({length:8},(_,i)=><option key={i} value={String(i+1)}>第{i+1}通道</option>)}</select></Field>
 <details><summary>调整语音检测门槛</summary><Field label="语音检测门槛" note="默认0.5。提高门槛可能漏掉轻声，降低门槛可能增加误报；需要听原音频核对。"><input aria-label="语音检测门槛" type="number" min="0.1" max="0.9" step="0.05" value={configuration.threshold} disabled={busy||!!pending} onChange={e=>setConfiguration({...configuration,threshold:Number(e.target.value)})}/></Field></details>
 <input type="file" ref={fileRef} hidden accept=".wav,.mp3,.flac,.ogg,.m4a" onChange={e=>upload(e.target.files[0])}/><div className="actions"><Btn primary disabled={busy||!allowed||!audioUploadReady(catalog)||pending?.invalid||!Number.isFinite(configuration.threshold)||configuration.threshold<.1||configuration.threshold>.9} onClick={()=>fileRef.current.click()}>{pending?'重新选择原音频重试语音检测':'上传音频并检测语音区间'}</Btn><Btn disabled={busy} onClick={()=>load().catch(e=>setError(e.message))}>刷新语音区间记录</Btn>{pending&&!pending.invalid&&<Btn disabled={busy} onClick={recover}>核对语音区间请求保存结果</Btn>}</div>
 {pending&&<p role="status">原音频请求和通道设置已暂存，保存结果待核对；请保留原文件。</p>}{!rows.length&&<p>还没有语音区间记录。原件、模型版本、候选区间和核查结果会保存在当前工作区。</p>}
 {rows.map(row=><div key={row.id}><ActivityRecord {...{row,account,Btn,Field,onSaved:saved,asrInstallation,onSelectASR,workspace,onCommit,onSave}}/><div className="actions">{['queued','running'].includes(row.status)&&<Btn disabled={busy||!allowed} onClick={()=>act(()=>json('/audio-activity/'+row.id+'/cancel',{method:'POST',body:JSON.stringify({version:row.version})}))}>取消语音检测，保留原件</Btn>}{['failed','cancelled'].includes(row.status)&&<Btn disabled={busy||!allowed} onClick={()=>act(()=>json('/audio-activity/'+row.id+'/retry',{method:'POST',body:JSON.stringify({version:row.version})}))}>以原音频重试语音检测</Btn>}{row.status==='succeeded'&&<Btn disabled={busy} onClick={()=>download(row)}>导出语音区间与核查记录</Btn>}</div></div>)}
 </section>;
}
