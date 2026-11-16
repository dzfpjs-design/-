import {useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {withDraftCacheLock,readDraftRecovery} from './draft-cache-contract.mjs';
import {applyJournalKey,readApplyPending,writeApplyPending,makeApplyPending,sameApplyPending,verifyApplyReceipt,finishApplyPending,digestApplyValue,archiveUnresolvedApply,readEndedApply} from './understanding-apply-contract.mjs';

// Only the original, scoped command can be retried. A failed read is never a POST.
export function useUnderstandingApply({account,record,workspace,prepareDraft,onApplied,onRecord,locks=globalThis.navigator?.locks}){
 const key=applyJournalKey(account),scope=useRef(key),live=useRef(null),pendingRef=useRef(null),epoch=useRef(0);scope.current=key;live.current={account,record,workspace};
 const [pending,setPending]=useState(null),[busy,setBusy]=useState(false),[storageError,setStorageError]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[retryable,setRetryable]=useState(false);
 const [notFound,setNotFound]=useState(false),[ended,setEnded]=useState(null);
 function current(expected,stamp){if(expected!==scope.current||stamp!==epoch.current)throw Error('账号、页面或原应用已变化，原请求保留。');}
 function assign(value){pendingRef.current=value;setPending(value);}
 async function persist(value,expected,stamp){return withDraftCacheLock(expected,()=>{current(expected,stamp);return writeApplyPending(localStorage,expected,value,account);},locks);}
 async function acknowledge(reply,original,expected,stamp){
  const receipt=await verifyApplyReceipt(reply,original);current(expected,stamp);
  const confirmed={...original,confirmed_receipt:receipt};await persist(confirmed,expected,stamp);current(expected,stamp);assign(confirmed);onRecord(reply.analysis);setRetryable(false);setNotFound(false);setError('');setNotice('原应用已保存到服务器，正在核对当前配置；不会再次应用。');
  let changed=!reply.current_matches,latest=reply;
  if(!changed){latest=await json('/setup/understanding/'+encodeURIComponent(original.scope.analysis_id)+'/apply-receipts/'+encodeURIComponent(original.body.request_id));const checked=await verifyApplyReceipt(latest,original);current(expected,stamp);if(checked.receipt_hash!==receipt.receipt_hash)throw Error('原应用回执在核对期间变化，原请求保留。');changed=!latest.current_matches;onRecord(latest.analysis);}
  if(!changed){
   const active=live.current.workspace,fingerprint=await digestApplyValue(active);current(expected,stamp);
   const drafts=readDraftRecovery(localStorage,'ga-setup:'+account.username+':'+account.workspace_id,globalThis.sessionStorage);
   changed=active!==live.current.workspace||fingerprint!==original.local_workspace_hash||drafts.candidates.length>0;
  }
  if(!changed){await onApplied({...latest,analysis:reply.analysis},original.body.names.length>0);current(expected,stamp);}
  await withDraftCacheLock(expected,()=>{current(expected,stamp);finishApplyPending(localStorage,expected,confirmed,account);},locks);assign(null);setError('');
  setNotice(changed?'原应用已保存。当前配置、流程状态或本地人稿已有变化，已保留较新的内容；本次只核对回执，没有覆盖当前配置。':'原应用回执与当前配置已核对，选中建议已保存；流程仍需逐项确认。');
 }
 async function inspect(original,expected,stamp){
  setNotFound(false);let reply;try{reply=await json('/setup/understanding/'+encodeURIComponent(original.scope.analysis_id)+'/apply-receipts/'+encodeURIComponent(original.body.request_id));}catch(e){current(expected,stamp);setNotFound(e.status===404);throw e;}current(expected,stamp);await acknowledge(reply,original,expected,stamp);return true;
 }
 async function check(original,{retry=false,initial=false}={}){
  if(!original)return;const expected=key,stamp=++epoch.current;setBusy(true);setError('');setRetryable(false);
  try{
   if(await digestApplyValue({scope:original.scope,body:original.body})!==original.command_hash)throw Error('本机原应用内容与摘要不一致，未提交。');current(expected,stamp);
   try{await inspect(original,expected,stamp);return;}catch(e){current(expected,stamp);if(e.status!==404||original.confirmed_receipt)throw e;}
   setRetryable(true);setNotice('服务器暂未查到原应用回执，原请求保留。可再次核对后，用完全相同的原请求重试。');
   if(!retry&&!initial)return;
   const stored=readApplyPending(localStorage,expected,account);if(!sameApplyPending(stored,original))throw Error('原应用请求已被另一页面更改，未提交。');
   if(!['admin','operator'].includes(live.current.account?.role))throw Error('当前账号只读，可以核对回执，不重试应用。');
   try{
    const reply=await json('/setup/understanding/'+encodeURIComponent(original.scope.analysis_id)+'/apply',{method:'POST',body:JSON.stringify(original.body)});current(expected,stamp);
   const receipt=await verifyApplyReceipt(reply,original,{checkConfiguration:false});const confirmed={...original,confirmed_receipt:receipt};await persist(confirmed,expected,stamp);assign(confirmed);onRecord(reply.analysis);setNotice('原应用已保存，正在读取服务器回执；当前不会重复提交。');
   }catch(e){current(expected,stamp);setError(e.message+' 原应用请求已保留，正在核对服务器回执。');}
   const storedAfter=readApplyPending(localStorage,expected,account);await inspect(storedAfter,expected,stamp);
  }catch(e){if(expected===scope.current&&stamp===epoch.current){setError((pendingRef.current?.confirmed_receipt?'原应用已保存；配置同步或回执读取未完成。':'')+e.message+' 原请求保留，请继续核对，未另起应用。');if(e.status===404&&!pendingRef.current?.confirmed_receipt)setRetryable(true);}}
  finally{if(expected===scope.current&&stamp===epoch.current)setBusy(false);}
 }
 useEffect(()=>{
  const stamp=++epoch.current;setStorageError('');setError('');setNotice('');setRetryable(false);setBusy(false);setNotFound(false);setEnded(null);
  try{const original=readApplyPending(localStorage,key,account);assign(original);if(original)check(original);}catch(e){setStorageError(e.message);assign(null);}
  try{setEnded(readEndedApply(localStorage,key,account));}catch(e){setError('原应用恢复历史暂不能读取，历史备份保留。'+e.message);}
  return()=>{if(stamp<=epoch.current)epoch.current++;};
 },[key]);
 useEffect(()=>()=>{scope.current=null;epoch.current++;},[]);
 async function begin(choice){
  if(busy||pendingRef.current||storageError)return;if(!['admin','operator'].includes(account?.role)){setError('当前账号只读，不能应用建议。');return;}
  const expected=key,stamp=++epoch.current;setBusy(true);setError('');setNotice('');
  try{
   const selectedRecord=record,active=live.current.workspace,revision=await prepareDraft(false);current(expected,stamp);
   if(active!==live.current.workspace||selectedRecord!==live.current.record)throw Error('配置或建议在准备期间已变化，请重新核对后应用。');
   const latest=await json('/setup/understanding/'+encodeURIComponent(selectedRecord.id));current(expected,stamp);
   if(latest.version!==selectedRecord.version||latest.status!==selectedRecord.status||latest.result_hash!==selectedRecord.result_hash){onRecord(latest);throw Error('原建议状态已变化，已读取当前状态，请重新核对选择后操作。');}
   const original=await makeApplyPending(account,selectedRecord,choice,{revision,request_id:crypto.randomUUID(),workspace:active});current(expected,stamp);
   await persist(original,expected,stamp);assign(original);await check(original,{initial:true});
  }catch(e){if(expected===scope.current)setError(e.message);}
  finally{if(expected===scope.current)setBusy(false);}
 }
 async function endWaiting(){
  const original=pendingRef.current;if(busy||!original||!notFound||original.confirmed_receipt)return;const expected=key,stamp=++epoch.current;setBusy(true);setError('');
  try{
   try{await inspect(original,expected,stamp);return;}catch(e){current(expected,stamp);if(e.status!==404)throw e;}
   const remote=await json('/setup/understanding/'+encodeURIComponent(original.scope.analysis_id));current(expected,stamp);
   const pointer=await withDraftCacheLock(expected,()=>{current(expected,stamp);return archiveUnresolvedApply(localStorage,expected,original,account);},locks);assign(null);setEnded(pointer);setNotFound(false);setRetryable(false);onRecord(remote);
   setNotice('已完整保留原请求并结束本机等待。没有取消服务器请求，原操作仍可能稍后完成；没有自动重试或改签。原建议当前状态已重新读取，新操作前仍会核对。');
  }catch(e){if(expected===scope.current&&stamp===epoch.current)setError(e.message+' 原请求保留，未结束等待。');}
  finally{if(expected===scope.current&&stamp===epoch.current)setBusy(false);}
 }
 async function viewEnded(){if(!ended||busy)return;const expected=key,stamp=++epoch.current;setBusy(true);setError('');try{const row=await json('/setup/understanding/'+encodeURIComponent(ended.analysis_id));current(expected,stamp);onRecord(row);setNotice('已只读查看原建议的当前状态。历史原编号与完整请求保留，不重送旧请求。');}catch(e){if(expected===scope.current)setError(e.message);}finally{if(expected===scope.current)setBusy(false);}}
 return {pending,busy,error,notice,retryable,canEndWaiting:notFound&&!pending?.confirmed_receipt,ended,viewEnded,endWaiting,blocked:!!pending||!!storageError,storageError,begin,probe:()=>check(pendingRef.current),retry:()=>check(pendingRef.current,{retry:true})};
}
