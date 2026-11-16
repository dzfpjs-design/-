import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {cancellationScope,readCancellation,writeCancellation,checkedCancellationReceipt} from './workflow-cancel-contract.mjs';

export default function WorkflowCancellation({task,account,Btn,open,onClose,onBeforeSend,onSaved,onBusy,onPermissionIssue,retainedNote=''}){
 const scope=cancellationScope(account,task.id),scopeKey=JSON.stringify(scope),base='/workspace/tasks/'+encodeURIComponent(task.id);
 const [journal,setJournal]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[cacheError,setCacheError]=useState(''),[probe,setProbe]=useState(null),[notice,setNotice]=useState('');
 const life=useRef(0),current=useRef(null),writing=useRef(false),renderScope=useRef(scopeKey);renderScope.current=scopeKey;
 const active=epoch=>life.current===epoch&&renderScope.current===scopeKey;
 useEffect(()=>{life.current++;writing.current=false;current.current=null;setJournal(null);setBusy(false);setError('');setCacheError('');setProbe(null);setNotice('');try{const saved=readCancellation(localStorage,scope);current.current=saved;setJournal(saved);}catch(e){setCacheError(e.message);}return()=>{life.current++;};},[scopeKey]);
 function persist(next){try{const saved=writeCancellation(localStorage,scope,next);current.current=saved;setJournal(saved);return saved;}catch(e){setCacheError(e.message);throw e;}}
 const canWrite=account.role!=='viewer'&&task.permissions?.can_cancel!==false&&!task.permission_issue;
 async function saved(receipt,pending,epoch){
  await checkedCancellationReceipt(receipt,pending,scope);if(!active(epoch))return;
  persist({...current.current,reason:'',pending:null,history:[...current.current.history,{body:pending.body,status:'saved',request_hash:receipt.request_hash}]});
  setProbe(null);setNotice('已按本账号原请求核实取消成功；理由、处理说明和原步骤记录已保存。');
  await onSaved?.();if(active(epoch))onClose?.();
 }
 async function run(action){if(writing.current||busy||cacheError||!current.current)return;const epoch=life.current;writing.current=true;setBusy(true);onBusy?.(true);setError('');try{await action(epoch);}catch(e){if(active(epoch)){if([401,403].includes(e.status))onPermissionIssue?.(e);setError(e.message+' 原取消理由和请求已保留；结果未核实前不会认定取消成功。');}}finally{if(active(epoch)){writing.current=false;setBusy(false);onBusy?.(false);}}}
 async function send(resume=false){if(!canWrite)return;await run(async epoch=>{
  let pending=current.current.pending;
  if(!pending){await onBeforeSend?.();if(!active(epoch))return;pending={body:{version:task.version,reason:current.current.reason,retained_note:retainedNote,request_id:crypto.randomUUID()}};persist({...current.current,pending});}
  else if(!resume||!probe||probe.saved||probe.task_status==='workflow_cancelled')throw Error('请先核对原取消请求及当前任务，原记录保留');
  const value=await json(base+'/cancel',{method:'POST',body:JSON.stringify(pending.body)});
  if(!active(epoch))return;await saved(value.cancel_request,pending,epoch);
 });}
 async function recover(){await run(async epoch=>{const pending=current.current.pending;if(!pending)return;const receipt=await json(base+'/cancel-requests/'+pending.body.request_id);await checkedCancellationReceipt(receipt,pending,scope);if(!active(epoch))return;setProbe(receipt);if(receipt.saved)await saved(receipt,pending,epoch);else setNotice('未查到本账号此请求的保存记录，送达状态仍未知。请保留原内容核对；其他操作取消任务不会被当作本请求成功。');});}
 async function reconfirm(){if(!probe||probe.saved||probe.version<=current.current.pending.body.version||probe.task_status==='workflow_cancelled'||task.status==='workflow_cancelled')return;await run(async epoch=>{
  const pending=current.current.pending,receipt=await json(base+'/cancel-requests/'+pending.body.request_id);await checkedCancellationReceipt(receipt,pending,scope);if(!active(epoch))return;if(receipt.saved){await saved(receipt,pending,epoch);return;}if(receipt.version<=pending.body.version||receipt.task_status==='workflow_cancelled')throw Error('当前任务不能替换原取消请求，请保留原记录');
  persist({...current.current,pending:null,history:[...current.current.history,{body:pending.body,status:'version_superseded'}]});setProbe(null);await onSaved?.();setNotice('旧取消请求和原内容已保留；其版本已失效，请核对当前任务后重新明确取消。');
 });}
 const history=journal?.history.length>0&&<details><summary>已保留取消请求历史（{journal.history.length}）</summary>{journal.history.map((item,index)=><article key={index}><strong>{item.status==='saved'?'本账号取消已核实保存':'旧版本请求已保留，未认定保存'}</strong><p>原任务版本：{item.body.version} · 原取消理由：{item.body.reason}</p><p>原保留说明：{item.body.retained_note||'未填写'}</p></article>)}</details>;
 if(!open&&!journal?.pending)return history||null;
 return <section className="media-form" aria-label="业务任务取消与恢复"><h3>取消业务任务</h3>{cacheError&&<p role="alert">{cacheError}</p>}{error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 <label>取消原因<textarea aria-label="业务任务取消原因" maxLength={1000} disabled={busy||!!journal?.pending||!canWrite||!!cacheError} value={journal?.reason||''} onChange={event=>{try{persist({...current.current,reason:event.target.value});}catch{}}}/></label>
 <p>理由先保留在本机，保存成功后进入任务记录。取消停止后续处理；已经发出的调用或设备动作不能由此撤回，结果未知时继续核对原回执。</p>
 {journal?.pending?<><p role="status">原取消请求送达结果待核对，完整理由、版本和保留说明已固定。刷新后可继续核对。</p><Btn disabled={busy||!!cacheError} onClick={recover}>核对原取消请求是否已保存</Btn>{probe&&!probe.saved&&<><Btn disabled={busy||!canWrite||!!cacheError||probe.task_status==='workflow_cancelled'} onClick={()=>send(true)}>按原编号继续取消请求</Btn>{probe.version>journal.pending.body.version&&probe.task_status!=='workflow_cancelled'&&task.status!=='workflow_cancelled'&&<Btn disabled={busy||!canWrite||!!cacheError} onClick={reconfirm}>保留旧请求，重新核对当前版本</Btn>}</>}</>:<Btn disabled={busy||!canWrite||!!cacheError||!journal?.reason.trim()} onClick={()=>send()}>确认取消此任务</Btn>}
 {history}{!journal?.pending&&<Btn disabled={busy} onClick={onClose}>收起，保留取消草稿</Btn>}</section>;
}
