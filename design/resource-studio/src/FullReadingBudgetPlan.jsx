import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';
import {digest, fullInt, fullSha, fullUUID, same, verifyFullDraft} from './report-full-reading-contract.mjs';

const base='/setup/report-evidence-delegated/budget-plan/';
const need=x=>{if(!x)throw Error('invalid');};
const fields=['source_mode','original_ref','copy_ref','provenance_hash','run_id','generation','run_version'];
const coverageFields=['expected_pages','prepared_pages','selected_pages','word_blocks','ocr_regions','word_characters','ocr_characters','versions_count'];
const pick=(value,keys)=>Object.fromEntries(keys.map(k=>[k,value[k]]));
async function nonce(value){const h=await digest(value);return 'fl_'+h.slice(0,8)+'-'+h.slice(8,12)+'-5'+h.slice(13,16)+'-a'+h.slice(17,20)+'-'+h.slice(20,32);}
async function command(account,row){const b=row.preview.body;return {original_draft_id:row.id,original_revision:row.revision,operator_asserted_history_hash:null,reading:{request_id:await nonce({kind:'report-budget-intent-v1',workspace_id:account.workspace_id,actor_id:account.user_id,draft_id:row.id,revision:row.revision,preview_hash:row.preview.preview_hash}),revision:b.revision,model_id:b.model_id,material_refs:structuredClone(b.material_refs),selection:structuredClone(b.selection),note:b.note}};}
async function checked(item,account,row){
  need(item?.schema==='report-evidence-budget-plan-v1'&&item.actor_id===account.user_id&&item.workspace_id===account.workspace_id&&item.original_draft_id===row.id&&fullUUID.test(item.preview_id||'')&&fullInt(item.original_revision,1)&&fullSha.test(item.summary_hash||''));
  const {summary_hash,...unsigned}=item;need(await digest(unsigned)===summary_hash);
  need(['saved','expired','started'].includes(item.status)&&typeof item.can_start==='boolean'&&typeof item.can_send==='boolean'&&item.historical_second_send==='not_independently_proven'&&item.source_relationship==='same_report_word_and_ocr_views_overlap'&&item.not_original_batch_recovery===true&&item.duplicate_cost_risk===true&&item.user_confirmed_business===false&&item.price_limit_available===false&&item.execution_history==='unknown'&&item.preview_action_calls_model===false&&['verified_issued','not_recorded'].includes(item.usage_marker));
  need(fullInt(item.max_calls,1)&&fullInt(item.max_tokens,1)&&typeof item.expires_at==='string'&&Number.isFinite(Date.parse(item.expires_at))&&typeof item.not_before==='string'&&Number.isFinite(Date.parse(item.not_before)));
  need(item.status==='started'?item.allowance_issued===true&&item.usage_marker==='verified_issued':[null,false].includes(item.allowance_issued)&&item.usage_marker==='not_recorded');
  need(item.budget_reserved===null&&item.model_called===null);
  if(item.source_summary_verified!==true)return {...item,verified:false};
  const selection=row.preview.body.selection, coverage=row.preview.source_snapshot.coverage;
  need(item.source_hash===row.preview.source_hash&&item.selection_hash===await digest(selection)&&same(item.selection,pick(selection,fields))&&same(item.coverage,pick(coverage,coverageFields))&&item.model_id===row.preview.body.model_id&&item.model_hash===row.preview.model_hash);
  const request=await command(account,row);request.original_revision=item.original_revision;request.reading.request_id=item.request_id;
  const expected=await digest({kind:'report_evidence_budget_preview_v2',workspace_id:account.workspace_id,actor_id:account.user_id,body:request});
  return {...item,verified:expected===item.command_hash};
}

