import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {REVIEW_LABELS,reviewJournalKey,saveReviewRequest,clearReviewRequest,rejectReviewRequest,reviewBusiness,checkReviewReceipt,definiteReviewConflict,newerReviewView,withReviewJournalLock,readReviewJournal,sameReviewRequest} from './automation-review-contract.mjs';
import './automation-review.css';
import {AutomationConfigurationState} from './AutomationRuntimePanel';
export default function AutomationReviewPanel({account,business,refreshKey,prepareDraft,onAdopt,onPending,onBusy,onReload,pending,initialView,Btn}){
 const [view,setView]=useState(initialView||null),[ready,setReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[notFound,setNotFound]=useState(false),[rejected,setRejected]=useState(false);
 const life=useRef(0),reads=useRef(0),latest=useRef(initialView||null),live=useRef({}),controller=useRef(null),mounted=useRef(true),operation=useRef(false);
 const scope=JSON.stringify([account.workspace_id,account.username,account.user_id]);
 live.current={account,business,refreshKey,pending,scope,prepareDraft,onAdopt,onPending,onBusy};
 const writable=['admin','operator'].includes(account.role);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;life.current++;reads.current++;controller.current?.abort();};},[]);
 function active(epoch,key=scope){return mounted.current&&epoch===life.current&&live.current.scope===key;}
 function begin(){if(operation.current)return null;operation.current=true;const epoch=life.current;setBusy(true);live.current.onBusy?.(true);setError('');return epoch;}
 function finish(epoch){operation.current=false;if(live.current.scope===scope)live.current.onBusy?.(false);if(!active(epoch))return;setBusy(false);controller.current=null;}
 function adoptRead(next){latest.current=newerReviewView(latest.current,next);setView(latest.current);return latest.current;}
 async function load(){
  const epoch=life.current,request=++reads.current,key=scope,signature=live.current.refreshKey;setReady(false);
  try{const revision=await live.current.prepareDraft(false),next=await json('/setup/draft');
   if(!active(epoch,key)||request!==reads.current||signature!==live.current.refreshKey)return;
   const current=adoptRead(next);if(current.revision!==revision)throw Error('服务器配置版本与本页输入不同，请读取服务器版本并核对；本页输入保留。');
   reviewBusiness(current,live.current.business);setReady(true);setRejected(false);setError('');
  }catch(e){if(active(epoch,key)&&request===reads.current)setError(e.message);}
 }
 useEffect(()=>{reads.current++;setReady(false);setNotFound(false);if(!pending&&!busy)load();},[scope,business,refreshKey,pending?.body.request_id]);
 async function saved(next,original,epoch){
  checkReviewReceipt(next,original);if(!active(epoch))return;
  reads.current++;const actual=adoptRead(next);
  if(actual.revision>next.revision)throw Error('收到较早的确认回执，请核对原请求以读取当前配置。');
  await live.current.onAdopt(actual);if(!active(epoch))return;
  const key=reviewJournalKey(live.current.account);
  if(!await withReviewJournalLock(key,()=>clearReviewRequest(localStorage,key,original)))throw Error('服务器已保存，但原请求恢复记录已变化；请再次核对。');
  live.current.onPending(null);setNotFound(false);setReady(true);setNotice('确认请求已保存。下方状态依据服务器当前配置；历史回执不会替代后续变化的重新核对。');
 }
 async function post(original){
  if(!writable||busy)return;const epoch=begin();if(epoch===null)return;
  try{await withReviewJournalLock(reviewJournalKey(live.current.account),()=>{if(!sameReviewRequest(readReviewJournal(localStorage,reviewJournalKey(live.current.account)),original))throw Error('原确认请求已变化，请重新核对恢复记录。');});if(!active(epoch))return;controller.current=new AbortController();const next=await json('/setup/automation/review',{method:'POST',body:JSON.stringify(original.body),signal:controller.current.signal});await saved(next,original,epoch);}
  catch(e){if(active(epoch)){if(definiteReviewConflict(e)){try{if(!await withReviewJournalLock(reviewJournalKey(live.current.account),()=>rejectReviewRequest(localStorage,reviewJournalKey(live.current.account),original,e.message)))throw Error('浏览器中的原确认请求已变化，请先核对当前恢复记录。');live.current.onPending(null);setReady(false);setRejected(true);setError(e.message+' 本页修改保留，原拒绝请求已留存。');}catch(storeError){setError(storeError.message+' 原请求保留，请继续核对。');}}else setError(e.message+' 保存结果尚未确认，原确认请求保留；请先核对，不另建请求。');}}
  finally{finish(epoch);}
 }
 async function confirmCurrent(){
  if(!writable||busy||pending||!ready)return;const epoch=begin();if(epoch===null)return;reads.current++;
  const originalAccount={...live.current.account},originalScope=live.current.scope,originalBusiness=live.current.business;
  const current=()=>active(epoch,originalScope)&&live.current.account.user_id===originalAccount.user_id&&live.current.business===originalBusiness;
  let original=null;
  try{const revision=await live.current.prepareDraft(true);if(!current())return;const next=await json('/setup/draft');if(!current())return;
   if(next.revision!==revision)throw Error('配置版本已变化，本页输入保留；请读取服务器版本并核对。');
   const row=reviewBusiness(next,originalBusiness);adoptRead(next);
   original={body:{revision:next.revision,business_id:row.business_id,request_id:crypto.randomUUID()},business_name:row.business_name,created_at:new Date().toISOString()};
   await withReviewJournalLock(reviewJournalKey(originalAccount),()=>{if(!current())throw Error('账号或业务已变化，未启动确认。');return saveReviewRequest(localStorage,reviewJournalKey(originalAccount),original);});if(!current())return;live.current.onPending(original);
   controller.current=new AbortController();const answer=await json('/setup/automation/review',{method:'POST',body:JSON.stringify(original.body),signal:controller.current.signal});await saved(answer,original,epoch);
  }catch(e){if(active(epoch)){if(original)try{const current=readReviewJournal(localStorage,reviewJournalKey(live.current.account));if(current&&!sameReviewRequest(current,original))live.current.onPending(current);}catch{}if(original&&definiteReviewConflict(e)){try{if(!await withReviewJournalLock(reviewJournalKey(live.current.account),()=>rejectReviewRequest(localStorage,reviewJournalKey(live.current.account),original,e.message)))throw Error('浏览器中的原确认请求已变化，请先核对当前恢复记录。');live.current.onPending(null);setRejected(true);setReady(false);setError(e.message+' 本页输入保留，原拒绝请求已留存。');}catch(storeError){setError(storeError.message+' 原请求保留。');}}else setError(e.message+(original?' 保存结果尚未确认，原请求保留，请先核对。':' 本页输入保留，未启动确认。'));}}
  finally{finish(epoch);}
 }
 async function probe(){
  const original=live.current.pending;if(!original||busy)return;const epoch=begin();if(epoch===null)return;setNotFound(false);
  try{controller.current=new AbortController();const answer=await json('/setup/automation/review/requests/'+encodeURIComponent(original.body.request_id)+'?business_id='+encodeURIComponent(original.body.business_id),{signal:controller.current.signal});if(!active(epoch))return;
   checkReviewReceipt(answer,original);
   if(writable)await saved(answer,original,epoch);
   else {adoptRead(answer);setNotice('服务器已有此确认回执。当前账号只读，原请求恢复记录保留，请由原操作账号核对完成。');}
  }catch(e){if(active(epoch)){if(e.status===404){setNotFound(true);setError('服务器尚未找到此确认回执。原请求保留，可按同一请求重试；不会换号或推断确认成功。');}else setError(e.message+' 原请求保留，可继续核对。');}}
  finally{finish(epoch);}
 }
 let row=null;try{if(view)row=reviewBusiness(view,business);}catch{}
 return <section className="automation-review-panel" aria-label="当前业务自动化确认">
 <h3>核对当前业务自动化规则</h3><p>先对照上面的业务启动说明与所选条件，再确认当前规则。确认仅保存配置边界；不会立即启动任务或设备。</p>
 <AutomationConfigurationState ready={ready} row={row} revision={view?.revision}/>
 {row&&<><strong className={row.status==='needs_review'?'warning-text':''}>{!ready&&!pending?'上次读到的服务器状态：':''}{REVIEW_LABELS[row.status]}</strong><p>{row.reason}</p><small>服务器配置第 {view.revision} 版{!ready&&!pending?' · 正在核对当前保存状态':''}</small></>}
 {!row&&!error&&<p role="status">{pending?'当前保存状态尚待核对，请先核对原确认请求。':'正在读取服务器保存的自动化状态…'}</p>}
 {!writable&&<p className="info">当前账号只读，可查看启动说明、规则和保存状态。</p>}
 {pending?<div className="automation-review-recovery"><strong>上次自动化确认尚待核对</strong><p>业务：{pending.business_name} · 提交时配置第 {pending.body.revision} 版。刷新或停止等待不会清除原请求。</p>
 <div className="row"><Btn disabled={busy} onClick={probe}>核对上次自动化确认</Btn>{writable&&notFound&&<Btn disabled={busy} onClick={()=>post(pending)}>重试原确认请求</Btn>}</div></div>:<div className="row"><Btn primary disabled={!writable||busy||!ready||!!error} onClick={confirmCurrent}>确认当前业务自动化规则</Btn><Btn disabled={busy} onClick={load}>重新读取已保存规则</Btn>{(rejected||!!error)&&<Btn disabled={busy} onClick={onReload}>查看服务器版本并核对</Btn>}</div>}
 {busy&&<p role="status">正在核对或保存当前规则…{controller.current&&<Btn onClick={()=>controller.current?.abort()}>停止等待，保留原确认请求</Btn>}</p>}
 {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 </section>;
}
