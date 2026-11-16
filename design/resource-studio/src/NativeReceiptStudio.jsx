import React,{useEffect,useRef,useState} from 'react';
import {perceptionObjectState,usablePerceptionObjects,assertPerceptionObjects} from './perception-entity-contract.mjs';
import {perceptionObjectOptions,perceptionSelectionNote} from './perception-object-fields';
import {json} from './runtime-api';
import NativeInformationValue from './NativeInformationValue';
import NativeVideoReviewV165 from './NativeVideoReviewV165';
import NativePixelReceiptV250 from './NativePixelReceiptV250.jsx';
import NativeSoundReceipt from './NativeSoundReceipt.jsx';
import {verifyGraphWire} from './full-semantic-graph-wire.mjs';
import {checkNativeAudioStagesV136} from './native-graph-sources-v136.mjs';
import {checkLearnedGeoStagesV141} from './native-learned-graph-sources-v141.mjs';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import NativeCandidateQueryV139 from './NativeCandidateQueryV139';
import {assertCandidateQueryDraftSavedV140} from './native-candidate-query-v139.mjs';
const names={imagery:'影像',audio:'音频',video:'视频'};
const videoReviewEntryV165=record=>record.native_video_review_available===true||(record.record?.modality==='video'&&record.data_reception?.schema==='instance-native-video-data-reception-v1'&&!record.record?.task_id);
const learnedGraphRecord=record=>record.graph_message_support?.schema==='native-learned-geo-graph-receipt-support-v141'||record.data_reception?.schema==='instance-native-learned-geo-pair-data-reception-v1';
export default function NativeReceiptStudio({account,instanceId,objects,releaseID,Btn,onOpenTask,onOpenSources,onBusy,currentAvailable=true}){
 const [selectedObject,setSelectedObject]=useState('');
 const [savedScope,setSavedScope]=useState(null),scope=JSON.stringify([account.user_id,account.workspace_id,account.role,instanceId,releaseID,selectedObject]),currentScope=useRef(scope);currentScope.current=scope;
 const syncEligible=selectedObject?perceptionObjectState(objects,selectedObject,releaseID).usable:usablePerceptionObjects(objects,releaseID).length>0;
 const [identityScope,setIdentityScope]=useState(null),reads=useRef(0);
 const [stored,setSaved]=useState(null),saved=savedScope===scope&&identityScope===scope?stored:null,[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [stages,setStages]=useState({scope:null,values:{}});
 const dataRecoveries=useRef(new Map()),dataSending=useRef(null),dataCallback=useRef(onBusy);dataCallback.current=onBusy;
 const [,redrawDataRecoveries]=useState(0),dataScope=JSON.stringify([account.user_id,account.workspace_id,instanceId]);
 const recoveryKey=record=>dataScope+':'+record.id,currentReady=currentAvailable&&!!releaseID;
 const visibleDataRecoveries=identityScope===scope?[...dataRecoveries.current.values()].filter(value=>value.owner===dataScope):[];
 useEffect(()=>{const leave=event=>{if(dataRecoveries.current.size){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',leave);return()=>{window.removeEventListener('beforeunload',leave);dataCallback.current?.(false);};},[]);
 function dataProtection(){dataCallback.current?.(dataRecoveries.current.size>0);redrawDataRecoveries(value=>value+1);}
 const base='/workspace/instances/'+instanceId+'/native-receipts',listURL=base+(selectedObject?'?entity_ids='+encodeURIComponent(selectedObject):'');
 async function scopedJSON(path,options={}){
  const original=scope,epoch=reads.current,request=scopedPreparationRequester(account,json),check=me=>{if(currentScope.current!==original||reads.current!==epoch||me.user_id!==account.user_id||me.workspace_id!==account.workspace_id||me.role!==account.role)throw Error('当前身份或实例范围已变化，原记录保留，未展示另一范围的资料。');};
  try{check(await request('/me'));const value=await request(path,options);check(await request('/me'));setIdentityScope(original);return value;}
  catch(error){if(currentScope.current===original&&reads.current===epoch)setIdentityScope(null);throw error;}
 }
 async function load(){const original=scope,epoch=reads.current,value=await scopedJSON(listURL);if(currentScope.current===original&&reads.current===epoch){setSaved(value);setSavedScope(original);}return value;}
 useEffect(()=>{
  let live=true;reads.current++;setIdentityScope(null);setSavedScope(null);
  const refresh=()=>{const epoch=reads.current;load().catch(async error=>{
   if(live&&currentScope.current===scope&&reads.current===epoch){
    setError(error.message);
    // A failed directory is not proof that the original observation is lost.
    // Verify actual identity independently before exposing its read-only retry
    // reconciliation; never make the old directory a current successful read.
    if([...dataRecoveries.current.values()].some(value=>value.owner===dataScope))try{await scopedJSON('/me');}catch{}
   }
  });};
  const wake=()=>{reads.current++;setIdentityScope(null);if(document.visibilityState!=='hidden')refresh();};
  refresh();window.addEventListener('focus',wake);document.addEventListener('visibilitychange',wake);
  return()=>{live=false;reads.current++;window.removeEventListener('focus',wake);document.removeEventListener('visibilitychange',wake);};
 },[listURL,scope]);
 async function act(fn){const original=scope,epoch=reads.current;setBusy(true);setError('');try{await fn();}catch(e){if(currentScope.current===original&&reads.current===epoch)setError(e.message);}finally{setBusy(false);}}
 function changeObject(identity){try{if(dataRecoveries.current.size)throw Error('原结果的数据接收仍待核对，请先读取原结果，再切换对象。');assertCandidateQueryDraftSavedV140(account,instanceId);setError('');setSaved(null);setSelectedObject(identity);}catch(error){setError(error.message);}}
 async function sync(){if(!currentReady)throw Error('当前启用版本尚未核实，暂不能新增归集；原结果仍可核对。');if(selectedObject)assertPerceptionObjects(objects,[selectedObject],releaseID);else if(!syncEligible)throw Error('当前没有已核实可用的对象，历史记录保留');if(savedScope!==scope||!saved)throw Error('归集预览的对象或实例版本已变化，请重新读取；历史记录保留');const result=await scopedJSON(base+'/sync',{method:'POST',body:JSON.stringify({preview_hash:saved.preview_hash,entity_ids:selectedObject?[selectedObject]:null})});setNotice(`已保存${result.created}条归集记录，${result.already_saved}条原记录保留。未重复运行模型。`);await load();}
 async function exportRow(id){const value=await scopedJSON(base+'/'+id+'/export'),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='多模处理观测与依据.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 async function readStages(record){
  const original=scope,epoch=reads.current,value=await verifyGraphWire(await scopedJSON('/workspace/instances/'+instanceId+'/graph-event-native-producers/observations/'+record.id+'/stages'));checkNativeAudioStagesV136(value,record);
  if(currentScope.current===original&&reads.current===epoch)setStages(previous=>({scope:original,values:{...(previous.scope===original?previous.values:{}),[record.id]:value}}));
 }
 async function readLearnedStages(record){
  const key=recoveryKey(record),recovery=dataRecoveries.current.get(key),basis=recovery?.owner===dataScope?recovery.record:record;
  if(!learnedGraphRecord(basis))throw Error('此原记录不是可核对的学习型变化记录，原结果保留。');
  const original=scope,epoch=reads.current,value=await verifyGraphWire(await scopedJSON('/workspace/instances/'+instanceId+'/graph-event-native-learned-geo-producers/observations/'+basis.id+'/stages'));checkLearnedGeoStagesV141(value,basis);
  if(currentScope.current===original&&reads.current===epoch){setStages(previous=>({scope:original,values:{...(previous.scope===original?previous.values:{}),[basis.id]:value}}));dataRecoveries.current.delete(key);dataProtection();if(recovery)setNotice(value.data_message+(value.data_received?' 没有重发原操作或重新推理。':' 本次只读取原结果，没有自动重试。'));}
 }
 async function retryLearnedData(record,stage){
  if(dataSending.current)throw Error('原结果的数据接收正在核对，暂不能重复发送。');
  if(!currentReady||!['admin','operator'].includes(account.role))throw Error('当前启用版本或操作资格尚未核实，暂不能重试接收；仍可只读核对原结果。');
  if(!learnedGraphRecord(record)||stage?.schema!=='native-learned-geo-graph-receipt-stages-v141'||stage.observation_id!==record.id||stage.data_status!=='blocked')throw Error('请先读取原结果的接收阶段；只有接收受阻时可以明确重试。');
  const original=scope,epoch=reads.current,key=recoveryKey(record),ticket={};
  if(dataRecoveries.current.has(key))throw Error('上次接收结果仍未确认，请先只核对原结果。');
  // Freeze only the original identifiers needed to verify a historical GET.
  // Current release/selection and current write eligibility remain separate.
  const originalRecord=Object.freeze({id:record.id,entity_id:record.entity_id,record:Object.freeze({task_id:record.record.task_id}),graph_message_support:record.graph_message_support,data_reception:record.data_reception});
  dataSending.current=ticket;dataRecoveries.current.set(key,{owner:dataScope,record:originalRecord});dataProtection();
  setStages(previous=>{const values={...(previous.scope===original?previous.values:{})};delete values[record.id];return {scope:original,values};});
  try{
   const value=await verifyGraphWire(await scopedJSON('/workspace/instances/'+instanceId+'/graph-event-native-learned-geo-producers/observations/'+record.id+'/retry-data-delivery',{method:'POST',body:JSON.stringify({expected_version:stage.observation_version,reason:'恢复原件和权限后，按原结果重试数据接收'})}));checkLearnedGeoStagesV141(value,record);
   if(currentScope.current!==original||reads.current!==epoch)throw Error('当前身份或对象范围已变化，原结果保留，请回原范围只核对接收状态。');
   await readLearnedStages(record);
  }catch(error){
   // A transport error conceals scopedJSON's private list. Restore its display
   // only after another actual identity read, while retaining the unknown
   // operation and keeping the old stage hidden. No delivery is resubmitted.
   if(currentScope.current===original&&reads.current===epoch)try{await scopedJSON('/me');}catch{}
   throw Error('本次数据接收结果尚未确认。请只核对这份原结果；不会自动重试，也不会重新运行模型。');
  }
  finally{if(dataSending.current===ticket)dataSending.current=null;}
 }
 async function learnedAction(callback){if(dataSending.current)return;await act(callback);}
 return <section className="media-form"><h3>归集已完成的影像、音频和视频处理</h3><p>已确认业务的本地能力完成后，后台自动保存统一观测。已有处理可在这里核对并归集，不重复推理。有业务任务的核查仍在原任务中完成；独立视频候选可在本页查看依据并保存意见。</p>
 {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
 <label>归集哪些对象<select aria-label="多模归集对象范围" disabled={busy} value={selectedObject} onChange={e=>changeObject(e.target.value)}><option value="">当前实例可用对象（历史记录仍保留）</option>{perceptionObjectOptions(objects,selectedObject,releaseID)}</select></label>{perceptionSelectionNote(objects,selectedObject,releaseID)}
 <NativeCandidateQueryV139 {...{account,instanceId,releaseID,selectedObject,Btn,onOpenTask}} objects={currentReady?objects:[]}/>
 <div className="actions"><Btn disabled={busy} onClick={()=>act(load)}>读取保存的多模处理</Btn><Btn primary disabled={busy||!currentReady||!syncEligible||savedScope!==scope||!saved?.available||saved?.truncated||!['admin','operator'].includes(account.role)} onClick={()=>act(sync)}>归集已完成处理</Btn>{onOpenSources&&<Btn disabled={busy} onClick={onOpenSources}>登记本实例的本地语音结果来源</Btn>}</div><p>归集保存、数据接收、消息接纳和等待匹配是不同阶段；这里只按实际回执分别核对，不自动把保存结果当作任务完成。</p>
 {!currentReady&&<p role="status">当前启用版本尚未核实，新归集和接收重试暂不可用；原结果、原待核操作及只读核对仍保留。</p>}
 {visibleDataRecoveries.map(recovery=><aside role="status" key={recovery.record.id}><p>原结果的数据接收仍待核对。旧阶段已收起；这里只读取同一结果，不会自动重试或重新推理。</p><Btn disabled={busy||!!dataSending.current} onClick={()=>learnedAction(()=>readLearnedStages(recovery.record))}>只核对这份结果的数据接收</Btn></aside>)}
 {!saved?<p>正在读取原处理和归集记录…</p>:<>{saved.current_issue&&<p role="status">{saved.current_issue}</p>}<p>{saved.available}条处理与对象关联可归集；{saved.not_ready}条尚未完成或需恢复原件。数量不是独立观测次数。</p>{saved.truncated&&<p className="error">达到扫描上限，不能以部分结果归集全部资料，请按对象分批。</p>}
 <details><summary>查看原处理清单</summary>{saved.candidates.length?saved.candidates.map((r,i)=><p key={i}>{names[r.modality]} · {r.name} · {r.entity_ids.map(id=>objects.find(o=>o.id===id)?.name||'原任务登记对象').join('、')} · {r.status==='succeeded'&&r.verified?'已完成，可核对归集':'请查看原处理状态或恢复依据'}{r.task_id&&<Btn disabled={busy} onClick={()=>act(()=>onOpenTask(r.task_id))}>查看原处理依据</Btn>}</p>):<p>所选范围尚无关联的处理记录。</p>}</details>
 {!saved.records.length?<p>尚无统一归集记录；已有原处理及人工说明仍保留。</p>:saved.records.map(r=>{if(Object.hasOwn(r,'native_sound_result_v1')||r.record?.schema==='native-sound-receipt-record-v1')return <NativeSoundReceipt key={r.id} {...{account,instanceId,Btn,onOpenTask}} record={r}/>;if(Object.hasOwn(r,'native_pixel_result_v250')||r.record?.schema==='native-pixel-receipt-record-v250')return <NativePixelReceiptV250 key={r.id} {...{account,instanceId,Btn,onOpenTask}} record={r}/>;const stage=stages.scope===scope?stages.values[r.id]:null;return <article className="media-form" key={r.id}>{r.integrity_error?<p role="alert">{r.integrity_error}</p>:<><h4>{names[r.record.modality]} · {r.record.name}</h4><p>原来源保存日期：{r.received_day} · 归集：已保存 · 数据管理：{(stage?.schema==='native-learned-geo-graph-receipt-stages-v141'?stage.data_received:r.data_received)?'已接收':'等待交付'} · 人工核查：{videoReviewEntryV165(r)?'请在下方查看视频并填写独立核查意见':r.record.review_state==='confirmed'?'原任务已保存核查':'请查看原任务'}。</p><p>采集时间和对象覆盖尚未核实，候选不自动更新现场状态。</p>{r.graph_message_support&&<p>{r.graph_message_support.message}</p>}{r.data_reception?.declaration_status==='not_declared'&&<p>{videoReviewEntryV165(r)?'已核对本地处理和接收记录；本页意见不会自动完成业务任务。':'已核对专用数据接收回执；原业务数据计划未声明此归集包，未冒称已声明。'}</p>}
 {stage&&r.record.modality==='audio'&&<aside aria-label="语音结果送达阶段"><h5>这份语音结果的送达情况</h5><p>{stage.data_received?'数据已接收':'尚未核实数据接收'} · {stage.data_message}</p>{!stage.deliveries.length?<p>尚无可核对的接纳记录；旧记录不会补发为新消息。</p>:stage.deliveries.map(delivery=><p key={delivery.intent_id}>{delivery.source_label}：{delivery.status==='admitted'?'已接纳':'尚未接纳'}。已接纳仍不代表等待匹配。</p>)}<p>{stage.waiting_message} 请打开对应的等待任务核对实际窗口和人工待办。</p></aside>}
 {stage?.schema==='native-learned-geo-graph-receipt-stages-v141'&&<aside aria-label="变化候选送达阶段"><h5>这份变化候选的送达情况</h5><p>{stage.data_received?'数据已接收':'尚未核实数据接收'} · {stage.data_message}</p>{stage.data_status==='blocked'&&<><p>恢复原件和权限后，重新核对同一结果；不重跑模型。</p><Btn disabled={busy||!!dataSending.current||!currentReady||!['admin','operator'].includes(account.role)} onClick={()=>learnedAction(()=>retryLearnedData(r,stage))}>按原结果重试数据接收</Btn></>}{!stage.deliveries.length?<p>尚无可核对的接纳记录。登记前的原处理不会补发为新消息。</p>:stage.deliveries.map(delivery=><p key={delivery.intent_id}>{delivery.source_label}：{delivery.status==='admitted'?'已接纳':'尚未接纳'}。已接纳仍不代表等待匹配。</p>)}<p>{stage.waiting_message} 请打开对应等待任务核对实际窗口。差异候选不是现场变化证明。</p></aside>}
 <table className="evidence-values"><thead><tr><th>提取信息</th><th>保存值</th></tr></thead><tbody>{Object.entries(r.record.attributes).map(([key,value])=><tr key={key}><td>{key}</td><td><NativeInformationValue name={key} value={value}/></td></tr>)}</tbody></table><div className="actions">{r.record.task_id&&<Btn disabled={busy} onClick={()=>act(()=>onOpenTask(r.record.task_id))}>查看原任务和核查依据</Btn>}{r.record.modality==='audio'&&<Btn disabled={busy} onClick={()=>act(()=>readStages(r))}>核对数据接收与消息接纳</Btn>}{learnedGraphRecord(r)&&<Btn disabled={busy||!!dataSending.current} onClick={()=>learnedAction(()=>readLearnedStages(r))}>核对变化候选接收与接纳</Btn>}<Btn disabled={busy} onClick={()=>act(()=>exportRow(r.id))}>导出多模观测与依据</Btn></div></>}{videoReviewEntryV165(r)&&<NativeVideoReviewV165 key={'video-review:'+account.user_id+':'+instanceId+':'+r.id+':'+releaseID} {...{account,instanceId,releaseID,Btn}} observationId={r.id} currentAvailable={currentAvailable}/>}</article>;})}
 {saved.history_count>0&&<p>{saved.history_count}条旧归集版本仍保留；这里按原处理显示最新版本，不重复计数。</p>}{saved.has_more&&<p>这里只显示最近100条归集记录，历史仍在服务器。</p>}<h4>本实例按原来源日期每日整理</h4>{saved.daily.length?saved.daily.map(d=>d.integrity_error?<p key={d.day} role="alert">{d.day} · {d.integrity_error}</p>:<p key={d.day}>{d.day} · {d.saved}条归集 · {d.reviewed}条原任务已核查 · {d.blocked}条依据待恢复。拍摄时间和领域效果仍待核验。</p>):<p>尚无多模每日整理回执。</p>}</>}
 </section>;
}
