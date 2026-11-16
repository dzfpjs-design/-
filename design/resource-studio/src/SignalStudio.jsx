import {AudioStreamSignalCard} from './AudioSourcePanel';
import SignalAdviceStudio from './SignalAdviceStudio';
import CandidateWatchStudio from './CandidateWatchStudio';
import SoundCandidateSignalCard from './SoundCandidateSignalCard.jsx';
import ManualObservationForm from './ManualObservationForm';
import SignalRoutingStudio from './SignalRoutingStudio';
import HTTPSignalApprovalStudioV247 from './HTTPSignalApprovalStudioV247';
import SignalGraphRoutingStudioV134,{SignalGraphDeliveryV134} from './SignalGraphRoutingStudioV134';
import {SignalDualSummary} from './SignalDualHandoffs';
import {routingDeliveryText} from './signal-routing-contract.mjs';
import {EvidenceImage} from './LocalObservationStudio';
import React,{useEffect,useRef,useState,useCallback} from 'react';
import {json} from './runtime-api';
import {perceptionObjectState,assertPerceptionObjects,assertPerceptionRequestRelease} from './perception-entity-contract.mjs';
const labels={unassigned:'等待业务核查',handed_off:'已交给业务处理',reviewed:'已保存业务判断',blocked_evidence:'需要恢复原依据'};
const decisions={abnormal:'属于业务异常',not_abnormal:'不属于业务异常',uncertain:'尚不能判断'};
const read=key=>{try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return {broken:true};}};
const text=v=>v===null?'未确定':Array.isArray(v)?v.map(text).join('；'):typeof v==='object'?Object.entries(v).map(([k,x])=>k+'：'+text(x)).join('；'):String(v);
export function SignalBasis({basis}){
 const candidate=basis.candidate_only,native=basis.original_model_result?.segmentation||basis.original_model_result?.detection;
 return <><p>{basis.object_name} · {basis.source_name}</p><p>{basis.reported_at?'登记时间：'+new Date(basis.reported_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（北京时间）':'来源采集时间未知'}。{candidate?'本地模型候选满足已确认的线索规则；不能据此认定现场异常。':'来源时间未经现场核实；规则匹配是待核查线索，不自动认定现场异常。'}</p>
 {candidate&&basis.watch_definition?.profile.family==='local'&&<p>固定筛选条件：{basis.watch_definition.label?"目标类别 "+basis.watch_definition.label+"；":""}{basis.watch_definition.profile.family==='local'?"模型分数至少 "+basis.watch_definition.min_score+"。":""}模型分数不等于业务异常概率。</p>}
 <table className="evidence-values"><thead><tr><th>{candidate?'模型候选指标':'异常字段'}</th><th>{candidate?'原候选结果':'登记值'}</th><th>触发条件</th></tr></thead><tbody>{basis.extracted.anomalies.map((a,i)=>{const rule=candidate?a:basis.trigger_rules.find(r=>r.id===a.rule_id);return <tr key={i}><td>{a.field}</td><td>{text(a.value)}</td><td>{rule?(rule.operator==='contains'?'包含“'+rule.needle+'”':(rule.operator==='gte'?'至少':rule.operator==='gt'?'大于':'小于')+' '+rule.threshold):'需核对原规则'}</td></tr>;})}</tbody></table>
 {native&&<details><summary>查看本地模型原图与候选</summary><EvidenceImage id={basis.evidence_ids[0]} sha={native.image_sha256} alt="线索原始图片"/>{native.detections?.map((d,i)=><p key={i}>{d.label} · 模型分数 {d.score.toFixed(3)} · 原图像素框 {d.bbox.join('，')}</p>)}{native.masks&&<div className="observation-masks">{native.masks.map((m,i)=><figure key={m.mask_evidence_id}><EvidenceImage id={m.mask_evidence_id} sha={m.mask_sha256} alt={'线索区域候选 '+(i+1)}/><figcaption>区域 {i+1} · {m.area_pixels} 像素；语义未确认</figcaption></figure>)}</div>}</details>}
 <details><summary>{candidate?'查看原提取字段':'查看原登记内容'}</summary><dl>{Object.entries(basis.original_attributes).map(([k,v])=><React.Fragment key={k}><dt>{k}</dt><dd>{text(v)}</dd></React.Fragment>)}</dl><p>{candidate?'已核对本地原结果、文件和固定线索规则。':'已核对原登记、提取调用与固定规则版本。'}记录完整性不证明现场真实性。</p></details></>;
}

function SignalCard({account,instanceId,initial,base,formKey,businesses,release,objects,releaseID,Btn,canWrite,onRefresh,onViewTask,onViewAssessment}){
 const key=formKey+':signal:'+initial.id,cache=useRef(read(key));
 const [signal,setSignal]=useState(initial),[note,setNote]=useState(cache.current?.note??initial.note),[dirty,setDirty]=useState(!!cache.current?.dirty),[business,setBusiness]=useState(cache.current?.business||''),[busy,setBusy]=useState(false),[error,setError]=useState(cache.current?.broken?'本地草稿无法读取，原说明保留，请先核对服务器记录。':''),[notice,setNotice]=useState('');
 const pending=useRef(cache.current?.pending||null),handoff=useRef(cache.current?.handoff||null),nativeLink=useRef(cache.current?.nativeLink||null),version=useRef(cache.current?.version||initial.version),life=useRef(0);
 useEffect(()=>()=>{life.current++;},[]);
 useEffect(()=>{setSignal(initial);if(!dirty&&!pending.current){setNote(initial.note);version.current=initial.version;}},[initial]);
 function persist(next={}){if(cache.current?.broken)throw Error('本地旧草稿尚未恢复，请先核对保存记录。');localStorage.setItem(key,JSON.stringify({note,dirty,business,version:version.current,pending:pending.current,handoff:handoff.current,nativeLink:nativeLink.current,...next}));}
 async function act(fn){const epoch=life.current;setBusy(true);setError('');setNotice('');try{await fn();}catch(e){if(life.current===epoch)setError(e.message+' 当前说明和请求仍保留。');}finally{if(life.current===epoch)setBusy(false);}}
 async function save(){
  if(!dirty&&!pending.current)return signal;
  if(!pending.current)pending.current={version:version.current,request_id:crypto.randomUUID(),note};
  persist();const result=await json(base+'/signals/'+signal.id+'/note',{method:'PUT',body:JSON.stringify(pending.current)});
  pending.current=null;version.current=result.version;setSignal(result);setNote(result.note);setDirty(false);persist({note:result.note,dirty:false,pending:null,version:result.version});setNotice('核查说明已保存到服务器。');return result;
 }
 async function send(){
  if(nativeLink.current)throw Error('原任务关联请求尚待核对，请先重试原关联。');const current=await save();if(!business&&!handoff.current)throw Error('请选择已启用的人工核查业务。');
  assertPerceptionObjects(objects,[current.entity_id],releaseID);assertPerceptionRequestRelease(current.basis?.release_id,releaseID);if(handoff.current)assertPerceptionRequestRelease(handoff.current.release_id,releaseID);
  if(!handoff.current)handoff.current={release_id:release.id,business_id:business,entity_ids:[current.entity_id],title:(current.basis.candidate_only?'核查模型线索：':'核查登记异常：')+current.basis.object_name.slice(0,220),request_id:crypto.randomUUID(),signal_id:current.id,signal_version:current.version,signal_basis_hash:current.basis_hash};
  persist();const task=await json(base+'/tasks',{method:'POST',body:JSON.stringify(handoff.current)});
  handoff.current=null;persist({handoff:null});await onRefresh();setNotice('异常已交给业务流程，原依据与说明已保存。');await onViewTask(task.id);
 }
 async function linkOriginal(){
  if(handoff.current)throw Error('另一个业务交接请求尚待核对，请先核对已保存任务。');
  const current=await save();
  if(!nativeLink.current){const task=await json('/workspace/tasks/'+current.basis.original_model_result.task_id);nativeLink.current={version:current.version,basis_hash:current.basis_hash,task_id:task.id,task_version:task.version,request_id:crypto.randomUUID()};persist();}
  const row=await json(base+'/signals/'+signal.id+'/original-task',{method:'POST',body:JSON.stringify(nativeLink.current)});nativeLink.current=null;setSignal(row);version.current=row.version;persist({nativeLink:null,version:row.version});await onRefresh();await onViewTask(row.source_task_link.task_id);
 }
 async function recover(){const listing=await json(base+'/signals'),latest=listing.records.find(r=>r.id===signal.id);if(!latest)throw Error('当前列表未找到此异常，请核对实例或历史范围。');setSignal(latest);version.current=latest.version;pending.current=null;if(nativeLink.current&&latest.source_task_link?.request_id===nativeLink.current.request_id)nativeLink.current=null;if(handoff.current&&latest.tasks.some(t=>t.request_id===handoff.current.request_id))handoff.current=null;persist({version:latest.version,pending:null,handoff:handoff.current,nativeLink:nativeLink.current});setNotice('已读取最新保存说明；当前输入仍保留，请对照服务器说明后继续保存。');}
 async function download(){const result=await json(base+'/signals/'+signal.id+'/export');const blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='异常核查与业务回执.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 const rejected=signal.source_review==='rejected',objectEligibility=perceptionObjectState(objects,signal.entity_id,releaseID),currentSignal=objectEligibility.usable&&signal.basis?.release_id===releaseID;
 const relatedTask=signal.related_native_receipts?.flatMap(r=>r.tasks)[0];
 const activeTask=signal.graph_route!=null||signal.dual_handoff||signal.tasks.find(t=>t.status!=='workflow_cancelled'),frozen=!!pending.current||!!handoff.current||!!nativeLink.current;
 return <article className="media-form signal-card"><h3>{signal.dual_handoff?'人工响应与异常核验分别处理':labels[signal.status]||'异常待核查'}</h3>{rejected&&<p role="alert">原提取结果已被人工拒绝，不能交给业务自动采用；说明仍可保存。</p>}{signal.integrity_issue?<p role="alert">{signal.integrity_issue}。核查说明仍保留。</p>:<SignalBasis basis={signal.basis}/>}
 {!currentSignal&&<p role="status">原通知与说明保留。{objectEligibility.usable?'此通知固定于原实例版本；请在当前版本明确确认新的线索规则，不能自动交给新版本业务。':objectEligibility.reason} 核查说明仍可保存，已关联任务仍可查看。</p>}
 {signal.dual_handoff&&<SignalDualSummary key={formKey+':'+signal.dual_handoff.id} handoffId={signal.dual_handoff.id} {...{instanceId,account,Btn,onViewTask}} refreshKey={signal.dual_handoff.view_hash}/>}{signal.dual_handoff_failure&&<p role="alert">双路交接未能安排：{signal.dual_handoff_failure.message}。原异常与说明保留，不自动重试或改成交给其他业务。</p>}
 {signal.route_failure?<p role="alert">自动交接未执行：{signal.route_failure.message}。异常与原依据保留；请在下方核查后手动决定，不会自动重试。</p>:signal.route_receipt&&<div role="status"><p>{routingDeliveryText(signal.route_receipt)}。{signal.route_receipt.message} 采集时间与实际情况仍需人工核实，未授权设备动作或云调用。</p>{signal.route_receipt.task_id&&<Btn disabled={busy} onClick={()=>act(()=>onViewTask(signal.route_receipt.task_id))}>打开自动交接的人工核查任务</Btn>}</div>}
 <SignalGraphDeliveryV134 {...{signal,Btn}} disabled={busy} onViewTask={id=>act(()=>onViewTask(id))}/>
 <label>核查说明<textarea aria-label="异常核查说明" value={note} disabled={busy||frozen||!canWrite} onChange={e=>{const value=e.target.value;try{persist({note:value,dirty:true});setNote(value);setDirty(true);}catch(x){setError(x.message);}}}/></label>
 {dirty&&<p>有尚未提交的说明，草稿保存在当前浏览器；请保存到服务器。</p>}{pending.current&&<p>上一保存请求尚待确认；重试会核对同一请求，不重复保存。</p>}
 <div className="actions"><Btn disabled={busy||!canWrite||(!dirty&&!pending.current)} onClick={()=>act(save)}>保存核查说明</Btn><Btn disabled={busy} onClick={()=>act(recover)}>核对最新保存的说明</Btn>{signal.integrity_issue&&<Btn disabled={busy||!canWrite} onClick={()=>act(async()=>{const value=await json(base+'/signals/'+signal.id+'/recheck',{method:'POST',body:JSON.stringify({version:signal.version})});setSignal(value);version.current=value.version;persist({version:value.version});await onRefresh();})}>重新核对恢复的原依据</Btn>}<Btn disabled={busy||!!signal.integrity_issue} onClick={()=>act(download)}>导出异常与核查依据</Btn></div>
 {signal.note!==note&&<p>服务器已保存说明：{signal.note||'尚未填写'}。当前输入仍保留，可核对后重新保存。</p>}
 {!activeTask&&!relatedTask&&<><label>交给哪个业务<select aria-label="异常接收业务" value={business} disabled={busy||frozen||!canWrite||!currentSignal||!!signal.integrity_issue||rejected} onChange={e=>{try{persist({business:e.target.value});setBusiness(e.target.value);}catch(x){setError(x.message);}}}><option value="">请选择已启用的人工核查业务</option>{businesses.map(b=><option key={b.id} value={b.id}>{b.name}</option>)}</select></label><Btn primary disabled={busy||!canWrite||!currentSignal||!!signal.integrity_issue||rejected||(!business&&!handoff.current)} onClick={()=>act(send)}>交给所选业务处理</Btn></>}
 {signal.tasks.filter(t=>!signal.dual_handoff||!Object.values(signal.dual_handoff.slots).some(slot=>slot.task_id===t.id)).map(t=><p key={t.id}>{t.business_name} · {t.title} · {t.status==='workflow_completed'?'处理完成':t.status==='workflow_cancelled'?'已取消':'待处理'}<Btn disabled={busy} onClick={()=>act(()=>onViewTask(t.id))}>打开已保存业务任务</Btn></p>)}
 {signal.basis?.original_model_result?.task_id&&!activeTask&&!relatedTask&&<Btn primary disabled={busy||!canWrite||!!signal.integrity_issue||rejected||!!handoff.current} onClick={()=>act(linkOriginal)}>{nativeLink.current?'重试原任务关联请求':'关联到原分析任务核查'}</Btn>}
 {signal.related_native_receipts?.length>0&&<p>还保留 {signal.related_native_receipts.length} 条来自同一原处理与相同规则的历史通知。新核查已按原处理去重，历史说明与原件保留。</p>}
 {relatedTask&&<p>同一原处理已关联业务任务，不重复安排审核。<Btn disabled={busy} onClick={()=>act(()=>onViewTask(relatedTask.id))}>打开同一原处理的已关联任务</Btn></p>}
 {nativeLink.current&&<p>原任务关联尚待确认，重试会核对同一请求；核查说明仍保留。</p>}
 {signal.basis?.original_model_result?.task_id&&<Btn disabled={busy} onClick={()=>act(async()=>{await save();onViewTask(signal.basis.original_model_result.task_id);})}>打开原分析任务</Btn>}
 {signal.business_review&&<> <p>业务判断：{decisions[signal.business_review.decision]}。</p><p>核查结论：{signal.business_review.note}</p><p>结论已保存为数据管理候选记录，不自动更新对象或发布经验。</p></>}
 <SignalAdviceStudio {...{signal,formKey,release,Btn,onViewTask,onViewAssessment}} canWrite={canWrite&&currentSignal} disabled={busy||frozen||dirty||!currentSignal||!!signal.integrity_issue||rejected}/>{error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}</article>;
}
export default function SignalStudio({instanceId,account,objects,release,releaseID,enabledBusinesses,enabled,Btn,onViewTask,onViewAssessment,onOpenAudioStream,onRegisterGuard}){
 const base='/workspace/instances/'+instanceId,key='ga-reported:'+account.workspace_id+':'+account.username+':'+instanceId;
 const alive=useRef(0);
 const guards=useRef(new Map()),register=useRef(onRegisterGuard);register.current=onRegisterGuard;
 const humanGuard=useCallback(guard=>{if(guard)guards.current.set('human',guard);else guards.current.delete('human');},[]),graphGuard=useCallback(guard=>{if(guard)guards.current.set('graph',guard);else guards.current.delete('graph');},[]),manualDeliveryGuard=useCallback((guard,previous)=>{if(guard)guards.current.set('manual-data-delivery',guard);else if(!previous||guards.current.get('manual-data-delivery')===previous)guards.current.delete('manual-data-delivery');},[]),httpApprovalGuard=useCallback((guard,previous)=>{if(guard)guards.current.set('http-approval-v247',guard);else if(!previous||guards.current.get('http-approval-v247')===previous)guards.current.delete('http-approval-v247');},[]),candidateWatchGuard=useCallback((guard,previous)=>{if(guard)guards.current.set('candidate-watch',guard);else if(!previous||guards.current.get('candidate-watch')===previous)guards.current.delete('candidate-watch');},[]);
 useEffect(()=>{const guard=()=>{let okay=true;for(const check of guards.current.values())try{if(check()===false)okay=false;}catch{okay=false;}return okay;};register.current?.(guard);return()=>register.current?.(null);},[instanceId,account.user_id,account.workspace_id,onRegisterGuard]);
 const [records,setRecords]=useState([]),[observations,setObservations]=useState([]),[hasMore,setHasMore]=useState(false),[reportedHasMore,setReportedHasMore]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[loaded,setLoaded]=useState(false);
 const writable=enabled&&['admin','operator'].includes(account.role),businesses=(release?.snapshot.businesses||[]).filter(b=>enabledBusinesses.includes(b.id)&&b.available&&b.execution_mode==='confirmed_human_workflow'&&b.flow.steps.every(s=>s.agent==='人工确认'));
 async function load(){const epoch=alive.current;const [signals,readings]=await Promise.all([json(base+'/signals'),json(base+'/reported-observations')]);if(epoch!==alive.current)return;if(guards.current.get('manual-data-delivery')?.()===false)throw Error('人工登记的收件说明或原申请尚未保存，当前登记与输入保留，列表暂未替换。');setRecords(signals.records);setHasMore(signals.has_more);setObservations(readings.records);setReportedHasMore(!!readings.has_more);setLoaded(true);}
 useEffect(()=>{const epoch=alive.current;load().catch(e=>setError(e.message));const timer=setInterval(()=>load().catch(e=>{if(epoch===alive.current)setError('业务收件暂无法更新：'+e.message);}),5000);return()=>{clearInterval(timer);alive.current++;};},[instanceId,enabled]);
 async function act(fn){setBusy(true);setError('');try{await fn();}catch(e){setError(e.message+' 原请求与输入仍保留，可恢复后重试。');}finally{setBusy(false);}}
 return <section className="media-form signal-studio"><h3>异常通知与业务核查</h3><p>已确认的本地规则或模型线索自动送到这里；原结果同步交数据管理。先核对来源和原依据，再交给已确认的业务流程；不自动改变对象或调度设备。</p><Btn disabled={busy} onClick={()=>act(load)}>读取保存的异常与登记</Btn>{error&&<p role="alert" className="error">{error}</p>}
 {!loaded?<p>正在读取业务收件…</p>:!records.length?<p>尚无已送达的待核查线索。没有异常通知不等于现场正常。</p>:records.map(r=>{const reference=r.basis?.original_model_result;if(Object.hasOwn(r,'audio_stream_reference')||reference?.schema==='gateway-audio-candidate-reference-v255')return <AudioStreamSignalCard key={r.id} signal={r} account={account} instanceId={instanceId} Btn={Btn} onOpenStream={onOpenAudioStream} canLeave={()=>{for(const guard of guards.current.values())if(guard()===false)return false;return true;}}/>;if(reference?.schema==='sound-candidate-signal-reference-v1'&&reference.family==='environment_sound')return <SoundCandidateSignalCard key={r.id} signal={r} {...{account,instanceId,Btn,onViewTask}} canLeave={()=>{for(const guard of guards.current.values())if(guard()===false)return false;return true;}}/>;if(reference?.schema==='sound-candidate-signal-reference-v1'||reference?.family==='environment_sound')return <article key={r.id} role="alert">声音通知的来源类型尚未通过核对，未展示候选数值或安排新任务；原记录保留。</article>;return <SignalCard key={r.id} initial={r} base={base} formKey={key} {...{account,instanceId,businesses,release,objects,releaseID,Btn,onViewTask,onViewAssessment}} canWrite={writable} onRefresh={load}/>;})}{hasMore&&<p>只显示最近100条异常，历史仍在服务器。</p>}
 <HTTPSignalApprovalStudioV247 key={['http-approval-v247',account.user_id,account.workspace_id,account.role,instanceId,releaseID,enabled].join(':')} {...{instanceId,account,releaseID,enabled,Btn,onViewTask}} onRegisterGuard={httpApprovalGuard}/>
 <SignalRoutingStudio key={[account.user_id,account.workspace_id,account.role,instanceId,releaseID,enabled].join(':')} {...{instanceId,account,releaseID,enabled,Btn,onViewTask}} onRegisterGuard={humanGuard}/>
 <SignalGraphRoutingStudioV134 key={['signal-graph',account.user_id,account.workspace_id,account.role,instanceId].join(':')} {...{instanceId,account,releaseID,enabled,Btn}} onRegisterGuard={graphGuard}/>
 <CandidateWatchStudio key={['candidate-watch',account.user_id,account.workspace_id,account.role,instanceId].join(':')} {...{account,instanceId,base,objects,releaseID,Btn,writable}} onRegisterGuard={candidateWatchGuard}/>
  <ManualObservationForm key={instanceId+':'+account.user_id} {...{instanceId,account,objects,releaseID,enabled,observations,Btn}} onRegisterGuard={manualDeliveryGuard} hasMore={reportedHasMore} onRefresh={load}/></section>;
}
