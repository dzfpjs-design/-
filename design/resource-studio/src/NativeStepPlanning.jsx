import React,{useEffect,useRef,useState} from 'react';
import {json as requestJSON} from './runtime-api';
import NativeStepExecution from './NativeStepExecution';
import {PLANNING_STATES,planningScope,planningStorageKey,planningSessionStamp,planningSessionMatches,emptyPlanningForm,checkPlanningListing,checkPlanningPreview,planningConsent,planningConsentMatches,planningPreviewCurrent,planningCanStart,planningNeedsPolling,planningPermissions,planningRecoveryVersion,planningStartBody,planningStopBody,planningPending,checkPlanningPending,restorePlanningJournal,writePlanningJournal,planningRecoveryBody,planningRequestPath} from './native-step-planning-contract.mjs';

const stamp=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未核实';
const roles={system:'系统权限与规划约束',user:'本次任务文字',assistant:'模型上下文',tool:'工具上下文'};
const actions={query_evidence:'核对已有资料',inspect_observation:'核对已保存观测',report:'整理依据与限制',invoke_capability:'使用已授权只读工具'};
const agents={data:'数据管理智能体',business:'业务智能体',perception:'多模感知智能体'};
const optionLabels={temperature:'生成随机度',max_tokens:'最大输出 token',max_completion_tokens:'最大输出 token',top_p:'采样范围',stream:'流式响应',response_format:'响应格式',type:'格式类型',model:'模型',seed:'随机种子',stage:'阶段',message:'说明'};
function ReadableValue({value}){
 if(value===null||value===undefined)return <span>未设置</span>;
 if(typeof value!=='object')return <span style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{typeof value==='boolean'?(value?'是':'否'):String(value)}</span>;
 return <dl>{Object.entries(value).map(([key,item])=><React.Fragment key={key}><dt>{Array.isArray(value)?'第 '+(Number(key)+1)+' 项':optionLabels[key]||key}</dt><dd><ReadableValue value={item}/></dd></React.Fragment>)}</dl>;
}
function Receipt({receipt}){
 if(!receipt)return <p>云调用收据尚未保存，用量与费用待核实。</p>;
 const usage=receipt.usage||{},total=usage.total_tokens??receipt.total_tokens;
 return <div><p>调用模型：{receipt.provider||receipt.model?.provider||'供应方待核实'} · {typeof receipt.model==='string'?receipt.model:receipt.model?.model||receipt.model_name||'模型待核实'}{receipt.model_profile_name?' · '+receipt.model_profile_name:''}。</p>
 <p>{Number.isInteger(total)&&total>=0?'供应方回报用量：'+total+' 个 token。':'供应方未提供完整用量。'}费用待核实；这里不估算准确费用。</p>
 <p>开始：{stamp(receipt.started_at||receipt.requested_at)} · 响应保存：{stamp(receipt.finished_at||receipt.responded_at||receipt.saved_at)}。</p></div>;
}
function PlanRecord({row,historical=false}){
 return <article className="media-session"><h5>{historical?'历史规划':'当前规划'} · {PLANNING_STATES[row.status]}</h5><p>创建时间（北京时间）：{stamp(row.created_at)}。</p>
 {row.progress&&<p>{typeof row.progress==='string'?row.progress:row.progress.message||'后台进度待核实'}</p>}
 {row.status==='response_unknown'&&<p role="alert">供应方可能已经处理并计费，当前无法确认结果。不会自动重发；须先停止这份规划，再重新预览并授权新的请求。停止无法撤销已发生的调用或费用。</p>}
 {row.error&&<div role="alert">规划未完成：<ReadableValue value={row.error}/></div>}
 {row.proposal?<><h6>待核对计划</h6><p style={{whiteSpace:'pre-wrap'}}>{row.proposal.summary||'请逐项核对下列步骤。'}</p><ol>{row.proposal.steps.map((step,index)=><li key={index}><strong>{step.title}</strong><p>{agents[step.agent]||'智能体身份待核实'} · {actions[step.action]||'尚未支持的步骤'}{step.capability_name?' · '+step.capability_name:''}</p></li>)}</ol><p>这份计划是候选；实际执行和结果核查以独立执行区保存的真实回执为准。生成计划本身不会完成工具调用、推进人工步骤或验收任务。</p></>:<p>尚无已保存的计划正文。{row.has_saved_response?'已有原始模型响应，计划是否可用仍待核对。':'不能据此认定模型已经返回有效计划。'}</p>}
 <Receipt receipt={row.model_receipt}/>{row.status==='cancelled'&&<p>已停止后续规划，原计划与调用历史保留；已发生的云调用不能由此撤回。</p>}{row.status==='superseded'&&<p>这份旧计划已由后续计划替代，保留供追溯；不会继续规划或执行。</p>}</article>;
}
function HistoricalExecution(props){const[open,setOpen]=useState(false);return <details onToggle={event=>{if(event.target===event.currentTarget)setOpen(event.currentTarget.open);}}><summary>查看此计划已保存的真实执行与核查</summary>{open&&<NativeStepExecution {...props} historical/>}</details>;}

