import React,{useEffect,useRef,useState} from 'react';
import {json as requestJSON} from './runtime-api';
import {EXECUTION_STATES,executionScope,executionStorageKey,executionSessionStamp,executionSessionMatches,emptyExecutionForm,checkExecutionListing,checkExecutionPreview,executionPermissions,executionNeedsPolling,executionPreviewCurrent,executionConsent,executionConsentMatches,executionApproveBody,executionCommandBody,executionPending,checkExecutionPending,restoreExecutionJournal,writeExecutionJournal,executionRecoveryBody,executionRecoveryVersion,executionRequestPath,executionExportMarkdown,executionFieldLabels} from './native-step-execution-contract.mjs';

const stamp=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未核实';
const agents={business:'业务智能体',data:'数据管理智能体',perception:'多模感知智能体'};
const actions={query_evidence:'核对原资料',inspect_observation:'核对已保存观测',report:'整理依据和限制',invoke_capability:'运行已授权只读工具'};
const stepStates={queued:'等待处理',running:'处理中',succeeded:'真实处理已完成',failed:'未完成',blocked:'条件待恢复',cancelled:'已停止',skipped:'未执行',pending:'等待处理',needs_review:'结果待核对'};
const decisions={accepted:'已采用',rejected:'已拒绝'};
const referenceKey=key=>/(?:^id$|_ids?$|_hash$|^hash$|^request_id$)/.test(key);
function FieldValue({value,fields=[],field=null,full=false}){
 const [limit,setLimit]=useState(25);
 if(value===null||value===undefined)return <span>未提供，保持未知</span>;
 if(typeof value!=='object')return <span style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{typeof value==='boolean'?(value?'是':'否'):String(value)}{typeof value==='number'&&<small> · {field?.unit?'单位：'+field.unit:'单位未声明，保持原值'}</small>}</span>;
 if(Array.isArray(value)){const shown=full?value.length:Math.min(limit,value.length);return <div><p>共 {value.length} 项，当前显示 {shown} 项。</p>{value.slice(0,shown).map((item,index)=><div className="media-form" key={index}><strong>第 {index+1} 项</strong><FieldValue value={item} full={full}/></div>)}{shown<value.length&&<button type="button" onClick={()=>setLimit(count=>count+25)}>再显示25项结果</button>}</div>;}
 const ordinary=Object.entries(value).filter(([key])=>!referenceKey(key)),proof=Object.entries(value).filter(([key])=>referenceKey(key));
 function entries(items){return <dl>{items.map(([key,item])=>{const definition=fields.find(entry=>entry.key===key);return <React.Fragment key={key}><dt>{definition?.label||executionFieldLabels[key]||key}</dt><dd><FieldValue value={item} field={definition} full={full}/></dd></React.Fragment>;})}</dl>;}
 return <div>{entries(ordinary)}{proof.length>0&&<details><summary>核对原来源与保存凭据</summary>{entries(proof)}</details>}{!ordinary.length&&!proof.length&&<p>没有此项内容。</p>}</div>;
}
function StepResult({step}){
 const fields=step.output_fields||[];
 return <article className="media-session"><h6>第 {step.index+1} 步 · {step.title}</h6><p>{agents[step.agent]||step.agent} · {actions[step.action]||step.action} · {stepStates[step.status]||'处理状态待核实'}。</p>
 {step.capability_name&&<p>实际工具：{step.capability_name}。{step.external?'外发目的地：'+step.destination:'在登记的本机只读工具中处理'}。</p>}
 <details><summary>核对本步骤完整实际输入</summary><FieldValue value={step.input} full/></details>
 {Object.hasOwn(step,'output')&&step.output!==null?<section aria-label={'第'+(step.index+1)+'步真实结果'}><strong>已保存真实结果</strong><FieldValue value={step.output} fields={fields}/></section>:<p>此步骤尚无已保存的真实结果，不能用计划正文代替。</p>}
 {step.error&&<div role="alert"><strong>此步骤未完成</strong><FieldValue value={step.error}/></div>}
 {step.receipt&&<section><strong>本步骤真实调用收据</strong><FieldValue value={step.receipt}/></section>}
 <p>用量、费用与观察时间仅按原服务回报显示；未提供的内容保持未知。</p>
 {step.invocation_id&&<details><summary>核对已保存调用的凭据</summary><p>{step.invocation_id}</p></details>}
 </article>;
}

