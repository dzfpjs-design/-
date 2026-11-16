import React from 'react';
import {nestedAuthorReviewState,nestedAuthorReviewForm,editNestedAuthorReview,previewNestedAuthorReview,submitNestedAuthorReview,recoverNestedAuthorReview,archiveRejectedNestedAuthorReview,loadNestedAuthorReviews} from './nested-author-review.mjs';

const modeLabel=mode=>mode==='engineering_simulation'?'工程模拟检查（非人工业务审查）':'作者自审';
export default function NestedFlowAuthorReview({controller:c,onConfigureExecution}){
 if(!c.active||c.suspended||c.identityStatus!=='verified')return <section className="bfr-card" aria-label="递归流程版本核对"><h3>递归流程版本核对</h3><p>正在核对当前账号；原核对输入与原请求保留在原工作区。</p></section>;
 const s=nestedAuthorReviewState(c),form=nestedAuthorReviewForm(c),mode=s?.mode||'author_self_review',simulation=mode==='engineering_simulation',preview=form?.preview,pending=s?.pending,questions=c.form?.value?.business?.questions,questionCount=Array.isArray(questions)?questions.length:null;
 const readable=c.localSaved&&!c.busy,ready=c.canEdit&&readable&&!c.pending,locked=!ready||!!pending;
 const action=fn=>{try{const result=fn();result?.catch?.(error=>{c.error=error.message;c.notify();});}catch(error){c.error=error.message;c.notify();}};
 return <section className="bfr-card" aria-label="递归流程版本核对"><h3>核对已保存的递归流程</h3>
  <p>核对仅针对服务器已保存的具体版本。原回答的失败记录保持原状；保存修订或记录核对，都不会自动采用、启用业务或执行设备。</p>
  {c.error&&<p role="alert">{c.error}</p>}
  <label className="bfr-field"><span>本次核对方式</span><select aria-label="递归流程核对方式" value={mode} disabled={locked} onChange={event=>action(()=>editNestedAuthorReview(c,{mode:event.target.value}))}><option value="author_self_review">作者自审</option><option value="engineering_simulation">工程模拟检查（非人工业务审查）</option></select></label>
  {simulation?<p role="alert" className="bfr-pending">工程模拟，非人工业务审查。可保留待核问题用于工程测试；这项记录不能冒充人工核对、解决业务疑问或证明业务质量。</p>:<p>作者须明确核对所有流程、分支、岗位和原文，并先处理全部待核问题；系统不会代替作者勾选。</p>}
  {!simulation&&questionCount!==0&&<p role="alert">{questionCount===null?'待核问题暂不能核对。':'当前稿还有 '+questionCount+' 项待核问题。'}请先修订并保存，再检查作者自审版本；工程模拟不能代替作者处理这些问题。</p>}
  <button disabled={locked||!c.form||c.form.dirty||!c.detail?.saved||c.detail.source_available!==true||(!simulation&&questionCount!==0)} onClick={()=>action(()=>previewNestedAuthorReview(c))}>检查已保存的递归流程</button>
  {preview&&<section aria-label="本次递归核对范围"><p>业务：{preview.business_name} · 保存版本 {preview.expected_version} · {modeLabel(preview.review_mode)}</p><ul>{preview.flows.map(flow=><li key={flow.flow_index}>{flow.name} · {flow.action_count} 个动作，保留条件、并行与循环层级。</li>)}</ul>
   <p>待核问题 {preview.open_question_count} 项。引用与结构检查不等于业务语义正确。</p>
   {!simulation&&preview.open_question_count>0&&<p role="alert">仍有未解决的待核问题。请先修订并保存，重新检查后才能记录作者自审。</p>}
   <label className="bfr-check"><input type="checkbox" aria-label="明确核对递归流程范围" checked={form.ack_scope===true} disabled={locked} onChange={event=>action(()=>editNestedAuthorReview(c,{ack_scope:event.target.checked}))}/>{simulation?'我明确本次工程检查只针对这一保存版本的流程、原文范围和输入输出接线，不作人工业务审查。':'我已对照原文检查这一版本全部流程的触发、条件、并行、循环、职责与输入输出。'}</label>
   <label className="bfr-check"><input type="checkbox" aria-label="明确本次核对性质" checked={form.ack_inferences===true} disabled={locked} onChange={event=>action(()=>editNestedAuthorReview(c,{ack_inferences:event.target.checked}))}/>{simulation?'我明确这是非人工业务审查的工程模拟；待核问题仍保留，不能作为正式业务结论或执行授权。':'我已处理推断和冲突，确认本版本没有未解决的待核问题。'}</label>
   <label className="bfr-field"><span>{simulation?'工程模拟检查说明':'作者自审说明'}</span><textarea aria-label="递归流程核对说明" value={form.reason} readOnly={locked} maxLength={12000} rows={3} onChange={event=>{if(!locked)action(()=>editNestedAuthorReview(c,{reason:event.target.value}));}}/></label>
   <button disabled={locked||c.form?.dirty||c.form?.version!==preview.expected_version||!form.ack_scope||!form.ack_inferences||Array.from(form.reason.trim()).length<2||(!simulation&&preview.open_question_count!==0)} onClick={()=>action(()=>submitNestedAuthorReview(c))}>{simulation?'记录此版本的工程模拟检查':'记录此版本的作者自审'}</button>
  </section>}
  {pending&&<aside className="bfr-pending" aria-label="递归核对原请求恢复"><h4>原{modeLabel(pending.body.review_mode)}结果待核对</h4><p>原编号、核对方式和完整内容已保留。刷新后继续查询同一次请求；查不到回执不表示未提交，也不会自动换号或改为另一种核对。</p><button disabled={!readable} onClick={()=>action(()=>recoverNestedAuthorReview(c))}>查询原递归核对结果</button>{s.notFound&&!pending.rejection&&!pending.direct_result&&<button disabled={!ready} onClick={()=>action(()=>recoverNestedAuthorReview(c,true))}>按原编号与原内容重试递归核对</button>}{pending.direct_result&&<p>直接响应已保留，仍须核对原编号回执；不能重新提交。</p>}{pending.rejection&&<button disabled={!ready} onClick={()=>action(()=>archiveRejectedNestedAuthorReview(c))}>保留被拒原请求，重新检查递归稿</button>}</aside>}
  {form?.result&&<><p role="status">保存版本 {form.result.draft_version} 的{modeLabel(form.result.review_mode)}已保存。{form.result.simulation_only?'该记录明确不是人工业务审查；待核问题未据此解决。':'后续修订需要重新核对。'}当前未采用或执行。</p>{typeof onConfigureExecution==='function'&&<button type="button" disabled={!readable} onClick={()=>action(()=>onConfigureExecution(form.result))}>配置此核对版本的实际执行</button>}</>}
  <p><button disabled={!readable||!c.state.selected} onClick={()=>action(()=>loadNestedAuthorReviews(c))}>读取递归核对历史</button></p>
  {!!s?.history?.length&&<ul aria-label="已保存的递归核对历史">{s.history.map(record=><li key={record.id}>保存版本 {record.draft_version} · {modeLabel(record.review_mode)} · {record.reviewed_at}<p>{record.reason}</p><p>此记录不继承到后续修订，也不是采用或执行记录。</p>{typeof onConfigureExecution==='function'&&<button type="button" disabled={!readable} onClick={()=>action(()=>onConfigureExecution(record))}>打开此核对版本的递归执行配置</button>}</li>)}</ul>}
 </section>;
}
