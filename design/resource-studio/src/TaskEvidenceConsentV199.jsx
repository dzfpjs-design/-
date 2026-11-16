import React,{useEffect,useRef,useState} from 'react';
import {createTaskEvidenceConsentController,evidenceConsentScopeKey} from './task-evidence-consent-v199.mjs';

const status={sufficient:'原查询资料充足，仍须业务核对',insufficient:'原查询资料不足',conflict:'原查询存在资料冲突'};
export default function TaskEvidenceConsentV199(props){
 const scope={actor_id:typeof props.actor==='string'?props.actor:props.actor?.user_id||props.actor?.id,workspace_id:typeof props.workspace==='string'?props.workspace:props.workspace?.workspace_id||props.workspace?.id,task_id:props.task?.id};let key;
 try{key=evidenceConsentScopeKey(scope);}catch{return <section className="local-model-card"><h3>业务分析资料授权</h3><p role="alert">当前账号或任务尚不能核对，未读取或授权资料。</p></section>;}
 return <Consent key={key} {...props} scope={scope}/>;
}
function Consent({scope,api,onRegisterGuard,onSaved}){
 const holder=useRef(),latest=useRef();latest.current={onSaved};if(!holder.current)holder.current=createTaskEvidenceConsentController({api,scope});const c=holder.current,[,draw]=useState(0),[download,setDownload]=useState('');
 const run=async action=>{const token=c.generation;try{return await action();}catch(error){if(c.active&&token===c.generation){c.error=error.message;c.notify();}return null;}};
 useEffect(()=>{let mounted=true;c.mount();const token=c.generation;c.onChange=()=>{if(mounted&&c.active&&token===c.generation)draw(n=>n+1);};c.onSaved=value=>mounted?latest.current.onSaved?.(value):undefined;
  const focus=()=>run(async()=>{await c.identity();c.notify();}),unload=event=>{if(!c.safeToLeave()){event.preventDefault();event.returnValue='';}},storage=event=>{if(event.key?.startsWith(c.prefix))run(async()=>{c.check();c.reloadPending();c.notify();});};
  window.addEventListener('focus',focus);window.addEventListener('beforeunload',unload);window.addEventListener('storage',storage);run(()=>c.open());return()=>{mounted=false;c.dispose();window.removeEventListener('focus',focus);window.removeEventListener('beforeunload',unload);window.removeEventListener('storage',storage);};
 },[c]);
 useEffect(()=>{const guard=()=>c.safeToLeave();onRegisterGuard?.(guard);return()=>onRegisterGuard?.(null,guard);},[c,onRegisterGuard]);
 const exportCopy=()=>{try{c.check();const url=URL.createObjectURL(new Blob([c.exportRecord()],{type:'application/json;charset=utf-8'}));try{const link=document.createElement('a');link.href=url;link.download='资料授权恢复记录.json';link.click();setDownload('恢复记录下载已交给浏览器，请确认文件已保留。');}finally{setTimeout(()=>URL.revokeObjectURL(url),0);}}catch(error){c.error=error.message;c.notify();}};
 if(c.denied)return <section className="local-model-card" aria-label="业务分析资料授权"><h3>业务分析资料授权</h3><p role="alert">账号、工作区或证据权限已变化，原资料和输入已隐藏；原请求保留在原账号范围。</p></section>;
 if(!c.readable)return <section className="local-model-card" aria-label="业务分析资料授权"><h3>业务分析资料授权</h3><p role={c.error?'alert':'status'}>{c.error||'正在核对任务资料与账号…'}</p>{c.error&&<button type="button" disabled={c.busy} onClick={()=>run(()=>c.open())}>重新读取资料</button>}</section>;
 const value=c.detail,form=c.form,model=c.model;
 return <section className="local-model-card" aria-label="业务分析资料授权"><div className="actions"><h3>业务分析资料授权</h3><button type="button" disabled={c.busy} onClick={()=>run(()=>c.refresh())}>刷新资料与授权状态</button></div>
  <p>以下为本任务已经保存的真实文本资料。先查看原文，再明确授权给本任务登记的模型；本页面不调用模型、不派设备，也不替人审批。</p>
  {c.error&&<p role="alert">{c.error}</p>}{c.notice&&<p role="status">{c.notice}</p>}{c.busy&&<p role="status">正在核对原资料，请稍候…</p>}
  {!c.localSaved&&<div role="alert">本机输入尚未安全保存，暂不能提交授权或离开。<button type="button" disabled={c.busy} onClick={()=>c.retryLocal()}>重试本机保存</button></div>}
  {c.pending&&<div className="media-form"><h4>原授权请求待核对</h4><p>只读取原件与原模型的当前授权状态，不重复提交。原理由和请求正文仍保留。</p><button type="button" disabled={c.busy} onClick={()=>run(()=>c.recover())}>核对原授权状态</button></div>}
  {c.loaded&&!c.items.length&&<p>当前任务尚无已保存的文本证据包；请等待资料汇总步骤完成。不会用观测编号冒充文件证据。</p>}
  <div className="media-form">{c.items.map((item,i)=><button key={item.id} type="button" disabled={c.busy} aria-pressed={c.state.selected===item.id} onClick={()=>run(()=>c.select(item.id))}>资料{i+1} · {item.task_title} · {item.source_summary.observation_count}条观测{item.simulation_only?' · 仅模拟（SIM）':''}</button>)}</div>
  {value&&form&&<article className="media-form"><h4>{value.task_title}的文本资料</h4>{value.simulation_only&&<p role="note">仅工程模拟（SIM），不证明现场事实或生产效果。</p>}<p>{status[value.evidence_status]||'原查询状态需核对'} · {value.source_summary.object_count}个对象 · {value.source_summary.observation_count}条观测 · {value.source_summary.gap_count}项缺口 · {value.source_summary.issue_count}项需注意事项</p><p>这是已保存的资料快照，不自动代表此刻全部最新事实；授权后也不等于业务结论成立。</p>
   <details open><summary>查看完整拟发送原文</summary><pre aria-label="拟发送模型的完整文本" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{value.text}</pre></details>
   {!value.model_options_available&&<p role="alert">当前任务的模型登记或权限不能核对；已保存原文仍可回看，暂不能新增授权。</p>}
   <label>授权给哪个已登记模型<select aria-label="授权模型" disabled={c.busy||!!c.pending||!value.model_options_available||!['admin','operator'].includes(c.role)} value={form.model_id||''} onChange={event=>run(()=>c.edit({model_id:event.target.value}))}><option value="">请选择本任务的实际模型</option>{value.model_options.map(option=><option key={option.id} value={option.id}>{option.name} · {option.model}{option.authorized?' · 当前已授权':' · 尚未授权'}</option>)}{form.model_id&&!model&&<option value={form.model_id}>原选模型当前不能核对，请重新选择</option>}</select></label>
   {model&&<p>本次仅授权这份原件给 {model.name}（{model.model}）。{model.authorized?'服务器当前已核对该授权。':'服务器尚未核对到该授权。'}</p>}
   <label>授权理由<textarea aria-label="资料授权理由" rows={3} disabled={c.busy||!!c.pending||!['admin','operator'].includes(c.role)} value={form.reason} onChange={event=>run(()=>c.edit({reason:event.target.value}))}/></label><p>最多2000字；输入在本机按账号、工作区和任务保护，尚不等于服务器已授权。</p>
   {form.stale&&<div role="alert"><p>原件授权版本或所选模型登记已变化，旧理由保留；请重新核对。</p><button type="button" disabled={c.busy||!!c.pending} onClick={()=>run(()=>c.useLatest())}>保留理由并核对当前版本</button></div>}
   <label><input type="checkbox" checked={form.acknowledged} disabled={c.busy||!!c.pending||!model||model.authorized||form.stale||!['admin','operator'].includes(c.role)} onChange={event=>run(()=>c.edit({acknowledged:event.target.checked}))}/>我已查看上方完整原文，同意仅将这份资料发送给所选模型进行本任务业务分析。</label>
   <button type="button" disabled={!c.canAuthorize} onClick={()=>run(()=>c.authorize())}>确认授权这份资料给所选模型</button>
   {!['admin','operator'].includes(c.role)&&<p>当前账号只能查看资料；服务器未允许新增授权。</p>}
  </article>}
  {!!c.state.history.length&&<details><summary>本机已核对的授权状态（{c.state.history.length}条）</summary><p>记录当前已授权状态，不冒充原请求的独立执行回执。</p><ul>{c.state.history.map(item=><li key={item.nonce}>{item.model_name} · {item.model}<p style={{whiteSpace:'pre-wrap'}}>{item.reason}</p></li>)}</ul></details>}
  <details><summary>保护授权理由与原请求</summary><p>导出文件含授权理由，请妥善保管；下载不等于已授权。</p><button type="button" disabled={c.busy} onClick={exportCopy}>导出授权恢复记录</button>{download&&<p role="status">{download}</p>}</details>
 </section>;
}
