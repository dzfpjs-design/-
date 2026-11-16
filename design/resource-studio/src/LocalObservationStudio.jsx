import React,{useEffect,useRef,useState} from 'react';
import {api,json,hashBytes} from './runtime-api';
import {usablePerceptionObjects,assertPerceptionObjects,assertPerceptionRequestRelease,retainPerceptionPending} from './perception-entity-contract.mjs';
import {perceptionObjectOptions,perceptionSelectionNote} from './perception-object-fields';
import {blankLocalObservation,localObservationReady,localObservationBody,sameLocalObservation} from './local-observation-form.mjs';
import {localImageRetryScope,localImageRetryKey,readLocalImageRetry,localImageRetryIssue,stageLocalImageRetry,localImageRetryReceipt,settleLocalImageRetry,requireOriginalLocalImageRetry} from './local-image-retry.mjs';
import DataDeliveryStudio from './DataDeliveryStudio.jsx';
const states={received:'原图和条件已保存，等待本地处理',processed:'本地候选已保存',failed:'处理失败，原图保留',cancelled:'已停止接受结果，原图保留'};
const clock=t=>new Date(t).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});
export function EvidenceImage({id,sha,alt}){
 const [url,setURL]=useState(''),[error,setError]=useState('');
 useEffect(()=>{let alive=true,created='';setURL('');setError('');(async()=>{try{const res=await api('/evidence/'+id+'/content'),blob=await res.blob();if(await hashBytes(await blob.arrayBuffer())!==sha)throw Error('原图摘要不一致，请恢复原件后重试。');created=URL.createObjectURL(blob);if(alive)setURL(created);else URL.revokeObjectURL(created);}catch(e){if(alive)setError(e.message);}})();return()=>{alive=false;if(created)URL.revokeObjectURL(created);};},[id,sha]);
 return error?<p role="alert" className="error">{error}</p>:url?<img className="observation-image" src={url} alt={alt}/>:<p>正在读回并核对保存的图片…</p>;
}
export default function LocalObservationStudio({account,instanceId,objects,releaseID,Btn,Field,onRegisterGuard}){
 const key='ga-local-observation:'+account.workspace_id+':'+account.username+':'+instanceId,base='/workspace/instances/'+instanceId;
 const [form,setForm]=useState(blankLocalObservation),[action,setAction]=useState(null),[models,setModels]=useState([]),[records,setRecords]=useState([]),[daily,setDaily]=useState([]),[row,setRow]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[hasMore,setHasMore]=useState(false);
 const fileInput=useRef(),live=useRef(true),journal=useRef({form:blankLocalObservation(),action:null}),controller=useRef(null),storageBroken=useRef(false);
 const [retryPending,setRetryPending]=useState(null),[retryCacheError,setRetryCacheError]=useState('');
 const deliveryGuard=useRef(null),guardCallback=useRef(onRegisterGuard);guardCallback.current=onRegisterGuard;
 function registerDeliveryGuard(fn,previous){if(fn||!previous||deliveryGuard.current===previous)deliveryGuard.current=fn;}
 function checkDelivery(){if(deliveryGuard.current&&deliveryGuard.current()===false)throw Error('当前观测的收件说明或原申请尚未完整保存，请先重试保存；当前输入保留。');}
 useEffect(()=>{const guard=()=>deliveryGuard.current?deliveryGuard.current():true;guardCallback.current?.(guard);return()=>guardCallback.current?.(null,guard);},[]);
 const retryContext=useRef(''),retryStamp=JSON.stringify([account.workspace_id,account.user_id,instanceId,releaseID,row?.id]);retryContext.current=retryStamp;
 function persist(next){if(storageBroken.current)throw Error('原草稿无法读回，暂不覆盖；可先查看服务器记录。');const encoded=JSON.stringify(next);localStorage.setItem(key,encoded);if(localStorage.getItem(key)!==encoded)throw Error('本地观测草稿未完整保存，原请求保留');journal.current=next;if(live.current){setForm(next.form);setAction(next.action);}}
 function edit(patch){try{persist({...journal.current,form:{...journal.current.form,...patch}});setError('');}catch{setError('无法保存当前草稿，请处理浏览器存储后重试。');}}
 async function load(){if(!live.current)return;const [m,list,d]=await Promise.all([json(base+'/local-observation-models'),json(base+'/local-observations'),json(base+'/local-observation-daily')]);if(!live.current)return;setModels(m);setRecords(list.records);setHasMore(list.has_more);setDaily(d);}
 async function inspect(id){if(!live.current)return;if(row?.id!==id)checkDelivery();const value=await json(base+'/local-observations/'+id);if(!live.current)return;if(row?.id!==id)checkDelivery();setRow(value);persist({...journal.current,form:{...journal.current.form,selected:id}});}
 async function act(fn){setBusy(true);setError('');try{await fn();}catch(e){if(live.current)setError(e.message);}finally{controller.current=null;if(live.current)setBusy(false);}}
 async function recover(submit=false){const saved=journal.current.action;if(!saved)return;let value;try{value=await json(base+'/local-observation-requests/'+saved.request_id);}catch(e){if(e.status!==404)throw e;if(!submit){setNotice(saved.evidence_id?'原图片已保存，可核对并重试同一提交。':'请重新选择同一原图，核对摘要后继续。');return;}assertPerceptionRequestRelease(saved.release_id,releaseID);assertPerceptionObjects(objects,[saved.entity_id],releaseID);value=await json(base+'/local-observations',{method:'POST',body:JSON.stringify(localObservationBody(saved)),signal:controller.current?.signal});}
  if(!sameLocalObservation(value,saved))throw Error('服务器原请求与本浏览器条件不一致，请保留记录并核对。');if(!live.current)return;
  if(row?.id!==value.id)checkDelivery();setRow(value);persist({form:{...journal.current.form,selected:value.id},action:null});setNotice('原图与条件已保存；后台在本机处理，离开页面也会保留结果。');await load();
 }
 useEffect(()=>{live.current=true;(async()=>{
  try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved){if(!saved.form||typeof saved.form.entity_id!=='string'||typeof saved.form.extractor_id!=='string'||!saved.form.drafts)throw Error('原浏览器草稿格式不一致，请保留记录并核对服务器历史。');persist(saved);}}
  catch(e){storageBroken.current=true;if(live.current)setError(e.message);}
  try{await load();if(!storageBroken.current){if(journal.current.action)await recover(false);else if(journal.current.form.selected)await inspect(journal.current.form.selected);}}
  catch(e){if(live.current)setError(e.message);}
 })();return()=>{live.current=false;controller.current?.abort();};},[]);
 useEffect(()=>{if(row?.status!=='received'&&!(row?.status==='processed'&&!row?.data_received))return;const timer=setInterval(()=>{if(row?.status==='received'||(row?.status==='processed'&&!row.data_received))act(async()=>{await inspect(row.id);await load();});},5000);return()=>clearInterval(timer);},[row?.id,row?.status,row?.data_received]);
 useEffect(()=>{setRetryPending(null);setRetryCacheError('');if(!row)return;try{setRetryPending(readLocalImageRetry(localStorage,localImageRetryScope(account,instanceId,row)).value.pending);}catch(e){setRetryCacheError(e.message);}},[account.workspace_id,account.user_id,instanceId,row?.id]);
 async function upload(event){const file=event.target.files?.[0];event.target.value='';if(!file)return;await act(async()=>{
  assertPerceptionObjects(objects,[journal.current.action?.entity_id||form.entity_id],releaseID);
  if(journal.current.action)assertPerceptionRequestRelease(journal.current.action.release_id,releaseID);
  if(!['image/png','image/jpeg'].includes(file.type))throw Error('请选PNG或JPEG原图。');const sha=await hashBytes(await file.arrayBuffer());let saved=journal.current.action;
  if(saved){if(saved.sha256!==sha||saved.bytes!==file.size)throw Error('这不是原请求选定的图片，请重新选择同一原件。');}
  else {if(!localObservationReady(form,usablePerceptionObjects(objects,releaseID),models))throw Error('请先选择当前业务对象及可用的本地模型。');saved={request_id:crypto.randomUUID(),release_id:releaseID,entity_id:form.entity_id,extractor_id:form.extractor_id,sha256:sha,bytes:file.size,name:file.name,evidence_id:null};persist({form:journal.current.form,action:saved});}
  controller.current=new AbortController();
  if(!saved.evidence_id){const uploaded=await json('/evidence',{method:'POST',headers:{'Content-Type':file.type},body:file,signal:controller.current.signal});if(uploaded.sha256!==sha)throw Error('服务器原件摘要与选图不一致，请保留请求并核对。');saved={...saved,evidence_id:uploaded.id};persist({form:journal.current.form,action:saved});}
  if(live.current)await recover(true);
 });}
 async function review(decision){const note=form.drafts[row.id];if(!note?.trim())throw Error('请填写核查说明。');await json('/observations/'+row.id+'/verify',{method:'POST',body:JSON.stringify({expected_version:row.version,decision,reason:note})});await inspect(row.id);await load();setNotice('人工核查已保存到服务器，候选仍不自动更新现场状态。');}
 async function retryProcessing(){
  const scope=localImageRetryScope(account,instanceId,row),stamp=retryStamp,path=base+'/local-observations/'+row.id;
  const current=()=>{if(!live.current||retryContext.current!==stamp)throw Error('当前对象、账号或实例版本已变化；原重试请求保留，请核对原回执');};
  let original=readLocalImageRetry(localStorage,scope),fresh=await json(path);current();setRow(fresh);
  if(original.value.pending&&await localImageRetryReceipt(fresh,original,scope)){
   current();original=await settleLocalImageRetry(localStorage,scope,original,fresh,current);current();setRetryPending(null);setNotice('原重试回执已核对，未另发处理请求。当前进度请以保存的处理记录为准。');await load();return;
  }
  const me=await json('/me');current();
  if(me.user_id!==scope.user_id||me.workspace_id!==scope.workspace_id||!['admin','operator'].includes(me.role))throw Error('当前账号或原处理权限已变化；原请求与人工说明保留，目前只能核对原回执');
  if(!original.value.pending){original=await stageLocalImageRetry(localStorage,scope,original,fresh,releaseID,current);current();setRetryPending(original.value.pending);}
  requireOriginalLocalImageRetry(fresh,original,releaseID);current();
  if(readLocalImageRetry(localStorage,scope).raw!==original.raw)throw Error('另一页面已更新原重试请求，尚未发送，请核对保存记录');
  let transportError=null;try{await json('/observations/'+scope.observation_id+'/retry',{method:'POST',body:JSON.stringify(original.value.pending.body)});}catch(e){transportError=e;}
  current();fresh=await json(path);current();setRow(fresh);
  if(!await localImageRetryReceipt(fresh,original,scope))throw Error((transportError?.message||'尚未读到匹配的重试回执')+'。原编号与条件保留，请先核对原请求，不能另换编号提交');
  original=await settleLocalImageRetry(localStorage,scope,original,fresh,current);current();setRetryPending(null);setNotice('原处理重试已保存并核对回执，未改原输入。后台尚可能失败，请继续核对处理进度；候选不代表现场状态。');await load();
 }
 async function download(){const exported=await json(base+'/local-observations/'+row.id+'/export'),url=URL.createObjectURL(new Blob([JSON.stringify(exported,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='本地观测与依据.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 const allowed=['admin','operator'].includes(account.role),native=row?.output?.detection||row?.output?.segmentation;
 return <section className="media-form"><h3>上传图片，在本机提取信息</h3><p>选择对象和已登记的本地图片模型。原件、候选与人工核查进入数据管理智能体；按原件首次保存的中国日期整理，采集时间未知就保留未知。</p>
 {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
 <fieldset disabled={busy||!!action||!allowed||storageBroken.current}><Field label="图片属于哪个对象"><select aria-label="本地观测对象" value={form.entity_id} onChange={e=>edit({entity_id:e.target.value})}><option value="">请选择当前业务对象</option>{perceptionObjectOptions(objects,form.entity_id,releaseID)}</select></Field>{perceptionSelectionNote(objects,form.entity_id,releaseID)}
 <Field label="使用哪个本地图片模型"><select aria-label="本地观测模型" value={form.extractor_id} onChange={e=>edit({extractor_id:e.target.value})}><option value="">请选择已登记的本地模型</option>{models.map(m=><option key={m.id} value={m.id}>{m.name} · {m.version}</option>)}</select></Field></fieldset>
 {!models.length&&<p>尚无可用本地图片模型。请先在资源与能力中安装、检查并登记模型。</p>}
 <input ref={fileInput} type="file" accept="image/png,image/jpeg" hidden onChange={upload}/>
 <div className="actions"><Btn primary disabled={busy||!allowed||storageBroken.current||(!action&&!localObservationReady(form,usablePerceptionObjects(objects,releaseID),models))||!!action?.evidence_id} onClick={()=>fileInput.current.click()}>{action?'重新选择原图并继续':'上传PNG或JPEG并在本机处理'}</Btn>{action?.evidence_id&&<Btn disabled={busy||!allowed} onClick={()=>act(async()=>{controller.current=new AbortController();await recover(true);})}>核对并重试原提交</Btn>}{busy&&controller.current&&<Btn onClick={()=>controller.current.abort()}>停止等待，保留原请求</Btn>}<Btn disabled={busy} onClick={()=>act(load)}>读取保存的本地观测</Btn></div>
 {action&&<Btn disabled={busy} onClick={()=>act(async()=>{retainPerceptionPending(localStorage,key,journal.current.action);persist({...journal.current,action:null});setNotice('原请求与完整草稿已在本机独立保留；后台记录未取消，可从已保存处理核对。重新提交需核对当前对象与模型。');})}>保留原请求，取消本机等待</Btn>}
 {action&&<p>未确认请求：{action.name}。条件已锁定；停止等待不会删除原图或取消已经排入后台的处理。</p>}
 <h4>已保存的处理</h4>{!records.length?<p>还没有本地图片观测记录。</p>:records.map(r=><p key={r.id}><Btn disabled={busy} onClick={()=>act(()=>inspect(r.id))}>{clock(r.created_at)} · {objects.find(o=>o.id===r.entity_id)?.name||'原登记对象'} · {states[r.status]}</Btn></p>)}{hasMore&&<p>这里只显示最近20条记录，历史记录仍保留在服务器。</p>}
 {row&&<article className="media-form"><h4>{states[row.status]}</h4><p>采集时间：{row.source_clock?clock(row.source_clock)+'（未经独立核实）':'未知'} · 数据管理：{row.data_received?'旧投递记录标记完成，实际独立收件请核对下方回执':'等待处理或交付，以独立收件回执为准'}。</p>{row.error&&<p className="error">{row.error}</p>}<DataDeliveryStudio key={row.id} {...{account,instanceId,Btn}} observationId={row.id} entityId={row.entity_id} sourceName="所选图片观测" draftChannel="local-image" onRegisterGuard={registerDeliveryGuard} onReload={()=>inspect(row.id)}/>{row.integrity_error?<p className="error">{row.integrity_error}</p>:<EvidenceImage id={row.evidence_id} sha={row.source_sha256} alt="本次本地观测固定原图"/>}
 {native&&!row.integrity_error&&<><p>本机 {native.backend.toUpperCase()} · 推理 {native.inference_ms} 毫秒（不含排队与加载） · 未调用云模型。</p>{native.detections&&<><h4>通用目标候选（{native.detections.length}个）</h4>{native.detections.length?native.detections.map((d,i)=><p key={i}>{d.label} · 模型分数 {d.score.toFixed(3)} · 像素框 {d.bbox.join('，')}</p>):<p>本次门槛下没有通用目标候选，不能据此判断没有业务异常。</p>}</>}{native.masks&&<><h4>通用区域候选（{native.masks.length}个）</h4><div className="observation-masks">{native.masks.map((m,i)=><figure key={m.mask_evidence_id}><EvidenceImage id={m.mask_evidence_id} sha={m.mask_sha256} alt={'区域候选 '+(i+1)}/><figcaption>区域 {i+1} · {m.area_pixels} 像素，语义待核</figcaption></figure>)}</div></>}</>}
 {row.status==='processed'&&<><p>专业模型领域效果仍待核验，候选及人工说明不直接更新对象状态或发布经验。</p>{row.review&&<p>已保存核查：{row.review.decision==='confirmed'?'候选信息已核对':'本次结果不适用'} · {row.review.reason}</p>}<Field label="人工核查说明"><textarea aria-label="本地观测核查说明" value={form.drafts[row.id]||''} disabled={busy||!allowed} onChange={e=>edit({drafts:{...form.drafts,[row.id]:e.target.value}})}/></Field><p>输入草稿保存在此浏览器；点击下方保存后写入服务器。</p><div className="actions"><Btn disabled={busy||!allowed||!!row.integrity_error||!form.drafts[row.id]?.trim()} onClick={()=>act(()=>review('confirmed'))}>保存：候选信息已核对</Btn><Btn disabled={busy||!allowed||!!row.integrity_error||!form.drafts[row.id]?.trim()} onClick={()=>act(()=>review('rejected'))}>保存：本次结果不适用</Btn><Btn disabled={busy||!!row.integrity_error} onClick={()=>act(download)}>导出观测与依据</Btn></div></>}
 {(row.status==='failed'||retryPending)&&<><p role="status">已尝试 {row.retry_attempts??'待核对'} 次，上限 {row.retry_limit??3} 次。{row.retry_reason||localImageRetryIssue(row,releaseID)}</p>{retryCacheError&&<p role="alert">{retryCacheError} 原等待记录保留，已阻止新重试。</p>}{retryPending&&<p>原处理重试编号与条件已保存在本机。恢复时先核对原回执；未确认前不另建重试，实例版本变化也不自动改签。</p>}<Btn disabled={busy||!!retryCacheError||(!retryPending&&(!allowed||!!localImageRetryIssue(row,releaseID)))} onClick={()=>act(retryProcessing)}>{retryPending?'核对并恢复原处理重试':'核对原图后重试本地处理'}</Btn></>}
 </article>}
 <h4>本地图片每日整理</h4>{daily.length?daily.map(d=><p key={d.id}>{d.received_day} · {d.processed}条已处理 · {d.failed}条失败 · {d.pending}条等待 · {d.reviewed}条已核查。按原件保存日期归类，现场时间及专业效果仍待核验。</p>):<p>还没有保存的每日整理回执。</p>}
 </section>;
}
