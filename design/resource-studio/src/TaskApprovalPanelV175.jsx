import React,{useEffect,useMemo,useRef,useState} from 'react';
import {createApprovalController,approvalScopeKey,approvalStatus,approvalCommandPath,checkApprovalTask,readApprovalDraft,writeApprovalDraft,listApprovalDrafts} from './layered-approval-v175.mjs';
import {cloneTyped,freshNumber,isTypedNumber,numericDisplay,typedRequestJSON} from './typed-numbers-v132.mjs';
import RegionBindingEvidence from './RegionBindingEvidence';
import HTTPApprovalTaskSourceV247 from './HTTPApprovalTaskSourceV247';
import TaskRuntimeDocumentSources from './TaskRuntimeDocumentSources';
import TaskDocumentPacketConsentV207 from './TaskDocumentPacketConsentV207';
import TaskLayeredCaseCaptureV209 from './TaskLayeredCaseCaptureV209';
import TaskLayeredCaseUseV210 from './TaskLayeredCaseUseV210';
import ApprovalGoalDraftV253 from './ApprovalGoalDraftV253.jsx';
import {beginApprovalGoalV253,editApprovalGoalV253,closeApprovalGoalV253,removeApprovalGoalV253,approvalGoalValueMatchesV253} from './approval-goal-v253.mjs';
import {checkedRef} from './runtime-task-document-v207-protocol.mjs';
import GraphSupplementBasis from './GraphSupplementBasis.jsx';
import './layered-approval-v175.css';

const names={purpose:'目标说明',requirements:'所需资料',criteria:'完成条件',scope:'范围',entity_ids:'关联对象',fields:'业务字段',field:'业务字段',source:'资料来源',value:'目标值',op:'比较方式',deadline:'截止时间',confirmation:'核查要求',allow_new_capture:'资料不足时申请补采'};
const label=key=>names[key]||key.replaceAll('_',' ');
const time=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'时间未记录';
export function ApprovalRequestStatus({controller,changed}){
 const s=controller.state,act=async action=>{try{await action();}catch{}finally{changed?.();}};
 return <>{s.error&&<p role="alert" className="approval-error">{s.error}</p>}{s.notice&&<p role="status">{s.notice}</p>}{s.pending&&<aside className="approval-pending" aria-label="核对原审批请求"><strong>原操作结果待核对</strong><p>原编号、原文和本机输入已经保留。刷新后仍可核对；核对完成前暂停新操作。</p><div className="approval-actions"><button type="button" disabled={s.busy} onClick={()=>act(()=>controller.recover())}>核对原请求结果</button>{s.missing&&<button type="button" disabled={s.busy} onClick={()=>act(()=>controller.resend())}>按原编号和原内容再次提交</button>}{s.pending.rejection_status&&<button type="button" disabled={s.busy} onClick={()=>act(()=>controller.editRejected())}>保留被拒请求，修改输入</button>}</div><details><summary>查看恢复编号</summary><p>{s.pending.request_id}</p></details></aside>}</>;
}

