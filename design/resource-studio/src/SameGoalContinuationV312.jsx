import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json} from './runtime-api';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
import {SameGoalContinuationControllerV312,continuationHasContent} from './same-goal-continuation-v312-controller.mjs';
import SameGoalExecutionV314 from './SameGoalExecutionV314.jsx';
import './same-goal-continuation-v312.css';

const ROUTES={information_acquisition:'补充资料方案',operational_action:'业务执行方案',business_conclusion:'业务结论整理'};
const REVIEWS={accepted:'原人工决定：采用建议',rejected:'原人工决定：不采用建议',needs_revision:'原人工决定：需补充'};
const SUITABILITY={suitable:'模型认为可考虑',unsuitable:'模型认为不适合',uncertain:'模型认为仍需核对'};
function Button({Btn,primary=false,children,...props}){return Btn?<Btn primary={primary} {...props}>{children}</Btn>:<button type="button" className={'btn '+(primary?'primary':'')} {...props}>{children}</button>;}
function time(value){return typeof value==='string'&&Number.isFinite(Date.parse(value))?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))+'（北京时间）':'时间尚未核对';}
function TextField({label,value,onChange,disabled,maxLength=2000,rows=2,optional=false,hint=null}){return <label className="continuation-field">{label}{optional?'（可选）':''}<textarea aria-label={label} rows={rows} maxLength={maxLength} value={value||''} disabled={disabled} onChange={event=>onChange(event.target.value)}/>{hint&&<small className="continuation-muted">{hint}</small>}</label>;}
function PhaseNotice({state,name}){return state==='loading'?<p role="status">正在读取{name}，尚未将它当作空记录。</p>:state==='failed'?<p role="alert">{name}暂未读回；已经单独读回的内容仍保留，请刷新原记录。</p>:null;}
function Basis({context}){
 const source=context?.source;if(!source)return null;
 const recommendation=source.recommendation,candidate=context.candidates.find(row=>row.candidate_ref===recommendation?.candidate_ref);
 return <aside className="continuation-basis" aria-label="这份办理草案的原建议与人工决定">
  <h5>沿用的原建议与人工决定</h5><p><strong>{REVIEWS[source.review.decision]||'原人工决定尚待核对'}</strong></p><p className="continuation-text">{source.review.reason}</p><p className="continuation-muted">{time(source.review.reviewed_at)}</p>
  {recommendation&&<><h6>模型原建议</h6><p><strong>{candidate?.proposal||(recommendation.candidate_ref?'原建议对应的方案未在本次目录中列出':'模型没有选择具体方案')}</strong></p><p className="continuation-text">{recommendation.reason}</p></>}
  <p><strong>资料截至：</strong>{time(source.data_as_of)}</p>
  {source.limitations?.length>0&&<div><h6>原资料与建议的局限</h6><ul>{source.limitations.map((text,i)=><li key={i}>{text}</li>)}</ul></div>}
  <p>这是一份独立办理草案，尚未绑定原业务流程。原人工决定和模型建议都不代替后续审批、设备权限或目标验收。</p>
 </aside>;
}
function DraftSummary({fields,context}){
 const candidate=context?.candidates?.find(row=>row.candidate_ref===fields.candidate_ref),assignee=context?.assignees?.find(row=>row.id===fields.assignee_id);
 return <dl className="continuation-summary"><dt>办理方向</dt><dd>{fields.route?(ROUTES[fields.route]||'原稿保留的办理方向'):'尚未填写'}</dd>
  <dt>参考方案</dt><dd>{fields.candidate_ref?(candidate?.proposal||'原稿保留的参考方案'):'尚未选择'}</dd>
  <dt>办理目的</dt><dd className="continuation-text">{fields.purpose||'尚未填写'}</dd><dt>办理说明</dt><dd className="continuation-text">{fields.work_description||'尚未填写'}</dd>
  <dt>预期结果</dt><dd className="continuation-text">{fields.expected_result||'尚未填写'}</dd>
  <dt>拟用工具</dt><dd>{fields.selected_tools.length?fields.selected_tools.map((pin,i)=><span className="continuation-tool-name" key={pin.id}>{context?.tools?.find(row=>row.id===pin.id)?.name||'原稿保留的工具 '+(i+1)}</span>):'尚未选择'}</dd>
  <dt>拟办理人</dt><dd>{fields.assignee_id?(assignee?.name||'原稿保留的办理人'):'尚未指定'}</dd><dt>期望完成时间</dt><dd>{fields.due_at?time(fields.due_at):'尚未填写'}</dd>
 </dl>;
}

