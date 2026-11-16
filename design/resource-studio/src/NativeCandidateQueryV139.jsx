import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {perceptionObjectState,assertPerceptionObjects} from './perception-entity-contract.mjs';
import NativeInformationValue from './NativeInformationValue';
import {CandidateQueryJournalV139,candidateQueryScopeV139,candidateQueryCommandV139,candidateQueryHashV139,checkCandidateQueryLookupV139,checkCandidateQueryListV139,checkCandidateQueryViewV139,candidateQueryStatusV139,candidateReviewTextV139,emptyCandidateQueryV139,rememberCandidateQueryDraftV139} from './native-candidate-query-v139.mjs';

export default function NativeCandidateQueryV139({account,instanceId,releaseID,objects,selectedObject,Btn,onOpenTask,draftChannel=''}){
 const scopeKey=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,instanceId,releaseID,selectedObject,draftChannel]),renderScope=useRef(scopeKey);renderScope.current=scopeKey;
 const epoch=useRef(0),context=useRef(null),busyRef=useRef(false);
 const [verifiedScope,setVerifiedScope]=useState(null),[form,setForm]=useState(emptyCandidateQueryV139),[pending,setPending]=useState(null),[query,setQuery]=useState(null),[history,setHistory]=useState([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[memoryUnsaved,setMemoryUnsaved]=useState(false),[cacheError,setCacheError]=useState('');
 const visible=verifiedScope===scopeKey,eligible=!!selectedObject&&perceptionObjectState(objects,selectedObject,releaseID).usable,writable=['admin','operator'].includes(account?.role);
 useEffect(()=>{
  const before=event=>{if(memoryUnsaved){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',before);return()=>window.removeEventListener('beforeunload',before);
 },[memoryUnsaved]);
 useEffect(()=>{
  let live=true;epoch.current++;busyRef.current=null;setBusy(false);setVerifiedScope(null);setPending(null);setQuery(null);setHistory([]);setError('');setNotice('');setCacheError('');setMemoryUnsaved(false);
  if(!selectedObject){context.current=null;return()=>{live=false;epoch.current++;};}
  let scope;try{scope=candidateQueryScopeV139(account,instanceId,releaseID,selectedObject);}catch(error){setError(error.message);return()=>{live=false;epoch.current++;};}
  const ctx={scope,scopeKey,base:'/workspace/instances/'+instanceId+'/native-candidate-queries',journal:new CandidateQueryJournalV139(scope,{draftChannel}),ready:false,displayed:false,closed:false};context.current=ctx;
  const requester=scopedPreparationRequester(account,json),current=ticket=>live&&!ctx.closed&&context.current===ctx&&renderScope.current===scopeKey&&epoch.current===ticket;
  ctx.current=current;
  ctx.identity=async ticket=>{try{const me=await requester('/me');if(!current(ticket)||me.user_id!==account.user_id||me.workspace_id!==account.workspace_id||me.role!==account.role)throw Error('当前登录或对象范围已变化。原输入与原请求保留，未展示其他账号资料。');return me;}catch(error){if(current(ticket))setVerifiedScope(null);throw error;}};
  ctx.read=async(path,ticket,options={})=>{await ctx.identity(ticket);const value=await requester(path,options);await ctx.identity(ticket);return value;};
  ctx.adopt=(value,ticket)=>{if(current(ticket)){setQuery(value);setNotice('服务器已保存本次检索；这是候选资料查询，不是现场状态结论。');}};
  ctx.list=async ticket=>{const value=checkCandidateQueryListV139(await ctx.read(ctx.base,ticket),scope);if(current(ticket))setHistory(value);};
  ctx.lookup=async(record,ticket)=>{
   if(await candidateQueryHashV139(record.value.body)!==record.value.body_hash)throw Error('原请求摘要无法核对，未发送。');
   const value=checkCandidateQueryLookupV139(await ctx.read(ctx.base+'/requests/'+record.value.body.request_id+'?expected_hash='+record.value.body_hash,ticket),scope,record.value.body);
   if(value){ctx.adopt(value,ticket);const active=ctx.journal.head();if(active){await ctx.journal.settle(record,value,()=>ctx.identity(ticket));if(current(ticket))setPending(null);}else if(ctx.journal.readOriginal(record.key).raw!==record.raw)throw Error('选中的原保存槽已变化，未结清。');}
   else if(current(ticket))setNotice('尚未找到这个原请求。不会自动重发；可明确按原条件重试。');
   return value;
  };
  const resume=async()=>{
   const ticket=++epoch.current;setVerifiedScope(null);
   if(document.visibilityState==='hidden')return;
   try{
    await ctx.identity(ticket);
    if(!ctx.ready){ctx.saved=ctx.journal.open();ctx.ready=true;}
    await ctx.identity(ticket);if(!current(ticket))return;
    if(!ctx.displayed){
     setForm(ctx.saved.form);setPending(ctx.saved.pending);ctx.displayed=true;
     try{if(ctx.saved.storage_error)throw Error(ctx.saved.storage_error);ctx.journal.save(ctx.saved.form);setMemoryUnsaved(false);setCacheError('');}catch(error){rememberCandidateQueryDraftV139(scope,ctx.saved.form,draftChannel);setMemoryUnsaved(true);setCacheError(error.message+' 输入尚未完整保存，暂不提交。');}
    }
    setError('');
    try{await ctx.list(ticket);}catch(error){if(current(ticket))setError(error.message+' 已保存查询与本页输入保留。');}
    const original=ctx.journal.head();if(current(ticket))setPending(original);
    const recover=original||ctx.saved.saved;if(recover)try{await ctx.lookup(recover,ticket);}catch(error){if(current(ticket))setError(error.message+' 原请求保留，可只核对原编号。');}
    await ctx.identity(ticket);if(current(ticket))setVerifiedScope(scopeKey);
   }catch(error){if(current(ticket)){setVerifiedScope(null);setError(error.message);}}
  };
  ctx.resume=resume;resume();
  const wake=()=>{resume();};window.addEventListener('focus',wake);document.addEventListener('visibilitychange',wake);
  return()=>{live=false;ctx.closed=true;epoch.current++;window.removeEventListener('focus',wake);document.removeEventListener('visibilitychange',wake);};
 },[scopeKey]);
 useEffect(()=>{
  if(!visible||!query||!['queued','running'].includes(query.status))return;
  const ctx=context.current,id=query.id,timer=setInterval(async()=>{const ticket=epoch.current;if(!ctx?.current(ticket)||busyRef.current===ctx)return;try{const value=checkCandidateQueryViewV139(await ctx.read(ctx.base+'/'+id,ticket),ctx.scope,query.request);if(ctx.current(ticket))setQuery(value);}catch(error){if(ctx.current(ticket)){setQuery(null);setError(error.message+' 已隐藏无法核对的结果；原查询和条件仍保留。');}}},3000);
  return()=>clearInterval(timer);
 },[visible,query?.id,query?.status,scopeKey]);
 async function act(callback){
  const ctx=context.current,ticket=epoch.current;if(!ctx?.current(ticket)||!visible||busyRef.current===ctx)return;busyRef.current=ctx;setBusy(true);setError('');
  try{await callback(ctx,ticket);}catch(error){if(ctx.current(ticket)){setError(error.message+' 原输入和请求保留。');try{setPending(ctx.journal.head());}catch{}}}finally{if(busyRef.current===ctx){busyRef.current=null;if(context.current===ctx)setBusy(false);}}
 }
 function change(delta){
  const next={...form,...delta};setForm(next);const ctx=context.current;if(!ctx||!visible)return;
  try{ctx.journal.save(next);setMemoryUnsaved(false);setCacheError('');}catch(error){rememberCandidateQueryDraftV139(ctx.scope,next,draftChannel);setMemoryUnsaved(true);setCacheError(error.message+' 输入尚未完整保存，暂不提交。');}
 }
 async function submit(ctx,ticket,record){
  await ctx.journal.withOriginal(record,()=>ctx.identity(ticket),async original=>{
   const found=checkCandidateQueryLookupV139(await ctx.read(ctx.base+'/requests/'+original.value.body.request_id+'?expected_hash='+original.value.body_hash,ticket),ctx.scope,original.value.body);
   if(found){ctx.adopt(found,ticket);return;}
   assertPerceptionObjects(objects,[selectedObject],releaseID);
   const value=checkCandidateQueryViewV139(await ctx.read(ctx.base,ticket,{method:'POST',body:original.value.exact_body}),ctx.scope,original.value.body);ctx.adopt(value,ticket);
  });
  // A successful POST cannot erase its outbox merely because list refresh
  // succeeds. Only the actual original-nonce lookup settles that request.
  await ctx.lookup(record,ticket);try{await ctx.list(ticket);}catch(error){if(ctx.current(ticket))setError(error.message+' 原检索已保存，清单暂未刷新；不要另建同一请求。');}
 }
 async function start(ctx,ticket){
  assertPerceptionObjects(objects,[selectedObject],releaseID);const body=candidateQueryCommandV139(ctx.scope,form,crypto.randomUUID()),record=await ctx.journal.install(body,()=>ctx.identity(ticket));
  if(ctx.current(ticket))setPending(record);await submit(ctx,ticket,record);
 }
 async function lookup(ctx,ticket){const record=ctx.journal.head();if(!record)throw Error('本窗口没有待核对原请求，请读取已保存历史。');if(ctx.current(ticket))setPending(record);await ctx.lookup(record,ticket);}
 async function openTask(id){await act(async(ctx,ticket)=>{
  if(memoryUnsaved)throw Error('请先保存本页输入，再打开原任务。');
  const entryCheck=()=>{if(!ctx.current(ticket))throw Error('当前登录或对象范围已变化，原查询和输入保留，未切换原任务。');};
  await ctx.identity(ticket);entryCheck();await onOpenTask?.(id,entryCheck);entryCheck();
 });}
 const result=visible?query:null;
 return <details className="media-form native-candidate-query-v139"><summary>查找已接收的变化候选</summary><p>只检索本对象已接收的本地学习型变化检测结果。不会重新推理、调用云模型或判断现场正常；采集时间和对象覆盖仍待核实。</p>
 {!selectedObject?<p>请先在上方选择一个业务对象，再查找它的候选和原业务核查。</p>:<>
  {error&&<p role="alert" className="error">{error}</p>}
  {!visible?<><p role="status">正在核对当前登录与对象范围，原输入和历史不会删除。</p><Btn onClick={()=>context.current?.resume?.()}>重新核对当前登录</Btn></>:<>
   <p>当前对象：{objects.find(row=>row.id===selectedObject)?.name||'原选择对象'}。检索字段与对象事实字段分开。</p>
   {!eligible&&<p role="status">当前对象资格待恢复。原查询可核对，暂不新建检索。</p>}
   <label>查找哪些资料<select aria-label="候选检索字段" value={form.field} disabled={busy||!!pending} onChange={event=>change({field:event.target.value})}><option value="search_text">候选内容</option><option value="review_status">人工核查状态</option></select></label>
   <label>{form.field==='review_status'?'人工核查状态文字':'候选搜索文字'}<input aria-label="候选搜索文字" value={form.text} maxLength={200} disabled={busy||!!pending} list={form.field==='review_status'?'candidate-review-v139-'+selectedObject:undefined} onChange={event=>change({text:event.target.value})}/></label>
   {form.field==='review_status'&&<><datalist id={'candidate-review-v139-'+selectedObject}><option value="已核查"/><option value="已有草稿"/><option value="待核查"/></datalist><p>可查“已核查”“已有草稿”或“待核查”；只表示原业务保存情况。</p></>}
   {cacheError&&<p role="alert">{cacheError}</p>}{memoryUnsaved&&<Btn disabled={busy} onClick={()=>change({})}>重试保存检索输入</Btn>}
   {!context.current?.journal.canSend()&&<p>本窗口尚未具备完整保存或多窗口协调条件，可读取原查询，暂不能提交。</p>}
   <div className="actions"><Btn primary disabled={busy||!!pending||!writable||!eligible||!!cacheError||!form.text.trim()||!context.current?.journal.canSend()} onClick={()=>act(start)}>开始查询</Btn><Btn disabled={busy} onClick={()=>act(async(ctx,ticket)=>{await ctx.list(ticket);setNotice('已重新读取服务器查询历史，没有新建查询。');})}>读取保存的查询</Btn></div>
   {pending&&<aside><p role="status">原查询结果待核对，已保存原条件与编号。刷新不会自动重发。</p><p>原条件：{pending.value.body.field==='review_status'?'人工核查状态':'候选内容'} · {pending.value.body.text}</p><Btn disabled={busy} onClick={()=>act(lookup)}>只核对原查询</Btn><Btn disabled={busy||!writable||!eligible||!!cacheError||!context.current?.journal.canSend()} onClick={()=>act((ctx,ticket)=>submit(ctx,ticket,pending))}>按原条件重试</Btn></aside>}
   {notice&&<p role="status">{notice}</p>}
   {result&&<article><h4>{candidateQueryStatusV139[result.status]}</h4><p>原检索条件：{result.request.field==='review_status'?'人工核查状态':'候选内容'} · {result.request.text}</p><p>结果保存后，原任务的人工核查可能继续变化；需要最新情况时请明确重新查询。</p>
    {(result.integrity_error||result.source_changed)&&<p role="alert">{result.integrity_error||'原资料或人工核查已变化，旧值已隐藏。请查看原任务并明确重新查询。'}</p>}
    {result.truncated&&<p role="alert">本次资料范围未能完整核对，不能把部分结果当作全部。</p>}
    {!result.records.length&&!['queued','running'].includes(result.status)&&<p>本次没有可显示的匹配候选；这不表示现场正常或不存在变化。</p>}
    {result.records.map(row=><section className="media-form" key={row.observation_id}><h5>{row.name}</h5><p>原业务人工核查：{candidateReviewTextV139(row.review_state)}。采集时间未知，现场状态未核实。</p><table className="evidence-values"><thead><tr><th>候选信息</th><th>保存值</th></tr></thead><tbody>{Object.entries(row.attributes).map(([name,value])=><tr key={name}><td>{name}</td><td><NativeInformationValue name={name} value={value}/></td></tr>)}</tbody></table><Btn disabled={busy||memoryUnsaved} onClick={()=>openTask(row.task_id)}>打开原业务任务与依据</Btn></section>)}
    {!pending&&['failed','blocked_evidence','cancelled','insufficient','sufficient'].includes(result.status)&&<p>原查询历史保留。调整上方条件后点“开始查询”，会保存一条新的检索。</p>}
   </article>}
   <details><summary>保存的查询历史（{history.length}）</summary>{!history.length?<p>当前对象和版本还没有可显示的查询。</p>:history.map(row=><p key={row.id}>{row.request.field==='review_status'?'人工核查状态':'候选内容'} · {row.request.text} · {candidateQueryStatusV139[row.status]} <Btn disabled={busy} onClick={()=>act(async(ctx,ticket)=>{const value=checkCandidateQueryViewV139(await ctx.read(ctx.base+'/'+row.id,ticket),ctx.scope,row.request);if(ctx.current(ticket))setQuery(value);})}>查看这次查询</Btn></p>)}</details>
  </>}
 </>}
 </details>;
}
