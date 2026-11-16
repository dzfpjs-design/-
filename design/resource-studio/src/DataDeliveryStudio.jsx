import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json} from './runtime-api.js';
import {DataDeliveryControllerV245} from './data-delivery-v245-controller.mjs';
import {deliveryCanonical,hasDeliveryLocalKeys} from './data-delivery-v245-protocol.mjs';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
const statuses={pending:'尚未收件，等待原投递',blocked:'原投递被拒收',received:'数据管理已收件',blocked_integrity:'完整性待处理，收件未确认'};
const decisions={released_for_delivery:'已保存重试授权，原投递已交回接收队列',pending_retained:'已保存原申请，继续等待原投递',already_received:'已保存原申请；是否实际收件以下方当前回执为准'};
const clock=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
export default function DataDeliveryStudio({account,instanceId,observationId,entityId,sourceName='本条观测',draftChannel='observation',initialOpen=false,Btn,onReload,onRegisterGuard,api=json,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const latest=useRef(),life=useRef();if(!life.current)life.current=createRetainedControllerLifecycle();let token='';try{token=tabStorage.getItem('ga-setup-csrf')||'';}catch{}
 const owner=deliveryCanonical([account?.user_id,account?.workspace_id,account?.role,instanceId,observationId,entityId,draftChannel,token]);
 const [opened,setOpened]=useState(false),[storedView,setView]=useState(null),[localError,setLocalError]=useState(''),[localBusy,setLocalBusy]=useState(false);
 latest.current={owner,opened,localBusy,onRegisterGuard};
 const holder=useMemo(()=>{try{return {c:new DataDeliveryControllerV245({account,instanceId,observationId,entityId,draftChannel,api,storage,session:tabStorage,locks,scopeAvailable:()=>latest.current.owner===owner})};}catch(e){return {error:e.message};}},[owner,api,storage,tabStorage,locks]),c=holder.c,view=storedView?.owner===owner?storedView.value:null;
 useEffect(()=>{setLocalError('');setLocalBusy(false);setOpened(!!c&&(initialOpen||hasDeliveryLocalKeys(storage,c.scope)));},[owner,c,storage,initialOpen]);
 useEffect(()=>{
  const guard=()=>latest.current.owner===owner&&!latest.current.localBusy&&(c?(c.started?c.guard():!hasDeliveryLocalKeys(storage,c.scope)):false);latest.current.onRegisterGuard?.(guard);
  if(!c)return()=>latest.current.onRegisterGuard?.(null,guard);const lease=life.current.setup(c,owner);if(!lease)return;
  let mounted=true;c.onChange=value=>{if(mounted)setView({owner,value});};setView({owner,value:c.view()});const unload=e=>{if(!guard()){e.preventDefault();e.returnValue='';}};globalThis.addEventListener?.('beforeunload',unload);
  return()=>{mounted=false;latest.current.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);life.current.cleanup(lease);};
 },[c,owner,storage]);
 useEffect(()=>{if(opened&&c&&!c.started){c.started=true;c.start().catch(()=>{});}},[opened,c]);
 async function run(fn){setLocalError('');try{await fn();}catch(e){if(latest.current.owner===owner)setLocalError(e.message);}}
 function openPanel(){setOpened(true);if(c?.started)run(()=>c.refresh());}
 async function close(){setLocalBusy(true);setLocalError('');try{await c.prepareLeave();if(latest.current.owner===owner)setOpened(false);}catch(e){if(latest.current.owner===owner)setLocalError(e.message);}finally{if(latest.current.owner===owner)setLocalBusy(false);}}
 const busy=localBusy||view?.busy,pending=view?.pending,context=view?.context,known=pending&&view?.lastCommand?.original.body.request_id===pending.body.request_id;
 if(!opened)return <div className="data-delivery-entry"><Btn disabled={!c} onClick={openPanel}>查看这条观测的数据收件</Btn>{holder.error&&<p role="alert">{holder.error}</p>}</div>;
 if(view?.denied)return <section className="media-form" aria-label="数据收件范围已隐藏"><p role="alert">当前账号、工作区或观测范围已变化，原说明与申请保留，未采用迟到回执。</p></section>;
 return <section className="media-form" aria-label="观测数据收件与恢复"><h4>{sourceName} · 数据管理收件</h4>
  <p>这里核对本条观测的实际收件。重试只交回原投递记录，不重新采集、不调用模型，也不改写观测事实。</p>
  {[holder.error,view?.error,localError].filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i).map((value,i)=><p role="alert" key={i}>{value}</p>)}
  {!view?.authenticated?<p role="status">正在核对当前账号与所选观测，完成前已隐藏原申请与说明。</p>:<>
   {view.notice&&<p role="status">{view.notice}</p>}
   {view.lastCommand&&<article className="info" aria-label="已保存的收件重试申请"><strong>原申请已保存</strong><p>{decisions[view.lastCommand.result.decision]}。这份回执只证明申请保存，不能代替数据管理的实际收件回执。</p><p>原申请说明：{view.lastCommand.original.body.reason}</p></article>}
   {context?<article className="media-form" aria-label="当前实际收件状态"><h5>{statuses[context.state]}</h5>{context.reason&&<p>{context.reason}</p>}<p>本条观测第 {context.observation_version} 版；按固定观测与原投递凭据核对。</p>
    {context.state==='received'&&<p>独立接收记录与原投递已核对。实际收件时间：{clock(context.receipt.received_at)}（北京时间）。现场真实性仍需业务核查。</p>}
    {context.state==='pending'&&<p>继续等待原接收流程；当前不提供新的重试授权。</p>}
    {context.state==='blocked_integrity'&&<p>请先恢复原件、版本或接收凭据。本入口不能补造回执或修复历史。</p>}
    <details><summary>查看本条收件的核对凭据</summary><p>观测依据摘要：{context.observation_proof_hash}</p>{context.payload_hash&&<p>原投递内容摘要：{context.payload_hash}</p>}{context.receipt&&<p>独立收件回执摘要：{context.receipt.receipt_hash}</p>}</details>
   </article>:<p role="status">当前实际收件尚未核对；不能用旧申请回执认定已经收件。</p>}
   {pending?<article className="media-form" aria-label="原收件申请仍待核对"><h5>{known?'原申请已保存，继续核对实际收件':'原申请结果仍待核对'}</h5><p>原说明：{pending.body.reason}</p><p>原请求号和原文已固定。尚未查到回执不能证明申请没有保存；离开后返回仍沿用本次原申请。</p><div className="actions"><Btn disabled={busy||!view.localSaved} onClick={()=>run(()=>c.recover(false))}>只核对原收件申请</Btn>{!known&&<Btn disabled={busy||!view.localSaved||!['admin','operator'].includes(account.role)||context?.state!=='blocked'||!context.can_retry||context.context_hash!==pending.body.expected_context_hash} onClick={()=>run(()=>c.recover(true))}>核对后按原号原文重试</Btn>}</div></article>:context?.state==='blocked'&&context.can_retry&&['admin','operator'].includes(account.role)&&<fieldset disabled={busy||!view.localSaved||view.detachedDraft}><legend>确认只重试原数据投递</legend><label>重试原因<textarea aria-label="原数据投递重试原因" maxLength={2000} value={view.draft.reason} onChange={e=>c.edit({reason:e.target.value})}/></label><label className="cap-option"><input type="checkbox" aria-label="确认只重试原数据投递" checked={view.draft.consent} onChange={e=>c.edit({consent:e.target.checked})}/>我已核对拒收原因，只批准原投递重新交给数据管理；不重新采集或调用模型。</label><Btn primary disabled={!view.draft.reason.trim()||!view.draft.consent} onClick={()=>run(()=>c.submit())}>保存原因并重试原数据投递</Btn></fieldset>}
   {!view.localSaved&&view.loaded&&!view.stageFailedBeforePost&&<Btn disabled={busy} onClick={()=>run(()=>c.saveAgain())}>重试保存当前说明</Btn>}
   {!view.localSaved&&view.loaded&&!view.stageFailedBeforePost&&<Btn disabled={busy} onClick={()=>run(()=>c.retainDraft())}>保存当前说明副本并允许返回</Btn>}
   {view.detachedDraft&&<p role="status">当前说明已独立保留。采用下方说明副本并重新确认后，才能发出新的重试申请。</p>}
   {view.retainedDrafts.length>0&&<details><summary>保留的说明副本（{view.retainedDrafts.length}份）</summary>{view.retainedDrafts.map((item,i)=><article key={item.key}><p>第{i+1}份：{item.draft.reason||'未填写说明'}</p><Btn disabled={busy||!!pending} onClick={()=>run(()=>c.useRetainedDraft(item.key))}>使用第{i+1}份说明并重新确认</Btn></article>)}</details>}
   {!pending&&view.draft.reason&&(!context||context.state!=='blocked'||!context.can_retry)&&<p>本机保留的说明：{view.draft.reason}</p>}
  </>}
  <div className="actions">{c&&(!view?.loaded||view?.stageFailedBeforePost)&&<Btn disabled={busy} onClick={()=>run(()=>c.start())}>重新读取本机原申请</Btn>}<Btn disabled={busy||!view?.authenticated} onClick={()=>run(()=>c.refresh())}>核对当前实际收件</Btn>{onReload&&context?.state==='received'&&<Btn disabled={busy} onClick={()=>run(async()=>{await c.prepareLeave();await onReload();})}>收件已核对，刷新上方记录</Btn>}<Btn disabled={busy||!view?.loaded||!view?.localSaved} onClick={close}>返回观测，保留原申请与说明</Btn></div>
 </section>;
}
