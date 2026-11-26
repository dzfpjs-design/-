import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';
import {businessNames} from './business-schema.js';
import {addReportCandidate, currentCandidateMaterial} from './report-candidate-draft.mjs';

const reasons={not_found:'未找到对应原文',ambiguous:'对应原文不唯一',invalid_quote:'引文需要补充',material_changed:'资料已变化',invalid_material:'资料暂不可用'};
function sessionToken(){try{return globalThis.sessionStorage?.getItem('ga-setup-csrf')||'';}catch{return '';}}

function Evidence({item}) {
 const citations=Array.isArray(item.citations)?item.citations:[], unresolved=Array.isArray(item.unresolved_sources)?item.unresolved_sources:[];
 if(!citations.length&&!unresolved.length&&!item.inference_reason)return <p>依据待补充</p>;
 return <div className="setup-candidate-evidence">
  {citations.map((ref,i)=><blockquote key={'c'+i}>{ref.quote}</blockquote>)}
  {unresolved.map((ref,i)=><div key={'u'+i}><p role="status">依据待核：{reasons[ref.reason]||'请重新核对'}</p>{ref.quote&&<blockquote>{ref.quote}</blockquote>}</div>)}
  {item.inference_reason&&<p>建议说明：{item.inference_reason}</p>}
 </div>;
}

