import React,{useEffect,useRef,useState} from 'react';
import {SimulationClientV176,simulationIdentityKeyV176,simulationFieldLabelsV176,simulationSampleLabelV176,simulationTimelineV176,canRecoverSimulationSampleV176,simulationRecoveryLabelV176} from './simulation-integration-v176.mjs';
import './simulation-integration-v176.css';
import SimulationEvolutionV176 from './SimulationEvolutionV176.jsx';

const actions={enable:'启用定时取样',pause:'暂停后续取样',sample:'立即取样一次',daily:'更新每日归集',recover:'核对原取样回执'};
function clock(value){if(!value)return '未记录';const date=new Date(value);return Number.isNaN(date.getTime())?'原时间声明待核':date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）';}
function device(value){return value==='sim-weather-01'?'模拟气象站':value==='sim-drone-01'?'模拟无人机':value==='sim-camera-01'?'模拟摄像头':'已配置模拟设备';}
function number(value){return Object.is(value,-0)?'-0':String(value);}
function operationLabel(record){if(!record)return '';if(record.operation==='daily.reconcile')return actions.daily;return actions[record.operation.split('.').at(-1)]||'原接入操作';}

export default function SimulationIntegrationV176({api,scope,onOpenTask}){
 let key='invalid';try{key=simulationIdentityKeyV176(scope);}catch{}
 const latest=useRef({key,api});latest.current={key,api};
 const context=useRef(null),epoch=useRef(0);
 const [view,setView]=useState(null),[selected,setSelected]=useState(null),[pending,setPending]=useState(null),[confirmation,setConfirmation]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[ready,setReady]=useState(null),[cacheError,setCacheError]=useState('');
 const visible=ready===key&&context.current?.key===key;
 const canWrite=visible&&context.current?.journalReady===true&&['admin','operator'].includes(scope?.role)&&!busy&&!pending&&!cacheError;
 function current(ctx,ticket){return context.current===ctx&&epoch.current===ticket&&latest.current.key===ctx.key&&latest.current.api===ctx.api&&!ctx.client.disposed;}
 function syncJournal(ctx,ticket){if(!current(ctx,ticket))return;setPending(ctx.client.journal.record?{...ctx.client.journal.record}:null);setCacheError(ctx.client.journal.isUnsaved()?'本机原请求尚未完整保存，尚未发送；请重试保存本机请求。':'');}
 async function refresh(ctx,ticket){const value=await ctx.client.readIndex();if(!current(ctx,ticket))return;setView(value);setReady(ctx.key);}
 async function operate(run){const ctx=context.current,ticket=epoch.current;if(!ctx||!current(ctx,ticket)||ctx.busy)return;ctx.busy=true;setBusy(true);setError('');setNotice('');try{await run(ctx,ticket);}catch(cause){if(current(ctx,ticket))setError(cause?.message||'操作未完成，原请求保留。');}finally{if(current(ctx,ticket)){syncJournal(ctx,ticket);ctx.busy=false;setBusy(false);}}}
 useEffect(()=>{
  const ticket=++epoch.current;setView(null);setSelected(null);setPending(null);setConfirmation(null);setReady(null);setError('');setNotice('');setCacheError('');setBusy(true);
  if(key==='invalid'){setError('请先选择当前账号、工作区与实例。');setBusy(false);return;}
  const ctx={key,api,busy:true,journalReady:false,client:null};ctx.client=new SimulationClientV176(api,scope,{current:()=>current(ctx,ticket)});context.current=ctx;
  const unload=event=>{if(ctx.client.journal.isUnsaved()){event.preventDefault();event.returnValue='';}};globalThis.addEventListener?.('beforeunload',unload);
  (async()=>{
   try{await ctx.client.identity();try{const row=await ctx.client.journal.pending(()=>current(ctx,ticket));if(!current(ctx,ticket))return;ctx.journalReady=true;setPending(row);syncJournal(ctx,ticket);}catch(cause){if(!current(ctx,ticket))return;setError(cause.message);syncJournal(ctx,ticket);}await refresh(ctx,ticket);}
   catch(cause){if(current(ctx,ticket)){setError(cause.message);syncJournal(ctx,ticket);}}
   finally{if(current(ctx,ticket)){ctx.busy=false;setBusy(false);}}
  })();
  return()=>{ctx.client.dispose();globalThis.removeEventListener?.('beforeunload',unload);if(context.current===ctx){context.current=null;epoch.current++;}};
 },[key,api]);
 async function recover(){await operate(async(ctx,ticket)=>{let row=await ctx.client.journal.pending(()=>current(ctx,ticket));if(!current(ctx,ticket))return;if(!row)throw Error('本范围没有待核对的原接入请求。');const result=await ctx.client.settle(row);if(!current(ctx,ticket))return;if(result.found){setNotice(row.operation.startsWith('sample.')?'已按原编号核对到后台核对请求，没有重复发送。请刷新原取样查看结果；不表示数据已接收。':'已按原编号核对到服务器保存结果，没有重复发送。');setConfirmation(null);await refresh(ctx,ticket);}else setNotice('服务器尚未找到原请求。原编号和正文保留；可继续核对，或明确按原编号重试。');});}
 async function retry(){await operate(async(ctx,ticket)=>{const row=await ctx.client.journal.pending(()=>current(ctx,ticket));if(!row||!current(ctx,ticket))return;const saved=await ctx.client.sendOriginal(row);if(!current(ctx,ticket))return;setNotice(saved.found?(row.operation.startsWith('sample.')?'已登记后台核对请求；请刷新原取样查看结果，不表示数据已接收。':'服务器已按原编号保存；接入清单正在刷新。'):'原请求尚待核对，没有更换编号。');if(saved.found){setConfirmation(null);await refresh(ctx,ticket);}});}
 async function persist(){await operate(async(ctx,ticket)=>{await ctx.client.identity();await ctx.client.journal.retryPersistence(()=>current(ctx,ticket));if(current(ctx,ticket)){ctx.journalReady=true;setNotice('本机原请求已写入并读回；尚未重新发送，请先核对原请求。');}});}
 function choose(action,row){if(!canWrite||action==='recover'&&!canRecoverSimulationSampleV176(row,scope))return;setConfirmation({action,id:row.id,version:row.version,label:action==='daily'?row.note:action==='recover'?'本账号的原取样记录':device(row.device_id)});setError('');}
 async function confirm(){const chosen=confirmation;if(!chosen)return;await operate(async(ctx,ticket)=>{const rows=chosen.action==='daily'?view?.environments:chosen.action==='recover'?view?.samples:view?.definitions,actual=rows?.find(row=>row.id===chosen.id);if(!actual||actual.version!==chosen.version)throw Error('原接入记录版本已变化，请刷新后重新核对。');if(chosen.action==='recover'&&!canRecoverSimulationSampleV176(actual,ctx.client.scope))throw Error('本次原取样不允许发起新的回执核对，请刷新原记录。');const saved=await ctx.client.start(chosen.action,chosen.id,chosen.version);if(!current(ctx,ticket))return;setConfirmation(null);setNotice(saved.found?(chosen.action==='recover'?'已登记后台核对请求；请刷新原取样查看结果，不表示数据已接收。':'服务器已保存，并已按原请求核对。'):'原请求尚待核对，请保留原编号。');if(saved.found)await refresh(ctx,ticket);});}
 async function inspect(sample){await operate(async(ctx,ticket)=>{const value=await ctx.client.readSample(sample);if(current(ctx,ticket))setSelected(value);});}
 async function openTask(sample){await operate(async(ctx,ticket)=>{await ctx.client.identity();if(!current(ctx,ticket))return;if(typeof onOpenTask!=='function')throw Error('当前页面尚未连接原任务入口，请从业务任务清单查看。');await onOpenTask(sample.task_id,()=>current(ctx,ticket));if(current(ctx,ticket))await ctx.client.identity();});}
 const allRecords=visible?(view?.daily||[]).flatMap(row=>row.records):[];
 return <section className="simulation-integration-v176" aria-label="模拟设备接入联调">
  <header><div><h3>模拟设备接入联调</h3><p className="sim-v176-mark">模拟数据 · 非现场验证</p></div><button type="button" disabled={busy} onClick={()=>operate(refresh)}>刷新接入记录</button></header>
  <p>查看设备取样、数据登记与接收、异常关联任务和每日归集。模拟值不证明现场状态；关联任务的审批、执行与验收仍在原业务任务办理。</p>
  {!visible&&<p role="status">{busy?'正在核对当前账号与实例接入记录。':'当前范围尚未核对，旧账号记录已隐藏。'}</p>}
  {error&&<p className="sim-v176-error" role="alert">{error}</p>}
  {notice&&visible&&<p className="sim-v176-notice" role="status">{notice}</p>}
  {(visible||context.current?.key===key)&&pending&&<article className="sim-v176-pending"><h4>原接入请求待核对</h4><p>{operationLabel(pending)}。原编号与完整正文已保留；不另建编号，也不会在重开页面时自动发送。</p>{cacheError&&<p role="alert">{cacheError}</p>}<div className="sim-v176-actions"><button type="button" disabled={busy} onClick={recover}>核对原接入请求</button><button type="button" disabled={busy||!!cacheError||!['admin','operator'].includes(scope?.role)} onClick={retry}>按原编号重试</button>{cacheError&&<button type="button" disabled={busy} onClick={persist}>重试保存本机请求</button>}</div></article>}
  {visible&&view&&<>
   {!['admin','operator'].includes(scope?.role)&&<p>当前账号只读。启停、取样和每日归集需当前工作区有权限的操作账号。</p>}
   {!view.environments.length&&<div className="sim-v176-empty"><h4>本实例暂无可见模拟接入</h4><p>请管理员先在真实网关与实例配置中登记独立模拟环境和取样定义。这里只办理已保存的定义，不以空界面冒充设备已接入。</p></div>}
   {view.environments.map(env=><article key={env.id} className="sim-v176-environment"><h4>{env.note||'已保存的模拟环境'}</h4><p>仅本实例的模拟设备。记录清单按服务器最近返回范围显示，不表示全部历史。</p>
    {view.definitions.filter(row=>row.environment_id===env.id).map(row=><div key={row.id} className="sim-v176-definition"><div><h5>{device(row.device_id)}</h5><span className="sim-v176-badge">{row.status==='enabled'?'定时取样已启用':row.status==='paused'?'后续取样已暂停':'计划状态待核'}</span></div><p>字段：{row.fields.map(key=>simulationFieldLabelsV176[key]||key).join('、')}；间隔 {row.interval_seconds} 秒。</p><p>下次计划：{clock(row.next_due)}；授权截止：{clock(row.valid_until)}。</p><div className="sim-v176-actions"><button type="button" disabled={!canWrite||row.status==='enabled'} onClick={()=>choose('enable',row)}>启用定时取样</button><button type="button" disabled={!canWrite||row.status!=='enabled'} onClick={()=>choose('pause',row)}>暂停后续取样</button><button type="button" disabled={!canWrite||row.status!=='enabled'} onClick={()=>choose('sample',row)}>立即取样一次</button></div><p className="sim-v176-muted">暂停只阻止后续取样，不宣称已在途调用被撤销。明确取样会交给原后台队列，页面不执行设备动作。</p></div>)}
    {!view.definitions.some(row=>row.environment_id===env.id)&&<p>最近清单内没有本环境的取样定义，请管理员核对已保存配置。</p>}
    <button type="button" disabled={!canWrite} onClick={()=>choose('daily',env)}>更新每日归集</button>
   </article>)}
   {confirmation&&<div className="sim-v176-confirm" role="group" aria-label="核对本次接入操作"><h4>核对本次操作</h4><p>{confirmation.label} · {actions[confirmation.action]}。此操作由服务器检查当前权限、版本、来源和额度。</p>{confirmation.action==='recover'&&<p>后台只以供应商原编号查询原回执，不重新发送设备取样动作，也不换供应商编号。登记核对任务不代表已取回结果或数据已接收。</p>}<button type="button" disabled={!canWrite} onClick={confirm}>确认本次接入操作</button><button type="button" disabled={busy} onClick={()=>setConfirmation(null)}>返回查看</button></div>}
   <h4>最近取样与接收链</h4>{!view.samples.length&&<p>最近清单中尚无本实例取样。没有记录不等于现场无异常。</p>}
   <div className="sim-v176-samples">{view.samples.map((sample,index)=><article key={sample.id}><h5>取样记录 {index+1} · {simulationSampleLabelV176(sample.status)}</h5><p>创建：{clock(sample.created_at)}；平台取回登记：{clock(sample.received_at)}。</p>{sample.status==='awaiting_receipt'&&<p>{simulationRecoveryLabelV176(sample.recovery_status)}。后台任务结束后仍须分别核对原取样、登记与数据接收。</p>}<ol className="sim-v176-timeline">{simulationTimelineV176(sample,allRecords).map(row=><li key={row.label}><strong>{row.label}</strong><span>{row.state}</span></li>)}</ol>{sample.issue&&<p role="alert">{sample.issue}</p>}<div className="sim-v176-actions"><button type="button" disabled={busy} onClick={()=>inspect(sample)}>查看取样依据</button>{sample.status==='awaiting_receipt'&&sample.observation_id===null&&<button type="button" disabled={!canWrite||!canRecoverSimulationSampleV176(sample,scope)} onClick={()=>choose('recover',sample)}>核对原取样回执</button>}{sample.task_id&&<button type="button" disabled={busy||typeof onOpenTask!=='function'} onClick={()=>openTask(sample)}>打开原业务任务</button>}</div>{sample.status==='awaiting_receipt'&&sample.actor_id!==scope.actor_id&&<p className="sim-v176-muted">原回执核对仅由原取样账号办理，管理员也不能代用其他账号的原请求。</p>}</article>)}</div>
   {selected&&<article className="sim-v176-detail"><h4>本次取样依据</h4><p>模拟采样声明时间：{clock(selected.observed_at)}；平台取回登记时间：{clock(selected.received_at)}。模拟设备时钟未作现场真实性认证。</p><p>处理状态：{selected.processing_status||'尚无处理结果'}；{selected.receipt_evidence_id?'已关联原取样回执副本，可在技术记录核对。':'尚无可核对的取样回执副本。'}</p>{selected.attributes&&<dl className="sim-v176-values">{Object.entries(selected.attributes).map(([key,value])=><div key={key}><dt>{simulationFieldLabelsV176[key]||key}</dt><dd>{number(value)} {selected.field_units[key]}</dd></div>)}</dl>}<ol className="sim-v176-timeline">{simulationTimelineV176(selected,allRecords).map(row=><li key={row.label}><strong>{row.label}</strong><span>{row.state}</span></li>)}</ol>{selected.issue&&<p role="alert">{selected.issue}</p>}<details><summary>技术记录（只读）</summary><pre>{JSON.stringify(selected,null,2)}</pre></details><button type="button" onClick={()=>setSelected(null)}>收起取样依据</button></article>}
   <h4>每日归集</h4><p>归集日期按平台规则整理，保留模拟采样声明时间；归集不发起云模型调用，也不把候选更新为现场事实。</p>{!view.daily.length&&<p>最近清单中尚无每日归集，可明确更新已有模拟环境的归集记录。</p>}{view.daily.map(row=><article key={row.id}><h5>{row.business_day} · 版本 {row.version}</h5><p>共 {row.records.length} 条；显示已接收 {row.records.filter(x=>x.data_received).length} 条；其余 {row.records.filter(x=>!x.data_received).length} 条仍待核对。云调用 {row.cloud_calls} 次。</p><p>对象可信状态未更新，现场状态未验证。</p><ul>{row.records.map((item,index)=><li key={item.sample_id}>记录 {index+1}：{simulationSampleLabelV176(item.sample_status)} · {item.data_received?'数据管理已接收':'数据管理接收尚待核对'}{item.task_id?' · 已关联业务核查任务':''}</li>)}</ul></article>)}
  </>}
  <SimulationEvolutionV176 api={api} scope={scope} index={visible?view:null}/>
 </section>;
}
