import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';
import {withDraftCacheLock} from './draft-cache-contract.mjs';
import {same} from './report-full-reading-contract.mjs';
import {provisionalScope,provisionalBody,provisionalKey,checkPending,checkReceipt,checkList,checkRecord} from './full-provisional-contract.mjs';

const canWrite=account=>['admin','operator'].includes(account?.role);
const canonical=x=>JSON.stringify(x);
const errorCode=e=>e?.detail?.code||e?.detail?.detail?.code;
function storageFor(value){if(value)return value;try{return globalThis.localStorage;}catch{return null;}}
function ownedRead(storage,key){
  if(!storage)throw Error('storage');
  const raw=storage.getItem(key);if(raw===null)return null;
  try{return JSON.parse(raw);}catch{throw Error('storage');}
}

function CandidateSummary({record}){
  const value=record.candidate,quarantine=record.quarantine;
  const objects=Array.isArray(value?.objects)?value.objects:[],businesses=Array.isArray(value?.businesses)?value.businesses:[],relationships=Array.isArray(value?.relationships)?value.relationships:[],questions=Array.isArray(value?.questions)?value.questions:[];
  return <section aria-label="已保存的机器候选内容">
    <p>这是服务器保存的机器候选，尚未人工确认；不能应用或发布。业务步骤只作为清单，先后与条件需核对原文。</p>
    {objects.length>0&&<><h5>对象与字段</h5>{objects.map((o,i)=><article key={i}><strong>{typeof o?.name==='string'?o.name:'待核对象'}</strong>
      <ul>{(Array.isArray(o?.fields)?o.fields:[]).map((f,j)=><li key={j}>{typeof f?.label==='string'?f.label:'待核字段'}{typeof f?.unit==='string'?' · '+f.unit:''}</li>)}</ul></article>)}</>}
    {businesses.length>0&&<><h5>候选业务</h5>{businesses.map((b,i)=><article key={i}><strong>{typeof b?.name==='string'?b.name:'待核业务'}</strong>
      <ul>{(Array.isArray(b?.steps)?b.steps:[]).map((s,j)=><li key={j}>{typeof s?.name==='string'?s.name:'待核步骤'}</li>)}</ul></article>)}</>}
    {relationships.length>0&&<><h5>候选关系（仍需核对）</h5>{relationships.map((r,i)=><article key={i}><strong>{typeof r?.name==='string'?r.name:'待核关系'}</strong>
      <p>{typeof r?.source_type==='string'?r.source_type:'待核来源'} → {typeof r?.target_type==='string'?r.target_type:'待核目标'}{typeof r?.description==='string'?'：'+r.description:''}；未人工确认。</p></article>)}</>}
    {quarantine.length>0&&<><h5>依据不足，保留待核</h5>{quarantine.map((item,i)=>{
      if(item?.kind==='unit'&&typeof item.original_value==='string')return <p key={i}>模型建议单位“{item.original_value}”依据不足，未确认。</p>;
      const relation=item?.kind==='relationship'&&item.original_value;
      if(relation&&typeof relation.name==='string')return <p key={i}>模型建议关系“{relation.name}”依据不足，未确认。</p>;
      return <p key={i}>存在暂不能展示的待核项，请核对原回答。</p>;
    })}</>}
    {questions.length>0&&<><h5>模型提出的待核问题</h5><ul>{questions.map((q,i)=><li key={i}>{typeof q==='string'?q:'待核问题暂无法展示'}</li>)}</ul></>}
    <p>此处为保存稿摘要；完整依据和字段请回到原回答及来源核对。</p>
  </section>;
}

