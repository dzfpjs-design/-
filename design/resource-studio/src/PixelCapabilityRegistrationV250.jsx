import React,{useEffect,useMemo,useRef,useState} from 'react';
import {PixelRegistrationControllerV250} from './workflow-pixel-registration-v250-controller.mjs';
import {pixelCanonical} from './pixel-change-v249-protocol.mjs';
import {json,api as binary} from './runtime-api.js';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
export default function PixelCapabilityRegistrationV250({account,instanceId,referenceRunId,Btn,onRegisterGuard,onUseFlow,request=json,readBinary=binary,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const latest=useRef(),life=useRef();if(!life.current)life.current=createRetainedControllerLifecycle();let token='';try{token=tabStorage.getItem('ga-setup-csrf')||'';}catch{}
 const owner=pixelCanonical([account?.user_id,account?.workspace_id,account?.role,instanceId,referenceRunId,token]),[stored,setStored]=useState(null),[error,setError]=useState('');latest.current={owner,onRegisterGuard};
 const holder=useMemo(()=>{try{return {c:new PixelRegistrationControllerV250({account,instanceId,referenceRunId,request,readBinary,storage,session:tabStorage,locks,current:()=>latest.current.owner===owner})};}catch(e){return {error:e.message};}},[owner,request,readBinary,storage,tabStorage,locks]),c=holder.c,v=stored?.owner===owner?stored.value:null;
 useEffect(()=>{setError('');const guard=()=>latest.current.owner===owner&&!!c&&c.guard();latest.current.onRegisterGuard?.(guard);if(!c)return()=>latest.current.onRegisterGuard?.(null,guard);const lease=life.current.setup(c,owner);if(!lease)return;let mounted=true;c.onChange=value=>{if(mounted)setStored({owner,value});};setStored({owner,value:c.view()});const unload=e=>{if(!guard()){e.preventDefault();e.returnValue='';}};globalThis.addEventListener?.('beforeunload',unload);return()=>{mounted=false;latest.current.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);life.current.cleanup(lease);};},[owner,c]);
 useEffect(()=>{if(c&&!c.started){c.started=true;c.start().catch(()=>{});}},[c]);
 async function run(fn){setError('');try{await fn();}catch(e){if(latest.current.owner===owner)setError(e.message);}}
 if(v?.denied)return <section aria-label="图像能力登记范围已隐藏"><p role="alert">当前账号或工作区已变化，原登记回执和原操作已隐藏并保留。</p></section>;
 return <section className="pixel-install" aria-label="登记两期PNG候选处理能力"><h5>用于业务流程</h5><p>这份成功结果仅作为能力登记依据。后续每个业务任务仍须明确选择自己的两张原图，并重新运行本地模型；不会复制这份样例结果。</p>
  {[holder.error,v?.error,error].filter(Boolean).filter((e,i,a)=>a.indexOf(e)===i).map((message,i)=><p role="alert" key={i}>{message}</p>)}
  {!v?.authenticated?<p role="status">正在核对原样例、当前身份与能力登记条件…</p>:<>
   {v.notice&&<p role="status">{v.notice}</p>}
   {v.result&&<article aria-label="已保存的像素能力登记"><strong>本地候选处理能力已登记</strong><p>版本 {v.result.capability.version} · 固定阈值 {v.result.threshold_text} · 本地CPU · 模型安装第{v.result.model_trace.generation}代</p><p>尚未加入或启用任何业务，也不表示领域质量通过。请在业务与流程刷新实际能力清单，选择本地两期PNG变化候选能力，配置负责人并重新确认。</p>{onUseFlow&&<Btn disabled={v.busy||!v.localSaved} onClick={()=>run(async()=>{if(!c.guard())throw Error('原登记尚未可靠保留，先重试保存。');await onUseFlow();})}>前往业务与流程选择此能力</Btn>}</article>}
   {v.options&&<><p>登记固定条件：阈值 {v.options.threshold_text} · 本地CPU · 模型安装第{v.options.model_trace.generation}代。仅输出像素候选，不计算地理面积、不更新对象状态、不自动发布经验。</p>{v.options.reasons.map((reason,i)=><p role="status" key={i}>{reason}</p>)}</>}
   {!v.localSaved&&<article role="alert"><p>原登记尚未在本机完整保存，未发送新的登记请求。请保留此页，恢复存储后补存同一次原操作。</p><Btn disabled={v.busy} onClick={()=>run(()=>c.repair())}>重试保存原登记（不发送）</Btn></article>}
   {v.pending&&<article aria-label="原像素能力登记待核对"><p>登记结果暂未确认，原申请已保留。原文、原样例与原请求固定；尚未找到回执不能证明没有登记。</p><div className="actions"><Btn disabled={v.busy||!v.localSaved} onClick={()=>run(()=>c.recover())}>只核对原登记结果</Btn><Btn disabled={v.busy||!v.localSaved||account.role!=='admin'} onClick={()=>run(()=>c.resend())}>明确按原登记重试</Btn></div><p>按原登记重试可能实际登记尚未保存的原请求；不会执行模型，也不会自动创建或启用业务。</p></article>}
   {!v.result&&!v.pending&&<Btn primary disabled={v.busy||!v.loaded||!v.localSaved||account.role!=='admin'||!v.options?.can_register} onClick={()=>run(()=>c.register())}>登记为本地候选处理能力</Btn>}
   <Btn disabled={v.busy} onClick={()=>run(()=>c.refresh())}>重新核对登记条件</Btn>
   {account.role!=='admin'&&<p>新能力由管理员登记；原发起人仍可核对自己的历史回执。</p>}
  </>}
 </section>;
}
