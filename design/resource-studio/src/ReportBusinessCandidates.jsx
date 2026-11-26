import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';
import {businessNames} from './business-schema.js';
import {addReportCandidate, addReportCandidates, candidateNameKey, currentCandidateMaterial} from './report-candidate-draft.mjs';
import './report-business-candidates.css';

const reasons={not_found:'未找到对应原文',ambiguous:'对应原文不唯一',invalid_quote:'引文需要补充',material_changed:'资料已变化',invalid_material:'资料暂不可用'};
function sessionToken(){try{return globalThis.sessionStorage?.getItem('ga-setup-csrf')||'';}catch{return '';}}

function Evidence({item}) {
 const citations=Array.isArray(item.citations)?item.citations:[], unresolved=Array.isArray(item.unresolved_sources)?item.unresolved_sources:[];
 if(!citations.length&&!unresolved.length&&!item.inference_reason)return <p>依据待补充</p>;
 return <div className="setup-candidate-evidence">
  {citations.map((ref,i)=><blockquote key={'c'+i}>{ref.quote.length>250?ref.quote.slice(0,250)+'…':ref.quote}</blockquote>)}
  {unresolved.map((ref,i)=><div key={'u'+i}><p role="status">依据待核：{reasons[ref.reason]||'请重新核对'}</p>{ref.quote&&<blockquote>{ref.quote.length>250?ref.quote.slice(0,250)+'…':ref.quote}</blockquote>}</div>)}
  {item.inference_reason&&<p>建议说明：{item.inference_reason}</p>}
 </div>;
}