export function SameGoalContinuationContentV312({view,account,Btn,onAction,onDraft}){
 if(view.denied)return <p role="alert">账号、工作区或权限已变化，原建议与草案已隐藏。原稿和原申请仍保留，请重新打开正确工作区。</p>;
 const ctx=view.context,plan=view.plan,draft=view.draft||{fields:{selected_tools:[]},reason:'',withdraw_reason:'',due_text:''},fields=draft.fields;
 const pending=!!view.pending||view.needsCheck||view.unsettledCount>0;
 const editingBlocked=view.busy||pending||!view.localSaved;
 const blocked=editingBlocked||view.loading||view.reads?.context!=='ready';
 const mayCreate=ctx?.permissions?.can_create===true&&ctx.source.review.decision!=='rejected';
 const isEditing=!!view.editId,editingSelected=isEditing&&plan?.continuation_id===view.editId;
 const maySave=isEditing?editingSelected&&plan.permissions.can_revise===true:mayCreate;
 const mayPreview=ctx?.permissions?.can_preview===true;
 const assignLocked=isEditing&&ctx?.permissions?.can_create!==true&&account?.role!=='admin';
 const tools=ctx?.tools||[],selected=fields.selected_tools||[],candidate=ctx?.candidates?.find(row=>row.candidate_ref===fields.candidate_ref);
 const hasContent=continuationHasContent(fields)||!!draft.due_text;
 return <>
  {view.error&&<p role="alert">{view.error}</p>}{view.notice&&<p role="status">{view.notice}</p>}
  {view.loading&&<p role="status">正在核对本轮办理条件；已读回的原草案可先查看，全部读取完成前不提交修改。</p>}
  <PhaseNotice state={view.reads?.context} name="原建议与办理条件"/>
  {!view.localSaved&&<aside role="alert">本机原稿尚未确认保存，尚未提交新的申请。<Button Btn={Btn} disabled={view.busy} onClick={()=>onAction('retryLocalSave')}>重新保存本机原稿</Button></aside>}
  {pending&&<aside className="continuation-pending" aria-label="核对原办理草案申请"><strong>{view.needsCheck?'原申请已保存，结果原件仍需核对':'原申请尚未核对完成'}</strong><p>先读取同一份原申请；刷新不会新建草案，也不会自动重传。</p><div className="actions">
   <Button Btn={Btn} disabled={view.busy||view.loading} onClick={()=>onAction('recover')}>读取原申请回执</Button>
   {!view.needsCheck&&<Button Btn={Btn} disabled={view.busy||view.loading} onClick={()=>onAction('resendOriginal')}>续传同一份原申请</Button>}
   {view.pendingRejected&&<Button Btn={Btn} disabled={view.busy||view.loading} onClick={()=>onAction('releaseRejected')}>保留被拒申请，恢复编辑</Button>}
  </div></aside>}
  <div className="continuation-columns"><div>
   {ctx?.blocked_reasons?.length>0&&<aside className="info"><ul>{ctx.blocked_reasons.map((row,i)=><li key={i}>{row.message}</li>)}</ul></aside>}
   {ctx?.source.review.decision==='rejected'&&<p role="note">原建议的人工决定为“不采用”，不能据此新建办理草案。已保存的历史草案仍可查阅。</p>}
   {ctx&&(mayCreate||isEditing||plan?.permissions.can_revise)&&<section className="media-form" aria-label="填写办理草案">
    <h5>{isEditing?'修改已保存的办理草案':'填写办理草案'}</h5><p>可先只写一段办理说明，其他信息以后补充。保存不完整草案也不会安排人员、调用工具或执行设备。</p>
    {isEditing&&!editingSelected&&<p role="note">本机保留的是另一份草案的修改稿，请先打开对应原草案核对后再保存。</p>}
    <div className="continuation-form-grid">
     <label className="continuation-field">办理方向（可选）<select aria-label="办理方向（可选）" value={fields.route||''} disabled={editingBlocked} onChange={event=>onDraft({route:event.target.value||null})}><option value="">暂不选择</option>{fields.route&&!ctx.allowed_routes.includes(fields.route)&&<option value={fields.route}>原稿保留的办理方向</option>}{ctx.allowed_routes.map(route=><option key={route} value={route}>{ROUTES[route]||'可用办理方向'}</option>)}</select></label>
     <label className="continuation-field">参考原候选方案（可选）<select aria-label="参考原候选方案（可选）" value={fields.candidate_ref||''} disabled={editingBlocked} onChange={event=>onDraft({candidate_ref:event.target.value||null})}><option value="">暂不选择</option>{fields.candidate_ref&&!candidate&&<option value={fields.candidate_ref}>原稿保留的参考方案</option>}{ctx.candidates.map(row=><option key={row.candidate_ref} value={row.candidate_ref}>{row.proposal}</option>)}</select></label>
    </div>
    {candidate?.model_review&&<p className="continuation-muted">{SUITABILITY[candidate.model_review.suitability]||'原模型意见'}：{candidate.model_review.reasoning}。这项意见不代表执行批准。</p>}
    <TextField label="办理目的（可选）" value={fields.purpose} disabled={editingBlocked} onChange={value=>onDraft({purpose:value})}/>
    <TextField label="办理说明（可先只写这一项）" rows={4} maxLength={4000} value={fields.work_description} disabled={editingBlocked} onChange={value=>onDraft({work_description:value})}/>
    <TextField label="预期结果（可选）" value={fields.expected_result} disabled={editingBlocked} onChange={value=>onDraft({expected_result:value})}/>
    <fieldset className="continuation-tools" disabled={editingBlocked}><legend>拟使用的已发布工具（可选，最多6项）</legend>
     {tools.length===0&&selected.length===0&&<p>当前没有列出的可用工具，可先保存办理说明。</p>}
     {tools.map(tool=>{const existing=selected.find(pin=>pin.id===tool.id),disabled=!existing&&(tool.planning_available!==true||selected.length>=6);
      return <label key={tool.id}><input type="checkbox" checked={!!existing} disabled={disabled} onChange={event=>onDraft({selected_tools:event.target.checked?[...selected,{id:tool.id,content_hash:tool.content_hash,release_version:tool.release_version}]:selected.filter(pin=>pin.id!==tool.id)})}/><span>{tool.name}{tool.planning_available!==true&&<small> · {tool.blocked_reason||'当前不能新选，原稿中的选择仍保留'}</small>}</span></label>;})}
     {selected.filter(pin=>!tools.some(tool=>tool.id===pin.id)).map((pin,i)=><label key={pin.id}><input type="checkbox" checked onChange={()=>onDraft({selected_tools:selected.filter(row=>row.id!==pin.id)})}/>原稿保留的工具 {i+1}（当前目录未列出，可保留或主动移除）</label>)}
    </fieldset>
    <div className="continuation-form-grid"><label className="continuation-field">拟办理人（可选）<select aria-label="拟办理人（可选）" value={fields.assignee_id||''} disabled={editingBlocked||assignLocked} onChange={event=>onDraft({assignee_id:event.target.value||null})}><option value="">暂不指定</option>{fields.assignee_id&&!ctx.assignees.some(row=>row.id===fields.assignee_id)&&<option value={fields.assignee_id}>原稿保留的办理人</option>}{ctx.assignees.map(row=><option key={row.id} value={row.id}>{row.name}{row.role==='admin'?'（管理员）':''}</option>)}</select></label>
     <label className="continuation-field">期望完成时间（可选，北京时间）<input type="datetime-local" aria-label="期望完成时间（可选，北京时间）" value={draft.due_text||''} disabled={editingBlocked} onChange={event=>onDraft({due_text:event.target.value})}/></label></div>
    {assignLocked&&<p className="continuation-muted">你可修改自己被指派的草案内容；改派办理人需由原业务负责人或管理员办理。</p>}
    <TextField label={isEditing?'本次修改说明':'本次保存说明'} optional hint="不填会记录保存动作；填写后会逐字保留你的说明。" value={draft.reason} disabled={editingBlocked} onChange={value=>onDraft({reason:value})}/>
    <div className="actions"><Button Btn={Btn} disabled={blocked||!mayPreview||!hasContent} onClick={()=>onAction('preparePreview')}>核对草案内容</Button>
     <Button Btn={Btn} primary disabled={blocked||!maySave||!view.preview||!hasContent} onClick={()=>onAction('save')}>{isEditing?'保存草案新版本':'保存办理草案'}</Button></div>
    {view.preview&&<section className="continuation-preview" aria-label="已核对的草案内容"><h6>已核对的草案内容</h6><DraftSummary fields={view.preview.normalized_draft} context={ctx}/>{view.preview.missing_fields?.length>0&&<><p>以下内容尚未填写，可保留为空保存草案，后续继续完善：</p><ul>{view.preview.missing_fields.map((row,i)=><li key={i}>{row.message}</li>)}</ul></>}<p>这份预览仅核对文字和选择，没有批准、派发、模型发送或目标验收权限。修改任意内容后需重新核对。</p></section>}
   </section>}
   {plan&&<section className="media-form" aria-label="已保存的办理草案"><h5>{plan.phase==='withdrawn'?'已撤回的办理草案':'已保存的办理草案'} · 第{plan.revision.version}版</h5><p className="continuation-muted">{time(plan.revision.saved_at)}</p><DraftSummary fields={plan.revision.draft} context={ctx}/><p><strong>保存说明：</strong><span className="continuation-text">{plan.revision.reason}</span></p><p>草案保存、审批决定和执行结果分别记录。</p>
    <div className="actions">{plan.permissions.can_revise&&<Button Btn={Btn} disabled={blocked} onClick={()=>onAction('continueEditing')}>保留本机原稿，继续编辑此稿</Button>}{mayCreate&&<Button Btn={Btn} disabled={blocked} onClick={()=>onAction('newDraft')}>另拟一份办理草案</Button>}</div>
    <details><summary>历版草案与保存说明（{plan.history.length}条）</summary>{plan.history.map(row=>{const revise=plan.phase==='draft'&&plan.permissions.can_revise===true,assignedBlocked=revise&&ctx?.permissions.can_create!==true&&account?.role!=='admin'&&(row.draft?.assignee_id!==account?.user_id||plan.revision.draft.assignee_id!==account?.user_id);return <article key={row.record_hash}><details><summary>第{row.version}版 · {time(row.saved_at)}</summary><p><strong>原保存说明：</strong><span className="continuation-text">{row.reason}</span></p>{row.draft?<><DraftSummary fields={row.draft} context={ctx}/>{assignedBlocked&&<p role="note">此旧版本指派的是其他办理人，请原业务负责人或管理员处理；不会自动改派。</p>}<Button Btn={Btn} disabled={blocked||(!revise&&!mayCreate)||assignedBlocked} onClick={()=>onAction('restoreRevision',row.version)}>保留当前输入，恢复第{row.version}版到编辑区</Button><p>这里只复制为新的编辑稿。核对并保存后才产生{revise?'新修订版本':'另一份草案'}，不改写此版原稿或已撤回状态。</p></>:<p>此版本正文尚未单独读回，未用当前版本冒充原稿；请刷新原记录。</p>}</details></article>;})}</details>
    {plan.permissions.can_withdraw&&<details><summary>撤回此草案，保留原记录</summary><TextField label="撤回草案的说明" value={draft.withdraw_reason} disabled={editingBlocked} onChange={value=>onDraft({withdraw_reason:value})}/><Button Btn={Btn} disabled={blocked||!draft.withdraw_reason?.trim()} onClick={()=>onAction('withdraw')}>确认撤回草案</Button></details>}
   </section>}
  </div><Basis context={ctx}/></div>
  <PhaseNotice state={view.reads?.plan} name="选中的原草案"/><PhaseNotice state={view.reads?.history} name="办理草案目录"/>
  {view.list&&<details className="continuation-history"><summary>本建议已保存的办理草案（{view.list.records.length}份）</summary>{view.list.records.length?view.list.records.map((row,i)=><article key={row.continuation_id}><p>办理草案 {i+1} · {row.phase==='withdrawn'?'已撤回':'草案'} · 第{row.revision.version}版 · {time(row.revision.saved_at)}</p><p className="continuation-text">{row.revision.draft.purpose||row.revision.draft.work_description||row.revision.reason}</p><Button Btn={Btn} disabled={view.busy||view.loading||pending||row.continuation_id===view.selectedId} onClick={()=>onAction('selectContinuation',row.continuation_id)}>{row.continuation_id===view.selectedId?'正在查看此草案':'打开原草案'}</Button></article>):<p>当前建议还没有保存的办理草案。</p>}</details>}
  {view.localDrafts?.length>0&&<details><summary>另行保留的本机原稿（{view.localDrafts.length}份）</summary><p>这里只保存你填写的内容与原申请；不保存原文、模型回答或旧权限。打开前会保留当前本机稿。</p>{view.localDrafts.map((row,i)=><p key={row.key}><Button Btn={Btn} disabled={editingBlocked||view.loading} onClick={()=>onAction('restoreDraft',row.key)}>保留当前输入，打开本机稿 {i+1}</Button></p>)}</details>}
 </>;
}

