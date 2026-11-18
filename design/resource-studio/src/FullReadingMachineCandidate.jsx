import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';
import {digest} from './report-full-reading-contract.mjs';

const SHA = /^[a-f0-9]{64}$/;
const MAX_RESPONSE = 2 * 1024 * 1024;
const text = (value, limit=2000) => typeof value === 'string' && [...value].length <= limit;
const cardinality = value => ({many:'多项关联',one_per_source:'每个来源最多对应一个目标',one_per_target:'每个目标最多对应一个来源',unspecified:'数量关系未明确'})[value] || '数量关系待核对';
const citations = value => Array.isArray(value) && value.length <= 4 && value.every(c =>
  c && typeof c === 'object' && text(c.source_ref,40) && text(c.quote,250) && !!c.quote);

function expectedScope(account, row, stage, rawHash) {
  if (!account?.user_id || !account?.workspace_id || !account?.role || !row?.id ||
      row.actor_id !== account.user_id || row.workspace_id !== account.workspace_id ||
      !Number.isInteger(row.revision) || row.revision < 1 || !/^extract-[12]$/.test(stage || '') ||
      !row.extractions?.some(s => s.batch_id === stage) || !SHA.test(row.preview?.preview_hash || '') ||
      (rawHash != null && !SHA.test(rawHash))) return null;
  return {workspace_id:account.workspace_id, actor_id:account.user_id, draft_id:row.id,
          revision:row.revision, stage, preview_hash:row.preview.preview_hash};
}

async function checkedResult(value, expected, rawHash) {
  if (value?.schema !== 'report-full-failed-candidate-preview-v1' || !value.scope ||
      Object.keys(expected).some(key => value.scope[key] !== expected[key]) ||
      !SHA.test(value.scope.raw_hash || '') || (rawHash != null && value.scope.raw_hash !== rawHash) ||
      value.status !== 'needs_review' || value.confirmed !== false ||
      value.can_apply !== false || value.can_publish !== false || !SHA.test(value.view_hash || '')) throw Error('scope');
  const {view_hash, ...signed}=value;
  if (await digest(signed)!==view_hash) throw Error('scope');
  const candidate=value.candidate, quarantine=value.quarantine;
  if (!candidate || candidate.schema !== 'report-business-candidate-v3' ||
      !text(candidate.domain,200) || !Array.isArray(candidate.objects) || candidate.objects.length>40 ||
      !Array.isArray(candidate.relationships) || candidate.relationships.length>30 ||
      !Array.isArray(candidate.businesses) || candidate.businesses.length>20 ||
      !Array.isArray(candidate.questions) || candidate.questions.length>30 ||
      !candidate.questions.every(q=>text(q,2000)) || !Array.isArray(quarantine) || quarantine.length>1630 ||
      new TextEncoder().encode(JSON.stringify({candidate,quarantine})).length>MAX_RESPONSE) throw Error('shape');
  for (const object of candidate.objects) {
    if (!object || !text(object.name,100) || !Array.isArray(object.fields) || object.fields.length>40 ||
        !object.fields.every(f=>f && text(f.label,80) && text(f.kind,10) &&
          (f.unit===null || text(f.unit,40)) && citations(f.citations))) throw Error('shape');
  }
  for (const b of candidate.businesses) {
    if (!b || !text(b.name,100) || !text(b.trigger,1000) || !text(b.output,1000) ||
        !Array.isArray(b.steps) || b.steps.length>40 || !b.steps.every(s=>s && text(s.name,120) &&
          text(s.role ?? '',100) && text(s.output ?? '',1000) && citations(s.citations ?? []))) throw Error('shape');
  }
  for (const r of candidate.relationships) if (!r || !text(r.name,100) || !text(r.description,1000) ||
      !text(r.source_type,100) || !text(r.target_type,100) || !citations(r.citations)) throw Error('shape');
  for (const item of quarantine) {
    if (!item || typeof item !== 'object') throw Error('shape');
    if (item.kind==='unit') {
      if (!/^\/objects\/\d+\/fields\/\d+\/unit$/.test(item.path || '') ||
          item.reason!=='unit_evidence_not_established' || !text(item.original_value,40) || !citations(item.citations)) throw Error('shape');
    } else if (item.kind==='relationship') {
      const r=item.original_value;
      if (!/^\/relationships\/\d+$/.test(item.path || '') || item.reason!=='relationship_evidence_not_established' ||
          !r || !text(r.name,100) || !text(r.description,1000) || !text(r.source_type,100) ||
          !text(r.target_type,100) || !citations(r.citations)) throw Error('shape');
    } else throw Error('shape');
  }
  return {candidate,quarantine};
}