export default function ReportBusinessCandidates({bundle, workspace, liveWorkspace, materials, materialReadStatus,
 canManage, onCommit, onSelectBusiness, onRefreshMaterials, onOpenSource, account, prepareDraft, onAdopt, Btn, request=json}) {
 const [open,setOpen]=useState(false),[preview,setPreview]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const generation=useRef(0),controller=useRef(null);
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
 useEffect(()=>{generation.current++;controller.current?.abort();setPreview(null);setBusy(false);setError('');setNotice('');},[identity]);
 useEffect(()=>{
  const check=()=>{if(sessionToken()!==sessionMarker){generation.current++;controller.current?.abort();setPreview(null);setBusy(false);setError('登录状态已变化，请重新登录后查看。');}};
  globalThis.window?.addEventListener('focus',check);globalThis.document?.addEventListener('visibilitychange',check);
  return ()=>{globalThis.window?.removeEventListener('focus',check);globalThis.document?.removeEventListener('visibilitychange',check);};
 },[identity]);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;generation.current++;controller.current?.abort();};},[]);
 if(!bundle||!Array.isArray(bundle.candidates)||!bundle.candidates.length)return null;
 async function load() {
  if(busy||!ready||sessionToken()!==sessionMarker)return;
  const current=++generation.current,abort=new AbortController(),valid=()=>current===generation.current&&identityRef.current===identity&&sessionToken()===sessionMarker;controller.current=abort;setBusy(true);setError('');setNotice('');setPreview(null);
  try {
   const material=bundle.material;
   const result=await request('/setup/candidates/preview',{method:'POST',body:JSON.stringify({schema:bundle.schema,origin:bundle.origin,material:{id:material.id,version:material.version,hash:material.hash,engine:material.engine},candidates:bundle.candidates}),signal:abort.signal});
   if(valid()){
    if(!result||!Array.isArray(result.candidates)||!result.material||result.candidates.length!==bundle.candidates.length)throw Error('候选暂不可用，请重新核对。');
    for(const item of bundle.candidates)addReportCandidate({businesses:[],flows:{}},bundle,result,item.key,materials,materialReadStatus,()=> 'check');
    setPreview({identity,value:result});
   }
  } catch(e) {if(valid())setError(e?.status===403?'当前账号无法查看候选。':'候选暂不可用，请重新核对。');}
  finally {if(valid()){setBusy(false);controller.current=null;}}
 }
 async function revalidate() {
  if(busy||!canManage||!canRevalidate||!prepareDraft||!onAdopt||sessionToken()!==sessionMarker)return;
  const current=++generation.current,abort=new AbortController(),sameSession=()=>mounted.current&&sessionRef.current.scope===scope&&sessionRef.current.epoch===sessionEpoch&&sessionToken()===sessionMarker,valid=()=>current===generation.current&&identityRef.current===identity&&sameSession();controller.current=abort;setBusy(true);setError('');setNotice('');let saved=false,dispatched=false;
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
   if(!valid())return;
   const flows=Object.values(result.draft.configuration.flows||{}),unresolved=flows.some(flow=>flow?.candidateKey&&(flow.candidateUnresolvedSources?.length||flow.steps?.some(step=>step.candidateUnresolvedSources?.length)));
   setPreview(null);setNotice(unresolved?'部分依据仍待核，请查看相应业务。':result.preview.material.status==='parsed'?'依据已重新核对。':'依据已重新核对，资料仍有内容待核。');
  } catch(e) {if(valid()||saved&&sameSession())setError(saved?'核对结果已保存，请刷新后查看。':!dispatched?'当前输入尚未保存，请先处理保存状态。':e?.status===409?'资料或配置已变化，请刷新后重试。':'核对结果暂未确认，请刷新后查看。');}
  finally {if(valid()){setBusy(false);controller.current=null;}}
 }
 function add(key) {
  if(!canManage||busy||!visiblePreview||sessionToken()!==sessionMarker)return;
  try {
   const current=liveWorkspace?.()||workspace;
   if(JSON.stringify([current.businessCandidates?.material,current.businessCandidates?.origin,current.businessCandidates?.candidates])!==fingerprint)throw Error('候选已变化，请重新核对。');
   const next=addReportCandidate(current,bundle,visiblePreview,key,materials,materialReadStatus);
   const selected=visiblePreview.candidates.find(item=>item.key===key);
   if(onCommit(next)) {onSelectBusiness?.(selected.name);setNotice('已加入业务清单，保存状态见页面顶部。');}
  } catch(e) {setError(e?.message||'暂时无法加入，请重新核对。');}
 }
 const names=businessNames(workspace);
 return <section className="media-form" aria-label="已整理的业务候选">
  <div className="section-title"><h2>已整理的候选</h2><div className="row"><Btn disabled={!ready||busy} onClick={()=>{setOpen(true);load();}}>{busy?'正在处理…':'查看候选'}</Btn>{onOpenSource&&<Btn disabled={!currentMaterial||currentMaterial.archived} onClick={()=>onOpenSource(currentMaterial)}>查看原资料</Btn>}{canManage&&prepareDraft&&onAdopt&&<Btn disabled={!canRevalidate||busy} onClick={revalidate}>重新核对依据</Btn>}</div></div>
  {!ready&&<p role="status">资料状态待核对，请刷新或重新核对依据。{onRefreshMaterials&&<Btn onClick={onRefreshMaterials}>刷新资料</Btn>}</p>}
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {open&&visiblePreview&&ready&&<div>{visiblePreview.material?.status!=='parsed'&&<p role="status">资料仍有内容待核，加入后请核对依据。</p>}
   {visiblePreview.candidates.map(candidate=><details key={candidate.key} className="setup-candidate-card"><summary>{candidate.name}{names.includes(candidate.name)?' · 已在业务清单':''}</summary>
    <p>启动：{candidate.trigger||'待补充'}</p><p>产出：{candidate.output||'待补充'}</p><Evidence item={candidate}/>
    <ol>{candidate.steps.map(step=><li key={step.key}><strong>{step.name}</strong><p>{step.purpose}</p><p>{step.agent}{step.role?' · '+step.role:''} · {step.output||'产出待补充'}</p><Evidence item={step}/></li>)}</ol>
    <Btn primary disabled={!canManage||busy||names.includes(candidate.name)} onClick={()=>add(candidate.key)}>{names.includes(candidate.name)?'已在业务清单':'加入业务清单'}</Btn>
   </details>)}
  </div>}
 </section>;
}
