import React,{useCallback,useEffect,useRef,useState} from 'react';
import {createGraphTaskController,graphTaskScopeKey,inspectHumanSchema,humanValues,humanNullToggle,graphHumanCanComplete} from './graph-task-controller.mjs';
import RegionBindingEvidence from './RegionBindingEvidence';
import GraphMessageStatus from './GraphMessageStatus';
import GraphGoalGateV133 from './GraphGoalGateV133';
import GraphSupplementBasis from './GraphSupplementBasis.jsx';
import GraphReasoningEvaluationStatusV335 from './GraphReasoningEvaluationStatusV335.jsx';
import {isManagedApprovalTask} from './layered-approval-v175.mjs';
import {inspectGraphGoalGate} from './graph-goal-gate-v133.mjs';
import {inspectSignalGraphTask} from './graph-signal-routing-v134.mjs';
import {numericDisplay,isTypedNumber} from './typed-numbers-v132.mjs';
import BusinessReasoningResult from './BusinessReasoningResult';
import BusinessDecisionReviewV284,{DecisionPlanSummaryV285} from './BusinessDecisionReviewV284';
import {BUSINESS_DECISION_REVIEW_RESULT,DECISION_PLAN_V285,reviewDecisionBinding} from './business-decision-review-v284.mjs';
import TechnicalArchiveResultV220 from './TechnicalArchiveResultV220.jsx';
import {TECHNICAL_ARCHIVE_RESULT} from './technical-archive-result-v220.mjs';
import TaskOriginalsQualityReportV221 from './TaskOriginalsQualityReportV221.jsx';
import {ORIGINALS_REPORT_V219} from './task-originals-quality-report-v221.mjs';
import {BUSINESS_REASONING_RESULT} from './business-reasoning-result.mjs';
import TaskArchivesV198 from './TaskArchivesV198';
import TaskWorkOrdersV198 from './TaskWorkOrdersV198';
import TaskEvidenceConsentV199 from './TaskEvidenceConsentV199';
import TaskSimulationReviewV202 from './TaskSimulationReviewV202';