export default function FullReadingBudgetPlan({account,row,Btn='button',request=json}){
  const expected=account?.role==='admin'&&fullUUID.test(account?.user_id||'')&&fullUUID.test(account?.workspace_id||'')&&row?.actor_id===account.user_id&&row?.workspace_id===account.workspace_id&&row?.status==='failed'&&row?.extractions?.length===2;
  const scope=JSON.stringify([account?.role,account?.user_id,account?.workspace_id,row?.id,row?.revision,row?.view_hash]);
  const live=useRef({scope,seq:0,controller:null});if(live.current.scope!==scope){live.current.scope=scope;live.current.seq++;}
  const [view,setView]=useState(null),current=view?.scope===scope?view:null;
  useEffect(()=>()=>{live.current.seq++;live.current.controller?.abort();},[scope]);
  async function run(action,plan){
    if(!expected||current?.busy)return;
    const seq=++live.current.seq,c=new AbortController();live.current.controller?.abort();live.current.controller=c;
    const active=()=>live.current.scope===scope&&live.current.seq===seq&&!c.signal.aborted;
    setView(v=>({...(v?.scope===scope?v:{}),scope,busy:true,error:null}));
    const post=(path,body)=>request(base+path,{method:'POST',signal:c.signal,cache:'no-store',headers:{'X-Workspace-ID':account.workspace_id},body:JSON.stringify(body)});
    try{
      await verifyFullDraft(row,account);
      if(action==='create')await checked(await post('preview',await command(account,row)),account,row);
      let notice=null;
      if(action==='renew'){
        need(plan?.verified&&plan.status==='expired'&&plan.original_revision===row.revision);
        const result=await post('renew',{preview_id:plan.preview_id,request_id:await nonce({kind:'report-budget-renew-v1',workspace_id:account.workspace_id,actor_id:account.user_id,preview_id:plan.preview_id})});
        need(result?.schema==='report-evidence-budget-renew-v1'&&typeof result.replayed==='boolean');await checked(result.plan,account,row);notice=result.replayed?'服务器确认该续期预览此前已保存；如未显示，请查看更多已保存预览。原预览仍保留。':'已保存续期预览，原预览仍保留。';
      }
      if(action==='start'){
        need(plan?.verified&&plan.status==='saved'&&plan.original_revision===row.revision&&plan.can_start===true&&plan.can_send===true&&plan.start_blocker===null);
        const result=await post('start',{preview_id:plan.preview_id,consent:true});
        need(result?.schema==='report-evidence-budget-start-v1'&&result.preview_id===plan.preview_id&&result.user_confirmed_business===false&&result.can_apply===false&&result.can_publish===false);
      }
      const lookup={original_draft_id:row.id,page_size:50};if(action==='more')lookup.before_preview_id=plan;const result=await post('find',lookup);
      need(result?.schema==='report-evidence-budget-plan-list-v1'&&result.workspace_id===account.workspace_id&&result.actor_id===account.user_id&&result.original_draft_id===row.id&&Array.isArray(result.items)&&typeof result.truncated==='boolean');
      const items=await Promise.all(result.items.map(i=>checked(i,account,row)));
      need(result.truncated ? items.length>0&&result.next_before_preview_id===items.at(-1).preview_id : result.next_before_preview_id===null);
      const combined=action==='more'?[...(current?.items||[]),...items]:items;need(new Set(combined.map(i=>i.preview_id)).size===combined.length);
      if(active())setView({scope,busy:false,items:combined,truncated:result.truncated,cursor:result.next_before_preview_id,checked:true,notice});
    }catch(e){if(active())setView({scope,busy:false,items:[],checked:false,truncated:false,cursor:null,error:e?.status===401||e?.status===403?'权限已变化，内容已隐藏。':e?.message==='invalid'?'服务器记录与原报告无法核对，未继续操作。':'请求结果暂时无法确认。请重新查找已保存预览；重试保持原范围，不能据此判断服务器没有保存。'});}
    finally{if(live.current.controller===c)live.current.controller=null;}
  }
  if(!expected)return null;
  return <section aria-label="报告分析额度预览"><h4>保存并找回分析额度预览</h4><p>预览保存在服务器，可在刷新或更换浏览器后找回；保存预览不会创建分析任务或调用模型。</p>
    <Btn type="button" disabled={current?.busy} onClick={()=>run('find')}>查找已保存预览</Btn>{current?.checked&&current.items.length===0&&<><p>没有找到已保存预览。若刚才请求超时，空列表不能证明请求未保存。</p><Btn type="button" disabled={current.busy} onClick={()=>run('create')}>保存当前范围的额度预览</Btn></>}
    {current?.notice&&<p role="status">{current.notice}</p>}{current?.busy&&<p role="status">正在核对服务器记录…</p>}{current?.error&&<p role="alert">{current.error}</p>}
    {current?.truncated&&<Btn type="button" disabled={current.busy} onClick={()=>run('more',current.cursor)}>查看更多已保存预览</Btn>}
    {current?.items?.map(p=><article key={p.preview_id}><h5>{p.status==='started'?'已创建分析任务':p.status==='expired'?'预览已失效':'已保存额度预览'}</h5>{!p.verified?<p role="alert">该记录缺少可核对的来源或与当前报告不一致；保留原记录，不能从这里启动。</p>:<><p>Word 正文 {p.coverage.word_blocks} 段，OCR {p.coverage.selected_pages} 页。两种视图来自同一报告，内容重叠，不是独立证据。</p><p>计划最多 {p.max_calls} 次调用、{p.max_tokens.toLocaleString()} token。token 不是金额上限；费用未知，可能产生重复费用，后续汇总可能另计。</p><p>现有记录仅支持第二批未发送的条件性工程推断，缺少独立网络或供应商证明；依赖运行环境、账本完整和不存在其他外部发送的假设。</p><p>机器结果和识别的图片、表格含义均未经过人工确认，不自动应用业务配置。</p>{p.status==='started'?<p>任务已创建；调用状态与费用未知，请查看对应分析记录及供应商账单。</p>:<p>本预览未显示已创建任务；实际发送状态未知，需进一步核验。现有记录不能独立证明没有在途调用或费用。{p.can_start!==true||p.can_send!==true?'当前暂不能从这里启动分析。':''}</p>}{p.status==='expired'&&p.original_revision===row.revision&&<><p>可尝试按原范围续期，服务器会核验记录是否符合条件；若已有后继或结果未知，请先查找已保存预览。</p><Btn type="button" disabled={current.busy} onClick={()=>run('renew',p)}>尝试重新保存预览</Btn></>}{p.status==='saved'&&p.can_start===true&&p.can_send===true&&p.start_blocker===null&&p.original_revision===row.revision&&<Btn type="button" disabled={current.busy} onClick={()=>run('start',p)}>确认范围并开始分析</Btn>}</>}</article>)}
  </section>;
}
