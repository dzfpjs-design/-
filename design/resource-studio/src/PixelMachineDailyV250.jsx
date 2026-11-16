import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json,api as binary} from './runtime-api.js';
import {pixelCanonical} from './pixel-change-v249-protocol.mjs';
import {dailyRebuildPins} from './pixel-machine-daily-v250-protocol.mjs';
import {PixelMachineDailyControllerV250} from './pixel-machine-daily-v250-controller.mjs';
import {PixelMachineRecordV250} from './TaskPixelResultsV250.jsx';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
const clock=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
export default function PixelMachineDailyV250({account,instanceId,day,Btn,onRegisterGuard,onViewTask,onRecoveryDay,request=json,readBinary=binary,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const latest=useRef(),life=useRef();if(!life.current)life.current=createRetainedControllerLifecycle();let token='';try{token=tabStorage.getItem('ga-setup-csrf')||'';}catch{}
 const owner=pixelCanonical([account?.user_id,account?.workspace_id,account?.role,instanceId,day,token]),[stored,setStored]=useState(null),[error,setError]=useState('');latest.current={owner,onRegisterGuard,onViewTask,onRecoveryDay};
 const holder=useMemo(()=>{try{return {c:new PixelMachineDailyControllerV250({account,instanceId,day,request,readBinary,storage,session:tabStorage,locks,current:()=>latest.current.owner===owner})};}catch(e){return {error:e.message};}},[owner,request,readBinary,storage,tabStorage,locks]),c=holder.c,v=stored?.owner===owner?stored.value:null;
 useEffect(()=>{setError('');const guard=()=>latest.current.owner===owner&&!!c&&c.guard();latest.current.onRegisterGuard?.(guard);if(!c)return()=>latest.current.onRegisterGuard?.(null,guard);const lease=life.current.setup(c,owner);if(!lease)return;let mounted=true;c.onChange=value=>{if(mounted)setStored({owner,value});};setStored({owner,value:c.view()});const unload=e=>{if(!guard()){e.preventDefault();e.returnValue='';}};globalThis.addEventListener?.('beforeunload',unload);return()=>{mounted=false;latest.current.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);life.current.cleanup(lease);};},[owner,c]);
 useEffect(()=>{if(c&&!c.started){c.started=true;c.start().catch(()=>{});}},[c]);
 async function run(fn){setError('');try{await fn();}catch(e){if(c?.active&&!c.suspended&&latest.current.owner===owner)setError(e.message);}}
 async function openTask(id){await c.access.identity();if(!c.guard())throw Error('本次汇总原因或原申请尚未可靠保留，请先保存。');const check=()=>{c.check();if(latest.current.owner!==owner||!c.guard())throw Error('账号或登记日已变化，没有打开旧任务。');};check();await latest.current.onViewTask?.(id,check);check();}
 async function selectRecoveryDay(value){await c.access.identity();if(!c.guard())throw Error('当前原因尚未可靠保留，未切换登记日。');await latest.current.onRecoveryDay?.(value);}
 if(v?.denied)return <section aria-label="机器日汇总范围已隐藏"><p role="alert">账号、实例或登记日范围已变化，原原因与原申请已隐藏并保留。</p></section>;
 const current=v?.value,editorDisabled=v?.busy||!!v?.pending||!v?.formLoaded||!['admin','operator'].includes(account.role),pinsMatch=!!current&&pixelCanonical(v?.draft.pins)===pixelCanonical(dailyRebuildPins(current.rebuild)),reasonLength=Array.from(v?.draft.reason||'').length,validReason=reasonLength>=1&&reasonLength<=2000&&!!v?.draft.reason.trim();
 return <section className="media-form pixel-pair-studio" aria-label="像素机器成果按登记日汇总"><h3>登记日 {day} · 机器成果汇总</h3><p>按机器成果真实登记的北京时间日期统计。登记日不是拍摄日，也不是接收日；晚到接收仍补到原登记日。指标只来自独立接收的机器计算，不是对象数量、面积或现场变化结论。</p>
  {[holder.error,v?.error,error].filter(Boolean).filter((e,i,a)=>a.indexOf(e)===i).map((message,i)=><p role="alert" key={i}>{message}</p>)}
  {!v?.authenticated?<p role="status">正在核对原登记日、账号与已保存汇总…</p>:<>
   {v.notice&&<p role="status">{v.notice}</p>}
   {v.recoveryDays.filter(value=>value!==day).map(value=><p key={value}>登记日{value}还有原重建申请待核对。<Btn disabled={v.busy} onClick={()=>run(()=>selectRecoveryDay(value))}>核对{value}原汇总申请</Btn></p>)}
   {v.saved.length>0&&<details><summary>已保存的原汇总重建命令</summary>{v.saved.map(row=><article key={row.original.body.request_id}><p>{row.result.registration_day} · 原命令已保存为第{row.result.summary.version}版 · {clock(row.result.summary.created_at)}（北京时间）</p><p>原原因：{row.original.body.reason}</p><p>这是原命令保存回执，当前日状态以下方重新读取结果为准；旧版本与原错误记录保留。</p></article>)}</details>}
   {v.pending&&<article aria-label="原登记日汇总重建待核对"><h4>原日汇总重建结果暂未确认</h4><p>原登记日：{v.pending.body.registration_day}；原原因：{v.pending.body.reason}</p><p>原申请已保留，尚未找到回执不能证明没有保存。核对不会自动重建，不会换号。</p>{!v.localSaved&&<Btn disabled={v.busy} onClick={()=>run(()=>c.repair())}>重试保存原日汇总申请（不发送）</Btn>}<div className="actions"><Btn disabled={v.busy||!v.localSaved} onClick={()=>run(()=>c.recover())}>只核对原日汇总申请</Btn><Btn disabled={v.busy||!v.localSaved||!['admin','operator'].includes(account.role)} onClick={()=>run(()=>c.resend())}>明确按原日汇总申请重试</Btn></div><p>明确重试可能实际保存尚未保存的原重建；仍只处理本地汇总，不运行模型或改写收件。</p></article>}
   <Btn disabled={v.busy||v.reading} onClick={()=>run(()=>c.refresh())}>重新读取这天的实际汇总</Btn>{v.reading&&<p role="status">正在核对该日原件、独立接收与保存版本…</p>}
   {current?.summary&&<p>已保存日版本：第{current.summary.version}版 · {clock(current.summary.created_at)}（北京时间）。{current.summary_state==='stale'?'原版本保留，当前登记或接收已有更新。':current.summary_state==='blocked_integrity'?'当前依据尚未通过核对，旧版本元数据保留。':'本次读取已核对当前机器汇总。'}</p>}
   {current?.summary_state==='blocked_integrity'?<article role="alert"><h4>当前日汇总的原始依据未通过核对</h4><p>{current.integrity_issue}</p><p>当前数量与指标暂不显示，原错误记录和旧版本保留。恢复原依据后，可明确填写原因重建本地汇总。</p></article>:current&&<><p>已登记成果 {current.counts.registered_results}份 · 已独立接收 {current.counts.received_results}份 · 等待接收 {current.counts.waiting_results}份</p>{current.metrics.candidate_pixels===null?<p>尚无可引用的独立接收计算指标，不能按零候选解释。</p>:<p className="info">已接收结果共 {current.metrics.candidate_pixels.toLocaleString('zh-CN')} 个候选像素 · 有像素范围 {current.metrics.common_valid_pixels.toLocaleString('zh-CN')} · 缺少像素 {current.metrics.unknown_pixels.toLocaleString('zh-CN')}。{current.metrics.candidate_pixels===0?'本次已接收模型未检出变化候选，仍可能存在漏检。':'候选需结合各原任务核对，不合并为对象事实。'}</p>}{current.summary_state==='not_saved'&&<p>目前尚无该日已保存版本；本次读取没有自动重建。</p>}</>}
   {current?.blocked_record&&<p>此前完整性问题记录保留，记录时间：{clock(current.blocked_record.created_at)}（北京时间）。</p>}
   {current?.records.map(entry=><PixelMachineRecordV250 key={entry.run.id+':'+entry.run.version} entry={entry} Btn={Btn} dataOnly daySupplement busy={v.busy} onOpenTask={onViewTask?id=>run(()=>openTask(id)):undefined}/>)}
   <p>人工核查（当前补充）来自原任务最新已完成人工回执，不是当日机器快照的一部分。机器汇总、人工办理和最终业务验收分别保存。</p>
   <section aria-label="明确重建登记日机器汇总"><h4>恢复原依据后，明确重建本地汇总</h4>{current?.rebuild.reason&&<p>{current.rebuild.reason}</p>}
    {v.detached&&<p role="status">当前原因已保留独立副本，另一页面的稿未被覆盖。先在下方恢复需要的副本，才可重建。</p>}
    {!pinsMatch&&current&&<article className="info"><p>汇总版本或原完整性问题已有变化。原原因保留，不能静默换成当前条件。</p><Btn disabled={editorDisabled||!v.localSaved||v.detached} onClick={()=>run(()=>c.adoptCurrent())}>保留原因并采用当前汇总版本</Btn></article>}
    <fieldset disabled={editorDisabled}><legend>重建原因与范围</legend><label>重建原因<textarea aria-label="登记日汇总重建原因" rows={3} value={v.draft.reason} onChange={e=>run(()=>c.edit({reason:e.target.value}))}/></label><p>请填写1至2000字有效原因，当前{reasonLength}字。保存失败时仍可继续修改纠正。</p><label className="cap-option"><input aria-label="确认只重建本地登记日汇总" type="checkbox" checked={v.draft.confirm_rebuild} onChange={e=>run(()=>c.edit({confirm_rebuild:e.target.checked}))}/>我已核对原依据，只重建这天的本地机器汇总；不运行模型、不改写收件或人工记录。</label></fieldset>
    {!v.localSaved&&!v.pending&&<p role="alert">当前原因尚未可靠保存，请保留本页，继续修改或重试保存。</p>}
    <div className="actions"><Btn disabled={v.busy||!!v.pending||!v.formLoaded} onClick={()=>run(()=>c.saveAgain())}>重试保存本次汇总原因</Btn><Btn disabled={v.busy||!!v.pending||!v.formLoaded} onClick={()=>run(()=>c.retainDraft())}>另存当前原因副本</Btn><Btn primary disabled={editorDisabled||!current?.rebuild.can_rebuild||!pinsMatch||!v.localSaved||v.detached||v.submitted||!validReason||!v.draft.confirm_rebuild} onClick={()=>run(()=>c.submit())}>保存原因并重建这天的汇总</Btn></div>
    {v.submitted&&<><p>这一轮重建已保存，不重复发送。需要再次核对时先明确准备新草稿。</p><Btn disabled={v.busy||!!v.pending||!current} onClick={()=>run(()=>c.prepareNew())}>保留原命令，准备新一轮核对</Btn></>}
    {v.history.length>0&&<details><summary>恢复本机汇总原因副本</summary>{v.history.map((row,i)=><article key={row.key}><p>副本{i+1}：{row.value.reason||'尚未填写原因'}</p><Btn disabled={v.busy||!!v.pending} onClick={()=>run(()=>c.restoreDraft(row.key))}>保留两份原稿并使用原因副本{i+1}</Btn></article>)}</details>}
   </section>
  </>}
 </section>;
}