function sourceMap(row,stage) {
  const batches=row?.preview?.batches;
  const batch=Array.isArray(batches) ? batches.find(item=>item.batch_id===stage) : null;
  if (!batch || !Array.isArray(batch.source_refs) || !Array.isArray(batch.payload?.blocks)) return {};
  const allowed=new Set(batch.source_refs), result={};
  for (const item of batch.payload.blocks) {
    if (Array.isArray(item) && item.length===4 && typeof item[0]==='string' && allowed.has(item[0]) &&
        typeof item[1]==='string' && Array.isArray(item[2]) && typeof item[3]==='string' && !(item[0] in result))
      result[item[0]]={kind:item[1],location:item[2],value:item[3]};
  }
  return result;
}

function Evidence({items,sources}) {
  if (!items.length) return <p>未提供原文引用，仍需核对。</p>;
  return <div>{items.map((item,i)=>{
    const source=Object.hasOwn(sources,item.source_ref) ? sources[item.source_ref] : null;
    const matched=!!source && source.value.includes(item.quote);
    const label=source?.kind==='ocr_page' && Number.isInteger(source.location[0]) ? 'PDF 第 '+source.location[0]+' 页' :
      source ? 'Word 正文位置 '+String(source.location[0] ?? '待核对') : '原文来源暂无法核对';
    return <details key={i}><summary>{label} · {matched?'引用逐字匹配，含义仍待核':'引用尚未核实'}</summary>
      <p>模型所引文字：</p><blockquote>{item.quote}</blockquote>
      {source && source.value.length<=200000 ? <><p>本次发送的完整来源文字：</p><blockquote style={{whiteSpace:'pre-wrap'}}>{source.value}</blockquote></> :
        <p>{source?'来源文字过长，请回到原报告查看。':'该引用不在本批冻结来源中，请回到原报告核查。'}</p>}
    </details>;
  })}</div>;
}

