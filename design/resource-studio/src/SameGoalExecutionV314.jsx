import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json} from './runtime-api';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
import {SameGoalExecutionControllerV314,correctionGrantUsable} from './same-goal-execution-v314-controller.mjs';
import './same-goal-execution-v314.css';

const PHASES={in_review:'待逐级审批',approved:'审批完成，待开始',returned:'审批退回',queued:'已申请执行',cancel_requested:'已申请取消',cancelled:'已取消',adopted:'已采用办理结果',result_returned:'办理结果已退回'};
const JOBS={queued:'等待后台领取',running:'后台正在处理',succeeded:'后台处理完成',failed:'后台处理失败，等待核查原记录',unknown:'原执行结果待核查',cancelled:'后台已取消'};
const OPERATIONS={mean:'平均值',sum:'求和',min:'最小值',max:'最大值',count:'有效记录数'};
const EVENTS={submit:'提交审批',decide:'审批决定',start:'申请开始执行',cancel:'申请取消',review:'结果复核',recover:'核对原执行'};
const DECISIONS={approve:'同意',return:'退回',adopt:'采用'};
const time=value=>typeof value==='string'&&Number.isFinite(Date.parse(value))?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'short'}).format(new Date(value))+'（北京时间）':'尚未读回';

function Button({Btn,primary=false,children,...props}){
 return Btn?<Btn primary={primary} {...props}>{children}</Btn>:<button type="button" className={'btn '+(primary?'primary':'')} {...props}>{children}</button>;
}
function Reason({label,value='',disabled,onChange}){
 return <label className="execution-field">{label}<textarea aria-label={label} rows={2} maxLength={2000} value={value} disabled={disabled} onChange={event=>onChange(event.target.value)}/></label>;
}
function Draft({value}){
 if(!value)return null;
 return <dl className="execution-summary"><dt>办理目的</dt><dd>{value.purpose||'原稿未填写'}</dd><dt>人工办理说明</dt><dd>{value.work_description||'原稿未填写'}</dd><dt>预期结果</dt><dd>{value.expected_result||'原稿未填写'}</dd>{value.due_at&&<><dt>草案期望时间</dt><dd>{time(value.due_at)}</dd></>}</dl>;
}
function Scope({value}){
 return <dl className="execution-summary">
  <dt>字段与单位</dt><dd>{value.field?.label||'原字段'}{value.field?.unit?' · '+value.field.unit:''}</dd>
  <dt>计算方式</dt><dd>{OPERATIONS[value.operation]||'原登记计算方式'}</dd>
  {Number.isSafeInteger(value.record_count)&&<><dt>原记录</dt><dd>{value.record_count} 条，缺测 {value.missing_count} 条</dd></>}
  {value.data_as_of&&<><dt>数据截至</dt><dd>{time(value.data_as_of)}</dd></>}
  {value.expires_at&&<><dt>授权有效至</dt><dd>{time(value.expires_at)}</dd></>}
  {value.policy_ref&&<><dt>审批政策</dt><dd>第 {value.policy_ref.revision} 版</dd></>}
  {(value.simulation_only||value.test_only)&&<><dt>来源限制</dt><dd>{value.simulation_only?'含模拟资料。':''}{value.test_only?'含测试资料。':''} 请在复核时保留这一限制。</dd></>}
 </dl>;
}
function Proposal({value}){
 return <div className="execution-basis">
  <h6>办理范围与人工依据</h6><Scope value={value}/>
  {value.supersedes_plan_id&&<p>本方案补正上一份已结束的原办理。原决定和执行结果继续保留。</p>}
  <p>方案来源为已保存的人工修订草案。</p><Draft value={value.draft}/>
  <p><strong>本次办理理由</strong></p><p className="execution-text">{value.rationale}</p>
  <p>本次范围为一次本地只读汇总。审批、执行和结果采用分别办理。</p>
 </div>;
}
function Output({plan}){
 if(!plan.output)return null;
 const output=plan.output;
 return <section aria-label="原执行结果与数据回执" className="execution-basis">
  <h6>原执行结果与数据回执</h6>
  <dl className="execution-summary"><dt>{OPERATIONS[output.operation]||'汇总结果'}</dt><dd>{output.value===null?'原记录没有可用结果':typeof output.value==='number'?String(output.value):'结果尚待核对'}{output.operation!=='count'&&plan.proposal.field.unit?' '+plan.proposal.field.unit:''}</dd>
   <dt>有效记录</dt><dd>{output.sample_count} 条</dd><dt>缺测记录</dt><dd>{output.missing_count} 条</dd>
   <dt>数据管理接收</dt><dd>{plan.data_received?'独立接收回执已核对':'独立接收回执尚未读回'}</dd>
   <dt>本次计算所用资料截至</dt><dd>{time(plan.proposal.data_as_of)}</dd>
  </dl>
  {(plan.proposal.simulation_only||plan.proposal.test_only)&&<p>本结果使用模拟或测试资料，人工复核时须保留这一限制。</p>}
 </section>;
}
function Action({operation,title,choices,form,disabled,Btn,onAction,onDraft}){
 const choice=operation+'_choice',reason=operation+'_reason';
 return <section className="execution-action" aria-label={title}>
  {choices&&<label className="execution-field">{title}<select aria-label={title} value={form[choice]||''} disabled={disabled} onChange={event=>onDraft({[choice]:event.target.value})}>
   <option value="">请选择</option>{choices.map(([key,label])=><option key={key} value={key}>{label}</option>)}
  </select></label>}
  <Reason label={title+'说明'} value={form[reason]} disabled={disabled} onChange={value=>onDraft({[reason]:value})}/>
  <Button Btn={Btn} primary={['decide','start','review'].includes(operation)} disabled={disabled||!form[reason]?.trim()||(choices&&!choices.some(([key])=>key===form[choice]))} onClick={()=>onAction('command',operation)}>{title}</Button>
 </section>;
}
function GrantSetup({view,Btn,onAction,blocked}){
 if(view.role!=='admin')return null;
 const context=view.grantContext;
 const form=view.draft.grantSetup;
 const tool=context?.tools.find(row=>row.id===form.toolId);
 const field=tool?.fields.find(row=>row.key===form.field);
 const edit=patch=>onAction('updateGrantDraft',patch);
 const canIssue=context?.permissions.can_issue===true;
 return <section className="execution-basis" aria-label="本次工具范围管理">
  <h6>本次工具范围</h6>
  <Button Btn={Btn} disabled={view.loading||view.busy||view.grantLoading} onClick={()=>onAction('openGrants')}>{view.grantOpen?'刷新工具范围':'添加本次工具范围'}</Button>
  {view.grantLoading&&<p role="status">正在读取已发布工具、字段与本草案的原授权。</p>}
  {context&&<>
   <p>授权范围限一次本地只读汇总，费用为零。资料留在本地。登记后可另行提交审批申请。</p>
   <p>有效期最长 2 小时，同时受原目标截止时间约束。登记后显示实际到期时间。</p>
   {context.policy&&<div>
    <p>当前审批政策第 {context.policy.ref.revision} 版</p>
    <ol>{context.policy.stages.map((stage,index)=><li key={index}>{stage.label}{stage.scope_name?' · '+stage.scope_name:''}</li>)}</ol>
   </div>}
   {context.blocked_reasons.map((row,index)=><p key={index}>{row.message}</p>)}
   <label className="execution-field">已发布工具<select aria-label="已发布工具" value={tool?.id||''} disabled={blocked||!canIssue} onChange={event=>edit({toolId:event.target.value||null})}>
    <option value="">请选择工具</option>{context.tools.map(row=><option key={row.id} value={row.id} disabled={!row.fields.length}>{row.name}</option>)}
   </select></label>
   {tool&&<p>{tool.binding==='archive_records'?'来源为已核档案记录。':'来源为已核观测记录。'}</p>}
   <label className="execution-field">本次汇总字段<select aria-label="本次汇总字段" value={field?.key||''} disabled={blocked||!canIssue||!tool} onChange={event=>edit({field:event.target.value})}>
    <option value="">请选择字段</option>{tool?.fields.map(row=><option key={row.key} value={row.key}>{row.label}{row.unit?' · '+row.unit:''}</option>)}
   </select></label>
   <label className="execution-field">本次计算方式<select aria-label="本次计算方式" value={form.operation} disabled={blocked||!canIssue} onChange={event=>edit({operation:event.target.value})}>
    <option value="">请选择计算方式</option>{context.operations.map(operation=><option key={operation} value={operation}>{OPERATIONS[operation]}</option>)}
   </select></label>
   <Reason label="授权说明" value={form.reason} disabled={blocked||!canIssue} onChange={value=>edit({reason:value})}/>
   <Button Btn={Btn} primary disabled={blocked||!canIssue||!field||!context.operations.includes(form.operation)||!form.reason.trim()} onClick={()=>onAction('issueGrant')}>登记本次工具范围</Button>
  </>}
  {view.grantList&&<div aria-label="本草案已保存的授权"><h6>本草案已保存的授权</h6>
   {view.grantList.records.length===0&&<p>当前草案还没有保存的授权。</p>}
   {view.grantList.records.map((row,index)=><details key={row.grant_id}>
    <summary>授权 {index+1} · {row.field.label} · {OPERATIONS[row.operation]} · {row.phase==='revoked'?'已撤销':row.expired?'已到期':'有效'}</summary>
    <Scope value={row}/>
    {row.revocation&&<p className="execution-text">撤销说明：{row.revocation.reason}</p>}
    {row.phase==='active'&&row.permissions.can_revoke&&<>
     <Reason label={'撤销授权 '+(index+1)+' 的原因'} value={form.revocations[row.grant_id]} disabled={blocked} onChange={reason=>edit({revoke:{id:row.grant_id,reason}})}/>
     <Button Btn={Btn} disabled={blocked||!form.revocations[row.grant_id]?.trim()} onClick={()=>onAction('revokeGrant',row.grant_id)}>撤销本次授权</Button>
    </>}
   </details>)}
  </div>}
 </section>;
}
export function SameGoalExecutionContentV314({view,Btn,onAction,onDraft}){
 if(view.denied)return <p role="alert">登录账号、工作区或权限已变化。原输入和原申请仍保留。</p>;
 const {context,plan,preview,draft}=view;
 const grants=context?.grants||[];
 const grant=grants.find(row=>row.grant_id===draft?.grantId);
 const correction=draft?.supersedesPlanId??null;
 const candidates=context?.returned_candidates||[];
 const records=view.list?.records||[];
 const selectable=row=>row.eligible&&row.phase==='active'&&!row.expired&&correctionGrantUsable(row,correction,records);
 const readyGrant=grant&&selectable(grant)&&(correction===null||candidates.some(row=>row.plan_id===correction));
 const pending=!!view.pending||view.needsCheck;
 const blocked=view.loading||view.grantLoading||view.busy||pending||view.staleRevision||!view.localSaved;
 const form=draft?.actions[plan?.plan_id]||{};
 return <>
  {view.loading&&<p role="status">正在读取原办理记录和当前权限。</p>}
  {view.error&&<p role="alert">{view.error}</p>}
  {view.notice&&<p role="status">{view.notice}</p>}
  {view.staleRevision&&<p role="status">草案版本已变化。原输入保留，请刷新审批与执行进度后再办理。</p>}
  {!view.localSaved&&!view.loading&&<p role="alert">本机输入尚未读回。<Button Btn={Btn} disabled={view.busy} onClick={()=>onAction('retryLocalSave')}>重新保存本机输入</Button></p>}
  {pending&&<aside className="execution-basis"><strong>{view.needsCheck?'原申请已保存，结果仍需核对':'原申请结果尚未核对'}</strong>
   <p>先读取同一份原申请。查不到回执时才可用原编号和原正文续传。</p>
   <div className="actions"><Button Btn={Btn} disabled={view.busy||view.loading} onClick={()=>onAction('recover')}>读取原申请回执</Button>
    {!view.needsCheck&&<Button Btn={Btn} disabled={view.busy||view.loading} onClick={()=>onAction('resendOriginal')}>续传原申请</Button>}
    {view.pendingRejected&&<Button Btn={Btn} disabled={view.busy||view.loading} onClick={()=>onAction('releaseRejected')}>保留被拒原件，恢复编辑</Button>}</div>
  </aside>}
  <GrantSetup {...{view,Btn,onAction,blocked}}/>
  {context&&<section className="execution-basis" aria-label="准备审批申请">
   <h6>准备审批申请</h6>
   {context.blocked_reasons?.map((row,index)=><p key={index}>{row.message}</p>)}
   {(candidates.length>0||correction!==null)&&<label className="execution-field">本次申请与原办理的关系<select aria-label="本次申请与原办理的关系" value={correction||''} disabled={blocked||context.permissions.can_preview!==true}
    onChange={event=>onDraft({supersedesPlanId:event.target.value||null})}>
    <option value="">新办理，不关联原退回记录</option>
    {correction!==null&&!candidates.some(row=>row.plan_id===correction)&&<option value={correction} disabled>原选择目前不可补正</option>}
    {candidates.map((row,index)=><option key={row.plan_id} value={row.plan_id}>补正原办理 {index+1} · {row.label}</option>)}
   </select></label>}
   {correction!==null&&<p>补正关联由你明确选择。原授权若已预留一次执行，需要登记并选择新的授权。</p>}
   {!grants.some(selectable)&&<><p>暂无可用的办理工具授权。请管理员在本草案下核对已发布的工具与数据范围，登记有效授权。</p><p><a href="/setup/?view=gateways&category=tools" target="_blank" rel="noopener">查看工具网关（新标签页）</a></p></>}
   <label className="execution-field">办理工具授权<select aria-label="办理工具授权" value={grant?.grant_id||''} disabled={blocked||context.permissions.can_preview!==true} onChange={event=>onDraft({grantId:event.target.value||null})}>
    <option value="">请选择授权</option>{grants.map((row,index)=><option key={row.grant_id} value={row.grant_id} disabled={!selectable(row)}>授权 {index+1} · {row.field?.label||'汇总字段'} · {OPERATIONS[row.operation]||'汇总'}{row.phase!=='active'?'（已撤销）':row.expired?'（已到期）':!row.eligible?'（暂不可用）':!selectable(row)?'（补正范围需核对）':''}</option>)}
   </select></label>
   {grant&&<Scope value={grant}/>}
   <Draft value={context.draft}/>
   <Reason label="人工办理理由" value={draft.rationale} disabled={blocked||context.permissions.can_preview!==true} onChange={value=>onDraft({rationale:value})}/>
   <div className="actions"><Button Btn={Btn} disabled={blocked||!readyGrant||!draft.rationale.trim()||context.permissions.can_preview!==true} onClick={()=>onAction('preparePreview')}>核对办理范围</Button>
    <Button Btn={Btn} primary disabled={blocked||!preview||!readyGrant||context.permissions.can_submit!==true} onClick={()=>onAction('submit')}>提交分级审批</Button></div>
   {preview&&<><Proposal value={preview}/><p role="status">范围已预览。提交后才生成审批申请。</p></>}
  </section>}
  {view.list&&<section aria-label="已保存办理记录"><h6>已保存的办理记录</h6>
   {view.list.records.length===0?<p>当前草案还没有提交审批的办理记录。</p>:<ul>{view.list.records.map((row,index)=><li key={row.plan_id}>
    <span>办理 {index+1} · {PHASES[row.phase]}</span>{' '}
    <Button Btn={Btn} disabled={blocked||view.selectedId===row.plan_id} onClick={()=>onAction('select',row.plan_id)}>{view.selectedId===row.plan_id?'正在查看':'打开办理记录'}</Button>
   </li>)}</ul>}
  </section>}
  {plan&&<section aria-label="办理进度与处理" className="execution-record">
   <h5>{plan.phase==='queued'&&plan.job?.status==='succeeded'&&plan.data_received?'执行已完成，待结果复核':PHASES[plan.phase]}</h5>
   <p>已通过 {plan.stage} 级审批{plan.policy_stage_count? '，共 '+plan.policy_stage_count+' 级':''}。{plan.current_stage_label?' 当前审批：'+plan.current_stage_label+'。':''}</p>
   <Proposal value={plan.proposal}/>
   {candidates.some(row=>row.plan_id===plan.plan_id)&&<Button Btn={Btn} disabled={blocked||context.permissions.can_preview!==true}
    onClick={()=>onDraft({supersedesPlanId:plan.plan_id})}>修改后重新提交</Button>}
   {plan.job&&<p role="status">{JOBS[plan.job.status]||'后台状态待核对'}。{plan.job.status==='running'&&plan.output?' 原结果已读回，后台还在核对执行和接收回执。':''}</p>}
   {plan.phase==='cancel_requested'&&<p>取消已申请。以后台是否已取消及原执行回执为准。</p>}
   <Output plan={plan}/>
   {plan.permissions.can_decide&&<Action operation="decide" title="保存本级审批决定" choices={ [['approve','同意本级申请'],['return','退回办理申请']] } {...{form,Btn,onAction,onDraft}} disabled={blocked}/>}
   {plan.permissions.can_start&&<Action operation="start" title="开始本次只读汇总" {...{form,Btn,onAction,onDraft}} disabled={blocked}/>}
   {plan.permissions.can_review&&plan.job?.status==='succeeded'&&plan.data_received&&plan.output&&<Action operation="review" title="保存结果复核决定" choices={ [['adopt','采用原结果'],['return','退回原结果']] } {...{form,Btn,onAction,onDraft}} disabled={blocked}/>}
   {plan.permissions.can_cancel&&<details><summary>取消本次办理</summary><Action operation="cancel" title="申请取消办理" {...{form,Btn,onAction,onDraft}} disabled={blocked}/></details>}
   {plan.permissions.can_recover&&<details><summary>核查原执行结果</summary><p>保留原执行与数据回执，核对同一份办理记录。</p><Action operation="recover" title="提交原执行核查" {...{form,Btn,onAction,onDraft}} disabled={blocked}/></details>}
   {plan.operations.length>0&&<details><summary>查看已保存的办理过程</summary><ol>{plan.operations.map(row=><li key={row.request_id}>
    {EVENTS[row.operation]||'原办理操作'}{row.decision?' · '+(DECISIONS[row.decision]||'原决定'):''}
    {row.occurred_at&&' · '+time(row.occurred_at)}{row.reason&&<p className="execution-text">{row.reason}</p>}
   </li>)}</ol></details>}
  </section>}
 </>;
}
function MountedExecution({account,task,advicePlan,continuation,requestJSON,Btn,storage,tabStorage,locks,visible,scopeKey,onRegisterGuard,onSaved}){
 const [,draw]=useState(0),[error,setError]=useState(''),lifetime=useRef(null),latest=useRef(null);
 if(!lifetime.current)lifetime.current=createRetainedControllerLifecycle();
 latest.current={onRegisterGuard,onSaved,visible};
 const controller=useMemo(()=>{
  try{return new SameGoalExecutionControllerV314({account,task,advicePlan,continuation,request:requestJSON,storage,session:tabStorage,locks});}
  catch{return null;}
 },[scopeKey,requestJSON,storage,tabStorage,locks]);
 const run=async(method,...args)=>{
  if(!controller?.active||controller.suspended)return;
  setError('');
  try{
   await controller[method](...args);
   if(controller.active&&!controller.suspended&&!controller.denied&&controller.notice==='原操作已保存。'&&['submit','command','recover','resendOriginal'].includes(method)){
    await latest.current.onSaved?.();
   }
  }catch{
   if(controller.active&&!controller.suspended&&!controller.denied)setError('操作尚未核对完成，原输入与原申请保留。');
  }
 };
 useEffect(()=>{
  if(!controller)return;
  const lease=lifetime.current.setup(controller,scopeKey);
  if(!lease)return;
  let mounted=true;
  controller.onChange=()=>{if(mounted&&controller.active&&!controller.suspended)draw(value=>value+1);};
  const guard=()=>controller.guard();
  latest.current.onRegisterGuard?.(guard);
  const unload=event=>{if(!guard()){event.preventDefault();event.returnValue='';}};
  const changed=event=>{if(!event.key||event.key.startsWith(controller.key)||event.key.startsWith('ga-setup-csrf'))controller.storageChanged();};
  globalThis.addEventListener?.('beforeunload',unload);
  globalThis.addEventListener?.('storage',changed);
  run('open');
  return ()=>{
   mounted=false;
   latest.current.onRegisterGuard?.(null,guard);
   globalThis.removeEventListener?.('beforeunload',unload);
   globalThis.removeEventListener?.('storage',changed);
   lifetime.current.cleanup(lease);
  };
 },[controller,scopeKey]);
 if(!controller)return <p role="alert">当前任务或登录范围尚未核对，请重新打开原草案。</p>;
 const view=controller.view();
 const staleRevision=Number.isSafeInteger(continuation.version)&&view.context&&view.context.continuation_version<continuation.version;
 return <div hidden={!visible}>
  <Button Btn={Btn} disabled={view.busy||view.loading||view.grantLoading||view.denied} onClick={()=>run('refresh')}>刷新审批与执行进度</Button>
  {error&&!view.error&&!view.denied&&<p role="alert">{error}</p>}
  <SameGoalExecutionContentV314 view={{...view,staleRevision}} Btn={Btn} onAction={run} onDraft={patch=>run('updateDraft',patch)}/>
 </div>;
}
export default function SameGoalExecutionV314({account,task,advicePlan,continuation,requestJSON=json,Btn,onRegisterGuard,onSaved,visible=true,
 storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const [section,setSection]=useState({key:'',open:false}),[warning,setWarning]=useState('');
 const guardRef=useRef(null);
 let csrf='',matches=false;
 try{
  csrf=tabStorage.getItem('ga-setup-csrf')||'';
  matches=!!csrf&&tabStorage.getItem('ga-setup-csrf-user')===account?.user_id&&tabStorage.getItem('ga-setup-csrf-workspace')===account?.workspace_id;
 }catch{}
 const key=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,task?.id,task?.instance_release_id,advicePlan?.plan_id,continuation?.continuation_id,csrf]);
 const mounted=matches&&section.key===key,open=mounted&&section.open;
 const register=(guard,previous)=>{
  if(guard)guardRef.current={key,guard};
  else if(guardRef.current?.guard===previous)guardRef.current=null;
  onRegisterGuard?.(guard,previous);
 };
 return <section className="media-form same-goal-execution-v314" hidden={!visible} aria-label="审批与执行办理">
  <div className="execution-heading"><div><h5>审批与执行办理</h5><p>核对已授权的范围，提交分级审批，再由具备岗位权限的人员开始与复核。</p></div>
   <Button Btn={Btn} aria-expanded={open} disabled={!matches} onClick={()=>{
    if(open&&guardRef.current?.key===key&&!guardRef.current.guard()){setWarning('当前办理输入尚未可靠保存，请先核对原记录。');return;}
    setWarning('');setSection({key,open:!open});
   }}>{open?'收起审批与执行':'打开审批与执行'}</Button>
  </div>
  {warning&&mounted&&<p role="alert">{warning}</p>}
  {mounted&&<MountedExecution key={key} scopeKey={key} {...{account,task,advicePlan,continuation,requestJSON,Btn,storage,tabStorage,locks,onSaved}} onRegisterGuard={register} visible={visible&&open}/>}
 </section>;
}
