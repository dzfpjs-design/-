import React,{useEffect,useMemo,useRef,useState} from 'react';
import {createTaskArchivesController,taskArchiveScopeKey} from './task-archives-v198.mjs';

const sourceKinds={human_product:'人工处理结果',invocation:'注册能力结果',data_run:'任务资料查询',evidence:'原始证据引用'};
const issueNames={unfinished_action:'仍有处理结果待补充',analysis_pending_review:'业务分析仍有冲突或缺少资料',query_not_sufficient:'查询资料尚不充分',no_saved_products:'尚无可归集的已保存步骤结果'};
const knownFields={note:'处理说明',summary:'摘要',reason:'原因',question:'查询问题',output:'已保存结果',value:'结果',status:'保存状态',data:'查询内容',analysis:'业务分析',candidate_assessments:'候选意见',assessment:'支持程度',missing_information:'缺少的资料',recommendation:'推荐说明',title:'标题',name:'名称',count:'数量',attributes:'属性',rows:'记录',records:'记录',sample_count:'有效对象数',missing_count:'缺少字段的对象数'};
const states={supported:'有资料支持',conflicting:'资料存在冲突',insufficient:'资料不足',sufficient:'资料充分',completed:'已完成',succeeded:'已保存',running:'处理中',waiting:'等待处理',blocked:'条件尚不具备',failed:'处理失败'};
const internal=key=>['schema','id','request_key','request_id','key','candidate_ref','recommended_candidate_ref','evidence_refs','source_id','source_kind'].includes(key)||key.endsWith('_id')||key.endsWith('_ids')||key.endsWith('_hash')||key.endsWith('_hashes');
const stamp=value=>{const date=new Date(value);return Number.isNaN(date.valueOf())?'保存时间暂不可显示':date.toLocaleString('zh-CN');};
const scalar=value=>value===null?'明确为空':typeof value==='boolean'?(value?'是':'否'):Object.hasOwn(states,value)?states[value]:String(value);
function LazyDetails({label,children,...props}){const [open,setOpen]=useState(false);return <details {...props} onToggle={event=>setOpen(event.currentTarget.open)}><summary>{label}</summary>{open&&(typeof children==='function'?children():children)}</details>;}
// Source snapshots stay readable, but their potentially large trees mount only
// when opened. System references remain available in an optional diagnostic view.
function SavedValue({value,label='已保存内容'}){
 if(value===null||typeof value!=='object')return <p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{scalar(value)}</p>;
 const entries=Array.isArray(value)?value.map((item,index)=>[String(index),item]):Object.entries(value).filter(([key])=>!internal(key));
 if(!entries.length)return <p>此项未保存可直接阅读的业务字段；来源引用仍保留。</p>;
 return <dl>{entries.map(([key,item],index)=>{const name=Array.isArray(value)?'记录 '+(index+1):Object.hasOwn(knownFields,key)?knownFields[key]:key.replaceAll('_',' ');return <React.Fragment key={key}><dt>{name}</dt><dd>{item!==null&&typeof item==='object'?<LazyDetails label={'展开'+name}><SavedValue value={item} label={name}/></LazyDetails>:<SavedValue value={item} label={name}/>}</dd></React.Fragment>;})}</dl>;
}
function sourceName(task,source){const identity=source.snapshot.activation_id,labels=task.graph_node_labels,info=identity&&labels&&Object.hasOwn(labels,identity)?labels[identity]:null;return info?.name||info?.display_name||source.label||sourceKinds[source.kind];}
function SourceCard({source,index,task}){
 const snap=source.snapshot;
 return <article className="media-form" aria-label={'档案来源 '+(index+1)}><h5>{index+1}. {sourceName(task,source)}</h5><p>{sourceKinds[source.kind]} · 已保存的归集时引用</p><LazyDetails label={'查看来源 '+(index+1)+' 的保存内容'}>
  {source.kind==='evidence'?<dl><dt>资料格式</dt><dd>{snap.media_type||'未标注'}</dd><dt>大小</dt><dd>{snap.size_bytes} 字节</dd><dt>归集时版本</dt><dd>第 {snap.version} 版</dd></dl>:source.kind==='data_run'?<><p style={{whiteSpace:'pre-wrap'}}>{snap.question}</p><SavedValue value={snap.data}/></>:<SavedValue value={snap.output}/>}
  <p>这里显示归集时保存的内容与引用；不代表原件当前仍可读取，也不代表内容已获人工确认。</p>
  <LazyDetails label="查看来源原始结构（诊断）">{()=><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'24rem',overflow:'auto'}}>{JSON.stringify(source.snapshot,null,2)}</pre>}</LazyDetails>
 </LazyDetails></article>;
}
function Issues({items}){return <section className="media-form" aria-label="档案待核事项"><h4>待核事项（{items.length}）</h4>{!items.length?<p>归集时没有记录缺项；候选说明仍待人工核对。</p>:items.map((item,index)=><article key={index}><strong>{index+1}. {Object.hasOwn(issueNames,item.code)?issueNames[item.code]:item.label||'需核对原处理结果'}</strong>{item.assessment&&<><p>{Object.hasOwn(states,item.assessment.assessment)?states[item.assessment.assessment]:'资料需核对'}</p><p style={{whiteSpace:'pre-wrap'}}>{item.assessment.reason}</p>{item.assessment.missing_information?.length>0&&<ul>{item.assessment.missing_information.map((text,i)=><li key={i} style={{whiteSpace:'pre-wrap'}}>{text}</li>)}</ul>}</>}</article>)}</section>;}