/** Preview a server-derived candidate only; no model call, edit, save or adoption. */
export default function FullReadingMachineCandidate({account,row,stage,rawHash=null,Btn='button',request=json}) {
  const expected=expectedScope(account,row,stage,rawHash);
  const scope=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,expected,rawHash]);
  const live=useRef({scope,generation:0,serial:0,controller:null});
  if(live.current.scope!==scope){live.current.scope=scope;live.current.generation++;live.current.serial++;}
  const generation=live.current.generation,[view,setView]=useState(null);
  const current=view?.scope===scope && view?.generation===generation ? view : null;
  useEffect(()=>()=>{live.current.serial++;live.current.controller?.abort();live.current.controller=null;},[scope]);
  async function load(){
    if(!expected || current?.phase==='loading')return;
    const stamp=++live.current.serial,ownGeneration=live.current.generation,controller=new AbortController();
    live.current.controller?.abort();live.current.controller=controller;
    setView({scope,generation:ownGeneration,phase:'loading'});
    const active=()=>live.current.scope===scope && live.current.generation===ownGeneration && live.current.serial===stamp && !controller.signal.aborted;
    try{
      const path='/setup/report-full-readings/'+encodeURIComponent(expected.draft_id)+'/stages/'+encodeURIComponent(stage)+'/failed-candidate?revision='+encodeURIComponent(expected.revision);
      const reply=await request(path,{signal:controller.signal,cache:'no-store',headers:{'X-Workspace-ID':account.workspace_id}});
      const result=await checkedResult(reply,expected,rawHash);
      if(active())setView({scope,generation:ownGeneration,phase:'ready',result});
    }catch(error){if(active())setView({scope,generation:ownGeneration,phase:'error',
      message:error?.detail?.code==='full_candidate_preview_budget'?'来源过多或文字重复，暂无法安全生成预览；原回答和费用记录保留，可先查看原回答。':
        error?.status===401||error?.status===403?'当前账号无法读取此候选，请重新核对权限。':
        error?.status===409||error?.message==='scope'||error?.message==='shape'?'候选或原记录暂不能核对，请刷新后重试。':'候选暂时无法读取，请重试。'});
    }finally{if(live.current.controller===controller)live.current.controller=null;}
  }
  if(!expected)return null;
  const value=current?.result, sources=value ? sourceMap(row,stage) : {};
  return <section aria-label="机器待核候选预览">
    <p>本操作只读取已保存原回答形成的机器待核候选，不调用模型，不修改原回答，也不会确认、应用或发布。</p>
    <Btn type="button" disabled={current?.phase==='loading'} onClick={load}>{current?.phase==='loading'?'正在核对候选…':current?.phase==='error'?'重试生成机器待核候选':value?'重新读取机器待核候选':'生成机器待核候选'}</Btn>
    {current?.phase==='error' && <p role="alert">{current.message}</p>}
    {value && <div role="status"><h4>机器待核候选</h4>
      <p>本页仅为第 {stage.slice(-1)} 批抽取的候选，并非整份报告结果。以下均未人工确认，引用逐字匹配也不代表业务理解正确；有 {value.quarantine.length} 项需要重点核查。</p>
      {value.candidate.domain && <p>候选领域：{value.candidate.domain}</p>}
      {value.candidate.objects.length>0 && <section><h5>对象与字段</h5>{value.candidate.objects.map((o,i)=><article key={i}><strong>{o.name}</strong>
        <ul>{o.fields.map((f,j)=><li key={j}>{f.label} · {f.kind}{f.unit?' · 单位 '+f.unit:''}<Evidence items={f.citations} sources={sources}/></li>)}</ul></article>)}</section>}
      {value.candidate.businesses.length>0 && <section><h5>候选业务概览</h5><p>下列步骤仅为展示清单，先后与条件需对照原文核实；完整内容请查看原回答。</p>{value.candidate.businesses.map((b,i)=><article key={i}><strong>{b.name}</strong><p>启动：{b.trigger||'待核对'}；产出：{b.output||'待核对'}</p><ul>{b.steps.map((s,j)=><li key={j}>{s.name}{s.role?' · '+s.role:''}{s.output?' · 产出：'+s.output:''}<Evidence items={s.citations ?? []} sources={sources}/></li>)}</ul></article>)}</section>}
      {value.candidate.relationships.length>0 && <section><h5>候选关系</h5>{value.candidate.relationships.map((r,i)=><article key={i}><strong>{r.name}</strong><p>{r.source_type} → {r.target_type}：{r.description}；{cardinality(r.cardinality)}</p>{r.inference_reason&&<p>模型说明：{r.inference_reason}</p>}<Evidence items={r.citations} sources={sources}/></article>)}</section>}
      {value.quarantine.length>0 && <section><h5>需要重点核查</h5>{value.quarantine.map((item,i)=>{
        if(item.kind==='unit'){
          const match=/^\/objects\/(\d+)\/fields\/(\d+)\/unit$/.exec(item.path),o=value.candidate.objects[Number(match[1])],f=o?.fields[Number(match[2])];
          return <article key={i}><strong>字段单位的依据不足</strong><p>{o?.name||'待核对象'} · {f?.label||'待核字段'}：模型原建议单位“{item.original_value}”已保留供核查，未作为确认单位。</p><Evidence items={item.citations} sources={sources}/></article>;
        }
        const r=item.original_value;
        return <article key={i}><strong>对象关系的依据不足</strong><p>{r.name}：{r.source_type} → {r.target_type}；{r.description}；{cardinality(r.cardinality)}</p>{r.inference_reason&&<p>模型说明：{r.inference_reason}</p>}<p>模型原建议关系已保留供核查，未作为确认关系。</p><Evidence items={r.citations} sources={sources}/></article>;
      })}</section>}
      {value.candidate.questions.length>0 && <section><h5>模型提出的待核问题</h5><ul>{value.candidate.questions.map((q,i)=><li key={i}>{q}</li>)}</ul></section>}
    </div>}
  </section>;
}
