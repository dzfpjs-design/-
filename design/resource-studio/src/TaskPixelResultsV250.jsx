import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json,api as binary} from './runtime-api.js';
import {pixelCanonical} from './pixel-change-v249-protocol.mjs';
import {TaskPixelResultsControllerV250} from './workflow-pixel-results-v250-controller.mjs';
import {PixelPairInputsV250,PixelRunEvidenceV250} from './PixelMachineEvidenceV250.jsx';
import DataDeliveryStudio from './DataDeliveryStudio.jsx';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
import './pixel-pair-v249.css';
const clock=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚无已核对时刻';
export const pixelReceptionLabels={waiting_data:'等待数据管理智能体接收完成',received:'数据管理智能体已独立接收',blocked_integrity:'原始数据核对未通过',producer_failed:'模型结果已保存，原结果交接未完成'};
export function PixelMachineRecordV250({entry,Btn,onOpenTask,readOriginal,readArtifact,settle,busy=false,dataOnly=false,daySupplement=false}){
 const run=entry.run,r=entry.reception,result=run.result;
 return <article className="pixel-focused" aria-label="一份任务像素机器成果"><h4>{run.sources.before.name} → {run.sources.after.name}</h4><p>本地CPU · 固定阈值 {run.threshold_text} · 模型安装第{run.model_trace.generation}代</p><p>按任务关联一个对象；该关联不证明整幅像素覆盖对象，也不证明现场变化。</p>
  <aside className="info" aria-label="像素成果实际接收阶段"><strong>{pixelReceptionLabels[r.state]}</strong>{r.integrity_issue&&<p role="alert">{r.integrity_issue}</p>}{r.state==='waiting_data'&&<p>原模型结果和独立接收是两件事。尚未收到实际回执，不当作数据管理已经采用。</p>}{r.state==='producer_failed'&&<p>不要重跑模型冒充交接成功；原任务按真实权限提供恢复已完成结果的交接。原失败作业保留。</p>}{r.state==='blocked_integrity'&&<p>请恢复原始证据和权限后重新核对；本页不补造接收回执，也不自动重跑模型。</p>}{r.state==='received'&&<p>实际独立接收时间：{clock(entry.timing.received_at)}（北京时间）。这不是拍摄时间或现场真实性确认。</p>}</aside>
  <dl><dt>模型结果保存时间</dt><dd>{clock(entry.timing.model_completed_at)}</dd><dt>机器成果登记时间</dt><dd>{clock(entry.timing.registered_at)}</dd></dl>
  {result?<><p className="info">{result.candidate_pixels===0?'本次模型未检出变化候选，仍可能存在漏检':'本次模型检出变化候选像素，仍需结合原图人工核对。'}</p><p>候选像素 {result.candidate_pixels.toLocaleString('zh-CN')} · 有像素范围 {result.common_valid_pixels.toLocaleString('zh-CN')} · 缺少像素 {result.unknown_pixels.toLocaleString('zh-CN')}</p><p>仅像素候选，无地图坐标、地理面积或对象事实更新；未筛除云和阴影。</p>{readOriginal&&readArtifact&&<PixelRunEvidenceV250 key={run.id+':'+run.version} {...{run,readOriginal,readArtifact,Btn}}/>}</>:<p>{dataOnly&&!entry.data_values_available?'这份机器成果尚未独立接收，查询中不提供可引用的计算数值；可到原任务核对模型处理。':run.status==='running'?'本地模型仍在处理，原图已经固定。':run.error||'本次尚无可用的已保存模型结果。'}</p>}
  {settle&&r.state==='producer_failed'&&<div className="actions"><Btn disabled={busy||!run.settlement_action.can_settle} onClick={()=>settle(run.id)}>恢复已完成结果的交接</Btn><p>{run.settlement_action.reason}</p></div>}
  {entry.human_receipts.length>0&&<details><summary>{daySupplement?'人工核查（当前补充，非当日机器快照）':'原任务已完成的人工办理记录'}</summary>{entry.human_receipts.map(row=><article key={row.receipt_hash}><strong>{row.step_name} · {row.role}</strong><p>{row.note===null?'该节点已保存结构化人工结果，请打开原任务节点核对具体内容。':row.note}</p><small>{clock(row.completed_at)}（北京时间）</small></article>)}{daySupplement&&<p>这些意见来自当前原任务回执，不能据此声称旧日版本已经包含后来意见。</p>}</details>}
  {onOpenTask&&<Btn disabled={busy} onClick={()=>onOpenTask(run.task_id)}>查看这份成果的原业务任务</Btn>}
 </article>;
}
export default function TaskPixelResultsV250({account,instanceId,taskId,Btn,onChanged,onRegisterGuard,request=json,readBinary=binary,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const latest=useRef(),life=useRef(),deliveryGuards=useRef(new Map()),deliveryTargets=useRef({owner:null,values:new Map()});if(!life.current)life.current=createRetainedControllerLifecycle();let token='';try{token=tabStorage.getItem('ga-setup-csrf')||'';}catch{}
 const owner=pixelCanonical([account?.user_id,account?.workspace_id,account?.role,instanceId,taskId,token]),[stored,setStored]=useState(null),[error,setError]=useState('');latest.current={owner,onChanged,onRegisterGuard};
 const holder=useMemo(()=>{try{return {c:new TaskPixelResultsControllerV250({account,instanceId,taskId,request,readBinary,storage,session:tabStorage,locks,current:()=>latest.current.owner===owner})};}catch(e){return {error:e.message};}},[owner,request,readBinary,storage,tabStorage,locks]),c=holder.c,v=stored?.owner===owner?stored.value:null;
 if(deliveryTargets.current.owner!==owner){deliveryTargets.current={owner,values:new Map()};deliveryGuards.current=new Map();}for(const row of v?.value?.records||[]){const id=row.reception.observation_id;if(id)deliveryTargets.current.values.set(id,{observationId:id,entityId:row.entity_id});}
 useEffect(()=>{setError('');const guard=()=>latest.current.owner===owner&&!!c&&c.guard()&&[...deliveryGuards.current.values()].every(check=>check());latest.current.onRegisterGuard?.(guard);if(!c)return()=>latest.current.onRegisterGuard?.(null,guard);const lease=life.current.setup(c,owner);if(!lease)return;let mounted=true;c.onChange=value=>{if(mounted)setStored({owner,value});};setStored({owner,value:c.view()});const unload=e=>{if(!guard()){e.preventDefault();e.returnValue='';}};globalThis.addEventListener?.('beforeunload',unload);return()=>{mounted=false;latest.current.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);life.current.cleanup(lease);};},[owner,c]);
 useEffect(()=>{if(c&&!c.started){c.started=true;c.start().catch(()=>{});}},[c]);
 useEffect(()=>{const timer=setInterval(()=>{if(c&&!c.busy&&!c.reading&&c.value?.records.some(row=>row.run.status==='running'||row.reception.state==='waiting_data'))c.refresh().catch(()=>{});},3000);return()=>clearInterval(timer);},[c]);
 async function run(fn,changed=false){setError('');try{await fn();if(changed&&c?.active&&!c.suspended&&latest.current.owner===owner)await latest.current.onChanged?.();}catch(e){if(c?.active&&!c.suspended&&latest.current.owner===owner)setError(e.message);}}
 if(v?.denied)return <section aria-label="任务像素依据范围已隐藏"><p role="alert">当前账号、任务或实例范围已变化，原图、交接原申请和回执已隐藏并保留。</p></section>;
 if(v?.value&&v.value.pixel_pair_snapshot===null&&!v.pending&&!v.saved.length&&!v.error&&!error)return null;
 return <section className="local-model-card pixel-pair-studio" aria-label="本任务图像对与三智能体处理阶段"><h3>本任务图像对与处理进度</h3>{[holder.error,v?.error,error].filter(Boolean).filter((e,i,a)=>a.indexOf(e)===i).map((message,i)=><p role="alert" key={i}>{message}</p>)}
  {!v?.authenticated?<p role="status">正在核对原任务与当前账号…</p>:<>
   {v.notice&&<p role="status">{v.notice}</p>}
   {v.saved.length>0&&<details><summary>已保存的原结果交接命令</summary>{v.saved.map(saved=><p key={saved.request_id}>原结果的交接命令已经保存；旧作业仍为失败记录。这份命令回执不代表数据管理已独立接收，接收以下方最新阶段为准。</p>)}</details>}
   {!v.localSaved&&<article role="alert"><p>原交接操作尚未完整暂存，未发送新的交接。请保持此页补存同一次原文。</p><Btn disabled={v.busy} onClick={()=>run(()=>c.repair())}>重试保存原交接（不发送）</Btn></article>}
   {v.pending&&<article aria-label="原结果交接申请待核对"><p>原交接结果暂未确认，原申请已保留。尚未找到回执不能证明没有保存；不重新推理，也不生成另一操作号。</p><Btn disabled={v.busy||!v.localSaved} onClick={()=>run(()=>c.recover(),true)}>只核对原交接命令</Btn><Btn disabled={v.busy||!v.localSaved||!['admin','operator'].includes(account.role)} onClick={()=>run(()=>c.resend(),true)}>明确按原交接命令重试</Btn><p>如果原命令尚未保存，明确重试可能实际交接这份已完成结果；不会运行模型、修改旧失败作业或完成人工步骤。</p></article>}
   <Btn disabled={v.busy||v.reading} onClick={()=>run(()=>c.refresh(),true)}>刷新本任务原件与接收阶段</Btn>
   {v.reading&&<p role="status">正在重新核对当前任务原件与结果，尚未采用旧显示认定接收。</p>}
   {v.value?.pixel_pair_snapshot&&<PixelPairInputsV250 key={v.value.pixel_pair_snapshot.pair_hash} pair={v.value.pixel_pair_snapshot} read={(side,image)=>c.input(side,image)} Btn={Btn}/>}
   {v.value?.pixel_pair_snapshot&&!v.value.records.length&&<p role="status">本次任务两图已固定，尚无已保存的模型运行结果；请按任务页的正式审批和执行步骤继续。</p>}
   {v.value?.records.map(entry=><PixelMachineRecordV250 key={entry.run.id+':'+entry.run.version} entry={entry} Btn={Btn} busy={v.busy||!!v.pending} readOriginal={(run,side)=>c.originalBytes(run,side)} readArtifact={(run,kind)=>c.artifactBytes(run,kind)} settle={id=>run(()=>c.settle(id),true)}/>)}
   {[...deliveryTargets.current.values.values()].map(target=><DataDeliveryStudio key={owner+':'+target.observationId} {...{account,instanceId,Btn,storage,tabStorage,locks,...target}} api={request} sourceName="本任务的原图像比较机器成果" draftChannel="workflow-pixel-v250" onReload={()=>run(()=>c.refresh())} onRegisterGuard={(fn,previous)=>{if(latest.current.owner!==owner)return;const map=deliveryGuards.current;if(fn)map.set(target.observationId,fn);else if(!previous||map.get(target.observationId)===previous)map.delete(target.observationId);}}/>)}
   <p>模型分析、独立数据接收、人工办理和正式目标验收分别保存。最终验收请核对本任务原“目标与验收”区；模型完成或收件都不代表已经验收，也不更新对象现场状态。</p>
  </>}
 </section>;
}
