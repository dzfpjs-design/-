import React,{useEffect,useId,useRef,useState} from 'react';
import {json} from './runtime-api';
import {GraphSourceDeliveryControllerV124,validDeliveryRetirementReason} from './graph-source-delivery-controller-v124.mjs';
import './graph-source-delivery-v124.css';

function NativeButton({primary,children,...props}){return <button type="button" {...props}>{children}</button>;}
function beijing(value){if(value===null)return '未记录';try{return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value))+'（北京时间）';}catch{return '记录时间暂无法显示';}}
function status(record){if(record.intent_status==='admitted')return '已送达';return {queued:'等待送达',running:'正在送达',failed:'送达未完成',blocked:'送达未完成',succeeded:'送达状态待核对',cancelled:'送达已停止'}[record.delivery?.status]||'送达情况待核对';}
function actionText(record){return {in_progress:'结果仍在等待或正在送达，可更新情况。',missing_job:'缺少原送达记录，请联系管理员核对；处理结果保留。',no_permission:'当前账号可以核对记录，不能恢复送达。',receipt_available:'结果已送达，可核对已送达结果并更新状态。',already_admitted:'结果已送达，无需再次恢复。',retry_limit:'已达到本条结果的恢复上限，请联系管理员核对来源。',retry_available:'可恢复本条处理结果的送达，不会重新调用模型生成结果。',registration_stopped:'此结果来源已停止，原记录保留，暂不能恢复送达。',no_execution_permission:'当前账号不能恢复送达，原记录保留。',source_unavailable:'来源或处理能力已变化，请联系管理员核对原发布版本及权限。'}[record.action.reason_code]||'送达情况暂无法完整核对，请联系管理员；原记录保留。';}
function ResultEvidence({result}){
 if(!result)return <p>尚未读取实际处理结果。可以更新本条结果详情。</p>;
 const rows=[];let incomplete=false,visited=0;
 const scalar=value=>{if(value===null)return '未提供';if(typeof value==='boolean')return value?'是':'否';if(typeof value==='number')return Number.isFinite(value)?String(value):'数值暂无法显示';if(typeof value==='string'){if(/^[0-9a-f]{64}$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)){incomplete=true;return '标识信息保存在原记录中';}if(value.length>1000){incomplete=true;return value.slice(0,1000)+'…';}return value||'空白文本';}incomplete=true;return '此项需查看原记录';};
 function visit(schema,value,label,depth){
  if(++visited>80||depth>5){incomplete=true;return;}
  if(value!==null&&typeof value==='object'){
   if(Array.isArray(value)){if(!value.length)rows.push({label,value:'没有条目'});value.slice(0,20).forEach((item,index)=>visit(schema?.items,item,label+' · 第'+(index+1)+'项',depth+1));if(value.length>20)incomplete=true;}
   else {const entries=Object.entries(value);if(!entries.length)rows.push({label,value:'没有字段值'});for(const [index,[key,item]] of entries.entries()){const field=schema?.properties?.[key],name=field?.title||field?.description||('结果字段 '+(index+1));visit(field,item,label==='处理结果'?name:label+' · '+name,depth+1);}}
  }else rows.push({label,value:scalar(value)});
 }
 visit(result.schema,result.value,'处理结果',0);
 return <div className="graph-delivery-evidence"><dl>{rows.map((row,index)=><React.Fragment key={index}><dt>{row.label}</dt><dd>{row.value}</dd></React.Fragment>)}</dl>{incomplete&&<p role="note">当前只展示可读范围，不能据此当作完整结果。其余内容仍保存在原记录，请联系管理员核对原件。</p>}</div>;
}

