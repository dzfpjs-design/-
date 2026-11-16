import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json} from './runtime-api';
import {HTTPSignalApprovalControllerV247} from './http-signal-approval-v247-controller.mjs';
import {httpApprovalCanonical,httpApprovalEnableBody} from './http-signal-approval-v247-protocol.mjs';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';

const routeStatus={active:'正在接收授权之后的新异常',paused:'后续草稿已暂停',expired:'本次授权已到期',exhausted:'本次草稿总额度已用完'};
const occurrenceStatus={frozen:'异常已记录，正在检查',waiting_data:'等待数据管理智能体接收完成',blocked_integrity:'原始数据核对未通过',blocked_authority:'当前授权或审批条件未满足',approval_draft:'已生成对应业务任务',linked_existing_task:'已有对应业务任务'};
const providerName={none:'人工办理，不自动运行专业工具',registered_archives:'已登记的业务档案',received_observation_records:'数据管理智能体已接收的本次数据'};
const operationName={enable:'启用',pause:'暂停',retry:'重新核对'};
const chinaTime=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});
const Check=({children,...props})=><label style={{display:'flex',gap:8,alignItems:'flex-start'}}><input type="checkbox" {...props}/><span>{children}</span></label>;

export default function HTTPSignalApprovalStudioV247({instanceId,account,releaseID,enabled=true,Btn,onViewTask,onRegisterGuard,api=json,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const latest=useRef(),lifecycle=useRef(),panel=useRef();if(!lifecycle.current)lifecycle.current=createRetainedControllerLifecycle();
 let token='';try{token=tabStorage.getItem('ga-setup-csrf')||'';}catch{}
 const owner=httpApprovalCanonical([account?.user_id,account?.workspace_id,account?.role,instanceId,releaseID,enabled,token]);latest.current={owner,onRegisterGuard,onViewTask};
 const [stored,setStored]=useState(null),[localError,setLocalError]=useState('');
 const holder=useMemo(()=>{try{return {controller:new HTTPSignalApprovalControllerV247({account,instanceId,releaseID,enabled,api,storage,session:tabStorage,locks,scopeAvailable:()=>latest.current.owner===owner})};}catch(error){return {error:error.message};}},[owner,api,storage,tabStorage,locks]),c=holder.controller,v=stored?.owner===owner?stored.value:null;
 useEffect(()=>{
  const guard=()=>latest.current.owner===owner&&!!c?.guard();latest.current.onRegisterGuard?.(guard);
  if(!c)return()=>latest.current.onRegisterGuard?.(null,guard);
  const lease=lifecycle.current.setup(c,owner);if(!lease)return;let mounted=true;
  c.onChange=value=>{if(mounted)setStored({owner,value});};setStored({owner,value:c.view()});
  const unload=event=>{if(!guard()){event.preventDefault();event.returnValue='';}};
  globalThis.addEventListener?.('beforeunload',unload);
  if(!c.started){c.started=true;c.start().catch(()=>{});}
  return()=>{mounted=false;latest.current.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);lifecycle.current.cleanup(lease);};
 },[owner,c]);
 useEffect(()=>{if(v?.pending&&panel.current)panel.current.open=true;},[owner,!!v?.pending]);
 useEffect(()=>{
  if(!c||!v?.authenticated||!v.occurrences.some(item=>['frozen','waiting_data'].includes(item.status)))return;
  const timer=setInterval(()=>{if(!c.busy&&c.localSaved)c.action(()=>Promise.all([c.readRoutes(),c.readHead()])).catch(()=>{});},5000);
  return()=>clearInterval(timer);
 },[c,owner,v?.authenticated,v?.occurrences.some(item=>['frozen','waiting_data'].includes(item.status))]);
 async function run(callback){setLocalError('');try{return await callback();}catch(error){if(latest.current.owner===owner)setLocalError(error.message);}}
 if(v?.denied)return <section aria-label="HTTP 异常授权范围已隐藏"><p role="alert">实际账号、工作区或实例已变化，原授权、人稿和原申请已隐藏并保留。</p></section>;
 if(!v?.authenticated)return <details ref={panel}><summary>异常转审批</summary><p role="status">正在核对实际账号与实例；完成前隐藏本机原稿。</p>{(holder.error||v?.error)&&<p role="alert">{holder.error||v.error}</p>}{c&&<Btn disabled={v?.busy} onClick={()=>run(()=>c.start())}>重新读取已保存的授权</Btn>}</details>;
 const f=v.draft.form,options=v.options,writer=['admin','operator'].includes(account.role),actionEditDisabled=!writer||!v.loaded||v.busy||!!v.pending||v.detached,editDisabled=actionEditDisabled||!enabled,submitDisabled=editDisabled||!v.localSaved,actionDisabled=actionEditDisabled||!v.localSaved;
 const source=options?.sources.find(item=>item.id===f.source_id),business=options?.businesses.find(item=>item.id===f.business_id),matches=!!options&&v.optionsCurrent&&v.draft.pins?.options_hash===options.options_hash&&v.draft.pins?.instance_version===options.instance_version&&v.draft.pins?.release_id===options.release_id&&options.release_id===releaseID;
 const enableIssue=(()=>{try{if(!options||!v.optionsCurrent)return '当前来源、业务及审批目录尚未完整核对。';if(!f.source_id||!f.business_id||!f.entity_ids.length||!f.reason.trim())return '请选择来源、业务和对象，并填写启用理由。';httpApprovalEnableBody(v.draft,options,releaseID,'24700000-0000-4000-8000-000000000099');return '';}catch(error){return error.message;}})();
 const blocked=v.occurrences.some(item=>['blocked_integrity','blocked_authority'].includes(item.status));
 return <details ref={panel} className="http-signal-approval-v247"><summary>异常转审批</summary><section className="media-form" aria-label="HTTP 异常审批草稿授权">
  <h3>让之后的新异常先进入正式审批草稿</h3>
  <p>本入口支持已配置字段映射的 HTTP 模拟数值来源。异常先保存，等数据管理智能体接收并核对同一份原始数据后，才可建立草稿。启用不访问设备、不调用云模型，也不代替审批或执行。</p>
  <Btn disabled={v.busy} onClick={()=>run(()=>c.refresh())}>更新来源、授权与草稿进度</Btn>
  {[v.error,localError].filter(Boolean).filter((value,index,all)=>all.indexOf(value)===index).map((message,index)=><p role="alert" key={index}>{message}</p>)}
  {v.notice&&<p role="status">{v.notice}</p>}
  {!writer&&<p>当前账号只读，可以核对真实进度与原回执。</p>}
  {!enabled&&<p role="alert">当前业务实例不能启用新授权；历史授权、原申请和任务继续保留。</p>}
  {v.pending&&<aside className="info" aria-label="原 HTTP 审批草稿授权请求"><h4>上次{operationName[v.pending.operation]}结果待核对</h4><p>原理由：{v.pending.body.reason}</p><p>这次操作与原条件已保存。没有查到结果不能证明没有执行；可靠保存后可以离开，返回同一账号与实例继续核对。</p>
   <Btn disabled={v.busy||!v.localSaved} onClick={()=>run(()=>c.recover(false))}>核对上次操作结果</Btn>
   {v.pendingNotFound&&writer&&<Btn disabled={v.busy||!v.localSaved} onClick={()=>run(()=>c.recover(true))}>按原条件重试上次操作</Btn>}
  </aside>}
  {v.stageFailedBeforePost&&<Btn disabled={v.busy} onClick={()=>run(()=>c.start())}>重新读回已保留的原申请</Btn>}
  {!v.localSaved&&v.loaded&&<aside><p role="alert">本次尝试输入仍留在页面，尚未完整保存；暂不能提交或离开。你可以继续纠正文字，或另存独立人稿。</p><Btn disabled={v.busy} onClick={()=>run(()=>c.saveAgain())}>重试保存 HTTP 授权人稿</Btn><Btn disabled={v.busy} onClick={()=>run(()=>c.retainDraft())}>另存 HTTP 授权人稿并允许离开</Btn></aside>}
  {v.detached&&<p role="status">当前人稿已独立保存；恢复为活动人稿并核对目录后才能启用。</p>}
  <section aria-label="明确本次 HTTP 异常授权范围"><h4>明确本次授权范围</h4>
   {!options?<p>当前来源与业务目录尚未读到；不能把目录未知当作没有可用业务。</p>:<>
    {options.blocking_reason&&<p role="alert">{options.blocking_reason}</p>}
    {!matches&&<aside className="info"><p>当前目录与原确认范围不一致。原选择没有丢弃或改成同名项，请读取并明确核对当前范围。</p><Btn disabled={v.busy||!writer||!!v.pending||!v.localSaved||v.detached||!v.optionsCurrent} onClick={()=>run(()=>c.adoptOptions())}>保留原选择，采用当前目录重新核对</Btn></aside>}
    <fieldset disabled={editDisabled} style={{border:0,padding:0}}>
     <label>HTTP 异常来源<select aria-label="HTTP 审批异常来源" value={f.source_id} onChange={event=>{const chosen=options.sources.find(item=>item.id===event.target.value);c.edit({source_id:event.target.value,extractor_id:chosen?.extractor_id||''});}}><option value="">请选择已登记的 HTTP 模拟来源</option>{f.source_id&&!source&&<option value={f.source_id} disabled>原来源当前不可用（已保留）</option>}{options.sources.map(item=><option key={item.id} value={item.id}>{item.name} · 第 {item.version} 版 · 模拟</option>)}</select></label>
     {source&&<p>固定提取规则：{options.extractors.find(item=>item.id===source.extractor_id)?.name||'原提取规则待核对'}。模拟来源未经现场验证；来源与规则变化不会自动扩大授权。</p>}
     <fieldset><legend>只为哪些对象建立草稿（最多20个）</legend>{options.entities.map(item=><Check key={item.id} aria-label={'HTTP 审批对象：'+item.name} checked={f.entity_ids.includes(item.id)} onChange={event=>c.edit({entity_ids:event.target.checked?[...f.entity_ids,item.id]:f.entity_ids.filter(id=>id!==item.id)})}>{item.name}</Check>)}{f.entity_ids.filter(id=>!options.entities.some(item=>item.id===id)).map((id,index)=><Check key={id} aria-label={'保留的 HTTP 原对象'+(index+1)} checked onChange={()=>c.edit({entity_ids:f.entity_ids.filter(value=>value!==id)})}>原对象当前不可用，仍保留；取消选择须由你明确操作。</Check>)}</fieldset>
     <label>接收草稿的业务<select aria-label="HTTP 审批接收业务" value={f.business_id} onChange={event=>c.edit({business_id:event.target.value})}><option value="">请选择业务</option>{f.business_id&&!business&&<option value={f.business_id} disabled>原业务当前不可用（已保留）</option>}{options.businesses.map(item=><option key={item.id} value={item.id} disabled={!item.can_create_approval_draft}>{item.name}{item.can_create_approval_draft?'':'（暂不可用）'}</option>)}</select></label>
     {business&&<p>业务输入：{providerName[business.input_provider]}。{business.can_create_approval_draft?'服务器已核对真实执行模式、固定输入及正式审批策略。':business.blocking_reason}</p>}
     <label>本次有效期（小时，最多{options.limits.valid_for_hours_max}小时）<input type="text" inputMode="numeric" aria-label="HTTP 审批授权有效小时" value={f.valid_for_hours} onChange={event=>c.edit({valid_for_hours:event.target.value})}/></label>
     <label>本次最多建立多少份草稿（总额度，最多{options.limits.max_tasks_max}份）<input type="text" inputMode="numeric" aria-label="HTTP 审批草稿总额度" value={f.max_tasks} onChange={event=>c.edit({max_tasks:event.target.value})}/></label>
     <label>启用理由<textarea aria-label="HTTP 审批授权启用理由" value={f.reason} onChange={event=>c.edit({reason:event.target.value})}/></label>
     <aside className="info"><h4>本次授权预览</h4><p>{source?.name||'待选来源'} → {business?.name||'待选业务'}；只限所选 {f.entity_ids.length} 个对象，启用后 {f.valid_for_hours||'待填'} 小时，总计最多 {f.max_tasks||'待填'} 份新草稿。旧登记不补交。</p><Check aria-label="确认 HTTP 仅创建正式审批草稿" disabled={!matches} checked={f.confirmed} onChange={event=>c.edit({confirmed:event.target.checked})}>我已核对来源、对象、业务、期限和额度；只允许在数据管理智能体接收完成后创建正式审批草稿，不自动审批、不自动启动，也不新增采集或云调用。</Check></aside>
    </fieldset>
    {enableIssue&&<p role="note">{enableIssue}</p>}
    <Btn primary disabled={submitDisabled||!matches||!!enableIssue} onClick={()=>run(()=>c.enable())}>启用本次审批草稿授权</Btn>
    {options.businesses.some(item=>!item.can_create_approval_draft)&&<aside aria-label="暂不可用业务及实际原因"><h4>这些业务为什么暂不可用</h4>{options.businesses.filter(item=>!item.can_create_approval_draft).map(item=><p key={item.id}><strong>{item.name}</strong>：{item.blocking_reason||'业务输入或审批资格尚未满足'}。输入来源：{providerName[item.input_provider]}。</p>)}</aside>}
   </>}
  </section>
  <section aria-label="已保存的 HTTP 审批草稿授权"><h4>已保存的授权</h4>{!v.listing?<p>服务器授权清单尚未读回。</p>:!v.listing.routes.length?<p>尚未建立本次类型的授权；其他旧授权不会自动升级。</p>:v.listing.routes.map(route=><article className="media-form" key={route.id}><h5>{routeStatus[route.status]}</h5><p>{route.source.name} → {route.business.name}；范围：{route.entities.map(item=>item.name).join('、')}。</p><p>已建立 {route.used_tasks} / {route.max_tasks} 份草稿；到期：{chinaTime(route.expires_at)}（北京时间）。理由：{route.reason}</p>{route.status==='active'&&<><label>暂停理由<textarea aria-label="HTTP 审批授权暂停理由" disabled={actionEditDisabled} value={f.pause_reason} onChange={event=>c.edit({pause_reason:event.target.value})}/></label><Btn disabled={actionDisabled||!f.pause_reason.trim()} onClick={()=>run(()=>c.pause(route.id))}>暂停之后的新草稿</Btn><p>只停止后续新草稿，已保存任务仍按原正式审批流程办理。</p></>}</article>)}</section>
  <section aria-label="HTTP 异常收件与草稿进度"><h4>每条异常的数据接收与审批进度</h4><p>收到异常通知与数据管理智能体接收完成是两回事；草稿已保存也不代表已审批、已执行或已确认现场异常。</p>{!v.occurrencesCurrent&&<p role="alert">最新异常记录尚未完整核对；已读历史继续保留。</p>}
   {blocked&&<label>重新检查这条异常的理由<textarea aria-label="HTTP 异常重新检查理由" disabled={actionEditDisabled} value={f.retry_reason} onChange={event=>c.edit({retry_reason:event.target.value})}/></label>}
   {!v.occurrences.length?<p>{v.occurrencesCurrent?'本次授权之后尚无新异常。':'异常记录数量尚未核对。'}</p>:v.occurrences.map(item=>{
    const route=v.listing?.routes.find(row=>row.id===item.route_id);
    return <article className="media-form" key={item.id} aria-label={'HTTP 异常记录 '+item.id}>
     <h5>{occurrenceStatus[item.status]}</h5><p>{route?route.source.name+' → '+route.business.name:'原授权名称暂未读回'}；异常登记：{chinaTime(item.created_at)}（北京时间）。</p>
     {route&&<p>本授权对象范围：{route.entities.map(row=>row.name).join('、')}。此条异常始终对应同一份原始数据。</p>}
     {item.reason&&<p role={item.status.startsWith('blocked')?'alert':'status'}>{item.reason}</p>}
     {item.status==='waiting_data'&&<p>系统正在等待数据管理智能体接收同一份原始数据，不会重新采集或重复创建草稿。</p>}
     {item.status==='approval_draft'&&<p>这条异常已生成审批草稿，打开任务查看当前办理进度；后续审批与执行以任务页为准。</p>}
     {item.status==='linked_existing_task'&&<p>此条异常已有对应业务任务；没有再次创建草稿或重复使用额度。</p>}
     <Btn disabled={v.busy} onClick={()=>run(()=>c.readOccurrence(item.id))}>检查这条异常的处理进度</Btn>
     {item.task_id&&<Btn disabled={v.busy||!v.localSaved} onClick={()=>run(()=>c.openTask(item.id,latest.current.onViewTask))}>查看对应任务</Btn>}
     {['blocked_integrity','blocked_authority'].includes(item.status)&&<><Btn disabled={actionDisabled||!f.retry_reason.trim()} onClick={()=>run(()=>c.retry(item.id))}>重新检查此条异常</Btn><p>只检查这条异常的原始数据与权限，不重新采集；检查通过后才可能建立草稿。</p></>}
    </article>;
   })}
   {v.nextCursor&&<Btn disabled={v.busy} onClick={()=>run(()=>c.loadMore())}>读取更早的异常记录</Btn>}
  </section>
  {v.completed.slice(-3).map(result=><p role="status" key={result.receipt.request_id}>原{operationName[result.receipt.operation]}操作的服务器回执已核对 · {chinaTime(result.receipt.accepted_at)}（北京时间）；实际草稿数量与办理状态以服务器进度为准。</p>)}
  {v.history.length>0&&<details><summary>恢复独立保留的 HTTP 人稿（{v.history.length}份）</summary>{v.history.map((item,index)=><article key={item.key}><p>第 {index+1} 份：{item.draft.form.reason||item.draft.form.pause_reason||item.draft.form.retry_reason||'原范围选择'}</p><Btn disabled={v.busy||!!v.pending} onClick={()=>run(()=>c.restoreDraft(item.key))}>恢复第{index+1}份 HTTP 人稿</Btn></article>)}</details>}
  <p role="status">{v.localSaved?'当前输入已在本机完整读回；启用、暂停和草稿保存分别以真实服务器回执为准。':'本次输入尚未完整保存。'}</p>
 </section></details>;
}