const phases={queued:'已排队',running:'正在处理',succeeded:'结果已保存',completed:'处理完成',failed:'处理失败',blocked:'处理条件需恢复',unknown:'原调用结果待核对',cancelled:'已取消后续处理',retired_for_new_evaluation:'旧资料步骤已停用，原记录保留',waiting_guard:'等待条件具备',waiting_provenance:'等待原资料检查或上一步结果保存完成',waiting_evidence_consent:'等待为本次摘要明确授权所选模型；尚未调用模型',waiting_document_packet_v207:'等待原资料包接收完成或为所选模型授权；尚未调用模型',waiting_service:'补充观测已安排，等待资料处理和接收完成',waiting_event:'等待资料或事件',waiting_join:'等待并行分支汇合',ready:'等待处理',timed_out:'等待已到期限',late_result_preserved:'执行条件变化后返回的原结果已保留，未推进流程'};
const states={running:'处理中',waiting:'等待条件、资料或负责人',succeeded:'完整流程已完成',cancelled:'任务已取消',stopped:'流程已停止',failed:'流程未完成',blocked:'完成标准尚未满足'};
const scalar=value=>value===null?'明确为空':typeof value==='boolean'?(value?'是':'否'):typeof value==='number'||isTypedNumber(value)?numericDisplay(value):String(value);
const nodeInfo=(task,item)=>task.graph_node_labels?.[item.activation_id||item.id]||task.graph_node_labels?.[item.node_id]||{};
const stepFor=(task,item)=>task.business?.flow?.steps?.find(step=>step.id===(item.source_step_id||nodeInfo(task,item).source_step_id||item.node_id));
const labelFor=(task,item,fallback)=>stepFor(task,item)?.name||item.name||item.display_name||nodeInfo(task,item).name||nodeInfo(task,item).display_name||fallback;
const exclusiveAmbiguity=value=>typeof value==='string'&&value.startsWith('exclusive_reference_ambiguous:');
const ambiguityMessage='同一项资料出现多个分支结果，暂不能确定应采用哪一份。请核查任务已保存结果和依据；流程尚未完成，不会自动选择其中一份。';
const publicReason=value=>exclusiveAmbiguity(value)?ambiguityMessage:typeof value==='string'&&value.startsWith('guarded_port_projection_')?(value.includes('_missing:')?'上一步所需资料尚未保存，流程会等待真实结果。':value.includes('_null:')?'上一步明确缺少本步骤所需资料；请补充资料后再继续。':value.includes('_ambiguous:')?'本步骤存在多份来源结果，需先核对；系统未自动选择。':'上一步资料与本步骤要求不一致；原结果已保留，未继续派发。'):value;
function blockedReason(value){
 const reason=publicReason(value);if(typeof reason!=='string'||!reason.trim())return '当前步骤未能完成，需核对原执行条件和已保存的失败记录。';
 if(/InstanceRelease[\s\S]*snapshot|has no attribute ['"]snapshot['"]/.test(reason))return '原件核验未能读取任务保存的业务版本，需要维护人员核对后再决定后续处理。';
 if(/^\s*[\[{]/.test(reason)||/(?:^|\s|[:：])[\[{]\s*["']/.test(reason)||/Traceback|\b(?:AttributeError|TypeError|KeyError|RuntimeError)\b/.test(reason))return '后台处理遇到技术问题，需要维护人员核对原失败记录；当前未取得本步骤结果。';
 return reason.replace(/\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b/gi,'相应记录').replace(/\b[a-f0-9]{64}\b/gi,'原校验摘要');
}
function SavedValues({value,schema={},depth=0,inputs,account,taskId,activationId,readerId,workspaceId,requestJSON,readBinary}){
 if(depth===0&&value?.schema===TECHNICAL_ARCHIVE_RESULT)return <TechnicalArchiveResultV220 {...{value,account,taskId,requestJSON,readBinary}}/>;
 if(depth===0&&value?.schema===BUSINESS_REASONING_RESULT)return <BusinessReasoningResult value={value} inputs={inputs}/>;
 if(depth===0&&value?.schema===BUSINESS_DECISION_REVIEW_RESULT)return <BusinessDecisionReviewV284 {...{value,account,taskId,activationId,readerId,workspaceId,requestJSON}}/>;
 if(depth===0&&value?.schema===DECISION_PLAN_V285)return <DecisionPlanSummaryV285 value={value} taskId={taskId}/>;
 if(value===undefined)return <p>尚无保存结果。</p>;
 if(value===null||typeof value!=='object'||isTypedNumber(value))return <span>{scalar(value)}</span>;
 if(depth>=4)return <p>已保存{Array.isArray(value)?value.length+'项':'结构化'}结果。</p>;
 // Wait/control outputs may legitimately have no declared schema. This is a
 // read-only display fallback, independent of the strict human form validator.
 const spec=schema!==null&&typeof schema==='object'&&!Array.isArray(schema)?schema:{};
 const entries=Object.entries(value).filter(([key])=>!!spec.properties?.[key]?.title||!['schema','id','request_id','activation_id','node_id','actor_id','job_id','invocation_id','body_hash','graph_hash','state_hash'].includes(key)&&!key.endsWith('_hash')&&!key.endsWith('_id'));
 return <dl className="ability-values">{entries.map(([key,item],index)=><React.Fragment key={key}><dt>{Array.isArray(value)?'记录 '+(index+1):spec.properties?.[key]?.title||({'ok':'确认结果','note':'核对说明','value':'处理结果','reason':'处理原因','name':'名称','count':'数量','sample_count':'有效对象数','missing_count':'缺少字段的对象数','field':'业务字段','operation':'统计方式','warnings':'需注意事项','summary':'处理说明','attributes':'候选字段'})[key]||key.replaceAll('_',' ')}</dt><dd><SavedValues value={item} schema={Array.isArray(value)?spec.items:spec.properties?.[key]} depth={depth+1}/></dd></React.Fragment>)}</dl>;
}
function DraftValues({schema,draft,values}){
 if(typeof draft==='string')return <p style={{whiteSpace:'pre-wrap'}}>{draft}</p>;
 const model=inspectHumanSchema(schema,{typedNumbers:true});let rows=values;
 try{rows=rows||humanValues(schema,draft,{typedNumbers:true});}catch{return <><p>这份原草稿不能自动填入当前表单；原内容仍保留。</p><SavedValues value={draft} schema={schema}/></>;}
 if(!model.supported)return <p>原草稿已保留；当前表单尚不能编辑此结构。</p>;
 return <dl>{model.fields.map(field=>{const item=rows[field.key];let value='未回答';if(item?.mode==='null')value='明确为空';else if(item?.mode==='value')value=field.spec.enum?scalar(field.spec.enum[Number(item.raw)]):field.type==='boolean'?(item.raw==='true'?'是':item.raw==='false'?'否':'未回答'):item.raw||'已填写空文字';return <React.Fragment key={field.key}><dt>{field.label}</dt><dd style={{whiteSpace:'pre-wrap'}}>{value}</dd></React.Fragment>;})}</dl>;
}
function HumanField({field,value,disabled,onChange}){
 const id=React.useId(),select=(raw)=>onChange({mode:raw===''?'unset':'value',raw}),setRaw=raw=>onChange({mode:'value',raw}),longText=field.type==='string'&&!field.spec.format&&(field.key.includes('note')||/说明|备注|结果/.test(field.label)||field.spec.maxLength>160);
 return <div className="media-form"><label htmlFor={id}><strong>{field.label}{field.required?'（必填）':'（可选）'}</strong></label>{field.spec.description&&<p>{field.spec.description}</p>}
  {field.spec.enum?<select id={id} disabled={disabled} value={value?.mode==='value'?value.raw:''} onChange={event=>select(event.target.value)}><option value="">请选择</option>{field.spec.enum.map((item,index)=><option key={index} value={String(index)}>{item===''?'空文字':scalar(item)}</option>)}</select>:
   field.type==='boolean'?<select id={id} disabled={disabled} value={value?.mode==='value'?value.raw:''} onChange={event=>select(event.target.value)}><option value="">尚未回答</option><option value="true">是</option><option value="false">否</option></select>:
   longText?<textarea id={id} rows={3} disabled={disabled||value?.mode==='null'} value={value?.mode==='value'?value.raw:''} onChange={event=>setRaw(event.target.value)} maxLength={field.spec.maxLength}/>:
   <input id={id} disabled={disabled||value?.mode==='null'} type={field.spec.format==='date'?'date':'text'} inputMode={['number','integer'].includes(field.type)?'decimal':undefined} value={value?.mode==='value'?value.raw:''} onChange={event=>setRaw(event.target.value)} maxLength={field.spec.maxLength} aria-describedby={field.type==='number'||field.type==='integer'?id+'-help':undefined}/>}
  {['number','integer'].includes(field.type)&&<small id={id+'-help'}>{field.type==='integer'?'填写整数':'填写数值'}；留空不会自动作为零。</small>}
  <div className="actions">{!field.required&&<button type="button" disabled={disabled} onClick={()=>onChange({mode:'unset',raw:''})}>留待填写</button>}{field.nullable&&!field.spec.enum&&<button type="button" disabled={disabled} onClick={()=>onChange(humanNullToggle(value))}>{value?.mode==='null'?'改为填写值（当前明确为空）':'明确填写为空'}</button>}</div>
 </div>;
}
function RequiredInputs({items,onEvidence,task,scope}){
 if(!Array.isArray(items)||!items.length)return null;
 return <section className="media-form" aria-label="本步骤所需输入"><h5>本步骤所需输入</h5>{items.map((item,index)=>{
  const ready=item.available===true&&(!Object.hasOwn(item,'availability_code')||item.availability_code==='ready'),ambiguous=item.availability_code==='ambiguous',source=typeof item.source_name==='string'&&item.source_name?item.source_name:({input:'流程输入',output:'前序步骤结果',exclusive_output:'互斥分支结果（来源仍须核对）'})[item.source]||'来源暂不可核对';
return <article key={index} data-required-input-ordinal={Number.isSafeInteger(item.ordinal)?item.ordinal:index}><strong style={{whiteSpace:'pre-wrap'}}>{item.label}</strong><p style={{whiteSpace:'pre-wrap'}}>资料项 {(Number.isSafeInteger(item.ordinal)?item.ordinal:index)+1} · 来源：{source}</p>{ready&&item.value?.schema===ORIGINALS_REPORT_V219?<TaskOriginalsQualityReportV221 report={item.value} task={task} scope={scope} source={item}/>:ready?<div style={{whiteSpace:'pre-wrap'}}><SavedValues value={{input:item.value}} schema={{type:'object',properties:{input:{...item.schema,title:item.label}},required:['input']}}/></div>:<><p role="status">{ambiguous?'这一输入出现多个分支结果，须先核查来源；可以保存草稿，暂不能完成待办。':'这一输入尚未产生或状态暂不能核对；可以先保存草稿，暂不能完成待办。'}</p>{ambiguous&&<button type="button" onClick={onEvidence}>查看这项输入的任务依据</button>}</>}</article>;
 })}</section>;
}
function TaskNotices({items,taskSimulation}){
 const [visible,setVisible]=useState(20);
 if(!Array.isArray(items))return null;
 return <section className="media-form" aria-label="本账号的站内通知"><h3>站内通知</h3><p>任务内协作通知，内容随任务依据共享；收件人会收到站内提醒。</p><p>以下为服务器提供给本账号的站内收件记录。仅送达站内，不等于收件人已读；本页不生成阅读确认，也不据此认定整个流程完成。</p>{!items.length&&<p>当前没有发送给本账号的站内通知。</p>}{items.slice(0,visible).map(item=><article className="media-form" key={item.id}><h4>发送人：{item.sender_name===null?'暂不可核对':item.sender_name}</h4><p>送达时间：<time dateTime={item.delivered_at}>{new Date(item.delivered_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</time>（北京时间）</p><p>已送达站内 · 阅读状态未跟踪</p>{(taskSimulation||item.simulation_only)&&<p role="note">仅工程模拟（SIM），非人工业务审查；通知送达不证明业务结论或生产权限。</p>}<p className="bfr-source-text" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{item.message}</p></article>)}{visible<items.length&&<button type="button" onClick={()=>setVisible(count=>count+20)}>继续显示后20条站内通知</button>}</section>;
}
export default function GraphTaskPanel({task,api,actor,workspace,onSaved,onRegisterGuard,executionBlockedReason='',artifactRequest}){
 const actorID=typeof actor==='string'?actor:actor?.user_id||actor?.id,workspaceID=typeof workspace==='string'?workspace:workspace?.workspace_id||workspace?.id;
 const scope={actor_id:actorID,workspace_id:workspaceID,task_id:task.id},scopeKey=graphTaskScopeKey(scope),[,render]=useState(0),[notice,setNotice]=useState(''),[cancelOpen,setCancelOpen]=useState(false);
 const latest=useRef();latest.current={scopeKey,onSaved};const holder=useRef(),evidenceRoot=useRef(null);
 const recordGuards=useRef({key:scopeKey,values:new Map()});
 if(recordGuards.current.key!==scopeKey)recordGuards.current={key:scopeKey,values:new Map()};
 const registerRecordGuard=useCallback((name,guard,previous)=>{
  if(recordGuards.current.key!==scopeKey)return;
  const guards=recordGuards.current.values;
  if(typeof guard==='function')guards.set(name,guard);
  else if(!previous||guards.get(name)===previous)guards.delete(name);
 },[scopeKey]);
 const registerArchiveGuard=useCallback((guard,previous)=>registerRecordGuard('archives',guard,previous),[registerRecordGuard]);
 const registerWorkOrderGuard=useCallback((guard,previous)=>registerRecordGuard('work_orders',guard,previous),[registerRecordGuard]);
 const registerConsentGuard=useCallback((guard,previous)=>registerRecordGuard('evidence_consent',guard,previous),[registerRecordGuard]);
 if(holder.current?.key!==scopeKey){holder.current?.controller.dispose();holder.current={key:scopeKey,controller:createGraphTaskController({api,scope,typedNumbers:true})};}
 const controller=holder.current.controller;
 useEffect(()=>{
  controller.disposed=false;
  controller.onChange=()=>render(value=>value+1);
  controller.onSaved=async saved=>{if(latest.current.scopeKey!==scopeKey)return false;const accepted=await latest.current.onSaved?.(saved);return latest.current.scopeKey===scopeKey&&accepted!==false;};
  const guard=()=>{try{return controller.guard()&&[...recordGuards.current.values.values()].every(check=>check()===true);}catch{return false;}};onRegisterGuard?.(guard);
  const unload=event=>{if(!guard()){event.preventDefault();event.returnValue='';}};
  const storage=event=>{if(event.key?.startsWith(controller.prefix)){try{controller.pending=controller.currentPending();}catch{controller.denied=true;controller.error='另一窗口的原请求无法核对，请保留恢复记录。';}controller.notify();}};
  const focus=()=>controller.identity().then(()=>controller.notify()).catch(error=>{controller.error=error.message;controller.notify();});
  window.addEventListener('beforeunload',unload);window.addEventListener('storage',storage);window.addEventListener('focus',focus);
  controller.open().catch(error=>{controller.error=error.message;controller.notify();});
  return()=>{onRegisterGuard?.(null);controller.dispose();window.removeEventListener('beforeunload',unload);window.removeEventListener('storage',storage);window.removeEventListener('focus',focus);};
 },[controller]);
 useEffect(()=>{if(controller.detail)try{controller.ingest(task);}catch(error){controller.error=error.message;controller.notify();}},[controller,task]);
 const recordSaved=async()=>{
  const fresh=await api('/workspace/tasks/'+task.id);
  if(latest.current.scopeKey!==scopeKey)return false;
  if(controller.ingest(fresh)===false)return false;
  controller.notify();
  const accepted=await latest.current.onSaved?.(fresh);
  return latest.current.scopeKey===scopeKey&&accepted!==false;
 };
 if(controller.denied)return <section className="local-model-card" aria-label="完整流程任务"><h2>完整流程任务</h2><p role="alert">账号、工作区或会话已变化，原任务与站内通知已隐藏；原草稿和原请求保留在原账号范围。</p></section>;
 if(!controller.detail)return <section className="local-model-card" aria-label="完整流程任务"><h2>完整流程任务</h2><p role={controller.error?'alert':'status'}>{controller.error||'正在核对当前账号、工作区和任务权限…'}</p>{controller.error&&!controller.denied&&<button type="button" onClick={()=>controller.open().catch(error=>{controller.error=error.message;controller.notify();})}>重新读取当前任务</button>}</section>;
 const current=controller.detail,state=current.graph_runtime,humans=current.graph_pending_humans||[],jobs=Object.entries(current.graph_jobs||{}),waits=current.graph_waits||[],terminal=['succeeded','cancelled','stopped','failed'].includes(state.status),account=typeof actor==='object'?actor:null,simulation=current.workflow_review_mode==='engineering_simulation'||current.workflow_simulation_only===true,ambiguous=exclusiveAmbiguity(state.error);
 const acceptedSupplement=current.layered_approval_v175?.status==='accepted'?current.layered_approval_v175.acceptance?.basis?.graph_supplement:null;
const retiredJob=jobs.some(([,job])=>job.phase==='retired_for_new_evaluation'),blocked=jobs.some(([,job])=>job.phase==='blocked')||retiredJob,runningJob=jobs.some(([,job])=>job.phase==='running'),queuedJob=jobs.some(([,job])=>job.phase==='queued'),processing=runningJob||queuedJob,paused=blocked&&!terminal,partialPause=paused&&(processing||humans.length>0),processingLabel=paused?(runningJob?'部分步骤暂停，其他步骤仍在处理':queuedJob?'部分步骤暂停，其他步骤正在排队':humans.length?'部分步骤暂停，仍有人工待办':'处理已暂停'):(states[state.status]||'执行状态待核对');
 const showEvidence=()=>{for(const details of evidenceRoot.current?.parentElement?.querySelectorAll('details')||[])details.open=true;evidenceRoot.current?.scrollIntoView?.({block:'center',behavior:'smooth'});evidenceRoot.current?.focus?.();};
 let canSubmitNewResult=true,routeGate=null,routeInvalid=false;try{canSubmitNewResult=inspectGraphGoalGate(current)?.can_submit_new_result??true;}catch{canSubmitNewResult=false;}
 try{routeGate=inspectSignalGraphTask(current);canSubmitNewResult=canSubmitNewResult&&(routeGate?.can_submit_new_result??true);}catch{routeInvalid=true;canSubmitNewResult=false;}
 const pendingHuman=humans.find(human=>human.activation_id===controller.pending?.body?.activation_id),canRetryHumanInputs=current.workflow_review_mode===undefined||!!pendingHuman&&graphHumanCanComplete(pendingHuman);
 canSubmitNewResult=canSubmitNewResult&&!ambiguous&&(!controller.pending||canRetryHumanInputs);
 const canStartMessageCheck=!routeInvalid&&(routeGate?.can_retry_ability??true);
 const run=async (action,operation=controller.pending?.operation)=>{setNotice('');try{const result=await action();if(result?.found)setNotice(operation==='message_retry'?'原恢复申请已保存；请查看后台检查进度。':operation==='reasoning_retirement'?'旧步骤已停用，原记录保留；请在业务建议中核对最新资料。':'原请求和当前任务已读回核对，处理结果已保存。');}catch(error){controller.error=error.message;controller.notify();}};
 return <section className="local-model-card" aria-label="完整流程任务"><h2>{current.title}</h2><p data-graph-processing-state={paused?(partialPause?'partially_paused':'paused'):state.status}>{current.business_name} · 固定第 {current.instance_release_number} 版 · {processingLabel}</p>
  <p>每项人工待办独立保存；并行能力和等待由后台推进。完整流程结束仍不等于业务事实已核实。</p>
  {acceptedSupplement&&<GraphSupplementBasis value={acceptedSupplement} task={current} accepted/>}
  {paused&&!acceptedSupplement&&<aside role="alert" className="info"><strong>{partialPause?'部分能力步骤需要处理':'流程在能力步骤暂停'}</strong><p>请核对下方受阻步骤的失败原因。原件、已保存结果和人工草稿保留；刷新进度不会自动重新执行受阻步骤。</p><p>能否继续须核对该步骤的执行条件和尝试限制；不能恢复时，需要重新确认并发起新任务。</p>{humans.length>0&&<p>下方人工待办仍按原负责人和输入条件办理，可以继续填写并保存草稿。</p>}{current.image_snapshots?.length>0&&<p>新建任务时，可在本实例的“任务图片”列表核对并重新选择已有原图；原件不因本次失败自动删除或覆盖。</p>}</aside>}
  {simulation&&<aside className="info" role="alert" aria-label="工程模拟任务"><strong>仅工程模拟（SIM），非人工业务审查。</strong><p>本任务仅验证工程流程与保存恢复。原待核问题和失败记录保留；即使流程结束，也不代表业务质量通过、设备状态属实或获得生产执行授权。</p></aside>}
  <TaskNotices items={current.workflow_notices_v195} taskSimulation={simulation}/>
  {Object.hasOwn(current,'workflow_review_mode')&&<>
   <TaskWorkOrdersV198 task={current} api={api} actor={actor} workspace={workspace} onSaved={recordSaved} onRegisterGuard={registerWorkOrderGuard}/>
   <TaskArchivesV198 task={current} api={api} actor={actor} workspace={workspace} onSaved={recordSaved} onRegisterGuard={registerArchiveGuard}/>
   <TaskEvidenceConsentV199 task={current} api={api} actor={actor} workspace={workspace} onSaved={recordSaved} onRegisterGuard={registerConsentGuard}/>
  </>}
  {!isManagedApprovalTask(task)&&<GraphGoalGateV133 task={current}/>}
  {isManagedApprovalTask(task)&&!acceptedSupplement&&<p>{current.layered_approval_v175?.status==='accepted'?'独立验收已保存。原流程结果与验收依据保留在上方记录中；此处查看已保存的流程结果。':'本任务的提交、审批、明确开始和独立验收在上方办理。流程结果保留在此处；流程完成后还须由独立岗位验收。'}</p>}
  {(routeGate||routeInvalid)&&<section className="media-form" aria-label="原异常自动流程授权"><h3>原异常自动流程授权</h3><p role={routeInvalid||routeGate.status!=='active'?'alert':'status'}>{routeInvalid?'原自动交接限制暂无法核对，不能提交新结果或启动新的消息检查。':routeGate.message}</p><p>{routeGate?.status==='active'?'当前授权有效仍须逐次核对目标、负责人、来源和执行条件，不代表另获额度。':'暂停、到期或来源变化不会被重试解除；需要继续处理时请重新确认合适的新任务。'} 原输入、已保存结果、草稿和旧请求保留；仍可保存草稿、只核对原请求或取消后续流程。</p></section>}
  {state.error&&<div role={ambiguous?'alert':'status'}>{({required_receipts_missing:'当前尚未满足已确认的完成标准，请核对已保存结果和待处理分支。',stop_guard:'停止条件已满足，后续流程已停止。',exception_guard:'异常条件已满足，流程已停止并保留已有结果。'})[state.error]||publicReason(state.error)}{ambiguous&&<p><button type="button" onClick={showEvidence}>查看任务已存结果与依据</button></p>}</div>}
  {controller.error&&<p role="alert" className="error">{publicReason(controller.error)}</p>}{notice&&<p role="status">{notice}</p>}
  {executionBlockedReason&&<p role="alert">当前对象或执行条件暂不可用：{executionBlockedReason} 已保存任务仍可查看、保存草稿、核对原请求或取消后续流程。</p>}
  {!controller.locks&&<p role="alert">当前浏览器缺少多窗口保存保护，暂不能提交；可以查看任务并保留本机草稿。</p>}
  {controller.pending&&<aside className="info"><h3>原{({draft:'草稿保存',complete:'人工结果',cancel:'任务取消',message_retry:'消息检查恢复',reasoning_retirement:'旧步骤停用'})[controller.pending.operation]}请求仍待核对</h3><p>原编号和完整内容已保留。本页不会另建请求，来源或任务状态变化也不会清除原提交。</p>{controller.pending.rejected&&<p>服务器拒绝了原请求：{controller.pending.rejected.message}</p>}<div className="actions"><button type="button" disabled={controller.busy||controller.denied} onClick={()=>run(()=>controller.recover())}>只核对原请求是否已保存</button>{controller.pending.operation==='message_retry'&&controller.pendingNotFound&&!controller.pending.rejected&&<button type="button" disabled={controller.busy||controller.denied||!canStartMessageCheck} onClick={()=>run(()=>controller.resendMessageRetry())}>按原内容重试提交</button>}{controller.pending.schema==='graph-task-outbox-v3'&&controller.pending.operation==='complete'&&controller.pendingNotFound&&!controller.pending.rejected&&<button type="button" disabled={controller.busy||controller.denied||!canSubmitNewResult} onClick={()=>run(()=>controller.resendHumanResult())}>按原内容重试提交</button>}{controller.pending.operation==='reasoning_retirement'&&controller.pendingNotFound&&!controller.pending.rejected&&<button type="button" disabled={controller.busy||controller.denied} onClick={()=>run(()=>controller.resendRetirement())}>按原内容重试提交</button>}{controller.pending.rejected&&<button type="button" disabled={controller.busy||controller.denied} onClick={()=>run(()=>controller.archiveRejected())}>保留被拒请求，返回编辑</button>}</div></aside>}
  <div className="workspace-job-layout"><div><h3>待人工处理</h3>{!humans.length&&<p>{terminal?'当前没有待处理的人工步骤。':retiredJob?'旧资料步骤已停用；请在业务建议中核对最新资料，任务尚未完成。':paused?'当前能力步骤受阻，尚未进入下一项人工处理；请先核对受阻原因和后续执行条件。':'当前没有可填写的人工待办；请查看右侧能力与等待进度。'}</p>}
{humans.map((human,index)=>{const form=controller.forms[human.activation_id],model=inspectHumanSchema(human.output_schema,{typedNumbers:true}),step=stepFor(current,human),decisionReview=reviewDecisionBinding(current,human),canEdit=human.can_edit&&human.actor_id===actorID&&!controller.denied,inputsReady=graphHumanCanComplete(human),values=form?.values||{};let recoveries=[];try{recoveries=controller.localRecoveries(human.activation_id);}catch{}return <article className="media-form" key={human.activation_id}><h4>{labelFor(current,human,'人工待办 '+(index+1))}</h4><p>{step?.purpose||human.purpose||'请根据任务依据填写此项实际处理结果。'}</p><p>负责人：{current.roles?.[human.role]?.username||human.role||'已确认负责人'} · {canEdit?'可由当前账号填写':'等待该负责人处理'}</p>{human.activation_status==='waiting_guard'&&<p role="status">本步骤正在等待输入或执行条件。可以继续编辑和保存草稿，当前不能完成待办。</p>}{step?.output&&<p>应保存：{step.output}</p>}<RequiredInputs items={human.required_inputs} onEvidence={showEvidence} task={current} scope={scope}/>
    {human.node_id==='review_decision'&&<aside aria-label="本次业务建议核查依据"><p role="note">这里只保存您的业务核查意见，不代替原审批、事实冲突处理、补采许可或目标验收。</p>{decisionReview?<BusinessDecisionReviewV284 value={decisionReview.result} plan={decisionReview.plan} taskId={current.id} activationId={decisionReview.activationId} account={account} readerId={actorID} workspaceId={workspaceID} requestJSON={api}/>:<><p role="alert">原建议与本待办的来源暂不能唯一核对；没有改用另一份建议。</p><button type="button" onClick={showEvidence}>查看任务已保存结果与依据</button></>}</aside>}
    {(step?.citations||[]).filter(citation=>citation&&Object.hasOwn(citation,'span_binding_id')).map((citation,i)=>account&&<RegionBindingEvidence key={i} account={account} marker={current.business.flow.reportSource} citation={citation} Btn={({children,...props})=><button type="button" {...props}>{children}</button>}/>)}
    {!model.supported?<p role="alert">{model.reason}</p>:model.fields.map(field=><HumanField key={field.key} field={field} value={values[field.key]} disabled={!canEdit||!form} onChange={value=>{try{controller.edit(human.activation_id,field.key,value);}catch(error){controller.error=error.message;controller.notify();}}}/>)}
    {form?.remoteConflict&&<aside role="alert"><h4>服务器已保存另一份草稿</h4><p>本页输入仍保留。请先对照服务器原稿，再决定保留本页输入继续保存。</p><DraftValues schema={human.output_schema} draft={human.draft}/><button type="button" disabled={!canEdit||controller.busy||!!controller.pending} onClick={()=>{try{controller.acknowledgeRemote(human.activation_id);}catch(error){controller.error=error.message;controller.notify();}}}>已对照服务器草稿，保留本页输入</button></aside>}
    {!!form?.server_drafts?.length&&<details><summary>查看已对照保留的服务器原草稿</summary>{form.server_drafts.map((draft,i)=><article key={i}><h5>原草稿 {i+1}</h5><DraftValues schema={human.output_schema} draft={draft}/></article>)}</details>}
    <p role="status">{form?.dirty?(form.localSaved===false?'本页输入尚未保存，请勿离开。':'本页修改已保留在本机，尚未保存到服务器。'):'已保存的草稿可在刷新或返回此待办后继续。'}</p>
    <div className="actions"><button type="button" disabled={!canEdit||!model.supported||!controller.locks||controller.busy||!!controller.pending||!form?.dirty||form?.remoteConflict} onClick={()=>run(()=>controller.submit('draft',human.activation_id))}>保存草稿，稍后继续</button><button type="button" disabled={!canEdit||!inputsReady||!canSubmitNewResult||!model.supported||!controller.locks||!!executionBlockedReason||controller.busy||!!controller.pending||form?.remoteConflict} onClick={()=>run(()=>controller.submit('complete',human.activation_id))}>保存结果并完成此待办</button></div>
    {!!recoveries.length&&<details><summary>查看此待办保留的本机草稿（{recoveries.length} 份）</summary>{recoveries.map((record,i)=><article className="media-form" key={i}><h5>保留草稿 {i+1}</h5><DraftValues schema={human.output_schema} values={record.values}/><button type="button" disabled={!canEdit||controller.busy} onClick={()=>{try{controller.restoreLocal(human.activation_id,record);}catch(error){controller.error=error.message;controller.notify();}}}>恢复这份到本页（仍未保存）</button></article>)}</details>}
   </article>;})}
   {Object.values(controller.forms).filter(form=>form.dirty&&!humans.some(human=>human.activation_id===form.activation_id)).map((form,index)=><aside className="media-form" key={form.activation_id}><h4>已结束待办仍有本页草稿 {index+1}</h4><p>原待办已有后续变化；这份本页输入已独立保留，不会自动填入新的待办。</p><DraftValues schema={form.schema} values={form.values}/></aside>)}
  </div><div><h3>能力与等待进度</h3><GraphMessageStatus task={current} busy={controller.busy} canSubmit={!!controller.locks&&!controller.denied&&!controller.pending&&canStartMessageCheck} blockedReason={executionBlockedReason||(canStartMessageCheck?'':'原异常自动流程授权当前不能继续新的消息检查；仍可更新进度或核对原请求。')} onRetry={reason=>run(()=>controller.retryMessages(reason),'message_retry')} onRefresh={()=>run(()=>controller.refresh())}/>{!jobs.length&&!waits.length&&!Object.hasOwn(current,'graph_message_status')&&<p>当前没有后台能力或等待分支。</p>}
{jobs.map(([identity,job],index)=>{const activation=state.activations[identity]||{};return <article className="media-form" key={identity}><h4>{labelFor(current,{...activation,...job},'能力处理 '+(index+1))}</h4><p>{job.phase==='blocked'?(terminal?'原受阻记录已保留':'本步骤已暂停'):(phases[job.phase]||'正在核对后台状态')}</p>{activation.node_id==='reasoning'&&<GraphReasoningEvaluationStatusV335 api={api} taskId={current.id} activationId={identity} scopeKey={scopeKey} onDenied={()=>{controller.denied=true;controller.notify();}} canRetire={!terminal&&!controller.busy&&!controller.pending&&!!controller.locks&&!executionBlockedReason&&job.phase==='queued'&&nodeInfo(current,activation).actor_id===actorID&&/^[a-f0-9]{64}$/.test(current.current_goal_evaluation_v288?.head_hash||'')} onRetire={()=>run(()=>controller.retireReasoning(identity),'reasoning_retirement')}/>}{job.phase==='blocked'?<p role="alert">{job.invocation_status==='failed'?'失败原因：':'受阻原因：'}{blockedReason(job.reason)}</p>:job.reason&&<p>{publicReason(job.reason)}</p>}{job.phase==='unknown'&&<p>原调用可能已处理；后台保留真实调用记录，不会用人工结果替代。</p>}{activation.output!==null&&activation.output!==undefined&&<SavedValues value={activation.output} schema={job.output_schema||nodeInfo(current,activation).output_schema} inputs={activation.inputs_snapshot?.arguments} taskId={current.id} activationId={activation.id} account={account} readerId={actorID} workspaceId={workspaceID} requestJSON={api} readBinary={artifactRequest}/>}{simulation&&<TaskSimulationReviewV202 task={current} activationID={identity} api={api} actor={actor} workspace={workspace} available={Array.isArray(current.graph_simulation_review_activation_ids)&&current.graph_simulation_review_activation_ids.includes(identity)} savedReview={current.graph_simulation_review_receipts_v202?.[identity]} blockedReason={executionBlockedReason} onSaved={recordSaved} onRegisterGuard={(guard,previous)=>registerRecordGuard('simulation_review:'+identity,guard,previous)}/>}</article>;})}
   {waits.filter(wait=>!Object.hasOwn(current,'graph_message_status')||wait.status!=='waiting_event').map((wait,index)=><article className="media-form" key={wait.id||wait.activation_id||index}><h4>{labelFor(current,wait,wait.status==='waiting_join'?'等待并行汇合':'等待项 '+(index+1))}</h4><p>{phases[wait.status]||'等待条件满足'}</p>{wait.reason&&<p>{publicReason(wait.reason)}</p>}{wait.deadline!=null&&<p>等待期限：{new Date(wait.deadline*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）</p>}</article>)}
   <h3 ref={evidenceRoot} tabIndex={-1}>已保存结果与任务依据</h3>{Object.values(state.activations).filter(activation=>activation.output!==null&&activation.output!==undefined).map((activation,index)=><details key={activation.id}><summary>{labelFor(current,activation,'处理结果 '+(index+1))} · {phases[activation.status]||'结果已保存'}</summary><SavedValues value={activation.output} schema={nodeInfo(current,activation).output_schema} inputs={activation.inputs_snapshot?.arguments} taskId={current.id} activationId={activation.id} account={account} readerId={actorID} workspaceId={workspaceID} requestJSON={api} readBinary={artifactRequest}/></details>)}
   {Object.values(state.activations).filter(activation=>activation.draft!==null&&activation.draft!==undefined&&!humans.some(human=>human.activation_id===activation.id)).map((activation,index)=><details key={'draft:'+activation.id}><summary>{labelFor(current,activation,'人工记录 '+(index+1))} · 已保留草稿</summary><DraftValues schema={nodeInfo(current,activation).output_schema||controller.forms[activation.id]?.schema||{}} draft={activation.draft}/></details>)}
   {(current.object_snapshots||[]).map(object=><article className="media-form" key={object.id}><h4>{object.type_name} · 对象第 {object.version} 版</h4>{(object.fields||[]).map(field=><p key={field.key}>{field.label}：{scalar(object.attributes?.[field.key]??'未填写')}{field.unit?' · '+field.unit:''}</p>)}</article>)}
   {(current.business?.flow?.citations||[]).filter(citation=>citation&&Object.hasOwn(citation,'span_binding_id')).map((citation,index)=>account&&<RegionBindingEvidence key={index} account={account} marker={current.business.flow.reportSource} citation={citation} Btn={({children,...props})=><button type="button" {...props}>{children}</button>}/>)}
   {!terminal&&!acceptedSupplement&&current.permissions?.can_cancel_task&&<><button type="button" disabled={controller.busy||controller.denied} onClick={()=>setCancelOpen(value=>!value)}>取消后续流程</button>{cancelOpen&&<div className="media-form"><label>取消原因<textarea aria-label="取消完整流程的原因" maxLength={1000} value={controller.cancelReason} onChange={event=>{try{controller.editCancelReason(event.target.value);}catch(error){controller.error=error.message;controller.notify();}}}/></label><p>已保存草稿、原能力调用与结果保留；取消后返回的能力结果不能推进流程。</p><button type="button" disabled={controller.busy||controller.denied||!controller.locks||!!controller.pending||!controller.cancelReason.trim()} onClick={()=>run(()=>controller.submit('cancel',null,controller.cancelReason))}>确认取消后续流程</button></div>}</>}
  </div></div>
 </section>;
}
