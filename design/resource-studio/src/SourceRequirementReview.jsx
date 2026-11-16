import React,{useEffect,useRef,useState} from 'react';
import {same} from './report-configuration-contract.mjs';
import {SourceRequirementSession,verifyRequirementSaved,verifyRequirementView} from './source-requirement-session.mjs';
import './source-requirement-review.css';

const dispositions={represented:'人工确认已体现',missing:'流程需要补充',incorrect:'流程理解有误',out_of_scope:'与业务流程无关',needs_evidence:'仍需核实'};
const states={current:'基于当前保存的流程核对',stale:'资料或流程已更新，需要重新核对',pending_candidate:'等待生成流程后核对',history:'历史保存记录'};
const sourceLabel=id=>id.startsWith('o:p')?'文档第 '+id.slice(3)+' 页':'原文位置 '+id.split(':').at(-1).replace(/^p/,'');
const Button=({children,...props})=><button type="button" {...props}>{children}</button>;

export default function SourceRequirementReview({account,row,catalog,request,identity,checkEntry,
    writable,storage,Btn=Button,onSessionInvalid,onRegisterGuard}){
 const scope=[account.user_id,account.workspace_id,row.id].join(':'),latest=useRef(scope);latest.current=scope;
 const mounted=useRef(scope),alive=useRef(true),sequence=useRef(0),busyRef=useRef(false),sessionRef=useRef(null);
 const rowRef=useRef(row),catalogRef=useRef(catalog);rowRef.current=row;catalogRef.current=catalog;
 const [,render]=useState(0),[opened,setOpened]=useState(false),[view,setView]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[search,setSearch]=useState('');
 const [history,setHistory]=useState(null);
 const query=useRef({offset:0,source_unit_id:null,target_offset:0,target_search:'',check_offset:0});
 const current=()=>{if(!alive.current||mounted.current!==latest.current)throw Error('账号或资料已变化，原输入保留。');checkEntry?.();};
 if(!sessionRef.current)sessionRef.current=new SourceRequirementSession({account,draftID:row.id,storage,request,identity,
  check:current,onChange:()=>{if(alive.current)render(n=>n+1);}});
 const draft=sessionRef.current,state=draft.state,form=state.form;
 const viewCurrent=view?.binding.draft_revision===row.revision,usable=viewCurrent?view:null;
 async function operate(action){
  if(busyRef.current)return;busyRef.current=true;setBusy(true);setError('');
  try{await action();current();}
  catch(failure){try{current();setError(failure.detail?.message||failure.message);
   if([401,403].includes(failure.status)||failure.code==='SESSION_SCOPE_CHANGED'){setView(null);onSessionInvalid?.();}}catch{}}
  finally{busyRef.current=false;if(alive.current)setBusy(false);}
 }
 async function read(changes={}){
  current();const seq=++sequence.current,next={...query.current,...changes};
  const params=new URLSearchParams(Object.entries(next).filter(([,v])=>v!==null));
  await identity();current();
  const received=await request('/setup/report-evidence-readings/'+row.id+'/requirement-review?'+params);
  await identity();current();if(seq!==sequence.current)throw Error('已保留后来选择，未采用较早的资料位置。');
  await verifyRequirementView(received,{account,row:rowRef.current,catalog:catalogRef.current});current();
  if(seq!==sequence.current)return;query.current=next;setView(received);
 }
 useEffect(()=>{
  alive.current=true;draft.active=true;onRegisterGuard?.(()=>draft.canLeave());
  return()=>{alive.current=false;sequence.current++;draft.deactivate();onRegisterGuard?.(null);};
 },[]);
 useEffect(()=>{if(opened&&!busyRef.current)operate(()=>read());},[row.revision]);
 function edit(change){try{current();draft.edit(change);setError('');setNotice('');}catch(failure){setError(failure.message);}}
 function newRequirement(){try{draft.begin(usable);setNotice('');}catch(failure){setError(failure.message);}}
 async function save(recover=null){
  let reply;
  try{reply=recover===null?await draft.save():await draft.recover(recover);}
  catch(failure){
   if(!draft.state.pending&&[409,422].includes(failure.status)){
    try{await read();}catch{/* Keep the original rejection and all human input. */}
   }
   throw failure;
  }
  current();setNotice(draft.state.dirty?'之前的内容已保存；后来输入仍保留在本机，尚未提交。':'核对结果已保存到服务器。');
  await read();return reply;
 }
 function restore(entry){try{draft.restore(entry);setNotice('已恢复本机输入。若有原保存请求，请先核对其结果。');setError('');}catch(failure){setError(failure.message);}}
 function recheck(){try{draft.recheck(usable);setNotice('已切换到当前保存的流程，请重新核对原文、对应位置和判断后保存。');}catch(failure){setError(failure.message);}}
 async function readHistory(item,offset=0){
  await identity();current();const received=await request('/setup/report-evidence-readings/'+row.id+'/requirement-checks/'+item.check_id+'/history?offset='+offset);
  await identity();current();if(!Array.isArray(received.entries))throw Error('历史核对记录无法读取。');
  for(const entry of received.entries){if(entry.check_id!==item.check_id)throw Error('历史记录与当前要求不一致。');await verifyRequirementSaved(entry,account,row.id);}
  current();setHistory({check_id:item.check_id,latest:item.version,entries:received.entries,offset:received.offset,limit:received.limit});
 }
 if(scope!==mounted.current)return null;
 const retained=draft.restoreCandidates(),bindingChanged=!!(form&&usable&&!same(state.binding,usable.binding));
 const newest=usable?.checks.find(item=>item.check_id===state.checkID);
 return <section className="source-requirement-review" data-source-requirement-review>
  <div className="source-review-heading"><div><h3>核对资料中的业务要求</h3><p>对照原文与流程，记录需要补充或重新理解的地方。核对结果单独保存。</p></div>
   <Btn disabled={busy} onClick={()=>{setOpened(!opened);if(!opened)operate(()=>read());}}>{opened?'收起核对':'打开原文与流程对照'}</Btn></div>
  {opened&&<>
   {busy&&<p role="status">正在核对服务器保存状态…</p>}
   {error&&<p role="alert" className="source-review-error">{error}</p>}
   {notice&&<p role="status">{notice}</p>}
   {draft.localError&&<p role="alert">{draft.localError}</p>}
   {view&&!viewCurrent&&<p>正在使用的资料或流程版本已变化，请重新读取后继续核对。</p>}
   <div className="source-review-toolbar"><Btn disabled={busy} onClick={()=>operate(()=>read())}>重新读取已保存内容</Btn>
    {form&&<span role="status">{!draft.localSaved?'本机草稿尚未可靠保存，请保留页面':state.pending?'本次保存结果待核对，本机请求已保留':state.dirty?'本机草稿已保留，尚未保存到服务器':state.lastReceipt?'核对结果已保存到服务器':''}</span>}</div>
   {retained.length>0&&<details><summary>本机保留的未完成要求 · {retained.length}份</summary>
    {retained.map(entry=><p key={entry.key}>{entry.state.form.requirement||'尚未填写的业务要求'} <Btn disabled={busy||state.dirty||!!state.pending} onClick={()=>restore(entry)}>恢复这份输入</Btn></p>)}</details>}
   {usable&&<>
    <nav className="source-review-positions" aria-label="原文位置">
     {usable.source_units.map(unit=><Btn key={unit.source_unit_id} disabled={busy} aria-pressed={usable.selected_unit?.source_unit_id===unit.source_unit_id}
      onClick={()=>operate(()=>read({source_unit_id:unit.source_unit_id,check_offset:0}))}>{unit.label}{unit.empty?'（空白）':''}</Btn>)}
     <Btn disabled={busy||usable.offset===0} onClick={()=>operate(()=>read({offset:Math.max(0,usable.offset-usable.limit)}))}>前一组</Btn>
     <Btn disabled={busy||usable.offset+usable.limit>=usable.source_total} onClick={()=>operate(()=>read({offset:usable.offset+usable.limit}))}>后一组</Btn>
     <span>共 {usable.source_total} 处原文位置；增加一条要求不代表整段资料已全部核完。</span>
    </nav>
    <div className="source-review-columns">
     <div className="source-review-source"><h4>{usable.selected_unit?.label||'暂无原文'}</h4>
      <div className="source-review-original">{usable.selected_unit?.text||'这处原文为空白，仍保留其原始位置。'}</div>
      <Btn disabled={!writable||busy||state.dirty||!!state.pending||!usable.selected_unit} onClick={newRequirement}>记录这处原文中的要求</Btn>
      {form&&usable.selected_unit&&!form.source_unit_ids.includes(usable.selected_unit.source_unit_id)&&<Btn disabled={!writable||busy} onClick={()=>edit({source_unit_ids:[...form.source_unit_ids,usable.selected_unit.source_unit_id]})}>把这处原文也作为依据</Btn>}
      <h4>这处原文已保存的核对</h4>
      {!usable.checks.length&&<p>还没有保存核对结果。</p>}
      {usable.checks.map(item=><article key={item.check_id} className="source-review-saved">
       <strong>{item.checked.record.requirement}</strong><p>{dispositions[item.checked.record.disposition]} · {states[item.review_status.status]}</p>
       <p>{item.checked.record.reason}</p><p>已保存第 {item.version} 版 · {new Date(item.receipt.saved_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</p>
       <Btn disabled={!writable||busy||state.dirty||!!state.pending} onClick={()=>{try{draft.openSaved(item);setNotice('已打开原保存内容。');}catch(failure){setError(failure.message);}}}>打开核对记录</Btn>
       <Btn disabled={busy} onClick={()=>operate(()=>readHistory(item))}>查看修改记录</Btn>
      </article>)}
      {usable.check_total>usable.check_limit&&<div><Btn disabled={busy||usable.check_offset===0} onClick={()=>operate(()=>read({check_offset:Math.max(0,usable.check_offset-usable.check_limit)}))}>较新的记录</Btn>
       <Btn disabled={busy||usable.check_offset+usable.check_limit>=usable.check_total} onClick={()=>operate(()=>read({check_offset:usable.check_offset+usable.check_limit}))}>较早的记录</Btn></div>}
      {history&&<section className="source-review-history"><h4>要求修改记录</h4>
       {history.entries.map(item=><article key={item.version}><strong>第 {item.version} 版 · {dispositions[item.checked.record.disposition]}</strong>
        <p>{item.checked.record.requirement}</p><p>{item.checked.record.reason}</p>
        <Btn disabled={!writable||busy||state.dirty||!!state.pending} onClick={()=>{try{draft.restoreHistorical(item,history.latest);setNotice('已保留历史原版。当前内容需重新核对，保存时会另存为新版本。');}catch(failure){setError(failure.message);}}}>以这版内容重新核对</Btn></article>)}
       <Btn disabled={busy||history.offset===0} onClick={()=>operate(()=>readHistory({check_id:history.check_id,version:history.latest},Math.max(0,history.offset-history.limit)))}>较新的版本</Btn>
       <Btn disabled={busy||history.entries.length<history.limit} onClick={()=>operate(()=>readHistory({check_id:history.check_id,version:history.latest},history.offset+history.limit))}>较早的版本</Btn>
       <Btn onClick={()=>setHistory(null)}>收起修改记录</Btn></section>}
     </div>
     <div className="source-review-form">
      {!form?<p>选择原文位置，开始记录一项业务要求；也可以先打开已经保存的核对记录。</p>:<>
       <label>这段资料提出了什么要求<textarea aria-label="业务要求" rows={3} maxLength={4000} disabled={!writable} value={form.requirement} onChange={event=>edit({requirement:event.target.value})}/></label>
       <div>依据：{form.source_unit_ids.map(id=><span key={id}>{sourceLabel(id)} {form.source_unit_ids.length>1&&<Btn disabled={!writable} onClick={()=>edit({source_unit_ids:form.source_unit_ids.filter(value=>value!==id)})}>移除此处依据</Btn>} </span>)}</div>
       <label>核对结果<select aria-label="要求核对结果" disabled={!writable} value={form.disposition} onChange={event=>edit({disposition:event.target.value})}>
        {Object.entries(dispositions).map(([key,label])=><option key={key} value={key} disabled={!usable.candidate_available&&key!=='needs_evidence'}>{label}</option>)}</select></label>
       {!usable.candidate_available&&<p>尚未生成可核对的流程。可以先保存待核要求，生成后再核对。</p>}
       <label>为什么作出这个判断<textarea aria-label="核对说明" rows={3} maxLength={4000} disabled={!writable} value={form.reason} onChange={event=>edit({reason:event.target.value})}/></label>
       {usable.candidate_available&&<fieldset><legend>对应的流程或资料要求（可多选）</legend>
        <div className="source-review-search"><input aria-label="搜索对应流程" value={search} onChange={event=>setSearch(event.target.value)} placeholder="按业务名、步骤或要求查找"/>
         <Btn disabled={busy} onClick={()=>operate(()=>read({target_search:search,target_offset:0}))}>查找</Btn></div>
        <div className="source-review-targets">{usable.target_options.map(option=><label key={option.pointer}><input type="checkbox" disabled={!writable} checked={form.targets.includes(option.pointer)} onChange={event=>edit({targets:event.target.checked?[...form.targets,option.pointer]:form.targets.filter(pointer=>pointer!==option.pointer)})}/><span>{option.label}{option.summary&&<small>{option.summary}</small>}</span></label>)}</div>
        {!usable.target_options.length&&<p>未找到对应内容；可以记录为需要补充。</p>}
        <p>已选择 {form.targets.length} 处。{form.targets.some(pointer=>!usable.target_options.some(option=>option.pointer===pointer))&&<Btn disabled={!writable} onClick={()=>edit({targets:[]})}>清除之前选择的位置</Btn>}</p>
        <Btn disabled={busy||usable.target_offset===0} onClick={()=>operate(()=>read({target_offset:Math.max(0,usable.target_offset-100)}))}>前一组流程位置</Btn>
        <Btn disabled={busy||usable.target_offset+usable.target_options.length>=usable.target_total} onClick={()=>operate(()=>read({target_offset:usable.target_offset+100}))}>后一组流程位置</Btn>
       </fieldset>}
       {bindingChanged&&<div className="source-review-warning"><p>资料或流程已更新，原确认不能自动沿用。</p><Btn disabled={!writable||busy||!!state.pending} onClick={recheck}>对照当前流程重新核对</Btn></div>}
       {newest&&newest.version>state.version&&<div className="source-review-warning"><p>另一个页面已保存了新版本。对照左侧记录后，可以保留你的输入并另存新版本。</p>
        <Btn disabled={!writable||busy||!!state.pending} onClick={()=>{try{draft.keepOwnWithLatest(newest);setNotice('你的修改仍保留，保存后将成为新版本；服务器旧版本不会删除。');}catch(failure){setError(failure.message);}}}>保留我的输入，基于新版本继续</Btn></div>}
       {state.pending?<div className="source-review-warning"><p>请先核对原保存结果；使用原请求重试不会另存一份。</p>
        <Btn disabled={busy} onClick={()=>operate(()=>save(true))}>查询保存结果</Btn>
        <Btn disabled={busy||!writable} onClick={()=>operate(()=>save(false))}>用原请求重试保存</Btn></div>:
        <Btn disabled={!writable||busy||!state.dirty||bindingChanged} onClick={()=>operate(()=>save())}>保存核对结果</Btn>}
       <Btn disabled={busy||!draft.localSaved} onClick={()=>{try{draft.stash();setNotice('这份输入已暂存在本机，可以开始核对其他要求。');}catch(failure){setError(failure.message);}}}>暂存这份输入，核对其他要求</Btn>
      </>}
     </div>
    </div>
   </>}
  </>}
 </section>;
}
