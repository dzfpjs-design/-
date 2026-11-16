import React,{useEffect,useId,useRef,useState} from 'react';
import {json} from './runtime-api';
import {GraphSourcesControllerV122,GRAPH_SOURCE_ADAPTERS,graphSourceResultFields} from './graph-sources-controller-v122.mjs';
import GraphSourceDeliveryPanelV124 from './GraphSourceDeliveryPanelV124';
import './graph-sources-v122.css';

function NativeButton({primary,children,...props}){return <button type="button" {...props}>{children}</button>;}
function ResultFields({schema}){
 const fields=graphSourceResultFields(schema);
 return fields.length?<ul className="graph-sources-fields">{fields.map((field,index)=><li key={index}><strong>{field.label}</strong><span>{field.type}{field.required?' · 必有字段':''}</span></li>)}</ul>:<p>以已发布的处理结果定义为准，尚无可展开的字段说明。</p>;
}
export default function GraphSourcesPanel({actorID,workspaceID,workspace,user,Btn=NativeButton,onBusy,onReturnToFlow}){
 const heading=useId(),controller=useRef(null),alive=useRef(false),callbacks=useRef({onBusy,onReturnToFlow});callbacks.current={onBusy,onReturnToFlow};
 const activity=useRef({source:false,delivery:false});
 const [,render]=useState(0),[fatal,setFatal]=useState(''),[confirmStop,setConfirmStop]=useState(null),[confirmRetire,setConfirmRetire]=useState(null);
 useEffect(()=>{
  alive.current=true;let current;
  const changed=()=>{if(alive.current&&controller.current===current){activity.current.source=current.busy||!current.localSaved;callbacks.current.onBusy?.(activity.current.source||activity.current.delivery);render(n=>n+1);}};
  try{current=new GraphSourcesControllerV122({api:json,scope:{actor_id:actorID,workspace_id:workspaceID},onChange:changed});controller.current=current;setFatal('');changed();current.open().catch(()=>{});}
  catch{controller.current=null;setFatal('当前账号或工作区尚未确定，请重新核对登录；未提交来源登记。');}
  const leave=event=>{if(activity.current.source||activity.current.delivery){event.preventDefault();event.returnValue='';}};
  const wake=()=>{if(document.visibilityState!=='hidden')current?.recheckIdentity().catch(()=>{});};
  window.addEventListener('beforeunload',leave);
  window.addEventListener('focus',wake);document.addEventListener('visibilitychange',wake);
  return()=>{alive.current=false;current?.dispose();if(controller.current===current)controller.current=null;activity.current.source=false;callbacks.current.onBusy?.(activity.current.delivery);window.removeEventListener('beforeunload',leave);window.removeEventListener('focus',wake);document.removeEventListener('visibilitychange',wake);};
 },[actorID,workspaceID]);
 const candidate=controller.current,c=candidate?.scope.actor_id===actorID&&candidate?.scope.workspace_id===workspaceID?candidate:null;
 async function perform(callback){try{await callback();}catch{}if(alive.current)render(n=>n+1);}
 const locked=!c||c.busy||c.corrupt||activity.current.delivery,canRegister=c?.canRegister===true,selected=c?.detail;
 const currentStop=selected?selected.id+':'+selected.version:null;
 const reason=selected?(c.form.reasons[selected.id]||''):'';
 const stopConfirmed=confirmStop===currentStop;
 const retirementReason=c?.pending?(c.form.reasons[c.pending.body.request_id]||''):'';
 const retirementKey=c?.pending?c.pending.body.request_id+':'+c.pending.command_hash+':'+retirementReason.trim():null;
 function choose(kind,id){if(!c)return;try{c.chooseCapability(kind,id);}catch{c.error='请选择本页读取到的已发布能力，原选择保留。';render(n=>n+1);}}
 const more=(kind,label)=>c?.pages[kind].next_after?<Btn disabled={locked} onClick={()=>perform(()=>c.loadMore(kind))}>{label}</Btn>:null;
 const deliveryPanel=<GraphSourceDeliveryPanelV124 key={workspaceID+':'+actorID} actorID={actorID} workspaceID={workspaceID} producer={selected?{id:selected.id,label:selected.label}:null} Btn={Btn} concealed={!c||c.identityStatus!=='verified'} blocked={!c||c.busy} onBusy={value=>{const changed=activity.current.delivery!==value;activity.current.delivery=value;callbacks.current.onBusy?.(activity.current.source||value);if(changed&&alive.current)render(n=>n+1);}}/>;
 if(!c||c.corrupt||c.identityStatus!=='verified')return <><section className="graph-sources-v122" aria-labelledby={heading}>
  <h2 id={heading}>供业务流程使用的处理结果</h2>
  <p role={fatal||c?.corrupt||c?.identityStatus==='concealed'?'alert':'status'}>{c?.corrupt?'本机恢复记录暂无法读取，原记录未被覆盖；请核对浏览器存储并重新进入。':fatal||c?.identityStatus==='concealed'?'当前登录身份尚无法核对，本机记录保留；请核对登录后重试。':'正在核对登录，本机记录保留。'}</p>
  <p>身份核对通过前，不展示本机输入、原请求或登记记录。</p>
  <Btn disabled={!c||c.busy} onClick={()=>perform(()=>c.recheckIdentity())}>重新核对登录</Btn>
 </section>{deliveryPanel}</>;
 return <><section className="graph-sources-v122" aria-labelledby={heading}>
  <header className="graph-sources-heading"><div><h2 id={heading}>供业务流程使用的处理结果</h2><p>选择已经发布的来源和处理能力，登记一种可供业务等待选择的结果。</p></div>
   <Btn disabled={locked||!c?.localSaved||typeof onReturnToFlow!=='function'} onClick={()=>callbacks.current.onReturnToFlow?.()}>返回业务与流程选择来源</Btn></header>
  <p className="graph-sources-boundary">登记不证明设备健康，也不代表已有处理管线正在运行。业务流程仍需单独确认来源、关联对象和等待规则；处理结果不能直接作为现场事实。</p>
  {fatal&&<p role="alert">{fatal}</p>}{c?.error&&<p className="graph-sources-problem" role="alert">{c.error}</p>}
  {c?.notice&&<p className="graph-sources-notice" role="status">{c.notice}</p>}
  {c&&!c.localSaved&&<aside role="alert"><p>本页新输入尚未保存。请保留页面，恢复浏览器存储后重试。</p><Btn disabled={c.busy||c.corrupt} onClick={()=>c.saveLocal()}>重试保存本页输入</Btn></aside>}
  <section className="graph-sources-local-recovery" aria-label="找回本机输入与原操作">
   <h3>找回本机保留的输入与原操作</h3><p>重新登录或打开新窗口后，可明确选择本机保留记录。保存提示只是本机线索，原操作仍会向服务器核对；不会自动提交。</p>
   <Btn disabled={locked} onClick={()=>perform(()=>c.discoverRecovery())}>读取本机保留记录</Btn>
   {c.recovery.problem_count>0&&<p role="alert">部分本机记录暂无法读取，原记录保留，未用空白内容替代。</p>}
   {c.recovery.loaded&&!c.recovery.items.length&&<p>{c.recovery.next_offset!==null?'本段没有可恢复项，可继续读取后续记录。':'当前范围未读取到可恢复的本机记录。'}</p>}
   <ul className="graph-sources-recovery-list">{c.recovery.items.map((candidate,index)=><li key={candidate.key+':'+index}>
    <div><strong>{candidate.label}</strong><p>{candidate.summary}</p>{candidate.local_completion&&<p>本机曾记下结果，选择后仍会核对服务器原记录。</p>}</div>
    <Btn disabled={locked||!c.localSaved} onClick={()=>perform(()=>c.restoreRecovery(candidate))}>{candidate.kind==='draft'?'保留当前输入并恢复此稿':candidate.kind==='request'?'恢复并核对原请求':'核对这次停止操作是否已保存'}</Btn>
   </li>)}</ul>
   {c.recovery.next_offset!==null&&<Btn disabled={locked} onClick={()=>perform(()=>c.discoverRecovery(true))}>读取更多本机记录</Btn>}
  </section>
  {c?.pending&&<aside className="graph-sources-recovery" aria-label="原请求核对">
   <h3>{c.pending.kind==='register'?'原登记结果待核对':'原停止结果待核对'}</h3>
   <p>完整原内容已独立保留。刷新和重新进入后先核对原结果，不会另建请求。</p>
   <p>{c.pending.kind==='register'?c.pending.body.label+' · '+GRAPH_SOURCE_ADAPTERS[c.pending.body.adapter_id]:'停止此结果来源 · '+c.pending.body.reason}</p>
   <p>若原发布版本已经变化，按原内容重试可能仍被拒绝。请先核对原操作；明确尚未找到后，可以停止这次未完成的操作，保留原稿。</p>
   <div className="graph-sources-actions"><Btn disabled={locked} onClick={()=>perform(()=>c.recover())}>只核对原请求是否已保存</Btn>
    {c.pendingNotFound&&!c.retirement&&<Btn disabled={locked} onClick={()=>perform(()=>c.resend())}>按原内容重试</Btn>}</div>
   {c.pendingNotFound&&!c.retirement&&<div className="graph-sources-retire">
    <label>停止这次未完成操作的原因<textarea maxLength={1000} value={retirementReason} disabled={locked} onChange={event=>{setConfirmRetire(null);c.setRetirementReason(event.target.value);}}/></label>
    <p>这里只停止这一次尚未保存的操作，不是停止已登记的结果来源，也不会关闭物理设备。已保存的原操作不能据此撤销。</p>
    {confirmRetire!==retirementKey?<Btn disabled={locked||!retirementReason.trim()||!c.localSaved} onClick={()=>setConfirmRetire(retirementKey)}>停止这次未完成的操作</Btn>:<div role="group" aria-label="确认停止未完成操作">
     <p>确认保留原稿并停止这一次未完成操作？</p><Btn disabled={locked||!c.localSaved} onClick={()=>{setConfirmRetire(null);perform(()=>c.retireOriginal());}}>确认停止这次操作</Btn><Btn disabled={locked} onClick={()=>setConfirmRetire(null)}>继续保留原请求</Btn>
    </div>}
   </div>}
  </aside>}
  {c.retirement&&<aside className="graph-sources-recovery" aria-label="停止未完成操作的原结果核对">
   <h3>停止未完成操作的结果待核对</h3><p>这次停止使用独立原请求，完整原内容已保留。未核对前不会重发原登记或原停止来源操作。</p>
   <p>停止原因：{c.retirement.body.reason}</p><div className="graph-sources-actions"><Btn disabled={locked} onClick={()=>perform(()=>c.recoverRetirement())}>只核对这次停止是否已保存</Btn>
    {c.retirementNotFound&&<Btn disabled={locked} onClick={()=>perform(()=>c.resendRetirement())}>按原内容重试停止操作</Btn>}</div>
  </aside>}
  {!c?<p role="status">正在核对账号与工作区…</p>:<div className="graph-sources-layout">
   <section className="graph-sources-form" aria-label="登记处理结果">
    <h3>登记处理结果</h3>
    {!canRegister&&<p>当前账号可查看登记记录；只有获服务器许可的管理员可以新增或停止供应。</p>}
    <label>结果名称<input maxLength={200} value={c.form.label} disabled={locked} placeholder="便于流程负责人辨认的名称" onChange={event=>c.edit({label:event.target.value})}/></label>
    <fieldset disabled={locked}><legend>本次登记哪一种结果</legend>{Object.entries(GRAPH_SOURCE_ADAPTERS).map(([id,label])=><label className="graph-sources-radio" key={id}><input type="radio" name={heading+'-kind'} checked={c.form.adapter_id===id} onChange={()=>c.edit({adapter_id:id})}/>{label}</label>)}</fieldset>
    <p>每次只登记一种结果。异常是待核实线索，不是现场结论。</p>
    {['source','extractor'].map(kind=>{const page=c.pages[kind],chosen=c.form[kind],inPage=chosen&&page.items.some(row=>row.id===chosen.id);return <div className="graph-sources-choice" key={kind}>
     <label>{kind==='source'?'选择已发布来源':'选择已发布处理能力'}<select disabled={locked} value={chosen?.id||''} onChange={event=>choose(kind,event.target.value)}>
      <option value="" disabled>请明确选择</option>{chosen&&!inPage&&<option value={chosen.id}>{chosen.name} · {chosen.release_version}（保留原选择）</option>}
      {page.items.map(row=><option key={row.id} value={row.id}>{row.name} · {row.release_version}</option>)}
     </select></label>{chosen?.description&&<p>{chosen.description}</p>}
     {page.loaded&&!page.items.length&&<p>{page.next_after?'本页尚无可选项，可继续读取后续记录。':'当前没有可选择的已发布能力。'}</p>}
     {more(kind,kind==='source'?'读取更多来源':'读取更多处理能力')}
    </div>;})}
    <div className="graph-sources-actions"><Btn disabled={locked||!canRegister||!!c.pending||!!c.retirement||!c.localSaved} onClick={()=>perform(()=>c.previewRegistration())}>核对登记内容</Btn>
     <Btn disabled={locked} onClick={()=>perform(()=>c.refresh())}>刷新已发布能力与登记记录</Btn></div>
    <p className="graph-sources-local">{c.localSaved?'本页输入已保存在当前浏览器。登记仍需下方明确保存。':'本页输入尚未完整保存。'}</p>
    {c.preview&&<aside className="graph-sources-preview" aria-label="登记前核对">
     <h4>登记前核对</h4><dl><dt>结果名称</dt><dd>{c.preview.body.label}</dd><dt>结果类型</dt><dd>{GRAPH_SOURCE_ADAPTERS[c.preview.body.adapter_id]}</dd>
      <dt>来源</dt><dd>{c.form.source.name} · {c.preview.contract.source_release_version}</dd><dt>处理能力</dt><dd>{c.form.extractor.name} · {c.preview.contract.extractor_release_version}</dd></dl>
     <h4>处理结果字段</h4><ResultFields schema={c.preview.contract.result_schema}/><p>这里只保存这项来源登记。返回流程后还需明确选择和采用；不会自动启用任何业务。</p>
     <Btn primary disabled={locked||!canRegister||!!c.pending||!!c.retirement||!c.localSaved} onClick={()=>perform(()=>c.register())}>登记并保存</Btn>
    </aside>}
   </section>
   <section className="graph-sources-records" aria-label="已登记的处理结果">
    <h3>已登记的处理结果</h3>
    {!c.pages.registrations.loaded?<p role="status">正在读取登记记录…</p>:c.pages.registrations.items.length===0?<p>当前工作区还没有读取到登记记录。</p>:<ul className="graph-sources-record-list">{c.pages.registrations.items.map(row=><li key={row.id}><button type="button" disabled={locked} aria-pressed={selected?.id===row.id} onClick={()=>{setConfirmStop(null);perform(()=>c.selectRegistration(row.id));}}><strong>{row.label}</strong><span>{GRAPH_SOURCE_ADAPTERS[row.contract.adapter_id]} · {row.status==='active'?'登记保留':'已停止供应'}</span><span>{row.source_name||'原登记来源'} → {row.extractor_name||'原登记处理能力'}</span></button></li>)}</ul>}
    {more('registrations','读取更多登记记录')}
    {selected&&<article className="graph-sources-detail" aria-label="登记详情">
     <h4>{selected.label}</h4><p>{GRAPH_SOURCE_ADAPTERS[selected.contract.adapter_id]} · {selected.status==='active'?'登记保留':'已停止供应'}</p>
     <dl><dt>来源</dt><dd>{selected.source_name||'原登记来源'} · {selected.contract.source_release_version}</dd><dt>处理能力</dt><dd>{selected.extractor_name||'原登记处理能力'} · {selected.contract.extractor_release_version}</dd></dl>
     <p>{selected.current_availability.available?'当前发布与权限可供流程选择；采用时仍会重新核对。':'当前不能用于新的流程选择，请核对发布版本、权限或停止状态；历史记录保留。'}</p>
     <h4>处理结果字段</h4><ResultFields schema={selected.contract.result_schema}/><p>来源登记和版本可查，不代表已收到处理结果；不证明现场状态。</p>
     {selected.status==='active'&&canRegister&&<div className="graph-sources-stop">
      <label>停止此结果来源的原因<textarea maxLength={1000} value={reason} disabled={locked} onChange={event=>{setConfirmStop(null);c.setReason(event.target.value);}}/></label>
      <p>停止此结果来源后，业务流程不能继续接收它的新结果；正在等待该来源的任务可能暂停。已保存的记录保留，物理设备不会关闭。</p>
      {!stopConfirmed?<Btn disabled={locked||!!c.pending||!!c.retirement||!reason.trim()||!c.localSaved} onClick={()=>setConfirmStop(currentStop)}>停止此结果来源</Btn>:<div className="graph-sources-confirm" role="group" aria-label="确认停止此结果来源"><p>确认按上方原因停止此结果来源？</p><Btn disabled={locked||!!c.pending||!!c.retirement||!c.localSaved} onClick={()=>{setConfirmStop(null);perform(()=>c.revoke());}}>确认停止</Btn><Btn disabled={locked} onClick={()=>setConfirmStop(null)}>继续保留来源</Btn></div>}
     </div>}
    </article>}
   </section>
  </div>}
 </section>{deliveryPanel}</>;
}
