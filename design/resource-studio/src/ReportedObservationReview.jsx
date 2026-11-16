import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import ThresholdFeedbackV147 from './ThresholdFeedbackV147';
import {manualFields,manualCanonical,reviewScope,readManualReview,writeManualReview,checkedManualIdentity,checkedManualPost,checkedManualProbe,reviewableRecord,displayManualValue} from './manual-registration-contract.mjs';

export default function ReportedObservationReview({record,instanceId,account,Btn,onRefresh}){
 const scope=reviewScope(account,instanceId,record.id),identity=manualCanonical(scope),current=useRef(null),epoch=useRef(0),operation=useRef(null);
 const [journal,setJournal]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[denied,setDenied]=useState(false),[refreshNeeded,setRefreshNeeded]=useState(false),[lastProbe,setLastProbe]=useState(null);
 current.current={scope,identity,record,account,journal};
 useEffect(()=>{epoch.current++;operation.current=null;setBusy(false);setNotice('');setDenied(false);setRefreshNeeded(false);setLastProbe(null);try{setJournal(readManualReview(localStorage,scope));setError('');}catch(e){setJournal(null);setError(e.message);}return()=>{epoch.current++;};},[identity]);
 let fields=[],fieldIssue='';
 try{fields=manualFields(record.review_basis?.fields);const keys=new Set(fields.map(field=>field.key));if([record.review_basis?.original_attributes,record.review_basis?.extracted_attributes].some(values=>values&&Object.keys(values).some(key=>!keys.has(key))))throw Error('登记与提取字段资料尚未完整核对。');}catch(e){fieldIssue=e.message;}
 const basis=record.review_basis,pending=journal?.pending,available=reviewableRecord(record)&&!fieldIssue;
 const blocked=busy||denied||!journal;
 function commit(value,expected=current.current.journal){const next=writeManualReview(localStorage,scope,value,expected);current.current.journal=next;setJournal(next);return next;}
 function edit(patch){try{commit({...current.current.journal,...patch});setError('');}catch(e){setError(e.message);}}
 async function authenticated(write){const value=checkedManualIdentity(await json('/me',{headers:{'X-Workspace-ID':scope.workspace_id}}),account);if(write&&!['admin','operator'].includes(value.role))throw Object.assign(Error('当前账号没有核查权限，原说明与请求保留。'),{status:403});}
 async function operate(fn,{write=false}={}){if(blocked||operation.current)return;const started=epoch.current,token={};operation.current=token;setBusy(true);setError('');const active=()=>operation.current===token&&epoch.current===started&&current.current.identity===identity;try{await authenticated(write);if(active())await fn(active);}catch(e){if(active()){if(e.status===401||e.status===403)setDenied(true);setError(e.message+' 原核查请求与说明保留。');}}finally{if(active()){operation.current=null;setBusy(false);}}}
 async function accept(review,sent,active,isCurrent=null){
  if(!active())return;
  const now=readManualReview(localStorage,scope);
  if(!now.pending||manualCanonical(now.pending)!==manualCanonical(sent))throw Error('本地核查记录已有变化，未覆盖其他页面的请求。');
  commit({...now,pending:null,history:[...now.history,{body:sent.body,review,status:'saved',current:isCurrent}]},now);
  setRefreshNeeded(true);setNotice(isCurrent===false?'此原编号的核查确已保存，但当前依据已变化；历史决定不能代替重新核查。':'此原编号的核查已保存；是否仍适用请核对刷新后的当前依据。');
  try{await onRefresh();if(active())setRefreshNeeded(false);}catch(e){if(active())setError('核查回执已保存，清单刷新未完成：'+e.message);}
 }
 async function send(){await operate(async active=>{
  let sent=current.current.journal.pending;
  if(!sent){
   if(!available||refreshNeeded)throw Error('当前登记和归集回执尚不能核查，请刷新核对。');
   const value=current.current.journal;
   sent={body:{expected_version:record.version,reason:value.reason,decision:value.decision,request_id:crypto.randomUUID(),expected_input_hash:basis.verification_input_hash}};
   commit({...value,pending:sent},value);setLastProbe(null);
  }
  const value=await json('/observations/'+record.id+'/verify',{method:'POST',body:JSON.stringify(sent.body)});
  const review=await checkedManualPost(value,sent,scope);await accept(review,sent,active);
 },{write:true});}
 async function recover(){await operate(async active=>{
  const sent=current.current.journal.pending;if(!sent)return;
  const value=await json('/workspace/instances/'+instanceId+'/reported-observations/'+record.id+'/review-requests/'+sent.body.request_id);
  await checkedManualProbe(value,sent,scope);if(!active())return;
  if(value.found)await accept(value.review,sent,active,value.current);
  else {setLastProbe(value);setNotice('尚未查到此原编号的核查回执，送达状态仍未知。可保留原内容按原编号重试。');}
 });}
 async function reconfirm(){await operate(async active=>{
  const sent=current.current.journal.pending;if(!sent)return;
  const firstProbe=lastProbe;await checkedManualProbe(firstProbe,sent,scope);
  if(firstProbe.found||firstProbe.version<=sent.body.expected_version)throw Error('请先按原编号核对当前保存状态与版本。');
  const value=await json('/workspace/instances/'+instanceId+'/reported-observations/'+record.id+'/review-requests/'+sent.body.request_id);
  await checkedManualProbe(value,sent,scope);if(!active())return;
  if(value.found){await accept(value.review,sent,active,value.current);return;}
  if(value.version<=sent.body.expected_version)throw Error('当前版本尚未越过原请求，请继续保留原编号。');
  const now=readManualReview(localStorage,scope);if(manualCanonical(now.pending)!==manualCanonical(sent))throw Error('原核查请求已变化，未替换其他页面的记录。');
  commit({...now,pending:null,history:[...now.history,{body:sent.body,status:'version_fenced',probes:[firstProbe,value]}]},now);setLastProbe(null);setRefreshNeeded(true);
  await onRefresh();if(active()){setRefreshNeeded(false);setNotice('旧请求送达仍未证实，原正文已保留；当前版本已变化，请重新对照依据后明确保存。');}
 });}
 async function refresh(){if(operation.current)return;const started=epoch.current;setError('');try{checkedManualIdentity(await json('/me',{headers:{'X-Workspace-ID':scope.workspace_id}}),account);await onRefresh();if(epoch.current===started){setDenied(false);setRefreshNeeded(false);}}catch(e){if(epoch.current===started)setError(e.message+' 原输入仍保留。');}}
 return <article className="local-model-card"><h4>核对原登记与本地提取回执</h4>
  {basis?<><p>{basis.source_name||'原登记来源'} · {basis.extractor_name||'本地处理能力'}</p><p>{basis.observed_at?'登记时间：'+new Date(basis.observed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（北京时间）':'登记时间待核对'}。这里只核对登记与提取一致性，不证明物理现场状态。</p>
   {fields.length>0&&<table><thead><tr><th>字段</th><th>原登记</th><th>本地提取</th></tr></thead><tbody>{fields.map(field=><tr key={field.key}><th>{field.label}{field.unit?'（'+field.unit+'）':''}</th><td>{displayManualValue(basis.original_attributes?.[field.key])}</td><td>{displayManualValue(basis.extracted_attributes?.[field.key])}</td></tr>)}</tbody></table>}
   {basis.verification&&<p>已保存核查：{basis.verification.decision==='confirmed'?'确认登记一致':'拒绝此登记依据'} · {basis.verification.reason}</p>}
   {basis.historical_verification&&<p>历史核查：{basis.historical_verification.decision==='confirmed'?'曾确认登记一致':'曾拒绝此登记依据'} · {basis.historical_verification.reason}。当前依据已变化，不能视为现在已确认。</p>}
   {basis.integrity_issue&&<p role="alert">{basis.integrity_issue}</p>}
  </>:<p>原登记与提取依据尚未完整保存，暂不能核查。</p>}
  {fieldIssue&&<p>{fieldIssue}</p>}{!available&&<p>{record.block_reason||'需本地处理完成、数据管理实际接收且依据完整后才能核查。'}</p>}
  {journal&&<><label>核查判断<select aria-label={'核查判断 '+record.id} value={journal.decision} disabled={blocked||!!pending||!available||refreshNeeded} onChange={e=>edit({decision:e.target.value})}><option value="confirmed">确认登记与回执一致</option><option value="rejected">拒绝此登记依据</option></select></label>
   <label>核查说明<textarea aria-label={'核查说明 '+record.id} value={journal.reason} maxLength={2000} disabled={blocked||!!pending||!available||refreshNeeded} onChange={e=>edit({reason:e.target.value})}/></label>
   <div className="actions">{pending?<><Btn disabled={blocked} onClick={recover}>核对原核查请求</Btn><Btn disabled={blocked||!available} onClick={send}>按原编号重试核查</Btn>{lastProbe?.version>pending.body.expected_version&&<Btn disabled={blocked||!available} onClick={reconfirm}>保留原请求，重新核对当前依据</Btn>}<p>原判断与说明已固定；停止等待、刷新或其他核查都不代表本请求已保存。</p></>:<Btn primary disabled={blocked||!available||refreshNeeded||!journal.reason.trim()} onClick={send}>保存人工核查</Btn>}<Btn disabled={busy} onClick={refresh}>刷新登记和当前权限</Btn></div>
  </>}{error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  <ThresholdFeedbackV147 {...{record,instanceId,account,Btn}}/>
 </article>;
}
