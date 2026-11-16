import React,{useEffect,useRef,useState} from 'react';
import {createTaskWorkOrdersController,workOrderScopeKey,WORK_ORDER_ACTIONS,WORK_ORDER_STATUSES} from './task-work-orders-v198.mjs';

const names={assigned:'分派工作',progress:'记录进展',submit:'提交办理结果',return:'退回补充',cancel:'取消工作单'};
const date=value=>{try{return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});}catch{return '时间暂不能核对';}};
export default function TaskWorkOrdersV198(props){
 const actorID=typeof props.actor==='string'?props.actor:props.actor?.user_id||props.actor?.id;
 const workspaceID=typeof props.workspace==='string'?props.workspace:props.workspace?.workspace_id||props.workspace?.id;
 const scope={actor_id:actorID,workspace_id:workspaceID,task_id:props.task?.id};let scopeKey;
 try{scopeKey=workOrderScopeKey(scope);}catch{return <section className="local-model-card" aria-label="人工工作单"><h3>人工工作单</h3><p role="alert">当前账号、工作区或任务尚不能核对，未读取或提交工作单。</p></section>;}
 return <WorkOrders key={scopeKey} {...props} scope={scope} scopeKey={scopeKey}/>;
}
function WorkOrders({scope,scopeKey,api,onRegisterGuard,onSaved}){
 const latest=useRef();latest.current={onSaved,scopeKey};const holder=useRef();
 if(!holder.current)holder.current=createTaskWorkOrdersController({api,scope});
 const c=holder.current,[,draw]=useState(0),[confirm,setConfirm]=useState(null),[exportNotice,setExportNotice]=useState('');
 const run=async action=>{const token=c.generation;try{return await action();}catch(error){if(c.active&&token===c.generation){c.error=error.message;c.notify();}return null;}};
 useEffect(()=>{
  let mounted=true;c.mount();const token=c.generation;
  c.onChange=()=>{if(mounted&&c.active&&token===c.generation)draw(value=>value+1);};
  c.onSaved=async value=>{if(mounted&&latest.current.scopeKey===scopeKey)return latest.current.onSaved?.(value);};
  const unload=event=>{if(!c.safeToLeave()){event.preventDefault();event.returnValue='';}};
  const focus=()=>{run(async()=>{await c.identity();c.notify();});};
  const storage=event=>{if(event.key?.startsWith(c.prefix))run(async()=>{c.check();c.reloadPending();c.notify();});};
  window.addEventListener('beforeunload',unload);window.addEventListener('focus',focus);window.addEventListener('storage',storage);
  run(()=>c.open());
  return()=>{mounted=false;c.dispose();window.removeEventListener('beforeunload',unload);window.removeEventListener('focus',focus);window.removeEventListener('storage',storage);};
 },[c,scopeKey]);
 useEffect(()=>{const guard=()=>c.safeToLeave();onRegisterGuard?.(guard);return()=>onRegisterGuard?.(null,guard);},[c,onRegisterGuard]);
 const exportCopy=()=>{try{c.check();const url=URL.createObjectURL(new Blob([c.exportRecord()],{type:'application/json;charset=utf-8'}));try{const anchor=document.createElement('a');anchor.href=url;anchor.download='工作单恢复记录.json';anchor.click();setExportNotice('恢复记录下载已交给浏览器，请确认文件已保留。');}finally{setTimeout(()=>URL.revokeObjectURL(url),0);}}catch(error){c.error=error.message;c.notify();}};
 if(c.denied)return <section className="local-model-card" aria-label="人工工作单"><h3>人工工作单</h3><p role="alert">账号、工作区或工作单权限已变化，原工作单与输入已隐藏；原稿和原请求保留在原账号范围。</p></section>;
 if(!c.readable)return <section className="local-model-card" aria-label="人工工作单"><h3>人工工作单</h3><p role={c.error?'alert':'status'}>{c.error||'正在核对账号与工作单权限…'}</p>{c.error&&<button type="button" disabled={c.busy} onClick={()=>run(()=>c.open())}>重新读取工作单</button>}</section>;
 const detail=c.detail,form=c.form,pending=c.pending;
 const command=operation=>{setConfirm(null);run(()=>c.submit(operation));};
 return <section className="local-model-card" aria-label="人工工作单">
  <div className="actions"><h3>人工工作单</h3><div role="group" aria-label="工作单范围"><button type="button" aria-pressed={c.state.mode==='assigned'} disabled={c.busy} onClick={()=>{setConfirm(null);run(()=>c.setMode('assigned'));}}>分派给我</button><button type="button" aria-pressed={c.state.mode==='dispatched'} disabled={c.busy} onClick={()=>{setConfirm(null);run(()=>c.setMode('dispatched'));}}>我派发的</button></div><button type="button" disabled={c.busy} onClick={()=>run(()=>c.refresh())}>刷新工作单</button></div>
  <p>这是当前任务的人工办理记录。提交结果只保存办理说明，不等于设备动作已完成、独立验收通过或整个业务结束。</p>
  {c.error&&<p role="alert">{c.error}</p>}{c.notice&&<p role="status">{c.notice}</p>}{c.busy&&<p role="status">正在读取或核对原记录，请稍候…</p>}
  {!c.localSaved&&<div role="alert"><p>本机输入尚未安全保存。暂不能提交或离开，请重试保护或导出恢复记录。</p><button type="button" disabled={c.busy} onClick={()=>c.retryLocal()}>重试本机保存</button></div>}
  {pending&&<div className="media-form" aria-label="待核对的工作单原请求"><h4>原请求尚待核对</h4><p>{WORK_ORDER_ACTIONS[pending.operation]}的原内容与编号已保留。仅查询原请求，不自动重复提交或更换编号。</p><button type="button" disabled={c.busy} onClick={()=>run(()=>c.recover())}>核对原提交结果</button>{pending.rejection&&<><p>服务器已明确拒绝这份旧版本提交。可主动核对原号和最新版，将被拒原件留存后保留原说明继续编辑；此操作不会重新提交。</p><button type="button" disabled={c.busy||!c.localSaved} onClick={()=>run(()=>c.useRejectedLatest())}>核对最新版并保留原说明</button></>}</div>}
  {c.loaded&&!c.items.length&&<p>{c.state.mode==='assigned'?'当前任务没有分派给我的工作单。':'当前任务没有我派发或负责的工作单。'}</p>}
  {!!c.items.length&&<div className="media-form" aria-label="工作单清单">{c.items.map(item=><button key={item.id} type="button" disabled={c.busy} aria-pressed={c.state.selected===item.id} onClick={()=>{setConfirm(null);run(()=>c.select(item.id));}}><strong>{item.title}</strong> · {WORK_ORDER_STATUSES[item.status]} · {item.assignee_name}{item.simulation_only?' · 仅模拟（SIM）':''}</button>)}</div>}
  {detail&&<article className="media-form" aria-label="当前工作单">
   <h4>{detail.title}</h4><p>状态：{WORK_ORDER_STATUSES[detail.status]} · 负责人：{detail.assignee_name}</p>
   {detail.simulation_only&&<p role="note">仅工程模拟（SIM）；这些记录不能证明现场动作或业务效果。</p>}
   <dl><dt>办理说明</dt><dd style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{detail.description}</dd><dt>预期产出</dt><dd style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{detail.expected_result}</dd><dt>办理期限（北京时间）</dt><dd><time dateTime={detail.due_at}>{date(detail.due_at)}</time></dd></dl>
   <p>服务器记录：第{detail.version}版，更新于{date(detail.updated_at)}（北京时间）。</p>
   {form&&<div className="media-form"><label>本次进展、办理结果或退回取消原因<textarea aria-label="工作单办理说明" rows={5} value={form.note} disabled={!c.canWriteNote} onChange={event=>{try{setConfirm(null);c.edit(event.target.value);}catch(error){c.error=error.message;c.notify();}}}/></label><p>说明最多12000字；超出时保留本机全文，需调整后提交。</p><p>{form.dirty?(c.localSaved?'本页输入已在本机保护，尚未提交服务器。':'本页输入仍未安全落盘。'):'显示已保存说明；补充本次情况后再提交。'}</p>
    {form.conflict&&<div role="alert"><p>服务器出现了新版本，原输入保留。请先对照上方状态与历史，再明确按最新版继续编辑。</p><button type="button" disabled={!c.canWriteNote} onClick={()=>run(()=>c.keepInputOnLatest())}>保留输入并按最新版继续编辑</button></div>}
    <div className="actions">{['progress','submit','return','cancel'].filter(operation=>detail.permissions[operation]===true).map(operation=><button key={operation} type="button" disabled={c.busy||!!pending||form.conflict||!c.localSaved||!form.note.trim()} onClick={()=>operation==='cancel'?setConfirm('cancel'):command(operation)}>{WORK_ORDER_ACTIONS[operation]}</button>)}</div>
    {confirm==='cancel'&&detail.permissions.cancel===true&&<div className="media-form"><p>取消这张工作单将保存本次原因并停止其后续办理。已保存说明和历史仍保留；不会撤销已经发生的实际动作。</p><button type="button" disabled={c.busy||!!pending||form.conflict||!c.localSaved} onClick={()=>command('cancel')}>确认取消这张工作单</button><button type="button" onClick={()=>setConfirm(null)}>继续办理</button></div>}
    {!Object.values(detail.permissions).some(v=>v===true)&&<p>服务器当前未允许本账号修改这张工作单。可查看记录，不提供提交操作。</p>}
   </div>}
   <details><summary>查看办理历史（{detail.history.length}条）</summary><ol>{detail.history.map(row=><li key={row.version}><strong>第{row.version}版 · {names[row.operation]} · {WORK_ORDER_STATUSES[row.status]}</strong><p>{row.actor_name} · <time dateTime={row.at}>{date(row.at)}</time>（北京时间）</p><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{row.note||'工作单已分派，尚无办理说明。'}</p></li>)}</ol></details>
  </article>}
  {!!c.state.rejections.length&&<details><summary>本机留存的未保存提交（{c.state.rejections.length}条）</summary><p>下列旧版本提交被服务器明确拒绝，尚未保存为办理结果；原请求全文另有留存。</p><ol>{c.state.rejections.map(row=><li key={row.request_id}>{WORK_ORDER_ACTIONS[row.operation]} · 原第{row.previous_version}版，重新核对时为第{row.current_version}版<p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{row.note}</p></li>)}</ol></details>}
  <details><summary>保护本页输入与恢复记录</summary><p>本机恢复记录按当前账号、工作区和任务隔离。导出文件可能包含办理说明，请妥善保管；下载不等于服务器已保存。</p><button type="button" disabled={c.busy} onClick={exportCopy}>导出本页恢复记录</button>{exportNotice&&<p role="status">{exportNotice}</p>}</details>
 </section>;
}