function Values({value,depth=0}){
 if(value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')==='source_set_hash,source_set_id')return <span>固定运行期资料依据，请在“任务运行期资料依据”中核对原文。</span>;
 if(value===null)return <span>明确为空</span>;
 if(typeof value==='boolean')return <span>{value?'是':'否'}</span>;
 if(typeof value!=='object'||isTypedNumber(value))return <span className="approval-wrap">{typeof value==='number'||isTypedNumber(value)?numericDisplay(value):String(value??'未填写')}</span>;
 if(depth>=6)return <p>已保留结构化内容；请结合原业务资料核对。</p>;
 return <dl className="approval-values">{Object.entries(value).map(([key,item])=><React.Fragment key={key}><dt>{Array.isArray(value)?'第 '+(Number(key)+1)+' 项':label(key)}</dt><dd><Values value={item} depth={depth+1}/></dd></React.Fragment>)}</dl>;
}
function StructuredFields({value,onChange,disabled,path='业务输入',depth=0,fieldInputs={},fieldErrors={},fieldPath='input'}){
 if(value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')==='source_set_hash,source_set_id')return <p>{path}：固定运行期资料依据；请在资料区选择，不能手填内部编号。</p>;
 if(value===null)return <p>{path}：明确为空；本次修订保留此设置。</p>;
 if(depth>6)return <p>{path}：结构较深，原内容完整保留；当前表单不修改这一层。</p>;
 if(typeof value==='boolean')return <label><span>{path}</span><select disabled={disabled} value={String(value)} onChange={e=>onChange(e.target.value==='true')}><option value="true">是</option><option value="false">否</option></select></label>;
 if(typeof value==='number'||isTypedNumber(value))return <label><span>{path}</span><input disabled={disabled} type="text" inputMode="decimal" value={fieldInputs[fieldPath]??numericDisplay(value)} onChange={e=>{const text=e.target.value;try{onChange(freshNumber(text,'number',path),{path:fieldPath,raw:text,error:''});}catch(err){onChange(value,{path:fieldPath,raw:text,error:err.message});}}}/>{fieldErrors[fieldPath]&&<small role="alert">{fieldErrors[fieldPath]} 未完成的原输入已保留；核对后才能保存。</small>}</label>;
 if(typeof value==='string')return <label><span>{path}</span><input disabled={disabled} value={value} onChange={e=>onChange(e.target.value)}/></label>;
 if(!value||typeof value!=='object')return <p>{path}：原类型不受支持，内容保留。</p>;
 return <fieldset disabled={disabled} className="approval-structured"><legend>{path}</legend>{Object.entries(value).map(([key,item])=><StructuredFields key={key} value={item} disabled={disabled} depth={depth+1} fieldPath={fieldPath+'/'+key} fieldInputs={fieldInputs} fieldErrors={fieldErrors} path={Array.isArray(value)?'第 '+(Number(key)+1)+' 项':label(key)} onChange={(next,meta)=>onChange(Array.isArray(value)?value.map((v,i)=>String(i)===key?next:v):{...value,[key]:next},meta)}/>)}</fieldset>;
}

export default function TaskApprovalPanelV175({task,api,account,onSaved,onRegisterGuard}){
 const scope={actor_id:account.user_id,workspace_id:account.workspace_id,role:account.role,subject:'task:'+task.id},key=approvalScopeKey(scope);
 const [,tick]=useState(0),[view,setView]=useState(null),[settings,setSettings]=useState(null),[draft,setDraft]=useState(null),[error,setError]=useState(''),[localError,setLocalError]=useState(''),[loading,setLoading]=useState(true),[action,setAction]=useState(''),[retained,setRetained]=useState(null);
 const sourceGuard=useRef(null),packetGuard=useRef(null),caseGuard=useRef(null),useGuard=useRef(null),goalGuard=useRef(null),goalValidation=useRef(null),sourceImporter=useRef(null),sourceScope=JSON.stringify([account.user_id,account.workspace_id,task.id,task.instance_id,task.instance_release_id]),[sourceInputs,setSourceInputs]=useState(null),sourceInputKeys=sourceInputs?.scope===sourceScope?sourceInputs.keys:[];
 const latest=useRef();latest.current={task,onSaved,key,draft};
 const controller=useMemo(()=>createApprovalController({api,scope,onChange:()=>tick(n=>n+1),onSaved:async()=>{await load();const updated=await controller.read('/workspace/tasks/'+task.id);if(latest.current.key===key)await latest.current.onSaved?.(updated);}}),[key,account.role,api]);
 async function load(){
  setLoading(true);try{const value=await controller.read('/workspace/tasks/'+task.id+'/approval-v175');if(latest.current.key!==key)return;if(value===null){setView(null);return;}checkApprovalTask(value,task.id);setView(value);
   const saved=readApprovalDraft(localStorage,controller.key);const same=saved&&(typedRequestJSON(saved.value)===typedRequestJSON(value.value)||approvalGoalValueMatchesV253(saved,value.value,task.id))&&!Object.values(saved.field_errors||{}).some(Boolean);const next=saved?{...saved,...(same?{base_version:value.version,...(saved.goal_editor_v253?{value:cloneTyped(value.value)}:{})}:{})}:{value:cloneTyped(value.value),reason:'',base_version:value.version,field_inputs:{},field_errors:{}};setDraft(next);if(!saved||same)writeApprovalDraft(localStorage,controller.key,next);
   const options=await controller.read('/workspace/approval-settings-v175');if(latest.current.key===key&&options?.schema==='approval-settings-view-v175')setSettings(options);setError('');
  }catch(e){if(latest.current.key===key)setError(e.message);}finally{if(latest.current.key===key)setLoading(false);}
 }
 useEffect(()=>{controller.state.disposed=false;controller.open().then(load).catch(e=>setError(e.message));const guard=()=>!localError&&controller.guard()&&(!sourceGuard.current||sourceGuard.current())&&(!packetGuard.current||packetGuard.current())&&(!caseGuard.current||caseGuard.current())&&(!useGuard.current||useGuard.current())&&(!goalGuard.current||goalGuard.current());onRegisterGuard?.(guard);const storage=e=>{if(e.key?.startsWith(controller.key))controller.open().catch(err=>setError(err.message));};window.addEventListener('storage',storage);return()=>{controller.dispose();onRegisterGuard?.(null);window.removeEventListener('storage',storage);};},[controller]);
 useEffect(()=>{const guard=()=>!localError&&controller.guard()&&(!sourceGuard.current||sourceGuard.current())&&(!packetGuard.current||packetGuard.current())&&(!caseGuard.current||caseGuard.current())&&(!useGuard.current||useGuard.current())&&(!goalGuard.current||goalGuard.current()),unload=e=>{if(!guard()){e.preventDefault();e.returnValue='';}};onRegisterGuard?.(guard);window.addEventListener('beforeunload',unload);return()=>window.removeEventListener('beforeunload',unload);},[controller,localError,onRegisterGuard]);
 function edit(next){latest.current.draft=next;setDraft(next);try{writeApprovalDraft(localStorage,controller.key,next);setLocalError('');return true;}catch(e){setLocalError(e.message);return false;}}
 async function run(operation,extra={},intent=null){setError('');try{if(!draft?.reason?.trim())throw Error('请先填写本次操作的说明或依据。');if(localError)throw Error(localError);const goalIssue=goalValidation.current?.();if(goalIssue)throw Error(goalIssue);if(operation==='submit'&&typedRequestJSON(draft.value)!==typedRequestJSON(view.value))throw Error('本机还有未保存修订，请先保存到服务器，再提交审批。');if(Object.values(draft.field_errors||{}).some(Boolean))throw Error('请先核对尚未完成的数值输入，原输入已保留。');await controller.send(approvalCommandPath('task_'+operation,task.id),'task_'+operation,task.id,{expected_version:intent?.expected_version??(operation==='revise'?draft.base_version:view.version),reason:intent?.reason??draft.reason,...extra});setAction('');}catch(e){setError(e.message);}}
 function chooseAction(operation){setAction({operation,expected_version:view.version,submission_hash:view.submission_hash,basis_hash:view.acceptance_basis?.basis_hash||null,reason:draft.reason,title:view.value.title});}
 function serverDraft(){try{writeApprovalDraft(localStorage,controller.key+':history:'+Date.now(),draft);edit({value:cloneTyped(view.value),reason:draft?.reason||'',base_version:view.version,field_inputs:{},field_errors:{}});setError('');}catch(e){setLocalError(e.message);}}
 function showRetained(){try{setRetained(listApprovalDrafts(localStorage,controller.key));}catch(e){setLocalError(e.message);}}
 function restoreRetained(row){try{if(draft)writeApprovalDraft(localStorage,controller.key+':history:'+crypto.randomUUID(),draft);edit(row.value);setRetained(null);}catch(e){setLocalError(e.message);}}
 function structured(field,value,meta){edit({...draft,value:{...draft.value,[field]:value},...(meta?{field_inputs:{...draft.field_inputs,[meta.path]:meta.raw},field_errors:{...draft.field_errors,[meta.path]:meta.error}}:{})});}
 function goalAction({kind,expectedEditor,info,form}){
  if(latest.current.key!==key||loading||controller.state.busy||controller.state.pending||!view.permissions?.revise)throw Error('审批范围或原请求状态已变化，原补证输入保留。');
  const current=latest.current.draft;if(!current)throw Error('原审批草稿尚未读回。');
  if(kind!=='begin'&&typedRequestJSON(current.goal_editor_v253)!==typedRequestJSON(expectedEditor))throw Error('另一处已更新补证表单，没有覆盖新输入，请重新核对。');
  let next;if(kind==='begin')next=beginApprovalGoalV253(current,task,info);else if(kind==='change')next=editApprovalGoalV253(current,task.id,info,form);else if(kind==='open'||kind==='close')next=closeApprovalGoalV253(current,task.id,kind==='open');else if(kind==='retry_save')next=current;else if(kind==='remove'){try{writeApprovalDraft(localStorage,controller.key+':history:'+crypto.randomUUID(),current);}catch(e){setLocalError(e.message);return false;}next=removeApprovalGoalV253(current,task.id);}else throw Error('未识别的补证编辑动作。');
  return edit(next);
 }
 async function applyRuntimeSources({inputKey,ref,expectedApprovalVersion,reason}){
  checkedRef(ref);if(!sourceInputKeys.includes(inputKey)||localError||controller.state.pending)throw Error('当前资料入口或审批原请求尚未核对，资料集已保留，未改任务。');
  const fresh=checkApprovalTask(await controller.read('/workspace/tasks/'+task.id+'/approval-v175'),task.id);
  if(latest.current.key!==key||fresh.version!==expectedApprovalVersion||!fresh.permissions?.revise)throw Error('任务审批稿或权限已变化，请刷新并核对后再采用；原资料集仍保留。');
  const saved=readApprovalDraft(localStorage,controller.key)||draft;
  if(!saved||typedRequestJSON(saved.value)!==typedRequestJSON(fresh.value)||Object.values(saved.field_errors||{}).some(Boolean))throw Error('审批表单有未保存修订，请先核对并保存原稿；资料集未丢失，也未自动采用。');
  const value=cloneTyped(fresh.value);value.graph_inputs={...value.graph_inputs,[inputKey]:cloneTyped(ref)};
  writeApprovalDraft(localStorage,controller.key+':history:'+crypto.randomUUID(),saved);
  const next={...saved,value,reason,base_version:fresh.version};writeApprovalDraft(localStorage,controller.key,next);setDraft(next);
  return controller.send(approvalCommandPath('task_revise',task.id),'task_revise',task.id,{expected_version:fresh.version,reason,value});
 }
 if(loading&&!view)return <section className="approval-panel"><h2>任务审批与验收</h2><p role={error?'alert':'status'}>{error||'正在读取已保存的审批和当前操作权限…'}</p><button type="button" onClick={load}>重新读取</button></section>;
 if(!view)return error?<section className="approval-panel"><h2>任务审批与验收</h2><p role="alert">{error}</p><button type="button" onClick={load}>重新读取</button></section>:null;
 const busy=loading||controller.state.busy||!!controller.state.pending||!!localError,permissions=view.permissions||{},changed=!!draft&&typedRequestJSON(draft.value)!==typedRequestJSON(view.value),member=id=>settings?.members?.find(x=>x.id===id)?.username||'成员名称暂不可读',unit=id=>settings?.units?.find(x=>x.id===id)?.name||'组织名称暂不可读',current=view.policy.stages[view.current_stage];
 const intentStale=!!action&&(action.expected_version!==view.version||action.submission_hash!==view.submission_hash||(['accept','acceptance_return'].includes(action.operation)&&action.basis_hash!==view.acceptance_basis?.basis_hash));
 const savedGraphSupplement=view.status==='accepted'?view.acceptance?.basis?.graph_supplement:null;
 const graphSupplement=view.acceptance_basis?.graph_supplement;
 const reason=view.blocked_reasons||{},reasonText=key=>typeof reason[key]==='string'?reason[key]:Array.isArray(reason[key])?reason[key].join('；'):'';
 const buttons=[['submit','提交审批'],['withdraw','撤回审批'],['start','明确开始执行'],['resubmit_acceptance','提交整改说明']];
 return <section className="approval-panel" aria-label="任务审批与独立验收"><div className="approval-heading"><div><h2>任务审批与独立验收</h2><p><strong>{approvalStatus[view.status]||'状态需核对'}</strong> · {view.policy.name}</p></div><button type="button" disabled={controller.state.busy} onClick={load}>更新审批状态</button></div>
  <HTTPApprovalTaskSourceV247 task={task} controller={controller} account={account}/>
  <TaskRuntimeDocumentSources task={task} account={account} api={api} approvalView={view} approvalDraft={draft} approvalController={controller} approvalLocalError={localError} onApply={applyRuntimeSources} onRefreshApproval={load} onDeclaredInputs={keys=>setSourceInputs(previous=>previous?.scope===sourceScope&&JSON.stringify(previous.keys)===JSON.stringify(keys)?previous:{scope:sourceScope,keys})} onRegisterGuard={guard=>{sourceGuard.current=guard;}} onRegisterReferenceImport={stage=>{sourceImporter.current=stage;}}/>
  {sourceInputKeys.length>0&&<TaskLayeredCaseUseV210 task={task} account={account} api={api} onRegisterGuard={guard=>{useGuard.current=guard;}} onStageReference={value=>{if(!sourceImporter.current)throw Error('任务资料区尚未就绪，案例参考没有丢失，请稍后核对。');return sourceImporter.current(value);}}/>}
  {sourceInputKeys.length>0&&<TaskDocumentPacketConsentV207 task={task} account={account} api={api} onRegisterGuard={guard=>{packetGuard.current=guard;}}/>}
  <ol className="approval-stages">{view.policy.stages.map((stage,i)=><li key={i} className={view.status==='in_review'&&view.current_stage===i?'current':''}><strong>第 {i+1} 级 · {stage.name}</strong><span>{unit(stage.scope_id)} · 岗位 {stage.role_key}</span>{i<view.current_stage&&<small>本轮已通过</small>}</li>)}<li><strong>执行 · {view.policy.executor.name}</strong><span>{unit(view.policy.executor.scope_id)}</span></li><li><strong>独立验收 · {view.policy.acceptance.name}</strong><span>{unit(view.policy.acceptance.scope_id)}</span></li></ol>
  <div className="approval-layout"><div><article className="approval-evidence"><h3>本次审批依据</h3><p>发起人：{member(view.owner_id)} · 组织范围：{unit(view.scope_id)}</p><p>业务：{task.business_name||task.business?.name||'已固定业务'} · 发起时的第 {task.instance_release_number||'—'} 版</p><strong>{view.value.title}</strong><p className="approval-wrap">{view.value.note||'尚未填写任务说明。'}</p>{(task.object_snapshots||[]).map((item,i)=><p key={item.id||i}>关联对象：{item.reference||item.name||item.type_name||'对象 '+(i+1)}</p>)}{(task.business?.flow?.citations||[]).filter(c=>c?.span_binding_id).map((citation,i)=><RegionBindingEvidence key={i} account={account} marker={task.business.flow.reportSource} citation={citation} Btn={({children,...props})=><button type="button" {...props}>{children}</button>}/>)}<details><summary>查看保存的业务输入与目标</summary><h4>业务输入</h4><Values value={view.value.graph_inputs}/><h4>目标与完成条件</h4><Values value={view.value.goal_spec}/></details><details><summary>查看原执行结果与回执</summary><Values value={task.workflow_runtime?.receipts||[]}/></details></article>
  {view.permissions?.revise&&draft&&<article className="approval-editor"><h3>修订任务草稿</h3><label>任务名称<input maxLength={300} disabled={busy} value={draft.value.title} onChange={e=>edit({...draft,value:{...draft.value,title:e.target.value}})}/></label><label>任务说明<textarea rows={4} maxLength={12000} disabled={busy} value={draft.value.note} onChange={e=>edit({...draft,value:{...draft.value,note:e.target.value}})}/></label>
  <ApprovalGoalDraftV253 {...{task,account,approval:view,draft,controller,localError}} disabled={loading||controller.state.busy||!!controller.state.pending} onAction={goalAction} onRegisterGuard={(fn,previous)=>{if(fn||!previous||goalGuard.current===previous)goalGuard.current=fn;}} onRegisterValidation={(fn,previous)=>{if(fn||!previous||goalValidation.current===previous)goalValidation.current=fn;}}/>
  <details><summary>修改已填写的业务输入和目标</summary><p>仅修改现有字段；关联对象、引用及完成条件仍由服务器核对。不会新增未经配置的业务结构。</p><StructuredFields value={draft.value.graph_inputs} disabled={busy} fieldPath="graph_inputs" fieldInputs={draft.field_inputs} fieldErrors={draft.field_errors} onChange={(value,meta)=>structured("graph_inputs",value,meta)}/>{!draft.goal_editor_v253&&<StructuredFields path="业务目标" value={draft.value.goal_spec} disabled={busy} fieldPath="goal_spec" fieldInputs={draft.field_inputs} fieldErrors={draft.field_errors} onChange={(value,meta)=>structured("goal_spec",value,meta)}/>}</details><p role="status">{localError?'本机保存遇到问题，请保持本页。':changed?'本机草稿已保留；尚未保存到服务器。':'当前内容与服务器保存稿一致。'}</p><div className="approval-actions"><button type="button" disabled={busy||!changed||!draft.reason.trim()} onClick={()=>run('revise',{value:draft.value})}>保存修订稿到服务器</button><button type="button" disabled={busy} onClick={serverDraft}>保留本机稿，读取服务器稿</button></div></article>}
  <details><summary>查看各轮提交与处理记录</summary>{view.submissions.map((s,i)=><article key={s.id||i}><h4>第 {s.round_number} 轮 · {s.value?.title||'已保存的提交'}</h4><p>{s.reason||'提交原文已保存'} · {time(s.at||s.saved_at)}</p><Values value={s.value}/></article>)}{(view.decision_history||view.decisions).map((d,i)=><p key={i}><strong>{member(d.actor_id)}</strong> · {({approve:'通过',return:'退回修改',withdraw:'撤回'})[d.decision]||'已保存处理'}{Number.isInteger(d.stage)?' · 第 '+(d.stage+1)+' 级':''} · {d.reason} · {time(d.at||d.saved_at)}</p>)}{view.execution&&<p>明确开始：{member(view.execution.actor_id)} · {view.execution.reason} · {time(view.execution.at)}</p>}{[...(view.acceptance_history||[]),...(view.acceptance?[view.acceptance]:[])].map((a,i)=><p key={'a'+i}>独立验收：{member(a.actor_id)} · {a.decision==='accept'?'验收通过':'验收退回'} · {a.reason} · {time(a.at)}</p>)}{view.supplements.map((s,i)=><p key={'s'+i}>整改说明：{s.reason||s.note} · {time(s.at||s.saved_at)}</p>)}</details>
  </div><aside className="approval-decision"><h3>{view.status==='in_review'?'当前处理：'+(current?.name||'本级审批'):
   graphSupplement?'核对补充办理并独立验收':view.acceptance_basis?'核对执行结果并独立验收':'办理本次操作'}</h3><p>当前账号：{member(view.current_user_id)}。提交人不能自审；各级审批及验收人员按服务端职责隔离核对，管理员也不自动获得代办权限。</p>{!view.permissions&&<p role="alert">服务器尚未返回可操作权限，暂不显示办理按钮。</p>}
  {savedGraphSupplement&&<GraphSupplementBasis value={savedGraphSupplement} task={task} accepted/>}
  {view.acceptance_basis&&<article className="approval-evidence"><h4>已保存的验收依据</h4>
   {graphSupplement||savedGraphSupplement?(savedGraphSupplement?null:<GraphSupplementBasis value={graphSupplement} task={task}/>):
    <p>流程已形成完整结果；本次验收绑定当前结果与补充说明，不重新运行原任务。</p>}
   <Values value={view.acceptance_basis.evaluation}/><details><summary>查看结果与证据摘要</summary><Values value={view.acceptance_basis.evidence}/></details>
  </article>}{view.acceptance_issue&&<p role="alert">暂不能验收：{view.acceptance_issue}</p>}
  {draft&&<label>本次操作的说明或依据<textarea rows={4} maxLength={2000} disabled={busy} value={draft.reason} onChange={e=>edit({...draft,reason:e.target.value})} placeholder="说明批准、退回、撤回、开始执行或验收的依据"/></label>}{changed&&!permissions.revise&&<p>本机保留了此前草稿，当前角色不能提交修订；本次决定以服务器保存的提交为准。</p>}{localError&&<p role="alert">{localError}</p>}{error&&<p role="alert">{error}</p>}
  <ApprovalRequestStatus controller={controller}/>
  <details><summary>其他窗口与此前版本的本机保留稿</summary><button type="button" disabled={busy} onClick={showRetained}>读取保留稿</button>{retained?.map((row,i)=><article key={row.key}><strong>{row.value.value?.title||'任务保留稿'}</strong><p>{time(row.at)} · {row.value.reason||'尚未填写办理依据'}</p><button type="button" disabled={busy} onClick={()=>restoreRetained(row)}>保留当前输入，恢复此稿</button></article>)}</details>
  <div className="approval-actions">{buttons.filter(([op])=>permissions[op]===true).map(([op,title])=><button type="button" key={op} disabled={busy||!draft?.reason?.trim()||(op==='submit'&&changed)} onClick={()=>chooseAction(op)}>{title}</button>)}{permissions.decide&&<><button type="button" disabled={busy||!draft?.reason?.trim()} onClick={()=>chooseAction('approve')}>本级通过</button><button type="button" disabled={busy||!draft?.reason?.trim()} onClick={()=>chooseAction('return')}>退回修改</button></>}{permissions.accept&&view.acceptance_basis&&<><button type="button" disabled={busy||!draft?.reason?.trim()} onClick={()=>chooseAction('accept')}>独立验收通过</button><button type="button" disabled={busy||!draft?.reason?.trim()} onClick={()=>chooseAction('acceptance_return')}>验收退回</button></>}</div>
  {Object.entries(reason).filter(([,v])=>!!v).map(([k])=><p className="approval-muted" key={k}>{reasonText(k)}</p>)}
  {action&&<div className="approval-confirm" role="group" aria-label="确认本次审批操作">
   <strong>{({submit:'提交后按第一级开始审批',withdraw:'撤回后原审批记录保留',start:'审批通过后现在开始执行',resubmit_acceptance:'仅提交整改说明，不重跑原任务',approve:'确认本级通过',return:'退回发起人修订',accept:graphSupplement?'确认按补充办理依据验收通过':'确认独立验收通过',acceptance_return:'退回整改说明'})[action.operation]}</strong>
   <p>任务：{action.title}</p><p>依据：{action.reason}</p>
   {graphSupplement&&['accept','acceptance_return'].includes(action.operation)&&<p>此决定绑定当前读回的补充办理验收依据。原流程历史继续保留。</p>}
   {intentStale&&<p role="alert">任务提交、审批状态或验收依据已更新；请关闭这次确认，重新核对。</p>}
   <button type="button" disabled={busy||intentStale||permissions[['approve','return'].includes(action.operation)?'decide':['accept','acceptance_return'].includes(action.operation)?'accept':action.operation]!==true}
    onClick={()=>action.operation==='approve'||action.operation==='return'?run('decide',{submission_hash:action.submission_hash,decision:action.operation},action):action.operation==='accept'||action.operation==='acceptance_return'?run('accept',{basis_hash:action.basis_hash,decision:action.operation==='accept'?'accept':'return'},action):run(action.operation,{},action)}>确认办理</button>
   <button type="button" disabled={busy} onClick={()=>setAction('')}>继续核对</button>
  </div>}
  </aside></div>
  {view.status==='accepted'&&<TaskLayeredCaseCaptureV209 task={task} api={api} account={account} onRegisterGuard={guard=>{caseGuard.current=guard;}}/>}
 </section>;
}
