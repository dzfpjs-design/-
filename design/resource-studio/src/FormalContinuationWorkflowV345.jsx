import React,{useEffect,useRef,useState} from 'react';
import {FormalContinuationControllerV345} from './formal-continuation-controller-v345.mjs';
const phase={draft:'草稿待确认',in_progress:'按责任人办理中',awaiting_acceptance:'步骤已记录，待核对目标验收',withdrawn:'已撤回'};
const empty={loaded:false,busy:false,error:'',notice:'',context:null,list:[],plan:null,pending:null,draft:null,saved:false,otherDrafts:[],denied:false};
export default function FormalContinuationWorkflowV345({account,task,advicePlan,requestJSON,storage,tabStorage,locks,Btn='button',onRegisterGuard}){
 const [view,setView]=useState(empty),[localError,setLocalError]=useState(''),controller=useRef(null),guardCallback=useRef(onRegisterGuard);guardCallback.current=onRegisterGuard;
 const key=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,task?.id,task?.instance_release_id,advicePlan?.plan_id]);
 useEffect(()=>{let c;try{c=new FormalContinuationControllerV345({account,task,advice:advicePlan,request:requestJSON,storage,session:tabStorage,locks,onChange:setView});controller.current=c;const guard=()=>c.guard();guardCallback.current?.(guard);c.open();
  return()=>{guardCallback.current?.(null,guard);if(controller.current===c)controller.current=null;c.dispose();};
 }catch{setLocalError('账号、任务或本机保存能力尚未核对，暂不能办理。');return()=>{};}},[key,requestJSON,storage,tabStorage,locks]);
 const invoke=(name,...args)=>{try{return controller.current?.[name](...args);}catch(e){setLocalError(e.message||'操作未完成，输入保留。');}};
 const disabled=view.busy||!!view.pending||!view.saved||view.denied,ctx=view.context,plan=view.plan,draft=view.draft;
 const current=plan?.steps?.find(s=>s.status==='ready'),last=plan?.steps?.at(-1),inputKey=current&&plan.plan_id+':'+current.key;
 return <section className="media-form" aria-label="正式业务续段办理">
  <h5>继续办理原业务</h5><p>续段保留原分析和已审核结果，不会自动执行设备或调用云模型。输入先保存在本机，提交后写入任务记录。{plan?.goal_completed?'原目标的独立验收已保存。':'目标仍需独立验收。'}</p>
  {(!view.loaded&&!view.error&&!localError)&&<p role="status">正在读取原申请和本机输入…</p>}
  {(view.error||localError)&&<p role="alert">{view.error||localError}</p>}{view.notice&&<p role="status">{view.notice}</p>}{view.contextError&&<p role="status">{view.contextError}</p>}
  {view.error&&view.saved&&!view.pending&&<Btn type="button" disabled={view.busy} onClick={()=>invoke('repairOriginal')}>核对并修复本机申请</Btn>}
  {view.loaded&&<div className="actions"><Btn type="button" disabled={view.busy} onClick={()=>invoke('refresh')}>刷新办理记录</Btn>
   {!view.saved&&<Btn type="button" disabled={view.busy} onClick={()=>invoke('repairDraft')}>重试保存本机输入</Btn>}</div>}
  {!!view.otherDrafts.length&&<div><p>发现其他页面或上次保留的人工输入，恢复时不会覆盖原件。</p>{view.otherDrafts.map((d,i)=><Btn type="button" key={d.key} disabled={view.busy||!view.saved} onClick={()=>invoke('restoreDraft',d.key)}>恢复第 {i+1} 份本机输入</Btn>)}</div>}
  {view.pending&&<div role="status"><p>原申请的结果仍待核对；原编号和输入已保留，请先核对或重试同一个申请。</p><div className="actions"><Btn type="button" disabled={view.busy} onClick={()=>invoke('refresh')}>核对原申请</Btn><Btn type="button" disabled={view.busy} onClick={()=>invoke('retryOriginal')}>{view.abandoning?'重试终止申请':'重试原申请'}</Btn></div>
   {!view.abandoning&&draft&&<details><summary>终止未完成的原申请</summary><p>系统会先核对原请求，确认没有结果后保留终止凭证；该编号不能再执行，输入仍保留。</p><label>终止原因<textarea value={draft.inputs['abandon:'+view.pending.body.request_id]||''} disabled={view.busy} onChange={e=>invoke('updateInput','abandon:'+view.pending.body.request_id,e.target.value)}/></label><Btn type="button" disabled={view.busy||!view.saved||!(draft.inputs['abandon:'+view.pending.body.request_id]||'').trim()} onClick={()=>invoke('abandonOriginal')}>确认终止原申请</Btn></details>}</div>}
  {view.loaded&&view.list.length>0&&<label>查看已保存续段<select value={draft?.selected_plan||''} disabled={view.busy||!!view.pending} onChange={e=>invoke('selectPlan',e.target.value)}>
   <option value="" disabled>请选择</option>{view.list.map((row,i)=><option key={row.plan_id} value={row.plan_id}>第 {i+1} 份 · {phase[row.phase]||'状态待核对'}</option>)}</select></label>}
  {view.loaded&&!plan&&ctx?.available&&draft&&<div>
   <label>选择已复核的办理结果<select value={draft.execution_id} disabled={disabled} onChange={e=>invoke('update',{execution_id:e.target.value})}><option value="">请选择</option>
    {ctx.results.filter(r=>r.reviewed&&r.current_data).map(r=><option key={r.execution_id} value={r.execution_id}>{r.field?.label||'数值'} · 第 {r.data_generation} 版资料 · {r.record_count} 条{r.simulation_only?' · 软件模拟':''}</option>)}</select></label>
   {ctx.different_generation&&<label><input type="checkbox" checked={draft.acknowledge_cross_version} disabled={disabled} onChange={e=>invoke('update',{acknowledge_cross_version:e.target.checked})}/>我已核对原建议与当前办理资料来自不同版本，不把旧建议当作已分析新资料。</label>}
   <label>确认说明<textarea value={draft.reason} disabled={disabled} onChange={e=>invoke('update',{reason:e.target.value})}/></label>
   <Btn type="button" disabled={disabled||!draft.execution_id||(ctx.different_generation&&!draft.acknowledge_cross_version)} onClick={()=>invoke('create')}>保存续段草稿</Btn>
  </div>}
  {plan&&<div><p>当前状态：{phase[plan.phase]||'状态待核对'}。{(plan.simulation_only||plan.test_only)&&'本次为软件模拟技术办理，不代表现场事实或真实业务签批。'}</p>
   {plan.source_notice&&<p role="alert">{plan.source_notice}</p>}
   <p>原建议依据第 {plan.original_advice_generation} 版资料；本次办理依据第 {plan.current_data_generation} 版资料。</p>
   <ol>{plan.steps.map(s=><li key={s.key}><strong>{s.name}</strong> · {s.role}{s.person&&`（账号：${s.person}）`} · {s.status==='completed'?'已记录':s.status==='ready'?'待责任人办理':'等待前序步骤'}{s.result&&<p>已保存结果：{s.result}</p>}</li>)}</ol>
   {plan.permissions.can_activate&&<Btn type="button" disabled={disabled} onClick={()=>invoke('command','activate')}>确认依据并开始办理</Btn>}
   {current&&plan.permissions.can_complete_step&&draft&&<div><label>{current.name}的处理结果<textarea value={draft.inputs[inputKey]||''} disabled={disabled} onChange={e=>invoke('updateInput',inputKey,e.target.value)}/></label>
    <div className="actions"><Btn type="button" disabled={disabled||!(draft.inputs[inputKey]||'').trim()} onClick={()=>invoke('command','complete_step',current.key)}>保存本步骤结果</Btn>
    {plan.permissions.can_return_step&&<details><summary>退回前一步</summary><label>退回原因<textarea value={draft.inputs[plan.plan_id+':return:'+current.key]||''} disabled={disabled} onChange={e=>invoke('updateInput',plan.plan_id+':return:'+current.key,e.target.value)}/></label><Btn type="button" disabled={disabled||!(draft.inputs[plan.plan_id+':return:'+current.key]||'').trim()} onClick={()=>invoke('command','return_step',current.key)}>确认退回</Btn></details>}</div></div>}
   {plan.phase==='awaiting_acceptance'&&<p>{plan.goal_completed?'原目标的独立验收已保存，续段记录只可回看。':'已记录的步骤不会自动完成原目标；请在目标验收中核对全部条件。'}</p>}
   {plan.phase==='awaiting_acceptance'&&plan.permissions.can_return_step&&last&&draft&&<details><summary>退回最后一步修改</summary><label>退回原因<textarea value={draft.inputs[plan.plan_id+':return:'+last.key]||''} disabled={disabled} onChange={e=>invoke('updateInput',plan.plan_id+':return:'+last.key,e.target.value)}/></label><Btn type="button" disabled={disabled||!(draft.inputs[plan.plan_id+':return:'+last.key]||'').trim()} onClick={()=>invoke('command','return_step',last.key)}>确认退回</Btn></details>}
   {plan.permissions.can_withdraw&&draft&&<details><summary>撤回续段</summary><p>已保存的步骤和依据将保留。</p><label>撤回原因<textarea value={draft.inputs[plan.plan_id+':withdraw']||''} disabled={disabled} onChange={e=>invoke('updateInput',plan.plan_id+':withdraw',e.target.value)}/></label><Btn type="button" disabled={disabled||!(draft.inputs[plan.plan_id+':withdraw']||'').trim()} onClick={()=>invoke('command','withdraw')}>确认撤回</Btn></details>}
  </div>}
 </section>;
}