export default function GraphSourceDeliveryPanelV124({actorID,workspaceID,producer=null,Btn=NativeButton,onBusy,concealed=false,blocked=false}){
 const heading=useId(),controller=useRef(null),alive=useRef(false),callbacks=useRef({onBusy});callbacks.current={onBusy};
 const [,render]=useState(0),[fatal,setFatal]=useState(''),[confirmation,setConfirmation]=useState(null),[endConfirmation,setEndConfirmation]=useState(null);
 const initialProducer=useRef(producer?.id||null);initialProducer.current=producer?.id||null;
 useEffect(()=>{
  alive.current=true;let current;
  const changed=()=>{if(alive.current&&controller.current===current){callbacks.current.onBusy?.(current.busy||!current.localSaved);render(n=>n+1);}};
  try{current=new GraphSourceDeliveryControllerV124({api:json,scope:{actor_id:actorID,workspace_id:workspaceID},onChange:changed});controller.current=current;setFatal('');changed();current.open(initialProducer.current).catch(()=>{});}
  catch{controller.current=null;setFatal('账号或工作区暂无法核对，原记录保留。');}
  const leave=event=>{if(current&&(current.busy||!current.localSaved)){event.preventDefault();event.returnValue='';}};
  const wake=()=>{if(document.visibilityState!=='hidden')current?.recheckIdentity().catch(()=>{});};
  window.addEventListener('beforeunload',leave);window.addEventListener('focus',wake);document.addEventListener('visibilitychange',wake);
  return()=>{alive.current=false;current?.dispose();if(controller.current===current)controller.current=null;callbacks.current.onBusy?.(false);window.removeEventListener('beforeunload',leave);window.removeEventListener('focus',wake);document.removeEventListener('visibilitychange',wake);};
 },[actorID,workspaceID]);
 const candidate=controller.current,c=candidate?.scope.actor_id===actorID&&candidate?.scope.workspace_id===workspaceID?candidate:null;
 useEffect(()=>{const current=controller.current;if(current&&!current.disposed&&current.scope.actor_id===actorID&&current.scope.workspace_id===workspaceID&&current.list.producer_id!==(producer?.id||null))current.selectProducer(producer?.id||null).catch(()=>{});},[actorID,workspaceID,producer?.id]);
 async function perform(callback){try{await callback();}catch{}if(alive.current)render(n=>n+1);}
 const visible=c&&!c.corrupt&&c.identityStatus==='verified'&&!concealed,locked=!c||c.busy||c.corrupt||blocked,d=c?.detail;
 const confirmKey=d?d.intent_id+':'+d.delivery?.version+':'+c.reason:null;
 const endKey=c?.pending?c.pending.intent_id+':'+c.pending.body.request_id+':'+c.pending.command_hash+':'+c.retirementReason:null;
 return <section className="graph-source-delivery-v124" aria-labelledby={heading}>
  <header><div><h2 id={heading}>处理结果送达情况</h2><p>核对已处理结果是否送达，并找回上次恢复操作。不会重新运行模型，也不会关闭物理设备。</p></div></header>
  {!visible?<div className="graph-delivery-concealed">
   <p role={fatal||c?.corrupt||c?.identityStatus==='concealed'?'alert':'status'}>{c?.corrupt?'本机恢复记录暂无法读取，原内容保留；请核对浏览器存储。':concealed?'送达记录暂时隐藏，请先核对当前页面的登录或保存范围。':fatal||c?.identityStatus==='concealed'?'当前登录身份尚无法核对，本机记录保留；请核对登录后重试。':c?.identityStatus==='checking'?'正在核对登录，本机记录保留。':'送达记录暂时隐藏，请先核对当前页面的登录或保存范围。'}</p>
   <p>身份核对通过前，不展示处理结果、本机说明或原操作。</p><Btn disabled={!c||c.busy||blocked||concealed} onClick={()=>perform(()=>c.recheckIdentity())}>重新核对送达页登录</Btn>
  </div>:<>
   {c.error&&<p role="alert" className="graph-delivery-problem">{c.error}</p>}{c.notice&&<p role="status" className="graph-delivery-notice">{c.notice}</p>}
   {!c.localSaved&&<aside role="alert"><p>当前说明或结束理由尚未完整保存，输入保留在本页。请恢复存储或缩短内容后重试，暂不能提交或离开。</p><Btn disabled={c.busy||blocked} onClick={()=>c.saveAllLocal()}>重试保存恢复说明</Btn></aside>}
   <section className="graph-delivery-local" aria-label="找回上次送达恢复操作">
    <h3>找回上次恢复操作</h3><p>换窗口或重新登录后，可选择本机保留记录，只向服务器核对原结果；不会自动再次提交。</p>
    <Btn disabled={locked} onClick={()=>perform(()=>c.discoverRecovery())}>读取本机恢复记录</Btn>
    {c.recovery.problem_count>0&&<p role="alert">部分本机记录暂无法读取，原内容保留，没有用空白内容替代。</p>}
    {c.recovery.loaded&&!c.recovery.items.length&&<p>{c.recovery.next_offset!==null?'本段尚无可恢复项，可继续读取。':'当前账号与工作区没有读取到本机恢复记录。'}</p>}
    <ul>{c.recovery.items.map((row,index)=><li key={row.key+':'+index}><div><strong>保留的送达恢复操作</strong><p>{row.local_completion?'本机曾记下结果，仍需向服务器核对。':'完整原说明已保留，选择后只核对上次恢复结果。'}</p><p className="graph-delivery-original-reason">{JSON.parse(row.raw).body.reason}</p></div><Btn disabled={locked||!c.localSaved} onClick={()=>perform(()=>c.restoreRecovery(row))}>选择并核对上次恢复结果</Btn></li>)}</ul>
    {c.recovery.next_offset!==null&&<Btn disabled={locked} onClick={()=>perform(()=>c.discoverRecovery(true))}>读取更多本机恢复记录</Btn>}
   </section>
   <section className="graph-delivery-local" aria-label="找回未完成恢复操作的结束结果">
    <h3>找回结束操作的结果</h3><p>结束操作也有独立的保存记录。换窗口后明确选择原记录，只核对服务器结果，不自动重发。</p>
    <Btn disabled={locked} onClick={()=>perform(()=>c.discoverRetirements())}>读取本机结束操作记录</Btn>
    {c.retirementRecovery.problem_count>0&&<p role="alert">部分结束操作记录暂无法读取，原内容保留。</p>}
    {c.retirementRecovery.loaded&&!c.retirementRecovery.items.length&&<p>{c.retirementRecovery.next_offset!==null?'本段尚无记录，可继续读取。':'当前账号与工作区没有读取到结束操作记录。'}</p>}
    <ul>{c.retirementRecovery.items.map((row,index)=>{const body=JSON.parse(row.raw).body;return <li key={row.key+':'+index}><div><strong>保留的结束操作</strong><p>原恢复说明：{body.original.reason}</p><p>结束理由：{body.reason}</p><p>{row.local_completion?'本机曾记下结果，仍需向服务器核对。':'完整原内容已独立保留。'}</p></div><Btn disabled={locked||!c.localSaved} onClick={()=>perform(()=>c.restoreRetirement(row))}>选择并核对结束结果</Btn></li>;})}</ul>
    {c.retirementRecovery.next_offset!==null&&<Btn disabled={locked} onClick={()=>perform(()=>c.discoverRetirements(true))}>读取更多结束操作记录</Btn>}
   </section>
   {c.pending&&<aside className="graph-delivery-pending" aria-label="上次送达恢复结果待核对">
    <h3>上次恢复结果待核对</h3><p>完整原内容已独立保存。这里只核对原操作，不会按当前选择的来源改写。</p><p>原恢复说明：{c.pending.body.reason}</p>
    <Btn disabled={locked} onClick={()=>perform(()=>c.recover())}>核对上次恢复结果</Btn>
    {c.pendingNotFound&&!c.retirement&&<><p>服务器尚未找到这次恢复操作。即使结果已送达，也不代表这次恢复操作已保存；明确重试将保持完整原内容。</p><Btn disabled={locked||!c.localSaved} onClick={()=>perform(()=>c.resend())}>按原内容重试恢复送达</Btn>
     <section className="graph-delivery-end"><h4>结束这次未完成的恢复操作</h4><p>只结束这一条尚未保存的恢复申请，不撤销原处理结果或已送达消息，也不停止来源。原说明和记录保留；明确结束后，可核对当前结果详情再恢复送达。</p>
      <label>结束这次恢复操作的理由<textarea value={c.retirementReason} disabled={locked} onChange={event=>{setEndConfirmation(null);c.setRetirementReason(event.target.value);}}/></label>
      <Btn disabled={locked||!c.localSaved} onClick={()=>perform(()=>c.loadRetirementReasonDrafts())}>读取本条操作保留的结束理由</Btn>
      {c.retirementReasonRecoveries.problem_count>0&&<p role="alert">部分结束理由暂无法读取，原内容保留。</p>}
      <ul>{c.retirementReasonRecoveries.items.map((row,index)=><li key={row.key+':'+index}><p>{row.reason}</p><Btn disabled={locked||!c.localSaved} onClick={()=>{setEndConfirmation(null);perform(()=>c.restoreRetirementReason(row));}}>保留当前理由并恢复此稿</Btn></li>)}</ul>
      {c.retirementReasonRecoveries.next_offset!==null&&<Btn disabled={locked} onClick={()=>perform(()=>c.loadRetirementReasonDrafts(true))}>读取更多结束理由</Btn>}
      {endConfirmation!==endKey?<Btn disabled={locked||!c.localSaved||!validDeliveryRetirementReason(c.retirementReason)} onClick={()=>setEndConfirmation(endKey)}>结束这次未完成的恢复操作</Btn>:<div role="group" aria-label="确认结束未完成的恢复操作"><p>确认仅结束这一条未完成的恢复申请，保留原处理结果、消息和来源？</p><Btn disabled={locked||!c.localSaved||!validDeliveryRetirementReason(c.retirementReason)} onClick={()=>{setEndConfirmation(null);perform(()=>c.retireOriginal());}}>确认结束这次恢复操作</Btn><Btn disabled={locked} onClick={()=>setEndConfirmation(null)}>继续保留原恢复操作</Btn></div>}
     </section>
    </>}
   </aside>}
   {c.retirement&&<aside className="graph-delivery-pending" aria-label="结束恢复操作的原结果待核对"><h3>结束操作的结果待核对</h3><p>完整原内容已独立保存。核对前不会重发原恢复申请，也不会另建结束申请。</p><p>原恢复说明：{c.retirement.body.original.reason}</p><p>结束理由：{c.retirement.body.reason}</p><Btn disabled={locked} onClick={()=>perform(()=>c.recoverRetirement())}>核对这次结束是否已保存</Btn>{c.retirementNotFound&&<Btn disabled={locked||!c.localSaved} onClick={()=>perform(()=>c.resendRetirement())}>按原内容重试结束操作</Btn>}</aside>}
   {c.lastRetirement&&<aside aria-label="已核对的结束事实"><h3>未完成的恢复操作已结束</h3><p>原恢复说明：{c.lastRetirement.command.original.reason}</p><p>服务器已保存结束记录。这不撤销原处理结果或已送达消息，也不停止来源。原说明保留；再次恢复送达前，请核对当前结果详情。</p></aside>}
   {(c.lastReceipt||c.lastLegacy)&&<aside aria-label="已核对的上次恢复结果"><h3>上次恢复操作已核对</h3><p>{c.lastLegacy?'已找到旧版保存记录；它没有独立回执或保存时间。':c.lastReceipt.result.outcome==='queued'?'上次恢复操作已保存，当时已安排送达；当前进度请看下方送达情况。':'恢复操作已保存，结果已送达。'}</p><p>这里显示上次操作的保存结果；下方送达情况是另行读取的当前状态。</p></aside>}
   <div className="graph-delivery-layout">
    <section aria-label="处理结果送达列表"><h3>{c.list.producer_id?(c.list.producer_id===producer?.id?producer.label+'的送达记录':(c.list.items[0]?.source_label||'此前选择来源')+'的送达记录'):'选择来源查看送达记录'}</h3>
     <p>这里只显示当前账号实际处理的结果。更换来源不会清除上次恢复操作或已经打开的原结果详情。</p>
     {!c.list.producer_id?<p>请先在上方选择已登记的处理结果；找回本机原操作不需要先选择来源。</p>:<><Btn disabled={locked} onClick={()=>perform(()=>c.selectProducer(c.list.producer_id))}>更新送达列表</Btn>
      {!c.list.loaded?<p role="status">正在读取送达记录…</p>:!c.list.items.length?<p>{c.list.next_after?'本页尚无记录，可继续读取。':'当前账号在此来源下没有读取到送达记录。'}</p>:<ul className="graph-delivery-records">{c.list.items.map(row=><li key={row.intent_id}><button type="button" disabled={locked||!c.localSaved} aria-pressed={d?.intent_id===row.intent_id} onClick={()=>{setConfirmation(null);perform(()=>c.selectIntent(row.intent_id));}}><strong>{row.object.name}</strong><span>{status(row)} · {row.source_label}</span><span>记录时间：{beijing(row.observed_at)}</span></button></li>)}</ul>}
      {c.list.next_after&&<Btn disabled={locked} onClick={()=>perform(()=>c.loadMore())}>读取更多送达记录</Btn>}
     </>}
    </section>
    {d&&<article className="graph-delivery-detail" aria-label="实际处理结果详情"><h3>{d.object.name} · {status(d)}</h3>
     <dl><dt>实际结果来源</dt><dd>{d.source_label}</dd><dt>记录时间</dt><dd>{beijing(d.observed_at)}</dd>{d.admission&&<><dt>送达时间</dt><dd>{beijing(d.admission.admitted_at)}</dd></>}</dl>
     <p>{d.intent_status==='admitted'?'结果已送达；是否用于某个任务、任务是否完成，请到任务详情核对。':actionText(d)}</p>
     <p className="graph-delivery-boundary">{d.candidate_only?'这是待核实的处理结果，不能直接作为现场事实。':'这是处理结果记录，不是现场事实认证。'}记录中的观测时间尚未独立认证，也不证明设备健康或物理状态。</p>
     <h4>实际处理结果</h4><ResultEvidence result={d.result}/>
     <Btn disabled={locked||!c.localSaved} onClick={()=>perform(()=>c.selectIntent(d.intent_id))}>更新本条结果详情</Btn>
     <section aria-label="本条结果的恢复说明"><h4>恢复说明</h4><label>本条结果的恢复说明<textarea value={c.reason} disabled={locked} onChange={event=>{setConfirmation(null);c.editReason(event.target.value);}}/></label>
      <p>{c.localSaved?'说明已保存在当前浏览器。输入说明不会自动提交恢复。':'说明尚未完整保存。'}提交说明最多1000个字符。</p>
      <Btn disabled={locked||!c.localSaved} onClick={()=>perform(()=>c.loadReasonDrafts())}>读取本条结果保留的说明</Btn>
      {c.reasonRecoveries.problem_count>0&&<p role="alert">部分说明暂无法读取，原内容保留。</p>}
      <ul>{c.reasonRecoveries.items.map((row,index)=><li key={row.key+':'+index}><p>{row.reason}</p><Btn disabled={locked||!c.localSaved} onClick={()=>{setConfirmation(null);perform(()=>c.restoreReason(row));}}>保留当前说明并恢复此稿</Btn></li>)}</ul>
      {c.reasonRecoveries.next_offset!==null&&<Btn disabled={locked} onClick={()=>perform(()=>c.loadReasonDrafts(true))}>读取更多保留说明</Btn>}
      {d.action.allowed?<><p>{actionText(d)}</p>{confirmation!==confirmKey?<Btn primary disabled={locked||!c.localSaved||!!c.pending||!!c.retirement||!c.reason.trim()} onClick={()=>setConfirmation(confirmKey)}>{d.action.kind==='reconcile_receipt'?'核对已送达结果并更新状态':'恢复送达'}</Btn>:<div role="group" aria-label="确认恢复送达"><p>按上方说明恢复这一条实际处理结果？不会重新调用模型。</p><Btn primary disabled={locked||!c.localSaved||!!c.pending||!!c.retirement} onClick={()=>{setConfirmation(null);perform(()=>c.submitRetry());}}>确认恢复送达</Btn><Btn disabled={locked} onClick={()=>setConfirmation(null)}>继续核对</Btn></div>}</>:<p>{actionText(d)}</p>}
     </section>
    </article>}
   </div>
  </>}
 </section>;
}