/** Persist only a server-generated machine candidate, never human decisions. */
export default function FullReadingProvisional({account,row,stage,preview=null,Btn='button',request=json,storage,locks,onSessionInvalid}){
  let ownScope=null;try{ownScope=provisionalScope(account,row,stage);}catch{}
  const scopeKey=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,ownScope,row?.revision]);
  const live=useRef({scopeKey,generation:0,serial:0,controller:null});
  if(live.current.scopeKey!==scopeKey){live.current.scopeKey=scopeKey;live.current.generation++;live.current.serial++;}
  const generation=live.current.generation,[state,setState]=useState(null),current=state?.scopeKey===scopeKey&&state?.generation===generation?state:null;
  const store=storageFor(storage),key=ownScope?provisionalKey(account,row,stage):'';
  const currentCheck=(stamp,gen,controller)=>live.current.scopeKey===scopeKey&&live.current.generation===gen&&live.current.serial===stamp&&!controller.signal.aborted;
  const base='/setup/report-full-readings/'+encodeURIComponent(row?.id||'');
  const listPath=base+'/stages/'+encodeURIComponent(stage||'')+'/provisional';
  const receiptPath=id=>base+'/provisional-receipts/'+encodeURIComponent(id);
  const options=signal=>({signal,cache:'no-store',headers:{'X-Workspace-ID':account.workspace_id}});

  useEffect(()=>{
    let active=true;
    if(!ownScope)return ()=>{};
    (async()=>{try{const entry=ownedRead(store,key);if(entry)await checkPending(entry,account,row,stage);
      if(active&&live.current.scopeKey===scopeKey&&live.current.generation===generation)setState({scopeKey,generation,phase:'ready',pending:entry,items:[],record:null,notice:''});
    }catch{if(active&&live.current.scopeKey===scopeKey&&live.current.generation===generation)setState({scopeKey,generation,phase:'blocked',pending:null,items:[],record:null,notice:'本机原保存编号暂不能核对；已保存的服务器记录可由本人查找，暂不发起新的保存。'});}})();
    return()=>{active=false;live.current.serial++;live.current.controller?.abort();live.current.controller=null;};
  },[scopeKey]);

  async function begin(){
    if(!ownScope||current?.phase==='loading')return null;
    const serial=++live.current.serial,gen=live.current.generation,controller=new AbortController();
    live.current.controller?.abort();live.current.controller=controller;
    setState(old=>({...old,scopeKey,generation:gen,phase:'loading',record:null,notice:''}));
    return {controller,active:()=>currentCheck(serial,gen,controller),gen};
  }
  function finish(task,change){if(task.active())setState(old=>({...old,...change,scopeKey,generation:task.gen}));if(live.current.controller===task.controller)live.current.controller=null;}
  function failed(task,e,pending=current?.pending){
    if(e?.status===401||e?.status===403){if(!task.active())return;finish(task,{phase:'ready',record:null,items:[],pending,notice:'账号或工作区权限已变化，私人内容已隐藏；原保存编号保留。'});onSessionInvalid?.();return;}
    finish(task,{phase:'ready',record:null,pending,notice:e?.status===404?'暂未查到原保存回执；这不证明原请求不会稍后提交。可再次核对，或按原编号重试。':
      errorCode(e)==='full_provisional_already_saved'?'此批次已有相同机器候选；请查找已保存记录，原编号保留。':
      e?.status===409?'来源、权限或原记录已变化；原保存编号保留，请刷新并核对。':'保存或读取结果暂未确认；原保存编号保留，请先核对原回执。'});
  }
  async function requestRecord(id,task,receipt=null){
    const result=await request(receiptPath(id),options(task.controller.signal));
    await checkRecord(result,account,row,stage,receipt);return result;
  }
  async function clearExact(entry){
    await withDraftCacheLock(key,async()=>{const latest=ownedRead(store,key);if(latest===null)return;
      if(!same(latest,entry))throw Error('storage');store.removeItem(key);if(store.getItem(key)!==null)throw Error('storage');},locks);
  }
  async function discover(){
    const task=await begin();if(!task)return;
    try{if(!task.active())return;const found=await request(listPath,options(task.controller.signal));const items=await checkList(found,account,row,stage);
      finish(task,{phase:'ready',items,record:null,notice:items.length?'已找到服务器保存的机器候选；请选择查看。':'当前未查到已保存候选；这不证明在途请求不会稍后提交。'});
    }catch(e){failed(task,e);}
  }
  async function readItem(item){
    const task=await begin();if(!task)return;
    try{if(!task.active())return;const record=await requestRecord(item.receipt.request_id,task,item.receipt);if(record.id!==item.id)throw Error('scope');
      finish(task,{phase:'ready',record,notice:'服务器已保存此机器候选，仍需人工核对。'});
    }catch(e){failed(task,e);}
  }
  async function recover({retry=false,entry=current?.pending}={}){
    if(!entry)return;const task=await begin();if(!task)return;
    try{
      await checkPending(entry,account,row,stage);
      if(!task.active())return;
      let record;
      try{record=await requestRecord(entry.body.request_id,task);}
      catch(e){
        if(e?.status!==404||!retry)throw e;
        if(!task.active()||!canWrite(account)||entry.body.revision!==row.revision)throw Error('stale');
        await withDraftCacheLock(key,async()=>{if(!same(ownedRead(store,key),entry))throw Error('storage');},locks);
        if(!task.active())return;
        record=await request(listPath,{...options(task.controller.signal),method:'POST',body:JSON.stringify(entry.body)});
        await checkRecord(record,account,row,stage);
      }
      if(record.receipt.request_id!==entry.body.request_id||record.receipt.raw_hash!==entry.body.raw_hash||
         record.receipt.view_hash!==entry.body.view_hash||record.receipt.source_hash!==entry.body.source_hash||
         record.receipt.algorithm_version!==entry.body.algorithm_version||record.receipt.draft_revision!==entry.body.revision)throw Error('scope');
      if(!task.active())return;
      await clearExact(entry);if(!task.active())return;
      finish(task,{phase:'ready',pending:null,record,notice:'原编号的机器候选已在服务器保存；尚未人工确认。'});
    }catch(e){failed(task,e,entry);}
  }
  async function save(){
    if(!ownScope||!current||current.phase!=='ready'||current.pending||!canWrite(account)||!preview)return;
    const task=await begin();if(!task)return;
    let entry,persisted=false,mayHaveSent=false;
    try{
      const body=await provisionalBody(account,row,stage,preview);
      entry={schema:'report-full-provisional-intent-v1',scope:ownScope,body,preview_hash:row.preview.preview_hash};
      await checkPending(entry,account,row,stage);
      if(!task.active())return;
      await withDraftCacheLock(key,async()=>{
        if(!task.active())return;
        const previous=ownedRead(store,key);if(previous&&!same(previous,entry))throw Error('storage');
        if(!previous){store.setItem(key,canonical(entry));if(!same(ownedRead(store,key),entry))throw Error('storage');}
      },locks);
      persisted=true;
      if(!task.active())return;
      // The command is durable before any request. Always probe/retry this
      // deterministic original ID; a second browser derives the same ID.
      setState(old=>({...old,pending:entry}));
      let record;
      try{record=await requestRecord(entry.body.request_id,task);}
      catch(e){if(e?.status!==404)throw e;if(!task.active())return;
        await withDraftCacheLock(key,async()=>{if(!same(ownedRead(store,key),entry))throw Error('storage');},locks);
        if(!task.active())return;mayHaveSent=true;
        record=await request(listPath,{...options(task.controller.signal),method:'POST',body:JSON.stringify(entry.body)});await checkRecord(record,account,row,stage);}
      if(record.receipt.request_id!==entry.body.request_id||record.receipt.raw_hash!==entry.body.raw_hash||
         record.receipt.view_hash!==entry.body.view_hash||record.receipt.source_hash!==entry.body.source_hash||
         record.receipt.algorithm_version!==entry.body.algorithm_version||record.receipt.draft_revision!==entry.body.revision)throw Error('scope');
      if(!task.active())return;
      await clearExact(entry);if(!task.active())return;
      finish(task,{phase:'ready',pending:null,record,notice:'原编号的机器候选已在服务器保存；尚未人工确认。'});
    }catch(e){
      if(!persisted&&!mayHaveSent)finish(task,{phase:'blocked',pending:null,record:null,notice:'本机原编号未可靠保存或范围无法核对；未发送保存请求。'});
      else if(!ownedReadSafe(store,key))finish(task,{phase:'blocked',pending:entry,record:null,notice:'原编号的本机恢复记录暂无法读取；服务器结果尚未确认，请保留页面并查找服务器记录。'});
      else failed(task,e,entry);
    }
  }
  if(!ownScope)return null;
  const items=current?.items||[],pending=current?.pending;
  return <section aria-label="机器候选保存与恢复">
    <p>只保存机器待核候选和原引用，不保存人工确认，不调用模型，也不能应用或发布。</p>
    {!current&&<p role="status">正在核对本机保存记录…</p>}
    {current?.notice&&<p role={current.phase==='blocked'?'alert':'status'}>{current.notice}</p>}
    {current&&<>
      <Btn type="button" disabled={current.phase==='loading'} onClick={discover}>查找已保存机器候选</Btn>
      <Btn type="button" disabled={current.phase!=='ready'||!!pending||!canWrite(account)||!preview} onClick={save}>保存此机器候选</Btn>
      {pending&&<div><p>原保存编号尚待核对；查找不会重发，重试只使用相同编号和原范围。</p>
        <Btn type="button" disabled={current.phase==='loading'} onClick={()=>recover()}>只核对原保存回执</Btn>
        <Btn type="button" disabled={current.phase==='loading'||!canWrite(account)||pending.body.revision!==row.revision} onClick={()=>recover({retry:true})}>按原编号重试保存</Btn>
      </div>}
      {items.map(item=><article key={item.id}><p>已保存的机器候选 · 原草案第 {item.receipt.draft_revision} 版 · 未人工确认</p><Btn type="button" disabled={current.phase==='loading'} onClick={()=>readItem(item)}>查看已保存候选</Btn></article>)}
      {current.record&&<CandidateSummary record={current.record}/>}
    </>}
  </section>;
}

function ownedReadSafe(storage,key){try{return ownedRead(storage,key);}catch{return null;}}