export default function NativeStepPlanning({task,account,Btn,locked,onChanged,executionBlockedReason=''}){
 let scope,identityError='';try{scope=planningScope(account,task.id);}catch(e){identityError=e.message;scope={workspace_id:account?.workspace_id||'',user_id:account?.user_id||'',username:account?.username||'',role:account?.role||'',task_id:task?.id||''};}
 const scopeKey=JSON.stringify(scope),storageKey=planningStorageKey(scope),base='/workspace/tasks/'+encodeURIComponent(task.id)+'/intelligent-plan',readOnly=account.role==='viewer';
 const [uiScope,setUIScope]=useState(null),[listing,setListing]=useState(null),[journal,setJournal]=useState(null),[preview,setPreview]=useState(null),[consent,setConsent]=useState(null),[busy,setBusy]=useState(false),[reading,setReading]=useState(false),[error,setError]=useState(''),[readError,setReadError]=useState(''),[cacheError,setCacheError]=useState(''),[notice,setNotice]=useState(''),[probe,setProbe]=useState(null),[sessionIssue,setSessionIssue]=useState(false);
 const life=useRef(null),renderScope=useRef(scopeKey),stateRef=useRef(null),listingRef=useRef(null),writeBusy=useRef(false),reads=useRef(0),readController=useRef(null),controllers=useRef(new Set());renderScope.current=scopeKey;
 const executionGate=useRef(executionBlockedReason);executionGate.current=executionBlockedReason;
 function requireExecution(operation){if(operation==='start'&&executionGate.current)throw Error(executionGate.current+' 原规划请求与人稿保留，请先核对当前执行条件。');}
 function json(path,options){if(options?.method==='POST'&&path===base)requireExecution('start');return requestJSON(path,options);}
 const visible=uiScope===scopeKey,data=visible?listing:null,state=visible?journal:null,form=state?.form||emptyPlanningForm(),pending=state?.pending||null,shownPreview=visible?preview:null;
 function active(ticket){try{return life.current===ticket&&renderScope.current===ticket?.key&&planningSessionMatches(ticket.scope,sessionStorage,ticket.stamp);}catch{return false;}}
 function ensure(ticket){if(active(ticket))return true;if(life.current===ticket&&renderScope.current===ticket?.key){setSessionIssue(true);setError('当前账号或登录会话已经变化，原请求保留。请重新进入任务核对会话后再操作。');}return false;}
 function persist(next,ticket=life.current){if(!ensure(ticket))throw Error('会话已经变化，原恢复记录保留。');try{writePlanningJournal(localStorage,storageKey,next,ticket.scope,stateRef.current?.pending);}catch(e){setCacheError('本机草稿或原请求未能保存，已阻止操作：'+e.message);throw e;}stateRef.current=next;setJournal(next);return next;}
 function updateListing(result,ticket){if(!active(ticket))return;checkPlanningListing(result);const previous=listingRef.current;if(previous&&previous.task_version>result.task_version)return;listingRef.current=result;setListing(result);}
 async function load(ticket=life.current){
  if(!ensure(ticket))return null;const sequence=++reads.current;readController.current?.abort();const controller=new AbortController();readController.current=controller;controllers.current.add(controller);setReading(true);
  try{const result=checkPlanningListing(await json(base,{signal:controller.signal}));if(active(ticket)&&sequence===reads.current){updateListing(result,ticket);setReadError('');}return result;}
  finally{controllers.current.delete(controller);if(active(ticket)&&sequence===reads.current){readController.current=null;setReading(false);}}
 }
 useEffect(()=>{
  const ticket={key:scopeKey,scope,stamp:''};life.current=ticket;reads.current++;listingRef.current=null;stateRef.current=null;writeBusy.current=false;
  setUIScope(scopeKey);setListing(null);setJournal(null);setPreview(null);setConsent(null);setBusy(false);setReading(false);setError('');setReadError('');setCacheError('');setNotice('');setProbe(null);setSessionIssue(false);
  try{ticket.stamp=planningSessionStamp(sessionStorage);if(identityError||!planningSessionMatches(scope,sessionStorage,ticket.stamp))throw Error(identityError||'当前登录会话与任务账号不一致，请重新进入任务。');
   if(!readOnly){try{const saved=restorePlanningJournal(JSON.parse(localStorage.getItem(storageKey)||'null'),scope);writePlanningJournal(localStorage,storageKey,saved,scope);stateRef.current=saved;setJournal(saved);}catch(e){setCacheError('本机计划草稿无法保存或恢复，已阻止写操作：'+e.message);}}
   load(ticket).catch(e=>{if(active(ticket)&&e.name!=='AbortError')setReadError(e.message);});
  }catch(e){setSessionIssue(true);setError(e.message);}
  return()=>{if(life.current===ticket)life.current=null;for(const controller of controllers.current)controller.abort();controllers.current.clear();};
 },[scopeKey]);
 useEffect(()=>{if(!visible||!planningNeedsPolling(data)||sessionIssue)return;const ticket=life.current;let stopped=false,timer;
  async function poll(){if(stopped||!active(ticket))return;if(!writeBusy.current){try{await load(ticket);}catch(e){if(active(ticket)&&e.name!=='AbortError')setReadError('规划进度暂时无法更新：'+e.message);}}if(!stopped&&active(ticket))timer=setTimeout(poll,4000);}
  timer=setTimeout(poll,4000);return()=>{stopped=true;clearTimeout(timer);};
 },[scopeKey,visible,data?.current?.id,data?.current?.status,sessionIssue]);
 useEffect(()=>{if(!visible||!life.current)return;const ticket=life.current;load(ticket).catch(e=>{if(active(ticket)&&e.name!=='AbortError')setReadError(e.message);});},[task.version]);
 useEffect(()=>{if(shownPreview&&!planningPreviewCurrent(shownPreview,data,form.model_profile_id))setConsent(null);},[shownPreview,data,form.model_profile_id]);
 const permissions=planningPermissions(data),writable=visible&&!readOnly&&(permissions.manage||permissions.stop||permissions.recover)&&!locked&&!busy&&!cacheError&&!sessionIssue,canNew=writable&&!executionBlockedReason&&permissions.manage&&!pending&&!task.native_execution_waiting&&planningCanStart(data),canStop=writable&&permissions.stop&&!pending&&!!data?.current&&!['cancelled','superseded'].includes(data.current.status),canRecover=writable&&permissions.recover;
 const previewCurrent=planningPreviewCurrent(shownPreview,data,form.model_profile_id),modelAvailable=!!data?.models.some(model=>model.id===form.model_profile_id),authorizationCurrent=previewCurrent&&planningConsentMatches(consent,scope,shownPreview);
 function edit(patch){if(!writable||pending||!stateRef.current||!ensure(life.current))return;try{persist({...stateRef.current,form:{...stateRef.current.form,...patch}});if(Object.hasOwn(patch,'model_profile_id')){setPreview(null);setConsent(null);}setProbe(null);}catch(e){setError(e.message);}}
 function begin(){const ticket=life.current;if(!ensure(ticket)||writeBusy.current)return null;writeBusy.current=true;reads.current++;readController.current?.abort();setReading(false);setBusy(true);setError('');setNotice('');return ticket;}
 function end(ticket){if(active(ticket)){writeBusy.current=false;setBusy(false);}}
 async function requestPreview(){if(!canNew||!modelAvailable)return;const ticket=begin();if(!ticket)return;setPreview(null);setConsent(null);const controller=new AbortController();controllers.current.add(controller);try{
  const result=checkPlanningPreview(await json(base+'/preview',{method:'POST',body:JSON.stringify({model_profile_id:stateRef.current.form.model_profile_id}),signal:controller.signal}));if(!active(ticket))return;setPreview(result);if(result.task_version!==listingRef.current?.task_version)await load(ticket);
 }catch(e){if(active(ticket))setError('发送预览未完成：'+e.message+' 尚未授权云调用。');}finally{controllers.current.delete(controller);end(ticket);}}
 async function savedResult(result,ticket){
  if(!active(ticket))return;updateListing(checkPlanningListing(result),ticket);setPreview(null);setConsent(null);
  // Keep the pending journal if local acknowledgement fails, even after server success.
  persist({...stateRef.current,pending:null},ticket);setProbe(null);setNotice('规划操作已保存。计划仅供核对，尚未执行任何业务步骤或工具。');await onChanged?.();
 }
 async function send(operation){if(operation==='start'&&executionGate.current){setError(executionGate.current);return;}if(operation==='start'?!canNew:!canStop)return;const ticket=begin();if(!ticket)return;const controller=new AbortController();controllers.current.add(controller);try{
  const args={scope:ticket.scope,listing:listingRef.current,form:stateRef.current.form,requestID:crypto.randomUUID()},body=operation==='start'?planningStartBody({...args,preview,consent}):planningStopBody(args),original=planningPending(ticket.scope,operation,body,operation==='stop'?listingRef.current.current.id:null);
  persist({...stateRef.current,pending:original},ticket);setProbe(null);setConsent(null);
  const result=await json(base+planningRequestPath(original,ticket.scope),{method:'POST',body:JSON.stringify(original.body),signal:controller.signal});await savedResult(result,ticket);
 }catch(e){if(active(ticket))setError(e.message+(stateRef.current?.pending?' 原编号及完整请求已保留。可能已经计费，请先核对原请求；不会自动重发。':''));}finally{controllers.current.delete(controller);end(ticket);}}
 async function recover(action='probe',resume=false){
  if(!canRecover||!pending)return;const ticket=begin();if(!ticket)return;const controller=new AbortController();controllers.current.add(controller);try{
   const original=checkPlanningPending(stateRef.current.pending,ticket.scope);
   if(resume){requireExecution(original.operation);if(original.operation==='start'&&(!listingRef.current?.can_manage||listingRef.current?.current?.status==='response_unknown'||[listingRef.current?.current,...(listingRef.current?.history||[])].some(row=>row?.request_id===original.body.request_id)))throw Error('当前不能恢复发送原调用，请先核对原请求；结果未知时须先停止原规划。');
    await savedResult(await json(base+planningRequestPath(original,ticket.scope),{method:'POST',body:JSON.stringify(original.body),signal:controller.signal}),ticket);return;
   }
   const body=planningRecoveryBody(original,ticket.scope,planningRecoveryVersion(probe,listingRef.current),action),result=await json(base+'/requests/recover',{method:'POST',body:JSON.stringify(body),signal:controller.signal});if(!active(ticket))return;
   if(typeof result.saved!=='boolean'||typeof result.abandoned!=='boolean')throw Error('原请求核对回执不完整，请保留原请求。');setProbe(result);
   if(result.saved||result.abandoned){await load(ticket);if(!active(ticket))return;persist({...stateRef.current,pending:null},ticket);setProbe(null);setConsent(null);setPreview(null);setNotice(result.saved?'原请求已经保存，已读取原规划历史；没有新增云调用。':'原未保存请求已明确作废，草稿与历史保留；重新生成需要再次预览并授权。');await onChanged?.();}
   else setNotice('尚未核实原请求保存。可继续核对，或明确作废未保存请求；恢复只发送原编号和完整原内容，不能改为新请求。');
  }catch(e){if(active(ticket))setError(e.message+(stateRef.current?.pending?' 原请求与草稿保留，不会自动重发云调用。':' 已保存的计划与草稿保留，任务页面刷新尚未完成。'));}finally{controllers.current.delete(controller);end(ticket);}
 }
 async function refresh(){if(busy)return;const ticket=life.current;try{await load(ticket);}catch(e){if(active(ticket)&&e.name!=='AbortError')setReadError(e.message);}}
 const serverKnowsPending=pending?.operation==='start'&&[data?.current,...(data?.history||[])].some(row=>row?.request_id===pending.body.request_id);
 return <section className="native-step-planning media-form" aria-label="原任务智能计划"><div className="section-title"><h4>当前任务的智能计划</h4><Btn disabled={busy||reading||sessionIssue} onClick={refresh}>{reading?'正在读取规划记录…':'刷新计划与历史'}</Btn></div>
 <p>在当前人工等待步骤核对实际发送文字，授权一次云调用，并保存待核对计划。生成阶段只形成候选。随后须核对具体输入和目的地、批准本次只读执行，并另行核对采用真实结果；原人工流程和目标验收分别保存。</p>
 {locked&&<p>当前任务有未保存内容或其他操作待核，已暂停计划写入。仍可刷新并查看历史。</p>}{readOnly&&<p>当前账号只读，可查看计划与调用历史。</p>}
 {visible&&error&&<p role="alert">{error}</p>}{visible&&readError&&<p role="alert">{readError} 已读取的计划与历史保留。</p>}{visible&&cacheError&&<p role="alert">{cacheError} 请保留当前浏览器的恢复记录；未保存的输入不会向服务器提交。</p>}{visible&&notice&&<p role="status">{notice}</p>}
 {!data?<p>{identityError||sessionIssue?'账号或会话尚未核实，暂不能读取或提交计划。':readError?'计划记录尚未读取，可刷新后继续。':'正在读取当前任务的计划与历史…'}</p>:<>
 {executionBlockedReason&&<p role="status">{executionBlockedReason} 原规划与原请求仍可核对。</p>}{data.scope_error&&<p role="alert">{data.scope_error}。已保存计划与调用历史保留，不能开始新规划。</p>}{task.native_execution_waiting&&<p role="status">原计划已批准的只读执行尚待采用、拒绝或停止，请先核对下方真实执行记录，再生成下一份计划。</p>}
 {pending&&<section aria-label="恢复原计划请求"><h5>原计划操作结果待核对</h5><p>已在本机保存原账号、任务、请求编号和完整内容。刷新或重新进入后可继续核对。授权仅用于原请求；网络错误不代表没有调用或计费。</p><p>原操作：{pending.operation==='start'?'生成计划':'停止后续规划'}。</p><details><summary>查看原请求核对凭据</summary><p>原请求编号：{pending.body.request_id}</p></details>
 <div className="actions"><Btn disabled={!canRecover} onClick={()=>recover()}>核对原请求是否已保存</Btn><Btn disabled={!canRecover||!!executionBlockedReason&&pending.operation==='start'||serverKnowsPending||pending.operation==='start'&&(!permissions.manage||data.current?.status==='response_unknown')} onClick={()=>recover('probe',true)}>按原编号恢复原请求</Btn>{probe&&!probe.saved&&!probe.abandoned&&<Btn disabled={!canRecover} onClick={()=>recover('abandon')}>作废未保存请求，保留草稿</Btn>}</div></section>}
 {!readOnly&&<><fieldset disabled={!writable||!!pending} style={{border:0,padding:0}}><label>用于本次规划的云模型<select aria-label="智能计划云模型" value={form.model_profile_id} onChange={event=>edit({model_profile_id:event.target.value})}><option value="">请选择已配置的云模型</option>{data.models.map(model=><option key={model.id} value={model.id}>{model.name} · {model.provider} · {model.model}</option>)}</select></label>
 {!data.models.length&&<p>尚无可用的云模型。请由管理员配置有效模型后刷新，当前不会使用演示模型替代。</p>}
 {form.model_profile_id&&!modelAvailable&&<p>草稿中的模型当前不可用，请重新选择可用模型再预览。</p>}
 <label>本次生成或停止的理由<textarea aria-label="智能计划操作理由" maxLength={2000} value={form.reason} onChange={event=>edit({reason:event.target.value})}/></label><p>操作理由用于保存本次决策，不加入下方实际发送文字；改变模型或任务版本后须重新预览并授权。</p></fieldset>
 <div className="actions"><Btn disabled={!canNew||!modelAvailable} onClick={requestPreview}>预览本次完整发送文字</Btn>{data.current&&!['cancelled','superseded'].includes(data.current.status)&&<Btn disabled={!canStop||!form.reason.trim()} onClick={()=>send('stop')}>停止后续规划，保留历史</Btn>}</div>
 {shownPreview&&<section aria-label="本次云调用完整预览"><h5>实际发送给云模型的完整内容</h5><p>模型：{shownPreview.model.name} · {shownPreview.model.provider} · {shownPreview.model.model}。输入 {shownPreview.input_bytes} 字节。</p><p>{shownPreview.notice}</p>
 {!previewCurrent&&<p role="alert">当前任务或模型已有变化，此预览仅供查看，请重新预览后授权。</p>}
 {shownPreview.messages.map((message,index)=><article key={index}><h6>第 {index+1} 段 · {roles[message.role]||message.role}</h6><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{message.content}</p>{Object.entries(message).filter(([key])=>!['role','content'].includes(key)).map(([key,value])=><div key={key}><strong>{key}</strong><ReadableValue value={value}/></div>)}</article>)}
 <h6>实际请求选项</h6>{Object.keys(shownPreview.options).length?<ReadableValue value={shownPreview.options}/>:<p>没有额外请求选项。</p>}
 <h6>本次计划可引用的工具</h6>{shownPreview.tools.length?<ul>{shownPreview.tools.map(tool=><li key={tool.id}>{tool.name} · {tool.external?'外部工具':'本地只读工具'}。仅可写入待核对计划，本阶段不调用。</li>)}</ul>:<p>没有可引用工具。</p>}
 <label className="checkbox"><input type="checkbox" aria-label="授权本次智能计划云调用" disabled={!canNew||!previewCurrent} checked={authorizationCurrent} onChange={event=>{if(!ensure(life.current))return;setConsent(event.target.checked?planningConsent(scope,shownPreview):null);}}/>我已核对以上完整文字及选项，授权此内容的一次云调用；供应方可能计费，生成结果仍须人工核对。</label>
 <Btn primary disabled={!canNew||!authorizationCurrent||!form.reason.trim()} onClick={()=>send('start')}>授权一次云调用并保存待核对计划</Btn></section>}
 </>}
 {data.current?<><PlanRecord row={data.current}/>{data.current.proposal&&<NativeStepExecution key={data.current.id} {...{task,account,Btn,onChanged,executionBlockedReason}} locked={locked||busy||!!pending} planID={data.current.id} planVersion={data.current.version}/>}</>:<p>当前尚未保存智能计划。</p>}
 <details><summary>查看原任务的规划历史（{data.history.length}）</summary>{data.history.length?data.history.map(row=><div key={row.id}><PlanRecord row={row} historical/>{row.proposal&&<HistoricalExecution {...{task,account,Btn,onChanged,executionBlockedReason}} locked={locked||busy||!!pending} planID={row.id} planVersion={row.version}/>}</div>):<p>暂没有历史规划。停止后的原计划及调用收据会保留在这里。</p>}{data.history_truncated&&<p>当前仅显示最近一部分规划历史，其余原记录仍保存在服务器；不能据此认定全部记录已核对。</p>}</details>
 </>}
 </section>;
}