export default function ReportBusinessCandidates({bundle, workspace, liveWorkspace, materials, materialReadStatus,
 canManage, onCommit, onSelectBusiness, onRefreshMaterials, onOpenSource, account, prepareDraft, onAdopt, Btn, Dialog, request=json}) {
 const [open,setOpen]=useState(false),[selected,setSelected]=useState(''),[preview,setPreview]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(null);
 const generation=useRef(0),controller=useRef(null),action=useRef(0),operationType=useRef('');
 const fingerprint=JSON.stringify([bundle?.material,bundle?.origin,bundle?.candidates]);
 // In-memory session marker prevents an old reply being shown after the same
 // person signs in again. The marker is never rendered, logged or persisted.
 const sessionMarker=sessionToken();
 const scope=JSON.stringify([account?.user_id||'',account?.username||'',account?.workspace_id||'',account?.role||'',sessionMarker]);
 const sessionRef=useRef({scope,epoch:0}),mounted=useRef(false);
 if(sessionRef.current.scope!==scope)sessionRef.current={scope,epoch:sessionRef.current.epoch+1};
 const sessionEpoch=sessionRef.current.epoch;
 const identity=JSON.stringify([scope,fingerprint]),identityRef=useRef(identity);identityRef.current=identity;
 const visiblePreview=preview?.identity===identity?preview.value:null;
 const ready=currentCandidateMaterial(bundle,materials,materialReadStatus);
 const currentMaterial=Array.isArray(materials)?materials.find(item=>item.id===bundle?.material?.id):null;
 const canRevalidate=materialReadStatus==='ready'&&currentMaterial&&!currentMaterial.archived&&currentMaterial.hash===bundle?.material?.hash;
 useEffect(()=>{generation.current++;controller.current?.abort();operationType.current='';setPreview(null);setBusy(false);setError('');},[identity]);
 useEffect(()=>{
  const check=()=>{if(sessionToken()!==sessionMarker){generation.current++;action.current++;controller.current?.abort();setPreview(null);setBusy(false);setError('登录状态已变化，请重新登录后查看。');}};
  globalThis.window?.addEventListener('focus',check);globalThis.document?.addEventListener('visibilitychange',check);
  return ()=>{globalThis.window?.removeEventListener('focus',check);globalThis.document?.removeEventListener('visibilitychange',check);};
 },[identity]);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;generation.current++;controller.current?.abort();};},[]);
 if(!bundle||!Array.isArray(bundle.candidates)||!bundle.candidates.length)return null;
 async function load() {
  if(busy||!ready||sessionToken()!==sessionMarker)return;
  action.current++;operationType.current='preview';const current=++generation.current,abort=new AbortController(),valid=()=>current===generation.current&&identityRef.current===identity&&sessionToken()===sessionMarker;controller.current=abort;setBusy(true);setError('');setNotice(null);setPreview(null);
  try {
   const material=bundle.material;
   const result=await request('/setup/candidates/preview',{method:'POST',body:JSON.stringify({schema:bundle.schema,origin:bundle.origin,material:{id:material.id,version:material.version,hash:material.hash,engine:material.engine},candidates:bundle.candidates}),signal:abort.signal});
   if(valid()){
    if(!result||!Array.isArray(result.candidates)||!result.material||result.candidates.length!==bundle.candidates.length)throw Error('候选暂不可用，请重新核对。');
    for(const item of bundle.candidates)addReportCandidate({businesses:[],flows:{}},bundle,result,item.key,materials,materialReadStatus,()=> 'check');
    setPreview({identity,value:result});setSelected(result.candidates[0]?.key||'');
   }
  } catch(e) {if(valid())setError(e?.status===403?'当前账号无法查看候选。':'候选暂不可用，请重新核对。');}
  finally {if(valid()){setBusy(false);controller.current=null;operationType.current='';}}
 }
 async function revalidate() {
  if(busy||!canManage||!canRevalidate||!prepareDraft||!onAdopt||sessionToken()!==sessionMarker)return;
  operationType.current='revalidate';const operation=++action.current,current=++generation.current,abort=new AbortController(),sameSession=()=>mounted.current&&sessionRef.current.scope===scope&&sessionRef.current.epoch===sessionEpoch&&sessionToken()===sessionMarker,valid=()=>current===generation.current&&identityRef.current===identity&&sameSession();controller.current=abort;setBusy(true);setError('');setNotice(null);let saved=false,dispatched=false;
  try {
   const revision=await prepareDraft();if(!valid())return;
   dispatched=true;const result=await request('/setup/candidates/revalidate',{method:'POST',body:JSON.stringify({revision}),signal:abort.signal});
   if(!valid())return;
   if(!result?.draft?.configuration?.businessCandidates||!result?.preview?.material||!Number.isInteger(result.draft.revision))throw Error('核对结果暂无法确认，请刷新后查看。');
   saved=true;await onAdopt(result.draft,{actor_id:account?.user_id,workspace_id:account?.workspace_id});
   // Adopting the new revision may update the bundle and invalidate the old
   // preview, but its material list still needs a fresh read in this session.
   if(!sameSession())return;
   await onRefreshMaterials?.();
   if(!sameSession()||action.current!==operation)return;
   const flows=Object.values(result.draft.configuration.flows||{}),unresolved=flows.some(flow=>flow?.candidateKey&&(flow.candidateUnresolvedSources?.length||flow.steps?.some(step=>step.candidateUnresolvedSources?.length)));
   const skipped=Number.isInteger(result.recheck?.skipped)&&result.recheck.skipped>0;
   const target=result.draft.configuration.businessCandidates,targetFingerprint=JSON.stringify([target?.material,target?.origin,target?.candidates]);
   setPreview(null);setNotice({identity:JSON.stringify([scope,targetFingerprint]),text:skipped?'部分已修改内容未自动核验，请查看相关业务。':unresolved?'部分依据仍待核，请查看相应业务。':result.preview.material.status==='parsed'?'核对结果已更新，请检查业务依据。':'核对结果已更新，资料仍有内容待核。'});
  } catch(e) {if(valid()||saved&&sameSession())setError(saved?'核对结果已保存，请刷新后查看。':!dispatched?'当前输入尚未保存，请先处理保存状态。':e?.status===409?'资料或配置已变化，请刷新后重试。':'核对结果暂未确认，请刷新后查看。');}
  finally {if(valid()){setBusy(false);controller.current=null;operationType.current='';}}
 }
 function close() {
  if(busy&&operationType.current==='revalidate'){setError('正在核对依据，请稍候。');return;}
  generation.current++;action.current++;controller.current?.abort();controller.current=null;operationType.current='';setBusy(false);setPreview(null);setError('');setOpen(false);
 }
 function add(keys) {
  if(!canManage||busy||!visiblePreview||sessionToken()!==sessionMarker)return;
  action.current++;
  try {
   const current=liveWorkspace?.()||workspace;
   if(JSON.stringify([current.businessCandidates?.material,current.businessCandidates?.origin,current.businessCandidates?.candidates])!==fingerprint)throw Error('候选已变化，请重新核对。');
   const result=addReportCandidates(current,bundle,visiblePreview,keys,materials,materialReadStatus);
   if(!result.added.length){setError('所选业务均已存在，未覆盖原配置。');return;}
   if(onCommit(result.workspace)) {onSelectBusiness?.(result.added[0]);close();setNotice({identity,text:`已加入 ${result.added.length} 项${result.skipped.length?`，跳过 ${result.skipped.length} 项已有业务`:''}；保存状态见页面顶部。`});}
   else setError('保存未完成，原配置保留，请检查页面保存状态。');
  } catch(e) {setError(e?.message||'暂时无法加入，请重新核对。');}
 }
 const names=businessNames(workspace),existing=new Set(names.map(candidateNameKey)),items=visiblePreview?.candidates||[],active=items.find(item=>item.key===selected)||items[0],available=items.filter(item=>!existing.has(candidateNameKey(item.name))),skipped=items.length-available.length;
 const unresolvedCount=active?(active.unresolved_sources?.length||0)+active.steps.reduce((sum,step)=>sum+(step.unresolved_sources?.length||0),0):0;
 return <section className="media-form" aria-label="已整理的业务候选">
  <div className="section-title"><h2>已整理的候选</h2><div className="row"><Btn disabled={busy} onClick={()=>{setOpen(true);if(ready)load();}}>查看候选</Btn>{!open&&canManage&&prepareDraft&&onAdopt&&<Btn disabled={!canRevalidate||busy} onClick={revalidate}>重新核对依据</Btn>}</div></div>
  {!open&&error&&<p role="alert">{error}</p>}
  {notice?.identity===identity&&<p role="status">{notice.text}</p>}
  {open&&<Dialog title="业务候选" onClose={close}><div className="modal-body business-candidate-dialog">
   <div className="business-candidate-tools"><span>{items.length?`${items.length} 项候选`:busy?'正在读取…':'候选待核对'}</span><div className="row">
    {onOpenSource&&<Btn disabled={!currentMaterial||currentMaterial.archived} onClick={()=>onOpenSource(currentMaterial)}>查看原资料</Btn>}
    {canManage&&prepareDraft&&onAdopt&&<Btn disabled={!canRevalidate||busy} onClick={revalidate}>重新核对依据</Btn>}
   </div></div>
   {!ready&&<p role="status">资料状态待核对，请刷新或重新核对依据。{onRefreshMaterials&&<Btn onClick={onRefreshMaterials}>刷新资料</Btn>}</p>}
   {error&&<p role="alert">{error}</p>}{notice?.identity===identity&&<p role="status">{notice.text}</p>}
   {ready&&!busy&&!visiblePreview&&<Btn onClick={load}>读取候选</Btn>}
   {visiblePreview&&ready&&<>{visiblePreview.material?.status!=='parsed'&&<p role="status">资料仍有内容待核，加入后请核对依据。</p>}<div className="business-candidate-layout"><nav className="business-candidate-list" aria-label="候选业务">{items.map(candidate=><button key={candidate.key} type="button" aria-pressed={active?.key===candidate.key} onClick={()=>setSelected(candidate.key)}><span>{candidate.name}</span><small>{existing.has(candidateNameKey(candidate.name))?'已加入':'待确认'}</small></button>)}</nav>
    {active&&<article className="business-candidate-detail"><h3>{active.name}</h3>{unresolvedCount>0&&<p role="status">{unresolvedCount} 项依据待核</p>}<p>启动：{active.trigger||'待补充'}　·　产出：{active.output||'待补充'}</p><Evidence item={active}/><ol>{active.steps.map(step=><li key={step.key}><strong>{step.name}</strong><span>{step.agent}{step.role?' · '+step.role:''} · {step.output||'产出待补充'}</span><details><summary>查看步骤说明与依据</summary><p>{step.purpose}</p><Evidence item={step}/></details></li>)}</ol>
     <Btn disabled={!canManage||busy||existing.has(candidateNameKey(active.name))} onClick={()=>add([active.key])}>{existing.has(candidateNameKey(active.name))?'已在业务清单':'加入当前业务'}</Btn></article>}</div>
    <div className="business-candidate-actions">{skipped>0&&<span role="status">{skipped} 项已在业务清单，将跳过</span>}<Btn primary disabled={!canManage||busy||!available.length} onClick={()=>add(items.map(item=>item.key))}>全部加入业务清单</Btn></div>
   </>}
  </div></Dialog>}
 </section>;
}