/** api is the parent's parsed-JSON transport; onSaved receives an archive receipt,
 * never a Task DTO. Register this guard alongside other Task form guards. */
export default function TaskArchivesV198({task,api,actor,workspace,onSaved,onRegisterGuard}){
 const actorID=typeof actor==='string'?actor:actor?.user_id||actor?.id,workspaceID=typeof workspace==='string'?workspace:workspace?.workspace_id||workspace?.id;
 const [,rerender]=useState(0),callbacks=useRef({onSaved,onRegisterGuard});callbacks.current={onSaved,onRegisterGuard};
 let key='invalid';try{key=taskArchiveScopeKey({actor_id:actorID,workspace_id:workspaceID,task_id:task?.id});}catch{}
 const c=useMemo(()=>key==='invalid'?null:createTaskArchivesController({api,scope:{actor_id:actorID,workspace_id:workspaceID,task_id:task.id},onChange:()=>rerender(n=>n+1),onSaved:receipt=>callbacks.current.onSaved?.(receipt)}),[key]);
 if(c)c.api=api;
 const run=fn=>{Promise.resolve().then(fn).catch(()=>{});};
 useEffect(()=>{
  if(!c)return;
  c.mount();const token=c.generation;run(()=>c.open());
  const guard=()=>c.guard();callbacks.current.onRegisterGuard?.(guard);
  const unload=event=>{if(!guard()){event.preventDefault();event.returnValue='';}};
  const storage=event=>{if(event.key===c.shared||event.key===null)try{c.check(token);c.reloadPending();c.notify();}catch(error){if(c.active&&c.generation===token){c.pendingUnreadable=true;c.error='本机原请求记录不能核对，原内容保留，暂不能提交。';c.notify();}}};
  const focus=()=>run(()=>c.identity(token));
  window.addEventListener('beforeunload',unload);window.addEventListener('storage',storage);window.addEventListener('focus',focus);
  return()=>{c.dispose();callbacks.current.onRegisterGuard?.(null);window.removeEventListener('beforeunload',unload);window.removeEventListener('storage',storage);window.removeEventListener('focus',focus);};
 },[c]);
 if(!c)return <section className="bfr-card" aria-label="任务档案"><h3>任务档案</h3><p role="alert">当前账号、工作区或任务范围尚未确定，暂不能读取档案。</p></section>;
 if(!c.readable)return <section className="bfr-card" aria-label="任务档案"><h3>任务档案</h3><p role={c.denied||c.error?'alert':'status'}>{c.error||'正在核对当前账号与工作区…'}</p>{!c.denied&&c.error&&<button type="button" disabled={c.loading} onClick={()=>run(()=>c.open())}>重新核对账号并读取档案</button>}</section>;
 const view=c.view,value=view?.value,form=c.form,pending=c.pending,busy=c.busy||c.loading,editable=c.canEdit&&!busy&&!pending&&!c.pendingUnreadable&&c.state.version===null,simulation=value?.simulation_only===true||task.workflow_simulation_only===true||task.workflow_review_mode==='engineering_simulation';
 let retained=[];try{retained=c.retained();}catch{}
 const edit=change=>{try{c.edit(change);}catch(error){c.error=error.message;c.notify();}};
 const exportInputs=()=>{try{c.check();const blob=new Blob([JSON.stringify({scope:c.scope,draft:c.state,original_request:c.pending},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='任务档案-本页输入与原请求.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),0);}catch{c.error='导出暂未成功，请保留本页并复制输入。';c.notify();}};
 return <section className="bfr-card" aria-label="任务档案"><header><h3>任务档案</h3><p>归集本任务已保存的处理结果和来源，修订档案说明并保留每一版。</p></header>
  {simulation&&<p role="note"><strong>工程模拟 · 非人工核对 · 仅用于模拟验证</strong></p>}
  <p role="note">当前均为候选档案，说明尚未确认，不代表正式归档或对象事实已经更新。</p>
  {c.role==='viewer'&&<p role="status">当前为只读权限，可查看历史档案和核对本账号原保存回执。</p>}
  {c.loading&&<p role="status">正在读取档案和本机草稿…</p>}
  {c.error&&<p role="alert">{c.error}</p>}{c.storageError&&<p role="alert">{c.storageError}</p>}{c.notice&&<p role="status">{c.notice}</p>}
  <div className="media-form" aria-label="查询任务档案"><label>档案类型筛选<input aria-label="档案类型筛选" maxLength={80} value={c.filter.record_type} onChange={e=>{c.filter.record_type=e.target.value;c.notify();}}/></label><label>标题或说明包含<input aria-label="标题或说明包含" maxLength={200} value={c.filter.q} onChange={e=>{c.filter.q=e.target.value;c.notify();}}/></label><div className="actions"><button type="button" disabled={busy} onClick={()=>run(()=>c.operation(token=>c.refresh(token)))}>查询档案</button><button type="button" disabled={busy} onClick={()=>run(()=>c.operation(async token=>{if(!c.listLoaded)return c.open();await c.refresh(token);if(c.state.selected)await c.select(c.state.selected,c.state.version,token);} ))}>重新读取档案</button><button type="button" disabled={c.busy} onClick={exportInputs}>导出本页输入与原请求</button></div></div>
  {pending&&<aside className="bfr-pending" aria-label="档案原保存请求"><h4>原保存请求待核对</h4><p>原编号、原输入和原版本均已保留；先查询原回执，不会自动重复提交。</p><p>原提交标题：{pending.body.title}</p>{pending.rejection&&<p role="alert">服务器明确拒绝了旧版本修订，本次未应用；原输入仍保留。</p>}<div className="actions"><button type="button" disabled={busy} onClick={()=>run(()=>c.recover())}>只核对原保存请求</button>{pending.not_found&&!pending.rejection&&!pending.direct_result&&<button type="button" disabled={busy||!c.canEdit||!c.locks} onClick={()=>run(()=>c.retrySame())}>按原内容重试同一次保存</button>}{pending.rejection&&<button type="button" disabled={busy||!c.canEdit||!c.locks} onClick={()=>run(()=>c.archiveRejected())}>保留被拒原请求，查看最新版</button>}</div></aside>}
  {c.listLoaded&&!c.items.length&&<p role="status">本次查询没有档案。自动归集步骤完成并保存后，候选档案会显示在这里。</p>}
  {!!c.items.length&&<nav aria-label="任务档案列表">{c.items.map(row=><button type="button" key={row.id} disabled={busy} aria-current={c.state.selected===row.id?'true':undefined} onClick={()=>run(()=>c.operation(token=>c.select(row.id,null,token)))}>{row.title} · 待核候选 · 第 {row.version} 版{row.simulation_only?' · 工程模拟':''} · {row.issue_count} 项待核</button>)}</nav>}
  {view&&value&&<article className="media-form" aria-label="档案详情"><h4>{value.title}</h4><p>待核候选 · 第 {view.version} 版 · {stamp(view.saved_at)}</p><p>任务：{value.basis.task_title||task.title||'当前任务'}{value.basis.business_name?' · 业务：'+value.basis.business_name:''}</p>
   <div className="actions" aria-label="档案版本历史"><button type="button" disabled={busy} onClick={()=>run(()=>c.operation(token=>c.select(view.id,null,token)))}>查看最新版</button>{view.history.map(row=><button type="button" key={row.version} disabled={busy} onClick={()=>run(()=>c.operation(token=>c.select(view.id,row.version,token)))}>查看第 {row.version} 版</button>)}</div>
   {c.state.version!==null?<section aria-label="历史档案说明"><h5>历史版本，只读保留</h5><dl><dt>档案类型</dt><dd>{value.record_type}</dd><dt>档案标题</dt><dd style={{whiteSpace:'pre-wrap'}}>{value.title}</dd><dt>候选说明</dt><dd style={{whiteSpace:'pre-wrap'}}>{value.summary}</dd></dl></section>:form&&<section aria-label="修订候选档案说明"><h5>档案说明</h5><label>档案类型<input aria-label="档案类型" maxLength={80} readOnly={!editable} value={form.values.record_type} onChange={e=>edit({record_type:e.target.value})}/></label><label>档案标题<textarea aria-label="档案标题" rows={2} maxLength={300} readOnly={!editable} value={form.values.title} onChange={e=>edit({title:e.target.value})}/></label><label>候选说明<textarea aria-label="候选说明" rows={5} maxLength={8000} readOnly={!editable} value={form.values.summary} onChange={e=>edit({summary:e.target.value})}/></label><p>说明是待核提案。修订仅保存新的说明版本，原处理结果、来源和旧版保留。</p><p role="status">{form.dirty?(c.localSaved?'本页修改已保留在本机，尚未保存到服务器。':'本页修改尚未可靠保存，请保留本页或导出。'):'当前说明已保存到服务器。'}</p>{form.conflict&&<p role="alert">服务器已有另一版本。本页输入保留，请先对照最新版；当前不能提交旧版本。</p>}<div className="actions"><button type="button" disabled={!editable||!c.localSaved||!c.locks||!form.dirty||form.conflict} onClick={()=>run(()=>c.save())}>保存修订为新版本</button>{form.conflict&&<button type="button" disabled={!editable} onClick={()=>run(()=>c.useCurrent())}>另存本页输入并使用最新版</button>}</div></section>}
   {!!retained.length&&<LazyDetails label={'查看另行保留的本页输入（'+retained.length+' 份）'}>{retained.map((record,index)=><section key={index}><h5>保留输入 {index+1}</h5><dl><dt>档案类型</dt><dd>{record.values.record_type}</dd><dt>档案标题</dt><dd>{record.values.title}</dd><dt>候选说明</dt><dd style={{whiteSpace:'pre-wrap'}}>{record.values.summary}</dd></dl></section>)}</LazyDetails>}
   <Issues items={value.issues}/><section aria-label="档案来源"><h4>已归集来源（{value.sources.length}）</h4><p>以下为归集时保存的步骤结果和引用；本次读取未重新核对原件当前状态。</p>{!value.sources.length&&<p>尚无已归集来源，不能视为资料完整。</p>}{value.sources.map((source,index)=><SourceCard key={source.kind+':'+source.id} source={source} index={index} task={task}/>)}</section>
  </article>}
 </section>;
}