export default function SameGoalContinuationV312({account,task,advicePlan,requestJSON=json,Btn,onRegisterGuard,onSaved,visible=true,
 storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const [,draw]=useState(0),latest=useRef(null),lifecycle=useRef(null),[error,setError]=useState('');
 const executionGuards=useRef(new Map()),executionIdentity=useRef(null);
 if(!lifecycle.current)lifecycle.current=createRetainedControllerLifecycle();
 let csrf='';try{csrf=tabStorage.getItem('ga-setup-csrf')||'';}catch{}
 const key=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,task?.id,task?.instance_release_id,advicePlan?.plan_id,csrf]);latest.current={key,visible,onSaved,onRegisterGuard};
 const holder=useMemo(()=>{try{return {controller:new SameGoalContinuationControllerV312({account,task,advicePlan,request:requestJSON,storage,session:tabStorage,locks,
  beforeContinuationChange:id=>executionGuards.current.get(JSON.stringify([key,id]))?.()??true})};}catch{return {controller:null};}},[key,requestJSON,storage,tabStorage,locks]);const c=holder.controller;
 const run=async(method,...args)=>{
  if(!c||latest.current.key!==key)return;
  setError('');
  try{
   const value=await c[method](...args);
   if(latest.current.key===key&&!c.denied&&['save','withdraw'].includes(method)&&!c.pending&&!c.error){
    try{await latest.current.onSaved?.();}
    catch{setError('草案操作已有原记录，任务摘要尚未刷新；请重新读取原记录。');}
   }
   return value;
  }catch(problem){
   if(latest.current.key===key&&c.active&&!c.denied){
    setError(problem?.message?.startsWith('审批与执行的本机输入')?
     '审批与执行的本机输入尚未可靠保存。请先保存原输入或核对原申请。':'操作尚未核对完成，原稿和原申请保留，请读取原记录。');
   }
  }
 };
 useEffect(()=>{const guard=()=>c?c.guard():false;latest.current.onRegisterGuard?.(guard);if(!c)return()=>latest.current.onRegisterGuard?.(null,guard);const lease=lifecycle.current.setup(c,key);if(!lease)return;
  let mounted=true;c.onChange=()=>{if(mounted&&c.active&&!c.suspended&&latest.current.key===key)draw(n=>n+1);};
  const unload=event=>{if(!guard()){event.preventDefault();event.returnValue='';}},focus=()=>{if(latest.current.visible&&c.active&&!c.suspended&&!c.busy&&!c.loading)run('refresh');},changed=event=>{if(!event.key||event.key.startsWith(c.key)||event.key.startsWith('ga-setup-csrf'))c.storageChanged();};
  globalThis.addEventListener?.('beforeunload',unload);globalThis.addEventListener?.('focus',focus);globalThis.addEventListener?.('storage',changed);run('open');
  return()=>{mounted=false;latest.current.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);globalThis.removeEventListener?.('focus',focus);globalThis.removeEventListener?.('storage',changed);lifecycle.current.cleanup(lease);};
 },[c,key]);
 let matches=false;try{matches=tabStorage.getItem('ga-setup-csrf-user')===account?.user_id&&tabStorage.getItem('ga-setup-csrf-workspace')===account?.workspace_id&&tabStorage.getItem('ga-setup-csrf')===csrf;}catch{}
 const view=c?.view();
 const executionKey=JSON.stringify([key,view?.selectedId]);
 // A same-scope refresh can temporarily clear the plan. Retain only the
 // authenticated identity so the mounted editor and its leave guard survive.
 if(!matches||view?.denied||executionIdentity.current?.key!==executionKey)executionIdentity.current=null;
 if(matches&&!view?.denied&&view?.plan?.continuation_id===view?.selectedId&&view?.plan
  &&(view.context?.source?.decision_intent==='business_conclusion'||view.plan.revision.draft.route==='business_conclusion')){
  executionIdentity.current={key:executionKey,continuation:{continuation_id:view.selectedId,version:view.plan.version}};
 }
 const execution=executionIdentity.current;
 const registerExecution=(guard,previous)=>{
  if(guard)executionGuards.current.set(executionKey,guard);
  else if(executionGuards.current.get(executionKey)===previous)executionGuards.current.delete(executionKey);
 };
 return <section className="media-form same-goal-continuation-v312" aria-label="拟定后续办理方案" hidden={!visible}>
  <div className="continuation-heading"><div><h4>拟定后续办理方案</h4><p>先保存办理草案。本地只读业务结论汇总可在已保存草案下登记工具范围、提交审批并核对执行结果。</p></div><Button Btn={Btn} disabled={!c||!matches||view?.denied||view?.busy||view?.loading} onClick={()=>run('refresh')}>刷新原草案与办理条件</Button></div>
  {!c?<p role="alert">当前账号、本机原稿或原任务范围尚不能核对，请重新打开原建议。</p>:!matches||view.denied?<p role="alert">账号、工作区或权限已变化，原建议与草案已隐藏；原稿和原申请保留。</p>:<>
   {error&&!view.error&&<p role="alert">{error}</p>}
   <SameGoalContinuationContentV312 view={view} account={account} Btn={Btn} onAction={run} onDraft={patch=>run('updateDraft',patch)}/>
   {execution&&<SameGoalExecutionV314 key={executionKey} continuation={execution.continuation}
    {...{account,task,advicePlan,requestJSON,Btn,storage,tabStorage,locks,visible}} onRegisterGuard={registerExecution}
    onSaved={()=>latest.current.key===key?latest.current.onSaved?.():undefined}/>}
  </>}
 </section>;
}
