import React,{useEffect,useMemo,useRef,useState} from 'react';
import {GraphHumanBudgetController} from './graph-human-budget-controller.mjs';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';

const statusLabels={in_review:'逐级审批中',approved:'额度已批准',returned:'已退回'};
const roleLabels={business:'业务岗位',data:'数据管理岗位',perception:'感知岗位'};
function StepPath({task,path}){
 return <ol>{path.map((item,index)=>{
  const label=task.graph_node_labels?.[item.activation_id]||task.graph_node_labels?.[item.node_id];
  const step=task.business?.flow?.steps?.find(row=>row.id===(label?.source_step_id||item.node_id));
  const actor=task.roles?.[item.role]?.username||roleLabels[item.role]||'原人工岗位';
  return <li key={item.activation_id}>{step?.name||label?.name||label?.display_name||'人工步骤 '+(index+1)} · {actor}</li>;
 })}</ol>;
}
function Note({label,value,disabled,onChange}){
 return <label className="same-advice-field">{label}<textarea aria-label={label} rows={2} maxLength={2000} value={value}
  disabled={disabled} onChange={event=>onChange(event.target.value)}/></label>;
}
function BudgetForm({controller,task,Btn,onSaved}){
 const [,draw]=useState(0),[error,setError]=useState(''),lifecycle=useRef(null);
 const latest=useRef(onSaved);
 latest.current=onSaved;
 if(!lifecycle.current)lifecycle.current=createRetainedControllerLifecycle();
 const run=async(method,...args)=>{
  if(!controller.active||controller.suspended)return;
  setError('');
  try{
   const saved=await controller[method](...args);
   if(saved===true&&['submit','retry'].includes(method)&&controller.active&&!controller.suspended&&!controller.denied){
    try{await latest.current?.();}
    catch{setError('额度操作已保存，请刷新原任务流程以核对待办。');}
   }
  }catch{if(controller.active&&!controller.suspended&&!controller.denied)setError('操作尚未核对完成，原输入和原申请保留。');}
 };
 useEffect(()=>{
  const lease=lifecycle.current.setup(controller,controller.key);
  if(!lease)return;
  controller.onChange=()=>draw(value=>value+1);
  const changed=event=>{if(!event.key||event.key.startsWith(controller.key)||event.key.startsWith('ga-setup-csrf'))controller.storageChanged();};
  globalThis.addEventListener?.('storage',changed);
  run('open');
  return()=>{globalThis.removeEventListener?.('storage',changed);lifecycle.current.cleanup(lease);};
 },[controller]);
 const view=controller.view();
 if(view.denied)return <p role="alert">账号或岗位已变化。请重新打开原任务，原输入仍保留。</p>;
 const {context,draft,pending}=view;
 const blocked=view.busy||view.loading||!view.localSaved||!!pending;
 const reviewing=context?.records.find(row=>row.status==='in_review');
 const decision=reviewing?draft.decisions[reviewing.submission_hash]||{reason:'',choice:''}:null;
 return <>
  <Btn disabled={view.busy||view.loading} onClick={()=>run('refresh')}>刷新人工步骤额度</Btn>
  {view.loading&&<p role="status">正在读取原流程剩余人工步骤和审批记录。</p>}
  {(view.error||error)&&<p role="alert">{view.error||error}</p>}
  {view.notice&&<p role="status">{view.notice}</p>}
  {!view.localSaved&&!view.loading&&<p role="alert">本机输入尚未可靠保存。<Btn disabled={view.busy} onClick={()=>run('retrySave')}>重新保存本机输入</Btn></p>}
  {pending&&<aside className="info">
   <p>上次{pending.operation==='submit'?'额度申请':'审批决定'}的结果仍需核对。重试会使用同一编号和完整原正文。</p>
   <Btn primary disabled={view.busy||view.loading} onClick={()=>run('retry')}>按原编号重试</Btn>
  </aside>}
  {context&&<>
   {context.path.length>0?<div className="media-form">
    <h5>本次可申请的人工步骤（{context.path.length} 项）</h5><StepPath task={task} path={context.path}/>
    {context.permissions.can_submit&&<>
     <Note label="申请人工步骤额度的理由" value={draft.submitReason} disabled={blocked} onChange={submitReason=>run('update',{submitReason})}/>
     <Btn primary disabled={blocked||!draft.submitReason.trim()} onClick={()=>run('submit','submit')}>提交额度审批</Btn>
    </>}
   </div>:<p>当前没有可单独申请额度的剩余人工步骤。</p>}
   {context.records.length>0&&<div aria-label="人工步骤额度审批记录">
    <h5>原申请与审批进度</h5>
    {context.records.map(row=><article className="media-form" key={row.submission_hash}>
     <h6>{statusLabels[row.status]} · 已通过 {row.stage} 级审批</h6>
     <p className="execution-text">{row.reason}</p><StepPath task={task} path={row.path}/>
     {row.status==='approved'&&<p>请由原岗位在下方任务流程中逐步办理，保存各项实际处理结果。</p>}
    </article>)}
   </div>}
   {reviewing&&context.permissions.can_decide&&<section className="media-form" aria-label="本级人工步骤额度审批">
    <h5>填写本级审批决定</h5>
    <label className="same-advice-field">本级审批决定<select aria-label="本级审批决定" value={decision.choice} disabled={blocked}
     onChange={event=>run('update',{decision:event.target.value})}>
     <option value="">请选择</option><option value="approve">同意本级额度</option><option value="return">退回本次申请</option>
    </select></label>
    <Note label="本级额度审批说明" value={decision.reason} disabled={blocked} onChange={decisionReason=>run('update',{decisionReason})}/>
    <Btn primary disabled={blocked||!decision.choice||!decision.reason.trim()} onClick={()=>run('submit','decide')}>保存本级额度决定</Btn>
   </section>}
  </>}
 </>;
}
export default function GraphHumanBudgetPanel({task,account,request,Btn,onSaved,onRegisterGuard,
 storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const [section,setSection]=useState({key:'',open:false}),[warning,setWarning]=useState('');
 const latest=useRef(onRegisterGuard);
 latest.current=onRegisterGuard;
 let csrf='',matches=false;
 try{
  csrf=session.getItem('ga-setup-csrf')||'';
  matches=!!csrf&&session.getItem('ga-setup-csrf-user')===account.user_id&&session.getItem('ga-setup-csrf-workspace')===account.workspace_id;
 }catch{}
 const key=JSON.stringify([account.user_id,account.workspace_id,account.role,task.id,task.instance_release_id,csrf]);
 const mounted=matches&&section.key===key,open=mounted&&section.open;
 const controller=useMemo(()=>{
  if(!mounted)return null;
  try{return new GraphHumanBudgetController({task,account,request,storage,session,locks});}
  catch{return null;}
 },[mounted,key,request,storage,session,locks]);
 useEffect(()=>{
  if(!controller)return;
  const guard=()=>controller.guard();
  latest.current?.(guard);
  const unload=event=>{if(!guard()){event.preventDefault();event.returnValue='';}};
  globalThis.addEventListener?.('beforeunload',unload);
  return()=>{latest.current?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);};
 },[controller]);
 return <section className="local-model-card" aria-label="原流程人工步骤额度">
  <h3>原流程人工步骤额度</h3>
  <p>为原流程剩余的人工步骤申请办理额度，按本任务的审批政策逐级核对。获批后仍需原岗位逐项办理并保存结果。</p>
  <Btn aria-expanded={open} disabled={!matches} onClick={()=>{
   if(open&&controller&&!controller.guard()){setWarning('本机输入或原申请尚未可靠保存，请先核对。');return;}
   setWarning('');setSection({key,open:!open});
  }}>{open?'收起人工步骤额度':'查看人工步骤额度'}</Btn>
  {warning&&open&&<p role="alert">{warning}</p>}
  {mounted&&<div hidden={!open}>{controller?<BudgetForm key={key} {...{controller,task,Btn,onSaved}}/>:<p role="alert">当前任务或登录范围无法核对。</p>}</div>}
 </section>;
}