export default function NativeStepExecution({task,account,Btn,locked=false,onChanged,planID,planVersion,historical=false,executionBlockedReason=''}){
 let scope,identityError='';try{scope=executionScope(account,task.id,planID);}catch(e){identityError=e.message;scope={workspace_id:account?.workspace_id||'',user_id:account?.user_id||'',username:account?.username||'',role:account?.role||'',task_id:task?.id||'',plan_id:planID||''};}
 const scopeKey=JSON.stringify(scope),key=executionStorageKey(scope),base='/workspace/tasks/'+encodeURIComponent(task.id)+'/intelligent-plan/'+encodeURIComponent(planID)+'/execution',viewer=account.role==='viewer';
 const [uiScope,setUIScope]=useState(null),[listing,setListing]=useState(null),[journal,setJournal]=useState(null),[preview,setPreview]=useState(null),[consent,setConsent]=useState(null),[externalConsent,setExternalConsent]=useState(false),[retryCharge,setRetryCharge]=useState(false),[busy,setBusy]=useState(false),[reading,setReading]=useState(false),[error,setError]=useState(''),[readError,setReadError]=useState(''),[cacheError,setCacheError]=useState(''),[notice,setNotice]=useState(''),[probe,setProbe]=useState(null),[sessionIssue,setSessionIssue]=useState(false);
 const life=useRef(null),renderScope=useRef(scopeKey),stateRef=useRef(null),dataRef=useRef(null),reads=useRef(0),writeBusy=useRef(false),readController=useRef(null),controllers=useRef(new Set());renderScope.current=scopeKey;
 const executionGate=useRef(executionBlockedReason);executionGate.current=executionBlockedReason;
 function requireExecution(operation){if(['approve','retry'].includes(operation)&&executionGate.current)throw Error(executionGate.current+' 原审批与执行记录保留，尚未启动新的执行。');}
 function json(path,options){if(options?.method==='POST'&&[base+'/approve',base+'/retry'].includes(path))requireExecution(path.slice(base.length+1));return requestJSON(path,options);}
 const visible=uiScope===scopeKey,data=visible?listing:null,state=visible?journal:null,form=state?.form||emptyExecutionForm(),pending=state?.pending||null,shownPreview=visible?preview:null,row=data?.execution;
 function active(ticket){try{return life.current===ticket&&renderScope.current===ticket?.key&&executionSessionMatches(ticket.scope,sessionStorage,ticket.stamp);}catch{return false;}}
 function ensure(ticket){if(active(ticket))return true;if(life.current===ticket&&renderScope.current===ticket?.key){setSessionIssue(true);setError('账号或登录会话已有变化，原执行请求保留。请重新进入任务核对会话。');}return false;}
 function persist(next,ticket=life.current){if(!ensure(ticket))throw Error('会话已经变化，原请求保留。');try{writeExecutionJournal(localStorage,key,next,ticket.scope,stateRef.current?.pending);}catch(e){setCacheError('本机执行恢复记录未能保存，已阻止操作：'+e.message);throw e;}stateRef.current=next;setJournal(next);return next;}
 function update(result,ticket){if(!active(ticket))return;checkExecutionListing(result,ticket.scope);if(dataRef.current&&dataRef.current.task_version>result.task_version)return;dataRef.current=result;setListing(result);}
 async function load(ticket=life.current){
  if(!ensure(ticket))return null;const sequence=++reads.current;readController.current?.abort();const controller=new AbortController();readController.current=controller;controllers.current.add(controller);setReading(true);
  try{const result=checkExecutionListing(await json(base,{signal:controller.signal}),ticket.scope);if(active(ticket)&&sequence===reads.current){update(result,ticket);setReadError('');if(result.task_version>task.version)await notifyChanged();}return result;}
  finally{controllers.current.delete(controller);if(active(ticket)&&sequence===reads.current){readController.current=null;setReading(false);}}
 }
 useEffect(()=>{
  const ticket={key:scopeKey,scope,stamp:''};life.current=ticket;dataRef.current=null;stateRef.current=null;reads.current++;writeBusy.current=false;
  setUIScope(scopeKey);setListing(null);setJournal(null);setPreview(null);setConsent(null);setExternalConsent(false);setRetryCharge(false);setBusy(false);setReading(false);setError('');setReadError('');setCacheError('');setNotice('');setProbe(null);setSessionIssue(false);
  try{ticket.stamp=executionSessionStamp(sessionStorage);if(identityError||!executionSessionMatches(scope,sessionStorage,ticket.stamp))throw Error(identityError||'登录会话与原任务账号无法核对。');
   try{const saved=restoreExecutionJournal(JSON.parse(localStorage.getItem(key)||'null'),scope);if(!viewer)writeExecutionJournal(localStorage,key,saved,scope);stateRef.current=saved;setJournal(saved);}catch(e){setCacheError('本机执行草稿无法恢复，已阻止写操作：'+e.message);}
   load(ticket).catch(e=>{if(active(ticket)&&e.name!=='AbortError')setReadError(e.message);});
  }catch(e){setSessionIssue(true);setError(e.message);}
  return()=>{if(life.current===ticket)life.current=null;for(const controller of controllers.current)controller.abort();controllers.current.clear();};
 },[scopeKey]);
 useEffect(()=>{if(!visible||!executionNeedsPolling(data)||sessionIssue)return;const ticket=life.current;let stopped=false,timer;async function poll(){if(stopped||!active(ticket))return;if(!writeBusy.current){try{await load(ticket);}catch(e){if(active(ticket)&&e.name!=='AbortError')setReadError('执行进度暂时无法更新：'+e.message);}}if(!stopped&&active(ticket))timer=setTimeout(poll,4000);}timer=setTimeout(poll,4000);return()=>{stopped=true;clearTimeout(timer);};},[scopeKey,visible,row?.id,row?.status,sessionIssue]);
 useEffect(()=>{if(!visible||!life.current)return;const ticket=life.current;load(ticket).catch(e=>{if(active(ticket)&&e.name!=='AbortError')setReadError(e.message);});},[task.version,planVersion]);
 useEffect(()=>{if(shownPreview&&!executionPreviewCurrent(shownPreview,data,planVersion)){setConsent(null);setExternalConsent(false);}},[shownPreview,data,planVersion]);
 const permissions=executionPermissions(data),writable=visible&&!viewer&&!locked&&!busy&&!cacheError&&!sessionIssue;
 const canNew=writable&&!executionBlockedReason&&!historical&&!pending&&!data?.scope_error,canPreview=canNew&&permissions.preview,canApprove=canNew&&permissions.approve,canRetry=canNew&&permissions.retry,canReview=writable&&!historical&&!pending&&permissions.review,canStop=writable&&!pending&&permissions.stop,canRecover=writable&&permissions.recover;
 const previewCurrent=executionPreviewCurrent(shownPreview,data,planVersion),authorized=previewCurrent&&executionConsentMatches(consent,scope,shownPreview),hasExternal=shownPreview?.steps.some(step=>step.external);
 function edit(value){if(!writable||pending||!stateRef.current)return;try{persist({...stateRef.current,form:{reason:value}});setProbe(null);}catch(e){setError(e.message);}}
 function begin(){const ticket=life.current;if(!ensure(ticket)||writeBusy.current)return null;writeBusy.current=true;reads.current++;readController.current?.abort();setReading(false);setBusy(true);setError('');setNotice('');return ticket;}
 function end(ticket){if(active(ticket)){writeBusy.current=false;setBusy(false);}}
 async function notifyChanged(){try{await onChanged?.();}catch(e){setReadError('操作已保存，原任务页面尚未更新：'+e.message);}}
 async function requestPreview(){if(!canPreview)return;const ticket=begin();if(!ticket)return;const controller=new AbortController();controllers.current.add(controller);setPreview(null);setConsent(null);setExternalConsent(false);try{
  const result=checkExecutionPreview(await json(base+'/preview',{method:'POST',body:'{}',signal:controller.signal}));if(!active(ticket))return;setPreview(result);if(result.task_version!==dataRef.current?.task_version)await load(ticket);
 }catch(e){if(active(ticket))setError('执行预览未完成：'+e.message+' 尚未批准工具调用。');}finally{controllers.current.delete(controller);end(ticket);}}
 async function saved(result,ticket){if(!active(ticket))return;update(checkExecutionListing(result,ticket.scope),ticket);persist({...stateRef.current,pending:null},ticket);setProbe(null);setPreview(null);setConsent(null);setExternalConsent(false);setRetryCharge(false);setNotice('原执行操作与回执已保存。采用结果、人工流程和正式业务验收仍分别核对。');await notifyChanged();}
 async function send(operation,decision){
  if(['approve','retry'].includes(operation)&&executionGate.current){setError(executionGate.current);return;}if(operation==='approve'?!canApprove:operation==='retry'?!canRetry:operation==='review'?!canReview:!canStop)return;const ticket=begin();if(!ticket)return;const controller=new AbortController();controllers.current.add(controller);try{
   const args={scope:ticket.scope,listing:dataRef.current,reason:stateRef.current.form.reason,requestID:crypto.randomUUID()},body=operation==='approve'?executionApproveBody({...args,preview:shownPreview,consent,externalConsent,planVersion}):executionCommandBody({...args,operation,decision,acknowledgePossibleCharge:retryCharge});
   const original=executionPending(ticket.scope,operation,body);persist({...stateRef.current,pending:original},ticket);setProbe(null);setConsent(null);setExternalConsent(false);
   await saved(await json(base+executionRequestPath(original,ticket.scope),{method:'POST',body:JSON.stringify(original.body),signal:controller.signal}),ticket);
  }catch(e){if(active(ticket))setError(e.message+(stateRef.current?.pending?' 原编号与完整请求已保留；工具可能已经处理或计费，请先核对原请求。':''));}finally{controllers.current.delete(controller);end(ticket);}
 }
 async function recover(action='probe',resume=false){if(!canRecover||!pending)return;const ticket=begin();if(!ticket)return;const controller=new AbortController();controllers.current.add(controller);try{
  const original=checkExecutionPending(stateRef.current.pending,ticket.scope);
  if(resume){requireExecution(original.operation);if(!probe||probe.saved||probe.abandoned)throw Error('请先核对原请求，确认尚未保存后再恢复送达。');const permission=executionPermissions(dataRef.current)[original.operation];if(!permission||historical&&['approve','retry','review'].includes(original.operation))throw Error('当前权限或原计划范围不能继续送达，请核对或作废原未保存请求。');await saved(await json(base+executionRequestPath(original,ticket.scope),{method:'POST',body:JSON.stringify(original.body),signal:controller.signal}),ticket);return;}
  const body=executionRecoveryBody(original,ticket.scope,executionRecoveryVersion(probe,dataRef.current),action),result=await json(base+'/requests/recover',{method:'POST',body:JSON.stringify(body),signal:controller.signal});if(!active(ticket))return;
  if(typeof result.saved!=='boolean'||typeof result.abandoned!=='boolean')throw Error('原请求恢复回执不完整，请保留原记录。');setProbe(result);
  if(result.saved||result.abandoned){await load(ticket);if(!active(ticket))return;persist({...stateRef.current,pending:null},ticket);setProbe(null);setPreview(null);setConsent(null);setExternalConsent(false);setNotice(result.saved?'已读取原请求保存结果，没有重复调用工具。':'原未保存请求已明确作废，草稿和执行历史保留；重新执行须重新预览和批准。');await notifyChanged();}
  else setNotice('服务器尚未保存原请求。可继续核对、按原编号送达，或明确作废；不会自动重复调用。');
 }catch(e){if(active(ticket))setError(e.message+' 原执行请求和草稿保留。');}finally{controllers.current.delete(controller);end(ticket);}}
 async function refresh(){if(busy)return;const ticket=life.current;try{await load(ticket);}catch(e){if(active(ticket)&&e.name!=='AbortError')setReadError(e.message);}}
 async function download(){if(busy||sessionIssue)return;const ticket=life.current;if(!ensure(ticket))return;setError('');try{const payload=await json(base+'/export');if(!active(ticket))return;const url=URL.createObjectURL(new Blob([executionExportMarkdown(payload)],{type:'text/markdown;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='原任务只读执行与核查记录.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);setNotice('已导出可读的原执行、真实结果和人工核查记录。');}catch(e){if(active(ticket))setError('导出失败：'+e.message+' 服务器已保存的结果与决定保留。');}}
 const operationLabels={approve:'批准本次只读执行',retry:'明确重试失败步骤',stop:'停止后续执行',review:'保存人工结果核查'};
 return <section className="native-step-execution media-form" aria-label={historical?'原计划执行历史':'本计划只读执行与结果核查'}><div className="section-title"><h5>{historical?'原计划的真实执行与核查记录':'核对计划后批准只读执行'}</h5><Btn disabled={busy||reading||sessionIssue} onClick={refresh}>{reading?'正在读取执行记录…':'刷新执行与核查'}</Btn></div>
 <p>计划是候选。以下批准只用于原任务的已授权只读步骤；真实结果需另行采用，原人工流程和最终业务验收继续独立保存。</p>
 {historical&&<p>此为历史计划。只能回看已保存结果；原请求核对与停止入口按当前账号的独立权限显示。</p>}{viewer&&<p>当前账号只读，可查看真实结果、已保存决定与导出记录。</p>}{locked&&<p>原任务有未保存内容或待核操作，已暂停执行写入；已保存结果仍可查看。</p>}
 {visible&&error&&<p role="alert">{error}</p>}{visible&&readError&&<p role="alert">{readError} 原先读取的回执仍保留。</p>}{visible&&cacheError&&<p role="alert">{cacheError} 请保留本机原恢复记录。</p>}{visible&&notice&&<p role="status">{notice}</p>}
 {!data?<p>{identityError||sessionIssue?'原账号或计划身份尚未核实。':readError?'执行记录尚未读取，可刷新核对。':'正在读取原计划执行记录…'}</p>:<>
 {executionBlockedReason&&<p role="status">{executionBlockedReason} 原执行结果与原请求仍可核对。</p>}{data.scope_error&&<p role="alert">{data.scope_error}。原审批、真实结果和核查历史保留；不能开始新的执行。</p>}
 {pending&&<section aria-label="原执行请求恢复"><h6>原执行操作结果待核对</h6><p>原操作：{operationLabels[pending.operation]}。完整请求与原账号、任务和计划一并保留；刷新或断网不会改成新调用。</p><div className="actions"><Btn disabled={!canRecover} onClick={()=>recover()}>核对原执行请求是否保存</Btn>{probe&&!probe.saved&&!probe.abandoned&&<><Btn disabled={!canRecover||!!executionBlockedReason&&['approve','retry'].includes(pending.operation)||!permissions[pending.operation]||historical&&['approve','retry','review'].includes(pending.operation)} onClick={()=>recover('probe',true)}>按原编号继续送达</Btn><Btn disabled={!canRecover} onClick={()=>recover('abandon')}>作废未保存请求，保留草稿</Btn></>}</div></section>}
 {!viewer&&<label>本次执行或核查理由<textarea aria-label={historical?'历史执行处理理由':'只读执行与结果核查理由'} maxLength={2000} value={form.reason} disabled={!writable||!!pending} onChange={event=>edit(event.target.value)}/></label>}
 {!historical&&<Btn disabled={!canPreview} onClick={requestPreview}>预览全部实际输入与目的地</Btn>}
 {shownPreview&&<section aria-label="完整只读执行批准预览"><h6>本次实际执行内容</h6><p>{shownPreview.notice}</p>{!previewCurrent&&<p role="alert">原任务、计划或条件已有变化。此内容保留供核对，请重新预览后批准。</p>}
 {shownPreview.steps.map(step=><article className="media-session" key={step.index}><h6>第 {step.index+1} 步 · {step.title}</h6><p>{agents[step.agent]||step.agent} · {actions[step.action]||step.action}{step.capability_name?' · '+step.capability_name:''}。</p><p>{step.external?'本步骤完整输入将发送至：'+step.destination:'本步骤由原任务的本机只读处理完成，不外发输入'}。</p><strong>完整具体输入</strong><FieldValue value={step.input} full/></article>)}
 <h6>原任务可核对的资料来源和字段</h6><FieldValue value={shownPreview.evidence} full/>
 <label className="checkbox"><input type="checkbox" aria-label="核对全部只读执行输入和目的地" checked={authorized} disabled={!canApprove||!previewCurrent} onChange={event=>{if(ensure(life.current))setConsent(event.target.checked?executionConsent(scope,shownPreview):null);}}/>我已核对以上全部实际输入、来源和目的地，批准本次只读执行；结果仍需人工核查。</label>
 {hasExternal&&<label className="checkbox"><input type="checkbox" aria-label="明确批准本次具体输入外发" checked={externalConsent&&previewCurrent} disabled={!canApprove||!previewCurrent} onChange={event=>{if(ensure(life.current))setExternalConsent(event.target.checked);}}/>明确允许上述完整输入发往所列登记服务；外部只读调用也可能计费。</label>}
 <Btn primary disabled={!canApprove||!authorized||hasExternal&&!externalConsent||!form.reason.trim()} onClick={()=>send('approve')}>批准本次只读执行</Btn></section>}
 {row?<section aria-label="已保存真实执行结果"><h6>{EXECUTION_STATES[row.status]}</h6>{row.progress&&<div role="status"><FieldValue value={row.progress}/></div>}{row.error&&<div role="alert"><strong>执行尚未完成</strong><FieldValue value={row.error}/></div>}{row.possible_charge&&<p role="alert">原外部调用可能已经处理或计费。停止不能撤回已经发生的调用；重试也可能产生费用。</p>}
 {row.steps.map(step=><StepResult key={step.index} step={step}/>)}
 {(row.evidence||row.source)&&<section><h6>本次执行核对依据</h6><FieldValue value={row.evidence||row.source}/></section>}
 {row.review?<p role="status">已保存人工核查：{decisions[row.review.decision]} · {row.review.reason} · 北京时间 {stamp(row.review.at)}。原决定及后续改判均保留。</p>:<p>尚未保存结果采用决定。技术执行完成不代表业务标准已满足。</p>}
 {canReview&&<div className="actions">{row.status!=='accepted'&&<Btn primary disabled={!!data.scope_error||!form.reason.trim()} onClick={()=>send('review','accepted')}>核对真实结果并采用</Btn>}<Btn disabled={!form.reason.trim()} onClick={()=>send('review','rejected')}>{row.status==='accepted'?'撤回采用，改为拒绝':'拒绝此结果'}</Btn></div>}
 {canRetry&&<section><p>重试只继续后端允许的未完成步骤，原成功回执保留。先核对失败原因及原调用；未知外部处理也可能计费。</p><label className="checkbox"><input type="checkbox" aria-label="确认重试可能再次计费" checked={retryCharge} onChange={event=>{if(ensure(life.current))setRetryCharge(event.target.checked);}}/>已知原调用及本次重试都可能计费，明确重试未完成步骤。</label><Btn disabled={!retryCharge||!form.reason.trim()} onClick={()=>send('retry')}>重试未完成步骤</Btn></section>}
 {permissions.stop&&!viewer&&<Btn disabled={!canStop||!form.reason.trim()} onClick={()=>send('stop')}>停止后续执行，保留原结果</Btn>}
 <details><summary>查看已保存执行与核查历史（{row.history.length}）</summary><FieldValue value={row.history}/></details>
 <Btn disabled={busy||sessionIssue} onClick={download}>导出可读执行与核查记录</Btn>
 </section>:<p>此计划尚未保存真实执行。不会用演示结果或计划文字代替工具回执。</p>}
 </>}
 </section>;
}
